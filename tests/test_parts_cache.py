"""Tests for the per-solve ``aircraft_parts_world`` memoization seam (#453).

The #381 profiling spike measured ``geometry.aircraft_parts_world`` as the
single cross-cutting bottleneck (83.8 % of its calls on ``roomy_three`` are
redundant rebuilds of an already-seen pose). The fix is a per-``solve()`` cache,
held in a :class:`contextvars.ContextVar`, consulted by
:func:`hangarfit.geometry.cached_parts_world` and scoped by
:func:`hangarfit.geometry.pose_cache_scope`.

These tests pin the cache's **correctness and determinism contract** — the part
the ``determinism-guard`` reviewer cares about:

* a repeated pose is a **hit** (same object back — no recompute),
* an exact-float-different pose is a **miss** (no false hit; correct geometry),
* outside a scope the wrapper is a pure passthrough (byte-identical, uncached),
* each scope owns a **fresh** dict, so two sequential solves never share state
  (this is what makes the guard's double-run byte-identical).
"""

from __future__ import annotations

from hangarfit.geometry import aircraft_parts_world, cached_parts_world, pose_cache_scope
from hangarfit.models import Aircraft, Part, Placement, Wheels


def _probe_aircraft() -> Aircraft:
    return Aircraft(
        id="probe",
        name="Probe",
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_own_gear",  # type: ignore[arg-type]
        turn_radius_m=5.0,
        measured=False,
        parts=(
            Part(
                kind="fuselage_aft",
                length_m=6.0,
                width_m=1.0,
                offset_x_m=0.0,
                offset_y_m=0.0,
                angle_deg=0.0,
                z_bottom_m=0.0,
                z_top_m=1.0,
            ),
        ),
        wheels=Wheels(main_offset_x_m=0.0, track_m=1.8, third_wheel_offset_x_m=-2.0),
    )


def _coords(parts: list) -> list:
    """Exterior coords of every part polygon — a bit-exact geometry fingerprint."""
    return [list(p.polygon.exterior.coords) for p in parts]


def test_repeated_pose_is_a_cache_hit() -> None:
    """Inside a scope, the same pose returns the *same* object (no recompute)."""
    ac = _probe_aircraft()
    pl = Placement(plane_id="probe", x_m=3.0, y_m=4.0, heading_deg=30.0, on_carts=False)
    with pose_cache_scope():
        first = cached_parts_world(ac, pl)
        second = cached_parts_world(ac, pl)
    assert second is first  # hit: identical object, geometry not rebuilt
    # and it is the same geometry the pure transform produces
    assert _coords(first) == _coords(aircraft_parts_world(ac, pl))


def test_exact_float_different_pose_is_a_miss_not_a_false_hit() -> None:
    """A pose nudged by 1e-12 is a distinct key → recomputed correct geometry."""
    ac = _probe_aircraft()
    pl = Placement(plane_id="probe", x_m=3.0, y_m=4.0, heading_deg=30.0, on_carts=False)
    pl_nudged = Placement(
        plane_id="probe", x_m=3.0 + 1e-12, y_m=4.0, heading_deg=30.0, on_carts=False
    )
    with pose_cache_scope():
        base = cached_parts_world(ac, pl)
        nudged = cached_parts_world(ac, pl_nudged)
    assert nudged is not base  # exact-float key → clean miss, never a false hit
    # the miss recomputed the *correct* geometry for the nudged pose
    assert _coords(nudged) == _coords(aircraft_parts_world(ac, pl_nudged))


def test_distinct_heading_is_a_miss() -> None:
    """heading is part of the key: a different heading does not collide."""
    ac = _probe_aircraft()
    pl = Placement(plane_id="probe", x_m=3.0, y_m=4.0, heading_deg=30.0, on_carts=False)
    pl_rot = Placement(plane_id="probe", x_m=3.0, y_m=4.0, heading_deg=31.0, on_carts=False)
    with pose_cache_scope():
        a = cached_parts_world(ac, pl)
        b = cached_parts_world(ac, pl_rot)
    assert b is not a
    assert _coords(b) == _coords(aircraft_parts_world(ac, pl_rot))


def test_passthrough_without_scope_is_pure_and_uncached() -> None:
    """No active scope → byte-identical to the pure transform, and not cached."""
    ac = _probe_aircraft()
    pl = Placement(plane_id="probe", x_m=1.0, y_m=2.0, heading_deg=45.0, on_carts=False)
    first = cached_parts_world(ac, pl)
    second = cached_parts_world(ac, pl)
    assert _coords(first) == _coords(aircraft_parts_world(ac, pl))
    # no module-global caching leaks across calls when no scope is active
    assert second is not first


def test_each_scope_owns_a_fresh_cache() -> None:
    """Two sequential scopes never share entries (the double-run determinism key)."""
    ac = _probe_aircraft()
    pl = Placement(plane_id="probe", x_m=3.0, y_m=4.0, heading_deg=30.0, on_carts=False)
    with pose_cache_scope():
        first = cached_parts_world(ac, pl)
    with pose_cache_scope():
        second = cached_parts_world(ac, pl)
    assert second is not first  # fresh dict per scope → recomputed, no carryover


def test_scope_resets_to_passthrough_on_exit() -> None:
    """After a scope exits, the wrapper returns to pure passthrough (cache gone)."""
    ac = _probe_aircraft()
    pl = Placement(plane_id="probe", x_m=3.0, y_m=4.0, heading_deg=30.0, on_carts=False)
    with pose_cache_scope():
        cached_parts_world(ac, pl)
    after_a = cached_parts_world(ac, pl)
    after_b = cached_parts_world(ac, pl)
    assert after_b is not after_a  # no active cache → uncached passthrough
