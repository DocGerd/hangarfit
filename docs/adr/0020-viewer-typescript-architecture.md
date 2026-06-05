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
positions"** to certain aircraft. Untyped, single-file growth toward that is risk
concentrated in the repo's only untested language. The question this ADR answers: **how
do we make the viewer typed, modular, and extendable for that future without losing the
offline single-file deliverable, the build/output determinism, the OpenSSF supply-chain
posture, or the Python-owned transform?**

## Decision Drivers

- **Type safety over the `scene/v1` contract** — catch shape errors at dev/CI time,
  not in a late headless screenshot.
- **Modular, extendable structure** — a foundation an interaction layer can plug into,
  not a 546-line monolith.
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
0. **Status quo** — keep the single untyped `viewer.js`.

## Decision Outcome

**Chosen option: Option 1**, because it is the only option that delivers *real
TypeScript + a shipped-modular, editor-ready structure* while keeping the **install and
runtime paths Node-free**: the toolchain is a dev/CI concern, the distributed artifact is
a single committed `viewer.js`, and determinism becomes *reproducibility of that
artifact*, enforced exactly like the repo's existing lockfile-drift guards.

Supporting choices:

- **esbuild, three external or bundled.** esbuild bundles `src/*.ts` → one ESM
  `viewer.js`, emitted **unminified** at `target: es2020` so the committed diff is
  legible and reviewable (matching the repo's choice to commit *readable* vendored
  three). Recommended direction: promote `three` to a **pinned npm dependency bundled
  into the output** (npm integrity-hashed supply chain; one self-contained file; drops
  the base64 `data:` import-map machinery). Keeping `three` **vendored & external** —
  unchanged `viewer.py`, the `data:` import-map preserved — is the documented
  conservative fallback. Either way the **offline single-file deliverable is preserved**
  and `@types/three` stays types-only.
- **`@types/three` pinned to `0.160.x`** to match vendored/​bundled three **r160**
  (`_viewer_assets/three/VENDOR.md`); a CI guard asserts the two agree so the API the
  TS type-checks against can't drift from the three the viewer actually runs.
- **Determinism via the lockfile-drift idiom.** A `viewer-build-drift` CI job rebuilds
  the bundle (`npm ci` → `npm run build`) and fails on any diff against the committed
  `viewer.js` — the same shape as `lockfile-drift`, with a stricter (sha256/byte)
  invariant.
- **The transform stays in Python.** The browser keeps applying Python-emitted affines
  only; the future interaction layer captures *intent* and exports a `Scenario`/
  constraints artifact for the Python solver — it must never import the affine/anchor
  modules to re-derive geometry.

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

- If `three` is bundled from npm, the `data:` import-map and the separate vendored
  `three/` source files may be retired; if it stays external, they are unchanged. Both
  are in-policy under this ADR.

## Compliance

- **`viewer-build-drift` CI job** rebuilds the bundle and fails on any diff against the
  committed `viewer.js` (the determinism/reproducibility check) — see
  `.github/workflows/ci.yml` (added by issue #438).
- **`tsc --noEmit`** + **eslint** + **`node --test`** for the pure units run in CI.
- A **three-version↔`@types/three` skew guard** asserts `VENDOR.md` and `package.json`
  agree on the three major.minor.
- A **packaging assertion** confirms the built sdist/wheel contains `viewer.js` but not
  `src/*.ts`, `node_modules/`, or `package.json` (Node stays out of the install path).
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
  schema reference [`docs/architecture/scene-v1-schema.md`](../architecture/scene-v1-schema.md).
- Related issues / PRs: #436 (epic), #437 (toolchain), #438 (CI), #439 (port), #440
  (typed contract + interaction seam), #441 (Python `priority` groundwork), #442
  (deferred interactive editor); follows #423, subsumes #433.
- External references: [esbuild](https://esbuild.github.io/) (reproducible builds),
  [`@types/three`](https://www.npmjs.com/package/@types/three), Three.js r160.
