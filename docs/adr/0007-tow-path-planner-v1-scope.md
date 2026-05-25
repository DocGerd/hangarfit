# ADR-0007: Tow-path planner v1 — empty-hangar fill, Dubins-only, cart-as-own-gear

- **Status:** Proposed

- **Date:** 2026-05-25
- **Deciders:** Patrick Kuhn (DocGerd)

## Context & Problem Statement

Phase 2a ships a solver that answers *where* each plane parks (a target
`Layout`). It does not answer *how* a human gets each plane there: in what
order the planes enter, and what collision-free path each one tows along from
the door to its slot. The [tow-path spike (#180)](../spikes/tow-path-planning.md)
explored eight design questions and recommended a deliberately small first cut:
the **empty-hangar fill** case (every plane starts outside and enters once),
planned with closed-form **Dubins arcs** and a deterministic greedy ordering.

This ADR locks that scope as the Phase 3a milestone and resolves the one
question the spike explicitly left open: cart-borne planes are modelled as
own-gear with a zero turn radius (a pivot-in-place), but `turn_radius_m = 0`
is **not representable in today's schema** — `data/fleet.yaml` carries `null`
for the three `always_cart` planes, and `Aircraft.required_turn_radius_m()`
(`src/hangarfit/models.py`) *raises* if that field is read for a carted plane.
Something has to give before the planner can construct a cart plane's path.

The label "v1" here means the **scope tier of the towplanner subsystem**, not a
`hangarfit` semver version: v1 = this Phase 3a milestone; v2 = a later
Phase 3b sized after v1 ships.

## Decision Drivers

- **Strict prerequisite first.** A planner that cannot fill an empty hangar
  certainly cannot rearrange a full one. The fill case is the smaller, common,
  one-direction-per-plane problem; the rearrangement case needs an apron, a
  `--current-layout` input, and bidirectional moves we have no ground truth on.
- **Reuse existing primitives.** `collisions.check`, `geometry.aircraft_parts_world`,
  and the `_draw_conflict_overlay` z-order pattern already exist. The smallest
  delta that ships a working planner wins.
- **Preserve the [ADR-0003](0003-rr-mc-solver-algorithm.md) determinism
  contract** end-to-end: same seed → same target layout → same plan, byte-identical.
- **Keep a v1 *approximation* out of the source-of-truth data.** Cart-as-own-gear
  is a modelling shortcut a v2 cart-lift primitive may replace. Whatever encodes
  it should be revisable without a data migration.
- **One motion primitive, one collision check.** No special-casing carts anywhere
  in the planner body.

## Considered Options

The decision is a bundle of four forks. For each, the chosen branch is listed
first; the spike doc carries the exhaustive reasoning for the seven questions
this ADR does not re-litigate.

1. **Scope:** empty-hangar fill *(chosen)* — vs. rearrangement-first.
2. **Motion model:** Dubins-only + bounded order-retry *(chosen)* — vs.
   RRT-Connect, vs. straight-line/Bezier.
3. **Cart kinematics:** cart = own-gear with `turn_radius_m = 0` (pivot-in-place)
   *(chosen)* — vs. a dedicated true cart-lift primitive, vs. ignore cart-mode.
4. **Cart-radius schema realization:** planner-internal
   `Aircraft.effective_turn_radius_m()` *(chosen — "option B")* — vs. loosening
   the loader/validator and setting `0.0` in `fleet.yaml` ("option A").

## Decision Outcome

**Chosen:** an **empty-hangar-fill** planner that plans every plane's path as a
**Dubins arc** with a **bounded greedy-order retry**, treats **cart-borne planes
as own-gear with `turn_radius_m = 0`**, and realizes that zero radius through a
new **planner-internal `Aircraft.effective_turn_radius_m()`** accessor rather
than a schema change.

Concretely on fork 4 (the question this ADR resolves): `effective_turn_radius_m()`
returns `0.0` for `always_cart` planes and delegates to `required_turn_radius_m()`
otherwise. `data/fleet.yaml` keeps `null` for the cart planes; the loader and
`Aircraft.__post_init__` validation are untouched; `required_turn_radius_m()`
keeps raising for carted planes as a bug-guard for own-gear-only callers. The
towplanner is the sole caller of the new accessor.

### Why not rearrangement-first (fork 1)?

The rearrangement case (pull planes out, repark in a new order) needs an apron
rectangle, a current-layout input, and bidirectional move primitives — a strictly
larger data model. Designing the harder problem before the simpler one has shipped
forces us to commit to a model we cannot yet validate. Rearrangement is deferred
to v2 with those three needs named explicitly in the spike's v2 list so they are
not forgotten.

### Why not RRT-Connect (fork 2)?

RRT-Connect is the escape hatch for tight packings where Dubins-only + order-retry
fails to converge. It adds 300–500 lines and a *probabilistic* determinism story
that would have to be reconciled with ADR-0003's seeded contract. Dubins-only is
~80 lines, closed-form, and deterministic. We take RRT-Connect on only once
Dubins-only has been shown empirically insufficient — and its failure is honest:
the planner bails with a structured error naming the offending plane.

### Why not a true cart-lift primitive (fork 3)?

A `lift_pose` / `place_pose` move with no path between is more physically faithful,
but it multiplies the move-data shape and the renderer surface for a v1 that does
not yet need it. A zero-radius "turn" is honest for cart-on-cart motion — operators
do pivot a carted plane in place. The true primitive is filed as v2 work, bundled
with the sequence-level cart-cap question (see *Open question* below).

### Why not loosen the schema — "option A" (fork 4)?

Option A (set `turn_radius_m: 0.0` in `data/fleet.yaml` for the three cart planes
and have `required_turn_radius_m()` return `0.0` instead of raising) gives a single
source of truth and no downstream special-casing — the spike doc leaned this way.
We rejected it because it **bakes a v1 modelling approximation into the
source-of-truth data file**: the value `0` is the *planner's* "carts pivot in place"
choice, not a physical property of the airframe. If the v2 cart-lift primitive
replaces that approximation, option A forces a `fleet.yaml` walk-back; option B
needs only a code change. Option A also weakens the `required_turn_radius_m()`
bug-guard — a real own-gear plane with a malformed `0` would no longer be caught —
and any future curvature (`1/radius`) reader would divide by zero on a value the
data now presents as legitimate. Keeping `null` in the data preserves its honest
meaning: *this carted plane has no own-gear taxi radius.*

## Consequences

### Positive

- `turn_radius_m` becomes load-bearing for the first time, consumed honestly
  through one accessor; the planner body has no cart special-case.
- No schema, loader, validator, or `fleet.yaml` value change — the smallest
  possible footprint for the cart decision, and the v2 cart-lift primitive can
  revise the approximation in code without a data migration.
- The planner is **deterministic by construction** (total-order sort with a
  `plane_id` tie-break; closed-form Dubins; deterministic retry-swap), so the
  bundled `(Layout, MovesPlan)` output of `solve` preserves ADR-0003's contract.
- `required_turn_radius_m()` stays strict, so own-gear-only callers keep their
  bug-guard.

### Negative

- Two turn-radius accessors (`required_` and `effective_`) coexist; a future
  planner (e.g. RRT-Connect) could call the wrong one. Mitigated by a docstring
  on each naming its intended caller, and by the towplanner being the only
  `effective_` consumer in v1.
- Dubins-only ignores intervening obstacles: a closed-form arc may pass through
  an already-placed plane, recoverable only by re-ordering. On tight packings the
  retry loop may fail to converge; that surfaces as a structured error, not a
  silent bad plan.

### Neutral

- **Supersedes the arc42 §8 cart-kinematics framing.**
  [§8 Crosscutting Concepts](../architecture/08-crosscutting-concepts.md) currently
  states *"holonomic on carts; Dubins-path-style on own gear"* — a two-mode model.
  This ADR collapses both onto a single Dubins primitive (carts get
  `turn_radius_m = 0`), the opposite convention. The §8 docs-sweep issue must
  retire the holonomic-on-carts wording or §8 will contradict the code.
- The door is promoted from "visual marker only" to a **towplanner-level motion
  gate** (entry pose constrained to the door interval, heading into the hangar).
  `collisions.check` semantics are untouched — nothing in the layout checker now
  cares about the door beyond the hangar-bounds rule it already enforces.

## Open question (deferred, not decided here)

**Sequence-level cart cap.** The existing rule caps `cart_eligible` planes at one
per *layout* (`Layout.__post_init__`); it says nothing about a *sequence*. If two
candidate layouts each legally place a different `cart_eligible` plane on the cart,
the v1 planner happily plans both — each target layout satisfies the per-layout cap.
Whether the rule should tighten to "at most one cart-eligible plane on the cart
across the whole sequence" is left open. `MovesPlan` deliberately carries no
cart-usage tally, so adding one later is non-breaking. The natural place to revisit
is the v2 true cart-lift primitive.

## Compliance

- The Dubins primitive ships with a **45°-heading canary test** modelled on
  `tests/test_geometry.py::test_heading_45_right_wingtip_in_plus_x_minus_y_quadrant`.
  Dubins literature uses CCW-positive-radians while hangarfit uses
  CW-positive-degrees compass headings ([ADR-0002](0002-determinant-minus-one-transform.md));
  the symmetric CSC/CCC matrix would silently pass a sign-flip regression, so the
  canary is the real compliance check for the convention.
- Any PR touching `geometry.py` / `collisions.py` callers (the sampled
  collision-during-motion check) must be reviewed by the `geometry-invariant-guard`
  subagent per the standing CLAUDE.md rule.
- The cart decision is verified by a unit test asserting
  `effective_turn_radius_m()` returns `0.0` for `always_cart` planes and matches
  `required_turn_radius_m()` otherwise, and that `data/fleet.yaml` still validates
  with `null` for the cart planes (no schema change leaked in).
- Determinism is verified by the existing seeded-reproducibility tests extended to
  the bundled `(Layout, MovesPlan)` output.

## More Information

- Related spike: [Tow-path planning (#180)](../spikes/tow-path-planning.md) — the
  eight-question exploration this ADR locks into scope.
- Related ADRs:
  [ADR-0002](0002-determinant-minus-one-transform.md) (the sign-flip trap any new
  geometry caller must respect),
  [ADR-0003](0003-rr-mc-solver-algorithm.md) (the determinism contract the planner
  preserves),
  [ADR-0004](0004-diversity-metric.md) (supplies the multiple target layouts the
  bundled output spans).
- Related issues: [#195](https://github.com/DocGerd/hangarfit/issues/195) (this ADR),
  [#196](https://github.com/DocGerd/hangarfit/issues/196) (the module + order-retry
  loop this ADR gates), [#180](https://github.com/DocGerd/hangarfit/issues/180)
  (the spike).
- Related concepts: [§8 Crosscutting Concepts](../architecture/08-crosscutting-concepts.md)
  — the cart-kinematics framing this ADR supersedes.
