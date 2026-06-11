# A1 (#601) — Ground-object data model + loader

**Status:** Approved (design), 2026-06-11
**Issue:** #601 (child of Epic A #600 — "Ground objects + Herrenteich calibration", milestone #34)
**Builds on:** #595 per-object catalog (`type:` discriminator → per-type builder)
**Blocks / siblings:** #602 (mover motion + ADR-0010 amendment), #603 (Caddy hard-door/egress), #604 (soft trailer region), #605 (Herrenteich calibration), #606 (rendering)

---

## 1. Purpose & scope

The hangar floor holds more than aircraft. The real Airfield Herrenteich set adds a **fuel trailer** (fixed, never moves), **two glider trailers** (towed), and a **VW Caddy** (self-driving). Today the model has exactly two non-aircraft geometry flavours, both *hangar-intrinsic* keep-outs: the state-gated `MaintenanceBay` and the always-on `StructuralNotch`. There is **no model for a free-standing object** — fixed or movable — authored as scenario/layout data with a pose.

A1 introduces a **general, Herrenteich-agnostic ground-object data model + loader** so non-aircraft objects get a clean home built on the #595 catalog. **A1 ships data + minimal verifier wiring only** — enough to prove the two object classes flow through `collisions.check` and the tow planner. The real route search, the Caddy egress gate, the soft trailer region, the feasibility calibration, and rendering are all sibling issues.

### In scope
1. `GroundObject` model type + loader (concrete catalog `type:` values, strict allowlists, `LoaderError`).
2. Fixed obstacles wired into the existing keep-out path (collisions pairwise-set + tow-planner static obstacles).
3. Movers registered into the placeable (pairwise collision) + routable (`plan_fill` enumeration) sets — **as data**, with the per-mover path search deferred to #602.
4. Empty-set **byte-identity** and **determinism** preserved (ADR-0003 / `determinism-guard`).
5. Docs: ADR-0025, arc42 §5/§8, CHANGELOG.

### Explicitly deferred (non-goals)
- Real mover **route search** (towed Reeds–Shepp/cart vs steerable arcs) + the **ADR-0010 motion amendment** → #602.
- Caddy **hard nearest-door + clear-egress** gate (a new rejection tier) → #603.
- Soft **right-side trailer region** preference → #604.
- Herrenteich **dims/clearance calibration** to a feasible real all-11 set, and authoring the **real** `fuel_trailer`/`vw_caddy`/`glider_trailer` catalog entries → #605. A1 uses **test-fixture** ground objects.
- **Rendering / 3D view** of ground objects → #606.
- **Constraint-hook fields** (hard-door flag, soft-region preference). YAGNI: #603/#604 add their own fields when they implement the semantics; appending optional fields to a frozen dataclass later is non-breaking.

---

## 2. Decisions (locked)

| # | Decision | Rationale |
|---|---|---|
| D1 | **Concrete catalog `type:` values** — `fixed_obstacle`, `car`, `trailer` — each with its own `_build_*` builder. `object_class` is *derived* from the type. | User choice. Friendlier, real-world catalog YAML. Motion behaviour is *defaulted per type* but stays an overridable **data field** (`motion_mode:`), so the issue's "carry the field" requirement holds. |
| D2 | **Layout-uniform via `Placement`.** Ground objects reuse `Part` (footprint), `Placement` (pose), and the det-−1 world transform. Held in parallel `ground_objects` / `ground_object_placements` fields on `Layout`. `fleet: dict[str,Aircraft]` is untouched. | User choice + max reuse. A fixed obstacle = "a placed body whose parts are keep-outs"; a mover = "a placed body in pairwise collision." No new geometry engine; aircraft callers see zero churn. |
| D3 | **Pairwise-set seam** for fixed-obstacle keep-out (not floor-polygon subtraction). | Falls out of D2. Supports an *oblique*-parked trailer (a fuel trailer at the door is the canonical case); floor-polygon subtraction is axis-aligned only. Matches the issue's recommendation. |
| D4 | **Real catalog entries deferred to #605.** A1 uses test fixtures under `tests/fixtures/catalog/`. | Keeps A1 from shipping un-calibrated real data; #605 authors + calibrates the real Herrenteich objects. |

