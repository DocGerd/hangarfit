import pytest

from hangarfit.models import (
    Door,
    GroundObject,
    Hangar,
    MaintenanceBay,
    Part,
    Placement,
    RegionAlignment,
    RegionPreference,
    Scenario,
    SolverDiagnostics,
)
from tests.conftest import make_test_aircraft  # noqa: E402

# ---------------------------------------------------------------------------
# Shared builders (copied from test_towplanner_ground_object.py pattern)
# ---------------------------------------------------------------------------


def _hangar(clearance: float = 0.3, wlc: float = 0.2) -> Hangar:
    return Hangar(
        length_m=40.0,
        width_m=40.0,
        door=Door(center_x_m=20.0, width_m=12.0),
        maintenance_bay=MaintenanceBay(center_x_m=20.0, width_m=8.0, depth_m=6.0),
        clearance_m=clearance,
        wing_layer_clearance_m=wlc,
    )


def _ground_part(length_m: float = 3.0, width_m: float = 2.0, z_top_m: float = 1.5) -> Part:
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


def _towed_mover(obj_id: str = "glider_trailer_1") -> GroundObject:
    """A valid placed_routed_mover with motion_mode='towed' (no turn_radius_m)."""
    return GroundObject(
        id=obj_id,
        name="Glider trailer",
        parts=(_ground_part(),),
        object_class="placed_routed_mover",
        motion_mode="towed",
    )


def _fixed_obstacle(obj_id: str = "maul_fuel_trailer") -> GroundObject:
    return GroundObject(
        id=obj_id,
        name="Fuel trailer",
        parts=(_ground_part(),),
        object_class="fixed_obstacle",
    )


def _minimal_scenario(**kwargs) -> Scenario:
    """Minimal valid Scenario with one aircraft."""
    ac = make_test_aircraft(id="husky")
    return Scenario(
        fleet={ac.id: ac},
        hangar=_hangar(),
        fleet_in=(ac.id,),
        **kwargs,
    )


def test_region_preference_valid_right():
    rp = RegionPreference(side="right", weight=1.0)
    assert rp.side == "right"
    assert rp.weight == 1.0


def test_region_preference_zero_weight_allowed():
    assert RegionPreference(side="left", weight=0.0).weight == 0.0


@pytest.mark.parametrize("bad", [-0.1, float("nan"), float("inf")])
def test_region_preference_rejects_bad_weight(bad):
    with pytest.raises(ValueError):
        RegionPreference(side="right", weight=bad)


def test_region_preference_rejects_bad_side():
    with pytest.raises(ValueError):
        RegionPreference(side="up", weight=1.0)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Task 2: Scenario.region_preferences + fixed_obstacle_placements + mover_ids
# ---------------------------------------------------------------------------


def test_scenario_region_preferences_default_empty():
    s = _minimal_scenario()
    assert dict(s.region_preferences) == {}


def test_scenario_region_preference_must_reference_placeable_id():
    with pytest.raises(ValueError, match="ghost"):
        _minimal_scenario(region_preferences={"ghost": RegionPreference(side="right", weight=1.0)})


def test_scenario_region_preference_on_aircraft_ok():
    ac_id = "husky"
    s = _minimal_scenario(region_preferences={ac_id: RegionPreference(side="left", weight=0.5)})
    assert s.region_preferences[ac_id].side == "left"


def test_scenario_mover_ids_from_ground_objects():
    mover = _towed_mover("glider_trailer_1")
    s = _minimal_scenario(
        ground_objects=(mover.id,),
        ground_object_defs={mover.id: mover},
    )
    assert s.mover_ids == ("glider_trailer_1",)


def test_scenario_region_preference_on_mover_ok():
    mover = _towed_mover("glider_trailer_1")
    s = _minimal_scenario(
        ground_objects=(mover.id,),
        ground_object_defs={mover.id: mover},
        region_preferences={mover.id: RegionPreference(side="right", weight=1.0)},
    )
    assert s.region_preferences[mover.id].side == "right"


def test_scenario_placeable_ids_is_fleet_in_when_no_movers():
    s = _minimal_scenario()
    assert s.placeable_ids == s.fleet_in


def test_scenario_fixed_obstacle_placement_validates():
    obs = _fixed_obstacle("maul_fuel_trailer")
    mover = _towed_mover("glider_trailer_1")
    # A fixed_obstacle_placements entry referencing a placed_routed_mover id must raise.
    with pytest.raises(ValueError):
        _minimal_scenario(
            ground_objects=(mover.id,),
            ground_object_defs={mover.id: mover},
            fixed_obstacle_placements=(
                Placement(plane_id=mover.id, x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False),
            ),
        )
    # An unknown id must also raise.
    with pytest.raises(ValueError):
        _minimal_scenario(
            fixed_obstacle_placements=(
                Placement(plane_id="ghost", x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False),
            ),
        )
    # A valid fixed_obstacle must be accepted.
    s = _minimal_scenario(
        ground_objects=(obs.id,),
        ground_object_defs={obs.id: obs},
        fixed_obstacle_placements=(
            Placement(plane_id=obs.id, x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False),
        ),
    )
    assert len(s.fixed_obstacle_placements) == 1


# ---------------------------------------------------------------------------
# Task 3: SolverDiagnostics.region_alignment
# ---------------------------------------------------------------------------


def _diag(**kw):
    base = dict(
        restarts_attempted=1, wall_time_s=0.0, best_partial=None, best_partial_layout=None, seed=0
    )
    base.update(kw)
    return SolverDiagnostics(**base)


def test_region_alignment_default_empty():
    assert _diag().region_alignment == ()


def test_region_alignment_valid():
    d = _diag(region_alignment=((RegionAlignment("glider_trailer_1", 0.92),),))
    assert d.region_alignment[0][0] == RegionAlignment("glider_trailer_1", 0.92)
    # NamedTuple compares equal to the bare tuple by value (determinism-neutral).
    assert d.region_alignment[0][0] == ("glider_trailer_1", 0.92)


@pytest.mark.parametrize("bad", [-0.01, 1.01, float("nan")])
def test_region_alignment_rejects_out_of_range(bad):
    with pytest.raises(ValueError):
        _diag(region_alignment=((RegionAlignment("t", bad),),))
