# anpr-server/sheets.py
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import time
import json
import os

import pytz
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
SPREADSHEET_ID = "16tP94LBR_oYS77-OdR6nJuAFEk-cdO5W6Px4iTZ-j1c"

NZ_TZ = pytz.timezone("Pacific/Auckland")


# =========================
# AUTH (SERVICE ACCOUNT)
# =========================
def _get_service():
    """
    Reads service account JSON from env var GOOGLE_SERVICE_ACCOUNT_JSON.
    (Railway Variables -> paste the whole JSON)
    """
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise RuntimeError("Missing GOOGLE_SERVICE_ACCOUNT_JSON env var")

    info = json.loads(raw)
    creds = Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )

    return build("sheets", "v4", credentials=creds)


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

    service = _get_service()
    tab = _today_tab_name()
    _ensure_sheet_exists(service, tab)

    timestamp = datetime.now(NZ_TZ).strftime("%Y-%m-%d %H:%M:%S")

    row = [
        timestamp,
        plate_text,
        float(meta.get("yolo_conf", 0.0)),
        float(meta.get("ocr_conf", 0.0)),
        str(meta.get("source", "unknown")),
    ]

    service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{tab}!A:E",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()

    return {"sheet": tab, "timestamp": timestamp}