---

## 3. Model design (`src/hangarfit/models.py`)

A new frozen-slots dataclass mirroring `Aircraft`'s conventions exactly (tuples not lists; `ValueError` from `__post_init__`; `_VALID_*` frozensets derived from `typing.get_args`; `object.__setattr__` only inside `__post_init__`).

```python
GroundObjectClass = Literal["fixed_obstacle", "placed_routed_mover"]
MoverMotionMode   = Literal["steerable", "towed"]
_VALID_GROUND_OBJECT_CLASSES = frozenset(typing.get_args(GroundObjectClass))
_VALID_MOVER_MOTION_MODES    = frozenset(typing.get_args(MoverMotionMode))

@dataclass(frozen=True, slots=True)
class GroundObject:
    id: str
    name: str
    parts: tuple[Part, ...]
    object_class: GroundObjectClass
    motion_mode: MoverMotionMode | None = None   # None iff fixed_obstacle
    turn_radius_m: float | None = None           # static catalog data; carried-but-unconsumed in A1
    measured: bool = False
```

**`__post_init__` invariants** (order: cheap field checks → cross-field consistency):
- `id` non-empty; `name` non-empty; `parts` non-empty.
- `object_class in _VALID_GROUND_OBJECT_CLASSES`.
- `object_class == "fixed_obstacle"` ⇒ `motion_mode is None and turn_radius_m is None` (a fixed obstacle never moves).
- `object_class == "placed_routed_mover"` ⇒ `motion_mode in _VALID_MOVER_MOTION_MODES`.
- `turn_radius_m`, when set, `> 0` (mirrors `Aircraft` own-gear radius rule).

