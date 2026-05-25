import dataclasses
import math

import pytest

from hangarfit.models import Placement
from hangarfit.towplanner import (
    DubinsArc,
    Move,
    MovesPlan,
    Pose,
    Segment,
    back_first_order,
)


def test_pose_from_placement_drops_identity_and_cart_state():
    p = Placement(plane_id="DG-ABC", x_m=3.0, y_m=4.0, heading_deg=30.0, on_carts=True)
    pose = Pose.from_placement(p)
    assert pose == Pose(x_m=3.0, y_m=4.0, heading_deg=30.0)
    assert not hasattr(pose, "plane_id")
    assert not hasattr(pose, "on_carts")


def test_pose_is_frozen():
    pose = Pose(x_m=0.0, y_m=0.0, heading_deg=0.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        pose.x_m = 1.0  # type: ignore[misc]


def test_dubins_arc_length_sums_segments():
    # A straight-only arc of length 5 (turn_radius irrelevant for S segments).
    arc = DubinsArc(
        start=Pose(0.0, 0.0, 0.0),
        end=Pose(0.0, 5.0, 0.0),
        turn_radius_m=10.0,
        segments=(Segment(kind="S", length_m=5.0),),
    )
    assert arc.length_m == pytest.approx(5.0)


def test_dubins_arc_rejects_unknown_segment_kind():
    with pytest.raises(ValueError):
        Segment(kind="Q", length_m=1.0)


def test_dubins_arc_rejects_negative_turn_radius():
    with pytest.raises(ValueError):
        DubinsArc(
            start=Pose(0.0, 0.0, 0.0),
            end=Pose(0.0, 1.0, 0.0),
            turn_radius_m=-1.0,
            segments=(Segment("S", 1.0),),
        )


def test_dubins_arc_allows_zero_turn_radius_pivot_sentinel():
    # turn_radius_m == 0 is the cart pivot-in-place sentinel (ADR-0007) and
    # must survive validation — guards against a `<= 0` reject.
    arc = DubinsArc(
        start=Pose(0.0, 0.0, 0.0),
        end=Pose(0.0, 0.0, 90.0),
        turn_radius_m=0.0,
        segments=(Segment("R", math.radians(90.0)),),
    )
    assert arc.turn_radius_m == 0.0


def test_dubins_arc_rejects_empty_segments():
    with pytest.raises(ValueError):
        DubinsArc(
            start=Pose(0.0, 0.0, 0.0),
            end=Pose(0.0, 0.0, 0.0),
            turn_radius_m=5.0,
            segments=(),
        )


def test_move_rejects_empty_plane_id():
    with pytest.raises(ValueError):
        Move(
            plane_id="",
            target_slot=Pose(1.0, 2.0, 0.0),
            path=DubinsArc(
                start=Pose(0.0, 0.0, 0.0),
                end=Pose(1.0, 2.0, 0.0),
                turn_radius_m=8.0,
                segments=(Segment("S", math.hypot(1.0, 2.0)),),
            ),
        )


def test_movesplan_construction_roundtrip():
    layout = object()  # placeholder; Move/MovesPlan do not validate layout in Wave 1
    move = Move(
        plane_id="DG-ABC",
        target_slot=Pose(1.0, 2.0, 0.0),
        path=DubinsArc(
            start=Pose(0.0, 0.0, 0.0),
            end=Pose(1.0, 2.0, 0.0),
            turn_radius_m=8.0,
            segments=(Segment(kind="S", length_m=math.hypot(1.0, 2.0)),),
        ),
    )
    plan = MovesPlan(target_layout=layout, moves=(move,))
    assert plan.moves[0].plane_id == "DG-ABC"
    assert plan.moves[0].path.end == plan.moves[0].target_slot


def _pl(pid: str, x: float, y: float) -> Placement:
    return Placement(plane_id=pid, x_m=x, y_m=y, heading_deg=0.0, on_carts=False)


def test_back_first_orders_deepest_y_first():
    placements = (_pl("A", 0.0, 1.0), _pl("B", 0.0, 9.0), _pl("C", 0.0, 5.0))
    assert [p.plane_id for p in back_first_order(placements)] == ["B", "C", "A"]


def test_back_first_tiebreak_is_x_asc_then_plane_id():
    placements = (
        _pl("Z", 3.0, 5.0),
        _pl("A", 3.0, 5.0),  # same y, same x as Z -> plane_id breaks tie
        _pl("M", 1.0, 5.0),  # same y, smaller x -> first among the y=5 group
    )
    assert [p.plane_id for p in back_first_order(placements)] == ["M", "A", "Z"]


def test_back_first_is_pure_and_deterministic():
    placements = (_pl("A", 0.0, 1.0), _pl("B", 0.0, 9.0))
    once = back_first_order(placements)
    twice = back_first_order(placements)
    assert once == twice
    assert placements == (_pl("A", 0.0, 1.0), _pl("B", 0.0, 9.0))  # input untouched
