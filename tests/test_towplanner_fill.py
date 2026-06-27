"""Empty-hangar fill planner (#196): door-cone entry pose + plan_fill.

The entry-pose tests pin the door as a motion gate (spike Q6 / ADR-0007);
the plan_fill tests pin the deterministic back-first order, the cart
pivot-straight-pivot path, the bounded order-retry swap, and the structured
bail. Retry/bail tests monkeypatch ``path_first_conflict`` (which plan_fill now
reaches via ``plan_path``'s internal calls) so the loop logic is exercised
independently of the real geometry search.
"""

import pytest

import hangarfit.towplanner as tp
from hangarfit.models import (
    Aircraft,
    Conflict,
    Door,
    Hangar,
    Layout,
    MaintenanceBay,
    Part,
    Placement,
    Wheels,
)
from hangarfit.towplanner import MovesPlan, NoFeasiblePlanError, Pose, entry_pose, plan_fill

_TAIL_WHEELS = Wheels(main_offset_x_m=0.20, track_m=1.8, third_wheel_offset_x_m=-2.0)


def _fuselage_box() -> Part:
    """A 1.0 m × 0.6 m fuselage box mounted forward of the plane origin, so a
    placement / entry at the front wall (y = 0) keeps every world vertex at
    y >= 0 (mirrors the test_towplanner_motion.py fixture)."""
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
    """A cart-borne plane (effective_turn_radius_m() == 0, turn_radius_m None)."""
    return Aircraft(
        id=plane_id,
        name=f"Cart plane {plane_id}",
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_cart",
        turn_radius_m=None,
        measured=False,
        parts=(_fuselage_box(),),
        wheels=_TAIL_WHEELS,
    )


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
        clearance_m=0.5,
        wing_layer_clearance_m=0.3,
    )


def _slot(pid: str, x: float, y: float, h: float = 0.0, on_carts: bool = False) -> Placement:
    return Placement(plane_id=pid, x_m=x, y_m=y, heading_deg=h, on_carts=on_carts)


def test_entry_pose_is_at_front_pointing_in() -> None:
    h = _hangar()
    e = entry_pose(_slot("A", x=10.0, y=20.0), h)
    assert e.y_m == 0.0
    assert e.heading_deg == 0.0  # nose toward +y, into the hangar


def test_entry_x_equals_slot_x_when_inside_door() -> None:
    h = _hangar(door_center=10.0, door_width=6.0)  # door interval [7, 13]
    e = entry_pose(_slot("A", x=9.0, y=20.0), h)
    assert e.x_m == pytest.approx(9.0)


def test_entry_x_clamps_to_door_interval() -> None:
    h = _hangar(door_center=10.0, door_width=6.0)  # door interval [7, 13]
    assert entry_pose(_slot("A", x=2.0, y=20.0), h).x_m == pytest.approx(7.0)
    assert entry_pose(_slot("B", x=18.0, y=20.0), h).x_m == pytest.approx(13.0)


def _layout(fleet: dict[str, Aircraft], hangar: Hangar, *placements: Placement) -> Layout:
    return Layout(fleet=fleet, hangar=hangar, placements=tuple(placements))


def test_plan_fill_orders_deepest_first_and_plans_all() -> None:
    h = _hangar(width_m=20.0, length_m=30.0)
    fleet = {"A": _box_plane("A"), "B": _box_plane("B")}
    target = _layout(
        fleet,
        h,
        _slot("A", x=8.0, y=8.0),  # shallow
        _slot("B", x=12.0, y=22.0),  # deep
    )
    plan = plan_fill(target)
    assert isinstance(plan, MovesPlan)
    assert plan.target_layout is target
    # Deepest (B) is towed first (back_first_order: y desc).
    assert [m.plane_id for m in plan.moves] == ["B", "A"]
    # Every move ends at its target slot pose.
    for m in plan.moves:
        slot = next(p for p in target.placements if p.plane_id == m.plane_id)
        assert m.target_slot == Pose.from_placement(slot)


def test_plan_fill_plans_cart_plane_with_zero_radius_arc() -> None:
    h = _hangar(width_m=20.0, length_m=30.0)
    fleet = {"C": _always_cart_plane("C")}
    target = _layout(fleet, h, Placement("C", x_m=10.0, y_m=14.0, heading_deg=90.0, on_carts=True))
    plan = plan_fill(target)
    (move,) = plan.moves
    assert move.path.turn_radius_m == 0.0  # cart = own-gear with zero radius
    last = move.path.pose_at(move.path.length_m)
    assert last.x_m == pytest.approx(10.0, abs=1e-6)
    assert last.y_m == pytest.approx(14.0, abs=1e-6)


