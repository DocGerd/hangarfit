# Changelog

All notable changes to this project are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

### Changed

### Fixed

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

[Unreleased]: https://github.com/DocGerd/hangarfit/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/DocGerd/hangarfit/releases/tag/v0.1.0
