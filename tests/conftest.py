"""Project-wide pytest helpers for hangarfit tests.

Kept deliberately lazy: no module-level model construction. The 6-plane
solver determinism canary uses a wall-clock budget (``budget_s=5.0``) and
even a few milliseconds of extra pytest-collection cost can perturb the
two runs into producing different layout counts. So every helper here
constructs its objects inside its own body, on demand.
"""

from __future__ import annotations

from typing import Any

from hangarfit.models import Aircraft, Gear, Part, Wheels


def _default_parts() -> tuple[Part, ...]:
    """Minimal valid parts tuple for a test aircraft. Built on demand to
    keep pytest collection cheap (see module docstring)."""
    return (
        Part(
            kind="fuselage_front",
            length_m=1.0,
            width_m=0.5,
            offset_x_m=0.5,
            offset_y_m=0.0,
            angle_deg=0.0,
            z_bottom_m=0.0,
            z_top_m=1.0,
        ),
        Part(
            kind="fuselage_aft",
            length_m=1.0,
            width_m=0.5,
            offset_x_m=-0.5,
            offset_y_m=0.0,
            angle_deg=0.0,
            z_bottom_m=0.0,
            z_top_m=1.0,
        ),
        Part(
            kind="wing",
            length_m=0.5,
            width_m=4.0,
            offset_x_m=0.0,
            offset_y_m=0.0,
            angle_deg=0.0,
            z_bottom_m=2.0,
            z_top_m=2.2,
        ),
    )


def default_wheels_for(gear: Gear) -> Wheels:
    """Sensible default Wheels for each gear type — wheelbase 2.0 m,
    satisfies the 0.5×–5× cross-check against ``turn_radius_m=4.0``."""
    if gear == "monowheel":
        return Wheels(main_offset_x_m=0.0, track_m=None, third_wheel_offset_x_m=None)
    if gear == "tailwheel":
        return Wheels(main_offset_x_m=0.0, track_m=1.8, third_wheel_offset_x_m=-2.0)
    return Wheels(main_offset_x_m=0.0, track_m=1.8, third_wheel_offset_x_m=2.0)


def make_test_aircraft(
    *,
    id: str = "test_plane",
    name: str = "Test",
    wing_position: str = "high",
    gear: Gear = "nosewheel",
    movement_mode: str = "always_own_gear",
    turn_radius_m: float | None = 4.0,
    measured: bool = False,
    parts: tuple[Part, ...] | None = None,
    notes: str = "",
    wheels: Wheels | None = None,
    **overrides: Any,
) -> Aircraft:
    """Build a minimal valid Aircraft for tests.

    ``wheels`` defaults to a gear-appropriate Wheels via :func:`default_wheels_for`
    when not supplied. ``**overrides`` are forwarded as additional Aircraft kwargs.
    """
    if wheels is None:
        wheels = default_wheels_for(gear)
    if parts is None:
        parts = _default_parts()
    return Aircraft(
        id=id,
        name=name,
        wing_position=wing_position,  # type: ignore[arg-type]
        gear=gear,
        movement_mode=movement_mode,  # type: ignore[arg-type]
        turn_radius_m=turn_radius_m,
        measured=measured,
        parts=parts,
        notes=notes,
        wheels=wheels,
        **overrides,
    )
