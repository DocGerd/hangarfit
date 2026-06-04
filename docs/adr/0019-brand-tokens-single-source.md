# ADR-0019: Brand tokens live in one Python module (`brand.py`), injected into the viewer as a canonical BRAND blob

- **Status:** Proposed
- **Date:** 2026-06-04
- **Deciders:** Patrick Kuhn (DocGerd)

## Context & Problem Statement

Every hangarfit render surface needs the same brand tokens — the CVD-safe
`PLANES` palette, the status inks, the dark-surface neutrals, opacities, darken
factors and font stacks. Until now those values were **hand-copied** across four
files: `visualize.py` (2D matplotlib constants), `scene.py` (which imported
`PLANES_DARK` from `visualize`), `viewer.py` (the HTML `_CSS` hex literals), and
`viewer.js` (hard-coded `0xRRGGBB` colour literals). `docs/assets/BRAND.md` is the
*human* source of truth, but nothing made the *code* read from one place, so a
value changed in one surface could silently drift from the others. How do we make
each token defined once and referenced everywhere, without changing any render
output, the determinism contract, or the `scene/v1` schema (#419)?

## Decision Drivers

- **Single source of truth in code** — one definition per token; the other
  surfaces reference it, so drift is structurally impossible.
- **Render-only / zero determinism risk** — no change to geometry, the
  determinant-−1 transform, the collision model, or solver/planner output; the
  emitted HTML must stay byte-deterministic (ADR-0003 spirit, ADR-0017).
- **Offline** — the viewer is one self-contained `file://` HTML with no network
  and no parsing libraries; whatever feeds `viewer.js` must be inert data already
  in the document.
- **`scene/v1` is frozen** — tokens must not leak into the scene dict or the
  schema (ADR-0017); they are presentation, not geometry.
- **CVD-safety preserved** — the Okabe–Ito-derived palette values (#326) must not
  change.

## Considered Options

1. **One Python module `brand.py` as the source; viewer.js fed by a canonical
   JSON `BRAND` blob injected into the HTML.** `visualize.py` re-exports the
   names it historically exposed (so importers keep working), `scene.py` reads
   `PLANES_DARK` from `brand`, `viewer.py` builds `_CSS` from brand tokens, and
   the viewer reads `BRAND.*` instead of `0x` literals.
2. **Python-only centralization.** Move the 2D/CSS tokens into `brand.py` but
   leave `viewer.js` with its hard-coded `0x` literals (JS can't import a Python
   module).
3. **Build-time codegen.** Generate a `viewer.colors.js` (or patch `viewer.js`)
   from `brand.py` at packaging time so the JS literals are emitted, not
   hand-written.

## Decision Outcome

**Chosen option: Option 1**, because it is the only one that removes *all* the
duplication — including `viewer.js` — while keeping the viewer fully offline and
the `scene/v1` schema untouched. The tokens are already injected into the HTML at
render time (the scene blob proves the pattern), so a sibling `BRAND` blob costs
one more `<script type="application/json">` and one `JSON.parse`, with no build
step and no new dependency.

The blob is serialized canonically — `json.dumps(tokens, sort_keys=True,
separators=(",", ":"), allow_nan=False)` and `<`-escaped exactly like the scene
blob — at a fixed position in the template, so the HTML stays byte-deterministic.
3D colours are carried as `#RRGGBB` strings and passed to `new THREE.Color(str)`
(Three.js accepts either a JS number or a CSS string; the string form needs no
`0x`→int conversion and no parsing library, so it stays offline).

### Why not Option 2 (Python-only)?

It leaves `viewer.js` as a second, unowned copy of the colours — exactly the
drift the issue exists to kill. A maintainer who re-tints the bay in `brand.py`
would silently not change the 3D bay. Half-centralizing is worse than none
because it *looks* centralized.

### Why not Option 3 (build-time codegen)?

It would emit the literals, but at the cost of a generation step in the package
build, a generated artifact to keep in sync (or `.gitignore` and reproduce in
CI), and a new failure mode (stale generated file). Runtime injection achieves
the same single-source guarantee with strictly less machinery, and the viewer
*already* parses an injected JSON blob, so the seam is free.

## Consequences

### Positive

- One definition per token; `visualize`, `scene`, `viewer` and `viewer.js` all
  reference `brand.py`. A value can no longer drift between surfaces.
- `viewer.js` carries **no** colour literals — a top-of-file comment forbids
  re-introducing them; colours arrive via the `BRAND` blob.
- Render output is unchanged (the 2D constants, the `_CSS` bytes, and the
  3D colours are identical values); the HTML is byte-identical across re-renders
  of the same scene.

### Negative

- The viewer now depends on a second injected blob; a malformed/absent `BRAND`
  blob would break the render. Mitigated: the blob is produced by the same pure
  assembler as the scene, and `_embed_brand()` `<`-escapes it like the scene.
- 3D colour casing is now whatever `brand.py`/`STATUS` use (e.g. `#7B63A3`)
  rather than the old lowercase `0x7b63a3`; `THREE.Color` is case-insensitive so
  the pixels are identical, but a substring test on the old `0x` form had to move
  to a parsed-token assertion.

### Neutral

- The token names in the JS-facing `BRAND` object are camelCase JS read-sites
  (`floor`, `wallsOpacity`, …), distinct from the Python constant names.

## Compliance

- `tests/test_viewer.py::test_brand_blob_is_present_canonical_and_round_trips`
  (canonical sorted/compact JSON, round-trips to a dict of str/number),
  `test_brand_blob_has_no_raw_angle_bracket` (escape parity with the scene blob),
  `test_render_viewer_is_byte_identical_across_two_calls` (determinism),
  `test_brand_module_exports` (palettes + 3D token keys), and
  `test_html_carries_brand_3d_tokens` (the brand values reach the HTML).
- `viewer.js` carries a top comment forbidding `0x` colour literals; reviewers
  grep for new `0x`/`rgba(`/named-colour literals.

## More Information

- Related ADRs: [ADR-0017](0017-3d-viewer-architecture.md) (the viewer
  architecture and the Python-owned transform), [ADR-0003](0003-rr-mc-solver-algorithm.md)
  (the determinism spirit the byte-stable blob honors).
- Related docs: [`docs/assets/BRAND.md`](../assets/BRAND.md) (the human source of
  truth `brand.py` mirrors), [`docs/architecture/scene-v1-schema.md`](../architecture/scene-v1-schema.md).
- Related issues / PRs: #419 (this decision), #326 (CVD-safe palette), #414/#415
  (the brand and its first application to the viewer).
