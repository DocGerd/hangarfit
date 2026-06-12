"""Tow-planner ground-object wiring (#601).

Fixed obstacles join the planner's static keep-out set (``_build_obstacles``);
placed-routed movers are enumerated in ``plan_fill`` as bodies the planner must
route, though the actual route search is deferred to #602 (their move carries a
``None`` / deferred path). The empty-ground-object case stays byte-identical
(determinism-guard, ADR-0003) — covered by tests/test_towplanner.py.
"""

from __future__ import annotations

import pytest

from hangarfit.models import Door, GroundObject, Hangar, Layout, MaintenanceBay, Part, Placement
from hangarfit.towplanner import _build_obstacles, plan_fill
from tests.conftest import make_test_aircraft


def _hangar(clearance: float = 0.3, wlc: float = 0.2) -> Hangar:
    """Inline minimal hangar (copied from tests/test_collisions.py:_hangar)."""
    return Hangar(
        length_m=40.0,
        width_m=40.0,
        door=Door(center_x_m=20.0, width_m=12.0),
        maintenance_bay=MaintenanceBay(center_x_m=20.0, width_m=8.0, depth_m=6.0),
        clearance_m=clearance,
        wing_layer_clearance_m=wlc,
    )


def _ground_part(length_m: float = 3.0, width_m: float = 13.0, z_top_m: float = 3.0) -> Part:
    return Part(
        kind="ground",
        length_m=length_m,
        width_m=width_m,
        offset_x_m=0.0,
        offset_y_m=0.0,
        angle_deg=0.0,
        z_bottom_m=0.0,
        z_top_m=z_top_m,
    )


def test_fixed_obstacle_in_static_obstacle_set() -> None:
    hangar = _hangar()
    ac = make_test_aircraft(id="p1")
    obj = GroundObject(
        id="doorblock",
        name="d",
        parts=(_ground_part(),),
        object_class="fixed_obstacle",
    )
    layout = Layout(
        fleet={ac.id: ac},
        hangar=hangar,
        placements=(Placement(plane_id=ac.id, x_m=6.0, y_m=8.0, heading_deg=0.0, on_carts=False),),
        ground_objects={obj.id: obj},
        # a wide obstacle just inside the door throat (small y)
        ground_object_placements=(
            Placement(plane_id=obj.id, x_m=7.0, y_m=1.0, heading_deg=0.0, on_carts=False),
        ),
    )
    obstacles = _build_obstacles(layout, mover_id=ac.id)
    # the obstacle's footprint is present among the planner's static world parts
    assert any(wp.plane_id == "doorblock" for wp in obstacles.world_parts)


def test_plan_fill_routes_ground_object_movers() -> None:
    hangar = _hangar()
    ac = make_test_aircraft(id="p1")
    car = GroundObject(
        id="caddy",
        name="c",
        parts=(_ground_part(width_m=2.0, length_m=4.5),),
        object_class="placed_routed_mover",
        motion_mode="steerable",
        turn_radius_m=5.5,
    )
    layout = Layout(
        fleet={ac.id: ac},
        hangar=hangar,
        placements=(Placement(plane_id="p1", x_m=6.0, y_m=30.0, heading_deg=0.0, on_carts=False),),
        ground_objects={car.id: car},
        ground_object_placements=(
            Placement(plane_id="caddy", x_m=10.0, y_m=20.0, heading_deg=90.0, on_carts=False),
        ),
    )
    plan = plan_fill(layout)
    caddy_moves = [m for m in plan.moves if m.plane_id == "caddy"]
    assert len(caddy_moves) == 1
    assert caddy_moves[0].path is not None  # #602: routed, not deferred None
    assert plan.moves[-1].plane_id == "caddy"  # movers appended after aircraft


def test_movers_excluded_from_static_obstacles_when_routed() -> None:
    """A mover being routed (mover_id) is excluded from the static obstacle set,
    exactly like the placed aircraft being routed. Here we route the mover itself
    and assert it is NOT among the obstacles."""
    hangar = _hangar()
    ac = make_test_aircraft(id="p1")
    mover = GroundObject(
        id="caddy",
        name="c",
        parts=(_ground_part(width_m=2.0),),
        object_class="placed_routed_mover",
        motion_mode="steerable",
        turn_radius_m=4.0,
    )
    layout = Layout(
        fleet={ac.id: ac},
        hangar=hangar,
        placements=(Placement(plane_id=ac.id, x_m=6.0, y_m=8.0, heading_deg=0.0, on_carts=False),),
        ground_objects={mover.id: mover},
        ground_object_placements=(
            Placement(plane_id=mover.id, x_m=3.0, y_m=8.0, heading_deg=0.0, on_carts=False),
        ),
    )
    obstacles = _build_obstacles(layout, mover_id="caddy")
    assert all(wp.plane_id != "caddy" for wp in obstacles.world_parts)


