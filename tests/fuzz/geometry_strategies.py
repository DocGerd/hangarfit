"""Hypothesis strategies + run-helpers for fuzzing the geometry transform and
the collision checker (#355 Part B).

Companion to ``strategies.py`` (which fuzzes the YAML *loader*). That module
feeds malformed *documents* and asserts the loader either returns a model or
raises ``LoaderError``. This one is different in kind: it builds **valid** model
objects (via the constructors, which enforce every invariant) and drives the
pure geometry/collision functions, asserting they never crash **and** that their
structural invariants hold. There is no "expected exception" here — the inputs
are valid by construction, so any exception is a finding, as is any invariant
violation.

Shared by the pytest property suite (``test_geometry_fuzz.py``) and the Atheris
bridge harness (``atheris_geometry_harness.py``) so input construction lives in
exactly one place — mirroring the loader-fuzz split.

The oracles, per target:

- :func:`hangarfit.geometry.local_to_world` — finite in ⇒ finite out;
  deterministic; the linear part is an isometry with determinant −1 (ADR-0002).
- :func:`hangarfit.geometry.aircraft_parts_world` — one ``WorldPart`` per source
  part, in order; area preserved (|det| = 1); z-range / kind / plane_id
  preserved; deterministic.
- :func:`hangarfit.collisions.check` — returns a ``CheckResult`` with
  ``total_penetration_m2 >= 0``; deterministic; **order-independent** (the
  conflict multiset and penetration do not depend on placement iteration order).
"""

from __future__ import annotations

import math
from typing import Any

from hypothesis import strategies as st

from hangarfit.collisions import check as check_layout
from hangarfit.geometry import aircraft_parts_world, local_to_world
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

# --- primitive strategies -------------------------------------------------
# Finite and reasonably bounded: the goal is coverage-guided search over OUR
# transform/checker logic, not probing how GEOS handles ±inf coordinates (a
# shapely concern, not ours — and a source of false findings). Headings range
# well outside [0, 360) to exercise the sin/cos wrap.
_PART_KINDS = ["fuselage_front", "fuselage_aft", "wing", "strut", "tail"]
_coord = st.floats(min_value=-60.0, max_value=60.0, allow_nan=False, allow_infinity=False)
_dim = st.floats(min_value=0.05, max_value=25.0, allow_nan=False, allow_infinity=False)
_heading = st.floats(min_value=-1080.0, max_value=1080.0, allow_nan=False, allow_infinity=False)
_angle = st.floats(min_value=-360.0, max_value=360.0, allow_nan=False, allow_infinity=False)
_local = st.floats(min_value=-40.0, max_value=40.0, allow_nan=False, allow_infinity=False)


@st.composite
def _part(draw: Any) -> Part:
    """A valid :class:`Part`: positive dims, ``0 <= z_bottom < z_top``."""
    z_bottom = draw(st.floats(min_value=0.0, max_value=8.0, allow_nan=False, allow_infinity=False))
    z_top = z_bottom + draw(
        st.floats(min_value=0.01, max_value=4.0, allow_nan=False, allow_infinity=False)
    )
    return Part(
        kind=draw(st.sampled_from(_PART_KINDS)),
        length_m=draw(_dim),
        width_m=draw(_dim),
        offset_x_m=draw(_coord),
        offset_y_m=draw(_coord),
        angle_deg=draw(_angle),
        z_bottom_m=z_bottom,
        z_top_m=z_top,
    )


@st.composite
def _wheels(draw: Any) -> Wheels:
    """A valid :class:`Wheels`: tricycle/tailwheel (both set) or monowheel (both None)."""
    if draw(st.booleans()):
        return Wheels(
            main_offset_x_m=draw(_coord),
            track_m=draw(st.floats(min_value=0.5, max_value=4.0, allow_nan=False)),
            third_wheel_offset_x_m=draw(_coord),
        )
    return Wheels(main_offset_x_m=draw(_coord), track_m=None, third_wheel_offset_x_m=None)


@st.composite
def _aircraft(draw: Any, plane_id: str) -> Aircraft:
    """A valid :class:`Aircraft` with 1–4 parts. ``always_own_gear`` so the
    Layout cart-rule never rejects it — this fuzzer targets geometry, not the
    cart invariants (those are covered by the loader fuzz suite)."""
    parts = tuple(draw(st.lists(_part(), min_size=1, max_size=4)))
    return Aircraft(
        id=plane_id,
        name=plane_id.upper(),
        wing_position=draw(st.sampled_from(["high", "mid", "low"])),
        gear=draw(st.sampled_from(["tailwheel", "nosewheel", "monowheel"])),
        movement_mode="always_own_gear",
        turn_radius_m=draw(st.floats(min_value=1.0, max_value=20.0, allow_nan=False)),
        measured=False,
        parts=parts,
        wheels=draw(_wheels()),
    )


@st.composite
def _placement(draw: Any, plane_id: str) -> Placement:
    return Placement(
        plane_id=plane_id,
        x_m=draw(_coord),
        y_m=draw(_coord),
        heading_deg=draw(_heading),
        on_carts=False,
    )


# A roomy hangar so bounds conflicts are possible but not the only outcome; the
# checker is exercised regardless of how many parts fall outside it.
_FUZZ_HANGAR = Hangar(
    length_m=60.0,
    width_m=60.0,
    door=Door(center_x_m=30.0, width_m=10.0),
    maintenance_bay=MaintenanceBay(center_x_m=30.0, width_m=8.0, depth_m=6.0),
    clearance_m=0.3,
    wing_layer_clearance_m=0.2,
)


# --- top-level strategies -------------------------------------------------


