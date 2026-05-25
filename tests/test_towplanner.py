import dataclasses
import math

import pytest

from hangarfit.models import Placement
from hangarfit.towplanner import DubinsArc, Move, MovesPlan, Pose, Segment


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
