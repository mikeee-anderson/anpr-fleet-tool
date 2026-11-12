# anpr-server/ocr.py
from pathlib import Path
from ultralytics import YOLO
import easyocr
import numpy as np
import cv2

MAX_OCR_WIDTH = 640  # px; plate crops wider than this are downscaled before OCR
PLATE_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
MIN_DESKEW_DEGREES = 3  # smaller tilts read fine as they are
STEEP_TILT_DEGREES = 15  # from here on, read at three angles around the estimate
DESKEW_SPREAD = 5  # degrees either side of the estimate tried for steep tilts
CONFIDENT_READ = 0.9  # OCR confidence that ends the search over angles early


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

            # OCR reads level text; a tilted plate reads as noise, so straighten
            # it first. The tilt estimate can be a few degrees off and EasyOCR is
            # sensitive to that, so strongly tilted plates are also read at
            # +/- DESKEW_SPREAD, keeping the most confident read. Each read
            # costs seconds on CPU, so stop as soon as one is confident.
            tilt = plate_tilt(crop)
            if tilt is None:
                angles = []
            elif abs(tilt) < STEEP_TILT_DEGREES:
                angles = [tilt]
            else:
                angles = [tilt, tilt - DESKEW_SPREAD, tilt + DESKEW_SPREAD]

            best = None
            for angle in angles:
                read = self._read(rotate_level(crop, angle))
                if read and (best is None or read["ocr_conf"] > best["ocr_conf"]):
                    best = read
                if best and best["ocr_conf"] >= CONFIDENT_READ:
                    break
            if best is None:
                best = self._read(crop)

            detections.append({
                "bbox": [x1, y1, x2, y2],
                "yolo_conf": conf,
                "ocr": best
            })

        return detections

    def _read(self, crop):
        """Best OCR reading of a plate crop, or None."""
        # EasyOCR time grows steeply with crop size: a 1600 px plate from a
        # phone photo took ~50 s on one vCPU vs 0.2 s at 640 px, with the
        # same reads across the training photos
        if crop.shape[1] > MAX_OCR_WIDTH:
            scale = MAX_OCR_WIDTH / crop.shape[1]
            crop = cv2.resize(crop, (MAX_OCR_WIDTH, max(1, int(crop.shape[0] * scale))),
                              interpolation=cv2.INTER_AREA)

        # NZ plates are A-Z and 0-9 only; this stops reads like "@" for "Q"
        ocr_results = self.reader.readtext(crop, allowlist=PLATE_CHARS)

        best = None
        for (_bbox, text, ocr_conf) in ocr_results:
            if ocr_conf < self.ocr_min_conf:
                continue
            cleaned = self._clean_text(text)
            if not cleaned:
                continue
            if best is None or ocr_conf > best["ocr_conf"]:
                best = {"text": cleaned, "ocr_conf": float(ocr_conf)}
        return best


def plate_tilt(crop):
    """
    Tilt of the plate in a crop, in degrees within [-90, 90), or None.

    The plate is the largest bright rectangle in the crop: threshold it and
    take the largest contour's minimum-area rectangle. Returns None when no
    plate-shaped rectangle is found or the tilt is already small.
    """
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(cv2.GaussianBlur(gray, (5, 5), 0), 0, 255,
                            cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) < 0.2 * crop.shape[0] * crop.shape[1]:
        return None

    rect = cv2.minAreaRect(contour)
    _, (rw, rh), _ = rect
    long_side, short_side = max(rw, rh), min(rw, rh)
    if short_side < 1 or not 1.5 <= long_side / short_side <= 7:
        return None  # not plate-shaped

    # Tilt of the rectangle's long edge, from its corners: minAreaRect's own
    # angle convention differs between OpenCV versions
    p = cv2.boxPoints(rect)
    edge = p[1] - p[0] if np.linalg.norm(p[1] - p[0]) >= np.linalg.norm(p[2] - p[1]) else p[2] - p[1]
    tilt = float(np.degrees(np.arctan2(edge[1], edge[0])))
    tilt = (tilt + 90) % 180 - 90  # a plate's direction, not its heading
    return tilt if abs(tilt) >= MIN_DESKEW_DEGREES else None


def rotate_level(crop, tilt):
    """
    Rotate the whole crop by +tilt (y points down, so this undoes the tilt)
    onto a canvas big enough to keep every corner. Cropping tight to the plate
    rectangle clipped the characters and hurt OCR.
    """
    h, w = crop.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), tilt, 1.0)
    cos, sin = abs(matrix[0, 0]), abs(matrix[0, 1])
    new_w, new_h = int(h * sin + w * cos), int(h * cos + w * sin)
    matrix[0, 2] += new_w / 2 - w / 2
    matrix[1, 2] += new_h / 2 - h / 2
    return cv2.warpAffine(crop, matrix, (new_w, new_h), flags=cv2.INTER_LINEAR,
                          borderValue=(255, 255, 255))


def load_pipeline():

    SERVER_DIR = Path(__file__).resolve().parent          # /app in the container
    MODEL_PATH = SERVER_DIR / "models" / "detector" / "best.pt"

    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Missing model: {MODEL_PATH} (cwd={Path.cwd()})")

    # 0.35 matches MIN_YOLO_CONF in app.py: weaker boxes were OCR'd and then
    # discarded by pick_best anyway
    return ANPRPipeline(model_path=MODEL_PATH, conf_threshold=0.35)
