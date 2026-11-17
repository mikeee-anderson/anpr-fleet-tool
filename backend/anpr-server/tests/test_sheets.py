"""
The yard-audit layout against an in-memory stand-in for Google Sheets and
Drive: daily tabs from TEMPLATE, the REGO column, undo and new months.
Plates here are made up.
"""
import re

import pytest

import sheets

TODAY = "17/11/2026"


# ---------------- A fake Google Sheets + Drive ----------------
def _col(letter):
    return sheets._col_index(letter)


class Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeSpreadsheet:
    def __init__(self, title, tabs):
        self.title = title
        self.tabs = {}  # title -> {"sheetId", "hidden", "rowCount", "cells": {(row, col): value}}
        for name, spec in tabs.items():
            self.add_tab(name, **spec)

    def add_tab(self, name, cells=None, hidden=False, rowCount=40, index=None):
        tab = {"sheetId": len(self.tabs) + 100, "hidden": hidden, "rowCount": rowCount, "cells": dict(cells or {})}
        items = list(self.tabs.items())
        items.insert(len(items) if index is None else index, (name, tab))
        self.tabs = dict(items)
        return tab

    def by_id(self, sheet_id):
        return next(t for t in self.tabs.values() if t["sheetId"] == sheet_id)

    def parse(self, a1):
        m = re.fullmatch(r"'((?:[^']|'')+)'!([A-Z]+)(\d+)(?::([A-Z]+)(\d*))?", a1)
        assert m, f"unexpected range {a1}"
        tab = self.tabs[m.group(1).replace("''", "'")]
        c1 = _col(m.group(2))
        r1 = int(m.group(3))
        c2 = _col(m.group(4)) if m.group(4) else c1
        r2 = int(m.group(5)) if m.group(5) else (tab["rowCount"] if m.group(4) else r1)
        return tab, r1, c1, r2, c2


class FakeGoogle:
    """Implements just the calls sheets.py makes, and records the writes."""

    def __init__(self):
        self.store = {}  # id -> FakeSpreadsheet
        self.parents = {}  # id -> folder
        self.created = []  # order of creation, oldest first
        self.writes = []

    def add_file(self, file_id, sheet, folder="folder"):
        self.store[file_id] = sheet
        self.parents[file_id] = folder
        self.created.append(file_id)

    # -------- Sheets --------
    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId, range=None, fields=None, valueRenderOption=None):
        sheet = self.store[spreadsheetId]
        if range is None:  # spreadsheets().get
            return Req(lambda: {
                "properties": {"title": sheet.title},
                "sheets": [{"properties": {"sheetId": t["sheetId"], "title": n, "hidden": t["hidden"],
                                           "gridProperties": {"rowCount": t["rowCount"]}}}
                           for n, t in sheet.tabs.items()],
            })
        tab, r1, c1, r2, c2 = sheet.parse(range)
        rows = [[tab["cells"].get((r, c), "") for c in range_(c1, c2)] for r in range_(r1, r2)]
        rows = [_trim(r) for r in rows]
        while rows and not rows[-1]:
            rows.pop()
        return Req(lambda: {"values": rows} if rows else {})

    def update(self, spreadsheetId, range, valueInputOption, body):
        tab, r1, c1, _, _ = self.store[spreadsheetId].parse(range)
        for dr, row in enumerate(body["values"]):
            for dc, v in enumerate(row):
                if v is not None:  # the API skips nulls
                    tab["cells"][(r1 + dr, c1 + dc)] = v
        self.writes.append(range)
        return Req(lambda: {})

    def clear(self, spreadsheetId, range, body):
        tab, r1, c1, r2, c2 = self.store[spreadsheetId].parse(range)
        for r in range_(r1, r2):
            for c in range_(c1, c2):
                tab["cells"].pop((r, c), None)
        self.writes.append(f"clear {range}")
        return Req(lambda: {})

    def batchUpdate(self, spreadsheetId, body):
        sheet = self.store[spreadsheetId]
        replies = []
        for req in body["requests"]:
            if "duplicateSheet" in req:
                d = req["duplicateSheet"]
                src = sheet.by_id(d["sourceSheetId"])
                new = sheet.add_tab(d["newSheetName"], cells=src["cells"], hidden=src["hidden"],
                                    rowCount=src["rowCount"], index=d["insertSheetIndex"])
                replies.append({"duplicateSheet": {"properties": {
                    "sheetId": new["sheetId"], "title": d["newSheetName"], "hidden": new["hidden"],
                    "gridProperties": {"rowCount": new["rowCount"]}}}})
            elif "updateSheetProperties" in req:
                p = req["updateSheetProperties"]["properties"]
                sheet.by_id(p["sheetId"])["hidden"] = p["hidden"]
                replies.append({})
            elif "appendDimension" in req:
                sheet.by_id(req["appendDimension"]["sheetId"])["rowCount"] += req["appendDimension"]["length"]
                replies.append({})
            elif "copyPaste" in req:
                s, d = req["copyPaste"]["source"], req["copyPaste"]["destination"]
                tab = sheet.by_id(s["sheetId"])
                assert d["endRowIndex"] <= tab["rowCount"], "pasted below the grid"
                shift = d["startRowIndex"] - s["startRowIndex"]
                for c in range(s["startColumnIndex"], s["endColumnIndex"]):
                    v = tab["cells"].get((s["startRowIndex"] + 1, c), "")
                    # formulas move down with the row, like Sheets' relative references
                    if isinstance(v, str) and v.startswith("="):
                        v = re.sub(r"([A-Z])(\d+)", lambda m: f"{m.group(1)}{int(m.group(2)) + shift}", v)
                    tab["cells"][(d["startRowIndex"] + 1, c)] = v
                replies.append({})
        return Req(lambda: {"replies": replies})

    # -------- Drive --------
    def files(self):
        return self

    def list(self, q, fields, orderBy=None, pageSize=None):
        folder = re.search(r"'([^']+)' in parents", q).group(1)
        name = re.search(r"name = '([^']+)'", q)
        ids = [i for i in reversed(self.created) if self.parents[i] == folder]  # newest first
        if name:
            ids = [i for i in ids if self.store[i].title == name.group(1)]
        return Req(lambda: {"files": [{"id": i, "name": self.store[i].title} for i in ids]})

    def copy(self, fileId, body, fields):
        src = self.store[fileId]
        new_id = f"file-{len(self.store) + 1}"
        copy = FakeSpreadsheet(body["name"], {})
        for name, tab in src.tabs.items():
            copy.add_tab(name, cells=tab["cells"], hidden=tab["hidden"], rowCount=tab["rowCount"])
        self.add_file(new_id, copy, folder=body["parents"][0])
        return Req(lambda: {"id": new_id, "name": body["name"]})


