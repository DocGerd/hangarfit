"""Tiny shared fixtures for ml tests — a minimal real Aircraft + Hangar."""

from __future__ import annotations

from hangarfit.loader import load_fleet, load_hangar
from hangarfit.models import Layout, Placement


def _fuji():
    fleet = load_fleet("data/fleet.yaml")
    return fleet


def empty_hangar():
    # The synthetic placeholder hangar is fine for unit geometry; apron set so the
    # env has a spawn region.
    from dataclasses import replace

    h = load_hangar("data/hangar.yaml")
    return replace(h, apron_depth_m=8.0)


def single_object_layout(*, x_m: float, y_m: float, heading_deg: float = 0.0):
    fleet = _fuji()
    # Use fuji (always_own_gear) so on_carts=False is always valid.
    pid = "fuji"
    body = fleet[pid]
    on_carts = body.movement_mode == "always_cart"
    return Layout(
        fleet={pid: body},
        hangar=empty_hangar(),
        placements=(
            Placement(plane_id=pid, x_m=x_m, y_m=y_m, heading_deg=heading_deg, on_carts=on_carts),
        ),
    )


def two_object_layout(*, parked_y_m: float, active_y_m: float, x_m: float = 5.0):
    """A 2-body layout: a parked ``fuji`` plus an ``aviat_husky`` placed behind it.

    Both are ``always_own_gear`` (on_carts=False is always valid). The active
    husky is positioned so a straight forward sweep drives it into the parked
    fuji's footprint — used to exercise the swept-intrusion gradient path.
    Returns ``(layout, active_body, active_id)``.
    """
    fleet = _fuji()
    parked, active = fleet["fuji"], fleet["aviat_husky"]
    layout = Layout(
        fleet={"fuji": parked, "aviat_husky": active},
        hangar=empty_hangar(),
        placements=(
            Placement(plane_id="fuji", x_m=x_m, y_m=parked_y_m, heading_deg=0.0, on_carts=False),
            Placement(
                plane_id="aviat_husky", x_m=x_m, y_m=active_y_m, heading_deg=0.0, on_carts=False
            ),
        ),
    )
    return layout, active, "aviat_husky"
