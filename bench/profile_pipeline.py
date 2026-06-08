"""CLI for the solve→tow profiling/benchmark harness (#381).

Examples::

    python -m bench.profile_pipeline                # fast regimes, timing table
    python -m bench.profile_pipeline --heavy        # + 9-plane and tight-fill
    python -m bench.profile_pipeline --profile      # + cProfile stage breakdown
    python -m bench.profile_pipeline --regime trivial_single --profile
    python -m bench.profile_pipeline --json         # machine-readable

The timing table reports, per regime, the placement vs routing wall-clock split
and the three correctness verdicts (validity / path-validity / determinism).
With ``--profile`` it additionally prints a cProfile attribution of where the
*routing* time goes (the dominant cost on multi-plane fills) bucketed into the
sub-stages named in #381.
"""

from __future__ import annotations

import argparse
import json
import sys

from .harness import (
    RegimeResult,
    profile_placement,
    profile_routing,
    run_regime,
)
from .regimes import FAST_REGIMES, REGIMES, regime_by_key

# ── speed-regression tripwire ceilings (the F6/#403 CI gate) ─────────────────
#
# Per-regime wall-clock ceilings (seconds) enforced ONLY under ``--gate``. These
# are a *catastrophic-regression tripwire, not a microbenchmark*: CI runs on
# shared GitHub-hosted runners with multi-x run-to-run variance, so a tight
# ceiling would flake. The regimes bind on ``max_restarts`` (regimes.py), which
# fixes the *work*, so the only thing that varies is machine speed — and each
# ceiling carries enough headroom to absorb that while still tripping on a real
# multi-x regression.
#
# Calibration history (ubuntu-24.04 GitHub-hosted runner, as measured by the
# ``bench gates`` workflow itself):
#
# * 2026-06-06: trivial_single 0.6 s, roomy_three_spread_on 54.6 s,
#   roomy_three_spread_off 2.7 s; binding ceiling spread_on at 100 s (~1.8x median).
# * 2026-06-08 (#263): nose-out parked heading is **default ON**, so every regime
#   now routes nose-out goals by **backing the plane in during the fill** — #480's
#   cheap analytic back-in only fires for a clear approach, so a multi-plane fill
#   pays node-level search per back-in. Measured impact: roomy_three_spread_on
#   ROUTING ~doubled (CI total 54.6 → 84.5 s) and roomy_three_apron ROUTING ~tripled
#   (CI total 170.5 s, was ~108 s) — the apron's enlarged start set (reverse cones ×
#   apron y-samples) compounds the back-in cost. This is the shipped default's real
#   cost (decided 2026-06-08: keep default-ON, re-baseline the ceilings), NOT a
#   regression, so the ceilings are raised to fit it. The two tiny regimes (trivial,
#   spread_off — the latter's lone 1-restart fill barely flips) are unaffected.
# * 2026-06-08 (#524): the empennage model (#518/#519/#520, ADR-0023) gives every
#   aircraft two more parts (a horizontal ``tail`` + a ``vertical_stabilizer``), so
#   every Hybrid-A*/Reeds–Shepp tow expansion validates more part pairs and per-check
#   cost rose. The route-heavy apron regime absorbed the most: CI total crept from
#   ~170.5 → ~269 s (~58 %), tripping the old 240 s ceiling, while all three
#   correctness verdicts stayed green. This is the heavier shipped model's real cost
#   (a sanctioned re-baseline, not a regression), so the apron ceiling is raised to
#   380 s (~1.4x the ~269 s CI median). spread_on (CI ~78 s) keeps ample headroom
#   under 130 and is left unchanged.
#
# The ceilings remain a *catastrophic-regression tripwire, not a microbenchmark*:
# spread_on at 130 s sits above CI machine-speed jitter (~1.5x the 84.5 s median —
# the regimes bind on ``max_restarts``, so only machine speed varies) yet still
# below a #453 memoization-revert (which adds ~+68 s of placement → ~152 s here),
# so the canonical regression still trips. apron at 380 s is ~1.4x its ~269 s
# post-empennage median (#524).
#
# Recalibrate — and only then — when the regimes change, the lever set / a default
# changes, or GitHub changes the runner class; re-confirm spread_on still trips on
# a memoization-revert. The gate refuses to run a regime with no ceiling defined
# (see ``_evaluate_gate``) so a newly added regime can never silently escape it.
# See docs/spikes/solve-tow-profiling.md §"F6 — the CI gates".
_SPEED_CEILING_S: dict[str, float] = {
    "trivial_single": 10.0,
    "roomy_three_spread_on": 130.0,
    "roomy_three_spread_off": 20.0,
    # Same placement as roomy_three_spread_on (apron is planner-only) plus the
    # apron's heavier routing — and since #263 the nose-out back-in from the apron's
    # enlarged start set dominates. Post-empennage (#518/#519/#520) the per-expansion
    # part-pair validation cost rose, pushing this regime to CI ~269 s for the 14 m
    # deep fill (was ~170 s). Tripwire, not a microbenchmark (#499 / #263 / #524).
    "roomy_three_apron": 380.0,
}


