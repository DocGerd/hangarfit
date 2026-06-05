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

    # Exit non-zero if any correctness invariant failed — the seed of the F6 gate.
    ok = all(r.layouts_valid and r.paths_valid and r.deterministic for r in results)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
