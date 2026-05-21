# hangarfit

An on-demand exception tool for a flying club's hangar parking.

The club parks nine aircraft in a deep, stack-style hangar with a single door at the front. There's a standard layout that works for the standard situation — every plane back from its flight at the expected time, no surprise maintenance, no late returns. When that standard situation breaks (a plane comes back late, a maintenance slot moves, two planes need to swap order), someone has to come up with an alternative parking arrangement on the spot. `hangarfit` is the tool that checks whether a proposed alternative is physically valid: no fuselage, wing, or strut collisions; everything fits inside the hangar; the plane scheduled for maintenance ends up at the back where the maintenance bay is. It also renders a top-down PNG so a human can sanity-check the result by eye.

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

Pre-release. Phase 1 is in active development — the data model, loader, geometry primitives, collision checker, and visualizer have landed; the CLI is the last piece before the first tagged cut. All aircraft and hangar dimensions in `data/` are placeholders pending real measurement and are flagged as such in the YAML; collision-checker output on the current data is illustrative, not authoritative.

Follow progress in [GitHub Issues](https://github.com/DocGerd/hangarfit/issues) and milestones.

## Install

Requires Python 3.11 or newer.

```bash
pip install -e ".[dev]"
```

This installs the package in editable mode along with the test dependencies (`pytest`).

## Smoke test

```bash
pytest
```

The test suite includes a strut-aware golden set for the collision checker (same-height wing overlap, high-over-low height-disjoint pass, strut-blocks-nesting, the maintenance-bay rule, the cart rule, and an all-nine-planes valid layout). If those pass, the geometry is intact.

The end-to-end CLI (`hangarfit check layouts/example.yaml --render out.png`) is tracked in issue #7 and not yet shipped.

## Project layout

```
src/hangarfit/      # models, loader, geometry, collisions, visualize (CLI pending)
data/               # fleet.yaml, hangar.yaml — placeholder measurements
layouts/            # hand-authored candidate layouts, one YAML per scenario
tests/              # pytest suite, including strut-aware collision golden tests
```

## More depth

[`CLAUDE.md`](CLAUDE.md) is the durable project spec: the fleet (nine aircraft, mix of high-wing strut-braced, high-wing cantilever, and one low-wing), the parts-based collision rule, the coordinate convention (including the non-obvious heading transform), and the Phase 1 deliverables list. Read it before contributing anything geometric.

A `CONTRIBUTING.md` will land alongside the public release.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
