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
    path_first_conflict,
    plan_fill,
    plan_reeds_shepp,
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


def test_entry_poses_with_apron_forces_start_onto_apron_and_adds_reverse_headings() -> None:
    h = _hangar(door_center=10.0, door_width=6.0, apron_depth_m=6.0)
    slot = _slot("A", x=10.0, y=12.0, h=0.0)
    poses = entry_poses(slot, h)
    # y=0 (door line) is excluded — every start is ON the apron (#412 slide-in).
    assert {p.y_m for p in poses} == {-3.0, -6.0}  # {-d/2, -d}
    assert all(p.y_m < 0.0 for p in poses)
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
    expected = [Pose(x_m=10.0, y_m=y, heading_deg=hd) for y in (-2.0, -4.0) for hd in headings]
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


# ── Task 5: grid-heuristic south-pad reconciliation ──────────────────────────


def test_grid_heuristic_south_pad_reconciles_with_apron_depth() -> None:
    """The free-space grid extends south by ``max(_GRID_H_Y_PAD_M, apron_depth_m)``
    rows: a shallow apron (<= 6 m) leaves the historic -12 floor; a deep apron
    (> 6 m) extends the field further south."""
    from hangarfit.towplanner import (
        _GRID_H_Y_PAD_M,
        _GRID_XY_M,
        _build_grid_heuristic,
        _build_obstacles,
    )

    goal = Pose(x_m=4.0, y_m=10.0, heading_deg=0.0)
    h_shallow = _hangar(apron_depth_m=3.0)  # <= 6 ⇒ pad stays 6
    h_deep = _hangar(apron_depth_m=10.0)  # > 6 ⇒ pad = 10
    field_shallow = _build_grid_heuristic(
        goal, _build_obstacles(_layout({}, h_shallow), mover_id="A"), h_shallow
    )
    field_deep = _build_grid_heuristic(
        goal, _build_obstacles(_layout({}, h_deep), mover_id="A"), h_deep
    )
    assert min(iy for _, iy in field_shallow) == -round(_GRID_H_Y_PAD_M / _GRID_XY_M)  # -12
    assert min(iy for _, iy in field_deep) == -round(10.0 / _GRID_XY_M)  # -20


# ── Task 6: integration — apron fill, byte-identity, reverse-into-apron canary ─


def test_apron_fill_routes_plane_from_outside_the_door() -> None:
    """The whole point of #412: with an apron the tow STARTS outside the hangar
    (first sample y < 0) and slides in to the slot, oracle-clean."""
    h = _hangar(width_m=20.0, length_m=30.0, door_center=10.0, door_width=6.0, apron_depth_m=6.0)
    fleet = {"A": _box_plane("A")}
    target = _layout(fleet, h, _slot("A", 10.0, 12.0, 0.0))
    plan = plan_fill(target)
    arc = plan.moves[0].path
    first = list(arc.sample(step_m=0.25, step_deg=5.0))[0]
    last = arc.pose_at(arc.length_m)
    assert first.y_m < 0.0  # originates OUTSIDE the hangar (the slide-in)
    assert last.x_m == pytest.approx(10.0, abs=1e-6)
    assert last.y_m == pytest.approx(12.0, abs=1e-6)
    assert (
        path_first_conflict(arc, fleet["A"], mover_on_carts=False, placed=_layout(fleet, h)) is None
    )


def test_apron_gate_both_ways_depth_zero_at_door_line_apron_outside() -> None:
    """Depth 0 keeps the pre-apron door-line start; depth>0 originates outside."""
    fleet = {"A": _box_plane("A")}
    slot = _slot("A", 10.0, 12.0, 0.0)
    p0 = plan_fill(_layout(fleet, _hangar(apron_depth_m=0.0), slot))
    p6 = plan_fill(_layout(fleet, _hangar(apron_depth_m=6.0), slot))
    first0 = list(p0.moves[0].path.sample(step_m=0.25, step_deg=5.0))[0]
    first6 = list(p6.moves[0].path.sample(step_m=0.25, step_deg=5.0))[0]
    assert first0.y_m == 0.0  # pre-apron behaviour: starts on the door line
    assert first6.y_m < 0.0  # apron: originates outside


def test_apron_movesplan_is_byte_deterministic() -> None:
    h = _hangar(apron_depth_m=6.0)
    fleet = {"A": _box_plane("A"), "B": _box_plane("B")}
    target = _layout(fleet, h, _slot("A", 8.0, 8.0, 0.0), _slot("B", 12.0, 22.0, 0.0))
    assert plan_fill(target) == plan_fill(target)


def test_apron_depth_absent_equals_explicit_zero() -> None:
    """Migration anchor: absent apron_depth_m ⇒ 0 ⇒ identical to explicit 0."""
    fleet = {"A": _box_plane("A"), "B": _box_plane("B")}
    slots = (_slot("A", 8.0, 8.0, 0.0), _slot("B", 12.0, 22.0, 0.0))
    h_absent = _hangar(width_m=20.0, length_m=30.0)  # apron_depth_m default 0.0
    h_explicit0 = _hangar(width_m=20.0, length_m=30.0, apron_depth_m=0.0)
    assert plan_fill(_layout(fleet, h_absent, *slots)) == plan_fill(
        _layout(fleet, h_explicit0, *slots)
    )


def test_reverse_into_apron_sign_canary() -> None:
    """ADR-0002 sign-flip guard for a y<0 reverse-into-apron start pose: backing
    in from the apron (heading ~180, nose-out) must reach the deep nose-out goal,
    not flip into the wrong quadrant. Backstops the new y<0 start poses against
    the symmetric Reeds–Shepp word matrix."""
    start = Pose(x_m=10.0, y_m=-4.0, heading_deg=180.0)  # on the apron, nose toward -y
    goal = Pose(x_m=10.0, y_m=6.0, heading_deg=180.0)  # parked deep, nose-out
    arc = plan_reeds_shepp(start, goal, turn_radius_m=4.0)
    last = arc.pose_at(arc.length_m)
    assert last.x_m == pytest.approx(10.0, abs=1e-3)
    assert last.y_m == pytest.approx(6.0, abs=1e-3)
