# Design: Fuselage outline polygon (#550) — capability-only

- **Status:** Approved design (brainstorming complete); ready for an implementation plan.
- **Date:** 2026-06-27
- **Issue:** [#550](https://github.com/DocGerd/hangarfit/issues/550) — *Fuselage outline polygon (approach b) — single-outline replacing front/aft boxes*
- **Refs:** spike #541 (`docs/spikes/polygon-part-geometry-feasibility.md`, PR #543), ADR-0012 (fuselage front/aft split), ADR-0023 (empennage surfaces), ADR-0001 (parts model), ADR-0002 (det(−1) transform), ADR-0003 (determinism contract).

---

## Goal & scope

Make a `kind: fuselage` part able to carry a tapered **outline polygon** that the loader **clips** into
`fuselage_front` / `fuselage_aft` sub-polygons at the wing trailing edge — the one genuinely-new
algorithm called out by #550. This is the deferred (b) piece of spike #541; ~85 % of the polygon-part
pipeline already shipped with that spike's (c)+(d-param) phase.

**This is a capability-only build.** No catalog aircraft adopts a fuselage outline, so the **entire real
fleet stays byte-identical**. A dedicated test-fixture aircraft exercises the new clip. A parametrized
fuselage-outline authoring DSL and any real fleet data are explicitly future work.

### Already shipped (do NOT rebuild)

| Component | Where | State |
|---|---|---|
| `Part.local_vertices` field + `_canonicalize_ring` (CCW, lexicographic-min rotation, degeneracy/self-intersection rejection) + `__post_init__` bbox-containment | `src/hangarfit/models.py` | ✓ shipped |
| Per-vertex det(−1) transform of polygon parts (`part_local_ring` + `aircraft_parts_world`) | `src/hangarfit/geometry.py` | ✓ shipped |
| scene/v2 `vertices` emission + viewer `ExtrudeGeometry` rendering | `src/hangarfit/scene.py`, `viewer/src/planes.ts`, `scene-contract.ts` | ✓ shipped |
| Polygon-part tests (canonicalization, transform equivalence, scene emission) | `tests/test_part_polygon.py`, `tests/test_geometry.py`, `tests/test_scene.py` | ✓ shipped |
| Wing taper authoring (`planform:` → 6-vertex hexagon) | `loader._build_planform` | ✓ shipped (wing-only) |

The only catalog polygon part today is `scheibe_falke`'s **wing** via `planform:`.

---

## Approach for the clip (the one real design choice)

**Chosen: Shapely `intersection` of the outline with each half-plane** (`x ≥ x_break` and `x ≤ x_break`).
Shapely is a core dependency; intersecting a simple polygon with a convex half-plane is robust and yields
exactly one `Polygon` per side for an x-monotone (tapered-tube) fuselage. Requiring "exactly one non-empty,
non-degenerate `Polygon` per side" *is* the formal "the front sub-outline is genuinely the cockpit"
guarantee #550 asks for.

Rejected alternatives:
- **`shapely.ops.split` with a vertical line** — returns a `GeometryCollection` needing side-assignment by
  centroid-x and multi-piece handling; no advantage over half-plane intersection.
- **Hand-rolled Sutherland–Hodgman half-plane clip** — reinvents Shapely and adds determinism surface for
  no benefit.

---

## Components & changes

### 1. `loader._split_fuselage` — dual path (the core change)

`src/hangarfit/loader.py`. Today the function **rejects** a polygon fuselage
(`if fuselage.local_vertices is not None: raise`) and box-interval-splits a scalar fuselage.

New behaviour:

- **Scalar fuselage (`local_vertices is None`):** the existing box-interval split is **unchanged**, line
  for line. This is what guarantees byte-identical output for every current aircraft.
- **Polygon fuselage (`local_vertices is not None`):** remove the rejection; clip instead.

The clip, given the fuselage `Part` (part-own `local_vertices` `V`, centre `(c, oy)`, `length L`,
`width W`, `angle_deg θ`) and the wing-derived break station `x_break = _wing_trailing_edge_x(wing)`
(plane-local):

1. **Axis-aligned guard.** Require `θ == 0` for a polygon fuselage (raise `LoaderError` otherwise). The
   clip works in part-local x, which equals plane-local x only when the part is unrotated; fuselages are
   angle-0 by construction, so rotation support is YAGNI.
2. Map the break to part-local x: `xs = x_break − c`.
3. Build the part-local outline `Polygon(V)` (a closed ring from the canonical open `local_vertices`).
4. **Break-inside guard.** Require `min_x(V) < xs < max_x(V)` strictly (mirrors today's
   `tail_x < x_break < nose_x`). Else `LoaderError` (degenerate split).
5. `front = outline ∩ {x ≥ xs}`, `aft = outline ∩ {x ≤ xs}` via intersection with a bbox-covering
   half-plane rectangle.
6. **Single-piece guard.** Each side must be exactly one non-empty, non-degenerate `Polygon` (no
   `MultiPolygon`, no empty, area above the canonicalizer's degeneracy floor). Else `LoaderError`
   ("the fuselage outline does not clip into a single front/aft piece at the wing trailing edge — the
   outline must be a simple, x-monotone polygon across the break").
7. For each side polygon `S` (in the source fuselage's own centred frame): compute its bbox → new
   `length_m` (x-extent), `width_m` (y-extent), `offset_x_m` (plane-local = `c + bbox_x_mid(S)`),
   `offset_y_m` (`oy + bbox_y_mid(S)`); re-express `S`'s exterior ring relative to its own sub-centre
   (subtract the sub-bbox centre) → new `local_vertices`. Construct
   `Part(kind="fuselage_front"|"fuselage_aft", …)`; `__post_init__`
   re-canonicalizes the ring and re-validates bbox containment. Inherit `z_bottom_m`, `z_top_m`,
   `angle_deg` (= 0) from the source.

**Area-conservation** holds by construction: the half-plane cut introduces matching vertices at `x = xs`
on both sides, so `front ∪ aft = outline` exactly and `front.area + aft.area == outline.area` (up to FP).

### 2. `loader._build_part` + `_ALLOWED_PART_KEYS` — accept raw `vertices:`

`src/hangarfit/loader.py`. Add `"vertices"` to `_ALLOWED_PART_KEYS`. A `vertices:` value is a list of
`[x, y]` pairs in the part's **own (centred) frame** — the same frame `planform:` emits and
`local_vertices` stores (each vertex within `±L/2 × ±W/2`) — set directly as `local_vertices`
(canonicalized + bbox-validated by `Part.__post_init__`).

Validation:
- `vertices:` and `planform:` are **mutually exclusive** (`LoaderError` if both present).
- For this issue, `vertices:` is valid only on `kind: fuselage` (parallel to the existing
  `planform:`-is-wing-only rule). Broadening to other kinds is a future decision.

The `kind: fuselage` placeholder Part (built in `_build_aircraft`) thus carries the whole authored
outline, which `_split_fuselage` then clips — preserving ADR-0012 D2's "author one fuselage, the loader
expands it into canonical Parts" model.

### 3. ADR-0012 amendment

`docs/adr/0012-fuselage-front-aft-split.md`. Add a dated **Amendment (#550)** to D2: when the source
fuselage carries an outline polygon (`vertices:`), the front/aft split is a Shapely **clip** at the same
wing-trailing-edge `x_break`, producing area-conserving front/aft **sub-polygons**; the scalar
box-interval path is unchanged. D1 (the `wing × fuselage_front` hard-conflict rule) and the PartKind
taxonomy are untouched. Matches the in-place dated-amendment convention used by ADR-0003.

### 4. Test-fixture aircraft

A small catalog-shaped fixture YAML under `tests/fixtures/` with a `kind: fuselage` + tapered `vertices:`
outline, plus a `wing` so the break derives. Used by the loader/collision tests.

---

## Data flow

```
YAML (kind: fuselage + vertices:)
  → _build_part            (canonical polygon Part, bbox-validated)
  → _build_aircraft        (placeholder fuselage held aside)
  → _split_fuselage CLIP   (half-plane intersection at wing TE → 2 polygon sub-Parts)
  → aircraft_parts_world   (UNCHANGED: per-vertex det(−1) local_to_world)
  → collisions.check       (UNCHANGED: wing×fuselage_front hard-conflict; wing×fuselage_aft z-gap)
  → scene.build_scene      (UNCHANGED: emits `vertices` for fuselage_front/aft)
  → viewer                 (UNCHANGED: ExtrudeGeometry)
```

Only the first three boxes change; everything downstream is already polygon-generic.

---

## Error handling (all deterministic `LoaderError`)

| Condition | Behaviour |
|---|---|
| `x_break` not strictly inside the outline x-span | existing degenerate-split error (retained, wording generalized) |
| Either clipped side empty / `MultiPolygon` / sub-degenerate | "outline does not clip into a single front/aft piece … must be simple & x-monotone across the break" |
| `angle_deg ≠ 0` on a polygon fuselage | "a polygon fuselage must be axis-aligned (angle_deg = 0)" |
| `vertices:` and `planform:` both present | mutual-exclusion error |
| `vertices:` on a kind other than `fuselage` | kind-scope error (parallel to planform-is-wing-only) |

No silent fallbacks: an outline that cannot be cleanly split is a load error, never a silent revert to a
box.

---

## Determinism & back-compat

- The clip is **interpolation-only** (linear half-plane intersection — no `sin/cos/atan2`), so it is far
  more cross-machine-robust than the trig in the transform; results are re-canonicalized by
  `Part.__post_init__`. Same YAML → byte-identical sub-polygons (ADR-0003).
- The scalar path is literally unchanged → **every current catalog/example aircraft is byte-identical**.
  A guard test asserts no shipped fuselage carries `vertices:` (so the fleet cannot silently change).

---

## Explicitly out of scope

- No catalog fuselage outline data (real fleet unchanged).
- No parametrized fuselage-outline DSL (future data-authoring issue).
- Empennage (`tail` / `vertical_stabilizer`, ADR-0023) stays as separate rectangles — the fuselage
  outline is the cockpit + cabin tube only.
- No change to the collision predicate, the det(−1) transform, or the scene/v2 schema.
- No rotation support for polygon fuselages (`angle_deg` must be 0).
- No change to the solver trivial-infeasibility area-gate (`_check_sum_areas`): it already sums polygon
  footprints (polygon area ≤ bbox area → still a sound lower bound, #425/#541); it only ever gets
  *tighter* for an outline plane, never unsound.

---

## Testing

- **Clip unit** (`_split_fuselage` polygon path): a tapered fuselage fixture → assert two sub-polygons;
  `front.area + aft.area == outline.area`; the two share the cut edge at `x_break` (abut, no gap/overlap);
  `offset_x_m`/`length_m`/`width_m` match the sub-bbox; `kind` correct.
- **Determinism:** double-load the fixture → byte-identical `local_vertices` on both sub-Parts.
- **Byte-identical guard:** scalar-fuselage fleet aircraft produce identical `fuselage_front`/`fuselage_aft`
  Parts as before (regression), and an assert that no shipped fuselage uses `vertices:`.
- **Error paths:** break outside span; non-x-monotone outline that clips to `MultiPolygon`; `angle_deg ≠ 0`;
  `vertices:`+`planform:`; `vertices:` on a wing.
- **Transform:** a heading ≠ 0 placement of the outline plane through `aircraft_parts_world` (per-vertex
  det(−1) correctness for the clipped sub-polygons).
- **Collision semantics (value preservation):** a wing overlapping the **polygon cockpit** conflicts
  (`fuselage_front_wing_overlap`) while a wing overlapping the **polygon aft tail** at a z-gap nests —
  the exact ADR-0012 D1 behaviour, now exercised on polygon front/aft.
- **Scene/viewer:** a fuselage-outline plane emits `vertices` for `fuselage_front`/`fuselage_aft` in
  `build_scene`.

### Review guards expected to apply

`geometry-invariant-guard` (collision/transform interaction), `silent-failure-hunter` (loader changes),
`comment-analyzer` (ADR amendment + docstrings), and a loader-determinism check on the clip. The standard
`pr-review-toolkit:code-reviewer` pass runs regardless.

---

## CHANGELOG

A user-facing `[Unreleased]` entry under `### Added`: authors can now give a `kind: fuselage` part a
`vertices:` outline polygon, which the loader clips into area-conserving front/aft sub-polygons at the
wing trailing edge (capability; no fleet behaviour change).
