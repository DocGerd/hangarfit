"""Unit tests for the learned-backend seam (epic #607 rung 1, ADR-0027).

The learned backend is a stub until #607 lands: :func:`solve_learned` must raise
the documented :class:`LearnedBackendUnavailableError` so the CLI can surface a
clean, actionable message instead of a traceback.
"""

from __future__ import annotations

import pytest

from hangarfit.learned import LearnedBackendUnavailableError, solve_learned


def test_unavailable_error_is_runtime_error():
    # A RuntimeError subclass so callers that don't know the seam still catch it,
    # and so it reads as "couldn't run", not "bad input".
    assert issubclass(LearnedBackendUnavailableError, RuntimeError)


def test_solve_learned_raises_unavailable():
    # The stub raises before touching the scenario, so a sentinel is fine here.
    with pytest.raises(LearnedBackendUnavailableError) as exc:
        solve_learned(None)
    msg = str(exc.value)
    assert "#607" in msg
    assert "rrmc" in msg  # points the user at the working default
