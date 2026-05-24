# ADR-0008: Inter-plane spread soft preference (repulsion-energy surrogate for maximin)

**Status:** Accepted
**Date:** 2026-05-24
**Issue:** #145
**Spec:** docs/superpowers/specs/2026-05-24-inter-plane-spread-design.md

## Context

The Phase 2a scoring tuple `(conflict_count, total_penetration_m2)` measures
only *illegal* overlap. Once a layout reaches `(0, 0.0)` the descent stops, so
inter-plane spacing is merely legal, not comfortable. Surfaced 2026-05-23 in
the v0.6.1 visual walkthrough. The agreed objective (reversing issue #145's
original "minimize/pack" framing) is to **maximize** inter-plane separation so
a human can tow a plane in/out with comfortable wingtip clearance.

## Decision

Add an isolated post-pass `_spread()` in `solver.py`, called when a trajectory
reaches `(0, 0.0)`, before the diversity check. It runs a seeded greedy
hill-climb that minimizes a smooth repulsion energy
`E = Σ_{i<j} exp(−gap_ij / scale)` over plane pairs, where `gap_ij` is the
minimum plan-view edge-to-edge footprint distance (shapely `polygon.distance`).
Only moves that keep the layout valid are accepted. On by default
(`SearchConfig.spread=True`), with a `--no-spread` CLI toggle.

## Rationale

- **Maximize, not minimize.** User reversal 2026-05-24: easier tow-in/out, less
  wingtip-strike risk. The "minimum overlap" half is already the hard constraint.
- **Repulsion energy over pure maximin.** Pure maximin (the p-dispersion
  objective) is exact but flat for a hill-climber — only the closest pair has a
  gradient. The repulsion energy is smooth (every plane move changes it) while
  weighting close pairs heavily, so it protects the minimum gap and converges
  toward maximin-like even spreading (the Riesz-energy → maximin-separation
  principle).
- **Repulsion energy over max-sum.** Max-sum (Σ pairwise distance) is smooth but
  Kuby (1987) showed it yields *uneven* spreads — it clusters subsets at extremes
  to maximize the aggregate, leaving some pairs close. The wrong objective for
  "maximum gap".
- **Bounded `exp` kernel over inverse-power `1/gap^s`.** No singularity near
  valid-but-touching planes; one near-touching pair can't dominate the sum.
- **Post-pass structure over fused descent.** The descent is conflict-driven —
  at zero conflicts there is no plane to perturb, so a "fused" approach would be
  two regimes bolted into one function. A separate `_spread()` keeps the
  hard-feasibility code and the `(int, float)` score tuple untouched, isolates
  the soft logic, and makes the toggle a trivial skip — preserving the ADR-0003
  determinism contract (with `spread=False` the RNG stream is byte-identical to
  the pre-spread solver).

## Consequences

- Each valid trajectory runs past first-valid to a spread stall ⇒ longer
  wall-time; fixture-matrix mechanics tests are pinned to `spread=False`.
- **Known limitation (plan-view gap):** the energy ignores z, so the single
  low-wing plane that could legally nest plan-view-overlapping under a high wing
  is mildly de-nested (spread does not reward the nest; the hard constraint
  still permits it). A z-aware kernel is a possible follow-up.
- **Known interaction (diversity):** spreading drives layouts toward a canonical
  even arrangement, so for `K > 1` two basins may spread to similar results and
  the second is diversity-rejected (wasted work, never invalid output).

## Alternatives considered

Pure maximin / leximin; max-sum dispersion; inverse-power Riesz kernel;
fused-descent 3-tuple score. All rejected above. Wall-adherence, aesthetic
alignment, and z-aware nesting are deferred as separate concerns.
