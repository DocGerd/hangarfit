"""Tests for the #550 fuselage outline polygon capability.

A `kind: fuselage` part may carry a raw `vertices:` outline that the loader
clips into area-conserving `fuselage_front`/`fuselage_aft` sub-polygons at the
wing trailing edge (capability-only; the real fleet stays byte-identical). See
`docs/superpowers/specs/2026-06-27-fuselage-outline-polygon-design.md`.
"""

import math
from pathlib import Path

import pytest
from shapely.geometry import Polygon

from hangarfit.collisions import check
from hangarfit.geometry import aircraft_parts_world
from hangarfit.loader import (
    LoaderError,
    _build_aircraft,
    _build_part,
    _split_fuselage,
    load_fleet,
    load_layout,
)
from hangarfit.models import Part, Placement

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _fuselage_outline_dict(**over):
    # A simple symmetric tapered outline in the part's own centred frame,
    # within +/- length/2 (=2.0) x +/- width/2 (=0.5). Pointed nose at +x.
    d = {
        "kind": "fuselage_aft",  # the loader's placeholder rename for `kind: fuselage`
        "length_m": 4.0,
        "width_m": 1.0,
        "z_bottom_m": 0.3,
        "z_top_m": 1.4,
        "vertices": [[2.0, 0.0], [0.5, 0.5], [-2.0, 0.5], [-2.0, -0.5], [0.5, -0.5]],
    }
    d.update(over)
    return d


def test_vertices_key_sets_local_vertices():
    part = _build_part(_fuselage_outline_dict(), 0)
    assert part.local_vertices is not None
    assert len(part.local_vertices) == 5


def test_vertices_and_planform_mutually_exclusive():
    d = _fuselage_outline_dict(kind="wing", planform={"root_chord_m": 1.0, "tip_chord_m": 0.5})
    with pytest.raises(LoaderError, match="mutually exclusive|both"):
        _build_part(d, 0)


def test_vertices_rejected_on_non_fuselage_kind():
    d = _fuselage_outline_dict(kind="wing")
    with pytest.raises(LoaderError, match="vertices"):
        _build_part(d, 0)


# --- Task 2: the _split_fuselage polygon clip ---


def _outline_fuselage(angle_deg=0.0):
    # Tapered tube: pointed nose at +x=2, full-width cabin to tail at x=-2.
    return Part(
        kind="fuselage_aft",
        length_m=4.0,
        width_m=1.0,
        offset_x_m=0.0,
        offset_y_m=0.0,
        angle_deg=angle_deg,
        z_bottom_m=0.3,
        z_top_m=1.4,
        local_vertices=((2.0, 0.0), (0.5, 0.5), (-2.0, 0.5), (-2.0, -0.5), (0.5, -0.5)),
    )


def _wing_at(te_x):
    # wing trailing edge = offset_x - length/2; pick offset/length to land TE at te_x
    return Part(
        kind="wing",
        length_m=1.0,
        width_m=8.0,
        offset_x_m=te_x + 0.5,
        offset_y_m=0.0,
        angle_deg=0.0,
        z_bottom_m=1.4,
        z_top_m=1.7,
    )


def _ring_world(part):
    # part-own centred ring shifted to plane-local (angle 0): (offset+vx, offset+vy)
    return Polygon([(part.offset_x_m + x, part.offset_y_m + y) for x, y in part.local_vertices])


def test_clip_produces_front_and_aft_polygons():
    fus = _outline_fuselage()
    parts = _split_fuselage(fus, _wing_at(0.0))  # break at plane-local x=0
    assert {p.kind for p in parts} == {"fuselage_front", "fuselage_aft"}
    for p in parts:
        assert p.local_vertices is not None


def test_clip_is_area_conserving_and_abutting():
    fus = _outline_fuselage()
    front, aft = sorted(
        _split_fuselage(fus, _wing_at(0.0)), key=lambda p: p.offset_x_m, reverse=True
    )
    orig = _ring_world(fus).area
    assert math.isclose(_ring_world(front).area + _ring_world(aft).area, orig, rel_tol=1e-9)
    # front is the nose side (greater plane-local x), aft the tail side
    assert front.offset_x_m > aft.offset_x_m


def test_clip_rejects_break_outside_span():
    with pytest.raises(LoaderError, match="strictly inside|span"):
        _split_fuselage(_outline_fuselage(), _wing_at(5.0))  # TE beyond the nose


def test_clip_rejects_rotated_polygon_fuselage():
    with pytest.raises(LoaderError, match="axis-aligned|angle_deg"):
        _split_fuselage(_outline_fuselage(angle_deg=10.0), _wing_at(0.0))


