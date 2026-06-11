# #605 — Herrenteich full-set static calibration + ground-object catalog

**Date:** 2026-06-11
**Issue:** #605 (Stage A / Epic #600, milestone #34) — *Calibrate Herrenteich
dims/clearances so the full real set is provably feasible.*
**Branch:** `feature/605-herrenteich-full-set-calibration`
**Status:** design — awaiting approval

---

## 1. Scope (this PR)

Deliver the **static-feasibility** half of #605: make the **full real
Herrenteich set** — 8 aircraft + VW Caddy + 2 glider trailers + 1 fixed fuel
trailer — pass `hangarfit check` as a single checked-in reference layout, with
the four ground objects given a real catalog home and a documented dims/clearance
calibration audit.

**Explicitly deferred** (gated on unbuilt siblings, per the issue's phased plan):

- **Tow-routability of the full set** → needs mover route search (**#602**). The
  18 m Scheibe is *not* tow-routable today (the #599 wall), and the two glider
  trailers + Caddy are routed movers whose `Move.path` is `None` until #602.
- **The hard Caddy nearest-door / egress gate** → a new rejection tier (**#603**).
  This PR *places* the Caddy near the door as the intended arrangement but does
  **not** enforce egress as a hard constraint.
- **Rendering ground objects** in PNG / scene-v2 (**#606**).

This split is sanctioned by the issue: *"If … the ground-object models are not yet
landed, do the aircraft-only calibration first … and gate Phase 2 on the object
models."* #601 landed the static ground-object model (keep-out + pairwise) but not
routing; this PR consumes that static model to its full extent.

## 2. Why a calibration is needed (empirical findings)

Measured this session by driving `collisions.check` directly (read-only probes):

1. The committed 8-aircraft `examples/herrenteich/layout.yaml` is **static-valid**
   (`check` exit 0) but **not tow-routable** (Scheibe can't thread the 13.46 m
   door) — routing stays deferred to #602.
2. Against that **fixed** 8-aircraft nest, the Caddy and fuel trailer fit fine,
   but a **9 m glider trailer fits zero positions at any clearance, down to 0.0** —
   the nest leaves no 9 m corridor. **Clearance is not the only lever; the
   aircraft arrangement is.**
3. A random-restart + annealing search over **all 11 bodies** (driving the
   checker directly — the same technique that produced the original `layout.yaml`)
   establishes the feasibility frontier:

   | `clearance_m` / `wing_layer_clearance_m` | full set valid? |
   |---|---|
   | 0.30 / 0.20 (current placeholder) | ✗ (1 residual conflict) |
   | 0.25 / 0.15 | ✗ (1 residual conflict) |
   | 0.20 / 0.20 | ✗ (1 residual *wing-layer* conflict) |
   | **0.22 / 0.15** | ✓ |
   | **0.20 / 0.15** | ✓ (chosen) |
   | 0.20 / 0.12 | ✓ |

   The full real set fits **only snugly** — feasible at `clearance_m ≤ ~0.22` **and**
   `wing_layer_clearance_m ≤ ~0.15`. Both placeholders (0.3 / 0.2) are simply too
   loose to model a real club hangar packed with 8 aircraft + 4 vehicles.

This is exactly the issue's **hypothesis #2** ("too-generous clearances … a
wing-layer clearance that is too large turns legal z-disjoint wing-over-tail
nesting into a false collision"). **No aircraft dimensions need to change**
(hypothesis #1 is *not* required); the dimension audit is documentation-only.

## 3. The calibration (the one real model change)

Edit **`examples/herrenteich/hangar.yaml`** only:

```yaml
clearance_m: 0.20            # was 0.30 — calibrated to real club packing density
wing_layer_clearance_m: 0.15 # was 0.20 — 0.20 falsely rejected legal nestings
```

**Why this is safe and local:**

- **Monotonic.** Lowering a clearance only *relaxes* the collision constraint, so
  any layout valid at 0.3/0.2 stays valid at 0.20/0.15. The existing 8-aircraft
  `layout.yaml` and `scenario_demo.yaml` cannot be broken by this change.
- **Local.** The synthetic `data/` fixtures and `examples/layouts/example.yaml`
  use `data/hangar.yaml` — a *different* file, untouched. The issue's "confirm the
  looser value doesn't break other fixtures" audit reduces to: re-run their checks
  (they pass unchanged) — done as a verification step, not a code concern.
- **Defensible against reality.** Reality clears fins "~0.40 m by hand"
  (`layout.yaml:11`) — well above the 0.15 model threshold, so 0.15 never accepts a
  nesting reality wouldn't. 0.20 m lateral gaps match hand-pushed club packing.

## 4. The four ground-object catalog entries

New files in **`data/catalog/`** (the shared central catalog home, #595/#601), each
`measured: false` with inline published/typical-spec citations:

| File | `type:` | object_class | Envelope (L×W×H, m) | Source basis |
|---|---|---|---|---|
| `vw_caddy.yaml` | `car` | mover (steerable) | 4.88 × 1.79 × 1.84 | VW Caddy Maxi (long-wheelbase) manufacturer data |
| `glider_trailer_1.yaml` | `trailer` | mover (towed) | 9.0 × 2.1 × 2.3 | typical closed single-glider road trailer (Cobra/Spindelberger class) |
| `glider_trailer_2.yaml` | `trailer` | mover (towed) | 9.0 × 2.1 × 2.3 | second instance, same envelope |
| `maul_fuel_trailer.yaml` | `fixed_obstacle` | keep-out | 4.5 × 2.0 × 1.9 | Maul road-trailer envelope (estimated) |

Two trailers = two files (the `ground_object_placements` field forbids duplicate
ids; one-file-per-object is the #595 grain). Each part is a single `kind: ground`
footprint at `z_bottom 0`.

## 5. Wiring + the reference layout

- **`examples/herrenteich/fleet.yaml`** — add a `ground_objects:` list referencing
  the four catalog files (`load_ground_objects` already parses this, #601).
- **`examples/herrenteich/layout_full.yaml`** (new) — the 8 aircraft **re-nested**
  + a `ground_objects:` block placing the four. A verified-valid arrangement at
  0.20/0.15 already exists (this session's search); final coordinates are polished
  during implementation by a throwaway checker-driven search (the documented norm
  for `layout.yaml`; the search script is **not** committed). Header documents
  provenance. **The Caddy is placed near the door** (front, low-y) as the intended
  arrangement — a soft search bias, not the hard #603 gate.
- **`examples/herrenteich/layout.yaml` is NOT modified** — it remains the
  aircraft-only nested arrangement. `layout_full.yaml` is the new full-set
  *calibration reference*.
- **No `scenario_full.yaml`** this PR — a solve/route input belongs to the routing
  phase (#602); the static slice's gate is `check layout_full.yaml`.

## 6. `collisions.check` — extend bounds/notch to ground objects

Today #601 bounds/notch-checks **aircraft only** (`collisions.py:89`, comment
defers ground objects to "#604/#605"). So `check` passing does **not** currently
prove the ground objects are in-bounds or clear the notch — making #605's
"in bounds, clears the notch" acceptance vacuous.

**Change:** pass the ground-object bodies (movers **and** fixed obstacles) to
`_hangar_bounds_conflicts` alongside the aircraft, so `check` enforces in-bounds +
notch-clearance for *every* placed body.

```python
# in check():
all_bounded_bodies = {**aircraft_parts, **mover_parts, **obstacle_parts}
conflicts.extend(_hangar_bounds_conflicts(all_bounded_bodies, layout.hangar))
# _bay_intrusion stays aircraft-only (the bay is an aircraft-occupancy rule).
```

- **Byte-identity preserved.** With no ground objects, `mover_parts`/`obstacle_parts`
  are empty → `all_bounded_bodies == aircraft_parts` (same dict order) → conflict
  order and `total_penetration_m2` are identical to pre-#605. A regression test
  pins this.
- **Not a new collision primitive.** It extends the *existing* universal bounds
  check to bodies the model already has — the change the #601 code comment
  anticipated for "#604/#605". `_bay_intrusion_conflicts` and the pairwise/obstacle
  logic are untouched.
- Touches `collisions.py` → review by **geometry-invariant-guard** +
  **silent-failure-hunter** + the main code-reviewer.

## 7. Tests

- **`test_herrenteich_dataset.py`** (extend): `layout_full.yaml` passes `check`
  (valid), contains all 11 objects (8 aircraft + 4 ground objects), every ground
  object is in-bounds + notch-clear via an **independent model-free vertex scan**
  (mirrors the existing `test_layout_clears_office_notch`), and the Caddy is the
  nearest-door ground object (a *soft* assertion documenting the pre-#603 intent).
  ≥1 non-slow assertion (two-pass-coverage gotcha).
- **`test_collisions_ground_object.py`** (extend): a ground object placed partly
  outside the hangar yields a `hangar_bounds` conflict; one placed in the notch
  yields a `structural_notch` conflict; **byte-identity** — a no-ground-object
  layout's `check` result is unchanged by the extension.
- Run the full suite + the synthetic-fixture checks (`example.yaml`, the `data/`
  layouts) to confirm the clearance change broke nothing.

## 8. Docs / audit trail

- `examples/herrenteich/hangar.yaml` — inline comments justifying 0.20 / 0.15.
- `examples/herrenteich/README.md` — point at `layout_full.yaml` as the *calibration
  reference* (full real set), distinct from the aircraft-only `layout.yaml`; note
  routing/egress are deferred (#602/#603).
- `data/catalog/README.md` — the four new ground objects + their sourcing.
- **Dimension audit** — inline per-field citations in the four catalog files; a note
  that no aircraft dimension changed (hypothesis #1 not triggered).
- **Clearance audit** — the table in §2 + the monotonic-safety argument, captured in
  the hangar.yaml comments and the PR body.
- `CHANGELOG.md [Unreleased]` — new full-set reference dataset + the four ground
  objects + the Herrenteich clearance recalibration.

## 9. Out of scope / non-goals (restated)

- No solver / learned-backend work; no tow-routing; no Caddy-egress gate; no GO
  rendering. No new collision or tow *primitives* (the bounds extension reuses the
  existing check). `layout.yaml`, `data/`, and the synthetic placeholders are not
  re-authored.

## 10. Risks

- **Tight feasibility.** The full set fits only snugly (≤0.22/0.15). The shipped
  layout is valid at 0.20/0.15 but near the frontier; the regression test locks it,
  and the monotonic argument means future *tightening* can't break it (only a
  future *loosening* of clearance or *growth* of a dimension could — the test
  catches that).
- **Existing #601 tests** may place a ground object out of bounds and assert valid;
  the bounds extension would flip them. Audited and fixed during TDD (expected to be
  none, since #601 fixtures are small in-bounds footprints).
- **Coordinate polish.** Final `layout_full.yaml` coordinates come from a throwaway
  search; they are re-verified by the committed regression test, not the search.