def test_plan_fill_is_deterministic() -> None:
    h = _hangar(width_m=20.0, length_m=30.0)
    fleet = {"A": _box_plane("A"), "B": _box_plane("B")}
    target = _layout(fleet, h, _slot("A", 8.0, 8.0), _slot("B", 12.0, 22.0))
    assert plan_fill(target) == plan_fill(target)


def test_plan_fill_skips_hand_placed_body_and_keeps_it_as_obstacle() -> None:
    """#667 Stage 0: a hand-positioned (dolly) body is pre-placed — NOT tow-routed
    (carried at rest with ``path is None``) while the rest route around it as an
    obstacle. Models the Herrenteich gliders, which go in by hand, not towed."""
    h = _hangar(width_m=20.0, length_m=30.0)
    fleet = {"A": _box_plane("A"), "G": _box_plane("G")}
    target = _layout(
        fleet,
        h,
        _slot("A", x=8.0, y=8.0),  # towed in
        Placement("G", x_m=12.0, y_m=22.0, heading_deg=0.0, on_carts=False, hand_placed=True),
    )
    plan = plan_fill(target)
    moves = {m.plane_id: m for m in plan.moves}
    assert set(moves) == {"A", "G"}  # both present in the plan
    assert moves["G"].path is None  # hand-placed: present at rest, NOT routed
    assert moves["A"].path is not None  # towed in normally
    assert moves["G"].target_slot == Pose.from_placement(
        next(p for p in target.placements if p.plane_id == "G")
    )


def test_plan_fill_inert_with_no_hand_placed_body() -> None:
    """#667 Rung A inert-path guarantee (ADR-0003): a layout with NO hand-placed
    body partitions the whole fleet into the routed set — no body is carried at
    rest — and re-planning is byte-identical. So activating Stage 0 in some
    layouts cannot perturb a layout that does not use it."""
    h = _hangar(width_m=20.0, length_m=30.0)
    fleet = {"A": _box_plane("A"), "B": _box_plane("B")}
    target = _layout(
        fleet,
        h,
        _slot("A", x=8.0, y=8.0),
        _slot("B", x=12.0, y=22.0),
    )
    plan = plan_fill(target)
    # No body is carried at rest: every move is tow-routed (no hand-placed keep-out).
    assert all(m.path is not None for m in plan.moves)
    # Re-planning the identical target yields a byte-identical plan.
    assert plan_fill(target) == plan


# ── plan_fill LOOP-logic tests ──────────────────────────────────────────────
# These pin the back-first scan / swap / bail mechanics in isolation by faking
# ``plan_path`` — the per-candidate call ``plan_fill`` actually makes. (Earlier
# they faked ``path_first_conflict``; since #222 routed plan_fill through the
# Hybrid-A* ``plan_path`` — which screens with the real-geometry ``_motion_clear``
# and only consults ``path_first_conflict`` as a final safety net — faking the
# oracle no longer controls the per-candidate verdict. Faking ``plan_path`` is
# the correct seam: it exercises the loop without any geometry or search budget.
# The real plan_path↔plan_fill integration is covered by
# ``test_plan_fill_routes_origin_spanning_planes``.)


def _fake_arc(mover: Aircraft, entry: "Pose", goal: "Pose") -> object:
    """A real (cheap) Dubins arc for the 'feasible' branch of a faked plan_path."""
    return tp.plan_dubins(entry, goal, turn_radius_m=mover.effective_turn_radius_m())


def _forced_infeasible(plane_id: str) -> NoFeasiblePlanError:
    return NoFeasiblePlanError(
        plane_id, Conflict.single(kind="fuselage_fuselage_overlap", plane=plane_id, detail="forced")
    )


