"""Tests for the ``hangarfit view`` subcommand."""

from __future__ import annotations

import pytest

from hangarfit.cli import build_parser, main

NESTING = "tests/fixtures/valid_left_side_nesting.yaml"


def test_view_apron_depth_parses_number_and_auto():
    parser = build_parser()

    def _apron(*extra):
        return parser.parse_args(["view", NESTING, "-o", "x.html", *extra]).apron_depth

    assert _apron("--apron-depth", "6") == 6.0
    assert _apron("--apron-depth", "auto") == "auto"
    assert _apron() is None


def test_view_apron_depth_garbage_exits_2():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["view", NESTING, "-o", "x.html", "--apron-depth", "wat"])
    assert exc.value.code == 2


def test_view_with_apron_writes_html(tmp_path):
    """End-to-end: view a layout with an apron — the slide-in path renders (or
    degrades to static); either way it writes a valid offline HTML, rc 0."""
    out = tmp_path / "apron.html"
    rc = main(["view", NESTING, "-o", str(out), "--apron-depth", "6"])
    assert rc == 0
    assert out.exists()
    assert out.read_text(encoding="utf-8").lstrip().startswith("<!DOCTYPE html>")


def test_view_layout_writes_html(tmp_path, capsys):
    out = tmp_path / "v.html"
    rc = main(["view", NESTING, "-o", str(out)])
    assert rc == 0
    assert out.exists()
    assert out.read_text(encoding="utf-8").lstrip().startswith("<!DOCTYPE html>")
    assert "wrote 3D viewer" in capsys.readouterr().out


def test_view_requires_output():
    with pytest.raises(SystemExit) as exc:  # argparse errors exit(2)
        main(["view", NESTING])
    assert exc.value.code == 2


def test_view_no_animate_static(tmp_path):
    out = tmp_path / "v.html"
    rc = main(["view", NESTING, "-o", str(out), "--no-animate"])
    assert rc == 0 and out.exists()


def test_view_static_degradation_on_untowable(tmp_path, capsys):
    # example.yaml is not tow-routable; a small expansion budget makes plan_fill
    # fail fast so this exercises the degradation path without burning the full
    # per-plane search budget.
    out = tmp_path / "v.html"
    rc = main(
        ["view", "examples/layouts/example.yaml", "-o", str(out), "--tow-max-expansions", "300"]
    )
    assert rc == 0 and out.exists()
    assert "not tow-routable" in capsys.readouterr().err


def test_view_layout_caps_total_expansions_by_default(tmp_path, monkeypatch):
    # #398: view layout-mode must pass a small *global* expansion cap by default
    # so an un-routable layout degrades to static within the cap rather than
    # riding the full ~16000-expansion disprove budget (~2 min). The bound is the
    # deterministic expansion count, NOT a wall-clock deadline (ADR-0003).
    import hangarfit.towplanner as tp
    from hangarfit.models import Conflict

    captured: dict = {}

    def fake_plan_fill(_target, **kwargs):
        captured.update(kwargs)
        raise tp.NoFeasiblePlanError(
            "p", Conflict.single(kind="no_feasible_path", plane="p", detail="stub")
        )

    monkeypatch.setattr(tp, "plan_fill", fake_plan_fill)
    out = tmp_path / "v.html"
    rc = main(["view", "examples/layouts/example.yaml", "-o", str(out)])
    assert rc == 0 and out.exists()
    assert captured["max_total_expansions"] == 300


def test_view_tow_max_expansions_overrides_view_cap(tmp_path, monkeypatch):
    # #398 AC4: an explicit --tow-max-expansions overrides the view-mode default
    # cap, bounding both the per-plane and the global view-degrade budget.
    import hangarfit.towplanner as tp
    from hangarfit.models import Conflict

    captured: dict = {}

    def fake_plan_fill(_target, **kwargs):
        captured.update(kwargs)
        raise tp.NoFeasiblePlanError(
            "p", Conflict.single(kind="no_feasible_path", plane="p", detail="stub")
        )

    monkeypatch.setattr(tp, "plan_fill", fake_plan_fill)
    out = tmp_path / "v.html"
    rc = main(
        ["view", "examples/layouts/example.yaml", "-o", str(out), "--tow-max-expansions", "500"]
    )
    assert rc == 0 and out.exists()
    assert captured["max_total_expansions"] == 500
    assert captured["max_expansions"] == 500


