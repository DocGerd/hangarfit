"""Door-entry cone (#262): entry_poses grid, candidate filtering, multi-start plan_path.

Tests are grouped into four blocks:
1. Cone-grid contents + determinism: ``entry_poses`` emits the right set of poses in a
   fixed order and the same input always yields the same sequence.
2. Candidate filtering: poses that clip side/back walls are dropped before the search;
   if all are filtered the fallback straight-in centre pose is returned.
3. Multi-start plan_path: the search accepts the cone and seeds all surviving entries.
4. Path-length regression: an off-to-the-side slot gets a shorter path than the v1
   single-start baseline (straight-in only).
"""

from __future__ import annotations

import pytest

import hangarfit.towplanner as tp
from hangarfit.models import (
    Aircraft,
    Door,
    Hangar,
    Layout,
    MaintenanceBay,
    Part,
    Placement,
)
from hangarfit.towplanner import (
    Pose,
    entry_pose,
    entry_poses,
    plan_path,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_HEADINGS = (330.0, 345.0, 0.0, 15.0, 30.0)  # the spec'd 5-heading cone


def _hangar(
    width_m: float = 20.0,
    length_m: float = 30.0,
    door_center: float = 10.0,
    door_width: float = 6.0,
) -> Hangar:
    return Hangar(
        length_m=length_m,
        width_m=width_m,
        door=Door(center_x_m=door_center, width_m=door_width),
        maintenance_bay=MaintenanceBay(center_x_m=width_m / 2, width_m=2.0, depth_m=2.0),
        clearance_m=0.3,
        wing_layer_clearance_m=0.2,
    )


def _slot(pid: str, x: float, y: float, h: float = 0.0) -> Placement:
    return Placement(plane_id=pid, x_m=x, y_m=y, heading_deg=h, on_carts=False)


def _box_plane(pid: str, *, turn_radius_m: float = 4.0) -> Aircraft:
    """Small own-gear plane — 1 m × 0.6 m fuselage mounted forward of origin."""
    return Aircraft(
        id=pid,
        name=pid,
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


def _wide_plane(pid: str, *, span_m: float = 6.0, turn_radius_m: float = 3.0) -> Aircraft:
    """A plane wide enough that angled entry poses may clip side walls."""
    return Aircraft(
        id=pid,
        name=pid,
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_own_gear",
        turn_radius_m=turn_radius_m,
        measured=False,
        parts=(
            Part(
                kind="fuselage",
                length_m=2.0,
                width_m=1.0,
                offset_x_m=0.0,
                offset_y_m=0.0,
                angle_deg=0.0,
                z_bottom_m=0.0,
                z_top_m=1.4,
            ),
            Part(
                kind="wing",
                length_m=1.2,
                width_m=span_m,
                offset_x_m=-0.4,
                offset_y_m=0.0,
                angle_deg=0.0,
                z_bottom_m=1.4,
                z_top_m=1.8,
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Block 1: Cone-grid contents + determinism
# ---------------------------------------------------------------------------


def test_entry_poses_returns_tuple_of_poses() -> None:
    h = _hangar()
    result = entry_poses(_slot("A", x=10.0, y=20.0), h)
    assert isinstance(result, tuple)
    assert len(result) >= 1
    assert all(isinstance(p, Pose) for p in result)


def test_entry_poses_all_at_y_zero() -> None:
    """Every cone candidate is on the front boundary."""
    h = _hangar()
    for pose in entry_poses(_slot("A", x=10.0, y=20.0), h):
        assert pose.y_m == pytest.approx(0.0)


def test_entry_poses_headings_are_the_five_cone_headings() -> None:
    """The heading set across all candidates is exactly the 5-heading cone."""
    h = _hangar()
    slot = _slot("A", x=10.0, y=20.0)
    headings = {p.heading_deg for p in entry_poses(slot, h)}
    assert headings == set(_HEADINGS)


def test_entry_poses_x_values_in_door_interval() -> None:
    """All x-samples must lie within the door interval [center − half, center + half]."""
    door_center, door_width = 10.0, 6.0
    h = _hangar(door_center=door_center, door_width=door_width)
    lo, hi = door_center - door_width / 2, door_center + door_width / 2
    for pose in entry_poses(_slot("A", x=10.0, y=20.0), h):
        assert lo - 1e-9 <= pose.x_m <= hi + 1e-9


def test_entry_poses_includes_door_center_x() -> None:
    """Door centre x must be among the samples."""
    h = _hangar(door_center=10.0, door_width=6.0)
    xs = [p.x_m for p in entry_poses(_slot("A", x=10.0, y=20.0), h)]
    assert any(abs(x - 10.0) < 1e-9 for x in xs)


def test_entry_poses_includes_clamped_target_x() -> None:
    """Clamped target x (same as entry_pose's single output) must be among the samples."""
    h = _hangar(door_center=10.0, door_width=6.0)
    slot = _slot("A", x=9.0, y=20.0)
    expected_x = entry_pose(slot, h).x_m  # the v1 clamped x
    xs = {p.x_m for p in entry_poses(slot, h)}
    assert any(abs(x - expected_x) < 1e-9 for x in xs)


def test_entry_poses_no_duplicates() -> None:
    """No two poses in the grid are identical."""
    h = _hangar()
    poses = entry_poses(_slot("A", x=10.0, y=20.0), h)
    assert len(poses) == len(set(poses))


def test_entry_poses_is_deterministic() -> None:
    """Same inputs → identical output, same order."""
    h = _hangar()
    slot = _slot("A", x=9.0, y=20.0)
    assert entry_poses(slot, h) == entry_poses(slot, h)


def test_entry_poses_order_is_fixed() -> None:
    """The sequence is identical across two calls; order matters for determinism."""
    h = _hangar(door_center=10.0, door_width=8.0)
    slot = _slot("A", x=7.0, y=15.0)
    first = entry_poses(slot, h)
    second = entry_poses(slot, h)
    assert list(first) == list(second)


def test_entry_poses_slot_outside_door_clamps_all_x_to_boundary() -> None:
    """A slot far to the left is still served — its target-x clamps to the door edge."""
    h = _hangar(door_center=10.0, door_width=6.0)
    slot = _slot("A", x=1.0, y=20.0)  # target x=1 is outside door [7,13]
    xs = [p.x_m for p in entry_poses(slot, h)]
    # The clamped target x must be 7.0 (left edge).
    assert any(abs(x - 7.0) < 1e-9 for x in xs)


def test_entry_poses_grid_size_is_at_most_15() -> None:
    """Grid is 3 x-samples × 5 headings = 15 max (deduplication may shrink it)."""
    h = _hangar()
    poses = entry_poses(_slot("A", x=10.0, y=20.0), h)
    assert 1 <= len(poses) <= 15


# ---------------------------------------------------------------------------
# Block 2: Candidate filtering
# ---------------------------------------------------------------------------


def test_entry_poses_filtering_drops_side_wall_clips(monkeypatch: pytest.MonkeyPatch) -> None:
    """Candidates whose footprint clips side/back walls are excluded from the
    cone that plan_path receives.

    We verify this through plan_path's ``entries`` keyword: monkeypatch
    ``_mover_motion_bounds_conflict`` to reject all candidates, then check
    that the fallback is used (at least one entry survives).
    """
    # A hangar so narrow that ANY non-zero heading entry pose clips a side wall
    # for a wide plane. We'll verify the filtering by patching the bounds check.
    h = _hangar(width_m=20.0, length_m=30.0, door_center=10.0, door_width=6.0)
    plane = _box_plane("A")
    placed = Layout(fleet={"A": plane}, hangar=h, placements=())

    rejected: list[Pose] = []
    original = tp._mover_motion_bounds_conflict

    def patched(mover: Aircraft, placement: Placement, hangar: Hangar):  # noqa: ANN202
        pose = Pose(placement.x_m, placement.y_m, placement.heading_deg)
        if pose.heading_deg != 0.0:
            rejected.append(pose)
            from hangarfit.models import Conflict

            return Conflict.single(
                kind="hangar_bounds",
                plane=mover.id,
                detail="test: forced rejection of non-straight-in pose",
            )
        return original(mover, placement, hangar)

    monkeypatch.setattr(tp, "_mover_motion_bounds_conflict", patched)

    slot = _slot("A", x=10.0, y=15.0)
    goal = Pose.from_placement(slot)
    # Should succeed — the fallback straight-in pose always survives.
    arc = plan_path(
        plane,
        entry_pose(slot, h),  # single-pose backward-compat baseline
        goal,
        hangar=h,
        placed=placed,
        mover_on_carts=False,
        entries=entry_poses(slot, h),
    )
    # At least one non-straight-in candidate was tested (and rejected) by the filter.
    assert len(rejected) > 0
    # The arc is still valid.
    from hangarfit.towplanner import path_first_conflict

    assert path_first_conflict(arc, plane, mover_on_carts=False, placed=placed) is None


def test_plan_path_fallback_when_all_filtered(monkeypatch: pytest.MonkeyPatch) -> None:
    """If all cone candidates are filtered, plan_path falls back to the straight-in
    centre pose and still succeeds (does not raise prematurely)."""
    h = _hangar()
    plane = _box_plane("A")
    placed = Layout(fleet={"A": plane}, hangar=h, placements=())

    # Patch to reject every candidate except the straight-in centre.
    door_center = h.door.center_x_m
    original = tp._mover_motion_bounds_conflict

    def patched(mover: Aircraft, placement: Placement, hangar: Hangar):  # noqa: ANN202
        pose = Pose(placement.x_m, placement.y_m, placement.heading_deg)
        is_centre_straight = abs(pose.x_m - door_center) < 1e-9 and pose.heading_deg == 0.0
        if not is_centre_straight:
            from hangarfit.models import Conflict

            return Conflict.single(
                kind="hangar_bounds",
                plane=mover.id,
                detail="test: forced rejection of non-centre-straight pose",
            )
        return original(mover, placement, hangar)

    monkeypatch.setattr(tp, "_mover_motion_bounds_conflict", patched)

    slot = _slot("A", x=10.0, y=15.0)
    goal = Pose.from_placement(slot)
    # Must succeed: fallback straight-in centre is always kept.
    arc = plan_path(
        plane,
        entry_pose(slot, h),
        goal,
        hangar=h,
        placed=placed,
        mover_on_carts=False,
        entries=entry_poses(slot, h),
    )
    assert arc is not None


# ---------------------------------------------------------------------------
# Block 3: Multi-start plan_path
# ---------------------------------------------------------------------------


def test_plan_path_accepts_entries_kwarg() -> None:
    """plan_path with entries= keyword succeeds and returns a valid DubinsArc."""
    h = _hangar()
    plane = _box_plane("A")
    placed = Layout(fleet={"A": plane}, hangar=h, placements=())
    slot = _slot("A", x=10.0, y=15.0)
    goal = Pose.from_placement(slot)
    cone = entry_poses(slot, h)

    from hangarfit.towplanner import DubinsArc

    arc = plan_path(
        plane,
        cone[0],
        goal,
        hangar=h,
        placed=placed,
        mover_on_carts=False,
        entries=cone,
    )
    assert isinstance(arc, DubinsArc)


def test_plan_path_multi_start_is_deterministic() -> None:
    """Same entries → identical arc segments and length."""
    h = _hangar(width_m=14.0, length_m=20.0)
    plane = _wide_plane("A", span_m=4.0, turn_radius_m=2.0)
    placed = Layout(fleet={"A": plane}, hangar=h, placements=())
    slot = _slot("A", x=10.0, y=8.0, h=45.0)
    goal = Pose.from_placement(slot)
    cone = entry_poses(slot, h)
    entry = cone[0]

    a = plan_path(plane, entry, goal, hangar=h, placed=placed, mover_on_carts=False, entries=cone)
    b = plan_path(plane, entry, goal, hangar=h, placed=placed, mover_on_carts=False, entries=cone)
    assert a.segments == b.segments
    assert a.length_m == pytest.approx(b.length_m)


def test_plan_path_multi_start_arc_is_exact_oracle_clean() -> None:
    """The arc returned from a multi-start search passes the exact oracle."""
    from hangarfit.towplanner import path_first_conflict

    h = _hangar(width_m=14.0, length_m=20.0)
    plane = _wide_plane("A", span_m=4.0, turn_radius_m=2.0)
    placed = Layout(fleet={"A": plane}, hangar=h, placements=())
    slot = _slot("A", x=10.0, y=8.0, h=45.0)
    goal = Pose.from_placement(slot)
    cone = entry_poses(slot, h)

    arc = plan_path(
        plane, cone[0], goal, hangar=h, placed=placed, mover_on_carts=False, entries=cone
    )
    assert path_first_conflict(arc, plane, mover_on_carts=False, placed=placed) is None


def test_plan_path_single_entry_tuple_same_as_no_entries() -> None:
    """Passing a single-element entries tuple yields the same result as no entries."""
    h = _hangar(width_m=14.0, length_m=20.0)
    plane = _box_plane("A", turn_radius_m=3.0)
    placed = Layout(fleet={"A": plane}, hangar=h, placements=())
    entry = Pose(7.0, 0.0, 0.0)
    goal = Pose(7.0, 12.0, 0.0)

    arc_single = plan_path(plane, entry, goal, hangar=h, placed=placed, mover_on_carts=False)
    arc_entries = plan_path(
        plane, entry, goal, hangar=h, placed=placed, mover_on_carts=False, entries=(entry,)
    )
    assert arc_single.segments == arc_entries.segments
    assert arc_single.length_m == pytest.approx(arc_entries.length_m)


# ---------------------------------------------------------------------------
# Block 4: Path-length regression — cone beats single-start for off-to-side slot
# ---------------------------------------------------------------------------


def test_cone_produces_shorter_path_for_off_side_slot() -> None:
    """An angled slot off to the side of the door yields a shorter path with the
    full cone search than the v1 single straight-in entry pose.

    This is the core regression check: the cone win demonstrates the feature
    actually helps. We use a slot at heading=30° near the left side of a wide hangar
    so the straight-in entry at x=door_center forces a large heading correction,
    while a 30°-heading cone entry from further left can close nearly straight.
    """
    from hangarfit.towplanner import path_first_conflict

    # Wide hangar, door in the centre.
    h = Hangar(
        length_m=30.0,
        width_m=30.0,
        door=Door(center_x_m=15.0, width_m=10.0),
        maintenance_bay=MaintenanceBay(center_x_m=15.0, width_m=2.0, depth_m=2.0),
        clearance_m=0.3,
        wing_layer_clearance_m=0.2,
    )
    plane = _box_plane("A", turn_radius_m=3.0)
    placed = Layout(fleet={"A": plane}, hangar=h, placements=())

    # Slot at x=8 (well left of centre), y=10, heading=30°. The straight-in
    # entry at door_center (15, 0, heading=0) needs a big course correction;
    # the 30°-entry from (10, 0) aims more directly at the goal.
    slot = _slot("A", x=8.0, y=10.0, h=30.0)
    goal = Pose.from_placement(slot)

    # v1 baseline: single straight-in entry (the old entry_pose).
    v1_entry = entry_pose(slot, h)
    arc_v1 = plan_path(plane, v1_entry, goal, hangar=h, placed=placed, mover_on_carts=False)
    len_v1 = arc_v1.length_m

    # Cone search: multiple entry poses, including angled ones.
    cone = entry_poses(slot, h)
    arc_cone = plan_path(
        plane,
        v1_entry,  # still needed as the positional arg for backward compat
        goal,
        hangar=h,
        placed=placed,
        mover_on_carts=False,
        entries=cone,
    )
    len_cone = arc_cone.length_m

    # The cone must find a shorter or equal path.
    assert len_cone <= len_v1 + 1e-9, (
        f"Cone path ({len_cone:.3f} m) should be ≤ v1 path ({len_v1:.3f} m)"
    )
    # And for this particular geometry it should actually be strictly shorter.
    assert len_cone < len_v1, (
        f"Expected cone ({len_cone:.3f} m) < v1 ({len_v1:.3f} m) for off-side slot"
    )
    # Both must be exact-oracle clean.
    assert path_first_conflict(arc_v1, plane, mover_on_carts=False, placed=placed) is None
    assert path_first_conflict(arc_cone, plane, mover_on_carts=False, placed=placed) is None
