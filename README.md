# hangarfit

![CI](https://github.com/DocGerd/hangarfit/actions/workflows/ci.yml/badge.svg?branch=develop)
[![codecov](https://codecov.io/gh/DocGerd/hangarfit/branch/develop/graph/badge.svg)](https://codecov.io/gh/DocGerd/hangarfit)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/DocGerd/hangarfit/badge)](https://securityscorecards.dev/viewer/?uri=github.com/DocGerd/hangarfit)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/12987/badge)](https://www.bestpractices.dev/projects/12987)

An on-demand exception tool for a flying club's hangar parking.

The club parks nine aircraft in a deep, stack-style hangar with a single door at the front. There's a standard layout that works for the standard situation — every plane back from its flight at the expected time, no surprise maintenance, no late returns. When that standard situation breaks (a plane comes back late, a maintenance slot moves, two planes need to swap order), someone has to come up with an alternative parking arrangement on the spot. `hangarfit` is the tool that checks whether a proposed alternative is physically valid: no fuselage, wing, or strut collisions; everything fits inside the hangar; the plane scheduled for maintenance ends up at the back where the maintenance bay is.

It also renders a top-down PNG so a human can sanity-check the result by eye.

`hangarfit` can also *find* a layout for you: given a scenario (the fleet to park, optional pins, the maintenance plane), `hangarfit solve` searches for a valid arrangement under hard constraints. The checker remains the source of truth — every accepted layout was validated by `collisions.check()` as its acceptance gate, so the solver cannot invent a layout the checker would reject.

## Scope

**Phase 1 — shipped.** Built the substrate: aircraft + hangar data model, parts-based collision checker, matplotlib top-down visualizer, and the `hangarfit check` CLI. Phase 1 was about getting the geometry right — once the collision rule was trustworthy, the downstream solver could be built on top of it.

**Phase 2a — shipped.** Added the static layout solver: `hangarfit solve` takes a scenario YAML (fleet, hangar, constraints, optional pins) and searches for up to K diverse valid layouts using random-restart hill climbing with min-conflicts descent. Acceptance runs through `collisions.check()` as its gate — the solver does not bypass the collision rule.

**Still explicitly out of scope:**

- No movement-sequence planning ("in what order do I roll planes out and back in to reach this layout").
- No tracking of hangar state across runs — each invocation is stateless.
- No soft constraints / preferences. Constraints are HARD: pin, force_on_carts, maintenance plane.
- No GUI or web frontend.
- No handling of late arrivals as a live event stream.

These boundaries are deliberate.

## Status

Pre-release. Phase 1 + Phase 2a are feature-complete (`hangarfit check` and `hangarfit solve`). All dimensions in `data/` are placeholders pending real measurement and are flagged as such in the YAML; checker output on the current data is illustrative, not authoritative — and so are any layouts the solver finds against it.

Follow progress in [GitHub Issues](https://github.com/DocGerd/hangarfit/issues) and milestones.

## Install

Requires Python 3.12 or newer.

```bash
pip install -e ".[dev]"
```

This installs the package in editable mode along with the test dependencies (`pytest`).

## Usage

```bash
# Install from a checkout (add "[dev]" if you will run the tests)
pip install -e .

# Check a hand-authored layout
hangarfit check layouts/example.yaml
```

> Note: against the current placeholder fleet/hangar measurements (see Status), the example layout fails validation. That's expected — Phase 1 ships the substrate; real measurements are tracked separately.

```bash
# Render the layout (works on invalid layouts too — conflicts highlighted in red)
hangarfit check layouts/example.yaml --render out.png

# Machine-readable output
hangarfit check layouts/example.yaml --json

# Override the fleet/hangar (advanced — for layouts without embedded fleet:/hangar: refs)
hangarfit check my_portable_layout.yaml --fleet path/to/fleet.yaml --hangar path/to/hangar.yaml
```

### Exit codes (`check`)

| Code | Meaning |
|---|---|
| 0 | Valid layout |
| 1 | Invalid layout (conflicts found) |
| 2 | Could not check (file not found, bad YAML, invariant violation, bad usage) |

### Solving a scenario

`hangarfit solve` takes a *scenario* YAML (fleet to park, optional per-plane pins, optional maintenance plane) and searches for a valid layout. The output is JSON-serializable; PNG renders are optional.

```bash
# Find one valid layout for a scenario
hangarfit solve tests/fixtures/solve_fresh_six_planes.yaml

# Reproducible search with a seed; render the result
hangarfit solve scenario.yaml --seed 42 --render out.png

# Find up to 3 diverse alternatives (each layout must differ from the others
# by at least 2 planes moved by 0.5 m or rotated by 30°)
hangarfit solve scenario.yaml --alternatives 3 --render out_{i}.png --write-yaml out_{i}.yaml

# Machine-readable output
hangarfit solve scenario.yaml --json

# Strict mode: exit non-zero if fewer than --alternatives layouts were found
hangarfit solve scenario.yaml --alternatives 3 --strict-k

# Budget the search to 5 wall-clock seconds (default 30)
hangarfit solve scenario.yaml --budget 5
```

A scenario YAML carries `fleet:` / `hangar:` refs plus a `fleet_in:` list (which planes are present), an optional `maintenance:` block (which plane is in the back bay), and an optional `constraints:` mapping (per-plane pins or `force_on_carts` locks). See `tests/fixtures/solve_*.yaml` for ready-to-read examples covering each constraint kind.

### Exit codes (`solve`)

| Code | Meaning |
|---|---|
| 0 | Found at least one valid layout (`status` = `found` or `found_partial`) |
| 1 | No valid layout found (`status` = `exhausted_budget` or `trivially_infeasible`); with `--strict-k`, also fires for `found_partial` |
| 2 | Could not solve (file not found, bad YAML, invariant violation, IO error during render/write, or `--render-paths` without `--render`) |
| 3 | `--render-paths` only: valid layout(s) found but the v1 tow planner could not route **any** of them. The layouts still render (without path overlays); each blocked layout gets a stderr warning naming the plane. Distinct from code 1 (no layout at all). |

`--render-paths` overlays each plane's tow path on the `--render` PNG(s) (one colour per plane). It tow-plans every returned layout; a layout the v1 planner cannot route is rendered without paths. If **at least one** candidate is routable the exit code is 0 (un-routable ones still warn); code 3 fires only when none are routable.

### JSON schemas

- `hangarfit check --json` emits payloads with `"schema": "hangarfit.check/v1"`.
- `hangarfit solve --json` emits payloads with `"schema": "hangarfit.solve/v1"`.

Bumping a schema version is reserved for breaking changes to the payload shape; additive fields do not bump the version.

## Run the tests

```bash
pytest
```

The test suite includes a strut-aware golden set for the collision checker covering the height-layer rule, the strut-blocks-nesting case, the maintenance-bay rule, the cart rule, and an all-nine-planes valid layout, plus the CLI's argparse dispatch, exit codes, JSON/render output, and override semantics. If those pass, the geometry and the CLI surface are intact.

## Project layout

```
src/hangarfit/      # models, loader, geometry, collisions, visualize, cli
data/               # fleet.yaml, hangar.yaml — placeholder measurements
layouts/            # hand-authored candidate layouts, one YAML per scenario
tests/              # pytest suite, including strut-aware collision golden tests
```

## Documentation

- [`docs/architecture/`](docs/architecture/) — the project's architecture documentation, in a slim subset of the [Arc42](https://arc42.org/) template. Read [§1 Introduction & Goals](docs/architecture/01-introduction-and-goals.md) and [§3 Context & Scope](docs/architecture/03-context-and-scope.md) first.
- [`docs/adr/`](docs/adr/) — Architecture Decision Records (the *why* behind the architecture). Start with [ADR-0000](docs/adr/0000-record-architecture-decisions.md), then read individual records as their topics come up.
- **Community & governance** — [`CONTRIBUTING.md`](CONTRIBUTING.md) (how to contribute), [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) (Contributor Covenant), and [`GOVERNANCE.md`](GOVERNANCE.md) (decision-making model and key roles).
- **Security & standards** — [`docs/security-posture.md`](docs/security-posture.md) (OpenSSF Scorecard structural zeros, explained), [`docs/openssf-best-practices-badge.md`](docs/openssf-best-practices-badge.md) (Best Practices Badge — Passing answer script), [`docs/openssf-best-practices-badge-silver.md`](docs/openssf-best-practices-badge-silver.md) (Best Practices Badge — Silver answer script), and [`docs/osps-baseline-L1.md`](docs/osps-baseline-L1.md) (OpenSSF Baseline Level-1 self-attestation).

## More depth

Architecture lives in [`docs/architecture/`](docs/architecture/) and [`docs/adr/`](docs/adr/) — start with [§8 Crosscutting Concepts](docs/architecture/08-crosscutting-concepts.md) for the parts-based collision rule, the coordinate convention (including the non-obvious heading transform), and the testing posture; the matching ADRs ([ADR-0001](docs/adr/0001-aircraft-parts-model.md), [ADR-0002](docs/adr/0002-determinant-minus-one-transform.md)) record *why* each decision has the shape it does. Read those before contributing anything geometric. [`CLAUDE.md`](CLAUDE.md) is the operational guide — workflow, subagents, project-local config — not the architectural canon.

## License

Licensed under the [Apache License 2.0](LICENSE).
