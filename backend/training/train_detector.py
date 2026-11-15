"""
Retrain the plate detector without test-set leakage.

    python backend/training/train_detector.py --data-dir backend/data/plates/train --out runs/retrain

Steps
1. Group images by their ORIGINAL photo. Roboflow exports each photo several
   times ("ABC123_png.rf.<hash>.jpg"); every copy of a photo shares a group,
   so near-identical images never land on both sides of a split.
2. Hold out a test set of whole groups first, write it to split.json, and
   check for leakage (shared groups or byte-identical images). The test set
   is used once, at the end.
   Training runs use val=False and keep the last epoch, so no evaluation set
   ever chooses which weights are kept.
3. Grouped k-fold cross-validation on the remaining groups compares
   training configurations. CV only picks the configuration; it never sees
   the test set.
4. Train the chosen configuration on all non-test groups and evaluate it on
   the test set: that is the reported score.
5. Optionally (--refit-all) train once more on every group to get the model
   to deploy; its score is the step-4 score, since it has no unseen data.

The photos are real plates and stay out of git; only this script is committed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shutil
from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}

# Training configurations compared by cross-validation
CONFIGS = {
    # What the original model was trained with
    "original": dict(degrees=0.0, perspective=0.0, shear=0.0, fliplr=0.5),
    # Angled plates, and no left-right flips: a mirrored plate never occurs
    "angles_no_flip": dict(degrees=15.0, perspective=0.0005, shear=5.0, fliplr=0.0),
}


def photo_group(image: Path) -> str:
    """The original photo an image came from: strip Roboflow's '_png.rf.<hash>' suffix."""
    stem = re.sub(r"\.rf\.[0-9a-f]+$", "", image.stem)
    return re.sub(r"_(png|jpe?g|heic)$", "", stem, flags=re.IGNORECASE)


