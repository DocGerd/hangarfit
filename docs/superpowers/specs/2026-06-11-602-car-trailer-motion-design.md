# #602 — Car + trailer motion models in the towplanner

**Date:** 2026-06-11
**Issue:** #602 (Stage A / Epic #600, milestone #34) — *Car + trailer motion
models (self-driving steerable car; towed rigid-body trailer) — ADR-0010
amendment.*
**Branch:** `feature/602-car-trailer-motion` (off `develop`, **created after PR
#611 merges** — consumes the #605 ground-object catalog entries).
**Status:** design — **approved** 2026-06-11; awaiting spec review.
**Builds on:** #595 (catalog), #601 (ground-object model + loader, the
`Move(path=None)` deferred-mover contract), #605/#611 (the 4 real GO catalog
entries + herrenteich `fleet.yaml` wiring).

---

## 1. Scope (this PR)

Give the two **mover** ground-object classes a *motion model* so the existing
tow planner can route each one with its own path:

- the **self-driving steerable car** (VW Caddy) → own-gear Reeds–Shepp with a
  positive minimum turn radius (the six-primitive fan);
- the **towed rigid-body trailer** (glider trailer) → free-swivel cart (r = 0),
  the four-primitive reverse-capable cart fan.

The load-bearing decision: **reuse the existing ADR-0010 closed-form motion
machinery — no new planner, no new `SegmentKind`, no new search loop.** A mover
is fully characterized to the planner by `(effective_turn_radius_m,
reverse-capability)`; both classes are parameterizations of the primitives the
aircraft tow planner already drives.

**Explicitly out of scope** (own siblings):

- the Caddy nearest-door / clear-egress HARD gate → **#603** (this PR only makes
  the Caddy *move* once placed; #603 *consumes* the routability oracle);
