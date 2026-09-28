"""Capture still images from a webcam."""

import time
from collections import deque
from datetime import datetime

import cv2
import numpy as np

from wash_cam.config import (
    CAMERA_CROP_BOX,
    CAMERA_OVEREXPOSED,
    CAMERA_SETTLE_MAX_S,
    CAMERA_SETTLE_TOLERANCE,
    CAMERA_SETTLE_WINDOW,
    CAMERA_UNDEREXPOSED,
    CAMERA_WARMUP_FRAMES,
    IMAGES_DIR,
    load_calibration,
)


def crop_to_box(frame, crop_box: tuple[int, int, int, int]):
    """Crop a frame to a (left, top, right, bottom) pixel box.

    Numpy slicing accepts out-of-range bounds silently, truncating the crop or
    producing an empty array, so the box is validated first. Raises ValueError
    if the box is inverted or does not fit inside the frame.
    """
    left, top, right, bottom = crop_box
    height, width = frame.shape[:2]

    if left >= right or top >= bottom:
        raise ValueError(
            f"Inverted crop box {crop_box}: expected left < right and top < bottom."
        )
    if left < 0 or top < 0 or right > width or bottom > height:
        raise ValueError(
            f"Out of bounds crop box {crop_box}: does not fit in {width}x{height} frame."
        )

    return frame[top:bottom, left:right]


def brightness(frame) -> float:
    """Mean brightness (0-255) of a frame, sampled sparsely for speed."""
    return float(frame[::8, ::8].mean())


def exposure_settled(recent: deque) -> bool:
    """Whether recent frame brightnesses show auto-exposure has converged."""
    if len(recent) <= CAMERA_SETTLE_WINDOW:
        return False
    first, last = recent[0], recent[-1]
    in_range = CAMERA_UNDEREXPOSED <= last <= CAMERA_OVEREXPOSED
    return in_range and abs(last - first) <= CAMERA_SETTLE_TOLERANCE * max(first, 1.0)


def grab_frame(camera_index: int = 0):
    """Capture one raw, unaligned frame from the webcam once exposure settles.

    Discards CAMERA_WARMUP_FRAMES frames, then keeps reading until brightness
    stops changing (see CAMERA_SETTLE_* in config.py) and returns the last
    frame. After CAMERA_SETTLE_MAX_S it returns the latest frame anyway, so a
    scene that is truly dark still gets captured.

    Raises RuntimeError if the camera cannot be opened or a frame cannot be read.
    """
    capture = cv2.VideoCapture(camera_index)
    if not capture.isOpened():
        capture.release()
        raise RuntimeError(f"Could not open camera at index {camera_index}.")

    try:
        for _ in range(CAMERA_WARMUP_FRAMES):
            capture.read()
        deadline = time.monotonic() + CAMERA_SETTLE_MAX_S
        # Brightness of the newest frames; one more than the window, so the
        # oldest entry is the baseline the newest is compared against.
        recent: deque = deque(maxlen=CAMERA_SETTLE_WINDOW + 1)
        while True:
            read_ok, frame = capture.read()
            if not read_ok:
                break
            recent.append(brightness(frame))
            if exposure_settled(recent) or time.monotonic() >= deadline:
                break
    finally:
        capture.release()

    if not read_ok:
        raise RuntimeError(
            f"Could not read a frame from camera at index {camera_index}."
        )

    return frame


def align_frame(frame):
    """Warp a frame into reference coordinates using the stored calibration.

    Returns the frame unchanged when the camera has not been calibrated, which
    is the correct transform for a camera still sitting in the reference spot.
    """
    calibration = load_calibration()
    if calibration is None:
        return frame

    matrix = np.array(calibration.matrix, dtype=np.float64)
    return cv2.warpAffine(frame, matrix, calibration.reference_size)


def take_picture(camera_index: int = 0) -> str:
    """Capture one frame from the webcam and save it to tmp/images.

    The frame is aligned to the reference position, then cropped, so captures
    frame the same part of the scene across camera setups.

    Returns the saved file's name (not the full path).
    Raises RuntimeError if the camera cannot be opened or a frame cannot be read,
    and ValueError if CAMERA_CROP_BOX does not fit inside the aligned frame.
    """
    frame = align_frame(grab_frame(camera_index))
    cropped_frame = crop_to_box(frame, CAMERA_CROP_BOX)

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    # Microsecond precision avoids collisions between captures taken in the same second.
    filename = f"{datetime.now().astimezone().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
    encoded, jpeg = cv2.imencode(".jpg", cropped_frame)
    if not encoded:
        raise RuntimeError("Could not encode the captured frame as JPEG.")
    # Write under a name the detection watcher ignores, then rename: the rename
    # is atomic, so the watcher never reads a half-written capture.
    partial = IMAGES_DIR / f"{filename}.part"
    partial.write_bytes(jpeg.tobytes())
    partial.replace(IMAGES_DIR / filename)

    return filename
