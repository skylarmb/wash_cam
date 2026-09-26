"""Align the camera to a stored reference frame.

The camera is re-seated between setups, so captures land a few pixels off the
reference framing. Calibration is a one-time step the user runs at setup: it
matches a fresh capture against the reference frame, solves for the offset, and
stores it. Every later capture reuses that stored transform, so framing stays
fixed until the user re-calibrates.
"""

import cv2
import numpy as np

from wash_cam.camera.capture import grab_frame
from wash_cam.config import (
    REFERENCE_IMAGE_PATH,
    Calibration,
    save_calibration,
)

# Identity: no translation, rotation, or scale relative to the reference.
IDENTITY_MATRIX = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))

# The camera moves centimetres between setups, so matches are plentiful. This
# floor only catches a genuinely unusable frame, such as a covered lens.
MIN_MATCHES = 12


def estimate_alignment(frame, reference):
    """Solve for the affine transform mapping frame coordinates onto reference.

    Uses a partial affine model (translation, rotation, uniform scale) because a
    re-seated camera cannot introduce meaningful perspective change from metres
    away. RANSAC discards features on cars, people, and other moving objects.

    Raises RuntimeError if the two images cannot be matched.
    """
    detector = cv2.ORB_create(nfeatures=5000)
    frame_keypoints, frame_descriptors = detector.detectAndCompute(
        cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), None
    )
    reference_keypoints, reference_descriptors = detector.detectAndCompute(
        cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY), None
    )
    if frame_descriptors is None or reference_descriptors is None:
        raise RuntimeError("Found no image features to align; check the camera view.")

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = matcher.match(frame_descriptors, reference_descriptors)
    if len(matches) < MIN_MATCHES:
        raise RuntimeError(
            f"Only matched {len(matches)} features against the reference frame; "
            f"need {MIN_MATCHES}. Re-aim the camera or set a new reference."
        )

    frame_points = np.float32([frame_keypoints[m.queryIdx].pt for m in matches])
    reference_points = np.float32([reference_keypoints[m.trainIdx].pt for m in matches])
    matrix, inliers = cv2.estimateAffinePartial2D(
        frame_points, reference_points, method=cv2.RANSAC
    )
    if matrix is None:
        raise RuntimeError(
            "Could not solve for camera alignment. Re-aim the camera or set a "
            "new reference."
        )

    return matrix, int(inliers.sum())


def calibrate(camera_index: int = 0, set_reference: bool = False) -> str:
    """Align the camera to the reference frame and store the result.

    Captures the reference frame itself when one does not exist yet, or when
    set_reference is True. Returns a short summary line for the CLI.
    """
    frame = grab_frame(camera_index)
    height, width = frame.shape[:2]

    if set_reference or not REFERENCE_IMAGE_PATH.is_file():
        REFERENCE_IMAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(REFERENCE_IMAGE_PATH), frame)
        save_calibration(
            Calibration(matrix=IDENTITY_MATRIX, reference_size=(width, height))
        )
        return f"Saved reference frame ({width}x{height}) and reset alignment."

    reference = cv2.imread(str(REFERENCE_IMAGE_PATH))
    if reference is None:
        raise RuntimeError(f"Could not read reference frame {REFERENCE_IMAGE_PATH}.")

    matrix, inlier_count = estimate_alignment(frame, reference)
    reference_height, reference_width = reference.shape[:2]
    save_calibration(
        Calibration(
            matrix=tuple(tuple(float(value) for value in row) for row in matrix),
            reference_size=(reference_width, reference_height),
        )
    )

    shift_x, shift_y = matrix[0][2], matrix[1][2]
    return (
        f"Aligned to reference on {inlier_count} features "
        f"(shift {shift_x:+.1f}, {shift_y:+.1f} px)."
    )


def run() -> None:
    """Calibrate the camera and report the result."""
    print(calibrate())
