"""Sampled collision-during-motion check (#191) — ``path_first_conflict``.

``path_first_conflict`` walks a :class:`~hangarfit.towplanner.DubinsArc`,
rebuilds the mover's :class:`~hangarfit.models.Placement` at each sampled
pose, and runs the *same* :func:`hangarfit.collisions.check` oracle the
static solver uses. It therefore inherits parts-overlap, hangar-bounds, and
bay-intrusion detection for free (spike Q4).

These tests pin the behaviours that are unique to *motion* (the static
oracle is already exhaustively tested in ``test_collisions.py``):

- the happy path: a clear corridor → ``None``; a path through a parked
  plane → a conflict naming the mover;
- the mover-only filter: a pre-existing conflict among *other* placed
  planes is never attributed to the mover;
- each of the three oracle checks fires *during motion* —
  ``fuselage_fuselage_overlap`` (parts), ``hangar_bounds``, and
  ``bay_intrusion`` — each gated on motion-specific wiring (the per-sample
  ``Placement`` heading/cart-state and the ``maintenance_plane`` thread-through);
- a *turning* (non-axis-aligned) arc, so the sampled poses' rotated headings
  actually reach the collision check (not only heading-0 straights);
- the cart pivot-in-place branch (``mover_on_carts=True``, ``turn_radius=0``),
  whose zero-translation angular sweep is sampled by ``step_deg``.

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


def _fuselage_box() -> Part:
    """A 1.0 m × 0.6 m fuselage box with its rear edge at the plane origin.

    Mounting it forward (``offset_x_m = 0.5``) means the heading-0 transform
    (``world_y = py + forward``) keeps a placement at ``y = 0`` inside the
    hangar (``world_y ∈ [0, 1]``)."""
    return Part(
        kind="fuselage",
        length_m=1.0,
        width_m=0.6,
        offset_x_m=0.5,
        offset_y_m=0.0,
        angle_deg=0.0,
        z_bottom_m=0.0,
        z_top_m=1.0,
    )


def _box_plane(plane_id: str, *, turn_radius_m: float = 4.0) -> Aircraft:
    """A minimal own-gear plane (one fuselage box)."""
    return Aircraft(
        id=plane_id,
        name=f"Plane {plane_id}",
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_own_gear",
        turn_radius_m=turn_radius_m,
        measured=False,
        parts=(_fuselage_box(),),
    )


def _cart_plane(plane_id: str, *, turn_radius_m: float = 4.0) -> Aircraft:
    """A cart-eligible plane — may ride on carts (``on_carts=True``) and so
    can pivot in place (``effective_turn_radius_m() == 0``)."""
    return Aircraft(
        id=plane_id,
        name=f"Cart plane {plane_id}",
        wing_position="high",
        gear="tailwheel",
        movement_mode="cart_eligible",
        turn_radius_m=turn_radius_m,
        measured=False,
        parts=(_fuselage_box(),),
    )


def _spanning_fuselage() -> Part:
    """A 4.0 m fuselage centered on the plane origin. At a ``y = 0`` entry pose
    (heading 0) its rear half sits at ``y < 0`` — the plane straddles the door
    line, exactly as a plane being towed *through* the door does. Unlike the
    forward-mounted :func:`_fuselage_box`, this exercises the front-door gap."""
    return Part(
        kind="fuselage",
        length_m=4.0,
        width_m=0.6,
        offset_x_m=0.0,
        offset_y_m=0.0,
        angle_deg=0.0,
        z_bottom_m=0.0,
        z_top_m=1.0,
    )


def _spanning_plane(plane_id: str, *, turn_radius_m: float = 4.0) -> Aircraft:
    """An own-gear plane whose fuselage spans the origin (rear protrudes to
    ``y < 0`` at a ``y = 0`` entry pose)."""
    return Aircraft(
        id=plane_id,
        name=f"Spanning {plane_id}",
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_own_gear",
        turn_radius_m=turn_radius_m,
        measured=False,
        parts=(_spanning_fuselage(),),
    )


@pytest.fixture
def simple_hangar() -> Hangar:
    """A 20 m × 20 m hangar — large enough for an unobstructed x = 8 corridor.

    The maintenance bay is the back-anchored rectangle ``x ∈ (8, 12)``,
    ``y ∈ (18, 20]`` (centre 10, width 4, depth 2)."""
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


def test_pre_existing_non_mover_conflict_not_attributed_to_mover(simple_hangar: Hangar) -> None:
    # A and C already overlap EACH OTHER at x ≈ 2; the mover B tows up the
    # far x = 8 corridor, clear of both. collisions.check reports the A–C
    # conflict at every sample — path_first_conflict must skip it (B is not
    # named) and return None. A regression that dropped the
    # ``mover.id in conflict.planes`` filter would return the A–C conflict
    # here, so this is the test that actually guards that branch.
    fleet = {"A": _box_plane("A"), "B": _box_plane("B"), "C": _box_plane("C")}
    placed = Layout(
        fleet=fleet,
        hangar=simple_hangar,
        placements=(
            Placement("A", 2.0, 8.0, 0.0, on_carts=False),
            Placement("C", 2.3, 8.0, 0.0, on_carts=False),  # overlaps A
        ),
    )
    # Sanity: the placed layout really does contain a non-mover conflict.
    from hangarfit.collisions import check

    pre = check(placed)
    assert not pre.valid and all("B" not in c.planes for c in pre.conflicts)

    arc = plan_dubins(Pose(8.0, 0.0, 0.0), Pose(8.0, 8.0, 0.0), turn_radius_m=4.0)
    assert path_first_conflict(arc, fleet["B"], mover_on_carts=False, placed=placed) is None


def test_hangar_bounds_during_motion_names_mover(
    simple_hangar: Hangar, two_planes_fleet: dict[str, Aircraft]
) -> None:
    # B is towed straight at the back wall and past it: once its nose box
    # crosses y = 20 the bounds check fires, naming the mover.
    fleet = two_planes_fleet
    placed = Layout(fleet=fleet, hangar=simple_hangar, placements=())
    arc = plan_dubins(Pose(5.0, 18.0, 0.0), Pose(5.0, 20.5, 0.0), turn_radius_m=4.0)
    conflict = path_first_conflict(arc, fleet["B"], mover_on_carts=False, placed=placed)
    assert conflict is not None
    assert conflict.kind == "hangar_bounds"
    assert "B" in conflict.planes


def test_front_door_protrusion_is_exempt_for_mover(simple_hangar: Hangar) -> None:
    # A plane whose fuselage spans the origin protrudes to y = -2 at the y = 0
    # entry pose (it is straddling the door, mid-tow). The front gap is exempt
    # during motion (#197 / front-gap exemption): an otherwise-clear straight
    # approach up the middle returns None, NOT a hangar_bounds conflict. The
    # static collisions.check oracle WOULD flag the y = -2 rear vertex; this is
    # the regression that the universal-no_feasible_plan bug was hiding behind.
    fleet = {"B": _spanning_plane("B")}
    placed = Layout(fleet=fleet, hangar=simple_hangar, placements=())
    arc = plan_dubins(Pose(10.0, 0.0, 0.0), Pose(10.0, 10.0, 0.0), turn_radius_m=4.0)
    assert path_first_conflict(arc, fleet["B"], mover_on_carts=False, placed=placed) is None


def test_side_wall_still_enforced_for_mover_during_motion(simple_hangar: Hangar) -> None:
    # Front-gap exemption removes ONLY the front (y < 0) boundary. A mover whose
    # part pokes past a side wall (x < 0) while inside (y >= 0) still conflicts:
    # heading 90 => nose toward +x, so the 4 m fuselage spans x in [-2, 2] at
    # px = 0 and the rear vertex sits at x = -2 < 0 with y ~ 5 >= 0.
    fleet = {"B": _spanning_plane("B")}
    placed = Layout(fleet=fleet, hangar=simple_hangar, placements=())
    arc = plan_dubins(Pose(0.0, 5.0, 90.0), Pose(2.0, 5.0, 90.0), turn_radius_m=4.0)
    conflict = path_first_conflict(arc, fleet["B"], mover_on_carts=False, placed=placed)
    assert conflict is not None
    assert conflict.kind == "hangar_bounds"
    assert "B" in conflict.planes


def test_bay_intrusion_during_motion_names_mover(
    simple_hangar: Hangar, two_planes_fleet: dict[str, Aircraft]
) -> None:
    # With A in maintenance (away), the back bay (y ∈ (18, 20]) is a keep-out.
    # B is towed up x = 10 into the bay; the intrusion must name the mover.
    # This is gated on `maintenance_plane` being threaded into the per-sample
    # Layout — a regression dropping it would silently disable bay detection.
    fleet = two_planes_fleet
    placed = Layout(fleet=fleet, hangar=simple_hangar, placements=(), maintenance_plane="A")
    arc = plan_dubins(Pose(10.0, 16.0, 0.0), Pose(10.0, 18.6, 0.0), turn_radius_m=4.0)
    conflict = path_first_conflict(arc, fleet["B"], mover_on_carts=False, placed=placed)
    assert conflict is not None
    assert conflict.kind == "bay_intrusion"
    assert "B" in conflict.planes


def test_turning_arc_to_blocked_slot_names_mover(simple_hangar: Hangar) -> None:
    # A genuinely turning arc (heading 0 → 90 with an x/y offset) ends on a
    # parked plane. The sampled poses carry rotated headings into the per-pose
    # Placement, so this exercises the heading→placement wiring on a non-
    # axis-aligned path — not just the heading-0 straights above.
    fleet = {"A": _box_plane("A"), "B": _box_plane("B")}
    arc = plan_dubins(Pose(4.0, 2.0, 0.0), Pose(10.0, 8.0, 90.0), turn_radius_m=4.0)
    assert [s.kind for s in arc.segments] != ["S"]  # really a turn, not a straight
    placed = Layout(
        fleet=fleet,
        hangar=simple_hangar,
        placements=(Placement("A", 10.0, 8.0, 90.0, on_carts=False),),  # B's end slot
    )
    conflict = path_first_conflict(arc, fleet["B"], mover_on_carts=False, placed=placed)
    assert conflict is not None
    assert conflict.kind == "fuselage_fuselage_overlap"
    assert "B" in conflict.planes


def test_cart_pivot_clear_returns_none(simple_hangar: Hangar) -> None:
    # A cart-borne mover pivots in place (turn_radius 0) with no obstacle.
    # Because nothing conflicts, sample() runs the FULL angular sweep, placing
    # the on-carts mover at every swept heading — exercising the pivot sampler
    # and the on_carts=True placement / cart-rule path end to end.
    fleet = {"A": _box_plane("A"), "B": _cart_plane("B")}
    placed = Layout(
        fleet=fleet,
        hangar=simple_hangar,
        placements=(Placement("A", 2.0, 2.0, 0.0, on_carts=False),),
    )
    pivot = plan_dubins(Pose(10.0, 10.0, 0.0), Pose(10.0, 10.0, 90.0), turn_radius_m=0.0)
    assert path_first_conflict(pivot, fleet["B"], mover_on_carts=True, placed=placed) is None


def test_cart_pivot_into_neighbour_names_mover(simple_hangar: Hangar) -> None:
    # Same cart pivot, but a neighbour sits on the pivot footprint: the
    # on-carts mover must be named in the resulting conflict.
    fleet = {"A": _box_plane("A"), "B": _cart_plane("B")}
    placed = Layout(
        fleet=fleet,
        hangar=simple_hangar,
        placements=(Placement("A", 10.0, 10.3, 0.0, on_carts=False),),
    )
    pivot = plan_dubins(Pose(10.0, 10.0, 0.0), Pose(10.0, 10.0, 90.0), turn_radius_m=0.0)
    conflict = path_first_conflict(pivot, fleet["B"], mover_on_carts=True, placed=placed)
    assert conflict is not None
    assert "B" in conflict.planes
