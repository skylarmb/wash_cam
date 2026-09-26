"""Check camera calibration without real webcam hardware."""

import cv2
import numpy as np
import pytest

from wash_cam.camera import calibrate
from wash_cam.config import Calibration


def textured_frame(width=640, height=480):
    """Build an image with distinctive corners for feature matching."""
    frame = np.full((height, width, 3), 30, dtype=np.uint8)
    rng = np.random.default_rng(seed=1234)
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


def test_estimate_alignment_recovers_a_known_shift():
    """Solve for the transform that undoes a known camera offset."""
    reference = textured_frame()
    # The camera sits 12px right and 7px up from where the reference was taken.
    moved = shift_frame(reference, 12, -7)

    matrix, inlier_count = calibrate.estimate_alignment(moved, reference)

    # Mapping the moved frame back onto the reference undoes that offset.
    assert matrix[0][2] == pytest.approx(-12, abs=1.0)
    assert matrix[1][2] == pytest.approx(7, abs=1.0)
    assert inlier_count > 0


def test_estimate_alignment_rejects_a_featureless_frame():
    """Refuse to align a frame with nothing to match, such as a covered lens."""
    reference = textured_frame()
    blank = np.zeros_like(reference)

    with pytest.raises(RuntimeError, match="features"):
        calibrate.estimate_alignment(blank, reference)


def test_calibrate_saves_the_first_frame_as_the_reference(monkeypatch, tmp_path):
    """Adopt the current view as the reference when none exists yet."""
    frame = textured_frame()
    reference_path = tmp_path / "reference.jpg"
    saved = []
    monkeypatch.setattr(calibrate, "grab_frame", lambda camera_index=0: frame)
    monkeypatch.setattr(calibrate, "REFERENCE_IMAGE_PATH", reference_path)
    monkeypatch.setattr(calibrate, "save_calibration", saved.append)

    message = calibrate.calibrate()

    assert reference_path.is_file()
    assert saved == [
        Calibration(matrix=calibrate.IDENTITY_MATRIX, reference_size=(640, 480))
    ]
    assert "reference" in message


def test_calibrate_aligns_against_an_existing_reference(monkeypatch, tmp_path):
    """Store the transform that maps the current view onto the reference."""
    reference = textured_frame()
    reference_path = tmp_path / "reference.jpg"
    cv2.imwrite(str(reference_path), reference)
    saved = []
    monkeypatch.setattr(
        calibrate, "grab_frame", lambda camera_index=0: shift_frame(reference, 12, -7)
    )
    monkeypatch.setattr(calibrate, "REFERENCE_IMAGE_PATH", reference_path)
    monkeypatch.setattr(calibrate, "save_calibration", saved.append)

    calibrate.calibrate()

    (stored,) = saved
    assert stored.reference_size == (640, 480)
    assert stored.matrix[0][2] == pytest.approx(-12, abs=1.0)
    assert stored.matrix[1][2] == pytest.approx(7, abs=1.0)


def test_calibrate_resets_the_reference_on_request(monkeypatch, tmp_path):
    """Replace an existing reference when the caller asks for a new one."""
    reference_path = tmp_path / "reference.jpg"
    cv2.imwrite(str(reference_path), textured_frame())
    replacement = textured_frame(width=800, height=600)
    saved = []
    monkeypatch.setattr(calibrate, "grab_frame", lambda camera_index=0: replacement)
    monkeypatch.setattr(calibrate, "REFERENCE_IMAGE_PATH", reference_path)
    monkeypatch.setattr(calibrate, "save_calibration", saved.append)

    calibrate.calibrate(set_reference=True)

    assert saved == [
        Calibration(matrix=calibrate.IDENTITY_MATRIX, reference_size=(800, 600))
    ]
    assert cv2.imread(str(reference_path)).shape[:2] == (600, 800)


def test_run_prints_the_summary(monkeypatch, capsys):
    """Report the calibration result on stdout."""
    monkeypatch.setattr(calibrate, "calibrate", lambda: "Aligned.")

    calibrate.run()

    captured = capsys.readouterr()
    assert captured.out == "Aligned.\n"
    assert captured.err == ""
