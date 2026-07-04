# #912 — Mover pin (hand-place a car/trailer, drag-to-fix)

**Date:** 2026-07-04
**Issue:** #912 (epic #436, milestone #39; builds on #911 drag-to-fix + #445 `hangarfit serve` + #910 catalog palette)
**Status:** design approved (scope + behavioral fork decided); implementation pending
**Extends:** ADR-0025 (ground-object taxonomy) with an optional **mover pin** — a domain decision, so PR A carries an ADR amendment (§8). ADR-0002 (Python owns the transform) and ADR-0003 (deterministic solve) bind it. Reuses the #859 (ADR-0021-adjacent) hand-placed / path-less machinery and the #911 `POST /convert` seam verbatim.

---

## 1. Problem & goal

A `placed_routed_mover` (car / towed trailer) is a full RR-MC search citizen — the solver chooses its pose (#604). There is **no way to hand-place one at an exact spot**: a Scenario's `ground_objects:` entry for a mover **rejects an authored pose** (`loader.py:914-919`, a #604 rule — *not* ADR-0025, which never forbids a mover pose). So the club can't say "park the Caddy *here*" as part of an exception layout, and the #911 drag gizmo can't move a cart.

**Goal:** let a `placed_routed_mover` carry an **optional pin** — a hand-authored resting pose the solver honors — set either by hand-authoring the Scenario YAML or by dragging the mover in the `--edit` viewer (reusing #911's gizmo + `POST /convert` verbatim). An **unpinned** mover stays solver-placed and byte-identical to today (ADR-0003).

**Decided scope:** end-to-end — backend schema+solver (**PR A**) then the editor drag (**PR B**), mirroring #911's A/B split.

## 2. Decisions

### 2.1 A pinned mover is a **path-less keep-out** (not tow-routed) — decided

A pinned mover is placed at its pin and treated as a **fixed keep-out** for the rest of the fill (reusing the #859 `Placement.hand_placed=True` path-less machinery): the tool does **not** compute a tow route to it. It keeps its `placed_routed_mover` class (so it renders as a mover, joins the pairwise overlap loop, and — if `hard_door_mover` — stays subject to the #603/ADR-0026 Caddy egress gate; see §3.3). Rationale: matches "I parked it here," reuses existing machinery, and a pin never triggers a false exit-3 from the coarse tow grid (the known fk9↔cessna grid-lock class). **Rejected:** "still tow-routed + verified" (rigorous but risks the coarse 0.5 m/15° grid declaring a physically-fine spot unroutable).

### 2.2 A pinned mover is **not** a `fixed_obstacle` — decided (recon-confirmed)

Making a pinned mover a `fixed_obstacle` would inject it via `Scenario.fixed_obstacle_placements` and **bypass the search + drop its mover identity** (routing enumeration, `hard_door_mover` egress, MOVER_3D rendering). Instead we mirror the **aircraft-pin pattern**: the mover stays a `placed_routed_mover`; its search sample is short-circuited to the pin and it is excluded from the descent's movable set.

### 2.3 Pin representation: a **parallel `Scenario.mover_pins` map** — decided

The pin lives in a new `Scenario.mover_pins: Mapping[str, Placement]`, a mover-scoped map alongside the existing `region_preferences` — **not** by overloading the aircraft `constraints` map (whose `constraints.keys() ⊆ fleet_in` invariant and `priority`/`force_on_carts`/`nose_out`/`movement_mode` fields are meaningless for a mover). Pin shape is the **3-field `{x_m, y_m, heading_deg}`** the `fixed_obstacle` scenario pose already uses — **no `on_carts`** (movers never ride carts). A mover may carry a pin **or** a `region_preference`, not both (a pin fixes position; a region preference steers a *searched* position — mutually exclusive).

## 3. Architecture — PR A (backend: schema + solver)

### 3.1 Model (`models.py`)

- `Scenario` gains `mover_pins: Mapping[str, Placement] = {}` (frozen/immutable like the other maps). Each value is a `Placement(plane_id=<mover_id>, x_m, y_m, heading_deg, on_carts=False, hand_placed=True)` — `hand_placed=True` is what makes it path-less downstream (§3.3).
- `Scenario.__post_init__` invariants (new): `mover_pins.keys() ⊆ set(mover_ids)`; `mover_pins.keys()` disjoint from `region_preferences.keys()` (mutually exclusive per §2.3); each pin's `plane_id` equals its key. No `GroundObject`/`Placement` model change (both already suffice — `Placement.hand_placed` shipped in #667 Rung A).
- `Scenario.placeable_ids` / `mover_ids` are unchanged (a pinned mover is still a mover); `pinned` membership is derived in the solver (§3.2), not a new model field.

### 3.2 Loader (`loader.py:857-945`, the Scenario `ground_objects:` branch)

Relax the single reject at `loader.py:914-919`. For a `placed_routed_mover` mapping-entry:
- **No pose** (bare id or `{object: id}`) → unchanged: solver-placed (optionally with `region_preference`).
- **Pose present** (`x_m`+`y_m`+`heading_deg`, all three required together; `region_preference` then forbidden with an actionable error) → build `mover_pins[id] = Placement(id, x_m, y_m, heading_deg, on_carts=False, hand_placed=True)` instead of raising. `_ALLOWED_SCENARIO_GO_KEYS` already admits the pose keys; the change is the class-conditional branch, not the allowlist.
- The **Layout**-level `ground_objects:` parser (`loader.py:613-647`, `check`/`view`) is unchanged — it already accepts a mover pose (this relaxation only aligns the *Scenario* path with it).

### 3.3 Solver (`solver.py`) + planner (`towplanner.py`)

- **`pinned_planes`** (`solver.py:502-506`, today iterating `fleet_in` constraints only) also includes `scenario.mover_pins.keys()`. This excludes a pinned mover from the min-conflicts descent movable set (`solver.py:1540/1604`) exactly as a pinned aircraft is excluded.
- **Initial placement:** the mover-sampling path (`_initial_placement_for_plane`, `solver.py:1174-1196`) returns `scenario.mover_pins[id]` verbatim when present (the pin short-circuit is already pose-source-agnostic; extend its lookup to consult `mover_pins` for a mover id in addition to `constraints` for an aircraft id).
- **Path-less keep-out:** because the pinned mover's `Placement.hand_placed=True`, the `plan_fill` routing (`towplanner.py:2291-2313`) skips computing a tow path for it and it is emitted as a path-less at-rest body (the #859 Rung A behavior). It still joins the pairwise overlap loop (collision) as a static body. **Plan-verification item:** #859's `hand_placed` path-less handling was introduced for dolly *aircraft*; the plan must confirm `plan_fill`/`scene._timeline` honor `hand_placed=True` on a **ground-object (mover)** placement too (the flag lives on `Placement`, which is shared) — and if any code path branches on aircraft-vs-mover before consulting `hand_placed`, extend it or provide the equivalent path-less handling for the mover.
- **Hard-door egress preserved:** a pinned `hard_door_mover` (the Caddy) **remains subject** to the #603/ADR-0026 egress gate — the pin fixes *where* it rests, it does not waive the hard safety constraint. (Implementation note for the plan: confirm the egress gate keys off the mover's resting pose, which the pin supplies, and does not require a computed tow path; if it does, the gate must still run against the pinned pose.)
- **Determinism (ADR-0003):** the entire relaxation is gated on `mover_pins` being non-empty. An empty `mover_pins` leaves `pinned_planes`, sampling, and routing byte-identical to today. The `determinism-guard` double-solve on an unpinned scenario must stay bit-identical.

### 3.4 `check` / verification

`hangarfit check` on a hand-authored **Layout** already renders a posed mover (unchanged). For a **Scenario**, `solve` seats the pinned mover at the pin and verifies the whole layout (collisions + egress); an invalid pin (overlap, or a pinned Caddy that fails egress) yields an INVALID/appropriate exit exactly as any invalid layout does — the pin does not weaken verification, it only fixes position.

## 4. Architecture — PR B (editor: drag a mover)

### 4.1 Editor-context pose source (`viewer.py build_editor_context`)

`currentPoses` (`viewer.py:160-173`, today aircraft-only from `layout.placements`) is **extended to also include placed movers** from `layout.ground_object_placements` whose `object_class == "placed_routed_mover"`, keyed by ground-object id (ids are disjoint from aircraft ids by the Layout/Scenario invariant, so the merge is safe). A mover entry is `{x_m, y_m, heading_deg, world_yaw_rad: compass_to_math_rad(heading_deg), on_carts: false}` — `on_carts` is inert for a mover (kept only so the `CurrentPose` shape is uniform; the mover-pin export drops it). This is an **editor-context** change (`hangarfit.editor-context/v1`, additive), **not** scene/v2 — `build_scene` is untouched (ADR-0017). Result: the #911 gizmo arms a focused mover with **zero gizmo change** (it reads `currentPoses[id]` uniformly). Fixed obstacles are **not** added (they aren't drag-pinnable in this scope).

### 4.2 Intent + convert (`intent-contract.ts`, `editor.ts`)

- New `Intent.moverPins: Record<string, { x: number; y: number; heading: number }>` — a 3-field mover-pose map (no `onCarts`), parallel to the aircraft `mustPositions`.
- `editor.ts` `onConverted(id, pose)` branches on `ctx.catalog?.[id]?.kind`: a `placed_routed_mover` → `moverPins[id] = { x: pose.x_m, y: pose.y_m, heading: pose.heading_deg }`; an aircraft → the existing `pinAtPose`/`mustPositions`. The **Fix position** button enablement (`syncControls`) admits a focused placed mover (has a `currentPose`) as well as a focused selected aircraft. `POST /convert` is reused **verbatim** — it is pose-kind-agnostic (Python owns the inverse either way, ADR-0002).

### 4.3 Export (`export.ts`)

The `ground_objects:` emission (today a bare-id list, `export.ts:27-35`) is widened: a mover with a `moverPins[id]` entry is emitted as a **mapping entry** `{ object: id, x_m, y_m, heading_deg }`; a mover without a pin stays a bare id. Byte-identical when no mover is pinned (ADR-0003). The exported Scenario round-trips through the PR-A loader.

## 5. Data flow (drag a mover, the full round-trip)

```
focus mover (#904) → Fix position → arm gizmo (proxy seeded from the mover's currentPoses world_yaw_rad)
  → drag on floor: mover mesh slides live (translation only) → drop: POST /convert {x, y, world_yaw_rad}
  → Python: {x_m, y_m, heading_deg}  → onConverted → moverPins[id]  → Calculate
  → export ground_objects:[{object:id, x_m, y_m, heading_deg}] → POST /solve
  → loader builds mover_pins[id] → solver seats it path-less at the pin → reRender (mover at the pinned pose)
```

## 6. ADR compliance

- **ADR-0002/0029:** the browser never authors the transform; `/convert` (Python) is reused verbatim; the mover mesh live-follow is translation-only (the #911 manipulator, unchanged). `world_yaw_rad` for a mover is Python-computed in `build_editor_context`.
- **ADR-0003:** empty `mover_pins` ⇒ byte-identical solve/plan/scene/export. Pinning changes the problem (fewer search bodies) — expected, and covered by an unpinned determinism canary.
- **ADR-0017 / scene-schema-guard:** `build_scene`/scene/v2 untouched; the mover pose in `currentPoses` is an editor-context field. Movers already carry a `final_pose` in scene/v2 (`scene.py:179-208`) — unchanged.
- **ADR-0025 (amended, §8):** the ground-object taxonomy gains an optional mover pin; a pinned mover stays a `placed_routed_mover`.

## 7. Testing

- **Loader (`tests/test_loader*.py`):** a Scenario mover with a pin loads → `mover_pins[id]` with the right pose + `hand_placed=True`; the 3-field requirement (all of `x_m/y_m/heading_deg` or none); pin + `region_preference` together → `LoaderError`; a pin on a non-mover id (aircraft/fixed_obstacle) → `LoaderError`. A round-trip (export → load) unit.
- **Solver (`tests/test_solver*.py` + `determinism-guard`):** a pinned mover seats at the pin and is excluded from the search; the rest of the fill routes around it (path-less mover, no tow path emitted for it); **unpinned** scenario is byte-identical (the double-solve canary).
- **Egress:** a pinned `hard_door_mover` that blocks the door → invalid (egress gate still fires); a valid pinned Caddy → valid.
- **Editor (PR B — `viewer/test`, `tests/test_viewer.py`):** `build_editor_context.currentPoses` includes a placed mover with the right pose + `world_yaw_rad`; the editor-context↔`intent-contract.ts` key-parity holds; `moverPins` export → `ground_objects` mapping entry (pure `node --test`); byte-identical when no mover is pinned.
- **Headless smoke (PR B):** a served `--edit` page focuses a mover, Fix-position arms the gizmo (no transform banner); the manual test drags a cart and Calculates.

## 8. Delivery (two sequential PRs)

- **PR A — backend (area:backend):** `Scenario.mover_pins` + `__post_init__` invariants; the loader relaxation; the solver `pinned_planes`/initial-placement/path-less plumbing; an **ADR** (amend ADR-0025 or a small new ADR recording the mover-pin semantics — path-less keep-out, retains class + egress); loader/solver/determinism tests; CHANGELOG. Independently mergeable + testable by hand-authoring a Scenario mover pin. **Review guards:** `code-reviewer`, `silent-failure-hunter` (loader), `type-design-analyzer` (`models.py` `mover_pins`), `determinism-guard` (`solver.py`).
- **PR B — editor (area:viewer):** `currentPoses` extended to movers; `Intent.moverPins` + `editor.ts` mover branch in `onConverted` + Fix-position enablement; `export.ts` mapping-entry widening; rebuilt `viewer.js`; editor/parity tests + headless smoke; CHANGELOG. Off `develop` **after** PR A merges (consumes the PR-A loader). **Review guards:** `code-reviewer`, `scene-schema-guard` (`viewer.py` editor-context + `viewer.js`).

## 9. Scope / non-goals

- **Not in scope:** dragging a **fixed_obstacle** (needs a pose the palette can't yet produce; its pose is already authored in a Layout). Multi-select mover drag. Changing a mover's *route* (only its resting pose). Pinning a mover that isn't yet placed (a palette-added mover with no `currentPose` — it must be solver-placed once before it can be dragged, same as an aircraft).
- **Deferred:** a mover pin does not gain a UI in a hand-authored Layout beyond what already works (`check`/`view` already accept a posed mover).
