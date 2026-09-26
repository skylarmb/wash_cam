"""Load machine settings from a YAML file."""

from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

# Pixel box (left, top, right, bottom) used to crop captured images down to
# the car wash bays. Tuned for the Logitech Brio 101 at its current mount
# position and 1920x1080 capture resolution; re-tune if either changes.
CAMERA_CROP_BOX = (390, 250, 1660, 590)


@dataclass(frozen=True)
class Config:
    """Provide typed settings that callers cannot change."""

    example: bool


class ConfigError(Exception):
    """Report invalid or unreadable configuration."""


@cache
def load_config() -> Config:
    """Return the shared settings or raise ConfigError for an invalid file.

    Read config.yaml from the checkout root on the first successful call.
    Later calls return the same immutable object. Failed loads can be retried.
    """
    # Anchor the default to the src layout, independent of the working directory.
    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
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
