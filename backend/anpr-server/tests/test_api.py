import os

import pytest
from google.auth.exceptions import DefaultCredentialsError

import app as app_module
import sheets


# ---------------- Health ----------------
def test_health_reports_status_and_console_logger(client):
    res = client.get("/health")

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["logger"] == "console"
    assert "pipeline_ready" in body


# ---------------- /anpr/detect ----------------
def test_detect_while_model_loading_returns_warming_up(client, plate_jpeg, monkeypatch):
    monkeypatch.setattr(app_module, "pipeline_ready", False)
    monkeypatch.setattr(app_module, "pipeline_error", None)

    res = client.post("/anpr/detect", files={"file": ("plate.jpg", plate_jpeg, "image/jpeg")})

    assert res.json()["status"] == "warming_up"


def test_detect_picks_best_candidate_above_thresholds(client, plate_jpeg, ready_pipeline):
    res = client.post("/anpr/detect", files={"file": ("plate.jpg", plate_jpeg, "image/jpeg")})

    body = res.json()
    assert body["status"] == "ok"
    # The second fake detection has higher OCR confidence but YOLO conf 0.30 < 0.35
    assert body["best"]["ocr"]["text"] == "ABC123"
    assert len(body["detections"]) == 2
    assert ready_pipeline.calls == 1


def test_detect_below_thresholds_asks_for_rescan(client, plate_jpeg, ready_pipeline):
    ready_pipeline.detections = [{"bbox": [0, 0, 5, 5], "yolo_conf": 0.9, "ocr": {"text": "A", "ocr_conf": 0.1}}]

    res = client.post("/anpr/detect", files={"file": ("plate.jpg", plate_jpeg, "image/jpeg")})

    assert res.json()["status"] == "needs_rescan"


def _jpeg(img):
    import cv2
    ok, buf = cv2.imencode(".jpg", img)
    return buf.tobytes()


def test_rescan_says_when_the_photo_is_too_dark(client, ready_pipeline):
    import numpy as np
    ready_pipeline.detections = []

    res = client.post("/anpr/detect", files={"file": ("dark.jpg", _jpeg(np.full((480, 640, 3), 20, np.uint8)), "image/jpeg")})

    assert res.json()["hint"] == "too_dark"


def test_rescan_says_when_the_plate_is_too_far_away(client, plate_jpeg, ready_pipeline):
    ready_pipeline.detections = [{"bbox": [300, 240, 340, 252], "yolo_conf": 0.5, "ocr": None}]  # 40 px of 640

    res = client.post("/anpr/detect", files={"file": ("plate.jpg", plate_jpeg, "image/jpeg")})

    assert res.json()["hint"] == "too_far"


def test_rescan_without_an_obvious_cause_has_no_hint(client, plate_jpeg, ready_pipeline):
    ready_pipeline.detections = [{"bbox": [170, 200, 470, 280], "yolo_conf": 0.9, "ocr": {"text": "A", "ocr_conf": 0.1}}]

    res = client.post("/anpr/detect", files={"file": ("plate.jpg", plate_jpeg, "image/jpeg")})

    assert res.json()["hint"] is None


def test_detect_rejects_non_image_upload(client, ready_pipeline):
    res = client.post("/anpr/detect", files={"file": ("notes.txt", b"not an image", "text/plain")})

    assert res.json() == {"status": "error", "message": "Could not decode image"}
    assert ready_pipeline.calls == 0


# ---------------- /anpr (scan + log) ----------------
def test_scan_and_log_uses_console_logger_without_credentials(client, plate_jpeg, ready_pipeline, capsys):
    res = client.post("/anpr", files={"file": ("plate.jpg", plate_jpeg, "image/jpeg")})

    body = res.json()
    assert body["status"] == "ok"
    assert body["logged_plate"] == "ABC123"
    assert body["sheet"] == "console"
    assert "ABC123" in capsys.readouterr().out


# ---------------- /anpr/log ----------------
def test_log_ignores_a_plate_already_logged_today(client):
    first = client.post("/anpr/log", json={"plate_text": "ABC123"})
    second = client.post("/anpr/log", json={"plate_text": "ABC123"})

    assert first.json()["status"] == "ok"
    assert second.json()["status"] == "duplicate_ignored"


