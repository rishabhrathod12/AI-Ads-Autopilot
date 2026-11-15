"""The offline `check-guard` command: report which wordings the action-request guard flags."""

import pytest

from app.cli import print_guard_report


def test_it_reports_each_wording_and_a_summary(capsys: pytest.CaptureFixture[str]) -> None:
    code = print_guard_report(["Pause my worst campaign.", "How is my account doing?", "  ", ""])
    out = capsys.readouterr().out
    assert code == 0
    assert "FLAGGED  Pause my worst campaign." in out
    assert "MISSED   How is my account doing?" in out
    assert "1 of 2 flagged; 1 not flagged" in out  # blank lines are ignored


def test_an_empty_input_is_not_an_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert print_guard_report([]) == 0
    assert "0 of 0 flagged" in capsys.readouterr().out
