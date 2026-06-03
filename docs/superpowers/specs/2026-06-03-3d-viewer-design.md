# 3D layout & tow-fill viewer (Phase 4, milestone #29)

- **Date:** 2026-06-03
- **Epic:** #392. **Issues:** #388 (scene/v1 builder), #389 (offline viewer:
  vendored Three.js + viewer.js + viewer.py), #390 (`hangarfit view` CLI), #391
  (docs: ADR-0017 + scene schema + arc42 + CLAUDE.md).
- **Status:** design record authored before implementation. User-approved the
  four shaping decisions on 2026-06-03 (see "Decisions" below).

## Problem

`hangarfit` validates a candidate hangar layout and renders a **top-down 2D PNG**
(`visualize.py`) for a human to eyeball, plus an optional 2D tow-path overlay.
A flat plan view cannot show what actually matters when wings stack: a high wing
overhanging another plane's tail is *valid* (z-disjoint) but reads identically in
plan view to an invalid cockpit overlap. The user wants:

1. a **full 3D** view of the hangar and each aircraft (box models, not realistic),
   so the vertical clearances the collision checker already reasons about are
   visible;
2. the **exact tow paths** replayable as a video / step-by-step view **with a
   slider** — i.e. an interactive scrub of the whole-hangar fill operation.

## What already supports this (no core changes needed)

- **The geometry is already 3D.** Every `Part` carries `z_bottom_m`/`z_top_m`
  (`models.py`), so each aircraft is *already* a stack of extruded oriented
  boxes. A 3D view renders dimensions the collision checker already uses.
- **The animation source already exists.** `DubinsArc.sample(step_m, step_deg)`
  yields a dense `Pose(x, y, heading)` stream along each plane's Reeds–Shepp tow
  path, and `MovesPlan.moves` is already in real execution order
  (`back_first_order`, deepest-first).
- **There is a single canonical transform.** `geometry.local_to_world()` is the
  one definition of the plane-local→world map; the determinant-−1 reflection
  (ADR-0002) lives only there.

## Goals

- A new, **decoupled** rendering path that does not touch the solver, collision
  checker, loader, or models.
- An interactive, **fully-offline, self-contained** HTML artifact: orbit camera,
  all planes as boxes, vertical stacking visible, conflicts highlighted, and a
  whole-fill **timeline** (scrub / play / pause / per-plane step).
- A documented, stable **`scene/v1` JSON contract** as the seam between the
  Python core and any viewer.

## Non-goals (YAGNI for the PoC)

- Realistic aircraft models / textures / lighting beyond flat-shaded boxes.
- A build toolchain (npm/Vite), a server, or any runtime network dependency.
- Editing layouts in 3D (view-only).
- Per-plane independent animation mode (deferred; whole-fill only — user choice).
- Re-implementing any transform / collision math in JavaScript.

## Decisions (user-approved, 2026-06-03)

1. **Viewer tech:** self-contained HTML + Three.js (WebGL).
2. **Offline:** fully offline — Three.js **vendored** into the repo.
3. **Integration:** a new **`hangarfit view`** subcommand (a documented
   `scene/v1` JSON export underlies it).
4. **Animation:** **whole-fill** timeline (planes enter back-first; one global
   slider scrubs the whole operation; step = per-plane).

## Architecture

Three layers, each ignorant of the next's internals:

```
Layout / MovesPlan / CheckResult        existing core — UNTOUCHED
        │
        ▼
src/hangarfit/scene.py                   NEW pure builder → scene/v1 dict
        │  (all geometry + transform math lives here)
        ▼
src/hangarfit/viewer.py                  NEW scene dict → one self-contained .html
        │  (inlines scene JSON + data:-import-map for vendored three + viewer.js)
        ▼
src/hangarfit/_viewer_assets/viewer.js   NEW thin Three.js app (pure scene consumer)
vendor/three/<version>/{three.module.js,OrbitControls.js,README,LICENSE}
```

The **`scene/v1` dict is the contract.** `scene.py` is a leaf consumer of the
core types (same role `visualize.py` plays for the 2D PNG); `viewer.js` is a leaf
consumer of `scene/v1` and knows nothing about hangarfit internals.

### The det-−1 transform: Python owns it, JS only consumes matrices

The single biggest risk is the documented determinant-−1 trap (ADR-0002):
plane-local→world is a rotation **composed with a reflection**. Re-deriving that
in JavaScript would re-open the exact off-by-handedness trap the project warns
about, in an untested language.

