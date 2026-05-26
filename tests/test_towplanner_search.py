import math

import pytest

from hangarfit.towplanner import (
    Pose,
    Segment,
    _cell,
    _primitives,
    _seg_cost,
    _step_pose,
)


def test_primitives_own_gear_are_left_straight_right_in_order() -> None:
    segs = _primitives(turn_radius_m=4.0)
    assert [s.kind for s in segs] == ["L", "S", "R"]
    # Each is a positive-length short step.
    assert all(s.length_m > 0.0 for s in segs)


def test_primitives_cart_are_pivot_straight_pivot_in_order() -> None:
    segs = _primitives(turn_radius_m=0.0)
    assert [s.kind for s in segs] == ["L", "S", "R"]
    # Pivots encode one heading cell in radians; the straight encodes metres.
    # Pin both so a length/unit swap between the two is caught.
    assert segs[0].length_m == pytest.approx(math.radians(15.0))
    assert segs[1].length_m == pytest.approx(0.5)
    assert segs[2].length_m == pytest.approx(math.radians(15.0))


def test_step_pose_straight_advances_along_heading() -> None:
    # heading 0 => +y. A straight step moves +y by its length.
    p = _step_pose(Pose(3.0, 1.0, 0.0), Segment("S", 0.5), turn_radius_m=4.0)
    assert p.x_m == pytest.approx(3.0, abs=1e-9)
    assert p.y_m == pytest.approx(1.5, abs=1e-9)
    assert p.heading_deg == pytest.approx(0.0, abs=1e-9)


def test_step_pose_cart_pivot_rotates_in_place() -> None:
    # r == 0 turn: position held, heading changes by the pivot radians.
    seg = Segment("R", math.radians(15.0))
    p = _step_pose(Pose(3.0, 1.0, 0.0), seg, turn_radius_m=0.0)
    assert p.x_m == pytest.approx(3.0, abs=1e-9)
    assert p.y_m == pytest.approx(1.0, abs=1e-9)
    # Compass CW-positive: an "R" pivot of +15 deg increases the compass heading.
    assert p.heading_deg == pytest.approx(15.0, abs=1e-6)


def test_seg_cost_counts_translation_plus_turn_penalty() -> None:
    # Straight: pure translation, no turn penalty.
    assert _seg_cost(Segment("S", 2.0), turn_radius_m=4.0) == pytest.approx(2.0)
    # r>0 turn of arc length L: translation L + penalty * (L / r) radians.
    c = _seg_cost(Segment("L", 2.0), turn_radius_m=4.0)
    assert c == pytest.approx(2.0 + 0.1 * (2.0 / 4.0))


def test_cell_bins_pose_into_grid() -> None:
    # Same 0.5 m / 15 deg cell for nearby poses; different for far ones.
    assert _cell(Pose(3.01, 1.02, 1.0)) == _cell(Pose(2.99, 0.98, 2.0))
    assert _cell(Pose(3.0, 1.0, 0.0)) != _cell(Pose(9.0, 9.0, 180.0))
    # Heading wraps: 359 deg rounds to bin 24 % 24 = 0; 1 deg rounds to bin 0.
    # Both land in bin 0.
    assert _cell(Pose(3.0, 1.0, 359.0))[2] == _cell(Pose(3.0, 1.0, 1.0))[2]
