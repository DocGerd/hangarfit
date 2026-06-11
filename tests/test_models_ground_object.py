"""GroundObject model + validation (#601)."""

import pytest

from hangarfit.models import GroundObject, Part


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
