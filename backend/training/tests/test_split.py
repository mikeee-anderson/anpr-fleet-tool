from pathlib import Path

import pytest

from train_detector import check_no_leakage, group_kfold, photo_group


def test_roboflow_copies_share_their_original_photo_group():
    a = Path("ABC123_png.rf.0a1b2c3d4e5f60718293a4b5c6d7e8f9.jpg")
    b = Path("ABC123_png.rf.f9e8d7c6b5a4938271605f4e3d2c1b0a.jpg")
    c = Path("XYZ9_jpg.rf.1234567890abcdef1234567890abcdef.jpg")

    assert photo_group(a) == photo_group(b) == "ABC123"
    assert photo_group(c) == "XYZ9"


def test_leakage_check_rejects_a_shared_photo_group(tmp_path):
    train = tmp_path / "AAA111_png.rf.aaaa.jpg"
    test = tmp_path / "AAA111_png.rf.bbbb.jpg"
    train.write_bytes(b"one")
    test.write_bytes(b"two")

    with pytest.raises(SystemExit, match="LEAKAGE"):
        check_no_leakage([train], [test])


def test_leakage_check_rejects_identical_files_under_different_names(tmp_path):
    train = tmp_path / "AAA111.jpg"
    test = tmp_path / "BBB222.jpg"
    train.write_bytes(b"same pixels")
    test.write_bytes(b"same pixels")

    with pytest.raises(SystemExit, match="identical"):
        check_no_leakage([train], [test])


def test_group_kfold_puts_every_group_in_exactly_one_fold():
    groups = [f"G{i}" for i in range(21)]

    folds = group_kfold(groups, 5, seed=0)

    assert sorted(g for f in folds for g in f) == sorted(groups)
    assert all(len(f) in (4, 5) for f in folds)
