# ADR-0029: The interactive editor captures a pinned pose by copying Python-emitted scalars and exports a full `Scenario` YAML — the browser never composes a transform

- **Status:** Proposed
  <!-- Proposed at PR-open; Accepted at PR-merge. Records the intent-artifact
       contract + the ADR-0002 carve-out that Chunks 1–3 of #442 build on. -->

- **Date:** 2026-07-02
- **Deciders:** Patrick Kuhn (DocGerd)

> **Scope of this ADR.** It records the contract for the Stage-2 interactive
> placement editor (`hangarfit view --edit`, epic [#442](https://github.com/DocGerd/hangarfit/issues/442)):
> **how** the browser turns a user's intent — which planes go in, which matter
> more, which must sit in a specific spot — into a loader-valid `Scenario` YAML
> the Python solver re-runs, and **why** that is safe under
> [ADR-0002](0002-determinant-minus-one-transform.md). It activates the
> `interaction/` seam that [ADR-0020](0020-viewer-typescript-architecture.md)
> reserved. It does **not** change `scene/v2`, the solver, the loader, or the
> Python model — those authorities are reaffirmed unchanged.

## Context & Problem Statement

`hangarfit view` renders a solved layout into a read-only offline 3D HTML page.
The next step ([ADR-0020](0020-viewer-typescript-architecture.md) Stage 2) turns
that page into the tool's **front door**: a human captures *intent* and hands it
back to the Python solver. Intent has three parts — **select** which planes go in
(→ `fleet_in`), assign a soft **priority** (→ `PlaneConstraint.priority`, #441),
and mark a hard **must-position** (→ `PlaneConstraint.pin`).

The third part is the dangerous one. A "must-position" is a *pose* — an
`(x, y, heading)` in hangar coordinates — and [ADR-0002](0002-determinant-minus-one-transform.md)
establishes that the plane-local → world transform has **determinant −1** (a
reflection, not a rotation), so it is owned by tested Python and **never
re-derived in JavaScript**. The question this ADR answers: **how does the editor
let a user express and refine a hard pose, and export a complete, loader-valid
`Scenario`, without the browser ever composing or inverting that transform?**

## Decision Drivers

- **ADR-0002 is inviolate.** No `interaction/` module may compose, invert, or
  re-derive the determinant-−1 transform — that is the one hazard the whole
  viewer architecture (ADR-0002/0017/0020) is built to prevent.
- **Python stays the sole authority** for solving, geometry, and validity. The
  browser is an intent-capture surface with no oracle.
- **The exported artifact must round-trip unchanged** — `hangarfit solve
  EXPORTED.yaml` must consume it with no hand-editing.
- **Ship a correct MVP fast** with a boundary that is crisp and testable in the
  one language pytest cannot exercise.
- **Determinism / diffability** of the artifact — a stable YAML shape so two
  exports of the same intent are byte-identical.

## Considered Options

The crux is **how a hard must-position is captured and edited**:

1. **Pin-at-current-pose + numeric edit** (chosen). Toggling "pin here" copies
   the plane's *current* pose — already computed by Python and emitted as plain
   scalars in `editor-context.currentPoses[id]` — into the `Intent`; the user
   refines it with `x` / `y` / `heading` **number fields**. Pure data entry; the
   browser performs no geometry math.
2. **Live translation drag.** Drag the plane in the floor plane; the browser
   derives `x`/`y` from pointer deltas (heading stays a number field).
3. **Drag + rotate as a non-authoritative preview.** A full move+rotate gizmo;
   the browser composes a *preview* affine (clearly marked "not validated"),
   with authoritative geometry only after the Python re-solve.

A supporting sub-decision is **what the editor exports**:

- **A complete `Scenario` YAML** (chosen) — `fleet` / `hangar` / `fleet_in` /
  `maintenance` / `constraints`, self-contained.
- **A `constraints`-only fragment** the user hand-merges into their scenario.

## Decision Outcome

**Chosen option: Option 1 (pin-at-current-pose + numeric edit), exporting a
complete `Scenario` YAML**, because it is the only option in which the browser
performs **zero** geometry math — every pose value it emits *originated in
Python* — so ADR-0002 holds **by construction** rather than by discipline, and it
is the fastest path to a correct, testable MVP.

### The recorded contract — `Intent` → `Scenario`

The browser builds an `Intent` and serializes it deterministically. The
non-constraint scaffold it cannot recover from `scene/v2` alone (fleet/hangar
paths, the maintenance passthrough, and each plane's current pose as scalars) is
injected by `viewer.py` as an `EditorContext` blob.

| `Intent` / `EditorContext` field | `Scenario` YAML target |
|---|---|
| `EditorContext.fleet` / `.hangar` (path strings) | top-level `fleet:` / `hangar:` (echoed verbatim) |
| `Intent.selectedPlaneIds` ∪ `{maintenance.plane}` | top-level `fleet_in: [...]` |
| `EditorContext.maintenance.plane` | `maintenance:\n  plane: <id>` (passthrough; **also** in `fleet_in`) |
| `Intent.priorities[id]` (soft, ≥0) | `constraints[id].priority` |
| `Intent.mustPositions[id]` `{x,y,heading,onCarts}` | `constraints[id].pin` `{x_m, y_m, heading_deg, on_carts}` |

**Serializer invariants** (enforced in `export.ts`, checked by node + Python
tests):

- `fleet_in` = `selectedPlaneIds` ∪ `{maintenance.plane}` when present. The
  maintenance occupant **must** appear in `fleet_in` — `models.py`
  `Scenario.__post_init__` rejects otherwise, even though it is never
  rendered/selectable.
- `constraints` keys ⊆ `selectedPlaneIds` ⊆ `fleet_in`; the maintenance plane is
  **never** emitted under `constraints` (pinning it is forbidden).
- `priority` is **omitted when unset** (round-trips "user never set a priority"
  as absent, per the #441 `float | None` design).
- A `pin` block carries **all** of `x_m` / `y_m` / `heading_deg` / `on_carts`
  (loader `pin` validation); `on_carts` defaults from `currentPoses[id].on_carts`
  and is only user-toggleable for cart-eligible planes (`Scenario.__post_init__`
  cross-checks it against `movement_mode`).
- Numbers are formatted deterministically (fixed decimals) so the artifact is
  stable and diffable.

### The ADR-0002 carve-out (why this is safe)

"Pin here" is a **scalar copy**, not a geometry operation:
`mustPositions[id] = {x: currentPoses[id].x_m, y: …, heading: …, onCarts: …}`.
Every value already exists because Python computed the solved pose. Editing a pin
is typing into number fields. The browser **never** composes an affine, never
maps screen coordinates to floor coordinates, and never touches the `final_poses`
affines for pose purposes. The 3D scene shows the *last solved* geometry; the
authoritative re-render happens in Python after the re-solve. This keeps the
editor strictly on the data-entry side of the ADR-0002 boundary.

### Why not Option 2 (live translation drag)?

Reading `x`/`y` from pointer deltas requires mapping screen coordinates onto the
hangar floor plane — i.e. **inverting** the Python-owned world transform in
JavaScript, or re-deriving an independent ground-plane mapping that can silently
diverge from the `checkAnchors()` oracle. That is exactly the determinant-−1
math ADR-0002 forbids in JS. Recorded as future work behind a design that keeps
the derivation on the Python side.

### Why not Option 3 (drag + rotate preview)?

Composing even a "preview-only" affine reintroduces the determinant-−1
sign-flip trap in the one language pytest cannot exercise, and a
non-authoritative preview that later disagrees with the post-solve render is a
UX foot-gun (the user "placed" a plane the solver then moves). Deferred behind a
clearly-marked, explicitly-non-authoritative preview design; not part of the MVP.

### Why not a `constraints`-only fragment?

`hangarfit solve` consumes a whole `Scenario` (fleet / hangar / fleet_in /
maintenance / constraints). A fragment would force the user to hand-merge it into
their source scenario — a manual, error-prone step at odds with the "re-run
`solve EXPORTED.yaml`" round-trip. Emitting a complete, self-contained
`Scenario` makes the round-trip a single command; the scaffold the browser can't
recover from `scene/v2` is supplied by Python via `EditorContext`.

## Consequences

### Positive

- **ADR-0002 holds by construction**: the browser emits only Python-origin
  scalars, so no `interaction/` module needs — or is permitted — to do transform
  math.
- The exported artifact is a **self-contained, diffable, loader-valid
  `Scenario`** the user re-runs with one command.
- The capture/serialize logic (`selection.ts`, `export.ts`) is **pure** and
  node-unit-testable — coverage the single-file viewer could not have had.

### Negative

- **No live drag/rotate in the MVP.** A user repositioning "by feel" must read
  the current coordinates and type new ones; the 3D scene is the last-solved
  geometry, not a live manipulation preview. (Options 2/3 are the recorded
  upgrade path.)

### Neutral

- The `editor-context` blob is a **viewer-HTML-level artifact** (schema
  `hangarfit.editor-context/v1`), layered over an **untouched** `scene/v2`
  document — the same pattern as the `viewer-compare/v1` wrapper
  ([ADR-0017](0017-3d-viewer-architecture.md), #666). It is **not** a `scene/v2`
  schema change, so `scene.build_scene()` and its key-parity guard are untouched.
- Prior intent is **not echoed** on re-open (the editor starts blank); the user's
  intent persists in the exported YAML. Echoing it is deferred future work.

## Compliance

- **Grep-able hard rule**: no file under `viewer/src/interaction/` imports
  `affine.ts` or `anchors.ts` (also stated in `interaction/README.md` and the
  ADR-0020 seam rule).
- **`viewer/test/selection.test.ts`** pins that `pinAtCurrent` is a pure scalar
  copy of `currentPoses[id]` (no math) and that the state functions are pure.
- **`viewer/test/export.test.ts`** golden-tests the `Scenario`-YAML shape and the
  reject/omit paths (maintenance ∈ `fleet_in`; deselected planes absent;
  `priority` omitted when unset).
- **`tests/test_viewer.py`** Python round-trip: a representative exported YAML
  **loads via `load_scenario`** and yields the expected `PlaneConstraint`s — the
  authoritative loader-validity guard.
- **`scene/v2` untouched**: `tests/test_scene.py` byte-identity + the
  `scene-schema-guard` subagent; the `editor-context` blob is asserted additive
  (the `render_edit_viewer` render-path invariants in `tests/test_viewer.py`: the
  `#scene` bytes are identical to `render_viewer`'s, and the non-edit path emits
  no `#editor-context`).
- The runtime **`checkAnchors()`** self-check remains the fail-loud cross-language
  transform-**value** backstop.

## More Information

- Related ADRs: [ADR-0002](0002-determinant-minus-one-transform.md) (the transform
  retained in Python — the constraint this ADR is designed around),
  [ADR-0017](0017-3d-viewer-architecture.md) (the viewer architecture + the
  viewer-HTML-level blob precedent), [ADR-0020](0020-viewer-typescript-architecture.md)
  (the typed modular viewer whose `interaction/` seam this activates),
  [ADR-0019](0019-brand-tokens-single-source.md) (a prior viewer-injected blob),
  [ADR-0003](0003-rr-mc-solver-algorithm.md) (the determinism spirit the artifact
  formatting extends).
- Related specs: [`docs/superpowers/specs/2026-07-02-v0.18.0-interactive-placement-editor-design.md`](../superpowers/specs/2026-07-02-v0.18.0-interactive-placement-editor-design.md)
  (design) and [`docs/superpowers/plans/2026-07-02-v0.18.0-interactive-placement-editor.md`](../superpowers/plans/2026-07-02-v0.18.0-interactive-placement-editor.md)
  (implementation plan).
- Related issues / PRs: #442 (epic / MVP), #896 (Chunk 0 — this ADR), #440 (typed
  `scene-contract.ts` + inert `interaction/` seam), #441 (Python soft `priority`
  groundwork), #444 (deferred JSON-Schema single-source spike), #445 (deferred
  Stage 3 — `hangarfit serve` full frontend).
