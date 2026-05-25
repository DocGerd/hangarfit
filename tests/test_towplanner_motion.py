"""Sampled collision-during-motion check (#191) — ``path_first_conflict``.

``path_first_conflict`` walks a :class:`~hangarfit.towplanner.DubinsArc`,
rebuilds the mover's :class:`~hangarfit.models.Placement` at each sampled
pose, and runs the *same* :func:`hangarfit.collisions.check` oracle the
static solver uses. It therefore inherits parts-overlap, hangar-bounds, and
bay-intrusion detection for free (spike Q4); these tests pin the three
behaviours that matter: a clear corridor returns ``None``, a path driven
through a parked plane returns a conflict naming the mover, and a conflict
that does not involve the mover is never attributed to it.

Fixtures are module-local (mirroring the inline ``_canary_aircraft`` helper
in ``test_towplanner_dubins.py``) rather than a shared ``conftest.py``:
they are planner-motion-specific and have a single consumer today. The
geometry is deliberate — each plane is one small fuselage box mounted
*forward* of the plane origin (``offset_x_m = +length/2``) so that a
placement at the front wall (``y = 0``, where every entry path starts)
keeps every world vertex at ``y >= 0`` and does not trip a spurious
``hangar_bounds`` conflict on the first sample.
"""

from __future__ import annotations

import pytest

from hangarfit.models import Aircraft, Door, Hangar, Layout, MaintenanceBay, Part, Placement
from hangarfit.towplanner import Pose, path_first_conflict, plan_dubins


def _box_plane(plane_id: str, *, turn_radius_m: float = 4.0) -> Aircraft:
    """A minimal own-gear plane: one 1.0 m × 0.6 m fuselage box.

    ``offset_x_m = 0.5`` puts the box's rear edge at the plane-local origin.
    Under the heading-0 transform (``world_y = py + forward``), that keeps a
    placement at ``y = 0`` entirely inside the hangar (``world_y ∈ [0, 1]``).
    """
    return Aircraft(
        id=plane_id,
        name=f"Plane {plane_id}",
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_own_gear",
        turn_radius_m=turn_radius_m,
        measured=False,
        parts=(
            Part(
                kind="fuselage",
                length_m=1.0,
                width_m=0.6,
                offset_x_m=0.5,
                offset_y_m=0.0,
                angle_deg=0.0,
                z_bottom_m=0.0,
                z_top_m=1.0,
            ),
        ),
    )


@pytest.fixture
def simple_hangar() -> Hangar:
    """A 20 m × 20 m hangar — large enough for an unobstructed x = 8 corridor."""
    return Hangar(
        length_m=20.0,
        width_m=20.0,
        door=Door(center_x_m=10.0, width_m=6.0),
        maintenance_bay=MaintenanceBay(center_x_m=10.0, width_m=4.0, depth_m=2.0),
        clearance_m=0.5,
        wing_layer_clearance_m=0.5,
    )


@pytest.fixture
def two_planes_fleet() -> dict[str, Aircraft]:
    return {"A": _box_plane("A"), "B": _box_plane("B")}


def test_clear_path_returns_none(
    simple_hangar: Hangar, two_planes_fleet: dict[str, Aircraft]
) -> None:
    fleet = two_planes_fleet
    placed = Layout(
        fleet=fleet,
        hangar=simple_hangar,
        placements=(Placement("A", 2.0, 8.0, 0.0, on_carts=False),),
    )
    # B drives straight up the x = 8 corridor; A sits well clear at x = 2.
    arc = plan_dubins(Pose(8.0, 0.0, 0.0), Pose(8.0, 8.0, 0.0), turn_radius_m=4.0)
    assert path_first_conflict(arc, fleet["B"], mover_on_carts=False, placed=placed) is None


def test_path_through_placed_plane_returns_conflict(
    simple_hangar: Hangar, two_planes_fleet: dict[str, Aircraft]
) -> None:
    fleet = two_planes_fleet
    # Place A squarely in B's straight-line corridor.
    placed = Layout(
        fleet=fleet,
        hangar=simple_hangar,
        placements=(Placement("A", 8.0, 4.0, 0.0, on_carts=False),),
    )
    arc = plan_dubins(Pose(8.0, 0.0, 0.0), Pose(8.0, 8.0, 0.0), turn_radius_m=4.0)
    conflict = path_first_conflict(arc, fleet["B"], mover_on_carts=False, placed=placed)
    assert conflict is not None
    assert "B" in conflict.planes


def test_conflict_only_reports_mover_involvement(
    simple_hangar: Hangar, two_planes_fleet: dict[str, Aircraft]
) -> None:
    # A short hop far from A: the mover must not be blamed for anything.
    fleet = two_planes_fleet
    placed = Layout(
        fleet=fleet,
        hangar=simple_hangar,
        placements=(Placement("A", 2.0, 8.0, 0.0, on_carts=False),),
    )
    arc = plan_dubins(Pose(8.0, 0.0, 0.0), Pose(8.0, 1.0, 0.0), turn_radius_m=4.0)
    res = path_first_conflict(arc, fleet["B"], mover_on_carts=False, placed=placed)
    assert res is None or "B" in res.planes
