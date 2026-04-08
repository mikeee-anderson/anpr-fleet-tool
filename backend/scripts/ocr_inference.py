from pathlib import Path
from ultralytics import YOLO
import cv2
import easyocr

CONF_THRESHOLD = 0.25

BASE_DIR = Path(__file__).resolve().parents[1]  # -> /Users/mikeanderson/ANPR

MODEL_PATH = BASE_DIR / "models" / "detector" / "best.pt"
IMAGE_PATH = BASE_DIR / "data" / "inference_images" / "bmw.jpg"

print("BASE_DIR:", BASE_DIR)
print("MODEL_PATH:", MODEL_PATH)
print("MODEL EXISTS:", MODEL_PATH.exists())
print("IMAGE_PATH:", IMAGE_PATH)
print("IMAGE EXISTS:", IMAGE_PATH.exists())

yolo = YOLO(str(MODEL_PATH))

reader = easyocr.Reader(
    ['en'],
    gpu=False # we don't have a CUDA GPU
)

# ------------------------------
# LOAD IMAGE
# ------------------------------
image = cv2.imread(str(IMAGE_PATH))
assert image is not None, "Image not found"

# ------------------------------
# YOLO DETECTION
# ------------------------------
results = yolo(image)[0]

for box in results.boxes:
    conf = float(box.conf[0])
    if conf < CONF_THRESHOLD:
        continue

    # Find bounding box coordinates
    x1, y1, x2, y2 = map(int, box.xyxy[0])

    plate_crop = image[y1:y2, x1:x2]

    # ------------------------------
    # OCR
    # ------------------------------
    ocr_results = reader.readtext(plate_crop)

    for (bbox, text, ocr_conf) in ocr_results:
        clean_text = text.replace(" ", "").upper()

        print(f"Detected plate: {clean_text}")
        print(f"YOLO confidence: {conf:.2f}, OCR confidence: {ocr_conf:.2f}")

        # Draw Results
        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(
            image,
            clean_text,
            (x1, y1 - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (0, 255, 0),
            2
        )

# ------------------------------
# RESULT
# ------------------------------
cv2.imshow("ANPR Result", image)
cv2.waitKey(0)
cv2.destroyAllWindows()


