import numpy as np

from ocr import ANPRPipeline


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
    def __init__(self):
        self.calls = 0
        self.crop_shapes = []

    def readtext(self, crop):
        self.calls += 1
        self.crop_shapes.append(crop.shape[:2])
        return [(None, f"PLATE{self.calls}", 0.9)]


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
