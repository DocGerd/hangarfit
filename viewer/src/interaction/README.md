# `interaction/` — the editor extension seam (active, #442)

This directory is the **extension seam** for the interactive plane-placement
editor (`hangarfit view --edit`, epic [#442](https://github.com/DocGerd/hangarfit/issues/442),
Stage 2 of the roadmap in [ADR-0020](../../../docs/adr/0020-viewer-typescript-architecture.md)).
Its contract is recorded in
[ADR-0029](../../../docs/adr/0029-editor-intent-artifact-contract.md).

**The seam is now active.** [ADR-0020](../../../docs/adr/0020-viewer-typescript-architecture.md)
reserved this directory (previously inert — README-only, so esbuild bundled
nothing and the committed `src/hangarfit/_viewer_assets/viewer.js` was
byte-unchanged); ADR-0029 activates it. The modules below land across #442 in
small, independently reviewable chunks — the bundle stays byte-identical until a
module is actually imported by `main.ts` (the `viewer-build-drift` guard tracks
that hand-off), so Chunk 1's pure modules ship with an **unchanged** `viewer.js`
and only Chunk 2's `main.ts` wiring rebuilds it.

## What the editor does

The viewer is, and stays, a **thin read-only consumer** of `scene/v2` whose
geometry is computed in Python (ADR-0002/0017/0020). The editor does **not**
change that: it captures user **intent** and hands it back to the Python solver
as a loader-valid `Scenario` YAML (the Stage-2 round-trip file). The user re-runs
`hangarfit solve` and re-opens the viewer. Stage 3 ([#445](https://github.com/DocGerd/hangarfit/issues/445))
later delivers the same intent object over a `hangarfit serve` localhost API
instead of a file. **Python stays the solver authority in every stage.**

## Module map

| Module | Purity | Responsibility | Lands |
|---|---|---|---|
| `intent-contract.ts` | types | `Intent`, `MustPosition`, `CurrentPose`, `EditorContext` — the typed mirror of the exported artifact. | Chunk 1 |
| `selection.ts` | **pure** | Selection-state machine (toggle/priority/pin-at-current/edit); pose lookup reads `EditorContext.currentPoses` scalars. No THREE/DOM, no affine math. | Chunk 1 |
| `export.ts` | **pure** | `(Intent, EditorContext) → Scenario-YAML string`; enforces the ADR-0029 serializer invariants. No THREE/DOM. | Chunk 1 |
| `editor.ts` | impure edge | THREE.Raycaster over `planes` groups, selection highlight, `controls.enabled` gating, HUD wiring, `Blob` + `<a download>` export. Reads `scene-contract.ts`. | Chunks 2–3 |

The `Intent` the browser builds mirrors `Scenario.constraints`:

```ts
interface Intent {
  selectedPlaneIds: string[];                    // → Scenario.fleet_in (∪ maintenance plane)
  priorities: Record<string, number>;            // → PlaneConstraint.priority (soft, #441)
  mustPositions: Record<string, MustPosition>;   // → PlaneConstraint.pin (hard)
}
interface MustPosition { x: number; y: number; heading: number; onCarts: boolean; }
```

The non-constraint scaffold the browser cannot recover from `scene/v2` (fleet /
hangar paths, the maintenance passthrough, and each plane's **current pose as
scalars**) is injected by `viewer.py` as an `editor-context` blob
(`hangarfit.editor-context/v1`) — a viewer-HTML-level artifact layered over an
untouched `scene/v2` document, exactly like the `viewer-compare/v1` wrapper. See
[ADR-0029](../../../docs/adr/0029-editor-intent-artifact-contract.md) for the full
`Intent → Scenario` mapping and serializer invariants.

## The one hard rule

A module in `interaction/` **must never import `affine.ts` or `anchors.ts`** to
re-derive geometry, and must never touch the `final_poses` affines for pose
purposes. A "must-position" pin is a **scalar copy** of the Python-emitted
`currentPoses[id]`, edited via number fields — pure data entry, never a composed
transform. The determinant-−1 transform stays owned by tested Python
(`geometry.local_to_world`); the browser never re-derives authoritative geometry
(ADR-0002/0020/0029). The runtime `checkAnchors()` self-check remains the
cross-language backstop.
