# anpr-server/ocr.py
from pathlib import Path
from ultralytics import YOLO
import easyocr
import numpy as np
import cv2

MAX_OCR_WIDTH = 640  # px; plate crops wider than this are downscaled before OCR


class ANPRPipeline:
    def __init__(
        self,
        model_path: str | Path,
        conf_threshold: float = 0.25,
        ocr_min_conf: float = 0.30,
        max_plates: int = 3,
        gpu: bool = False,
        warm_up: bool = True,
    ):
        self.model_path = str(model_path)
        self.conf_threshold = conf_threshold
        self.ocr_min_conf = ocr_min_conf
        self.max_plates = max_plates

        self.yolo = YOLO(self.model_path)
        self.reader = easyocr.Reader(["en"], gpu=gpu)

        if warm_up:
            # The first YOLO / EasyOCR call is several times slower than the
            # rest (lazy initialisation); pay that at startup, not on a scan.
            self.yolo(np.zeros((640, 640, 3), np.uint8), verbose=False)
            self.reader.readtext(np.zeros((64, 200, 3), np.uint8))

    @staticmethod
    def _clean_text(text: str) -> str:
        return text.replace(" ", "").replace("-", "").upper()

    def run(self, image_bgr):
        results = self.yolo(image_bgr)[0]
        detections = []

        h, w = image_bgr.shape[:2]

        # OCR is the slow step (~seconds per crop on CPU), so only read the
        # most confident boxes
        boxes = [b for b in results.boxes if float(b.conf[0]) >= self.conf_threshold]
        boxes.sort(key=lambda b: float(b.conf[0]), reverse=True)

        for box in boxes[: self.max_plates]:
            conf = float(box.conf[0])

            x1, y1, x2, y2 = map(int, box.xyxy[0])
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w - 1, x2), min(h - 1, y2)

            crop = image_bgr[y1:y2, x1:x2]
            if crop.size == 0:
                continue

            # EasyOCR time grows steeply with crop size: a 1600 px plate from a
            # phone photo took ~50 s on one vCPU vs 0.2 s at 640 px, with the
            # same reads across the training photos
            if crop.shape[1] > MAX_OCR_WIDTH:
                scale = MAX_OCR_WIDTH / crop.shape[1]
                crop = cv2.resize(crop, (MAX_OCR_WIDTH, max(1, int(crop.shape[0] * scale))),
                                  interpolation=cv2.INTER_AREA)

            ocr_results = self.reader.readtext(crop)

            best = None
            for (_bbox, text, ocr_conf) in ocr_results:
                if ocr_conf < self.ocr_min_conf:
                    continue
                cleaned = self._clean_text(text)
                if not cleaned:
                    continue
                if best is None or ocr_conf > best["ocr_conf"]:
                    best = {"text": cleaned, "ocr_conf": float(ocr_conf)}

            detections.append({
                "bbox": [x1, y1, x2, y2],
                "yolo_conf": conf,
                "ocr": best
            })

        return detections


def load_pipeline():

    SERVER_DIR = Path(__file__).resolve().parent          # /app in the container
    MODEL_PATH = SERVER_DIR / "models" / "detector" / "best.pt"

    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Missing model: {MODEL_PATH} (cwd={Path.cwd()})")

    # 0.35 matches MIN_YOLO_CONF in app.py: weaker boxes were OCR'd and then
    # discarded by pick_best anyway
    return ANPRPipeline(model_path=MODEL_PATH, conf_threshold=0.35)