def test_plan_fill_swaps_past_a_conflicting_plane(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the deepest plane (B, scanned first) to be unroutable while A is not
    # yet placed; the loop must skip to A, then route B once A is down.
    h = _hangar(width_m=20.0, length_m=30.0)
    fleet = {"A": _box_plane("A"), "B": _box_plane("B")}
    target = _layout(fleet, h, _slot("A", 8.0, 8.0), _slot("B", 12.0, 22.0))

    def fake_plan_path(mover, entry, goal, *, hangar, placed, mover_on_carts, **kw):  # noqa: ANN001, ANN202
        placed_ids = {p.plane_id for p in placed.placements}
        if mover.id == "B" and "A" not in placed_ids:
            raise _forced_infeasible("B")
        return _fake_arc(mover, entry, goal)

    monkeypatch.setattr(tp, "plan_path", fake_plan_path)
    plan = plan_fill(target)
    assert [m.plane_id for m in plan.moves] == ["A", "B"]


def test_plan_fill_backtracks_order_when_greedy_commit_deadlocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#667 Stage 1 (order search): greedy back-first commits the deepest routable
    plane, but that commit can deadlock a later plane. The planner must BACKTRACK
    the order and find a feasible one ([B, A]) rather than bail.

    Unlike the swap test, here the deepest plane A IS routable first, so the
    per-step skip happily commits it — and only then does B deadlock. Only placing
    B before A works, which the monotonic loop cannot reach without backtracking."""
    h = _hangar(width_m=20.0, length_m=30.0)
    fleet = {"A": _box_plane("A"), "B": _box_plane("B")}
    target = _layout(fleet, h, _slot("A", 12.0, 22.0), _slot("B", 8.0, 8.0))  # A deep, B shallow

    def fake_plan_path(mover, entry, goal, *, hangar, placed, mover_on_carts, **kw):  # noqa: ANN001, ANN202
        placed_ids = {p.plane_id for p in placed.placements}
        # A is always routable; B only while A is not yet placed. Greedy commits A
        # first (deeper, scanned first, feasible) → B deadlocks. Only [B, A] works.
        if mover.id == "B" and "A" in placed_ids:
            raise _forced_infeasible("B")
        return _fake_arc(mover, entry, goal)

    monkeypatch.setattr(tp, "plan_path", fake_plan_path)
    plan = plan_fill(target)
    assert [m.plane_id for m in plan.moves] == ["B", "A"]


def test_plan_fill_backtrack_cap_bounds_the_order_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#667: the order search is bounded by ``max_backtracks`` so an un-routable
    fill bails predictably regardless of the per-plane expansion budget. With zero
    backtracks allowed the deadlock case (which needs one reorder) bails instead of
    finding [B, A]; the generous default still finds it."""
    h = _hangar(width_m=20.0, length_m=30.0)
    fleet = {"A": _box_plane("A"), "B": _box_plane("B")}
    target = _layout(fleet, h, _slot("A", 12.0, 22.0), _slot("B", 8.0, 8.0))

    def fake_plan_path(mover, entry, goal, *, hangar, placed, mover_on_carts, **kw):  # noqa: ANN001, ANN202
        placed_ids = {p.plane_id for p in placed.placements}
        if mover.id == "B" and "A" in placed_ids:
            raise _forced_infeasible("B")
        return _fake_arc(mover, entry, goal)

    monkeypatch.setattr(tp, "plan_path", fake_plan_path)
    assert [m.plane_id for m in plan_fill(target).moves] == ["B", "A"]  # default reorders
    with pytest.raises(NoFeasiblePlanError) as ei:
        plan_fill(target, max_backtracks=0)  # no backtracking allowed → bails
    # The bail must name the actually-stuck body (B — unroutable once A is placed),
    # not the already-placed deepest plane, and preserve a real conflict (#668 review).
    assert ei.value.plane_id == "B"
    assert ei.value.conflict is not None and ei.value.conflict.planes[0] == "B"


def test_plan_fill_bails_with_structured_error_when_unplannable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = _hangar(width_m=20.0, length_m=30.0)
    fleet = {"A": _box_plane("A"), "B": _box_plane("B")}
    target = _layout(fleet, h, _slot("A", 8.0, 8.0), _slot("B", 12.0, 22.0))

    def fake_plan_path(mover, entry, goal, *, hangar, placed, mover_on_carts, **kw):  # noqa: ANN001, ANN202
        raise _forced_infeasible(mover.id)

    monkeypatch.setattr(tp, "plan_path", fake_plan_path)
    with pytest.raises(NoFeasiblePlanError) as ei:
        plan_fill(target)
    # The bail names the DEEPEST unplaceable plane (ordered[0]); B is at y=22, A
    # at y=8, so back_first_order scans B first and the bail pins B.
    assert ei.value.plane_id == "B"
    assert ei.value.conflict is not None


def test_plan_fill_succeeds_when_only_last_scanned_is_feasible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression for the PR #220 lifetime swap-budget false-bail. Six planes;
    # back_first_order ties resolve to plane_id asc (scan order A..F). Each
    # iteration only the alphabetically-LAST not-yet-placed plane is routable,
    # forcing maximal rejections (5+4+3+2+1 = 15). A lifetime budget of 2*n = 12
    # would bail on this fully-plannable target; plan_fill must place all six (it
    # makes monotonic progress, no budget).
    h = _hangar(width_m=20.0, length_m=30.0)
    ids = ["A", "B", "C", "D", "E", "F"]
    fleet = {pid: _box_plane(pid) for pid in ids}
    target = _layout(fleet, h, *[_slot(pid, 10.0, 15.0) for pid in ids])

    def fake_plan_path(mover, entry, goal, *, hangar, placed, mover_on_carts, **kw):  # noqa: ANN001, ANN202
        remaining = set(ids) - {p.plane_id for p in placed.placements}
        if mover.id == max(remaining):  # only the last-scanned remaining is routable
            return _fake_arc(mover, entry, goal)
        raise _forced_infeasible(mover.id)

    monkeypatch.setattr(tp, "plan_path", fake_plan_path)
    plan = plan_fill(target)
    assert [m.plane_id for m in plan.moves] == ["F", "E", "D", "C", "B", "A"]


def test_plan_fill_routes_origin_spanning_planes() -> None:
    """plan_fill must succeed for origin-spanning planes whose Dubins arc clips bounds.

    The forward-mounted-box fixture (offset_x_m=0.5) keeps all vertices at y>=0
    from the entry pose, masking out-of-bounds sweeps. An origin-spanning fuselage
    (offset_x_m=0.0) means the rear half extends behind the nose pose. When the
    target slot is at heading=90 (pointing west) near the left wall, the shortest
    Dubins arc swings the fuselage outside the left hangar wall — single-shot
    plan_dubins raises a bounds conflict, but Hybrid-A* (plan_path) finds an
    in-bounds detour. This test is the primary integration regression guard for
    the plan_fill → plan_path wiring: it FAILS with the old plan_dubins-only code
    (NoFeasiblePlanError) and PASSES after the plan_path integration.
    """
    from hangarfit.towplanner import path_first_conflict

    def span_plane(pid: str) -> Aircraft:
        """Origin-spanning plane: fuselage centred at the nose pose (offset_x_m=0).

        Defined locally rather than reusing ``_box_plane``/``_fuselage_box``: the
        zero x-offset (vs their 0.5) is the whole point — it makes the fuselage
        straddle the entry pose, the case the forward-mounted fixtures mask.
        """
        return Aircraft(
            id=pid,
            name=pid,
            wing_position="high",
            gear="tailwheel",
            movement_mode="always_own_gear",
            turn_radius_m=5.0,
            measured=False,
            parts=(
                Part(
                    kind="fuselage_aft",
                    length_m=2.0,
                    width_m=1.5,
                    offset_x_m=0.0,
                    offset_y_m=0.0,
                    angle_deg=0.0,
                    z_bottom_m=0.0,
                    z_top_m=1.0,
                ),
            ),
            wheels=_TAIL_WHEELS,
        )

    # 30 × 30 m hangar so both slots are well inside the back wall.
    h = Hangar(
        length_m=30.0,
        width_m=30.0,
        door=Door(center_x_m=15.0, width_m=8.0),
        maintenance_bay=MaintenanceBay(center_x_m=15.0, width_m=2.0, depth_m=2.0),
        clearance_m=0.3,
        wing_layer_clearance_m=0.3,
    )
    fleet = {"A": span_plane("A"), "B": span_plane("B")}
    # A: heading=90 (west), y=10 — shallower; single-shot Dubins clips the left wall.
    # B: heading=0 (north), y=20 — deeper; committed first by back_first_order, no bounds issue.
    target = Layout(
        fleet=fleet,
        hangar=h,
        placements=(
            Placement(plane_id="A", x_m=8.0, y_m=10.0, heading_deg=90.0, on_carts=False),
            Placement(plane_id="B", x_m=15.0, y_m=20.0, heading_deg=0.0, on_carts=False),
        ),
    )
    plan = plan_fill(target)
    assert {m.plane_id for m in plan.moves} == {"A", "B"}
    # Deepest (B, y=20) is towed first.
    assert plan.moves[0].plane_id == "B"
    # Every move's path is exact-oracle clean against the planes placed before it.
    placed: list = []
    for m in plan.moves:
        pl = Layout(
            fleet=fleet,
            hangar=h,
            placements=tuple(placed),
            maintenance_plane=target.maintenance_plane,
        )
        slot = next(p for p in target.placements if p.plane_id == m.plane_id)
        assert (
            path_first_conflict(m.path, fleet[m.plane_id], mover_on_carts=slot.on_carts, placed=pl)
            is None
        )
        placed.append(slot)


def test_plane_wider_than_door_is_untowable() -> None:
    """#411: a plane whose wing span exceeds the door width cannot pass through
    the door at any admissible entry orientation, so plan_fill reports it
    un-towable (raises NoFeasiblePlanError naming it) rather than routing a path
    that clips the solid front wall beside the door. This is the
    "no orientation fits -> un-towable" half of the door-gate fix.
    """

    def wide_wing_plane(pid: str) -> Aircraft:
        # 16 m wing span vs a 12 m door: no heading gets the wing through the gap.
        return Aircraft(
            id=pid,
            name=pid,
            wing_position="high",
            gear="tailwheel",
            movement_mode="always_own_gear",
            turn_radius_m=5.0,
            measured=False,
            parts=(
                Part(
                    kind="wing",
                    length_m=1.5,
                    width_m=16.0,
                    offset_x_m=0.0,
                    offset_y_m=0.0,
                    angle_deg=0.0,
                    z_bottom_m=1.8,
                    z_top_m=2.1,
                ),
                Part(
                    kind="fuselage_aft",
                    length_m=4.0,
                    width_m=1.0,
                    offset_x_m=0.0,
                    offset_y_m=0.0,
                    angle_deg=0.0,
                    z_bottom_m=0.0,
                    z_top_m=1.2,
                ),
            ),
            wheels=_TAIL_WHEELS,
        )

    h = Hangar(
        length_m=30.0,
        width_m=30.0,
        door=Door(center_x_m=15.0, width_m=12.0),
        maintenance_bay=MaintenanceBay(center_x_m=15.0, width_m=2.0, depth_m=2.0),
        clearance_m=0.3,
        wing_layer_clearance_m=0.3,
    )
    fleet = {"W": wide_wing_plane("W")}
    target = Layout(
        fleet=fleet,
        hangar=h,
        placements=(Placement(plane_id="W", x_m=15.0, y_m=15.0, heading_deg=0.0, on_carts=False),),
    )
    with pytest.raises(NoFeasiblePlanError) as ei:
        plan_fill(target)
    assert ei.value.plane_id == "W"


def test_off_centre_plane_within_door_still_routes() -> None:
    """#411 (no over-fire): the door-gate must NOT block a plane whose y<0 entry
    protrusion stays WITHIN the door opening. A narrow origin-spanning plane
    parked off-centre — but inside a wide door — tows in cleanly. The gate only
    rejects protrusions BESIDE the door, not through it; this is the positive
    mirror of test_plane_wider_than_door_is_untowable.
    """
    from hangarfit.towplanner import path_first_conflict

    def span_plane(pid: str) -> Aircraft:
        return Aircraft(
            id=pid,
            name=pid,
            wing_position="high",
            gear="tailwheel",
            movement_mode="always_own_gear",
            turn_radius_m=4.0,
            measured=False,
            parts=(
                Part(
                    kind="fuselage_aft",
                    length_m=3.0,
                    width_m=0.6,
                    offset_x_m=0.0,
                    offset_y_m=0.0,
                    angle_deg=0.0,
                    z_bottom_m=0.0,
                    z_top_m=1.0,
                ),
            ),
            wheels=_TAIL_WHEELS,
        )

    # Wide door [4, 16]; the narrow plane parks off-centre at x = 6, well inside
    # it, so its y<0 entry protrusion (x ~ [5.7, 6.3]) passes through the doorway.
    h = _hangar(width_m=20.0, length_m=30.0, door_center=10.0, door_width=12.0)
    fleet = {"P": span_plane("P")}
    target = _layout(fleet, h, Placement("P", x_m=6.0, y_m=12.0, heading_deg=0.0, on_carts=False))
    plan = plan_fill(target)
    (move,) = plan.moves
    assert move.plane_id == "P"
    # The routed path is oracle-clean: no front-wall-beside-door clip anywhere.
    empty = Layout(fleet=fleet, hangar=h, placements=())
    assert path_first_conflict(move.path, fleet["P"], mover_on_carts=False, placed=empty) is None
