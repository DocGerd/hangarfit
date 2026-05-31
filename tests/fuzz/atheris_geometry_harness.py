"""Atheris bridge harness for the geometry transform & collision checker (#355 Part B).

Like ``atheris_loader_harness.py``, the ``import atheris`` below is what OpenSSF
Scorecard's Python fuzzing probe greps for. Running it (nightly) drives
libFuzzer through the SAME Hypothesis strategies the pytest property suite
(``test_geometry_fuzz.py``) uses, via ``fuzz_one_input``.

Run (from repo root, with atheris installed):
    python -m tests.fuzz.atheris_geometry_harness -max_total_time=300
"""

import sys

import atheris

with atheris.instrument_imports():
    from hypothesis import given

    from tests.fuzz import geometry_strategies


@given(geometry_strategies.geometry_tagged_documents())
def _fuzz_geometry(tagged):
    geometry_strategies.run_geometry_tagged(tagged)


if __name__ == "__main__":
    atheris.Setup(sys.argv, _fuzz_geometry.hypothesis.fuzz_one_input)
    atheris.Fuzz()
