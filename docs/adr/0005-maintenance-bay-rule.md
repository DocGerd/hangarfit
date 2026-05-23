# ADR-0005: Maintenance bay rule — fuselage centroid in back strip, explicit conflict on no fuselage

- **Status:** Deprecated — superseded in code by the `bay_intrusion` rule shipped during milestone #9 (the bay-walling work, [#103](https://github.com/DocGerd/hangarfit/issues/103)). A formal successor ADR is tracked in [#158](https://github.com/DocGerd/hangarfit/issues/158); the body below records the Phase 1 rule as it stood until then.
- **Date:** 2026-05-21
- **Deciders:** [@DocGerd](https://github.com/DocGerd)

## Context & Problem Statement

The hangar's back-most strip doubles as the **maintenance bay** — the
curtained-off area where the technician works. A scenario designates one
aircraft as the maintenance occupant; the checker has to decide whether
a candidate layout actually parks that aircraft in the bay. "In the bay"
is a domain rule, not a geometric tautology — it has to be defined
precisely enough that the solver can pin against it and the visualizer
can render conflicts against it. Phase 1 had to commit to a definition
before any test fixture or solver constraint could be written.

This ADR records the rule as it stands today
(`src/hangarfit/collisions.py`). Milestone #9 ("Maintenance bay
walling") is in flight and is layering a richer model on top —
notably the bay as a physical obstacle (`bay_intrusion_rule`, #104) and
partial-width bays (#103). If that work extends or replaces this rule,
a follow-up ADR will record the supersession.

## Decision Drivers

- **The rule must be a single-point check.** "Plane X is in the bay" has
  to be true or false for any given placement, computable from
  `layout.placements[X]` and the aircraft's parts alone — no
  layout-global state, no order dependence, no iteration.
- **The rule must match the operational intuition.** The maintenance
  technician looks at the layout and asks "is the body of the plane
  back where I work on it?" — they don't care about wingtips that
  protrude forward toward the door, and they don't care about
  millimeter-precise edge alignment.
- **The rule must surface unevaluatable cases loudly, not silently.**
  A scenario where the designated maintenance plane has *no* fuselage
  parts is structurally undefined; silently passing or silently failing
  would be the worst-case outcome. The checker has to say so.
- **The rule must compose with the parts model (ADR-0001) and the
  transform (ADR-0002).** It is consumed by the collision checker,
  which already iterates over world-coordinate parts; the rule should
  reuse that pipeline rather than introduce a parallel geometry path.

## Considered Options

1. **Fuselage-parts centroid in the back strip, with an explicit
   `maintenance_no_fuselage` conflict when no fuselage parts exist**
   — the chosen option.
2. **Any fuselage part touches the back strip.**
3. **All fuselage parts entirely inside the back strip.**
4. **The aircraft's plan-view bounding box overlaps the bay rectangle.**
5. **Centroid of *all* parts (not just fuselage) in the back strip.**
6. **Silent pass when no fuselage parts exist.** (Listed explicitly so
   the rejection is on the record — see Decision Outcome.)

## Decision Outcome

**Chosen option: the fuselage centroid rule with an explicit
`maintenance_no_fuselage` conflict on the structural edge case.**

Concretely, in
[`src/hangarfit/collisions.py`](../../src/hangarfit/collisions.py)
(`_maintenance_conflicts`, around line 107): the check filters the
world-coordinate parts of `layout.maintenance_plane` to those with
`kind == "fuselage"`, computes the area-weighted centroid, and tests
whether its `y` coordinate satisfies
`y ≥ hangar.length_m − hangar.maintenance_bay.depth_m` — equivalently,
the code emits a `maintenance_position` conflict iff
`centroid_y < bay_start_y` (strict `<`; a fuselage centroid that lands
*exactly* on the bay boundary counts as parked in the bay, not as
violating). If the designated plane has zero fuselage parts, emit a
`maintenance_no_fuselage` conflict instead of silently passing.

### Why not "any fuselage part touches the back strip"?

A plane with its nose 30 cm inside the bay and its tail near the door
would qualify — but that is not what the operational rule wants. The
technician needs the *body* of the plane back where they work, not just
its nose. "Touches" is too lenient; a single conforming centimeter
should not satisfy the rule.

### Why not "all fuselage parts entirely inside the back strip"?

Too tight. A tailwheel plane parked correctly inside the bay might still
have its tail-fuselage segment protrude 5 cm past the rear-side
boundary of the bay rectangle (or, more commonly, project a small slice
past the *forward* boundary because the bay depth is set by partition
position, not by aircraft length). The human operator would unambiguously
call that placement "in the bay." A strict-containment rule fails such
placements while adding no operational value.

### Why not "plane's bounding box overlaps the bay rectangle"?

This reverts to a bbox model, which the project rejected globally in
ADR-0001 — for good reasons that still apply here. Worse, it would
include the *wings* in the qualifying overlap. A high-wing whose
wingtip happens to project over the bay rectangle would qualify as "in
the bay" even though the fuselage is nowhere near the back strip. The
fuselage is the body of the plane; including wings turns the rule into
"some plane geometry is over the bay area," which is not what
"maintenance plane is in the bay" means.

### Why not "centroid of all parts (not just fuselage)"?

Wings, struts, and tail surfaces can have substantial plan-view area
relative to the fuselage. For wing-area-dominated aircraft (most
notably the `scheibe_falke`, with an 18 m wingspan and a comparatively
short fuselage), the all-parts centroid lands further forward than the
fuselage centroid — sometimes meters forward. A "plane is at the back"
rule that registers `scheibe_falke` as further forward than every
other aircraft when its body is identically placed is the wrong
answer. Fuselage-only is the right narrowing: the body of the plane is
what defines "the plane is at the back."

### Why not silent pass on no-fuselage?

This is exactly the silent-failure shape that the project's general
guidance rejects: a hard constraint that quietly admits an
unevaluatable scenario will be discovered only when a downstream
consumer (the solver, the visualizer, a human reviewer) notices a
suspicious layout — and may not be discovered at all. The explicit
`maintenance_no_fuselage` conflict surfaces the case at the checker
level, with a named conflict kind that fixtures can pin against and
that the visualizer can render. It is rare on the current fleet (every
placeholder aircraft has fuselage parts), but the cost of the explicit
emission is one branch in `_maintenance_conflicts`, and the benefit is
that a future aircraft definition or a YAML-authoring slip cannot ever
silently bypass the rule.

## Consequences

### Positive

- **Order-independent and stateless.** The rule depends only on the
  designated plane's parts and the hangar back-strip threshold; nothing
  about other planes or about iteration order can change the verdict.
- **Matches operational intuition.** "Body of the plane at the back" is
  what the technician asks; the fuselage centroid encodes it cleanly.
- **The no-fuselage edge case is observable.** Tests can pin the
  `maintenance_no_fuselage` conflict; the visualizer can show it;
  scenarios that hit it fail loudly rather than silently.
- **Composes with the rest of the checker.** The rule sits inside
  `check()` between hangar-bounds and pairwise-parts overlap, and uses
  the same world-coordinate parts the rest of the checker uses; no
  parallel geometry path is needed.

### Negative

- **The rule does not check bay *containment* of wings or tail.** A
  plane whose body is centered correctly in the bay but whose long
  tail-cone protrudes back through a (real, physical) bay wall is
  reported as conforming. Milestone #9's `bay_intrusion_rule` (#104)
  is the planned defense; until that ships, the current rule trusts
  the layout author / solver to not park planes whose tails punch
  through walls.
- **The centroid is a single point.** A pathologically shaped fuselage
  (e.g., a hypothetical aircraft whose fuselage has been modeled as two
  disjoint segments fore and aft) can have a centroid that lands
  between them in empty plan-view space. The fleet today contains no
  such aircraft, but the assumption deserves naming.

### Neutral

- **The rule lives in `collisions.py`, not in `models.py`.** The cart
  rule and other cross-reference invariants are enforced upstream in
  `Layout.__post_init__`, but the maintenance-bay rule needs the
  hangar's bay geometry, the aircraft's fuselage parts, AND the
  plane-local-to-world transform — all of which the checker already
  composes. Moving the rule into `models.py` would import the geometry
  layer into the data layer, which the parts model deliberately keeps
  separate.

## Compliance

- **Positive cases:** `tests/test_collisions.py` covers a layout whose
  designated maintenance plane is centered in the back strip and
  asserts no `maintenance_position` conflict is emitted. Fixtures in
  `tests/fixtures/valid_maintenance_*.yaml` exercise this directly.
- **Negative cases:** `tests/test_collisions.py` covers a layout whose
  designated maintenance plane is parked forward of the bay; the
  expected `maintenance_position` conflict is asserted. Fixtures in
  `tests/fixtures/invalid_maintenance_*.yaml` exercise this.
- **No-fuselage case:** a dedicated test constructs an `Aircraft` with
  no fuselage parts, designates it as the maintenance plane, and
  asserts the `maintenance_no_fuselage` conflict is emitted. This is
  the regression-test for the explicit-emission decision and was a
  conscious add during Phase 1 to lock the behaviour in.
- **Cross-reference invariants** (the maintenance plane must be in
  `layout.fleet` and absent from `layout.placements`) are enforced in
  `Layout.__post_init__`, not here, so the checker can assume a
  well-formed layout. See `src/hangarfit/models.py`.

## More Information

- [ADR-0001: Aircraft geometry as a list of parts](0001-aircraft-parts-model.md)
  — makes "fuselage centroid" a coherent concept (fuselage is one
  closed-set `PartKind`; centroid is over those parts in plan view).
- [ADR-0002: Coordinate transform with determinant −1](0002-determinant-minus-one-transform.md)
  — the transform that maps the plane-local fuselage parts into hangar
  coordinates for the back-strip test.
- [`src/hangarfit/collisions.py`](../../src/hangarfit/collisions.py)
  — `_maintenance_conflicts` is the implementation; the
  `maintenance_no_fuselage` and `maintenance_position` conflict
  emissions are both inside that function.
- [`src/hangarfit/models.py`](../../src/hangarfit/models.py)
  — `Hangar.maintenance_bay`, `Conflict.kind` taxonomy,
  `Layout.__post_init__` cross-reference checks.
- [§8 Crosscutting Concepts — "The maintenance bay rule"](../architecture/08-crosscutting-concepts.md#the-maintenance-bay-rule)
  — operational statement of the current `bay_intrusion` rule (which
  supersedes this ADR's centroid-in-back-strip rule); §5 Building
  Block View describes `collisions.py`'s role in enforcing it.
- **Open follow-up:** [Milestone #9 — Maintenance bay walling](https://github.com/DocGerd/hangarfit/milestone/9).
  Issues [#103](https://github.com/DocGerd/hangarfit/issues/103)
  (partial-width bay),
  [#104](https://github.com/DocGerd/hangarfit/issues/104)
  (`bay_intrusion_rule`), and the milestone epic
  [#110](https://github.com/DocGerd/hangarfit/issues/110) are layering
  a richer bay model on top. If that work redefines "in the bay" or
  introduces a bay-as-obstacle rule that subsumes this ADR's centroid
  check, a successor ADR should mark this one **Superseded by
  ADR-XXXX**. The centroid rule itself may survive as one of several
  conditions (e.g., centroid in bay AND no wall intrusion), in which
  case this ADR stays Accepted and the successor ADR layers on top.
- Related issue: [#136](https://github.com/DocGerd/hangarfit/issues/136)
  (the retroactive-ADR backfill that produced this record).
