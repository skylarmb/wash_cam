"""Check CLI commands, help, and argument errors."""

import sys

import pytest

from wash_cam.cli import main


def test_dummy_command(monkeypatch, capsys):
    """Run the selected command and print its output."""
    monkeypatch.setattr(sys, "argv", ["wash-cam", "dummy"])

    main()

    captured = capsys.readouterr()
    assert captured.out == "Wash Cam is ready.\n"
    assert captured.err == ""


@pytest.mark.parametrize("arguments", [["--help"], ["dummy", "--help"]])
def test_help_exits_successfully(arguments, monkeypatch, capsys):
    """Show help without running a command."""
    monkeypatch.setattr(sys, "argv", ["wash-cam", *arguments])

    with pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == 0
    captured = capsys.readouterr()
    assert "usage:" in captured.out
    assert "dummy" in captured.out
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