def range_(a, b):
    return range(a, b + 1)


def _trim(row):
    while row and row[-1] == "":
        row = row[:-1]
    return row


def template_cells(rows=3):
    """Title rows, headers on row 5, and MODEL/FOUND formulas on rows 6..5+rows; REGO empty."""
    cells = {(2, 0): "PHYSICAL FLEET", (5, 0): "REGO", (5, 1): "MODEL", (5, 2): "FOUND", (5, 3): "NOTES",
             (5, 5): "REGO", (5, 6): "FOUND"}
    for r in range(6, 6 + rows):
        cells[(r, 1)] = f'=IFERROR(VLOOKUP(A{r},FLEET!A:B,2,0),"")'
        cells[(r, 2)] = f"=COUNTIF(F:F,A{r})>0"
    cells[(6, 5)] = "SYS001"  # the system list on the right
    return cells


@pytest.fixture
def google(monkeypatch):
    fake = FakeGoogle()
    fake.add_file("template", FakeSpreadsheet("Template", {"TEMPLATE": {"cells": template_cells(), "hidden": True}}),
                  folder="elsewhere")
    fake.add_file("nov", FakeSpreadsheet("Yard audit - November 2026",
                                         {"TEMPLATE": {"cells": template_cells(), "hidden": True}}))
    monkeypatch.setattr(sheets, "FOLDER_ID", "folder")
    monkeypatch.setattr(sheets, "TEMPLATE_ID", "template")
    monkeypatch.setattr(sheets, "SPREADSHEET_ID", None)
    monkeypatch.setattr(sheets, "_sheets", lambda: fake)
    monkeypatch.setattr(sheets, "_drive", lambda: fake)
    monkeypatch.setattr(sheets, "today_tab", lambda: TODAY)
    sheets.forget_cache()
    yield fake
    sheets.forget_cache()


def rego(fake, file_id, row):
    return fake.store[file_id].tabs[TODAY]["cells"].get((row, 0))


# ---------------- Daily tab ----------------
def test_first_scan_of_the_day_makes_a_visible_tab_from_template(google):
    out = sheets.log_plate("AAA111")

    tabs = google.store["nov"].tabs
    assert list(tabs)[0] == TODAY  # newest first
    assert tabs[TODAY]["hidden"] is False
    assert tabs["TEMPLATE"]["cells"].get((6, 0)) is None  # the template stays blank
    assert out == {"sheet": "Yard audit - November 2026", "tab": TODAY,
                   "range": f"'{TODAY}'!A6", "duplicate": False}


def test_a_spreadsheet_without_a_template_tab_says_so(google):
    del google.store["nov"].tabs["TEMPLATE"]

    with pytest.raises(sheets.SheetSetupError, match="TEMPLATE"):
        sheets.log_plate("AAA111")


# ---------------- REGO column ----------------
def test_plates_fill_the_rego_column_and_touch_nothing_else(google):
    sheets.log_plate("AAA111")
    sheets.log_plate("BBB222")

    cells = google.store["nov"].tabs[TODAY]["cells"]
    assert (cells[(6, 0)], cells[(7, 0)]) == ("AAA111", "BBB222")
    assert cells[(6, 1)].startswith("=IFERROR")  # formulas untouched
    assert cells[(6, 5)] == "SYS001"  # system list untouched
    assert google.writes == [f"'{TODAY}'!A6", f"'{TODAY}'!A7"]


