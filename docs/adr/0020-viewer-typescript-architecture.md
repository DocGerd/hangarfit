# ADR-0020: The viewer is a typed, modular TypeScript application built by a dev-only toolchain; the Python-owned transform is retained

- **Status:** Proposed
  <!-- Proposed at PR-open; Accepted at PR-merge. Supersedes ADR-0017's
       "No build toolchain" / thin-renderer sub-decision (see below). -->

- **Date:** 2026-06-04
- **Deciders:** Patrick Kuhn (DocGerd)

> **Scope of this ADR.** It revisits exactly **one** part of
> [ADR-0017](0017-3d-viewer-architecture.md): the "**No build toolchain**" decision
> driver and the *thin read-only renderer* posture that flowed from it. ADR-0017's
> **load-bearing correctness decision — the determinant-−1 plane-local→world
> transform is owned by Python and never re-derived in JavaScript (ADR-0002) — is
> reaffirmed unchanged**, as are the `scene/v1` seam and the single offline HTML
> deliverable. ADR-0017 is updated to *Accepted (build-toolchain decision
> superseded by ADR-0020)*.

## Context & Problem Statement

`hangarfit view` ships its 3D viewer as one hand-written, untyped, ~546-line ES
module (`src/hangarfit/_viewer_assets/viewer.js`) — the only application JavaScript
in an otherwise Python-only repo, with **zero static analysis in CI** (ruff is
Python-only; the `typescript-lsp`/`jsconfig.json` from #423 is editor-only). ADR-0017
deliberately rejected a build toolchain when the viewer was a *render-only* artifact.
That premise is changing: the user's outlook is an **interactive editor** — let a user
select which planes go into the hangar and assign **priorities** and **"must
positions"** to certain aircraft — and, ultimately, a **full frontend that triggers the
solve itself** (a local `hangarfit serve` backend; Python stays the solver/authority — see
the spec's Roadmap and #445). Untyped, single-file growth toward that is risk concentrated
in the repo's only untested language. The question this ADR answers: **how
do we make the viewer typed, modular, and extendable for that future without losing the
offline single-file deliverable, the build/output determinism, the OpenSSF supply-chain
posture, or the Python-owned transform?**

## Decision Drivers

- **Type safety over the `scene/v1` contract** — catch shape errors at dev/CI time,
  not in a late headless screenshot.
- **Modular, extendable structure** — a foundation an interaction layer can plug into,
  not a single-file monolith.
- **Preserve every ADR-0017 invariant that still holds** — Python owns the det-−1
  transform (ADR-0002); the viewer does no transform math; one self-contained offline
  HTML; deterministic output; fail-loud in-browser anchor self-check.
- **Keep the supply-chain surface out of the install/runtime path** — a flying-club
  laptop and `pip install` must stay Node-free; the OpenSSF-scored, hash-pinned
  discipline must extend to any new toolchain rather than be breached by it.

## Considered Options

1. **A dev-only TypeScript toolchain (esbuild + tsc + eslint) that bundles modular
   `*.ts` into a single, committed `viewer.js` artifact** (chosen). Node is required
   only to *change* the viewer (dev/CI); the wheel and `pip install` consume the
   committed bundle and never invoke npm.
2. **`tsc`-only, multi-module, no bundler** — emit one `.js` per module and have
   `viewer.py` resolve each via additional `data:`-URL import-map entries.
3. **No-npm, JSDoc-typed plain-JS modules** — keep hand-written `.js`, add `// @ts-check`
   + JSDoc typedefs, type-check with the existing editor LSP.
4. **Vite (+ `vite-plugin-singlefile`)** — a full dev server / HMR toolchain that also
   inlines to one offline HTML.
0. **Status quo** — keep the single untyped `viewer.js`.

A cross-cutting sub-decision under the chosen option is **where the Node project lives**:
nested inside the Python package at `src/hangarfit/_viewer_assets/src/` vs a **separate
top-level `viewer/`** directory that only *emits* the artifact into the package (see the
first Supporting choice).

## Decision Outcome

**Chosen option: Option 1**, because it is the only option that delivers *real
TypeScript + a shipped-modular, editor-ready structure* while keeping the **install and
runtime paths Node-free**: the toolchain is a dev/CI concern, the distributed artifact is
a single committed `viewer.js`, and determinism becomes *reproducibility of that
artifact*, enforced exactly like the repo's existing lockfile-drift guards.

Supporting choices:

- **Top-level `viewer/` project; the bundle is emitted *into* the package.** The whole
  Node project — `package.json`, `package-lock.json`, `tsconfig.json`,
  `esbuild.config.mjs`, eslint config, `node_modules/` — lives in a **new top-level
  `viewer/` directory, *outside* the importable Python package**. esbuild emits the one
  committed artifact to `outfile: ../src/hangarfit/_viewer_assets/viewer.js`, exactly where
  `viewer.py` already reads it via `importlib.resources`. This keeps the declarative
  `[tool.setuptools.packages.find]` (`where = ["src"]`) discovering exactly
  `{hangarfit, hangarfit._viewer_assets, hangarfit._viewer_assets.three}` with **zero**
  packaging changes and makes wheel /
  discovery / ruff / mypy / editable-install hygiene **structural rather than
  discipline-dependent** — a nested `_viewer_assets/src/` would expose
  `hangarfit._viewer_assets.src` as a PEP-420 namespace subpackage and let any future
  `*.js`→`**/*.js` glob ship `node_modules` + the TS sources into the wheel. This mirrors
  how comparable Python-packages-shipping-a-JS-build lay out (ipywidgets, JupyterLab
  extension cookiecutter, Streamlit component template). Node commands use
  `npm --prefix viewer/ …` (Pattern-A, keeps the Bash allowlist matching).
- **esbuild; Three.js stays vendored & external + Python-inlined (recommended).** esbuild
  bundles `viewer/src/*.ts` → one ESM `viewer.js`, emitted **unminified** at
  `target: es2022` for a legible, code-reviewable committed diff. **Three.js stays
  vendored & external**: the bare `three` / OrbitControls imports keep resolving through
  the existing `data:` import-map and **`viewer.py` is unchanged**. Bundling three from
  npm is an explicit **deferred** option, *not* the default — `three.module.js` is
  ~1.27 MB vs `viewer.js`'s ~22 KB, so bundling would bloat the byte-diffed drift artifact
  ~58× and make it esbuild-version-churn-sensitive, **eroding the very
  `viewer-build-drift` guard this ADR makes load-bearing**, and would move asset assembly
  off the Python-authority seam `viewer.py` owns. (three is already SHA-256-pinned in
  `VENDOR.md`, so npm integrity-hashing — the only upside of bundling — is largely
  redundant.) The **offline single-file deliverable is preserved either way**; `@types/three`
  stays types-only.
- **Exact-pin the toolchain.** esbuild's semver treats **minor** releases as
  *intentionally* backwards-incompatible, so the lockfile must **exact-pin** esbuild
  (never `^`/`~`) and Node is pinned (`.nvmrc` / `setup-node`) — otherwise the drift guard
  flakes on a routine toolchain bump instead of catching a source edit.
- **`@types/three` pinned to `0.160.x`** to match vendored three **r160**
  (`_viewer_assets/three/VENDOR.md`); a CI guard asserts the two agree so the API the
  TS type-checks against can't drift from the three the viewer actually runs.
- **Determinism via the lockfile-drift idiom.** A `viewer-build-drift` CI job rebuilds
  the bundle (`npm --prefix viewer/ ci` → `npm --prefix viewer/ run build`) and fails on
  any diff against the committed `viewer.js` — the same shape as `lockfile-drift`, with a
  stricter (sha256/byte) invariant.
- **The transform stays in Python.** The browser keeps applying Python-emitted affines
  only; the future interaction layer captures *intent* and exports a `Scenario`/
  constraints artifact for the Python solver — it must never import the affine/anchor
  modules to re-derive geometry.
- **Contract-drift guard (cheap now, single-source later).** `scene-contract.ts` /
  `brand-contract.ts` are hand-written typed mirrors of `scene.py` / `brand.py`; a Python
  **key-set parity test** (in `tests/test_scene.py`, which already pins byte-determinism)
  asserts `build_scene()`'s top-level keys match the TS contract's expected set, and a
  brand parity test checks the token-name set — closing the silent cross-language drift gap
  at **zero new deps**. A JSON-Schema single-source (`scene-v1.schema.json` →
  `json-schema-to-typescript` + Python `jsonschema`) is the **deferred** principled target
  (spike #444), to adopt when `scene/v1` starts to churn; JSON Schema can express *shape*
  but not the det-−1 affine tuple, so the runtime `checkAnchors()` stays the load-bearing
  transform-**value** guard regardless.

### Why not Option 2?

A `tsc`-only multi-module layout forces `viewer.py` to grow one `data:`-URL import-map
entry and one `_asset_text` read per emitted module, changing the HTML assembly and its
byte output and multiplying the offline `data:` surface — reopening exactly the
complexity ADR-0017 minimized — for **no** modularity benefit over a single bundle. The
contract we want to protect (one `viewer.js`, deterministic assembly) is best served by a
single bundled artifact, not N inlined modules.

### Why not Option 3?

JSDoc-typed plain JS has the smallest supply-chain surface but does not deliver the
stated goal: a *shipped-modular*, editor-ready foundation. It would either ship N `.js`
modules (Option 2's import-map problem) or keep one big file (defeats "modular"), and it
still needs `tsc` to type-check — so it is not truly zero-toolchain. It is the right
answer only if the goal were "add types to the existing single file with zero build,"
which is not where the viewer is going. Recorded as the fallback if Node is vetoed.

### Why not Option 4?

Vite is the conventional choice for a TS web app and `vite-plugin-singlefile` can produce
the inlined offline HTML, so it is a genuine candidate for the *eventual* interactive
editor. It is **deferred, not rejected**: its Rollup/Rolldown output is less trivially
byte-reproducible than esbuild's, which is hazardous under a byte-diff drift guard, and it
adds a much larger pinned supply-chain surface for no benefit to the current render-only
bundle. esbuild's own watch/serve covers iteration, and because Vite uses esbuild
internally a later migration stays open — to be revisited only on a concrete trigger (the
editor needs HMR-grade tooling or a Vite-only plugin), so it is recorded here rather than
re-proposed ad hoc.

### Why not Option 0?

Untyped single-file growth toward an interactive editor concentrates risk in the one
language pytest cannot exercise — the precise hazard ADR-0002/0017 guard against. The
`scene/v1` contract and the future intent artifact both deserve compile-time types.

## Consequences

### Positive

- A typed `scene/v1` contract and a modular structure the future interaction layer plugs
  into; pure units (`affine`, `anchors`, `timeline`) become **node-unit-testable** — new
  coverage the single-file design could not have.
- ADR-0017's correctness invariants are all preserved: Python-owned transform, offline
  single file, deterministic output, fail-loud anchor check.
- Determinism is *stronger*, not weaker: a CI guard pins the committed bundle.

### Negative

- Adds a pinned **dev/CI** npm surface (`typescript`, `esbuild`, `@types/three`,
  `eslint`). Mitigated: committed `package-lock.json` + `npm ci`, minimal pinned deps,
  the `viewer-build-drift` guard, and **never** in the wheel build or `pip install`.
- Contributors need Node to *change* the viewer (not to build the wheel or run pytest).
- The new bundle's bytes will **not** equal the old hand-written `viewer.js`; equivalence
  to the prior viewer is **semantic** (verified by the headless render + anchor check),
  not a byte-diff. This is stated so reviewers don't chase an impossible diff.

### Neutral

- `three` stays vendored & external (recommended), so the `data:` import-map and the
  vendored `three/` source files are unchanged. The deferred bundle-from-npm option would
  retire them, but only behind a separate, explicitly-justified decision.
- A second top-level project root (`viewer/`) is introduced; contributors edit the viewer
  via Node there (`npm --prefix viewer/ …`) but still build the wheel and run pytest with
  no Node at all.

## Compliance

- **`viewer-build-drift` CI job** rebuilds the bundle and fails on any diff against the
  committed `viewer.js` (the determinism/reproducibility check) — see
  `.github/workflows/ci.yml` (added by issue #438).
- **`tsc --noEmit`** + **eslint** + **`node --test`** for the pure units run in CI.
- A **three-version↔`@types/three` skew guard** asserts `VENDOR.md` and `package.json`
  agree on the three major.minor.
- A **contract parity test** (`tests/test_scene.py`) asserts `build_scene()`'s top-level
  key set matches `scene-contract.ts`; a brand parity test matches `brand.py`'s token names.
- The TypeScript toolchain lives in the top-level `viewer/` dir, *outside* `src/`, so
  nothing it touches can enter the package by construction; package-data is tightened from
  `*.js` to the explicit `viewer.js`, and a **packaging assertion** confirms the built
  sdist/wheel contains `viewer.js` but no `node_modules/`, `*.ts`, or `package.json` (Node
  stays out of the install path).
- The unchanged in-browser `checkAnchors()` fails loud on the `#banner` if the JS matrix
  path diverges from the Python oracle; `tests/test_viewer.py` keeps the offline +
  `</script>`-escape asserts.

## More Information

- Related ADRs: [ADR-0017](0017-3d-viewer-architecture.md) (the viewer architecture whose
  build-toolchain sub-decision this supersedes), [ADR-0002](0002-determinant-minus-one-transform.md)
  (the transform retained in Python), [ADR-0019](0019-brand-tokens-single-source.md) (the
  BRAND blob the typed `brand-contract.ts` mirrors), [ADR-0003](0003-rr-mc-solver-algorithm.md)
  (the determinism spirit the build-drift guard extends).
- Related specs: [`docs/superpowers/specs/2026-06-04-viewer-typescript-architecture-design.md`](../superpowers/specs/2026-06-04-viewer-typescript-architecture-design.md);
  schema reference [`docs/architecture/scene-v2-schema.md`](../architecture/scene-v2-schema.md).
- Related issues / PRs: #436 (epic), #437 (toolchain), #438 (CI), #439 (port), #440
  (typed contract + interaction seam), #441 (Python `priority` groundwork), #442
  (deferred interactive editor — Stage 2 round-trip), #444 (deferred JSON-Schema
  single-source spike), #445 (deferred Stage 3: viewer-as-full-frontend via `hangarfit
  serve` — own ADR); follows #423, subsumes #433.
- External references: [esbuild](https://esbuild.github.io/) (reproducible builds,
  semver: minor = breaking), [`@types/three`](https://www.npmjs.com/package/@types/three),
  Three.js r160, [`vite-plugin-singlefile`](https://github.com/richardtallent/vite-plugin-singlefile)
  (the deferred Vite path).
