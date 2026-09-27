"""Parse CLI arguments and call the selected command."""

import argparse
from pathlib import Path

from wash_cam import (
    bays,
    camera,
    dashboard,
    db,
    detect,
    dummy,
    evaluate,
    sessions,
    watch,
)
from wash_cam.camera import calibrate
from wash_cam.config import DATABASE_PATH


def main() -> None:
    """Run one subcommand."""
    parser = argparse.ArgumentParser(description="Car wash monitoring.")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("dummy", help="Print a message.")
    command.set_defaults(run=dummy.run)
    command = commands.add_parser("camera", help="Capture one picture from the webcam.")
    command.set_defaults(run=camera.run)
    command = commands.add_parser("dashboard", help="Show the latest captured image.")
    command.set_defaults(run=dashboard.run)
    command = commands.add_parser(
        "calibrate", help="Align the camera to the reference frames."
    )
    command.add_argument(
        "--set-reference",
        metavar="NAME",
        dest="reference_name",
        help="Record the current view as a named reference (e.g. day, night).",
    )
    command.set_defaults(run=calibrate.run)
    command = commands.add_parser(
        "bays", help="Draw the bay regions on captures for review."
    )
    command.add_argument(
        "images",
        nargs="*",
        type=Path,
        help="Images to draw on. Defaults to every capture in tmp/images/.",
    )
    command.add_argument(
        "--output",
        type=Path,
        default=bays.REVIEW_DIR,
        help="Directory for the overlays. Defaults to tmp/bay_review/.",
    )
    command.set_defaults(run=bays.run)
    command = commands.add_parser(
        "evaluate", help="Score a vehicle-count model against verified labels."
    )
    command.add_argument(
        "--model",
        choices=sorted(evaluate.MODELS),
        default="empty",
        help="Model to score.",
    )
    command.set_defaults(run=evaluate.run)
    command = commands.add_parser(
        "detect", help="Count vehicles per bay and save review overlays."
    )
    command.add_argument(
        "images",
        nargs="*",
        type=Path,
        help="Images to detect in. Defaults to every capture in tmp/images/.",
    )
    command.add_argument(
        "--output",
        type=Path,
        default=detect.REVIEW_DIR,
        help="Directory for the overlays. Defaults to tmp/detections/.",
    )
    command.set_defaults(run=detect.run)
    command = commands.add_parser(
        "migrate", help="Create or upgrade the database to the latest schema."
    )
    command.set_defaults(run=lambda: db.migrate(DATABASE_PATH))
    command = commands.add_parser(
        "watch", help="Detect vehicles in every new capture and store the results."
    )
    command.set_defaults(run=watch.run)
    command = commands.add_parser(
        "sessions", help="Update bay visit sessions from stored detections."
    )
    command.add_argument(
        "--rebuild",
        action="store_true",
        help="Recompute every session, e.g. after changing thresholds or detections.",
    )
    command.set_defaults(run=sessions.run)
    args = parser.parse_args()
    arguments = vars(args)
    arguments.pop("command", None)
    run_command = arguments.pop("run")
    run_command(**arguments)
