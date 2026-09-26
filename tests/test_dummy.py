"""Check the dummy command's public output."""

from wash_cam import dummy


def test_run_prints_ready_message(capsys):
    """Print the readiness message with a trailing newline."""
    dummy.run()

    captured = capsys.readouterr()
    assert captured.out == "Wash Cam is ready.\n"
    assert captured.err == ""
