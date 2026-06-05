# Spike: profiling the solve→tow pipeline — where the wall-clock actually goes

- **Status:** Findings + recommendation. The benchmark/profiling harness ships
  (`bench/`); no production speedup lands from this spike itself. Accepted levers
  graduate to their own implementation issues (see the ranked table).
- **Date:** 2026-06-05
- **Spike issue:** [#381](https://github.com/DocGerd/hangarfit/issues/381)
- **Consumes into:** [#403](https://github.com/DocGerd/hangarfit/issues/403) (F6 —
  CI gates + one measured lever), [#404](https://github.com/DocGerd/hangarfit/issues/404)
  (F7 — pool-stagnation early-exit), milestone
  [#31](https://github.com/DocGerd/hangarfit/milestone/31) (v0.11.0).
- **Prior art (don't re-tread):** [#336](https://github.com/DocGerd/hangarfit/issues/336)
  (RRT-Connect NO-GO; grid heuristic routed aviat_husky 601 s → 136 s),
  [#335](https://github.com/DocGerd/hangarfit/issues/335) (`_MAX_EXPANSIONS` bump),
  [#280](https://github.com/DocGerd/hangarfit/issues/280) (spread-vs-towability
  tension), [#331](https://github.com/DocGerd/hangarfit/issues/331)/[#332](https://github.com/DocGerd/hangarfit/issues/332)
  (CNN NO-GO).

---

## TL;DR

The premise everyone (including this spike's own issue and the `solve()`
docstring) had been repeating — *"tow-planning is the dominant cost on
multi-plane fills"* — **is false for the default path.** The measured profile
says:

1. **On the default (spread-ON) path, placement dominates routing by ~50×.** A
   3-plane roomy solve spends **40.6 s in placement and 0.77 s in routing**.
2. **99.6 % of that placement time is the spread post-pass** (`_spread`), and
   inside it the cost is `collisions.check` (57 %) + `_inter_plane_energy`
   (41 %). The "default solve is always ~30 s" pain ([#404](https://github.com/DocGerd/hangarfit/issues/404))
   is the spread hill-climb, not the planner.
3. **The single cross-cutting bottleneck is shapely polygon (re)construction.**
   `geometry.aircraft_parts_world` rebuilds every aircraft's part polygons from
   scratch on *every* collision check, in both stages. There is no memoization
   and no AABB broad-phase before the exact overlap predicate.
4. **The highest-leverage lever is therefore "boring" and determinism-safe:**
   cache / broad-phase the per-check geometry. It is a pure-function
   optimization → byte-identical output → zero ADR-0003 risk and zero canary
   churn. Routing-side cleverness (tighter A\*, warm-start) is *low* payoff
   because routing is already <1 s on the paths that matter.

This is exactly the "pre-commit to a boring fix over a new algorithm" outcome
[#403](https://github.com/DocGerd/hangarfit/issues/403) hoped for — and it is the
*fourth* heavy-algorithm temptation (after the CNN×2 and RRT-Connect NO-GOs) that
the measurement defused.

---

## Method

The harness lives in [`bench/`](../../bench/) (`python -m bench.profile_pipeline`);
see [`bench/README.md`](../../bench/README.md). Two deliberate choices make the
numbers trustworthy and comparable:

- **Bind on `max_restarts`, not the wall-clock `budget_s`.** Fixing the restart
  count fixes the *work*, so wall-clock is comparable run-to-run and machine-to-
  machine (the same reason ADR-0003 scopes determinism to `max_restarts`). A
  wall-clock budget would let the achieved restart count drift under CPU load and
  turn the numbers into noise.
- **Route via a direct `plan_fill` call.** `solve()` forwards only the *per-plane*
  tow budget; the global fill cap (`max_total_expansions`) is reachable only by
  calling `plan_fill` directly. Routing through it lets the un-routable regimes be
  bounded instead of running to the 16000-expansion module default (~hundreds of
  seconds). For the **fast** regimes (`tow_max_total_expansions=None`) this is
  *exactly* what `solve(plan_paths=True)` runs internally, so `placement_s +
  routing_s` is a faithful decomposition of end-to-end wall-clock. The two
  **heavy** regimes deliberately pass a tighter global cap than `solve()` ever
  does, so their `routing_s` / "un-routable" verdict is a harness-specific *lower
  bound* on what `solve()` would spend before bailing at its 16000 default — not
  a reproduction of it.

The harness also asserts three correctness invariants per regime (the substrate
F6/#403 will promote to always-on CI gates): **validity** (every layout scores
`(0, 0.0)`), **path-validity** (every committed arc passes `path_first_conflict`
at 0.05 m / 1° against the faithful back-first obstacle context), and
**determinism** (a second run yields a byte-identical layout + plan digest).

> cProfile inflates absolute time (~2×) but the **relative attribution and call
> counts are faithful**; the un-profiled `bench` table gives true wall-clock.
> All figures below are fixed-seed (`seed=1`) on one developer machine — read the
> *ratios, call counts, and %-of-stage*, not the absolute seconds.

### Regimes

| Regime | Scenario | Planes | Hangar | Spread | Restarts |
|---|---|---:|---|---|---:|
| `trivial_single` | `solve_trivial_single_plane` | 1 | 30×25 | on | 20 |
| `roomy_three_spread_on` | `solve_fresh_alternatives_three` | 3 | 30×25 | on | 30 |
| `roomy_three_spread_off` | `solve_fresh_alternatives_three` | 3 | 30×25 | off | 30 |
| `full_nine_spread_on` | `solve_all_nine_large_hangar` | 9 | 30×25 | on | 4 |
| `tight_six_placeholder` | `solve_fresh_six_planes` | 6 | 25×18 | on | 6 |

---

## Findings

### 1. Placement vs routing (un-profiled wall-clock)

| Regime | placement_s | routing_s | total_s | note |
|---|---:|---:|---:|---|
| `trivial_single` | 0.01 | 0.20 | 0.21 | 1 plane — routing > placement only because placement is trivial |
| `roomy_three_spread_on` | **40.64** | 0.77 | 41.41 | **placement = 53× routing** |
| `roomy_three_spread_off` | 0.001 | 0.89 | 0.89 | spread OFF early-exits at 1 restart → placement vanishes |
| `full_nine_spread_on` | 25.75 | **137.60** | 163.36 | un-routable (bailed @8000-cap) — routing dominates here |
| `tight_six_placeholder` | 15.59 | **69.50** | 85.09 | un-routable (bailed @4000-cap) — routing dominates here |

The `spread_on` vs `spread_off` contrast on the *same* 3-plane scenario is the
headline: turning spread off collapses placement from **40.6 s to 1 ms**. The
default path's entire cost is the spread post-pass.

### 2. Where placement time goes (`roomy_three_spread_on` cProfile)

| Stage | cum. time | % of placement | calls |
|---|---:|---:|---:|
| spread post-pass (`_spread`) | 88.76 s | **99.6 %** | 30 |
| ↳ `collisions.check` | 51.00 s | 57.2 % | **56,130** |
| ↳ `_inter_plane_energy` | 36.91 s | 41.4 % | 48,660 |
| ↳ `_parts_conflict` | 8.62 s | 9.7 % | **2,189,070** |
| `_descent_step` | 0.35 s | 0.4 % | 36 |

The descent (finding a *valid* layout) is nearly free; the spread polish (making
it *pretty*) is the whole bill. Each spread candidate move re-runs a **full**
`collisions.check` and a **full** `_inter_plane_energy` even though only one
plane moved.

### 3. Where routing time goes (`trivial_single` cProfile)

| Stage | cum. time | % of routing | calls |
|---|---:|---:|---:|
| `path_first_conflict` (final-arc re-check @0.05 m/1°) | 0.336 s | **80.8 %** | 1 |
| ↳ `aircraft_parts_world` (shapely rebuild) | 0.317 s | — | 1,207 |
| ↳ `collisions.check` | 0.167 s | 40.2 % | 496 |
| `_motion_clear` (fast in-search check) | 0.058 s | 13.9 % | 100 |
| `_build_grid_heuristic` | 0.016 s | 3.8 % | 1 |
| Reeds–Shepp enumeration | <0.001 s | 0.1 % | 1 |

Routing a single plane's path triggers **12,070 shapely `Polygon.__new__`
calls** — every pose sample of the fine final re-validation rebuilds all world
parts from scratch. The A\* search math (Reeds–Shepp, grid heuristic) is
negligible; the cost is *geometry construction inside the collision checks*.

### 4. Heavy regimes — where routing *does* dominate (un-routable fills)

Routing only overtakes placement when the fill is **un-routable and floods the
expansion budget**. Both heavy regimes bailed (`no_feasible_path`) at their
bounded global cap, and there routing is the bill:

| Regime | placement_s | routing_s | routed |
|---|---:|---:|---|
| `full_nine_spread_on` (bail @8000) | 25.75 | **137.60** | 0/1 |
| `tight_six_placeholder` (bail @4000) | 15.59 | **69.50** | 0/1 |

Routing cProfile, `full_nine_spread_on`:

| Stage | cum. time | % of routing | calls |
|---|---:|---:|---:|
| `_motion_clear` (in-search fast check) | 312.95 s | **98.7 %** | **432,080** |
| ↳ `aircraft_parts_world` | 243.22 s | — | **865,459** |
| ↳ shapely `Polygon.__new__` | 149.20 s | — | **8,621,118** |
| ↳ `_mover_motion_bounds_conflict` | 148.50 s | — | 432,744 |
| `_build_grid_heuristic` | 0.30 s | **0.1 %** | 2 |
| Reeds–Shepp enumeration | 0.01 s | 0.0 % | 33 |

Two things this nails down:

- **The grid-heuristic-rebuild hypothesis is refuted.** A reasonable guess was
  that the Dijkstra grid rebuild dominates un-routable fills; the data says it is
  **0.1 %**. The cost is `_motion_clear` flooding the expansion budget, and under
  it, again, `aircraft_parts_world` → shapely. (`tight_six` is identical: 98.0 %
  `_motion_clear`, 227,497 calls → 455,009 `aircraft_parts_world` → 4.55 M
  polygons.)
- **The routing redundancy is the *mover's* parts rebuilt twice per pose — not
  the obstacles.** Obstacle geometry is *already* built once per `plan_path` and
  reused for every sample (`_Obstacles` / `_build_obstacles`, towplanner.py).
  The tell is the call ratio: 432,080 `_motion_clear` → 865,459
  `aircraft_parts_world` is exactly **2.00×**, not the ~9× you would see if all
  eight obstacles were rebuilt per sample. The 2× is the *mover's* parts
  reconstructed once in `_motion_clear` (towplanner.py:1514) and again in
  `_mover_motion_bounds_conflict` (:979) for the **same** pose. A per-(plane,
  pose) memo collapses both to one build — byte-identical, and the routing-side
  application of the very same lever as placement.

### 5. The one root cause

Every regime — placement *and* routing, routable *and* not — bottlenecks on
`geometry.aircraft_parts_world` → `geometry.oriented_rect` → shapely `Polygon`
construction. The one existing cache (`_Obstacles`) already holds *obstacle*
parts constant within a `plan_path`, but everything else — every placement-side
`collisions.check`, every `path_first_conflict` sample, and the mover in
`_motion_clear` — rebuilds parts from scratch with **no memoization and no
broad-phase**. A single un-routable 9-plane fill still constructs **8.6 million**
shapely polygons. That one function is the lever.

---

## Ranked proposal table

Eight candidates (the #381 seed list plus the two the profile surfaced) were each
researched against the code and then **adversarially verified** — the verifiers
*instrumented the live code* (cache hit-rates, A\* expansion counts, bit-level
float divergence) rather than reasoning in the abstract, which corrected several
first-pass payoff guesses in both directions. Payoff is scored against the
*measured* numbers: a lever that only speeds up routing on the default path is
**low** payoff because routing is already <1 s there.

| Candidate | Payoff | Risk | Determinism | Verdict |
|---|---|---|---|---|
| **Memoize `aircraft_parts_world`** (per-solve, exact-key, no eviction) | **high** — 83.8 % of calls are redundant rebuilds (314,460 calls / 50,889 unique poses on roomy-3); cross-cuts *both* stages and the un-routable 8.6 M-polygon case | low | **none — byte-identical** (verified end-to-end: memoized solve → identical `SolveResult` digest; GEOS distance bit-matched) | **BUILD** — this is F6's "one cheap lever" |
| **Incremental single-plane re-scoring in `_spread`** | **high** — `_spread` is >99 % of placement | medium — the naive delta-update drifts ~1e-15 in 29 % of moves and flips acceptance; the safe form re-sums *all* pairs in canonical sorted order | safe-with-care (canonical re-sum is byte-identical; delta-update **breaks** it) | build **after** caching |
| **AABB / circle-distance broad-phase** in `_parts_conflict` | medium — payoff overstated ~5–7×: `collisions.check` rebuilds polygons *unconditionally* before the pairwise loop the filter sits in, so it can't shrink the 86 % rebuild; caching is the real win | low | none — byte-identical (a per-axis gap is a sound lower bound) | build after caching; **do not** copy `_motion_clear`'s z-prefilter (divergence trap) |
| Routing-side mover-parts caching across `path_first_conflict` / `_motion_clear` | high *for un-routable fills* — `_motion_clear` is 98 % of that routing; the mover's parts are rebuilt **twice per pose** (`_motion_clear` + `_mover_motion_bounds_conflict`), 2.00× ratio. (Obstacles are already cached per `plan_path`.) | low | none — byte-identical, RNG-free planner | folds into the memoization lever |
| `incremental_collision_across_restarts` | the determinism-safe subset *is* the per-solve memoization lever above | — | safe only as per-restart, exact-key, **no eviction** (the named LRU variant breaks basin selection) | folds into the memoization lever |
| `grid_heuristic_rebuild_caching` | **~0** — instrumented: 0 cache hits (obstacles grow monotonically); `_build_grid_heuristic` is 0.1 % of routing | — | not robustly safe (h participates in heap pop order) | **REJECT** |
| `warm_start_tow_from_placement` | **~0** — instrumented: the analytic Reeds–Shepp shot closes in **0 expansions** on routable cases; nothing to warm-start | — | breaks (changes the returned arc) | **REJECT** |
| `plane_ordering_restart_strategy` | **~0** — descent is <0.4 s; not the bottleneck | — | breaks (reorders the single seeded RNG draw sequence) | **REJECT** |
| `astar_heuristic_tiebreak` | low — routing is cheap on the default path | — | breaks (changes which arc `plan_path` returns first) | **DEFER** |

**The whole table collapses to one lever:** a per-solve, exact-key, no-eviction
**memoization of `aircraft_parts_world`**, applied across placement
(`collisions.check`) *and* routing (`_motion_clear` obstacle parts +
`path_first_conflict`). It is byte-identical → zero canary churn, zero ADR-0003
risk — exactly the "boring fix over a new algorithm" F6 pre-committed to. The
broad-phase and incremental-`_spread` levers are smaller, byte-identical
follow-ups *after* it. Two plausible levers (grid-rebuild caching, warm-start)
were killed by direct measurement — the value of measuring first.

### Filed follow-up issues

- _(primary, → F6/#403's lever)_ [#453](https://github.com/DocGerd/hangarfit/issues/453)
  — Cache `aircraft_parts_world` geometry per solve. **v0.11.0.**
- _(secondary, blocked-by #453)_ [#454](https://github.com/DocGerd/hangarfit/issues/454)
  — AABB / circle-distance broad-phase in `collisions._parts_conflict`. Backlog.
- _(secondary, blocked-by #453)_ [#455](https://github.com/DocGerd/hangarfit/issues/455)
  — Incremental single-plane re-scoring in `_spread` (canonical re-sum). Backlog.

`grid_heuristic_rebuild_caching`, `warm_start_tow_from_placement`,
`plane_ordering_restart_strategy` (all REJECTED) and `astar_heuristic_tiebreak`
(DEFER) are intentionally *not* filed — recorded here so they are not
re-proposed without new evidence.

---

## Determinism contract (ADR-0003) — recommendation: **keep as-is**

**The contract's measured cost is near-zero.** The canary asserts byte-identity
on `placements` / `best_partial` / `seed` — *not* on timing, and *not* on how a
score is computed. Every high-value lever the profile surfaced (memoization,
broad-phase, incremental moved-plane re-scoring) returns the **identical**
conflict-count + penetration tuple, so it is byte-identical and the contract
leaves none of those ~40 s on the table.

The only forbidden lever with real headroom is **parallel restarts** (RR-MC is
embarrassingly parallel, but bit-identical reproducibility forces a single
`random.Random(seed)`; a parallel variant needs per-worker RNGs + a deterministic
merge). The cheaper, byte-identical geometry-caching lever attacks the *same*
40.6 s first — so the contract is not what stands between us and a fast solver.
First-valid early-exit on the spread-ON path was already traded away in #267
(best-spread-over-first-valid), and spread-OFF keeps it; #404/F7's seed-fixed,
machine-independent early-exit is *admissible* under the contract and narrows
#267's timing scope rather than widening it.

**Recommendation: keep the contract unchanged.** Relaxing it would surrender the
determinism canary, the shareable-seed claim, and reviewable diagnostics — for
levers the profile shows are not the bottleneck. Pursue geometry caching,
preserving float-summation order in the penetration and energy accumulators.

---

## Out of scope (unchanged from #381)

Implementing the speedups (each is its own follow-up issue), CNN approaches
(#331/#332), and articulated trailers (#204).