**Decision:** the viewer performs **no transform math**. For each animation frame
and each plane, `scene.py` emits the explicit world affine as six floats

```
world_x = a·u + b·v + tx       a =  sin(h)   b =  cos(h)   tx = x_m
world_y = c·u + d·v + ty       c =  cos(h)   d = −sin(h)   ty = y_m
```

computed from the **same** formula as `geometry.local_to_world` (h =
`radians(heading_deg)`, `(u, v)` = plane-local `(forward, right)`). The viewer
builds a `THREE.Matrix4` from `(a,b,tx,c,d,ty)` and assigns it to a statically
built plane-local `Group` (`matrixAutoUpdate = false`). The reflection rides
inside the matrix; because its determinant is negative, **box materials use
`side: THREE.DoubleSide`** so the reflected winding is not back-face-culled.

This keeps **all** transform logic in Python, where the canonical, tested
formula lives, and reduces `viewer.js` to matrix application.

**Cross-language guard:** `scene.py` also emits, per plane at its *final*
placement, the world corner points of each box computed by the production
`aircraft_parts_world()` oracle (the `anchors` block). `viewer.js` recomputes the
same corners from its box geometry + the final-frame matrix and **asserts
agreement on load** (throws to the console + an on-page banner if it diverges) —
a fail-loud check that the matrix path matches the oracle. A Python unit test
pins the affine formula against `aircraft_parts_world` so drift is caught in CI
(JS cannot run in pytest).

## The `scene/v1` schema

A single JSON-serializable dict (`build_scene(...) -> dict`), inlined into the
HTML. Field-for-field:

```jsonc
{
  "schema": "hangarfit.scene/v1",
  "units": "m",
  "coordinate_note": "world: x right along door wall, y deeper into hangar, z up. "
                     "heading_deg compass-style (from +y, CW+). See ADR-0002.",
  "hangar": {
    "length_m": 25.0, "width_m": 18.0,
    "door": { "center_x_m": 9.0, "width_m": 12.0 },
    "maintenance_bay": {
      "center_x_m": 13.5, "width_m": 9.0, "depth_m": 9.0,
      "closed": true, "plane_id": "fk9_mkii"        // closed iff a maintenance_plane is set
    }
  },
  "planes": [
    {
      "id": "aviat_husky",
      "color": "#0079B5",                            // reuse visualize.PLANES, keyed by sorted id
      "boxes": [                                     // plane-local, built ONCE
        { "kind": "fuselage_front",
          "cx": 1.2, "cy": 0.0, "cz": 0.75,          // local (forward, right, height) centre
          "length_m": 3.0, "width_m": 0.7, "height_m": 1.5,
          "angle_deg": 0.0 }                         // part.angle_deg (CCW within plane-local)
        // ... one per Part
      ]
    }
  ],
  "timeline": {
    "total_s": 24.0,
    "segments": [                                    // one per plane, in entry (back-first) order
      { "plane_id": "fk9_mkii",
        "start_s": 0.0, "end_s": 3.2,
        "samples": [ [0.0,1.0,9.0,1.0,0.0,0.0], ...] // 6-float affines along the path, door→slot
      }
      // ... remaining planes, start_s == previous end_s (strictly sequential)
    ]
  },
  "final_poses": {                                   // each plane at its slot (== its segment's last sample)
    "fk9_mkii": [0.0, 1.0, 9.0, 1.0, 0.0, 0.0]
  },
  "conflicts": ["plane_a", "plane_b"],               // plane ids to tint red (from CheckResult)
  "anchors": {                                       // final-placement world corners, per plane per box
    "aviat_husky": [ [[x,y],[x,y],[x,y],[x,y]], ... ]
  }
}
```

### Timeline construction

- Entry order = `back_first_order(target_layout.placements)` (deepest-first;
  shallower slots are obstacles for deeper ones).
- Each plane's move = sampled `move.path.sample(step_m=…, step_deg=…)` → a list of
  `Pose`. Each pose becomes a 6-float affine via the formula above; the ordered
  list is the segment's `samples` (door→slot).
- **Sequential whole-fill:** segments are laid end-to-end — `segment[k].start_s ==
  segment[k-1].end_s`. Per-plane duration ∝ path length (`DubinsArc.length_m`) via
  a fixed tow speed, clamped to a sane `[min, max]` per plane; `total_s` is the
  last `end_s`. `final_poses[p]` is plane *p*'s last sample (its parked affine).
