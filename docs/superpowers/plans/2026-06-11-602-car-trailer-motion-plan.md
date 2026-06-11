# #602 Car + Trailer Motion Models — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route the two ground-object *mover* classes (steerable car, towed trailer) with their own tow paths by parameterizing the existing ADR-0010 planner — no new planner, no new `SegmentKind`.

**Architecture:** The tow planner is already mover-agnostic in its math (`_primitives` / `_plan_cart` / `plan_reeds_shepp` dispatch on a scalar turn radius). Two gaps close it: (1) `GroundObject` gains `effective_turn_radius_m()`; (2) the mover-routing oracle (`path_first_conflict`, `plan_path`, `_motion_clear`, `_mover_motion_bounds_conflict`) becomes **body-type-aware** — it injects an aircraft mover into a per-sample `Layout` as a `placement` (against `fleet`) but a ground-object mover as a `ground_object_placement` (against `ground_objects`). Then `plan_fill` routes each placed-routed mover (replacing the `#601` `Move(path=None)` placeholder). Every new branch triggers only for a `GroundObject`, so aircraft routing stays byte-identical (ADR-0003).

**Tech stack:** Python 3.12, frozen dataclasses (`slots=True`), shapely (geometry), pytest. Determinism is closed-form + RNG-free.

**Branch:** `feature/602-car-trailer-motion` (off `develop`, already created; spec committed at `8861f1a`).

---

## File / change map

| File | Change |
|---|---|
| `src/hangarfit/models.py` | Add `GroundObject.effective_turn_radius_m()`; add `__post_init__` guard "steerable mover requires a positive `turn_radius_m`". |
| `src/hangarfit/towplanner.py` | Import `GroundObject`; widen 4 mover-param type hints to `Aircraft \| GroundObject`; add the `isinstance` branch in `path_first_conflict`; in `plan_fill` add fixed-obstacle routing context + route GO movers. |
| `docs/adr/0010-reeds-shepp-motion-model.md` | Dated amendment: car = own-gear RS, trailer = free-swivel cart, no-new-planner rationale. |
| `tests/test_models.py` | `effective_turn_radius_m()` + steerable-radius-guard tests (co-locate with existing `GroundObject` validator tests; grep `GroundObject` in tests/ to confirm file). |
| `tests/test_towplanner_ground_object.py` | Update `test_mover_in_routable_enumeration` (path now non-`None`); add a `plan_path`-routes-a-GO-mover test + reverse-cart trailer test. |
| `tests/test_towplanner.py` | Zero-mover `plan_fill` byte-identical canary (if not already present). |
| `tests/test_towplanner_mover_routing.py` (new) | `@slow` integration: Herrenteich-shaped car + trailer route; fixed-seed determinism canary. |

---

## Task 1: `GroundObject.effective_turn_radius_m()` + steerable-radius guard

**Files:**
- Modify: `src/hangarfit/models.py` (`GroundObject`, ~lines 450–500)
- Test: `tests/test_models.py` (co-locate with `GroundObject` validator tests)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_models.py  (alongside the GroundObject validator tests)
import pytest
from hangarfit.models import GroundObject, Part


def _gp() -> Part:
    return Part(kind="ground", length_m=4.0, width_m=2.0, offset_x_m=0.0,
               offset_y_m=0.0, angle_deg=0.0, z_bottom_m=0.0, z_top_m=1.8)


def test_steerable_mover_effective_radius_is_its_turn_radius() -> None:
    car = GroundObject(id="caddy", name="c", parts=(_gp(),),
                       object_class="placed_routed_mover", motion_mode="steerable",
                       turn_radius_m=5.5)
    assert car.effective_turn_radius_m() == 5.5


def test_towed_mover_without_radius_is_free_swivel_cart() -> None:
    trailer = GroundObject(id="tr1", name="t", parts=(_gp(),),
                           object_class="placed_routed_mover", motion_mode="towed")
    assert trailer.effective_turn_radius_m() == 0.0