@st.composite
def local_to_world_inputs(draw: Any) -> tuple[float, float, Placement]:
    return (draw(_local), draw(_local), draw(_placement("probe")))


@st.composite
def aircraft_world_inputs(draw: Any) -> tuple[Aircraft, Placement]:
    return (draw(_aircraft("probe")), draw(_placement("probe")))


@st.composite
def layout_inputs(draw: Any) -> Layout:
    """A valid multi-aircraft :class:`Layout` (1–4 planes), optionally with a
    maintenance occupant (which is in the fleet but absent from placements, per
    the Layout invariant)."""
    n = draw(st.integers(min_value=1, max_value=4))
    ids = [f"p{i}" for i in range(n)]
    fleet = {pid: draw(_aircraft(pid)) for pid in ids}
    # Optionally close the bay: pick an occupant that is NOT placed.
    maintenance_plane = None
    placeable = list(ids)
    if n >= 2 and draw(st.booleans()):
        maintenance_plane = draw(st.sampled_from(ids))
        placeable = [pid for pid in ids if pid != maintenance_plane]
    placements = tuple(draw(_placement(pid)) for pid in placeable)
    return Layout(
        fleet=fleet,
        hangar=_FUZZ_HANGAR,
        placements=placements,
        maintenance_plane=maintenance_plane,
    )


# --- run-helpers (the oracles) --------------------------------------------


def run_local_to_world(inputs: tuple[float, float, Placement]) -> None:
    u, v, placement = inputs
    wx, wy = local_to_world(u, v, placement)
    assert math.isfinite(wx) and math.isfinite(wy), f"non-finite world point ({wx}, {wy})"
    # Determinism.
    assert local_to_world(u, v, placement) == (wx, wy)
    # The linear part is an isometry with determinant −1 (ADR-0002): the images
    # of the plane-local basis vectors, relative to the placement origin, form a
    # det=−1 matrix. Use a probe placement at the same heading but at the origin
    # to isolate the linear part.
    origin = Placement(
        plane_id="probe", x_m=0.0, y_m=0.0, heading_deg=placement.heading_deg, on_carts=False
    )
    ax, ay = local_to_world(1.0, 0.0, origin)
    bx, by = local_to_world(0.0, 1.0, origin)
    det = ax * by - ay * bx
    assert math.isclose(det, -1.0, rel_tol=1e-9, abs_tol=1e-9), f"determinant {det} != -1"


def run_aircraft_parts_world(inputs: tuple[Aircraft, Placement]) -> None:
    aircraft, placement = inputs
    world = aircraft_parts_world(aircraft, placement)
    assert len(world) == len(aircraft.parts), "part count not preserved"
    for src, dst in zip(aircraft.parts, world, strict=True):
        # Area is preserved by a |det|=1 map (ADR-0002). A tiny part can have a
        # tiny area, so use a relative tolerance with an absolute floor.
        expected = src.length_m * src.width_m
        assert math.isclose(dst.polygon.area, expected, rel_tol=1e-7, abs_tol=1e-9), (
            f"area {dst.polygon.area} != {expected} for {src.kind}"
        )
        assert dst.polygon.is_valid, f"invalid polygon for {src.kind}"
        # Metadata carried through untouched.
        assert dst.kind == src.kind
        assert dst.z_bottom_m == src.z_bottom_m
        assert dst.z_top_m == src.z_top_m
        assert dst.plane_id == placement.plane_id
    # Determinism: same inputs ⇒ identical world coordinates.
    again = aircraft_parts_world(aircraft, placement)
    assert [w.polygon.bounds for w in again] == [w.polygon.bounds for w in world]


def run_collisions_check(layout: Layout) -> None:
    result = check_layout(layout)
    assert result.total_penetration_m2 >= 0.0, "negative penetration"
    assert all(1 <= len(c.planes) <= 2 for c in result.conflicts), "conflict has bad plane count"
    # Determinism.
    again = check_layout(layout)
    assert sorted(c.kind for c in result.conflicts) == sorted(c.kind for c in again.conflicts)
    assert result.total_penetration_m2 == again.total_penetration_m2
    # Order independence: reversing the placement order must not change the
    # conflict multiset or the accumulated penetration (the alphabetised kind
    # taxonomy in _pairwise_conflicts exists to guarantee this).
    if len(layout.placements) >= 2:
        reversed_layout = Layout(
            fleet=layout.fleet,
            hangar=layout.hangar,
            placements=tuple(reversed(layout.placements)),
            maintenance_plane=layout.maintenance_plane,
        )
        rev = check_layout(reversed_layout)
        assert sorted(c.kind for c in result.conflicts) == sorted(c.kind for c in rev.conflicts), (
            "conflict multiset depends on placement order"
        )
        assert math.isclose(
            result.total_penetration_m2, rev.total_penetration_m2, rel_tol=1e-9, abs_tol=1e-9
        ), "penetration depends on placement order"


# --- tagged union (mirrors strategies.tagged_documents / run_tagged) ------

_RUNNERS = {
    "local_to_world": run_local_to_world,
    "aircraft_parts_world": run_aircraft_parts_world,
    "collisions_check": run_collisions_check,
}


def geometry_tagged_documents() -> st.SearchStrategy[tuple[str, Any]]:
    return st.one_of(
        st.tuples(st.just("local_to_world"), local_to_world_inputs()),
        st.tuples(st.just("aircraft_parts_world"), aircraft_world_inputs()),
        st.tuples(st.just("collisions_check"), layout_inputs()),
    )


def run_geometry_tagged(tagged: tuple[str, Any]) -> None:
    tag, payload = tagged
    _RUNNERS[tag](payload)
