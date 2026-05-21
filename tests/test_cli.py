"""Tests for the hangarfit CLI."""

from __future__ import annotations

from pathlib import Path

import pytest

from hangarfit.cli import main

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


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
        exit_code = main(["check", str(FIXTURES_DIR / "valid_two_separated.yaml")])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == "valid"
        assert captured.err == ""

    def test_check_invalid_layout_returns_1(self, capsys):
        exit_code = main(["check", str(FIXTURES_DIR / "invalid_fuselage_wing_overlap.yaml")])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.out.startswith("invalid:")
        # Conflict line uses the spec's format: "  - <kind> [<plane>[, <plane>]]: <detail>"
        assert "fuselage_wing_overlap" in captured.out
        # Every conflict line starts with the two-space-dash prefix
        for line in captured.out.strip().split("\n")[1:]:
            assert line.startswith("  - ")
        assert captured.err == ""


class TestCheckLoadErrors:
    """LoaderError (file not found, bad YAML, invariant violation) → exit 2 on stderr."""

    def test_check_missing_file_returns_2(self, capsys):
        exit_code = main(["check", "definitely/does/not/exist.yaml"])
        assert exit_code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "error:" in captured.err
        assert "not found" in captured.err

    def test_check_malformed_yaml_returns_2(self, tmp_path, capsys):
        bad = tmp_path / "bad.yaml"
        bad.write_text(":::not valid yaml:::\n", encoding="utf-8")
        exit_code = main(["check", str(bad)])
        assert exit_code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "error:" in captured.err

    def test_check_invariant_violation_returns_2(self, capsys):
        # invalid_cart_rule.yaml puts two cart_eligible planes on_carts.
        # Layout.__post_init__ raises ValueError; loader wraps it in LoaderError.
        exit_code = main(["check", str(FIXTURES_DIR / "invalid_cart_rule.yaml")])
        assert exit_code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "error:" in captured.err
        assert "cart" in captured.err.lower()
