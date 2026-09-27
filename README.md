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
The package uses OpenCV for image processing and PyYAML for configuration. Setuptools builds the package.

Each command package exposes its interface through `__init__.py`.
The dummy command exposes `run()`. Register commands in `src/wash_cam/cli.py`.

## Run the dashboard

Start the dashboard in a separate terminal:

```sh
uv run wash-cam dashboard
```

Open http://127.0.0.1:8000 on this computer. The page shows the latest JPEG from the checkout's `images/` directory.
It reloads every 30 seconds and selects the latest image by its timestamp filename.
The same image stays visible until a newer capture appears. An empty or missing directory shows `No images yet`.
The dashboard does not capture images. Press Ctrl+C to stop it.

## Configure local settings

Copy the example file from the checkout root:

```sh
cp config.example.yaml config.yaml
```

Git ignores `config.yaml`. The example contains only `example: true`.
Add machine settings to your local file as features require them.

Modules read settings through the configuration loader:

```python
from wash_cam.config import load_config

# Get the shared settings. The first call reads the file.
config = load_config()
example = config.example
```

The config module owns file resolution and reads only `config.yaml` at the source checkout root.
`load_config()` accepts no arguments and works independently of the current directory.
The first successful call creates an immutable `Config` object. Later calls return the same object without reading the file.
Restart the program to load file changes. Failed loads can be retried after correcting the file.
Imports do not read configuration, and the dummy command does not require it.

The file must contain a YAML mapping with string keys and a required boolean `example` field.
Modules access the typed field as `config.example`. Callers cannot change fields on the shared object.
The loader raises `wash_cam.config.ConfigError` for missing, unreadable, or invalid files.
Missing-file errors include the copy instruction. The loader does not supply defaults or merge the example file.

## Store verified image labels

The SQLite database at `db/labels.sqlite3` stores flexible JSON metadata for each verified image.
Each image can include labels for multiple bays.
See [the database documentation](db/README.md) for the schema, metadata convention, and migration command.
Database inputs are planned for a future iteration.

## Run unit tests

`uv sync --locked` installs pytest as a development dependency. Run the full suite:

```sh
uv run --locked pytest
```

Run one test during development:

```sh
uv run --locked pytest tests/test_cli.py::test_dummy_command
```

Tests reside in `tests/` and import the installed package. They require no camera hardware or external services.
Follow the red/green workflow in `AGENTS.md` for new behavior and bug fixes.
