"""Staging apron (#412 / ADR-0021): apron-pose grid, apron-aware bounds rule,
apron-started fill, byte-identity at depth 0, and the reverse-into-apron sign
canary.

The apron is gated on ``hangar.apron_depth_m > 0``: at depth 0 every changed
planner function executes its pre-apron path verbatim, so the no-apron
``MovesPlan`` is byte-identical (ADR-0003). These tests pin both sides of that
gate — the depth-0 reproduction and the depth>0 apron behaviour.

Fixture builders are module-local on purpose (mirroring the other towplanner
test files); the box plane's fuselage is mounted forward (offset_x_m=0.5,
length_m=1.0 ⇒ at heading 0 the body occupies world y ∈ [ref, ref + 1]).
"""

import pytest

from hangarfit.models import Aircraft, Door, Hangar, MaintenanceBay, Part, Placement, Wheels
from hangarfit.towplanner import (
    Pose,
    _mover_motion_bounds_conflict,
    derive_apron_depth,
    entry_poses,
)

_TAIL_WHEELS = Wheels(main_offset_x_m=0.20, track_m=1.8, third_wheel_offset_x_m=-2.0)


def _fuselage_box() -> Part:
    """A 1.0 m × 0.6 m fuselage box mounted forward of the plane origin, so a
    placement at the front wall (y = 0) keeps every world vertex at y >= 0."""
    return Part(
        kind="fuselage_aft",
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
        wheels=_TAIL_WHEELS,
    )


def _hangar(
    width_m: float = 20.0,
    length_m: float = 30.0,
    door_center: float = 10.0,
    door_width: float = 6.0,
    apron_depth_m: float = 0.0,
) -> Hangar:
    return Hangar(
        length_m=length_m,
        width_m=width_m,
        door=Door(center_x_m=door_center, width_m=door_width),
        maintenance_bay=MaintenanceBay(center_x_m=width_m / 2, width_m=2.0, depth_m=2.0),
        clearance_m=0.5,
        wing_layer_clearance_m=0.3,
        apron_depth_m=apron_depth_m,
    )


def _slot(pid: str, x: float, y: float, h: float = 0.0, on_carts: bool = False) -> Placement:
    return Placement(plane_id=pid, x_m=x, y_m=y, heading_deg=h, on_carts=on_carts)


def _layout(fleet: dict[str, Aircraft], hangar: Hangar, *placements: Placement):
    from hangarfit.models import Layout

    return Layout(fleet=fleet, hangar=hangar, placements=tuple(placements))


# ── Task 2: derive_apron_depth (the opt-in 'auto' value) ─────────────────────


def test_derive_apron_depth_is_max_length_plus_max_turn_radius() -> None:
    # _box_plane fuselage: offset_x_m=0.5, length_m=1.0 ⇒ fore-aft extent [0, 1] = 1.0 m.
    fleet = {"A": _box_plane("A", turn_radius_m=4.0), "B": _box_plane("B", turn_radius_m=6.0)}
    assert derive_apron_depth(fleet) == pytest.approx(1.0 + 6.0)


def test_derive_apron_depth_empty_fleet_is_zero() -> None:
    assert derive_apron_depth({}) == 0.0


# ── Task 3: entry_poses apron grid (byte-identical at depth 0) ────────────────


def test_entry_poses_depth_zero_exact_order_unchanged() -> None:
    """Depth 0 reproduces the pre-apron grid EXACTLY: same poses, same order,
    all at y=0, forward cone only (the ADR-0003 byte-identity anchor)."""
    h = _hangar(door_center=10.0, door_width=6.0)  # door interval [7, 13]
    slot = _slot("A", x=8.0, y=12.0, h=0.0)  # x_centre=10, x_target=8, x_mid=9
    expected = [
        Pose(x_m=x, y_m=0.0, heading_deg=hd)
        for x in (10.0, 8.0, 9.0)
        for hd in (330.0, 345.0, 0.0, 15.0, 30.0)
    ]
    assert list(entry_poses(slot, h)) == expected


