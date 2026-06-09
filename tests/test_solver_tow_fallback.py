"""Library-level spread-off tow fallback in :func:`hangarfit.solver.solve` (#402 / F5).

The ADR-0016 spread-off re-solve was previously orchestrated in ``cli.py``, so a
non-CLI caller of ``solve(plan_paths=True)`` bypassed it and could get a
tow-hostile, spread-maximized layout. F5 promotes the fallback into the library
``solve()`` so every caller gets the tow-routable arrangement.

These tests pin the orchestration by stubbing the inner ``_run_solve`` body so
the control flow runs on synthetic results, wall-clock-independently. The final
test exercises the wiring on a *real* solve + route. The fallback is RNG-free
re-selection: it changes *which* valid layout is returned, never *whether* it is
valid (the ``(0, 0.0)`` gate is untouched).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import hangarfit.solver as solver_mod
from hangarfit.loader import load_layout, load_scenario
from hangarfit.models import SearchConfig, SolverDiagnostics, SolveResult
from hangarfit.solver import solve
from hangarfit.towplanner import DubinsArc, Move, MovesPlan, Pose, Segment

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _layout():
    return load_layout(FIXTURES_DIR / "valid_two_separated.yaml")


def _plan(layout):
    start = Pose(x_m=2.0, y_m=0.0, heading_deg=0.0)
    end = Pose(x_m=2.0, y_m=5.0, heading_deg=0.0)
    arc = DubinsArc(start=start, end=end, turn_radius_m=1.0, segments=(Segment("S", 5.0),))
    return MovesPlan(target_layout=layout, moves=(Move(plane_id="a", target_slot=end, path=arc),))


def _result(layouts, plans, apron_drops=()):
    diag = SolverDiagnostics(
        restarts_attempted=1,
        wall_time_s=0.1,
        best_partial=None,
        best_partial_layout=None,
        seed=5,
        apron_shallow_drops=apron_drops,
    )
    return SolveResult(status="found", layouts=layouts, plans=plans, diagnostics=diag)


def _patch_run_solve(monkeypatch, results):
    """Stub ``_run_solve`` to return ``results[i]`` on the i-th call.

    Records each call's kwargs so a test can assert which search config and
    seed each pass used. More calls than results is a test-setup error.
    """
    calls: list[dict] = []
    seq = list(results)

    def fake_run_solve(scenario, **kwargs):
        calls.append(kwargs)
        if not seq:
            raise AssertionError("_run_solve called more times than results provided")
        return seq.pop(0)

    monkeypatch.setattr(solver_mod, "_run_solve", fake_run_solve)
    return calls


def _scenario():
    return load_scenario(str(FIXTURES_DIR / "solve_trivial_single_plane.yaml"))


def test_plan_paths_falls_back_to_no_spread_when_spread_unroutable(monkeypatch):
    layout = _layout()
    plan = _plan(layout)
    calls = _patch_run_solve(
        monkeypatch,
        [_result((layout,), (None,)), _result((layout,), (plan,))],
    )

    result = solve(_scenario(), seed=5, plan_paths=True, search=SearchConfig(spread=True))

    # The routable (no-spread) arrangement is substituted.
    assert result.plans == (plan,)
    assert result.diagnostics.spread_fallback_applied is True
    # Two passes: spread ON, then the fallback with spread OFF.
    assert len(calls) == 2
    assert calls[0]["search"].spread is True
    assert calls[1]["search"].spread is False
    # Determinism: the fallback pass reuses the same resolved seed.
    assert calls[0]["seed"] == calls[1]["seed"] == 5


def test_no_fallback_when_spread_layout_already_routes(monkeypatch):
    layout = _layout()
    plan = _plan(layout)
    calls = _patch_run_solve(monkeypatch, [_result((layout,), (plan,))])

    result = solve(_scenario(), seed=5, plan_paths=True, search=SearchConfig(spread=True))

    assert result.plans == (plan,)
    assert result.diagnostics.spread_fallback_applied is False
    assert len(calls) == 1


def test_explicit_no_spread_does_not_fall_back(monkeypatch):
    # spread already off → nothing to fall back FROM; one pass, flag stays False
    # even though the single result is un-routable.
    layout = _layout()
    calls = _patch_run_solve(monkeypatch, [_result((layout,), (None,))])

    result = solve(_scenario(), seed=5, plan_paths=True, search=SearchConfig(spread=False))

    assert result.plans == (None,)
    assert result.diagnostics.spread_fallback_applied is False
    assert len(calls) == 1
    assert calls[0]["search"].spread is False


def test_fallback_also_unroutable_keeps_original_spread_result(monkeypatch):
    # Genuinely too tight: both spread and no-spread route nothing. The fallback
    # ran but did not help, so keep the original spread result and DO NOT claim a
    # swap that did not happen.
    layout = _layout()
    original = _result((layout,), (None,))
    calls = _patch_run_solve(monkeypatch, [original, _result((layout,), (None,))])

    result = solve(_scenario(), seed=5, plan_paths=True, search=SearchConfig(spread=True))

    assert result is original
    assert result.diagnostics.spread_fallback_applied is False
    assert len(calls) == 2


def test_no_fallback_when_plan_paths_false(monkeypatch):
    # plan_paths=False means the caller never asked for tow plans; the fallback
    # (a tow-routability rescue) must not fire.
    layout = _layout()
    calls = _patch_run_solve(monkeypatch, [_result((layout,), (None,))])

    result = solve(_scenario(), seed=5, plan_paths=False, search=SearchConfig(spread=True))

    assert result.diagnostics.spread_fallback_applied is False
    assert len(calls) == 1


def test_fallback_inherits_max_restarts_not_wall_clock(monkeypatch):
    # The fallback pass must inherit the caller's max_restarts so the rescue is
    # bound by a deterministic restart count (ADR-0003), NOT wall-clock — else a
    # max_restarts-scoped determinism check on the fallback path could flake.
    layout = _layout()
    plan = _plan(layout)
    calls = _patch_run_solve(
        monkeypatch,
        [_result((layout,), (None,)), _result((layout,), (plan,))],
    )

    solve(
        _scenario(),
        seed=5,
        plan_paths=True,
        search=SearchConfig(spread=True, max_restarts=7),
    )

    assert calls[1]["search"].spread is False
    assert calls[1]["search"].max_restarts == 7


def test_discarded_spread_pass_apron_drops_do_not_surface(monkeypatch):
    # #503 phantom-layout guard: the spread pass is valid-but-unroutable (so it is
    # DISCARDED) and reports an apron-shallow drop for a plane the user never gets;
    # the returned fallback pass reports a DIFFERENT drop set. solve() returns the
    # fallback result, so only ITS apron_shallow_drops surface — the discarded
    # spread pass's drops must NOT appear (they describe a layout never returned).
    from hangarfit.models import ApronShallowDrop

    layout = _layout()
    plan = _plan(layout)
    spread_drop = ApronShallowDrop(plane_id="phantom", min_depth_m=9.0)
    fallback_drop = ApronShallowDrop(plane_id="real", min_depth_m=7.0)
    calls = _patch_run_solve(
        monkeypatch,
        [
            _result((layout,), (None,), apron_drops=(spread_drop,)),
            _result((layout,), (plan,), apron_drops=(fallback_drop,)),
        ],
    )

    result = solve(_scenario(), seed=5, plan_paths=True, search=SearchConfig(spread=True))

    assert len(calls) == 2  # both passes ran
    assert result.diagnostics.spread_fallback_applied is True
    # Only the RETURNED (fallback) layout's drop is present; the discarded spread
    # pass's "phantom" drop is gone.
    assert result.diagnostics.apron_shallow_drops == (fallback_drop,)
    drop_ids = [d.plane_id for d in result.diagnostics.apron_shallow_drops]
    assert "phantom" not in drop_ids


def test_real_library_fallback_routes_single_plane(monkeypatch):
    # End-to-end on the REAL solver + REAL planner, wall-clock-independently
    # (mirrors the technique in tests/test_cli_solve.py's real fallback test):
    # force ONLY the spread pass to report no plans via a thin wrapper that
    # otherwise delegates to the real _run_solve, so the real no-spread routing
    # actually runs. Single-plane fixture → found in restart 0; the spread pass
    # is re-bounded to max_restarts=1 so producing a valid layout is a fixed
    # amount of WORK, not a wall-clock race. Deliberately NOT @slow so CI runs it.
    real_run_solve = solver_mod._run_solve

    def spread_pass_unroutable(scenario, **kwargs):
        search = kwargs.get("search")
        if search is not None and search.spread and kwargs.get("plan_paths"):
            one_restart = dataclasses.replace(search, max_restarts=1)
            res = real_run_solve(scenario, **{**kwargs, "search": one_restart, "plan_paths": False})
            return dataclasses.replace(res, plans=tuple(None for _ in res.layouts))
        return real_run_solve(scenario, **kwargs)

    monkeypatch.setattr(solver_mod, "_run_solve", spread_pass_unroutable)

    result = solve(
        _scenario(),
        budget_s=30.0,
        seed=5,
        plan_paths=True,
        search=SearchConfig(spread=True),
    )

    assert result.layouts
    # The real no-spread fallback pass routed the single plane.
    assert any(plan is not None for plan in result.plans)
    assert result.diagnostics.spread_fallback_applied is True
