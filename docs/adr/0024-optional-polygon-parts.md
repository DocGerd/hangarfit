# ADR-0024: Optional polygon footprints on `Part` — load-time-canonicalized, authored via `planform:`

- **Status:** Accepted

- **Date:** 2026-06-10
- **Deciders:** [@DocGerd](https://github.com/DocGerd)

## Context & Problem Statement

Every `Part` in the collision model is an oriented rectangle in plan view
([ADR-0001](0001-aircraft-parts-model.md)). Subsequent refinements
([ADR-0012](0012-fuselage-front-aft-split.md), [ADR-0023](0023-empennage-tail-surfaces.md))
improved realism only by *adding* rectangles. A tapered glider wing (e.g.
the Scheibe SF-25E) fits in the plan-view footprint of its bounding
rectangle, yet the bounding rectangle extends past the wingtip into
space that is genuinely free. The spike (#541,
`docs/spikes/polygon-part-geometry-feasibility.md`) measured a robust
**0.10–0.30 m verdict-flip window** on the real Herrenteich layout:
a tapered Scheibe wingtip nests safely against the Stemme empennage where
the bounding-rectangle model falsely reports a conflict. The collision
build-path (`geometry.aircraft_parts_world`) is already polygon-generic
(Shapely `Polygon`); the only thing missing is a vertex sequence on `Part`
and a loader schema to author it honestly.

This ADR records the polygon extension of [ADR-0001](0001-aircraft-parts-model.md).
The parts model's founding design — closed `PartKind` set, scalar
`length_m`/`width_m`/`offset_x_m`/`offset_y_m`, part-own height band
`[z_bottom_m, z_top_m]` — is unchanged and stays Accepted. ADR-0012's
front/aft split and ADR-0023's empennage surfaces are likewise untouched.

## Decision Drivers

- **Verdict accuracy.** A `valid` report on a layout whose bounding
  rectangles falsely conflict (when the real polygon does not) is a
  spuriously rejected arrangement — lost packing density for no safety
  reason. The measured flip window is 0.10–0.30 m, large enough to
  matter for a tight hangar.
- **Determinism contract (ADR-0003).** The geometry layer must never
  re-orient polygon rings at solve time. Identical shapes in any author
  order must produce a byte-identical `Part`, or the seeded random-restart
  solver's byte-identity contract is broken.
- **No honest raw-outline data source.** 0/8 fleet aircraft expose a
  dimensioned plan-view outline in any TCDS; raw vertices would look
  surveyed while being fabricated. The authoring primitive must be
  parametrized (not free-form) so that every polygon traces back to a
  published scalar.
- **Scalar fleets must stay byte-identical.** Existing placements and
  solver runs must be unaffected by this change; `None` vertices must
  be a genuine no-op.
- **Forward-compat for tilt and meshes.** The transform pipeline and
  `[z_bottom, z_top]` bands must remain self-contained per part so a
  future wing-tilt/roll DoF and true-3D meshes (ADR-0001's deferred
  upgrade path) can extend without rewriting consumers.

## Considered Options

### P1 — How to store the polygon on `Part`

1. **Optional `local_vertices: tuple[tuple[float, float], ...] | None`
   field, defaulting to `None`, load-time-canonicalized in
   `__post_init__`** *(chosen)*.
2. **Optional `local_polygon: shapely.Polygon | None`** — store the
   Shapely object directly on the model.
3. **A subclass `PolygonPart(Part)`** — separate type for parts with
   polygon footprints.

### P2 — Canonicalization site and contract

1. **Load-time only: `Part.__post_init__` calls a module-level
   `_canonicalize_ring(verts)` helper that is unit-testable in
   isolation; the geometry layer never re-orders** *(chosen)*.
2. **Lazy: canonicalize on first call to `aircraft_parts_world`.**
3. **No canonicalization: caller is responsible for vertex order.**

### P3 — The bounding-box / area trade

1. **Polygon must be a strict subset of the `length_m × width_m` bbox;
   wing area is intentionally under-conserved** *(chosen)*.
2. **Grow `length_m` to the root chord** — polygon is area-conserving
   but the scalar `length_m` changes.

### P4 — Loader authoring primitive

1. **Parametrized `planform: {root_chord_m, tip_chord_m}` block only;
   no raw `vertices:` field** *(chosen)*.
2. **Both `planform:` and a raw `vertices:` field, with the same
   canonicalization applied.**

## Decision Outcome

**Chosen: P1 = option 1; P2 = option 1; P3 = option 1; P4 = option 1.**

The pivot is **P2**: canonicalization at `Part.__post_init__` is the
ADR-0003 determinism crux. The geometry layer calls `Shapely.Polygon()`
with the stored vertices verbatim — Shapely preserves vertex order, so
canonical storage is the only safe place to enforce byte-identity across
equivalent author orderings. A unit-testable `_canonicalize_ring` helper
decouples the invariant from the build-path and makes the contract
directly verifiable.

`_canonicalize_ring` applies four steps: (1) reject non-finite
coordinates, fewer than 3 vertices, self-intersecting (non-simple) rings,
and degenerate / collinear rings (signed area ≈ 0); (2) force CCW by
signed-area sign; (3) rotate to a lex-min start vertex; (4) drop the
closing duplicate (store an open ring). Two equivalent input orderings of
the same shape produce a byte-identical `Part`.

**P1 tuple-of-tuples field**, because `Part` is `frozen=True, slots=True`
(hashable dataclass); Shapely objects are not hashable and cannot live in a
frozen slot. `None` is a genuine no-op: the `aircraft_parts_world`
build-path's `oriented_rect` branch is reached unchanged; no scalar
consumer sees any change; the solver's byte-identity over the entire
existing fleet is preserved.

The `aircraft_parts_world` branch for a non-None `local_vertices` routes
every canonical vertex through `local_to_world` directly (the det(−1)
affine from [ADR-0002](0002-determinant-minus-one-transform.md)), skipping
`oriented_rect`. No centroid or bbox shortcut is taken — every declared
vertex rides the same transform, so the geometry-invariant-guard's
per-vertex non-axis-aligned canary passes.

### Why not P1 option 2 (Shapely object on `Part`)?

`shapely.Polygon` is not hashable, which breaks `frozen=True` and the
model's use of `Part` in sets and as dict keys throughout the codebase.
Storing the raw vertex tuple and letting the geometry layer construct the
Shapely object at call time is the clean separation — the model owns the
geometry data, the geometry module owns the Shapely construction.

### Why not P1 option 3 (subclass `PolygonPart`)?

It fractures every `isinstance(part, Part)` check, every pattern-match
on `PartKind`, and the closed-type guarantees throughout `models.py`,
`collisions.py`, and `visualize.py`. The optional field is strictly
additive — scalar and polygon parts are the same kind of thing, just
with different footprint precision.

### Why not P2 option 2 (lazy canonicalization)?

Lazy canonicalization means the `Part` object's internal state depends on
when `aircraft_parts_world` is first called. Two `Part` instances built
from the same vertices but never yet passed to the geometry layer would
compare unequal — breaking the frozen-dataclass equality semantics and
making the ADR-0003 byte-identity contract impossible to reason about
without tracing call order.

### Why not P2 option 3 (no canonicalization)?

Author vertex order becomes load-bearing. The same tapered wing authored
with the lex-first corner starting or the lex-second corner starting would
produce different `Part`s, different solver states, and a broken
byte-identity contract. The canonicalization step exists precisely to close
that gap.

### Why not P3 option 2 (grow `length_m` to root chord)?

Setting `length_m` equal to the root chord (longer than the published mean
chord) changes the scalar `length_m` that drives `metrics`, scene box
sizing, `towplanner` apron calculations, and the trivial-infeasibility area
gate. Every consumer that reads the scalar would get a different number,
forcing a golden-value re-pin across tests and potentially changing solver
and tow-planner outputs. The verdict-flip value — the entire reason this
ADR exists — is reproduced equally well by a polygon that sits strictly
inside the existing `length_m` bbox, because the flip is driven by the
polygon's *reduced* footprint, not by its root chord. Keeping
`root_chord_m = existing length_m` means no scalar changes anywhere.

**The bbox / area trade (from spec §5.1, important):** `length_m × width_m`
remains the bounding box for all scalar consumers (`metrics`, scene box,
`towplanner` apron). The loader asserts the canonical polygon ring is a
strict subset of that bbox. Wing area is intentionally **under-conserved**:
the bounding box over-claims the true footprint, and the polygon sits safely
inside it. This is the *conservative* footprint direction — it never
creates a false "valid" verdict (the box can only over-reject, never
under-reject). The trivial-infeasibility area gate reads
`length_m × width_m`, so it stays a sound lower bound.

### Why not P4 option 2 (also a raw `vertices:` field)?

0/8 fleet aircraft expose a dimensioned plan-view outline in any TCDS
(spike Q2, unanimous panel verdict). A raw `vertices:` field would ship
with zero honest data, *look* surveyed (which `measured: false` understates
on a per-vertex basis), and turn every canonicalization branch into a
load-bearing gate against adversarially-authored rings with no validation
payoff. `Part.local_vertices` is the shared internal store, so a raw
authoring primitive is a strictly additive future branch behind the same
invariant if a surveyed outline ever becomes available. YAGNI.

**The `planform:` schema.** `loader._build_part` gains a `planform:
{root_chord_m, tip_chord_m}` block, mirroring the `struts:` idiom exactly.
The block expands into `local_vertices` via a symmetric double-taper with
**no sweep and a root kink at y=0**, producing a **hexagon** (both leading
and trailing edges recede toward each tip; not a simple 4-vertex trapezoid).
Validation: `0 < tip_chord_m ≤ root_chord_m` (a glider wing does not
taper outward). Unknown keys are rejected; required keys are checked.

The folded Stemme wing deliberately stays a rectangle. Folding swings the
outer panels — it is not a taper, and a linear-taper polygon would fabricate
a planform that does not physically exist in the hangared (folded)
configuration. That is a provenance violation, not a fidelity gain.

## Consequences

### Positive

- The measured 0.10–0.30 m verdict-flip window on the Herrenteich layout
  is resolved: a tapered glider wingtip that genuinely clears a neighbour
  is no longer falsely rejected.
- The determinism contract (ADR-0003) is preserved end-to-end: canonical
  storage means two equivalent author orderings produce byte-identical
  solver runs.
- Scalar fleets are byte-identical — no existing placement, solver output,
  or test golden value changes.
- The bbox-subset invariant keeps the area gate and all scalar consumers
  (`metrics`, scene box, `towplanner` apron) sound and unchanged.
- The transform pipeline (`local_to_world` per vertex, no shortcuts)
  leaves the seam open for a future wing-tilt/roll DoF and true-3D meshes
  without rewriting any consumer (ADR-0001's mesh deferral stands; see §6
  of the design spec).

### Negative

- The 3D viewer (`hangarfit view`) renders bounding-box prisms until the
  `scene/v2` work (#549); during this transitional period the collision
  model uses the polygon while the viewer displays the enclosing rectangle.
- Each `planform:`-authored part gains a canonicalization cost at load
  time (negligible in practice; the geometry layer constructs Shapely
  objects at solve time regardless).
- A `planform:` block ported to an aircraft whose `length_m` differs from
  the expected bbox fails at load time (the bbox-subset assertion), which
  is the intended behaviour but requires a data update alongside any
  `length_m` change.

### Neutral

- **Refines ADR-0001's mesh deferral.** ADR-0001 stated "rectangles now,
  meshes later via a superseding ADR." This ADR is the first step: optional
  polygon footprints at 2.5D. The full mesh deferral — true-3D curved
  geometry — remains deferred; ADR-0001 stays Accepted.
- `PartKind` and the collision predicate are unchanged. The polygon is a
  footprint refinement, not a new kind of part or a new collision rule.
- The `local_vertices` field is `None` for every part in `data/fleet.yaml`
  until explicit `planform:` blocks are authored; the feature is invisible
  to all existing callers.

## Compliance

- **`tests/test_part_polygon.py`** — unit matrix for `_canonicalize_ring`
  (CCW forcing, lex-min-start rotation, closing-duplicate drop, rejection of
  non-finite coordinates / fewer-than-3 vertices / self-intersecting rings /
  degenerate rings; two equivalent input orderings → identical `Part`) plus
  the `Part.local_vertices` field and bbox-subset tests.
- **`tests/test_geometry.py`** — per-vertex det(−1) transform at a
  non-axis-aligned heading (geometry-invariant-guard requirement); confirms
  no centroid shortcut is taken.
- **`tests/test_loader_planform.py`** — `planform:` parse, `tip > root`
  rejection, unknown-key rejection, root-exceeds-bbox rejection.
- **`tests/test_solver_canaries.py::test_solve_deterministic_polygon_taper_fleet`**
  — a placed-taper scenario fed to a `solve()` double-run asserts
  byte-identity; necessary because existing canaries exclude the Scheibe or
  park it as the maintenance occupant (geometrically absent from
  `placements`).
- **`tests/test_fleet_polygon_optin.py`** — guards that every shipped-fleet
  `Part` keeps `local_vertices is None`, proving no regression and the
  byte-identity guarantee for this PR.
- No automated check guards the bbox-subset invariant at merge time beyond
  the loader test; breakage is caught at load time with a `ValueError`.

## More Information

- Related ADRs: [ADR-0001](0001-aircraft-parts-model.md) (the mesh-deferral
  ADR this refines), [ADR-0002](0002-determinant-minus-one-transform.md)
  (the det(−1) transform applied per vertex), [ADR-0003](0003-rr-mc-solver-algorithm.md)
  (the byte-identity contract the canonicalization protects),
  [ADR-0012](0012-fuselage-front-aft-split.md) and
  [ADR-0023](0023-empennage-tail-surfaces.md) (the prior rectangle-based
  refinements that motivated a more accurate footprint).
- Related spec:
  [`docs/superpowers/specs/2026-06-10-realistic-polygon-plane-geometry-design.md`](../superpowers/specs/2026-06-10-realistic-polygon-plane-geometry-design.md).
- Related spike:
  [`docs/spikes/polygon-part-geometry-feasibility.md`](../spikes/polygon-part-geometry-feasibility.md).
- Related issues / PRs: [#548](https://github.com/DocGerd/hangarfit/issues/548)
  (polygon parts Phase 1), [#541](https://github.com/DocGerd/hangarfit/issues/541)
  (feasibility spike).
- [§8 Crosscutting Concepts — "The parts model"](../architecture/08-crosscutting-concepts.md#the-parts-model)
  — the operational statement, updated alongside this ADR.
