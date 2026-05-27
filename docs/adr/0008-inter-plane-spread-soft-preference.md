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
