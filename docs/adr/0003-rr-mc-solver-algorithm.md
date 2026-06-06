# ADR-0003: Random-restart min-conflicts (RR-MC) for the static layout solver

- **Status:** Accepted
- **Date:** 2026-05-22
- **Deciders:** [@DocGerd](https://github.com/DocGerd)

## Context & Problem Statement

Phase 2a had to commit `hangarfit solve` to a search algorithm before any
restart loop, scoring function, or diversity filter was written. The
solver takes a `Scenario` (fleet subset, hangar, hard constraints,
optional pins) and must return up to K diverse valid `Layout`s within a
wall-clock budget — or, on failure, an honest diagnostic instead of a
black-box "didn't work." The search runs on the Phase 1 parts-based
collision substrate (see [ADR-0001](0001-aircraft-parts-model.md)), so
the state space is **continuous in `(x_m, y_m, heading_deg)` per plane**
and the collision predicate has **non-smooth boundaries** at the
plan-view-distance threshold. Phase 1 also exposes only HARD constraints
in v1 (maintenance plane, per-plane `pin`, per-plane `force_on_carts`) —
no soft preferences to optimize against. Whichever algorithm shipped
would lock in the failure-mode taxonomy, the diagnostic surface, and the
reproducibility contract for every downstream user; getting it wrong
would have meant tearing out and re-shaping the public API the day after
ship.

## Decision Drivers

- **Continuous state.** A plane's placement is a point in
  `(x_m ∈ [0, width], y_m ∈ [0, length], heading_deg ∈ [0, 360°))`.
  Quantising for a constraint solver (Z3, MiniZinc, OR-tools CP-SAT)
  loses geometric fidelity at the same resolution real measurements will
  support, and the cell count explodes — a 0.05 m grid over a 25 × 18 m
  hangar with a 1° heading grid is 500 × 360 × 360 ≈ 6.5 × 10⁷ states
  per plane, before considering 6–9 planes simultaneously.
- **Non-smooth collision boundary.** The two-clause part-pair predicate
  (plan-view distance < `clearance_m` AND height gap <
  `wing_layer_clearance_m`) is discontinuous: penetration is 0 just past
  threshold and jumps as soon as parts touch. Gradient-based optimisers
  stall in the zero-gradient infeasible regions where every direction
  looks equally bad.
- **Satisficing, not optimising.** The tool's job is to find *a* valid
  alternative when the standard layout breaks. There is no objective
  function ranking valid layouts against each other (modulo the
  diversity filter, which is structural rather than scalar). RR-MC is a
  satisficer by construction.
- **Reproducibility is a contract, not a nice-to-have.** Same scenario +
  same seed → bit-identical `SolveResult`. This is needed for canary
  tests (`tests/test_solver_canaries.py`), for users who want to share a
  "this layout came out of seed 42" claim, and for the CLI's
  diagnostics to be reviewable in a PR.
- **K diverse alternatives from the same engine.** The public contract
  is "return up to K diverse layouts" (see
  [ADR-0004](0004-diversity-metric.md) for the metric). Restart-based
  algorithms get this for free; single-shot constraint solvers do not.
- **Honest failure modes.** Three-way termination
  (`found` / `found_partial` / `exhausted_budget`) plus the literal
  `trivially_infeasible` pre-search status. Each must be distinguishable
  to the CLI and the JSON schema.

## Considered Options

1. **Random-restart hill climbing with min-conflicts descent (RR-MC) on
   continuous state** — the chosen option.
2. **Constraint solver (Z3 / MiniZinc / CP-SAT) with quantised state.**
3. **Genetic algorithm / evolutionary search on continuous state.**
4. **Gradient-based continuous optimisation (e.g. simulated annealing
   with a smoothed penetration cost).**
5. **Pure random sampling with reject-if-invalid.**
6. **Geometric packing heuristic (one-pass placement: largest plane
   first, fit each subsequent plane into remaining free space).**

## Decision Outcome

**Chosen option: RR-MC on continuous state**, because it is the
smallest algorithm that natively handles continuous placement, tolerates
the non-smooth collision boundary, produces bit-identical output under a
seed, and trivially extends to K alternatives via restart count. The
implementation lives in
[`src/hangarfit/solver.py`](../../src/hangarfit/solver.py); the full
design rationale is in the Phase 2a spec
[`docs/superpowers/specs/2026-05-22-phase2a-static-layout-solver-design.md`](../superpowers/specs/2026-05-22-phase2a-static-layout-solver-design.md).

### Algorithm shape (as actually implemented)

- **Random restart loop.** Each restart samples a fresh initial
  placement for every non-pinned plane (uniform in the hangar interior
  with a bbox-derived margin; heading uniform on `[0°, 360°)`). The
  maintenance plane (if not pinned) gets a back-bay biased initial.
  Cart-eligible planes round-robin over the `C + 1` legal cart buckets
  across restarts (`{none, plane_1_on_carts, …, plane_C_on_carts}` for
  the C unlocked cart-eligible planes) to guarantee coverage instead of
  leaving buckets unsampled.
- **Min-conflicts perturbation step.** From the current candidate's
  `CheckResult.conflicts`, build the set of conflicting non-pinned
  planes. Pick one uniformly at random; generate `N` candidate moves
  (`N − 2` small Gaussian nudges + 1 large jump + 1 180° heading flip);
  pick the candidate with the lowest score, tie-broken by smallest
  displacement.
- **Scoring is hierarchical.** `_score(layout) → (conflict_count,
  total_penetration_m2)`. The integer primary key drives descent
  direction; the smooth secondary key breaks plateaus (which integer
  packing scores are known to produce). `(0, 0.0)` means valid.
  `total_penetration_m2` is the Phase 1 extension shipped in Chunk A
  (see [`src/hangarfit/collisions.py`](../../src/hangarfit/collisions.py)
  and `CheckResult` in
  [`src/hangarfit/models.py`](../../src/hangarfit/models.py)).
- **Acceptance gate.** Every accepted candidate is run through
  `collisions.check()` — the solver does NOT bypass the checker. The
  checker is the source of truth, and `Layout.__post_init__` is the
  invariant trap behind it (cart rule, movement-mode consistency, etc.).
- **Diversity filter** runs post-acceptance; candidates within the
  diversity threshold of an already-accepted layout are rejected, with
  `diversity_rejected_count` surfaced in `SolverDiagnostics`. See
  [ADR-0004](0004-diversity-metric.md) for the metric.
- **Three-way termination.** `found` (K layouts accepted),
  `found_partial` (`0 < n < K`, budget exhausted), `exhausted_budget`
  (0 accepted). Plus `trivially_infeasible` from the pre-search gate.
- **Pre-search infeasibility checks** run before the main loop:
  (1) per-plane bbox vs. hangar, (2) Σ part-footprint areas vs. floor area (#425),
  (3) pin self-collision via `check()` on a pin-only Layout. Catches
  literal impossibilities (a 200 m wingspan typo, two pins on top of
  each other) in milliseconds instead of after the full budget burns.
- **Single-threaded, fully seeded RNG.** One `random.Random(seed)` drives
  every sampling decision (initial placement, perturbation,
  candidate selection, conflicting-plane pick). `seed=None` resolves to
  `secrets.randbits(32)` at `solve()` entry and is recorded in
  `SolverDiagnostics.seed`. Cart-bucket round-robin uses the
  non-random restart index, so it is deterministic by construction.

### Why not a constraint solver (Z3 / MiniZinc / CP-SAT)?

The state space is continuous, and constraint solvers want it discrete
or interval. Quantising to a grid fine enough to preserve the
operational resolution (centimetres for position, degrees for heading)
explodes the per-plane variable domain into the tens of millions, and
the combinatorial product over 6–9 planes was judged intractable for
the scenarios that matter (full fleet, tight clearance). This is an
engineering judgement, not a benchmarked claim — no CP-SAT prototype
was built; a future ADR could revisit this with measured data.
Reproducibility would be easier (constraint solvers are deterministic
modulo solver-version pinning), but that gain doesn't buy back the
fidelity loss. The K-diverse-alternatives contract also fits awkwardly
onto a single-shot solver: you have to either re-solve K times with
ad-hoc "diversity" constraints encoding the previous solutions
("planes A and B must be > 0.5 m from their previous positions"), or
emit a model that asks the solver to find K disjoint solutions
simultaneously — both substantially heavier than "restart with a fresh
seed." A future ADR could supersede this if a CP-SAT-friendly variant of
the problem emerges (e.g. coarse-grid pre-placement with a continuous
RR-MC refinement pass), but for v1 the cost-benefit is clearly against
it.

### Why not a genetic / evolutionary algorithm?

Population-based search needs a population, crossover, and mutation
operators, all of which are harder to make bit-deterministic across
runs than a single-threaded greedy descent. Crossover between two
layouts is also conceptually awkward: there is no obvious "swap the
front half" operation when each plane is an independent
`(x_m, y_m, heading_deg)` triple — the closest sensible operator is
"adopt plane X's placement from parent A and the rest from parent B,"
which is essentially a coarse min-conflicts move applied across an
artificial population boundary. The diagnostic story is also worse:
"why did this layout win" is "it scored highest at generation 47 of
population size 64," which is harder to trace than RR-MC's "it was the
4th restart with seed 42, descended in 23 iterations." Rejected on
operational and reproducibility grounds, not capability grounds.

### Why not gradient-based continuous optimisation?

The collision boundary is non-smooth: penetration area is 0 wherever
parts are farther apart than `clearance_m`, and jumps to a positive
value as soon as they're closer. Gradient methods stall in the flat
zero-gradient regions of infeasible space (where every direction looks
equally bad to a local sensor), and smoothing the boundary (e.g. with a
sigmoid penalty around the threshold) introduces an arbitrary bias that
shifts the location of valid solutions in ways the operator cannot
inspect. Simulated annealing without gradients would work, but it
collapses to RR-MC's behaviour at the temperature schedule that matters
(cold enough to actually accept improvements), with extra hyperparameters
to tune for no clear gain. Rejected.

### Why not pure random sampling with reject-if-invalid?

On the placeholder fleet, finding *any* valid layout for the all-9-plane
scenario requires the larger test hangar (see
`tests/fixtures/test_hangar_large.yaml`); on the default 25 × 18 m
hangar even a 6-plane subset has a low success rate for fully-random
placements. The rejection rate is dominated by the tightest pair
clearance, and the resulting wall-clock-per-valid-sample is so high
that the budget exhausts before the first valid layout. Min-conflicts
descent uses information from the `CheckResult` to direct the next
move toward fewer conflicts, which is exactly what rejection sampling
discards. Rejected.

### Why not a geometric packing heuristic (largest-first one-pass)?

One-pass heuristics (sort planes by bbox area descending, place each
into the largest remaining free region) are common in bin-packing
literature and would be fast. They fail on this problem for two
reasons. First, the parts model (ADR-0001) means "free region" is not a
2D shape but a 2.5D structure (per-height-layer occupancy), and the
height-disjoint pass-through case (a high-wing's wingtip over the
Fuji's fuselage area) is exactly the leverage an exception tool needs
— a one-pass heuristic that flattens to 2D throws that away. Second,
the cart rule and the maintenance-bay rule are global constraints that
local placement greedy doesn't see; a one-pass heuristic that places
plane 5 in the back bay because it fit there will be reversed when
plane 7 turns out to be the maintenance plane. RR-MC handles both by
construction (the maintenance plane gets a back-bay biased initial; the
cart rule is enforced by `Layout.__post_init__` and the candidate is
silently rejected at scoring time). Rejected.

## Consequences

### Positive

- **Continuous state is native.** Every degree of heading and every
  centimetre of position is reachable; nothing about the algorithm
  forces quantisation.
- **The non-smooth collision boundary is the algorithm's friend.**
  Min-conflicts works on integer conflict counts; the smooth
  `total_penetration_m2` secondary key handles plateaus without
  requiring the boundary itself to be differentiable.
- **Reproducibility is bit-identical** under a seed — see the
  `tests/test_solver_canaries.py` parametrized fixtures.
- **K alternatives compose with restarts.** No extra machinery; the
  diversity filter runs at acceptance time, the search keeps going.
- **Budget-based termination is honest.** The CLI surfaces `found` /
  `found_partial` / `exhausted_budget` distinctly; `--strict-k` maps
  `found_partial` to exit 1 for callers who want it strict.

### Negative

- **No completeness guarantee.** `exhausted_budget` is a legitimate
  outcome for a feasible scenario if the budget was too short or the
  basin of attraction was too narrow. The CLI's exit codes and
  `--strict-k` mode are the interpretive lever; users have to read the
  documentation to understand that `exhausted_budget ≠ infeasible`.
- **`found_partial` is a real outcome.** Users asking for K = 3
  alternatives can receive 1 or 2 within budget, especially when many
  planes are pinned (the "diversity impossible" heuristic in
  `solve()` even warns about this case up front, see `solver.py`'s
  `diversity_impossible` branch).
- **Hyperparameters are unprincipled.** `candidates_per_iter = 8`,
  `k_stall = 50`, `pos_sigma_m = 0.5`, `heading_sigma_deg = 10.0` are
  guesses calibrated on the placeholder fleet; the spec explicitly
  flags them as "tune with real data." A `SearchConfig` kwarg exposes
  them programmatically, but the CLI does not (deferred to a future
  release).

### Neutral

- **Single-threaded by design.** Parallelism is the price of
  bit-identical reproducibility; a parallel RR-MC would need
  per-worker independent RNGs and a deterministic merge order. Could
  be revisited if performance becomes a real constraint, but for v1's
  budget defaults (30 s) the cost is acceptable.
- **The 180° heading-flip candidate** is a domain bet that
  "nose-wrong-way" cases are common enough to deserve a dedicated
  candidate type. It costs one extra `check()` call per iteration; if
  it never fires usefully, the cost is small and the safety is real.
- **Cart-bucket round-robin** is the deterministic-by-construction
  alternative to per-restart random cart picks. Guarantees every
  cart configuration is sampled at least once within `C + 1` restarts.

## Compliance

- [`tests/test_solver_canaries.py`](../../tests/test_solver_canaries.py)
  — parametrized over three fixtures, asserts `solve(seed=42)` returns
  bit-for-bit identical `SolveResult` across two consecutive runs.
  **Intentionally fragile**: any deliberate algorithm change (RNG
  consumption order, perturbation mix, scoring tweak) requires updating
  the expected outputs. This is the canary that the reproducibility
  contract above is actually being honored.
- [`tests/test_solver_fixture_matrix.py`](../../tests/test_solver_fixture_matrix.py)
  — per-fixture matrix tests with the shared
  `_assert_universal_properties` helper enforcing the six spec §6.2
  universal property assertions on every solver run: `status` is one
  of the four legal values, every returned `Layout` validates under
  `collisions.check()`, the seed is populated in diagnostics,
  `best_partial` is fused with the right status set, pairwise diversity
  holds for `len(layouts) ≥ 2`, and the pre-search wall-time guard is
  respected.
- The Phase 2a spec
  [`docs/superpowers/specs/2026-05-22-phase2a-static-layout-solver-design.md`](../superpowers/specs/2026-05-22-phase2a-static-layout-solver-design.md)
  is the authoritative design document; any future algorithm change
  should be reflected there (and supersede this ADR if it changes the
  algorithm family).

## More Information

- [ADR-0001: Aircraft geometry as a list of parts](0001-aircraft-parts-model.md)
  — the collision substrate the solver descends against. The
  non-smooth collision boundary discussed in the decision drivers is a
  direct consequence of the parts-based predicate; without ADR-0001
  this ADR's "why not gradient" argument would not hold.
- [ADR-0004: Diversity metric for K alternatives](0004-diversity-metric.md)
  — the edit-count metric the diversity filter uses; co-designed with
  this algorithm choice (restart-based search makes K alternatives cheap
  to generate; the metric defines when two are different enough).
- Phase 2a design spec:
  [`docs/superpowers/specs/2026-05-22-phase2a-static-layout-solver-design.md`](../superpowers/specs/2026-05-22-phase2a-static-layout-solver-design.md).
- Implementation: [`src/hangarfit/solver.py`](../../src/hangarfit/solver.py).
- Determinism canaries:
  [`tests/test_solver_canaries.py`](../../tests/test_solver_canaries.py).
- Universal property tests:
  [`tests/test_solver_fixture_matrix.py`](../../tests/test_solver_fixture_matrix.py).
- Related issue:
  [#136](https://github.com/DocGerd/hangarfit/issues/136)
  (the retroactive-ADR backfill that produced this record).

## Amendments

### 2026-05-27 — best-of-all-basins selection and determinism scoping (issue #267)

Since [#267](https://github.com/DocGerd/hangarfit/issues/267), `solve()` no
longer breaks on the first valid layout. The restart loop runs to its
termination gate, appends every valid spread-polished basin to a pool, and calls
`_select_spread_diverse` to pick the best-spread layout(s) subject to the
diversity gate (ADR-0004). See the ADR-0008 amendment (2026-05-27) for the
spread-selection rationale.

**Consequence for the determinism contract.** The seed still fixes the complete
RNG sequence, and `_select_spread_diverse` is a fully-ordered pure sort
(`restart_index` is the final tiebreak, so no two pool entries compare equal).
Therefore:

- A run bounded by `max_restarts` (a deterministic restart count) is **fully
  reproducible across runs and machines** — same seed → same pool → same
  selected layout. The `tests/test_solver_canaries.py` determinism canaries
  remain meaningful on any `max_restarts`-bounded solve call.
- A run bounded by a pure wall-clock `budget_s` allows a *variable* number of
  restarts depending on machine speed and load — **but only on the spread-ON
  best-of-all path.** When basins are near-tied on maximin gap the selected
  layout can differ between machines (or the same machine under different load)
  — the pool size varies and a near-tie may resolve differently. Same seed on
  the same machine under the same load remains reproducible.

- With `spread=False` the wall-clock timing-dependence does **not** apply: the
  loop keeps the pre-#267 first-valid early exit and terminates at a
  seed-deterministic restart (once `alternatives` diverse valid layouts are
  found), independent of `budget_s` / machine speed. That mode is therefore
  reproducible across runs and machines regardless of the wall-clock bound.

**Guidance.** For guaranteed cross-machine reproducibility, bound the search by
`max_restarts` rather than (or in addition to) wall-clock `budget_s`. The
default CLI path uses `budget_s` for responsiveness; this is an accepted,
deliberate tradeoff — spread robustness was chosen over wall-clock-independent
determinism for the interactive use case.

The RR-MC algorithm itself (descent step, scoring, diversity filter, status
taxonomy) is unchanged by this amendment. The amendment block above under
*Consequences → Positive* ("Reproducibility is bit-identical under a seed")
remains accurate for the `max_restarts`-bounded path; under a pure `budget_s`
bound it is timing-scoped as described above.

---

### 2026-06-06 — opt-in spread-stagnation early-exit narrows the timing scope (issue #404 / F7)

The opt-in `SearchConfig.spread_stall_restarts` (see the ADR-0008 amendment
dated 2026-06-06) terminates the spread-ON restart loop once N consecutive
restarts fail to improve the selected set's maximin gap by an absolute epsilon.
This **narrows** — never widens — the wall-clock timing-dependence scoped above:

- The stop condition reads only the seed-fixed restart sequence, the
  fully-ordered `_select_spread_diverse` pool, and an integer counter. It adds no
  RNG draws and no wall-clock reads, so under a `max_restarts` bound a run with
  `spread_stall_restarts` set is **still fully reproducible across machines** —
  the counter trips at the same restart everywhere.
- It cannot make a previously-reproducible (`max_restarts`-bounded) run
  non-reproducible: it only changes the *restart at which the loop stops*, and it
  stops at a seed-deterministic point. For the wall-clock-bounded spread-ON path
  it can only cause the loop to stop *earlier* than `budget_s` would — turning a
  timing-variable tail into a seed-fixed one.

The default (`spread_stall_restarts=None`) preserves the pre-F7 behaviour
byte-for-byte, so the `tests/test_solver_canaries.py` determinism canaries (which
run `spread=False`) and the `determinism-guard` contract are unaffected. No
change to the RR-MC algorithm itself.

---

### 2026-05-23 — maintenance-plane handling, post-milestone-#9

Two passages in the body above describe how RR-MC handles the
maintenance plane:

- Algorithm shape section: *"The maintenance plane (if not pinned)
  gets a back-bay biased initial."*
- Rejection-rationale (Why not LP/ILP?): *"RR-MC handles both by
  construction (the maintenance plane gets a back-bay biased initial; the
  cart rule is enforced by Layout.__post_init__ …)"*

Both predate milestone #9. As of [#108](https://github.com/DocGerd/hangarfit/issues/108)
(part of [Milestone #9](https://github.com/DocGerd/hangarfit/milestone/9)),
the solver drops the maintenance plane from the placeable set entirely —
no initial placement, no perturbation, no cart-bucket slot. The bay
rectangle is enforced as a hard obstacle by the `bay_intrusion`
collision rule (see [ADR-0006](0006-bay-intrusion-maintenance-rule.md)),
so no surrogate sample is needed. The "back-bay biased initial"
machinery was removed.

The RR-MC algorithm itself (descent, restart, scoring, diversity
filter, termination) is unchanged by this — the maintenance plane's
absence from the placeable set is a setup change, not an algorithm
change. This ADR's `Status: Accepted` stands; the amendment block here
records the behavioral diff so a future reader walking ADR-0003 in
isolation isn't misled by the in-body wording.

See [§5 Building Block View — `solver.py`](../architecture/05-building-block-view.md#solverpy--rr-mc-layout-search)
for the current "Maintenance plane handling" bullet.
