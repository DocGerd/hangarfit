"""Tests for the RNG-free nose-out parked-heading post-pass (#263, ADR-0022).

`_nose_out` flips a movable plane's parked heading 180° toward nose-out
(heading 180 = toward the door, ADR-0002) when that is strictly more nose-out AND
the layout stays valid. It is a soft preference: never overrides validity, never
moves a plane, never un-parks one, and draws NO RNG (byte-identical determinism
holds even with the feature ON).

These unit tests call `_nose_out` directly with hand-built placements in the roomy
30×25 test hangar (one `aviat_husky`), so they are fast and exercise the exact
accept/reject branches. The solve()-level tests at the bottom check the per-layout
diagnostic and determinism contract.
"""

from __future__ import annotations

import dataclasses
import math
from types import MappingProxyType

from hangarfit.loader import load_scenario
from hangarfit.models import Placement, PlaneConstraint, Scenario, SearchConfig
from hangarfit.solver import _heading_delta_short_arc, _nose_out, solve

_TRIVIAL = "tests/fixtures/solve_trivial_single_plane.yaml"
_ROOMY_THREE = "tests/fixtures/solve_fresh_alternatives_three.yaml"
_PID = "aviat_husky"


def _scenario(constraints: dict[str, PlaneConstraint] | None = None) -> Scenario:
    """A roomy single-plane (`aviat_husky`, 30×25 hangar) scenario, optionally
    with per-plane constraints injected."""
    s = load_scenario(_TRIVIAL)
    if constraints is not None:
        s = dataclasses.replace(s, constraints=MappingProxyType(dict(constraints)))
    return s


def _placements(
    heading_deg: float, *, x_m: float = 15.0, y_m: float = 12.0
) -> dict[str, Placement]:
    return {_PID: Placement(_PID, x_m=x_m, y_m=y_m, heading_deg=heading_deg, on_carts=False)}


def test_flips_nose_in_plane_toward_out() -> None:
    out, flips = _nose_out(_placements(0.0), _scenario(), SearchConfig(), pinned_planes=frozenset())
    assert flips == 1
    assert math.isclose(out[_PID].heading_deg, 180.0)
    # Position is unchanged — the flip is zero-displacement (gap-neutral).
    assert (out[_PID].x_m, out[_PID].y_m) == (15.0, 12.0)
    assert out[_PID].on_carts is False


def test_already_nose_out_is_noop() -> None:
    out, flips = _nose_out(
        _placements(180.0), _scenario(), SearchConfig(), pinned_planes=frozenset()
    )
    assert flips == 0 and out[_PID].heading_deg == 180.0


def test_sideways_90_not_flipped() -> None:
    # short_arc(90, 180) == 90 == short_arc(270, 180): strict `<` => no flip.
    out, flips = _nose_out(
        _placements(90.0), _scenario(), SearchConfig(), pinned_planes=frozenset()
    )
    assert flips == 0 and out[_PID].heading_deg == 90.0


def test_flip_rejected_when_it_breaks_validity() -> None:
    # Near the back wall (y=27): nose-in (h=0) is valid, but flipping to nose-out
    # (h=180) pushes the tail through the back wall -> invalid. Soft, not hard:
    # the plane stays nose-in rather than being forced out.
    placements = _placements(0.0, y_m=27.0)
    out, flips = _nose_out(placements, _scenario(), SearchConfig(), pinned_planes=frozenset())
    assert flips == 0 and out[_PID].heading_deg == 0.0


def test_pinned_plane_never_flips() -> None:
    out, flips = _nose_out(
        _placements(0.0), _scenario(), SearchConfig(), pinned_planes=frozenset({_PID})
    )
    assert flips == 0 and out[_PID].heading_deg == 0.0


def test_per_plane_false_excludes_even_when_global_on() -> None:
    scenario = _scenario({_PID: PlaneConstraint(nose_out=False)})
    out, flips = _nose_out(
        _placements(0.0), scenario, SearchConfig(nose_out=True), pinned_planes=frozenset()
    )
    assert flips == 0 and out[_PID].heading_deg == 0.0


def test_per_plane_true_flips_when_global_off() -> None:
    scenario = _scenario({_PID: PlaneConstraint(nose_out=True)})
    out, flips = _nose_out(
        _placements(0.0), scenario, SearchConfig(nose_out=False), pinned_planes=frozenset()
    )
    assert flips == 1 and math.isclose(out[_PID].heading_deg, 180.0)


def test_global_off_no_constraint_does_not_flip() -> None:
    out, flips = _nose_out(
        _placements(0.0), _scenario(), SearchConfig(nose_out=False), pinned_planes=frozenset()
    )
    assert flips == 0 and out[_PID].heading_deg == 0.0


def _load_roomy_three() -> Scenario:
    return load_scenario(_ROOMY_THREE)


def test_solve_reports_nose_out_flips_per_layout_and_prefers_out() -> None:
    r = solve(
        _load_roomy_three(),
        budget_s=1000.0,
        alternatives=1,
        seed=1,
        search=SearchConfig(max_restarts=30, nose_out=True),
        plan_paths=False,
    )
    assert r.layouts
    assert len(r.diagnostics.nose_out_flips) == len(r.layouts)
    # At least one parked plane lands within 90° of nose-out (heading 180).
    headings = [p.heading_deg for p in r.layouts[0].placements]
    assert any(_heading_delta_short_arc(h, 180.0) < 90.0 for h in headings)
