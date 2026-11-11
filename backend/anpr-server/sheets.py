# anpr-server/sheets.py
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import time
import json
import os

import pytz
import google.auth
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


# =========================
# DEDUP MEMORY
# =========================
_recent: dict[str, float] = {}  # plate_text -> last_logged_epoch_seconds

def recently_logged(plate_text: str, within_seconds: int = 15) -> bool:
    now = time.time()
    last = _recent.get(plate_text)
    if last and (now - last) < within_seconds:
        return True
    _recent[plate_text] = now
    return False


# =========================
# CONFIG
# =========================
SPREADSHEET_ID = os.environ.get("SHEET_ID")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

NZ_TZ = pytz.timezone("Pacific/Auckland")


def sheets_enabled() -> bool:
    """No SHEET_ID -> console mode: plates are printed instead of logged."""
    return bool(SPREADSHEET_ID)


# =========================
# AUTH
# =========================
def _get_credentials():
    """
    1) GOOGLE_SERVICE_ACCOUNT_JSON: the whole key JSON in one env var
    2) otherwise Application Default Credentials: a key file named by
       GOOGLE_APPLICATION_CREDENTIALS, or on Cloud Run the service's own
       identity (no key needed at all)
    """
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw:
        return Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)

    creds, _project = google.auth.default(scopes=SCOPES)
    return creds


def _get_service():
    return build("sheets", "v4", credentials=_get_credentials(), cache_discovery=False)


# =========================
# DAILY TAB HELPERS
# =========================
def _today_tab_name() -> str:
    # e.g. 2026-02-04
    return datetime.now(NZ_TZ).strftime("%Y-%m-%d")


def _ensure_sheet_exists(service, title: str):
    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    sheets = meta.get("sheets", [])
    existing_titles = {s["properties"]["title"] for s in sheets}

    if title in existing_titles:
        return

    # Create new tab
    service.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
    ).execute()

    # Add header row
    header = [["timestamp", "plate", "yolo_conf", "ocr_conf", "source"]]
    service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{title}!A:E",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": header},
    ).execute()


# =========================
# PUBLIC API
# =========================
def log_plate(plate_text: str, meta: dict | None = None) -> dict:
    """
    Appends to today's sheet tab:
    [timestamp, plate_text, yolo_conf, ocr_conf, source]
    """
    meta = meta or {}

    tab = _today_tab_name()
    timestamp = datetime.now(NZ_TZ).strftime("%Y-%m-%d %H:%M:%S")

    row = [
        timestamp,
        plate_text,
        float(meta.get("yolo_conf", 0.0)),
        float(meta.get("ocr_conf", 0.0)),
        str(meta.get("source", "unknown")),
    ]

    if not sheets_enabled():
        print(f"[console log] {tab}: {row}", flush=True)
        return {"sheet": "console", "timestamp": timestamp}

    service = _get_service()
    _ensure_sheet_exists(service, tab)

    service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{tab}!A:E",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()

    return {"sheet": tab, "timestamp": timestamp}