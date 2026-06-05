# `viewer/` — dev/CI-only TypeScript toolchain for the 3D viewer

This top-level directory is the **source project** for the `hangarfit view` 3D
viewer. It is **not** part of the Python package, is **never** imported, and is
**never** shipped in the wheel. esbuild bundles `src/*.ts` into the one committed
artifact the package actually ships:

```
viewer/src/*.ts  --esbuild-->  ../src/hangarfit/_viewer_assets/viewer.js  (committed, shipped)
```

See [ADR-0020](../docs/adr/0020-viewer-typescript-architecture.md) and the
[design spec](../docs/superpowers/specs/2026-06-04-viewer-typescript-architecture-design.md)
for the full rationale. Key invariants:

- **Node is dev/CI-only.** `pip install hangarfit` and `python -m build` invoke no
  npm — they consume the committed `viewer.js`. You only need Node to *change* the
  viewer.
- **The committed bundle is the source of truth at runtime.** The
  `viewer-build-drift` CI guard (issue #438) rebuilds it and fails on any diff, so
  the shipped artifact can never drift from the TS source.
- **Three.js stays vendored & external.** The bare `three` / OrbitControls imports
  are left in the bundle and resolved by `viewer.py`'s `data:` import-map over the
  vendored r160 sources; `viewer.py` is unchanged. `@types/three` is pinned to
  `0.160.x` to match.
- **The transform stays in Python** (ADR-0002/0017). The viewer applies
  Python-emitted affines and re-derives no geometry.

## Commands (run with Node pinned via `.nvmrc`)

```bash
npm --prefix viewer/ ci          # install from the committed lockfile (CI uses this)
npm --prefix viewer/ run build   # rebuild ../src/hangarfit/_viewer_assets/viewer.js
npm --prefix viewer/ run typecheck   # tsc --noEmit (strict)
npm --prefix viewer/ run lint    # eslint
npm --prefix viewer/ run test    # node --test (pure units; lands with #439/#440)
```

After editing any `src/*.ts`, **rebuild and commit `viewer.js`** in the same change,
or the drift guard will fail.

### Verifying a build without clobbering the committed bundle

`esbuild.config.mjs` honours `VIEWER_OUTFILE` to redirect the output (e.g. for the
reproducibility check or a headless render test), leaving the committed `viewer.js`
untouched:

```bash
VIEWER_OUTFILE=/tmp/viewer-scratch.js npm --prefix viewer/ run build
```

## Status

Scaffold only (issue #437): the toolchain + a placeholder `src/main.ts`. The real
port of `viewer.js` into typed modules is the atomic #439 PR.
