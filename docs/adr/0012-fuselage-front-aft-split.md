# ADR-0012: Split the fuselage into front/aft so a wing may overhang a tail but not a cockpit

- **Status:** Accepted
- **Date:** 2026-05-28
- **Deciders:** [@DocGerd](https://github.com/DocGerd)

## Context & Problem Statement

The Phase-1 parts model ([ADR-0001](0001-aircraft-parts-model.md)) represents
each aircraft's fuselage as a **single** oriented rectangle. The pairwise
overlap rule treats *any* wing-over-fuselage overlap at a separated height
(z-gap ≥ `wing_layer_clearance_m`) as valid — the height-disjoint pass-through
case. That rule is geometrically correct but operationally too coarse: a wing
hanging over the **aft fuselage / tail** of a parked plane is fine (empty,
walkable, no canopy, no prop disc), but a wing over the **cockpit / front
fuselage** is not (it blocks the canopy, sits in/near the prop arc on a
tractor, and obstructs pilot ingress/egress). The single undifferentiated
fuselage box cannot tell these two regions apart. This ADR records two
coupled decisions: **(D1)** how the new `wing × fuselage_front` rule treats
height, and **(D2)** how existing data migrates to the split representation.

This is a refinement *within* the parts model, not a replacement of it —
ADR-0001 stays **Accepted**.

## Decision Drivers

- **The operational rule must be expressible.** "A wing may overhang another
  plane's aft fuselage / tail, but not its cockpit / front fuselage."
- **Robustness to placeholder measurements.** Every fleet dimension is a guess
  (`measured: false`). A rule that depends on a precise height gap over the
  cockpit would give different verdicts as those guesses are refined.
- **Consistency with existing idioms.** The closed `PartKind` literal, the
  alphabetical-sort conflict-kind taxonomy, the `struts:` YAML-expands-to-Parts
  authoring model, and one ADR per load-bearing decision.
- **No silent verdict changes by accident.** Migration must change a layout's
  verdict *only* where a wing actually overlaps the now-front segment — which
  is precisely the behaviour change this work wants.

## Considered Options

### D1 — how `wing × fuselage_front` treats height

1. **Hard conflict, `z` ignored** *(chosen)* — a wing within
   `clearance_m` of another plane's `fuselage_front` in plan view conflicts
   regardless of the height gap.
2. **Large vertical clearance (`cockpit_clearance_m`)** — a new, much larger
   configurable height threshold (e.g. 2.0 m) than `wing_layer_clearance_m`
   (0.2 m), keeping one uniform "plan-view-close AND height-gap < threshold"
   predicate shape.

### D2 — schema migration

1. **No new field — auto-split a legacy `kind: fuselage` at the wing
   trailing-edge station, derived from the aircraft's own `wing` part**
   *(chosen)*. The loader splits a `fuselage` part into `fuselage_front` +
   `fuselage_aft` at `x_break = wing.offset_x_m − wing.length_m/2` before
   constructing the `Aircraft`; there is no `wing_root_x_m` YAML field.
   Explicit `fuselage_front`/`fuselage_aft` parts remain a valid override.
2. **Require explicit `fuselage_front` / `fuselage_aft` declarations** —
   remove `kind: fuselage` from the accepted set; every aircraft hand-declares
   two area-conserving segments and the loader does no splitting.
3. **Auto-split at a per-aircraft `wing_root_x_m` field** — keep `kind:
   fuselage` and add an optional plane-local break station, defaulting to the
   wing trailing edge when omitted.

## Decision Outcome

**Chosen: D1 = option 1 (hard conflict, `z` ignored); D2 = option 1 (no field,
derive the break from the wing).**

**D1**, because the domain objection to wing-over-cockpit is categorical, not
geometric: there is no nesting height at which a wing over a cockpit becomes
acceptable in a club hangar. Option 1 encodes that directly, adds zero config,
and is the most robust to the project's pervasive placeholder measurements.

**D2**, because deriving the break from the wing geometry is the direct analogue
of the wing-spar anchoring the project already trusts ([#282](https://github.com/DocGerd/hangarfit/issues/282)):
a readable YAML convenience the loader expands into canonical Parts, with the
parts tuple remaining the single source of truth — zero data diff to
`fleet.yaml`, and the area-conserving arithmetic owned in one tested place.

The contract:

- **Geometry.** In plane-local coords (`+x` forward), a fuselage spanning
  `x ∈ [c − L/2, c + L/2]` splits at `x_break = wing.offset_x_m −
  wing.length_m/2` (the wing trailing edge) into `fuselage_front`
  (`[x_break, c + L/2]`, nose side) and `fuselage_aft` (`[c − L/2, x_break]`,
  tail side). Both inherit the source `width_m`, `z_bottom_m`, `z_top_m`,
  `angle_deg`, `offset_y_m`; the two boxes abut at `x_break` and their union
  reconstitutes the original footprint exactly (area-conserving).
- **The rule.** `wing × fuselage_front` → conflict on plan-view overlap, `z`
  ignored. `wing × fuselage_aft` → today's two-clause z-gap rule. Every other
  pair is unchanged.
- **Taxonomy.** The conflict kind is the two part kinds sorted alphabetically
  + `_overlap`: `"fuselage_aft"` < `"fuselage_front"` < `"wing"`, giving
  `fuselage_aft_wing_overlap`, `fuselage_front_wing_overlap`, and (for two
  fuselages) `fuselage_aft_fuselage_aft_overlap` etc. The single legacy
  `fuselage_fuselage_overlap` / `fuselage_wing_overlap` kinds are retired.

### Why not D1 option 2 (large `cockpit_clearance_m`)?

It invents a number nobody can measure. The "right" cockpit clearance is a
proxy for a 3-D pilot-access / prop-arc cone, not a single height; picking
2.0 m is as arbitrary as picking ∞, but ∞ (option 1) is honestly arbitrary
with no tuning knob to get wrong. It also adds config surface (`Hangar` field,
loader default, `hangar.yaml`, the §8 clearance table, model/loader tests) for
a knob whose every realistic value drives toward "always conflict" in a tight
club hangar, and it re-introduces the placeholder-height fragility option 1
avoids. The extra expressiveness only models a wing clearing a short cockpit
by metres — a case the operator would reject anyway. If a future hangar ever
genuinely needs it, a later ADR can add `cockpit_clearance_m` additively
without invalidating option-1 code.

### Why not D2 option 2 (explicit two-part declarations)?

It breaks every existing file at once: all nine `fleet.yaml` entries and every
embedded-fleet fixture would need hand-edited, hand-computed, area-conserving
`offset_x_m` / `length_m` for each segment — exactly the manual,
mirror-the-numbers burden ADR-0001 rejected for struts. It walks back the
established YAML-ergonomics decision for no benefit the auto-split lacks, and
each of the nine hand-splits is a chance to fat-finger a station and silently
shift a verdict.

### Why not D2 option 3 (`wing_root_x_m` field)?

It is backward-compatible like the chosen option, but adds a second way to
express the same plane (`fuselage` + `wing_root_x_m` vs. explicit segments vs.
the derived default) and a YAML field that, in the absence of real
measurements, every aircraft would leave at the derived default anyway. The
wing-spar precedent (#282) already established that anchoring to the wing
chord — not a hand-typed station — is the right model; a field would be an
un-exercised knob carrying drift risk. Dropping it keeps the `fleet.yaml` diff
at zero (only a header comment) and the break a single derived value.

## Consequences

### Positive

- The operational rule is expressible: wing-over-tail valid, wing-over-cockpit
  invalid at any height.
- Zero `fleet.yaml` data diff; existing layouts/fixtures keep loading.
- The area-conserving split arithmetic lives in one tested place
  (`loader._split_fuselage`), mirroring `_expand_struts`.
- Robust to placeholder heights — the cockpit rule is z-independent.

### Negative

- One more pairwise branch in the collision predicate (the cockpit exception),
  and a larger conflict-kind taxonomy (segment-pair kinds replace the single
  `fuselage_*` kinds).
- Two valid input shapes for a fuselage (auto-split `kind: fuselage`, or
  explicit `fuselage_front`/`fuselage_aft`). Mitigated by documenting the
  auto-split as the canonical authoring path; explicit segments are the escape
  hatch for asymmetric fuselages.
- Verdicts that depended on a wing-over-front-fuselage being legal change
  (intended): the default `examples/layouts/example.yaml` was re-nudged and several
  fixtures' expected conflict-kind sets were re-pinned.

### Neutral

- `PartKind` stays a closed `Literal`, now five kinds; `tail` remains a
  separate, currently-unused kind (the empennage folds into `fuselage_aft` —
  the aft *region* includes the tail, so a separate tail segment would add a
  kind with no distinct rule).
  **Amended by [ADR-0023](0023-empennage-tail-surfaces.md):** this last claim no
  longer holds. The empennage does *not* fully fold into `fuselage_aft` — its
  lateral span and its fin's vertical extent are unmodelled there — so ADR-0023
  makes the tail surfaces explicit (`tail` for the horizontal stabilizer, a new
  `vertical_stabilizer` for the fin). The fin *does* exercise a distinct
  outcome, though via the *uniform* two-clause predicate, not a new rule.
  ADR-0012's front/aft split (D2) and the `wing × fuselage_front` cockpit rule
  (D1) are unaffected and stay Accepted.
- The visualizer tints `fuselage_front` a darker shade of the wing-position
  fill so the cockpit boundary reads at a glance. (At the time of this ADR
  `_draw_gear_glyph` reconstructed the full fuselage span from both segments to
  place the gear heuristically; [ADR-0013](0013-wheels-canonical-data.md) later
  replaced that with canonical per-aircraft wheel data, so the gear glyph no
  longer depends on the fuselage segments.)

## Compliance

- **`tests/test_collisions.py`** — `TestWingOverFuselageSegment` pins D1: a
  wing over `fuselage_front` at a z-disjoint height fires exactly one
  `fuselage_front_wing_overlap` (`invalid_wing_over_cockpit.yaml`); the same
  wing over `fuselage_aft` is valid (`valid_wing_over_tail.yaml`). Cases 4 / 5
  pin the segment-pair taxonomy and the alphabetical kind order; case 3
  (`valid_high_over_low_aft_z_disjoint`) is the reframed wing-over-tail
  positive control.
- **`tests/test_loader.py`** — `TestFuselageSplit` pins the area-conservation
  invariant (front ∪ aft == original, abutting at `x_break`), the no-wing
  rejection, the break-outside-span rejection, and the explicit-segment
  override.
- **`PartKind` is a `Literal` validated in `Part.__post_init__`** — a raw
  `kind: "fuselage"` Part is rejected at construction; the loader's pre-pass is
  the only place a `fuselage` keyword is accepted.
- **`geometry-invariant-guard`** reviews the `collisions.py` change to confirm
  the det(−1) transform is untouched (the new branch only drops a height
  clause; no coordinate math changed).

## More Information

- [ADR-0001: Aircraft geometry as a list of parts](0001-aircraft-parts-model.md)
  — the parts model this refines (stays Accepted; the fuselage split is a
  refinement within it, not a supersession).
- [ADR-0002: Coordinate transform with determinant −1](0002-determinant-minus-one-transform.md)
  — the transform the collision change must not disturb.
- [§8 Crosscutting Concepts — "The parts model"](../architecture/08-crosscutting-concepts.md#the-parts-model)
  — the operational statement of this decision.
- Related spec: [`docs/superpowers/specs/2026-05-27-fuselage-parts-split-design.md`](../superpowers/specs/2026-05-27-fuselage-parts-split-design.md).
- Related issue: [#50](https://github.com/DocGerd/hangarfit/issues/50).

## Amendment (#550, 2026-06-27): polygon fuselage outline → Shapely clip

D2's auto-split now has a second path. When the source `kind: fuselage` part
carries an outline polygon (the raw `vertices:` YAML key, part-own centred
frame), the front/aft split is a **Shapely half-plane clip** at the same
wing-trailing-edge `x_break`, producing two area-conserving **sub-polygons**
(`fuselage_front` / `fuselage_aft`) instead of two boxes. The scalar
box-interval split is unchanged and stays the path for every current aircraft
(byte-identical — no catalog fuselage carries `vertices:`). D1 (the
`wing × fuselage_front` hard-conflict rule), the conflict-kind taxonomy, and the
"break derived from the wing chord, not a YAML station" decision are all
unchanged. The clip is interpolation-only (deterministic, cross-machine-robust),
re-canonicalized by `Part.__post_init__`, and requires an axis-aligned fuselage
(`angle_deg = 0`). It enforces "exactly one non-degenerate Polygon per side" —
the formal guarantee that the front sub-outline is genuinely the cockpit; a
non-x-monotone outline that would clip into disconnected nose-side pieces is a
`LoaderError`. This re-opens D2 additively (a capability; no fleet behaviour
change). See
[`docs/superpowers/specs/2026-06-27-fuselage-outline-polygon-design.md`](../superpowers/specs/2026-06-27-fuselage-outline-polygon-design.md).
