"""Property tests for the YAML loader.

Invariant under test for every entry point: given any near-valid (or raw)
document, the loader must either return a model or raise ``LoaderError`` — never
a bare KeyError/AttributeError/IndexError/TypeError/ValueError/RecursionError.
The run-helpers in ``strategies`` swallow only ``LoaderError``; any other
exception propagates here and Hypothesis shrinks it to a minimal repro.

Runs under the ``ci`` profile by default (fast, every PR); the nightly workflow
sets HYPOTHESIS_PROFILE=nightly for a deep run.
"""

from __future__ import annotations

from hypothesis import given

from tests.fuzz import strategies as s


@given(s.fleet_documents())
def test_load_fleet_never_crashes(doc):
    s.run_fleet(doc)


@given(s.hangar_documents())
def test_load_hangar_never_crashes(doc):
    s.run_hangar(doc)


@given(s.layout_documents())
def test_load_layout_never_crashes(doc):
    s.run_layout(doc)


@given(s.scenario_documents())
def test_load_scenario_never_crashes(doc):
    s.run_scenario(doc)


@given(s.raw_documents())
def test_load_raw_input_never_crashes(doc):
    s.run_raw(doc)
