# `bench/` — solve→tow profiling harness (#381)

A committed, repeatable measurement substrate for the `hangarfit` **solve → tow**
pipeline. It exists so future perf work measures against a fixed baseline instead
of anecdotes ("601 s → 136 s on my machine"), and so the v0.11.0 reliability
roadmap (#403/F6) has a ready foothold for turning the three correctness
invariants into always-on CI gates.

**Not shipped in the wheel.** This package lives outside `src/`, so
`pip install hangarfit` / `python -m build` / the test suite never touch it. It is
a dev/CI tool only.

## Run it

```bash
python -m bench.profile_pipeline                 # fast regimes, timing + verdicts table
python -m bench.profile_pipeline --heavy         # + 9-plane fill and tight placeholder
python -m bench.profile_pipeline --profile       # + cProfile stage attribution
python -m bench.profile_pipeline --regime trivial_single --profile
python -m bench.profile_pipeline --json          # machine-readable
```

Exit code is **non-zero** if any regime fails an invariant — the seed of the F6
CI gate.

## What it measures

Per regime it splits wall-clock into **placement** (the RR-MC restart loop) vs
**routing** (the bounded Hybrid-A* tow planner) and asserts three invariants:

| Invariant | Check |
|---|---|
| **validity** | every returned layout scores `(0, 0.0)` under `collisions.check` |
| **path-validity** | every committed tow arc passes `path_first_conflict` at `0.05 m / 1°`, re-validated against the *faithful* back-first obstacle context |
| **determinism** | a second run yields a byte-identical layout + plan digest (ADR-0003, `max_restarts`-scoped) |

`--profile` additionally buckets a routing cProfile into the sub-stages named in
#381: grid-heuristic build, Reeds–Shepp enumeration, fast `_motion_clear`, exact
`_parts_conflict`, shapely `polygon_overlap`, and the `path_first_conflict`
re-check.

## Design notes

* **Bound on `max_restarts`, not `budget_s`.** Fixing the restart count makes the
  *work* deterministic, so wall-clock is comparable run-to-run and machine-to-
  machine — the same reason ADR-0003 scopes determinism to `max_restarts`. A
  wall-clock budget would let the achieved restart count drift under CPU load.
* **Routing via a direct `plan_fill` call.** `solve()` forwards only the
  *per-plane* tow budget; the global fill cap (`max_total_expansions`) is reachable
  only by calling `plan_fill` directly. Routing through it lets the heavy
  regimes bound the un-routable "gives-up" failure mode instead of running to the
  16000-expansion module default (~hundreds of seconds). Because
  `solve(plan_paths=True)` internally calls the *same* `plan_fill` on the selected
  layouts, `placement_s + routing_s` is a faithful decomposition of the
  end-to-end wall-clock.

* **Apron regimes (#499/ADR-0021).** `Regime.apron_depth` applies a staging
  apron to the scenario's hangar (`0` ⇒ no apron, the load path stays
  byte-identical; a number or `"auto"` ⇒ apron on). It enlarges the per-plane tow
  start set and lengthens each path, so it characterises the apron's
  routing-cost effect. Finding: the apron is planner-only (placement unchanged),
  feasible fills route at the default budgets, and the un-routable disprove rises
  only modestly — no budget re-tune needed. See the
  [profiling spike §6](../docs/spikes/solve-tow-profiling.md).

The regimes themselves are defined in [`regimes.py`](regimes.py); they reference
committed `tests/fixtures/*.yaml` scenarios so the harness has no private data.

> **Editor note:** the intra-package imports (`from .regimes import …`) may show
> as unresolved in Pyright — the repo intentionally ships no `pyrightconfig.json`
> ([.claude/README.md](../.claude/README.md): *mypy is the CI gate, Pyright is the
> live editor signal*), and `bench/` is outside the mypy/CI scope. Run via
> `python -m bench.profile_pipeline`; the imports resolve correctly at runtime.
