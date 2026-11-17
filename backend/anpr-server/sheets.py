# anpr-server/sheets.py
"""
Writes scanned plates into the yard audit spreadsheet.

The layout follows the branch's existing physical-fleet report:

- One spreadsheet per month, all in one Drive folder (SHEETS_FOLDER_ID). The
  newest spreadsheet in the folder is the one in use. "Start new month"
  copies a template spreadsheet (SHEETS_TEMPLATE_ID) into the folder.
  Without a folder, SHEET_ID names a single spreadsheet to use instead.
- One tab per day, named DD/MM/YYYY. The first scan of the day creates it as
  a copy of the TEMPLATE tab (headers, formatting and formulas, no regos).
- A scanned plate goes into the next empty cell of the REGO column, from
  row 6 down. Nothing else is written: MODEL, FOUND and the system list on
  the right are the sheet's own formulas and data.

With nothing configured, plates are printed to the console instead.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime

import google.auth
import pytz
from google.oauth2.credentials import Credentials as UserCredentials
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from googleapiclient.discovery import build


# =========================
# CONFIG
# =========================
FOLDER_ID = os.environ.get("SHEETS_FOLDER_ID")  # Drive folder of monthly spreadsheets
TEMPLATE_ID = os.environ.get("SHEETS_TEMPLATE_ID")  # spreadsheet copied for a new month
SPREADSHEET_ID = os.environ.get("SHEET_ID")  # one fixed spreadsheet, when there's no folder
TITLE_PREFIX = os.environ.get("SHEET_TITLE_PREFIX", "Yard audit")  # "<prefix> - November 2026"

TEMPLATE_TAB = os.environ.get("SHEET_TEMPLATE_TAB", "TEMPLATE")
FIRST_ROW = int(os.environ.get("SHEET_FIRST_ROW", "6"))  # first row under the REGO header
# The physical-cars block: REGO is its first column, MODEL/FOUND/NOTES follow
BLOCK_FIRST, BLOCK_LAST = os.environ.get("SHEET_BLOCK", "A:D").split(":")
REGO_COL = BLOCK_FIRST

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",  # find the month's file, copy the template
]

NZ_TZ = pytz.timezone("Pacific/Auckland")
SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"

# A REGO cell as returned when a plate is logged, e.g. "'17/11/2026'!A12"
CELL = re.compile(rf"'(\d{{2}}/\d{{2}}/\d{{4}})'!{REGO_COL}(\d+)")


class SheetSetupError(Exception):
    """The spreadsheet isn't set up the way FleetScan expects (message says how to fix it)."""


class NoSheetYet(SheetSetupError):
    """The Drive folder has no spreadsheet yet: start one from the app."""


def sheets_enabled() -> bool:
    """No folder and no SHEET_ID -> console mode: plates are printed instead of logged."""
    return bool(FOLDER_ID or SPREADSHEET_ID)


def can_start_month() -> bool:
    return bool(FOLDER_ID and TEMPLATE_ID)


# Every Sheets/Drive call runs under this lock. The service runs as a single
# instance, so it is enough to stop two scans claiming the same empty cell.
_lock = threading.Lock()


# =========================
# AUTH
# =========================
def _get_credentials():
    """
    1) GOOGLE_OAUTH_TOKEN_JSON: a sign-in to your own Google account (from
       backend/tools/google_sign_in.py). Needed to create monthly files in a
       personal Drive: service accounts can't own files there.
    2) GOOGLE_SERVICE_ACCOUNT_JSON: a service account key, in one env var.
    3) Application Default Credentials: GOOGLE_APPLICATION_CREDENTIALS (a key
       or a sign-in file), or on Cloud Run the service's own identity.
    """
    token = os.environ.get("GOOGLE_OAUTH_TOKEN_JSON")
    if token:
        return UserCredentials.from_authorized_user_info(json.loads(token), scopes=SCOPES)
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw:
        return ServiceAccountCredentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
    creds, _project = google.auth.default(scopes=SCOPES)
    return creds


_clients: dict = {}


def _sheets():
    if "sheets" not in _clients:
        _clients["sheets"] = build("sheets", "v4", credentials=_get_credentials(), cache_discovery=False)
    return _clients["sheets"]


def _drive():
    if "drive" not in _clients:
        _clients["drive"] = build("drive", "v3", credentials=_get_credentials(), cache_discovery=False)
    return _clients["drive"]


# =========================
# WHICH SPREADSHEET, WHICH TAB
# =========================
def today_tab() -> str:
    """Today's tab name, as the branch names them: 17/11/2026."""
    return datetime.now(NZ_TZ).strftime("%d/%m/%Y")


def month_title(now: datetime | None = None) -> str:
    return f"{TITLE_PREFIX} - {(now or datetime.now(NZ_TZ)).strftime('%B %Y')}"


