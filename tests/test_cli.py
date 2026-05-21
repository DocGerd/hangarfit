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
