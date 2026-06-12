# ADR-0008: Inter-plane spread — repulsion-energy surrogate for maximin separation

- **Status:** Accepted

- **Date:** 2026-05-24
- **Deciders:** Patrick Kuhn (DocGerd)

## Context & Problem Statement

The Phase 2a scoring tuple `(conflict_count, total_penetration_m2)` measures
only *illegal* overlap. Once a layout reaches `(0, 0.0)` the descent stops, so
inter-plane spacing is merely legal, not comfortable. Surfaced during the
v0.6.1 visual walkthrough (issue #145), the agreed objective — reversing the
original "minimize/pack" framing — is to **maximize** inter-plane separation so
a human can tow a plane in/out with comfortable wingtip clearance. The question
this ADR answers is: what post-pass strategy maximizes separation without
touching the hard-feasibility machinery?

## Decision Drivers

- **Maximize, not minimize.** User reversal 2026-05-24: easier tow-in/out,
  less wingtip-strike risk. The "minimum overlap" half is already the hard
  constraint.
- **Smooth gradient over flat maximin.** A hill-climber needs a gradient on
  every move; pure maximin is flat except at the closest pair.
- **Even spacing over aggregate sum.** Max-sum dispersion (Σ pairwise
  distance) is smooth but Kuby (1987) showed it yields uneven spreads —
  clusters at extremes to maximize the aggregate, leaving some pairs close.
- **No singularity.** The energy kernel must remain bounded even for
  valid-but-touching planes.
- **Preserve the ADR-0003 determinism contract.** With `spread=False` the
  RNG stream must be byte-identical to the pre-spread solver.
- **Isolate the soft logic from the hard-feasibility code.** A fused
  approach would require two descent regimes in one function.

## Considered Options

1. **Repulsion-energy post-pass `_spread()` — `E = Σ_{i<j} exp(−gap_ij / scale)`**
   *(Chosen.)*
2. **Pure maximin / leximin** — exact p-dispersion objective; flat for a
   hill-climber.
3. **Max-sum dispersion (Σ pairwise distance)** — smooth but yields uneven
   spreads (Kuby 1987).
4. **Inverse-power Riesz kernel `1/gap^s`** — has a singularity near
   valid-but-touching planes; one near-touching pair can dominate the sum.
5. **Fused 3-tuple descent** — bolt spread into the existing descent loop as
   a third score component; collapses two independent regimes into one
   function.

## Decision Outcome

**Chosen option: repulsion-energy post-pass (`_spread`)**, because the
bounded `exp` kernel is smooth everywhere (every plane move changes `E`),
weights close pairs heavily so it protects the minimum gap, and converges
toward maximin-like even spreading (the Riesz-energy → maximin-separation
principle) — while keeping the hard-feasibility code and `(int, float)` score
tuple completely untouched.

Concretely: `_spread()` is called in `solver.py` when a trajectory reaches
`(0, 0.0)`, before the diversity check. It runs a seeded greedy hill-climb
that minimizes `E = Σ_{i<j} exp(−gap_ij / scale)` over plane pairs, where
`gap_ij` is the minimum plan-view edge-to-edge footprint distance (shapely
`polygon.distance`). Only moves that keep the layout valid (`_score == (0,
0.0)`) are accepted. On by default (`SearchConfig.spread=True`), with a
`--no-spread` CLI toggle.

### Why not pure maximin / leximin?

Pure maximin (the p-dispersion objective) is exact but flat for a
hill-climber — only the closest pair has a gradient. A hill-climb on a flat
objective wanders without converging; the repulsion energy is a smooth
surrogate that recovers the same qualitative result.

### Why not max-sum dispersion?

Max-sum (Σ pairwise distance) is smooth but Kuby (1987) showed it yields
*uneven* spreads — it clusters subsets at extremes to maximize the aggregate,
leaving some pairs close. The wrong objective for "maximum gap."

### Why not an inverse-power Riesz kernel?

No singularity near valid-but-touching planes is a hard requirement. The
inverse-power kernel `1/gap^s` diverges as `gap → 0`; one near-touching pair
can dominate the sum and cause numerical instability. The bounded `exp` kernel
has none of that complexity.

### Why not fused 3-tuple descent?

The descent is conflict-driven — at zero conflicts there is no "most-violating
plane" to perturb and the `(int, float)` score tuple has no term for
inter-plane preference. A fused approach would be two regimes bolted into one
function with a mode-switch. A separate `_spread()` keeps the hard-feasibility
code unchanged, isolates the soft logic, and makes the toggle a trivial skip —
preserving the ADR-0003 determinism contract (with `spread=False` the RNG
stream is byte-identical to the pre-spread solver).

## Consequences

### Positive

- Each valid trajectory is refined toward better human usability (wider
  wingtip clearances) at no correctness cost — `_spread` can only improve
  or no-op, never invalidate.
- The hard-feasibility machinery (`_descent_step`, `_score`, `check_layout`)
  is completely untouched; the spread logic is isolated and independently
  testable.
- Toggle is trivial: `--no-spread` / `SearchConfig.spread=False` skips
  `_spread` entirely and the RNG stream is byte-identical to pre-spread.

### Negative

- Each valid trajectory runs past first-valid to a spread stall, increasing
  wall-time. Fixture-matrix mechanics tests are pinned to `spread=False` to
  avoid this cost.
- **Known limitation (plan-view gap):** the energy ignores z, so the single
  low-wing plane that could legally nest plan-view-overlapping under a high
  wing is mildly de-nested (spread does not reward the nest; the hard
  constraint still permits it). A z-aware kernel is a possible follow-up.

### Neutral

- **Known interaction (diversity):** spreading drives layouts toward a
  canonical even arrangement, so for `K > 1` two basins may spread to
  similar results and the second is diversity-rejected (wasted work, never
  invalid output).

## Compliance

- **`tests/test_solver_spread.py`** — solve()-level spread tests: verifies
  `_spread` is called when `SearchConfig.spread=True`, that the resulting
  layout is valid, and that `spread=False` produces a byte-identical RNG
  stream to the pre-spread solver.
- **`tests/test_solver_search.py`** — unit tests for `_spread` and
  `_inter_plane_energy` directly: energy function correctness, early-return
  guards (all pinned / fewer than 2 planes), stall-exit, budget-exit, and
  the "only valid moves accepted" invariant.

## More Information

- Related ADRs: [ADR-0003 — RR-MC solver algorithm and determinism contract](0003-rr-mc-solver-algorithm.md)
- Related specs: [`docs/superpowers/specs/2026-05-24-inter-plane-spread-design.md`](../superpowers/specs/2026-05-24-inter-plane-spread-design.md)
- Related issues / PRs: [#145](https://github.com/DocGerd/hangarfit/issues/145)
- External references: Kuby, M. J. (1987). "Programming models for facility
  dispersion: the *p*-dispersion and maxisum dispersion problems."
  *Geographical Analysis* 19(4): 315–329.

## Amendments

### 2026-05-27 — best-of-all-basins spread selection (issue #267)

**Background.** ADR-0008's `_spread` post-pass is a greedy single-plane
hill-climb. It can only polish whichever basin the restart loop handed it — a
single-plane nudge cannot cross a validity barrier out of a nested pair (e.g. a
low fuselage legally tucked under a high wing, producing a 0.0 m plan-view gap).
Because `solve()` originally accepted the **first** valid basin, spread quality
was seed-luck: a seed sweep on a representative 5-plane fill showed ~1/3 of
seeds settled with a nested pair (0.0 m gap) while the rest spread to 1.7–2.5 m
— same scenario, same fleet, same hangar, only the restart seed different.

**Change.** Since [#267](https://github.com/DocGerd/hangarfit/issues/267),
`solve()` runs all restarts within budget to their termination gate, appends
every valid spread-polished basin to a pool, and **selects the layout(s) with
the largest minimum plan-view gap** (energy tiebreak, then restart index as the
deterministic final key) subject to the existing diversity gate (ADR-0004).
`_spread` itself is unchanged — the robustness gain comes from choosing among
basins, not from a smarter climb. Returned alternatives are ordered
best-spread-first (`layouts[0]` is the roomiest).

Best-of-all engages **only when spread is enabled**: with `spread=False` there
is nothing to optimize, so `solve()` retains the pre-#267 first-valid fast path
(`--no-spread` / `SearchConfig.spread=False`) — the restart loop stops as soon
as `alternatives` diverse valid layouts have been found rather than running to
budget.

**Observability.** The achieved minimum pairwise plan-view gap per returned
layout is reported in `SolverDiagnostics.min_pairwise_gap_m` (index-aligned with
`layouts`; `math.inf` / `null` for <2 planes), in the human CLI summary, and in
`--json`. `SolverDiagnostics.valid_basins_found` records the pool size — how
many spread-polished basins the selection had to choose from.

**Space-bound limitation (best-effort, not a guarantee).** Best-of-all returns
the roomiest *available* basin; it cannot eliminate nesting when the fill is
space-tight and every reachable basin nests. Empirically, on the tight 6-plane
default fixture some seeds still return a 0.0 m nested pair because no
non-nested arrangement is reachable within the budget. Spread is a soft
preference (see ADR-0008's design drivers), not a guarantee of positive
separation.

**Determinism.** See the ADR-0003 amendment dated 2026-05-27 for the precise
scoping: reproducible under a `max_restarts` bound; timing-dependent under a
pure wall-clock `budget_s` bound.

### 2026-06-01 — back-of-hangar fill bias (issue #320)

**Background.** The repulsion energy is *position-symmetric*: it rewards
inter-plane separation but is indifferent to *where* in the hangar that
separation sits. So a lone plane settles mid-hangar, and multi-plane fills can
leave free space wasted in the *middle* (between two filled bands) rather than
at the door, where it is operationally useful — for the next exit, or for an
out-of-band plane that needs to enter. This is also the lead lever of the
tow-friendly-placement work ([#280](https://github.com/DocGerd/hangarfit/issues/280),
Direction A): keeping the door-side approach corridors clear is what lets the
bounded tow planner thread a path to each slot.

**Change.** A secondary **back-bias** term is folded into the `_spread`
hill-climb energy:

```
E_total = Σ_{i<j} exp(−gap_ij / scale)  +  back_bias_weight · Σ_p (length_m − y_p) / length_m
          └─────────── spread (unchanged) ──────────┘     └──────── back bias B (#320) ───────┘
```

`B` is minimized when planes park deep (large `y`, toward the back wall at
`y = hangar.length_m`), normalized by `length_m` so a single weight reads
consistently across hangar sizes. It is a **secondary** term, not a hard
back-wall snap: the smooth gradient and the "only valid moves accepted"
invariant of the original `_spread` are preserved, and `min_pairwise_gap_m`
remains the **primary** basin-selection key (#267) — the back-bias only
re-ranks candidates *within* a basin's hill-climb. The `<2 planes` no-op guard
is relaxed when back-bias is active so a lone plane is still pulled to the back
wall (with `<2` planes the inter-plane energy is identically 0, so only the
back-bias drives the climb).

Because the back-bias scales ~linearly with plane count while the repulsion sum
scales ~quadratically, a single weight is gentler on crowded hangars (where
spread genuinely matters) and stronger on sparse ones — the intended behavior.

**Default & toggle.** `SearchConfig.back_bias_weight` defaults to **0.0**
(neutral — the raw spread mechanism and the `spread=False` determinism canaries
stay byte-unchanged). The **CLI enables it by default** at weight `1.0`
(`--no-back-fill` sets it to 0.0; no effect under `--no-spread`, since the bias
rides the spread post-pass). `1.0` was chosen from a sweep over the acceptance
fixtures as the smallest weight that breaks the mid-hangar symmetry (a lone
plane reaches the back wall; the 2-plane `scenario_minimal` fill clears the
door) while staying a secondary term that does not collapse the inter-plane gap.

**Determinism.** The back-bias is RNG-free re-ranking: it adds no random draws
and does not change the draw count or order (candidate generation is unchanged),
so same-seed output stays byte-identical (`tests/test_solver_search.py::
test_spread_back_fill_is_deterministic_for_same_seed`). No `determinism-guard`
amendment is required.

### 2026-06-06 — opt-in spread-stagnation early-exit (issue #404 / F7)

**Background.** The #267 best-of-all-basins change (above) runs *every* restart
within budget even after an excellent basin appears early, so a default-spread
single-alternative solve is "always ~30 s". The F6 profile
(`bench.profile_pipeline`) made the waste concrete on the canonical
`roomy_three_spread_on` regime: the selected maximin gap reaches ~96 % of its
30-restart value by restart 3 (10.09 → 12.05 → 12.06 m) and then sits on a
~17-restart plateau before two negligible late nudges (+0.048 m at restart 21,
+0.396 m at restart 30). Most of the budget buys nothing.

**Change.** A new **opt-in** `SearchConfig.spread_stall_restarts: int | None`
(default `None` ≡ today's run-to-budget behaviour) lets the spread-ON restart
loop stop once `spread_stall_restarts` *consecutive* restarts fail to improve the
selected set's maximin gap by at least `spread_stall_epsilon_m` (default
`0.05 m`). The metric is `min(min_gap)` over the `_select_spread_diverse`
selection — for `alternatives == 1` that is the pool's best gap. The counter is
armed **only after a complete (`≥ alternatives`) selection exists**, so a hard
scenario still gets the full budget to find its first answer; the early-exit only
trims the polish-the-incumbent tail. The improvement test uses Keras-style
`min_delta` semantics: a restart resets the counter only if it beats the running
best by `≥ epsilon`, so steady sub-epsilon accumulation eventually crosses the
threshold and resets rather than triggering a premature exit. No effect when
`spread=False` (that path already first-valid early-exits per #267).

**Calibration.** `spread_stall_restarts=5` with the default `0.05 m` epsilon
stops `roomy_three_spread_on` at restart 7 — ~4× fewer restarts while keeping
12.058 m of the 12.502 m final gap (96 %). `0.05 m` (5 cm) treats the +0.048 m
plateau bump as noise (operationally negligible for wingtip clearance) while a
genuinely better-separated basin (the +0.396 m late jump) would reset the
counter. These are the *recommended* values documented on the fields; the
shipped default leaves the feature **off** (`None`). The `trivial_single` regime
(1 plane, `min_gap = math.inf`) confirms the degenerate inf case never stagnates
and so never early-exits — harmless, since single-plane solves are not the slow
ones.

**Observability.** `SolverDiagnostics.spread_stall_applied` is `True` when the
loop stopped on stagnation rather than budget / `max_restarts`. Because the
early-exit arms only after a complete selection, a `True` value always
accompanies a `found` result. Advisory — it changes *when* the search stops,
never *whether* the returned layout is valid.

**Determinism.** The stop depends only on the seed-fixed restart sequence + an
integer counter (and the existing fully-ordered `_select_spread_diverse` sort) —
never wall-clock. So under a `max_restarts` bound the selected layout is
identical for a given seed across machines, which *narrows* the #267 wall-clock
timing scope rather than widening it (see the ADR-0003 amendment dated
2026-06-06). The default `None` path is byte-identical to pre-F7, so the
`spread=False` determinism canaries and `determinism-guard` are untouched.

### 2026-06-12 — #604 right/left-region soft term + ground objects as solver-placed bodies

**Background.** The club aligns its glider trailers to one hangar wall to keep
the central corridor clear — a preference that the spread repulsion energy is
position-symmetric and therefore blind to. #604 makes ground-object movers
(glider trailers) full solver-placed citizens and adds a per-object soft
wall-alignment preference: the soft-tier sibling of the #603 HARD Caddy
egress gate, using the same `_spread` hill-climb as the platform.

**Change.** A per-object soft wall-alignment term is folded into the SAME
`_spread` energy, alongside the spread repulsion and the #320 back-bias:

```
E_total = Σ_{i<j} w_i·w_j·exp(−gap_ij/scale)  +  back_bias_weight·Σ_p (L−y_p)/L  +  Σ_{o∈prefs} w_o·d_o/W
          └────────── spread (#145) ─────────┘     └──────── back bias (#320) ──────┘   └──── region (#604) ────┘

where d_o = (W − x_o) for side="right", x_o for side="left", and W = hangar.width_m.
```

- The preference is **per-object scenario data** (`RegionPreference{side, weight}`,
  `None`/absent ≡ neutral/inert) — not a global search knob; normalized by
  `width_m` so one weight reads across hangar sizes (matching the back-bias
  normalization by `length_m`).
- It is **secondary**: `min_pairwise_gap_m` remains the PRIMARY cross-basin
  selection key (#267); the region term, like the back-bias, re-ranks candidates
  only WITHIN a basin's validity-gated hill-climb (`_spread` accepts only moves
  keeping `_score==(0,0.0)`), and never enters basin selection.
- **Ground objects are now solver-placed.** Movers (`placed_routed_mover`) are
  full search citizens (sampled, perturbed in the descent, spread, routed, and —
  when flagged `hard_door_mover` (e.g. the Caddy) — egress-gated (#603));
  fixed obstacles are authored static keep-outs. The region alignment achieved
  is surfaced via `SolverDiagnostics.region_alignment`
  (per-layout per-object 0-1, 1.0 = at the preferred wall).

**Default & toggle.** Default INERT: a scenario with no `region_preferences`
in its ground-object block adds nothing. The Herrenteich scenario opts its two
glider trailers in (side `"right"`, weight 1.5).

**Determinism.** The region term is RNG-free re-ranking — no random draws, no
change to candidate generation — so same-scenario+same-seed output stays
byte-identical (ADR-0003, max_restarts-scoped). The whole ground-object
integration is byte-identical to pre-#604 when a scenario has no ground objects
(no new draws, empty Layout GO args). No determinism-guard / ADR-0003 amendment
is required (same reasoning as the #320 back-bias).

**Known limitation.** The region pull is a *preference*, not a guarantee: a
space-tight basin may keep a trailer off its preferred side (validity wins).
Like the back-bias, it cannot move a body across an invalidating position.

### 2026-06-07 — incremental single-plane gap cache (issue #455)

**Background.** A **fresh** profile taken *after* #453 (parts-world memo) and #454
(AABB broad-phase) had landed —
`python -m bench.profile_pipeline --regime roomy_three_spread_on --profile` —
confirms the spread post-pass is still ~99 % of placement on the canonical
`roomy_three_spread_on` regime, with the per-candidate split now **collisions.check
~72 % / `_inter_plane_energy` ~25 %** (cumulative cProfile buckets). That inverts
the pre-#453 spike's 57 % / 41 % split (`solve-tow-profiling.md`): #453's geometry
memo turned the energy term's repeated world-part rebuilds into cache hits, so its
*share* shrank while `collisions.check` stayed the dominant placement cost. The
upshot is that the energy term this amendment optimizes is the **secondary** lever
(the validity `collisions.check` is the bigger slice, attacked cross-cuttingly by
#453). Each `_spread` iteration perturbs **one** plane and scores several candidate
positions for it, yet the energy recomputed the (expensive) shapely
`polygon.distance` for *all* O(n²) pairs on every candidate — even the pairs whose
gap cannot have changed because neither endpoint moved.

**Change.** `_inter_plane_energy` takes an optional `gap_cache` + `moved` plane.
For every pair **not** touching `moved` it memoizes the edge-to-edge distance and
reuses it across the candidates that share the cache (one per `_spread`
iteration); the `moved` plane's pairs are always recomputed. This turns the
per-candidate energy cost from O(n²) to O(n) pairwise shapely distances. Every
caller outside `_spread` passes `gap_cache=None` and gets the original full sweep.

**Determinism — the safe form, NOT a delta-update.** The energy is still summed
over **all** pairs in canonical `sorted`-id order; only the *source* of each
pair's gap changes (cache vs. fresh), and a cached gap is the identical float
(same unchanged poses, deterministic shapely). So the sum is **byte-for-byte
identical** to the cache-free recompute (ADR-0003) — verified empirically by
diffing solve output against the pre-change `develop` across the two
**spread-active** fixtures (`solve_fresh_alternatives_three`, 3 planes, and
`solve_fresh_six_planes`, 6 planes) over 5 seeds each — the cache path runs only
under spread — all identical, plus the two `test_gap_cache_*` unit assertions and
the bench run-twice determinism check. **Do not** convert this to a delta-update
("subtract the moved plane's old pair energies, add the new"): the #455 issue's
adversarial review measured that form diverging at the float-ULP level (~1e-15) on
a substantial fraction of moves (~29 %), which — because energy is the primary
acceptance key — flips candidate acceptance and breaks the contract.
The `back_bias` term (#320) is a per-plane sum with no pairs, so it is always
re-summed in full. Measured: `roomy_three_spread_on` placement 15.04 s → 14.08 s
median (~6 %) at n = 3 — baseline ~15 s, itself down from the spike's 40.6 s after
#453/#454 landed. The saving is O(n²)→O(n) in plane count, so it grows with fleet
size.
