"""Parse CLI arguments and call the selected command."""

import argparse

from wash_cam import camera, dashboard, dummy
from wash_cam.camera import calibrate


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
    args = parser.parse_args()
    arguments = vars(args)
    arguments.pop("command", None)
    run_command = arguments.pop("run")
    run_command(**arguments)
