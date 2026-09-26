"""Parse CLI arguments and call the selected command."""

import argparse

from wash_cam import camera, dummy


def main() -> None:
    """Run one subcommand."""
    parser = argparse.ArgumentParser(description="Car wash monitoring.")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("dummy", help="Print a message.")
    command.set_defaults(run=dummy.run)
    command = commands.add_parser("camera", help="Capture one picture from the webcam.")
    command.set_defaults(run=camera.run)
    args = parser.parse_args()
    args.run()
