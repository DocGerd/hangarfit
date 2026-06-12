# Spike: third-pass performance — local test wall-clock, CPU under-utilization, and the post-#604 mover re-profile

- **Status:** Findings + recommendation. No production speedup lands from this
  spike itself; accepted levers graduate to their own implementation issues (see
  the ranked table), mirroring #381 → #453/#454/#455 and #540 → #544/#545/#546/#547.
- **Date:** 2026-06-12
- **Spike issue:** [#617](https://github.com/DocGerd/hangarfit/issues/617)
- **Builds on:** [#381](https://github.com/DocGerd/hangarfit/issues/381) (first
  profiling pass), [#540](https://github.com/DocGerd/hangarfit/issues/540) (second
  pass — the architecture levers), [#476](https://github.com/DocGerd/hangarfit/issues/476)/[#492](https://github.com/DocGerd/hangarfit/issues/492)
  (CI xdist two-pass), and the shipped byte-identical levers #453/#454/#455
  (geometry memo / AABB broad-phase / spread gap-cache) + #544 (parallel restarts).
- **The reason to re-profile:** [#604](https://github.com/DocGerd/hangarfit/issues/604)
  / PR [#616](https://github.com/DocGerd/hangarfit/pull/616) threaded
  ground-object **movers** (cars/trailers) through the placement search and the
  tow planner — the #540 numbers predate that.
- **Machine:** Intel i9-13900F (32 logical cores), 31 GiB RAM, Python 3.12.3,
  WSL2. Read the **ratios and %-of-stage**, not the absolute seconds — a faster
  or slower box scales them.

---

## TL;DR

1. **"Slow tests + idle CPU" is fully explained, and measured.** A developer's
   plain `pytest -m "not slow"` runs **serial**: **588 s at 111 % CPU** — one core
   of 32 busy, 31 idle. The fix is already known and shipped *for CI* (#492's
   two-pass split) but was **never wired for local dev**: running the same split
   locally is **588 s → ~169 s (3.5×)**, and the parallel bulk alone is **113 s
   (5.2×)**. This is the single cheapest, highest-value win in the spike and it is
   a **test-config change, zero solver risk**.

2. **The algorithm itself is single-threaded by default** — the bench harness runs
   at **102 % CPU**. Per-restart work is irreducibly serial (min-conflicts descent
   + spread hill-climb); only *restarts* parallelise, via `--workers N` (#544),
   and only in the `--max-restarts` + spread-on regime — which **defaults to
   `workers=1`**. So multi-core sits idle on every default solve. Not a bug: a
   documented design point with one small lever (a smarter default).

3. **Placement is still geometry-construction-bound, and #604 did not change that
   for aircraft-only regimes.** The default `roomy_three_spread_on` path is 98.8 %
   spread post-pass, inside which **world-part build (shapely) is 35.5 %** and the
   exact collision predicate is **0.4 %** (the #454 AABB broad-phase still rejects
   ~98 %). Identical shape to #540 — the cheap byte-identical wins remain spent;
   the determinism-safe headroom remains **parallel restarts (#544, shipped)**.

4. **#604 introduced a real, new, measurable cost: mover-routing congestion.**
   Routing the #604 right-region demo (2 aircraft + **2 right-preferring glider
   trailers** + 1 fixed fuel trailer) took **245 s and then BAILED**, versus
   **3.4 s** for the identical scenario with **no** ground objects and **11.8 s**
   when the trailers prefer the **left** wall. The asymmetry is the tell: the soft
   right-region bias (`_region_energy`, #604) packs the right wall, then the
   planner routes the trailers **last** into that self-made congestion. The profile
   pins the cost to **geometry-rebuild churn** — and the root cause is concrete:
   **movers bypass the #453 pose cache** (`_body_parts_world` sends GroundObjects
   to the *uncached* `aircraft_parts_world`), reintroducing for movers exactly the
   rebuild cost #453 eliminated for aircraft.

5. **The @slow perf canary over-budgets on slow boxes doing byte-identical work.**
   `tests/test_towplanner_perf.py::_PLAN_FILL_CEILING_S = 400 s` is an **absolute
   wall-clock** ceiling (calibrated `~271 s × 1.5` on a dev machine). On WSL2 the
   same deterministic fill exceeds it without any regression. It is `@slow` (never
   in CI), so this only bites a developer running `pytest -m slow` — but it should
   be made host-relative or skip-on-slow-host so it stops crying wolf.

---

## Method

- **Axis A (test suite):** a clean serial measurement harness ran, in sequence on
  an otherwise-idle box, (A1) `pytest -m "not slow" --durations=50` serial, (A2)
  `pytest -n auto -m "not slow and not serial"`, (A3) `pytest -m "serial and not
  slow"`, each wrapped in GNU `time -v` to capture **wall-clock + "Percent of CPU
  this job got"** (the under-utilization metric).
- **Axis B (algorithm):** `python -m bench.profile_pipeline` (fast) and `--heavy
  --profile` (cProfile stage breakdown), the #381/#540 harness, which binds on
  `max_restarts` (fixed work, only machine speed varies).
- **Post-#604 (new):** a dedicated probe (the bench regimes predate #604 and have
  no movers) timed `solve()` placement + `plan_fill()` routing on
  `scenario_region_demo.yaml` (movers, right pref) vs `…_no_go.yaml` (baseline)
  vs `…_left.yaml` (left pref), plus a cProfile attribution of the mover routing.

---

## Axis A — test-suite wall-clock & CPU utilization

### The numbers (GNU `time -v`, 1661 non-slow tests)

| Run | Command | Wall | % CPU | Cores busy (of 32) |
|---|---|---:|---:|---:|
| **A1 serial** (a dev's plain run) | `pytest -m "not slow"` | **588.1 s** | **111 %** | ~1 |
| **A2 parallel bulk** | `pytest -n auto -m "not slow and not serial"` | **113.1 s** | 733 % | ~7 |
| **A3 serial canaries** | `pytest -m "serial and not slow"` | 56.2 s | 135 % | ~1 |
| **Two-pass total (A2 + A3)** | the CI-mirroring split | **~169 s** | — | — |

- **Serial baseline = 588 s @ 111 % CPU** is the literal answer to "why slow + idle
  CPU": the default `addopts = "-ra -m 'not slow'"` has **no `-n auto`**, so a dev's
  `pytest` is single-process on a 32-core box.
- **Two-pass = 169 s ⇒ 3.5×**; the parallel bulk alone is **5.2×**. This exactly
  mirrors CI's #492 split (`ci.yml:88-90`) — which was wired for CI but **left
  local dev serial**.
- **A2 only reached 733 % CPU (~7 cores), not 3200 %.** The ceiling is an **Amdahl
  floor** from a handful of heavy `budget_s` solver tests (the #476 spike predicted
  this: 4→32 workers buys little). The `--durations` top offenders confirm it:

  | test | s |
  |---|---:|
  | `test_solver_nose_out::…prefers_out` | 26.7 |
  | `test_solver_towplanner::…bundle_is_deterministic` | 21.5 |
  | `test_solver_nose_out::…byte_identical` (×3) | ~19 each |
  | `test_solver_region::test_region_pref_pulls_trailer_right` | 17.5 |
  | `test_cli_view::…untowable` · `test_towplanner_grid_heuristic::…global_cap` | ~11.6 |
  | `test_solver_parallel::…` (×2) · `test_solver_ground_objects::…byte_identical` | ~10–11 |

  So past ~8 workers, further test speedup needs attacking **these heavy
  wall-clock tests** (smaller `budget_s`/`max_restarts` fixtures), not more cores.

### Why a bare `pytest -n auto` is NOT the fix

The 7 `@serial` wall-clock determinism canaries double-solve under a `budget_s`
deadline and assert byte-identity; under xdist sibling-worker CPU starvation the
two in-process solves complete different restart counts and **flake** (`pyproject`
markers note + `docs/dev/test-flakes-and-ci-gotchas.md`). `xdist_group` is
insufficient (it co-locates the group but does not stop other workers saturating
the CPU). The split — parallel pool for `not slow and not serial`, separate serial
pass for `serial` — is mandatory; a developer must mirror it.

### The @slow perf canary (WSL2 over-budget)

`tests/test_towplanner_perf.py:51 _PLAN_FILL_CEILING_S = 400.0` is an **absolute**
wall-clock ceiling (`~271 s × 1.5`, dev-machine-calibrated). `plan_fill` is RNG-free
and `max_restarts`/expansion-bound (#336's global cap), so the *work* is fixed;
only machine speed varies. On a slower box (WSL2) the byte-identical fill exceeds
400 s and the canary fails with **no regression**. It is `@slow` (excluded from CI
and from the default run), so it only bites `pytest -m slow` — but the ceiling
should be host-relative (calibrate against a quick warm-up solve) or skip-on-slow-host.

---

## Axis B — algorithm (re-profiled, post-#604)

### Placement vs routing (bench, `max_restarts`-bound, 102 % CPU = single core)

| Regime | place_s | route_s | total_s | routed | note |
|---|---:|---:|---:|---|---|
| `trivial_single` (R=20) | 0.01 | 0.31 | 0.32 | 1/1 | |
| **`roomy_three_spread_on`** (R=30) | **25.2** | 4.0 | 29.2 | 1/1 | the default path — placement dominates |
| `roomy_three_spread_off` (R=30→exit@1) | 0.002 | 5.8 | 5.8 | 1/1 | spread OFF collapses placement to ~0 |
| `roomy_three_apron` (R=30) | 24.6 | 7.1 | 31.7 | 1/1 | apron is planner-only (placement ≈ no-apron) |
| **`full_nine_spread_on`** (R=4) | 12.2 | **261.3** | 273.4 | 0/1 | un-routable — **routing dominates** |
| `full_nine_placement` (R=8, tiny tow cap) | 30.6 | 12.2 | 42.8 | 0/1 | placement-guard regime |
| `tight_six_placeholder` (R=6) | 7.9 | 123.1 | 131.0 | 0/1 | un-routable |
| `tight_six_apron` (R=6) | 8.0 | 134.8 | 142.9 | 0/1 | un-routable |

The #540 structure **holds, unchanged**: placement dominates the cheap *feasible*
spread-ON path (it **is** the spread post-pass — `spread_off` collapses it to
0.002 s), routing dominates the *un-routable* heavy fills (the expansion budget
floods). #604's movers don't appear in these aircraft-only regimes, so the
aircraft-only mix is identical to #540 — as expected.

### Where placement time goes NOW (cProfile, `roomy_three_spread_on`, cumtime %)

| Stage | % of placement | ncalls | reading |
|---|---:|---:|---|
| spread post-pass | **98.8 %** | 30 | placement *is* the hill-climb |
| ↳ collisions.check | 70.9 % | 50,227 | |
| ↳↳ **world-part build (shapely)** | **35.5 %** | 45,553 | the dominant primitive — geometry construction |
| ↳ inter-plane energy | 25.9 % | 44,007 | #455 gap-cache holds it here |
| ↳↳ exact parts-conflict | **0.4 %** | 25,630 | narrow phase is tiny — #454 AABB rejects ~98 % |
| ↳↳ polygon overlap (shapely) | 0.3 % | 25,630 | |

Same conclusion as #540: **geometry-construction-bound, not predicate-bound.** You
cannot cache your way past evaluating new candidate poses (the cache is already
~84 % hit, the misses are genuinely-new poses); you spread that across cores
(#544) or make the per-pose primitive cheaper.

### Routing time goes to the same place (cProfile, `roomy_three_spread_on`)

`world-part build (shapely)` **69.9 %**, `path_first_conflict re-check` 78.8 %,
`collisions.check` 48.1 %, the in-transit swept-bounds check
(`_mover_motion_bounds_conflict`, despite the name applies to the towed body too)
35.4 %. Routing is **also geometry-construction-bound** — the same `aircraft_parts_world`
shapely-Polygon rebuild, now per sampled pose along every primitive.

### Post-#604: the mover-routing congestion (new — the probe)

`solve()` placement + `plan_fill()` routing on the #604 region demo, seed 7:

| scenario | #GO | placement_s | routing_s | result |
|---|---:|---:|---:|---|
| **`region_demo`** (2 ac + 2 **right**-pref trailers + fixed) | 3 | 10.0 | **245.3** | **BAIL** (`no_feasible_path`) |
| `region_demo_no_go` (baseline, 0 GO) | 0 | 10.0 | **3.4** | routed ✓ (2 moves) |
| `region_demo_left` (trailers prefer **left**) | 3 | 10.0 | 11.8 | routed ✓ (4 moves) |

**The right-region 2-trailer fill is ~72× the no-GO routing and then fails;** the
left variant routes fine. cProfile of the mover routing (cap-bounded for cheap
attribution, 674 M function calls):

| symbol | cumtime | calls | reading |
|---|---:|---:|---|
| `_motion_clear` | 270 s (98 %) | 257,687 | per-expansion collision sweep |
| `cached_parts_world` (aircraft obstacles) | 231 s | 520,923 | the #453 cache *is* used for aircraft |
| **`aircraft_parts_world` (uncached — movers + towed body)** | **231 s** | **523,684** | movers **bypass** the cache → rebuilt every check |
| `_mover_motion_bounds_conflict` (#602) | 140 s | 260,476 | the mover swept-bounds in-transit test |
| `shapely Polygon.__new__` | (churn) | **7.27 M** | the allocation the rebuilds produce |

**Root cause (concrete):** `_body_parts_world` (`solver.py:1220-1230`) routes
aircraft to the pose-memoized `cached_parts_world` but a **GroundObject mover to
the union-typed uncached `aircraft_parts_world`** — by design (#604/#602 matched
the towplanner's mover geometry). For a *static* mover obstacle (an already-placed
trailer) this rebuilds its world parts on **every** `_motion_clear` sample, which
is exactly the rebuild churn #453 eliminated for aircraft. The congestion then
multiplies it: more expansions × more bodies × uncached mover geometry.

---

## Why is CPU under-utilized? (the question, answered)

| Layer | Parallel? | Why idle by default |
|---|---|---|
| **Test suite** | Yes (xdist) | `addopts` has no `-n auto`; local dev runs serial (588 s @ 111 %). CI fixed this (#492); local never did. |
| **Restarts** | Yes, process-level (#544) | `--workers` defaults to **1**; parallel only in the `--max-restarts` + spread-on regime. |
| **Within a restart** (descent + spread) | **No** | Irreducibly serial — each step depends on the previous placement state. |
| **Geometry / shapely** | **No (per call)** | GEOS scalar `.distance`/`.intersects` don't release the GIL on the path hangarfit uses; #540 measured a ThreadPool **5× slower**. Only a ProcessPool helps (that *is* #544). |

So: serial test runner **and** a single-threaded-by-default solver. Both are
explainable and (partly) fixable; neither is a mystery or a regression.

---

## Ranked levers

Determinism class is the gate (ADR-0003 byte-identity; `determinism-guard` runs on
`solver.py`/`towplanner.py`). Payoff is scored against the measured numbers above.

| # | Lever | Payoff | Determinism | Verdict |
|---|---|---|---|---|
| **L1** | **Local two-pass test invocation** (Makefile `test`/`test-fast` + doc) mirroring CI's #492 split | **high** — 588 s → ~169 s (3.5×), bulk 5.2×; the dev-experience headline | n/a (test-config only; the serial split *preserves* the canary contract) | **BUILD** |
| **L2** | **Host-relative @slow perf-canary budget** (`test_towplanner_perf`) — calibrate vs a warm-up solve or skip-on-slow-host | medium (stops false WSL2 failures) | n/a (@slow, never gates CI/coverage) | **BUILD** |
| **L3** | **Extend the #453 pose cache to GroundObject movers** (`_body_parts_world` uncached path) | **high for mover scenarios** — directly attacks the 231 s uncached `aircraft_parts_world` in the congestion profile; static mover obstacles stop rebuilding | **byte-identical** (cache returns the same immutable object; key already pose-generic) | **BUILD** |
| **L4** | **Mover routing-order / congestion handling** — route congested-region (or `hard_door_mover`) bodies first, and/or reserve a per-mover expansion floor / adaptive cap so a late trailer doesn't starve the global budget | **high for mover fills** — the 245 s→BAIL case; turns "blow the budget then fail" into "fail fast or route" | safe-with-care (the order/threshold must be RNG-free & deterministic; `towplanner.py` is determinism-guarded) | **FILE** (design needed) |
| **L5** | **Smarter `--workers` default** in the eligible regime (`--max-restarts` + spread-on) — e.g. auto-suggest or opt-in `auto` | low-medium wall-clock; removes the "32 idle cores" surprise on multi-restart solves | none (default stays 1; only the explicit-parallel path changes) | **FILE** (low priority) |
| **L6** | Per-iteration world-parts snapshot reuse in `_spread` (rebuild only the moved body) | low — #453 already makes unmoved *aircraft* O(1); the residual is *movers*, which **L3 subsumes** | byte-identical (immutable WorldParts) | **DEFER** (gated on L3 not sufficing) |
| **L7** | Attack the heavy `budget_s` tests behind the Amdahl floor (smaller fixtures for the nose-out / determinism-bundle tests) | low-medium (lifts the ~7-core parallel ceiling) | n/a (test-fixture sizing) | **DEFER** (only if 3.5× isn't enough) |

**REJECTED (recorded so they are not re-proposed without new evidence):**
placement-side STRtree (#540 measurement-killed: 97.6–99.2 % AABB-rejected),
most-conflicted-first descent ordering (#540: ~0 payoff, breaks seeded draws),
within-restart candidate parallelism (determinism-fragile, marginal). **GPU/CUDA
of the current algorithm is its own spike** ([#620](https://github.com/DocGerd/hangarfit/issues/620),
in the GPU set [#623](https://github.com/DocGerd/hangarfit/issues/623)) — out of
scope here.

---

## Recommendation

Ship **L1** (local two-pass — the cheap 3.5× dev win) and **L2** (host-relative
perf canary) first: both are test-config, zero solver risk. Then **L3** (mover
pose-cache — byte-identical, attacks the #604 congestion root cause) and design
**L4** (mover routing order / budget — the real fix for the 245 s→BAIL case). L5 is
a nice-to-have default. The determinism contract is, a third time, **not** the
bottleneck — every BUILD lever here is byte-identical or test-only.

---

## Filed follow-up issues

- [#624](https://github.com/DocGerd/hangarfit/issues/624) **(L1, BUILD)** Local
  two-pass test invocation (Makefile) mirroring CI's #492 split — the 3.5× dev win.
- [#625](https://github.com/DocGerd/hangarfit/issues/625) **(L2, BUILD)** Make the
  @slow `plan_fill` perf canary host-relative (stop WSL2 false-fails on
  byte-identical work).
- [#626](https://github.com/DocGerd/hangarfit/issues/626) **(L3, BUILD)** Extend the
  #453 pose cache to GroundObject movers (they bypass it → reintroduced
  geometry-rebuild churn — the concrete post-#604 root cause).
- [#627](https://github.com/DocGerd/hangarfit/issues/627) **(L4, FILE — design)**
  Mover routing-order / congestion handling in `plan_fill` (the 245 s→BAIL case).
- [#628](https://github.com/DocGerd/hangarfit/issues/628) **(L5, FILE — low
  priority)** Smarter `--workers` default / auto-suggest in the parallel-eligible
  regime.

**DEFERred (recorded, not filed):** L6 per-iteration world-parts snapshot reuse in
`_spread` (subsumed by #626), L7 attacking the heavy `budget_s` tests behind the
Amdahl floor (only if the 3.5× L1 win proves insufficient). **REJECTED** (see the
ranked table): placement-side STRtree, most-conflicted-first descent ordering,
within-restart candidate parallelism.

---

## Out of scope (unchanged from #381/#540)

Implementing the speedups (each its own follow-up); GPU/CUDA (the #620/#621/#622
spike set under #623); the learned backend / CNN (#331/#332/#607); relaxing
ADR-0003.
