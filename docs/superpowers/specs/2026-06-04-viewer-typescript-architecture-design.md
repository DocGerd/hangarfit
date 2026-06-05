# Viewer TypeScript migration — modular, typed, editor-ready (epic #436)

- **Date:** 2026-06-04
- **Epic:** #436. **Issues:** #437 (toolchain scaffold), #438 (CI guard), #439
  (port viewer.js → TS), #440 (typed scene-contract + interaction seam + node tests),
  #441 (Python `PlaneConstraint.priority` groundwork), #442 (deferred interactive
  editor). Follows #423 (`typescript-lsp` + `jsconfig.json`); subsumes #433.
- **Status:** design record authored before implementation. Decision basis recorded in
  [ADR-0020](../../adr/0020-viewer-typescript-architecture.md). User-approved the
  shaping choices on 2026-06-04 (build approach: design for the future app, not the
  current thin-renderer constraints; this pass is design + issues only; the interactive
  feature is tracked + groundwork).

## Problem

The `hangarfit view` 3D viewer is one hand-written, untyped, ~546-line ES module
(`src/hangarfit/_viewer_assets/viewer.js`) — the repo's only application JavaScript and
its only code with **no static analysis in CI**. [ADR-0017](../../adr/0017-3d-viewer-architecture.md)
kept it a *thin read-only renderer* with no build toolchain, which fit "render what
Python computed."

The user's outlook changes the premise: the JS app should grow into an **interactive
editor** — select which planes go into the hangar, and assign **priorities** and **"must
positions"** to certain aircraft. Untyped single-file growth toward that is risk in the
one language pytest cannot reach. We want a typed, modular, extendable foundation —
**without** giving up the offline single-file deliverable, output determinism, the
OpenSSF supply-chain posture, or the Python-owned transform.

## What already supports this (no core changes needed)

- **The seam is already a clean data contract.** `scene.py` emits a deterministic
  `hangarfit.scene/v1` dict; the viewer is a pure consumer. Typing it is additive.
- **The transform already lives in one tested place.** `geometry.local_to_world` is the
  single det-−1 definition; `scene.py` emits per-frame affines `[a,b,tx,c,d,ty]`. The TS
  viewer keeps applying them and re-derives nothing (ADR-0002).
