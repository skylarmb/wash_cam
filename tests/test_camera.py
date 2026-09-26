"""Check camera capture behavior without real webcam hardware."""

import cv2
import numpy as np
import pytest

from wash_cam import camera
from wash_cam.camera import capture
from wash_cam.config import CAMERA_CROP_BOX


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


def fake_frame():
    """Build a frame large enough to contain the crop box."""
    _, _, right, bottom = CAMERA_CROP_BOX
    return np.zeros((bottom + 10, right + 10, 3), dtype=np.uint8)


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


def test_run_prints_saved_filename(monkeypatch, capsys):
    """Print the filename returned by take_picture."""
    monkeypatch.setattr(camera, "take_picture", lambda: "20260101_000000_000000.jpg")

    camera.run()

    captured = capsys.readouterr()
    assert captured.out == "Saved 20260101_000000_000000.jpg\n"
    assert captured.err == ""
