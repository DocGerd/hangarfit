# ADR-0018: Model the non-rectangular hangar footprint — a list of keep-out rects + a derived Shapely floor polygon for containment

- **Status:** Proposed
  <!-- This is a design-note SPIKE (#424). The deliverable is the decision
       record only; no `src/` code ships in the PR that introduces this ADR.
       Implementation is a separate follow-up issue. The status flips to
       Accepted when the *implementing* PR merges, not when this note lands. -->

- **Date:** 2026-06-04
- **Deciders:** Patrick Kuhn (DocGerd)

## Context & Problem Statement

The model assumes every hangar floor is the axis-aligned rectangle
`[0, width_m] × [0, length_m]`. The real Airfield Herrenteich hangar
(`herrenteich/hangar.yaml`, added in #426) is **not** a rectangle: it is
L-shaped. Its `15.08 m (x) × 31.76 m (y)` bounding rectangle has an office/annex
**notch** cut out of the back-right corner — `~2.36 m (x) × ~9.10 m (y)`, i.e.
`x ∈ [12.72, 15.08]`, `y ∈ [22.66, 31.76]` in the standard coordinate frame
(origin front-left, `+x` right along the door wall at `y = 0`, `+y` deeper in —
[ADR-0002](0002-determinant-minus-one-transform.md)). There is **no floor** in
the notch.

Today the model records only the bounding rectangle. A layout that parks a plane
in the notch is therefore a **false-valid**: `collisions.check` happily reports it
as fine because every vertex is inside `[0, 15.08] × [0, 31.76]`. The notch is
currently "modelled" only as a prose comment in `herrenteich/hangar.yaml` and is
avoided **by hand** when authoring that folder's `layout.yaml`. This ADR answers:
**how should the data model and the containment algorithm represent a hangar
floor that is not a single rectangle?**

A second, latent bug surfaced while grounding this design and must be fixed in
the same breath as adding the notch: the existing bounds check is **vertex-only**,
so even a perfectly-modelled notch would still be under-enforced (see the
edge-crossing case below).

## Decision Drivers

- **Correctness — stop the false-valid.** A part with geometry inside the notch
  must be rejected, just as a part outside the outer wall is rejected today.
- **Fix the vertex-only edge-crossing bug, not just paper over the notch.**
  `_first_out_of_bounds_vertex` tests only the *corners* of a part polygon. A long
  thin part (a wing, a strut) can have **both endpoints outside the notch and its
  edge crossing straight through it**, with no vertex inside — undetected. Adding
  notch geometry without upgrading from vertex-test to true polygon containment
  would leave this hole open.
- **Reuse Shapely, add no new dependency or determinism surface.** Shapely is
  already a runtime dependency and is already used in `collisions.py` for pairwise
  part overlap. A polygon-containment floor check is the *same* library doing the
  *same kind* of operation — not a new exposure.
- **Honor the byte-determinism contract ([ADR-0003](0003-rr-mc-solver-algorithm.md)).**
  Any new geometry the solver consumes must be RNG-free and float-stable: same
  scenario + seed → bit-identical plan, scoped to `max_restarts`.
- **Keep the maintenance-bay keep-out semantics ([ADR-0006](0006-bay-intrusion-maintenance-rule.md))
  intact.** The bay is an *operational, two-state* keep-out (a wall only when an
  occupant is in maintenance); the notch is a *structural, always-on* boundary.
  The model must not conflate the two state machines.
- **Don't over-build.** Herrenteich has exactly one notch. The model should
  generalize cleanly to "a few rectangular cut-outs," not to arbitrary curved
  floor plans we have no data for.

## Considered Options

**The data-model question and the containment-algorithm question are
orthogonal**; the options below pair a representation with a test so each is a
complete, shippable alternative.

1. **List-of-keep-out-rects in the model, but keep the per-vertex containment
   test** — generalize `maintenance_bay` into a list of tagged keep-out
   rectangles (one of `kind = structural_notch`), and extend the existing
   `_first_out_of_bounds_vertex` / `_first_vertex_in_bay` vertex loops to also
   reject any vertex inside a `structural_notch` rect.
2. **List-of-keep-out-rects in the model + a derived Shapely floor polygon for
   containment** — same data model as (1), but derive the floor *once* as a
   Shapely polygon (`bounding_rectangle − structural_notch_rects`) and test
   containment with polygon operations (`covers` + boundary/edge intersection)
   instead of per-vertex. **(Chosen.)**
3. **Inscribed sub-rectangle** — don't model the notch at all; instead shrink the
   usable floor to the largest axis-aligned rectangle that excludes the notch
   (here, clip `width_m` to `12.72` for the deep back band, or clip `length_m` to
   `22.66`). Keep everything rectangular.

## Decision Outcome

**Chosen option: (2) — a list of tagged keep-out rectangles in the data model,
plus a Shapely floor polygon derived once for polygon-based containment** —
because it is the only option that *both* represents the real footprint faithfully
*and* closes the vertex-only edge-crossing bug, while reusing the Shapely
machinery already in `collisions.py` (no new dependency, no new determinism
surface).

### Data model

Generalize the single `Hangar.maintenance_bay: MaintenanceBay` into a **list** of
keep-out rectangles, each tagged with a `kind`:

- `kind = maintenance_bay` — the existing operational, two-state keep-out
  (active only when `layout.maintenance_plane is not None`), with its
  back-wall-anchored geometry and [ADR-0006](0006-bay-intrusion-maintenance-rule.md)
  semantics **unchanged**.
- `kind = structural_notch` — a new, **always-on** keep-out. The Herrenteich notch
  becomes one rect `[x_min, y_min, x_max, y_max] = [12.72, 22.66, 15.08, 31.76]`
  with `kind = structural_notch`.

The two kinds stay **orthogonal** (see "Keep the bay and the notch as distinct
kinds" below): they share a rectangle representation and a list, but their
*activation* differs — the notch is part of the floor's definition; the bay is a
runtime state. Backward compatibility: a YAML with a scalar `maintenance_bay:`
maps to a one-element list `[{kind: maintenance_bay, ...}]`; the loader keeps
accepting today's `herrenteich/hangar.yaml` shape and grows an optional
`structural_notches:` (or `keep_outs:`) list.

### Containment algorithm

Derive the floor polygon **once** when the `Hangar` is built:

```
floor = box(0, 0, width_m, length_m).difference(
    unary_union([box(*r) for r in structural_notch_rects])
)
```

Replace the per-vertex `_first_out_of_bounds_vertex` test with a true containment
test of each world part against `floor`: a part is in-bounds iff
`floor.covers(part.polygon)` (`covers`, not `contains`, so a part edge flush with
the outer wall — the existing inclusive-boundary convention — still counts as
inside). This **fixes the edge-crossing bug**: a strut whose endpoints straddle
the notch but whose edge crosses it fails `covers`, because the edge is not
contained in the floor polygon. The conflict detail can still name the first
offending vertex *or* report "part edge crosses a `structural_notch`" — the
diagnostic richness of the current per-part emission is preserved.

The maintenance-bay keep-out is left exactly as ADR-0006 describes (a separate,
state-gated `_bay_intrusion_conflicts` pass). The notch is *not* gated on
`maintenance_plane` — it is always subtracted from the floor.

### The four touch-points this changes

The rectangular-floor assumption is baked into four places (each confirmed by
reading the code on `develop` @ `9527b84`):

1. **`src/hangarfit/models.py`** — `Hangar` (≈ L359–418) carries only `length_m`
   / `width_m` and a single `maintenance_bay: MaintenanceBay` (≈ L325–356, the
   only keep-out concept today). *Change:* generalize to a list of tagged keep-out
   rects; add the always-on `structural_notch` kind; add the bay/notch-fits-in-
   hangar validation (the existing `__post_init__` already validates that the bay
   fits — extend it per-rect). The derived floor polygon is cached here (or lazily
   on first use) so it is computed once, not per check.
2. **`src/hangarfit/collisions.py`** — `_hangar_bounds_conflicts` (≈ L71–105) +
   `_first_out_of_bounds_vertex` (≈ L108–114) do per-vertex strict-inside
   containment against the rectangle; `_bay_intrusion_conflicts` (≈ L117–178) +
   `_first_vertex_in_bay` (≈ L181–191) do the bay keep-out, also vertex-based.
   *Change:* `_hangar_bounds_conflicts` switches to `floor.covers(part.polygon)`
   (fixing the edge-crossing bug); the bay pass is untouched.
3. **`src/hangarfit/solver.py`** — `_initial_placement_for_plane` (≈ L757–810)
   samples `(x, y)` uniformly within rectangle margins (`margin = max(max_length,
   max_width) / 2`). *Change:* sampling stays rectangular (it is a *seed*, not a
   validity oracle — `collisions.check` is the oracle, and it will now reject notch
   placements). Optionally rejection-sample seeds out of the notch for efficiency,
   but this is a perf nicety, **not** required for correctness, and must stay
   RNG-stream-stable (a rejection loop changes the draw sequence — see Consequences).
4. **`src/hangarfit/towplanner.py`** — `_mover_motion_bounds_conflict`
   (≈ L951–1006) enforces rectangular side/back walls + the #411 door-aware
   front-gap exemption, also vertex-based. *Change:* a plane in transit must also
   stay out of the notch; route the in-transit bounds check through the same
   derived floor polygon (the door-gap `y < 0` exemption is preserved as-is — the
   notch is interior to the floor, unrelated to the front-wall door gate).

## Why not (1) — list-of-rects but keep the per-vertex test?

It models the notch but **does not fix the edge-crossing bug**, the very class of
under-enforcement that motivated upgrading containment in the first place. A part
edge can cross the notch with no vertex inside it and go undetected — exactly the
false-valid the spike set out to kill, just relocated from "no notch at all" to
"notch present but edges leak through it." Since the data-model change is identical
to (2) and the only delta is the test, paying for the data change while leaving the
known hole open is the worst of both: more code, same bug. Rejected.

## Why not (3) — inscribed sub-rectangle?

It is the **low-risk fallback**, and worth naming honestly: it keeps every
algorithm rectangular (no Shapely-floor, no edge-crossing question, zero
determinism re-analysis) and is trivially correct — nothing can be placed in the
notch because the floor simply does not extend there. But it is **conservative to a
fault**: clipping the back band to `x ≤ 12.72` *also* discards the
`[0, 12.72] × [22.66, 31.76]` strip of perfectly usable back-left floor (a
`12.72 × 9.10 ≈ 116 m²` band), or clipping `length_m ≤ 22.66` discards the whole
back third. For a hangar this tight — where the glider fleet barely nests (#425) —
throwing away real parking area to dodge a modelling gap is unacceptable. Held as
the fallback if (2) hits an unforeseen determinism or perf wall during
implementation, but not the recommendation.

## Why keep the bay and the notch as distinct kinds, not unify them?

They share a *shape* (an axis-aligned rectangle) and now a *container* (the list),
but they are different concepts and unifying their **activation** would be a
regression:

- The **maintenance bay** is *operational and two-state*: it is normal floor when
  open and a hard wall only when `layout.maintenance_plane is not None`
  ([ADR-0006](0006-bay-intrusion-maintenance-rule.md)). It is anchored to the back
  wall, has a distinct `bay_intrusion` conflict kind, and the occupant is *away*
  (absent from placements).
- The **structural notch** is *physical and always-on*: there is simply no floor
  there, ever, regardless of any plane's state. It is a property of the building.

Tagging both with a `kind` and storing them in one list is the right amount of
unification — one data structure, one fits-in-hangar validation loop — while the
notch feeds the *always-on floor polygon* and the bay keeps its *state-gated*
`_bay_intrusion_conflicts` pass and its own conflict kind. Collapsing the bay into
the floor polygon would either make it always-walled (wrong — it is open floor when
no one is in maintenance) or require a dynamic floor polygon rebuilt per check
(needless, and it would blur the `hangar_bounds` vs `bay_intrusion` conflict
streams ADR-0006 deliberately separated). Keep them orthogonal.

## Consequences

### Positive

- **The false-valid is closed.** A part with geometry in the notch is rejected by
  the same `hangar_bounds` mechanism that rejects parts outside the outer wall.
- **The vertex-only edge-crossing bug is fixed as a bonus.** Switching to
  `floor.covers(part.polygon)` rejects a part whose *edge* crosses any out-of-floor
  region (notch or beyond the wall) even when no vertex is inside — a strictly more
  correct containment than the current per-vertex loop, for *all* hangars, not just
  L-shaped ones.
- **No new dependency, no new determinism surface.** Shapely already ships and is
  already used for pairwise overlap; this is the same library doing the same class
  of operation.
- **The model now describes the real building.** `herrenteich/`'s prose-comment
  notch becomes enforced data; the all-8 layout no longer relies on hand-avoidance.

### Negative / risks to manage

- **Determinism analysis is required, even though the polygon is static.** The
  floor polygon is derived from static config (RNG-free), so the *containment test*
  is deterministic by construction. The watch-point is **float stability**:
  `shapely.difference` / `box` must produce a bit-identical polygon across runs on
  the fixed Python 3.12 toolchain (it is a pure function of the input floats — no
  iteration order, no RNG — so it does), and the solver must not let the new check
  perturb its RNG stream. If seed-rejection-sampling out of the notch is added in
  `solver.py` (the optional perf nicety), it **changes the draw sequence** and must
  be re-pinned by the `determinism-guard` (run twice on a fixed seed, diff). The
  conservative default is to *not* rejection-sample — keep seeds rectangular and let
  the oracle reject — so the RNG stream is untouched and ADR-0003 holds verbatim.
  Cite [ADR-0003](0003-rr-mc-solver-algorithm.md) in the implementing PR and run
  the guard.
- **The `scene/v1` viewer needs its own follow-up.** The 3D viewer
  ([ADR-0017](0017-3d-viewer-architecture.md)) renders the hangar floor as a
  rectangle. A non-rectangular floor will *not* render the notch until the
  `scene/v1` schema gains a floor-polygon (or keep-out-rect) field and the
  Three.js side draws it. Until then the viewer would show a plane sitting on a
  floor tile that the checker says is off-floor — a visual inconsistency. **File a
  follow-up to extend `scene/v1`** so the viewer stays honest; the checker can ship
  first.
- **Performance.** One extra `covers` call per part per `check_layout` (≈ same
  order as the pairwise-overlap loop already pays). The floor polygon is derived
  **once** at `Hangar` construction and reused, so the `difference` cost is paid
  once per scenario, not per check. Negligible at fleet scale (≤ ~9 planes).
- **Loader / schema churn.** The YAML schema grows (a `structural_notches:` /
  `keep_outs:` list); the loader must accept both the legacy scalar
  `maintenance_bay:` and the new list, with an actionable error on a notch that
  doesn't fit the bounding rectangle. Touches loader + (potentially) `MaintenanceBay`
  typing — invite `silent-failure-hunter` and `type-design-analyzer` on the
  implementing PR per CLAUDE.md's subagent map.

### Neutral

- **The bounding rectangle stays the outer boundary.** `width_m` / `length_m` are
  unchanged; the notch is *subtracted* from that rectangle, not a replacement
  representation. Hangars with no notch (the synthetic `data/` placeholders, all
  existing fixtures) derive a floor polygon identical to today's rectangle and
  behave exactly as before.
- **`data/` placeholders are untouched.** This is a `herrenteich/`-driven realism
  fix; the synthetic demo/test fixtures keep their rectangular floors.

## Implementation sketch (NOT implemented in this spike — follow-up issue)

This ADR is the **design note only**. Implementation is deferred to a separate
follow-up issue. Rough shape, in dependency order:

1. **`models.py`** — add a `KeepOut` (or reuse a tagged `MaintenanceBay`-like)
   record with `kind ∈ {maintenance_bay, structural_notch}` and `[x_min, y_min,
   x_max, y_max]`; turn `Hangar.maintenance_bay` into a list; validate each rect
   fits in `[0, width_m] × [0, length_m]`; derive + cache the `floor` Shapely
   polygon (`box − union(structural_notches)`).
2. **`loader.py`** — accept legacy scalar `maintenance_bay:` (→ one-element list)
   and a new `structural_notches:` / `keep_outs:` list; actionable error on
   out-of-bounds rects.
3. **`collisions.py`** — replace `_first_out_of_bounds_vertex` with
   `floor.covers(part.polygon)` in `_hangar_bounds_conflicts`; keep
   `_bay_intrusion_conflicts` as-is.
4. **`towplanner.py`** — route `_mover_motion_bounds_conflict` through the floor
   polygon (preserve the #411 door-gap `y < 0` exemption).
5. **`solver.py`** — leave seeding rectangular (correctness comes from the oracle);
   *optionally* and separately, add notch-rejection seeding behind a determinism
   re-pin.
6. **Data** — turn `herrenteich/hangar.yaml`'s prose notch into a real
   `structural_notch` rect `[12.72, 22.66, 15.08, 31.76]`.
7. **`scene/v1` + viewer** — separate follow-up: add a floor-polygon field and draw
   the L-shape (ADR-0017 extension).
8. **Tests** — golden fixtures: a plane *in* the notch (rejected), a part *edge
   crossing* the notch with no vertex inside (rejected — the bug-fix pin), a plane
   in the now-still-usable back-left strip (accepted); a `determinism-guard` run if
   seeding changes.

## Compliance

No automated check ships with this spike — it is a design note. When the
implementing PR lands, compliance is:

- A `collisions` regression class pinning: notch placement rejected, **edge-crossing
  with no vertex inside rejected** (the bug-fix canary), back-left strip accepted,
  no-notch hangars behave identically to today.
- `geometry-invariant-guard` on the `collisions.py` change (touches the guarded
  containment path; [ADR-0002](0002-determinant-minus-one-transform.md)).
- `determinism-guard` on any `solver.py` / `towplanner.py` change
  ([ADR-0003](0003-rr-mc-solver-algorithm.md)).
- This ADR's status flips **Proposed → Accepted** when the implementing PR merges.

## More Information

- Related ADRs:
  [ADR-0002](0002-determinant-minus-one-transform.md) (coordinate convention the
  notch box is expressed in),
  [ADR-0006](0006-bay-intrusion-maintenance-rule.md) (the maintenance-bay keep-out
  this generalizes alongside, kept orthogonal),
  [ADR-0001](0001-aircraft-parts-model.md) (the world-part geometry the containment
  test consumes),
  [ADR-0003](0003-rr-mc-solver-algorithm.md) (the byte-determinism contract the
  float-stability argument is measured against),
  [ADR-0017](0017-3d-viewer-architecture.md) (the `scene/v1` viewer that needs a
  follow-up to render a non-rect floor).
- Real data: [`herrenteich/hangar.yaml`](../../herrenteich/hangar.yaml) — records
  the notch in comments today; [`herrenteich/README.md`](../../herrenteich/README.md).
- Related issues / PRs: spike **#424** (this design note); the real-data PR that
  surfaced the gap is **#426**; the sibling glider-fleet false-reject is **#425**.
  Implementation is a **separate follow-up issue** (to be filed).
