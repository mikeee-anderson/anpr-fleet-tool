"""
End-to-end check on the held-out photos: detector + deskew + OCR, as the API runs it.

    python backend/training/eval_end_to_end.py --report runs/v2/report.json \
        --raw-dir backend/data/raw --weights old=backend/anpr-server/models/detector/best.pt \
        --weights new=runs/v2/final_pool/weights/last.pt

Ground truth is the photo filename (e.g. ABC123.png -> ABC123). Only the test
groups from split.json are used. Note: a model trained before this split
(the current production model) saw these photos, so its score here is
optimistic; a model trained on the pool alone never did.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1] / "anpr-server"
sys.path.insert(0, str(SERVER_DIR))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", type=Path, required=True, help="split.json or report.json from train_detector.py")
    ap.add_argument("--raw-dir", type=Path, required=True, help="original photos named <PLATE>.<ext>")
    ap.add_argument("--weights", action="append", required=True, help="name=path, repeatable")
    args = ap.parse_args()

    import cv2
    from ocr import ANPRPipeline

    data = json.loads(args.report.read_text())
    test_groups = data.get("split", data)["test_groups"]
    photos = {p.stem: p for p in args.raw_dir.iterdir() if p.stem in test_groups}

    results = {}
    reader = None
    for spec in args.weights:
        name, path = spec.split("=", 1)
        pipeline = ANPRPipeline(model_path=path, conf_threshold=0.35, warm_up=False)
        if reader is None:
            reader = pipeline.reader
        pipeline.reader = reader  # same OCR for every model: only the detector differs

        found = exact = 0
        for truth, photo in sorted(photos.items()):
            detections = pipeline.run(cv2.imread(str(photo)))
            reads = [d["ocr"] for d in detections if d["ocr"]]
            found += bool(detections)
            best = max(reads, key=lambda r: r["ocr_conf"])["text"] if reads else ""
            exact += best == truth
        results[name] = {"photos": len(photos), "plate_found": found, "exact_read": exact}
        print(f"{name}: plate found {found}/{len(photos)}, exact read {exact}/{len(photos)}")

    print(json.dumps(results))


if __name__ == "__main__":
    main()
