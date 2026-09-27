"""Check camera calibration without real webcam hardware."""

import cv2
import numpy as np
import pytest

from wash_cam.camera import calibrate
from wash_cam.config import Calibration


def textured_frame(width=640, height=480, seed=1234):
    """Build an image with distinctive corners for feature matching."""
    frame = np.full((height, width, 3), 30, dtype=np.uint8)
    rng = np.random.default_rng(seed=seed)
    for _ in range(60):
        x, y = int(rng.integers(20, width - 60)), int(rng.integers(20, height - 60))
        size = int(rng.integers(12, 40))
        color = [int(value) for value in rng.integers(80, 255, 3)]
        if rng.random() < 0.5:
            cv2.rectangle(frame, (x, y), (x + size, y + size), color, -1)
        else:
            cv2.circle(frame, (x, y), size // 2, color, -1)
    return frame


def shift_frame(frame, shift_x, shift_y):
    """Translate a frame, simulating a camera re-seated slightly off position."""
    matrix = np.float32([[1, 0, shift_x], [0, 1, shift_y]])
    height, width = frame.shape[:2]
    return cv2.warpAffine(frame, matrix, (width, height))


@pytest.fixture
def references_dir(monkeypatch, tmp_path):
    """Point calibration at an empty reference directory."""
    path = tmp_path / "references"
    path.mkdir()
    monkeypatch.setattr(calibrate, "REFERENCES_DIR", path)
    return path


@pytest.fixture
def saved(monkeypatch):
    """Capture what calibration would persist."""
    written = []
    monkeypatch.setattr(calibrate, "save_calibration", written.append)
    return written


def test_estimate_alignment_recovers_a_known_shift():
    """Solve for the transform that undoes a known camera offset."""
    reference = textured_frame()
    # The camera sits 12px right and 7px up from where the reference was taken.
    moved = shift_frame(reference, 12, -7)

    matrix, inlier_count = calibrate.estimate_alignment(moved, reference)

    assert matrix[0][2] == pytest.approx(-12, abs=1.0)
    assert matrix[1][2] == pytest.approx(7, abs=1.0)
    assert inlier_count > 0


def test_estimate_alignment_rejects_a_featureless_frame():
    """Refuse to align a frame with nothing to match, such as a covered lens."""
    with pytest.raises(RuntimeError, match="features"):
        calibrate.estimate_alignment(
            np.zeros((480, 640, 3), np.uint8), textured_frame()
        )


def test_best_reference_wins_over_a_mismatched_one(references_dir):
    """Pick the reference that actually corresponds to the view."""
    matching = textured_frame(seed=1)
    unrelated = textured_frame(seed=99)
    cv2.imwrite(str(references_dir / "night.jpg"), matching)
    cv2.imwrite(str(references_dir / "day.jpg"), unrelated)

    name, _, inlier_count = calibrate.align_to_best_reference(
        shift_frame(matching, 9, -4)
    )

    assert name == "night"
    assert inlier_count >= calibrate.MIN_CALIBRATION_INLIERS


def test_alignment_is_rejected_when_no_reference_matches(references_dir):
    """Refuse to store a transform solved from an unrelated reference.

    A daytime reference matched against a night frame yields a handful of
    inliers and a wildly wrong transform, which must not be persisted.
    """
    cv2.imwrite(str(references_dir / "day.jpg"), textured_frame(seed=1))

    with pytest.raises(RuntimeError, match="inliers"):
        calibrate.align_to_best_reference(textured_frame(seed=77))


def test_alignment_requires_a_reference(references_dir):
    """Explain what to do when nothing has been recorded yet."""
    with pytest.raises(RuntimeError, match="set-reference"):
        calibrate.align_to_best_reference(textured_frame())


def test_first_reference_defines_canonical_coordinates(
    references_dir, saved, monkeypatch
):
    """Store identity for the first reference, which sets the canonical frame."""
    frame = textured_frame()
    monkeypatch.setattr(calibrate, "grab_frame", lambda camera_index=0: frame)
    monkeypatch.setattr(calibrate, "load_calibration", lambda: None)
    monkeypatch.setattr(calibrate, "align_frame", lambda f: f)

    message = calibrate.set_reference("day")

    assert (references_dir / "day.jpg").is_file()
    assert saved == [
        Calibration(matrix=calibrate.IDENTITY_MATRIX, reference_size=(640, 480))
    ]
    assert "canonical" in message


def test_later_reference_is_stored_in_canonical_coordinates(
    references_dir, saved, monkeypatch
):
    """Warp a new reference through the existing calibration before storing it.

    Otherwise a reference recorded from a re-seated camera would disagree with
    the others about where the crop box points.
    """
    frame = textured_frame()
    existing = Calibration(
        matrix=((1.0, 0.0, -5.0), (0.0, 1.0, 3.0)), reference_size=(640, 480)
    )
    monkeypatch.setattr(calibrate, "grab_frame", lambda camera_index=0: frame)
    monkeypatch.setattr(calibrate, "load_calibration", lambda: existing)
    aligned = []
    monkeypatch.setattr(
        calibrate, "align_frame", lambda f: aligned.append(f) or shift_frame(f, -5, 3)
    )

    calibrate.set_reference("night")

    assert (references_dir / "night.jpg").is_file()
    assert aligned == [frame]
    # The existing calibration still defines canonical, so it is left alone.
    assert saved == []


def test_calibrate_stores_the_best_alignment(references_dir, saved, monkeypatch):
    """Persist the transform mapping the current view onto the reference."""
    reference = textured_frame()
    cv2.imwrite(str(references_dir / "night.jpg"), reference)
    monkeypatch.setattr(
        calibrate, "grab_frame", lambda camera_index=0: shift_frame(reference, 12, -7)
    )

    calibrate.calibrate()

    (stored,) = saved
    assert stored.reference_size == (640, 480)
    assert stored.matrix[0][2] == pytest.approx(-12, abs=1.5)
    assert stored.matrix[1][2] == pytest.approx(7, abs=1.5)


def test_run_records_a_named_reference(monkeypatch, capsys):
    """Route --set-reference to recording rather than aligning."""
    monkeypatch.setattr(calibrate, "set_reference", lambda name: f"Recorded {name}.")

    calibrate.run(reference_name="night")

    assert capsys.readouterr().out == "Recorded night.\n"


def test_run_calibrates_by_default(monkeypatch, capsys):
    """Align against stored references when no reference name is given."""
    monkeypatch.setattr(calibrate, "calibrate", lambda: "Aligned.")

    calibrate.run()

    assert capsys.readouterr().out == "Aligned.\n"