def test_clip_rejects_non_x_monotone_outline():
    # A concave "C" opening toward +x: a vertical cut at x=0 leaves two
    # disconnected nose-side arms -> MultiPolygon -> rejected (the "front is
    # genuinely the cockpit" guard). Simple, non-self-intersecting, fits +/-2 x +/-0.5.
    cshape = Part(
        kind="fuselage_aft",
        length_m=4.0,
        width_m=1.0,
        offset_x_m=0.0,
        offset_y_m=0.0,
        angle_deg=0.0,
        z_bottom_m=0.3,
        z_top_m=1.4,
        local_vertices=(
            (2.0, 0.5),
            (-2.0, 0.5),
            (-2.0, -0.5),
            (2.0, -0.5),
            (2.0, -0.3),
            (-1.5, -0.3),
            (-1.5, 0.3),
            (2.0, 0.3),
        ),
    )
    with pytest.raises(LoaderError, match="single|x-monotone|piece"):
        _split_fuselage(cshape, _wing_at(0.0))


# --- Task 3: integration build, determinism, byte-identical-fleet guard ---


def _outline_entry():
    # Minimal aircraft entry with a tapered fuselage outline + a wing whose
    # trailing edge (offset_x - length/2 = 0.6 - 0.7 = -0.1) lands inside the
    # fuselage x-span [-3, 3]. Required entry keys: id, name, wing_position,
    # gear, movement_mode, parts.
    return {
        "id": "outline_test",
        "name": "Outline Test Plane",
        "wing_position": "high",
        "gear": "nosewheel",
        "movement_mode": "cart_eligible",
        "turn_radius_m": 8.0,
        "wheels": {"main_offset_x_m": -0.5, "track_m": 2.0, "third_wheel_offset_x_m": 1.5},
        "parts": [
            {
                "kind": "wing",
                "length_m": 1.4,
                "width_m": 9.0,
                "offset_x_m": 0.6,
                "z_bottom_m": 1.7,
                "z_top_m": 2.0,
            },
            {
                "kind": "fuselage",
                "length_m": 6.0,
                "width_m": 1.2,
                "offset_x_m": 0.0,
                "z_bottom_m": 0.3,
                "z_top_m": 1.6,
                "vertices": [[3.0, 0.0], [1.0, 0.6], [-3.0, 0.6], [-3.0, -0.6], [1.0, -0.6]],
            },
        ],
    }


def test_outline_aircraft_builds_polygon_front_aft():
    ac = _build_aircraft(_outline_entry())
    by_kind = {p.kind: p for p in ac.parts}
    assert by_kind["fuselage_front"].local_vertices is not None
    assert by_kind["fuselage_aft"].local_vertices is not None


def test_outline_build_is_deterministic():
    a = _build_aircraft(_outline_entry())
    b = _build_aircraft(_outline_entry())
    fa = next(p for p in a.parts if p.kind == "fuselage_front")
    fb = next(p for p in b.parts if p.kind == "fuselage_front")
    assert fa.local_vertices == fb.local_vertices


def test_no_shipped_fuselage_part_is_a_polygon():
    # byte-identical-fleet guard: every real fleet fuselage stays a scalar box
    # (the scalar split produces fuselage_front/aft with local_vertices is None).
    fleet = load_fleet(Path(__file__).resolve().parents[1] / "data" / "fleet.yaml")
    for ac in fleet.values():
        for p in ac.parts:
            if p.kind in ("fuselage_front", "fuselage_aft"):
                assert p.local_vertices is None, f"{ac.id} {p.kind} became a polygon"


# --- Task 4: downstream behavior (det(-1) transform + collision semantics) ---


def _overflyer_entry():
    # A high-winger whose broad wing (z [1.7, 2.0], above the fuselage z [0.3, 1.6])
    # sits over the outline plane's cockpit (fuselage_front).
    return {
        "id": "overflyer",
        "name": "Overflyer",
        "wing_position": "high",
        "gear": "nosewheel",
        "movement_mode": "cart_eligible",
        "turn_radius_m": 8.0,
        "wheels": {"main_offset_x_m": -0.5, "track_m": 2.0, "third_wheel_offset_x_m": 1.5},
        "parts": [
            {
                "kind": "wing",
                "length_m": 6.0,
                "width_m": 9.0,
                "offset_x_m": 1.5,
                "z_bottom_m": 1.7,
                "z_top_m": 2.0,
            },
            {
                "kind": "fuselage",
                "length_m": 6.0,
                "width_m": 1.2,
                "offset_x_m": 0.0,
                "z_bottom_m": 0.3,
                "z_top_m": 1.6,
            },
        ],
    }


def test_clipped_front_flows_through_det_minus_one_transform():
    ac = _build_aircraft(_outline_entry())
    pl = Placement(plane_id="outline_test", x_m=10.0, y_m=5.0, heading_deg=37.0, on_carts=False)
    world = aircraft_parts_world(ac, pl)
    fronts = [w for w in world if w.kind == "fuselage_front"]
    assert len(fronts) == 1
    assert fronts[0].polygon.is_valid and not fronts[0].polygon.is_empty


def test_wing_over_polygon_cockpit_conflicts():
    fleet = {
        "outline_test": _build_aircraft(_outline_entry()),
        "overflyer": _build_aircraft(_overflyer_entry()),
    }
    layout = load_layout(_FIXTURES / "outline_wing_over_cockpit.yaml", fleet=fleet)
    result = check(layout)
    kinds = {c.kind for c in result.conflicts}
    assert "fuselage_front_wing_overlap" in kinds, (
        f"expected a cockpit conflict, got {result.conflicts!r}"
    )