def test_steerable_mover_requires_positive_turn_radius() -> None:
    with pytest.raises(ValueError, match="steerable.*turn_radius_m"):
        GroundObject(id="bad", name="b", parts=(_gp(),),
                     object_class="placed_routed_mover", motion_mode="steerable")
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_models.py -k "effective_radius or free_swivel or steerable_mover_requires" -v`
Expected: FAIL — `effective_turn_radius_m` does not exist; the steerable-no-radius case currently constructs without error.

- [ ] **Step 3: Implement the method + guard**

In `GroundObject.__post_init__`, inside the `else:  # placed_routed_mover` block (after the existing `turn_radius_m must be positive` check), add:

```python
            if self.motion_mode == "steerable" and self.turn_radius_m is None:
                raise ValueError(
                    f"GroundObject {self.id!r}: a steerable mover requires a "
                    f"positive turn_radius_m (a self-driven car has a turning "
                    f"circle; it never pivots in place)"
                )
```

Add the method to the `GroundObject` class body (after `__post_init__`):

```python
    def effective_turn_radius_m(self) -> float:
        """Turn radius the tow planner consumes (ADR-0010, 2026-06-12 amendment).

        Data-driven, mirroring :meth:`Aircraft.effective_turn_radius_m`: a
        steerable car returns its positive ``turn_radius_m`` (own-gear
        Reeds-Shepp, six-primitive fan); a towed trailer with no radius returns
        ``0.0`` — a free-swivel cart (four-primitive reverse-capable fan, the
        ground-crew hand-positioning model). Only ever called on movers; never
        raises (``__post_init__`` guarantees a steerable mover has a radius)."""
        if self.turn_radius_m is not None:
            return self.turn_radius_m
        return 0.0
```

- [ ] **Step 4: Run to verify they pass + the full model/loader suite stays green**

Run: `pytest tests/test_models.py tests/test_loader.py tests/test_collisions_ground_object.py tests/test_towplanner_ground_object.py -q`
Expected: PASS. If any existing fixture builds a `steerable` mover with no `turn_radius_m`, the new guard will flag it — fix that fixture to pass `turn_radius_m` (it is a misconfiguration the guard correctly rejects).

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/models.py tests/test_models.py
git commit -m "feat(602): GroundObject.effective_turn_radius_m() + steerable-radius guard

Mirrors Aircraft.effective_turn_radius_m(): steerable car -> positive
turn_radius_m (own-gear RS); towed trailer with no radius -> 0.0 free-swivel
cart. A steerable mover must carry a positive radius (no pivot-in-place car).

Refs #602"
```

---

## Task 2: Body-type-aware mover-routing oracle

**Files:**
- Modify: `src/hangarfit/towplanner.py` (imports ~line 45; `_mover_motion_bounds_conflict` ~1108; `path_first_conflict` ~1215; `_motion_clear` ~1807; `plan_path` ~2021)
- Test: `tests/test_towplanner_ground_object.py`

- [ ] **Step 1: Write the failing test (route a GO mover directly via `plan_path`)**

```python
# tests/test_towplanner_ground_object.py
from hangarfit.geometry import aircraft_parts_world  # noqa: F401 (pattern parity)
from hangarfit.models import Pose
from hangarfit.towplanner import plan_path, path_first_conflict


