# ADR-0021: Tow-planner staging apron — a bounded entry-staging start-region in front of the door; rearrangement holding-area deferred

- **Status:** Accepted
  <!-- Proposed at the spike (#494); Accepted on implementation (#412). Extends
       (does not supersede) ADR-0007's v1 fill scope. -->

- **Date:** 2026-06-07
- **Deciders:** Patrick Kuhn (DocGerd)

> **Implementation notes (#412, 2026-06-07).** Five choices the spike left open
> were settled with the deciders during implementation:
>
> 1. **`auto` derived depth — shipped.** `apron_depth_m` accepts a number or the
>    keyword `auto` (≈ `max(plane fore-aft length) + max(turn_radius_m)`), both in
>    `hangar.yaml` and via `--apron-depth`. `auto` is resolved by the loader once
>    the fleet is known and is **never** injected on absence (absent ⇒ `0`).
> 2. **Reverse-entry seed headings — shipped, gated on `apron_depth_m > 0`.** The
>    apron-pose grid adds the rear-entry cone `{150°, 165°, 180°, 195°, 210°}` so a
>    plane can back in tail-first (unblocks [#263](https://github.com/DocGerd/hangarfit/issues/263)
>    routing; an additional deterministic seed, never a forced orientation). Gating
>    on depth keeps the no-apron pose set — and the `MovesPlan` — byte-identical.
> 3. **The `y = 0` door-line start is *excluded* when an apron exists.** The spike
>    sketched apron y-samples `{0, −d/2, −d}`, but with all starts seeded at `g = 0`
>    the shortest path always wins, so the `y = 0` start would always be chosen and
>    **no plane would visibly slide in** — defeating the issue's motivation. The
>    implementation uses `{−d/2, −d}` (no `y = 0`) so every plane originates outside
>    and slides in. Depth 0 is unaffected (single `y = 0` sample ⇒ byte-identical).
> 4. **Viewer apron *ground* (the additive `scene/v1` `hangar.apron` field) —
>    deferred** to a render-only follow-up. The slide-in *motion* needs no schema
>    change (the first timeline sample simply has `ty < 0`); drawing the ground so
>    the plane does not float over bare space is a separate cosmetic ticket.
> 5. **`--apron-depth` is on `solve` *and* `view`** (both tow-plan); not on `check`
>    (the static `collisions.check` oracle is apron-inert by design).

> **Scope of this ADR.** It **extends** [ADR-0007](0007-tow-path-planner-v1-scope.md)
> by adding a geometric staging apron in the `y < 0` region in front of the door, so
> each plane's tow path begins *outside* the hangar and slides in through the door
> rather than starting *on* the door line (`y = 0`). It does **not** supersede any
> ADR-0007 decision: the empty-hangar-fill scope, cart-as-own-gear
> (`effective_turn_radius_m()`), the door-as-motion-gate, and the bounded greedy
> ordering all stand; the reverse-capable motion of
> [ADR-0010](0010-reeds-shepp-motion-model.md) is retained. The full design
> exploration is in the [staging-apron spike (#494)](../spikes/tow-apron-v2.md).

## Context & Problem Statement

The shipped tow planner models **no apron**: every plane's path starts on the door
line (`y = 0`) — [ADR-0007](0007-tow-path-planner-v1-scope.md) Q6 ("hard door with
no apron"). "On the apron" is conceptual only. In the 3D viewer this reads as the
plane *starting inside the wall*: the first animation frame places it straddling the
threshold rather than approaching from outside. [#412](https://github.com/DocGerd/hangarfit/issues/412)
asks to model a real staging apron (the `y < 0` region) and route **apron → door →
slot**, which also unlocks the viewer "slide-in from outside" animation and turns the
nose-out objective ([#263](https://github.com/DocGerd/hangarfit/issues/263)) from an
abstraction into a routing question.

The question this ADR answers: **what apron model do we adopt — how far does it
extend, what scope tier, and how does the enlarged search space keep the
[ADR-0003](0003-rr-mc-solver-algorithm.md) planner-half byte-identity contract?**
The foundation [#411](https://github.com/DocGerd/hangarfit/issues/411) (centre the
mover in the door + reject jamb-clipping; the minimal no-apron correctness fix) is
already merged and is what this builds on.

The label "v2" is the towplanner subsystem's scope tier (matching ADR-0007's "v1"),
not a `hangarfit` semver version.

## Decision Drivers

- **Unlock the physical entry + viewer slide-in** that #412 motivates, with the
  smallest honest model.
- **Ship the prerequisite, not the superset.** Entry staging (slide one plane in from
  a start-region) is a strict prerequisite for rearrangement (juggle several on a
  holding area). Build the smaller, common case first; defer rearrangement.
- **Preserve the ADR-0003 determinism contract end-to-end.** The planner is
  RNG-free / closed-form; the apron must keep it so — bounded, finite, fixed-order.
- **Reuse existing primitives.** `entry_poses`, `path_first_conflict`,
  `_mover_motion_bounds_conflict`, the multi-start Hybrid-A\* search, and
  `aircraft_parts_world` already exist; the smallest delta wins.
- **Keep `collisions.check` untouched.** The apron is a planner-level motion concept,
  exactly as the door already is ([§8](../architecture/08-crosscutting-concepts.md#the-door-is-a-visual-marker-only)).
- **Do not foreclose #263.** Nose-out is a *soft* preference; the apron must enable it
  without baking in a hard rule.

## Considered Options

The decision is a bundle of three forks. The chosen branch is listed first.

1. **Apron extent:** a **bounded fixed-depth rectangle** `x ∈ [0, width_m]`,
   `y ∈ [−apron_depth_m, 0)` *(chosen)* — vs. an unbounded `y < 0` half-plane, vs. a
   purely-derived depth with no stored field.
2. **Apron representation:** an **explicit deterministic apron-pose grid** extending
   `entry_poses` *(chosen)* — vs. an implicit single `y < 0` start with no model, vs.
   a full holding-area with capacity.
3. **Scope tier:** **entry-staging start-region (one plane staged at a time, order ≡
   `back_first_order`)** *(chosen)* — vs. the full rearrangement holding-area
   (simultaneous multi-plane occupancy + `--current-layout` + bidirectional moves).

## Decision Outcome

**Chosen:** a **bounded fixed-depth staging apron** modelled as an **entry-staging
start-region**, with apron start poses drawn from an **explicit deterministic grid**
extending `entry_poses` and seeded into the existing Hybrid-A\* search.

Concretely:

- **Geometry.** The apron is the rectangle `x ∈ [0, width_m]`, `y ∈ [−apron_depth_m, 0)`.
  `apron_depth_m` is a new optional `Hangar` scalar on `data/hangar.yaml` (default `0`;
  **absent ⇒ `0` ⇒ today's no-apron behaviour reproduced byte-for-byte**). A site opts
  into an apron by setting it explicitly, or requests a fleet-derived depth
  (`≈ max(plane length) + max(turn_radius_m)`) via an explicit `auto` value — the derived
  depth is opt-in, **never auto-injected on absence** (which would break byte-identity).
  `--apron-depth N` overrides per run — the [#210 `max_carts`](0007-tow-path-planner-v1-scope.md)
  site-equipment precedent (whose default `1` likewise reproduces today). The apron spans
  the full hangar frontage in `x` so the grid only extends *south*, never sideways.
- **Order.** Staging order **is** the existing `back_first_order` tow order; exactly
  one plane is staged on the apron per move. `MovesPlan`'s shape is **unchanged** — each
  `Move` still carries one `DubinsArc`, whose `start` now sits at `y < 0`.
- **Front-wall rule.** `_mover_motion_bounds_conflict` generalises: a `y < 0` vertex
  *inside the apron rectangle* is open ground (free); the front wall at `y = 0` stays a
  solid barrier with the door gap, and the **#411 jamb-clip rejection is retained
  verbatim**. Side and back walls are enforced unchanged. The static
  `collisions.check` oracle is **untouched** (it still forbids `y < 0` entirely); the
  final parked slot remains a fully in-bounds placement.
- **Determinism.** The apron-pose grid is a fixed 3 × N_y × 5 set of `(x, y, heading)`
  samples in a fixed emit order, exact-float deduplicated, multi-start-seeded at `g = 0`
  with the existing monotonic-counter heap tie-break. Bounded depth ⇒ a finite,
  reproducible grid. No RNG, no clock. Budgets stay deterministic *counts*. The grid
  heuristic (`_build_grid_heuristic`) already extends a fixed `_GRID_H_Y_PAD_M = 6.0 m`
  south-pad and already treats `y < 0` as free space (the jamb gate lives only in the
  `_mover_motion_bounds_conflict` oracle), so the southward change *reconciles/parameterises*
  that existing pad with `apron_depth_m` rather than adding it from scratch.
- **#263.** The apron + reverse motion (ADR-0010) make nose-out *routable* (back the
  plane in tail-first); the goal heading stays an upstream input and a layout is never
  rejected for being nose-in. Reverse-entry start headings (near `180°`) may join the
  apron grid as additional deterministic seeds, never as a forced orientation. The
  *choice* of soft nose-out mechanism stays [#263](https://github.com/DocGerd/hangarfit/issues/263).
- **Viewer.** The `scene/v1` schema is **unchanged**: the first timeline sample affine
  simply has `ty < 0`, which the viewer applies verbatim, so the plane slides in from
  outside. An optional additive `hangar.apron` field (render-only, to draw apron ground)
  is a nice-to-have, not required.

### Why not an unbounded apron half-plane (fork 1)?

An unbounded `y < 0` region means an unbounded grid-heuristic free-space grid (the
default since [#336](0007-tow-path-planner-v1-scope.md)), an undefined geodesic bound,
and an unbounded expansion budget — directly hostile to the determinism and
bounded-bail-time guarantees the planner ships. The entry manoeuvre needs only about
one turning-radius plus one plane-length of run-up, so a bounded depth is *sufficient*.
Unboundedness buys arbitrarily-far-from-door manoeuvring the entry case never needs.

### Why not a purely-derived depth with no stored field (fork 1)?

A derived-only depth (compute from the fleet, store nothing) denies the site an
authoring knob: one hangar fronts a wide apron, another a tight taxiway. The apron is a
property of the *site*, like `clearance_m` and `max_carts`, so it belongs on the hangar
floor plan. A stored scalar (absent ⇒ `0`) plus an opt-in `auto` derived value gives
both a per-site authoring knob and zero-config sanity when wanted; the default `0` makes
the no-apron model the clean `apron_depth_m = 0` special case — the best possible
migration story.

### Why not an implicit single `y < 0` start with no apron model (fork 2)?

Moving `entry_poses`' `y = 0` to one fixed `y = −d` would ship the slide-in with almost
no other change — but it is a magic constant, not a modelled site property: no authoring
knob, no honest collision model for the off-to-the-side run-up that eases angled /
nose-out entries, and no clean default-of-zero migration. The explicit-grid +
bounded-rectangle design costs little more and is the honest model.

### Why not the full rearrangement holding-area (fork 3)?

Simultaneous multi-plane apron occupancy (planes parked on the apron while pulled out),
apron-capacity and collision *among* staged planes, the `--current-layout` input, and
pull-out/repark bidirectional sequencing are a strictly larger data model with no ground
truth — the same reason ADR-0007 deferred rearrangement in the first place. Entry
staging is the strict prerequisite (a planner that cannot slide one plane in from a
start-region certainly cannot juggle several). Ship the prerequisite; keep the
rearrangement tier's extra needs named (spike Q7) so they are not foreclosed.

## Consequences

### Positive

- Tow paths begin *outside* the hangar and slide in through the door; the viewer's
  "slide-in from outside" animation falls out **with no `scene/v1` change**.
- The apron + reverse motion make nose-out ([#263](https://github.com/DocGerd/hangarfit/issues/263))
  *routable*, removing the last manoeuvring-room coupling that blocked it — without
  deciding the soft-preference mechanism.
- Small, additive, code-mostly footprint: one optional `Hangar` scalar (default `0`
  reproduces today exactly), a generalised front-wall rule, a wider entry-pose grid, and
  a southward grid-heuristic bound (reconciling the existing `_GRID_H_Y_PAD_M = 6.0 m`
  pad with `apron_depth_m`). No solver change, no new motion math, no probabilistic
  component.
- The planner stays **deterministic by construction** (fixed pose grid, bounded grid,
  RNG-free), so the bundled `(Layout, MovesPlan)` output preserves the ADR-0003
  contract.

### Negative

- The enlarged search space (apron starts, more seed poses) raises the worst-case
  expansion count to *disprove* an un-routable fill; the `_MAX_FILL_EXPANSIONS` global
  cap still bounds bail time, but the budget *values* may need re-tuning (the *count*
  stays deterministic). Re-measure with the [profiling harness (#381)](../spikes/solve-tow-profiling.md).
- A second front-wall code path (apron-aware vs the static `collisions.check` rule)
  coexists; mitigated by the apron living entirely in the planner and the #411 jamb
  regression test staying green.

### Neutral

- The front-gap exemption ([#222](0007-tow-path-planner-v1-scope.md)/#411) widens from a
  transient door-width dip to *originating and manoeuvring* in the full apron rectangle;
  the wall barrier and jamb rejection are intact.
- The "walkable region = hangar interior ∪ apron − jamb keep-outs" model is naturally a
  footprint-⊆-polygon containment — the direction [ADR-0018](0018-non-rectangular-hangar-footprint.md)
  (Proposed) takes. The apron should reuse that substrate **if ADR-0018 lands first**;
  otherwise the vertex-test generalisation suffices and ADR-0018 can subsume it later.
  Complementary, not blocking.
- An optional additive `hangar.apron` `scene/v1` field (render-only apron ground) may be
  added later, mirroring the additive [#399/#400/#401](0017-3d-viewer-architecture.md)
  viewer fields; the contract stays backward-compatible.

## Compliance

- **Determinism** (ADR-0003): the planner byte-identity test (the planner analogue of
  `tests/test_scene.py::test_build_scene_is_byte_deterministic`) extends to assert an
  apron-started `MovesPlan` is byte-identical on repeat; the apron-pose grid's fixed
  emit order + exact-float dedup + monotonic-counter heap tie-break are the mechanism.
  The `determinism-guard` subagent (solver-twice-on-a-seed diff) must still pass — the
  apron touches only the planner half, downstream of the seeded solver.
- **Coordinate trap** (ADR-0002): the 45°-heading canary family gains a
  reverse-into-apron case so a CW/CCW sign flip on the new `y < 0` start poses fails
  loud rather than passing the symmetric Reeds–Shepp word matrix. Any PR touching
  `_mover_motion_bounds_conflict`, the entry-pose grid, or the sampled motion check must
  be reviewed by the `geometry-invariant-guard` subagent (standing CLAUDE.md rule).
- **#411 jamb rejection**: the existing jamb-clip regression test (a wide wing
  overhanging the solid wall beside the door) must stay green — the apron only widens
  the legal region *below* `y = 0`, never the `y = 0` wall line.
- **`collisions.check` untouched**: a test asserts a layout's validity verdict is
  independent of `apron_depth_m` (the static oracle still forbids `y < 0` entirely).
- **No-apron migration**: a test asserts `apron_depth_m = 0` / absent reproduces the
  pre-apron `MovesPlan` byte-for-byte.

## More Information

- Spike: [Tow-planner v2 staging apron (#494)](../spikes/tow-apron-v2.md) — the full
  design exploration (apron extent, entry/exit, staging→order, determinism, viewer,
  alternatives) this ADR locks.
- Related ADRs:
  [ADR-0007](0007-tow-path-planner-v1-scope.md) (the v1 fill scope this extends),
  [ADR-0010](0010-reeds-shepp-motion-model.md) (reverse motion that makes apron-staged
  nose-out cheap),
  [ADR-0002](0002-determinant-minus-one-transform.md) (the sign-flip trap the apron
  poses must respect),
  [ADR-0003](0003-rr-mc-solver-algorithm.md) (the determinism contract the apron
  preserves),
  [ADR-0017](0017-3d-viewer-architecture.md) (the `scene/v1` seam the slide-in flows
  through unchanged),
  [ADR-0018](0018-non-rectangular-hangar-footprint.md) (the floor-polygon / keep-out
  substrate the apron+jamb model can reuse).
- Related concepts: [§5 — `towplanner.py`](../architecture/05-building-block-view.md) ·
  [§8 — the door & the coordinate convention](../architecture/08-crosscutting-concepts.md) ·
  [scene-v1-schema](../architecture/scene-v1-schema.md).
- Related issues: [#412](https://github.com/DocGerd/hangarfit/issues/412)
  (implementation), [#494](https://github.com/DocGerd/hangarfit/issues/494) (this
  spike), [#411](https://github.com/DocGerd/hangarfit/issues/411) (merged foundation),
  [#263](https://github.com/DocGerd/hangarfit/issues/263) (nose-out, unblocked by the
  apron).
- External references: Reeds, J. A. & Shepp, L. A. (1990), *Optimal paths for a car
  that goes both forwards and backwards*, Pacific J. Math. 145(2).
