# Spike: second-pass solve→tow performance — the architecture levers beyond #381's single lever

- **Status:** Findings + recommendation. No production speedup lands from this
  spike itself; accepted levers graduate to their own implementation issues (see
  the ranked table), mirroring #381 → #453/#454/#455.
- **Date:** 2026-06-09
- **Spike issue:** [#540](https://github.com/DocGerd/hangarfit/issues/540)
- **Builds on (the baseline this supersedes):**
  [#381](https://github.com/DocGerd/hangarfit/issues/381) (first profiling pass)
  and its three shipped byte-identical levers —
  [#453](https://github.com/DocGerd/hangarfit/issues/453) (per-solve
  `aircraft_parts_world` memo), [#454](https://github.com/DocGerd/hangarfit/issues/454)
  (AABB broad-phase), [#455](https://github.com/DocGerd/hangarfit/issues/455)
  (incremental `_spread` gap-cache). **The #381 tables are stale post-#453/#454/#455
  — this doc replaces them.**
- **Prior art (don't re-tread):** #336 (RRT-Connect NO-GO), #331/#332 (CNN NO-GO),
  and the three #381 REJECTs — `grid_heuristic_rebuild_caching`,
  `warm_start_tow_from_placement`, `plane_ordering_restart_strategy` — plus the
  DEFERred `astar_heuristic_tiebreak`.

---

## TL;DR

The cheap, byte-identical wins are spent. Re-profiling against the
post-#453/#454/#455 baseline and prototyping the larger architecture levers gives
four conclusions:

1. **Placement is geometry-construction-bound, not collision-predicate-bound.**
   Post-#453, `collisions.check` is **71.6 %** of placement on the default
   `roomy_three_spread_on` path — but *inside* `check` the cost is
   `aircraft_parts_world` → `oriented_rect` (the **moving** plane's per-candidate
   shapely polygon rebuilds, ~36 % of placement), **not** the part-pair sweep
   (`_pairwise_conflicts` 9.7 %, `_parts_conflict` 0.3 %). The #454 AABB
   broad-phase rejects **97.6–99.2 %** of part-pairs, so the narrow phase is
   already cheap.

2. **The one architecture lever with large, *measured* headroom is parallel
   restarts — and it is determinism-PRESERVING, not determinism-dropping.**
   A throwaway ProcessPool prototype delivered **4.5× at 8 workers** on the
   binding regime, **byte-identical** to a re-seeded serial baseline (the parallel
   merge is completion-order-invariant by construction). The only price is a
   **one-time canary re-golden + ADR-0003 amendment** (per-restart-index seeding),
   *not* surrendering reproducibility.

3. **Two tempting levers were measurement-killed.** A placement-side **STRtree**
   is net-negative at current scale (the sweep it would replace is already 97.6 %
   AABB-rejected — tree build > the cheap float-compares it saves). **Incremental
   conflict-tracking in `_spread`** attacks the `_pairwise_conflicts` 9.7 %, not
   the 36 % geometry residual — small payoff, real float-summation-order risk.
   Both join #381's graveyard.

4. **The determinism contract is, again, not what stands between us and a fast
   solver.** The headroom is in a determinism-*safe* lever (parallel restarts as a
   re-base). Dropping ADR-0003 on top of Approach A buys ~0 (the merge already
   costs microseconds) while losing the canary, shareable seeds, and reproducible
   diagnostics.

---

## Method

Same harness (`bench/`, binds on `max_restarts` not wall-clock). Three probes,
all fixed-seed (`seed=1`) on one developer machine — **read the ratios, call
counts, and %-of-stage, not the absolute seconds**:

- **Placement-only cProfile** (`solve(plan_paths=False)`) on `roomy_three_spread_on`
  (R=30) and `full_nine_spread_on` (R=4) — isolates the spread post-pass attribution
  the #381 table no longer reports post-#453/#455.
- **Part-pair census** — instrumented `_aabbs_separated_beyond_clearance` /
  `_parts_conflict` counters over the two parts-heaviest valid layouts (the
  all-nine synthetic fixture and the real Herrenteich all-8), to settle the STRtree
  question with the AABB-reject hit-rate.
- **Parallel-restart prototype** (throwaway, not committed) — each restart run as a
  pure function of `(scenario, seed_i)` under its own `pose_cache_scope` (option-A
  re-seeding), fanned across a `ProcessPoolExecutor(spawn)`, with the
  `_select_spread_diverse` total-order merge. Measured speedup vs worker count and
  **verified the parallel result is byte-identical to the serial-reseeded
  baseline**.

---

## Findings

### 1. Placement vs routing (re-measured wall-clock)

Fast set, clean (machine quiet, fixed `seed=1`, `max_restarts`-bound):

| Regime | placement_s | routing_s | total_s | routed |
|---|---:|---:|---:|---|
| `trivial_single` (R=20) | 0.01 | 0.53 | 0.54 | 1/1 |
| `roomy_three_spread_on` (R=30) | **24.85** | 4.69 | 29.54 | 1/1 |
| `roomy_three_spread_off` (R=30→exit@1) | 0.01 | 4.53 | 4.54 | 1/1 |

Heavy/apron set (these ran under light concurrent load — read the *structure*,
routing-dominates-when-un-routable, not the absolutes):

| Regime | placement_s | routing_s | routed | note |
|---|---:|---:|---|---|
| `full_nine_spread_on` (R=4) | 14.8 | **207.9** | 0/1 | un-routable, bailed @8000-cap |
| `tight_six_placeholder` (R=6) | 9.3 | **60.3** | 0/1 | un-routable, bailed @4000-cap |
| `roomy_three_apron` (R=30, 14 m) | 25.6 | 81.9 | 1/1 | apron is planner-only (placement ≈ no-apron) |
| `tight_six_apron` (R=6, 10 m) | 8.8 | 64.9 | 0/1 | un-routable disprove |

The `spread_on` vs `spread_off` contrast on the same 3-plane scenario is the
headline: spread OFF collapses placement from **24.85 s to 0.01 s** (it early-exits
at restart 1) — the default path's entire placement cost is the spread post-pass.

The structure from #381 holds qualitatively: **placement dominates the cheap,
feasible spread-ON path** (the spread post-pass), while **routing dominates the
un-routable heavy fills** (where `_motion_clear` floods the expansion budget).
What changed since #381: #453's memo roughly halved placement
(roomy-3 ≈ 40.6 s → ~24.5 s), and nose-out-default-ON (#263) raised routing on the
feasible path, so the placement-to-routing ratio on `roomy_three_spread_on`
narrowed from ~53× to ~5×.

### 2. Where placement time goes NOW — supersedes #381 §2

`roomy_three_spread_on` (R=30), placement-only cProfile (cumtime; cProfile
inflates absolutes ~2×, read the %):

| Stage | % of placement | ncalls | note |
|---|---:|---:|---|
| `_spread` (the post-pass) | **100 %** | 30 | placement *is* the spread hill-climb |
| ↳ `collisions.check` | **71.6 %** | 55,383 | the dominant half |
| ↳↳ `cached_parts_world` / `aircraft_parts_world` | **~36 %** | 310,761 / **50,162 builds** | the moving plane's new-pose shapely rebuilds |
| ↳↳ `oriented_rect` | 12.5 % | 284,190 | building the rectangles inside the rebuild |
| ↳↳ `_pairwise_conflicts` | **9.7 %** | 55,383 | the O(P²·K²) part-pair sweep |
| ↳↳ `_parts_conflict` (exact predicate) | **0.3 %** | 23,129 | the narrow phase is tiny |
| ↳ `_inter_plane_energy` | 26.7 % | 48,174 | #455's gap-cache cut this from the old 41 % |
| ↳ `polygon_overlap_area` | 0.6 % | 8,575 | |

**The key reshape:** #381 read `check` as "57 % collisions, mostly the predicate."
Post-#453 the predicate is gone (0.3 %); what's left inside `check` is
**geometry construction for the moving plane's distinct candidate poses** (~36 %),
which is *intrinsic* work — ~1 real `aircraft_parts_world` build per `check`
(50,162 builds / 55,383 checks ≈ 0.9), i.e. the cache is already near-optimal
(84 % hit; the misses are genuinely-new poses). You cannot cache your way out of
evaluating 8 new candidate poses per iteration — you can only **spread that work
across cores** (Finding 4) or make the per-pose primitive cheaper.

On the fuller `full_nine_spread_on` (R=4) the mix shifts: `_pairwise_conflicts`
rises to 25.5 % and `_parts_conflict` to 6.6 % (more planes → more pairs, and
descent runs on conflicted layouts where fewer pairs AABB-reject) — so the
narrow-phase levers have *more* headroom on full fills, but geometry construction
(`cached_parts_world` + `oriented_rect` + `polygon_overlap[_area]` ≈ 30 %; vs the
~36 % `cached_parts_world`-alone bucket on roomy-3) stays comparable.

### 3. The part-pair census — the STRtree-killer

Instrumented counts over the two parts-heaviest **valid** layouts:

| Layout | world-parts | cross-plane part-pairs | AABB-rejected | reached exact predicate |
|---|---:|---:|---:|---:|
| `full_nine` fixture (8 placed, valid) | 52 | 1,180 | 1,152 (**97.6 %**) | 28 (2.4 %) |
| Herrenteich real all-8 | 46 | 1,090 | 1,081 (**99.2 %**) | 9 (0.8 %) |

(The `full_nine` fixture's `fleet_in` is 9 distinct aircraft — theoretical max 57
world-parts / **1,440** cross-plane part-pairs; the seed-1 R=4 solve returned a
valid layout with 8 of them placed, so the censused figures are the 8-plane
52 / 1,180.)

The #454 AABB broad-phase rejects **97.6–99.2 %** of part-pairs at a few float
comparisons each; only **9–28** pairs reach the exact shapely predicate. An
STRtree/grid would replace a ≤1,180-iteration loop of 4-float compares with an
O(N log N) tree build+query **per check** — and because every plane moves each
spread candidate the tree cannot be reused across checks. At N≤57 parts this is
**net-negative**, exactly the failure mode that killed `grid_heuristic_rebuild_caching`
in #381. **Placement-side STRtree: NO-GO.** (A routing-side static-obstacle STRtree
is a *different*, untested change — but routing's bottleneck is also geometry
rebuild, not the pair sweep, so it is lean-skeptical pending its own probe.)

### 4. Parallel restarts — the central architecture question (measured)

RR-MC is embarrassingly parallel across restarts; the expensive `_spread` lives
*inside* the per-restart body (Amdahl-favourable). #381 named this "the only
forbidden lever with real headroom" but never measured it. The throwaway prototype
settles it.

**The blocker is a serial RNG dependency, not the contract.** Today
`solver.py` builds *one* `random.Random(seed)` and threads it through every
restart: restart *i* begins drawing wherever *i−1* stopped, and *i−1*'s draw count
is data-dependent. So you cannot reproduce *today's* goldens with independent
per-worker RNGs. But re-seeding each restart from its index
(`random.Random((seed, i))`) — applied to **both** the serial and parallel paths —
makes each restart a pure function of `(scenario, seed, restart_index)`, so
parallel ≡ serial-reseeded **byte-for-byte**. That is a determinism *re-base*
(re-golden the canaries once + an ADR-0003 amendment), **not** a determinism drop.

**Speed** (`roomy_three_spread_on`, R=30, placement-only, ProcessPool/spawn):

| Workers | Wall-clock | Speedup | Efficiency |
|---:|---:|---:|---:|
| serial (today, 1 warm cache) | 24.55 s | 1.00× | — |
| 2 | 13.39 s | 1.83× | 92 % |
| 4 | 8.43 s | 2.91× | 73 % |
| 8 | 5.45 s | **4.50×** | 56 % |
| 16 | 4.34 s | 5.66× | 35 % |

**Determinism verification** (per-index reseed, R=30):

| Check | Result |
|---|---|
| each single restart byte-identical run-to-run | **True** |
| serial-merge ≡ parallel-merge (completion-order-invariant) | **True** (same selected gap 12.664 m) |
| per-index reseed differs from today's serial-RNG golden | **True** (the one-time canary churn) |

**Honest caveats (the speedup is bounded):**
- **Sub-linear and falling** (56 % efficiency at 8, 35 % at 16): lost cross-restart
  pose-cache (each worker warms only its chunk), chunk-straggler imbalance, and
  process spawn (~0.26 s/pool).
- **Placement-only.** Routing is post-merge, serial, RNG-free — *not* parallelized
  by this lever. On the cheap spread-ON path (placement ≫ routing) a 4.5× placement
  speedup is most of the end-to-end win; on un-routable routing-heavy fills it does
  **~nothing**.
- **Few-restart heavy fills can be net-negative at low N** (`full_nine` R=4: N=1 was
  0.92× — the cold per-worker cache + spawn exceeded the warm-serial benefit).
- **`Scenario` is not picklable today** (`mappingproxy`), so a ProcessPool design
  must make it picklable or have workers reconstruct it from the path (cheap here:
  0.022 s load). A prerequisite, not a blocker.
- **ThreadPool is dead.** shapely/GEOS releases the GIL only on its vectorized
  ufunc path, not the scalar `.distance`/`.intersects`/`.intersection` calls
  hangarfit uses everywhere; a 2-thread micro-probe ran **5× slower**. Only a
  ProcessPool delivers speedup.

**What the prototype proved vs what the implementation must still preserve.** The
byte-identity result is over the **selected layout's placements at
`alternatives=1`** (the prototype's merge is a single total-order `min`, matching
`_select_spread_diverse` only for one alternative). The implementation issue must
additionally preserve, under the re-base, the two cross-restart pieces the prototype
did not exercise: the **`best_partial_layout`** accumulator (deterministically
reconstructible as a tie-broken min over per-restart partials) and the
**`alternatives > 1` diversity-gated** `_select_spread_diverse` selection. Both are
reconstructible deterministically from per-restart outputs — but they are
obligations to verify, not yet-measured facts.

`★` The decisive result: **4.5× at 8 workers (placement-only), byte-identical to a
re-seeded serial baseline** for the selected layout. Determinism is preserved; the
cost is a one-time re-golden.

### 5. Tow heuristic / ordering / early-exit (Q5–Q7)

- **Q6 — footprint-inflated grid heuristic** (recover post-#480 deep-slot
  routability without a budget bump): plausible but **breaks tow-plan canary
  byte-identity** (the heuristic participates in A\* heap-pop order → changes the
  returned arc). Routing's dominant cost is also geometry rebuild, not the
  heuristic, so it only helps the tight/un-routable regimes. **DEFER** — gated on an
  isolated single-plane `plan_path` expansion-count probe; do not file until that
  shows a sufficient expansion cut.
- **Q5 — most-conflicted-first descent ordering:** the cProfile shows descent is
  0.6 % of placement on spread-ON, and the tight-six wall-clock is *routing*, not
  descent — so the win is ~0 and it **breaks** byte-identity (reorders the seeded
  draw sequence). **REJECT** (joins #381's `plane_ordering` reject).
- **Q7 — default-on spread-stall early-exit:** F7 (#404) shipped the knob opt-in
  and unreachable from the CLI. Flipping the default + wiring `--spread-stall-restarts`
  narrows the perceived-latency tail on easy interactive solves; it is
  `max_restarts`-reproducible (narrows, never widens, #267's timing scope). A small
  **defaulting** decision, not a new risk — **file as a low-priority enhancement.**

---

## Ranked proposal table

Payoff is scored against the *measured* post-#453/#454/#455 numbers. Determinism
class: byte-identical / safe-with-care / **re-base** (byte-identical only against a
new golden) / breaks.

| Candidate | Payoff | Determinism | Verdict |
|---|---|---|---|
| **Parallel restarts** (per-restart-index reseed + ordered merge, ProcessPool) | **high** — measured **4.5× @ 8 workers** on the binding regime; the only large headroom on the geometry-bound placement path | **re-base** — byte-identical to a re-seeded baseline; one-time canary re-golden + ADR-0003 amendment | **BUILD** (the spike's central GO), gated on maintainer accepting the re-golden |
| Default-on spread-stall early-exit + CLI flag (Q7) | low-medium wall-clock, high *perceived* responsiveness | `max_restarts`-reproducible (a defaulting choice) | **FILE** (low priority) |
| Footprint-inflated grid heuristic (Q6) | medium *for tight/un-routable routing only*; uncertain it fully recovers seed-1 six-plane | breaks tow-canary byte-identity (heap-pop order) | **DEFER** — gated on an isolated expansion-count probe |
| Make `Scenario` picklable | enabler for the parallel lever | byte-identical (no behaviour change) | **FILE** (blocks parallel restarts) |
| Empennage-heavy placement bench regime (`full_nine_placement`) | guards parts²-scaling as the model grows (the `full_nine` fixture already maximizes part-pairs — 1,440 theoretical for all 9; `fleet_in`-uniqueness caps it) | n/a (measurement) | **FILE** (optional regression guard) |
| Incremental conflict-tracking in `_spread` | **low** — attacks `_pairwise_conflicts` (9.7 % on roomy-3, 25.5 % on full-9); the 36 % geometry residual is untouched | safe-with-care (canonical re-sum only; delta-update breaks via `total_penetration_m2` float order) | **DEFER/borderline** — only if a fuller-fill profile re-confirms a material share |
| Placement-side STRtree / grid index | **~0/negative** — 97.6–99.2 % AABB-reject; tree build > the float-compares it saves at N≤57 | safe-with-care (canonical re-sort) | **REJECT** (measurement-killed) |
| Routing-side static-obstacle STRtree | untested; routing bottleneck is geometry, not the sweep | byte-identical (RNG-free planner) | **DEFER** — own probe |
| Most-conflicted-first descent ordering (Q5) | **~0** — descent is 0.6 % of placement | breaks (reorders seeded draws) | **REJECT** |

---

## Determinism contract (ADR-0003) — recommendation: **keep it; parallel restarts is a re-base, not a drop**

The second pass reaches the same place as the first: the contract is *not* the
bottleneck, and the headroom is in a determinism-*safe* lever. The parallel-restart
prototype **proved** that the parallel speedup (4.5×) is achievable while keeping
byte-identity — the only adjustment is re-seeding per restart-index, which changes
the goldens *once* (a documented ADR-0003 amendment, exactly the "intentionally
fragile / re-golden on a deliberate algorithm change" clause the contract already
anticipates).

Dropping determinism entirely would buy essentially nothing on top of Approach A —
the merge is already completion-order-invariant and costs microseconds — while
losing the determinism canary (a whole class of solver-drift regressions goes
un-guarded), shareable seeds, reproducible diagnostics, and the `max_restarts`-bound
comparability the `bench/` harness depends on. **Recommendation: keep the contract;
if the maintainer accepts the one-time re-golden, build parallel restarts as a
deliberate, documented re-base.**

---

## Filed follow-up issues

- [#544](https://github.com/DocGerd/hangarfit/issues/544) _(primary, the central
  GO — **greenlit to build**)_ **Parallel restarts via per-restart-index reseed +
  ordered merge (ProcessPool).** Determinism: re-base (re-golden canaries +
  ADR-0003 amendment). Measured 4.5× @ 8 workers. **Must also preserve, under the
  re-base, the `best_partial_layout` accumulator and the `alternatives > 1`
  diversity-gated `_select_spread_diverse` selection** — both unexercised by the
  prototype (which proved the `alternatives=1` selected layout) and reconstructible
  deterministically, but obligations to verify in the implementation. Blocked by
  #545.
- [#545](https://github.com/DocGerd/hangarfit/issues/545) _(enabler, blocks #544)_
  **Make `Scenario` picklable** (drop the `mappingproxy`, or provide
  `__getstate__`/`__setstate__`).
- [#546](https://github.com/DocGerd/hangarfit/issues/546) _(low priority)_
  **Default-on spread-stall early-exit + `--spread-stall-restarts` CLI wiring** (Q7).
- [#547](https://github.com/DocGerd/hangarfit/issues/547) _(optional guard)_
  **`full_nine_placement` bench regime** to guard parts²-scaling.

REJECTED (recorded so they are not re-proposed without new evidence):
placement-side STRtree, most-conflicted-first descent ordering, incremental
conflict-tracking in `_spread` (borderline, measurement-gated). DEFERred:
footprint-inflated grid heuristic (Q6), routing-side static-obstacle STRtree — both
gated on their own isolated probes.

---

## Out of scope (unchanged from #381)

Implementing the speedups (each is its own follow-up), CNN approaches
(#331/#332), RRT-Connect (#336), and relaxing ADR-0003.
