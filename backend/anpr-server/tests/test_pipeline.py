import cv2
import numpy as np
import pytest

from ocr import PLATE_CHARS, ANPRPipeline, pick_plate_text, plate_tilt, rotate_level


def box(x, y, w, h):
    """EasyOCR-style corner list for an axis-aligned text box."""
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def tilted_plate_crop(angle):
    """A synthetic white plate rotated by `angle` degrees, cropped tight as YOLO would."""
    canvas = np.full((900, 900, 3), 70, np.uint8)
    canvas[370:530, 190:710] = 235
    rotated = cv2.warpAffine(canvas, cv2.getRotationMatrix2D((450, 450), angle, 1.0), (900, 900),
                             borderValue=(70, 70, 70))
    ys, xs = np.where((rotated > 150).all(axis=2))
    return rotated[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


class FakeBox:
    def __init__(self, conf, xyxy):
        self.conf = [conf]
        self.xyxy = [xyxy]


class FakeYolo:
    def __init__(self, boxes):
        self.boxes = boxes

    def __call__(self, image, **kwargs):
        return [self]


class FakeReader:
    def __init__(self, confidences=(0.9,)):
        self.calls = 0
        self.crop_shapes = []
        self.confidences = confidences  # per call; the last value repeats

    def readtext(self, crop, **kwargs):
        self.calls += 1
        self.crop_shapes.append(crop.shape[:2])
        self.kwargs = kwargs
        conf = self.confidences[min(self.calls, len(self.confidences)) - 1]
        return [(box(0, 0, 100, 30), f"PLATE{self.calls}", conf)]


def make_pipeline(boxes, max_plates=3, conf_threshold=0.35):
    """ANPRPipeline with fake models, skipping the real weights and warm-up."""
    pipeline = ANPRPipeline.__new__(ANPRPipeline)
    pipeline.conf_threshold = conf_threshold
    pipeline.ocr_min_conf = 0.30
    pipeline.max_plates = max_plates
    pipeline.yolo = FakeYolo(boxes)
    pipeline.reader = FakeReader()
    return pipeline


def test_ocr_runs_only_on_most_confident_boxes_above_threshold():
    boxes = [
        FakeBox(0.40, [0, 0, 50, 20]),
        FakeBox(0.20, [0, 0, 50, 20]),  # below threshold: never read
        FakeBox(0.95, [0, 0, 50, 20]),
        FakeBox(0.60, [0, 0, 50, 20]),
        FakeBox(0.80, [0, 0, 50, 20]),
    ]
    pipeline = make_pipeline(boxes)

    detections = pipeline.run(np.zeros((100, 100, 3), np.uint8))

    assert pipeline.reader.calls == 3
    assert [d["yolo_conf"] for d in detections] == [0.95, 0.80, 0.60]


def test_no_boxes_means_no_ocr():
    pipeline = make_pipeline([FakeBox(0.10, [0, 0, 50, 20])])

    assert pipeline.run(np.zeros((100, 100, 3), np.uint8)) == []
    assert pipeline.reader.calls == 0


def test_wide_plate_crop_is_downscaled_before_ocr():
    # A phone-photo-sized plate: 1600 x 1000 px
    pipeline = make_pipeline([FakeBox(0.9, [0, 0, 1600, 1000])])

    pipeline.run(np.zeros((1200, 2000, 3), np.uint8))

    assert pipeline.reader.crop_shapes == [(400, 640)]


def test_ocr_is_restricted_to_plate_characters():
    pipeline = make_pipeline([FakeBox(0.9, [0, 0, 50, 20])])

    pipeline.run(np.zeros((100, 100, 3), np.uint8))

    assert pipeline.reader.kwargs["allowlist"] == PLATE_CHARS


@pytest.mark.parametrize("angle", [-70, -45, -20, 20, 45, 70])
def test_plate_tilt_is_measured_and_undone(angle):
    crop = tilted_plate_crop(angle)

    tilt = plate_tilt(crop)
    assert tilt == pytest.approx(-angle, abs=2)

    # after rotating level, the plate (grey 235) is wider than tall again;
    # opening drops thin blended lines where the canvas edges meet the fill
    level = rotate_level(crop, tilt)
    plate = cv2.inRange(level, (225, 225, 225), (245, 245, 245))
    plate = cv2.morphologyEx(plate, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    ys, xs = np.nonzero(plate)
    assert (xs.max() - xs.min()) > 2.5 * (ys.max() - ys.min())


def test_level_plate_is_not_rotated():
    assert plate_tilt(tilted_plate_crop(0)) is None


def steep_plate_pipeline(confidences):
    canvas = np.zeros((900, 900, 3), np.uint8)
    crop = tilted_plate_crop(60)
    canvas[: crop.shape[0], : crop.shape[1]] = crop
    pipeline = make_pipeline([FakeBox(0.9, [0, 0, crop.shape[1], crop.shape[0]])])
    pipeline.reader = FakeReader(confidences)
    return pipeline, canvas


def test_steep_tilt_stops_after_a_confident_read():
    pipeline, canvas = steep_plate_pipeline([0.95])

    pipeline.run(canvas)

    assert pipeline.reader.calls == 1


def test_steep_tilt_tries_three_angles_and_keeps_the_most_confident():
    pipeline, canvas = steep_plate_pipeline([0.40, 0.80, 0.60])

    detections = pipeline.run(canvas)

    assert pipeline.reader.calls == 3
    assert detections[0]["ocr"]["text"] == "PLATE2"
    assert detections[0]["ocr"]["ocr_conf"] == pytest.approx(0.80)


def clean(text):
    return text.replace(" ", "").upper()


def test_tall_plate_text_beats_more_confident_surround_text():
    results = [
        (box(10, 120, 200, 18), "CITY MOTORS", 0.97),  # dealer surround: small, very confident
        (box(20, 40, 260, 60), "ABC123", 0.71),  # plate characters: tall
    ]

    assert pick_plate_text(results, 0.3, clean) == {"text": "ABC123", "ocr_conf": 0.71}


def test_split_plate_pieces_are_joined_left_to_right():
    results = [
        (box(160, 42, 120, 58), "123", 0.9),
        (box(20, 40, 125, 60), "ABC", 0.6),
        (box(30, 120, 200, 15), "PH0800", 0.95),  # surround line below
    ]

    read = pick_plate_text(results, 0.3, clean)

    assert read["text"] == "ABC123"
    assert read["ocr_conf"] == pytest.approx(0.75)  # length-weighted mean


def test_over_long_join_falls_back_to_tallest_piece():
    results = [
        (box(0, 40, 150, 60), "ABCDE", 0.8),
        (box(160, 41, 150, 58), "12345", 0.9),
    ]

    assert pick_plate_text(results, 0.3, clean) == {"text": "ABCDE", "ocr_conf": 0.8}


def test_low_confidence_pieces_are_ignored():
    assert pick_plate_text([(box(0, 0, 100, 60), "ABC123", 0.1)], 0.3, clean) is None
