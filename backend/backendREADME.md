# ANPR Backend
 
FastAPI server that receives plate images from the mobile app, runs YOLOv8 to detect the plate region, applies EasyOCR to extract the text, deduplicates, and logs to a Google Sheet tab named by today's date.
 
Runs locally with `docker compose up --build` (from the repo root) and deploys to **Google Cloud Run** with `deploy/cloud-run.sh`.
 
---
 
## Structure
 
```
backend/
├── anpr-server/
│   ├── app.py              # FastAPI app — endpoints, pipeline loading, error handling
│   ├── ocr.py              # ANPRPipeline class (YOLOv8 + EasyOCR)
│   ├── sheets.py           # Google Sheets (or console) logging, credentials, dedup
│   ├── models/detector/
│   │   └── best.pt         # Fine-tuned YOLOv8 weights (the only committed weights)
│   ├── tests/              # pytest suite (synthetic images only)
│   ├── Dockerfile
│   ├── requirements.txt    # pinned runtime deps (torch installed in the Dockerfile)
│   ├── requirements-dev.txt
│   └── .env.example
├── data/plates/data.yaml   # YOLO dataset config (images are not committed)
├── runs/detect/train/      # Training curves and metrics
└── scripts/                # One-off inference script from development
```
 
---
 
## Setup
 
```bash
# Easiest: from the repo root
docker compose up --build
 
# Or without Docker
cd backend/anpr-server
python3.11 -m venv .venv && source .venv/bin/activate
pip install torch==2.15.0 torchvision==0.29.2 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-dev.txt
uvicorn app:app --reload
 
# Tests
pytest
```
 
Interactive API docs at `http://localhost:8000/docs`. No credentials are needed: without `SHEET_ID`, plates are printed to the console.
 
> The model loads in a background thread on startup — check `/health` before sending images. (On Cloud Run it loads before the server accepts traffic instead.)
 
---
 
## How It Works
 
### Startup (`app.py`)
The YOLOv8 model and EasyOCR reader are loaded in a background thread so the server accepts requests immediately while the models load. `/health` reports `pipeline_ready` once complete.
 
### Detection (`ocr.py` — `ANPRPipeline`)
1. Runs YOLOv8 on the incoming image to get bounding boxes
2. Crops each detected region
3. Runs EasyOCR on each crop
4. Filters results by YOLO confidence (`>0.25`) and OCR confidence (`>0.30`)
5. Cleans plate text — strips spaces, hyphens, uppercases
6. Returns a list of detections with bbox, YOLO conf, and best OCR result
 
### Best Candidate Selection (`app.py` — `pick_best`)
From all detections, picks the one with the highest OCR confidence (then YOLO confidence as tiebreaker), subject to minimum thresholds:
- YOLO confidence: `>0.35`
- OCR confidence: `>0.45`
 
### Logging (`sheets.py`)
- If `SHEET_ID` is unset, rows are printed to the console instead (no credentials needed)
- Credentials: `GOOGLE_SERVICE_ACCOUNT_JSON` if set, otherwise Application Default Credentials (a key file via `GOOGLE_APPLICATION_CREDENTIALS`, or the Cloud Run service account)
- Creates a new Sheet tab named `YYYY-MM-DD` if it doesn't exist for today (NZ timezone)
- Appends a row: `[timestamp, plate_text, yolo_conf, ocr_conf, source]`
- Deduplicates: ignores the same plate logged within 15 seconds (in-memory)
 
---
 
## API Endpoints
 
### `GET /health`
Returns server and pipeline status.
 
```json
{
  "ok": true,
  "pipeline_ready": true,
  "pipeline_error": null,
  "logger": "console"
}
```
 
### `POST /anpr/detect`
Upload an image, get detection results. Does **not** log to Sheets.
 
**Request:** `multipart/form-data` with `file` field (JPEG/PNG)
 
**Response (success):**
```json
{
  "status": "ok",
  "best": {
    "bbox": [x1, y1, x2, y2],
    "yolo_conf": 0.87,
    "ocr": { "text": "ABC123", "ocr_conf": 0.91 }
  },
  "detections": [...]
}
```
 
**Response (low confidence):**
```json
{
  "status": "needs_rescan",
  "message": "No plate met confidence thresholds"
}
```
 
### `POST /anpr/log`
Log a plate text supplied by the client.
 
**Request:** `application/json`
```json
{
  "plate_text": "ABC123",
  "yolo_conf": 0.87,
  "ocr_conf": 0.91,
  "bbox": [x1, y1, x2, y2],
  "source": "mobile_confirm"
}
```
 
**Response:**
```json
{
  "status": "ok",
  "logged_plate": "ABC123",
  "sheet": "2026-04-08",
  "timestamp": "2026-04-08 14:32:01"
}
```
 
### `POST /anpr`
Combined endpoint — detect and log in a single call. Used by the mobile app's quick-scan flow.
 
---
 
## Environment Variables
 
All optional — see `anpr-server/.env.example`.
 
| Variable | Description |
|----------|-------------|
| `SHEET_ID` | Google Sheet to log to. Unset → console logging |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Full service account JSON as one string |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to a key file (alternative to the above) |
| `PORT` | Listen port (default 8000; Cloud Run sets 8080) |
 
On Cloud Run, leave the credential variables empty: the service runs as its own service account, which you share the Sheet with.
 
---
 
## Docker
 
```bash
# from the repo root
docker compose up --build
```
 
The image bakes in the EasyOCR weights so containers don't download them on startup.
 
---
 
## Dependencies
 
| Package | Purpose |
|---------|---------|
| `fastapi` / `uvicorn` | Web framework and server |
| `ultralytics` | YOLOv8 detection |
| `easyocr` | Plate text recognition |
| `opencv-python-headless` | Image decoding and processing |
| `numpy` | Array operations |
| `google-api-python-client` | Google Sheets API |
| `google-auth` | Service account authentication |
| `pytz` | NZ timezone handling |
 