- **Lean by construction:** a parked plane is *not* repeated in later segments —
  the viewer derives each plane's state from time *t* against the segments:
  - `t < start_s` → **hidden** (plane is still outside the hangar);
  - `start_s ≤ t < end_s` → **animating**, affine = `samples[round(frac·(n−1))]`;
  - `t ≥ end_s` → **parked**, affine = `final_poses[plane_id]`.
- Sample count per path is bounded: the `step_m`/`step_deg` passed to
  `DubinsArc.sample` is coarsened if a path would emit more than a cap, to keep
  the HTML small.
- **No `MovesPlan`** (static-only / unroutable layout) ⇒ `segments == []`,
  `total_s == 0`, and `final_poses` carries every plane at its slot; the viewer
  shows the static scene and disables the transport controls.

### Determinism

`build_scene` is pure and deterministic: planes sorted by id, frames in entry
order, floats from deterministic closed-form paths. Same `(Layout, MovesPlan,
CheckResult)` ⇒ byte-identical scene dict (same spirit as ADR-0003). A golden
test pins this.

## The viewer (`viewer.js`)

Hand-written ES module, no framework, no build step:

- **Scene:** hangar floor (translucent plane) + wireframe walls with a door gap;
  closed maintenance bay as a red translucent box; ground grid + axes helper.
- **Planes:** each a `THREE.Group` of `BoxGeometry` children placed at plane-local
  box centres (with `angle_deg` about local Z); group transform = per-frame
  `Matrix4` from the affine. `material.side = DoubleSide`. Conflicting planes
  tinted toward red.
- **Camera:** `PerspectiveCamera` + `OrbitControls`; sensible default framing the
  hangar; a "reset view" button.
- **HUD (plain DOM/CSS):** timeline slider (scrub), play / pause, speed (0.5/1/2×),
  per-plane step ◀▮▶, current-plane + clock readout, a legend (plane id → colour),
  and a toggle for hangar-wall opacity.
- **Robustness:** the load-time `anchors` self-check; an on-page error banner if
  WebGL is unavailable or the anchor check fails (fail-loud, no silent blank
  canvas).

## `viewer.py` — self-contained HTML assembly

`render_viewer(scene: dict, output_path) -> None`:

- Read the vendored `three.module.js` and `OrbitControls.js` and the
  `_viewer_assets/viewer.js`.
- Emit **one** `.html` with:
  - an **import-map** mapping `"three"` and
    `"three/addons/controls/OrbitControls.js"` to `data:text/javascript;base64,…`
    URLs of the vendored sources. A `data:` URL in an import-map resolves from a
    `file://` page with **zero network**, sidestepping the ES-module `file://`
    CORS block without a server.
  - the scene inlined as `<script type="application/json" id="scene">…</script>`
    (no `fetch()` — `fetch` is CORS-blocked from `file://`).
  - `viewer.js` inlined as `<script type="module">…</script>`.
- Result: a double-clickable, fully-offline HTML file (the HTML analogue of the
  PNG artifact).

## Offline & vendoring

- `vendor/three/<version>/` holds `three.module.js`, `OrbitControls.js`, the MIT
  `LICENSE`, and a `README.md` recording version, upstream source URLs, and
  **SHA-256** of each file (matches the repo's hash-pinning / OpenSSF posture).
- A pinned Three.js release line (r0.16x — exact version chosen at vendor time
  and recorded). The PoC adds the files manually; a documented refresh procedure
  goes in the vendor README. (If the user prefers a fetch-on-build step instead
  of committing ~1 MB, that's a noted alternative — default is commit, for
  offline reproducibility.)

## CLI: `hangarfit view`

```
hangarfit view LAYOUT.yaml -o out.html [--fleet P] [--hangar P] [--check] [--no-animate]
hangarfit view --solve SCENARIO.yaml -o out.html [--fleet P] [--hangar P] [solve opts]
```

- **Layout mode:** load + (default) `plan_fill` the layout for the tow animation,
  **best-effort** — if unroutable, emit the static scene + a stderr note (mirrors
  `--render-paths` degradation; never a hard failure for an otherwise-valid
  layout). `--check` overlays conflicts. `--no-animate` skips tow planning.
- **Solve mode:** `--solve` runs the solver, takes the first returned layout +
  its bundled `MovesPlan`, and views that.
- Exit codes: `0` success; `2` usage error (e.g. missing `-o`); load/solve errors
  reuse the existing patterns. (No new exit-code semantics for the PoC.)

## Testing & determinism

- **`scene.py` (pure, TDD):**
  - schema shape + required keys/types;
  - **matrix correctness vs `aircraft_parts_world` oracle** — the affine applied
    to each plane-local box corner equals the oracle's world corner (incl. a
    non-axis-aligned 45° heading, per the geometry-test convention);
  - timeline segments in `back_first_order`; sequential (`start_s[k] ==
    end_s[k-1]`); `final_poses` == each segment's last sample;
  - static-only (no `MovesPlan`) ⇒ `segments == []`, `total_s == 0`,
    `final_poses` covers all planes;
  - **byte-identical golden** for a fixed input.
