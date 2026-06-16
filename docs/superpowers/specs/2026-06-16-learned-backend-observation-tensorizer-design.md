# Learned backend — Observation tensorizer (sub-project #2)

**Status:** Draft (design under review)
**Date:** 2026-06-16
**Scope:** Sub-project #2 of the learned-backend epic (#607) — the **observation tensorizer ONLY**: turn the env's semantic `Observation` into fixed-shape numpy tensors a policy can consume. **No policy network, no heads, no `torch`.**
**Builds on:** the cold-joint RL env + reward, sub-project #1 ([`2026-06-12-learned-backend-cold-joint-rl-env-design.md`](2026-06-12-learned-backend-cold-joint-rl-env-design.md), implemented in `ml/` via #672).
**Governing decomposition:** the cold-joint spec §3, which splits **#2 = observation encoding** from a later **#3 = policy architecture + curriculum**. (Epic #607's body bundles them and still sketches a superseded "coarse-pose head + deterministic refiner" network; that network text is **not** in scope here and is superseded by the cold-joint primitive-by-primitive MDP.)

---

## 1. Context & why this sub-project exists

The env (sub-project #1) emits a **semantic** `Observation` (`ml/types.py`):

```python
Observation(active: ActiveObject | None,        # body + pose + on_carts of the object being driven
            parked: tuple[ParkedObject, ...],   # frozen obstacles, each (id, Placement)
            unplaced_ids: tuple[str, ...],       # the remaining queue (variable length)
            steps_this_object: int, steps_total: int)
```

A learned policy cannot consume that directly — it needs **fixed-shape numeric tensors**. This sub-project defines that translation as a **pure, deterministic, numpy-only function** with a **versioned contract**, so sub-project #3 (network) and a future gym adapter can depend on a stable interface while the network and training churn behind it.

The encoding is the **durable interface** of the learned backend; the network is the **experimental** part. Keeping them in separate sub-projects (per the cold-joint spec's own §3 decomposition) keeps the stable contract free of the churny bits and defers the `torch`/`[train]` dependency until training (#3) actually needs it.

## 2. Goals & non-goals

**Goals**
- A pure function `encode(observation, hangar, bodies, config) -> ObservationTensors` producing fixed-shape numpy arrays.
- A **multi-channel world-frame raster** (static hangar keep-outs + dynamic object occupancy, z-split) so a CNN can perceive **oblique z-nesting** spatially.
- A **variable-length object set → padded token table + mask** so a set-model can reason relationally over the fleet.
- A **legal-action mask** derived from each active object's movement mode, ready for policy action-masking.
- **Determinism**: identical `(Observation, hangar, config)` → byte-identical tensors (no RNG).
- A **versioned schema** (`SCHEMA_VERSION`) + a reference doc, so later additions are non-breaking bumps.
- **Scale-preserving, cross-hangar-ready** framing (fixed metres/cell), so CNN filters transfer across hangars/fleets.

**Non-goals (deferred)**
- The **policy network** (Set-Transformer, mask-CNN, heads) and `torch` → sub-project #3.
- **Action *magnitude*** parameterization (continuous vs binned) → #3 (this sub-project encodes only the discrete `(kind, gear)` action *legality*).
- **Training, curriculum schedule, reward weights** → #3/#4.
- Wiring `RegionPreference`/door-order into the env (the encoder *reserves* slots for them; populating them is an env change tracked separately).

## 3. Public surface (`ml/encoding.py`)

```python
SCHEMA_VERSION = 1

@dataclass(frozen=True, slots=True)
class EncoderConfig:
    cell_m: float = 0.25            # metres per raster cell (scale-preserving)
    grid_w: int = 96               # cap: 96 * 0.25 = 24 m wide
    grid_h: int = 192              # cap: 192 * 0.25 = 48 m tall (covers Herrenteich 32 m + 8 m apron + margin)
    max_objects: int = 16          # token padding cap (Herrenteich set is 12)
    z_split_m: float = 1.6         # low-band / wing-band boundary (see §5; tied to ADR-0023 / wing-layer clearance)
    pos_ref_m: float = 20.0        # normalization reference for body-frame dims

@dataclass(frozen=True, slots=True)
class ObservationTensors:
    raster: np.ndarray             # (C, grid_h, grid_w) float32
    tokens: np.ndarray             # (max_objects, F) float32
    token_mask: np.ndarray         # (max_objects,) bool — True = real object
    active_index: int              # row index of the active object in `tokens`, or -1 at a terminal state
    legal_action_mask: np.ndarray  # (9,) bool — canonical action legality (§6)
    meta: dict[str, float]         # un-normalization constants + step counters (debug / round-trip)
    schema_version: int            # == SCHEMA_VERSION

def encode(obs: Observation, hangar: Hangar,
           bodies: Mapping[str, Aircraft | GroundObject],
           config: EncoderConfig = EncoderConfig()) -> ObservationTensors: ...
```

`bodies = fleet ∪ ground_objects` is required because `Observation.parked` carries only `(id, Placement)`; the encoder looks each body up to render its polygons and read its features. This deliberately keeps sub-project #1's `types.py` untouched (no body smuggled into `ParkedObject`). A one-line `HangarFitEnv.encode()` convenience forwarder is **optional** and deferred to #3.

## 4. Raster framing — fixed metres/cell, zero-padded to a cap

- **Scale-preserving:** a fixed `cell_m` means an 18 m wing occupies the same pixel count in every hangar/fleet → convolutional filters transfer across hangars (the cross-hangar generalization goal of the epic).
- **Fixed world anchor:** the hangar front-left corner is pinned to a fixed cell so the same world point maps to the same cell across episodes. Row axis = world `y` (apron `y<0` occupies the top rows), column axis = world `x`.
- **Zero-padded cap:** hangars smaller than `(grid_h, grid_w)` zero-pad; the default cap comfortably covers Herrenteich (15.08 × 31.76 m) + an 8 m apron + margin. A scene exceeding the cap is clipped at the window edge (documented; the cap is a config knob, not a hard limit).
- **Rasterization:** no rasterizer exists in the repo (visualize.py is matplotlib-only). We rasterize from scratch with **`shapely.contains_xy`** (vectorized point-in-polygon at cell centres) — deterministic, numpy-native, matplotlib-free. Cell value is binary occupancy (1.0/0.0) at the cell centre; sub-pixel area-fraction is an optional future refinement (a `SCHEMA_VERSION` bump), not v1.

## 5. Raster channels — `(C=7, grid_h, grid_w)`

| ch | name | source | kind |
|---|---|---|---|
| 0 | `oob_mask` | outside `hangar.floor_polygon` (incl. L-notch); falls back to outer rect when no notches | static |
| 1 | `bay_mask` | maintenance-bay rectangle (`models.MaintenanceBay`, back-anchored) | static |
| 2 | `apron_mask` | staging region `y ∈ [-apron_depth_m, 0)` (ADR-0021) | static |
| 3 | `door_gap` | door opening band on the front wall (`hangar.door.center_x_m ± width_m/2` at `y≈0`) | static |
| 4 | `parked_occ_low` | parked part polygons whose z-interval intersects `[0, z_split_m)` | dynamic |
| 5 | `parked_occ_wing` | parked part polygons whose z-interval intersects `[z_split_m, ∞)` | dynamic |
| 6 | `active_occ` | active object's part polygons at its current pose (single band) | dynamic |

**The z-split (ch4/ch5) is the design's payoff for oblique nesting.** A flat 2-D occupancy cannot distinguish a wingtip overhanging a *low tailplane* (legal, ADR-0023) from a wingtip into a *fin* (illegal). Each `WorldPart` from `geometry.aircraft_parts_world()` carries `z_bottom_m`/`z_top_m`; a part paints into a band when its `[z_bottom, z_top]` interval intersects that band's z-range. `z_split_m` (default 1.6 m) is a config knob aligned with the wing-layer clearance / typical low-tailplane height. The active object stays single-channel (it is the one being controlled; its z-structure matters less than its footprint).

Channel polygons come from the **same** `aircraft_parts_world()` the collision verifier uses (ADR-0001 parts model, ADR-0002 determinant-−1 transform), so the raster's geometry matches ground truth exactly — no second, drifting source of footprints.

## 6. Set tokens — `(max_objects, F)` + `token_mask`

One row per object (parked + active + unplaced), in a **stable order** (parked-in-placement-order, then active, then unplaced-in-queue-order), padded to `max_objects`. `token_mask[i]` is True for real rows. `active_index` is the active object's row (or -1 at a terminal state). Feature block (`F = 22`, fully tabulated in the schema doc):

| slots | feature | notes |
|---|---|---|
| 3 | status one-hot | `[parked, active, unplaced]` |
| 3 | type one-hot | `[aircraft, fixed_obstacle, mover]` |
| 2 | body-frame dims | `[length, width]` from `aircraft_parts_world()` at identity pose, ÷ `pos_ref_m` |
| 1 | wing position | `is_high_wing` (aircraft only; 0 for ground objects) |
| 3 | movement mode | `[always_cart, cart_eligible, always_own_gear]` (ground objects: 0) |
| 2 | cart flags | `[on_carts, tow_pivotable]` |
| 1 | turn radius | `effective_turn_radius_m()` ÷ `pos_ref_m` |
| 1 | door flag | `hard_door_mover` (ground objects; 0 for aircraft) |
| 4 | pose | `[x, y, sinθ, cosθ]` — parked & active; **zeros** for unplaced |
| 2 | **reserved (zero in v1)** | `[region_side, seq_order]` |
| **22** | | |

**Reserved slots** (`region_side`, `seq_order`): the reward already names these soft terms (`RegionPreference` #604, door-order #614) but the env does not yet expose them (they read 0.0 in `env.step` today). Reserving two zero-filled columns keeps the eventual wiring a non-breaking `SCHEMA_VERSION` bump rather than a token-width reshape mid-training.

**Body-frame dims:** computed by calling `aircraft_parts_world(body, Placement(id, 0, 0, 0))` and taking the union bounding box — a deterministic, reuse-the-verifier-geometry derivation, not a separate dim field.

## 7. Legal-action mask — `(9,)` bool

A **fixed canonical action order**:

```
[ (L,+1), (S,+1), (R,+1), (L,-1), (S,-1), (R,-1), (T,+1), (T,-1), PARK ]
   idx0    idx1    idx2    idx3    idx4    idx5    idx6    idx7    idx8
```

derived from `geometry_oracle.legal_primitives(active_body, on_carts)`:
- **own-gear** (`turn_radius > 0`): `L/S/R × fwd/rev` legal (idx 0–5); strafe `T` illegal.
- **cart** (`turn_radius == 0`, non-lateral): `L,S,R` fwd + `S` rev legal.
- **cart, lateral** (strafe-eligible): the above + `T` fwd/rev (idx 6–7).
- `PARK` (idx 8) is always legal.

Magnitude stays **continuous** and is a sub-project #3 decision — this mask covers only discrete `(kind, gear)` legality for action masking. At a terminal state (`active is None`), the mask is all-False except `PARK` is left False too (no action applies); `active_index == -1` signals the terminal case.

## 8. Normalization & determinism

- **Positions** (raster-frame token poses) normalized to `[-1, 1]` over the raster window; **headings** as `(sinθ, cosθ)`; **dims / radii** ÷ `pos_ref_m` (≈20 m).
- `meta` carries the un-normalization constants (`cell_m`, world origin, `grid_h`, `grid_w`, `steps_this_object`, `steps_total`) for debugging and round-trip checks.
- **Determinism:** `encode()` is a pure function of `(Observation, hangar, config)` with no RNG and a fixed iteration order; rasterization via `shapely.contains_xy` is deterministic. A test double-encodes and asserts `np.array_equal` on every field. This stands in for `determinism-guard` (which guards `solver.py`/`towplanner.py`, not `ml/`) on the learned path.

## 9. Testing (TDD, numpy-only)

`tests/ml/test_encoding.py`, reusing `tests/ml/conftest.py` fixtures (`empty_hangar`, `single_object_layout`, `two_object_layout`, `_fuji`):

- **shape & dtype**: `raster (7, grid_h, grid_w) float32`; `tokens (16, 22) float32`; `token_mask (16,) bool`; `legal_action_mask (9,) bool`; `schema_version == 1`.
- **static channels**: `oob_mask` set outside the floor / inside the L-notch; `bay_mask` over the bay rectangle; `apron_mask` over the `y<0` rows; `door_gap` spans the door opening.
- **rasterization correctness**: a known axis-aligned box occupies the expected cell count (±1 boundary cell); a part at a known world pose lands in the expected cells.
- **z-split**: a high-wing aircraft's wing paints `parked_occ_wing`; a low part paints `parked_occ_low`; a part spanning the split paints both.
- **tokens**: parked/active/unplaced status one-hots; active row matches `active_index`; unplaced rows have zero pose; padding rows masked False; reserved slots zero.
- **legal mask**: cart body → strafe legal; own-gear body → strafe illegal; `PARK` always legal; terminal `Observation` → `active_index == -1`.
- **determinism**: double-encode → `np.array_equal` on all fields.

## 10. Reuse map (from the codebase survey)

| need | reuse |
|---|---|
| world part polygons (aircraft **and** ground objects) | `geometry.aircraft_parts_world()` → `list[WorldPart]` with `.polygon`, `.z_bottom_m`, `.z_top_m`, `.kind` |
| hangar floor incl. L-notch | `hangar.floor_polygon` (or outer-rect fallback) |
| bay / door / apron geometry | `models.MaintenanceBay`, `hangar.door`, `hangar.apron_depth_m` |
| legal primitive set | `geometry_oracle.legal_primitives(body, on_carts)` |
| movement-mode / kinematics fields | `Aircraft` / `GroundObject` in `models.py` (`movement_mode`, `effective_turn_radius_m()`, `on_carts`, `tow_pivotable`, `hard_door_mover`) |
| rasterizer | **none exists** — implement with `shapely.contains_xy` over cell-centre meshgrid |

## 11. Determinism & packaging notes

- `ml/` is **never in the wheel** (`[tool.setuptools.packages.find] where = ["src"]`), like `bench/`/`viewer/`. The tensorizer adds **no new runtime dependency** (numpy + shapely are already deps); `torch` arrives only with the #3 `[train]` extra.
- Not under ADR-0003 / `determinism-guard`; the §8 determinism test is the local guard.
- A schema reference doc `docs/architecture/ml-observation-schema.md` (mirroring `docs/architecture/scene-v2-schema.md`) tabulates every channel and token slot against `SCHEMA_VERSION`.

## 12. Workflow

File a GitHub issue *"#607 rung 3: observation tensorizer (sub-project #2)"* (rung 1 = #670, rung 2 = #672; Part of #607) before coding; branch `feature/<slug>` off `develop`; TDD; draft PR with `Closes #<n>`; review arc (`code-reviewer` main pass + `silent-failure-hunter` for the geometry/keep-out edges + `geometry-invariant-guard` is **not** required since `geometry.py`/`collisions.py` are untouched — the encoder only *consumes* `aircraft_parts_world`); CHANGELOG `[Unreleased]` entry. Flip to ready when the review arc is clean.

## 13. Open questions (resolve in #3, not here)

- Continuous vs binned action **magnitude** (the encoder already emits the discrete legality; magnitude is the policy head's concern).
- Whether the network wants **area-fraction** raster cells instead of binary occupancy (a non-breaking `SCHEMA_VERSION=2` refinement if so).
- Whether `max_objects=16` / `cell_m=0.25` / `z_split_m=1.6` need tuning once a CNN is attached (all config knobs).
- Whether cross-*hangar* training needs a synthetic hangar generator (revisited in #4 with measured results).
