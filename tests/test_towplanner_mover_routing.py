"""Mover-routing integration tests (#602 / #603).

Covers:
- a steerable car AND a towed trailer both route collision-free via plan_fill
- a towed trailer (cart, r=0) routes and honours the effective_turn_radius_m()==0.0 contract
- plan_fill is byte-identical across two runs when ground-object movers are present (ADR-0003)
- egress_first_conflict returns None when corridor is clear (#603)
- egress_first_conflict returns a caddy_egress Conflict when corridor is walled off (#603)

Fixture helpers copied from tests/test_towplanner_ground_object.py per the
repo's per-module fixture-duplication convention.
"""

from __future__ import annotations

import pytest

from hangarfit.models import (
    Door,
    GroundObject,
    Hangar,
    Layout,
    MaintenanceBay,
    Part,
    Placement,
)
from hangarfit.towplanner import path_first_conflict, plan_fill
from tests.conftest import make_test_aircraft

# ── Inline fixture helpers ────────────────────────────────────────────────────


def _hangar(clearance: float = 0.3, wlc: float = 0.2) -> Hangar:
    """Inline minimal hangar (copied from tests/test_towplanner_ground_object.py)."""
    return Hangar(
        length_m=40.0,
        width_m=40.0,
        door=Door(center_x_m=20.0, width_m=12.0),
        maintenance_bay=MaintenanceBay(center_x_m=20.0, width_m=8.0, depth_m=6.0),
        clearance_m=clearance,
        wing_layer_clearance_m=wlc,
    )


def _ground_part(length_m: float = 3.0, width_m: float = 1.3, z_top_m: float = 1.5) -> Part:
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


# ── Integration tests ─────────────────────────────────────────────────────────


@pytest.mark.slow
def test_car_and_trailer_both_route_collision_free() -> None:
    """A steerable car + a towed trailer both receive non-None paths from
    plan_fill, and each path is collision-free when checked with
    path_first_conflict (the mover is excluded from placed.ground_object_placements,
    matching the routing contract in plan_fill)."""
    hangar = _hangar()
    ac = make_test_aircraft(id="p1")

    car = GroundObject(
        id="caddy",
        name="VW Caddy",
        parts=(_ground_part(length_m=4.5, width_m=1.8),),
        object_class="placed_routed_mover",
        motion_mode="steerable",
        turn_radius_m=5.5,
    )
    trailer = GroundObject(
        id="trailer1",
        name="Glider trailer",
        parts=(_ground_part(length_m=6.0, width_m=1.2),),
        object_class="placed_routed_mover",
        motion_mode="towed",
        # No turn_radius_m → cart routing (r=0.0)
    )

    # Aircraft parked deep in the hangar, well clear of the mover slots.
    # Movers placed a few metres in from the door so paths are short and
    # definitely achievable within the default expansion budget.
    layout = Layout(
        fleet={ac.id: ac},
        hangar=hangar,
        placements=(Placement(plane_id="p1", x_m=20.0, y_m=30.0, heading_deg=0.0, on_carts=False),),
        ground_objects={car.id: car, trailer.id: trailer},
        ground_object_placements=(
            Placement(plane_id="caddy", x_m=10.0, y_m=10.0, heading_deg=90.0, on_carts=False),
            Placement(plane_id="trailer1", x_m=28.0, y_m=10.0, heading_deg=90.0, on_carts=False),
        ),
    )

    plan = plan_fill(layout)

    car_moves = [m for m in plan.moves if m.plane_id == "caddy"]
    trailer_moves = [m for m in plan.moves if m.plane_id == "trailer1"]

    assert len(car_moves) == 1, "car must have exactly one move"
    assert len(trailer_moves) == 1, "trailer must have exactly one move"

    assert car_moves[0].path is not None, "steerable car must route (non-None path)"
    assert trailer_moves[0].path is not None, "towed trailer must route (non-None path)"

    # Check collision-freedom: the mover under check is EXCLUDED from
    # placed.ground_object_placements (path_first_conflict re-injects it per
    # sample — the routing contract), while it stays registered in
    # placed.ground_objects so the per-sample Layout can resolve its parts.
    def _placed_excluding(mover_id: str) -> Layout:
        return Layout(
            fleet=layout.fleet,
            hangar=hangar,
            placements=layout.placements,
            ground_objects=layout.ground_objects,
            ground_object_placements=tuple(
                gp for gp in layout.ground_object_placements if gp.plane_id != mover_id
            ),
        )

    assert (
        path_first_conflict(
            car_moves[0].path,
            car,
            mover_on_carts=False,
            placed=_placed_excluding("caddy"),
        )
        is None
    ), "car path must be collision-free"

    assert (
        path_first_conflict(
            trailer_moves[0].path,
            trailer,
            mover_on_carts=False,
            placed=_placed_excluding("trailer1"),
        )
        is None
    ), "trailer path must be collision-free"


