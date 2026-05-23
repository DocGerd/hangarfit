"""Tests for the ``hangarfit solve`` subcommand.

Covers spec §5 (CLI surface). The solver itself is a black-box library
function from Chunks A-E; the tests here are scoped to IO + argparse.
"""

from __future__ import annotations

import json
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


class TestSolveHumanOutput:
    """Spec §5.3 — human stdout for each status."""

    def test_human_output_found(self, capsys):
        rc = main(["solve", SMOKE_FIXTURE, "--budget", "2.0", "--seed", "42"])
        assert rc == 0
        out = capsys.readouterr().out
        # First line: status summary with count, time, seed, restarts.
        assert "Found 1 layout" in out
        assert "seed=42" in out
        assert "restart" in out
        # Per-layout line includes plane count + conflict count + score.
        assert "#1:" in out
        assert "0 conflicts" in out
        assert "score=" in out

    def test_human_output_trivially_infeasible(self, capsys, tmp_path):
        # Pin two carted planes to the same spot -> trivially infeasible.
        fixture = str(FIXTURES_DIR / "solve_infeasible_pins_clash.yaml")
        rc = main(["solve", fixture, "--budget", "1.0", "--seed", "42"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "Trivially infeasible" in out

    def test_human_output_trivially_infeasible_plane_too_big(self, capsys):
        # Plane bigger than hangar -> trivially infeasible via check #1
        # (per-plane bbox > max hangar dim). Pairs with the pins-clash
        # variant above to cover both pre-search infeasibility kinds.
        fixture = str(FIXTURES_DIR / "solve_infeasible_plane_too_big.yaml")
        rc = main(["solve", fixture, "--budget", "1.0", "--seed", "42"])
        assert rc == 1
        out = capsys.readouterr().out
        # plane_too_big hits the per-plane infeasibility check (#1), so
        # it surfaces as trivially_infeasible too.
        assert "Trivially infeasible" in out

    def test_human_output_exhausted_budget(self, capsys):
        """Real `exhausted_budget` branch of `_emit_solve_human`.

        Uses ``solve_fresh_six_planes.yaml`` (the six-plane fixture
        already used by ``test_solver_search`` for the same purpose)
        with a tiny budget so the solver almost certainly exhausts.
        Skip-on-lucky guard follows the
        ``tests/test_solver_search.py:355-391`` pattern: if a fast
        machine accidentally finds a layout we skip rather than fail.
        """
        import pytest

        fixture = str(FIXTURES_DIR / "solve_fresh_six_planes.yaml")
        rc = main(["solve", fixture, "--budget", "0.05", "--seed", "42"])
        out = capsys.readouterr().out
        if rc == 0 and "Found" in out:
            pytest.skip("seed=42 + 0.05s got lucky; tighten budget if this skips often")
        assert rc == 1
        # Three diagnostic lines printed by the exhausted_budget branch:
        assert "No valid layout found in" in out
        assert "Best partial had" in out and "conflict" in out
        assert "Hint: increase --budget" in out


class TestSolveJsonOutput:
    """Spec §5.4 — hangarfit.solve/v1 schema."""

    def test_json_schema_and_top_level_keys(self, capsys):
        rc = main(["solve", SMOKE_FIXTURE, "--budget", "2.0", "--seed", "42", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["schema"] == "hangarfit.solve/v1"
        assert payload["scenario"] == SMOKE_FIXTURE
        assert payload["status"] == "found"
        assert isinstance(payload["layouts"], list)
        assert len(payload["layouts"]) == 1
        assert "diagnostics" in payload

    def test_json_layout_placements_shape(self, capsys):
        rc = main(["solve", SMOKE_FIXTURE, "--budget", "2.0", "--seed", "42", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        layout = payload["layouts"][0]
        assert "placements" in layout
        assert "maintenance_plane" in layout
        placement = layout["placements"][0]
        assert set(placement) == {"plane", "x_m", "y_m", "heading_deg", "on_carts"}
        assert placement["plane"] == "aviat_husky"
        assert isinstance(placement["on_carts"], bool)

    def test_json_diagnostics_shape(self, capsys):
        rc = main(["solve", SMOKE_FIXTURE, "--budget", "2.0", "--seed", "42", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        d = payload["diagnostics"]
        assert d["seed"] == 42
        assert d["restarts_attempted"] >= 1
        assert isinstance(d["wall_time_s"], float)
        # best_partial / best_partial_layout: spec §5.4 says null for `found`.
        assert d["best_partial"] is None
        assert d["best_partial_layout"] is None

    def test_json_trivially_infeasible_carries_best_partial(self, capsys):
        fixture = str(FIXTURES_DIR / "solve_infeasible_plane_too_big.yaml")
        rc = main(["solve", fixture, "--budget", "1.0", "--seed", "42", "--json"])
        assert rc == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "trivially_infeasible"
        assert payload["layouts"] == []
        d = payload["diagnostics"]
        assert d["best_partial"] is not None
        # best_partial mirrors hangarfit.check/v1 conflicts structure.
        assert "conflicts" in d["best_partial"]
        assert len(d["best_partial"]["conflicts"]) >= 1
        first = d["best_partial"]["conflicts"][0]
        assert set(first) == {"kind", "planes", "detail"}


class TestSolveRender:
    """--render PATTERN flag — {i} substitution + early validation."""

    def test_render_k1_no_braces_ok(self, tmp_path, capsys):
        out = tmp_path / "single.png"
        rc = main(
            [
                "solve",
                SMOKE_FIXTURE,
                "--budget",
                "2.0",
                "--seed",
                "42",
                "--render",
                str(out),
            ]
        )
        assert rc == 0
        assert out.exists()
        assert out.stat().st_size > 0

    def test_render_k1_with_braces_substitutes_1(self, tmp_path, capsys):
        pattern = str(tmp_path / "layout_{i}.png")
        rc = main(
            [
                "solve",
                SMOKE_FIXTURE,
                "--budget",
                "2.0",
                "--seed",
                "42",
                "--render",
                pattern,
            ]
        )
        assert rc == 0
        assert (tmp_path / "layout_1.png").exists()

    def test_write_yaml_k1_no_braces_creates_file(self, tmp_path, capsys):
        out = tmp_path / "single.yaml"
        rc = main(
            [
                "solve",
                SMOKE_FIXTURE,
                "--budget",
                "2.0",
                "--seed",
                "42",
                "--write-yaml",
                str(out),
            ]
        )
        assert rc == 0
        assert out.exists()

    def test_write_yaml_roundtrips_via_check(self, tmp_path, capsys):
        out = tmp_path / "out.yaml"
        rc = main(
            [
                "solve",
                SMOKE_FIXTURE,
                "--budget",
                "2.0",
                "--seed",
                "42",
                "--write-yaml",
                str(out),
            ]
        )
        assert rc == 0
        # The written layout YAML must be loadable + valid via hangarfit check.
        capsys.readouterr()  # drain solve output
        rc2 = main(["check", str(out)])
        assert rc2 == 0
        check_out = capsys.readouterr().out
        assert "valid" in check_out

    def test_strict_k_flips_found_partial_to_1(self, tmp_path, capsys):
        # diversity_impossible fixture: K=3 requested but only 1 free
        # plane → found_partial with 1 layout. Default exit code is 0;
        # --strict-k flips it to 1.
        fixture = str(FIXTURES_DIR / "solve_diversity_impossible_warn.yaml")
        rc = main(
            [
                "solve",
                fixture,
                "--alternatives",
                "3",
                "--budget",
                "2.0",
                "--seed",
                "42",
            ]
        )
        assert rc == 0
        capsys.readouterr()

        rc_strict = main(
            [
                "solve",
                fixture,
                "--alternatives",
                "3",
                "--budget",
                "2.0",
                "--seed",
                "42",
                "--strict-k",
            ]
        )
        assert rc_strict == 1

    def test_strict_k_leaves_found_at_0(self, tmp_path, capsys):
        # `found` (full K) stays exit 0 even with --strict-k — only
        # found_partial flips.
        rc = main(
            [
                "solve",
                SMOKE_FIXTURE,
                "--budget",
                "2.0",
                "--seed",
                "42",
                "--strict-k",
            ]
        )
        assert rc == 0

    def test_write_yaml_k_gt_1_without_braces_returns_2(self, tmp_path, capsys):
        out = tmp_path / "noplaceholder.yaml"
        rc = main(
            [
                "solve",
                SMOKE_FIXTURE,
                "--alternatives",
                "3",
                "--budget",
                "2.0",
                "--seed",
                "42",
                "--write-yaml",
                str(out),
            ]
        )
        assert rc == 2
        captured = capsys.readouterr()
        assert "--write-yaml" in captured.err
        assert "{i}" in captured.err

    def test_render_k_gt_1_without_braces_returns_2(self, tmp_path, capsys):
        # Validation fires BEFORE solve() — no PNG written, no solve cost.
        out = tmp_path / "noplaceholder.png"
        rc = main(
            [
                "solve",
                SMOKE_FIXTURE,
                "--alternatives",
                "3",
                "--budget",
                "2.0",
                "--seed",
                "42",
                "--render",
                str(out),
            ]
        )
        assert rc == 2
        captured = capsys.readouterr()
        assert "{i}" in captured.err
        assert "--render" in captured.err
        assert not out.exists()