def test_plan_path_routes_a_ground_object_car_mover() -> None:
    """A steerable GO car routes from the door cone to a parked slot against a
    placed aircraft, and the returned arc is collision-free (the oracle places
    the mover as a ground_object_placement, not an aircraft placement)."""
    from hangarfit.towplanner import entry_poses
    hangar = _hangar()
    ac = make_test_aircraft(id="p1")
    car = GroundObject(id="caddy", name="c", parts=(_ground_part(width_m=2.0, length_m=4.5),),
                       object_class="placed_routed_mover", motion_mode="steerable",
                       turn_radius_m=5.5)
    slot = Placement(plane_id="caddy", x_m=10.0, y_m=20.0, heading_deg=90.0, on_carts=False)
    placed = Layout(
        fleet={ac.id: ac}, hangar=hangar,
        placements=(Placement(plane_id="p1", x_m=6.0, y_m=30.0, heading_deg=0.0, on_carts=False),),
        ground_objects={car.id: car},
        ground_object_placements=(slot,),
    )
    cone = entry_poses(slot, hangar)
    arc = plan_path(car, cone[0], Pose.from_placement(slot), hangar=hangar,
                    placed=placed, mover_on_carts=False, entries=cone, heuristic="grid")
    assert arc is not None
    assert path_first_conflict(arc, car, mover_on_carts=False, placed=placed) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_towplanner_ground_object.py::test_plan_path_routes_a_ground_object_car_mover -v`
Expected: FAIL — `path_first_conflict` builds the per-sample `Layout` with the mover in `placements`, so `Layout.__post_init__` raises `ValueError` ("placement references unknown plane_id 'caddy'").

- [ ] **Step 3: Generalize the oracle (5 edits)**

**(a)** Imports (~line 45) — add `GroundObject`:
```python
from hangarfit.models import Aircraft, ApronShallowDrop, Conflict, GroundObject, Hangar, Layout, Placement
```

**(b)** `_mover_motion_bounds_conflict` (~1108) — widen the type hint (body unchanged; it only uses `aircraft_parts_world`, which already accepts the union):
```python
def _mover_motion_bounds_conflict(
    mover: Aircraft | GroundObject, placement: Placement, hangar: Hangar
) -> Conflict | None:
```

**(c)** `path_first_conflict` (~1215) — widen the type hint and branch the per-sample `Layout`. Replace the current `sample_layout = Layout(...)` block:
```python
def path_first_conflict(
    arc: DubinsArc,
    mover: Aircraft | GroundObject,
    *,
    mover_on_carts: bool,
    placed: Layout,
    step_m: float = 0.05,
    step_deg: float = 1.0,
) -> Conflict | None:
```
and inside the sample loop, replace the single `sample_layout = Layout(...)` with:
```python
        if isinstance(mover, Aircraft):
            sample_layout = Layout(
                fleet=placed.fleet,
                hangar=placed.hangar,
                placements=(*placed.placements, moving),
                maintenance_plane=placed.maintenance_plane,
                ground_objects=placed.ground_objects,
                ground_object_placements=placed.ground_object_placements,
            )
        else:  # GroundObject mover -> belongs in ground_object_placements
            sample_layout = Layout(
                fleet=placed.fleet,
                hangar=placed.hangar,
                placements=placed.placements,
                maintenance_plane=placed.maintenance_plane,
                ground_objects=placed.ground_objects,
                ground_object_placements=(*placed.ground_object_placements, moving),
            )
```
> Note: the Aircraft branch now also threads `ground_objects` / `ground_object_placements` so a routed aircraft collides with placed ground objects (fuel trailer + parked movers). With no ground objects both are empty ⇒ byte-identical to today.

**(d)** `_motion_clear` (~1807) — widen the type hint (body unchanged; uses `cached_parts_world` + `_mover_motion_bounds_conflict`, both union-safe):
```python
def _motion_clear(mover: Aircraft | GroundObject, pose: Pose, obstacles: _Obstacles, hangar: Hangar) -> bool:
```

**(e)** `plan_path` (~2021) — widen the type hint. The body calls `mover.effective_turn_radius_m()` (now on both types), `_build_obstacles(placed, mover_id=mover.id)` (already filters both placement lists), `_mover_motion_bounds_conflict`, `_motion_clear`, `path_first_conflict` (all now union-typed):
```python
def plan_path(
    mover: Aircraft | GroundObject,
    entry: Pose,
    goal: Pose,
    *,
    ...
) -> DubinsArc:
```

- [ ] **Step 4: Run to verify it passes + aircraft routing untouched**

Run: `pytest tests/test_towplanner_ground_object.py tests/test_towplanner.py tests/test_towplanner_reeds_shepp.py -q`
Expected: PASS. Then `mypy src/hangarfit/towplanner.py` — if mypy flags an `Aircraft`-only attribute access on the union, narrow it with `isinstance(mover, Aircraft)` at that site (none expected: only `.id`, `.parts`, `.effective_turn_radius_m()` are used).

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/towplanner.py tests/test_towplanner_ground_object.py
git commit -m "feat(602): body-type-aware mover-routing oracle (route GO movers)

path_first_conflict/plan_path/_motion_clear/_mover_motion_bounds_conflict now
accept Aircraft | GroundObject; a GO mover is injected into the per-sample
Layout as a ground_object_placement (vs an aircraft placement). _build_obstacles
already includes ground objects. Aircraft routing byte-identical (empty GO dicts).

Refs #602"
```

