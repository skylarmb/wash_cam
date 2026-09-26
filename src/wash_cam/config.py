"""Hardcoded configuration values, centralized for future adjustment."""

# Pixel box (left, top, right, bottom) used to crop captured images down to
# the car wash bays. Tuned for the Logitech Brio 101 at its current mount
# position and 1920x1080 capture resolution; re-tune if either changes.
CAMERA_CROP_BOX = (390, 250, 1660, 590)
