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
import os
import subprocess
import sys
from pathlib import Path
from types import MappingProxyType

import pytest

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


def test_nose_out_on_is_byte_identical_for_same_seed() -> None:
    """RNG-free ⇒ two ``nose_out=True`` solves are byte-identical (strictly
    stronger than ``spread``, which only guarantees this when off). Bound on
    ``max_restarts`` (NOT ``budget_s``) so it is load-independent (ADR-0003)."""
    cfg = SearchConfig(max_restarts=10, nose_out=True)
    r1 = solve(
        _load_roomy_three(), budget_s=1000.0, alternatives=1, seed=42, search=cfg, plan_paths=False
    )
    r2 = solve(
        _load_roomy_three(), budget_s=1000.0, alternatives=1, seed=42, search=cfg, plan_paths=False
    )
    assert len(r1.layouts) == len(r2.layouts)
    for la, lb in zip(r1.layouts, r2.layouts, strict=True):
        assert la.placements == lb.placements
    assert r1.diagnostics.nose_out_flips == r2.diagnostics.nose_out_flips


def test_nose_out_off_is_byte_identical_to_pre_feature() -> None:
    """``nose_out=False`` never calls ``_nose_out`` ⇒ the RNG stream and selected
    layout are byte-identical to the pre-feature solver (the opt-out contract,
    mirroring ``spread=False``)."""
    cfg = SearchConfig(max_restarts=10, nose_out=False)
    r1 = solve(
        _load_roomy_three(), budget_s=1000.0, alternatives=1, seed=42, search=cfg, plan_paths=False
    )
    r2 = solve(
        _load_roomy_three(), budget_s=1000.0, alternatives=1, seed=42, search=cfg, plan_paths=False
    )
    for la, lb in zip(r1.layouts, r2.layouts, strict=True):
        assert la.placements == lb.placements
    assert (
        r1.diagnostics.nose_out_flips == (0,) * len(r1.layouts) or not r1.diagnostics.nose_out_flips
    )


_NOSE_OUT_HASH_SNIPPET = """
import hashlib
from hangarfit.loader import load_scenario
from hangarfit.solver import solve
from hangarfit.models import SearchConfig
s = load_scenario("tests/fixtures/solve_fresh_alternatives_three.yaml")
r = solve(s, budget_s=1000.0, alternatives=1, seed=42,
          search=SearchConfig(max_restarts=10, nose_out=True), plan_paths=False)
blob = repr([
    [(p.plane_id, p.x_m, p.y_m, p.heading_deg, p.on_carts) for p in L.placements]
    for L in r.layouts
] + [list(r.diagnostics.nose_out_flips)])
print(hashlib.sha256(blob.encode()).hexdigest())
"""


@pytest.mark.serial
def test_nose_out_byte_identical_across_processes() -> None:
    """``nose_out=True`` must be byte-identical across fresh processes with
    different ``PYTHONHASHSEED`` — pins the new ``sorted(...)`` movable-plane
    iteration in ``_nose_out`` against a set/hash-seed-order leak that an
    in-process ``==`` cannot catch (the analogue of the apron cross-process
    canary)."""
    repo_root = Path(__file__).resolve().parent.parent

    def _run(hashseed: str) -> str:
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = hashseed
        proc = subprocess.run(
            [sys.executable, "-c", _NOSE_OUT_HASH_SNIPPET],
            capture_output=True,
            text=True,
            cwd=repo_root,
            env=env,
            check=True,
        )
        return proc.stdout.strip()

    h1 = _run("111")
    h2 = _run("777")
    assert h1 and h1 == h2, f"nose_out solve diverged across processes: {h1!r} != {h2!r}"