---

## Task 3: `plan_fill` routes the GO movers

**Files:**
- Modify: `src/hangarfit/towplanner.py` (`plan_fill`: `placed_layout` ~1379; the GO-mover emission loop ~1463)
- Test: `tests/test_towplanner_ground_object.py`

- [ ] **Step 1: Update the (now-wrong) deferred-path test + add a routing assertion**

Replace `test_mover_in_routable_enumeration` so it asserts a **routed** (non-`None`) path, and that movers come id-sorted after aircraft:

```python
def test_plan_fill_routes_ground_object_movers() -> None:
    hangar = _hangar()
    ac = make_test_aircraft(id="p1")
    car = GroundObject(id="caddy", name="c", parts=(_ground_part(width_m=2.0, length_m=4.5),),
                       object_class="placed_routed_mover", motion_mode="steerable",
                       turn_radius_m=5.5)
    layout = Layout(
        fleet={ac.id: ac}, hangar=hangar,
        placements=(Placement(plane_id="p1", x_m=6.0, y_m=30.0, heading_deg=0.0, on_carts=False),),
        ground_objects={car.id: car},
        ground_object_placements=(
            Placement(plane_id="caddy", x_m=10.0, y_m=20.0, heading_deg=90.0, on_carts=False),
        ),
    )
    plan = plan_fill(layout)
    caddy_moves = [m for m in plan.moves if m.plane_id == "caddy"]
    assert len(caddy_moves) == 1
    assert caddy_moves[0].path is not None        # #602: routed, not deferred None
    assert plan.moves[-1].plane_id == "caddy"     # movers appended after aircraft
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_towplanner_ground_object.py::test_plan_fill_routes_ground_object_movers -v`
Expected: FAIL — `caddy_moves[0].path` is `None` (the current deferred emission).

- [ ] **Step 3: Implement — fixed-obstacle routing context + mover routing**

Near the top of `plan_fill` (after `hangar = target.hangar`, ~line 1363), precompute the fixed-obstacle placements once:
```python
    # Fixed obstacles (e.g. the fuel trailer) are static keep-outs for BOTH
    # aircraft routes and mover routes. Empty when no ground objects ⇒ inert.
    fixed_obstacle_placements = tuple(
        gp for gp in target.ground_object_placements
        if target.ground_objects[gp.plane_id].object_class == "fixed_obstacle"
    )
```

In the aircraft scan's `placed_layout` (~1379), thread the ground-object context so aircraft route around fixed obstacles:
```python
        placed_layout = Layout(
            fleet=fleet,
            hangar=hangar,
            placements=tuple(placed),
            maintenance_plane=target.maintenance_plane,
            ground_objects=target.ground_objects,
            ground_object_placements=fixed_obstacle_placements,
        )
```

