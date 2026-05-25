"""Empty-hangar fill planner (#196): door-cone entry pose + plan_fill.

The entry-pose tests pin the door as a motion gate (spike Q6 / ADR-0007);
the plan_fill tests pin the deterministic back-first order, the cart
pivot-straight-pivot path, the bounded order-retry swap, and the structured
bail. Retry/bail tests monkeypatch ``path_first_conflict`` so the loop logic
is exercised independently of Dubins geometry.
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
)
from hangarfit.towplanner import MovesPlan, NoFeasiblePlanError, Pose, entry_pose, plan_fill


def _fuselage_box() -> Part:
    """A 1.0 m × 0.6 m fuselage box mounted forward of the plane origin, so a
    placement / entry at the front wall (y = 0) keeps every world vertex at
    y >= 0 (mirrors the test_towplanner_motion.py fixture)."""
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


def test_plan_fill_swaps_past_a_conflicting_plane(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the deepest plane (B, scanned first) to conflict while A is not yet
    # placed; the loop must skip to A, then find B feasible. Monkeypatching
    # path_first_conflict isolates the retry logic from Dubins geometry.
    h = _hangar(width_m=20.0, length_m=30.0)
    fleet = {"A": _box_plane("A"), "B": _box_plane("B")}
    target = _layout(fleet, h, _slot("A", 8.0, 8.0), _slot("B", 12.0, 22.0))

    def fake_conflict(arc, mover, *, mover_on_carts, placed, **kw):  # noqa: ANN001, ANN202
        placed_ids = {p.plane_id for p in placed.placements}
        if mover.id == "B" and "A" not in placed_ids:
            return Conflict.single(kind="fuselage_fuselage_overlap", plane="B", detail="forced")
        return None

    monkeypatch.setattr(tp, "path_first_conflict", fake_conflict)
    plan = plan_fill(target)
    assert [m.plane_id for m in plan.moves] == ["A", "B"]


def test_plan_fill_bails_with_structured_error_when_unplannable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = _hangar(width_m=20.0, length_m=30.0)
    fleet = {"A": _box_plane("A"), "B": _box_plane("B")}
    target = _layout(fleet, h, _slot("A", 8.0, 8.0), _slot("B", 12.0, 22.0))

    def always_conflict(arc, mover, *, mover_on_carts, placed, **kw):  # noqa: ANN001, ANN202
        return Conflict.single(kind="fuselage_fuselage_overlap", plane=mover.id, detail="forced")

    monkeypatch.setattr(tp, "path_first_conflict", always_conflict)
    with pytest.raises(NoFeasiblePlanError) as ei:
        plan_fill(target)
    assert ei.value.plane_id in {"A", "B"}
    assert ei.value.conflict is not None


def test_plan_fill_succeeds_when_only_last_scanned_is_feasible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression for the PR #220 lifetime swap-budget false-bail. Six planes,
    # all at the same slot so back_first_order ties resolve to plane_id asc
    # (scan order A..F). Each iteration only the alphabetically-LAST not-yet-
    # placed plane is feasible, forcing maximal rejections (5+4+3+2+1 = 15). A
    # lifetime budget of 2*n = 12 would bail on this fully-plannable target;
    # plan_fill must place all six (it makes monotonic progress, no budget).
    h = _hangar(width_m=20.0, length_m=30.0)
    ids = ["A", "B", "C", "D", "E", "F"]
    fleet = {pid: _box_plane(pid) for pid in ids}
    target = _layout(fleet, h, *[_slot(pid, 10.0, 15.0) for pid in ids])

    def fake_conflict(arc, mover, *, mover_on_carts, placed, **kw):  # noqa: ANN001, ANN202
        remaining = set(ids) - {p.plane_id for p in placed.placements}
        if mover.id == max(remaining):  # only the last-scanned remaining is OK
            return None
        return Conflict.single(kind="fuselage_fuselage_overlap", plane=mover.id, detail="forced")

    monkeypatch.setattr(tp, "path_first_conflict", fake_conflict)
    plan = plan_fill(target)
    assert [m.plane_id for m in plan.moves] == ["F", "E", "D", "C", "B", "A"]
