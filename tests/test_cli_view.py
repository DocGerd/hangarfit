"""Tests for the ``hangarfit view`` subcommand."""

from __future__ import annotations

import pytest

from hangarfit.cli import main

NESTING = "tests/fixtures/valid_left_side_nesting.yaml"


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
    rc = main(["view", "layouts/example.yaml", "-o", str(out), "--tow-max-expansions", "300"])
    assert rc == 0 and out.exists()
    assert "not tow-routable" in capsys.readouterr().err


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
