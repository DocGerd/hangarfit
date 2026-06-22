"""#643: a separate, tighter tow-MOTION clearance, distinct from the PARKED
clearance used by the static checker.

The tow planner threads a mover past parked bodies at a hand-realistic motion
margin (spotters clear wingtips far tighter than the parked spacing), while
``collisions.check`` keeps the parked ``clearance_m`` for static validity. When
the motion fields are unset the motion clearance IS the parked clearance, so the
plan is byte-identical to today (ADR-0003).
"""

from __future__ import annotations

import pytest

from hangarfit.models import (
    Aircraft,
    Door,
    Hangar,
    Layout,
    MaintenanceBay,
    Part,
    Placement,
    Wheels,
)
from hangarfit.towplanner import Pose, path_first_conflict, plan_dubins

_WHEELS = Wheels(main_offset_x_m=0.20, track_m=1.0, third_wheel_offset_x_m=-1.0)


def _box_plane(pid: str, *, always_cart: bool = False) -> Aircraft:
    """A minimal plane: one 1.0 m × 0.6 m fuselage box, mounted forward."""
    return Aircraft(
        id=pid,
        name=pid,
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_cart" if always_cart else "always_own_gear",
        turn_radius_m=None if always_cart else 3.0,
        measured=False,
        parts=(
            Part(
                kind="fuselage_aft",
                length_m=1.0,
                width_m=0.6,
                offset_x_m=0.5,
                offset_y_m=0.0,
                angle_deg=0.0,
                z_bottom_m=0.0,
                z_top_m=1.0,
            ),
        ),
        wheels=_WHEELS,
    )


def _hangar(**overrides: object) -> Hangar:
    kwargs: dict[str, object] = dict(
        length_m=30.0,
        width_m=20.0,
        door=Door(center_x_m=10.0, width_m=6.0),
        maintenance_bay=MaintenanceBay(center_x_m=10.0, width_m=2.0, depth_m=2.0),
        clearance_m=0.3,
        wing_layer_clearance_m=0.2,
    )
    kwargs.update(overrides)
    return Hangar(**kwargs)  # type: ignore[arg-type]


def test_motion_hangar_is_self_when_unset() -> None:
    """With no motion clearance set, the tow-motion hangar IS the parked hangar
    (identity) — so nothing about the plan changes (byte-identical default)."""
    h = _hangar()
    assert h.motion_clearance_m is None
    assert h.motion_hangar() is h


def test_motion_hangar_folds_motion_clearance_into_parked_clearance() -> None:
    """With a tighter motion clearance set, the motion hangar's PARKED clearance
    fields ARE the motion values (so collisions.check applies them per pose),
    while the original hangar keeps its parked spacing."""
    h = _hangar(
        clearance_m=0.30,
        wing_layer_clearance_m=0.20,
        motion_clearance_m=0.05,
        motion_wing_layer_clearance_m=0.04,
    )
    m = h.motion_hangar()
    assert m is not h
    assert m.clearance_m == 0.05
    assert m.wing_layer_clearance_m == 0.04
    # the motion fields are folded away on the returned (parked-style) hangar
    assert m.motion_clearance_m is None
    assert m.motion_wing_layer_clearance_m is None
    # the original hangar is unchanged — static checks still use parked spacing
    assert h.clearance_m == 0.30 and h.wing_layer_clearance_m == 0.20


def test_motion_clearance_negative_rejected() -> None:
    """A negative motion clearance is a construction error (mirrors clearance_m)."""
    with pytest.raises(ValueError, match="motion_clearance_m must be non-negative"):
        _hangar(motion_clearance_m=-0.1)


# --- the behaviour: the tow-MOTION oracle (``path_first_conflict``) applies the
# motion clearance, not the parked one. A mover drives straight past a parked
# obstacle with a ~0.10 m part gap — flagged at the 0.5 m parked clearance, clear
# at a 0.05 m motion clearance. Direct-on-the-oracle (no A* search) ⇒ fast.


def _pass_hangar(**overrides: object) -> Hangar:
    kwargs: dict[str, object] = dict(
        length_m=16.0,
        width_m=3.0,
        door=Door(center_x_m=1.5, width_m=3.0),  # whole front: the y>=0 pass needs no door gate
        maintenance_bay=MaintenanceBay(center_x_m=2.7, width_m=0.2, depth_m=0.2),
        clearance_m=0.5,
        wing_layer_clearance_m=0.3,
    )
    kwargs.update(overrides)
    return Hangar(**kwargs)  # type: ignore[arg-type]


def _close_pass(hangar: Hangar):
    """Mover B drives straight in at x=0.5 past obstacle A (x in [0.9, 1.5]) — a
    ~0.10 m gap at the closest point. Returns the first conflict (or None)."""
    a = _box_plane("A")
    b = _box_plane("B", always_cart=True)
    placed = Layout(
        fleet={"A": a, "B": b},
        hangar=hangar,
        placements=(Placement("A", x_m=1.2, y_m=6.0, heading_deg=0.0, on_carts=False),),
    )
    arc = plan_dubins(Pose(0.5, 0.0, 0.0), Pose(0.5, 12.0, 0.0), turn_radius_m=0.0)
    return path_first_conflict(arc, b, mover_on_carts=True, placed=placed)


def test_parked_clearance_flags_the_close_pass() -> None:
    """Sanity: at the 0.5 m parked clearance the close pass is a conflict (so the
    motion-clearance test below proves a real behaviour change)."""
    conflict = _close_pass(_pass_hangar())
    assert conflict is not None
    assert "B" in conflict.planes and "A" in conflict.planes


def test_tighter_motion_clearance_allows_the_close_pass() -> None:
    """The point of #643: at a 0.05 m tow-MOTION clearance the same pass is clean,
    even though the parked clearance stays 0.5 m for static validity."""
    conflict = _close_pass(
        _pass_hangar(motion_clearance_m=0.05, motion_wing_layer_clearance_m=0.05)
    )
    assert conflict is None


_HANGAR_YAML = (
    "length_m: 30\nwidth_m: 20\n"
    "door: {center_x_m: 10, width_m: 6}\n"
    "maintenance_bay: {center_x_m: 10, width_m: 2, depth_m: 2}\n"
    "clearance_m: 0.3\nwing_layer_clearance_m: 0.2\n"
)


def test_load_hangar_parses_motion_clearance(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from hangarfit.loader import load_hangar

    p = tmp_path / "hangar.yaml"
    p.write_text(_HANGAR_YAML + "motion_clearance_m: 0.05\nmotion_wing_layer_clearance_m: 0.04\n")
    h = load_hangar(p)
    assert h.motion_clearance_m == 0.05
    assert h.motion_wing_layer_clearance_m == 0.04
    assert h.motion_hangar().clearance_m == 0.05


def test_load_hangar_motion_clearance_absent_is_none(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from hangarfit.loader import load_hangar

    p = tmp_path / "hangar.yaml"
    p.write_text(_HANGAR_YAML)
    h = load_hangar(p)
    assert h.motion_clearance_m is None
    assert h.motion_hangar() is h
