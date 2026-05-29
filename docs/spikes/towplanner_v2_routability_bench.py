#!/usr/bin/env python3
"""Reproducible characterisation + benchmark harness for the towplanner-v2
routability spike (issue #332).

NOT shipped, NOT imported by the package, NOT covered by CI lint/type gates
(it lives under ``docs/spikes/``, outside ``src/`` and ``tests/``). It exists so
the numbers in ``docs/superpowers/specs/2026-05-28-towplanner-v2-routability-spike.md``
can be regenerated on demand:

    PYTHONPATH=src python docs/spikes/towplanner_v2_routability_bench.py

What it does, against a fixed set of deterministic target layouts:

1. CHARACTERISE — walk ``back_first_order`` and, for each plane in its natural
   back-first slot against the already-placed deeper planes, run the EXISTING
   Hybrid-A* search (euclidean heuristic, default budget) with the new ``stats``
   out-param. Reports per-plane: expansions used, routed?, and — when it failed —
   whether it hit the expansion CAP (``budget_exhausted``) or emptied the open
   heap first (``space_exhausted`` = genuine local infeasibility). Independently
   computes the free-space (point-robot) geodesic field and asks whether the
   goal is even REACHABLE from the door cone — separating "no heuristic can help"
   (geometrically infeasible) from "the heuristic got lost" (a routability bug a
   better heuristic fixes).

2. BENCHMARK — route the same layouts plane-by-plane under four planner configs
   (euclidean@700, grid@700, euclidean@2000, grid@2000) and report planes-routed
   and wall-clock. This is the go/no-go table.

Determinism: every layout target is reproducible (a fixed-seed ``solve`` with a
``max_restarts`` cap, or a checked-in static layout fixture). The grid heuristic
is RNG-free, so its routes are byte-stable across runs (asserted in
``tests/test_towplanner_grid_heuristic.py``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from hangarfit.loader import load_layout, load_scenario
from hangarfit.models import Layout, Placement, SearchConfig
from hangarfit.solver import solve
from hangarfit.towplanner import (
    _GRID_XY_M,
    NoFeasiblePlanError,
    Pose,
    _build_grid_heuristic,
    _build_obstacles,
    _mover_motion_bounds_conflict,
    back_first_order,
    entry_poses,
    plan_path,
)


def _xy_cell(p: Pose) -> tuple[int, int]:
    """The 2D occupancy-grid cell of a pose (the grid field's key — NOT the
    search's 3-tuple ``_cell`` which also bins heading)."""
    return (round(p.x_m / _GRID_XY_M), round(p.y_m / _GRID_XY_M))


@dataclass
class PlaneResult:
    plane_id: str
    routed: bool
    expansions: int
    budget_exhausted: bool
    space_exhausted: bool
    seconds: float
    goal_reachable: bool  # point-robot free-space reachability from the door cone


def _goal_reachable_from_cone(layout: Layout, placed: list[Placement], slot: Placement) -> bool:
    """Is the goal cell reachable from ANY surviving door-cone start cell in the
    free-space (point-robot) occupancy grid? A NO here means even an
    infinitely-agile point robot cannot get there — genuine infeasibility that no
    heuristic, and no RRT-Connect, can route around."""
    hangar = layout.hangar
    mover = layout.fleet[slot.plane_id]
    placed_layout = Layout(
        fleet=layout.fleet,
        hangar=hangar,
        placements=tuple(placed),
        maintenance_plane=layout.maintenance_plane,
    )
    obstacles = _build_obstacles(placed_layout, mover_id=mover.id)
    field = _build_grid_heuristic(Pose.from_placement(slot), obstacles, hangar)
    for cand in entry_poses(slot, hangar):
        cand_pl = Placement(mover.id, cand.x_m, cand.y_m, cand.heading_deg, on_carts=False)
        if _mover_motion_bounds_conflict(mover, cand_pl, hangar) is not None:
            continue
        if _xy_cell(cand) in field:
            return True
    return False


def route_in_order(
    layout: Layout, *, heuristic: str, max_expansions: int, characterise: bool = False
) -> list[PlaneResult]:
    """Place planes in back-first order; for each, try a tow path against the
    already-placed deeper planes. Always advances to the next slot (records the
    failure but keeps placing) so every plane is measured in its natural slot."""
    ordered = back_first_order(layout.placements)
    placed: list[Placement] = []
    out: list[PlaneResult] = []
    for slot in ordered:
        mover = layout.fleet[slot.plane_id]
        placed_layout = Layout(
            fleet=layout.fleet,
            hangar=layout.hangar,
            placements=tuple(placed),
            maintenance_plane=layout.maintenance_plane,
        )
        cone = entry_poses(slot, layout.hangar)
        stats: dict[str, object] = {}
        t0 = time.monotonic()
        routed = True
        try:
            plan_path(
                mover,
                cone[0],
                Pose.from_placement(slot),
                hangar=layout.hangar,
                placed=placed_layout,
                mover_on_carts=slot.on_carts,
                entries=cone,
                heuristic=heuristic,  # type: ignore[arg-type]
                max_expansions=max_expansions,
                stats=stats,
            )
        except NoFeasiblePlanError:
            routed = False
        dt = time.monotonic() - t0
        reachable = _goal_reachable_from_cone(layout, placed, slot) if characterise else False
        out.append(
            PlaneResult(
                plane_id=slot.plane_id,
                routed=routed,
                expansions=int(stats.get("expansions", -1)),  # type: ignore[arg-type]
                budget_exhausted=bool(stats.get("budget_exhausted", False)),
                space_exhausted=bool(stats.get("space_exhausted", False)),
                seconds=dt,
                goal_reachable=reachable,
            )
        )
        placed.append(slot)
    return out


def _placeholder_six() -> Layout:
    sc = load_scenario("tests/fixtures/solve_fresh_six_planes.yaml")
    r = solve(
        sc,
        budget_s=15.0,
        alternatives=1,
        seed=1,
        search=SearchConfig(spread=False),
        plan_paths=False,
    )
    assert r.layouts, "placeholder six-plane solve produced no layout"
    return r.layouts[0]


def _targets() -> list[tuple[str, Layout]]:
    return [
        ("placeholder-25x18  solve_fresh_six_planes seed=1 (5 floor planes)", _placeholder_six()),
        (
            "roomy-30x25        valid_all_nine_planes (9 planes)",
            load_layout("tests/fixtures/valid_all_nine_planes.yaml"),
        ),
        (
            "placeholder-25x18  valid_two_separated (2 planes, control)",
            load_layout("tests/fixtures/valid_two_separated.yaml"),
        ),
    ]


def main() -> None:
    import sys

    part1_only = "--part1" in sys.argv
    targets = _targets()

    print("=" * 100)
    print("PART 1 — FAILURE CHARACTERISATION (euclidean heuristic, default budget=700)")
    print("=" * 100)
    for name, layout in targets:
        print(f"\n### {name}")
        rows = route_in_order(layout, heuristic="euclidean", max_expansions=700, characterise=True)
        print(
            f"{'plane':<16}{'routed':<8}{'exp':>6}{'cap?':>7}{'space?':>8}{'reach?':>8}{'secs':>8}"
        )
        for r in rows:
            print(
                f"{r.plane_id:<16}{('yes' if r.routed else 'NO'):<8}{r.expansions:>6}"
                f"{('Y' if r.budget_exhausted else '-'):>7}"
                f"{('Y' if r.space_exhausted else '-'):>8}"
                f"{('Y' if r.goal_reachable else 'N'):>8}{r.seconds:>8.2f}"
            )
        nr = sum(1 for r in rows if r.routed)
        print(f"  --> routed {nr}/{len(rows)} planes")

    if part1_only:
        return

    print("\n" + "=" * 100)
    print("PART 2 — BENCHMARK: planes routed (and wall-clock) by planner config")
    print("=" * 100)
    configs = [
        ("euclidean", 700),
        ("grid", 700),
        ("euclidean", 2000),
        ("grid", 2000),
    ]
    header = f"{'target':<52}" + "".join(f"{h + '@' + str(b):>16}" for h, b in configs)
    print(header)
    for name, layout in targets:
        cells = []
        n_total = len(layout.placements)
        for heuristic, budget in configs:
            t0 = time.monotonic()
            rows = route_in_order(layout, heuristic=heuristic, max_expansions=budget)
            dt = time.monotonic() - t0
            nr = sum(1 for r in rows if r.routed)
            cells.append(f"{nr}/{n_total} ({dt:.1f}s)")
        short = name.split("  ")[0] + " " + name.split("  ")[-1][:28]
        print(f"{short:<52}" + "".join(f"{c:>16}" for c in cells))


if __name__ == "__main__":
    main()
