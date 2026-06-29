# `hangarfit.scene/v2` — 3D viewer scene schema

> **v2 (#549)** is an additive bump from v1: each plane box gains two
> always-present keys — `z_band` (explicit `[z_bottom_m, z_top_m]`) and
> `vertices` (a plane-local polygon footprint, or `null` for a scalar rectangle).
> A scalar box (`vertices: null`) renders byte-identically to v1; a polygon part
> renders as an extruded prism. The transform contract is unchanged. See the
> `planes[]` section and [ADR-0017](../adr/0017-3d-viewer-architecture.md)'s v2
> amendment.
>
> **v2 also gains `ground_objects` + `go_anchors` (#606)** — placed fixed
> obstacles and movers, additive and inert-when-empty (`[]`/`{}`), so an
> aircraft-only scene is byte-identical apart from those two empty collections.

The JSON contract between the Python core and the 3D viewer (see
[ADR-0017](../adr/0017-3d-viewer-architecture.md)). Produced by
`hangarfit.scene.build_scene(layout, *, moves_plan=None, check_result=None, …)`
and consumed by `hangarfit.viewer` / `_viewer_assets/viewer.js`. It is the *only*
thing the viewer reads — the viewer knows nothing about hangarfit internals, and
the core knows nothing about Three.js.

The whole point of the schema is that **all geometry/transform math is precomputed
in Python**. In particular the plane-local→world transform — a rotation composed
with a reflection, determinant −1 (ADR-0002) — is emitted as ready-to-apply affine
matrices, so the viewer never re-derives it.

## Coordinate convention

World: `x` runs right along the door wall, `y` runs deeper into the hangar, `z` is
up. `heading_deg` is compass-style (measured from world `+y`, clockwise-positive).
Plane-local: `+u` forward (toward the nose), `+v` right, `+w` up. All lengths in
metres.

## Top-level object

| Key | Type | Meaning |
|---|---|---|
| `schema` | string | Always `"hangarfit.scene/v2"`. |
| `units` | string | Always `"m"`. |
| `coordinate_note` | string | Human reminder of the convention above. |
| `hangar` | object | Hangar shell — see below. |
| `planes` | array | One entry per placed plane (sorted by id) — static box geometry. |
| `ground_objects` | array | One entry per placed ground object (sorted by id) — fixed obstacles + placed movers (#606). `[]` when none. See below. |
| `timeline` | object | The whole-fill tow animation — see below. |
| `final_poses` | object | `plane_id → affine`: each plane at its parked slot. |
| `conflicts` | array of string | Plane ids to tint red (flattened from a `CheckResult`); `[]` if none / not checked. |
| `anchors` | object | `plane_id → [box → [corner → [x, y]]]`: oracle world corners at the final placement, for the viewer's load-time self-check. A scalar box has 4 corners; a polygon box has N (one per `vertices` entry). |
| `gear_anchors` | object | `plane_id → [wheel → [x, y]]`: oracle world wheel positions at the final placement — each canonical plane-local wheel pushed through `geometry.local_to_world` (the same determinant-−1 map `anchors` applies via `aircraft_parts_world`), so the viewer self-check also covers the gear render and a sign-flip regression fails both at once. |
| `go_anchors` | object | `ground_object_id → [box → [corner → [x, y]]]`: oracle world corners for each placed ground object (#606), the ground-object sibling of `anchors` for the same load-time self-check. `{}` when none. |
| `egress_lanes` | object | `mover_id → [[x, y], …]`: sampled world points of each hard-door mover's drive-out corridor (#652), drawn as the "keep clear" egress-lane decal (2D + 3D). Always present; `{}` when there is no hard-door egress lane (no hard-door mover, or its egress is blocked / undrawable). Draw-only geometry — NOT anchored / not part of the det-−1 self-check. |
| `placeholder` | bool | `true` iff any placed aircraft is on unmeasured (`measured: false`) data — drives the "PLACEHOLDER DATA" honesty banner on the 2D PNG and the 3D viewer (#401, #79). |
| `readouts` | object \| null | Actionable quality numbers for a **valid** layout: `{ "min_gap_m", "min_wing_over_tail_clearance_m" }` (either may be `null` — single plane / no overhang). `null` when the layout is invalid — validity is taken from the supplied `CheckResult`, or collision-checked by `build_scene` itself when none was supplied, so readouts never imply an unverified validity. |

## The affine

A plane-local→world 2D affine, serialized as a flat 6-list `[a, b, tx, c, d, ty]`:

```
world_x = a·u + b·v + tx        with  a = sin(h)   b =  cos(h)   tx = x_m
world_y = c·u + d·v + ty              c = cos(h)   d = −sin(h)   ty = y_m
```

where `h = radians(heading_deg)` and `(u, v)` is plane-local `(forward, right)`.
This is exactly `geometry.local_to_world`; its linear part has determinant −1. The
viewer builds a `THREE.Matrix4` `[[a,b,0,tx],[c,d,0,ty],[0,0,1,0],[0,0,0,1]]` (the
`z`-row is identity — box height passes through unchanged) and assigns it to the
plane's group.

## `hangar`

```jsonc
{
  "length_m": 25.0,
  "width_m": 18.0,
  "door": { "center_x_m": 9.0, "width_m": 12.0 },
  "maintenance_bay": {
    "center_x_m": 13.5, "width_m": 9.0, "depth_m": 9.0,
    "closed": true,            // true iff layout.maintenance_plane is set
    "plane_id": "fk9_mkii"     // the absent occupant, or null
  },
  "structural_notches": [      // always emitted; empty for a rectangular hangar
    { "x_min_m": 12.72, "y_min_m": 22.66, "x_max_m": 15.08, "y_max_m": 31.76 }
  ]
}
```

The bay is back-anchored: it spans `y ∈ [length_m − depth_m, length_m]`. The viewer
draws it as a translucent red box only when `closed`.

`structural_notches` is the list of always-on rectangular floor keep-outs (ADR-0018)
— corners/edges of the bounding rectangle that are **not** hangar floor (e.g. the
Herrenteich office annex). Each is the axis-aligned rectangle
`[x_min_m, x_max_m] × [y_min_m, y_max_m]`. The viewer cuts each from the floor
(rendering the L-shaped footprint) and raises the notch's interior-facing walls.
The list is always present and is empty for the common rectangular hangar.

## `planes[]`

```jsonc
{
  "id": "aviat_husky",
  "color": "#0079B5",          // reuse of visualize.PLANES, keyed by sorted id (2D/3D parity)
  "boxes": [
    {
      "kind": "fuselage_front",          // PartKind
      "cx": 1.2, "cy": 0.0, "cz": 0.75,  // plane-local centre (forward, right, mid-height)
      "length_m": 3.0,                   // extent along +u (forward) — always the bbox
      "width_m": 0.7,                    // extent along +v (right) — always the bbox
      "height_m": 1.5,                   // extent along +w (up) = z_top − z_bottom
      "angle_deg": 0.0,                  // CCW rotation within plane-local (oriented_rect)
      "z_band": [0.0, 1.5],              // v2: explicit [z_bottom_m, z_top_m]
      "vertices": null                   // v2: null ⇒ scalar rectangle (box render path)
    },
    {
      "kind": "wing",
      "cx": 1.5, "cy": 0.0, "cz": 2.0,
      "length_m": 1.2, "width_m": 18.0, "height_m": 0.2, "angle_deg": 0.0,
      "z_band": [1.9, 2.1],
      // v2: a polygon part's plane-local (u, v) footprint, angle+offset already
      // folded in (so the affine applies directly, no transform math). Canonical:
      // CCW, open (no closing-dup), lex-min-start (ADR-0024). Here a 6-vertex
      // tapered-wing hexagon (root chord 1.2 at cx=1.5 → ±0.6 → [0.9, 2.1]).
      "vertices": [[0.9,0.0],[1.36,-9.0],[1.64,-9.0],[2.1,0.0],[1.64,9.0],[1.36,9.0]]
    }
    // … one box per Part
  ],
  "wheels": [[0.0, 1.0], [0.0, -1.0], [-3.0, 0.0]],  // plane-local (u, v) per wheel (ADR-0013)
  "on_carts": false                                  // true ⇒ plane rides a dolly
}
```

Boxes are static plane-local geometry, built once. Each is the 3D box of one
`Part` (`offset_x/y` → `cx/cy`, `z_bottom/z_top` → `cz`/`height_m`). The viewer
renders `wing` boxes translucent so vertical stacking is visible.

**v2 box keys (#549).** Every box always carries `z_band` (`[z_bottom_m,
z_top_m]`) and `vertices`. `vertices` is `null` for a scalar part — the viewer
builds a `BoxGeometry(length_m, width_m, height_m)` exactly as in v1, so scalar
fleets render byte-identically. For a **polygon part** `vertices` is the
plane-local `(u, v)` ring (CCW, open) from `geometry.part_local_ring` — the same
ring the `anchors` oracle routes through the affine — and the viewer extrudes it
into a prism (`ExtrudeGeometry`, base lifted to `z_band[0]`). `length_m`/`width_m`
stay the **bounding box** for both kinds (the scalar consumers and the area gate
read them), so a polygon footprint is always a subset of its box (ADR-0024). The
ring carries the part's `angle_deg`/offset already, so the viewer applies **only**
the per-plane affine — never any rotation of its own (ADR-0002).

`wheels` is the aircraft's canonical plane-local wheel positions
([ADR-0013](../adr/0013-wheels-canonical-data.md)) — one `(u, v)` per wheel (1 for
a monowheel, 3 for tricycle/tailwheel). `on_carts` is the per-placement dolly flag.
The viewer draws a wheel at each point (+ a short leg up to the belly where the
belly clears the wheel) inside the same affine Group as the boxes — so the gear
inherits the plane-local→world transform and animates along the tow path — plus a
pallet deck under each wheel when `on_carts`. Render *sizes* (wheel radius, pallet extent) are viewer-layer
constants mirroring `visualize.py`, never data: the schema carries only positions.

## `ground_objects[]`

```jsonc
{
  "id": "vw_caddy",
  "object_class": "placed_routed_mover", // | "fixed_obstacle"
  "color": "#8A8F98",                    // brand fill by class (mover slate /
                                         //   obstacle graphite), resolved in scene.py
  "hard_door_mover": true,               // the Caddy egress flag (highlight cue)
  "boxes": [ /* same box shape as planes[] — a kind:"ground" scalar box */ ],
  "final_pose": [s, c, x, c, -s, y]      // plane-local → world affine at the placed pose
}
```

The non-aircraft floor bodies (#606, [ADR-0025](../adr/0025-ground-object-taxonomy.md)):
a **fixed obstacle** (a placed keep-out, e.g. the Maul fuel trailer) and the
**placed/routed movers** (the VW Caddy + glider trailers). Each is the 3D analogue
of the 2D PNG render (`visualize.py`, #649): the obstacle reads as a keep-out, the movers
as placed bodies, visually distinct from aircraft. Box geometry is the *same*
`boxes` shape as `planes[]` (a `kind:"ground"` part is a scalar box), so the viewer
renders both through one shared box path. A ground object has no `wheels`/`on_carts`
and no per-id colour — `color` is brand-resolved per **class** in Python (the plane
colour-map idiom, #419), so the viewer reads it and hard-codes nothing.

`final_pose` is the placement (resting) affine. A **fixed obstacle** is static (no
`timeline` segment); a **placed-routed mover** animates along its drive path via a
`timeline` segment (#651) and rests here. The Caddy hard-door egress lane is the
drive-out corridor that mover must keep clear, surfaced from the egress oracle
(`towplanner.egress_first_conflict` / `egress_corridors`) into the `egress_lanes`
top-level key (#652). The `ground_objects` list is always present and empty when a
layout has no ground objects (the `structural_notches` inert-when-empty discipline).
Each body's world corners are oracled in `go_anchors` for the same load-time det-−1
self-check as `anchors`; the egress lane is draw-only and not anchored.

## `timeline`

```jsonc
{
  "total_s": 24.0,
  "segments": [                // one per LEG; back_first_order — or global execution order for a move-aside body
    {
      "plane_id": "fk9_mkii",
      "start_s": 0.0,
      "end_s": 3.2,            // segments are sequential: start_s[k] == end_s[k-1]
      "samples": [             // affines along the tow path, door → slot
        [0.0, 1.0, 9.0, 1.0, 0.0, 0.0],
        // …
      ]
      // "leg_index": 0        // OPTIONAL (#865) — present ONLY for a multi-leg body
    }
  ]
}
```

Built from each `MovesPlan` move's `DubinsArc.sample()` over `back_first_order` (a
move-aside body's legs are instead laid in **global execution order** — see *Multi-leg
bodies* below). Per-plane duration is proportional to path length (`DubinsArc.length_m`) via a tow
speed, clamped to `[min_seg_s, max_seg_s]`. Sample count per path is capped (the
sampling step is coarsened) to keep the HTML small.

### Multi-leg bodies (`leg_index`, #865 Rung D)

A body normally has **one** segment (one leg, door → slot). A *move-aside* body
([#667](https://github.com/DocGerd/hangarfit/issues/667) Rung E) instead drives to a
transient **staging pose** (leg 0), waits while another body routes past, then drives
to its final slot (leg 1) — so `segments` may carry **more than one entry per
`plane_id`**, laid end-to-end in leg order. Each such segment then carries an
**optional** `leg_index: int` (`0`-based, execution order).

`leg_index` is emitted **only for a multi-leg body** — a single-leg body (every body in a plan that needs no
move-aside; no default-shipped layout triggers one) omits the key entirely, so an existing scene is **byte-identical** to the
pre-Rung-D form. The `SCHEMA` stays `hangarfit.scene/v2` (additive only). A consumer
that ignores `leg_index` still animates correctly (segments are already sequential);
the field is an explicit, robust ordering label. The body's **final** pose is the
*last* leg's end; a staging pose is **not** in `final_poses` / `placements`.

> **Producer status (Rung E, #667 / shipped via #869).** Move-aside is the first
> multi-leg producer: `towplanner.plan_fill`'s phase-2 move-aside emits a displaced
> body's staging + return legs, and `scene._timeline` lays a shuffle's legs in
> **global execution order**, so the "waiting at staging" gap row is reachable. It
> is a **byte-identical capability seam** (ADR-0003) — phase-2 move-aside engages
> only on an in-budget phase-1 deadlock with `apron_depth_m > 0` and a positive
> displacement cap, so the default (no-apron) path still emits single-leg bodies
> exactly as before. The dense Herrenteich all-8 is budget-bound (it bails at the
> global expansion cap before phase 2 engages), so no *default-shipped* layout
> exercises the multi-leg path today; the fk9_mkii↔cessna_140 pair stays a
> documented manual-insertion case.

**Viewer state machine** — for a plane with leg list `S` (sorted by `leg_index`) at
time `t`:

| Condition | State | Affine |
|---|---|---|
| `t < S[0].start_s` | hidden (still outside) | — |
| `S[k].start_s ≤ t < S[k].end_s` | animating leg `k` | `S[k].samples[round(frac·(n−1))]` |
| `S[k].end_s ≤ t < S[k+1].start_s` | waiting at staging | `S[k].samples[−1]` |
| `t ≥ S[−1].end_s` | parked | `final_poses[plane_id]` |

For a single-leg body these collapse to the original hidden → animating → parked
transitions. A plane with **no** segment (static scene) is always shown at
`final_poses`.

### Static / un-routable layouts

When no `MovesPlan` is supplied (or the layout is not tow-routable), `segments`
is `[]`, `total_s` is `0`, and `final_poses` still carries every plane at its
slot. The viewer renders the static scene and disables the transport controls.

## Determinism

`build_scene` is pure and deterministic: planes sorted by id, segments in entry
order, all values from RNG-free closed-form paths. The same
`(layout, moves_plan, check_result)` yields a byte-identical dict (the spirit of
[ADR-0003](../adr/0003-rr-mc-solver-algorithm.md); pinned by
`tests/test_scene.py::test_build_scene_is_byte_deterministic` and, for a polygon
part, `::test_build_scene_v2_byte_deterministic_with_polygon`). A polygon box's
`vertices` come straight from the load-time-canonicalized `Part.local_vertices`
(CCW, lex-min start — ADR-0024), so two equivalent author orderings produce a
byte-identical scene.

## The multi-solution compare container (NOT part of scene/v2)

`hangarfit view --solve --alternatives N` (#666) carries several solver
alternatives in **one** offline HTML so a human can switch between them. This is a
**viewer-HTML-level wrapper, deliberately layered _over_ N independent scene/v2
documents — it is not part of this schema.** It rides in a separate
`<script type="application/json" id="solutions">` blob (the single-scene viewer keeps
its `id="scene"` blob), with shape:

```jsonc
{
  "schema": "hangarfit.viewer-compare/v1",
  "count_requested": 3,        // the --alternatives N the user asked for
  "count_found": 2,            // diverse solutions actually found ("Found n of N")
  "solutions": [
    { "label": "#1",
      "scene": { /* a full, standalone hangarfit.scene/v2 doc */ },
      "summary": { "min_gap_m": 1.23, "planes_moved_vs_first": 0,
                   "mean_shift_m": 0.0, "routable": true } },
    // …
  ]
}
```

Keeping the container out of scene/v2 is deliberate: `scene.build_scene` (and its
byte-determinism + the `scene-contract.ts` key-parity guard) stay untouched, and each
carried `scene` is **byte-identical** to a standalone single-solution render of that
layout (ADR-0003) — the container is purely additive. The `summary` numbers are the
same compare metrics `solve` narrates (`cli._placement_delta`, the diagnostics
`min_pairwise_gap_m`). The viewer reads this blob into the `CompareManifest` interface
(`viewer/src/scene-contract.ts`), which — being viewer-only — is **not** checked against
any Python key set.