Replace the GO-mover emission loop (~1463) — route each placed-routed mover against all placed aircraft + fixed obstacles + already-routed movers (id-sorted accumulation), best-effort (`path=None` if unroutable):
```python
    # Ground-object movers (#602): route each placed-routed mover with its own
    # path. id-sorted + appended after the aircraft loop ⇒ aircraft moves stay
    # byte-identical (no ground objects ⇒ this loop is empty). Each mover routes
    # against all parked aircraft + fixed obstacles + movers already routed this
    # pass; the one being routed is excluded by _build_obstacles(mover_id=...).
    routed_mover_placements: list[Placement] = []
    for gp in sorted(target.ground_object_placements, key=lambda p: p.plane_id):
        obj = target.ground_objects[gp.plane_id]
        if obj.object_class != "placed_routed_mover":
            continue
        mover_placed = Layout(
            fleet=fleet,
            hangar=hangar,
            placements=tuple(placed),
            maintenance_plane=target.maintenance_plane,
            ground_objects=target.ground_objects,
            ground_object_placements=(*fixed_obstacle_placements, *routed_mover_placements),
        )
        cone = entry_poses(gp, hangar)
        stats: dict[str, object] = {}
        remaining = total_budget - total_used
        try:
            arc: DubinsArc | None = plan_path(
                obj,
                cone[0],
                Pose.from_placement(gp),
                hangar=hangar,
                placed=mover_placed,
                mover_on_carts=False,
                entries=cone,
                heuristic=heuristic,
                max_expansions=min(budget, remaining) if remaining > 0 else 1,
                stats=stats,
            )
        except NoFeasiblePlanError:
            # Best-effort (ADR-0007 #197): an unroutable mover keeps a None path
            # (surfaced by the caller, same contract as an un-tow-routable plane).
            arc = None
        exp = stats.get("expansions", 0)
        total_used += exp if isinstance(exp, int) else 0
        moves.append(Move(gp.plane_id, Pose.from_placement(gp), path=arc))
        routed_mover_placements.append(gp)
```

- [ ] **Step 4: Run to verify it passes + no-GO byte-identity**

Run: `pytest tests/test_towplanner_ground_object.py tests/test_towplanner.py -q`
Expected: PASS. The no-ground-object `plan_fill` path is unchanged (empty `fixed_obstacle_placements`, empty mover loop).

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/towplanner.py tests/test_towplanner_ground_object.py
git commit -m "feat(602): plan_fill routes ground-object movers

Replaces the #601 Move(path=None) placeholder: each placed-routed mover routes
from the door cone to its slot against parked aircraft + fixed obstacles +
already-routed movers (id-sorted, best-effort path=None if unroutable). Fixed
obstacles now also constrain aircraft routes. Inert (byte-identical) with no
ground objects.

Refs #602"
```

---

## Task 4: ADR-0010 dated amendment

**Files:**
- Modify: `docs/adr/0010-reeds-shepp-motion-model.md`

- [ ] **Step 1: Append a dated amendment section** (match the file's existing amendment-block format — a `### Amendment YYYY-MM-DD (#602): ...` section with `**Status:** Accepted`). Content:
  - Car = own-gear Reeds–Shepp parameterization (positive `r_min`, six-primitive fan).
  - Towed trailer = **free-swivel cart** (r = 0, four-primitive reverse-capable fan) — chosen model + rationale (ground-crew hand-positioning of a balanced trailer; the tug turning-circle / jackknife is deferred).
  - Decision: these are **new object behaviours, not a new planner** — `_primitives` / `_plan_cart` / `plan_reeds_shepp` reused unchanged; mover characterized by `(effective_turn_radius_m, reverse-capable)`.
  - Rejected: a separate kinematic-bicycle / trailer-jackknife planner (closed-form RS + cart already give forward+reverse arcs/straights + pivot — sufficient for v1; reuse preserves the byte-identical determinism contract, ADR-0003).

- [ ] **Step 2: Verify the link/anchor** — `grep -n "0010" CLAUDE.md docs/` to confirm no reference needs updating (the amendment lives inside the existing ADR; no new ADR number).

- [ ] **Step 3: Commit**

```bash
git add docs/adr/0010-reeds-shepp-motion-model.md
git commit -m "docs(602): ADR-0010 amendment — car RS + free-swivel-cart trailer

Refs #602"
```

---

## Task 5: Integration + determinism canaries

**Files:**
- Test: `tests/test_towplanner.py` (zero-mover byte-identical), `tests/test_towplanner_mover_routing.py` (new, `@slow` integration + determinism)

- [ ] **Step 1: Zero-mover byte-identical canary** (if `tests/test_towplanner.py` lacks an explicit one)

```python
def test_plan_fill_no_ground_objects_byte_identical() -> None:
    """plan_fill is byte-identical to a no-ground-object run when the Layout has
    no ground objects — the #602 mover code must be inert (ADR-0003)."""
    # build a small multi-plane Layout WITHOUT ground objects (reuse an existing
    # fixture/helper in this file), then:
    p1 = plan_fill(layout)
    p2 = plan_fill(layout)
    assert [(m.plane_id, m.path) for m in p1.moves] == [(m.plane_id, m.path) for m in p2.moves]
```

