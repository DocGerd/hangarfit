# Examples

Demo and example artifacts for `hangarfit`. None of this is shipped in the wheel
or sdist — it is repo-only material you point the CLI at explicitly.

| Directory | What it is |
|---|---|
| [`layouts/`](layouts/) | Hand-authored **demo** layouts. They reference the **synthetic** placeholder fleet/hangar in the root [`data/`](../data/) directory (via `../../data/…`), so any verdict on them is illustrative, not authoritative. `example.yaml` is the canonical valid smoke test; `example_invalid.yaml` is its deliberately-broken companion that drives the conflict-overlay demo. |
| [`herrenteich/`](herrenteich/README.md) | The **real** Airfield Herrenteich dataset: a DWG-measured hangar plus a published-spec, second-source-verified fleet and a valid all-eight `layout.yaml`. Self-contained (same-directory `fleet.yaml` / `hangar.yaml` refs). |

## Real vs. synthetic — the invariant

The root [`data/`](../data/) directory holds the **synthetic** placeholders
(`fleet.yaml`, `hangar.yaml`) that are the project's stable demo/test fixtures —
every dimension there is eyeballed, pending real measurement.

`examples/herrenteich/` is the opposite: **real** data, kept deliberately
**separate** from those synthetic placeholders. Moving these example folders
under `examples/` does not change that separation — `data/` stays at the root
and unchanged; only the demo `layouts/` and the real `herrenteich/` dataset moved
here. See [`herrenteich/README.md`](herrenteich/README.md) and the root
[`CLAUDE.md`](../CLAUDE.md) for the full real-vs-synthetic taxonomy.

## Quick start

```bash
# Validate the default demo layout (synthetic data → illustrative verdict):
hangarfit check examples/layouts/example.yaml

# Validate the real Herrenteich "everyone home" layout (all eight occupants):
hangarfit check examples/herrenteich/layout.yaml
```