@pytest.mark.slow
def test_towed_trailer_routes_with_cart_reverse_capability() -> None:
    """A towed trailer (motion_mode='towed', no turn_radius_m) routes via plan_fill
    and honours the cart contract: effective_turn_radius_m() == 0.0 and the
    returned path is collision-free.

    NOTE: forcing a reverse segment deterministically is impractical in a black-box
    integration test (slot geometry, path search, and the Reeds–Shepp primitive fan
    all interact non-trivially). The reverse-leg sub-assertion is therefore DROPPED
    per the task spec — the meaningful contract asserted here is that the trailer
    is treated as a cart (radius 0.0) and still receives a valid, collision-free
    path from plan_fill.
    """
    hangar = _hangar()
    ac = make_test_aircraft(id="p1")

    trailer = GroundObject(
        id="gl_trailer",
        name="Glider trailer",
        parts=(_ground_part(length_m=5.5, width_m=1.2),),
        object_class="placed_routed_mover",
        motion_mode="towed",
        # No turn_radius_m → effective_turn_radius_m() == 0.0
    )

    layout = Layout(
        fleet={ac.id: ac},
        hangar=hangar,
        placements=(Placement(plane_id="p1", x_m=20.0, y_m=30.0, heading_deg=0.0, on_carts=False),),
        ground_objects={trailer.id: trailer},
        ground_object_placements=(
            Placement(plane_id="gl_trailer", x_m=28.0, y_m=10.0, heading_deg=90.0, on_carts=False),
        ),
    )

    # Cart contract: radius 0.0
    assert trailer.effective_turn_radius_m() == 0.0

    plan = plan_fill(layout)
    trailer_moves = [m for m in plan.moves if m.plane_id == "gl_trailer"]
    assert len(trailer_moves) == 1
    assert trailer_moves[0].path is not None, "towed trailer (cart) must route"

    placed_no_trailer = Layout(
        fleet=layout.fleet,
        hangar=hangar,
        placements=layout.placements,
        ground_objects=layout.ground_objects,
        ground_object_placements=(),
    )
    assert (
        path_first_conflict(
            trailer_moves[0].path,
            trailer,
            mover_on_carts=False,
            placed=placed_no_trailer,
        )
        is None
    ), "towed trailer path must be collision-free"


# ── #603 egress_first_conflict helpers + tests ───────────────────────────────


def _caddy(hdm: bool = True) -> GroundObject:
    return GroundObject(
        id="caddy",
        name="c",
        parts=(_ground_part(length_m=4.5, width_m=1.8),),
        object_class="placed_routed_mover",
        motion_mode="steerable",
        turn_radius_m=5.5,
        hard_door_mover=hdm,
    )


def _wide_wall_parts() -> tuple[Part, ...]:
    """Parts for a wall aircraft with 44 m wide fuselage parts (ground level)
    that span beyond the 40 m hangar width, leaving no path around the wall.
    Ground-level z (0–1.5 m) so they overlap the caddy's ground part."""
    return (
        Part(
            kind="fuselage_front",
            length_m=1.0,
            width_m=44.0,
            offset_x_m=0.5,
            offset_y_m=0.0,
            angle_deg=0.0,
            z_bottom_m=0.0,
            z_top_m=1.5,
        ),
        Part(
            kind="fuselage_aft",
            length_m=1.0,
            width_m=44.0,
            offset_x_m=-0.5,
            offset_y_m=0.0,
            angle_deg=0.0,
            z_bottom_m=0.0,
            z_top_m=1.5,
        ),
    )


def test_egress_clear_returns_none() -> None:
    """egress_first_conflict returns None when the corridor to the door is open.

    Caddy placed close to the door (y=6 m), nothing between it and the door
    → a direct egress path exists."""
    from hangarfit.towplanner import egress_first_conflict

    hangar = _hangar()
    caddy = _caddy()
    layout = Layout(
        fleet={},
        hangar=hangar,
        placements=(),
        ground_objects={caddy.id: caddy},
        ground_object_placements=(
            Placement("caddy", x_m=20.0, y_m=6.0, heading_deg=0.0, on_carts=False),
        ),
    )
    assert egress_first_conflict(layout, "caddy") is None


