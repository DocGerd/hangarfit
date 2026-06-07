"""Door-entry cone (#262): entry_poses grid, candidate filtering, multi-start plan_path.

Tests are grouped into four blocks:
1. Cone-grid contents + determinism: ``entry_poses`` emits the right set of poses in a
   fixed order and the same input always yields the same sequence.
2. Candidate filtering: poses that clip side/back walls are dropped before the search;
   if all are filtered the fallback straight-in centre pose is returned.
3. Multi-start plan_path: the search accepts the cone and seeds all surviving entries.
4. Door-gate (#411) + legal strict wins (#420, #431): for an off-to-the-side slot
   the cone does NOT corner-cut through the solid front wall beside the door (its
   best legal path equals the v1 baseline there; the prior sub-cm "win" was a jamb
   wall-clip). But the cone DOES earn a strictly shorter LEGAL path than v1 in two
   orthogonal regimes: on a STEEPLY-angled OPEN-SPACE off-side slot, its angled
   door entry beats the straight-in (#420); and on an OBSTACLE-FORCED DETOUR, where
   a placed plane sitting broadside on the v1 straight-in lane forces v1 the long
   way around while an offset cone entry threads the shorter side (#431). Both
   guard the cone's path-length keep post-#411.
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
    Wheels,
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
                kind="fuselage_aft",
                length_m=1.0,
                width_m=0.6,
                offset_x_m=0.5,
                offset_y_m=0.0,
                angle_deg=0.0,
                z_bottom_m=0.0,
                z_top_m=1.0,
            ),
        ),
        wheels=Wheels(main_offset_x_m=0.0, track_m=1.8, third_wheel_offset_x_m=-2.0),
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
                kind="fuselage_aft",
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
        wheels=Wheels(main_offset_x_m=0.0, track_m=1.8, third_wheel_offset_x_m=-2.0),
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


def test_off_side_slot_cone_does_not_corner_cut_through_jamb() -> None:
    """Door-gate regression (#411): for an off-side slot, the only route shorter
    than the straight-in v1 entry would cut the corner THROUGH the solid front
    wall beside the door (the box dipping to ``y < 0`` just left of the door
    edge). The door-aware front-gap exemption forbids that, so the cone must NOT
    take the shortcut — its best legal path EQUALS the v1 path, and neither clips
    the front wall.

    Pre-#411 the cone was ~6 mm shorter here (10.422 vs 10.428 m) via exactly
    that jamb corner-cut — a path that clipped the wall. This now guards that the
    planner does not corner-cut through the jamb even when shorter; reverting the
    door-gate would make the cone beat v1 again and fail this test. (The #262
    cone still provides valid multi-start search + determinism, guarded by the
    other tests in this module; that it ALSO yields a *legal* strict path win on a
    steeply-angled off-side slot is now guarded by
    ``test_steeply_angled_off_side_slot_cone_yields_legal_strict_win`` — #420.)
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

    # The cone must NOT beat v1 by corner-cutting through the door jamb: with the
    # #411 door-gate the only shorter route is illegal, so the best legal cone
    # path EQUALS the v1 path (a door-gate revert would make it shorter again and
    # fail here).
    assert len_cone == pytest.approx(len_v1, abs=1e-9), (
        f"Cone path ({len_cone:.3f} m) should equal the v1 path ({len_v1:.3f} m) — "
        "the only shorter route corner-cuts through the door jamb, which #411 forbids"
    )
    # Both must be exact-oracle clean — which, post-#411, also means neither
    # protrudes to y < 0 beside the door (the door-gate lives in path_first_conflict).
    assert path_first_conflict(arc_v1, plane, mover_on_carts=False, placed=placed) is None
    assert path_first_conflict(arc_cone, plane, mover_on_carts=False, placed=placed) is None


def test_steeply_angled_off_side_slot_cone_yields_legal_strict_win() -> None:
    """#420 strict-win guard: on a steeply-angled, off-to-the-side slot the
    entry-cone's *angled* door entry produces a strictly shorter LEGAL path than
    the v1 straight-in — restoring the intentional path-length guard #411 retired.

    After #411 made the front-gap exemption door-aware, the only test that proved
    the cone beat v1 (``test_off_side_slot_cone_does_not_corner_cut_through_jamb``)
    lost its win — that win had been an illegal jamb corner-cut. #420 reassessed
    whether the cone still earns its path-length keep. It does: a broad geometry
    sweep found many *legal* wins on steeply-turned (90°/135°) off-side slots (the
    cone also wins on obstacle-forced detours). This fixture pins one.

    The slot is at (21, 10) heading 135° — far to the right of the door interval
    [6, 18], facing back across the hangar. v1 clamps its straight-in entry to the
    right door edge (x=18, heading 0°). The cone additionally fans angled entries
    at the door; the −30° (330°) entry at the door edge pre-aligns the nose so the
    search needs far less corrective turning at the door (the v1 straight-in opens
    with a ~3.1 m turn arc; the angled entry ~0.18 m), reaching the goal via a
    ~0.18 m shorter total arc whose swept footprint stays within bounds and the
    door opening (legal under the #411 gate). The win is deterministic — closed-form
    Reeds–Shepp (ADR-0010) + the RNG-free search (ADR-0003) — so it is a stable
    guard: dropping the cone (``entries=None``) makes the cone path equal v1 again
    and fails this test.

    Two notes for a future maintainer:

    - **This is a point-wise win, not a universal one.** The bounded multi-start
      Hybrid-A* returns the first goal-reaching start under its expansion budget /
      heuristic ordering, NOT the global minimum across the cone's seeds — so off
      this fixture the cone can even be *longer* than v1 despite the cone fan
      containing the v1 pose. Do NOT add a "the cone is never worse than v1" test;
      it would be wrong.
    - **The fixture sits inside a fairly narrow win region** (sensitive to
      ``turn_radius_m`` and the slot's x / y / heading). The ~0.18 m margin is
      ~3.6× the 0.05 m threshold here, so the test is stable — but if it breaks
      after a geometry or ``_box_plane`` turn-radius change, re-derive a winning
      fixture; don't just lower the threshold.
    """
    from hangarfit.towplanner import path_first_conflict

    h = _hangar(width_m=24.0, length_m=26.0, door_center=12.0, door_width=12.0)
    plane = _box_plane("A", turn_radius_m=4.0)
    placed = Layout(fleet={"A": plane}, hangar=h, placements=())

    slot = _slot("A", x=21.0, y=10.0, h=135.0)
    goal = Pose.from_placement(slot)

    # v1 baseline: the single straight-in entry, clamped to the right door edge.
    v1_entry = entry_pose(slot, h)
    arc_v1 = plan_path(plane, v1_entry, goal, hangar=h, placed=placed, mover_on_carts=False)

    # Cone: the multi-start fan (includes v1's straight-in among its 15 poses).
    cone = entry_poses(slot, h)
    arc_cone = plan_path(
        plane, v1_entry, goal, hangar=h, placed=placed, mover_on_carts=False, entries=cone
    )
    arc_cone_again = plan_path(
        plane, v1_entry, goal, hangar=h, placed=placed, mover_on_carts=False, entries=cone
    )

    # Strict legal win: the cone is meaningfully shorter than the v1 straight-in
    # (measured margin ~0.18 m; the threshold leaves float headroom while proving
    # it is a real win, not a tie).
    assert arc_cone.length_m < arc_v1.length_m - 0.05, (
        f"cone path {arc_cone.length_m:.4f} m should strictly beat v1 "
        f"{arc_v1.length_m:.4f} m on this steeply-angled off-side slot"
    )
    # The winning start is an ANGLED cone entry, not the heading-0 straight-in —
    # i.e. the cone's fan is what delivers the win, not the v1 pose it also holds.
    assert arc_cone.start.heading_deg != 0.0
    # Both paths are exact-oracle clean (incl. the #411 door-gate: no y<0
    # protrusion outside the door opening).
    assert path_first_conflict(arc_v1, plane, mover_on_carts=False, placed=placed) is None
    assert path_first_conflict(arc_cone, plane, mover_on_carts=False, placed=placed) is None
    # Deterministic (ADR-0003): the win is stable, not a float-tie artifact.
    assert arc_cone.segments == arc_cone_again.segments
    assert arc_cone.length_m == pytest.approx(arc_cone_again.length_m)


def test_obstacle_detour_off_side_cone_yields_legal_strict_win() -> None:
    """#431 strict-win guard (orthogonal to #420): on an OBSTACLE-FORCED DETOUR
    the entry-cone's *offset* door entry produces a strictly shorter LEGAL path
    than the v1 straight-in. Where #420 pins the cone's open-space win on a
    steeply-angled off-side slot, this pins its win when a placed plane blocks the
    v1 lane.

    A plane ``B`` is parked broadside (heading 90°) across the v1 straight-in lane
    between the door and the deep slot. v1 clamps its single entry to ``x = 10`` (the
    target x, inside the door interval [6, 18]) and shoots straight in — but B's
    broadside **fuselage** (z 0–1.4 m, ~1 m across the lane after the 90° rotation)
    sits squarely on that lane, so v1 must swing the long way around it. (B is a
    high-wing plane, but its wing at z 1.4–1.8 m is *inert* here: the mover's
    fuselage tops out at z 1.0 m, leaving a 0.4 m layer gap ≥ the 0.2 m
    ``wing_layer_clearance_m``, so the mover tows clean *under* B's wing — the detour
    is driven by B's fuselage, not its span.) The cone additionally fans entries at
    three door x-samples; an offset door entry (here the door-centre ``x = 12``,
    heading 0°) starts the search one lane over, off the fuselage, and threads a
    shorter total arc whose swept footprint stays clear of B, the walls and the door
    opening (legal under the #411 gate). The winning start is therefore an *offset*
    cone pose, NOT v1's clamped-x straight-in — i.e. the cone's fan delivers the win.

    Chosen config (hangar 24×26, door centre 12 width 12, ``turn_radius_m`` 4):
    slot A at (10, 20) heading 0°; obstacle B at (10, 10) heading 90° (its broadside
    fuselage, not its 6 m wing span, is the blocker — see above).
    Measured ``len_v1`` ≈ 20.530 m, ``len_cone`` ≈ 20.178 m → margin ≈ 0.352 m,
    ~7× the 0.05 m threshold. The win survives ±0.25 m obstacle jitter in every
    cell of the 3×3 perturbation grid (worst-case margin ≈ 0.12 m, all legal), so
    it is not a knife-edge. Deterministic — closed-form Reeds–Shepp (ADR-0010) +
    the RNG-free search (ADR-0003) — so dropping the cone (``entries=None``) makes
    the cone path equal v1 again and fails this test.

    Two notes for a future maintainer:

    - **This is a point-wise win, not a universal one.** The bounded multi-start
      Hybrid-A* returns the first goal-reaching start under its expansion budget /
      heuristic ordering, NOT the global minimum across the cone's seeds — so off
      this fixture the cone can even be *longer* than v1 despite the cone fan
      containing the v1 pose. Do NOT add a "the cone is never worse than v1" test;
      it would be wrong.
    - **The win is tied to B's fuselage blocking the v1 lane.** Slide B off the
      lane and v1 no longer detours, so the win shrinks (that is the point — it is
      an obstacle-detour win). The blocker is B's broadside *fuselage* (its z-range
      0–1.4 m overlaps the mover's 0–1.0 m); B's high wing is inert, so changing
      ``_wide_plane``'s ``span_m`` will NOT move the margin. If this breaks after a
      geometry / ``_box_plane`` / ``_wide_plane`` change, re-derive by re-sweeping
      the slot + obstacle *placement* (or the fuselage z-range), not the span, for a
      robust margin; do NOT just lower the threshold.
    """
    from hangarfit.towplanner import path_first_conflict

    h = _hangar(width_m=24.0, length_m=26.0, door_center=12.0, door_width=12.0)
    mover = _box_plane("A", turn_radius_m=4.0)
    obstacle = _wide_plane("B", span_m=6.0, turn_radius_m=3.0)

    # Obstacle B parked broadside across the v1 straight-in lane.
    placed = Layout(
        fleet={"A": mover, "B": obstacle},
        hangar=h,
        placements=(_slot("B", x=10.0, y=10.0, h=90.0),),
    )

    slot = _slot("A", x=10.0, y=20.0, h=0.0)
    goal = Pose.from_placement(slot)

    # v1 baseline: the single straight-in entry, clamped to the target x.
    v1_entry = entry_pose(slot, h)
    arc_v1 = plan_path(mover, v1_entry, goal, hangar=h, placed=placed, mover_on_carts=False)

    # Premise anchor (#431): confirm the obstacle genuinely FORCES the v1 detour.
    # Without B on the lane v1 shoots straight in; B adds ~0.53 m. This makes the
    # guard fail loud if a future geometry change stops B blocking the lane, rather
    # than silently degrading into a duplicate of the #420 open-space case.
    placed_clear = Layout(fleet={"A": mover, "B": obstacle}, hangar=h, placements=())
    arc_v1_clear = plan_path(
        mover, v1_entry, goal, hangar=h, placed=placed_clear, mover_on_carts=False
    )
    assert arc_v1.length_m > arc_v1_clear.length_m + 0.1, (
        f"obstacle B must force a v1 detour: with-B v1 {arc_v1.length_m:.4f} m should "
        f"exceed the no-obstacle v1 {arc_v1_clear.length_m:.4f} m"
    )

    # Cone: the multi-start fan (includes v1's straight-in among its poses).
    cone = entry_poses(slot, h)
    arc_cone = plan_path(
        mover, v1_entry, goal, hangar=h, placed=placed, mover_on_carts=False, entries=cone
    )
    arc_cone_again = plan_path(
        mover, v1_entry, goal, hangar=h, placed=placed, mover_on_carts=False, entries=cone
    )

    # Strict legal win: the cone is meaningfully shorter than the v1 straight-in
    # (measured margin ~0.35 m; the threshold leaves float headroom while proving
    # it is a real win, not a tie).
    assert arc_cone.length_m < arc_v1.length_m - 0.05, (
        f"cone path {arc_cone.length_m:.4f} m should strictly beat v1 "
        f"{arc_v1.length_m:.4f} m on this obstacle-detour case"
    )
    # The winning start is an OFFSET cone entry, not v1's clamped-x straight-in —
    # i.e. the cone's fan (here the door-centre x=12 sample) delivers the win, not
    # the v1 pose it also holds. We assert only "offset != v1" (not the exact winning
    # sample) to stay robust to which fan pose wins; exact float `!=` is safe on these
    # door-geometry literals (won x=12.0 vs v1 x=10.0).
    won = arc_cone.start
    assert won.x_m != v1_entry.x_m or won.heading_deg != v1_entry.heading_deg, (
        f"winning cone start ({won.x_m:.3f}, {won.heading_deg:.1f}) should differ "
        f"from the v1 entry ({v1_entry.x_m:.3f}, {v1_entry.heading_deg:.1f})"
    )
    # Both paths are exact-oracle clean: neither clips obstacle B, the walls, or
    # protrudes y<0 outside the door opening (#411 door-gate).
    assert path_first_conflict(arc_v1, mover, mover_on_carts=False, placed=placed) is None
    assert path_first_conflict(arc_cone, mover, mover_on_carts=False, placed=placed) is None
    # Deterministic (ADR-0003): the win is stable, not a float-tie artifact.
    assert arc_cone.segments == arc_cone_again.segments
    assert arc_cone.length_m == pytest.approx(arc_cone_again.length_m)
