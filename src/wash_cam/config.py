"""Load machine settings from a YAML file."""

from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

# Anchor paths to the src layout, independent of the working directory.
CHECKOUT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = CHECKOUT_ROOT / "config.yaml"
EXAMPLE_CONFIG_PATH = CHECKOUT_ROOT / "config.example.yaml"

# Reference frames that calibration aligns later captures against, one per
# lighting condition (day, night, ...). All are stored in canonical
# coordinates, so CAMERA_CROP_BOX means the same thing against any of them.
# Local state like config.yaml, so they live outside version control.
REFERENCES_DIR = CHECKOUT_ROOT / "calibration" / "references"

# Minimum RANSAC inliers before a solved alignment is trusted. Matching a night
# frame against a daytime reference yielded 4 inliers and an 809px error, while
# genuine matches yield thousands, so a low count means the scenes did not
# really correspond and the transform must be rejected rather than stored.
MIN_CALIBRATION_INLIERS = 50

# Pixel box (left, top, right, bottom) used to crop captured images down to
# the car wash bays. Expressed in reference-frame coordinates, so calibration
# keeps it pointed at the same part of the scene when the camera is re-seated.
CAMERA_CROP_BOX = (390, 250, 1660, 590)

# Floor quad of each wash bay as (x, y) corners, indexed left to right from 0 to
# match the label bay IDs. Expressed in cropped-capture coordinates (the image
# CAMERA_CROP_BOX produces), which calibration keeps fixed on the scene. A car
# belongs to a bay when the bottom-center of its box, where its wheels meet the
# ground, falls inside that bay's quad; this excludes cars queueing in the
# foreground and cars parked behind the bays, whose boxes can still overlap it.
# The back edge sits at y=115, not the painted floor's y=95: a car parked
# behind bay 3 at night had its box bottom at y=95-100, while cars in a bay
# have box bottoms at y=150-170.
# Review with `wash-cam bays` after any change.
BAY_POLYGONS = (
    ((129, 115), (268, 115), (268, 170), (85, 170)),
    ((308, 115), (468, 115), (468, 175), (300, 175)),
    ((484, 115), (650, 115), (655, 170), (480, 175)),
    ((771, 115), (908, 115), (935, 172), (768, 172)),
    ((964, 115), (1094, 115), (1135, 165), (962, 170)),
)

# A capture is night when more than NIGHT_DARK_FRACTION of its pixels are darker
# than DARK_PIXEL_LEVEL (grayscale 0-255). Mean brightness cannot tell the two
# apart, because auto-exposure lifts night frames to daytime levels. Tuned on
# the first collection run: daytime frames had 22-33% dark pixels, night 41-100%.
DARK_PIXEL_LEVEL = 25
NIGHT_DARK_FRACTION = 0.38

# Frames read and discarded before the one that gets saved. A cold camera needs
# streaming time before auto-exposure converges: after the USB device powers
# back up, captures ramped from near-black to correctly exposed over roughly
# three seconds of cumulative sensor run-time. Costs about 0.2s per capture and
# makes no difference once the camera is warm.
CAMERA_WARMUP_FRAMES = 4


@dataclass(frozen=True)
class Config:
    """Provide typed settings that callers cannot change."""

    example: bool


@dataclass(frozen=True)
class Calibration:
    """Map coordinates in a freshly captured frame onto the reference frame."""

    # 2x3 affine matrix: translation, rotation, and uniform scale.
    matrix: tuple[tuple[float, float, float], tuple[float, float, float]]
    # Reference frame size (width, height) that the matrix maps into.
    reference_size: tuple[int, int]


class ConfigError(Exception):
    """Report invalid or unreadable configuration."""


@cache
def load_config() -> Config:
    """Return the shared settings or raise ConfigError for an invalid file.

    Read config.yaml from the checkout root on the first successful call.
    Later calls return the same immutable object. Failed loads can be retried.
    """
    config_path = CONFIG_PATH
    try:
        settings = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConfigError(
            f"Configuration file not found: {config_path}. "
            "Copy config.example.yaml to config.yaml at the checkout root."
        ) from error
    except (OSError, UnicodeError) as error:
        raise ConfigError(
            f"Cannot read configuration file {config_path}: {error}"
        ) from error
    except yaml.YAMLError as error:
        raise ConfigError(f"Invalid YAML in {config_path}: {error}") from error

    if not isinstance(settings, dict) or not all(
        isinstance(key, str) for key in settings
    ):
        raise ConfigError(
            f"Configuration file {config_path} must contain a mapping with string keys."
        )
    if not isinstance(settings.get("example"), bool):
        raise ConfigError(
            f"Configuration file {config_path}: example must be a boolean."
        )
    return Config(example=settings["example"])


def _read_settings() -> dict:
    """Return the raw config mapping, or an empty mapping when there is no file.

    Calibration is read and written without going through load_config so that
    capturing keeps working before anyone copies config.example.yaml.
    """
    try:
        settings = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError) as error:
        raise ConfigError(
            f"Cannot read configuration file {CONFIG_PATH}: {error}"
        ) from error
    except yaml.YAMLError as error:
        raise ConfigError(f"Invalid YAML in {CONFIG_PATH}: {error}") from error

    return settings if isinstance(settings, dict) else {}


def load_calibration() -> Calibration | None:
    """Return the stored calibration, or None when the camera is uncalibrated."""
    stored = _read_settings().get("calibration")
    if stored is None:
        return None
    if not isinstance(stored, dict):
        raise ConfigError(
            f"Configuration file {CONFIG_PATH}: calibration must be a mapping."
        )

    matrix = stored.get("matrix")
    if (
        not isinstance(matrix, list)
        or len(matrix) != 2
        or not all(isinstance(row, list) and len(row) == 3 for row in matrix)
        or not all(isinstance(value, (int, float)) for row in matrix for value in row)
    ):
        raise ConfigError(
            f"Configuration file {CONFIG_PATH}: calibration.matrix must be 2 rows of 3 numbers."
        )

    size = stored.get("reference_size")
    if (
        not isinstance(size, list)
        or len(size) != 2
        or not all(isinstance(value, int) and value > 0 for value in size)
    ):
        raise ConfigError(
            f"Configuration file {CONFIG_PATH}: "
            "calibration.reference_size must be two positive integers."
        )

    return Calibration(
        matrix=tuple(tuple(float(value) for value in row) for row in matrix),
        reference_size=(size[0], size[1]),
    )


def save_calibration(calibration: Calibration) -> None:
    """Write the calibration into config.yaml, leaving other settings in place."""
    settings = _read_settings()
    if not settings and EXAMPLE_CONFIG_PATH.is_file():
        # Seed from the example so the file stays valid for load_config.
        seed = yaml.safe_load(EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"))
        settings = seed if isinstance(seed, dict) else {}

    settings["calibration"] = {
        "matrix": [list(row) for row in calibration.matrix],
        "reference_size": list(calibration.reference_size),
    }
    # default_flow_style=None keeps the matrix rows on one line each.
    CONFIG_PATH.write_text(
        yaml.safe_dump(settings, sort_keys=False, default_flow_style=None),
        encoding="utf-8",
    )
    # Drop the memoized Config so a later load sees the file we just wrote.
    load_config.cache_clear()
