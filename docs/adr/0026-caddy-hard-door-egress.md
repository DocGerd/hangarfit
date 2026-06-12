# ADR-0026: Caddy hard-door egress — clear-egress routability gate

- **Status:** Accepted

- **Date:** 2026-06-12
- **Deciders:** [@DocGerd](https://github.com/DocGerd)

## Context & Problem Statement

The real Airfield Herrenteich set includes a **VW Caddy** (`vw_caddy`) — the
club's rescue / safety vehicle — which must be able to drive **out of the hangar
door** against the full parked scene in every valid layout. Without an explicit
rule the solver may produce a valid layout (no aircraft–aircraft / aircraft–ground
conflicts) in which the Caddy is geometrically boxed in and cannot exit. Such a
layout would be operationally unsafe and must be rejected.

This ADR records the decision reached in #603: what rule governs the Caddy egress,
how it is implemented, and why an earlier geometric "nearest-door" approach was
rejected after real-data falsification.

## Decision Drivers

- **Safety semantics.** The Caddy must *actually be able to drive out*, not merely
  happen to be near the door. A static position check cannot guarantee routability.
- **Reuse.** #602 already wired `plan_path` (Reeds–Shepp, ADR-0010) for general
  mover routing. Reusing the same oracle for the egress check costs nothing extra.
- **Determinism.** The check must be closed-form and RNG-free so the ADR-0003
  byte-identical-plan contract is preserved. `plan_path` is closed-form
  (Hybrid-A\* over Reeds–Shepp primitives, no sampling).
- **Inert when absent.** Layouts with no `hard_door_mover` objects must produce
  bit-identical results — no regression on any existing fixture or solver seed.
- **Data-driven flag.** Whether a ground object is subject to the egress rule
  should be a catalog-level datum (`hard_door_mover: true`), not a hard-coded
  name match, so the rule generalises without code changes.

## Considered Options

### O1 — geometric "nearest-door" Conflict in `collisions.check` (exit 2)

A static check: test whether the `hard_door_mover` is the front-most body
(minimum `y`) among all placed bodies; if not, raise a `Conflict`
(`nearest_door_mover`) at exit 2.

**Rejected** — falsified by the real `examples/herrenteich/layout_full.yaml`.
In the calibrated 11-body arrangement, `cessna_140` parks with its min-y vertex
at `y ≈ 0.01 m` and `ctsl` at `y ≈ 0.03 m` — both forward of the Caddy
(`y ≈ 5.82 m`), and both *within the Caddy's own x-lane* laterally. The predicate
"front-most among all bodies" is structurally unachievable in a packed hangar:
aircraft noses park as close to the door as geometry permits, and in any dense
packing several will always be in front of the Caddy. "Nearest the door" is
retained only as a **soft** placement preference (filed as #614), not a hard rule.

### O2 — separate static near-door footprint check at exit 2

Test whether the Caddy's footprint has a clear axis-aligned lane to the door,
without running the full path planner.

**Rejected** for the same reason as O1: the real data shows that aircraft legally
park in front of the Caddy's x-lane; a static lane-clearance check would fire on
valid layouts. It also under-approximates: a lane that appears clear under a
rectangular sweep might still be unroutable once Reeds–Shepp turning radii are
accounted for, and vice versa.

### O3 — routability gate in `solver._tow_plan_layouts` (exit 3) *(chosen)*

Reuse `plan_path` (the existing Reeds–Shepp `egress_first_conflict` oracle in
`towplanner`) to verify that the Caddy can drive from its parked slot to *outside*
the door against the full parked scene. A blocked egress raises
`NoFeasiblePlanError` → exit-3 tow-unroutable (the Caddy's id named on stderr).

## Decision Outcome

**Chosen: O3 — clear-egress routability gate (exit 3).**

### The HARD rule

A `GroundObject` with `hard_door_mover: true` must be able to drive OUT the door
against the full parked scene (all aircraft + all other ground objects in their
parked positions). The check is implemented by `egress_first_conflict`
in `src/hangarfit/towplanner.py`, which calls `plan_path` from the parked slot
toward a door-cone set of exit poses.

**Reeds–Shepp reversibility (ADR-0010):** an egress (slot → out) is feasible if
and only if an equivalent entry (door → slot) path exists. The closed-form
Reeds–Shepp word set is symmetric under time-reversal; the same Hybrid-A\* search
therefore serves both directions without a separate reverse planner.

The check is wired into `solver._tow_plan_layouts`: after routing all aircraft
placements, `egress_first_conflict` runs for every `hard_door_mover` body. A
failure raises `NoFeasiblePlanError`, which propagates as exit-3 tow-unroutable
with the Caddy id on stderr — the same exit code and surface as a blocked-aircraft
tow path (#603 wiring, consistent with ADR-0007 exit semantics).

### Data-driven flag; inert/byte-identical otherwise

`hard_door_mover: false` (the default for every existing catalog entry) makes
every new code path a guarded no-op. Layouts with no `hard_door_mover` bodies
produce bit-identical `CheckResult` and `MovesPlan` output — the ADR-0003
determinism contract is unchanged. The flag is a boolean field on `GroundObject`;
the `vw_caddy` catalog entry sets it to `true`.

### Known-hard finding: `layout_full` Caddy is egress-blocked

The calibrated `examples/herrenteich/layout_full.yaml` (8 aircraft + 4 ground
objects, the all-11 reference layout added in #605) places the Caddy at
`(x=11.57, y=8.26, heading=180°)` — 5th-deepest of the 11 bodies, its front edge
at `y ≈ 5.82 m` within the door window, boxed behind `cessna_140` (`y ≈ 0.01 m`).
A bounded scan of **96 door-adjacent Caddy poses** found **2 collision-free** and
**0 egress-routable** — the same geometric packing wall encountered in #599.

`layout_full.yaml` is **NOT modified**. It is the real-data reference for the
herrenteich full set; its Caddy block is a correct and expected verdict from the
verifier. Making the Caddy egress-routable requires re-nesting the full 11-body
set, which is gated on the learned backend (Epic C, #607).

#603 ships the **deterministic verifier machinery**: the egress oracle correctly
identifies the blockage and reports it as tow-unroutable.

## Consequences

### Positive

- The Caddy safety constraint is enforceable: any solver-produced layout in which
  the Caddy cannot exit is rejected at exit 3, before the user sees it.
- The rule reuses the existing `plan_path` oracle and Reeds–Shepp motion model
  with zero new geometry code.
- Inert/byte-identical when absent — no regression on any existing layout,
  fixture, or solver seed.
- Data-driven: extending the rule to a second mover (if the fleet gains one)
  requires only setting `hard_door_mover: true` in its catalog entry.

### Negative

- The egress check adds one `plan_path` call per `hard_door_mover` body per
  candidate layout evaluated by the solver. For the current fleet (one Caddy)
  this is one additional path search per layout. The search is bounded by the
  same Hybrid-A\* expansion cap as aircraft routing, so cost is predictable.
- The real `layout_full.yaml` now surfaces an exit-3 verdict. This is the
  correct result but means the all-11 reference layout is not fully tow-routable
  until a re-nesting solution is found (#607).

### Neutral

- Exit code 3 (tow-unroutable) already existed for blocked-aircraft tow paths.
  The Caddy egress failure uses the same exit code and the same `NoFeasiblePlanError`
  propagation path — no new exit code, no new error surface.
- "Nearest the door" as a SOFT placement preference (#614) is orthogonal and does
  not interact with this HARD routability gate.

## More Information

- Related ADRs: [ADR-0003](0003-rr-mc-solver-algorithm.md) (determinism contract —
  inert/byte-identical guarantee), [ADR-0007](0007-tow-path-planner-v1-scope.md)
  (exit-code semantics, tow-unroutable = exit 3),
  [ADR-0010](0010-reeds-shepp-motion-model.md) (Reeds–Shepp motion model and its
  reversibility property), [ADR-0025](0025-ground-object-taxonomy.md)
  (ground-object taxonomy — the `GroundObject` model, `hard_door_mover` flag).
- Related issues: #602 (mover motion + `plan_path` oracle, the code this ADR
  reuses), #603 (this ADR), #599 (packing wall — same blockage surfaced during
  lateral-strafe work), #614 (soft nearest-door placement preference, the
  non-hard complement), #607 (learned backend — prerequisite for making
  `layout_full` egress-routable).
- [§5 Building Block View](../architecture/05-building-block-view.md) —
  `towplanner` module responsibilities include the `egress_first_conflict` oracle.
- [§8 Crosscutting Concepts — "The Caddy hard-door egress gate"](../architecture/08-crosscutting-concepts.md#the-caddy-hard-door-egress-gate)
  — companion crosscutting entry added alongside this ADR.
