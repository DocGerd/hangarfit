# ADR-0025: Ground-object taxonomy — concrete catalog types, Layout-uniform placement, and pairwise-set keep-out seam

- **Status:** Accepted

- **Date:** 2026-06-11
- **Deciders:** [@DocGerd](https://github.com/DocGerd)

## Context & Problem Statement

The hangar floor holds more than aircraft. The real Airfield Herrenteich set
adds a **fuel trailer** (fixed, never moves), **two glider trailers** (towed),
and a **VW Caddy** (self-driven). The current model has exactly two non-aircraft
geometry flavours, both *hangar-intrinsic* keep-outs: the state-gated
`MaintenanceBay` and the always-on `StructuralNotch` list (ADR-0018). There
is **no model for a free-standing object** — fixed or moveable — authored as
scenario/layout data with a pose. Without one there is no way to represent a
parked fuel trailer blocking the door-throat, a glider trailer occupying corner
space, or a van that must be able to drive out past every parked aircraft.

This ADR records the **taxonomy of ground objects** introduced in #601:
the concrete catalog `type:` vocabulary, the Layout-uniform placement approach,
and the collision seam that makes fixed obstacles into keep-outs and movers
into pairwise collision bodies. It does *not* cover mover route search — the
actual path-planning for movers, including the ADR-0010 motion-model amendment,
is deferred to issue #602.

## Decision Drivers

- **Real-world fidelity.** A fuel trailer parked at the door *is* a physical
  obstacle; the validity checker must see it.
- **Max reuse.** The existing `Part` / `Placement` / det-−1 transform
  machinery already handles oriented rectangles at arbitrary poses — there is
  no reason to build a parallel geometry engine.
- **Oblique obstacles.** A fuel trailer parked diagonally at the door-corner
  cannot be represented as a floor-polygon subtraction (axis-aligned only);
  the pairwise-set seam handles any heading natively.
- **Byte-identical when empty.** Existing layouts must produce bit-identical
  `CheckResult` and `MovesPlan` output when no ground objects are present —
  protecting the ADR-0003 solver contract.
- **Forward-compat for movers.** The motion-mode field must be *carried as
  data* even if the route search is deferred, so #602 and #603/#604 can layer
  on without breaking the model.
- **Friendlier authoring.** Catalog YAML should read like real objects
  (`type: car`, `type: trailer`) rather than a generic discriminator
  (`type: placed_routed_mover`).

## Considered Options

### D1 — catalog-type vocabulary

1. **Concrete `type:` values** — `fixed_obstacle`, `car`, `trailer` — each
   with its own `_build_*` builder; `object_class` is *derived* from the type
   *(chosen)*.
2. **Two abstract `type:` values** — `fixed_obstacle` and
   `placed_routed_mover` — matching the two `object_class` literals, one
   builder per abstract class.
3. **Single `ground_object` type** with a mandatory `motion_mode:` field
   (including `None` for fixed obstacles); one builder.

### D2 — placement approach

1. **Layout-uniform via existing `Placement`.** Ground objects reuse `Part`
   (footprint), `Placement` (pose), and the det-−1 world transform, held in
   parallel `ground_objects` / `ground_object_placements` fields on `Layout`
   *(chosen)*.
2. **A new `GroundPlacement` type** mirroring `Placement` but typed to
   `GroundObject` rather than `Aircraft`. Separate transform path.
3. **Embed placement inside `GroundObject`** — the catalog entry carries its
   own pose; no separate `Placement` needed.

### D3 — keep-out seam for fixed obstacles

1. **Pairwise-set seam** — a fixed obstacle is a placed body whose
   `kind="ground"` parts are checked against every aircraft/mover part exactly
   like the existing pairwise predicate, emitting a `ground_obstacle` conflict
   *(chosen)*.
2. **Floor-polygon subtraction** — union the fixed-obstacle footprints into a
   forbidden-zone Shapely polygon, check aircraft against it like the hangar
   bounds.
3. **Dedicated `keep_out_zones:` top-level field on `Layout`** — axis-aligned
   rectangles, similar to `structural_notches`.

## Decision Outcome

**Chosen: D1 = option 1 (concrete types); D2 = option 1 (Layout-uniform via
`Placement`); D3 = option 1 (pairwise-set seam).**

### D1 — concrete catalog types

Three concrete `type:` values map onto two `object_class` values:

| `type:` in catalog | builder | `object_class` | motion default |
|---|---|---|---|
| `fixed_obstacle` | `_build_fixed_obstacle` | `"fixed_obstacle"` | `None` (rejected if authored) |
| `car` | `_build_car` | `"placed_routed_mover"` | `"steerable"` (overridable) |
| `trailer` | `_build_trailer` | `"placed_routed_mover"` | `"towed"` (overridable) |

The `object_class` is derived from the type at load time and stored on
`GroundObject`. Motion behaviour is **defaulted per concrete type** but kept
as an overridable **data field** (`motion_mode:`) so a car that must be
towed (hypothetically) can say so without requiring a code change.

**Why not D1 option 2 (two abstract types)?**
`placed_routed_mover` is a valid internal classifier but a poor catalog
keyword — a hangar operator authoring a VW Caddy entry should write
`type: car`, not `type: placed_routed_mover`. The concrete types are
self-documenting, per-type allowlists catch authoring errors early, and
the per-type builders set sensible motion defaults so the catalog author
rarely needs `motion_mode:` at all.

**Why not D1 option 3 (single type + mandatory motion_mode)?**
It conflates the type discrimination (what the object *is*) with the
motion model (how it *moves*). Fixed obstacles would need `motion_mode:
null`, a surprising author requirement. A single builder cannot enforce
the "fixed_obstacle must not carry motion fields" invariant without
per-type branches — which is exactly what the per-type builders give
cleanly.

### D2 — Layout-uniform via `Placement`

A `GroundObject` carries a `parts: tuple[Part, ...]` (footprint geometry,
using the new `"ground"` `PartKind`) and a catalog-level pose. The layout
adds two parallel fields:

```python
ground_objects: Mapping[str, GroundObject] = {}          # catalog subset
ground_object_placements: tuple[Placement, ...] = ()     # poses (reuses Placement)
```

The `Placement.plane_id` field carries the ground-object id, exactly as it
carries an aircraft id for aircraft placements. The `ground_objects` key set
and the `fleet` key set are kept **disjoint** by a `Layout.__post_init__`
invariant — a placement id resolves to either an aircraft or a ground object,
never ambiguously.

The det-−1 world transform (`geometry.aircraft_parts_world`) is widened to
accept a `GroundObject` in addition to an `Aircraft`. Ground-object `Part`s
carry the new `PartKind` value `"ground"` — a footprint-only kind, not
subject to the wing-nesting height rule.

**Why not D2 option 2 (new `GroundPlacement` type)?**
It duplicates the `Placement` dataclass and the transform path for no
behavioural gain. Every caller that iterates `layout.placements` would need
a second loop over `layout.ground_object_placements` with a different type —
two code paths to maintain, with all the same geometry logic.

**Why not D2 option 3 (pose embedded in the catalog entry)?**
The catalog is static data — it describes *what* an object is, not where it
sits in a specific hangar layout. Embedding a pose would prevent reusing the
same catalog entry at different positions in different layouts (e.g. a
`glider_trailer` entry used twice in one layout). The aircraft catalog
imposes the same discipline: `data/catalog/aviat_husky.yaml` has no position;
every layout places its own instance.

### D3 — pairwise-set keep-out seam

Fixed-obstacle world parts join the pairwise overlap loop in
`collisions.check`. Any aircraft or mover part whose plan-view polygon (plus
clearance) overlaps a fixed-obstacle part and whose height range is within
`wing_layer_clearance_m` fires a **`ground_obstacle`** `Conflict` — a
single-plane conflict (only the aircraft/mover is named; the obstacle is
named in `detail`). The `_parts_conflict` predicate is reused unchanged.
Fixed-obstacle ↔ fixed-obstacle overlaps are **ignored** (two static keep-outs
may coincide).

Movers join the existing pairwise aircraft↔aircraft loop, emitting the
standard `<sorted_kinds>_overlap` conflicts (using `"ground"` as the part
kind tag in the alphabetical sort). Mover↔aircraft and mover↔mover pairs
are both checked.

**Why not D3 option 2 (floor-polygon subtraction)?**
It is axis-aligned only — a fuel trailer parked diagonally at the door-corner
cannot be correctly represented. It also diverges from the `_parts_conflict`
predicate used by the oracle, creating a risk of in-search (pairwise) vs
verifier (polygon subtraction) divergence. The pairwise approach handles any
heading and reuses the validated predicate.

**Why not D3 option 3 (dedicated `keep_out_zones:`)?**
It would be a third keep-out mechanism alongside `structural_notches` and the
maintenance bay, for what is fundamentally the same geometry question — does
this shape overlap that shape? Reusing the pairwise seam keeps the rule count
small and the predicate surface unified.

### Tow-planner wiring (A1 scope)

Fixed obstacles' world parts join `_build_obstacles`' **static** obstacle set
in `towplanner.py`, sorted by id for determinism. Movers appear in `plan_fill`'s
routable enumeration alongside aircraft. In A1 the per-mover **path search is
deferred to #602**: a mover is recognised in the routed set but receives
`path=None` (the existing best-effort `plans[i] = None` pattern), naming
itself on stderr. AC4 is satisfied by the mover *appearing in the enumeration*,
not by a successful route.

The ADR-0010 *motion* amendment (Reeds–Shepp arc parameters for steerable /
towed movers) is deferred to #602.

### The `"ground"` PartKind

A new `PartKind` value `"ground"` is added to the closed `Literal` set. It is
a footprint-only kind — it participates in plan-view overlap (pairwise seam)
and in `_parts_conflict`'s height clause when checked against aircraft/mover
parts, but it is never overhangable (not in `metrics._OVERHANGABLE`) and is
never split by the fuselage front/aft loader logic.

### Byte-identity with empty ground-object sets

When `Layout.ground_objects` is empty and `ground_object_placements` is an
empty tuple, every new code path is a guarded no-op: `collisions.check`
produces a bit-identical `CheckResult`, `plan_fill` produces a bit-identical
`MovesPlan`, and the solver's double-run canary passes unchanged. This is the
primary mechanism by which A1 is additive — existing fixtures and existing
solver seeds are unaffected.

### Real catalog entries deferred to #605

> **Update (#605, 2026-06-11): landed.** The four real entries shipped as
> `vw_caddy`, `glider_trailer_1`, `glider_trailer_2`, and `maul_fuel_trailer`
> (the fixed fuel trailer; the `fuel_trailer` name below was provisional), along
> with the Herrenteich clearance calibration (0.3/0.2 → 0.20/0.15) and an
> extension of `collisions.check` to bounds/notch-check ground objects. The text
> below is retained as the #601 (A1) decision record.

A1 ships the taxonomy and the loader; the catalog currently carries only
**test fixtures** under `tests/fixtures/catalog/`. The real Herrenteich
objects (`fuel_trailer`, `vw_caddy`, `glider_trailer_1`, `glider_trailer_2`)
and the dims/clearance calibration to a feasible all-11 arrangement are
deferred to #605.

## Consequences

### Positive

- Ground objects have a clean first-class home: a frozen-slots `GroundObject`
  model with the same invariant discipline as `Aircraft`.
- Fixed obstacles are keep-outs that block aircraft *and* movers; the
  fuel-trailer-at-the-door scenario is now expressible and checked.
- Movers join collision pairwise immediately; route search lands in #602
  without any model change.
- Empty-set byte-identity protects the ADR-0003 solver contract; no existing
  test or canary needs updating.
- Oblique obstacles (any heading) work natively via the existing pairwise path.

### Negative

- `PartKind` gains a new value (`"ground"`), widening the closed `Literal`.
  Any exhaustive match on `PartKind` outside the project must be updated.
- `geometry.aircraft_parts_world` is widened to accept `GroundObject`; callers
  that type-annotate the first argument strictly need to update to the union
  type.
- The `Layout` dataclass gains two new fields; construction sites that pass
  positional arguments rather than keyword arguments would break (standard
  dataclass convention says: always use kwargs for anything beyond the first
  field).

### Neutral

- The `"ground"` kind does not participate in the wing-nesting height rule
  directly — ground objects are not aircraft and do not nest wings. The two-
  clause predicate still applies when checking an *aircraft* part against a
  ground part (the aircraft is the one that might fly over); from the ground
  object's perspective it is simply a static obstacle at whatever z-band it
  occupies.
- `PartKind` is now seven values: `"fuselage_front"`, `"fuselage_aft"`,
  `"ground"`, `"strut"`, `"tail"`, `"vertical_stabilizer"`, `"wing"`. The
  alphabetical conflict-kind taxonomy auto-derives `ground_ground_overlap`
  (only ever between two **movers**; two fixed obstacles are exempt — obstacles
  never enter the pairwise set), `ground_wing_overlap`, etc.

## Compliance

- **`tests/test_models_ground_object.py`** — `GroundObject` valid
  construction for all three concrete types; `__post_init__` rejects empty
  id/name/parts, `fixed_obstacle` with motion fields, mover without
  `motion_mode`, non-positive `turn_radius_m`.
- **`tests/test_loader_ground_object.py`** — each `type:` loads from a
  fixture; defaults applied; unknown `type:` → `LoaderError`; per-type
  allowlists; layout `ground_objects:` block round-trip.
- **`tests/test_collisions.py`** — a fixed obstacle overlapped by an aircraft
  part ⇒ `ground_obstacle` conflict; non-overlapping ⇒ valid; byte-identity
  on an existing fixture layout with empty ground-object fields.
- **`tests/test_towplanner.py`** — a door-blocking fixed obstacle makes an
  otherwise-routable plane un-routable; a mover appears in `plan_fill`'s
  routed enumeration (path deferred/`None`); double-solve on a fixed seed
  with no ground objects ⇒ byte-identical plan.
- **The `determinism-guard`** runs on every PR touching `solver.py` or
  `towplanner.py`; the empty-set byte-identity guarantee is its primary
  protection here.

## More Information

- Related ADRs: [ADR-0001](0001-aircraft-parts-model.md) (the parts model
  this extends), [ADR-0003](0003-rr-mc-solver-algorithm.md) (determinism
  contract, empty-set byte-identity), [ADR-0010](0010-reeds-shepp-motion-model.md)
  (motion model — amendment for movers deferred to #602),
  [ADR-0018](0018-non-rectangular-hangar-footprint.md) (structural notches,
  the other non-aircraft keep-out mechanism).
- Related spec:
  [`docs/superpowers/specs/2026-06-11-601-ground-object-data-model-design.md`](../superpowers/specs/2026-06-11-601-ground-object-data-model-design.md).
- Related issues: #601 (this ADR), #602 (mover motion + ADR-0010 amendment),
  #603 (Caddy hard-egress gate), #604 (soft trailer region), #605 (real
  Herrenteich catalog entries + calibration), #606 (rendering).
- [§5 Building Block View](../architecture/05-building-block-view.md) — module
  responsibilities updated alongside this ADR.
- [§8 Crosscutting Concepts — "The parts model"](../architecture/08-crosscutting-concepts.md#the-parts-model)
  — "Ground objects" subsection added alongside this ADR.
