"""Unit tests for the learned-backend seam (epic #607 rung 1, ADR-0027).

The learned backend raises :class:`LearnedBackendUnavailableError` with actionable
messages when weights are absent, the file is missing, or the ml/onnxruntime
dependencies are not installed — so the CLI surfaces a clean error instead of a
traceback.
"""

from __future__ import annotations

import pathlib

import pytest

from hangarfit.learned import LearnedBackendUnavailableError, solve_learned


def _minimal_scenario():
    from hangarfit.loader import load_scenario

    root = pathlib.Path(__file__).resolve().parent.parent
    return load_scenario(str(root / "tests/fixtures/scenario_minimal.yaml"))


def test_unavailable_error_is_runtime_error():
    # A RuntimeError subclass so callers that don't know the seam still catch it,
    # and so it reads as "couldn't run", not "bad input".
    assert issubclass(LearnedBackendUnavailableError, RuntimeError)


def test_solve_learned_raises_unavailable():
    # Calling without weights raises LearnedBackendUnavailableError before
    # touching the scenario, so a sentinel None is fine here.
    with pytest.raises(LearnedBackendUnavailableError) as exc:
        solve_learned(None)
    msg = str(exc.value)
    assert "--weights" in msg  # actionable: tells the user what to pass


def test_solve_learned_no_weights_is_clean_error():
    with pytest.raises(LearnedBackendUnavailableError, match="--weights"):
        solve_learned(_minimal_scenario(), weights_path=None)


def test_solve_learned_missing_weights_file_is_clean_error(tmp_path):
    missing = tmp_path / "nope.onnx"
    # "not found" pins the missing-FILE branch specifically; a loose "weights" match would
    # also pass on the weights_path-is-None branch's message.
    with pytest.raises(LearnedBackendUnavailableError, match="not found"):
        solve_learned(_minimal_scenario(), weights_path=str(missing))
