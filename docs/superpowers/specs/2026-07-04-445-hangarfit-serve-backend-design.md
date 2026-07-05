# `hangarfit serve` — local loopback backend (Stage 3, capability E) — design

**Status:** Accepted (brainstormed 2026-07-04, scope confirmed **serve-only**). Implements issue
**#445** (Stage-3 `hangarfit serve`). Drag-to-fix (#911) and mover-pin (#912) remain separate
follow-ups that *depend on* this. Ships with a new **ADR-0030** recording the local-server
deployment model.

**Date:** 2026-07-04
**Author:** Claude (brainstorming session)
**Epic:** #436 (TypeScript migration & modular, extendable viewer architecture)
**Umbrella spec:** `docs/superpowers/specs/2026-07-03-editor-full-frontend-redesign-design.md` (capability E)
**Related:** #442 (Stage-2 editor, shipped) · #911 (drag, depends on this) · #912 (mover-pin, depends on this) ·
ADR-0002 (Python-owned det-−1 transform) · ADR-0003 (determinism) · ADR-0017 (offline single-file viewer) ·
ADR-0020 (viewer TS architecture) · ADR-0029 (editor transform policy) · new **ADR-0030** (this deployment model)

---

## 1. Why this exists

The v0.18.0 editor (`hangarfit view --solve --edit`, #442) is an **intent-capture surface** over an
already-solved layout: the user edits, clicks **Export scenario YAML**, and re-runs
`hangarfit solve` on the CLI by hand. Stage 3 (#445) closes that loop **in the browser**: a local
`hangarfit serve` backend lets the editor trigger the solve directly and live-re-render — the
**Calculate** button.

This is the deferred capability **E** from the umbrella spec. Per that spec's sequencing
(E → D → A2), `serve` is the unlocking piece: it is what drag-to-fix (#911) and mover hand-placement
(#912) depend on, because it lets Python own the (trivial, policy-fenced) coordinate inverse instead
of the browser (ADR-0029). This work item is **serve-only**; D and A2 are out of scope here.

### 1.1 The one non-negotiable invariant

**Python stays the solver and the transform authority.** `serve` adds **no** solver, geometry, or
transform logic. The whole `scenario → scene/v2` chain is already a pure pipeline; `serve` only swaps
the transport from *file* to *HTTP*. Because solving never leaves the single Python runtime:

- ADR-0002 (Python owns the determinant-−1 plane-local→world transform) is untouched — the browser
  still only *consumes* Python-emitted affines, never re-derives them.
- ADR-0003 (byte-identical determinism) is **transport-neutral**: one runtime, one seed, same result.
- ADR-0017's offline single-file export **survives unchanged** as the shareable/pure-view artifact;
  `serve` is an *additional*, opt-in deployment mode, not a replacement.

---

## 2. Architecture

### 2.1 Reused verbatim (the pure pipeline)

```
load_scenario  (loader.py:671)  →  solve  (solver.py:59)  →  build_scene  (scene.py:388)  →  scene/v2 dict
                                                            →  build_editor_context (viewer.py:129)
                                                            →  render_edit_viewer   (viewer.py:203)
```

`GET /` produces essentially the same HTML that `view --solve --edit` produces today (scene +
editor-context + `viewer.js` all inlined). Nothing in this chain changes.

### 2.2 Net-new units

| Unit | Purpose | Depends on |
|---|---|---|
| **`src/hangarfit/server.py`** | `serve(...)` entrypoint + `BaseHTTPRequestHandler` subclass. Owns the HTTP surface, the `Host`-header guard, and the `POST /solve` resolution. | `loader`, `solver`, `scene`, `viewer` (all existing); stdlib `http.server`, `webbrowser`, `tempfile` |
| **`serve` subparser + `cmd_serve`** in `cli.py` | Parse args, build the seed context (fleet/hangar/ground-object catalog), call `server.serve(...)`. Mirrors the existing `check`/`solve`/`view` pattern. | `server` |
| **`viewer/src/interaction/calculate.ts`** | The dormant-offline / live-under-serve **Calculate** control: gather intent → reuse `export.ts` → `POST /solve` → re-render via the existing scene-swap path. | existing `export.ts`, the `mount→buildWorld→checkAnchors` re-render path |
| **`<script id="serve-config">` flag** in the served shell | Tells the client it is running under `serve` (robust vs. sniffing `location.protocol`). Absent in the offline export → Calculate stays dormant. | `viewer.py` render path (serve variant) |

`server.py` is deliberately a **new module** rather than more surface on the already-1562-line
`cli.py`: the HTTP concern is cohesive and independently testable, and `cmd_serve` stays a thin
adapter.

---

## 3. Endpoints & the `/solve` contract

- **`GET /`** — run the initial `solve` on the seed scenario server-side, then return the edit-viewer
  HTML with scene + editor-context + `viewer.js` **inlined** (identical to the offline edit render,
  plus the `serve-config` flag). The page opens already showing a solved layout.
- **`POST /solve`** — request body is the editor's exported **Scenario YAML** (the same artifact the
  offline "Export scenario YAML" button downloads). The server resolves it against the seed scenario's
  directory context, runs the identical `solve` pipeline, and returns a JSON
  `{ "scene": <scene/v2>, "editorContext": <editor-context/v1> }` document (`Content-Type:
  application/json`). The client swaps the scene in place **and** re-mounts the editor on the
  refreshed context. *(Review refinement: the response carries the refreshed editor-context, not the
  bare scene, so the editor's "pin at current pose" re-bases on the new solved poses — the browser
  must not derive them itself, ADR-0002.)*
- **Errors:** a `LoaderError`/validation failure or an unsolvable scenario returns a `4xx`/`5xx` with a
  short JSON `{ "error": "…" }` body (not a stack trace); the client surfaces it inline without
  discarding the current render.

**Deliberately deferred / dropped (YAGNI):**

- **`POST /check`** (validity-only via `collisions.check`) is **deferred** — its only consumer is
  live drag-validity feedback, which lands with #911. Trivial to add then.
- **`GET /viewer.js`** as a separate endpoint is **dropped**: `viewer.js` is **inlined** into the
  shell exactly as the offline path does, which reuses the tested render path and removes a moving
  part. (Can be split out later if a caching/streaming need appears.)

### 3.1 The `/solve` resolution decision

`load_scenario(path, ...)` takes a **path**, not text, and resolves `fleet:`/`hangar:` refs
*relative to the scenario file's directory*. So the posted YAML cannot be handed to the loader as a
bare string. Three approaches were weighed:

| Approach | Mechanism | Trade-off |
|---|---|---|
| **Temp file in seed dir (chosen)** | Write the body to `tempfile.NamedTemporaryFile(dir=<seed scenario dir>, suffix=".yaml", delete=False)`, call `load_scenario(tmp)`, `finally: os.unlink`. | **Zero loader change.** `POST /solve` is *byte-identically* `hangarfit solve <exported.yaml>` — the strongest possible equivalence. Cost: a short-lived temp file in the project dir (owned + always unlinked). |
| Kwargs + strip | Strip `fleet:`/`hangar:` from the body; pass startup-resolved objects via `load_scenario(..., fleet=, hangar=, ground_objects=)`. | No temp file, no catalog re-read (faster), but diverges from "≡ offline solve" and must dance around the loader's kwarg-vs-YAML conflict guard. |
| New loader text entrypoint | Add `load_scenario_text(text, *, base_dir)`. | Cleanest long-term, but a `loader.py` change → triggers `silent-failure-hunter` and widens blast radius onto the loader. |

**Chosen: temp-file in the seed directory.** It keeps this PR entirely off `loader.py` and preserves
the invariant that a served solve is indistinguishable from re-running the exported file on the CLI.
The temp file is created with an explicit name in the seed scenario's directory (so relative
`fleet:`/`hangar:` refs resolve identically) and unlinked in a `finally` even on solve failure.

---

## 4. CLI shape & UX

```
hangarfit serve <scenario.yaml> [--port 8765] [--no-open] [--seed N] [--apron-depth N|auto] [--max-restarts N]
```

- `serve` **implies** `--solve --edit` (it is the live editor).
- **Loopback-only, no `--host` flag.** The server always binds `127.0.0.1`; a "bind to LAN" option is
  a deliberate non-feature (footgun). Recorded in ADR-0030.
- **`--port`** default `8765`, overridable. On `EADDRINUSE` the server exits with a clear message
  naming the flag (it does **not** silently pick another port — a stable, printed URL is friendlier
  and the failure is actionable).
- **Auto-open** the browser (`webbrowser.open(url)`); `--no-open` suppresses. The URL is always
  printed to stdout regardless.
- Solve-shaping flags (`--seed`, `--apron-depth`, `--max-restarts`) pass through to the seed solve
  **and** to each `POST /solve` (session-consistent). `--alternatives`/compare mode is **out of
  scope** — `serve` is single-solution edit mode.

---

## 5. Security / threat model (→ ADR-0030)

- **Bind `127.0.0.1` only.** No external interface is ever opened.
- **`ThreadingHTTPServer`** so a slow `POST /solve` doesn't block `GET /`/asset serving. Each request
  loads its own `Scenario` and solves independently — the solver holds **no shared mutable state**
  (ADR-0003: pure given seed), so concurrent requests are safe.
- **`Host`-header allowlist** — accept only `127.0.0.1[:port]` / `localhost[:port]`; anything else →
  `403`. This is the **DNS-rebinding guard**: a malicious page that resolves `evil.com → 127.0.0.1`
  still sends `Host: evil.com`, which is rejected. (CSRF blast radius is already tiny — see below —
  but the Host check is cheap and closes the rebinding vector cleanly.)
- **Input safety is inherited**, not re-implemented: `_read_yaml` uses `yaml.safe_load` (no
  arbitrary-object construction) and `_ALLOWED_SCENARIO_KEYS` is a strict top-level allowlist
  (unknown keys → `LoaderError`). The server's parse boundary is exactly the loader's.
- **Blast radius of `/solve`** = one CPU-bound solve returning a scene. No shell, no network egress,
  no filesystem writes beyond the temp scenario file the server creates and unlinks. The server only
  ever reads the catalog/hangar YAML it was launched against.
- Documented in ADR-0030 as an accepted local-developer-tool posture (single-user, loopback, no auth
  surface). Multi-user / remote serving is an explicit non-goal.

---

## 6. Client bootstrap & Calculate

- **`main.ts` gains a serve branch.** Boot order becomes: `#solutions` (compare) → `#serve-config`
  present (serve) → `#scene` (offline single). Serve mode still reads the **inlined initial scene**
  from `GET /` (so first paint needs no fetch); the `serve-config` flag only enables the live
  Calculate affordance and records the endpoint origin. The offline `#scene` path is byte-identical
  and untouched.
- **Calculate** (`viewer/src/interaction/calculate.ts`) is dormant in the offline export and live
  under serve. On click it:
  1. gathers the current intent (selection, priorities, pins, `door_order`, maintenance — whatever the
     editor already tracks),
  2. reuses the existing **`export.ts`** path to serialize the Scenario YAML (no new serializer),
  3. `POST`s that YAML to `/solve`,
  4. feeds the returned `scene/v2` through the **existing `mount → buildWorld → checkAnchors`
     re-render path** — the same scene-swap the #666 compare switcher already drives (proven to
     re-run the transform self-check per swap).
- On a `POST /solve` error, the banner/readout shows the server's `{error}` message and the current
  render is preserved.
- **`viewer.js` is rebuilt** from `viewer/src/*.ts` and committed in the same PR (the
  `viewer-build-drift` CI guard, #438).

---

## 7. Data flow

```
Offline (survives):   scenario.yaml --CLI--> solve --> build_scene --> inlined <script id=scene> --> viewer

Serve (new):
  GET  /       --> [seed] load_scenario --> solve --> build_scene + build_editor_context
                                        --> render_edit_viewer (scene+ctx+viewer.js+serve-config inlined) --> browser
  [edit in browser] --Calculate--> export.ts --Scenario YAML-->
  POST /solve  --> tempfile(dir=seed) --> load_scenario --> solve --> build_scene --> scene/v2 JSON -->
                   fetch() --> mount/buildWorld/checkAnchors --> re-render
```

The exported **Scenario YAML** is the single contract between UI and server — identical to the
offline round-trip artifact, just delivered over HTTP.

---

## 8. Testing strategy

- **Python integration (`tests/test_server.py`, new):** start `serve` on an **ephemeral port**
  (`port=0`, read back the bound port) in a background thread; then
  - `POST /solve` a fixture scenario → assert `200`, `application/json`, a valid `scene/v2` doc
    (schema version + expected top-level keys);
  - `GET /` → assert `200` HTML with inlined `#scene`, `#editor-context`, and `#serve-config`;
  - a non-loopback `Host` header → `403`;
  - an unknown method/path → `404`; a malformed YAML body → `4xx` with a JSON `{error}` (no stack
    trace, no `500` leak);
  - server-lifecycle: shuts down cleanly (no port leak between tests).
- **Node unit (`viewer/test/`):** Calculate payload shape (the exported YAML string is reused verbatim)
  + serve-mode detection (`serve-config` present vs. absent) — pure, no browser.
- **Headless http smoke (stretch):** chrome-headless (swiftshader) pointed at the live
  `http://127.0.0.1:<port>/`, assert the `checkAnchors` banner stays hidden. Kept minimal; the pure
  units carry the contract, this only proves the http bootstrap wires up.
- **Guards:** `scene-schema-guard` applies (the `serve-config` inline + any editor-context touch);
  **no** `determinism-guard` needed (transport-only, no `solver.py`/`towplanner.py` change).

## 9. ADR-0030 (to author alongside)

Records the deployment-model shift: from "a double-clicked offline file" to "a local loopback
server", framed as **additive**. Captures:

- **Decision:** stdlib `http.server` on `127.0.0.1`, no web framework (matches the no-heavy-deps
  ethos), Python remains solver/transform authority, offline export preserved.
- **Rejected / deferred:** in-browser Pyodide/WASM solve (**rejected** — re-opens the det-−1 trap and
  adds a second determinism runtime); desktop wrapper Tauri/pywebview (**deferred** — same
  "Python owns the solve" benefit, heavier tooling, nicer distribution; a future packaging option).
- **Threat model:** loopback-only, `Host`-header guard, `safe_load` boundary, no auth surface,
  single-user; multi-user/remote is a non-goal.
- Reaffirms ADR-0002/0003/0017; does not supersede them.

## 10. Non-goals / YAGNI

- Drag-to-fix (#911) and mover-pin (#912) — separate, dependent follow-ups.
- `POST /check` — deferred until #911 needs it.
- In-browser (Pyodide/WASM) solving — rejected.
- Any JS-authored coordinate inverse — rejected (ADR-0002/0029).
- Compare/`--alternatives` mode under serve; LAN/remote binding; auth.

## 11. Delivery

One feature branch `feature/445-serve-backend` → **one PR closing #445**, carrying: ADR-0030, this
spec, `server.py`, the `serve` CLI wiring, the client Calculate unit, the rebuilt `viewer.js`, and the
tests above. Internal order: Python backend (independently integration-testable) → client Calculate
wiring → `viewer.js` rebuild. `CHANGELOG.md [Unreleased]` gets a `serve` entry. Full `/pr-review` arc
(code-reviewer + `scene-schema-guard` + `silent-failure-hunter` for the new error paths) before ready.
