# ADR-0004: Diversity metric for K alternatives (edit count with per-plane thresholds)

- **Status:** Accepted
- **Date:** 2026-05-22
- **Deciders:** [@DocGerd](https://github.com/DocGerd)

## Context & Problem Statement

`hangarfit solve --alternatives K` promises "up to K **diverse** valid
layouts." The RR-MC search loop ([ADR-0003](0003-rr-mc-solver-algorithm.md)) reaches
valid layouts repeatedly during its random-restart trajectory — but
without a filter, the layouts it accepts in succession tend to cluster
near the same easily-reached local minima. An early prototype with
`K = 3` regularly returned three near-identical layouts that differed
only by centimeter-scale jitter and degree-scale heading wobble, which is
operationally useless: the human looking at the PNGs cannot tell them
apart, let alone choose between them as genuine alternatives. Phase 2a
Chunk E ([#90](https://github.com/DocGerd/hangarfit/issues/90),
[PR #91](https://github.com/DocGerd/hangarfit/pull/91)) had to commit to
a *metric* — a function of two layouts that decides whether the second
counts as a real alternative to the first — before the K-alternatives
feature could ship. Diversity-by-what is the question this ADR answers.

## Decision Drivers

- **The metric must be domain-meaningful, not numerical.** Two layouts
  that differ by 5 cm in a single plane are not meaningfully different
  to a human eyeballing the top-down PNG; they should be filtered as
  duplicates. The metric must distinguish "noise" from "design intent."
- **The metric must be per-plane semantic.** "Every plane jiggled 5 cm"
  is noise; "one plane rotated 45°" is a real second option. An
  aggregate-over-the-fleet score conflates these two cases and rewards
  the wrong one.
- **Determinism is non-negotiable.** A given `seed` must produce a
  bit-identical `SolveResult` (enforced by
  `tests/test_solver_canaries.py`). The filter must therefore be
  deterministic — no RNG inside the diversity test, no order-dependent
  clustering state.
- **The metric is in the inner loop.** For each candidate valid layout
  the filter runs against every already-accepted layout. With small K
  (≤ 5) the cost is bounded, but the per-comparison work still has to
  be cheap; nothing exotic.
- **Thresholds must be tunable per call.** The placeholder hangar is
  25 × 18 m, but the operational hangar may differ substantially once
  measured; a hard-coded threshold that suits one scale will not suit
  another. The metric's parameters live in a `DiversityConfig`
  dataclass so a caller can override them without forking the solver.

## Considered Options

1. **Edit count with per-plane thresholds** — count the planes whose
   `(x_m, y_m)` shifted by ≥ `position_threshold_m` *or* whose heading
   shifted by ≥ `heading_threshold_deg`; accept the candidate iff that
   count is ≥ `min_planes_moved`, against *every* already-accepted
   layout (pairwise, not aggregate). Encoded as
   [`DiversityConfig`](../../src/hangarfit/models.py) with defaults
   `min_planes_moved = 2`, `position_threshold_m = 0.5`,
   `heading_threshold_deg = 30.0`.
2. **L2 distance over flattened `(x, y, heading)` vectors** across all
   planes, with a single scalar threshold for "different enough."
3. **Hamming distance over a per-plane same-or-different binary
   predicate** — count how many planes "changed" under some equality
   rule, accept if the count exceeds a threshold.
4. **K-medoids / clustering in a learned plane-displacement space** —
   cluster the candidate pool, return one representative per cluster.
5. **No diversity filter** — return the first K accepted candidates in
   order of acceptance.
6. **Footprint-overlap fraction** — compute the area of the symmetric
   difference between the two layouts' fleet footprints (sum of plane
   bounding polygons in plan view) as a fraction of total fleet
   footprint; accept if that fraction exceeds a threshold.

## Decision Outcome

**Chosen option: edit count with per-plane thresholds**, with defaults
`min_planes_moved = 2`, `position_threshold_m = 0.5 m`, and
`heading_threshold_deg = 30°`. Since the best-of-all-basins change
(#267), the filter is applied at *selection* time — after the restart
loop has collected every valid spread-polished basin into a pool — in
[`_select_spread_diverse()`](../../src/hangarfit/solver.py), which sorts
the pool by separation quality and greedily admits layouts using the
[`_is_diverse_enough()`](../../src/hangarfit/solver.py) predicate. Pool
candidates the gate turns away (examined in best-spread order until the
`alternatives` quota is met) increment
`SolverDiagnostics.diversity_rejected_count` so inefficient runs are
observable; the count is always `0` for `alternatives == 1`.

Concretely: a plane is considered "moved" iff its position differs by
≥ 0.5 m *or* its heading differs by ≥ 30° (short-arc — so 359° and 0°
register as 1°, not 359°). A candidate layout is accepted iff at least
two planes are moved relative to *every* already-accepted layout
(pairwise diversity, not aggregate).

### Why these specific numbers

- **0.5 m** is large enough to be visually obvious in a top-down PNG of
  a 25 × 18 m hangar (≈ 1/50 of the long axis) and small enough that
  two layouts that genuinely differ in plane positions register as
  different. Below this, the difference reads as fitting tolerance.
- **30°** is the rough boundary at which a plane looks "noticeably
  re-aimed" in the render. Smaller angle deltas read as the solver
  settling into the same local minimum from a slightly different start,
  not as a different design intent.
- **M = 2** ensures the alternative is not just "one plane moved a bit."
  One plane different is not a real second option; the operator wants
  to see the layout *reorganize*. Two planes moved is the minimum
  useful disturbance.

These are domain-judgement calls, not derived from a formal
optimisation. They are listed under the spec's
"[Pre-empirical default](../superpowers/specs/2026-05-22-phase2a-static-layout-solver-design.md)"
risk register and are explicitly tunable via `DiversityConfig` per call.

### Why not L2 distance over flattened `(x, y, heading)` vectors?

It is aggregate over the whole fleet and insensitive to which plane
moved. "Every plane jiggled 5 cm" sums to the same L2 distance as
"one plane rotated 45°," but only the second is a real alternative.
There is no choice of scalar threshold that separates the two cases —
the metric has thrown the per-plane structure away. A weighted L2
(weights per plane and per axis) would recover some of that, but at
the cost of having to pick the weights without operational data; the
chosen edit-count metric exposes its parameters in domain-meaningful
units (meters, degrees, plane count) instead of opaque weights.

### Why not Hamming distance over a binary same-or-different predicate?

It still requires a threshold for "same" — the binary predicate
collapses some `(position_threshold, heading_threshold)` choice into
itself rather than removing it. So this is not actually a different
metric; it is the edit-count metric with the thresholds hidden inside
the predicate. The chosen option keeps those thresholds explicit and
caller-visible in `DiversityConfig` — exactly the surface a future
operator-driven retune will need.

### Why not K-medoids / clustering in a learned plane-displacement space?

Overkill for K ≤ 5 alternatives. Clustering algorithms introduce their
own non-determinism (initial-centroid choice, tie-breaking) which would
have to be carefully seeded to preserve the solver's reproducibility
guarantee. The learning step assumes a corpus of layouts to learn from,
which the on-demand-exception use case does not produce. Adds a heavy
dependency (sklearn or hand-rolled equivalent) for marginal benefit
over a five-line filter.

### Why not "no diversity filter, return the first K accepted"?

This is what the prototype did, and it is why the filter exists. With
RR-MC's randomized restarts, the first K candidates tend to cluster
near the most-easily-reached local minima; with `K = 3` the run
regularly returned three near-identical layouts. The filter is the
*reason* the K-alternatives feature is useful at all.

### Why not footprint-overlap fraction?

Plan-view symmetric difference is geometrically appealing — it directly
measures "how different do the layouts look in the PNG." Rejected on
two grounds. First, computing polygon symmetric difference across the
whole fleet for every candidate-vs-accepted pair is substantially more
arithmetic than the per-plane numeric comparison, and the filter runs
inside the inner loop. Second, the metric still has a threshold that
needs to be picked in opaque units (square meters), and unlike the
chosen metric's `(0.5 m, 30°, 2 planes)` parameters it does not
correspond cleanly to a human description of "what makes this a real
alternative." If a future visualization-driven use case needs it, a
later ADR can supersede this one without invalidating
`DiversityConfig` — the edit-count metric and a footprint metric can
coexist as alternative filter strategies.

## Consequences

### Positive

- **Domain-meaningful.** A user inspecting a rejected candidate via the
  diagnostics can read "1 plane moved (need 2)" and immediately
  understand why; the metric speaks the operator's language.
- **Deterministic.** No RNG, no order-dependent state. The same
  `(scenario, seed)` always produces the same accept/reject decision
  sequence; the canaries in `tests/test_solver_canaries.py` depend on
  this.
- **Cheap.** Each diversity test is O(planes × accepted_so_far) numeric
  comparisons — well under a millisecond for typical fleets and
  K ≤ 5.
- **Tunable per call.** `DiversityConfig` is a `solve()` kwarg; a
  caller with a small hangar or a tight tolerance can override the
  defaults without changing the solver source.
- **Observable.** `SolverDiagnostics.diversity_rejected_count` surfaces
  filter activity: a high reject count signals a tight scenario where
  the basin pool holds many near-identical layouts, suggesting either
  loosening `DiversityConfig` or raising `budget_s`.

### Negative

- **Thresholds are domain judgement, not derived.** `0.5 m`, `30°`, and
  `M = 2` were chosen by eye against the placeholder hangar; they may
  be wrong for the real hangar's scale once measured, or for an
  unusually small or large fleet. Mitigation: `DiversityConfig` is
  per-call, and a future operator-feedback round can adjust the
  defaults without changing the metric's shape.
- **No interpolation between "diverse" and "not diverse."** The metric
  is a hard predicate, not a continuous score. A pair of layouts that
  differs by 1.9 planes (e.g., one plane fully moved and one plane
  borderline) gets rejected; a pair that differs by exactly 2 planes
  marginally gets accepted. Mitigation: this matches how a human reads
  the PNGs — "two things changed" *is* the natural threshold.

### Neutral

- **`diversity_rejected_count` adds one field to `SolverDiagnostics`.**
  Worth it for the observability; the spec §6.2 universal property
  test in `tests/test_solver_fixture_matrix.py` exercises the field's
  monotonic-non-negative invariant alongside the rest of the
  diagnostics.
- **Pairwise, not aggregate.** The candidate must be diverse vs *every*
  already-accepted layout, not just the most recent. This is the only
  sane choice for K > 2 — aggregate-over-accepted would let a third
  layout be accepted that is identical to the first as long as it
  differs from the second. Pairwise costs O(K) per candidate, which is
  cheap at the K ≤ 5 regime the CLI exposes.

## Compliance

- The pairwise-diversity universal property assertion lives in
  [`tests/test_solver_fixture_matrix.py::_assert_universal_properties`](../../tests/test_solver_fixture_matrix.py)
  — specifically the `if len(r.layouts) > 1:` block, which mirrors the
  solver's edit-count metric via the local `_count_planes_moved`
  helper, then asserts `n_moved >= cfg.min_planes_moved` for every pair
  of accepted layouts. Every fixture in the v1 matrix runs this
  helper, so any future change to the filter that breaks the
  invariant fails CI immediately.
- The
  [`DiversityConfig`](../../src/hangarfit/models.py) dataclass in
  `src/hangarfit/models.py` is the single source of truth for the
  thresholds. It is `@dataclass(frozen=True, slots=True)` with
  `__post_init__` validation rejecting `min_planes_moved < 1`,
  non-positive `position_threshold_m`, and `heading_threshold_deg`
  outside `[0, 180]` (the short-arc range).
- The filter itself —
  [`_is_diverse_enough()`](../../src/hangarfit/solver.py) — is module-
  private but exercised end-to-end by every `solve_*` fixture test;
  the per-fixture rejection-count assertions in the same file pin its
  behavior in addition to the universal property.

## More Information

- [ADR-0003: RR-MC solver](0003-rr-mc-solver-algorithm.md) — the search algorithm
  this filter sits on top of. The diversity filter assumes the search
  produces multiple valid layouts; without RR-MC's random-restart
  trajectory there would be nothing to filter.
- [`src/hangarfit/models.py`](../../src/hangarfit/models.py) —
  `DiversityConfig` dataclass with default thresholds and invariant
  validation.
- [`src/hangarfit/solver.py`](../../src/hangarfit/solver.py) —
  `_is_diverse_enough()` (the filter), `_heading_delta_short_arc()`
  (the angle normalization), and the K-acceptance loop in `solve()`
  that calls them.
- [Phase 2a design spec — §4.6 Diversity filter](../superpowers/specs/2026-05-22-phase2a-static-layout-solver-design.md) —
  the full design rationale, including the diversity-impossible warning
  flow (`(|fleet_in| − |pinned|) < min_planes_moved` ⇒ `found_partial`
  is honest, no silent K downgrade).
- [`tests/test_solver_fixture_matrix.py`](../../tests/test_solver_fixture_matrix.py) —
  the `_assert_universal_properties` helper and the per-fixture
  diversity-rejection assertions.
- Related issues / PRs:
  [#90](https://github.com/DocGerd/hangarfit/issues/90) (Chunk E),
  [PR #91](https://github.com/DocGerd/hangarfit/pull/91)
  (K-diverse alternatives implementation),
  [#95](https://github.com/DocGerd/hangarfit/issues/95) (the
  pairwise-diversity assertion placement),
  [#136](https://github.com/DocGerd/hangarfit/issues/136) (the
  retroactive-ADR backfill that produced this record).
