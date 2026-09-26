"""Capture still images from a webcam."""

from datetime import datetime
from pathlib import Path

import cv2

from wash_cam.config import CAMERA_CROP_BOX

# Images are saved relative to the repository root, three levels above this file
# (src/wash_cam/camera/capture.py -> repo root).
IMAGES_DIR = Path(__file__).resolve().parents[3] / "tmp" / "images"


def take_picture(camera_index: int = 0) -> str:
    """Capture one frame from the webcam and save it to tmp/images.

    Returns the saved file's name (not the full path).
    Raises RuntimeError if the camera cannot be opened or a frame cannot be read.
    """
    capture = cv2.VideoCapture(camera_index)
    if not capture.isOpened():
        capture.release()
        raise RuntimeError(f"Could not open camera at index {camera_index}.")

    try:
        read_ok, frame = capture.read()
    finally:
        capture.release()

    if not read_ok:
        raise RuntimeError(
            f"Could not read a frame from camera at index {camera_index}."
        )

    left, top, right, bottom = CAMERA_CROP_BOX
    cropped_frame = frame[top:bottom, left:right]

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    # Microsecond precision avoids collisions between captures taken in the same second.
    filename = f"{datetime.now().astimezone().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
    cv2.imwrite(str(IMAGES_DIR / filename), cropped_frame)

    return filename