- **`viewer.py` (smoke):** valid HTML; scene JSON present and parseable; three
  source embedded; **no `http(s)://` references** (offline guarantee asserted by
  a regression test); import-map present.
- **CLI:** arg parsing, both modes, `-o` written, unroutable degradation, exit
  codes.
- **`viewer.js`:** not unit-tested in pytest (no JS runtime); its contract is
  pinned Python-side (the affine formula test) + the in-browser load-time anchor
  assert. Manual verification via the `/verify` flow on the produced HTML is part
  of acceptance.

## Process

- GitFlow: feature branch `feature/3d-viewer` off `develop`; PR into `develop`
  with `Closes #388 #389 #390 #391` and `Closes #392`; full pr-review-toolkit arc
  (code-reviewer; comment-analyzer for the docs; type-design-analyzer not needed —
  `models.py` untouched; geometry-invariant-guard *not* triggered — `geometry.py`
  / `collisions.py` untouched, but the affine-vs-oracle test is the equivalent
  guard for this PR). User is sole approver/merger.
- The tightly-coupled PoC ships as **one feature branch**; the four issues are
  the logical decomposition and are closed together. Follow-up polish (per-plane
  mode, richer models, legend niceties) becomes new issues if wanted.

## PoC acceptance ("done")

`pip install -e ".[dev]"` then
`hangarfit view layouts/example.yaml -o out.html`; double-click `out.html`
**offline** →

- orbitable 3D hangar with door gap, walls, ground grid;
- every placed plane as its stack of boxes, vertical stacking visible;
- conflicts (if any) tinted red;
- a working whole-fill timeline: scrub, play/pause, per-plane step;
- `pytest`, `ruff check`, `ruff format --check`, `mypy` all green.

## Alternatives considered (rejected)

| Decision | Chosen | Rejected | Why |
|---|---|---|---|
| World transform | Python emits per-frame affine | JS re-derives `local_to_world` | avoids re-opening the det-−1 trap in untested JS |
| Geometry per frame | static group + `Matrix4` swap | rebuild `ExtrudeGeometry`/`BoxGeometry` per frame | avoids GC churn; matrix swap is O(1) |
| Offline delivery | `data:` import-map, vendored | CDN import-map / npm bundle | zero network, no node toolchain, no supply-chain blow-up |
| Scene assembly | one self-contained HTML | `scene.json` + `fetch()` | `fetch()` is CORS-blocked from `file://`; inlining = double-click-to-open |
| Viewer tech | Three.js + WebGL | matplotlib-3D / PyVista / vedo | interactivity + scrub + shareable single file; no display-server / VTK dep |
| Animation | whole-fill sequential timeline | per-plane only | matches the physical tow operation (user choice) |

## Risks

- **Det-−1 trap on the JS side** — mitigated by Python-owns-the-transform +
  affine emission + the load-time anchor self-check + the Python formula test.
- **Reflected-matrix rendering** (back-face culling, normals) — mitigated by
  `DoubleSide`; verified visually in the `/verify` pass.
- **HTML size** (vendored three ≈ 1 MB + frames) — mitigated by base64 overhead
  being acceptable for an offline artifact and by capping frame count via
  adaptive sampling.
- **`file://` ES-module quirks across browsers** — the `data:` import-map is the
  known-robust technique; verified in the acceptance pass.

## Open questions

- Vendor-commit vs fetch-on-build for Three.js (default: commit). Flagged to the
  user; proceed with commit unless told otherwise.
