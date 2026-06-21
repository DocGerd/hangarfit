"""ml/gate.py — read a ``--metrics-out`` JSONL training curve and emit a per-rung
gate verdict (#730, towards the #698 train-to-mastery frontier).

PURE: stdlib only (``json`` / ``dataclasses``), **no torch** — so it runs under the
``[dev]``-only CI and can be invoked post-hoc on any run's metrics file via
``python -m ml.gate METRICS.jsonl --rung trio-box``.

Reads ``valid_placed`` (the #710 compound mastery axis: ``fraction_placed`` credited
only on **valid** episodes — the #694 product checker, ``collisions.check`` *plus* Caddy
egress, not merely collision-free), NOT ``valid_rate`` — an empty layout is vacuously
"valid", so ``valid_rate -> 1`` under place-nothing is the *failure* signature, not a
win. The piling watchdog flags the other failure mode: ``valid_placed`` low while
``fraction_placed`` high = the policy commits objects *invalidly* (piling), distinct
from fleeing to place-nothing.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast


@dataclass(frozen=True, slots=True)
class GateVerdict:
    """The verdict for one rung's ``valid_placed`` learning curve."""

    rung: str
    outcome: str  # "mastered" | "piling" | "place-nothing" | "in-progress" | "no-data"
    n_iters: int  # iterations with at least one completed episode
    peak_valid_placed: float | None
    peak_valid_placed_iter: int | None
    final_valid_placed: float | None
    competency_iter: int | None  # first iter with valid_placed >= threshold, else None
    peak_fraction_placed: float | None = None
    piling_iters: int = 0  # iters that placed much (fraction high) but validly little


def gate_verdict(
    records: list[dict[str, object]],
    rung: str,
    *,
    threshold: float = 0.9,
    piling_floor: float = 0.2,
    piling_fraction: float = 0.5,
) -> GateVerdict:
    """Reduce ``records`` (history_metric_records()-shaped) for ``rung`` to a verdict.

    An iteration is a *piling* iteration when it placed much (``fraction_placed >=
    piling_fraction``) but validly little (``valid_placed <= piling_floor``) — the policy
    is committing objects invalidly. The headline ``outcome`` when not yet mastered:
    ``piling`` if any piling iter occurred, ``place-nothing`` if it never placed much
    (peak ``fraction_placed < piling_fraction``: fled to do-nothing), else ``in-progress``
    (placing validly and climbing, just below ``threshold``)."""
    rows = [
        r
        for r in records
        if r.get("stage") == rung
        and r.get("iter") is not None
        and r.get("valid_placed") is not None
        and r.get("fraction_placed") is not None
    ]
    # JSON values are typed `object`; the filter above guarantees the three keys are present
    # and numeric (the emitter writes the rates as None together for n_eps==0 iterations, and
    # a hand-rolled file with a partial row is skipped here rather than crashing on a None
    # comparison), so the casts are sound. Sort by iter so `final_*` is the last *iteration*,
    # not merely the last line of a possibly out-of-order or concatenated file.
    curve: list[tuple[int, float, float]] = sorted(
        (
            (
                cast("int", r["iter"]),
                cast("float", r["valid_placed"]),
                cast("float", r["fraction_placed"]),
            )
            for r in rows
        ),
        key=lambda ivf: ivf[0],
    )

    if not curve:
        return GateVerdict(
            rung=rung,
            outcome="no-data",
            n_iters=0,
            peak_valid_placed=None,
            peak_valid_placed_iter=None,
            final_valid_placed=None,
            competency_iter=None,
        )

    peak_iter, peak_vp, _ = max(curve, key=lambda ivf: ivf[1])
    peak_fp = max(fp for _, _, fp in curve)
    competency_iter = next((it for it, vp, _ in curve if vp >= threshold), None)
    piling_iters = sum(1 for _, vp, fp in curve if fp >= piling_fraction and vp <= piling_floor)

    if peak_vp >= threshold:
        outcome = "mastered"
    elif piling_iters > 0:
        outcome = "piling"
    elif peak_fp < piling_fraction:
        outcome = "place-nothing"
    else:
        outcome = "in-progress"

    return GateVerdict(
        rung=rung,
        outcome=outcome,
        n_iters=len(curve),
        peak_valid_placed=peak_vp,
        peak_valid_placed_iter=peak_iter,
        final_valid_placed=curve[-1][1],
        competency_iter=competency_iter,
        peak_fraction_placed=peak_fp,
        piling_iters=piling_iters,
    )


def read_metric_records(path: str | Path) -> list[dict[str, object]]:
    """Parse a ``--metrics-out`` JSONL file into the record list ``gate_verdict`` wants
    (one JSON object per non-blank line)."""
    text = Path(path).read_text()
    return [json.loads(line) for line in text.splitlines() if line.strip()]


_OUTCOME_GLOSS = {
    "mastered": "valid_placed crossed the threshold — the WIN.",
    "piling": "placed much but validly little — committing objects invalidly, NOT a win.",
    "place-nothing": "fled to do-nothing — clean collapse, routes to a pose-scaffold rung.",
    "in-progress": "placing validly and climbing, but below the threshold — not yet mastered.",
    "no-data": "no completed-episode iteration recorded for this rung.",
}


def render_verdict(v: GateVerdict) -> str:
    """A human-readable multi-line verdict, headlining ``valid_placed`` (see the module
    docstring for why never ``valid_rate``)."""
    if v.outcome == "no-data":
        return f"[{v.rung}] NO-DATA — {_OUTCOME_GLOSS['no-data']}"
    comp = f"iter {v.competency_iter}" if v.competency_iter is not None else "never"
    return (
        f"[{v.rung}] {v.outcome.upper()} — {_OUTCOME_GLOSS[v.outcome]}\n"
        f"  peak valid_placed  = {v.peak_valid_placed:.3f} (iter {v.peak_valid_placed_iter})\n"
        f"  final valid_placed = {v.final_valid_placed:.3f}\n"
        f"  competency (vp>=thr): {comp}\n"
        f"  peak fraction_placed = {v.peak_fraction_placed:.3f}   piling iters = {v.piling_iters}\n"
        f"  iters with episodes  = {v.n_iters}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m ml.gate METRICS.jsonl --rung trio-box``. Prints the verdict and returns
    an exit code: ``0`` mastered, ``1`` ran but did not reach competency (piling /
    place-nothing / in-progress — all valid *results*, just not the WIN), ``2`` no data
    for the rung."""
    p = argparse.ArgumentParser(
        prog="python -m ml.gate",
        description="Read a --metrics-out JSONL training curve and emit a per-rung gate verdict.",
    )
    p.add_argument("metrics", help="path to the --metrics-out JSONL file")
    p.add_argument("--rung", default="trio-box", help="curriculum rung to gate (default: trio-box)")
    p.add_argument(
        "--threshold",
        type=float,
        default=0.9,
        help="valid_placed competency threshold (default: 0.9, matching PromotionPolicy)",
    )
    args = p.parse_args(argv)

    verdict = gate_verdict(read_metric_records(args.metrics), args.rung, threshold=args.threshold)
    print(render_verdict(verdict))
    if verdict.outcome == "no-data":
        return 2
    return 0 if verdict.outcome == "mastered" else 1


if __name__ == "__main__":
    raise SystemExit(main())
