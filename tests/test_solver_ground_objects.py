from hangarfit.geometry import aircraft_parts_world, cached_parts_world
from hangarfit.models import Aircraft, GroundObject, Placement
from hangarfit.solver import _body, _body_parts_world


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
