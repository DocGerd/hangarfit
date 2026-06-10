# `hangarfit.scene/v2` — 3D viewer scene schema

> **v2 (#549)** is an additive bump from v1: each plane box gains two
> always-present keys — `z_band` (explicit `[z_bottom_m, z_top_m]`) and
> `vertices` (a plane-local polygon footprint, or `null` for a scalar rectangle).
> A scalar box (`vertices: null`) renders byte-identically to v1; a polygon part
> renders as an extruded prism. The transform contract is unchanged. See the
> `planes[]` section and [ADR-0017](../adr/0017-3d-viewer-architecture.md)'s v2
> amendment.

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
| `timeline` | object | The whole-fill tow animation — see below. |
| `final_poses` | object | `plane_id → affine`: each plane at its parked slot. |
| `conflicts` | array of string | Plane ids to tint red (flattened from a `CheckResult`); `[]` if none / not checked. |
| `anchors` | object | `plane_id → [box → [corner → [x, y]]]`: oracle world corners at the final placement, for the viewer's load-time self-check. A scalar box has 4 corners; a polygon box has N (one per `vertices` entry). |
| `gear_anchors` | object | `plane_id → [wheel → [x, y]]`: oracle world wheel positions at the final placement — each canonical plane-local wheel pushed through `geometry.local_to_world` (the same determinant-−1 map `anchors` applies via `aircraft_parts_world`), so the viewer self-check also covers the gear render and a sign-flip regression fails both at once. |
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
      // folded in (so the affine applies directly, no transform math). CCW,
      // open (no closing-dup). Here a 6-vertex tapered-wing hexagon.
      "vertices": [[2.1,0.0],[2.1,9.0],[1.96,9.0],[0.9,0.0],[1.96,-9.0],[2.1,-9.0]]
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

## `timeline`

```jsonc
{
  "total_s": 24.0,
  "segments": [                // one per plane, in back_first_order (deepest first)
    {
      "plane_id": "fk9_mkii",
      "start_s": 0.0,
      "end_s": 3.2,            // segments are sequential: start_s[k] == end_s[k-1]
      "samples": [             // affines along the tow path, door → slot
        [0.0, 1.0, 9.0, 1.0, 0.0, 0.0],
        // …
      ]
    }
  ]
}
```

Built from each `MovesPlan` move's `DubinsArc.sample()` over `back_first_order`.
Per-plane duration is proportional to path length (`DubinsArc.length_m`) via a tow
speed, clamped to `[min_seg_s, max_seg_s]`. Sample count per path is capped (the
sampling step is coarsened) to keep the HTML small.

**Viewer state machine** — for a plane with segment `s` at time `t`:

| Condition | State | Affine |
|---|---|---|
| `t < s.start_s` | hidden (still outside) | — |
| `s.start_s ≤ t < s.end_s` | animating | `s.samples[round(frac·(n−1))]` |
| `t ≥ s.end_s` | parked | `final_poses[plane_id]` |

A plane with **no** segment (static scene) is always shown at `final_poses`.

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
