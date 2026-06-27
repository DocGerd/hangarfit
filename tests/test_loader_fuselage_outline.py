"""Tests for the #550 fuselage outline polygon capability.

A `kind: fuselage` part may carry a raw `vertices:` outline that the loader
clips into area-conserving `fuselage_front`/`fuselage_aft` sub-polygons at the
wing trailing edge (capability-only; the real fleet stays byte-identical). See
`docs/superpowers/specs/2026-06-27-fuselage-outline-polygon-design.md`.
"""

import pytest

from hangarfit.loader import LoaderError, _build_part


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
