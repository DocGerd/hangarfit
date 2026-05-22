"""Determinism canary tests for the static layout solver.

These tests verify that ``solve(scenario, seed=42)`` returns an
IDENTICAL :class:`SolveResult` across runs. They are intentionally
fragile — any deliberate algorithm change requires updating them.
That's the point: loud signal on accidental determinism breaks
(e.g., dict iteration ordering, set ordering, unseeded random).

Universe of common culprits when one of these flips on you:
- ``set()`` iteration order (different across processes by default).
- An ``os.urandom``/``random.random()`` call that bypassed the seeded
  ``rng`` parameter.
- ``dict.items()`` ordering across YAML-load boundaries (Python 3.7+
  preserves insertion order, but the loader rebuilds the dict).
- ``time.time()`` or ``time.monotonic()`` used as a tiebreaker.

Distinct from ``test_solver_search.py::test_solve_is_deterministic_*``
which exercise specific search-internal paths on individual fixtures.
This file is the parametrized matrix that catches the same regression
across more than one scenario.
"""

from __future__ import annotations

import pytest

from hangarfit.loader import load_scenario
from hangarfit.models import SearchConfig
from hangarfit.solver import solve

# Three canary fixtures chosen for coverage breadth:
#  - trivial_single_plane: simplest possible search; sensitive to the
#    initial-placement RNG.
#  - pinned_one_plane: search runs with one fixed slot; sensitive to
#    cart-bucket ordering and pin-honoring code paths.
#  - fresh_six_planes: full multi-plane search; sensitive to descent
#    step's conflict-plane pick and candidate sampling.
CANARY_FIXTURES = [
    "tests/fixtures/solve_trivial_single_plane.yaml",
    "tests/fixtures/solve_pinned_one_plane.yaml",
    "tests/fixtures/solve_fresh_six_planes.yaml",
]


@pytest.mark.parametrize("fixture", CANARY_FIXTURES)
def test_solve_deterministic_given_seed(fixture):
    """``solve(scenario, seed=42)`` must yield bit-for-bit identical
    accepted layouts across two runs (even on different Scenario
    instances loaded from the same file).
    """
    s1 = load_scenario(fixture)
    r1 = solve(s1, budget_s=5.0, alternatives=1, seed=42)

    s2 = load_scenario(fixture)
    r2 = solve(s2, budget_s=5.0, alternatives=1, seed=42)

    # Status + seed must match.
    assert r1.status == r2.status
    assert r1.diagnostics.seed == r2.diagnostics.seed == 42

    # Layout sequences match element-wise on placements + maintenance.
    # NOTE: Layout objects use MappingProxyType-wrapped fleet dicts,
    # so direct `==` would compare proxy identity in corner cases.
    # Compare the user-visible fields directly.
    assert len(r1.layouts) == len(r2.layouts)
    for la, lb in zip(r1.layouts, r2.layouts, strict=True):
        assert la.placements == lb.placements
        assert la.maintenance_plane == lb.maintenance_plane

    # If the run exhausted budget, best_partial_layout must also match.
    bp1 = r1.diagnostics.best_partial_layout
    bp2 = r2.diagnostics.best_partial_layout
    if bp1 is not None:
        assert bp2 is not None
        assert bp1.placements == bp2.placements

    # NOT asserted: diagnostics.wall_time_s and diagnostics.restarts_attempted.
    # Both depend on machine speed and the wall-clock-based budget cutoff —
    # a faster run completes more restarts within the same 5.0 s budget.
    # The deterministic-layout assertion above is enough to catch any
    # accidental non-determinism (unseeded random, set/dict ordering, etc.):
    # different RNG state -> different layouts.


