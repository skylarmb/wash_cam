"""Capture still images from a webcam."""

from datetime import datetime
from pathlib import Path

import cv2

from wash_cam.config import CAMERA_CROP_BOX

# Images are saved relative to the repository root, three levels above this file
# (src/wash_cam/camera/capture.py -> repo root).
IMAGES_DIR = Path(__file__).resolve().parents[3] / "tmp" / "images"


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


def take_picture(camera_index: int = 0) -> str:
    """Capture one frame from the webcam and save it to tmp/images.

    Returns the saved file's name (not the full path).
    Raises RuntimeError if the camera cannot be opened or a frame cannot be read,
    and ValueError if CAMERA_CROP_BOX does not fit inside the captured frame.
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

    cropped_frame = crop_to_box(frame, CAMERA_CROP_BOX)

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    # Microsecond precision avoids collisions between captures taken in the same second.
    filename = f"{datetime.now().astimezone().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
    cv2.imwrite(str(IMAGES_DIR / filename), cropped_frame)

    return filename
