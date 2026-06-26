"""SE(2) heading-aware heuristic — Step-0 headroom probe (DEV/CI-ONLY, #840).

Not shipped in the wheel (top-level ``bench/``, ``where=["src"]``). Measures whether a
heading-aware SE(2) cost-to-go heuristic collapses Hybrid-A* expansions on the
fk9↔cessna nook vs today's position-only ``grid`` heuristic. Run:

    python -m bench.se2_heuristic_probe
"""

from __future__ import annotations

import contextlib
from collections.abc import Generator
from dataclasses import dataclass

from hangarfit import towplanner
from hangarfit.loader import load_layout
from hangarfit.models import Aircraft, Door, Hangar, Layout, MaintenanceBay, Placement
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
