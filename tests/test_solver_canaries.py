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


@pytest.fixture(autouse=True)
def _stub_towplanning(monkeypatch):
    """Keep these determinism canaries fast by stubbing tow-planning.

    solve() tow-plans every returned layout by default (``plan_paths=True``,
    #197), and tow-planning runs a bounded Hybrid-A* search per plane —
    seconds on a multi-plane fill. These canaries assert IDENTICAL solver
    output across runs (layouts/seed/best_partial), never on ``plans``, so a
    trivial ``plan_fill`` stub preserves the default code path while removing
    the cost. The real planner↔solver integration lives in
    ``tests/test_solver_towplanner.py``.
    """
    import hangarfit.solver as solver_mod
    from hangarfit.towplanner import MovesPlan

    monkeypatch.setattr(
        solver_mod, "plan_fill", lambda target, **kwargs: MovesPlan(target_layout=target, moves=())
    )


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


# `serial` pins these wall-clock-budget canaries OUTSIDE the `-n auto`
# pytest-xdist pool (CI runs them in a separate serial pass, #492). Each call
# runs solve() twice in-process under a `budget_s=5.0` deadline and asserts
# bit-identical output; under CPU starvation the two solves can complete
# different restart counts within the same budget and diverge. `xdist_group` /
# `--dist loadgroup` is INSUFFICIENT — it only co-locates a group on one worker,
# it does not stop sibling workers from saturating the CPU, so the wall-clock
# race persists. The max_restarts-scoped companion below is load-independent and
# stays in the parallel pool.
@pytest.mark.serial
@pytest.mark.parametrize("fixture", CANARY_FIXTURES)
def test_solve_deterministic_given_seed(fixture):
    """``solve(scenario, seed=42)`` must yield bit-for-bit identical
    accepted layouts across two runs (even on different Scenario
    instances loaded from the same file).
    """
    s1 = load_scenario(fixture)
    r1 = solve(
        s1, budget_s=5.0, alternatives=1, seed=42, search=SearchConfig(spread=False, nose_out=False)
    )

    s2 = load_scenario(fixture)
    r2 = solve(
        s2, budget_s=5.0, alternatives=1, seed=42, search=SearchConfig(spread=False, nose_out=False)
    )

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

    Calibration (recorded 2026-05-23 for #108):
        After the solver stopped sampling an initial placement for
        the maintenance occupant (#108 closes the milestone-#9 cleanup
        that began at #103), the RNG state shifted — one fewer
        ``random()``/``uniform()`` call per restart — and every
        previously-tried (fixture, seed=42) combination collapsed to
        natural-success on restart 0, leaving no ``max_restarts``
        value that could trip before success.

        Re-probed with ``solve(scenario, budget_s=10.0)`` across the
        existing fixture set at seeds {7, 13, 42, 99, 123, 256}.
        ``solve_fresh_six_planes.yaml`` at ``seed=256`` records
        ``status=found, restarts_attempted=3`` — the most headroom in
        the probe. Per the v0.6.0 plan Task 2 recipe, the cap is set
        deliberately small (``max_restarts = observed // 2 = 1``) so
        it trips well before the natural success at restart 3,
        forcing exhaustion. Two-restart headroom protects against
        future RNG-shift regressions tightening natural success to 2.

        Re-calibration trigger: if a future algorithm change pushes
        natural success to ``restarts_attempted <= 1`` at seed=256,
        re-probe seeds for the same fixture and pick one whose
        ``restarts_attempted >= 2``; update ``seed`` below.

        Floor: ``observed >= 2`` is required because
        ``max_restarts = observed // 2`` must be ``>= 1`` and
        :class:`SearchConfig.__post_init__` rejects
        ``max_restarts=0``. With ``observed=2``, the cap is still
        ``1 < 2`` (the predicate is strictly ``<``, not ``<=``), so
        the canary keeps working — but headroom drops to one restart
        and is one regression away from breaking.

        Re-calibrated 2026-06-08 for #519/#520 (ADR-0023): widening
        ``data/hangar.yaml`` 18 -> 22 m (so the demo keeps its full plane
        set with the bulkier tail surfaces) made the shared
        ``solve_fresh_six_planes.yaml`` *solvable* within ``max_restarts=1``,
        flipping this canary to ``status=found``. It now uses a dedicated
        tight 18 m hangar fixture (``solve_canary_six_planes_tight.yaml`` ->
        ``canary_hangar_tight_18m.yaml``) so the fill stays un-fully-solvable
        within the cap — decoupling the canary from ``data/hangar.yaml``'s
        width so future demo-hangar tweaks cannot re-break it.

        Re-calibrated 2026-06-09 for #544 (per-restart-index reseed,
        ADR-0003 amendment): seeding each restart from its index re-bases
        the per-restart trajectories, so the old ``seed=256`` collapsed to
        natural success at restart 1 (``max_restarts=1`` no longer
        exhausted). Re-probed the same fixture across seeds; ``seed=3``
        has by far the most headroom — first natural success at restart
        **18**. ``max_restarts`` is set to a small fixed **3** (well below
        18): that is a *15-restart* drift margin against future RNG shifts,
        yet only ~2 s per solve so the wall-clock budget (30 s) can never
        trip before the cap. Re-calibration trigger unchanged: if a future
        change pushes ``seed=3`` natural success to ``<= 3``, re-probe and
        pick a higher-headroom seed.
    """
    fixture = "tests/fixtures/solve_canary_six_planes_tight.yaml"
    max_restarts = 3
    seed = 3

    s1 = load_scenario(fixture)
    r1 = solve(
        s1,
        budget_s=30.0,
        alternatives=1,
        seed=seed,
        search=SearchConfig(max_restarts=max_restarts, spread=False, nose_out=False),
    )

    s2 = load_scenario(fixture)
    r2 = solve(
        s2,
        budget_s=30.0,
        alternatives=1,
        seed=seed,
        search=SearchConfig(max_restarts=max_restarts, spread=False, nose_out=False),
    )

    # The max_restarts cap (not wall-clock) must be the gate that trips —
    # otherwise the canary's purpose (cross-machine determinism of the
    # exhausted-budget branch) is lost.
    assert r1.status == "exhausted_budget", (
        f"expected exhausted_budget under max_restarts={max_restarts}; "
        f"got {r1.status} (calibration drift — see test docstring)"
    )
    assert r2.status == r1.status
    # restarts_attempted == max_restarts confirms the cap was the gate that
    # tripped, not the wall-clock budget. If wall_time_s drifts close to
    # budget_s=30.0 (e.g., per-restart slowdown on a slow CI VM), the budget
    # would trip first and restarts_attempted would be < max_restarts —
    # exhausted_budget status would still hold but for the wrong reason.
    assert r1.diagnostics.restarts_attempted == max_restarts, (
        f"max_restarts cap must trip before wall-clock budget. "
        f"Got restarts_attempted={r1.diagnostics.restarts_attempted}, "
        f"max_restarts={max_restarts}, wall_time_s={r1.diagnostics.wall_time_s:.2f}, "
        f"budget_s=30.0. If wall_time is near budget, the canary's "
        f"cross-machine determinism guarantee is broken — raise budget_s "
        f"or investigate per-restart slowdown."
    )
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
