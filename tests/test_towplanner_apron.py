"""Staging apron (#412 / ADR-0021): apron-pose grid, apron-aware bounds rule,
apron-started fill, byte-identity at depth 0, and the reverse-into-apron sign
canary.

The apron is gated on ``hangar.apron_depth_m > 0``: at depth 0 every changed
planner function executes its pre-apron path verbatim, so the no-apron
``MovesPlan`` is byte-identical (ADR-0003). These tests pin both sides of that
gate — the depth-0 reproduction and the depth>0 apron behaviour.

Fixture builders are module-local on purpose (mirroring the other towplanner
test files); the box plane's fuselage is mounted forward (offset_x_m=0.5,
length_m=1.0 ⇒ at heading 0 the body occupies world y ∈ [ref, ref + 1]).
"""

import pytest

from hangarfit.models import Aircraft, Door, Hangar, MaintenanceBay, Part, Placement, Wheels
from hangarfit.towplanner import derive_apron_depth

_TAIL_WHEELS = Wheels(main_offset_x_m=0.20, track_m=1.8, third_wheel_offset_x_m=-2.0)


def _fuselage_box() -> Part:
    """A 1.0 m × 0.6 m fuselage box mounted forward of the plane origin, so a
    placement at the front wall (y = 0) keeps every world vertex at y >= 0."""
    return Part(
        kind="fuselage_aft",
        length_m=1.0,
        width_m=0.6,
        offset_x_m=0.5,
        offset_y_m=0.0,
        angle_deg=0.0,
        z_bottom_m=0.0,
        z_top_m=1.0,
    )


def _box_plane(plane_id: str, *, turn_radius_m: float = 4.0) -> Aircraft:
    """A minimal own-gear plane (one fuselage box)."""
    return Aircraft(
        id=plane_id,
        name=f"Plane {plane_id}",
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_own_gear",
        turn_radius_m=turn_radius_m,
        measured=False,
        parts=(_fuselage_box(),),
        wheels=_TAIL_WHEELS,
    )


def _hangar(
    width_m: float = 20.0,
    length_m: float = 30.0,
    door_center: float = 10.0,
    door_width: float = 6.0,
    apron_depth_m: float = 0.0,
) -> Hangar:
    return Hangar(
        length_m=length_m,
        width_m=width_m,
        door=Door(center_x_m=door_center, width_m=door_width),
        maintenance_bay=MaintenanceBay(center_x_m=width_m / 2, width_m=2.0, depth_m=2.0),
        clearance_m=0.5,
        wing_layer_clearance_m=0.3,
        apron_depth_m=apron_depth_m,
    )


def _slot(pid: str, x: float, y: float, h: float = 0.0, on_carts: bool = False) -> Placement:
    return Placement(plane_id=pid, x_m=x, y_m=y, heading_deg=h, on_carts=on_carts)


def _layout(fleet: dict[str, Aircraft], hangar: Hangar, *placements: Placement):
    from hangarfit.models import Layout

    return Layout(fleet=fleet, hangar=hangar, placements=tuple(placements))


# ── Task 2: derive_apron_depth (the opt-in 'auto' value) ─────────────────────


def test_derive_apron_depth_is_max_length_plus_max_turn_radius() -> None:
    # _box_plane fuselage: offset_x_m=0.5, length_m=1.0 ⇒ fore-aft extent [0, 1] = 1.0 m.
    fleet = {"A": _box_plane("A", turn_radius_m=4.0), "B": _box_plane("B", turn_radius_m=6.0)}
    assert derive_apron_depth(fleet) == pytest.approx(1.0 + 6.0)


def test_derive_apron_depth_empty_fleet_is_zero() -> None:
    assert derive_apron_depth({}) == 0.0
