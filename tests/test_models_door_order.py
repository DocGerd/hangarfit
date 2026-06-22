"""#614 SOFT door-priority tie-breaker — Scenario.door_order model validation.

``door_order`` is a scenario-level ordered sequence of placeable-body ids
expressing a desired door-proximity (the first id should park nearest the
door). It is the deferred SOFT half of #603's HARD Caddy egress gate: a
lexicographically-subordinate selection term, validated like
``region_preferences`` (ids must resolve to placeable bodies; no duplicates).
"""

import pytest

from hangarfit.models import Door, GroundObject, Hangar, MaintenanceBay, Part, Scenario
from tests.conftest import make_test_aircraft  # noqa: E402


def _hangar() -> Hangar:
    return Hangar(
        length_m=40.0,
        width_m=40.0,
        door=Door(center_x_m=20.0, width_m=12.0),
        maintenance_bay=MaintenanceBay(center_x_m=20.0, width_m=8.0, depth_m=6.0),
        clearance_m=0.3,
        wing_layer_clearance_m=0.2,
    )


def _ground_part() -> Part:
    return Part(
        kind="ground",
        length_m=3.0,
        width_m=2.0,
        offset_x_m=0.0,
        offset_y_m=0.0,
        angle_deg=0.0,
        z_bottom_m=0.0,
        z_top_m=1.5,
    )


def _towed_mover(obj_id: str = "glider_trailer_1") -> GroundObject:
    return GroundObject(
        id=obj_id,
        name="Glider trailer",
        parts=(_ground_part(),),
        object_class="placed_routed_mover",
        motion_mode="towed",
    )


def _scenario(**kwargs) -> Scenario:
    """Valid Scenario with three aircraft (husky, fuji, scout)."""
    fleet = {pid: make_test_aircraft(id=pid) for pid in ("husky", "fuji", "scout")}
    return Scenario(
        fleet=fleet,
        hangar=_hangar(),
        fleet_in=("husky", "fuji", "scout"),
        **kwargs,
    )


def test_scenario_door_order_default_none():
    s = _scenario()
    assert s.door_order is None  # unset ⇒ inert (byte-identical solver, ADR-0003)


def test_scenario_door_order_valid_aircraft_ids():
    s = _scenario(door_order=("fuji", "husky"))
    assert s.door_order == ("fuji", "husky")


def test_scenario_door_order_rejects_unknown_id():
    with pytest.raises(ValueError, match="door_order"):
        _scenario(door_order=("husky", "ghost"))


def test_scenario_door_order_rejects_duplicate():
    with pytest.raises(ValueError, match="duplicate"):
        _scenario(door_order=("husky", "husky"))


def test_scenario_door_order_on_mover_ok():
    mover = _towed_mover()
    s = Scenario(
        fleet={"husky": make_test_aircraft(id="husky")},
        hangar=_hangar(),
        fleet_in=("husky",),
        ground_objects=(mover.id,),
        ground_object_defs={mover.id: mover},
        door_order=(mover.id, "husky"),
    )
    assert s.door_order == (mover.id, "husky")
