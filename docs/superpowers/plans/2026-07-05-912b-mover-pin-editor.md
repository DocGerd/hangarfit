# #912 PR B — Mover pin editor (drag a car/trailer) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the `--edit` viewer (under `hangarfit serve`) drag a placed **mover** (car/trailer) to a chosen pose, round-trip the drop through the existing `POST /convert`, and export a `ground_objects: [{object, x_m, y_m, heading_deg}]` mapping entry the PR-A loader turns into a `mover_pins` keep-out — completing `Closes #912`.

**Architecture:** The backend (PR A, merged) already accepts a posed-mover scenario entry and seats it path-less. PR B is the **client/editor half**: extend the editor-context `currentPoses` to placed movers (so the #911 gizmo arms them verbatim), plumb the mover Groups from `addGroundObjects` into `mountEditor` (they are separate from the plane Groups today), branch the drag `onConverted` on catalog kind into a new `Intent.moverPins`, and widen `export.ts` to emit pinned movers as mapping entries. `POST /convert` and the gizmo are reused **unchanged** (Python owns the transform inverse — ADR-0002).

**Tech Stack:** Python 3.12 (`viewer.py`), TypeScript (esbuild-bundled `viewer/src/*.ts` → committed `src/hangarfit/_viewer_assets/viewer.js`), `node --test` for pure TS units, `pytest` for Python.

## Global Constraints

- **ADR-0002 / ADR-0029 — the browser never authors the transform.** `world_yaw_rad` for a mover is Python-computed (`compass_to_math_rad`) in `build_editor_context`; the drag inverse is the existing `POST /convert`. Zero JS trig; the mover mesh live-follow is translation-only (the #911 manipulator, unchanged).
- **ADR-0003 — byte-identical when nothing is pinned.** An editor that pins no mover exports a byte-identical scenario; a layout with no placed mover yields byte-identical `currentPoses`. `build_scene` / scene/v2 are untouched (this is an editor-context change).
- **ADR-0017 / scene-schema-guard.** `build_scene` and the `scene/v2` schema are NOT touched. `currentPoses` is a `hangarfit.editor-context/v1` field (additive, no schema bump).
- **Rebuild the bundle in the same change.** After any `viewer/src/*.ts` edit, rebuild `src/hangarfit/_viewer_assets/viewer.js` (`npm --prefix viewer/ run build`) and commit it, or the `viewer-build-drift` CI guard fails. The committed bundle must be byte-identical to a fresh build.
- **Loader round-trip shape (PR A, verified):** a posed mover is `{ object: <id>, x_m, y_m, heading_deg }` — **no `on_carts`** (`_ALLOWED_SCENARIO_GO_KEYS = {object, x_m, y_m, heading_deg, region_preference}`). Movers never ride carts.
- **Scope line (deliberate, documented):** a focused mover shows **no blue focus glow** (the enabled *Fix position* button + the armed gizmo are its affordance; painting movers via the highlight loop would regress their colour to excluded-amber). A mover pin has **no editable x/y/heading text fields** (unlike an aircraft pin) — the drag is the input. An **unpinned** pre-existing mover is **not** re-emitted on a served re-solve (it stays a pre-existing #445/#910 export limitation — the export owns only aircraft-from-`currentPoses` + user intent, never pre-existing ground objects; re-emitting them would break the offline byte-identity contract). All three are noted in the CHANGELOG/PR as known limitations / candidate follow-ups.

---

### Task 1: Editor-context `currentPoses` includes placed movers (Python)

**Files:**
- Modify: `src/hangarfit/viewer.py:160-173` (`build_editor_context`, the `currentPoses` dict-comprehension)
- Test: `tests/test_viewer.py`

**Interfaces:**
- Consumes: `Layout.ground_object_placements: tuple[Placement, ...]` (each `Placement.plane_id` is the ground-object id), `Layout.ground_objects: Mapping[str, GroundObject]` (`.object_class`), the already-imported `compass_to_math_rad`.
- Produces: `currentPoses[<mover_id>] = {x_m, y_m, heading_deg, on_carts: False, world_yaw_rad}` for every `placed_routed_mover` placement, merged into the existing aircraft entries. Ground-object ids are disjoint from aircraft ids (Layout/Scenario invariant), so the merge is collision-free. A layout with no placed mover yields the identical dict as today.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_viewer.py` (find an existing `build_editor_context` test for the import/fixture idiom; a herrenteich-style layout with a placed mover — e.g. the Caddy — is the natural fixture. If no mover-bearing Layout fixture is handy, build a minimal `Layout` with one aircraft placement + one `placed_routed_mover` `ground_object_placement`).

```python
def test_editor_context_currentposes_includes_placed_mover():
    # A Layout carrying a placed_routed_mover exposes that mover in currentPoses
    # (keyed by its ground-object id) so PR B's drag gizmo can arm it (#912).
    layout = _layout_with_placed_mover()  # 1 aircraft + 1 mover 'caddy' at a known pose
    ctx = build_editor_context(
        fleet_ref="fleet.yaml", hangar_ref="hangar.yaml",
        maintenance_plane=None, layout=layout,
    )
    poses = ctx["currentPoses"]
    assert "caddy" in poses
    mp = poses["caddy"]
    assert mp["x_m"] == pytest.approx(<mover_x>)
    assert mp["y_m"] == pytest.approx(<mover_y>)
    assert mp["heading_deg"] == pytest.approx(<mover_heading>)
    assert mp["on_carts"] is False            # movers never ride carts
    assert mp["world_yaw_rad"] == pytest.approx(compass_to_math_rad(<mover_heading>))
    # A fixed_obstacle placement (if any) is NOT added — not drag-pinnable here.


def test_editor_context_currentposes_unchanged_without_movers():
    # A GO-free layout's currentPoses is exactly the aircraft entries (byte path).
    layout = _layout_aircraft_only()
    ctx = build_editor_context(
        fleet_ref="f", hangar_ref="h", maintenance_plane=None, layout=layout,
    )
    assert set(ctx["currentPoses"]) == {p.plane_id for p in layout.placements}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_viewer.py::test_editor_context_currentposes_includes_placed_mover -v`
Expected: FAIL — `"caddy" not in poses` (movers absent from `currentPoses` today).

- [ ] **Step 3: Write minimal implementation**

In `src/hangarfit/viewer.py`, replace the `currentPoses` dict-comprehension (lines 160-173) with a merge of aircraft + placed movers:

```python
        "currentPoses": {
            **{
                p.plane_id: {
                    "x_m": p.x_m,
                    "y_m": p.y_m,
                    "heading_deg": p.heading_deg,
                    "on_carts": p.on_carts,
                    # #911: the world-space yaw (radians, math convention) the drag
                    # gizmo's clean PROXY is seeded with. Python-owned so the browser
                    # does no heading↔yaw trig (ADR-0002); its inverse is server.py's
                    # /convert. compass_to_math_rad(h) = radians(90 - h).
                    "world_yaw_rad": compass_to_math_rad(p.heading_deg),
                }
                for p in layout.placements
            },
            # #912 PR B: placed movers (cars/trailers) so the drag gizmo can arm
            # one and pin its pose. Keyed by ground-object id (disjoint from
            # aircraft ids, so the merge is collision-free). on_carts is forced
            # False (movers never ride carts) and kept only so the CurrentPose
            # shape is uniform; the mover-pin export drops it. Fixed obstacles are
            # excluded — they are not drag-pinnable in this scope.
            **{
                gp.plane_id: {
                    "x_m": gp.x_m,
                    "y_m": gp.y_m,
                    "heading_deg": gp.heading_deg,
                    "on_carts": False,
                    "world_yaw_rad": compass_to_math_rad(gp.heading_deg),
                }
                for gp in layout.ground_object_placements
                if layout.ground_objects[gp.plane_id].object_class
                == "placed_routed_mover"
            },
        },
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_viewer.py -k editor_context -v`
Expected: PASS (both new tests + the pre-existing editor-context tests, which use aircraft-only layouts and are unaffected).

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/viewer.py tests/test_viewer.py
git commit -m "feat(viewer): editor-context currentPoses includes placed movers (#912)"
```

---

### Task 2: `Intent.moverPins` — contract + selection ops (pure TS)

**Files:**
- Modify: `viewer/src/interaction/intent-contract.ts` (add `Intent.moverPins`)
- Modify: `viewer/src/interaction/selection.ts` (`initialIntent`, `toggleSelection`, new `setMoverPin`)
- Test: `viewer/test/selection.test.ts`

**Interfaces:**
- Consumes: `EditorContext.currentPoses` (now includes movers, Task 1), `EditorContext.catalog?.[id]?.kind` (`"aircraft" | "placed_routed_mover" | "fixed_obstacle"`).
- Produces:
  - `Intent.moverPins: Record<string, { x: number; y: number; heading: number }>` — a 3-field mover-pose map (no `onCarts`), parallel to `mustPositions`.
  - `setMoverPin(intent, id, pose: { x: number; y: number; heading: number }): Intent` — sets `moverPins[id]`, immutably.
  - `initialIntent` now excludes movers from `selectedPlaneIds` and seeds `moverPins: {}`.

- [ ] **Step 1: Write the failing test**

Add to `viewer/test/selection.test.ts` (Node's `node:test` + `node:assert`; import `initialIntent`, `toggleSelection`, `setMoverPin` from `../src/interaction/selection.ts`):

```ts
test('initialIntent excludes movers from selectedPlaneIds and seeds moverPins', () => {
  const ctx = {
    fleet: 'f', hangar: 'h', maintenance: null,
    currentPoses: {
      plane_a: { x_m: 1, y_m: 2, heading_deg: 0, on_carts: false, world_yaw_rad: 1.5708 },
      caddy:   { x_m: 3, y_m: 4, heading_deg: 90, on_carts: false, world_yaw_rad: 0 },
    },
    catalog: {
      plane_a: { name: 'A', kind: 'aircraft' },
      caddy:   { name: 'Caddy', kind: 'placed_routed_mover' },
    },
  };
  const intent = initialIntent(ctx);
  assert.deepStrictEqual(intent.selectedPlaneIds, ['plane_a']); // mover NOT selected
  assert.deepStrictEqual(intent.moverPins, {});
});

test('setMoverPin sets a 3-field pose immutably', () => {
  const base = initialIntent({ fleet: 'f', hangar: 'h', maintenance: null, currentPoses: {} });
  const next = setMoverPin(base, 'caddy', { x: 5, y: 6, heading: 45 });
  assert.deepStrictEqual(next.moverPins, { caddy: { x: 5, y: 6, heading: 45 } });
  assert.deepStrictEqual(base.moverPins, {}); // original untouched
});

test('toggleSelection carries moverPins forward', () => {
  const ctx = {
    fleet: 'f', hangar: 'h', maintenance: null,
    currentPoses: { plane_a: { x_m: 0, y_m: 0, heading_deg: 0, on_carts: false, world_yaw_rad: 0 } },
    catalog: { plane_a: { name: 'A', kind: 'aircraft' } },
  };
  let intent = setMoverPin(initialIntent(ctx), 'caddy', { x: 1, y: 2, heading: 3 });
  intent = toggleSelection(intent, 'plane_a'); // deselect
  assert.deepStrictEqual(intent.moverPins, { caddy: { x: 1, y: 2, heading: 3 } });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix viewer/ run test`
Expected: FAIL — `setMoverPin is not a function` / `intent.moverPins` is `undefined`.

- [ ] **Step 3: Write minimal implementation**

In `viewer/src/interaction/intent-contract.ts`, add a field to `Intent` (after `cartModeOverrides`):

```ts
  // #912 PR B: hand-placed mover poses (drag-to-fix). A placed_routed_mover the
  // user drags → a 3-field pose (no onCarts — movers never ride carts), exported
  // as a `ground_objects: [{object, x_m, y_m, heading_deg}]` mapping entry the
  // loader turns into a mover_pins keep-out. Empty ⇒ no such entry is emitted
  // (byte path unchanged). Parallel to `mustPositions` (the aircraft pin map).
  moverPins: Record<string, { x: number; y: number; heading: number }>;
```

In `viewer/src/interaction/selection.ts`:

Update `initialIntent` (filter movers out of the selection; seed `moverPins`):

```ts
export function initialIntent(ctx: EditorContext): Intent {
  return {
    // Only aircraft are fleet_in members. currentPoses now also carries placed
    // movers (#912) so the drag gizmo can arm them — exclude those here, else a
    // mover would export into fleet_in (the loader rejects a non-aircraft there).
    selectedPlaneIds: Object.keys(ctx.currentPoses)
      .filter((id) => ctx.catalog?.[id]?.kind !== 'placed_routed_mover')
      .sort(),
    priorities: {},
    mustPositions: {},
    doorOrder: [],
    groundObjectIds: [],
    cartModeOverrides: {},
    // Mover pins start empty — an untouched editor exports byte-identically (#912).
    moverPins: {},
  };
}
```

Update `toggleSelection`'s return literal to carry `moverPins` (it builds a fresh literal, so every field must be explicit — deselecting a plane never touches mover pins):

```ts
  return {
    selectedPlaneIds, priorities, mustPositions, doorOrder,
    groundObjectIds: intent.groundObjectIds, cartModeOverrides,
    moverPins: intent.moverPins,
  };
```

Add the new op (near `pinAtPose`):

```ts
// #912 PR B: a mover-pose sibling of pinAtPose. A placed_routed_mover dragged in
// the editor converts (POST /convert, Python-owned inverse) to a 3-field pose set
// here (no onCarts — movers never ride carts). Exported as a ground_objects
// mapping entry, distinct from the aircraft `mustPositions` pin map.
export function setMoverPin(
  intent: Intent,
  id: string,
  pose: { x: number; y: number; heading: number },
): Intent {
  return { ...intent, moverPins: { ...intent.moverPins, [id]: pose } };
}
```

- [ ] **Step 4: Run tests + typecheck to verify pass**

Run: `npm --prefix viewer/ run test && npm --prefix viewer/ run typecheck`
Expected: PASS (new tests green; `tsc --noEmit` clean — note that any other module building an `Intent` literal must now include `moverPins`; those are Tasks 4/5, so `typecheck` may flag them until then — if so, defer the typecheck assertion to Step 4 of Task 5 and just assert `run test` here).

- [ ] **Step 5: Commit**

```bash
git add viewer/src/interaction/intent-contract.ts viewer/src/interaction/selection.ts viewer/test/selection.test.ts
git commit -m "feat(viewer): Intent.moverPins + setMoverPin, initialIntent excludes movers (#912)"
```

---

### Task 3: `export.ts` widens `ground_objects:` to pinned movers (pure TS)

**Files:**
- Modify: `viewer/src/interaction/export.ts:27-35` (the `ground_objects` emission)
- Test: `viewer/test/export.test.ts`

**Interfaces:**
- Consumes: `Intent.moverPins` (Task 2), `Intent.groundObjectIds`, `EditorContext.catalog`.
- Produces: a `ground_objects: [...]` line whose entries are the **union** of palette-added movers (bare ids, `#910`) and pinned movers (`{ object: id, x_m, y_m, heading_deg }` mapping entries, `#912`). A mover that is both added and pinned emits **once** as a mapping entry (the pin supersedes the bare id). Byte-identical to today when `moverPins` is empty.

- [ ] **Step 1: Write the failing test**

Add to `viewer/test/export.test.ts` (import `intentToScenarioYaml`; reuse the file's existing `ctx`/`intent` builders — a `ctx.catalog` with a `placed_routed_mover` id is required):

```ts
test('a pinned mover exports a ground_objects mapping entry', () => {
  const ctx = mkCtx({ catalog: { p1: { name: 'P1', kind: 'aircraft' }, caddy: { name: 'Caddy', kind: 'placed_routed_mover' } } });
  const intent = { ...initialIntent(ctx), selectedPlaneIds: ['p1'] };
  const yaml = intentToScenarioYaml(setMoverPin(intent, 'caddy', { x: 3.5, y: 4, heading: 90 }), ctx);
  assert.match(yaml, /ground_objects: \[\{ object: caddy, x_m: 3\.5, y_m: 4\.0, heading_deg: 90\.0 \}\]/);
});

test('an added-and-pinned mover emits once, as a mapping entry (pin wins)', () => {
  const ctx = mkCtx({ catalog: { caddy: { name: 'Caddy', kind: 'placed_routed_mover' } } });
  let intent = { ...initialIntent(ctx), groundObjectIds: ['caddy'] };
  intent = setMoverPin(intent, 'caddy', { x: 1, y: 2, heading: 0 });
  const yaml = intentToScenarioYaml(intent, ctx);
  const matches = yaml.match(/caddy/g) ?? [];
  assert.strictEqual(matches.length, 1); // not both a bare id and a mapping entry
  assert.match(yaml, /\{ object: caddy, x_m: 1\.0, y_m: 2\.0, heading_deg: 0\.0 \}/);
});

test('no mover pinned + none added → no ground_objects line (byte path)', () => {
  const ctx = mkCtx({ catalog: { p1: { name: 'P1', kind: 'aircraft' } } });
  const yaml = intentToScenarioYaml({ ...initialIntent(ctx), selectedPlaneIds: ['p1'] }, ctx);
  assert.ok(!yaml.includes('ground_objects'));
});
```

(If `export.test.ts` has no `mkCtx`/`initialIntent` import yet, add the imports; keep the helper consistent with the file's existing style.)

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix viewer/ run test`
Expected: FAIL — the pinned-mover mapping entry is absent (today only bare `groundObjectIds` are emitted).

- [ ] **Step 3: Write minimal implementation**

In `viewer/src/interaction/export.ts`, replace the `ground_objects` block (lines 27-35):

```ts
  // Palette-added movers (#910, bare ids) ∪ hand-pinned movers (#912, mapping
  // entries). A fixed_obstacle needs an authored pose the offline editor can't
  // produce, so added ids are filtered to movers via the catalog. A mover that is
  // BOTH added and pinned emits once, as a mapping entry (the pin supersedes the
  // bare id). Emitted only when non-empty ⇒ an editor that adds/pins no mover is
  // byte-identical (ADR-0003).
  const addedMovers = [...intent.groundObjectIds].filter(
    (id) => ctx.catalog?.[id]?.kind === 'placed_routed_mover',
  );
  const moverIds = [...new Set([...addedMovers, ...Object.keys(intent.moverPins)])].sort();
  if (moverIds.length) {
    const entries = moverIds.map((id) => {
      const p = intent.moverPins[id];
      return p
        ? `{ object: ${id}, x_m: ${num(p.x)}, y_m: ${num(p.y)}, heading_deg: ${num(p.heading)} }`
        : id;
    });
    lines.push(`ground_objects: [${entries.join(', ')}]`);
  }
```

- [ ] **Step 4: Run tests to verify pass**

Run: `npm --prefix viewer/ run test`
Expected: PASS (new tests + the pre-existing `export.test.ts` cases — the palette-only and no-mover cases stay byte-identical).

- [ ] **Step 5: Commit**

```bash
git add viewer/src/interaction/export.ts viewer/test/export.test.ts
git commit -m "feat(viewer): export a pinned mover as a ground_objects mapping entry (#912)"
```

---

### Task 4: `editor.ts` — focus + drag a mover

**Files:**
- Modify: `viewer/src/interaction/editor.ts` (`mountEditor` opts + `idByObject`/`targets` build + `pick` + `syncControls` + `onConverted`)
- Verification: `npm --prefix viewer/ run typecheck && npm --prefix viewer/ run lint && VIEWER_OUTFILE=/tmp/viewer-scratch.js npm --prefix viewer/ run build` (no unit test — editor.ts's raycaster/DOM wiring is untested by precedent; #904 `focusAwareHex` was the pure part, already extracted; the mover branch is impure orchestration covered by build + the Task 6 headless smoke + review).

**Interfaces:**
- Consumes: `setMoverPin`/`pinAtPose` (selection.ts), `EditorContext.catalog?.[id]?.kind`, the new `opts.moverGroups`.
- Produces: `mountEditor` gains `moverGroups?: Record<string, THREE.Group>` (default `{}`). Task 5 supplies it.

- [ ] **Step 1: Add `moverGroups` to the opts + import `setMoverPin`**

In the `import { ... } from './selection.ts'` list, add `setMoverPin`.

In the `mountEditor` opts object type, add (after `groups`):

```ts
  // #912 PR B: the placed-mover Groups (from addGroundObjects), kept SEPARATE
  // from the plane `groups` so the highlight loop (aircraft-only) never repaints
  // a mover, while the raycaster + gizmo still reach it. Default {}: an offline
  // export or a mover-free scene passes none, so the mover-drag path is inert.
  moverGroups?: Record<string, THREE.Group>;
```

- [ ] **Step 2: Include mover Groups in the raycaster (pick) but NOT the highlight targets**

The `targets` loop (lines 80-91) stays as-is (iterates `opts.groups` — aircraft only — so movers keep their colour). After it, add a second loop that registers mover objects for picking only:

```ts
  const moverGroups = opts.moverGroups ?? {};
  // #912: movers are pickable (click-to-focus) + arm-able, but NOT highlight
  // targets — painting them via the emissive channel would force excluded-amber
  // over their real colour. So register their objects in idByObject only.
  for (const [id, g] of Object.entries(moverGroups)) {
    g.traverse((o) => idByObject.set(o, id));
  }
```

Update `pick`'s intersect set (line 98) to include movers:

```ts
    const hits = ray.intersectObjects(
      [...Object.values(opts.groups), ...Object.values(moverGroups)],
      true,
    );
```

- [ ] **Step 3: Enable *Fix position* for a focused mover in `syncControls`**

In `syncControls`, after `const hasPose = id !== null && id in opts.ctx.currentPoses;`, add the mover predicate and widen the fix-button gate (replace the existing `if (fixBtn) fixBtn.disabled = !active || !hasPose;` line):

```ts
    // #912: a focused placed mover is drag-pinnable even though it is not a
    // fleet_in member (isSelected is false for it). It has a currentPose (Task 1),
    // so gate the Fix-position button on hasPose + (selected aircraft OR mover).
    // Every other control stays gated on `active`, so a focused mover leaves the
    // aircraft-only priority/pin/cart controls disabled (they don't apply to it).
    const isMover = id !== null && opts.ctx.catalog?.[id]?.kind === 'placed_routed_mover';
    if (fixBtn) fixBtn.disabled = !hasPose || !(active || isMover);
```

- [ ] **Step 4: Branch `onConverted` on catalog kind**

Replace the manipulator's `onConverted` callback (lines 431-438):

```ts
      onConverted: (id, pose) => {
        if (opts.ctx.catalog?.[id]?.kind === 'placed_routed_mover') {
          // #912: a dragged mover pins a 3-field pose (no onCarts) → exported as a
          // ground_objects mapping entry, seated path-less by the PR-A loader.
          intent = setMoverPin(intent, id, {
            x: pose.x_m, y: pose.y_m, heading: pose.heading_deg,
          });
        } else {
          // Aircraft: carry onCarts from the plane's existing pin, else its pose.
          const onCarts =
            intent.mustPositions[id]?.onCarts ?? opts.ctx.currentPoses[id]?.on_carts ?? false;
          intent = pinAtPose(intent, id, pose, onCarts);
        }
        focusedId = id;
        syncControls(); // re-gate fixBtn (+ the aircraft pin fields, when aircraft)
        opts.onEdit?.(); // flag Calculate "unsolved"
      },
```

- [ ] **Step 5: Pass the merged Groups to the manipulator**

In the `createManipulator({ ... })` call, change `groups: opts.groups` to the merged map so `arm(id)`/live-follow resolve a mover Group:

```ts
      groups: { ...opts.groups, ...moverGroups }, // #912: planes ∪ movers (gizmo targets)
```

- [ ] **Step 6: Typecheck, lint, scratch-build**

Run: `npm --prefix viewer/ run typecheck && npm --prefix viewer/ run lint && VIEWER_OUTFILE=/tmp/viewer-scratch.js npm --prefix viewer/ run build`
Expected: all clean. (`typecheck` may still flag `main.ts` for not passing `moverGroups` — that's fine, it is optional — and for any remaining `Intent`-literal gaps, which Task 5 closes. If `tsc` is red only on `main.ts` call sites, proceed; Task 5 Step 4 is the clean-typecheck gate.)

- [ ] **Step 7: Commit**

```bash
git add viewer/src/interaction/editor.ts
git commit -m "feat(viewer): editor focuses + drags a placed mover into a mover pin (#912)"
```

---

### Task 5: `main.ts` + `buildWorld` — plumb mover Groups to the editor

**Files:**
- Modify: `viewer/src/main.ts` (`World` interface, `buildWorld` return, both `mountEditor` call sites in `bootSingle`)
- Verification: `npm --prefix viewer/ run typecheck && npm --prefix viewer/ run test` (no new unit — `main.ts` is orchestration, covered by build + the Task 6 smoke).

**Interfaces:**
- Consumes: `addGroundObjects(...).groups` (already computed as `goGroups` in `buildWorld`), `mountEditor`'s new `moverGroups` opt (Task 4).
- Produces: `World.goGroups: Record<string, THREE.Group>`; both editor mounts receive `moverGroups: <world>.goGroups`.

- [ ] **Step 1: Expose `goGroups` on `World`**

In the `World` interface (lines 36-43), add:

```ts
  goGroups: Record<string, THREE.Group>; // #912: placed-mover Groups for editor drag
```

In `buildWorld`, the return already destructures `goGroups` (line 68); add it to the returned object (line 94):

```ts
  return { group, groups, goGroups, labelMeshes, noseMeshes, setPathsVisible, timeline };
```

- [ ] **Step 2: Pass `moverGroups` at the initial editor mount**

In `bootSingle`, the first `mountEditor` call (line 181) — add `moverGroups`:

```ts
    let editor = mountEditor({
      groups: world.groups,
      moverGroups: world.goGroups, // #912
      renderer: stage.renderer,
      cam: stage.cam,
      ctx,
      ...editHostOpts,
    });
```

- [ ] **Step 3: Pass `moverGroups` at the reRender (Calculate) re-mount**

In the `reRender` callback's `mountEditor` (lines 199-210) — add `moverGroups: nextWorld.goGroups`:

```ts
          const nextEditor = mountEditor({
            groups: nextWorld.groups,
            moverGroups: nextWorld.goGroups, // #912
            renderer: stage.renderer,
            cam: stage.cam,
            ctx: resp.editorContext,
            initialIntent: preserved,
            scene: stage.scene,
            orbit: stage.controls,
            onEdit: () => markUnsolved(),
          });
```

- [ ] **Step 4: Typecheck + full node tests (the clean-typecheck gate)**

Run: `npm --prefix viewer/ run typecheck && npm --prefix viewer/ run test && npm --prefix viewer/ run lint`
Expected: all clean — `tsc --noEmit` now green across the whole `viewer/src` tree (every `Intent` literal carries `moverPins`; `main.ts` supplies `moverGroups`), all node units pass, eslint clean.

- [ ] **Step 5: Commit**

```bash
git add viewer/src/main.ts
git commit -m "feat(viewer): plumb placed-mover Groups into the editor mount (#912)"
```

---

### Task 6: Rebuild bundle, full gate, headless smoke, CHANGELOG

**Files:**
- Modify: `src/hangarfit/_viewer_assets/viewer.js` (rebuilt bundle)
- Modify: `CHANGELOG.md` (`[Unreleased] → Added`)
- Verification: `make test`, `npm --prefix viewer/ run build`, headless swiftshader smoke

- [ ] **Step 1: Rebuild the committed bundle**

Run: `npm --prefix viewer/ run build`
This regenerates `src/hangarfit/_viewer_assets/viewer.js` from the Task 2-5 TS. `git diff --stat src/hangarfit/_viewer_assets/viewer.js` should show it changed.

- [ ] **Step 2: Full Python + TS gate**

Run: `make test && npm --prefix viewer/ run test && npm --prefix viewer/ run typecheck && npm --prefix viewer/ run lint && ruff check src/ tests/ && mypy src/hangarfit/`
Expected: green (bulk + 8 serial canaries; node 100+; tsc/eslint/ruff/mypy clean). `test_viewer.py` (45+) and the editor-context↔intent-contract key-parity test (`test_scene.py`) stay green — `moverPins` is on `Intent`, not `EditorContext`, and `currentPoses` keeps the `CurrentPose` shape.

- [ ] **Step 3: Headless smoke — a served page renders a mover, no transform banner**

Serve a scenario that carries a placed mover (e.g. a herrenteich scenario with the Caddy, or `tests/fixtures/` equivalent). In one shell:

```bash
PYTHONPATH=$PWD/src python -m hangarfit serve <scenario-with-a-mover>.yaml --port 8799 --no-open &
```

Then screenshot the served editor headlessly:

```bash
google-chrome --headless=new --use-gl=angle --use-angle=swiftshader \
  --enable-unsafe-swiftshader --virtual-time-budget=8000 \
  --screenshot=/tmp/mover-edit.png "http://127.0.0.1:8799/"
```

Expected: the page renders the mover + planes, the *Fix position* button is present, and no `TRANSFORM CHECK FAILED` banner. (Headless can't click/drag; the full drag→Calculate round-trip is a manual test — note it in the PR as manually verified.) Kill the server after.

- [ ] **Step 4: CHANGELOG entry**

Add to `CHANGELOG.md` under `## [Unreleased]` → `### Added`, after the existing `Mover pin (hand-place a car/trailer)` (#912) bullet:

```markdown
- **Editor drag-to-fix for movers** — with `hangarfit serve`, the `--edit` viewer can now drag a placed car/trailer the same way it drags a plane: focus a mover, hit **Fix position**, drag it on the hangar floor + set its heading, and on drop the pose round-trips through `POST /convert` (Python owns the transform inverse, ADR-0002) into a `ground_objects: [{object, x_m, y_m, heading_deg}]` pin, then **Calculate** re-solves with it seated path-less. The mover mesh slides live during the drag (translation only). A focused mover shows no fleet highlight (the gizmo is its cue) and its pin has no editable text fields; an unpinned pre-existing mover is not re-emitted on a served re-solve (candidate follow-ups). Offline exports are unchanged. Completes #912 (editor half; the backend shipped separately). (#912)
```

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/_viewer_assets/viewer.js CHANGELOG.md
git commit -m "build(viewer): rebuild bundle + CHANGELOG for mover drag-to-fix (#912)"
```

---

## Self-Review

**Spec coverage (§4 + §7):**
- §4.1 `currentPoses` extended to movers → **Task 1**. ✓
- §4.2 `Intent.moverPins` → **Task 2**; `onConverted` branch + Fix-position enablement → **Task 4**; `POST /convert` reused verbatim (no task — unchanged). ✓
- §4.3 `export.ts` mapping-entry widening → **Task 3**. ✓
- §5 data-flow round-trip: currentPoses seed (T1) → gizmo arm via merged Groups (T4/T5) → /convert (unchanged) → moverPins (T2/T4) → export (T3) → PR-A loader (merged) → reRender (T5). ✓
- §7 tests: editor-context includes a placed mover + `world_yaw_rad` (T1); key-parity holds (T6 gate — `moverPins` is Intent-side); `moverPins` export → mapping entry (T3); byte-identical when none pinned (T3); headless smoke (T6). ✓
- §8 PR-B review guards: `code-reviewer`, `scene-schema-guard` (viewer.py editor-context + viewer.js) — run in the review arc after Task 6.

**Under-specified seams the plan adds (found in recon, not in §4):**
- Mover Groups reach the editor (`World.goGroups` + `moverGroups` opt) → **Tasks 4/5**.
- `initialIntent` must exclude movers from `selectedPlaneIds` (ripple of §4.1) → **Task 2**.
- Highlight `targets` stay aircraft-only so movers keep their colour → **Task 4**.
- Timeline hold-gate already covers movers (verified — no task needed).

**Type consistency:** `moverPins: Record<string, { x; y; heading }>` — same shape in intent-contract.ts (T2), `setMoverPin` (T2), `onConverted` (T4), and `export.ts` (T3: reads `p.x`/`p.y`/`p.heading`). `moverGroups: Record<string, THREE.Group>` — same in `mountEditor` opts (T4) and both call sites (T5). `World.goGroups` (T5) ← `addGroundObjects(...).groups` (existing). ✓

**Placeholder scan:** the `<mover_x>` / `_layout_with_placed_mover()` tokens in Task 1's test are fixture-dependent — the implementer fills them from the chosen mover-bearing Layout fixture (the one concrete unknown; every code step is complete). No other placeholders.
