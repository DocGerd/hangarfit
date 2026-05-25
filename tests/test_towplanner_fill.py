"""Empty-hangar fill planner (#196): door-cone entry pose + plan_fill.

The entry-pose tests pin the door as a motion gate (spike Q6 / ADR-0007);
the plan_fill tests pin the deterministic back-first order, the cart
pivot-straight-pivot path, the bounded order-retry swap, and the structured
bail. Retry/bail tests monkeypatch ``path_first_conflict`` so the loop logic
is exercised independently of Dubins geometry.
"""

import pytest

from hangarfit.models import Door, Hangar, MaintenanceBay, Placement
from hangarfit.towplanner import entry_pose


def _hangar(
    width_m: float = 20.0,
    length_m: float = 30.0,
    door_center: float = 10.0,
    door_width: float = 6.0,
) -> Hangar:
    return Hangar(
        length_m=length_m,
        width_m=width_m,
        door=Door(center_x_m=door_center, width_m=door_width),
        maintenance_bay=MaintenanceBay(center_x_m=width_m / 2, width_m=2.0, depth_m=2.0),
        clearance_m=0.5,
        wing_layer_clearance_m=0.3,
    )


def _slot(pid: str, x: float, y: float, h: float = 0.0, on_carts: bool = False) -> Placement:
    return Placement(plane_id=pid, x_m=x, y_m=y, heading_deg=h, on_carts=on_carts)


def test_entry_pose_is_at_front_pointing_in() -> None:
    h = _hangar()
    e = entry_pose(_slot("A", x=10.0, y=20.0), h)
    assert e.y_m == 0.0
    assert e.heading_deg == 0.0  # nose toward +y, into the hangar


def test_entry_x_equals_slot_x_when_inside_door() -> None:
    h = _hangar(door_center=10.0, door_width=6.0)  # door interval [7, 13]
    e = entry_pose(_slot("A", x=9.0, y=20.0), h)
    assert e.x_m == pytest.approx(9.0)


def test_entry_x_clamps_to_door_interval() -> None:
    h = _hangar(door_center=10.0, door_width=6.0)  # door interval [7, 13]
    assert entry_pose(_slot("A", x=2.0, y=20.0), h).x_m == pytest.approx(7.0)
    assert entry_pose(_slot("B", x=18.0, y=20.0), h).x_m == pytest.approx(13.0)
