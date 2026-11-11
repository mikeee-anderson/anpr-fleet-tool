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
def test_log_ignores_same_plate_within_dedup_window(client):
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
