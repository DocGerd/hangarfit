# Towplanner v2 — Reeds–Shepp motion + door entry-cone (Phase 3b, milestone #23)

- **Date:** 2026-05-26
- **Issues:** #261 (Reeds–Shepp), #262 (entry-cone). **#263 (nose-out) deferred** by user decision this session.
- **Surfaced by:** the 2026-05-26 UAT seed-13 tow-path (a plane drove a full
  unnecessary turning circle to reorient before parking).
- **Status:** #261 shipped 2026-05-27 (PR #269, ADR-0010 supersedes ADR-0007
  fork-2) and #262 shipped 2026-05-27 (PR #270) — both on `develop`. #263
  remains open/deferred. This document is the design record as authored before
  implementation; the shipped behaviour matches it except for two review-time
  refinements noted inline below (`_REVERSE_COST_FACTOR` selection consistency
  and the cart-fan pruned to four primitives).

## Problem & diagnostic

v1 (ADR-0007) uses a **forward-only Dubins** motion vocabulary
(`SegmentKind = L|S|R`) and a **single hardcoded straight-in entry pose**
(`entry_pose`, heading 0, one clamped x). A forward-only car must loop to
reorient; the entry pose — the search *start* — is itself never searched.

A throwaway diagnostic (`/tmp/diag_motion.py`, obstacle-free closed-form
comparison) quantified the two levers on representative goals in the placeholder
18×25 hangar (`fk9_mkii`, r=4 m):

| goal | v1 fwd straight-in | #262 free entry (fwd) | #261 reverse-in | #261+#262 |
|---|---|---|---|---|
| seed-13 fk9 (hdg 246°) | 21.0 m | 19.6 m (−1.4) | 16.5 m (**−4.5**) | 16.3 m |
| nose-OUT deep (hdg 180°) | 32.4 m | 30.6 m (−1.8) | 18.0 m (**−14.4**) | 18.0 m |
| nose-IN deep (hdg 0°) | 18.0 m | 18.0 m | 32.4 m (reverse correctly *worse*) | — |
| side-angled (hdg 300°) | 16.9 m | 16.7 m | 22.4 m (fwd wins) | — |

**Findings:** (1) **#261 reverse is the dominant lever** but only when the goal
faces outward — exactly the nose-out loop the UAT exposed (−14 m); for nose-in it
is correctly worse, so a cost-minimising RS planner won't reverse there.
(2) **#262 is a smaller independent win** (≤1.8 m here). (3) This confirms the
filed dependency graph: #263 nose-out is cheap *only* with #261 (and eased by
#262), justifying its deferral until both land.

## #261 — Full closed-form Reeds–Shepp

### Data model
- `Segment` gains `gear: Literal[1, -1] = 1` (forward default ⇒ every existing
  Dubins `Segment` and all current tests stay valid). `kind` (L/S/R) still means
  steering; `gear` means travel direction.
- `DubinsArc.pose_at` integrates a reverse leg by negating the translation step:
  `S` reverse → drive −cos/−sin; arc reverse → position update flips while the
  `L`/`R` steering sign still sets curvature. `sample()` density uses `abs(length)`
  so resolution is unchanged. The integrator remains the single source of truth
  (a wrong closed form surfaces as a sample mismatch — the existing invariant).

### Closed form
- New `plan_reeds_shepp(start, end, *, turn_radius_m)` beside `plan_dubins`.
- Implement the textbook RS family via the **base-formula + symmetry-generation**
  method (the **timeflip / reflect** goal symmetries — four combinations:
  identity, timeflip, reflect, timeflip+reflect — applied to 8 base
  word-solvers) to generate the full word set mechanically, rather than
  hand-coding 48 formulae. Each feasible word → signed `(t,u,v,…)` legs → `Segment`s
  carrying `gear`. Standard reference: Reeds & Shepp (1990); the symmetry approach
  follows the widely-used `reeds_shepp` formulation.
- The integrator round-trip (`pose_at(length) == end`) is the correctness oracle,
  mirrored on the existing `test_dubins_roundtrip_grid` — add `test_reeds_shepp_roundtrip_grid`.

