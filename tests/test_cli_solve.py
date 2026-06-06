"""Tests for the ``hangarfit solve`` subcommand.

Covers spec §5 (CLI surface). The solver itself is a black-box library
function from Chunks A-E; the tests here are scoped to IO + argparse.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
        ``test_solve_exhausted_budget_reports_best_partial_pair``
        pattern (in ``tests/test_solver_search.py``): if a fast
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


class TestSolveFleetHangarOverrides:
    """`--fleet` / `--hangar` override flags exercise both branches of
    :func:`hangarfit.cli._resolve_fleet_hangar_refs` (override + embedded)
    plus the LoaderError collision when both are set.
    """

    # Resolve repo-relative defaults to absolute paths once: scenarios
    # written into tmp_path can't use the original "../../data/..." refs.
    REPO_ROOT = Path(__file__).resolve().parents[1]
    DEFAULT_FLEET = str(REPO_ROOT / "data" / "fleet.yaml")
    DEFAULT_HANGAR = str(REPO_ROOT / "data" / "hangar.yaml")

    def test_fleet_and_hangar_overrides_positive_path(self, tmp_path, capsys):
        """Scenario without embedded refs + both CLI overrides → solves.

        Exercises the `args.fleet is not None` and `args.hangar is not
        None` branches of `_resolve_fleet_hangar_refs` (plus the
        load_scenario override paths).
        """
        scenario = tmp_path / "scenario_no_refs.yaml"
        scenario.write_text("fleet_in: [aviat_husky]\n")
        rc = main(
            [
                "solve",
                str(scenario),
                "--budget",
                "2.0",
                "--seed",
                "42",
                "--fleet",
                self.DEFAULT_FLEET,
                "--hangar",
                self.DEFAULT_HANGAR,
            ]
        )
        assert rc == 0, f"override solve failed; stderr={capsys.readouterr().err}"
        out = capsys.readouterr().out
        assert "Found" in out

    def test_fleet_override_collides_with_embedded_ref(self, tmp_path, capsys):
        """`--fleet` + scenario with `fleet:` → LoaderError → rc=2."""
        rc = main(
            [
                "solve",
                SMOKE_FIXTURE,
                "--budget",
                "0.1",
                "--seed",
                "42",
                "--fleet",
                self.DEFAULT_FLEET,
            ]
        )
        assert rc == 2
        captured = capsys.readouterr()
        assert "error:" in captured.err
        # LoaderError text identifies the ambiguous field.
        assert "fleet" in captured.err

    def test_hangar_override_collides_with_embedded_ref(self, tmp_path, capsys):
        """`--hangar` + scenario with `hangar:` → LoaderError → rc=2."""
        rc = main(
            [
                "solve",
                SMOKE_FIXTURE,
                "--budget",
                "0.1",
                "--seed",
                "42",
                "--hangar",
                self.DEFAULT_HANGAR,
            ]
        )
        assert rc == 2
        captured = capsys.readouterr()
        assert "error:" in captured.err
        assert "hangar" in captured.err


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

    def test_write_yaml_roundtrip_preserves_maintenance_plane(self, tmp_path, capsys):
        """Maintenance round-trip: the `payload["maintenance"] = ...`
        branch at the bottom of ``_write_yamls`` had no coverage because
        the smoke fixture is single-Husky with no maintenance block.

        Pre-condition fence: this test uses an existing maintenance
        fixture as-is — Milestone #9 (maintenance bay walling) is
        deferred per spec §8 and this test does NOT assume walled-bay
        semantics. The current soft-hint bay model is what's under test.
        """
        from hangarfit import loader

        fixture = str(FIXTURES_DIR / "solve_maintenance_bay_required.yaml")
        out = tmp_path / "roundtrip.yaml"
        rc = main(
            [
                "solve",
                fixture,
                "--budget",
                "5.0",
                "--seed",
                "42",
                "--write-yaml",
                str(out),
            ]
        )
        assert rc == 0, f"solve failed (rc={rc}); stderr={capsys.readouterr().err}"
        assert out.exists()
        capsys.readouterr()  # drain solve stdout

        # Round-trip: load the written layout and verify the
        # maintenance plane key survived the dump → parse cycle.
        layout = loader.load_layout(out)
        assert layout.maintenance_plane == "wild_thing"

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
                "--no-spread",
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
                "--no-spread",
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

    def test_render_to_nonexistent_dir_returns_2(self, tmp_path, capsys):
        """OSError during write → rc=2 with `error:` in stderr.

        Covers the ``except OSError`` arm at cli.py wrapping
        ``_write_renders`` / ``_write_yamls`` — currently unexercised
        because every other render/write test routes to a tmp_path
        directory that already exists.
        """
        target = tmp_path / "no_such_dir" / "out.png"
        rc = main(
            [
                "solve",
                SMOKE_FIXTURE,
                "--budget",
                "2.0",
                "--seed",
                "42",
                "--render",
                str(target),
            ]
        )
        assert rc == 2
        captured = capsys.readouterr()
        assert "error:" in captured.err

    def test_k_gt_1_substitutes_i_for_each_alternative(self, tmp_path, capsys):
        """K>1 happy path: ``{i}`` substitutes at every i in 1..K.

        The K=1 substitute test only exercises ``i=1``. This test runs
        ``--alternatives 2`` against a fixture that reliably yields two
        diverse layouts and asserts the enumerate-loop body fires for
        both i=1 AND i=2 (the i>=2 case is the previously-uncovered
        branch).
        """
        fixture = str(FIXTURES_DIR / "solve_fresh_alternatives_three.yaml")
        render_pattern = str(tmp_path / "out_{i}.png")
        yaml_pattern = str(tmp_path / "out_{i}.yaml")
        rc = main(
            [
                "solve",
                fixture,
                "--alternatives",
                "2",
                "--budget",
                "5.0",
                "--seed",
                "42",
                "--render",
                render_pattern,
                "--write-yaml",
                yaml_pattern,
                "--no-spread",
            ]
        )
        assert rc == 0, f"K>1 solve failed (rc={rc}); stderr={capsys.readouterr().err}"
        # Both alternatives must be present — i=1 and the new i=2 branch.
        assert (tmp_path / "out_1.png").exists()
        assert (tmp_path / "out_2.png").exists()
        assert (tmp_path / "out_1.yaml").exists()
        assert (tmp_path / "out_2.yaml").exists()


def test_solve_no_spread_flag_accepted_and_runs(tmp_path):
    """`--no-spread` is accepted on `solve` and drives a successful no-spread run."""
    from hangarfit.cli import main

    out = tmp_path / "layout.yaml"
    rc = main(
        [
            "solve",
            "tests/fixtures/solve_all_nine_large_hangar.yaml",
            "--seed",
            "42",
            "--budget",
            "10",
            "--no-spread",
            "--write-yaml",
            str(out),
        ]
    )
    assert rc == 0
    assert out.exists()


def test_solve_back_fill_defaults_on_and_no_back_fill_opts_out():
    """`--no-back-fill` flips the ``back_fill`` namespace default (#320); absent,
    it defaults ON (the back-of-hangar bias rides the spread post-pass)."""
    from hangarfit.cli import build_parser

    assert build_parser().parse_args(["solve", "s.yaml"]).back_fill is True
    assert build_parser().parse_args(["solve", "s.yaml", "--no-back-fill"]).back_fill is False


def test_solve_no_back_fill_flag_accepted_and_runs(tmp_path):
    """`--no-back-fill` is accepted on `solve` and drives a successful run."""
    from hangarfit.cli import main

    out = tmp_path / "layout.yaml"
    rc = main(
        [
            "solve",
            "tests/fixtures/solve_all_nine_large_hangar.yaml",
            "--seed",
            "42",
            "--budget",
            "10",
            "--no-back-fill",
            "--write-yaml",
            str(out),
        ]
    )
    assert rc == 0
    assert out.exists()


class TestSolveRenderPaths:
    """`--render-paths` flag: tow-path overlay rendering + exit-3 semantics (#193).

    Uses a monkeypatched ``solve`` so exit-code / overlay behaviour is tested
    deterministically and fast, without paying for a real Hybrid-A* search.
    ``load_scenario`` still runs on the real fixture (validation), then the
    fake ``solve`` returns a controlled bundle.
    """

    @staticmethod
    def _layout():
        from hangarfit.loader import load_layout

        return load_layout(FIXTURES_DIR / "valid_two_separated.yaml")

    @staticmethod
    def _plan(layout):
        from hangarfit.towplanner import DubinsArc, Move, MovesPlan, Pose, Segment

        start = Pose(x_m=2.0, y_m=0.0, heading_deg=0.0)
        end = Pose(x_m=2.0, y_m=5.0, heading_deg=0.0)
        arc = DubinsArc(start=start, end=end, turn_radius_m=1.0, segments=(Segment("S", 5.0),))
        return MovesPlan(
            target_layout=layout, moves=(Move(plane_id="a", target_slot=end, path=arc),)
        )

    @staticmethod
    def _result(status, layouts, plans, unroutable=()):
        from hangarfit.models import SolverDiagnostics, SolveResult

        diag = SolverDiagnostics(
            restarts_attempted=1,
            wall_time_s=0.1,
            best_partial=None,
            best_partial_layout=None,
            seed=42,
            unroutable_planes=unroutable,
        )
        return SolveResult(status=status, layouts=layouts, plans=plans, diagnostics=diag)

    @staticmethod
    def _patch_solve(monkeypatch, result):
        import hangarfit.solver as solver_mod

        captured = {}

        def fake_solve(scenario, **kwargs):
            captured.update(kwargs)
            return result

        monkeypatch.setattr(solver_mod, "solve", fake_solve)
        return captured

    def test_flag_defaults_false_and_parses(self):
        parser = build_parser()
        assert parser.parse_args(["solve", SMOKE_FIXTURE]).render_paths is False
        parsed = parser.parse_args(["solve", SMOKE_FIXTURE, "--render", "x.png", "--render-paths"])
        assert parsed.render_paths is True

    def test_render_paths_requires_render(self, capsys):
        rc = main(["solve", SMOKE_FIXTURE, "--render-paths"])
        assert rc == 2
        assert "requires --render" in capsys.readouterr().err

    def test_without_flag_solve_is_not_asked_to_plan(self, tmp_path, monkeypatch):
        captured = self._patch_solve(monkeypatch, self._result("found", (self._layout(),), (None,)))
        rc = main(["solve", SMOKE_FIXTURE, "--render", str(tmp_path / "p.png"), "--seed", "42"])
        assert rc == 0
        assert captured["plan_paths"] is False

    def test_routable_exit_0_and_overlay_passed_to_renderer(self, tmp_path, monkeypatch):
        from hangarfit import visualize

        layout = self._layout()
        plan = self._plan(layout)
        self._patch_solve(monkeypatch, self._result("found", (layout,), (plan,)))
        seen = []
        real = visualize.render_layout

        def spy(lay, path, **kw):
            seen.append(kw.get("moves_plan"))
            return real(lay, path, **kw)

        monkeypatch.setattr(visualize, "render_layout", spy)
        out = tmp_path / "p.png"
        rc = main(["solve", SMOKE_FIXTURE, "--render", str(out), "--render-paths", "--seed", "42"])
        assert rc == 0
        assert seen == [plan]  # the per-layout MovesPlan was threaded through
        assert out.exists() and out.stat().st_size > 0

    def test_all_unroutable_exits_3_warns_and_renders_plain(self, tmp_path, monkeypatch, capsys):
        layout = self._layout()
        self._patch_solve(
            monkeypatch, self._result("found", (layout,), (None,), unroutable=("husky",))
        )
        out = tmp_path / "p.png"
        rc = main(["solve", SMOKE_FIXTURE, "--render", str(out), "--render-paths", "--seed", "42"])
        assert rc == 3
        err = capsys.readouterr().err
        assert "no feasible tow path" in err and "husky" in err
        assert out.exists()  # the valid layout is still rendered, just plain

    def test_partial_mix_exit_0_warns_only_the_unroutable(self, tmp_path, monkeypatch, capsys):
        layout = self._layout()
        plan = self._plan(layout)
        self._patch_solve(
            monkeypatch,
            self._result("found", (layout, layout), (plan, None), unroutable=("b",)),
        )
        pattern = str(tmp_path / "p_{i}.png")
        rc = main(
            [
                "solve",
                SMOKE_FIXTURE,
                "--alternatives",
                "2",
                "--render",
                pattern,
                "--render-paths",
                "--seed",
                "42",
            ]
        )
        assert rc == 0  # >=1 routable candidate
        err = capsys.readouterr().err
        assert "layout 2" in err and "b" in err
        assert "layout 1" not in err
        assert (tmp_path / "p_1.png").exists() and (tmp_path / "p_2.png").exists()

    def test_exit_3_precedes_strict_k(self, tmp_path, monkeypatch):
        layout = self._layout()
        self._patch_solve(
            monkeypatch,
            self._result("found_partial", (layout,), (None,), unroutable=("z",)),
        )
        rc = main(
            [
                "solve",
                SMOKE_FIXTURE,
                "--render",
                str(tmp_path / "p.png"),
                "--render-paths",
                "--strict-k",
                "--seed",
                "42",
            ]
        )
        assert rc == 3  # all-un-routable wins over found_partial+strict-k

    def test_json_surfaces_unroutable_planes(self, tmp_path, monkeypatch, capsys):
        layout = self._layout()
        self._patch_solve(
            monkeypatch, self._result("found", (layout,), (None,), unroutable=("husky",))
        )
        rc = main(
            [
                "solve",
                SMOKE_FIXTURE,
                "--render",
                str(tmp_path / "p.png"),
                "--render-paths",
                "--json",
                "--seed",
                "42",
            ]
        )
        assert rc == 3
        payload = json.loads(capsys.readouterr().out)
        assert payload["diagnostics"]["unroutable_planes"] == ["husky"]

    def test_multi_none_warnings_name_planes_in_order(self, tmp_path, monkeypatch, capsys):
        # Three layouts, the 1st and 3rd un-routable: the compacted
        # unroutable_planes (["alpha","gamma"]) must pair with the 1st and 3rd
        # None positions in order — guards the zip/correspondence in
        # _warn_unroutable that a single-None test cannot.
        layout = self._layout()
        plan = self._plan(layout)
        self._patch_solve(
            monkeypatch,
            self._result(
                "found",
                (layout, layout, layout),
                (None, plan, None),
                unroutable=("alpha", "gamma"),
            ),
        )
        rc = main(
            [
                "solve",
                SMOKE_FIXTURE,
                "--alternatives",
                "3",
                "--render",
                str(tmp_path / "p_{i}.png"),
                "--render-paths",
                "--seed",
                "42",
            ]
        )
        assert rc == 0  # layout 2 routable
        err = capsys.readouterr().err
        assert "layout 1" in err and "alpha" in err
        assert "layout 3" in err and "gamma" in err
        assert "layout 2" not in err
        # Order: layout 1's warning (alpha) precedes layout 3's (gamma).
        assert err.index("alpha") < err.index("gamma")

    def test_unroutable_planes_desync_is_loud(self, tmp_path, monkeypatch):
        # A None plan with an empty unroutable_planes is a producer-side
        # invariant violation (solver appends one plane per None). _warn_unroutable
        # must surface it loudly, not paper over it with a placeholder name.
        layout = self._layout()
        self._patch_solve(monkeypatch, self._result("found", (layout,), (None,), unroutable=()))
        with pytest.raises(AssertionError, match="out of sync"):
            main(
                [
                    "solve",
                    SMOKE_FIXTURE,
                    "--render",
                    str(tmp_path / "p.png"),
                    "--render-paths",
                    "--seed",
                    "42",
                ]
            )

    def test_empty_layouts_exits_1_not_3(self, tmp_path, monkeypatch):
        # No layouts (exhausted_budget): exit 1 wins over the exit-3 check even
        # under --render-paths (the all-None test would otherwise be vacuously
        # true on empty plans). Pins the "no layouts > no-tow-order" precedence.
        self._patch_solve(monkeypatch, self._result("exhausted_budget", (), ()))
        rc = main(
            [
                "solve",
                SMOKE_FIXTURE,
                "--render",
                str(tmp_path / "p.png"),
                "--render-paths",
                "--seed",
                "42",
            ]
        )
        assert rc == 1

    @pytest.mark.slow
    def test_real_solve_render_paths_end_to_end(self, tmp_path):
        # Real solve -> plan_fill -> render_layout(moves_plan=...), no monkeypatch:
        # guards the live integration the monkeypatched tests can't (the single
        # smoke-fixture plane is towable). rc 0 (routable) or 3 (if not); either
        # way the pipeline must run and write a PNG.
        out = tmp_path / "real.png"
        rc = main(
            [
                "solve",
                SMOKE_FIXTURE,
                "--budget",
                "5.0",
                "--seed",
                "42",
                "--render",
                str(out),
                "--render-paths",
            ]
        )
        assert rc in (0, 3)
        assert out.exists() and out.stat().st_size > 0


class TestSolveRenderPathsSpreadFallback:
    """`--render-paths` auto-falls-back to no-spread when the spread layout is
    un-routable (#280). Two individually-correct features (ADR-0008 spread,
    bounded tow planner) compose into a worse default: spread pushes planes into
    positions the bounded Hybrid-A* can no longer thread, so every plan is None
    and the CLI returns a bare exit 3 — even though the same fleet+hangar routes
    cleanly with spread off. The CLI re-solves with spread disabled, reports the
    swap on stderr, and renders the routable arrangement instead.
    """

    @staticmethod
    def _layout():
        from hangarfit.loader import load_layout

        return load_layout(FIXTURES_DIR / "valid_two_separated.yaml")

    @staticmethod
    def _plan(layout):
        from hangarfit.towplanner import DubinsArc, Move, MovesPlan, Pose, Segment

        start = Pose(x_m=2.0, y_m=0.0, heading_deg=0.0)
        end = Pose(x_m=2.0, y_m=5.0, heading_deg=0.0)
        arc = DubinsArc(start=start, end=end, turn_radius_m=1.0, segments=(Segment("S", 5.0),))
        return MovesPlan(
            target_layout=layout, moves=(Move(plane_id="a", target_slot=end, path=arc),)
        )

    @staticmethod
    def _result(status, layouts, plans, unroutable=()):
        from hangarfit.models import SolverDiagnostics, SolveResult

        diag = SolverDiagnostics(
            restarts_attempted=1,
            wall_time_s=0.1,
            best_partial=None,
            best_partial_layout=None,
            seed=42,
            unroutable_planes=unroutable,
        )
        return SolveResult(status=status, layouts=layouts, plans=plans, diagnostics=diag)

    @staticmethod
    def _patch_solve_sequence(monkeypatch, results):
        """Patch ``solve`` to return ``results[i]`` on the i-th call.

        Records every call's ``search`` SearchConfig + ``seed`` so a test can
        assert the fallback re-solve disabled spread while keeping the seed
        (determinism). Calling more than ``len(results)`` times is a test-setup
        error and raises.
        """
        import hangarfit.solver as solver_mod

        calls: list[dict] = []
        seq = list(results)

        def fake_solve(scenario, **kwargs):
            calls.append(kwargs)
            if not seq:
                raise AssertionError("solve() called more times than results provided")
            return seq.pop(0)

        monkeypatch.setattr(solver_mod, "solve", fake_solve)
        return calls

    def test_spread_unroutable_falls_back_to_no_spread_and_routes(
        self, tmp_path, monkeypatch, capsys
    ):
        # Spread layout un-routable (all-None) -> re-solve with spread off,
        # which routes -> exit 0, paths rendered, swap reported on stderr.
        from hangarfit.models import SearchConfig

        layout = self._layout()
        plan = self._plan(layout)
        calls = self._patch_solve_sequence(
            monkeypatch,
            [
                self._result("found", (layout,), (None,), unroutable=("fuji",)),
                self._result("found", (layout,), (plan,)),
            ],
        )
        out = tmp_path / "p.png"
        rc = main(["solve", SMOKE_FIXTURE, "--render", str(out), "--render-paths", "--seed", "5"])
        assert rc == 0
        err = capsys.readouterr().err
        # The swap is reported, never silent (#280 acceptance).
        assert "spread" in err and "re-solved" in err
        # Two solve() calls: spread ON, then the fallback with spread OFF.
        assert len(calls) == 2
        assert isinstance(calls[0]["search"], SearchConfig) and calls[0]["search"].spread is True
        assert calls[1]["search"].spread is False
        # Determinism: the fallback re-solve reuses the same resolved seed.
        assert calls[0]["seed"] == calls[1]["seed"] == 5
        assert out.exists()

    def test_explicit_no_spread_does_not_retry(self, tmp_path, monkeypatch, capsys):
        # The user already asked for --no-spread; there is nothing to fall back
        # FROM. One solve() call, exit 3 stands, no swap note.
        layout = self._layout()
        calls = self._patch_solve_sequence(
            monkeypatch, [self._result("found", (layout,), (None,), unroutable=("fuji",))]
        )
        out = tmp_path / "p.png"
        rc = main(
            [
                "solve",
                SMOKE_FIXTURE,
                "--render",
                str(out),
                "--render-paths",
                "--no-spread",
                "--seed",
                "5",
            ]
        )
        assert rc == 3
        assert len(calls) == 1
        assert calls[0]["search"].spread is False
        assert "re-solved" not in capsys.readouterr().err

    def test_fallback_also_unroutable_keeps_exit_3_no_misleading_note(
        self, tmp_path, monkeypatch, capsys
    ):
        # Genuinely too tight (e.g. placeholder hangar): both spread and
        # no-spread route nothing. The fallback ran but did not help, so we keep
        # the original spread result and exit 3 — and must NOT claim a swap that
        # did not happen.
        layout = self._layout()
        calls = self._patch_solve_sequence(
            monkeypatch,
            [
                self._result("found", (layout,), (None,), unroutable=("fuji",)),
                self._result("found", (layout,), (None,), unroutable=("fuji",)),
            ],
        )
        out = tmp_path / "p.png"
        rc = main(["solve", SMOKE_FIXTURE, "--render", str(out), "--render-paths", "--seed", "5"])
        assert rc == 3
        assert len(calls) == 2  # the fallback was attempted
        assert "re-solved" not in capsys.readouterr().err  # but not claimed

    def test_spread_routable_does_not_retry(self, tmp_path, monkeypatch, capsys):
        # Spread layout is already routable -> no fallback, no note, exit 0,
        # spread arrangement preserved.
        layout = self._layout()
        plan = self._plan(layout)
        calls = self._patch_solve_sequence(monkeypatch, [self._result("found", (layout,), (plan,))])
        out = tmp_path / "p.png"
        rc = main(["solve", SMOKE_FIXTURE, "--render", str(out), "--render-paths", "--seed", "5"])
        assert rc == 0
        assert len(calls) == 1  # no re-solve
        assert "re-solved" not in capsys.readouterr().err

    def test_json_surfaces_spread_fallback_applied_true_after_swap(
        self, tmp_path, monkeypatch, capsys
    ):
        # #280 — non-interactive consumers must get a structured signal that the
        # tighter no-spread layout was substituted, not just the human stderr
        # note. After a successful fallback swap, --json carries True.
        layout = self._layout()
        plan = self._plan(layout)
        self._patch_solve_sequence(
            monkeypatch,
            [
                self._result("found", (layout,), (None,), unroutable=("fuji",)),
                self._result("found", (layout,), (plan,)),
            ],
        )
        out = tmp_path / "p.png"
        rc = main(
            [
                "solve",
                SMOKE_FIXTURE,
                "--render",
                str(out),
                "--render-paths",
                "--json",
                "--seed",
                "5",
            ]
        )
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["diagnostics"]["spread_fallback_applied"] is True

    def test_json_spread_fallback_applied_false_when_no_swap(self, tmp_path, monkeypatch, capsys):
        # The normal --render-paths --json case (spread layout routes on the
        # first try): the field is present and False so consumers can rely on it.
        layout = self._layout()
        plan = self._plan(layout)
        self._patch_solve_sequence(monkeypatch, [self._result("found", (layout,), (plan,))])
        out = tmp_path / "p.png"
        rc = main(
            [
                "solve",
                SMOKE_FIXTURE,
                "--render",
                str(out),
                "--render-paths",
                "--json",
                "--seed",
                "5",
            ]
        )
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["diagnostics"]["spread_fallback_applied"] is False

    def test_write_yaml_carries_provenance_header_after_swap(self, tmp_path, monkeypatch, capsys):
        # #280 — a human reading the written .yaml later must see that it is the
        # tighter no-spread arrangement, not what a plain spread solve yields.
        # When --render-paths swaps in the no-spread fallback, --write-yaml emits
        # a leading `# note:` provenance comment. Paired with the no-swap case
        # below, this covers both branches of the `spread_fallback_applied`
        # guard in `_write_yamls`.
        layout = self._layout()
        plan = self._plan(layout)
        self._patch_solve_sequence(
            monkeypatch,
            [
                self._result("found", (layout,), (None,), unroutable=("fuji",)),
                self._result("found", (layout,), (plan,)),
            ],
        )
        out_png = tmp_path / "p.png"
        out_yaml = tmp_path / "fallback.yaml"
        rc = main(
            [
                "solve",
                SMOKE_FIXTURE,
                "--render",
                str(out_png),
                "--render-paths",
                "--write-yaml",
                str(out_yaml),
                "--seed",
                "5",
            ]
        )
        assert rc == 0, f"expected fallback to route; stderr={capsys.readouterr().err}"
        assert out_yaml.exists()
        contents = out_yaml.read_text()
        assert (
            "# note: produced with inter-plane spread disabled (auto-fallback, see #280)"
            in contents
        )

    def test_write_yaml_no_provenance_header_when_no_swap(self, tmp_path, monkeypatch, capsys):
        # The normal no-swap path: the spread layout routes on the first try, so
        # the written .yaml must NOT carry the fallback provenance header (False
        # branch of the `spread_fallback_applied` guard).
        layout = self._layout()
        plan = self._plan(layout)
        self._patch_solve_sequence(monkeypatch, [self._result("found", (layout,), (plan,))])
        out_png = tmp_path / "p.png"
        out_yaml = tmp_path / "no_swap.yaml"
        rc = main(
            [
                "solve",
                SMOKE_FIXTURE,
                "--render",
                str(out_png),
                "--render-paths",
                "--write-yaml",
                str(out_yaml),
                "--seed",
                "5",
            ]
        )
        assert rc == 0
        assert out_yaml.exists()
        assert "auto-fallback, see #280" not in out_yaml.read_text()

    def test_real_solve_spread_fallback_end_to_end(self, tmp_path, monkeypatch, capsys):
        # Deterministic, CI-runnable integration of the #280 fallback against the
        # REAL solver + REAL tow planner.
        #
        # The *natural* trigger — a spread layout the bounded Hybrid-A* can't route
        # while no-spread can — is a narrow band that SHIFTS as towability improves.
        # This test previously pinned seed=5 to a wall-clock ``--budget`` and rotted
        # (#457): improved routing made that spread layout directly routable, so the
        # fallback never fired, stderr was empty, and the assertion failed — yet the
        # failure stayed invisible because the test was ``@slow`` and CI runs
        # ``-m 'not slow'``. Determinism is ``max_restarts``-scoped (ADR-0003), NOT
        # wall-clock-scoped, and the CLI's only knob is ``--budget``, so a real,
        # ``--budget``-driven trigger is inherently machine-dependent.
        #
        # Fix: force ONLY the spread pass to report no plans, via a thin wrapper that
        # otherwise delegates to the real ``solve()``. That reproduces the #280
        # condition (all plans ``None`` on the spread layout) deterministically while
        # keeping the real placement and — crucially — the real no-spread routing, so
        # the live cli.py fallback wiring (re-solve with spread off → swap → stderr
        # note → exit 0 → render) is exercised end-to-end without depending on the
        # fragile trigger band. The fully-mocked sibling tests above cover the same
        # control flow on synthetic data; this one proves it on a real solve+route,
        # and is deliberately NOT ``@slow`` so CI actually runs it.
        #
        # Two choices make the outcome wall-clock-INDEPENDENT (so it cannot flake
        # under CPU contention the way the old budget-pinned version could):
        #   1. The trivial single-plane fixture — placement is the same control flow
        #      regardless of plane count (this test exercises the cli FALLBACK wiring,
        #      not multi-plane spread geometry, whose coverage #457 established is dead
        #      anyway), and a one-plane layout is found in the very first restart.
        #   2. The intercepted spread pass is re-bounded to a deterministic
        #      ``max_restarts=1`` so a *valid layout* is produced after exactly one
        #      restart — a fixed amount of WORK, not a wall-clock race. ``--budget``
        #      is therefore a non-binding ceiling here (the no-spread fallback pass
        #      first-valid early-exits too), and the test is fast (~0.3 s).
        import dataclasses

        import hangarfit.solver as solver_mod

        real_solve = solver_mod.solve

        def spread_pass_unroutable(scenario, **kwargs):
            search = kwargs.get("search")
            if search is not None and search.spread and kwargs.get("plan_paths"):
                # The bounded planner "routes nothing" for the spread arrangement —
                # exactly the case #280's fallback exists to rescue. Run placement
                # ONLY (skip the expensive Hybrid-A* routing whose result we would
                # discard anyway — routing a hard spread layout floods the expansion
                # budget, the very cost #280 sidesteps), bounded to a single
                # deterministic restart so producing a valid layout is wall-clock-
                # independent, then synthesize all-None plans (one per valid layout)
                # so the fallback trigger (`result.layouts and all(plan is None …)`)
                # holds.
                one_restart = dataclasses.replace(search, max_restarts=1)
                result = real_solve(
                    scenario, **{**kwargs, "search": one_restart, "plan_paths": False}
                )
                return dataclasses.replace(result, plans=tuple(None for _ in result.layouts))
            return real_solve(scenario, **kwargs)

        monkeypatch.setattr(solver_mod, "solve", spread_pass_unroutable)

        out = tmp_path / "real.png"
        rc = main(
            [
                "solve",
                str(FIXTURES_DIR / "solve_trivial_single_plane.yaml"),
                "--seed",
                "5",
                "--budget",
                "5.0",
                "--render",
                str(out),
                "--render-paths",
            ]
        )
        err = capsys.readouterr().err
        assert rc == 0, f"expected the real no-spread fallback to route; stderr={err}"
        assert "spread" in err and "re-solved" in err
        assert out.exists() and out.stat().st_size > 0