@pytest.mark.slow
def test_egress_blocked_returns_caddy_egress_conflict() -> None:
    """egress_first_conflict returns a caddy_egress Conflict when an aircraft
    walls off the caddy's only path to the door.

    A wide-wing aircraft (wing_m=16, wider than the 12 m door) is placed across
    the corridor between the caddy (y=30) and the door (y=0).  With no gap to
    route through, egress must fail."""
    from hangarfit.towplanner import egress_first_conflict

    hangar = _hangar()
    wall = make_test_aircraft(id="wall", parts=_wide_wall_parts())
    caddy = _caddy()
    layout = Layout(
        fleet={wall.id: wall},
        hangar=hangar,
        placements=(Placement("wall", x_m=20.0, y_m=15.0, heading_deg=0.0, on_carts=False),),
        ground_objects={caddy.id: caddy},
        ground_object_placements=(
            Placement("caddy", x_m=20.0, y_m=30.0, heading_deg=0.0, on_carts=False),
        ),
    )
    c = egress_first_conflict(layout, "caddy")
    assert c is not None and c.kind == "caddy_egress" and "caddy" in c.planes


def test_mover_routing_is_byte_identical_across_runs() -> None:
    """plan_fill with ground-object movers is byte-identical across two calls
    on the same Layout — RNG-free, ADR-0003.

    NON-@slow so two-pass coverage keeps this test in the fast set and the
    mover-routing code path is never dropped from coverage."""
    hangar = _hangar()
    ac = make_test_aircraft(id="p1")

    car = GroundObject(
        id="caddy",
        name="VW Caddy",
        parts=(_ground_part(length_m=4.5, width_m=1.8),),
        object_class="placed_routed_mover",
        motion_mode="steerable",
        turn_radius_m=5.5,
    )
    trailer = GroundObject(
        id="trailer1",
        name="Glider trailer",
        parts=(_ground_part(length_m=6.0, width_m=1.2),),
        object_class="placed_routed_mover",
        motion_mode="towed",
    )

    layout = Layout(
        fleet={ac.id: ac},
        hangar=hangar,
        placements=(Placement(plane_id="p1", x_m=20.0, y_m=30.0, heading_deg=0.0, on_carts=False),),
        ground_objects={car.id: car, trailer.id: trailer},
        ground_object_placements=(
            Placement(plane_id="caddy", x_m=10.0, y_m=10.0, heading_deg=90.0, on_carts=False),
            Placement(plane_id="trailer1", x_m=28.0, y_m=10.0, heading_deg=90.0, on_carts=False),
        ),
    )

    a = plan_fill(layout)
    b = plan_fill(layout)
    assert [(m.plane_id, m.path) for m in a.moves] == [(m.plane_id, m.path) for m in b.moves], (
        "plan_fill must be byte-identical (ADR-0003)"
    )


def _fixed_wall() -> GroundObject:
    """A full-width fixed obstacle (44 m, spanning beyond the 40 m hangar) — a
    static keep-out with no corridor around it, so a mover behind it cannot be
    routed in from the door. A fixed_obstacle is NOT itself routed, so (unlike a
    44 m wall *aircraft*) it doesn't abort the aircraft scan — it just blocks the
    mover, isolating the unroutable-mover path."""
    return GroundObject(
        id="wall",
        name="w",
        parts=(_ground_part(length_m=1.0, width_m=44.0),),
        object_class="fixed_obstacle",
    )


def test_unroutable_mover_is_surfaced_not_silently_dropped() -> None:
    """#627/#612: a mover whose slot has no collision-free corridor keeps a
    None-path Move (best-effort, ADR-0007 #197) AND is reported through the
    optional ``unroutable_movers`` out-param — never silently dropped. The
    ``MovesPlan`` stays byte-identical whether or not the out-param is passed (it
    is observational, like ``apron_dropped_out``).

    NON-@slow: a small global cap keeps it in the fast set so the surfacing path
    is never dropped from coverage; the full-width wall makes the bail genuine
    (no analytic shot), not merely budget-starved."""
    hangar = _hangar()
    wall = _fixed_wall()
    caddy = _caddy(hdm=False)  # plain mover (no egress gate); slot walled off
    layout = Layout(
        fleet={},
        hangar=hangar,
        placements=(),
        ground_objects={wall.id: wall, caddy.id: caddy},
        ground_object_placements=(
            Placement("wall", x_m=20.0, y_m=15.0, heading_deg=0.0, on_carts=False),
            Placement("caddy", x_m=20.0, y_m=30.0, heading_deg=0.0, on_carts=False),
        ),
    )
    movers: list[str] = []
    plan = plan_fill(layout, unroutable_movers=movers, max_total_expansions=200)
    # surfaced, not silently dropped
    assert movers == ["caddy"]
    # ...and it kept a best-effort None-path Move (not aborted/omitted)
    caddy_move = next(m for m in plan.moves if m.plane_id == "caddy")
    assert caddy_move.path is None
    # byte-identical: the same fill WITHOUT the out-param yields the same plan
    plan_no_out = plan_fill(layout, max_total_expansions=200)
    assert [(m.plane_id, m.path) for m in plan_no_out.moves] == [
        (m.plane_id, m.path) for m in plan.moves
    ]