**`Layout` changes** (`models.py`, the `Layout` dataclass). Two new fields, both default-empty so an existing layout is byte-identical:
```python
ground_objects: Mapping[str, GroundObject] = field(default_factory=dict)   # wrapped MappingProxyType in __post_init__, like `fleet`
ground_object_placements: tuple[Placement, ...] = ()                       # poses, reusing Placement
```
- Register `ground_objects` in `_PROXY_FIELDS` so the MappingProxyType wrap + pickle (`__getstate__`/`__setstate__`) round-trips (the #544/#545 path).
- New invariants paralleling the aircraft ones:
  - `ground_objects[k].id == k` for every entry (mirrors the `fleet` key==id check).
  - every `ground_object_placement.plane_id ∈ ground_objects` (mirrors "placements reference real planes").
  - no duplicate `ground_object_placements` ids.
  - **`set(ground_objects) ∩ set(fleet) == ∅`** — ground-object ids are disjoint from aircraft ids, so a placement id resolves to aircraft XOR ground-object unambiguously. (New invariant unique to A1.)

**`Scenario` changes** — a thin id-list for the solve path:
```python
ground_objects: tuple[str, ...] = ()   # like fleet_in; validated to resolve against the catalog/ground-object set
```
The solver does **not** place movers in A1 (that's #604); the field is carried + validated for forward-compat and for the loader round-trip.

---

## 4. Catalog + loader design (`src/hangarfit/loader.py`)

### 4.1 Builder registry (replaces the Stage-A guard)
`_build_catalog_object` (currently `loader.py:157`, `if obj_type != "aircraft": raise LoaderError("…Stage A #600…")`) becomes a registry dispatch:
```python
_CATALOG_BUILDERS = {
    "aircraft": _build_aircraft,
    "fixed_obstacle": _build_fixed_obstacle,
    "car": _build_car,
    "trailer": _build_trailer,
}
# obj_type = raw.get("type", _DEFAULT_OBJECT_TYPE); strip "type"; dispatch.
builder = _CATALOG_BUILDERS.get(obj_type)
if builder is None:
    raise LoaderError(f"{source}: unknown catalog type {obj_type!r}; known types: {sorted(_CATALOG_BUILDERS)}")
return builder(entry, source=source)
```
`_build_catalog_object`'s return type widens to `Aircraft | GroundObject`. **`load_fleet` keeps returning `dict[str, Aircraft]`** — it parses only the manifest's `aircraft:` list; ground objects are loaded by a separate `load_ground_objects`. (No caller churn; the agent-flagged union-return break is avoided by keeping the two manifest lists and two loaders separate.) **Defensive guard:** `load_fleet` asserts each built object `isinstance(... , Aircraft)` and raises `LoaderError` if a `ground_object`/`car`/`trailer` ref is mistakenly listed under `aircraft:` (and `load_ground_objects` raises symmetrically if an `aircraft` ref appears under `ground_objects:`). This keeps the `dict[str,Aircraft]` contract honest rather than violating it silently.

### 4.2 The three new builders
Each takes `(raw: dict, *, source: Path) -> GroundObject`, validates a strict per-type key allowlist (mirroring `_ALLOWED_AIRCRAFT_KEYS`), and sets `object_class` + motion defaults:

| `type:` | builder | `object_class` | motion default | turn_radius |
|---|---|---|---|---|
| `fixed_obstacle` | `_build_fixed_obstacle` | `"fixed_obstacle"` | `None` (rejected if authored) | `None` (rejected) |
| `car` | `_build_car` | `"placed_routed_mover"` | `"steerable"` (overridable via `motion_mode:`) | optional, default `None` |
| `trailer` | `_build_trailer` | `"placed_routed_mover"` | `"towed"` (overridable) | optional, default `None` |

Allowlists: `fixed_obstacle` → `{id, name, parts, measured}`; `car`/`trailer` → `{id, name, parts, motion_mode, turn_radius_m, measured}`. `parts` parsing reuses the existing aircraft part parser (single-rectangle is the common case; the parts machinery already supports it).

### 4.3 Manifest + scenario/layout wiring
- **Manifest** (`fleet.yaml`): optional top-level `ground_objects: [catalog refs]`. New `load_ground_objects(path) -> dict[str, GroundObject]` parses it via `_build_catalog_object`; returns `{}` when the key is absent (existing manifests byte-identical). Add `ground_objects` to the manifest key allowlist.
- **Layout YAML**: optional `ground_objects:` block — a list of `{object: <catalog id>, x_m, y_m, heading_deg}` entries. The set of *available* ground objects comes from the **same manifest** the layout's `fleet:` key already references (that manifest now carries both an `aircraft:` and a `ground_objects:` list) — i.e. when `load_layout` resolves the fleet via `load_fleet(manifest)` it also resolves the ground-object catalog via `load_ground_objects(manifest)`; an injected `ground_objects=` map overrides, mirroring `fleet=`. Each layout entry's `object` id is resolved against that set, and builds `Placement(plane_id=<id>, x_m, y_m, heading_deg, on_carts=False)`. Extend `_ALLOWED_LAYOUT_KEYS` with `"ground_objects"`; strict per-entry allowlist `{object, x_m, y_m, heading_deg}` with `LoaderError` on unknown/missing keys and on an unresolved `object` id (naming the id + the catalog, via the `_resolve_known_plane_id` near-match pattern).
- **Scenario YAML**: optional `ground_objects: [ids]`. Extend `_ALLOWED_SCENARIO_KEYS`; validate each id resolves; pass to `Scenario(...)`.
- `load_layout` / `load_scenario` gain an optional `ground_objects=` injection param mirroring `fleet=`/`hangar=`.

### 4.4 Error discipline
All new parsing follows the existing rules: inner builders omit the path prefix; outer loaders catch `(ValueError, KeyError, TypeError, LoaderError)` and re-wrap with `f"{path}: …"`; list fields validated `isinstance(list)` early then coerced to tuple; unknown keys → loud `LoaderError` with `sorted(unknown)` + `sorted(allowed)`.

---

## 5. Collision wiring (`src/hangarfit/collisions.py`)

`check(layout)` builds **ground-object world parts** through the *same* transform path used for aircraft (no new geometry — sidesteps the det-−1 sign-flip trap; ground-object `Part`s carry plane-local coords transformed by the placement exactly like aircraft parts). Each world part is tagged with its owner's `object_class`.

- **fixed_obstacle → keep-out.** Any aircraft or mover world part overlapping a fixed-obstacle world part ⇒ a new **single-object `ground_obstacle`** `Conflict` (`Conflict.single(kind="ground_obstacle", plane=<aircraft/mover id>, detail=…naming the obstacle…)`). Reuse the `_parts_conflict` predicate (plan-view `polygon_overlap` + height clause) for consistency with the oracle. `fixed↔fixed` overlaps are ignored (two static keep-outs may coincide).
- **placed_routed_mover → pairwise body.** Mover world parts join the pairwise overlap loop exactly like aircraft (mover↔aircraft, mover↔mover), emitting the existing deterministic `<sorted_kinds>_overlap` conflicts and accumulating `total_penetration_m2`.
- **Bounds.** Ground-object bounds checking is **out of A1 scope** (no AC; #604/#605 own valid placement). `_hangar_bounds_conflicts` is unchanged.
- **Byte-identity:** with empty `ground_objects`/`ground_object_placements`, the conflict set + order + `total_penetration_m2` are bit-identical to today (the new code is a guarded no-op over an empty collection). The per-vertex-before-polygon gate ordering in the existing checkers is untouched.

Implementation note: prefer one helper that converts a `(GroundObject, Placement)` pair to `list[WorldPart]` (reusing the aircraft world-part builder), so the obstacle/mover geometry uses the **same boundary semantics** as the oracle — preventing in-search vs verifier divergence (the towplanner reuse-of-oracle invariant).

---

## 6. Tow-planner wiring (`src/hangarfit/towplanner.py`)

- **`_build_obstacles(placed, mover_id)`**: fixed-obstacle world parts (+ their AABBs, preserving the `_Obstacles` parallel-array `__post_init__` invariant) join the **static** obstacle set beside the existing `notch_boxes`/placed-plane parts. Other movers (≠ the one being routed) join as static placed bodies, exactly like placed aircraft. Fixed obstacles are collected **separately from `placed.placements`** (they are not in the aircraft placement tuple) and in a **stable sorted order** (sort by id) so the world-parts tuple — and thus every AABB/polygon-test order — is deterministic.
- **`plan_fill`**: the routable enumeration (`back_first_order`) covers aircraft **and movers**. In A1 the per-mover path search is **deferred to #602**: a mover is recognised in the routed set but yields a deferred/`None` path, reusing the existing best-effort `plans[i] = None` Phase-3a pattern (an un-routable body names itself on stderr and the plan still renders). AC4 is satisfied by the mover *appearing in the routed enumeration*, not by a successful route. Fixed obstacles are **never** in the routable set.
- **Determinism / byte-identity:** with no ground objects, `_build_obstacles` and `plan_fill` produce bit-identical plans; the `determinism-guard` double-run on a fixed seed stays identical (its fixtures carry no ground objects). The apron/door-throat machinery is untouched in A1 (a door-blocking fixed obstacle *narrowing* the throat is a natural consequence of it being a static obstacle; the explicit nose-out gating against door-blockers is #602/#603 polish).

---

## 7. Docs & housekeeping

- **ADR-0025** "Ground-object taxonomy + keep-out reuse": concrete-type rationale (D1), Layout-uniform via `Placement` (D2), the pairwise-set keep-out seam (D3), and the A1 scope line. Note that the ADR-0010 *motion* amendment is deferred to #602.
- **arc42 §5** (building-block view): add a ground-object responsibility line to `models`, `loader`, `collisions`, `towplanner`.
- **arc42 §8** (crosscutting): a short "Ground objects" subsection under the parts model — the two object classes, the keep-out-vs-pairwise distinction, and the catalog `type:` vocabulary.
- **CHANGELOG.md `[Unreleased]`**: a user-facing entry (new catalog types + layout `ground_objects:` block).
- **Catalog README** (`data/catalog/README.md`): document the new `type:` values and the ground-object conventions.

---

## 8. Test plan (TDD — red first)

Driven test-first; the byte-identity + empty-case guarantees are ideal for red-green.

**Model (`tests/test_models*.py`)**
- `GroundObject` valid construction (fixed_obstacle, car, trailer).
- `__post_init__` rejects: empty id/name/parts; fixed_obstacle with a motion_mode/turn_radius; mover without motion_mode; non-positive turn_radius; bad object_class.
- `Layout` rejects: ground_objects key≠id; placement referencing a missing ground object; **ground-object id colliding with a fleet id**; duplicate ground-object placement ids.

**Loader (`tests/test_loader*.py`)**
- Each new `type:` loads from a fixture catalog entry; defaults applied (car→steerable, trailer→towed); `motion_mode:` override honoured.
- Unknown `type:` → `LoaderError` listing known types (updates `test_unknown_type_is_stage_a_error`).
- Per-type allowlist rejects unknown keys; `fixed_obstacle` rejects `motion_mode`/`turn_radius_m`.
- Layout `ground_objects:` block: round-trip load; unknown/missing entry key → `LoaderError`; unresolved `object` id → `LoaderError` (named). New `test_all_allowed_layout_keys_load`-sibling exercising the extended allowlist.
- Manifest `ground_objects:` list resolves via `load_ground_objects`; absent key → `{}`.

**Collisions (`tests/test_collisions*.py`)**
- A layout with a fixed obstacle overlapped by an aircraft part ⇒ non-empty `CheckResult` with a `ground_obstacle` conflict naming both. Non-overlapping ⇒ valid.
- A mover overlapping an aircraft ⇒ pairwise `*_overlap` conflict; non-overlapping ⇒ valid.
- **Byte-identity:** an existing fixture layout with empty ground-object fields ⇒ identical `CheckResult` (conflicts + `total_penetration_m2`) to the pre-A1 path.

**Tow-planner (`tests/test_towplanner*.py`)**
- A door-blocking fixed obstacle makes an otherwise-routable plane un-routable through that throat (its box is present in the `_build_obstacles` static set).
- A mover appears in `plan_fill`'s routed enumeration (path deferred/`None`).
- **Determinism:** double-solve on a fixed seed with no ground objects ⇒ byte-identical plan (det-guard).

**Slow/non-slow split:** keep ≥1 non-slow test per new path (the two-pass coverage gotcha).

---

## 9. Files touched (estimate)

`models.py` (GroundObject + Layout/Scenario fields), `loader.py` (registry + 3 builders + manifest/layout/scenario parsing + allowlists), `collisions.py` (ground-object world parts + keep-out + pairwise), `towplanner.py` (`_build_obstacles` + `plan_fill`), `geometry.py` (possibly a small `(GroundObject, Placement) → WorldPart` helper, reusing the aircraft path), `docs/adr/0025-*.md`, `docs/architecture/05-*.md` + `08-*.md`, `data/catalog/README.md`, `CHANGELOG.md`, `tests/fixtures/catalog/*` (fixture ground objects), and the test modules above.

**Review arc:** `code-reviewer` (main) + `geometry-invariant-guard` (collisions/geometry) + `silent-failure-hunter` (loader/collisions) + `type-design-analyzer` (models) + `determinism-guard` (towplanner). Draft PR, `Closes #601`, base `develop`.
