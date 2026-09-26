# CLI

The `wash-cam` entry point calls `wash_cam.cli.main`. Python 3.10 or later is required. Setuptools builds the package. The CLI uses `argparse`. The package depends on OpenCV for image processing and PyYAML for configuration.

Command packages reside in `src/wash_cam/` and expose their interfaces through `__init__.py`. Register subcommands in `cli.py`. The dummy package exposes `run()`, which prints `Wash Cam is ready.` The camera package exposes `run()`, which calls `take_picture()` and prints the saved filename. The command interface remains provisional.

The camera package also exposes `calibrate.run()`, registered as `calibrate`, which aligns the camera to the stored reference frame; see `.agents/docs/calibration.md`. `scripts/capture_loop.sh` captures on a fixed interval for unattended collection runs, configured through the `INTERVAL`, `DURATION`, and `LOG` environment variables.

Install the package with `uv sync --locked`. uv creates the virtual environment from `uv.lock`. Run `uv run wash-cam dummy` to check the CLI. Run `uv run wash-cam camera` to capture a picture from the webcam. Run `uv run wash-cam --help` to list commands.