def sheet_url(spreadsheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"


def _quote(tab: str) -> str:
    """A tab name in A1 notation: '17/11/2026'."""
    return "'" + tab.replace("'", "''") + "'"


def _drive_literal(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _col_index(letter: str) -> int:
    n = 0
    for ch in letter.upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


# Cached so a scan costs one read and one write, not a lookup of the folder
# and the tab list every time. Cleared on any Sheets error, and after a new
# month is started.
_current: dict = {}
_tabs: dict[tuple[str, str], dict] = {}


def forget_cache() -> None:
    _current.clear()
    _tabs.clear()


def current_spreadsheet() -> dict:
    """The spreadsheet in use: {"id", "name"}."""
    if _current:
        return _current
    if FOLDER_ID:
        query = (f"{_drive_literal(FOLDER_ID)} in parents and mimeType = '{SPREADSHEET_MIME}' "
                 "and trashed = false")
        files = _drive().files().list(
            q=query, orderBy="createdTime desc", pageSize=10, fields="files(id,name)",
        ).execute().get("files", [])
        files = [f for f in files if f["id"] != TEMPLATE_ID]
        if not files:
            raise NoSheetYet("There's no audit spreadsheet yet. Start this month's sheet in the app's settings.")
        _current.update(id=files[0]["id"], name=files[0]["name"])
    else:
        meta = _sheets().spreadsheets().get(spreadsheetId=SPREADSHEET_ID, fields="properties.title").execute()
        _current.update(id=SPREADSHEET_ID, name=meta["properties"]["title"])
    return _current


def _tab(spreadsheet_id: str, title: str) -> dict:
    """Today's tab ({"sheetId", "rowCount"}), copied from the TEMPLATE tab if it doesn't exist yet."""
    key = (spreadsheet_id, title)
    if key in _tabs:
        return _tabs[key]

    service = _sheets().spreadsheets()
    meta = service.get(
        spreadsheetId=spreadsheet_id,
        fields="sheets.properties(sheetId,title,hidden,gridProperties.rowCount)",
    ).execute()
    tabs = {s["properties"]["title"]: s["properties"] for s in meta.get("sheets", [])}

    if title not in tabs:
        template = tabs.get(TEMPLATE_TAB)
        if template is None:
            raise SheetSetupError(f"The spreadsheet has no '{TEMPLATE_TAB}' tab to copy for today.")
        reply = service.batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": [{
            "duplicateSheet": {"sourceSheetId": template["sheetId"], "insertSheetIndex": 0, "newSheetName": title},
        }]}).execute()
        new = reply["replies"][0]["duplicateSheet"]["properties"]
        if new.get("hidden"):  # a hidden template makes a hidden copy
            service.batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": [{
                "updateSheetProperties": {"properties": {"sheetId": new["sheetId"], "hidden": False}, "fields": "hidden"},
            }]}).execute()
        tabs[title] = new

    props = tabs[title]
    _tabs[key] = {"sheetId": props["sheetId"], "rowCount": props.get("gridProperties", {}).get("rowCount", 1000)}
    return _tabs[key]


def _is_formula(cell) -> bool:
    return isinstance(cell, str) and cell.startswith("=")


def _extend_block(spreadsheet_id: str, tab: dict, row: int) -> None:
    """
    The template has run out of rows: copy the row above (formulas and
    formatting, columns A-D only) into `row`, adding grid rows if needed.
    """
    requests = []
    if row > tab["rowCount"]:
        requests.append({"appendDimension": {"sheetId": tab["sheetId"], "dimension": "ROWS", "length": 20}})
        tab["rowCount"] += 20
    first, last = _col_index(BLOCK_FIRST), _col_index(BLOCK_LAST) + 1
    requests.append({"copyPaste": {
        "source": {"sheetId": tab["sheetId"], "startRowIndex": row - 2, "endRowIndex": row - 1,
                   "startColumnIndex": first, "endColumnIndex": last},
        "destination": {"sheetId": tab["sheetId"], "startRowIndex": row - 1, "endRowIndex": row,
                        "startColumnIndex": first, "endColumnIndex": last},
        "pasteType": "PASTE_NORMAL",
    }})
    _sheets().spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()


# =========================
# PUBLIC API
# =========================
_console_logged: dict[str, set[str]] = {}  # console mode: tab -> plates logged


def log_plate(plate_text: str) -> dict:
    """
    Put the plate in the next empty REGO cell of today's tab.

    Returns {"sheet", "tab", "range", "duplicate"}. A plate already on today's
    tab isn't written again (duplicate=True, range=None): each car is on the
    parking once.
    """
    tab = today_tab()
    if not sheets_enabled():
        seen = _console_logged.setdefault(tab, set())
        if plate_text in seen:
            return {"sheet": "console", "tab": tab, "range": None, "duplicate": True}
        seen.add(plate_text)
        print(f"[console log] {tab}: {plate_text}", flush=True)
        return {"sheet": "console", "tab": tab, "range": None, "duplicate": False}

    with _lock:
        try:
            return _log_to_sheet(plate_text, tab)
        except Exception:
            forget_cache()  # a tab or file may have been renamed or deleted
            raise