def test_solve_deterministic_best_partial_under_max_restarts() -> None:
    """``solve(..., search=SearchConfig(max_restarts=K))`` exhausts
    deterministically across machines.

    The existing wall-clock canaries above don't assert on
    ``best_partial_layout`` when ``status == "exhausted_budget"`` because
    a fast machine may flip to ``found`` within the 5 s budget. With
    ``max_restarts=K`` capping the outer loop, exhaustion is the
    inevitable outcome regardless of machine speed, and the
    ``best_partial_layout`` becomes machine-independent.

    Calibration (recorded 2026-05-22 for traceability):
        Ran ``solve(scenario, budget_s=10.0, seed=42)`` once on
        ``solve_fresh_six_planes.yaml`` to record the natural restart
        count to first success. Observed ``restarts_attempted=2``
        (status=found, wall_time≈1.3 s on the calibration machine).
        Per the v0.6.0 plan Task 2 brief, the canary's ``max_restarts``
        is set deliberately small (``observed // 2 = 1``) so the cap
        trips before the natural success, forcing exhaustion. The
        ``budget_s=30.0`` here is generous; ``max_restarts`` is the
        gate that trips first. If a future algorithm change pushes
        natural success below 2 restarts at seed=42 (i.e., succeeds on
        restart 0), this canary needs re-calibration on
        ``solve_diversity_impossible_warn.yaml`` per the plan's
        fallback procedure.

    Fallback fixture calibration (recorded 2026-05-23):
        Ran ``solve(scenario, budget_s=10.0, seed=42)`` on
        ``solve_diversity_impossible_warn.yaml`` to pre-pin the
        fallback. Observed ``status=found, restarts_attempted=1,
        wall_time≈0.009 s``. With observed=1, the standard
        ``max_restarts = observed // 2`` recipe would yield 0, which
        :class:`SearchConfig.__post_init__` rejects. If a future
        switch to this fallback fixture is needed, pick a different
        seed whose natural restart count is ≥ 2 (so ``observed // 2
        >= 1``) or use a different fixture — do NOT clamp to
        ``max_restarts=1``, which would NOT trip before natural
        success on this fixture (the cap would equal the natural
        success count and the predicate is strictly ``<``, not
        ``<=``).
    """
    fixture = "tests/fixtures/solve_fresh_six_planes.yaml"
    max_restarts = 1

    s1 = load_scenario(fixture)
    r1 = solve(
        s1,
        budget_s=30.0,
        alternatives=1,
        seed=42,
        search=SearchConfig(max_restarts=max_restarts),
    )

    s2 = load_scenario(fixture)
    r2 = solve(
        s2,
        budget_s=30.0,
        alternatives=1,
        seed=42,
        search=SearchConfig(max_restarts=max_restarts),
    )

    # The max_restarts cap (not wall-clock) must be the gate that trips —
    # otherwise the canary's purpose (cross-machine determinism of the
    # exhausted-budget branch) is lost.
    assert r1.status == "exhausted_budget", (
        f"expected exhausted_budget under max_restarts={max_restarts}; "
        f"got {r1.status} (calibration drift — see test docstring)"
    )
    assert r2.status == r1.status
    assert r1.diagnostics.restarts_attempted == max_restarts
    assert r2.diagnostics.restarts_attempted == max_restarts

    # The fused-pair contract — both Some when exhausted_budget.
    bpl1 = r1.diagnostics.best_partial_layout
    bpl2 = r2.diagnostics.best_partial_layout
    assert bpl1 is not None, "exhausted_budget must populate best_partial_layout"
    assert bpl2 is not None

    # The headline assertion: bit-for-bit identical best_partial_layout
    # across the two runs. This is what the canary buys over the existing
    # wall-clock canaries — under max_restarts the outcome is fully
    # reproducible, not just the accepted layouts.
    assert bpl1.placements == bpl2.placements
    assert bpl1.maintenance_plane == bpl2.maintenance_plane


def test_solve_budget_trips_before_max_restarts() -> None:
    """When ``budget_s`` would cut the loop short of ``max_restarts``,
    the budget gate wins — exercises the compound termination
    condition's other branch.

    Pair with ``test_solve_deterministic_best_partial_under_max_restarts``
    above which pins the inverse case (max_restarts trips first). The two
    canaries together pin both branches of the
    ``time.monotonic() - start < budget_s and (… or restart_index <
    search.max_restarts)`` compound predicate in ``solver.solve``'s outer
    loop. If a future refactor flips the short-circuit order, drops a
    clause, or accidentally turns ``and`` into ``or``, one or the other
    canary will go red.
    """
    fixture = "tests/fixtures/solve_fresh_six_planes.yaml"

    s = load_scenario(fixture)
    r = solve(
        s,
        budget_s=0.001,  # tiny budget — trips first
        alternatives=1,
        seed=42,
        search=SearchConfig(max_restarts=1000),  # cap that won't be reached
    )

    # The budget — not the restart cap — must be the gate that trips.
    # ``restarts_attempted`` may be 0 or a small handful depending on
    # machine speed and per-restart cost, but it is bounded by what the
    # tiny 1 ms budget allows, well below the 1000-restart cap. If this
    # tripped at the cap, ``restarts_attempted`` would equal 1000 and
    # the assertion would fire loud.
    assert r.diagnostics.restarts_attempted < 100, (
        f"expected budget-first termination (a few restarts at most), "
        f"got restarts_attempted={r.diagnostics.restarts_attempted}"
    )
