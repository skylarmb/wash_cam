"""Check camera capture behavior without real webcam hardware."""

import cv2
import numpy as np
import pytest

from wash_cam import camera
from wash_cam.camera import capture
from wash_cam.config import CAMERA_CROP_BOX, Calibration


class FakeVideoCapture:
    """Stand in for cv2.VideoCapture so tests do not need a webcam."""

    def __init__(self, opened, read_ok, frame):
        self.opened = opened
        self.read_ok = read_ok
        self.frame = frame
        self.released = False

    def isOpened(self):
        return self.opened

    def read(self):
        return self.read_ok, self.frame

    def release(self):
        self.released = True


@pytest.fixture(autouse=True)
def uncalibrated(monkeypatch):
    """Default to an uncalibrated camera so tests ignore any local config.yaml."""
    monkeypatch.setattr(capture, "load_calibration", lambda: None)


def fake_frame():
    """Build a frame large enough to contain the crop box."""
    _, _, right, bottom = CAMERA_CROP_BOX
    return np.zeros((bottom + 10, right + 10, 3), dtype=np.uint8)


@pytest.mark.parametrize(
    ("crop_box", "reason"),
    [
        ((390, 250, 2200, 900), "wider than the frame"),
        ((390, 250, 1660, 1200), "taller than the frame"),
        ((390, -40, 1660, 590), "negative top"),
        ((-10, 250, 1660, 590), "negative left"),
        ((1660, 250, 390, 590), "left past right"),
        ((390, 590, 1660, 250), "top past bottom"),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_take_picture_rejects_out_of_bounds_crop_box(
    crop_box, reason, monkeypatch, tmp_path
):
    """Raise a clear error instead of silently saving a mis-sized crop."""
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    fake_capture = FakeVideoCapture(opened=True, read_ok=True, frame=frame)
    monkeypatch.setattr(cv2, "VideoCapture", lambda index: fake_capture)
    monkeypatch.setattr(capture, "IMAGES_DIR", tmp_path)
    monkeypatch.setattr(capture, "CAMERA_CROP_BOX", crop_box)

    with pytest.raises(ValueError, match="crop box"):
        capture.take_picture()

    assert fake_capture.released
    assert list(tmp_path.iterdir()) == []


def test_take_picture_saves_cropped_frame(monkeypatch, tmp_path):
    """Save the frame cropped to CAMERA_CROP_BOX and return its filename."""
    fake_capture = FakeVideoCapture(opened=True, read_ok=True, frame=fake_frame())
    monkeypatch.setattr(cv2, "VideoCapture", lambda index: fake_capture)
    monkeypatch.setattr(capture, "IMAGES_DIR", tmp_path)

    filename = capture.take_picture()

    saved_image = cv2.imread(str(tmp_path / filename))
    left, top, right, bottom = CAMERA_CROP_BOX
    assert saved_image.shape == (bottom - top, right - left, 3)
    assert fake_capture.released


def spy_video_capture(requested_indexes):
    """Return a fake cv2.VideoCapture constructor that records the requested index."""

    def open_capture(index):
        requested_indexes.append(index)
        return FakeVideoCapture(opened=True, read_ok=True, frame=fake_frame())

    return open_capture


def test_take_picture_defaults_to_camera_index_zero(monkeypatch, tmp_path):
    """Open camera index 0 when no index is given."""
    requested_indexes = []
    monkeypatch.setattr(cv2, "VideoCapture", spy_video_capture(requested_indexes))
    monkeypatch.setattr(capture, "IMAGES_DIR", tmp_path)

    capture.take_picture()

    assert requested_indexes == [0]


def test_take_picture_uses_given_camera_index(monkeypatch, tmp_path):
    """Open the requested camera index."""
    requested_indexes = []
    monkeypatch.setattr(cv2, "VideoCapture", spy_video_capture(requested_indexes))
    monkeypatch.setattr(capture, "IMAGES_DIR", tmp_path)

    capture.take_picture(camera_index=2)

    assert requested_indexes == [2]


def test_take_picture_raises_when_camera_not_opened(monkeypatch, tmp_path):
    """Raise RuntimeError and release the camera when it fails to open."""
    fake_capture = FakeVideoCapture(opened=False, read_ok=True, frame=None)
    monkeypatch.setattr(cv2, "VideoCapture", lambda index: fake_capture)
    monkeypatch.setattr(capture, "IMAGES_DIR", tmp_path)

    with pytest.raises(RuntimeError, match="open"):
        capture.take_picture()

    assert fake_capture.released
    assert list(tmp_path.iterdir()) == []


def test_take_picture_raises_when_frame_not_read(monkeypatch, tmp_path):
    """Raise RuntimeError and release the camera when no frame is read."""
    fake_capture = FakeVideoCapture(opened=True, read_ok=False, frame=None)
    monkeypatch.setattr(cv2, "VideoCapture", lambda index: fake_capture)
    monkeypatch.setattr(capture, "IMAGES_DIR", tmp_path)

    with pytest.raises(RuntimeError, match="read"):
        capture.take_picture()

    assert fake_capture.released
    assert list(tmp_path.iterdir()) == []


def test_take_picture_applies_the_stored_calibration(monkeypatch, tmp_path):
    """Warp into reference coordinates before cropping, undoing a camera offset."""
    left, top, right, bottom = CAMERA_CROP_BOX
    reference_size = (right + 10, bottom + 10)
    # A marker placed where the camera's offset put it, not where it belongs.
    frame = np.zeros((reference_size[1], reference_size[0], 3), dtype=np.uint8)
    frame[top + 25 : top + 35, left + 45 : left + 55] = 255
    fake_capture = FakeVideoCapture(opened=True, read_ok=True, frame=frame)
    monkeypatch.setattr(cv2, "VideoCapture", lambda index: fake_capture)
    monkeypatch.setattr(capture, "IMAGES_DIR", tmp_path)
    # Alignment shifts the frame 20px left and 10px up.
    monkeypatch.setattr(
        capture,
        "load_calibration",
        lambda: Calibration(
            matrix=((1.0, 0.0, -20.0), (0.0, 1.0, -10.0)),
            reference_size=reference_size,
        ),
    )

    filename = capture.take_picture()

    saved_image = cv2.imread(str(tmp_path / filename))
    assert saved_image.shape == (bottom - top, right - left, 3)
    # The marker moved with the warp, landing 20px left and 10px up in the crop.
    brightest = np.unravel_index(saved_image[:, :, 0].argmax(), saved_image.shape[:2])
    assert brightest == (25 - 10, 45 - 20)


def test_run_prints_saved_filename(monkeypatch, capsys):
    """Print the filename returned by take_picture."""
    monkeypatch.setattr(camera, "take_picture", lambda: "20260101_000000_000000.jpg")

    camera.run()

    captured = capsys.readouterr()
    assert captured.out == "Saved 20260101_000000_000000.jpg\n"
    assert captured.err == ""
