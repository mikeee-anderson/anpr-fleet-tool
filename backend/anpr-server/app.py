# anpr-server/app.py
from fastapi import FastAPI, UploadFile, File, Request
from fastapi.responses import JSONResponse
import numpy as np
import cv2

from threading import Thread

from ocr import load_pipeline
from sheets import log_plate, recently_logged

from google.auth.exceptions import RefreshError
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
    # Start server immediately; load ML models in background
    Thread(target=_load_pipeline_bg, daemon=True).start()

# ---------------- Config ----------------
MIN_YOLO_CONF = 0.35
MIN_OCR_CONF = 0.45
DEDUP_SECONDS = 15

# ---------------- Health ----------------
@app.get("/health")
def health():
    return {
        "ok": True,
        "pipeline_ready": pipeline_ready,
        "pipeline_error": pipeline_error,
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
        return {"status": "needs_rescan", "message": reason, "detections": detections}

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

    # Dedupe
    if recently_logged(plate_text, within_seconds=DEDUP_SECONDS):
        return {"status": "duplicate_ignored", "logged_plate": plate_text}

    try:
        row_info = log_plate(plate_text, meta=payload)
    except RefreshError as e:
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