def test_entry_poses_with_apron_adds_y_offsets_and_reverse_headings() -> None:
    h = _hangar(door_center=10.0, door_width=6.0, apron_depth_m=6.0)
    slot = _slot("A", x=10.0, y=12.0, h=0.0)
    poses = entry_poses(slot, h)
    assert {p.y_m for p in poses} == {0.0, -3.0, -6.0}  # {0, -d/2, -d}
    headings = {p.heading_deg for p in poses}
    assert {330.0, 345.0, 0.0, 15.0, 30.0} <= headings  # forward cone retained
    assert {150.0, 165.0, 180.0, 195.0, 210.0} <= headings  # reverse cone added


def test_entry_poses_with_apron_is_deterministic() -> None:
    h = _hangar(apron_depth_m=6.0)
    slot = _slot("A", x=8.0, y=12.0, h=0.0)
    assert entry_poses(slot, h) == entry_poses(slot, h)


def test_entry_poses_apron_emit_order_x_outer_y_middle_heading_inner() -> None:
    """The fixed emit order is x-outer, y-middle, heading-inner (ADR-0003)."""
    h = _hangar(door_center=10.0, door_width=6.0, apron_depth_m=4.0)
    slot = _slot("A", x=10.0, y=12.0, h=0.0)  # x_centre == x_target == x_mid == 10 ⇒ 1 x-sample
    poses = list(entry_poses(slot, h))
    headings = (330.0, 345.0, 0.0, 15.0, 30.0, 150.0, 165.0, 180.0, 195.0, 210.0)
    expected = [Pose(x_m=10.0, y_m=y, heading_deg=hd) for y in (0.0, -2.0, -4.0) for hd in headings]
    assert poses == expected


# ── Task 4: apron-aware front-wall rule (#411 jamb retained) ─────────────────
# _box_plane at heading 0 occupies world x ∈ [ref_x-0.3, ref_x+0.3],
# y ∈ [ref_y, ref_y+1]. Door interval below is [7, 13]; x=3 is beside the door.


def test_apron_open_pose_beside_door_free_with_apron_but_conflict_without() -> None:
    plane = _box_plane("A")
    # Wholly in front of the wall (body y ∈ [-2, -1], all < 0), off to the side.
    placement = _slot("A", x=3.0, y=-2.0, h=0.0)
    no_apron = _hangar(door_center=10.0, door_width=6.0)
    with_apron = _hangar(door_center=10.0, door_width=6.0, apron_depth_m=5.0)
    assert _mover_motion_bounds_conflict(plane, placement, no_apron) is not None  # #411 jamb clip
    assert _mover_motion_bounds_conflict(plane, placement, with_apron) is None  # open apron ground


def test_straddling_front_wall_beside_door_still_conflicts_with_apron() -> None:
    plane = _box_plane("A")
    # Body y ∈ [-0.5, 0.5] straddles y=0, beside the door (x ≈ 3) ⇒ crosses solid wall.
    placement = _slot("A", x=3.0, y=-0.5, h=0.0)
    with_apron = _hangar(door_center=10.0, door_width=6.0, apron_depth_m=5.0)
    assert _mover_motion_bounds_conflict(plane, placement, with_apron) is not None


def test_beyond_apron_south_bound_conflicts() -> None:
    plane = _box_plane("A")
    # Body y ∈ [-7, -6], past the apron south bound y = -apron_depth = -5.
    placement = _slot("A", x=10.0, y=-7.0, h=0.0)
    with_apron = _hangar(door_center=10.0, door_width=6.0, apron_depth_m=5.0)
    assert _mover_motion_bounds_conflict(plane, placement, with_apron) is not None


def test_door_passage_through_opening_allowed_with_apron() -> None:
    plane = _box_plane("A")
    # Body y ∈ [-0.5, 0.5] straddles y=0 but within the door opening (x ≈ 10).
    placement = _slot("A", x=10.0, y=-0.5, h=0.0)
    with_apron = _hangar(door_center=10.0, door_width=6.0, apron_depth_m=5.0)
    assert _mover_motion_bounds_conflict(plane, placement, with_apron) is None