def test_log_requires_plate_text(client):
    res = client.post("/anpr/log", json={"yolo_conf": 0.9})

    assert res.json()["status"] == "error"


def test_log_reports_auth_error_when_sheet_set_but_no_credentials(client, monkeypatch):
    monkeypatch.setattr(sheets, "SPREADSHEET_ID", "dummy-sheet-id")

    def no_credentials():
        raise DefaultCredentialsError("no credentials configured")

    monkeypatch.setattr(sheets, "_get_credentials", no_credentials)

    res = client.post("/anpr/log", json={"plate_text": "ABC123"})

    assert res.status_code == 200
    assert res.json()["status"] == "sheet_auth_error"


# ---------------- Real model (opt-in) ----------------
@pytest.mark.skipif(not os.environ.get("ANPR_INTEGRATION"), reason="set ANPR_INTEGRATION=1 to load the real model")
def test_real_pipeline_runs_end_to_end_on_synthetic_image(plate_jpeg):
    import cv2
    import numpy as np
    from ocr import load_pipeline

    pipeline = load_pipeline()
    img = cv2.imdecode(np.frombuffer(plate_jpeg, np.uint8), cv2.IMREAD_COLOR)

    detections = pipeline.run(img)

    # A drawn plate isn't guaranteed to be detected; this checks the model
    # loads and returns well-formed output.
    assert isinstance(detections, list)
    for d in detections:
        assert set(d) == {"bbox", "yolo_conf", "ocr"}


@pytest.mark.parametrize("bad", ["ABC-123", "<script>", "ABC 123", "ÄBC123", "ABCDEFG", "🚗"])
def test_log_rejects_non_alphanumeric_plate_text(client, bad):
    res = client.post("/anpr/log", json={"plate_text": bad})

    assert res.json()["status"] == "error"


def test_log_uppercases_valid_plate_text(client):
    res = client.post("/anpr/log", json={"plate_text": "abc123"})

    assert res.json()["logged_plate"] == "ABC123"


# ---------------- /anpr/log/undo ----------------
def test_undo_in_console_mode_lets_the_plate_be_logged_again(client):
    client.post("/anpr/log", json={"plate_text": "ABC123"})

    res = client.post("/anpr/log/undo", json={"plate_text": "ABC123", "range": None})

    assert res.json() == {"status": "ok", "undone_plate": "ABC123"}
    # no longer counted as on today's list
    assert client.post("/anpr/log", json={"plate_text": "ABC123"}).json()["status"] == "ok"


@pytest.mark.parametrize("bad_range", [
    "Sheet1!A1:Z999",          # not a daily tab
    "'17/11/2026'!A5:E9",      # more than one cell
    "'17/11/2026'!B12",        # not the REGO column
    "'17/11/2026'!A2",         # the title rows above the list
    "17/11/2026!A12",          # unquoted
])
def test_undo_rejects_anything_but_one_rego_cell(client, monkeypatch, bad_range):
    monkeypatch.setattr(sheets, "SPREADSHEET_ID", "sheet-id")

    res = client.post("/anpr/log/undo", json={"plate_text": "ABC123", "range": bad_range})

    assert res.json()["status"] == "error"


# ---------------- API key ----------------
@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
def test_writing_endpoints_need_the_api_key_when_one_is_set(client, monkeypatch, headers):
    monkeypatch.setattr(app_module, "API_KEY", "s3cret")

    for path in ("/anpr/log", "/anpr/log/undo", "/sheets/new-month"):
        assert client.post(path, json={"plate_text": "ABC123"}, headers=headers).status_code == 401
    assert client.get("/sheets/current", headers=headers).status_code == 401


def test_the_right_api_key_is_accepted(client, monkeypatch):
    monkeypatch.setattr(app_module, "API_KEY", "s3cret")

    res = client.post("/anpr/log", json={"plate_text": "ABC123"}, headers={"X-API-Key": "s3cret"})

    assert res.json()["status"] == "ok"


def test_detect_stays_open_for_the_demo(client, plate_jpeg, ready_pipeline, monkeypatch):
    monkeypatch.setattr(app_module, "API_KEY", "s3cret")

    res = client.post("/anpr/detect", files={"file": ("plate.jpg", plate_jpeg, "image/jpeg")})

    assert res.json()["status"] == "ok"
