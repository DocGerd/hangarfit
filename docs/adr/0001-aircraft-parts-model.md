# ADR-0001: Aircraft geometry as a list of parts (not a single bounding box)

- **Status:** Accepted
- **Date:** 2026-05-23
- **Deciders:** [@DocGerd](https://github.com/DocGerd)

## Context & Problem Statement

`hangarfit` has to decide, for any candidate parking layout, whether two
aircraft physically collide. The mixed fleet makes the simple answer
("compare bounding boxes") wrong: a high-wing's wingtip is legally
allowed to hang over a low-wing's fuselage area in plan view because
the two are at different heights, and a strut-braced plane's wing
volume is *not* free for another plane's wing to nest through — the
strut itself occupies a thin column between fuselage and wing
underside. Phase 1 had to commit, before any solver, visualizer, or
test fixture was written, to a geometric representation that could
distinguish "they overlap in plan view but at disjoint heights" from
"they collide." Every later subsystem keys off this choice; getting it
wrong would have made every downstream layout wrong.

## Decision Drivers

- **Height-disjoint pass-through must be expressible.** The only
  low-wing in the fleet (`fuji`) sits well below every high-wing's
  wing layer; a representation that cannot reason about height-disjoint
  parts cannot model this case at all.
- **Strut volume must be representable as occupied.** Six of the nine
  aircraft (`aviat_husky`, `wild_thing`, `zlin_savage`, `cessna_140`,
  `cessna_150`, `fk9_mkii`) are strut-braced. The strut is what
  prevents another plane's wing from nesting through the gap between
  fuselage and wing — and nothing in the wing's own footprint expresses
  that constraint.
- **Same-aircraft "self-collision" must not register.** A Husky's wing
  and its own strut occupy the same column by design; the
  representation has to give a natural way to skip pairs that share an
  aircraft ID, not require special-case suppression rules.
- **The collision predicate should stay small.** A clean two-part rule
  ("close in plan view AND close in height") is easier to test, easier
  to render the failure of (the visualizer overdraws conflicting parts
  in red), and easier to extend with a penetration metric later (which
  the Phase 2a solver depends on as a smooth secondary score).
- **YAML authoring stays human-scale.** Whichever representation wins
  internally, the file an operator edits (`data/fleet.yaml`) cannot
  require them to type out every strut twice with mirrored offsets.

## Considered Options

1. **A list of oriented-rectangle `Part`s per aircraft, each with a
   `[z_bottom_m, z_top_m]` height range** — the chosen option. The
   closed set of `PartKind` values (`fuselage`, `wing`, `strut`,
   `tail`) lives in [`src/hangarfit/models.py`](../../src/hangarfit/models.py).
2. **A single 2D axis-aligned (or oriented) bounding rectangle per
   aircraft** — one footprint, no height.
3. **A single 3D axis-aligned bounding box per aircraft** — adds a
   height range to option 2 but keeps the "one box per plane"
   simplification.
4. **A general 3D convex polygon mesh per aircraft** — the most
   expressive of the alternatives; full 3D overlap test.

## Decision Outcome

**Chosen option: a list of oriented-rectangle `Part`s with height
ranges.** It is the smallest representation that can express both
height-disjoint pass-through (heights live on the part) and strut
nesting (the strut is its own part, separately checkable), while
keeping the collision predicate to a two-line rule and skipping
same-aircraft pairs structurally rather than by special case.

The shape of the chosen representation: each `Aircraft` carries a
`tuple[Part, ...]`; each `Part` is an oriented rectangle in
plane-local coordinates plus `[z_bottom_m, z_top_m]`. The collision
checker [`src/hangarfit/collisions.py`](../../src/hangarfit/collisions.py)
iterates over part-pairs across distinct aircraft and applies:

> Two parts from different aircraft conflict iff (a) their plan-view
> polygons are closer than `hangar.clearance_m` AND (b) the gap
> between their `[z_bottom_m, z_top_m]` ranges is less than
> `hangar.wing_layer_clearance_m` (overlap counted as zero gap).

For YAML readability, the loader ([`src/hangarfit/loader.py`](../../src/hangarfit/loader.py))
accepts a high-level `struts:` block on each strut-braced aircraft
and expands it into two mirrored strut `Part`s before constructing
the `Aircraft`. The constructed `Aircraft` has no `struts:` field —
the parts tuple is the single source of truth, eliminating any risk
of strut volume being double-counted from both a struts block and a
strut Part.

### Why not a single 2D bounding rectangle?

It cannot model the only low-wing in the fleet at all. With a flat
footprint, the Fuji and any high-wing whose wingtip plan-projects
over the Fuji's fuselage area would have to be reported as
colliding — but they are physically fine; their wings live in
different height layers. The whole point of the exception tool is to
fit more planes in by overlapping in plan view where heights allow,
which a 2D-only model forbids by construction. It also cannot
distinguish a strut-braced plane from a cantilever plane: both
project to identical wing-plus-fuselage footprints in plan view, even
though only one of them blocks wing-nesting.

### Why not a single 3D axis-aligned bounding box?

A 3D bbox per plane captures height, which solves the high-wing /
low-wing case. But it cannot capture struts: the entire column from
ground to wing-top is part of *one* per-plane box, so even the
strut-free outboard region (where another plane's wing could legally
nest) is marked occupied. The fleet has both strut-braced and
cantilever high-wings; a representation that treats them identically
loses the most operationally-relevant distinction in the data. The 3D
bbox also forces a 3D overlap test, which is more arithmetic than the
chosen 2D-distance + height-gap predicate without buying back the
fidelity it cost.

### Why not a general 3D convex polygon mesh per aircraft?

Expressively, this is a superset of the chosen option — every Part
can be lifted to a 3D prism, and convex meshes can represent
fuselage curvature, wing dihedral, and tail empennage faithfully.
Rejected on cost-benefit grounds. The collision question that
actually matters for a flying club's tight hangar is "do these two
structures touch?" which the part-pair predicate already answers
correctly at the resolution real measurements will support
(centimeters via tape measure, not millimeters via 3D scan). Mesh
overlap is also substantially more code to write, test, and visualize
than the current `shapely`-polygon-pairs implementation, with no
operational decision riding on the extra fidelity. If a future use
case demands true 3D — for example, modeling a tug clearance under a
wing — a later ADR can supersede this one without invalidating any
of the Phase 1 or Phase 2a code that depends on the part-pair
predicate; the upgrade path is additive.

## Consequences

### Positive

- **Both fleet edge cases are expressible.** High-wing over low-wing
  fuselage passes (height-disjoint), and strut-blocks-nesting fails
  (strut is its own part).
- **Same-aircraft pairs are skipped structurally.** The collision
  loop iterates over pairs of aircraft, then pairs of parts within
  each aircraft pair; no special-case rule needed for a plane's wing
  vs. its own strut.
- **The collision predicate is small.** Two clauses (plan-view distance
  + height gap) — easy to test, easy to render the failure of, and
  easy to extend with a penetration metric (added in Phase 2a Chunk A
  as `CheckResult.total_penetration_m2`, used by the solver as a
  smooth secondary score that breaks plateaus in the integer
  `len(conflicts)` metric).
- **Visualization composes naturally.** Each Part is already a
  renderable polygon; the top-down PNG renderer
  [`src/hangarfit/visualize.py`](../../src/hangarfit/visualize.py)
  draws each Part once and overdraws conflicting parts in red without
  a separate geometry pipeline.

### Negative

- **More arithmetic per check.** For each aircraft pair, the loop
  considers O(parts_a × parts_b) part-pairs instead of one
  bbox-pair test. In the placeholder fleet (≤ 5 parts per plane —
  fuselage + wing + ≤ 2 struts + optional tail) the constant is
  small, but it scales linearly in part count if a future aircraft
  model needs more articulation.
- **YAML authors have to think in parts.** A bbox model would have
  one length × width per plane; the parts model has multiple offsets,
  orientations, and height ranges. The `struts:` convenience block
  mitigates this for the common strut-braced case but does not
  eliminate it; adding a new aircraft is more involved than filling
  in two numbers.

### Neutral

- **`PartKind` is a closed set.** Adding a new structural element
  (engine nacelle, ventral fin, wing-tip fuel tank) is a code change
  in `models.py` and the matching `_VALID_PART_KINDS` set, not just a
  YAML edit. This is a deliberate trade-off: a closed kind set keeps
  the visualizer's color scheme and the conflict-kind taxonomy
  stable, at the cost of YAML-only extension. The four kinds in use
  today cover the placeholder fleet.
- **The parts model says nothing about motion.** Holonomic-vs-Dubins
  motion (relevant for the future planner) is a property of the
  `Aircraft`, not its `Part`s; the geometry layer and the planner
  layer remain decoupled. This is intentional, not an oversight of
  the parts model.

## Compliance

- **The strut-aware golden tests in
  [`tests/test_collisions.py`](../../tests/test_collisions.py)** are
  the canary that the parts model is intact. They cover:
  same-height wing overlap (must fail), high-over-low height-disjoint
  pass-through (must pass), strut-blocks-nesting (must fail),
  inboard- and outboard-strut-free nesting (must pass), the
  maintenance-bay position rule, and a known-good layout for all
  nine aircraft on the larger test hangar. If those tests pass, the
  geometry is trustworthy on the current (placeholder) measurements.
- **Loader expansion is covered by the loader's tests** — the
  `struts:` YAML block is exercised end-to-end into the matching
  pair of mirrored strut `Part`s, so the YAML convenience can never
  silently desync from the canonical parts representation.
- **`PartKind` is a `Literal` validated in `Part.__post_init__`** —
  any code path constructing a `Part` with an unknown kind raises at
  construction time, not at the collision step. This is the type-level
  enforcement that keeps the closed set actually closed.

## More Information

- [ADR-0012: Split the fuselage into front/aft](0012-fuselage-front-aft-split.md)
  — refines this parts model: the single `fuselage` kind is replaced by
  `fuselage_front` + `fuselage_aft` so a wing may overhang another plane's
  tail but not its cockpit. ADR-0001 stays **Accepted** — the split is a
  refinement *within* the parts model (still oriented-rectangle Parts with
  height ranges, still the two-clause predicate plus one cockpit exception),
  not a supersession of the parts-not-bbox decision.
- [ADR-0002: Coordinate transform with determinant −1](0002-determinant-minus-one-transform.md)
  — the plane-local → world transform that makes Parts addressable in
  the hangar frame. The parts model and the transform are
  co-dependent: parts authored in plane-local coordinates only become
  collidable once placed.
- [§8 Crosscutting Concepts — "The parts model"](../architecture/08-crosscutting-concepts.md#the-parts-model)
  is the operational statement of this decision (the rule itself,
  with the two-clause collision predicate); this ADR records *why*
  the rule has the shape it does.
- [`src/hangarfit/models.py`](../../src/hangarfit/models.py) —
  `PartKind` literal, `Part` / `StrutsSpec` / `Aircraft` dataclasses,
  invariant checks.
- [`src/hangarfit/collisions.py`](../../src/hangarfit/collisions.py) —
  the part-pair loop and the two-clause predicate implementation
  (`_parts_conflict`).
- [`src/hangarfit/loader.py`](../../src/hangarfit/loader.py) —
  the `struts:` block expansion into mirrored strut `Part`s.
- Related issue: [#136](https://github.com/DocGerd/hangarfit/issues/136)
  (the retroactive-ADR backfill that produced this record).
