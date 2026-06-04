"""Tests for the read-only render-annotation metrics (hangarfit.metrics, #401)."""

from __future__ import annotations

import dataclasses
import math

from hangarfit import metrics
from hangarfit.geometry import aircraft_parts_world
from hangarfit.loader import load_layout

NESTING = "tests/fixtures/valid_left_side_nesting.yaml"
SEPARATED = "tests/fixtures/valid_two_separated.yaml"
WING_OVER_AFT = "tests/fixtures/valid_high_over_low_aft_z_disjoint.yaml"


def test_has_placeholder_data_true_for_unmeasured_fleet():
    # The shipped fleet is all measured: false, so any loaded layout is placeholder.
    assert metrics.has_placeholder_data(load_layout(NESTING)) is True


def test_has_placeholder_data_false_when_all_measured():
    lay = load_layout(NESTING)
    measured_fleet = {pid: dataclasses.replace(ac, measured=True) for pid, ac in lay.fleet.items()}
    lay2 = dataclasses.replace(lay, fleet=measured_fleet)
    assert metrics.has_placeholder_data(lay2) is False


def test_min_pairwise_gap_matches_direct_computation():
    lay = load_layout(NESTING)
    got = metrics.min_pairwise_gap_m(lay)
    by_id = {p.plane_id: p for p in lay.placements}
    ids = sorted(by_id)
    world = {pid: aircraft_parts_world(lay.fleet[pid], by_id[pid]) for pid in ids}
    want = min(pa.polygon.distance(pb.polygon) for pa in world[ids[0]] for pb in world[ids[1]])
    assert got is not None and math.isclose(got, want, abs_tol=1e-12)


def test_min_pairwise_gap_none_for_single_plane():
    lay = load_layout(NESTING)
    single = dataclasses.replace(lay, placements=(lay.placements[0],))
    assert metrics.min_pairwise_gap_m(single) is None


def test_min_wing_over_tail_clearance_positive_when_overhang():
    # A high wing sitting (z-disjoint) over a low aft fuselage: there IS a positive
    # vertical clearance where the wing footprint overlaps the aft footprint.
    c = metrics.min_wing_over_tail_clearance_m(load_layout(WING_OVER_AFT))
    assert c is not None and c >= 0.0


def test_min_wing_over_tail_clearance_none_when_no_overhang():
    # Two well-separated planes: no wing footprint overlaps another's tail/aft.
    assert metrics.min_wing_over_tail_clearance_m(load_layout(SEPARATED)) is None