def _evaluate_gate(results: list[RegimeResult]) -> list[str]:
    """Return a list of human-readable gate failures (empty == gate passes).

    Enforces all three correctness invariants (validity / path-validity /
    determinism — already reflected in each ``RegimeResult``) *plus* the speed
    ceiling. A regime with no ceiling defined is itself a failure, so the gate
    targets exactly the fast set and a new regime cannot slip past the speed
    check unnoticed.
    """
    failures: list[str] = []
    for r in results:
        if not r.layouts_valid:
            failures.append(f"{r.key}: VALIDITY — a layout did not score (0, 0.0)")
        if not r.paths_valid:
            failures.append(f"{r.key}: PATH-VALIDITY — a committed tow arc conflicts")
        if not r.deterministic:
            failures.append(f"{r.key}: DETERMINISM — second run digest differed")
        ceiling = _SPEED_CEILING_S.get(r.key)
        if ceiling is None:
            failures.append(
                f"{r.key}: NO SPEED CEILING defined — add one to "
                "bench/profile_pipeline.py::_SPEED_CEILING_S (the gate targets the fast set)"
            )
        elif r.total_s > ceiling:
            failures.append(f"{r.key}: SPEED — {r.total_s:.1f}s exceeds the {ceiling:.1f}s ceiling")
    return failures


def _print_gate_summary(results: list[RegimeResult], failures: list[str]) -> None:
    """Print a PASS/FAIL gate summary to stderr (keeps --json stdout clean)."""
    print("\n── bench gate (validity + path-validity + determinism + speed) ──", file=sys.stderr)
    for r in results:
        ceiling = _SPEED_CEILING_S.get(r.key)
        budget = (
            f"{r.total_s:.2f}s / {ceiling:.0f}s" if ceiling is not None else f"{r.total_s:.2f}s / —"
        )
        print(
            f"  {r.key:<24} valid={_ok(r.layouts_valid).strip():<4} "
            f"paths={_ok(r.paths_valid).strip():<4} det={_ok(r.deterministic).strip():<4} "
            f"speed[{budget}]",
            file=sys.stderr,
        )
    if failures:
        print("\nGATE FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  ✗ {f}", file=sys.stderr)
    else:
        print("\nGATE PASSED", file=sys.stderr)


def _ok(flag: bool) -> str:
    return "ok  " if flag else "FAIL"


def _print_table(results: list[RegimeResult]) -> None:
    header = (
        f"{'regime':<24}{'planes':>7}{'spread':>7}{'restarts':>9}"
        f"{'place_s':>9}{'route_s':>9}{'total_s':>9}"
        f"{'routed':>8}{'valid':>6}{'paths':>6}{'det':>5}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r.key:<24}{r.n_planes:>7}{('on' if r.spread else 'off'):>7}"
            f"{r.restarts_done:>9}{r.placement_s:>9.3f}{r.routing_s:>9.3f}"
            f"{r.total_s:>9.3f}{f'{r.n_routed}/{r.n_layouts}':>8}"
            f"{_ok(r.layouts_valid):>6}{_ok(r.paths_valid):>6}{_ok(r.deterministic):>5}"
        )
    print()
    for r in results:
        if r.notes:
            print(f"  {r.key}: " + "; ".join(r.notes))


