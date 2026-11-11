"""
Shared fixtures. Every test image is generated here at runtime: the suite
contains no real number plates or photos.
"""
import os

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

# Force console logging before the app (and sheets.py) is imported.
os.environ.pop("SHEET_ID", None)

import app as app_module  # noqa: E402
import sheets  # noqa: E402


def make_synthetic_plate() -> bytes:
    """A grey background with a white 'ABC123' plate, encoded as JPEG."""
    img = np.full((480, 640, 3), 90, np.uint8)
    cv2.rectangle(img, (170, 200), (470, 280), (255, 255, 255), -1)
    cv2.rectangle(img, (170, 200), (470, 280), (0, 0, 0), 3)
    cv2.putText(img, "ABC123", (190, 265), cv2.FONT_HERSHEY_SIMPLEX, 2.2, (0, 0, 0), 6)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


class FakePipeline:
    """Stands in for YOLOv8 + EasyOCR so API tests run in milliseconds."""

    def __init__(self, detections):
        self.detections = detections
        self.calls = 0

    def run(self, image_bgr):
        self.calls += 1
        return self.detections


@pytest.fixture
def plate_jpeg() -> bytes:
    return make_synthetic_plate()


@pytest.fixture
def client(monkeypatch):
    """TestClient without the startup hook, so the real model never loads."""
    monkeypatch.setattr(sheets, "SPREADSHEET_ID", None)
    sheets._recent.clear()
    return TestClient(app_module.app, raise_server_exceptions=False)


@pytest.fixture
def ready_pipeline(monkeypatch):
    """Installs a fake pipeline that 'reads' ABC123 with high confidence."""
    fake = FakePipeline([
        {"bbox": [170, 200, 470, 280], "yolo_conf": 0.91, "ocr": {"text": "ABC123", "ocr_conf": 0.88}},
        {"bbox": [10, 10, 40, 30], "yolo_conf": 0.30, "ocr": {"text": "XX", "ocr_conf": 0.95}},
    ])
    monkeypatch.setattr(app_module, "pipeline", fake)
    monkeypatch.setattr(app_module, "pipeline_ready", True)
    monkeypatch.setattr(app_module, "pipeline_error", None)
    return fake