def test_plan_path_routes_a_ground_object_car_mover() -> None:
    """A steerable GO car routes from the door cone to a parked slot against a
    placed aircraft, and the returned arc is collision-free (the oracle places
    the mover as a ground_object_placement, not an aircraft placement)."""
    from hangarfit.towplanner import Pose, entry_poses, path_first_conflict, plan_path

    hangar = _hangar()
    ac = make_test_aircraft(id="p1")
    car = GroundObject(
        id="caddy",
        name="c",
        parts=(_ground_part(width_m=2.0, length_m=4.5),),
        object_class="placed_routed_mover",
        motion_mode="steerable",
        turn_radius_m=5.5,
    )
    slot = Placement(plane_id="caddy", x_m=10.0, y_m=20.0, heading_deg=90.0, on_carts=False)
    # `placed` carries only the already-committed bodies (the aircraft) — NOT the
    # mover's own goal slot. Mirrors the aircraft routing contract in plan_fill:
    # the body being routed is excluded from `placed`, then re-injected per-sample
    # by path_first_conflict (else Layout rejects the duplicate id). The mover IS
    # registered in `ground_objects` so the per-sample layout can resolve it.
    placed = Layout(
        fleet={ac.id: ac},
        hangar=hangar,
        placements=(Placement(plane_id="p1", x_m=6.0, y_m=30.0, heading_deg=0.0, on_carts=False),),
        ground_objects={car.id: car},
    )
    cone = entry_poses(slot, hangar)
    arc = plan_path(
        car,
        cone[0],
        Pose.from_placement(slot),
        hangar=hangar,
        placed=placed,
        mover_on_carts=False,
        entries=cone,
        heuristic="grid",
    )
    assert arc is not None
    assert path_first_conflict(arc, car, mover_on_carts=False, placed=placed) is None


def test_empty_ground_objects_no_extra_obstacles() -> None:
    """Byte-identity guard rail: with no ground objects the obstacle set carries
    only the (other) placed planes — no spurious entries."""
    hangar = _hangar()
    a = make_test_aircraft(id="p1")
    b = make_test_aircraft(id="p2")
    layout = Layout(
        fleet={a.id: a, b.id: b},
        hangar=hangar,
        placements=(
            Placement(plane_id="p1", x_m=6.0, y_m=8.0, heading_deg=0.0, on_carts=False),
            Placement(plane_id="p2", x_m=14.0, y_m=8.0, heading_deg=0.0, on_carts=False),
        ),
    )
    obstacles = _build_obstacles(layout, mover_id="p1")
    plane_ids = {wp.plane_id for wp in obstacles.world_parts}
    assert plane_ids == {"p2"}


def test_static_mover_obstacle_served_from_pose_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """#626: within a ``pose_cache_scope``, a STATIC mover obstacle's world parts
    are memoized across repeated ``_build_obstacles`` calls instead of rebuilt each
    time — the pre-#626 uncached ``aircraft_parts_world`` path that drove the #604
    routing congestion. Pre-#626 the trailer is rebuilt once per ``_build_obstacles``
    call; after #626 it is built once and served from the cache thereafter."""
    import hangarfit.geometry as geom
    import hangarfit.towplanner as tp
    from hangarfit.geometry import pose_cache_scope

    hangar = _hangar()
    ac = make_test_aircraft(id="p1")
    trailer = GroundObject(
        id="trailer",
        name="t",
        parts=(_ground_part(width_m=2.5, length_m=8.0),),
        object_class="placed_routed_mover",
        motion_mode="towed",
        turn_radius_m=None,
    )
    layout = Layout(
        fleet={ac.id: ac},
        hangar=hangar,
        placements=(Placement(plane_id="p1", x_m=6.0, y_m=30.0, heading_deg=0.0, on_carts=False),),
        ground_objects={trailer.id: trailer},
        ground_object_placements=(
            Placement(plane_id="trailer", x_m=10.0, y_m=20.0, heading_deg=90.0, on_carts=False),
        ),
    )

    # Count world-part builds per body id. cached_parts_world (post-#626) builds
    # via geometry's binding; the pre-#626 _build_obstacles called towplanner's
    # imported binding directly for the mover (removed by #626, so patch it
    # tolerantly so this test is meaningful on both sides of the change).
    real = geom.aircraft_parts_world
    builds: list[str] = []

    def _counting(obj: object, placement: object) -> object:
        builds.append(obj.id)  # type: ignore[attr-defined]
        return real(obj, placement)  # type: ignore[arg-type]

    monkeypatch.setattr(geom, "aircraft_parts_world", _counting)
    monkeypatch.setattr(tp, "aircraft_parts_world", _counting, raising=False)

    # Route the aircraft (mover_id="p1") so the trailer is a STATIC obstacle.
    with pose_cache_scope():
        _build_obstacles(layout, mover_id="p1")
        _build_obstacles(layout, mover_id="p1")

    assert builds.count("trailer") == 1, (
        f"static mover obstacle rebuilt {builds.count('trailer')}× across two "
        "_build_obstacles calls — expected 1 (the pose cache should serve it; #626)"
    )
