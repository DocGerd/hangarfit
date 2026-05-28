# ADR-0013: Wheel positions are canonical per-aircraft data; turn_radius_m stays empirical, cross-checked at load

- **Status:** Proposed
  <!-- Proposed at PR-open; flip to Accepted at PR-merge. -->
- **Date:** 2026-05-28
- **Deciders:** [@DocGerd](https://github.com/DocGerd)

## Context & Problem Statement

Two surface representations of the same physical property — *where each
aircraft's wheels are* — disagreed, with nothing linking them:

- `data/fleet.yaml` carried `turn_radius_m` per aircraft (load-bearing for the
  Reeds–Shepp tow-path planner since Phase 3a — [ADR-0007](0007-tow-path-planner-v1-scope.md) /
  [ADR-0010](0010-reeds-shepp-motion-model.md)), but **no wheel positions**.
- `src/hangarfit/visualize.py` invented wheel-glyph positions on the fly from
  heuristic fractions of fuselage half-length (`_NOSE_GEAR_FRAC` and friends).
  The module itself flagged the fractions as "intentionally approximate".

A contributor could change `turn_radius_m` without touching the rendered
wheelbase, or move the rendered wheels without touching the radius, and nothing
noticed — the property was modelled twice and the two could drift apart
silently. Surfaced during the v0.7.2 pre-release visual smoke (2026-05-28,
[#322](https://github.com/DocGerd/hangarfit/issues/322)).

A coupled latent inconsistency: the `fleet.yaml` header asserted *"Origin =
main-gear / cart centroid"*, which the data did not honor — `visualize.py` drew
mains offset from origin and the fleet entries followed that convention.

## Decision Drivers

- **One source of truth for wheel positions.** The same property must not have
  two independent surface representations that can disagree.
- **Honesty over false precision.** Every fleet dimension is a guess
  (`measured: false`). The model should not claim a derivation it cannot
  actually justify from the data we have.
- **Determinism contract is sacred.** [ADR-0003](0003-rr-mc-solver-algorithm.md):
  same scenario + seed ⇒ bit-identical output. The change must not perturb
  `turn_radius_m` values, or every Reeds–Shepp solution and canary baseline
  shifts.
- **Unblock the dependent work.** [#321](https://github.com/DocGerd/hangarfit/issues/321)
  (cart glyph at wheel positions) needs canonical wheel coordinates to consume.
- **Consistency with existing idioms.** The `struts:` and `kind: fuselage`
  blocks already establish "readable YAML expanded/validated by the loader,
  one ADR per load-bearing decision" ([ADR-0001](0001-aircraft-parts-model.md),
  [ADR-0012](0012-fuselage-front-aft-split.md)).

## Considered Options

### D1 — what wheels become

1. **Canonical per-aircraft data + load-time cross-check of `turn_radius_m`**
   *(chosen)*. Wheels are first-class `fleet.yaml` data; the renderer reads
   them; the loader sanity-checks `turn_radius_m` against the wheel-derived
   wheelbase.
2. **Render-only data, no cross-check.** Wheels become render data but
   `turn_radius_m` is left unlinked.
3. **Collision participation.** Wheels become Parts in the collision model so a
   wing cannot overhang another plane's main gear.

### D2 — the wheels schema

1. **β-schema: `main_offset_x_m` + `track_m` + `third_wheel_offset_x_m`**
   *(chosen)*. Compact, intent-documenting, symmetry-enforcing. Monowheel sets
   only `main_offset_x_m`.
2. **α-schema: explicit per-wheel `(x, y)` pairs.** Verbose; the loader must
   enforce left/right symmetry by convention rather than by structure.

### D3 — `turn_radius_m`

1. **Keep it as an independent empirical value, cross-checked only** *(chosen)*.
2. **Derive it from wheelbase + steering geometry.**

### D4 — the origin-documentation inconsistency

1. **Reconcile the docs: origin is a per-aircraft anchor; main-gear centroid is
   derived** *(chosen)*.
2. **Re-anchor every aircraft so `origin = main-gear centroid` (schema γ).**

## Decision Outcome

**Chosen: D1.1, D2.1, D3.1, D4.1.**

**D1.1**, because the framing is "two representations disagree" — render-only
(D1.2) leaves the drift in place, and collision participation (D1.3) is a
larger parts-model change with its own canary re-bake, out of scope here. The
cross-check is the cheapest thing that makes drift *loud*.

**D2.1**, because the β-schema documents intent (this is a wheelbase, this is a
track) and makes a left/right mismatch structurally impossible, where α would
re-introduce the mirror-the-numbers-by-hand burden ADR-0001 rejected for struts.

**D3.1**, because deriving `turn_radius_m` (D3.2) needs a per-aircraft
`max_steer_angle_deg` we don't have, and the real relationship is non-trivial
(a taildragger pivots differently than a tricycle). The empirical number we
already carry is more honest than a model-derived one — and *crucially* keeping
it unchanged is what preserves the determinism contract.

**D4.1**, because re-anchoring (D4.2) would churn every fixture with a
hard-coded placement coordinate for no functional gain; reconciling the header
comment is a zero-data-diff fix.

The contract:

- **Schema.** `wheels:` is **required** on every aircraft.
  ```yaml
  wheels:
    main_offset_x_m: <float>          # mains' plane-local +x station
    track_m: <float>                  # tricycle/tailwheel only; full track, > 0
    third_wheel_offset_x_m: <float>   # tricycle/tailwheel only; sign by gear
  ```
  Monowheel sets only `main_offset_x_m`. Sign rule: a nosewheel's third wheel
  is **forward** of the mains (`third > main`); a tailwheel's is **aft**
  (`third < main`). `Wheels.positions` yields the plane-local `(x, y)` of every
  wheel (mains at `(main_offset_x_m, ±track_m/2)`, then the third at
  `(third_wheel_offset_x_m, 0)`); `Wheels.wheelbase_m` is
  `abs(third − main)`, or `None` for monowheel.
- **Cross-check.** For any non-monowheel aircraft carrying a `turn_radius_m`
  (i.e. `always_own_gear` *and* `cart_eligible`, both of which keep a real
  radius and wheelbase) the loader requires
  `0.5 × wheelbase ≤ turn_radius_m ≤ 5 × wheelbase`; a violation is a hard
  `LoaderError`. The skip keys off `turn_radius_m is None` (every `always_cart`
  entry today) and monowheel (no wheelbase) — not off `movement_mode`, so a
  stray radius on an `always_cart` plane would still be checked. The band is
  deliberately **loose** — a sanity guard
  against a fat-fingered radius or coordinate, not a derivation, and wide enough
  not to false-positive on the `measured: false` estimates.
- **Rendering.** `_draw_gear_glyph` loops over `aircraft.wheels.positions`
  through `local_to_world`; the heuristic fraction constants are deleted.

## Consequences

### Positive

- One source of truth: the rendered wheelbase and the planner's `turn_radius_m`
  can no longer drift silently — the loader fails loudly if they diverge wildly.
- `visualize.py` drops `_NOSE_GEAR_FRAC` / `_MAIN_GEAR_FWD_FRAC` /
  `_MAIN_GEAR_TAILDRAGGER_FWD_FRAC` / `_MAIN_GEAR_LATERAL_FRAC` and the
  fuselage-segment reconstruction; `_draw_gear_glyph` becomes a short loop.
- [#321](https://github.com/DocGerd/hangarfit/issues/321) consumes
  `wheels.positions` directly — no new heuristic surface to maintain.
- The main-gear centroid, a useful reference for future motion work (e.g.
  [#263](https://github.com/DocGerd/hangarfit/issues/263) nose-out parking), is
  available as `(wheels.main_offset_x_m, 0)` in plane-local coords.

### Negative

- `Aircraft.wheels` is a **required** field — a breaking change to the model
  and the loader; every inline `Aircraft(...)` in tests had to pass `wheels=`
  (absorbed via the `tests/conftest.py::make_test_aircraft` helper), and a
  `fleet.yaml` entry with no `wheels:` block is now a hard load error.
- The backfilled wheel numbers are still estimates (`measured: false` stays);
  the cross-check guards plausibility, not correctness.

### Neutral

- **Determinism contract unchanged** — `turn_radius_m` values are not modified,
  so Reeds–Shepp solutions and `test_solver_canaries.py` baselines are
  byte-identical (verified: zero canary diff).
- Wheel collision participation (D1.3) remains a future decision; if revisited,
  this ADR is the starting point.
- This supersedes the neutral-consequence note in
  [ADR-0012](0012-fuselage-front-aft-split.md) that `_draw_gear_glyph`
  reconstructs the fuselage span from both segments — it no longer does.

## Compliance

- **`tests/test_loader_wheels.py`** — `TestWheelsLoadingHappyPath` /
  `TestWheelsLoadingErrorPaths` pin the schema, the gear-keyed key sets, and the
  nose-forward / tail-aft sign rules; `TestCrossCheck` pins the 0.5×–5× band and
  the `always_cart` / monowheel skips; `test_no_wheels_block_now_raises` pins
  the required-field flip.
- **`tests/test_visualize_wheels.py`** — pins that the renderer draws one wheel
  per `wheels.positions` entry at the `local_to_world`-mapped coordinates, and
  that `on_carts=True` takes the cart path (regression guard for #321).
- **`Wheels.__post_init__`** rejects `track_m ≤ 0` and the monowheel/tricycle
  field XOR; the loader wraps the `ValueError` as a `LoaderError`.
- **`determinism-guard`** confirms `test_solver_canaries.py` and the towplanner
  suites are byte-identical (towplanner.py is untouched by this change).

## More Information

- Related issue: [#322](https://github.com/DocGerd/hangarfit/issues/322) (this
  decision); [#321](https://github.com/DocGerd/hangarfit/issues/321) (cart glyph
  — consumes wheels); [#79](https://github.com/DocGerd/hangarfit/issues/79)
  (real fleet measurements).
- Related spec: [`docs/superpowers/specs/2026-05-28-wheels-canonical-design.md`](../superpowers/specs/2026-05-28-wheels-canonical-design.md).
- [ADR-0001: Aircraft geometry as a list of parts](0001-aircraft-parts-model.md)
  — same shape: explicit data over heuristic, loader-validated.
- [ADR-0007: Tow-path planner v1 scope](0007-tow-path-planner-v1-scope.md) —
  introduced `turn_radius_m` as load-bearing.
- [ADR-0010: Reeds–Shepp motion model](0010-reeds-shepp-motion-model.md) — the
  arcs that consume `turn_radius_m`.
- [ADR-0012: Fuselage front/aft split](0012-fuselage-front-aft-split.md) — the
  prior ADR whose gear-glyph note this supersedes.
