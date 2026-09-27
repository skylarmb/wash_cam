"""Align the camera to stored reference frames.

The camera is re-seated between setups, so captures land a few pixels off the
reference framing. Calibration is a one-time step the user runs at setup: it
matches a fresh capture against the stored reference frames, solves for the
offset, and stores it. Every later capture reuses that stored transform, so
framing stays fixed until the user re-calibrates.

One reference per lighting condition is kept, because a scene looks too
different between day and night to match across the two. Rather than
classifying the capture, every reference is tried and the best match wins: the
inlier count already says which reference genuinely corresponds to the view.
"""

import cv2
import numpy as np

from wash_cam.camera.capture import align_frame, grab_frame
from wash_cam.config import (
    MIN_CALIBRATION_INLIERS,
    REFERENCES_DIR,
    Calibration,
    load_calibration,
    save_calibration,
)

# Identity: no translation, rotation, or scale relative to the reference.
IDENTITY_MATRIX = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))

# The camera moves centimetres between setups, so matches are plentiful. This
# floor only catches a genuinely unusable frame, such as a covered lens.
MIN_MATCHES = 12


def load_references() -> list[tuple[str, np.ndarray]]:
    """Load every stored reference frame, keyed by its name."""
    if not REFERENCES_DIR.is_dir():
        return []

    references = []
    for path in sorted(REFERENCES_DIR.glob("*.jpg")):
        image = cv2.imread(str(path))
        if image is not None:
            references.append((path.stem, image))
    return references


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
            f"need {MIN_MATCHES}."
        )

    frame_points = np.float32([frame_keypoints[m.queryIdx].pt for m in matches])
    reference_points = np.float32([reference_keypoints[m.trainIdx].pt for m in matches])
    matrix, inliers = cv2.estimateAffinePartial2D(
        frame_points, reference_points, method=cv2.RANSAC
    )
    if matrix is None:
        raise RuntimeError("Could not solve for camera alignment.")

    return matrix, int(inliers.sum())


def align_to_best_reference(frame):
    """Match a frame against every reference and return the strongest fit.

    Returns (name, matrix, inlier_count). Raises RuntimeError when no reference
    matches well enough, rather than storing a transform that would silently
    mis-frame every later capture.
    """
    references = load_references()
    if not references:
        raise RuntimeError(
            f"No reference frames in {REFERENCES_DIR}. "
            "Run calibrate --set-reference NAME to record one."
        )

    results = []
    for name, reference in references:
        try:
            matrix, inlier_count = estimate_alignment(frame, reference)
        except RuntimeError:
            # A reference from another lighting condition simply will not match.
            continue
        results.append((name, matrix, inlier_count))

    if not results:
        raise RuntimeError("No reference frame could be matched against this view.")

    name, matrix, inlier_count = max(results, key=lambda result: result[2])
    if inlier_count < MIN_CALIBRATION_INLIERS:
        scores = ", ".join(f"{n}={c}" for n, _, c in results)
        raise RuntimeError(
            f"Best match '{name}' had only {inlier_count} inliers "
            f"(need {MIN_CALIBRATION_INLIERS}); scores: {scores}. "
            "Record a reference for the current lighting instead."
        )

    return name, matrix, inlier_count


def set_reference(name: str, camera_index: int = 0) -> str:
    """Record the current view as a named reference frame.

    The frame is stored in canonical coordinates, using the existing
    calibration, so every reference agrees on where CAMERA_CROP_BOX points even
    when recorded from a re-seated camera.
    """
    frame = grab_frame(camera_index)
    existing = load_calibration()
    canonical = align_frame(frame)
    height, width = canonical.shape[:2]

    REFERENCES_DIR.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(REFERENCES_DIR / f"{name}.jpg"), canonical)

    if existing is None:
        # The first reference defines the canonical frame.
        save_calibration(
            Calibration(matrix=IDENTITY_MATRIX, reference_size=(width, height))
        )
        return f"Recorded '{name}' ({width}x{height}) as the canonical reference."

    return f"Recorded '{name}' ({width}x{height}) in canonical coordinates."


def calibrate(camera_index: int = 0) -> str:
    """Align the camera to the best-matching reference and store the result."""
    frame = grab_frame(camera_index)
    name, matrix, inlier_count = align_to_best_reference(frame)

    references = dict(load_references())
    reference_height, reference_width = references[name].shape[:2]
    save_calibration(
        Calibration(
            matrix=tuple(tuple(float(value) for value in row) for row in matrix),
            reference_size=(reference_width, reference_height),
        )
    )

    shift_x, shift_y = matrix[0][2], matrix[1][2]
    return (
        f"Aligned to '{name}' on {inlier_count} inliers "
        f"(shift {shift_x:+.1f}, {shift_y:+.1f} px)."
    )


def run(reference_name: str | None = None) -> None:
    """Calibrate the camera, or record a reference, and report the result."""
    if reference_name:
        print(set_reference(reference_name))
    else:
        print(calibrate())
