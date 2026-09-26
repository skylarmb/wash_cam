"""Webcam capture commands."""

from wash_cam.camera.capture import take_picture

__all__ = ["run", "take_picture"]


def run() -> None:
    """Capture one picture from the webcam and print the saved filename."""
    filename = take_picture()
    print(f"Saved {filename}")
