"""SE(2) heading-aware heuristic — Step-0 headroom probe (DEV/CI-ONLY, #840).

Not shipped in the wheel (top-level ``bench/``, ``where=["src"]``). Measures whether a
heading-aware SE(2) cost-to-go heuristic collapses Hybrid-A* expansions on the
fk9↔cessna nook vs today's position-only ``grid`` heuristic. Run:

    python -m bench.se2_heuristic_probe
"""

from __future__ import annotations

import contextlib
import heapq
import math
from collections.abc import Callable, Generator
from dataclasses import dataclass

from hangarfit import towplanner
from hangarfit.loader import load_layout
from hangarfit.models import Aircraft, Door, GroundObject, Hangar, Layout, MaintenanceBay, Placement
from hangarfit.towplanner import Pose

# Fine-grid resolution for the probe (the witness found the real path at 0.25 m/10°).
_FINE_XY_M = 0.25
_FINE_DEG = 10.0

# The real Herrenteich fk9↔cessna goal poses define the binding nook geometry.
# The fk9−cessna relative pose is derived dynamically in build_toy_nook() straight
# from this layout, so the toy stays faithful even if the catalog is corrected — no
# hardcoded snapshot to drift.
_HERRENTEICH_LAYOUT = "examples/herrenteich/layout.yaml"


@contextlib.contextmanager
def fine_grid() -> Generator[None, None, None]:
    """Run the towplanner at the fine 0.25 m/10° grid, restoring the deployed
    constants afterwards. ``_HEADING_BINS`` is import-time-derived from ``_GRID_DEG``,
    so it must be patched too."""
    saved = (towplanner._GRID_XY_M, towplanner._GRID_DEG, towplanner._HEADING_BINS)
    try:
        towplanner._GRID_XY_M = _FINE_XY_M
        towplanner._GRID_DEG = _FINE_DEG
        towplanner._HEADING_BINS = round(360.0 / _FINE_DEG)
        yield
    finally:
        (towplanner._GRID_XY_M, towplanner._GRID_DEG, towplanner._HEADING_BINS) = saved


@dataclass(frozen=True, slots=True)
class ToyNook:
    mover: Aircraft
    entry: Pose
    goal: Pose
    hangar: Hangar
    placed: Layout
    mover_on_carts: bool


def _real_pair() -> tuple[Aircraft, Aircraft, Placement, Placement]:
    """Load the real fk9_mkii + cessna_140 aircraft and their Herrenteich goal
    placements (the binding nook geometry)."""
    layout = load_layout(_HERRENTEICH_LAYOUT)
    fleet = dict(layout.fleet)
    by_id = {p.plane_id: p for p in layout.placements}
    return fleet["fk9_mkii"], fleet["cessna_140"], by_id["fk9_mkii"], by_id["cessna_140"]


def build_toy_nook() -> ToyNook:
    """A SMALL hangar holding the real cessna_140 (parked obstacle) with the real
    fk9_mkii routing to a goal at the REAL fk9−cessna relative pose — so the
    binding parallel-park geometry is preserved but the arena is small enough that
    fine-grid A* runs in seconds. Calibrated values; tune ``_CX/_CY/hangar dims`` if
    the calibration tests fail (see tests).

    Calibration notes:
    - ``_CX=9.0, _CY=7.0`` and ``length_m=18.0``:
      * cessna wing y-range at heading 90° = [1.92, 12.08] → clear of fk9 entry
        wing y-range [0.12, 1.30] by 0.62 m (well above the 0.15 m clearance).
      * fk9 goal y = _CY + 4.65 = 11.65; wing top = 11.65 + 4.925 = 16.575 ≤ 18.0.
      * fk9 is tow_pivotable (effective_turn_radius_m() = 0), so it pivots in place
        — the cessna wall across the hangar is what forces genuine search.
    - MaintenanceBay uses a small inert back-left placeholder (same idiom as the
      real Herrenteich hangar) because the field is required and width/depth must
      be positive.
    """
    fk9, cessna, fk9_p, cessna_p = _real_pair()
    # Real relative pose (preserves the mutual-block geometry; translation only).
    rel_x = fk9_p.x_m - cessna_p.x_m
    rel_y = fk9_p.y_m - cessna_p.y_m
    # Park the cessna here in the small hangar; fk9's goal is offset by the real rel.
    _CX, _CY = 9.0, 7.0
    hangar = Hangar(
        length_m=18.0,
        width_m=14.0,
        door=Door(center_x_m=7.0, width_m=12.0),
        # Inert placeholder — no maintenance plane set, so the bay keep-out is
        # inactive (bay_active=False in _build_obstacles).
        maintenance_bay=MaintenanceBay(center_x_m=2.0, width_m=3.0, depth_m=2.0),
        clearance_m=0.15,
        wing_layer_clearance_m=0.15,
        max_carts=0,
    )
    cessna_place = Placement("cessna_140", _CX, _CY, cessna_p.heading_deg, on_carts=False)
    placed = Layout(
        fleet={"cessna_140": cessna, "fk9_mkii": fk9},
        hangar=hangar,
        placements=(cessna_place,),
    )
    goal = Pose(x_m=_CX + rel_x, y_m=_CY + rel_y, heading_deg=fk9_p.heading_deg)
    entry = Pose(x_m=hangar.door.center_x_m, y_m=0.0, heading_deg=0.0)
    return ToyNook(
        mover=fk9, entry=entry, goal=goal, hangar=hangar, placed=placed, mover_on_carts=False
    )


