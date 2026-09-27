"""Check bay regions and the review overlay without real captures."""

import cv2
import numpy as np

from wash_cam import bays
from wash_cam.config import BAY_POLYGONS


def floor_center(bay_index):
    """Return a point inside a bay's floor polygon."""
    points = np.array(BAY_POLYGONS[bay_index], dtype=np.float64)
    return points.mean(axis=0)


def box_standing_at(point, width=100, height=60):
    """Build a (left, top, right, bottom) box whose bottom-center sits on point."""
    x, y = point
    return (x - width / 2, y - height, x + width / 2, y)


def test_bay_polygons_are_ordered_left_to_right():
    """Index 0 is the leftmost bay, matching the labeling convention."""
    centers = [floor_center(index)[0] for index in range(len(BAY_POLYGONS))]

    assert len(BAY_POLYGONS) == 5
    assert centers == sorted(centers)


def test_assign_to_bay_uses_the_ground_contact_point():
    """A car standing on a bay floor belongs to that bay."""
    for index in range(len(BAY_POLYGONS)):
        box = box_standing_at(floor_center(index))

        assert bays.assign_to_bay(box) == index


def test_assign_to_bay_ignores_cars_outside_the_bays():
    """Cars queueing in the foreground or parked behind the bays are not counted."""
    queueing = box_standing_at((800, 290))
    # Its box overlaps bay 3, but its wheels are behind the floor polygon.
    parked_behind = box_standing_at((810, 80))

    assert bays.assign_to_bay(queueing) is None
    assert bays.assign_to_bay(parked_behind) is None


def test_draw_bays_marks_a_copy_of_the_image():
    """The overlay draws on a copy and leaves the source frame untouched."""
    image = np.zeros((340, 1270, 3), dtype=np.uint8)

    overlay = bays.draw_bays(image)

    assert overlay.shape == image.shape
    assert not image.any()
    # A polygon corner lies on the drawn outline.
    x, y = BAY_POLYGONS[0][0]
    assert overlay[y, x].any()


def test_run_writes_one_overlay_per_image(tmp_path):
    """Each input image produces an overlay with the same name in the output directory."""
    source = tmp_path / "images"
    source.mkdir()
    for name in ("a.jpg", "b.jpg"):
        cv2.imwrite(str(source / name), np.zeros((340, 1270, 3), dtype=np.uint8))
    output = tmp_path / "review"

    bays.run(images=[source / "a.jpg", source / "b.jpg"], output=output)

    assert sorted(path.name for path in output.iterdir()) == ["a.jpg", "b.jpg"]
