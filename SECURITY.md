# Security Policy

## Supported Versions

`hangarfit` is a single-maintainer hobby project. Only the latest release (the `main` branch) receives security updates; older tagged versions do not receive backports.

| Version | Status |
|---------|--------|
| Current (`main`) | ✅ Supported |
| Older releases | ⚠️ No backports |

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.** Instead, use GitHub's [private security advisory](https://github.com/DocGerd/hangarfit/security/advisories/new) feature to report the issue confidentially.

For non-sensitive questions or feature requests, regular issues are welcome.

## Response Expectation

This is a hobby project maintained on a best-effort basis. We appreciate vulnerability reports and will make a reasonable effort to investigate and respond, but we cannot promise SLA timelines or guaranteed patch releases.

## Scope

The following areas have non-zero attack surface and merit scrutiny:

- **YAML loader** (`src/hangarfit/loader.py`): parses user-supplied layout, fleet, and scenario files (including `ground_objects:` obstacle / car / trailer definitions)
- **Visualizer** (`src/hangarfit/visualize.py`): renders user-supplied aircraft placements and conflict data
- **3D viewer** (`src/hangarfit/scene.py`, `src/hangarfit/viewer.py`): builds a self-contained offline HTML file embedding user-supplied plane ids and a JSON scene. By design, ids reach the page only via safe text APIs — canvas `fillText`, `textContent`, and `createTextNode` — never `innerHTML`; the inlined scene JSON escapes every `<` (to its JSON unicode escape) so a value can never close the `<script>` block. Three.js is vendored (r160) and hash-recorded in its `_viewer_assets/three/VENDOR.md`.
- **Learned backend** (`src/hangarfit/learned.py`, opt-in): when the dev-only `ml/` package and the `[learned-infer]` extra are installed, `--backend learned --weights model.onnx` runs `onnxruntime` on a user-supplied ONNX weights file to *propose* a layout. The proposal is not trusted — `collisions.check` remains the sole acceptance gate (#694, [ADR-0027](docs/adr/0027-learned-backend-determinism-scope.md)); a missing package, extra, or weights file exits cleanly via `LearnedBackendUnavailableError` rather than importing anything. Only the seam ships in the wheel; the inference implementation lives in `ml/`, present in source checkouts only.

We welcome reports of resource exhaustion, parsing edge cases, rendering defects, or any way the generated viewer HTML could be coerced into executing injected markup — anything that could affect the tool's reliability or the user's system.

## Project security posture

`hangarfit` runs an [OpenSSF Scorecard](https://securityscorecards.dev/) workflow on every push to `develop` and on a weekly schedule (see [`.github/workflows/scorecard.yml`](.github/workflows/scorecard.yml)). Several of the checks score 0 for structural reasons specific to this project — single maintainer, single deployment site, deliberately unpublished — rather than because the underlying concern is unaddressed.

The rationale for each structural zero, plus what we *do* in lieu of the standard remediation (e.g. the `/pr-review` workflow substituting for formal `APPROVED` reviews), is documented in [`docs/security-posture.md`](docs/security-posture.md). Read that before drawing conclusions from the aggregate Scorecard number.

## Per-release security assessment

A mandatory, documented security assessment (CodeQL, fuzzing, and threat-surface review) is required before each release tag is pushed. The checklist and recording instructions live in [`docs/release-security-assessment.md`](docs/release-security-assessment.md).
