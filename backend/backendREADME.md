# ANPR Backend
 
FastAPI server that receives plate images from the mobile app, runs YOLOv8 to detect the plate region, applies EasyOCR to extract the text, deduplicates, and logs to a Google Sheet tab named by today's date.
 
Deployed on **Railway**.
 
---
 
## Structure
 
```
backend/
├── anpr-server/
│   ├── app.py              # FastAPI app — endpoints, pipeline loading, error handling
│   ├── ocr.py              # ANPRPipeline class (YOLOv8 + EasyOCR)
│   ├── sheets.py           # Google Sheets auth, tab creation, plate logging, dedup
│   └── models/
│       └── detector/
│           └── best.pt     # Fine-tuned YOLOv8 weights
├── data/
│   ├── annotated/          # Labelled training images
│   ├── inference_images/   # Test inference images
│   ├── plates/             # Cropped plate images
│   ├── raw/                # Raw source images
│   └── ocr_labels.csv      # OCR ground truth labels
├── models/                 # Additional model artefacts
├── notebooks/              # Training and experimentation notebooks
├── runs/                   # YOLOv8 training run logs
├── scripts/                # Utility and preprocessing scripts
├── requirements.txt
├── Dockerfile
├── .dockerignore
├── .env.example
└── .railwayignore
```
 
---
 
## Setup
 
```bash
# 1. Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate
 
# 2. Install dependencies
pip install -r requirements.txt
 
# 3. Configure environment
cp .env.example .env
# Set GOOGLE_SERVICE_ACCOUNT_JSON to your full service account JSON string
 
# 4. Run
python anpr-server/app.py
```
 
Interactive API docs at `http://localhost:8000/docs`.
 
> The model loads in a background thread on startup — check `/health` before sending images.
 
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
- Authenticates via Google service account JSON stored in `GOOGLE_SERVICE_ACCOUNT_JSON` env var
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
  "pipeline_error": null
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
 
| Variable | Description |
|----------|-------------|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Full service account JSON as a string |
 
```env
GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account","project_id":"...","private_key":"..."}
```
 
> Never store the service account as a file in production — pass the full JSON as an env var as shown above.
 
---
 
## Docker
 
```bash
docker build -t anpr-server .
docker run -p 8000:8000 --env-file .env anpr-server
```
 
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
 
