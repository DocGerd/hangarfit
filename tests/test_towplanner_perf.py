"""Performance gate for the bound-aware tow-path planner (#222).

``slow``-marked (excluded from the default ``pytest`` run; see pyproject
``addopts``). ``plan_fill`` now runs a Hybrid-A* search (``plan_path``) per
plane instead of a single closed-form Reeds–Shepp analytic shot, so this guards
against the search running away on realistic, solver-produced layouts.

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
from hangarfit.models import Layout, SolveResult
from hangarfit.solver import solve
from hangarfit.towplanner import NoFeasiblePlanError, path_first_conflict, plan_fill

# This `slow`-marked gate is a runaway guard, not a tight regression bound — it
# catches the order-of-magnitude. `plan_fill` is RNG-free and expansion-bound
# (per-plane budget _MAX_EXPANSIONS; GLOBAL per-fill cap _MAX_FILL_EXPANSIONS),
# so the WORK it does for a given layout is FIXED across machines — only the
# wall-clock speed varies. The budget evolved as the model grew: the absolute
# ceiling went 30s → 75s (Reeds–Shepp 3→6 primitive fan, ADR-0010) → 400s
# (_MAX_EXPANSIONS 700→2000, #335; the seed=7 six-plane bail measured ~271s on
# the development machine, ×1.5 + rounding) and held at 400s through #336 (budget
# 2000→8000 + the _MAX_FILL_EXPANSIONS=16000 global cap, which bounds the fill at
# ~334s on that machine instead of grid@8000's uncapped ~997s).
#
# #625: an ABSOLUTE ceiling false-fails byte-identical work on a slower box. The
# same seed=7 fill measured ~492s on a WSL2 dev box — over 400s with NO
# regression. So the ceiling is now HOST-RELATIVE: time one cheap warm-up fill
# (its work is also fixed, so its wall-clock is a faithful per-host speed probe),
# then bound the real fill at a machine-INVARIANT multiple of it. The multiple is
# the measured heavy/warm-up work ratio (~871 on the WSL2 reference box) doubled
# for margin; the absolute FLOOR keeps a fast box from deriving a TIGHTER bound
# than the original 400s guard. A genuine algorithmic runaway (e.g. the #336
# global cap removed) balloons the heavy fill while the easy warm-up — which
# routes without exhausting any budget — stays flat, so the ratio still trips it.
_PLAN_FILL_CEILING_FLOOR_S = 400.0
_WARMUP_CEILING_MULTIPLE = 1800.0  # heavy/warm-up ≈ 871 measured (WSL2 ref box); ×~2 margin
_WARMUP_SCENARIO = "tests/fixtures/solve_feasible_smoke.yaml"


def _solve_for_fill(scenario_path: str) -> SolveResult:
    """Solve a scenario the way this gate drives the planner (seed=7, no internal
    tow-planning — the gate times its OWN ``plan_fill`` calls)."""
    return solve(
        load_scenario(scenario_path), budget_s=10.0, alternatives=1, seed=7, plan_paths=False
    )


@pytest.fixture(scope="module")
def host_plan_fill_ceiling_s() -> float:
    """Per-host wall-clock ceiling for one fill, calibrated off a warm-up probe.

    Times one ``plan_fill`` of the light feasible scenario (a faithful machine-
    speed probe, since its work is fixed) and scales it by the measured
    heavy/warm-up work ratio. Floored at ``_PLAN_FILL_CEILING_FLOOR_S`` so a fast
    box never gets a tighter bound than the original absolute runaway guard.
    """
    result = _solve_for_fill(_WARMUP_SCENARIO)
    if not result.layouts:
        return _PLAN_FILL_CEILING_FLOOR_S
    t0 = time.monotonic()
    try:
        plan_fill(result.layouts[0])
    except NoFeasiblePlanError:
        # A bailed warm-up is a poor speed probe (a fast bail under-measures host
        # speed → an artificially tight ceiling), so fall back to the floor.
        return _PLAN_FILL_CEILING_FLOOR_S
    warmup_s = time.monotonic() - t0
    return max(_PLAN_FILL_CEILING_FLOOR_S, warmup_s * _WARMUP_CEILING_MULTIPLE)


@pytest.mark.slow
@pytest.mark.parametrize(
    "scenario_path",
    [
        "tests/fixtures/solve_feasible_smoke.yaml",
        "tests/fixtures/solve_fresh_six_planes.yaml",
    ],
)
def test_plan_fill_on_solved_layouts_completes_within_budget(
    scenario_path: str, host_plan_fill_ceiling_s: float
) -> None:
    # plan_paths=False: this gate times its OWN plan_fill calls below, so the
    # layout search must NOT also tow-plan internally (wasted work that, since
    # #336's grid default + larger budget, costs as much as the timed section).
    result = _solve_for_fill(scenario_path)
    if not result.layouts:
        pytest.skip(f"{scenario_path}: solve produced no layout (status={result.status})")

    ceiling_s = host_plan_fill_ceiling_s
    for layout in result.layouts:
        t0 = time.monotonic()
        try:
            plan = plan_fill(layout)
        except NoFeasiblePlanError:
            # Un-towable solver layout: the only guarantee here is a fast bail.
            elapsed = time.monotonic() - t0
            assert elapsed < ceiling_s, (
                f"{scenario_path}: plan_fill ran away "
                f"({elapsed:.1f}s > {ceiling_s:.1f}s host ceiling) before bailing"
            )
            continue
        elapsed = time.monotonic() - t0
        assert elapsed < ceiling_s, (
            f"{scenario_path}: plan_fill took {elapsed:.1f}s (> {ceiling_s:.1f}s host ceiling)"
        )

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
