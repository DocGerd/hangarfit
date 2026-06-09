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

import os
import subprocess
import sys
from pathlib import Path

import pytest

from hangarfit.models import (
    Aircraft,
    ApronShallowDrop,
    Door,
    Hangar,
    MaintenanceBay,
    Part,
    Placement,
    Wheels,
)
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


def _always_cart_plane(plane_id: str) -> Aircraft:
    """A cart-borne plane (turn_radius_m None) — for the all-cart derive branch."""
    return Aircraft(
        id=plane_id,
        name=f"Cart {plane_id}",
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_cart",
        turn_radius_m=None,
        measured=False,
        parts=(_fuselage_box(),),
        wheels=_TAIL_WHEELS,
    )


def _long_box_plane(plane_id: str, *, body_length_m: float) -> Aircraft:
    """A plane whose fuselage box is ``body_length_m`` long fore-aft and CENTRED on
    the origin (``offset_x_m=0.0``), so at heading 0 the body spans world
    y ∈ [ref_y − body_length_m/2, ref_y + body_length_m/2]. A long body cannot fit
    an apron start pose in a shallow apron (its aft vertex overflows the south
    bound) — the too-shallow-apron-drops-the-plane case (#503). Its fore-aft extent
    is exactly ``body_length_m`` (one part centred on the origin)."""
    part = Part(
        kind="fuselage_aft",
        length_m=body_length_m,
        width_m=0.6,
        offset_x_m=0.0,
        offset_y_m=0.0,
        angle_deg=0.0,
        z_bottom_m=0.0,
        z_top_m=1.0,
    )
    return Aircraft(
        id=plane_id,
        name=f"Long {plane_id}",
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_own_gear",
        turn_radius_m=4.0,
        measured=False,
        parts=(part,),
        wheels=_TAIL_WHEELS,
    )


