# Changelog

All notable changes to this project are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Real Airfield Herrenteich dataset (`herrenteich/`, refs #79).** A
  self-contained real-world dataset kept separate from the synthetic `data/`
  placeholders: the DWG-measured hangar (15.08 m × 31.76 m, 13.46 m door), the
  eight aircraft usually hangared there (published-spec dimensions,
  second-source verified; adds a folded **Stemme S10** and a confirmed 18 m
  Scheibe SF-25E; drops Fuji/Cessna 150), and a valid all-eight `layout.yaml`
  (`hangarfit check` → exit 0) with a regression test. Surfaced two follow-ups:
  the L-shaped hangar's office **notch** is not yet modelled (spike #424, the
  files keep clear of it by hand), and the solver's bounding-box
  trivial-infeasibility gate then false-rejected this glider fleet (#425, fixed
  below) — the layout was found by driving the real part-collision checker
  directly. The default `data/` demo data is unchanged.
- **Brand source of truth in-repo (#414).** `docs/assets/BRAND.md` captures the
  hangarfit brand (DocGerdSoft lineage + the 2D tokens + the 3D dark-surface
  section + the full token table), so the viewer's colours, banners, and
  typography trace to one document.

### Changed

- **3D viewer renders on the DocGerdSoft dark-surface brand (#415).** `hangarfit
  view` now uses the dark-lifted fleet palette (`PLANES_DARK`, keyed by the same
  sorted id so 2D/3D plane identity is preserved), a unified scene shell
  (floor/grid/walls on the STATUS `wall` ink), a `maint`-violet maintenance bay
  (retiring the viewer's off-system red), an accent fill light, branded HUD chrome (dark
  neutrals, accent focus ring, amber honesty banner, Geist/mono typography), and a
  non-colour `⚠ conflict` label cue (the 3D analogue of the 2D hatch — "never hue
  alone"). Render-only: the `scene/v1` contract, the Python-owned determinant-−1
  transform, `build_scene` byte-determinism, and the collision model are unchanged.
- **Brand tokens centralized into one source (`src/hangarfit/brand.py`, #419).**
  Every brand colour, opacity, darken factor and font stack is now *defined once*
  in `brand.py` and *referenced* by all four render surfaces: `visualize.py` (2D)
  re-exports the names it always exposed, `scene.py` reads `PLANES_DARK` from
  `brand`, `viewer.py` builds its `_CSS` from brand tokens, and `viewer.js` reads
  its colours from a new canonical `BRAND` JSON blob injected into the HTML
  (separate from the scene blob — the `scene/v1` schema is unchanged) instead of
  hard-coded `0x` literals. Render-only and determinism-neutral: the emitted HTML
  is byte-identical, the CVD-safe palette (#326) values are unchanged, and the
  collision model / determinant-−1 transform are untouched (ADR-0019).

### Fixed

- **Solver no longer false-rejects glider fleets (#425).** The pre-search
  trivial-infeasibility gate (`solve` check #2) summed each plane's *bounding
  box* (`fuselage_length × wingspan`), which for a thin-winged glider is mostly
  empty air — so an 18 m-span Scheibe Falke could push Σ bbox over the hangar
  floor and the solver would return `trivially_infeasible` without ever
  searching, even when a valid nested layout existed. The gate now sums each
  plane's actual **part-footprint rectangles** (a much tighter estimate), so
  glider-containing fleets reach the search; only genuinely-too-big fleets still
  short-circuit. RNG-free and pre-search, so the byte-identical determinism
  contract (ADR-0003) is unchanged.

## [0.10.0] — 2026-06-04

### Added

- **3D viewer renders landing gear + tow carts (#399).** `scene/v1` now emits
  per-plane `wheels[]` (canonical plane-local positions, ADR-0013) and an
  `on_carts` flag, plus a `gear_anchors` oracle. The viewer draws a wheel at each
  wheel point (+ a short leg up to the belly where it clears) — and a pallet deck
  under each wheel for carted planes — all parented to the existing per-plane
  affine Group, so the gear
  inherits the determinant-−1 transform and animates along the tow path for free.
  The load-time anchor self-check now also oracles gear world positions (the only
  cross-language backstop, since `viewer.js` is not pytest-covered). Wheels/carts
  are render-only and never enter the collision model (ADR-0015); `build_scene`
  stays byte-deterministic and `collisions.py` is untouched.
- **3D viewer polish — shadows, materials, labels, nose arrows (#400).** The
  viewer now casts soft contact shadows (a `PCFSoftShadowMap` key sun + ortho
  frustum sized to the hangar, a soft fill, softened ambient) so vertical
  clearance is legible — a high wing's shadow across a neighbour's tail is the
  viewer's reason to exist (ADR-0017). Materials are kind-based (translucent wings,
  thin metallic struts, a darker cockpit tint echoing the 2D render's cockpit
  shading). Each plane gets
  a billboarded id label (a `CanvasTexture` sprite drawn with safe `fillText`,
  never `innerHTML`) and a nose-cone arrow at its `+x` tip, both behind a new
  `labels` HUD toggle. All client-side with the already-vendored Three.js r160 —
  still a single self-contained offline HTML, no new assets, no determinism or
  collision risk.
- **Honesty banner + actionable readouts (#401).** A persistent "PLACEHOLDER
  DATA — illustrative only, not for real parking" banner now appears on both the
  2D PNG and the 3D viewer whenever any placed aircraft is on unmeasured
  (`measured: false`) data — so a club member never mistakes an illustrative
  render for a real parking plan (#79). It disappears once the data is measured.
  Valid layouts also surface two actionable numbers — the tightest plan-view
  inter-plane gap and the smallest wing-over-tail vertical clearance — computed by
  a new read-only `hangarfit.metrics` module (never entering the collision model).

### Changed

- **Plain-language conflict messages (#401).** `check` (exit 1) and the solver's
  trivially-infeasible / exhausted-budget summaries now lead each conflict with a
  readable sentence ("`fuji` overlaps `scheibe_falke`", "`x` intrudes into the
  maintenance bay", "`x` extends outside the hangar") instead of the raw `kind`
  enum, while keeping the precise `detail` (parts + z-gaps) verbatim. The exit-3
  "no feasible tow path (plane …)" message already named the blocking plane.

### Fixed

- **`hangarfit view` degrades to a static scene in seconds, not minutes
  (#398).** Layout-mode `view` now passes a small deterministic *global*
  tow-expansion cap (`_VIEW_TOW_MAX_TOTAL_EXPANSIONS`, 300) to `plan_fill`, so an
  un-routable layout (e.g. the default `layouts/example.yaml`) falls back to a
  static 3D render in ~5 s instead of grinding through the full ~16000-expansion
  disprove budget (~2 min). The bound is a deterministic expansion count, not a
  wall-clock deadline (ADR-0003); a fast-routable layout still animates, and an
  explicit `--tow-max-expansions` overrides the cap.

## [0.9.0] — 2026-06-02

### Added

- **`hangarfit --version`.** A top-level `--version` flag prints the installed
  package version and exits (#360).
- **DocGerdSoft "Horizon" brand identity.** Brand mark assets — avatar, banner,
  favicon, mark, and monogram SVGs under `docs/assets/` — and a brand identity
  note in the README (#380).

### Changed

- **Solver — back-of-hangar fill bias (#320).** The CLI now biases the spread
  post-pass to pack planes toward the back wall (default on; `--no-back-fill`
  disables, no effect under `--no-spread`), keeping the door-side approach
  corridors clear so `solve --render-paths` can thread a tow path to each slot.
  The bias is RNG-free re-ranking — same-seed output stays byte-identical.
  Documented as the 2026-06-01 amendment to ADR-0008.
- **Tow planner — `grid` heuristic is now the default, with a global
  fill-budget cap (#336).** The obstacle-aware `grid` A\* heuristic (added
  opt-in in v0.8.0) is now the default for `solve` / `plan_fill` / the CLI; the
  per-plane `_MAX_EXPANSIONS` is raised to 8000, and a *separate* deterministic
  global fill cap (`_MAX_FILL_EXPANSIONS`, 16000) bounds the total expansions
  across one fill so it never hangs. `--tow-heuristic euclidean` opts back into
  the older straight-line heuristic. Documented as the 2026-06-01 amendment to
  ADR-0007.
- **CLI `solve --render-paths` — spread-vs-towability backstop (#280).** When a
  default (spread-on) layout is fully un-routable, the CLI now re-solves once
  with spread disabled (reusing the same seed) and renders that tighter
  arrangement *if it routes* — reporting the swap on stderr, in `--json`
  (`diagnostics.spread_fallback_applied`), and as a `--write-yaml` provenance
  comment, never silently. With the #320 placement bias in play, multi-plane
  fills that were previously a bare exit 3 now route under default settings
  without the backstop firing at all. New ADR-0016.
- **Tow-path render palette retuned to the brand "Horizon" set (#380).** The
  renderer's per-plane colours move to the DocGerdSoft `PLANES` palette (Horizon
  `#0079B5` first), still derived from the Okabe–Ito CVD-safe set so every fill
  keeps maximal pairwise colour-blind separation.

### Security

- **Nightly fuzzing extended to the geometry and collision layers (#362, #369).**
  An Atheris + Hypothesis harness now fuzzes the oriented-rect transform and the
  pairwise collision checker on the nightly schedule, alongside the existing
  loader fuzzing.

## [0.8.0] — 2026-05-29

### Added

- **Wheel positions are now canonical per-aircraft data.** A new `Wheels` dataclass carries each aircraft's measured wheel positions in `fleet.yaml`, replacing the renderer's heuristic fuselage-fraction guesses; at load time `turn_radius_m` is cross-checked against the wheelbase (a 0.5×–5× sanity band). Documented in ADR-0013 (#322).
- Opt-in, default-off obstacle-aware A\* heuristic seam (`heuristic=` / `stats=`) on the tow-path planner, plus a reproducible routability benchmark and the towplanner-v2 spike write-up under `docs/superpowers/specs/`. The spike characterised why tight multi-plane fills are un-routable (budget-exhausted on tight finite-width maneuvering, not obstacle clutter) and found the obstacle-aware grid heuristic buys no extra routability (#332).

### Changed

- **BREAKING (pre-1.0):** `Aircraft.wheels` is now a required field — the loader raises a `LoaderError` on a missing or malformed `wheels:` block. All nine fleet aircraft carry a backfilled `wheels:` block (#322).
- Tow-path overlay now uses the CVD-safe Okabe–Ito 8-colour palette; the mid-wing colour moves to vermillion (`#d55e00`) for better protanopic separation from the low-wing yellow; and conflict overdraw is signalled with a hatch fill and dashed outline in addition to colour, so it survives greyscale and colour-blind viewing (#326).
- Cart-borne aircraft (`on_carts=True`) render as a small pallet under each wheel, oriented with the aircraft, instead of one body-sized deck rectangle — matching the physical cart geometry (#321).
- Hybrid-A\* per-plane node-expansion budget (`_MAX_EXPANSIONS`) raised 700 → 2000 — the empirical knee from a budget sweep — so more tight fills route; the slow-test per-plane perf ceiling was raised to match (#335).
- README badge row gains a CodeQL badge (slot 2) and the CI badge is now a clickable link, consistent with the other badges (#339).
- Release documentation prep is split into a dedicated `/release-prep` skill (CHANGELOG promotion + doc-freshness audit on its own focused-review PR into `develop`); `/release-cut` gains a Check E that refuses to cut until the CHANGELOG has been promoted (#325).

### Fixed

- `hangarfit.__version__` was a stale hard-coded `"0.0.1"` that never tracked `pyproject.toml`; it is now sourced from the installed package metadata via `importlib.metadata.version("hangarfit")`, with a `PackageNotFoundError` fallback for an uninstalled source tree, so it stays in sync with the release version (#341).

## [0.7.2] — 2026-05-28

Housekeeping cut. Two doc/test items left over from the v0.7.0/v0.7.1 release campaign — no behavioural change to `check`, `solve`, or `solve --render-paths` output for any existing scenario.

### Changed

- `tests/test_solver_search.py` now anchors every fixture / layout / data load on `Path(__file__).resolve().parent.parent` rather than process cwd, so pytest can be invoked from any directory and the tests still resolve the right files. Matches the existing convention in `tests/test_loader.py` (#317).
- README status section updated to reflect Phase 3a (tow-path planner v1) and Phase 3b (Reeds–Shepp v2) having shipped in v0.7.0/v0.7.1; removed the stale "No movement-sequence planning" out-of-scope claim and the stale "the example layout fails validation" parenthetical.

### Fixed

- LICENSE Apache-2.0 copyright line was the unfilled `[yyyy] [name of copyright owner]` template placeholder; now reads `Copyright 2026 DocGerdSoft (Patrick Kuhn)` (#310).
- `solver._plane_footprint_area` no longer leaves a `tail` part in both the reconstructed-fuselage span *and* the per-part lengths list — a structural double-count for an aircraft declaring both fuselage segments and a separate tail. Dormant in real use today (no fleet aircraft has a `tail` part) and behaviorally inert under the current `max()` reduction, but a regression guard against future helper refactors. Includes a unit test pinning the post-fix value (#317).

## [0.7.1] — 2026-05-27

First published release of the 0.7.x line. v0.7.0 was tagged on `main` but its GitHub Release could not be published — the tag was consumed by an immutable release during the release cut and is permanently reserved — so v0.7.1 supersedes it with identical features plus the release-workflow fix below.

### Fixed

- Release workflow is now compatible with GitHub immutable releases: it creates the release as a draft, uploads the Sigstore-signed artifacts while the draft is still mutable, then publishes — replacing the create-published-then-upload sequence that failed to attach assets to a sealed release (#285).

## [0.7.0] — 2026-05-27

The first release with tow-path planning: `hangarfit` can now plan how each aircraft is towed in and out, not just whether a static layout is collision-free. Also lands the full Arc42 architecture documentation set, the maintenance-bay walling rule, a spread-aware solver, and an OpenSSF supply-chain hardening pass.

### Added

- Tow-path planner (`towplanner` module): `hangarfit solve --render-paths` renders a per-plane tow path overlay plus a tow order. Best-effort — a layout the planner can't fully route still renders (blocking plane named on stderr); exit code `3` only when no candidate layout is tow-routable ([ADR-0007](docs/adr/0007-tow-path-planner-v1-scope.md), #188, #189, #190, #191, #196, #222, #197, #192, #193).
- Reeds–Shepp motion model — reverse arcs eliminate the reorientation loops of the Dubins-only first cut — and door **entry-cone** search over heading × offset (planner v2, [ADR-0010](docs/adr/0010-reeds-shepp-motion-model.md), #261, #262, #271).
- `bay_intrusion` maintenance-bay perimeter collision rule with partial-width, back-anchored geometry, replacing the legacy maintenance check ([ADR-0006](docs/adr/0006-bay-intrusion-maintenance-rule.md), #103, #104, #106, #107).
- Spread-aware solver: a best-of-all-basins post-pass maximizes the minimum inter-plane gap, surfacing `min_pairwise_gap_m` and `valid_basins_found` ([ADR-0008](docs/adr/0008-inter-plane-spread-soft-preference.md), #145, #267).
- Full Arc42 architecture documentation under `docs/architecture/` and an Architecture Decision Records system (ADR-0001 … ADR-0010) under `docs/adr/` (#132, #133, #134, #135, #136).
- Loader validates plane ids and `maintenance.plane` at the load boundary with did-you-mean suggestions (#221, #171, #175, #177).
- Nightly polyglot YAML-loader fuzzing (Hypothesis + Atheris); OpenSSF Scorecard Fuzzing 0→10 (#143, #253).
- OpenSSF Baseline L1 self-attestation and Best Practices **Silver** badge, with GOVERNANCE.md and Code-of-Conduct links (#232, #256, #259).
- Sigstore keyless cosign signing workflow for releases (#167).

### Changed

- Raised the supported Python floor to **3.12** (was 3.11) and collapsed the CI test matrix to a single 3.12 job; both hash-pinned lockfiles are now resolved on 3.12. **Breaking change** for 3.11 users ([ADR-0009](docs/adr/0009-single-supported-python-version.md), #213).
- Hash-pinned every lockfile end-to-end — dev deps, build toolchain, fuzz toolchain, and the pip-tools bootstrap — each guarded by a CI drift check (#140, #198, #199, #224).
- Solver determinism is now scoped to `max_restarts` ([ADR-0003](docs/adr/0003-rr-mc-solver-algorithm.md) amended, #267).
- Slimmed CLAUDE.md to operational guidance; migrated domain content to Arc42 (#137).
- LICENSE now ships in the sdist and wheel (#230).

### Removed

- Python 3.11 support and the multi-version CI matrix (#213).
- Legacy maintenance-bay collision check, superseded by `bay_intrusion` (#104).

### Security

- Added a security-posture document explaining the structural-zero OpenSSF Scorecard checks; made SECURITY.md phase-agnostic; documented the branch-protection residual cap (#142, #260, #225).

## [0.6.1] — 2026-05-23

Solver-polish follow-ups.

### Changed

- Broadened the `diversity_impossible` precondition wording in the solver spec (#119).

### Fixed

- Bounded `wall_time_s` in the fixture-matrix tests to stop time-sensitive flakes (#122).
- Fixed the OpenSSF Scorecard workflow push trigger to fire on the default branch and added `workflow_dispatch` (#126).
- Wired `CODECOV_TOKEN` so non-`main` coverage uploads succeed (#127).

## [0.6.0] — 2026-05-23

A large cut bundling the "going public" repository-hardening pass and the Phase 2a static layout solver. (There were no 0.2.0–0.5.0 release tags; that work shipped here.)

### Added

- `hangarfit solve` — a Random-Restart Monte-Carlo static layout solver that finds a valid arrangement when no hand-authored candidate exists, with pinning, minimal-edit repair, and forced-cart modes ([ADR-0003](docs/adr/0003-rr-mc-solver-algorithm.md)).
- Diversity metric for alternative layouts (`--alternatives`, edit-count thresholds) ([ADR-0004](docs/adr/0004-diversity-metric.md)).
- `SearchConfig.max_restarts` to bound the outer search loop (#111).
- Scenario types and penetration-depth reporting in `CheckResult`.

### Changed

- Default `layouts/example.yaml` is now a valid 6-plane layout.
- Corrected the placeholder dimensions in `fleet.yaml`.

### Security

- Added SECURITY.md, CONTRIBUTING.md, GitHub issue/PR templates, and Dependabot config (going-public milestone).
- Added CodeQL scanning and the OpenSSF Scorecard workflow + README badge; pinned all GitHub Actions to commit SHAs.
- Adopted ruff (lint + format), mypy, pre-commit, and pytest-cov → Codecov coverage in CI.

### Fixed

- Fixed a solver-determinism flake and added fail-loud regression canaries across the solver fixtures (#98).

## [0.1.0] — 2026-05-21

First Phase 1 cut — substrate for arranging the flying club fleet in a stack-style hangar.

### Added

- Aircraft, hangar, layout data models with cross-reference invariants (cart rule, movement-mode ↔ on-carts, maintenance-plane membership) (#1, #2).
- YAML loader with high-level `struts:` block expansion into mirrored Part instances (#3).
- Geometry primitives: plane-local → world transform (heading 0° = +y, CW positive), `aircraft_parts_world()` (#4).
- Collision checker: hangar bounds + maintenance-bay rule + pairwise parts overlap with 2D-plus-height clearances (#5).
- Visualizer: top-down PNG renderer, headless matplotlib, conflict highlighting (#6).
- CLI: `hangarfit check <layout> [--render <png>]` (#7).
- Apache-2.0 license, public-audience README, CI matrix (Python 3.11 + 3.12), branch protection on develop + main (#13, #14, #15, #16).
- Strut-aware golden tests + all-9-planes fixture using larger test-only hangar to accommodate strut-bracing geometry on placeholder dimensions (#5).

[Unreleased]: https://github.com/DocGerd/hangarfit/compare/v0.10.0...HEAD
[0.10.0]: https://github.com/DocGerd/hangarfit/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/DocGerd/hangarfit/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/DocGerd/hangarfit/compare/v0.7.2...v0.8.0
[0.7.2]: https://github.com/DocGerd/hangarfit/compare/v0.7.1...v0.7.2
[0.7.1]: https://github.com/DocGerd/hangarfit/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/DocGerd/hangarfit/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/DocGerd/hangarfit/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/DocGerd/hangarfit/compare/v0.1.0...v0.6.0
[0.1.0]: https://github.com/DocGerd/hangarfit/releases/tag/v0.1.0
