"""Loader tests for the parametrized `planform:` wing block (#548)."""

from __future__ import annotations

import pytest

from hangarfit.loader import LoaderError, _build_part


def _wing_data(**planform):
    return {
        "kind": "wing",
        "length_m": 2.0,
        "width_m": 10.0,
        "offset_x_m": 1.5,
        "z_bottom_m": 1.9,
        "z_top_m": 2.1,
        "planform": {"root_chord_m": 2.0, "tip_chord_m": 0.9, **planform},
    }


def test_planform_expands_to_canonical_hexagon() -> None:
    part = _build_part(_wing_data(), 0)
    assert part.local_vertices is not None
    assert len(part.local_vertices) == 6
    # Within the 2.0 x 10.0 bbox.
    for x, y in part.local_vertices:
        assert abs(x) <= 1.0 + 1e-9
        assert abs(y) <= 5.0 + 1e-9


def test_planform_absent_leaves_local_vertices_none() -> None:
    data = {"kind": "wing", "length_m": 2.0, "width_m": 10.0, "z_bottom_m": 1.9, "z_top_m": 2.1}
    assert _build_part(data, 0).local_vertices is None


def test_planform_rejects_tip_exceeding_root() -> None:
    with pytest.raises(LoaderError, match="taper outward"):
        _build_part(_wing_data(root_chord_m=1.0, tip_chord_m=1.5), 0)


def test_planform_rejects_root_exceeding_length_bbox() -> None:
    # root_chord 3.0 > length_m 2.0 -> a vertex pokes outside the bbox.
    with pytest.raises((LoaderError, ValueError), match="bbox"):
        _build_part(_wing_data(root_chord_m=3.0, tip_chord_m=0.9), 0)


def test_planform_rejects_unknown_nested_key() -> None:
    with pytest.raises(LoaderError, match="unknown key"):
        _build_part(_wing_data(sweep_deg=3.0), 0)


def test_build_part_rejects_unknown_part_key() -> None:
    data = {
        "kind": "wing",
        "length_m": 2.0,
        "width_m": 10.0,
        "z_bottom_m": 1.9,
        "z_top_m": 2.1,
        "planfrm": {},
    }
    with pytest.raises(LoaderError, match="unknown key"):
        _build_part(data, 0)


def test_planform_rejected_on_non_wing_part() -> None:
    data = {
        "kind": "fuselage",
        "length_m": 6.0,
        "width_m": 0.8,
        "z_bottom_m": 0.0,
        "z_top_m": 1.5,
        "planform": {"root_chord_m": 6.0, "tip_chord_m": 3.0},
    }
    with pytest.raises(LoaderError, match="only valid on a kind 'wing'"):
        _build_part(data, 0)