def _wide_box_plane(plane_id: str, *, body_width_m: float) -> Aircraft:
    """A plane whose fuselage is ``body_width_m`` wide (lateral). At heading 0 the
    body spans world x ∈ [ref_x − body_width_m/2, ref_x + body_width_m/2]; used to
    test a footprint wider than the door."""
    part = Part(
        kind="fuselage_aft",
        length_m=1.0,
        width_m=body_width_m,
        offset_x_m=0.5,
        offset_y_m=0.0,
        angle_deg=0.0,
        z_bottom_m=0.0,
        z_top_m=1.0,
    )
    return Aircraft(
        id=plane_id,
        name=f"Wide {plane_id}",
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_own_gear",
        turn_radius_m=4.0,
        measured=False,
        parts=(part,),
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


def test_derive_apron_depth_all_cart_fleet_is_length_only() -> None:
    # All planes are always_cart (turn_radius_m is None) ⇒ no radius term, just
    # the longest fore-aft length (1.0). Exercises the `is not None` filter branch.
    fleet = {"G": _always_cart_plane("G"), "H": _always_cart_plane("H")}
    assert derive_apron_depth(fleet) == pytest.approx(1.0)


def test_derive_apron_depth_mixed_fleet_skips_cart_radius() -> None:
    fleet = {"G": _always_cart_plane("G"), "A": _box_plane("A", turn_radius_m=5.0)}
    assert derive_apron_depth(fleet) == pytest.approx(1.0 + 5.0)


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
    # Nose-out target (h=180): the apron forces the start onto the apron AND, since
    # the target is nose-out, the rear cone is emitted (#480 gates it on nose-out).
    h = _hangar(door_center=10.0, door_width=6.0, apron_depth_m=6.0)
    slot = _slot("A", x=10.0, y=12.0, h=180.0)
    poses = entry_poses(slot, h)
    # y=0 (door line) is excluded — every start is ON the apron (#412 slide-in).
    assert {p.y_m for p in poses} == {-3.0, -6.0}  # {-d/2, -d}
    assert all(p.y_m < 0.0 for p in poses)
    headings = {p.heading_deg for p in poses}
    assert {330.0, 345.0, 0.0, 15.0, 30.0} <= headings  # forward cone retained
    assert {150.0, 165.0, 180.0, 195.0, 210.0} <= headings  # rear cone (nose-out target)


def test_entry_poses_with_apron_is_deterministic() -> None:
    h = _hangar(apron_depth_m=6.0)
    slot = _slot("A", x=8.0, y=12.0, h=0.0)
    assert entry_poses(slot, h) == entry_poses(slot, h)


def test_entry_poses_apron_emit_order_x_outer_y_middle_heading_inner() -> None:
    """The fixed emit order is x-outer, y-middle, heading-inner (ADR-0003).

    Uses a nose-out target (h=180) so the rear cone is emitted (#480 gates the
    rear cone on a nose-out target, not on the apron)."""
    h = _hangar(door_center=10.0, door_width=6.0, apron_depth_m=4.0)
    slot = _slot("A", x=10.0, y=12.0, h=180.0)  # x_centre == x_target == x_mid == 10 ⇒ 1 x-sample
    poses = list(entry_poses(slot, h))
    headings = (330.0, 345.0, 0.0, 15.0, 30.0, 150.0, 165.0, 180.0, 195.0, 210.0)
    expected = [Pose(x_m=10.0, y_m=y, heading_deg=hd) for y in (-2.0, -4.0) for hd in headings]
    assert poses == expected


# ── #480: rear-entry cone is gated on a NOSE-OUT target, not on the apron ──────


def test_entry_poses_rear_cone_for_nose_out_target_without_apron() -> None:
    """#480: a nose-out target (heading ~180) gets the rear-entry cone even with
    NO apron — so the plane can be backed in rather than pirouetting inside.
    This deliberately changes the depth-0 grid for nose-out targets (superseding
    the #412 depth-0 cross-version byte-identity for that case)."""
    h = _hangar(door_center=10.0, door_width=6.0)  # no apron
    slot = _slot("A", x=10.0, y=12.0, h=180.0)  # nose-out
    poses = entry_poses(slot, h)
    assert {p.y_m for p in poses} == {0.0}  # still the door line (no apron)
    headings = {p.heading_deg for p in poses}
    assert {330.0, 345.0, 0.0, 15.0, 30.0} <= headings  # forward cone
    assert {150.0, 165.0, 180.0, 195.0, 210.0} <= headings  # rear cone, no apron needed


def test_entry_poses_no_rear_cone_for_nose_in_target_even_with_apron() -> None:
    """#480: a nose-in target (heading ~0) keeps the forward cone only, even with
    an apron — a nose-in slot never wins a rear-entry seed, so don't waste it."""
    h = _hangar(door_center=10.0, door_width=6.0, apron_depth_m=6.0)
    slot = _slot("A", x=10.0, y=12.0, h=0.0)  # nose-in
    poses = entry_poses(slot, h)
    assert all(p.y_m < 0.0 for p in poses)  # apron still forces the start onto the apron
    headings = {p.heading_deg for p in poses}
    assert headings == {330.0, 345.0, 0.0, 15.0, 30.0}  # forward cone ONLY, no rear cone


def test_entry_poses_nose_out_gate_boundary() -> None:
    """#480: the rear cone is emitted iff |wrap180(h-180)| <= 45 (covers the rear
    cone's own +/-30 span plus margin). h=135 and h=225 are in; h=134/226 are out."""
    h = _hangar(door_center=10.0, door_width=6.0)
    rear = {150.0, 165.0, 180.0, 195.0, 210.0}

    def _headings(target_h: float) -> set[float]:
        return {p.heading_deg for p in entry_poses(_slot("A", x=10.0, y=12.0, h=target_h), h)}

    assert rear <= _headings(135.0)  # boundary in
    assert rear <= _headings(225.0)  # boundary in (symmetric)
    assert not (rear & _headings(134.0))  # just outside
    assert not (rear & _headings(226.0))  # just outside


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


def test_wide_part_free_on_apron_but_conflicts_straddling() -> None:
    """The headline apron contract at its defining boundary: a footprint WIDER
    than the door (8 m body > 6 m door) is open ground when wholly staged on the
    apron, but conflicts when it straddles the wall (it cannot thread the door)."""
    plane = _wide_box_plane("W", body_width_m=8.0)  # body x-span 8 m, door is 6 m
    with_apron = _hangar(width_m=20.0, door_center=10.0, door_width=6.0, apron_depth_m=5.0)
    # Wholly on the apron (body y ∈ [-2, -1]), centred at the door x ⇒ x ∈ [6, 14]
    # exceeds the door [7, 13] but is within the side walls [0, 20]: open ground.
    assert _mover_motion_bounds_conflict(plane, _slot("W", 10.0, -2.0, 0.0), with_apron) is None
    # Straddling y=0: the y<0 corners at x≈6 and x≈14 sit beside the door ⇒ conflict.
    assert _mover_motion_bounds_conflict(plane, _slot("W", 10.0, -0.5, 0.0), with_apron) is not None


def test_footprint_tangent_at_y_zero_is_free_with_apron() -> None:
    """Boundary at the `y >= 0` branch: a body tangent at y=0 from below (y ∈
    [-1, 0]) beside the door does not straddle (ymax == 0, not > 0) ⇒ free."""
    plane = _box_plane("A")
    placement = _slot("A", x=3.0, y=-1.0, h=0.0)  # body y ∈ [-1, 0], beside the door
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


_APRON_PLAN_HASH_SNIPPET = """
import hashlib
from tests.test_towplanner_apron import _box_plane, _hangar, _slot, _layout
from hangarfit.towplanner import plan_fill
h = _hangar(width_m=20.0, length_m=30.0, apron_depth_m=6.0)
f = {"A": _box_plane("A"), "B": _box_plane("B")}
p = plan_fill(_layout(f, h, _slot("A", 6.0, 8.0, 0.0), _slot("B", 14.0, 16.0, 180.0)))
blob = repr([
    (m.plane_id, m.target_slot, m.path.start, m.path.end, m.path.turn_radius_m, m.path.segments)
    for m in p.moves
])
print(hashlib.sha256(blob.encode()).hexdigest())
"""


@pytest.mark.serial
def test_apron_movesplan_byte_identical_across_processes() -> None:
    """The depth>0 apron plan must be byte-identical across fresh processes with
    different PYTHONHASHSEED — the set()-iteration-order / hash-seed guard that an
    in-process ``==`` cannot catch (the analogue of the solver double-solve
    canary). The apron's only set (`entry_poses` dedup) is membership-only, so
    this should hold; this pins it permanently."""
    repo_root = Path(__file__).resolve().parent.parent

    def _run(hashseed: str) -> str:
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = hashseed
        proc = subprocess.run(
            [sys.executable, "-c", _APRON_PLAN_HASH_SNIPPET],
            capture_output=True,
            text=True,
            cwd=repo_root,
            env=env,
            check=True,
        )
        return proc.stdout.strip()

    h1 = _run("111")
    h2 = _run("777")
    assert h1 and h1 == h2, f"apron MovesPlan diverged across processes: {h1!r} != {h2!r}"


# ── #503: too-shallow-apron drops (plan-inert out-param; plan byte-identical) ──
# When apron_depth_m > 0 but is too shallow for a given plane's footprint, ALL of
# that plane's apron start poses are filtered and plan_path silently falls back to
# the y=0 door-line pose (no slide-in). plan_fill records the drop into the
# OPTIONAL `apron_dropped_out` list (plane id + suggested min depth) — it does NOT
# print. The data is purely observational: it never touches the MovesPlan (the
# apron byte-identity canaries above remain the determinism proof). The CLI emits
# the user-facing warning from this data (see tests/test_cli_solve.py /
# tests/test_cli_view.py).


def test_shallow_apron_populates_drop_naming_plane_and_min_depth() -> None:
    """The fuji-at-6 m analogue (#503): an 8 m-long plane in a 6 m apron has all
    apron start poses filtered → it tows via the y=0 door line. plan_fill records
    one ApronShallowDrop naming the plane with a suggested depth ≈ its 8 m
    footprint — and never prints."""
    h = _hangar(width_m=20.0, length_m=30.0, door_center=10.0, door_width=6.0, apron_depth_m=6.0)
    fleet = {"A": _long_box_plane("A", body_length_m=8.0)}
    target = _layout(fleet, h, _slot("A", 10.0, 12.0, 0.0))
    drops: list[ApronShallowDrop] = []
    plan = plan_fill(target, apron_dropped_out=drops)
    # The plane is still routed (best-effort) — via the door line, not the apron.
    first = list(plan.moves[0].path.sample(step_m=0.25, step_deg=5.0))[0]
    assert first.y_m == 0.0  # door-line fallback, NOT a slide-in
    assert len(drops) == 1
    assert drops[0].plane_id == "A"
    assert drops[0].min_depth_m == pytest.approx(8.0)  # the 8 m footprint extent


def test_deep_apron_records_no_drop() -> None:
    """A 14 m apron clears the 8 m plane's footprint → it slides in from the apron,
    so NO drop is recorded."""
    h = _hangar(width_m=20.0, length_m=30.0, door_center=10.0, door_width=6.0, apron_depth_m=14.0)
    fleet = {"A": _long_box_plane("A", body_length_m=8.0)}
    target = _layout(fleet, h, _slot("A", 10.0, 12.0, 0.0))
    drops: list[ApronShallowDrop] = []
    plan = plan_fill(target, apron_dropped_out=drops)
    first = list(plan.moves[0].path.sample(step_m=0.25, step_deg=5.0))[0]
    assert first.y_m < 0.0  # slides in from the apron
    assert drops == []


def test_no_apron_records_no_drop() -> None:
    """With no apron (depth 0) the y=0 door-line start is the CORRECT behaviour,
    not a dropped slide-in — so no drop is recorded."""
    h = _hangar(width_m=20.0, length_m=30.0, door_center=10.0, door_width=6.0, apron_depth_m=0.0)
    fleet = {"A": _long_box_plane("A", body_length_m=8.0)}
    target = _layout(fleet, h, _slot("A", 10.0, 12.0, 0.0))
    drops: list[ApronShallowDrop] = []
    plan_fill(target, apron_dropped_out=drops)
    assert drops == []


def test_shallow_apron_drop_only_for_the_dropped_plane() -> None:
    """Mixed fleet (the #499 §6 observation): the long plane drops to the door
    line; the short plane engages the apron. Exactly one drop, naming the long
    plane only."""
    h = _hangar(width_m=20.0, length_m=30.0, door_center=10.0, door_width=6.0, apron_depth_m=6.0)
    fleet = {
        "LONG": _long_box_plane("LONG", body_length_m=8.0),
        "SHORT": _box_plane("SHORT"),
    }
    target = _layout(fleet, h, _slot("LONG", 6.0, 22.0, 0.0), _slot("SHORT", 14.0, 8.0, 0.0))
    drops: list[ApronShallowDrop] = []
    plan_fill(target, apron_dropped_out=drops)
    assert [d.plane_id for d in drops] == ["LONG"]


def test_shallow_apron_out_param_does_not_change_the_plan() -> None:
    """The out-param is observational: the MovesPlan is byte-identical whether or
    not it is passed (it is plan-inert). Both branches — with and without the
    out-param — yield the same plan, and equal to each other."""
    h = _hangar(width_m=20.0, length_m=30.0, door_center=10.0, door_width=6.0, apron_depth_m=6.0)
    fleet = {"A": _long_box_plane("A", body_length_m=8.0)}
    target = _layout(fleet, h, _slot("A", 10.0, 12.0, 0.0))
    drops: list[ApronShallowDrop] = []
    with_out = plan_fill(target, apron_dropped_out=drops)
    without_out = plan_fill(target)
    assert with_out == without_out == plan_fill(target)
    assert len(drops) == 1  # the out-param was still populated
