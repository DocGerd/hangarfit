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
