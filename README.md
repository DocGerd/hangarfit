# hangarfit

Helper tool for arranging the flying club fleet in a stack-style hangar when the standard layout doesn't work. **On-demand exception tool**, not a daily ops system.

Given a hand-authored candidate layout (in YAML), `hangarfit`:

1. checks whether the layout is **physically valid** (no fuselage / wing / strut collisions, fits in the hangar, maintenance plane in the right spot), and
2. **renders** a top-down PNG so a human can eyeball it.

A planner / search algorithm is **out of scope for Phase 1**. Phase 1 builds the substrate (data model, collision checker, visualizer) that any future planner will sit on top of.

## Status

Phase 1 in active development. See [GitHub Issues](https://github.com/DocGerd/hangarfit/issues) and milestones.

## Quickstart (Phase 1 placeholder — not all components exist yet)

```bash
pip install -e ".[dev]"
pytest
hangarfit check layouts/example.yaml --render out.png
```

End-to-end usage examples will land with the CLI in milestone `v0.3.0`.

## Project layout

```
src/hangarfit/      # Python package (models, loader, geometry, collisions, visualize, cli)
data/               # fleet.yaml, hangar.yaml — measured-once club data
layouts/            # hand-authored candidate layouts (one .yaml per scenario)
tests/              # pytest suite, including the 12 collision golden tests
```

## Workflow

This repo uses GitFlow with PR-driven review. See [`CLAUDE.md`](CLAUDE.md) for the full workflow and project context.

## License

MIT
