"""Tests for the hangarfit CLI."""

from __future__ import annotations

import pytest

from hangarfit.cli import main


class TestArgparseUsageErrors:
    """Bare / unknown commands fall through to argparse's own SystemExit(2)."""

    def test_subparser_no_command_shows_help(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 2
        # argparse writes its usage error to stderr
        captured = capsys.readouterr()
        assert "usage:" in captured.err.lower()

    def test_unknown_subcommand_returns_2(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["nope"])
        assert exc_info.value.code == 2


class TestCheckHappyPath:
    """Valid layouts exit 0 with a 'valid' line on stdout."""

    def test_check_valid_layout_returns_0(self, capsys):
        exit_code = main(["check", "tests/fixtures/valid_two_separated.yaml"])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == "valid"
        assert captured.err == ""
