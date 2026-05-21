# hangarfit

![CI](https://github.com/DocGerd/hangarfit/actions/workflows/ci.yml/badge.svg?branch=develop)

An on-demand exception tool for a flying club's hangar parking.

The club parks nine aircraft in a deep, stack-style hangar with a single door at the front. There's a standard layout that works for the standard situation — every plane back from its flight at the expected time, no surprise maintenance, no late returns. When that standard situation breaks (a plane comes back late, a maintenance slot moves, two planes need to swap order), someone has to come up with an alternative parking arrangement on the spot. `hangarfit` is the tool that checks whether a proposed alternative is physically valid: no fuselage, wing, or strut collisions; everything fits inside the hangar; the plane scheduled for maintenance ends up at the back where the maintenance bay is.

It also renders a top-down PNG so a human can sanity-check the result by eye.

It is not a planner. It does not search for a layout — you hand it one, it tells you whether it works.

## Scope

**Phase 1 (current focus).** Build the substrate: an aircraft + hangar data model, a parts-based collision checker, a matplotlib top-down visualizer, and a CLI that ties them together. Phase 1 is about getting the geometry right — once the collision rule is trustworthy, anything downstream can be built on top of it.

**Explicitly out of scope for Phase 1:**

- No planner, search, or optimization — you provide the candidate layout.
- No movement-sequence planning (no "in what order do I roll planes out and back in to reach this layout").
- No tracking of hangar state across runs.
- No GUI or web frontend.
- No handling of late arrivals as a live event stream.

These boundaries are deliberate. The collision model is the load-bearing piece; layering search on top of a wobbly geometry foundation would compound errors.

## Status

Pre-release. Phase 1 is feature-complete (the CLI shipped in v0.3.0). All dimensions in `data/` are placeholders pending real measurement and are flagged as such in the YAML; collision-checker output on the current data is illustrative, not authoritative.

Follow progress in [GitHub Issues](https://github.com/DocGerd/hangarfit/issues) and milestones.

## Install

Requires Python 3.11 or newer.

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

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Valid layout |
| 1 | Invalid layout (conflicts found) |
| 2 | Could not check (file not found, bad YAML, invariant violation, bad usage) |

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

## More depth

[`CLAUDE.md`](CLAUDE.md) is the durable project spec: the fleet (nine aircraft, mostly high-wing, with one low-wing), the parts-based collision rule, the coordinate convention (including the non-obvious heading transform), and the Phase 1 deliverables list. Read it before contributing anything geometric.

## License

Licensed under the [Apache License 2.0](LICENSE).