def collect(data_dir: Path) -> dict[str, list[Path]]:
    images = sorted(p for p in (data_dir / "images").iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    groups: dict[str, list[Path]] = {}
    for image in images:
        label = data_dir / "labels" / f"{image.stem}.txt"
        if not label.exists():
            raise SystemExit(f"Missing label for {image.name}")
        groups.setdefault(photo_group(image), []).append(image)
    return groups


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_no_leakage(train: list[Path], test: list[Path]) -> None:
    """Fail loudly if any photo group or identical file is on both sides."""
    shared_groups = {photo_group(p) for p in train} & {photo_group(p) for p in test}
    if shared_groups:
        raise SystemExit(f"LEAKAGE: groups in both train and test: {sorted(shared_groups)}")
    shared_files = {file_hash(p) for p in train} & {file_hash(p) for p in test}
    if shared_files:
        raise SystemExit(f"LEAKAGE: {len(shared_files)} identical image(s) in both train and test")


def group_kfold(groups: list[str], k: int, seed: int) -> list[list[str]]:
    shuffled = groups[:]
    random.Random(seed).shuffle(shuffled)
    return [shuffled[i::k] for i in range(k)]


def write_dataset(work: Path, name: str, train: list[Path], val: list[Path]) -> Path:
    """An Ultralytics dataset defined by image lists (labels are found next to images/)."""
    work.mkdir(parents=True, exist_ok=True)
    (work / f"{name}_train.txt").write_text("\n".join(str(p.resolve()) for p in train) + "\n")
    (work / f"{name}_val.txt").write_text("\n".join(str(p.resolve()) for p in val) + "\n")
    yaml = work / f"{name}.yaml"
    yaml.write_text(
        f"train: {(work / f'{name}_train.txt').resolve()}\n"
        f"val: {(work / f'{name}_val.txt').resolve()}\n"
        "nc: 1\nnames: ['Number-Plates']\n"
    )
    return yaml


def train(yaml: Path, out: Path, name: str, config: dict, args) -> Path:
    """
    Train for a fixed number of epochs and return the LAST weights.

    val=False: no evaluation during training, so no data set (least of all the
    test set) picks the "best" epoch. Ultralytics' best.pt is chosen on the
    val list, which would let that list leak into the model.
    """
    from ultralytics import YOLO

    YOLO(args.base_weights).train(
        data=str(yaml), epochs=args.epochs, imgsz=args.imgsz, batch=args.batch, device=args.device,
        seed=args.seed, deterministic=True, project=str(out.resolve()), name=name, exist_ok=True,
        val=False, plots=False, verbose=False, **config,
    )
    return out / name / "weights" / "last.pt"


def evaluate(weights: Path, yaml: Path, args) -> dict:
    """Score trained weights on the yaml's val list (images the model never trained on)."""
    from ultralytics import YOLO

    m = YOLO(str(weights)).val(data=str(yaml), split="val", imgsz=args.imgsz, device=args.device,
                              plots=False, verbose=False)
    return {
        "mAP50": round(float(m.box.map50), 4),
        "mAP50_95": round(float(m.box.map), 4),
        "precision": round(float(m.box.mp), 4),
        "recall": round(float(m.box.mr), 4),
        "weights": str(weights),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, required=True, help="folder with images/ and labels/")
    ap.add_argument("--out", type=Path, default=Path("runs/retrain"))
    ap.add_argument("--base-weights", default="yolov8n.pt")
    ap.add_argument("--test-frac", type=float, default=0.2)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--epochs", type=int, default=50)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--device", default="mps")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--refit-all", action="store_true", help="also train the deployable model on every group")
    args = ap.parse_args()

    groups = collect(args.data_dir)
    names = sorted(groups)
    rng = random.Random(args.seed)
    rng.shuffle(names)
    n_test = max(1, round(len(names) * args.test_frac))
    test_groups, pool_groups = sorted(names[:n_test]), sorted(names[n_test:])

    test_imgs = [p for g in test_groups for p in groups[g]]
    pool_imgs = [p for g in pool_groups for p in groups[g]]
    check_no_leakage(pool_imgs, test_imgs)

    args.out.mkdir(parents=True, exist_ok=True)
    work = args.out / "datasets"
    split = {"seed": args.seed, "test_groups": test_groups, "pool_groups": pool_groups,
             "n_test_images": len(test_imgs), "n_pool_images": len(pool_imgs)}
    (args.out / "split.json").write_text(json.dumps(split, indent=2))
    print(f"{len(names)} photos: {len(pool_groups)} for CV/training, {len(test_groups)} held out for test")

    # Cross-validation on the pool only
    folds = group_kfold(pool_groups, args.folds, args.seed)
    cv = {}
    for cfg_name, cfg in CONFIGS.items():
        scores = []
        for i, val_groups in enumerate(folds):
            tr = [p for g in pool_groups if g not in val_groups for p in groups[g]]
            va = [p for g in val_groups for p in groups[g]]
            check_no_leakage(tr, va)
            yaml = write_dataset(work, f"{cfg_name}_fold{i}", tr, va)
            weights = train(yaml, args.out / "cv", f"{cfg_name}_fold{i}", cfg, args)
            scores.append(evaluate(weights, yaml, args))
            print(f"[cv] {cfg_name} fold {i}: {scores[-1]}")
        cv[cfg_name] = {
            "folds": scores,
            "mean_mAP50_95": round(sum(s["mAP50_95"] for s in scores) / len(scores), 4),
            "mean_mAP50": round(sum(s["mAP50"] for s in scores) / len(scores), 4),
        }
    best_cfg = max(cv, key=lambda c: cv[c]["mean_mAP50_95"])
    print(f"[cv] chosen configuration: {best_cfg}")

    # Train on the whole pool with the chosen configuration; test exactly once
    yaml = write_dataset(work, "final", pool_imgs, test_imgs)
    weights = train(yaml, args.out, "final_pool", CONFIGS[best_cfg], args)
    test = evaluate(weights, yaml, args)
    print(f"[test] held-out score ({len(test_groups)} photos): {test}")

    report = {"split": split, "cv": cv, "chosen": best_cfg, "test": test}

    if args.refit_all:
        everything = pool_imgs + test_imgs
        yaml = write_dataset(work, "refit_all", everything, everything)  # no evaluation: nothing unseen left
        refit = train(yaml, args.out, "refit_all", CONFIGS[best_cfg], args)
        report["refit_all_weights"] = str(refit)
        print(f"[refit] deployable model trained on all {len(names)} photos: {refit}")

    (args.out / "report.json").write_text(json.dumps(report, indent=2))
    shutil.copy(Path(test["weights"]), args.out / "candidate_best.pt")
    print(f"Report: {args.out / 'report.json'}")


if __name__ == "__main__":
    main()
