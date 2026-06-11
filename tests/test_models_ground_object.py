"""GroundObject model + validation (#601)."""

import pytest

from hangarfit.models import Door, GroundObject, Hangar, Layout, MaintenanceBay, Part, Placement
from tests.conftest import make_test_aircraft


def _hangar(clearance: float = 0.3, wlc: float = 0.2) -> Hangar:
    return Hangar(
        length_m=40.0,
        width_m=40.0,
        door=Door(center_x_m=20.0, width_m=12.0),
        maintenance_bay=MaintenanceBay(center_x_m=20.0, width_m=8.0, depth_m=6.0),
        clearance_m=clearance,
        wing_layer_clearance_m=wlc,
    )


def _rect_part(*, kind: str = "ground", length_m: float = 4.0, width_m: float = 2.0) -> Part:
    return Part(
        kind=kind,  # type: ignore[arg-type]
        length_m=length_m,
        width_m=width_m,
        offset_x_m=0.0,
        offset_y_m=0.0,
        angle_deg=0.0,
        z_bottom_m=0.0,
        z_top_m=1.5,
    )


def test_fixed_obstacle_constructs() -> None:
    obj = GroundObject(
        id="fuel_trailer",
        name="Fuel trailer",
        parts=(_rect_part(),),
        object_class="fixed_obstacle",
    )
    assert obj.object_class == "fixed_obstacle"
    assert obj.motion_mode is None
    assert obj.turn_radius_m is None


def test_mover_constructs_with_motion() -> None:
    obj = GroundObject(
        id="vw_caddy",
        name="VW Caddy",
        parts=(_rect_part(),),
        object_class="placed_routed_mover",
        motion_mode="steerable",
        turn_radius_m=4.5,
    )
    assert obj.motion_mode == "steerable"
    assert obj.turn_radius_m == 4.5


def test_ground_partkind_is_valid() -> None:
    # A "ground" footprint Part must construct without error.
    assert _rect_part(kind="ground").kind == "ground"


@pytest.mark.parametrize(
    "kwargs, msg",
    [
        (dict(id="", name="x", parts=(_rect_part(),), object_class="fixed_obstacle"), "id"),
        (dict(id="x", name="", parts=(_rect_part(),), object_class="fixed_obstacle"), "name"),
        (dict(id="x", name="x", parts=(), object_class="fixed_obstacle"), "parts"),
        (
            dict(id="x", name="x", parts=(_rect_part(),), object_class="bogus"),
            "object_class",
        ),
        (
            dict(
                id="x",
                name="x",
                parts=(_rect_part(),),
                object_class="fixed_obstacle",
                motion_mode="towed",
            ),
            "fixed_obstacle",  # a fixed obstacle must not carry motion
        ),
        (
            dict(
                id="x",
                name="x",
                parts=(_rect_part(),),
                object_class="placed_routed_mover",
            ),
            "motion_mode",  # a mover must carry motion
        ),
        (
            dict(
                id="x",
                name="x",
                parts=(_rect_part(),),
                object_class="placed_routed_mover",
                motion_mode="steerable",
                turn_radius_m=-1.0,
            ),
            "turn_radius_m",
        ),
    ],
)
def test_invalid_ground_object_rejected(kwargs: dict, msg: str) -> None:
    with pytest.raises(ValueError, match=msg):
        GroundObject(**kwargs)  # type: ignore[arg-type]


def _mover(obj_id: str = "caddy") -> GroundObject:
    return GroundObject(
        id=obj_id,
        name="Caddy",
        parts=(_rect_part(),),
        object_class="placed_routed_mover",
        motion_mode="steerable",
    )


def test_layout_accepts_ground_objects() -> None:
    hangar = _hangar()
    ac = make_test_aircraft(id="p1")
    obj = _mover()
    layout = Layout(
        fleet={ac.id: ac},
        hangar=hangar,
        placements=(Placement(plane_id=ac.id, x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False),),
        ground_objects={obj.id: obj},
        ground_object_placements=(
            Placement(plane_id=obj.id, x_m=2.0, y_m=2.0, heading_deg=0.0, on_carts=False),
        ),
    )
    assert layout.ground_objects[obj.id] is obj
    assert len(layout.ground_object_placements) == 1


def test_layout_rejects_ground_key_id_mismatch() -> None:
    hangar = _hangar()
    ac = make_test_aircraft(id="p1")
    obj = _mover("caddy")
    with pytest.raises(ValueError, match="ground_object"):
        Layout(
            fleet={ac.id: ac},
            hangar=hangar,
            placements=(),
            ground_objects={"WRONG": obj},
            ground_object_placements=(),
        )


def test_layout_rejects_ground_placement_unknown_id() -> None:
    hangar = _hangar()
    ac = make_test_aircraft(id="p1")
    obj = _mover("caddy")
    with pytest.raises(ValueError, match="unknown"):
        Layout(
            fleet={ac.id: ac},
            hangar=hangar,
            placements=(),
            ground_objects={obj.id: obj},
            ground_object_placements=(
                Placement(plane_id="ghost", x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=False),
            ),
        )


def test_layout_rejects_ground_id_colliding_with_fleet() -> None:
    # Ground-object id must be disjoint from fleet ids.
    hangar = _hangar()
    ac = make_test_aircraft(id="p1")
    obj = GroundObject(
        id=ac.id,
        name="clash",
        parts=(_rect_part(),),
        object_class="fixed_obstacle",
    )
    with pytest.raises(ValueError, match="disjoint|collide|both"):
        Layout(
            fleet={ac.id: ac},
            hangar=hangar,
            placements=(),
            ground_objects={obj.id: obj},
            ground_object_placements=(),
        )


def test_layout_empty_ground_objects_default() -> None:
    hangar = _hangar()
    ac = make_test_aircraft(id="p1")
    layout = Layout(fleet={ac.id: ac}, hangar=hangar, placements=())
    assert dict(layout.ground_objects) == {}
    assert layout.ground_object_placements == ()


def test_layout_with_ground_objects_pickles() -> None:
    import pickle

    hangar = _hangar()
    ac = make_test_aircraft(id="p1")
    obj = _mover()
    layout = Layout(
        fleet={ac.id: ac},
        hangar=hangar,
        placements=(),
        ground_objects={obj.id: obj},
        ground_object_placements=(
            Placement(plane_id=obj.id, x_m=1.0, y_m=1.0, heading_deg=0.0, on_carts=False),
        ),
    )
    back = pickle.loads(pickle.dumps(layout))
    assert back.ground_objects[obj.id].id == obj.id
    assert back.ground_object_placements[0].plane_id == obj.id


# ---------------------------------------------------------------------------
# Task 3: Scenario ground-object id-list
# ---------------------------------------------------------------------------

from hangarfit.models import Scenario  # noqa: E402


def test_scenario_ground_objects_idlist() -> None:
    hangar = _hangar()
    ac = make_test_aircraft(id="p1")
    obj = _mover()
    scn = Scenario(
        fleet={ac.id: ac},
        hangar=hangar,
        fleet_in=(ac.id,),
        ground_objects=(obj.id,),
        ground_object_defs={obj.id: obj},
    )
    assert scn.ground_objects == (obj.id,)


def test_scenario_rejects_unknown_ground_object_ref() -> None:
    hangar = _hangar()
    ac = make_test_aircraft(id="p1")
    with pytest.raises(ValueError, match="ground_object"):
        Scenario(
            fleet={ac.id: ac},
            hangar=hangar,
            fleet_in=(ac.id,),
            ground_objects=("ghost",),
            ground_object_defs={},
        )
