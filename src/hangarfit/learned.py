"""Learned-backend seam (epic #607).

The deterministic RR-MC :func:`hangarfit.solver.solve` is the default backend.
This module is the **seam** for the opt-in learned backend: a sibling entry point
that returns the same :class:`~hangarfit.models.SolveResult`
shape, so every downstream consumer (render / ``view`` / ``--write-yaml``) stays
backend-agnostic.

Calling :func:`solve_learned` without a ``weights_path``, with a missing weights
file, or without the ``[learned-infer]`` / ``ml/`` dependencies installed raises
:class:`LearnedBackendUnavailableError` with an actionable message. When a valid
weights path is provided and the inference dependencies are present, the call
delegates to :func:`ml.infer.solve_learned_impl` (lazy-imported inside the
function body so the wheel never drags in ``ml``/``onnxruntime`` at module load).

Determinism: the learned proposer is **not** under the ADR-0003 byte-identical
contract — that contract stays on the verifier (``collisions.check`` + ``towplanner``),
which remains the sole arbiter of validity and routability. The learned path's own
(weaker) determinism contract is governed by ADR-0027.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hangarfit.models import Scenario, SolveResult


class LearnedBackendUnavailableError(RuntimeError):
    """Raised when the learned backend is requested but cannot run.

    Signals a missing optional ``[learned-infer]`` runtime dependency, absent
    model weights, or a bare wheel distribution that does not include the ``ml/``
    package — letting the CLI surface a clean, actionable error rather than an
    import traceback.
    """


def solve_learned(
    scenario: Scenario,
    *,
    weights_path: str | Path | None = None,
    budget_s: float = 30.0,
    alternatives: int = 1,
    seed: int | None = None,
    plan_paths: bool = True,
) -> SolveResult:
    """Learned-backend counterpart to :func:`hangarfit.solver.solve` (epic #607).

    Lazy-imports the torch-free ``ml.infer`` inference path (onnxruntime). Raises
    :class:`LearnedBackendUnavailableError` with an actionable message when the backend
    cannot run: no ``--weights`` given, the ``ml`` package is absent (a bare wheel — see
    #6), ``onnxruntime`` (the ``[learned-infer]`` extra) is missing, or the weights file
    does not exist. The deterministic verifier remains the sole arbiter of validity
    (ADR-0027); an invalid proposal returns a no-layout ``SolveResult``, not an error.
    """
    if weights_path is None:
        raise LearnedBackendUnavailableError(
            "the learned backend needs trained weights: pass --weights PATH "
            "(no default weights ship yet; tracked in #6)"
        )
    if not Path(weights_path).is_file():
        raise LearnedBackendUnavailableError(
            f"learned-backend weights not found at {weights_path!r}"
        )
    try:
        from ml.infer import solve_learned_impl
    except ImportError as exc:
        raise LearnedBackendUnavailableError(
            "the learned backend requires the inference dependencies: install the "
            "'[learned-infer]' extra (onnxruntime) in a source checkout that includes "
            "the 'ml/' package (wheel distribution is tracked in #6). "
            f"(import failed: {exc})"
        ) from exc
    return solve_learned_impl(
        scenario,
        weights_path=weights_path,
        budget_s=budget_s,
        alternatives=alternatives,
        seed=seed,
        plan_paths=plan_paths,
    )
