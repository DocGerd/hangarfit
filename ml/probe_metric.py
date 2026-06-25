"""Marginal last-object completion metric for the ADR-0028 trigger-#3 completion probe.

With ``seed_anchor_k = N-1`` pre-parked of a valid witness, a place-nothing / abstain policy
already reads ``valid_placed = (N-1)/N`` (the pre-parked prefix is valid and counts in both the
numerator ``len(_parked)`` and the denominator ``len(requested_ids)`` — env.py:112,355). So the
training-time aggregate ``valid_placed`` is FLOORED and must be read MARGINALLY, never as a raw
success rate (the #821 0.63 masquerade). For a DRIVE-ONE rung (k = N-1, no backplay mixture) the
relation is exact and conservative:

    valid_placed = (N-1)/N + p/N - (2/N)*q     where p = P(last object validly parked),
                                                     q = P(last object INVALIDLY piled)
    => p = N*valid_placed - (N-1)   when q ≈ 0 (abstain, not pile);
       q>0 only makes N*valid_placed-(N-1) an UNDER-estimate, so clamping at 0 never yields a
       false positive. Hence marginal_completion = max(0, N*valid_placed - (N-1)).

Analysis-only: no env/reward/encoding change. Read the door-spawn rung's windowed-final
``valid_placed`` (both seeds) through this transform; threshold the result, never the raw value.
"""

from __future__ import annotations


def marginal_completion(valid_placed: float, *, n: int = 3, k: int = 2) -> float:
    """Floor-aware marginal last-object completion from a drive-one completion rung's aggregate
    ``valid_placed``. Returns ``max(0, n*valid_placed - k)``. Requires k == n-1 (drive exactly
    one object); the affine transform is undefined for drive>1."""
    if k != n - 1:
        raise ValueError(
            f"marginal_completion is defined only for a drive-one rung (k == n-1); got n={n}, k={k}"
        )
    return max(0.0, n * valid_placed - k)