def test_view_check_overlay(tmp_path):
    # --no-animate: we are testing the conflict overlay, not the tow animation
    # (and tow-planning an invalid layout would just burn the search budget).
    out = tmp_path / "v.html"
    rc = main(
        [
            "view",
            "tests/fixtures/invalid_fuselage_wing_overlap.yaml",
            "-o",
            str(out),
            "--check",
            "--no-animate",
        ]
    )
    assert rc == 0 and out.exists()


def test_view_solve_mode(tmp_path, capsys):
    # scenario_minimal solves in well under a second; bound with seed+budget so
    # it stays fast/deterministic and runs in CI's default (non-slow) suite.
    # --check exercises the layout-only-flag note path under --solve.
    out = tmp_path / "v.html"
    rc = main(
        [
            "view",
            "--solve",
            "tests/fixtures/scenario_minimal.yaml",
            "-o",
            str(out),
            "--check",
            "--seed",
            "1",
            "--budget",
            "5",
        ]
    )
    assert rc == 0 and out.exists()
    assert "--check is ignored with --solve" in capsys.readouterr().err


def test_view_solve_no_layout_returns_1(tmp_path, capsys):
    # A trivially-infeasible scenario → solver returns no layouts → exit 1
    # (distinct from the bad-file exit 2). Trivially infeasible is instant.
    out = tmp_path / "v.html"
    rc = main(
        ["view", "--solve", "tests/fixtures/solve_infeasible_plane_too_big.yaml", "-o", str(out)]
    )
    assert rc == 1
    assert "no valid layout" in capsys.readouterr().err


def test_view_solve_untowable_degrades_to_static(tmp_path, capsys, monkeypatch):
    # Solver finds a layout but the bundled tow plan is None (un-routable) →
    # the "not tow-routable" degradation note + a static scene. Monkeypatch
    # solve() to return that shape deterministically (the real planner routes
    # the small fixtures, so we don't depend on its internals here).
    import hangarfit.solver as solver_mod
    from hangarfit.loader import load_layout
    from hangarfit.models import SolverDiagnostics, SolveResult

    lay = load_layout(NESTING)
    diag = SolverDiagnostics(
        restarts_attempted=0,
        wall_time_s=0.0,
        best_partial=None,
        best_partial_layout=None,
        seed=1,
    )
    fake = SolveResult(status="found", layouts=(lay,), diagnostics=diag, plans=(None,))
    monkeypatch.setattr(solver_mod, "solve", lambda *_a, **_k: fake)

    out = tmp_path / "v.html"
    rc = main(["view", "--solve", "tests/fixtures/scenario_minimal.yaml", "-o", str(out)])
    assert rc == 0 and out.exists()
    assert "not tow-routable" in capsys.readouterr().err


def test_view_bad_input_returns_2(tmp_path):
    out = tmp_path / "v.html"
    rc = main(["view", "tests/fixtures/does_not_exist.yaml", "-o", str(out)])
    assert rc == 2


def test_view_unwritable_output_returns_2(tmp_path):
    # render_viewer's write raises OSError (nonexistent parent dir) → exit 2.
    # --no-animate keeps it fast (skips tow planning).
    out = tmp_path / "nope" / "deeper" / "v.html"
    rc = main(["view", NESTING, "-o", str(out), "--no-animate"])
    assert rc == 2


def test_view_check_populates_conflicts(tmp_path):
    # --check must actually flag conflicts in the scene, not be a silent no-op:
    # assert the conflicting plane ids reach the embedded scene/v1 JSON.
    import json
    import re

    out = tmp_path / "v.html"
    rc = main(
        [
            "view",
            "tests/fixtures/invalid_fuselage_wing_overlap.yaml",
            "-o",
            str(out),
            "--check",
            "--no-animate",
        ]
    )
    assert rc == 0
    block = re.search(r'id="scene">(.*?)</script>', out.read_text(encoding="utf-8"), re.S)
    assert block is not None
    scene = json.loads(block.group(1))
    assert scene["conflicts"]  # non-empty — the overlay is wired, not ignored