- **The constraint machinery for "must positions" already exists.** `PlaneConstraint.pin`
  (models.py) hard-fixes a plane's `(x,y,heading)`; `Scenario.constraints` validates it.
  Only a *soft* `priority` is missing (#441).
- **`viewer.js` is already internally modular.** Its comment-delimited sections
  (renderer, affine, hangar, gear, planes, labels, anchors, timeline, HUD) map 1:1 onto
  TS modules, so the port is mechanical and reviewable section-by-section.

## Goals

- Real **TypeScript**, modular under `src/hangarfit/_viewer_assets/src/*.ts`, typed
  against the `scene/v1` contract and `@types/three`.
- A **dev/CI-only** Node toolchain (esbuild + tsc + eslint) that **never** enters
  `pip install`, the wheel build, or the runtime — the committed bundle is the artifact.
- **Reproducible** output, guarded in CI like the existing hash-pinned lockfiles.
- An **extension seam** (typed `scene-contract.ts` + an inert `interaction/` namespace)
  the future editor plugs into.
- Every ADR-0017 invariant preserved: offline single file, no JS transform math, the
  fail-loud anchor self-check, deterministic assembly.

## Non-goals (this epic)

- Building the interactive editor (selection / priorities / must-positions UI) — that is
  the deferred #442; this epic only lays its seam.
- Re-deriving any transform / collision / solver math in JavaScript.
- A runtime Node server, client-side solving, or shipping `node_modules` in the wheel.
- Byte-identity with the *old* `viewer.js` (a bundled TS port differs in bytes by
  construction — see §Verification).

## Decisions (basis in ADR-0020)

1. **Build approach:** esbuild bundles modular `*.ts` → a single **committed**
   `viewer.js`, emitted **unminified**, `target: es2020`, for a legible diff. Node is
   dev/CI-only.
2. **three:** recommended — promote to a **pinned npm dependency bundled into the
   output** (drops the base64 `data:` import-map); **fallback** — keep vendored &
   external with the import-map unchanged. Decided concretely in #437; both preserve the
   offline single-file deliverable.
3. **Types:** `@types/three` pinned to `0.160.x` (matches vendored three r160), types-only.
4. **Determinism:** redefined as *reproducibility of the committed bundle*, enforced by a
   `viewer-build-drift` CI job (rebuild + sha256/diff), mirroring `lockfile-drift`.
5. **Transform ownership:** unchanged — Python owns it; the editor exports a `Scenario`
   artifact for the solver.

## Architecture — module map

TS sources excluded from the wheel; only the emitted `viewer.js` ships.

```
src/hangarfit/_viewer_assets/
  viewer.js                    # COMMITTED esbuild artifact — the only shipped JS
  src/
    main.ts                    # entry: init in viewer.js's exact current order
    scene-contract.ts          # typed scene/v1 (the extension seam) — pure types
    brand-contract.ts          # typed BRAND token blob (#419)
    dom.ts                      # element lookups, banner(), readouts wiring (#401)
    affine.ts                   # affineMatrix()->Matrix4, applyAffine() — PURE, tested
    anchors.ts                  # boxCornersLocal() + oracle-compare — PURE; banner at edge
    renderer.ts                 # renderer/scene/camera/lights/OrbitControls, home(), resize
    hangar.ts                   # floor/grid/walls(door split)/bay + walls toggle
    gear.ts                     # wheels/legs/pallets + render constants
    planes.ts                   # boxMaterial(), per-plane Group loop, legend chips
    labels.ts                   # makeLabel() (CanvasTexture, safe fillText), nose, toggle
    timeline.ts                 # segByPlane, affineAt() — PURE, tested; applyTime()
    hud.ts                      # play/scrub/step/speed wiring + render loop
    interaction/                # FUTURE seam — README.md only now (no .ts; bundle unchanged)
  three/                        # vendored r160 (retained if three stays external)
  package.json, package-lock.json, tsconfig.json, esbuild.config.mjs, eslint config
```

**Pure, testable units** (the high-value extraction, covered by `node --test` in #440):
`affine.ts` (the only math the viewer does), `anchors.ts` (the cross-language oracle
compare; banner side-effect stays at the thin edge so the comparison is testable without
a DOM), `timeline.ts` (`affineAt` is pure given `(segByPlane, finals, t)`).

**The `checkAnchors()` backstop** (viewer.js 389–438) is ported behavior-identically: it
recomputes world corners + wheel positions from geometry + the final affine and **fails
loud on the `#banner`** (never throws) if they diverge from the Python oracle past 1e-6.
It is the only thing pinning the JS matrix path to Python and is non-negotiable.

## The future-editor seam (deferred — #442)

The `interaction/` namespace documents the contract now and stays inert (README-only) so
the bundle is unchanged. When #442 lands, an `interaction/` module:

- reads `scene-contract` types;
- builds an **intent object** mirroring `Scenario.constraints`:
  `{ selectedPlaneIds, priorities: Record<id, number>, mustPositions: Record<id, {x,y,heading}> }`
  — "must positions" → `PlaneConstraint.pin` (hard), "priorities" → the new soft
  `PlaneConstraint.priority` (#441);
- **exports a `Scenario`/constraints YAML** the Python solver consumes (round-trip); the
  user re-runs `hangarfit solve` and a fresh viewer renders the result;
- **must never import `affine.ts`/`anchors.ts`** to re-derive geometry (ADR-0002/0020):
  Python remains the authority/solver.

`intent-contract.ts` becomes the typed mirror of that artifact when #442 lands.

## Build & CI

- **#437 scaffold:** `package.json` (devDeps only), committed integrity-pinned
  `package-lock.json` (`npm ci`), strict `tsconfig.json`, `esbuild.config.mjs`
  (unminified, es2020), eslint, `@types/three@0.160.x`, `.gitignore node_modules`. A
  packaging assertion proves `src/*.ts` / node artifacts stay out of the sdist/wheel.
- **#438 CI** (new Node job, pinned `setup-node`): `viewer-build-drift` (rebuild + diff
  the committed `viewer.js`, `::error::` on drift), `tsc --noEmit`, eslint, `node --test`,
  the three↔types skew guard. Python-3.12 jobs untouched. Recommend making
  `viewer-build-drift` + `tsc` required checks on `develop`.

## Verification

- **Reproducibility:** `viewer-build-drift` is the determinism pin — `npm ci && npm run
  build` must reproduce the committed `viewer.js` byte-for-byte (unminified + pinned
  toolchain).
- **Semantic equivalence to the old viewer** (NOT a byte-diff — the bundle's bytes
  necessarily differ): the documented headless check renders a fixture and asserts the
  `#banner` TRANSFORM CHECK stays **hidden**:
  ```
  google-chrome --headless=new --use-gl=angle --use-angle=swiftshader \
    --enable-unsafe-swiftshader --virtual-time-budget=8000 \
    --screenshot=out.png "file://$PWD/out.html"
  ```
  Optionally grep `--dump-dom` for the banner text as a hard gate. (swiftshader WebGL
  "ReadPixels stall" / dbus/UPower lines are noise.)
- **Assembly determinism:** extend `tests/test_viewer.py` with a golden-HTML pin for a
  fixed fixture, plus the unchanged offline + `</script>`-escape asserts.
- **Pure units:** `node --test` covers `affine` (matrix algebra), `anchors`
  (oracle-compare incl. structural-mismatch banners), `timeline` (hidden→animating→
  parked) — coverage pytest cannot reach.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Supply-chain surface vs ADR-0017 | dev/CI-only; committed artifact; `npm ci` + pinned lockfile; minimal pinned devDeps; never in wheel/install path; ADR-0020 distinguishes this from ADR-0017's rejected *runtime* node app. |
| esbuild output drift across versions/machines | exact-pin esbuild in the lockfile; unminified + fixed target → byte-stable per version; `viewer-build-drift` is the backstop (drift = guard failure, not silent change). |
| Node leaking into the install path | wheel ships only the committed `viewer.js` (package-data `*.js` top-level glob excludes `src/`); build-system untouched; packaging assertion. |
| `@types/three` skew vs three r160 | pin `@types/three@0.160.x`; CI skew guard; the VENDOR.md refresh procedure bumps types in lockstep. |
| JS untestable by pytest | extract pure units + `node --test`; in-browser `checkAnchors` + headless banner-grep as integration backstop; `test_scene.py` still pins the producer side. |
| Half-ported intermediate states | the port (#439) is one atomic, behavior-neutral PR committing `viewer.js` + `src/*.ts` together. |

## Impact map

- **New (dev-only):** `_viewer_assets/src/*.ts`, `package.json`, `package-lock.json`,
  `tsconfig.json`, `esbuild.config.mjs`, eslint config; `.github/workflows/ci.yml` Node job.
- **Changed:** `_viewer_assets/viewer.js` (becomes the build artifact); possibly
  `viewer.py` (only if three moves to bundled — the import-map is then dropped);
  `tests/test_viewer.py` (golden pin); `CLAUDE.md` + `.claude/README.md` (build commands);
  `pyproject.toml` (confirm package-data excludes TS/node); `VENDOR.md` (types-lockstep note).
- **Deferred:** `models.py`/`solver.py`/`loader.py` for `priority` (#441);
  `interaction/*.ts` for the editor (#442).
