# `interaction/` — the editor extension seam (inert)

This directory is the **extension seam** for the future interactive plane-placement
editor (deferred — issue #442, the Stage 2 of the roadmap in ADR-0020 / the spec).

**It is intentionally inert.** Right now it holds this `README.md` and **no `.ts`
files**, so esbuild bundles nothing from here and the committed
`src/hangarfit/_viewer_assets/viewer.js` is byte-unchanged. The `viewer-build-drift`
CI guard stays green until a real module lands with #442.

## The contract this seam will implement (#442)

The viewer is, and stays, a **thin read-only consumer** of `scene/v2` whose geometry
is computed in Python (ADR-0002/0017/0020). The editor does **not** change that: it
captures user **intent** and hands it back to the Python solver. When #442 lands, an
`interaction/` module will:

1. **Read** the typed `scene-contract.ts` models to know what is on screen.
2. **Build an intent object** mirroring `Scenario.constraints`:

   ```ts
   interface Intent {
     selectedPlaneIds: string[];
     priorities: Record<string, number>;            // soft PlaneConstraint.priority (#441)
     mustPositions: Record<string, { x: number; y: number; heading: number }>; // hard pin
   }
   ```

   "must positions" map to `PlaneConstraint.pin`; "priorities" to the soft
   `PlaneConstraint.priority` groundwork (#441). `intent-contract.ts` becomes the
   typed mirror of this artifact.
3. **Export a `Scenario` / constraints YAML** the Python solver consumes. The user
   re-runs `hangarfit solve` and re-opens the viewer (the Stage-2 round-trip file);
   Stage 3 (#445) delivers the same intent object over a `hangarfit serve` localhost
   API instead of a file. **Python stays the solver authority** in every stage.

## The one hard rule

A module in `interaction/` **must never import `affine.ts` or `anchors.ts`** to
re-derive geometry. It captures intent and serializes it; the determinant-−1
transform stays owned by tested Python (`geometry.local_to_world`), and the browser
never re-derives authoritative geometry (ADR-0002/0020). The runtime `checkAnchors()`
self-check remains the cross-language backstop.
