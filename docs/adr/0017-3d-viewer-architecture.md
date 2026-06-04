# ADR-0017: 3D viewer — a `scene/v1` JSON seam fed to a self-contained offline Three.js HTML, with the transform owned by Python

- **Status:** Accepted

- **Date:** 2026-06-03
- **Deciders:** Patrick Kuhn (DocGerd)

> **v0.10.0 amendment (milestone #30, "viewer appeal").** The core decision —
> the `scene/v1` seam and the Python-owned determinant-−1 transform — is
> unchanged. Additive, render-only extensions shipped on top: gear + tow carts
> (#399; `scene/v1` gains per-plane `wheels[]`/`on_carts` + a `gear_anchors`
> oracle, still absent from the collision model per
> [ADR-0015](0015-wheels-not-in-collision-model.md)); soft contact shadows,
> kind-based materials, billboarded id labels (`CanvasTexture` via safe
> `fillText`, never `innerHTML`) and nose-cone arrows behind a `labels` toggle
> (#400); and a "PLACEHOLDER DATA" honesty banner + valid-layout readouts driven
> by the new read-only `metrics` module (#401). Layout-mode `view` also gained a
> small deterministic *global* tow-expansion cap so an un-routable layout
> degrades to a static render in seconds (#398) — an expansion count, **not** a
> wall-clock deadline ([ADR-0003](0003-rr-mc-solver-algorithm.md)). Schema-level
> record of the new fields: [`scene-v1-schema.md`](../architecture/scene-v1-schema.md).

## Context & Problem Statement

The 2D top-down PNG (`visualize.py`) cannot show the vertical clearances the
collision checker already reasons about: a high wing overhanging another plane's
tail is *valid* (z-disjoint) but reads in plan view identically to an invalid
cockpit overlap. The user asked for a **full 3D** view of the hangar and each
aircraft (box models) and the **exact tow paths** replayable as a scrubbable
whole-fill timeline. This ADR records how that rendering path is structured, how
it talks to the core, and — the load-bearing decision — **where the
determinant-−1 plane-local→world transform (ADR-0002) is computed**, since
re-deriving it in JavaScript would re-open the project's signature off-by-handedness
trap in an untested language.

## Decision Drivers

- **Don't re-open the det-−1 trap.** The transform must be computed exactly once,
  in the tested Python definition (`geometry.local_to_world`), never re-derived.
- **Decouple from the core.** The solver / collision checker / loader / models
  must not learn about rendering tech.
- **Offline, shareable, zero-setup.** A flying-club laptop with no internet and no
  Python must be able to open the result; emailing one file should work.
- **No build toolchain.** The repo is Python-only with a hash-pinned, OpenSSF-scored
  supply chain; an npm/Vite/node_modules surface is disproportionate.
- **Interactivity.** Orbit camera + a timeline slider (scrub / play / step).

## Considered Options

1. **`scene/v1` JSON seam → self-contained offline Three.js HTML; Python emits
   per-frame affine matrices, the viewer only consumes them** (chosen).
2. **Three.js viewer that re-implements `local_to_world` in JavaScript** (emit
   plane-local geometry + per-frame `(x, y, heading)`, transform in JS).
3. **Python-native 3D** (matplotlib-3D / PyVista / vedo): an interactive VTK
   window and/or an exported MP4.
4. **A full npm/Vite web app** with a bundler and `node_modules`.

## Decision Outcome

**Chosen option: Option 1.** A pure builder (`scene.py`) turns a `Layout`
(+ optional `MovesPlan`, `CheckResult`) into a JSON-serializable
`hangarfit.scene/v1` dict; `viewer.py` inlines that scene plus the vendored
Three.js into **one** self-contained HTML; `_viewer_assets/viewer.js` is a thin
consumer. The deciding factor is the transform: **`scene.py` emits, per animation
frame per plane, the explicit 2×3 world affine `[a, b, tx, c, d, ty]`** (with
`a=sin h, b=cos h, tx=x, c=cos h, d=−sin h, ty=y`, computed from the same formula
as `geometry.local_to_world`), and the viewer drops it straight into a
`THREE.Matrix4` applied to a statically-built plane-local `Group`. The reflection
(det −1) rides inside the matrix; `material.side = THREE.DoubleSide` keeps the
reflected winding visible. The viewer does **no transform math**.

Supporting choices:

- **Offline via a `data:` import-map.** ES modules cannot be `fetch`-ed from a
  `file://` page (CORS), so the HTML maps `three` / its OrbitControls addon to
  `data:text/javascript;base64,…` URLs of the vendored sources, and the scene is
  inlined as a JSON `<script>` (no `fetch`). Result: a double-clickable, fully
  offline file.
- **Vendored Three.js as package data.** The pinned `three.module.js` +
  `OrbitControls.js` (r160, MIT) live under `src/hangarfit/_viewer_assets/three/`
  — *not* a repo-root `vendor/` — so they ship in the wheel and are reachable via
  `importlib.resources` after a plain `pip install`. SHA-256 + provenance are
  recorded in their `VENDOR.md`.
- **+Z-up viewer convention.** Three.js defaults to +Y up; the viewer sets
  `camera.up = (0,0,1)` and builds the scene in hangarfit's x-right / y-deep /
  z-up world so the affine's z-row is identity and box height runs along world up.
- **Cross-language guard.** `scene.py` emits, per plane at its final placement,
  the world box corners from the `aircraft_parts_world` oracle (`anchors`); the
  viewer recomputes them from its geometry + the final affine and **fails loud**
  (on-page banner) if they disagree past 1e-6.

### Why not Option 2?

Re-implementing `local_to_world` in JavaScript duplicates the exact map ADR-0002
warns is a rotation *composed with a reflection*. A sign slip there is invisible
until a non-axis-aligned heading renders mirror-imaged, and JS cannot be exercised
by the pytest suite — so the regression that the project's `test_geometry` 45°
canary exists to catch would have no equivalent. Option 1 keeps the formula in one
tested place and reduces the JS to matrix application.

### Why not Option 3?

Python-native 3D keeps everything in Python but trades away exactly what the
feature is for: a smooth interactive scrubber and a single shareable file.
matplotlib-3D has no real depth-sorted box rendering; PyVista/vedo pull in VTK
(tens of MB) and need a display server (hostile to the headless/CI ethos), with
clunky in-window sliders; an MP4 export is a video, not the requested scrub. None
let a non-Python club member open the result.

### Why not Option 4?

A bundler + `node_modules` is a large, churning supply-chain surface that clashes
with the repo's hash-pinned, OpenSSF-scored, single-lockfile discipline, and adds
a build step for no PoC benefit. Hand-written ESM + one vendored, hash-pinned
dependency stays inside the existing posture.

## Consequences

### Positive

- The det-−1 transform is computed once, in tested Python; the affine-vs-oracle
  parity test pins it and the in-browser anchor check backstops it.
- The core is untouched; `scene`/`viewer` are leaf consumers like `visualize`.
- One double-clickable, fully-offline HTML — the HTML analogue of the PNG artifact.
- The `scene/v1` contract lets any future viewer (or a different renderer) consume
  the same data without re-reading hangarfit internals.

### Negative

- ~1.2 MB of vendored Three.js is committed and rides in every wheel; base64 in the
  `data:` import-map inflates the HTML to ~1.8 MB. Acceptable for an offline artifact.
- `viewer.js` is not exercised by pytest (no JS runtime); its correctness rests on
  the Python affine test + the in-browser anchor assert + manual verification.
- A reflected (det −1) group matrix needs `DoubleSide` materials and flips face
  winding; lighting is via recomputed normals. Fine for flat-shaded boxes.

### Neutral

- A `view`-specific `--tow-max-expansions` mirrors `solve`'s knob so un-routable
  layouts degrade to a static scene quickly rather than burning the full search.

## Compliance

- `tests/test_scene.py::test_affine_matches_oracle_with_angle_and_heading` and
  `::test_affine_matches_oracle_across_headings` pin the affine against
  `aircraft_parts_world` (incl. a 45° heading + a synthetic nonzero-`angle_deg`
  part) — the Python-side equivalent of the geometry 45° canary.
- The viewer's load-time `anchors` self-check fails loud (on-page banner) if the JS
  matrix path diverges from the Python oracle.
- `tests/test_viewer.py` asserts the HTML is self-contained and carries **no**
  `http(s)://` references (the offline guarantee) and escapes `<` in the embedded
  scene JSON (no `</script>` breakout).
- `src/hangarfit/_viewer_assets/three/VENDOR.md` records the pinned version, source
  URLs, and SHA-256 of each vendored file.

## More Information

- Related ADRs: [ADR-0002](0002-determinant-minus-one-transform.md) (the transform
  this ADR refuses to re-derive in JS), [ADR-0007](0007-tow-path-planner-v1-scope.md)
  / [ADR-0010](0010-reeds-shepp-motion-model.md) (the tow paths the timeline replays),
  [ADR-0003](0003-rr-mc-solver-algorithm.md) (determinism spirit the scene builder
  preserves).
- Related specs: [`docs/superpowers/specs/2026-06-03-3d-viewer-design.md`](../superpowers/specs/2026-06-03-3d-viewer-design.md);
  schema reference [`docs/architecture/scene-v1-schema.md`](../architecture/scene-v1-schema.md).
- Related issues / PRs: #392 (epic), #388 (scene), #389 (viewer), #390 (CLI), #391 (docs).
- External references: Three.js (https://threejs.org), import-maps spec, Reeds & Shepp (1990).