def build_se2_field(
    mover: Aircraft | GroundObject,
    goal: Pose,
    obstacles: towplanner._Obstacles,
    motion_hangar: Hangar,
    r: float,
    *,
    mover_on_carts: bool,
    max_cells: int = 300_000,
) -> dict[tuple[int, int, int], float]:
    """Backward-SE(2) Dijkstra cost-to-go field from ``goal`` over the ``_cell``
    lattice using the real primitive fan.

    Runs a Dijkstra flood **from** the goal pose.  Because the primitive fan is
    inverse-closed (it contains both forward *and* reverse-gear segments) and
    the motion costs are gear-agnostic, the forward distance from the goal to
    any reachable cell equals the backward cost-to-go from that cell to the
    goal.  Concretely: applying a reverse primitive from pose ``p`` yields the
    predecessor ``q`` that can reach ``p`` via the corresponding forward
    primitive at equal cost — so one Dijkstra pass from the goal computes
    cost-to-go for all reachable ``_cell``-binned poses.

    Cusp penalties are intentionally omitted to keep the field an
    admissible-leaning lower bound (exact admissibility is not guaranteed once
    the flood is capped at ``max_cells``).  Obstacle-blocked edges are pruned via
    ``_motion_clear`` over the sampled arc.  The flood is capped at
    ``max_cells`` to bound memory and time.
    """
    field: dict[tuple[int, int, int], float] = {towplanner._cell(goal): 0.0}
    counter = 0
    heap: list[tuple[float, int, Pose]] = [(0.0, counter, goal)]
    prims = towplanner._primitives(r, lateral=mover_on_carts)
    while heap and len(field) < max_cells:
        d, _, pose = heapq.heappop(heap)
        if d > field.get(towplanner._cell(pose), math.inf) + 1e-12:
            continue  # stale entry
        for seg in prims:
            nxt = towplanner._step_pose(pose, seg, r)
            edge = towplanner.DubinsArc(pose, nxt, r, (seg,))
            if not all(
                towplanner._motion_clear(mover, p, obstacles, motion_hangar)
                for p in edge.sample(
                    step_m=towplanner._SEARCH_STEP_M,
                    step_deg=towplanner._SEARCH_STEP_DEG,
                )
            ):
                continue
            nd = d + towplanner._seg_cost(seg, r)
            nkey = towplanner._cell(nxt)
            if nd < field.get(nkey, math.inf) - 1e-12:
                field[nkey] = nd
                counter += 1
                heapq.heappush(heap, (nd, counter, nxt))
    return field


def make_field_h(
    field: dict[tuple[int, int, int], float],
    goal: Pose,
) -> Callable[[Pose], float]:
    """Return a heuristic function backed by the SE(2) cost-to-go ``field``.

    For poses whose ``_cell`` is present in the field the stored value is
    returned directly.  For poses outside the flooded region (e.g. the start
    pose when the flood was capped before reaching it) the fallback is the
    Euclidean distance to the goal — a valid admissible lower bound that
    mirrors the ``plan_path`` grid-heuristic fallback so no pose is ever
    un-expandable.
    """

    def _h(p: Pose) -> float:
        v = field.get(towplanner._cell(p))
        if v is None:
            return math.hypot(goal.x_m - p.x_m, goal.y_m - p.y_m)
        return v

    return _h


if __name__ == "__main__":
    import sys

    from hangarfit.towplanner import plan_path

    nook = build_toy_nook()
    print(f"entry={nook.entry}  goal={nook.goal}")
    print(f"hangar {nook.hangar.width_m}×{nook.hangar.length_m} m")
    stats: dict[str, object] = {}
    with fine_grid():
        try:
            arc = plan_path(
                nook.mover,
                nook.entry,
                nook.goal,
                hangar=nook.hangar,
                placed=nook.placed,
                mover_on_carts=nook.mover_on_carts,
                max_expansions=40_000,
                heuristic="grid",
                stats=stats,
            )
            print(f"found  expansions={stats['expansions']}")
        except towplanner.NoFeasiblePlanError:
            print(f"no path  expansions={stats.get('expansions', '?')}")
    sys.exit(0)
