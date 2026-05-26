"""Performance gate for the bound-aware tow-path planner (#222).

``slow``-marked (excluded from the default ``pytest`` run; see pyproject
``addopts``). ``plan_fill`` now runs a Hybrid-A* search (``plan_path``) per
plane instead of a single closed-form Dubins shot, so this guards against the
search running away on realistic, solver-produced layouts.

NOTE on scope: on this branch the solver does NOT yet call the planner — that
bundling is #197 (blocked-by #222). So this gate drives ``plan_fill`` DIRECTLY
on layouts that ``solve`` produces, timing only the planning. A statically
valid solver layout may still be un-towable; #197 will reject those at solve
time. Here we only require the planner to either route the layout
(exact-oracle clean) or FAIL FAST — never run away.
"""

from __future__ import annotations

import time

import pytest

from hangarfit.loader import load_scenario
from hangarfit.models import Layout
from hangarfit.solver import solve
from hangarfit.towplanner import NoFeasiblePlanError, path_first_conflict, plan_fill

# Wall-clock ceiling for one layout's fill. The search budget is per-plane
# (_MAX_EXPANSIONS expansions), so an un-towable plane costs ~budget-exhaustion
# time to prove infeasible. This `slow`-marked gate is a runaway guard, not a
# tight regression bound — it catches the order-of-magnitude (pre-tuning the
# same layout took ~177s).
#
# Bumped 30s → 75s for the Reeds–Shepp motion model (ADR-0010): the primitive
# fan grew from 3 (forward L/S/R) to 6 (+ reverse L/S/R), so each expansion now
# screens roughly twice the edges and the worst-case budget-exhaustion bail on
# an un-towable layout (the six-plane fresh fill, where several planes are
# un-routable at 700 expansions each) doubled to ~50s observed. 75s keeps the
# runaway guard meaningful — it is still less than half the 177s pre-tuning
# baseline — with margin over the observed worst case for slower machines.
# Tighten once #197/v2 exercise the planner through solve() on real (measured)
# hangar geometry, or once the primitive fan is pruned (e.g. dropping the
# redundant reverse cart pivots, or gating reverse edges behind a heuristic).
_PLAN_FILL_CEILING_S = 75.0


@pytest.mark.slow
@pytest.mark.parametrize(
    "scenario_path",
    [
        "tests/fixtures/solve_feasible_smoke.yaml",
        "tests/fixtures/solve_fresh_six_planes.yaml",
    ],
)
def test_plan_fill_on_solved_layouts_completes_within_budget(scenario_path: str) -> None:
    scenario = load_scenario(scenario_path)
    result = solve(scenario, budget_s=10.0, alternatives=1, seed=7)
    if not result.layouts:
        pytest.skip(f"{scenario_path}: solve produced no layout (status={result.status})")

    for layout in result.layouts:
        t0 = time.monotonic()
        try:
            plan = plan_fill(layout)
        except NoFeasiblePlanError:
            # Un-towable solver layout: the only guarantee here is a fast bail.
            elapsed = time.monotonic() - t0
            assert elapsed < _PLAN_FILL_CEILING_S, (
                f"{scenario_path}: plan_fill ran away ({elapsed:.1f}s) before bailing"
            )
            continue
        elapsed = time.monotonic() - t0
        assert elapsed < _PLAN_FILL_CEILING_S, f"{scenario_path}: plan_fill took {elapsed:.1f}s"

        # Every routed move's path is exact-oracle clean against the planes towed
        # before it — the real #222 tow-ability guarantee, not just timing.
        placed: list[object] = []
        for move in plan.moves:
            obstacles = Layout(
                fleet=layout.fleet,
                hangar=layout.hangar,
                placements=tuple(placed),  # type: ignore[arg-type]
                maintenance_plane=layout.maintenance_plane,
            )
            slot = next(p for p in layout.placements if p.plane_id == move.plane_id)
            assert (
                path_first_conflict(
                    move.path,
                    layout.fleet[move.plane_id],
                    mover_on_carts=slot.on_carts,
                    placed=obstacles,
                )
                is None
            )
            placed.append(slot)
