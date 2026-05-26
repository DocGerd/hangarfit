"""Atheris bridge harness for the YAML loader.

The ``import atheris`` below is exactly what OpenSSF Scorecard's Python fuzzing
probe greps for (scorecard checks/raw/fuzzing.go: filePatterns ``["*.py"]``,
funcPattern ``import atheris``) — its presence is what flips the Fuzzing check
0 -> 10. Running it (nightly) drives libFuzzer through the SAME Hypothesis
strategies the pytest property suite uses, via ``fuzz_one_input``.

Run (from repo root, with atheris installed):
    python -m tests.fuzz.atheris_loader_harness -max_total_time=300
"""

import sys

import atheris

with atheris.instrument_imports():
    from hypothesis import given

    from tests.fuzz import strategies


@given(strategies.tagged_documents())
def _fuzz_loader(tagged):
    strategies.run_tagged(tagged)


if __name__ == "__main__":
    atheris.Setup(sys.argv, _fuzz_loader.hypothesis.fuzz_one_input)
    atheris.Fuzz()
