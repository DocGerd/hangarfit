# ADR-0006: Maintenance bay rule — `bay_intrusion` on any non-occupant vertex strictly inside the bay rectangle

- **Status:** Accepted (supersedes [ADR-0005](0005-maintenance-bay-rule.md))
- **Date:** 2026-05-23
- **Deciders:** [@DocGerd](https://github.com/DocGerd)

## Context & Problem Statement

The Phase 1 maintenance bay rule (ADR-0005) treated the bay as a *soft*
placement hint: a single check on the maintenance plane's
fuselage centroid, with no semantics for the bay as a physical
obstacle. Milestone #9 — "Maintenance bay walling" —
made the bay a real two-state region of the hangar: **open** (no
occupant) is just normal floor, **closed** (occupant in maintenance)
turns the bay perimeter into a hard wall that no other plane's parts
may cross. The occupant itself is treated as *away* — absent from
`layout.placements` by Layout invariant (#103) — and no longer
participates in any collision check at all.

That semantic shift required a new collision rule. The Phase 1
fuselage-centroid-in-back-strip check could not be extended to express
"the bay rectangle is a wall" without preserving the centroid pipeline
and the `maintenance_no_fuselage` edge case it carried with it. The
milestone work also introduced **partial-width** bay geometry
(`center_x_m`, `width_m`, `depth_m` in `MaintenanceBay`, also #103) —
a back-anchored sub-rectangle that doesn't always span the full hangar
width — and the new rule had to honor that geometry exactly.

This ADR records the rule as it stands today in
[`src/hangarfit/collisions.py::_bay_intrusion_conflicts`](../../src/hangarfit/collisions.py).
ADR-0005 documents the Phase 1 predecessor that this rule supersedes.

## Decision Drivers

- **The bay is now a hard obstacle.** The check must answer "does any
  non-occupant part have geometry inside the bay rectangle?" — a
  containment question on actual part vertices, not a centroid-vs-strip
  comparison.
- **Match the per-part granularity of `_hangar_bounds_conflicts`.** The
  hangar-bounds rule already emits one conflict per offending part with
  the first-violating vertex; the bay rule is the structural dual
  (inverted-rectangle keep-out rather than rectangle containment) and
  should mirror that shape so the two conflict streams read consistently
  in the `CheckResult` and in the visualizer.
- **No structural edge case to surface.** The `maintenance_no_fuselage`
  conflict from ADR-0005 existed because the centroid was undefined
  when no fuselage parts were present. The new rule iterates per-vertex
  and per-part with no special-casing — there is no "what if the
  occupant has no fuselage parts" question because the rule doesn't
  inspect the occupant at all.
- **Compose with the parts model (ADR-0001) and transform (ADR-0002).**
  The check consumes the same world-coordinate parts the rest of
  `collisions.py` produces; it adds no parallel geometry path.
- **Honor the partial-width geometry from #103.** The bay rectangle
  is `x ∈ (center_x_m − width_m/2, center_x_m + width_m/2)`,
  `y ∈ (length_m − depth_m, length_m]` — not a full-width back strip.
  The rule must use the exact same arithmetic the visualizer and the
  loader use, so the three layers agree on what "inside the bay" means.

## Considered Options

1. **Vertex strictly inside the bay rectangle fires `bay_intrusion`** —
   per-part, per-vertex check; mirrors `_hangar_bounds_conflicts` as
   the inverted dual. *(Chosen.)*
2. **Polygon ∩ bay-rectangle area > ε fires `bay_intrusion`** —
   integer geometric overlap rather than per-vertex containment.
3. **Phantom-occupant model** — represent the closed bay as a
   non-aircraft `Part`-shaped obstacle inside the existing pairwise
   parts-overlap check.
4. **`shapely.difference` model** — treat the closed bay as
   geometrically subtracted from the hangar floor; the existing
   hangar-bounds check then naturally rejects anything that lands in
   the subtracted region.
5. **Keep the ADR-0005 centroid rule and layer a wall-clearance test
   on top of it** — preserve "in the bay" semantics for the occupant
   and add a "non-occupant clearance from bay" check separately.

## Decision Outcome

**Chosen option: vertex-strictly-inside-bay-rectangle fires
`bay_intrusion`**, because it composes with the parts model
(ADR-0001) and the existing world-part pipeline without introducing a
parallel geometry layer, surfaces *which part* of which plane intruded
(at the same per-part granularity as `_hangar_bounds_conflicts`), and
naturally honors partial-width geometry by using the same rectangle
arithmetic the visualizer and loader use.

Concretely, in
[`src/hangarfit/collisions.py::_bay_intrusion_conflicts`](../../src/hangarfit/collisions.py):
when `layout.maintenance_plane is not None`, every non-occupant part is
scanned vertex-by-vertex. The bay is the axis-aligned rectangle
anchored to the back wall:

- `x ∈ (center_x_m − width_m/2, center_x_m + width_m/2)` — three
  interior edges use strict `<` (a vertex *on* the edge sits in the
  side aisle, not in the bay).
- `y ∈ (length_m − depth_m, length_m]` — the front-of-bay edge uses
  strict `<`, but the back edge is inclusive: it coincides with the
  hangar's outer wall, which `_hangar_bounds_conflicts` already treats
  as inside, so the bay rule must match.

Any vertex that lands strictly inside the rectangle emits a single
`bay_intrusion` conflict on the owning plane, naming the offending part
and the first-violating vertex.

### Why not polygon-area-overlap?

It is more arithmetic for identical semantics at the resolution we
care about. Any non-occupant part whose overlap area is positive has at
least one vertex (of the part *or* of the bay) inside the other
rectangle; the vertex check is faster and more diagnostically useful
(it can name the violating vertex's coordinates in the conflict
detail, which area-overlap cannot). The area-overlap approach also
would have required choosing an ε threshold and defending it under
floating-point edge cases — the strict-inside `<` predicate has none of
that complexity.

### Why not the phantom-occupant model?

Two reasons. **Semantic confusion:** the closed bay is a wall, not a
plane; treating it as a `Part` in the pairwise-overlap loop conflates
the two categories. A future reader of `CheckResult` would have to know
that one of the "planes" in the conflict graph is actually a wall.
**Conflict-count inflation:** the bay would generate a part-pair
collision with every overlapping part of every plane, so a plane with
five offending parts would emit five `pairwise_overlap` conflicts
against the phantom (matching the existing pairwise granularity) — but
that conflates "this plane crashes into the wall" with "these two
planes crash into each other," which the operator and the solver care
about distinguishing. A dedicated `bay_intrusion` kind keeps the two
streams separate.

### Why not the `shapely.difference` model?

It collapses two concerns into one — bay-keep-out and hangar-bounds —
which sounds elegant but loses information: a conflict at the bay
boundary would report as `hangar_bounds` even though it is a
maintenance-bay intrusion. The downstream consumers (visualizer
conflict overlay, JSON output, solver pre-search) would lose the
ability to distinguish the two. The new rule deliberately keeps them
distinct: `hangar_bounds` for the outer wall, `bay_intrusion` for the
inner wall. The `shapely.difference` model also requires the rest of
`collisions.py` to keep importing shapely-via-the-difference-result,
where today the rule lives in a small dedicated function that only
needs vertex-list iteration.

### Why not centroid + wall-clearance?

The centroid rule applied to the occupant; the wall-clearance check
would apply to non-occupants — two checks instead of one, with
different shapes, against different planes. The new rule's whole point
is that the occupant is *away* (absent from `placements`) and
contributes nothing geometric; layering a centroid check for "is the
occupant in the bay?" on top of "is anyone else in the bay?" would
re-introduce the `maintenance_no_fuselage` edge case for the first
check and produce two conflict streams the operator has to mentally
correlate. The chosen rule eliminates both.

## Consequences

### Positive

- **Composes with the parts model.** No parallel geometry layer; the
  rule reuses the same world-coordinate parts that
  `_hangar_bounds_conflicts` and the pairwise-overlap check already
  iterate.
- **Per-part conflict emission identifies the offending geometry.**
  When a plane has multiple parts inside the bay, each one fires its
  own `bay_intrusion` conflict with the first-violating vertex named
  in `detail`. The operator sees exactly which wingtip is in the
  wrong place, not just "this plane is misplaced."
- **The `maintenance_no_fuselage` edge case is gone.** The new rule
  doesn't inspect the occupant at all — there is no
  centroid-of-undefined-set question to surface. The legacy conflict
  kind has been retired from the taxonomy.
- **Partial-width bay geometry is honored exactly.** The rule uses the
  same arithmetic (`center_x_m ± width_m/2`, `length_m − depth_m`) as
  the visualizer (`_draw_maintenance_bay`) and the loader, so the
  three layers agree on what "inside the bay" means.
- **The bay's back edge is inherited correctly.** It coincides with
  the hangar's back wall, and `_hangar_bounds_conflicts` treats the
  hangar boundary as inclusive — the bay rule uses the same
  convention, so a vertex at `y = length_m` is consistently treated
  as inside the closed bay (not in some no-man's-land between the
  rules).
- **The defensive occupant-skip is a free safety net.** Even though
  the Layout invariant from #103 guarantees the occupant is absent
  from placements, the implementation skips
  `plane_id == layout.maintenance_plane` explicitly. A future bug that
  let the occupant leak into a Layout (a hand-built test fixture
  bypassing `__post_init__`, a solver regression) would otherwise
  silently emit a nonsense "occupant intrudes into its own bay"
  conflict.

### Negative

- **Per-vertex iteration replaces a single centroid computation.** The
  ADR-0005 rule was one centroid per `check_layout` call; the new rule
  is `O(parts × vertices_per_part)` per call. In practice this is
  still small (≤ 9 planes × ≤ 4 parts × 4 vertices ≈ 144 vertex tests
  per call, all axis-aligned comparisons), and the hangar-bounds rule
  already pays the same per-part cost, so the practical impact is
  negligible — but it is a real change worth naming.
- **The Layout invariant from #103 is now load-bearing for the rule's
  correctness.** If a future change weakened the invariant (allowing
  the occupant in `placements`), the rule would silently emit the
  nonsense "occupant intrudes into its own bay" conflict (the
  defensive skip catches this, but only on the assumption that the
  skip stays in place). The dependency is documented inline.

### Neutral

- **The rule lives in `collisions.py`, not in `models.py`.** Same
  rationale as ADR-0005: the rule needs bay geometry + part geometry +
  the plane-local-to-world transform, all of which the checker already
  composes. Moving the rule into the data layer would import the
  geometry layer into `models.py`, which the parts model deliberately
  keeps separate.
- **`Layout.__post_init__` invariant flipped from Phase 1.** Phase 1
  required the occupant to be *in* `placements`; the new invariant
  requires the occupant to be *absent* from `placements`. The flip is
  the structural prerequisite for this ADR but lives in `models.py`,
  not here.

## Compliance

- **Positive cases:**
  [`tests/test_collisions.py::TestBayIntrusion`](../../tests/test_collisions.py)
  covers the open-bay no-op path (`maintenance_plane is None`),
  closed-bay clear (occupant set but no intruder), partial-width
  side-aisle parking, and vertices exactly on the bay edge (strict
  `<` boundary). Five fixture-based goldens
  (`tests/fixtures/valid_bay_*`).
- **Negative case:**
  `tests/test_collisions.py::TestBayIntrusion::test_intrusion_wingtip_fires_bay_intrusion`
  exercises the canonical "wingtip into closed bay" scenario; the
  `invalid_bay_intrusion_wingtip.yaml` fixture pins it.
- **Legacy retirement:**
  [`tests/test_collisions.py:377-386`](../../tests/test_collisions.py)
  asserts that the `maintenance_position` and
  `maintenance_no_fuselage` conflict kinds are not present in any
  emitted conflict — a regression guard against accidental
  re-introduction.
- **Solver pre-search integration:**
  `tests/test_solver_infeasibility.py::test_solve_trivially_infeasible_when_pinned_plane_intrudes_into_closed_bay`
  exercises the case where the pin-only Layout build inside
  `_check_trivially_infeasible` fires a `bay_intrusion` — confirming
  the rule short-circuits the solver before any restart runs.
- **Defensive skip:**
  `tests/test_collisions.py` contains a synthetic-Layout test that
  calls `_bay_intrusion_conflicts` directly with a `world_parts` dict
  that includes the maintenance plane (bypassing the Layout invariant)
  and asserts no conflict is emitted — the defensive `continue` is
  pinned.

## More Information

- [ADR-0005: Maintenance bay rule — fuselage centroid in back strip](0005-maintenance-bay-rule.md)
  — the Phase 1 predecessor; this ADR supersedes it. ADR-0005's
  status is now `Superseded by ADR-0006`.
- [ADR-0001: Aircraft geometry as a list of parts](0001-aircraft-parts-model.md)
  — the parts model the new rule consumes; vertex-iteration is the
  natural primitive at this layer.
- [ADR-0002: Coordinate transform with determinant −1](0002-determinant-minus-one-transform.md)
  — the transform that maps plane-local parts into hangar coordinates
  for the vertex-in-bay test.
- [§8 Crosscutting Concepts — "The maintenance bay rule"](../architecture/08-crosscutting-concepts.md#the-maintenance-bay-rule)
  — operational statement of this rule (the architectural single
  source of truth; the ADR records the *decision*, §8 records the
  *behavior*).
- [§5 Building Block View — `collisions.py`](../architecture/05-building-block-view.md)
  — the rule's module-level role.
- **Spec:**
  [`docs/superpowers/specs/2026-05-22-maintenance-bay-walling-design.md`](../superpowers/specs/2026-05-22-maintenance-bay-walling-design.md)
  §4 (approach selection: A = vertex-inverted-rectangle, B = phantom
  occupant, C = shapely subtract) and §5 (rule details).
- **Implementation:**
  [`src/hangarfit/collisions.py::_bay_intrusion_conflicts`](../../src/hangarfit/collisions.py).
- **Milestone:**
  [Milestone #9 — Maintenance bay walling](https://github.com/DocGerd/hangarfit/milestone/9)
  and epic [#110](https://github.com/DocGerd/hangarfit/issues/110).
  Sub-issues: [#103](https://github.com/DocGerd/hangarfit/issues/103)
  (model expansion + invariant flip),
  [#104](https://github.com/DocGerd/hangarfit/issues/104)
  (`bay_intrusion` rule),
  [#107](https://github.com/DocGerd/hangarfit/issues/107) (fixtures
  for the rule), [#108](https://github.com/DocGerd/hangarfit/issues/108)
  (solver drops occupant), [#106](https://github.com/DocGerd/hangarfit/issues/106)
  (visualizer conditional rendering),
  [#105](https://github.com/DocGerd/hangarfit/issues/105) (loader
  occupant-in-placements actionable error),
  [#109](https://github.com/DocGerd/hangarfit/issues/109) (docs sweep).
- Related issue: this ADR backfills the formal record per
  [#158](https://github.com/DocGerd/hangarfit/issues/158).
