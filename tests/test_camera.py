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


@pytest.fixture(autouse=True)
def no_settling(monkeypatch):
    """Keep the first frame after warm-up unless a test is about settling.

    Test frames are flat and often black, which never counts as settled, so
    each capture would otherwise wait the full settle cap.
    """
    monkeypatch.setattr(capture, "CAMERA_SETTLE_MAX_S", 0.0)


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


class SequenceVideoCapture:
    """Return a different frame per read, so warm-up discards are observable."""

    def __init__(self, frames):
        self.frames = list(frames)
        self.reads = 0

    def isOpened(self):
        return True

    def read(self):
        frame = self.frames[min(self.reads, len(self.frames) - 1)]
        self.reads += 1
        return True, frame

    def release(self):
        pass


def flat_frames(*values):
    """Frames of one flat brightness each, so a returned frame identifies itself."""
    return [np.full((16, 16, 3), value, dtype=np.uint8) for value in values]


def test_grab_frame_keeps_reading_until_exposure_settles(monkeypatch):
    """Discard the warm-up, then wait for brightness to stop changing.

    A camera leaving daylight exposure at dusk ramps up slowly, so a fixed
    handful of frames can stay near-black; the kept frame is the first one
    after brightness holds steady within range.
    """
    # Four warm-up frames, then a ramp from near-black that levels off at 30.
    frames = flat_frames(1, 1, 1, 1, 2, 5, 20, 30, 30, 30, 30, 31)
    fake_capture = SequenceVideoCapture(frames)
    monkeypatch.setattr(cv2, "VideoCapture", lambda index: fake_capture)
    monkeypatch.setattr(capture, "CAMERA_WARMUP_FRAMES", 4)
    monkeypatch.setattr(capture, "CAMERA_SETTLE_MAX_S", 60.0)

    frame = capture.grab_frame()

    # Settled once the last three changes left brightness at 30.
    assert fake_capture.reads == 11
    assert frame[0, 0, 0] == 30


def test_grab_frame_never_settles_on_a_near_black_frame(monkeypatch):
    """A steady but near-black frame is still converging, not settled."""
    frames = flat_frames(1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 40, 40, 40, 40)
    fake_capture = SequenceVideoCapture(frames)
    monkeypatch.setattr(cv2, "VideoCapture", lambda index: fake_capture)
    monkeypatch.setattr(capture, "CAMERA_SETTLE_MAX_S", 60.0)

    frame = capture.grab_frame()

    assert frame[0, 0, 0] == 40


def test_grab_frame_gives_up_after_the_time_cap(monkeypatch):
    """A scene that is truly dark is captured anyway once the cap passes."""
    fake_capture = SequenceVideoCapture(flat_frames(1, 1, 1, 1, 2, 3, 4, 5, 6))
    monkeypatch.setattr(cv2, "VideoCapture", lambda index: fake_capture)
    monkeypatch.setattr(capture, "CAMERA_SETTLE_MAX_S", 3.0)
    # Each clock reading is one second later than the last.
    clock = iter(range(100))
    monkeypatch.setattr(capture.time, "monotonic", lambda: next(clock))

    frame = capture.grab_frame()

    # The deadline is read at 0 s (so it is 3 s), then checked at 1, 2, 3 s.
    assert fake_capture.reads == 4 + 3
    assert frame[0, 0, 0] == 4


def test_grab_frame_without_warmup_or_settling_keeps_the_first_frame(monkeypatch):
    """Read exactly one frame when warm-up and settling are disabled."""
    fake_capture = SequenceVideoCapture(flat_frames(1, 2))
    monkeypatch.setattr(cv2, "VideoCapture", lambda index: fake_capture)
    monkeypatch.setattr(capture, "CAMERA_WARMUP_FRAMES", 0)
    monkeypatch.setattr(capture, "CAMERA_SETTLE_MAX_S", 0.0)

    frame = capture.grab_frame()

    assert fake_capture.reads == 1
    assert frame[0, 0, 0] == 1


def test_night_frames_are_averaged_and_shadow_lifted(monkeypatch):
    """A settled night frame becomes an average with lifted shadows."""
    # Warm-up, four steady frames at 20 (night), then frames alternating
    # around 20 so only their average is exactly 20.
    frames = flat_frames(1, 1, 1, 1, 20, 20, 20, 20, *([18, 22] * 8))
    fake_capture = SequenceVideoCapture(frames)
    monkeypatch.setattr(cv2, "VideoCapture", lambda index: fake_capture)
    monkeypatch.setattr(capture, "CAMERA_SETTLE_MAX_S", 60.0)
    monkeypatch.setattr(capture, "NIGHT_AVERAGE_FRAMES", 16)

    frame = capture.grab_frame(enhance_night=True)

    # 15 extra frames after the settled one: 7 x (18 + 22) + 18 = 298, plus 20.
    average = (20 + 7 * (18 + 22) + 18) / 16
    assert fake_capture.reads == 4 + 4 + 15
    assert frame.dtype == np.uint8
    assert frame[0, 0, 0] == round(255 * (average / 255) ** capture.NIGHT_SHADOW_GAMMA)


@pytest.mark.parametrize(
    ("level", "enhance"),
    [
        (100, True),  # Daylight is saved as captured.
        (2, True),  # Still too dark after the cap: kept, so it reads as unusable.
        (20, False),  # Calibration wants raw frames, night or not.
    ],
)
def test_other_frames_are_not_enhanced(monkeypatch, level, enhance):
    """Only usable night frames of a capture are averaged and lifted."""
    fake_capture = SequenceVideoCapture(flat_frames(level))
    monkeypatch.setattr(cv2, "VideoCapture", lambda index: fake_capture)
    monkeypatch.setattr(capture, "CAMERA_WARMUP_FRAMES", 0)

    frame = capture.grab_frame(enhance_night=enhance)

    assert fake_capture.reads == 1
    assert frame[0, 0, 0] == level


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