- [ ] **Step 2: `@slow` integration — car + trailer both route, reverse-cart leg exercised**

```python
# tests/test_towplanner_mover_routing.py
import pytest
from hangarfit.models import GroundObject, Layout, Placement, Pose
from hangarfit.towplanner import plan_fill, path_first_conflict
# reuse _hangar / _ground_part / make_test_aircraft patterns from
# tests/test_towplanner_ground_object.py (copy the helpers per the repo's
# deliberate per-module fixture-duplication convention).


@pytest.mark.slow
def test_car_and_trailer_both_route_collision_free() -> None:
    ...  # car (steerable r=5.5) + trailer (towed r=0) + 1 aircraft; assert each
         # mover's move has a non-None path and path_first_conflict(...)==None.


@pytest.mark.slow
def test_towed_trailer_uses_reverse_cart_leg() -> None:
    ...  # a trailer slot reachable only by a reverse-straight cart leg; assert
         # the routed arc contains a reverse segment (gear == -1).
```

- [ ] **Step 3: Fixed-seed determinism canary (non-slow, ≥1 per new path for two-pass coverage)**

```python
def test_mover_routing_is_byte_identical_across_runs() -> None:
    """Same Layout -> identical mover paths across two plan_fill calls (closed-form,
    RNG-free; ADR-0003)."""
    # small car+trailer Layout (non-slow), then:
    a = plan_fill(layout)
    b = plan_fill(layout)
    assert [(m.plane_id, m.path) for m in a.moves] == [(m.plane_id, m.path) for m in b.moves]
```

- [ ] **Step 4: Run the full suite + determinism-guard**

Run: `pytest -q` then `pytest -m slow -q` then `ruff check src/ tests/ && ruff format --check src/ tests/ && mypy src/hangarfit/`
Then run the **determinism-guard** subagent (mandated: this PR touches `towplanner.py`) — a fixed-seed double-solve must be byte-identical.
Expected: all green; determinism-guard PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_towplanner.py tests/test_towplanner_mover_routing.py
git commit -m "test(602): mover-routing integration + zero-mover & determinism canaries

Refs #602"
```

---

## Self-review (against the spec)

**1. Spec coverage:**
- Object → `(turn_radius, reverse)` mapping → **Task 1** (`effective_turn_radius_m`) + **Task 2** (oracle consumes it via existing `_primitives` dispatch). ✓
- No new planner / `SegmentKind`; params from catalog → Tasks 1–3 (no primitive/segment changes; `turn_radius_m` from catalog). ✓
- Movers routed via `path_first_conflict` + `_mover_motion_bounds_conflict` → **Task 2/3**. ✓
- Unroutable mover → best-effort `path=None` → **Task 3** (`except NoFeasiblePlanError`). ✓
- ADR-0010 amendment → **Task 4**. ✓
- Determinism: closed-form, RNG-free, fixed id-order, `determinism-guard`, zero-mover byte-identical → **Task 3/5**. ✓
- Tests: car-RS + reverse-cart, integration, canaries → **Tasks 1, 2, 5**. ✓

**2. Placeholder scan:** Task 5 integration bodies use `...` for the fixture wiring — these reuse the documented `_hangar`/`_ground_part`/`make_test_aircraft` helpers (concrete construction shown in Tasks 1–3); fill them by copying those helpers per the repo's per-module fixture convention.

**3. Type consistency:** `effective_turn_radius_m()` (Task 1) is the name `plan_path` calls (Task 2). `Aircraft | GroundObject` is applied uniformly across all 4 oracle functions. `fixed_obstacle_placements` defined once (Task 3) and reused in both the aircraft `placed_layout` and the mover loop. `Move(..., path=arc|None)` matches the existing `Move.path: DubinsArc | None` contract.

**Sequencing:** #603's plan is written after this lands (its egress oracle reuses the **final** generalized `path_first_conflict`).
