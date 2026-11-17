# anpr-server/app.py
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
import numpy as np
import cv2
import hmac
import os
import re

from threading import Thread

from ocr import load_pipeline
import sheets
from sheets import CELL, FIRST_ROW, NoSheetYet, SheetSetupError, log_plate, sheets_enabled, undo_log

from google.auth.exceptions import DefaultCredentialsError, RefreshError
from googleapiclient.errors import HttpError

app = FastAPI(title="ANPR Server")

# ---------------- Pipeline lazy loading ----------------
pipeline = None
pipeline_ready = False
pipeline_error = None

def _load_pipeline_bg():
    global pipeline, pipeline_ready, pipeline_error
    try:
        pipeline = load_pipeline()
        pipeline_ready = True
    except Exception as e:
        pipeline_error = str(e)

@app.on_event("startup")
def startup_event():
    if os.environ.get("K_SERVICE"):
        # Cloud Run only gives the container CPU while it serves requests, so
        # a background thread would stall. Load before accepting traffic; the
        # startup probe holds requests until the model is ready.
        _load_pipeline_bg()
        return
    # Start server immediately; load ML models in background
    Thread(target=_load_pipeline_bg, daemon=True).start()

# ---------------- Config ----------------
MIN_YOLO_CONF = 0.35
MIN_OCR_CONF = 0.45
VALID_PLATE = re.compile(r"[A-Z0-9]{1,6}")  # NZ plates, personalised too, are at most 6 characters

# The endpoints that write to the Sheet or Drive need this key (header
# X-API-Key) when API_KEY is set. /anpr/detect stays open for the demo.
API_KEY = os.environ.get("API_KEY")


def require_key(x_api_key: str | None = Header(default=None)):
    if API_KEY and not hmac.compare_digest(x_api_key or "", API_KEY):
        raise HTTPException(status_code=401, detail="Missing or wrong X-API-Key")


def sheet_error(e: Exception, plate_text: str | None = None) -> dict:
    """A Sheets/Drive failure as a reply the app can show."""
    if isinstance(e, NoSheetYet):
        out = {"status": "no_sheet", "message": str(e)}
    elif isinstance(e, SheetSetupError):
        out = {"status": "sheet_setup_error", "message": str(e)}
    elif isinstance(e, (RefreshError, DefaultCredentialsError)):
        out = {"status": "sheet_auth_error", "message": "Google sign-in for the Sheet failed.", "error": str(e)}
    else:
        out = {"status": "sheet_http_error", "message": "Google Sheets returned an error.", "error": str(e)}
    return {**out, "logged_plate": plate_text} if plate_text else out


SHEET_ERRORS = (SheetSetupError, RefreshError, DefaultCredentialsError, HttpError)

# ---------------- Health ----------------
@app.get("/health")
def health():
    return {
        "ok": True,
        "pipeline_ready": pipeline_ready,
        "pipeline_error": pipeline_error,
        "logger": "google_sheets" if sheets_enabled() else "console",
    }

# -------- error handling so you don't get "mystery 500s" ----------
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": "Internal server error", "detail": str(exc)},
    )

# ---------------- Helpers ----------------
def pick_best(detections: list[dict]):
    """
    Picks the best candidate using OCR confidence first, then YOLO confidence.
    Returns (best_detection, reason_if_none).
    """
    candidates = []
    for d in detections:
        yconf = float(d.get("yolo_conf", 0.0))
        o = d.get("ocr") or {}
        text = o.get("text")
        oconf = float(o.get("ocr_conf", 0.0)) if o else 0.0

        if not text:
            continue
        if yconf < MIN_YOLO_CONF:
            continue
        if oconf < MIN_OCR_CONF:
            continue

        candidates.append((oconf, yconf, d))

    if not candidates:
        return None, "No plate met confidence thresholds"

    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return candidates[0][2], None

TOO_DARK_MEAN = 60  # mean grey level (0-255) below which the photo is too dark to read
TOO_FAR_WIDTH = 0.12  # a plate box narrower than this share of the photo is too far away


def rescan_hint(img, detections) -> str | None:
    """Why a scan probably failed, so the app can say what to change."""
    if float(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).mean()) < TOO_DARK_MEAN:
        return "too_dark"
    widths = [d["bbox"][2] - d["bbox"][0] for d in detections]
    if widths and max(widths) < TOO_FAR_WIDTH * img.shape[1]:
        return "too_far"
    return None


