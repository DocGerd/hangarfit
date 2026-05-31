"""Property tests for the geometry transform and the collision checker (#355 Part B).

Invariant under test for every target: given any **valid** model input (built by
the constructors, which enforce every invariant), the pure geometry/collision
functions must not crash and must hold their structural invariants — see the
per-target oracles in ``geometry_strategies`` (finiteness, determinism,
determinant −1, area preservation, metadata preservation, non-negative
penetration, and placement-order independence).

This is the Hypothesis side of #355 Part B; the SAME strategies drive the
nightly Atheris coverage-guided run via ``atheris_geometry_harness``. Companion
to ``test_loader_fuzz.py`` (which fuzzes the YAML loader). Runs under the ``ci``
profile by default (fast, every PR); the nightly workflow sets
HYPOTHESIS_PROFILE=nightly for a deep run.
"""

from __future__ import annotations

from hypothesis import given

from tests.fuzz import geometry_strategies as g


@given(g.local_to_world_inputs())
def test_local_to_world_never_crashes(inputs):
    g.run_local_to_world(inputs)


@given(g.aircraft_world_inputs())
def test_aircraft_parts_world_never_crashes(inputs):
    g.run_aircraft_parts_world(inputs)


@given(g.layout_inputs())
def test_collisions_check_never_crashes(layout):
    g.run_collisions_check(layout)