### Cost model — weighted length (prefer forward)
- Selection minimises Σ(|leg_length| × factor), reverse legs × `_REVERSE_COST_FACTOR`.
- **Default `_REVERSE_COST_FACTOR = 1.5`.** Justified by the diagnostic: the
  nose-out reverse win is 18 m vs 32 m forward; 1.5× (→27 m) keeps the win while
  discouraging gratuitous short reverses, whereas 2.0× (→36 m > 32 m) would
  suppress it. Tunable module constant, documented with these numbers.

### Search integration (#222 Hybrid-A*)
- `_primitives(r)` returns 6 primitives, fixed order `Lf, Sf, Rf, Lr, Sr, Rr`.
- `_seg_cost` multiplies reverse legs by `_REVERSE_COST_FACTOR`.
- `plan_path` analytic expansion: `plan_dubins` → `plan_reeds_shepp`.
- `_cell` grid binning unchanged.

### Cart r=0
RS degrades to pivot-in-place + forward/reverse straight; extend the existing
`turn_radius_m == 0` branch in `plan_dubins`/the new code to emit a reverse-`S`
when backing a carted plane is cheaper. Confirm cleanly via a unit test.

### Determinism (ADR-0003)
Fixed word-iteration order + strict `<` cost tie-break (identical discipline to
today's `_WORD_SOLVERS`). The reverse front-gap exemption (#222) is free:
`_mover_motion_bounds_conflict` is pose-only and gear-agnostic, so a plane backing
out through the door at y<0 is still exempt on the front wall only (the side
and back walls remain enforced).

### Governance
- **New ADR-0010** "Reeds–Shepp motion model (towplanner v2)", **Supersedes
  ADR-0007 fork-2 "Dubins-only"** (update ADR-0007 status note + the ADR README).
- Extend the ADR-0007-mandated **45° heading canary** with a reverse case
  (the CW-compass vs CCW-radians sign-flip trap, ADR-0002).
- **`geometry-invariant-guard` subagent review** is mandatory (touches the
  heading-convention integrator) per CLAUDE.md.
- Update arc42 §8 motion-model wording + the `towplanner.py` module docstring.

## #262 — Search the door entry cone

- `entry_pose(target, hangar) -> Pose` becomes
  `entry_poses(target, hangar) -> tuple[Pose, ...]`: a fixed deterministic grid.
- **Grid: 3 x-samples × 5 headings.** Headings = straight-in ±30° in 15° steps
  `{330, 345, 0, 15, 30}` (a forward-admissible cone — keeps #262 independent of
  #261; reverse/near-180 entry is left to #261's primitives and #263's deferred
  nose-out). x-samples = the v1 clamped target-x, the door centre, and their
  midpoint (all within the door interval).
- Each candidate is filtered by `_mover_motion_bounds_conflict` at the entry pose
  itself; jamb/side-wall-clipping starts are dropped before search.
- `plan_path` seeds the open-heap with **all** surviving start poses at `g=0`
  (each with its own `_cell` entry in `best_g`); A* returns the best total path
  across the cone. `plan_fill` passes the full tuple through.
- **Determinism:** fixed grid emission order + the existing monotonic-counter
  heap tie-break. More start states, still reproducible.
- Update the `entry_pose`/`entry_poses` docstring + arc42 §8 door-cone note.
  No new ADR (a refinement restoring the spike-Q6 "cone", not a reversal).

## Out of scope
- **#263 nose-out** — deferred this session (blocked-by #261+#262; cheap only once
  both land). Milestone #23 stays open holding #263.
- **RRT-Connect** — unrelated escape hatch (still forward-only); not touched.

## Execution
Two parallel worktrees off freshly-pulled `develop`: `feature/261-reeds-shepp`
and `feature/262-entry-cone`. Both edit `towplanner.py` (#261: word-solvers /
`plan_dubins` / `pose_at` / analytic shot; #262: `entry_pose` / `plan_path`
start-frontier) — small but real conflict in `plan_path`. Resolve-on-merge: after
the first merges, pull `develop` into the second and resolve before its final
review. Each PR: tests + ruff + mypy green → PR with `Closes #N` → `/pr-review` →
threads resolved → clean-for-review handed to the user (user is sole merger).