- the glider-trailer soft right-region preference → **#604**;
- rendering movers + their paths in PNG / scene-v2 → **#606**;
- the **fixed fuel trailer is not a mover** — it stays a static keep-out (#601),
  only constraining the door throat / routing corridor.

## 2. Decisions locked (this session)

| Decision | Choice | Rationale |
|---|---|---|
| **Trailer kinematics** | **Free-swivel cart (r = 0)** — `_plan_cart` reverse-enabled, 4-primitive fan `(Lf, Sf, Rf, Sr)`. | Models ground-crew hand-positioning of a balanced glider trailer (push/pull + swivel the tongue) in a club hangar. Reuses the existing cart path verbatim; needs **no new catalog data** (trailer `turn_radius_m` stays unset → 0). Jackknife / tug-turning-circle is explicitly deferred (issue non-goal). |
| **Car kinematics** | **Positive-radius Reeds–Shepp** — own-gear 6-primitive fan, `turn_radius_m = 5.5` (already in `vw_caddy.yaml`). | A self-driven car has a real turning circle and cannot pivot in place. |
| **No new planner** | Parameterize `_primitives` / `_plan_cart` / `plan_reeds_shepp` unchanged. | Preserves the byte-identical determinism contract (ADR-0003); closed-form RS + cart already give forward+reverse arcs/straights + pivot — sufficient for v1. |

## 3. Substrate (verified by the understand-phase map)

The planner is already mover-agnostic:

- `plan_path()` (`towplanner.py:2021`) calls `mover.effective_turn_radius_m()`
  (`:2097`) and routes the result through `_primitives(r)` (`:1545`),
  `_plan_cart(allow_reverse=True)` (`:449`, r = 0), and `plan_reeds_shepp()`
  (`:866`, r > 0). **No new branch is needed** — only an
  `effective_turn_radius_m()` on `GroundObject` and an actual route call for
  movers.
- `plan_fill()` (`towplanner.py:1297`) today appends each GO mover as
  `Move(path=None)` after the aircraft loop (the #601 deferred-route contract,
  ≈ lines 1463–1474, id-sorted so the aircraft plan is byte-identical).
- `Aircraft.effective_turn_radius_m()` (`models.py:410`) is the precedent:
  returns `0.0` for cart / `tow_pivotable`, else the positive radius — never
  raises, never returns `None`/negative.
- Catalog (already on #611): `vw_caddy.yaml` carries `turn_radius_m: 5.5`
  ("carried for #602 routing"); the glider trailers carry **no** `turn_radius_m`;
  `maul_fuel_trailer` is a motion-less `fixed_obstacle`. Mover YAML whitelist
  `_ALLOWED_MOVER_KEYS = {id, name, parts, measured, motion_mode, turn_radius_m}`.

## 4. Architecture / components

### 4.1 `models.py` — `GroundObject.effective_turn_radius_m()`

Mirror `Aircraft.effective_turn_radius_m()`. Data-driven so Herrenteich
*configures* the value and the model stays general:

```python
def effective_turn_radius_m(self) -> float:
    """Routing turn radius for a placed-routed mover (ADR-0010/0026).

    Steerable (car) -> its positive ``turn_radius_m`` (own-gear Reeds-Shepp,
    six-primitive fan). Towed (trailer) with no radius -> 0.0 free-swivel cart
    (four-primitive reverse-capable fan). Never called on fixed obstacles
    (they are not routed)."""
    if self.turn_radius_m is not None:
        return self.turn_radius_m
    return 0.0
```

`__post_init__` gains **one** guard: a **steerable** mover must carry a
*positive* `turn_radius_m` (a self-driven car must never silently degrade to a
pivot-in-place cart). A **towed** mover may omit it (→ cart). `fixed_obstacle`
still forbids motion fields (unchanged). *Implementation note:* this validator is
stricter than #601 — sweep test fixtures that build a steerable mover without a
radius and the loader/`_build_car` path.

### 4.2 `towplanner.py` — route GO movers in `plan_fill()`

Replace the `Move(path=None)` placeholder with a real `plan_path()` call per GO
mover:

- **Order:** aircraft loop unchanged; then GO movers in **id-sorted** order,
  appended after the aircraft moves (preserves aircraft-plan byte-identity).
- **Obstacles** for a mover's search = already-placed aircraft + the fixed fuel
  trailer keep-out + previously-routed movers (the back-first / id-sorted
  accumulation already used for aircraft).
- `mover_on_carts = False` for every GO mover (ground objects have no cart
  accounting; never look up / mutate `on_carts` for them).
- **Best-effort preserved:** a mover that cannot route stays `Move(path=None)`
  (blocking object named on stderr), the same exit-3 tow-routability contract as
  aircraft. `#606` rendering continues to skip `None` paths.
- **Inert when no GO movers** → the loop body never executes → byte-identical
  aircraft `MovesPlan`.

### 4.3 ADR-0010 dated amendment

A timestamped section within `docs/adr/0010-reeds-shepp-motion-model.md` (the
project's amendment-block convention, not a new ADR), documenting:

(a) car = own-gear Reeds–Shepp parameterization (positive `r_min`, six-primitive
fan); (b) towed trailer = free-swivel cart (r = 0, four-primitive
reverse-capable fan) — the chosen model + rationale (ground-crew
hand-positioning; jackknife / hitch turning-circle deferred); (c) the explicit
decision that these are **new object behaviours, not a new planner**, reusing
`_primitives` / `_plan_cart` / `plan_reeds_shepp` unchanged; (d) why a separate
kinematic-bicycle / trailer-jackknife planner was rejected (closed-form RS +
cart already give forward+reverse arcs/straights + pivot — sufficient for v1, and
reuse preserves the byte-identical determinism contract).

## 5. Data flow

```
Scenario/Layout ground_objects
  -> plan_fill(): aircraft loop (UNCHANGED, byte-identical)
                  then GO movers, id-sorted:
        mover.effective_turn_radius_m()  -- 5.5 (car) | 0.0 (trailer)
          -> _primitives(r)              -- 6-prim RS | 4-prim cart
          -> plan_reeds_shepp(r>0) | _plan_cart(allow_reverse=True, r==0)
          -> DubinsArc                   -- closed-form, RNG-free
          -> Move(plane_id, target_slot, path=arc)
  -> MovesPlan   -- consumed by #606 rendering
```

## 6. Determinism (ADR-0003 — non-negotiable)

- All new motion code stays **closed-form and RNG-free** (reuses
  `plan_reeds_shepp` / `_plan_cart`); no new randomness, no dict/set iteration
  order leaking into output.
- GO movers enumerated in a **fixed id-sorted order**, appended after aircraft;
  the existing strict tie-break discipline is untouched.
- **`determinism-guard` is run** (touches `towplanner.py`): a fixed-seed
  double-solve produces byte-identical output.
- A regression test asserts the **zero-mover plan is byte-identical** to today
  (the inert-when-empty ⇒ byte-identical pattern, like apron / notch).

## 7. Testing

- **Unit (non-slow):**
  - `GroundObject.effective_turn_radius_m()` → `5.5` for the car, `0.0` for the
    trailers; `__post_init__` rejects a steerable mover with no/`<=0` radius.
  - Car → positive-radius RS reachable: a roundtrip-grid mirroring
    `test_reeds_shepp_roundtrip_grid` (`tests/test_towplanner_reeds_shepp.py`),
    `arc.pose_at(arc.length_m) == goal` (`approx(abs=1e-3)` on position,
    `_heading_close(0.5)` on heading).
  - Trailer → reverse-capable cart: a `test_reverse_cart_roundtrip_grid` spanning
    trailer geometries + reverse arcs (the reverse-straight cart leg).
  - **Zero-mover byte-identical canary** (non-slow): `plan_fill` output unchanged
    when no GO movers present.
- **Integration (`@slow`):** a small Herrenteich-shaped scenario with the Caddy +
  one glider trailer routes each mover with its own non-`None` path through
  `path_first_conflict`; a reverse-only trailer leg is exercised.
- **Determinism canary** (non-slow, ≥ 1 per new path per the two-pass-coverage
  rule): a fixed-seed mover scenario routes byte-identically across two runs.

## 8. Non-goals

- No new planner / no new `SegmentKind`. The strafe `"T"` primitive from the
  un-merged `feature/599` branch is explicitly **not** a dependency.
- No trailer jackknife / multi-link articulation (rigid-body reverse-capable
  approximation only; richer kinematics are a `later` follow-on).
- The fixed fuel trailer is **not routed** here (static keep-out, #601).
- No placement / constraint logic (Caddy egress = #603, trailer region = #604).
- No ML — deterministic planner only (shippable Stage A core).

## 9. Acceptance criteria (from the issue)

- [ ] Each mover type resolves to `(effective_turn_radius_m, reverse_capable)`
      consumed by the existing `plan_path` / `path_first_conflict` machinery; car
      → positive-radius six-primitive fan, trailer → reverse-capable cart.
- [ ] No new `SegmentKind` / search loop; motion params come from the #595/#605
      catalog, not hardcoded in `towplanner`.
- [ ] Movers routed through the same `path_first_conflict` mover-sampling oracle;
      bounds via `_mover_motion_bounds_conflict` (front-gap-exempt) exactly as
      aircraft.
- [ ] Unroutable mover surfaces via the best-effort / exit-3 path (named on
      stderr); no silent skip.
- [ ] ADR-0010 amendment landed (car, trailer, no-new-planner rationale).
- [ ] Determinism: closed-form, RNG-free, fixed iteration order; `determinism-
      guard` passes; zero-mover plan byte-identical.
- [ ] Tests: car-RS + reverse-cart roundtrip grids, integration route, canaries.

## 10. Sequencing & dependencies

- **Build after PR #611 merges** (consumes its GO catalog entries). Branch off
  `develop`.
- **#602 lands before #603** — #603's clear-egress half routes the Caddy via this
  motion model.
- Amends ADR-0010; stays under ADR-0003 (determinism), ADR-0007 (cart = r 0).
