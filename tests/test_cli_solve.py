"""Tests for the ``hangarfit solve`` subcommand.

Covers spec §5 (CLI surface). The solver itself is a black-box library
function from Chunks A-E; the tests here are scoped to IO + argparse.
"""

from __future__ import annotations

from pathlib import Path

from hangarfit.cli import build_parser, main

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SMOKE_FIXTURE = str(FIXTURES_DIR / "solve_feasible_smoke.yaml")


class TestSolveSubparser:
    """Argparse surface — flags, defaults, presence of subcommand."""

    def test_solve_subcommand_in_parser(self):
        parser = build_parser()
        args = parser.parse_args(["solve", str(FIXTURES_DIR / "solve_feasible_smoke.yaml")])
        assert args.cmd == "solve"
        assert args.scenario == str(FIXTURES_DIR / "solve_feasible_smoke.yaml")

    def test_solve_subcommand_default_flags(self):
        parser = build_parser()
        args = parser.parse_args(["solve", str(FIXTURES_DIR / "solve_feasible_smoke.yaml")])
        assert args.budget == 30.0
        assert args.alternatives == 1
        assert args.seed is None
        assert args.render is None
        assert args.write_yaml is None
        assert args.strict_k is False
        assert args.json is False
        assert args.fleet is None
        assert args.hangar is None

    def test_solve_subcommand_explicit_flags(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "solve",
                "scenario.yaml",
                "--budget",
                "5.0",
                "--alternatives",
                "3",
                "--seed",
                "42",
                "--render",
                "out_{i}.png",
                "--write-yaml",
                "layout_{i}.yaml",
                "--strict-k",
                "--json",
            ]
        )
        assert args.budget == 5.0
        assert args.alternatives == 3
        assert args.seed == 42
        assert args.render == "out_{i}.png"
        assert args.write_yaml == "layout_{i}.yaml"
        assert args.strict_k is True
        assert args.json is True


class TestSolveLoaderErrors:
    """LoaderError → exit 2, message to stderr; no traceback to user."""

    def test_missing_file_returns_2(self, tmp_path, capsys):
        rc = main(["solve", str(tmp_path / "does_not_exist.yaml"), "--budget", "0.1"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "error:" in captured.err
        assert captured.out == ""

    def test_malformed_yaml_returns_2(self, tmp_path, capsys):
        bad = tmp_path / "bad.yaml"
        bad.write_text("fleet: [unclosed list\n")
        rc = main(["solve", str(bad), "--budget", "0.1"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "error:" in captured.err


class TestSolveSmoke:
    """End-to-end happy path on the trivial single-plane fixture."""

    def test_solve_smoke_exit_0_and_human_output(self, capsys):
        rc = main(["solve", SMOKE_FIXTURE, "--budget", "2.0", "--seed", "42"])
        assert rc == 0
        captured = capsys.readouterr()
        assert captured.out != ""
        assert "error:" not in captured.err
