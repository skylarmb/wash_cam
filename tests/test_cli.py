"""Check CLI commands, help, and argument errors."""

import sys

import pytest

from wash_cam import camera
from wash_cam.cli import main


def test_dummy_command(monkeypatch, capsys):
    """Run the selected command and print its output."""
    monkeypatch.setattr(sys, "argv", ["wash-cam", "dummy"])

    main()

    captured = capsys.readouterr()
    assert captured.out == "Wash Cam is ready.\n"
    assert captured.err == ""


def test_camera_command(monkeypatch, capsys):
    """Run the camera command without touching real hardware."""
    monkeypatch.setattr(camera, "take_picture", lambda: "20260101_000000_000000.jpg")
    monkeypatch.setattr(sys, "argv", ["wash-cam", "camera"])

    main()

    captured = capsys.readouterr()
    assert captured.out == "Saved 20260101_000000_000000.jpg\n"
    assert captured.err == ""


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["--help"], "dummy"),
        (["dummy", "--help"], "dummy"),
        (["camera", "--help"], "camera"),
    ],
)
def test_help_exits_successfully(arguments, expected, monkeypatch, capsys):
    """Show help without running a command."""
    monkeypatch.setattr(sys, "argv", ["wash-cam", *arguments])

    with pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == 0
    captured = capsys.readouterr()
    assert "usage:" in captured.out
    assert expected in captured.out
    assert "Wash Cam is ready." not in captured.out
    assert captured.err == ""


@pytest.mark.parametrize(
    ("arguments", "message"),
    [([], "required"), (["unknown"], "invalid choice")],
)
def test_invalid_command_reports_error(arguments, message, monkeypatch, capsys):
    """Reject missing or unknown commands with an argument error."""
    monkeypatch.setattr(sys, "argv", ["wash-cam", *arguments])

    with pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error:" in captured.err
    assert message in captured.err
