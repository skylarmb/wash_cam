# Wash Cam

Wash Cam is a program that takes periodic photos of a car wash and analyzes them to determine:
- how many cars go through the wash per day
- how much time is spent in the wash per car

## Technology

wash_cam is written in Python 3 and uses the OpenCV library to analyze the images.

The source code is split into modules that are imported into the main program. Each module follows the Unix principle of doing one thing and doing it well. Each module has a well-defined interface.

The main program is exposed via a CLI interface for running and monitoring the program from a terminal.

## Run the CLI

Use uv and Python 3.10 or later. Install the package from the lockfile:

```sh
uv sync --locked
uv run wash-cam dummy
```

The dummy command prints `Wash Cam is ready.` Use `uv run wash-cam --help` to list commands.
The CLI has no runtime dependencies. Setuptools builds the package.

Each command package exposes its interface through `__init__.py`.
The dummy command exposes `run()`. Register commands in `src/wash_cam/cli.py`.