def _log_to_sheet(plate_text: str, tab_title: str) -> dict:
    spreadsheet = current_spreadsheet()
    tab = _tab(spreadsheet["id"], tab_title)
    values = _sheets().spreadsheets().values()

    rows = values.get(
        spreadsheetId=spreadsheet["id"],
        range=f"{_quote(tab_title)}!{BLOCK_FIRST}{FIRST_ROW}:{BLOCK_LAST}",
        valueRenderOption="FORMULA",
    ).execute().get("values", [])
    regos = [str(r[0]).strip().upper() if r else "" for r in rows]

    result = {"sheet": spreadsheet["name"], "tab": tab_title}
    if plate_text in regos:
        return {**result, "range": None, "duplicate": True}

    index = regos.index("") if "" in regos else len(rows)
    row = FIRST_ROW + index
    above = rows[index - 1] if index > 0 else []
    here = rows[index] if index < len(rows) else []

    if any(map(_is_formula, above[1:])) and not any(map(_is_formula, here[1:])):
        # Past the template's last row: carry the formulas on, then write the
        # plate and blank the copied plain values (None leaves a cell as is)
        _extend_block(spreadsheet["id"], tab, row)
        width = _col_index(BLOCK_LAST) - _col_index(BLOCK_FIRST) + 1
        above = above + [""] * (width - len(above))
        new_row = [plate_text] + [None if _is_formula(c) else "" for c in above[1:width]]
        target = f"{_quote(tab_title)}!{BLOCK_FIRST}{row}:{BLOCK_LAST}{row}"
    else:
        new_row = [plate_text]
        target = f"{_quote(tab_title)}!{REGO_COL}{row}"

    values.update(
        spreadsheetId=spreadsheet["id"], range=target, valueInputOption="RAW", body={"values": [new_row]},
    ).execute()
    return {**result, "range": f"{_quote(tab_title)}!{REGO_COL}{row}", "duplicate": False}


def undo_log(cell: str | None, plate_text: str) -> bool:
    """
    Undo a logged plate by clearing its REGO cell, only if the cell still
    holds that plate. Clearing (not deleting) means no other row moves, and
    the formulas beside it stay. Returns False when the cell holds something else.
    """
    if not sheets_enabled():
        _console_logged.get(today_tab(), set()).discard(plate_text)
        print(f"[console undo] {cell}: {plate_text}", flush=True)
        return True

    with _lock:
        try:
            spreadsheet_id = current_spreadsheet()["id"]
            values = _sheets().spreadsheets().values()
            current = values.get(spreadsheetId=spreadsheet_id, range=cell).execute().get("values", [])
            if not current or not current[0] or str(current[0][0]).strip().upper() != plate_text:
                return False
            values.clear(spreadsheetId=spreadsheet_id, range=cell, body={}).execute()
            return True
        except Exception:
            forget_cache()
            raise


def status() -> dict:
    """What the app's settings show: where plates are going, and whether a new month can be started."""
    base = {"tab": today_tab(), "month_title": month_title(), "can_start_month": can_start_month()}
    if not sheets_enabled():
        return {**base, "mode": "console", "name": None, "url": None}
    with _lock:
        try:
            spreadsheet = current_spreadsheet()
        except NoSheetYet:
            return {**base, "mode": "sheets", "name": None, "url": None}
        except Exception:
            forget_cache()
            raise
    return {**base, "mode": "sheets", "name": spreadsheet["name"], "url": sheet_url(spreadsheet["id"])}


def start_new_month() -> dict:
    """
    Copy the template spreadsheet into the folder as this month's file, which
    then becomes the one in use. Starting a month that already has a file
    just switches to it, so a double tap never makes two.
    """
    if not can_start_month():
        raise SheetSetupError("Set SHEETS_FOLDER_ID and SHEETS_TEMPLATE_ID on the server to start new months.")
    title = month_title()
    with _lock:
        try:
            files = _drive().files()
            existing = files.list(
                q=(f"{_drive_literal(FOLDER_ID)} in parents and name = {_drive_literal(title)} "
                   f"and mimeType = '{SPREADSHEET_MIME}' and trashed = false"),
                fields="files(id,name)",
            ).execute().get("files", [])
            if existing:
                made, created = existing[0], False
            else:
                made = files.copy(fileId=TEMPLATE_ID, body={"name": title, "parents": [FOLDER_ID]},
                                  fields="id,name").execute()
                created = True
        finally:
            forget_cache()
    # The newest file is the one in use: an existing one may be older, so pin it
    _current.update(id=made["id"], name=made["name"])
    return {"created": created, "name": made["name"], "url": sheet_url(made["id"])}
