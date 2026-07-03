# ADR-0030: `hangarfit serve` — a local loopback backend so the editor triggers the solve

- **Status:** Accepted
- **Date:** 2026-07-04
- **Deciders:** Patrick Kuhn (DocGerd)

> **Scope of this ADR.** It records the deployment-model shift for the Stage-3
> editor ([#445](https://github.com/DocGerd/hangarfit/issues/445)): from a
> double-clicked **offline file** to an **additional**, opt-in **local loopback
> server** that runs the *unchanged* Python solver and returns a `scene/v2`
> payload the viewer renders. The offline single-file export
> ([ADR-0017](0017-3d-viewer-architecture.md)) survives unchanged. It does **not**
> change `scene/v2`, the solver, the loader, or the coordinate transform — those
> authorities are reaffirmed unchanged.

## Context

The `--edit` viewer ([#442](https://github.com/DocGerd/hangarfit/issues/442),
[ADR-0029](0029-editor-intent-artifact-contract.md)) captures a
user's intent and exports a `Scenario` YAML they re-run on the CLI by hand. Closing
that loop *in the browser* — a **Calculate** button — needs a solver the page can
call. A `file://` page cannot even `fetch` (CORS; ADR-0017 is the very reason the
offline build inlines its scene), so an in-page solve is impossible without a
runtime the page can reach.

The whole `scenario → scene/v2` chain is already a **pure pipeline** (`load_scenario
→ solve → build_scene`). Delivering it over HTTP instead of a file changes only the
*transport*, not the computation.

## Decision

Add a `hangarfit serve <scenario>` subcommand: a **stdlib `http.server`** bound to
**`127.0.0.1`** exposing

- `GET /` — the inlined interactive-editor viewer (the initial solved scene), and
- `POST /solve` — body = an exported `Scenario` YAML → a `scene/v2` JSON document.

It reuses `load_scenario → solve → build_scene` verbatim; the POST body is resolved
via a temp file written in the seed scenario's directory, so a served solve is
byte-identically `hangarfit solve <exported.yaml>`.

- **No web framework** — stdlib only, matching the project's no-heavy-deps ethos.
- **Python stays the authority** — the determinant-−1 plane-local→world transform
  ([ADR-0002](0002-determinant-minus-one-transform.md)) and byte-identical
  determinism ([ADR-0003](0003-rr-mc-solver-algorithm.md)) are untouched because
  solving never leaves the one runtime. The browser only ever *consumes* a
  Python-emitted scene.
- **Loopback-only, no `--host`.** A LAN/remote bind is a deliberate non-feature.
- **Threat model:** a `Host`-header allowlist (`127.0.0.1` / `localhost`) is the
  DNS-rebinding guard (a page resolving `evil.com → 127.0.0.1` still sends
  `Host: evil.com`); YAML input safety is inherited from the loader's
  `yaml.safe_load` + scenario-key allowlist; `/solve`'s blast radius is one
  CPU-bound solve — no shell, no network egress, no writes beyond a temp file the
  server owns and unlinks.

## Alternatives considered

- **In-browser Pyodide/WASM solve — rejected.** Re-opens the determinant-−1 trap
  (ADR-0002) in a second language CI cannot exercise, and introduces a second
  determinism runtime (ADR-0003).
- **Desktop wrapper (Tauri / pywebview) — deferred.** Same "Python owns the solve"
  benefit, heavier tooling, nicer distribution; a viable future *packaging* of the
  same idea, not a different decision about where the solve runs.

## Consequences

- Unlocks the drag-to-fix ([#911](https://github.com/DocGerd/hangarfit/issues/911))
  and mover-pin ([#912](https://github.com/DocGerd/hangarfit/issues/912))
  follow-ups: the browser can round-trip a dragged pose as *intent* and let Python
  own the (trivial, self-inverse) coordinate inverse, honouring ADR-0002 by
  construction.
- `serve` is an additional deployment mode; the offline single-file export remains
  the shareable/pure-view artifact.
- Reaffirms ADR-0002/0003/0017; supersedes none.
