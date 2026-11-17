# anpr-server/app.py
from fastapi import FastAPI, UploadFile, File, Request
from fastapi.responses import JSONResponse
import numpy as np
import cv2
import os
import re

from threading import Thread

from ocr import load_pipeline
from sheets import log_plate, recently_logged, sheets_enabled, undo_log

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
DEDUP_SECONDS = 15
VALID_PLATE = re.compile(r"[A-Z0-9]{1,8}")
# One row of a daily tab, as returned when a plate is logged: "2026-11-17!A5:E5"
ROW_RANGE = re.compile(r"\d{4}-\d{2}-\d{2}!A(\d+):E\1")

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


@app.post("/anpr/log")
async def anpr_log(payload: dict):
    """
    Debug endpoint: logs a plate text that the client provides.
    Expects: { "plate_text": "...", "yolo_conf": ..., "ocr_conf": ..., "bbox": [...], ... }
    """
    plate_text = payload.get("plate_text")
    if not plate_text:
        return {"status": "error", "message": "plate_text missing"}
    # Client-supplied text reaches the log/Sheet: allow only what a plate can be
    plate_text = str(plate_text).upper()
    if not VALID_PLATE.fullmatch(plate_text):
        return {"status": "error", "message": "plate_text must be 1-8 letters or digits"}
    payload = {**payload, "plate_text": plate_text}

    # Dedupe
    if recently_logged(plate_text, within_seconds=DEDUP_SECONDS):
        return {"status": "duplicate_ignored", "logged_plate": plate_text}

    try:
        row_info = log_plate(plate_text, meta=payload)
    except (RefreshError, DefaultCredentialsError) as e:
        return {
            "status": "sheet_auth_error",
            "message": "Detected plate but Google Sheets auth failed.",
            "logged_plate": plate_text,
            "error": str(e),
        }
    except HttpError as e:
        return {
            "status": "sheet_http_error",
            "message": "Google Sheets API returned an error.",
            "logged_plate": plate_text,
            "error": str(e),
        }

    return {"status": "ok", "logged_plate": plate_text, **row_info}


@app.post("/anpr/log/undo")
async def anpr_log_undo(payload: dict):
    """
    Undo a log from the app: clears the row the plate was written to, if it
    still holds that plate. Expects { "plate_text": "...", "range": "<tab>!A5:E5" }.
    """
    plate_text = str(payload.get("plate_text", "")).upper()
    row_range = str(payload.get("range") or "")
    if not VALID_PLATE.fullmatch(plate_text):
        return {"status": "error", "message": "plate_text must be 1-8 letters or digits"}
    if sheets_enabled() and not ROW_RANGE.fullmatch(row_range):
        return {"status": "error", "message": "range must be one row of a daily tab"}
    try:
        undone = undo_log(row_range, plate_text)
    except (RefreshError, DefaultCredentialsError, HttpError) as e:
        return {"status": "sheet_error", "message": "Could not reach the Sheet to undo.", "error": str(e)}
    if not undone:
        return {"status": "mismatch", "message": "That row no longer holds this plate; nothing was changed."}
    return {"status": "ok", "undone_plate": plate_text}


@app.post("/anpr")
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