def ensure_pipeline_ready():
    """
    If pipeline isn't ready yet, return a response dict; otherwise None.
    """
    if pipeline_ready and pipeline is not None:
        return None
    if pipeline_error:
        return {
            "status": "error",
            "message": "Pipeline failed to load",
            "detail": pipeline_error,
        }
    return {
        "status": "warming_up",
        "message": "Server is starting up (loading model). Try again in 10–30 seconds.",
    }

# ---------------- Endpoints ----------------
@app.post("/anpr/detect")
async def anpr_detect(file: UploadFile = File(...)):
    """
    Debug endpoint: returns detections + best candidate but does NOT log.
    """
    warm = ensure_pipeline_ready()
    if warm is not None:
        return warm

    data = await file.read()
    np_img = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(np_img, cv2.IMREAD_COLOR)

    if img is None:
        return {"status": "error", "message": "Could not decode image"}

    detections = pipeline.run(img)
    best, reason = pick_best(detections)

    if best is None:
        return {"status": "needs_rescan", "message": reason, "detections": detections,
                "hint": rescan_hint(img, detections)}

    return {"status": "ok", "best": best, "detections": detections}


@app.post("/anpr/log", dependencies=[Depends(require_key)])
async def anpr_log(payload: dict):
    """
    Log a plate the app has confirmed: it goes into the next empty REGO cell
    of today's tab. Expects { "plate_text": "...", ... }.
    """
    plate_text = payload.get("plate_text")
    if not plate_text:
        return {"status": "error", "message": "plate_text missing"}
    # Client-supplied text reaches the log/Sheet: allow only what a plate can be
    plate_text = str(plate_text).upper()
    if not VALID_PLATE.fullmatch(plate_text):
        return {"status": "error", "message": "plate_text must be 1-6 letters or digits"}

    try:
        info = log_plate(plate_text)
    except SHEET_ERRORS as e:
        return sheet_error(e, plate_text)

    duplicate = info.pop("duplicate")
    return {"status": "duplicate_ignored" if duplicate else "ok", "logged_plate": plate_text, **info}


@app.post("/anpr/log/undo", dependencies=[Depends(require_key)])
async def anpr_log_undo(payload: dict):
    """
    Undo a log from the app: clears the REGO cell the plate was written to, if
    it still holds that plate. Expects { "plate_text": "...", "range": "'17/11/2026'!A12" }.
    """
    plate_text = str(payload.get("plate_text", "")).upper()
    cell = str(payload.get("range") or "")
    if not VALID_PLATE.fullmatch(plate_text):
        return {"status": "error", "message": "plate_text must be 1-6 letters or digits"}
    if sheets_enabled():
        match = CELL.fullmatch(cell)
        if not match or int(match.group(2)) < FIRST_ROW:
            return {"status": "error", "message": "range must be one REGO cell of a daily tab"}
    try:
        undone = undo_log(cell, plate_text)
    except SHEET_ERRORS as e:
        return {**sheet_error(e), "status": "sheet_error"}
    if not undone:
        return {"status": "mismatch", "message": "That cell no longer holds this plate; nothing was changed."}
    return {"status": "ok", "undone_plate": plate_text}


@app.get("/sheets/current", dependencies=[Depends(require_key)])
def sheets_current():
    """Where plates are going: the spreadsheet in use, today's tab, and whether a new month can be started."""
    try:
        return {"status": "ok", **sheets.status()}
    except SHEET_ERRORS as e:
        return sheet_error(e)


@app.post("/sheets/new-month", dependencies=[Depends(require_key)])
def sheets_new_month():
    """Copy the template into the folder as this month's spreadsheet and switch to it."""
    try:
        return {"status": "ok", **sheets.start_new_month()}
    except SHEET_ERRORS as e:
        return sheet_error(e)


@app.post("/anpr", dependencies=[Depends(require_key)])
async def anpr_scan_and_log(file: UploadFile = File(...)):
    """
    MAIN MOBILE ENDPOINT:
    Upload image -> detect -> OCR -> log -> return confirmation or needs_rescan.
    """
    out = await anpr_detect(file)
    if out.get("status") != "ok":
        return out

    best = out["best"]
    payload = {
        "plate_text": best["ocr"]["text"],
        "yolo_conf": float(best["yolo_conf"]),
        "ocr_conf": float(best["ocr"]["ocr_conf"]),
        "bbox": best["bbox"],
        "source": "mobile_scan",
    }
    return await anpr_log(payload)