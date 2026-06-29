# ML observation tensor schema (`SCHEMA_VERSION = 1`)

The contract produced by `ml/encoding.py:encode()` (learned-backend sub-project #2,
epic #607). Mirrors `docs/architecture/scene-v2-schema.md`. Pure/deterministic; numpy
only. `ObservationTensors` fields:

## `raster` — `(7, grid_h, grid_w)` float32 (default 7×192×96)
World-frame, fixed `cell_m=0.25` m/cell, hangar front-left = world origin; row 0 sits
`apron_band_m=10` m below the front (apron `y<0` occupies the top rows).

| ch | name | meaning |
|---|---|---|
| 0 | `oob_mask` | cell centre outside `hangar.floor_polygon` (incl. L-notch + apron + beyond-length margin) |
| 1 | `bay_mask` | maintenance-bay rectangle |
| 2 | `apron_mask` | staging band `y ∈ [-apron_depth_m, 0)` |
| 3 | `door_gap` | a ~2-cell band straddling the door opening on the front wall (one cell either side of the wall line) |
| 4 | `parked_occ_low` | parked part polygons with `z_bottom < z_split_m` |
| 5 | `parked_occ_wing` | parked part polygons with `z_top > z_split_m` |
| 6 | `active_occ` | active object footprint at its current pose |

## `tokens` — `(max_objects, 24)` float32 (default 16×24); `token_mask` — `(max_objects,)` bool
Row order: parked (placement order) → active → unplaced (queue order), padded. Column layout:

| cols | feature |
|---|---|
| 0–2 | status one-hot `[parked, active, unplaced]` |
| 3–5 | type one-hot `[aircraft, fixed_obstacle, placed_routed_mover]` |
| 6–7 | body-frame dims `[length, width]` ÷ `pos_ref_m` |
| 8–10 | wing one-hot `[high, mid, low]` (aircraft only; all-zero for ground objects) |
| 11–13 | movement one-hot `[always_cart, cart_eligible, always_own_gear]` (aircraft only) |
| 14–15 | `[on_carts, tow_pivotable]` (`on_carts` from `Placement`/`ActiveObject`) |
| 16 | `effective_turn_radius_m()` ÷ `pos_ref_m` |
| 17 | `hard_door_mover` (ground objects) |
| 18–21 | pose `[x, y, sinθ, cosθ]` (parked & active; zeros for unplaced) |
| 22–23 | reserved (zero in v1): `region_side`, `seq_order` |

`active_index`: row of the active object, or `-1` at a terminal state.

Pose `[x, y]` normalize to `[-1, 1]` over the raster **window** (`grid_w * cell_m` ×
`grid_h * cell_m` metres), not the hangar — so a hangar smaller than the window occupies
only part of the range (e.g. a 15 m-wide hangar in a 24 m window reaches ~+0.25 in x, not
+1). Use the `meta` `cell_m`/`grid_*`/`origin_*` keys to un-normalize.

## `legal_action_mask` — `(9,)` bool
Canonical order `[(L,+), (S,+), (R,+), (L,−), (S,−), (R,−), (T,+), (T,−), PARK]`. Cart
reverse-arc slots idx3/idx5 are False (`_primitives` omits `Lr`/`Rr` at r=0). Entirely
False (incl. PARK) at a terminal state.

## `meta` — read-only mapping of floats
Keys: `cell_m`, `origin_x_m`, `origin_y_m`, `grid_h`, `grid_w`, `steps_this_object`,
`steps_total` (integers stored as floats). For debugging / un-normalization. Returned as
an immutable `MappingProxyType`; writing to any key raises `TypeError`.

## Schema 2 — ego-centric augment (`--relative-encoder`, opt-in; #827)
With `EncoderConfig.ego_centric` (CLI `--relative-encoder`), each token is **augmented** to
width **28** and `schema_version` is stamped **2**. Cols 0–23 are written identically to schema 1
(the absolute pose stays in 18–21); four columns are appended:

| cols | feature |
|---|---|
| 24–25 | active-frame position `[fwd, right]` of this object relative to the active object, ÷ `pos_ref_m` |
| 26–27 | active-frame relative heading `[sinΔθ, cosΔθ]` |

The frame is the active object's SE(2) body frame — basis `forward=(sinθ_a, cosθ_a)`,
`right=(cosθ_a, −sinθ_a)`, the compass-convention (det −1) frame that matches the kinematic
integrator. The active object's own ego cols are `(0, 0, 0, 1)`; unplaced objects' ego cols are
zero. The encoding is invariant under proper rigid scene motions (SE(2)). Default off =
byte-identical to schema 1. See [ADR-0028](../adr/0028-learned-backend-train-to-mastery-resolved-negative.md)
(re-open trigger #2) and `docs/superpowers/specs/2026-06-24-relative-encoder-ego-centric-design.md`.

## Versioning
Adding/reordering channels or token columns, or wiring the reserved slots, bumps
`SCHEMA_VERSION`. Validity-only across machines; bit-identical within a build. The opt-in
ego-centric augment stamps `SCHEMA_VERSION_EGO = 2` (default off stays `1`, byte-identical).