def _print_profile(key: str) -> None:
    regime = regime_by_key(key)
    print(f"\n═══ cProfile: {regime.key} — {regime.description} ═══")

    print("  (bucket times are cumulative — nested stages overlap, do not sum to 100%)")

    p_elapsed, p_buckets, _ = profile_placement(regime)
    print(f"\n  PLACEMENT  ({p_elapsed:.3f} s wall)")
    for stage, cum, ncalls in p_buckets:
        pct = (100.0 * cum / p_elapsed) if p_elapsed else 0.0
        print(f"    {stage:<32}{cum:>8.3f} s  {pct:>5.1f}%  ({ncalls:,} calls)")

    r_elapsed, r_buckets, r_top = profile_routing(regime)
    print(f"\n  ROUTING    ({r_elapsed:.3f} s wall)")
    for stage, cum, ncalls in r_buckets:
        pct = (100.0 * cum / r_elapsed) if r_elapsed else 0.0
        print(f"    {stage:<32}{cum:>8.3f} s  {pct:>5.1f}%  ({ncalls:,} calls)")
    print("\n  routing pstats (top 18 by cumulative):")
    for line in r_top.splitlines():
        print(f"    {line}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="solve→tow profiling harness (#381)")
    ap.add_argument(
        "--regime",
        action="append",
        metavar="KEY",
        help="run only these regime keys (repeatable); default = fast set",
    )
    ap.add_argument(
        "--heavy",
        action="store_true",
        help="include heavy regimes (9-plane fill, tight un-routable placeholder)",
    )
    ap.add_argument(
        "--profile",
        action="store_true",
        help="also print a cProfile stage breakdown per regime",
    )
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    ap.add_argument(
        "--gate",
        action="store_true",
        help="enforce the F6 CI gate: the three correctness invariants PLUS the "
        "per-regime speed ceilings; exit non-zero on any failure. Used by the "
        "bench-gates workflow; targets the fast regime set.",
    )
    args = ap.parse_args(argv)

    if args.regime:
        try:
            regimes = [regime_by_key(k) for k in args.regime]
        except KeyError as exc:
            print(f"unknown regime: {exc}", file=sys.stderr)
            print("known:", ", ".join(r.key for r in REGIMES), file=sys.stderr)
            return 2
    else:
        regimes = list(REGIMES if args.heavy else FAST_REGIMES)

    results = [run_regime(r) for r in regimes]

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "key": r.key,
                        "n_planes": r.n_planes,
                        "spread": r.spread,
                        "restarts_done": r.restarts_done,
                        "placement_s": r.placement_s,
                        "routing_s": r.routing_s,
                        "total_s": r.total_s,
                        "n_layouts": r.n_layouts,
                        "n_routed": r.n_routed,
                        "layouts_valid": r.layouts_valid,
                        "paths_valid": r.paths_valid,
                        "deterministic": r.deterministic,
                        "status": r.status,
                        "notes": r.notes,
                    }
                    for r in results
                ],
                indent=2,
            )
        )
    else:
        _print_table(results)
        if args.profile:
            for r in regimes:
                _print_profile(r.key)

    if args.gate:
        # F6 CI gate: enforce correctness invariants AND the speed ceilings.
        # Summary goes to stderr so it never corrupts --json on stdout.
        failures = _evaluate_gate(results)
        _print_gate_summary(results, failures)
        return 0 if not failures else 1

    # Default (non-gate) behaviour: exit non-zero only on a correctness invariant
    # failure — the seed the F6 gate builds on. Speed is reported, never enforced,
    # so the profiling tool stays usable on a slow or loaded dev machine.
    ok = all(r.layouts_valid and r.paths_valid and r.deterministic for r in results)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
