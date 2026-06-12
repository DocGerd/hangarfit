from hangarfit.geometry import aircraft_parts_world, cached_parts_world
from hangarfit.models import Aircraft, GroundObject, Layout, Placement
from hangarfit.solver import _body, _body_parts_world, _build_layout


def _bounds_key(parts):
    return [(wp.plane_id, wp.kind, tuple(round(c, 9) for c in wp.polygon.bounds)) for wp in parts]


def test_body_returns_aircraft_for_fleet_id(region_scenario):
    b = _body(region_scenario, "fuji")
    assert isinstance(b, Aircraft) and b.id == "fuji"
    # identity: same object the solver looks up today
    assert b is region_scenario.fleet["fuji"]


def test_body_returns_ground_object_for_mover_id(region_scenario):
    b = _body(region_scenario, "glider_trailer_1")
    assert isinstance(b, GroundObject) and b.object_class == "placed_routed_mover"


def test_body_parts_world_aircraft_byte_identical(region_scenario):
    p = Placement(plane_id="fuji", x_m=10.0, y_m=12.0, heading_deg=30.0, on_carts=False)
    # aircraft path delegates to cached_parts_world on the SAME aircraft object
    got = _body_parts_world(region_scenario, "fuji", p)
    ref = cached_parts_world(region_scenario.fleet["fuji"], p)
    assert _bounds_key(got) == _bounds_key(ref)


def test_body_parts_world_mover_matches_uncached_transform(region_scenario):
    p = Placement(plane_id="glider_trailer_1", x_m=20.0, y_m=15.0, heading_deg=90.0, on_carts=False)
    got = _body_parts_world(region_scenario, "glider_trailer_1", p)
    ref = aircraft_parts_world(region_scenario.ground_object_defs["glider_trailer_1"], p)
    assert _bounds_key(got) == _bounds_key(ref)


def test_build_layout_aircraft_only_matches_plain_layout(region_scenario_no_go):
    s = region_scenario_no_go  # NO ground objects
    placements = {
        "fuji": Placement("fuji", 5.0, 8.0, 0.0, on_carts=False),
        "cessna_150": Placement("cessna_150", 12.0, 9.0, 0.0, on_carts=False),
    }
    built = _build_layout(s, placements)
    plain = Layout(
        fleet=s.fleet,
        hangar=s.hangar,
        placements=tuple(placements.values()),
        maintenance_plane=s.maintenance_plane,
    )
    assert built.placements == plain.placements  # same order, same poses
    assert built.ground_object_placements == ()


def test_build_layout_splits_movers_and_injects_fixed(region_scenario):
    s = region_scenario  # fuji+cessna_150 ; movers glider_trailer_1/2 ; fixed maul_fuel_trailer
    placements = {
        "fuji": Placement("fuji", 5.0, 8.0, 0.0, on_carts=False),
        "cessna_150": Placement("cessna_150", 12.0, 9.0, 0.0, on_carts=False),
        "glider_trailer_1": Placement("glider_trailer_1", 20.0, 20.0, 90.0, on_carts=False),
    }
    built = _build_layout(s, placements)
    assert {p.plane_id for p in built.placements} == {"fuji", "cessna_150"}
    go_ids = {p.plane_id for p in built.ground_object_placements}
    assert "glider_trailer_1" in go_ids and "maul_fuel_trailer" in go_ids  # mover + injected fixed
