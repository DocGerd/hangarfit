# hangarfit

Helper tool for arranging the flying club fleet in a stack-style hangar when the standard layout doesn't work. **On-demand exception tool**, not a daily ops system.

Given a hand-authored candidate layout (in YAML), `hangarfit`:

1. checks whether the layout is **physically valid** (no fuselage / wing / strut collisions, fits in the hangar, maintenance plane in the right spot), and
2. **renders** a top-down PNG so a human can eyeball it.

A planner / search algorithm is **out of scope for Phase 1**. Phase 1 builds the substrate (data model, collision checker, visualizer) that any future planner will sit on top of.

## Status

Phase 1 in active development. See [GitHub Issues](https://github.com/DocGerd/hangarfit/issues) and milestones.

## Quickstart

What works today (after merging the scaffolding PR):

```bash
pip install -e ".[dev]"
pytest                              # currently collects 0 tests
```

The full end-to-end loop will work once milestone `v0.3.0` ships the CLI:

```bash
hangarfit check layouts/example.yaml --render out.png   # ← lands in #7
```

## Project layout (target — most files land in later milestones)

```
src/hangarfit/      # Python package (models, loader, geometry, collisions, visualize, cli)
data/               # fleet.yaml, hangar.yaml — measured-once club data
layouts/            # hand-authored candidate layouts (one .yaml per scenario)
tests/              # pytest suite, including the strut-aware collision golden tests
```

## Workflow

This repo uses GitFlow with PR-driven review. See [`CLAUDE.md`](CLAUDE.md) for the full workflow and project context.

## License

MIT
