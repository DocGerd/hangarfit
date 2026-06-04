# `hangarfit.scene/v1` — 3D viewer scene schema

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
| `schema` | string | Always `"hangarfit.scene/v1"`. |
| `units` | string | Always `"m"`. |
| `coordinate_note` | string | Human reminder of the convention above. |
| `hangar` | object | Hangar shell — see below. |
| `planes` | array | One entry per placed plane (sorted by id) — static box geometry. |
| `timeline` | object | The whole-fill tow animation — see below. |
| `final_poses` | object | `plane_id → affine`: each plane at its parked slot. |
| `conflicts` | array of string | Plane ids to tint red (flattened from a `CheckResult`); `[]` if none / not checked. |
| `anchors` | object | `plane_id → [box → [corner → [x, y]]]`: oracle world corners at the final placement, for the viewer's load-time self-check. |
| `gear_anchors` | object | `plane_id → [wheel → [x, y]]`: oracle world wheel positions at the final placement (same `local_to_world` as `anchors`), so the viewer self-check also covers the gear render. |

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
  }
}
```

The bay is back-anchored: it spans `y ∈ [length_m − depth_m, length_m]`. The viewer
draws it as a translucent red box only when `closed`.

## `planes[]`

```jsonc
{
  "id": "aviat_husky",
  "color": "#0079B5",          // reuse of visualize.PLANES, keyed by sorted id (2D/3D parity)
  "boxes": [
    {
      "kind": "fuselage_front",          // PartKind
      "cx": 1.2, "cy": 0.0, "cz": 0.75,  // plane-local centre (forward, right, mid-height)
      "length_m": 3.0,                   // extent along +u (forward)
      "width_m": 0.7,                    // extent along +v (right)
      "height_m": 1.5,                   // extent along +w (up) = z_top − z_bottom
      "angle_deg": 0.0                   // CCW rotation within plane-local (oriented_rect)
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
`tests/test_scene.py::test_build_scene_is_byte_deterministic`).
