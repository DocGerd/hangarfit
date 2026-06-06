"""Opt-in spread-stagnation early-exit on the spread-ON path (#404 / F7).

When :class:`~hangarfit.models.SearchConfig.spread_stall_restarts` is set,
``solve()``'s spread-ON restart loop stops once N consecutive restarts fail to
improve the selected set's maximin plan-view gap by ``spread_stall_epsilon_m``.
The stop depends only on the seed-fixed restart sequence + an integer counter
(never wall-clock), so the selected layout is identical for a given seed across
machines — this *narrows* the #267 timing scope rather than widening it
(ADR-0003). The default (``None``) preserves run-to-budget behavior, so the
determinism canaries (which run ``spread=False``) and the byte-identical contract
are untouched.

These run the real solver on a 3-plane fixture (single-plane gaps are
``math.inf`` — nothing to stagnate on), kept cheap via a large epsilon so the
loop exits after a couple of restarts.
"""

from __future__ import annotations

from pathlib import Path

from hangarfit.collisions import check as check_layout
from hangarfit.loader import load_scenario
from hangarfit.models import SearchConfig
from hangarfit.solver import solve

FIXTURES = Path(__file__).resolve().parent / "fixtures"
# 3 free planes in the 30x25 test hangar: spread ON yields finite, improvable
# inter-plane gaps and restart 0 already finds a valid basin.
_THREE_PLANE = FIXTURES / "solve_fresh_alternatives_three.yaml"


def _scenario():
    return load_scenario(str(_THREE_PLANE))


def test_spread_stall_early_exits_and_flags_it() -> None:
    """A large epsilon means no further restart can improve the first complete
    basin's gap, so N=2 stops the spread-ON loop after a few restarts — far
    short of max_restarts — and records ``spread_stall_applied``."""
    result = solve(
        _scenario(),
        budget_s=600.0,  # huge: stagnation / max_restarts are the only gates
        seed=1,
        plan_paths=False,
        search=SearchConfig(
            spread=True,
            max_restarts=20,
            spread_stall_restarts=2,
            spread_stall_epsilon_m=1000.0,
        ),
    )

    assert result.status == "found"
    assert check_layout(result.layouts[0]).valid  # the basin really scores (0, 0.0)
    assert result.diagnostics.spread_stall_applied is True
    # Stopped well before the cap (the whole point of the feature).
    assert result.diagnostics.restarts_attempted < 20


def test_opt_out_default_runs_to_max_restarts() -> None:
    """Default ``spread_stall_restarts=None`` preserves run-to-budget behavior:
    every restart runs and the stall is never flagged (the new code path is fully
    gated behind the opt-in, so the byte-identical default is untouched)."""
    result = solve(
        _scenario(),
        budget_s=600.0,
        seed=1,
        plan_paths=False,
        search=SearchConfig(spread=True, max_restarts=4),  # None stall (default)
    )

    assert result.diagnostics.restarts_attempted == 4
    assert result.diagnostics.spread_stall_applied is False


def test_spread_stall_exit_is_deterministic_across_runs() -> None:
    """The early-exit depends only on the seed-fixed restart sequence + an
    integer counter (never wall-clock), so two same-seed runs are identical —
    the cross-machine reproducibility the issue promises (ADR-0003)."""
    cfg = SearchConfig(
        spread=True,
        max_restarts=20,
        spread_stall_restarts=1,
        spread_stall_epsilon_m=1000.0,
    )
    r1 = solve(_scenario(), budget_s=600.0, seed=1, plan_paths=False, search=cfg)
    r2 = solve(_scenario(), budget_s=600.0, seed=1, plan_paths=False, search=cfg)

    assert r1.status == r2.status
    assert r1.diagnostics.restarts_attempted == r2.diagnostics.restarts_attempted
    assert r1.diagnostics.spread_stall_applied is True
    assert r2.diagnostics.spread_stall_applied is True
    assert r1.diagnostics.min_pairwise_gap_m == r2.diagnostics.min_pairwise_gap_m
    for la, lb in zip(r1.layouts, r2.layouts, strict=True):
        assert la.placements == lb.placements


def test_spread_stall_with_multiple_alternatives() -> None:
    """For ``alternatives > 1`` the stagnation metric is ``min(min_gap)`` over the
    *selected set* (not just the single best basin), so this exercises the
    multi-element path. A large epsilon forces the early-exit once two diverse
    basins exist, deterministically, while still returning the full set."""
    cfg = SearchConfig(
        spread=True,
        max_restarts=20,
        spread_stall_restarts=2,
        spread_stall_epsilon_m=1000.0,
    )
    r1 = solve(_scenario(), budget_s=600.0, alternatives=2, seed=1, plan_paths=False, search=cfg)
    r2 = solve(_scenario(), budget_s=600.0, alternatives=2, seed=1, plan_paths=False, search=cfg)

    assert r1.status == "found"
    assert len(r1.layouts) == 2
    assert r1.diagnostics.spread_stall_applied is True
    assert r1.diagnostics.restarts_attempted < 20
    # Same seed + config ⇒ byte-identical, on the multi-alternative metric path.
    assert r1.diagnostics.restarts_attempted == r2.diagnostics.restarts_attempted
    assert r1.diagnostics.min_pairwise_gap_m == r2.diagnostics.min_pairwise_gap_m
    for la, lb in zip(r1.layouts, r2.layouts, strict=True):
        assert la.placements == lb.placements
