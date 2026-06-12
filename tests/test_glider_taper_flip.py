"""The value proof for the polygon-parts feature (#593, #541): the *shipped*,
loader-built Scheibe SF-25E wing taper makes a wingtip nest where its bounding
rectangle would falsely conflict.

The spike (``docs/spikes/polygon-part-geometry-feasibility.md``) measured a robust
flip window (~0.2 m wide; 0.10–0.30 m of crowding) of rect-rejects / taper-accepts
by crowding the Scheibe wing toward the Stemme empennage. Reproducing that exact
two-aircraft crowd from a fixed shipped layout means positioning both planes
precisely inside the flip band — fiddly and brittle. So this regression reproduces
the identical *mechanism* directly and deterministically with a synthetic
wing-layer neighbour at the wingtip: a tapered tip clears a neighbour the bbox
rectangle falsely fouls, across a window whose width is the taper's tip footprint
reduction.

This exercises ``geometry.polygon_overlap`` — the exact plan-view conflict
primitive ``collisions._parts_conflict`` aggregates — on the real shipped taper
vs its bounding rectangle, so the assertion can only hold if the taper genuinely
under-fills its box.
"""

from __future__ import annotations

import dataclasses

from hangarfit.geometry import aircraft_parts_world, oriented_rect, polygon_overlap
from hangarfit.loader import load_fleet
from hangarfit.models import Placement

# A fixed placement (origin, nose to +y) and a probe square in the wing z-layer at
# the LEFT wingtip (world x ≈ −9). ``s`` crowds the probe across the chord
# direction (world y) away from the wing chord centre (world y = offset_x 1.5).
_PLACE = Placement("scheibe_falke", x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=False)
_TIP_X = -8.95  # just inboard of the v = −9 tip so the chord is well-defined
_CHORD_CENTRE_Y = 1.5  # wing offset_x_m folds to world y at heading 0


def _scheibe(*, taper: bool):
    fleet = load_fleet("examples/herrenteich/fleet.yaml")
    ac = fleet["scheibe_falke"]
    if taper:
        return ac
    # The bounding-rectangle counterfactual: strip the wing's polygon footprint.
    parts = tuple(
        dataclasses.replace(p, local_vertices=None) if p.kind == "wing" else p for p in ac.parts
    )
    return dataclasses.replace(ac, parts=parts)


def _wing_world(ac):
    return next(wp.polygon for wp in aircraft_parts_world(ac, _PLACE) if wp.kind == "wing")


def _probe(s: float):
    # A 0.1 m square neighbour part at the wingtip, crowded by ``s`` along the chord.
    return oriented_rect(_TIP_X, _CHORD_CENTRE_Y + s, 0.1, 0.1, 0.0)


def _conflicts(wing, s: float) -> bool:
    return polygon_overlap(wing, _probe(s), 0.0)  # pure plan-view overlap


def test_taper_clears_a_wingtip_neighbour_the_rectangle_fouls():
    """Mid-window: the rectangle wing fouls the neighbour; the shipped taper clears it."""
    taper, rect = _wing_world(_scheibe(taper=True)), _wing_world(_scheibe(taper=False))
    s = 0.42  # inside the measured flip window [~0.28, ~0.55]
    assert _conflicts(rect, s), "the bounding rectangle should foul the crowded neighbour"
    assert not _conflicts(taper, s), "the tapered wingtip should clear it — the value"


def test_both_conflict_when_genuinely_overlapped():
    """Tight crowding: the taper is NOT trivially clear — it fouls a true overlap."""
    taper, rect = _wing_world(_scheibe(taper=True)), _wing_world(_scheibe(taper=False))
    s = 0.15  # well inside both chords
    assert _conflicts(rect, s) and _conflicts(taper, s)


def test_both_clear_when_uncrowded():
    """Generous spacing: neither fouls — the flip is a genuine band, not always-accept."""
    taper, rect = _wing_world(_scheibe(taper=True)), _wing_world(_scheibe(taper=False))
    s = 0.70  # outside both chords
    assert not _conflicts(rect, s) and not _conflicts(taper, s)


def test_flip_window_width_matches_the_spike():
    """The rect-rejects / taper-accepts band reproduces the spike's ~0.2 m order
    (this shipped model's band is ≈0.26 m wide — the taper's tip footprint
    reduction), grounded in the shipped parametrization."""
    taper, rect = _wing_world(_scheibe(taper=True)), _wing_world(_scheibe(taper=False))
    flips = [
        i * 0.01
        for i in range(0, 100)
        if _conflicts(rect, i * 0.01) and not _conflicts(taper, i * 0.01)
    ]
    assert flips, "expected a non-empty rect-rejects / taper-accepts window"
    width = max(flips) - min(flips)
    assert 0.15 <= width <= 0.40, f"flip window {width:.3f} m off the spike's ~0.2 m order"


def test_taper_wing_underfills_its_bounding_box():
    """The cause of the flip: the polygon footprint is a strict subset of the bbox
    (ADR-0024), so its area is strictly smaller — the rectangle over-claims."""
    taper, rect = _wing_world(_scheibe(taper=True)), _wing_world(_scheibe(taper=False))
    assert taper.area < rect.area
    assert taper.within(rect.buffer(1e-9))  # taper ⊆ bbox rectangle


def _const_chord_wing_world(ac):
    # The negative control's wing: a CONSTANT-chord planform (the loader's hexagon
    # with tip == root). It degenerates to the full bbox rectangle, so it can never
    # clear what the rectangle fouls.
    wing = next(p for p in ac.parts if p.kind == "wing")
    hr, half = wing.length_m / 2.0, wing.width_m / 2.0
    flat = ((hr, 0.0), (hr, half), (-hr, half), (-hr, 0.0), (-hr, -half), (hr, -half))
    parts = tuple(
        dataclasses.replace(p, local_vertices=flat) if p.kind == "wing" else p for p in ac.parts
    )
    return _wing_world(dataclasses.replace(ac, parts=parts))


def test_constant_chord_wing_shows_no_flip():
    """The spike's negative control: a non-tapered (constant-chord) wing flips
    NOWHERE — its footprint equals its bbox, so the value is taper-specific and
    not an artefact of the probe geometry."""
    rect = _wing_world(_scheibe(taper=False))
    const = _const_chord_wing_world(_scheibe(taper=True))
    flips = [
        i * 0.01
        for i in range(0, 100)
        if _conflicts(rect, i * 0.01) and not _conflicts(const, i * 0.01)
    ]
    assert not flips, f"a constant-chord wing must not flip; got window {flips}"
