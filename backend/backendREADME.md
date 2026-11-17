# ANPR Backend
 
FastAPI server that receives plate images from the mobile app, runs YOLOv8 to detect the plate region, applies EasyOCR to extract the text, and writes confirmed plates into the REGO column of today's tab in the month's audit spreadsheet.
 
Runs locally with `docker compose up --build` (from the repo root) and deploys to **Google Cloud Run** with `deploy/cloud-run.sh`.
 
---
 
## Structure
 
```
backend/
├── anpr-server/
│   ├── app.py              # FastAPI app — endpoints, pipeline loading, error handling
│   ├── ocr.py              # ANPRPipeline class (YOLOv8 + EasyOCR)
│   ├── sheets.py           # audit spreadsheet (or console): month files, day tabs, REGO column, undo
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
 
Interactive API docs at `http://localhost:8000/docs`. No credentials are needed: with no spreadsheet configured, plates are printed to the console.
 
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
- No `SHEETS_FOLDER_ID` or `SHEET_ID` → plates are printed to the console (no credentials needed)
- The spreadsheet in use is the newest in `SHEETS_FOLDER_ID` (or the fixed `SHEET_ID`); `POST /sheets/new-month` copies `SHEETS_TEMPLATE_ID` into the folder as "`<prefix>` - Month YYYY"
- Today's tab is `DD/MM/YYYY` (NZ time), copied from the `TEMPLATE` tab on the first scan of the day and placed first
- The plate goes into the next empty cell of column A from row 6; nothing else is written. A plate already on today's tab isn't added again. Past the template's last row, the row above (A:D) is copied so the formulas continue
- Undo clears that one cell, only if it still holds the plate
- Credentials, in order: `GOOGLE_OAUTH_TOKEN_JSON` (a sign-in from `backend/scripts/google_sign_in.py`), `GOOGLE_SERVICE_ACCOUNT_JSON`, then Application Default Credentials (`GOOGLE_APPLICATION_CREDENTIALS` may point at a sign-in or a key file)
- All Sheets calls run under one lock, so two scans can't claim the same cell (the service runs as one instance)
 
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
Log a plate the app has confirmed. Needs header `X-API-Key` when the server sets `API_KEY` (as do undo, `/anpr` and `/sheets/*`).
 
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
  "sheet": "Yard audit - November 2026",
  "tab": "17/11/2026",
  "range": "'17/11/2026'!A12"
}
```

`status` is `duplicate_ignored` when the plate is already on today's tab, `no_sheet` when the folder has no spreadsheet yet (the app keeps the scan and sends it once a month is started), and `sheet_setup_error` / `sheet_auth_error` / `sheet_http_error` with a `message` otherwise.

### `POST /anpr/log/undo`
`{ "plate_text": "ABC123", "range": "'17/11/2026'!A12" }` clears that REGO cell if it still holds the plate (`ok`), otherwise `mismatch`.

### `GET /sheets/current` · `POST /sheets/new-month`
The spreadsheet in use (`name`, `url`, today's `tab`, `month_title`, `can_start_month`), and starting this month's spreadsheet from the template.
 
### `POST /anpr`
Combined endpoint — detect and log in a single call. Used by the mobile app's quick-scan flow.
 
---
 
## Environment Variables
 
All optional — see `anpr-server/.env.example`.
 
| Variable | Description |
|----------|-------------|
| `SHEETS_FOLDER_ID` | Drive folder of monthly spreadsheets; the newest is used |
| `SHEETS_TEMPLATE_ID` | Spreadsheet copied by "start new month" |
| `SHEET_TITLE_PREFIX` | Monthly file name prefix (default `Yard audit`) |
| `SHEET_ID` | One fixed spreadsheet instead of a folder |
| `SHEET_TEMPLATE_TAB` / `SHEET_FIRST_ROW` / `SHEET_BLOCK` | Day-tab layout (defaults `TEMPLATE`, `6`, `A:D`) |
| `GOOGLE_OAUTH_TOKEN_JSON` | Sign-in to your Google account, as one string |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Full service account JSON as one string |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to a sign-in or key file |
| `API_KEY` | Required `X-API-Key` for logging, undo and `/sheets/*` |
| `PORT` | Listen port (default 8000; Cloud Run sets 8080) |
 
On Cloud Run, `deploy/cloud-run.sh` puts the sign-in and API key in Secret Manager; see the main README's Google Sheets section.
 
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
 
