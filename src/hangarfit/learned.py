"""Learned-backend seam (epic #607).

The deterministic RR-MC :func:`hangarfit.solver.solve` is the default and the
only *working* backend today. This module is the **seam** for the opt-in learned
backend: a sibling entry point that returns the same :class:`~hangarfit.models.SolveResult`
shape, so every downstream consumer (render / ``view`` / ``--write-yaml``) stays
backend-agnostic.

It is **not yet implemented**. Calling :func:`solve_learned` always raises
:class:`LearnedBackendUnavailableError`; the learned policy, its training, and the
``[learned-infer]`` runtime extra arrive in later rungs of #607.

Determinism: the learned proposer is **not** under the ADR-0003 byte-identical
contract — that contract stays on the verifier (``collisions.check`` + ``towplanner``),
which remains the sole arbiter of validity and routability. The learned path's own
(weaker) determinism contract is governed by ADR-0027.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hangarfit.models import Scenario, SolveResult


class LearnedBackendUnavailableError(RuntimeError):
    """Raised when the learned backend is requested but cannot run.

    Today this is *always* raised: the learned backend (epic #607) is not yet
    implemented. Once it ships, this will instead signal a missing optional
    ``[learned-infer]`` runtime dependency or absent model weights — letting the
    CLI surface a clean, actionable error rather than an import traceback.
    """


def solve_learned(
    scenario: Scenario,
    *,
    budget_s: float = 30.0,
    alternatives: int = 1,
    seed: int | None = None,
    plan_paths: bool = True,
) -> SolveResult:
    """Learned-backend counterpart to :func:`hangarfit.solver.solve`.

    Intended contract (once implemented): propose poses **and** the place-and-tow
    sequence with a learned policy, then return the same
    :class:`~hangarfit.models.SolveResult` shape (poses + tow ``MovesPlan``) as the
    deterministic backend, with the verifier accepting/rejecting every layout. The
    signature mirrors the user-facing knobs of :func:`solve` so the CLI can dispatch
    on ``--backend`` without reshaping the call.

    **Not yet implemented** (epic #607): always raises
    :class:`LearnedBackendUnavailableError`.
    """
    raise LearnedBackendUnavailableError(
        "the learned solver backend is not yet available (tracked in #607); "
        "use the default --backend rrmc"
    )