def test_a_plate_already_on_todays_tab_is_not_written_again(google):
    sheets.log_plate("AAA111")
    sheets.forget_cache()  # survives a server restart: the tab itself is checked

    out = sheets.log_plate("AAA111")

    assert out["duplicate"] is True and out["range"] is None
    assert google.writes == [f"'{TODAY}'!A6"]


def test_a_gap_left_by_undo_is_filled_first(google):
    for plate in ("AAA111", "BBB222", "CCC333"):
        sheets.log_plate(plate)
    assert sheets.undo_log(f"'{TODAY}'!A7", "BBB222")

    out = sheets.log_plate("DDD444")

    assert out["range"] == f"'{TODAY}'!A7"


def test_past_the_templates_last_row_the_formulas_carry_on(google):
    for plate in ("AAA111", "BBB222", "CCC333"):  # the template has 3 formula rows
        sheets.log_plate(plate)

    out = sheets.log_plate("DDD444")

    cells = google.store["nov"].tabs[TODAY]["cells"]
    assert out["range"] == f"'{TODAY}'!A9"
    assert cells[(9, 0)] == "DDD444"
    assert cells[(9, 1)] == '=IFERROR(VLOOKUP(A9,FLEET!A:B,2,0),"")'
    assert cells[(9, 3)] == ""  # copied NOTES are blanked
    assert (9, 5) not in cells  # the system list isn't copied


def test_rows_are_added_when_the_tab_is_full(google):
    google.store["nov"].tabs["TEMPLATE"]["rowCount"] = 8  # formulas fill rows 6-8 exactly
    for plate in ("AAA111", "BBB222", "CCC333"):
        sheets.log_plate(plate)

    sheets.log_plate("DDD444")

    tab = google.store["nov"].tabs[TODAY]
    assert tab["rowCount"] > 8 and tab["cells"][(9, 0)] == "DDD444"


# ---------------- Undo ----------------
def test_undo_clears_only_the_rego_cell(google):
    sheets.log_plate("AAA111")

    assert sheets.undo_log(f"'{TODAY}'!A6", "AAA111") is True

    cells = google.store["nov"].tabs[TODAY]["cells"]
    assert (6, 0) not in cells and cells[(6, 1)].startswith("=")


def test_undo_leaves_a_cell_holding_another_plate(google):
    sheets.log_plate("AAA111")

    assert sheets.undo_log(f"'{TODAY}'!A6", "ZZZ999") is False
    assert rego(google, "nov", 6) == "AAA111"


# ---------------- Months ----------------
def test_the_newest_spreadsheet_in_the_folder_is_used(google):
    google.add_file("dec", FakeSpreadsheet("Yard audit - December 2026",
                                           {"TEMPLATE": {"cells": template_cells()}}))

    sheets.log_plate("AAA111")

    assert rego(google, "dec", 6) == "AAA111"
    assert TODAY not in google.store["nov"].tabs


def test_an_empty_folder_asks_for_a_month_to_be_started(google):
    google.parents["nov"] = "elsewhere"

    with pytest.raises(sheets.NoSheetYet):
        sheets.log_plate("AAA111")
    assert sheets.status()["name"] is None


def test_starting_a_month_copies_the_template_and_switches_to_it(google, monkeypatch):
    monkeypatch.setattr(sheets, "month_title", lambda: "Yard audit - December 2026")
    sheets.log_plate("AAA111")  # caches November

    out = sheets.start_new_month()
    sheets.log_plate("BBB222")

    new_id = google.created[-1]
    assert out["created"] is True and out["name"] == "Yard audit - December 2026"
    assert out["url"].endswith(f"/d/{new_id}/edit")
    assert rego(google, new_id, 6) == "BBB222"


def test_starting_a_month_twice_makes_one_file(google, monkeypatch):
    monkeypatch.setattr(sheets, "month_title", lambda: "Yard audit - December 2026")
    sheets.start_new_month()

    again = sheets.start_new_month()

    assert again["created"] is False
    assert sum(f.title == "Yard audit - December 2026" for f in google.store.values()) == 1


def test_months_cant_be_started_without_a_template(google, monkeypatch):
    monkeypatch.setattr(sheets, "TEMPLATE_ID", None)

    assert sheets.status()["can_start_month"] is False
    with pytest.raises(sheets.SheetSetupError):
        sheets.start_new_month()


def test_status_names_the_sheet_in_use(google):
    out = sheets.status()

    assert out["mode"] == "sheets" and out["name"] == "Yard audit - November 2026"
    assert out["url"].endswith("/d/nov/edit") and out["tab"] == TODAY


def test_month_title_uses_the_month_name():
    from datetime import datetime

    assert sheets.month_title(datetime(2026, 12, 1)).endswith(" - December 2026")
