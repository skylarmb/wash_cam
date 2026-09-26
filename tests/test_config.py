"""Check configuration loading through its public interface."""

import importlib.util
import shutil
import sys
from dataclasses import FrozenInstanceError

import pytest

from wash_cam import config as config_module


@pytest.fixture
def config(tmp_path, monkeypatch):
    """Import from a temporary checkout to isolate settings between tests."""
    module_path = tmp_path / "src" / "wash_cam" / "config.py"
    module_path.parent.mkdir(parents=True)
    shutil.copyfile(config_module.__file__, module_path)
    spec = importlib.util.spec_from_file_location("isolated_config", module_path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)

    # Import before config.yaml exists to verify that import does not read it.
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def config_path(tmp_path):
    """Locate the config file inside the temporary checkout."""
    return tmp_path / "config.yaml"


@pytest.mark.parametrize("value", [True, False])
def test_load_config_returns_typed_settings(config, config_path, value):
    """Expose the required boolean through the Config interface."""
    config_path.write_text(f"example: {str(value).lower()}\n", encoding="utf-8")

    settings = config.load_config()

    assert isinstance(settings, config.Config)
    assert settings.example is value


def test_load_config_caches_first_success(config, config_path):
    """Serve the same settings after the source file changes or disappears."""
    config_path.write_text("example: true\n", encoding="utf-8")
    first = config.load_config()
    config_path.write_text("example: false\n", encoding="utf-8")

    assert config.load_config() is first
    config_path.unlink()
    assert config.load_config() is first
    assert first.example is True


def test_config_is_immutable(config, config_path):
    """Prevent a caller from changing the settings shared by other callers."""
    config_path.write_text("example: true\n", encoding="utf-8")
    settings = config.load_config()
    assert isinstance(settings, config.Config)

    with pytest.raises(FrozenInstanceError):
        settings.example = False

    assert config.load_config().example is True


def test_load_config_does_not_accept_a_path(config, config_path):
    """Keep file selection inside the configuration module."""
    config_path.write_text("example: true\n", encoding="utf-8")

    with pytest.raises(TypeError):
        config.load_config(config_path)


@pytest.mark.parametrize("contents", [None, "example: [", "- value", "example: 1"])
def test_failed_load_can_be_retried(config, config_path, contents):
    """Cache only successful loads so callers can correct an invalid file."""
    if contents is not None:
        config_path.write_text(contents, encoding="utf-8")

    with pytest.raises(config.ConfigError):
        config.load_config()

    config_path.write_text("example: true\n", encoding="utf-8")
    assert config.load_config().example is True


def test_default_path_uses_checkout_root(config, config_path, tmp_path, monkeypatch):
    """Read the checkout's file independently of the current directory."""
    config_path.write_text("example: true\n", encoding="utf-8")
    other_directory = tmp_path / "other"
    other_directory.mkdir()
    (other_directory / "config.yaml").write_text("example: false\n", encoding="utf-8")
    monkeypatch.chdir(other_directory)

    settings = config.load_config()

    assert isinstance(settings, config.Config)
    assert settings.example is True


def test_missing_config_reports_setup_hint(config, config_path):
    """Identify the missing file and explain how to create it."""
    with pytest.raises(config.ConfigError) as error:
        config.load_config()

    assert str(config_path) in str(error.value)
    assert "Copy config.example.yaml to config.yaml" in str(error.value)


@pytest.mark.parametrize(
    "contents", ["example: [", "!!python/object:builtins.object {}"]
)
def test_invalid_yaml_reports_path(config, config_path, contents):
    """Reject malformed YAML and Python object tags."""
    config_path.write_text(contents, encoding="utf-8")

    with pytest.raises(config.ConfigError, match="YAML") as error:
        config.load_config()

    assert str(config_path) in str(error.value)


@pytest.mark.parametrize(
    "contents", ["", "# empty\n", "null", "true", "- value", "1: value"]
)
def test_invalid_mapping_reports_path(config, config_path, contents):
    """Require a mapping with string keys at the document root."""
    config_path.write_text(contents, encoding="utf-8")

    with pytest.raises(config.ConfigError, match="mapping with string keys") as error:
        config.load_config()

    assert str(config_path) in str(error.value)


@pytest.mark.parametrize(
    "contents", ["{}", "example: null", 'example: "true"', "example: 1"]
)
def test_example_must_be_a_boolean(config, config_path, contents):
    """Reject absent or incorrectly typed required settings."""
    config_path.write_text(contents, encoding="utf-8")

    with pytest.raises(config.ConfigError, match="example.*boolean") as error:
        config.load_config()

    assert str(config_path) in str(error.value)


def test_unreadable_file_reports_path(config, config_path):
    """Report a directory in place of the file as a configuration read error."""
    config_path.mkdir()

    with pytest.raises(config.ConfigError) as error:
        config.load_config()

    assert str(config_path) in str(error.value)


def test_invalid_utf8_reports_path(config, config_path):
    """Report files that do not contain UTF-8 text."""
    config_path.write_bytes(b"example: \xff")

    with pytest.raises(config.ConfigError) as error:
        config.load_config()

    assert str(config_path) in str(error.value)
