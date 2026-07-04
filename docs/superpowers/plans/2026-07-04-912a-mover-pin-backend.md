# #912 PR A — mover-pin backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a `placed_routed_mover` (car/trailer) carry an optional **pin** — a hand-authored resting pose the solver seats it at as a **path-less keep-out** — while an unpinned mover stays solver-placed and byte-identical (ADR-0003).

**Architecture:** A new `Scenario.mover_pins` map (mirroring `region_preferences`) carries the pin. The loader builds it from a mover `ground_objects:` entry that now *may* carry a pose. The solver excludes a pinned mover from the search (extends `pinned_planes`), seats it at the pin, and materializes it into the built `Layout.ground_object_placements` with `hand_placed=True`. `hand_placed` is generalized from aircraft to movers: the `Layout` guard is relaxed to allow it on a mover, the `plan_fill` mover loop emits a path-less `Move` for it, and aircraft routing treats it as an obstacle. The hard-door egress gate is unchanged (it already keys off the resting pose).

**Tech Stack:** Python 3.12; `pytest` (+ the `@serial` determinism canaries); `ruff`/`mypy`.

## Global Constraints

- **ADR-0003 (determinism):** an empty `mover_pins` ⇒ byte-identical solve/plan/scene to today. The whole feature is gated on a pin being present. The `@serial` double-solve canary on an **unpinned** scenario must stay bit-identical.
- **ADR-0002:** no transform math changes here (backend only).
- **Pin shape:** exactly `{x_m, y_m, heading_deg}` (3 fields, **no `on_carts`** — movers never ride carts). Materialized as `Placement(plane_id=<mover_id>, x_m, y_m, heading_deg, on_carts=False, hand_placed=True)`.
- **`hand_placed` on a ground object is allowed ONLY for `placed_routed_mover`** — a `fixed_obstacle` placement with `hand_placed=True` must still raise.
- **Mutual exclusion:** a mover may carry a pin **or** a `region_preference`, never both.
- **Scope:** backend only. NO editor/gizmo/`currentPoses`/`export.ts`/`viewer.py` (that is PR B). Deliver via a draft PR (`Part of #912`; PR B carries `Closes #912`).

**Recon reference (verbatim current code + exact anchors):** `/tmp/claude-1000/-home-pkuhn-hangarfit/46869df9-a28d-4a9d-9a1d-8960d3de0014/scratchpad/912-mover-pipeline.md`. Each task's brief points at the relevant section; implementers read the exact current code there + at the named `file:line` before editing.

---

## File Structure

- **Modify `src/hangarfit/models.py`** — relax the `Layout.__post_init__` `hand_placed` guard (allow on movers); add `Scenario.mover_pins` field + `Scenario.__post_init__` invariants.
- **Modify `src/hangarfit/loader.py`** — the Scenario `ground_objects:` mover branch: a pose builds a pin instead of raising.
- **Modify `src/hangarfit/towplanner.py`** — the `plan_fill` mover loop path-less branch; include hand-placed movers in the aircraft obstacle set.
- **Modify `src/hangarfit/solver.py`** — `pinned_planes` + the pinned-mover initial placement + materialize the pin into `ground_object_placements` with `hand_placed=True`.
- **Create `docs/adr/0031-mover-pin.md`** (or amend ADR-0025) — record the mover-pin semantics.
- **Modify `CHANGELOG.md`** — one `[Unreleased]` entry.
- **Tests:** `tests/test_models.py`, `tests/test_loader*.py`, `tests/test_towplanner*.py`, `tests/test_solver*.py` (+ a `@serial` determinism canary alongside the existing ones).

---

### Task 1: Model — `hand_placed`-on-mover guard relaxation + `Scenario.mover_pins`

**Files:**
- Modify: `src/hangarfit/models.py` (`Layout.__post_init__` guard ~1006-1015; `Scenario` dataclass + `__post_init__` ~1177-1300)
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `Scenario.mover_pins: Mapping[str, Placement]` (default `{}`); a `Layout.ground_object_placements` entry with `hand_placed=True` is legal iff its `object_class == "placed_routed_mover"`. Task 2 (loader) builds `mover_pins`; Tasks 3/4 consume it.

- [ ] **Step 1: Read the exact current code.** Read `models.py:1000-1020` (the `hand_placed` guard inside `Layout.__post_init__` — recon §B/§handPlaced evidence quotes it) and the `Scenario` dataclass + `__post_init__` (fields include `fleet_in`, `constraints`, `ground_objects`, `region_preferences`, `fixed_obstacle_placements`; the invariant loop asserting `constraints.keys() ⊆ set(fleet_in)`; the `mover_ids` property). Note the exact field-ordering and the frozen/`Mapping` types used by the sibling maps.

- [ ] **Step 2: Write the failing model tests.**

```python
# tests/test_models.py
def test_layout_allows_hand_placed_on_a_mover_placement():
    # #912: hand_placed generalizes to placed_routed_mover (path-less keep-out).
    layout = _layout_with_ground_object(  # helper: build a minimal valid Layout with one mover
        object_class="placed_routed_mover",
        placement_hand_placed=True,
    )
    # constructing it must NOT raise
    assert any(gp.hand_placed for gp in layout.ground_object_placements)

def test_layout_rejects_hand_placed_on_a_fixed_obstacle_placement():
    with pytest.raises(ValueError, match="placed_routed_mover"):
        _layout_with_ground_object(object_class="fixed_obstacle", placement_hand_placed=True)

def test_scenario_mover_pins_must_be_a_subset_of_mover_ids():
    with pytest.raises(ValueError, match="mover_pins"):
        _scenario(mover_pins={"not_a_mover": _placement("not_a_mover")})

def test_scenario_mover_pin_and_region_preference_are_mutually_exclusive():
    with pytest.raises(ValueError, match="both a pin and a region_preference"):
        _scenario(mover_ids=("caddy",), mover_pins={"caddy": _placement("caddy")},
                  region_preferences={"caddy": "right"})

def test_scenario_mover_pin_plane_id_must_equal_its_key():
    with pytest.raises(ValueError, match="plane_id"):
        _scenario(mover_ids=("caddy",), mover_pins={"caddy": _placement("other_id")})

def test_scenario_without_mover_pins_is_unchanged():
    sc = _scenario(mover_ids=("caddy",))
    assert sc.mover_pins == {}
```
Use the file's existing Scenario/Layout construction helpers (grep `tests/test_models.py` for how it builds a `Scenario`/`Layout` with a ground object; reuse those rather than inventing `_scenario`/`_layout_with_ground_object` if equivalents exist — the names above are placeholders for whatever the file already uses).

- [ ] **Step 3: Run → verify FAIL.** `pytest tests/test_models.py -k "mover_pin or hand_placed_on_a" -v` → FAIL (field/invariant absent; guard still rejects).

- [ ] **Step 4: Relax the `Layout` guard.** In `Layout.__post_init__`, change the unconditional `if gp.hand_placed: raise ValueError(...)` to fire only for a non-mover. The guard runs inside a loop over `ground_object_placements` and has `self.ground_objects` in scope:

```python
if gp.hand_placed and self.ground_objects[gp.plane_id].object_class != "placed_routed_mover":
    raise ValueError(
        f"ground_object_placement {gp.plane_id!r} sets hand_placed=True, which is only "
        f"valid on a placed_routed_mover (a fixed_obstacle is positioned by its pose, #912)"
    )
```
(Adapt to the exact current guard text/structure read in Step 1 — preserve the fixed_obstacle rejection.)

- [ ] **Step 5: Add `Scenario.mover_pins` + invariants.** Add the field to the `Scenario` dataclass beside `region_preferences` (same `Mapping[str, ...]` immutable style):
```python
mover_pins: Mapping[str, Placement] = field(default_factory=dict)
```
In `Scenario.__post_init__`, after the existing mover/region checks, add:
```python
mover_id_set = set(self.mover_ids)
for mid, pin in self.mover_pins.items():
    if mid not in mover_id_set:
        raise ValueError(f"mover_pins key {mid!r} is not a placed_routed_mover in this scenario")
    if pin.plane_id != mid:
        raise ValueError(f"mover_pins[{mid!r}] has plane_id {pin.plane_id!r} (must equal the key)")
    if mid in self.region_preferences:
        raise ValueError(f"mover {mid!r} has both a pin and a region_preference (mutually exclusive, #912)")
```
(If `Scenario` is frozen and stores maps as a specific immutable type, match it — e.g. wrap in the same helper the other maps use.)

- [ ] **Step 6: Run → verify PASS.** `pytest tests/test_models.py -k "mover_pin or hand_placed_on_a" -v` → PASS.

- [ ] **Step 7: Full model suite + lint/type.** `pytest tests/test_models.py -q && ruff check src/hangarfit/models.py tests/test_models.py && mypy src/hangarfit/` → clean.

- [ ] **Step 8: Commit.**
```bash
git add src/hangarfit/models.py tests/test_models.py
git commit -m "feat(models): Scenario.mover_pins + allow hand_placed on a mover placement (#912)"
```

---

### Task 2: Loader — a mover `ground_objects:` pose builds a pin

**Files:**
- Modify: `src/hangarfit/loader.py` (the Scenario `ground_objects:` branch, ~857-945; the mover `has_pose` reject at ~914-919)
- Test: `tests/test_loader.py` (or the file holding the Scenario ground-object loader tests — grep for `region_preference` / `must not carry a pose`)

**Interfaces:**
- Consumes: `Scenario.mover_pins` (Task 1).
- Produces: a Scenario whose mover `ground_objects:` mapping-entry with `{x_m, y_m, heading_deg}` yields `scenario.mover_pins[id] = Placement(id, x_m, y_m, heading_deg, on_carts=False, hand_placed=True)`.

- [ ] **Step 1: Read the exact current code.** Read `loader.py:857-945` (recon §C.3 quotes the mover reject + the `fixed_obstacle` pose-required pattern at :892-913 to mirror). Note `_ALLOWED_SCENARIO_GO_KEYS` (already admits `x_m/y_m/heading_deg`), the bare-string vs mapping entry parse, and how `has_pose`/`region_preference` are detected.

- [ ] **Step 2: Write the failing loader tests.** Use the file's existing scenario-YAML fixture helper (grep for how it writes a temp scenario + calls `load_scenario`).
```python
def test_scenario_mover_with_pose_builds_a_pin(tmp_path):
    sc = _load_scenario(tmp_path, """
        hangar: {ref}
        fleet_in: [c1]
        ground_objects:
          - {{object: caddy, x_m: 3.0, y_m: 4.0, heading_deg: 90.0}}
    """)  # caddy is a placed_routed_mover in the referenced fleet
    pin = sc.mover_pins["caddy"]
    assert (pin.x_m, pin.y_m, pin.heading_deg) == (3.0, 4.0, 90.0)
    assert pin.on_carts is False and pin.hand_placed is True

def test_scenario_mover_without_pose_stays_unpinned(tmp_path):
    sc = _load_scenario(tmp_path, "...ground_objects: [caddy]...")
    assert sc.mover_pins == {}
    assert "caddy" in sc.mover_ids

def test_scenario_mover_pin_requires_all_three_pose_fields(tmp_path):
    with pytest.raises(LoaderError, match="x_m.*y_m.*heading_deg|requires"):
        _load_scenario(tmp_path, "...ground_objects: [{object: caddy, x_m: 3.0}]...")

def test_scenario_mover_pin_and_region_preference_rejected(tmp_path):
    with pytest.raises(LoaderError, match="region_preference|pin"):
        _load_scenario(tmp_path, "...ground_objects: [{object: caddy, x_m: 3.0, y_m: 4.0, heading_deg: 0, region_preference: right}]...")

def test_scenario_pose_on_a_fixed_obstacle_still_works(tmp_path):
    # regression: fixed_obstacle pose path unchanged
    sc = _load_scenario(tmp_path, "...ground_objects: [{object: pillar, x_m: 1, y_m: 1, heading_deg: 0}]...")
    assert any(p.plane_id == "pillar" for p in sc.fixed_obstacle_placements)
```

- [ ] **Step 3: Run → verify FAIL.** `pytest tests/test_loader.py -k "mover_with_pose or mover_pin or mover_without_pose" -v` → FAIL (today the pose raises `LoaderError`).

- [ ] **Step 4: Relax the mover branch.** Replace the `placed_routed_mover` `if has_pose: raise LoaderError(...)` (loader.py:914-919) with: if a pose is present, require all three fields, forbid `region_preference` alongside it, and build the pin; else keep today's unpinned path. Concrete shape (adapt to the exact current variable names for the parsed `fields`/`gid`/accumulators):
```python
else:  # placed_routed_mover
    pose_keys = ("x_m", "y_m", "heading_deg")
    has_pose = any(k in fields for k in pose_keys)
    if has_pose:
        missing = [k for k in pose_keys if k not in fields]
        if missing:
            raise LoaderError(f"{path}: ground_objects[{i}] ({gid}): a pinned mover requires {missing}")
        if "region_preference" in fields:
            raise LoaderError(f"{path}: ground_objects[{i}] ({gid}): a mover pin and region_preference are mutually exclusive (#912)")
        mover_pins[gid] = Placement(
            plane_id=gid,
            x_m=_to_float(fields["x_m"], "x_m"),
            y_m=_to_float(fields["y_m"], "y_m"),
            heading_deg=_to_float(fields["heading_deg"], "heading_deg"),
            on_carts=False,
            hand_placed=True,
        )
    ground_object_ids.append(gid)   # a pinned mover is STILL a mover_id (keep today's append)
    if "region_preference" in fields:
        region_preferences[gid] = ...   # unchanged existing handling
```
Thread the new `mover_pins` dict into the `Scenario(...)` construction at the end of the scenario loader (add `mover_pins=mover_pins`). Initialise `mover_pins: dict[str, Placement] = {}` where the other accumulators are initialised.

- [ ] **Step 5: Run → verify PASS.** `pytest tests/test_loader.py -k "mover_with_pose or mover_pin or mover_without_pose or fixed_obstacle_still" -v` → PASS.

- [ ] **Step 6: Full loader suite + lint/type.** `pytest tests/test_loader.py -q && ruff check src/hangarfit/loader.py tests/test_loader.py && mypy src/hangarfit/` → clean.

- [ ] **Step 7: Commit.**
```bash
git add src/hangarfit/loader.py tests/test_loader.py
git commit -m "feat(loader): a placed_routed_mover ground_objects pose builds a mover pin (#912)"
```

---

### Task 3: Planner — path-less mover + aircraft keep-out

**Files:**
- Modify: `src/hangarfit/towplanner.py` (`plan_fill` mover loop ~2298-2347; the aircraft obstacle tuple ~1969-1973)
- Test: `tests/test_towplanner.py` (or the towplanner test file — grep for `plan_fill` / `ground_object`)

**Interfaces:**
- Consumes: a `Layout` whose `ground_object_placements` may contain a `placed_routed_mover` with `hand_placed=True` (legal since Task 1).
- Produces: `plan_fill` emits a **path-less** `Move(id, pose, path=None)` for a `hand_placed` mover (never routed), and aircraft route around it. Task 4 relies on this when it materializes a pinned mover.

- [ ] **Step 1: Read the exact current code.** Read `towplanner.py:2298-2347` (the mover loop — recon §handPlaced evidence quotes it: `for gp in sorted(target.ground_object_placements ...)`, the `object_class != "placed_routed_mover": continue`, the `plan_path(...)`/`except NoFeasiblePlanError`, `moves.append(Move(...))`, `routed_mover_placements.append(gp)`) and the aircraft obstacle tuple built at ~1969-1973 (`fixed_obstacle_placements = tuple(p for p in ... )` predicate). Confirm `Move` accepts `path=None` (recon: yes — `scene._timeline` renders `path is None` bodies path-less).

- [ ] **Step 2: Write the failing planner tests.** Build a `Layout` fixture directly (a hand-authored layout with one `placed_routed_mover` placement `hand_placed=True`, plus one aircraft whose only feasible tow lane crosses the mover's footprint).
```python
def test_plan_fill_emits_pathless_move_for_a_hand_placed_mover():
    layout = _layout_with_hand_placed_mover(...)  # mover placement hand_placed=True
    plan = plan_fill(layout, ...)
    mover_moves = [m for m in plan.moves if m.plane_id == "caddy"]
    assert len(mover_moves) == 1 and mover_moves[0].path is None  # path-less, never routed

def test_aircraft_routes_around_a_hand_placed_mover():
    # aircraft whose straight lane is blocked by the pinned mover must detour or fail —
    # assert its routed path does not intersect the mover footprint (or that removing the
    # mover changes the aircraft's route), proving the mover is in the aircraft obstacle set.
    ...
```
(Reuse existing towplanner fixtures/geometry helpers; the discriminating assertion is *mover Move has `path is None`* and *the mover is an obstacle for aircraft*.)

- [ ] **Step 3: Run → verify FAIL.** `pytest tests/test_towplanner.py -k "pathless_move_for_a_hand_placed or routes_around_a_hand_placed" -v` → FAIL (today the mover loop routes it; aircraft ignore it).

- [ ] **Step 4a: Path-less mover-loop branch.** In the `plan_fill` mover loop, immediately after resolving `obj = target.ground_objects[gp.plane_id]` and the `fixed_obstacle` `continue`, add:
```python
if gp.hand_placed:  # #912: a pinned (hand-placed) mover is a path-less keep-out — never routed
    moves.append(Move(gp.plane_id, Pose.from_placement(gp), path=None))
    routed_mover_placements.append(gp)  # still an obstacle for later-routed movers
    continue
```

- [ ] **Step 4b: Aircraft keep-out.** Extend the obstacle predicate at ~1969-1973 so a `hand_placed` `placed_routed_mover` is included in the aircraft obstacle tuple. If the current predicate selects `fixed_obstacle` placements, widen it, e.g.:
```python
fixed_and_pinned = tuple(
    gp for gp in target.ground_object_placements
    if target.ground_objects[gp.plane_id].object_class == "fixed_obstacle" or gp.hand_placed
)
```
(Adapt to the exact current comprehension read in Step 1 — the goal: aircraft routing sees a hand-placed mover as a static obstacle.)

- [ ] **Step 5: Run → verify PASS + no regressions.** `pytest tests/test_towplanner.py -q` → the new tests PASS and the existing towplanner suite stays green.

- [ ] **Step 6: Lint/type.** `ruff check src/hangarfit/towplanner.py tests/test_towplanner.py && mypy src/hangarfit/` → clean.

- [ ] **Step 7: Commit.**
```bash
git add src/hangarfit/towplanner.py tests/test_towplanner.py
git commit -m "feat(towplanner): path-less routing + aircraft keep-out for a hand-placed mover (#912)"
```

---

### Task 4: Solver — pin the mover, seat it path-less, keep determinism

**Files:**
- Modify: `src/hangarfit/solver.py` (`pinned_planes` ~502-506; `_initial_placement_for_plane` ~1174-1196; `_build_layout` ~226-256)
- Test: `tests/test_solver.py` + a `@serial` determinism canary alongside the existing ones (grep `tests/` for `@serial` + `double`/`byte`)

**Interfaces:**
- Consumes: `Scenario.mover_pins` (Task 1), the path-less planner (Task 3).
- Produces: a solved `Layout` where a pinned mover sits at its pin in `ground_object_placements` with `hand_placed=True`, excluded from the search; an unpinned scenario is byte-identical to today.

- [ ] **Step 1: Read the exact current code.** Read `solver.py:502-506` (`pinned_planes = frozenset(pid for pid in scenario.fleet_in if ...)` — recon §D quotes it), `_initial_placement_for_plane` ~1174-1196 (the `constraint.pin` short-circuit), and `_build_layout` ~226-256 (splits the unified `placements` into `aircraft` vs `movers` by `pid in scenario.fleet`, then `go_placements = tuple(movers) + scenario.fixed_obstacle_placements`). Note where a mover's `Placement` is built during initial sampling.

- [ ] **Step 2: Write the failing solver tests + determinism canary.**
```python
def test_pinned_mover_seats_at_its_pin_and_is_pathless():
    scenario = _scenario_with_mover_pin(mover="caddy", x=3.0, y=4.0, heading=90.0)
    result = solve(scenario, ..., render_paths=True)
    gp = next(p for p in result.layout.ground_object_placements if p.plane_id == "caddy")
    assert (gp.x_m, gp.y_m, gp.heading_deg) == (3.0, 4.0, 90.0)
    assert gp.hand_placed is True
    caddy_moves = [m for m in result.moves_plan.moves if m.plane_id == "caddy"]
    assert caddy_moves and all(m.path is None for m in caddy_moves)  # path-less

def test_pinned_mover_is_excluded_from_the_search():
    # two solves of a pinned scenario at different seeds both seat the caddy at the SAME pin
    r1 = solve(_scenario_with_mover_pin(...), seed=1)
    r2 = solve(_scenario_with_mover_pin(...), seed=2)
    assert _mover_pose(r1, "caddy") == _mover_pose(r2, "caddy")  # the pin, seed-independent

def test_pinned_caddy_blocking_the_door_is_rejected():
    # a hard_door_mover pinned across the door → egress gate fails
    with pytest.raises(NoFeasiblePlanError):  # or the exit-3 path the harness uses
        solve(_scenario_with_mover_pin(mover="caddy", **_pose_blocking_door()), render_paths=True)
```
Add a `@serial` canary mirroring the existing unpinned double-solve one (grep `tests/test_solver_canaries.py`), asserting an **unpinned**-mover scenario is byte-identical across two solves (the ADR-0003 guard — the change must not perturb the unpinned path).

- [ ] **Step 3: Run → verify FAIL.** `pytest tests/test_solver.py -k "pinned_mover or pinned_caddy" -v` → FAIL (mover_pins not honored).

- [ ] **Step 4a: Extend `pinned_planes`.** Add the pinned mover ids:
```python
pinned_planes = frozenset(
    pid for pid in scenario.fleet_in
    if pid in scenario.constraints and scenario.constraints[pid].pin is not None
) | frozenset(scenario.mover_pins)
```

- [ ] **Step 4b: Return the pin for a pinned mover.** In `_initial_placement_for_plane` (or the mover-sampling path that calls it), before sampling, short-circuit a mover id to its pin:
```python
mover_pin = scenario.mover_pins.get(plane_id)
if mover_pin is not None:
    return mover_pin   # already Placement(..., on_carts=False, hand_placed=True)
```
(Place this beside the existing aircraft `constraint.pin` short-circuit; the two are disjoint by id.)

- [ ] **Step 4c: Materialize into the Layout with `hand_placed`.** Confirm `_build_layout`'s `movers` split carries the sampled mover `Placement` (which, for a pinned mover, IS the pin with `hand_placed=True`) into `Layout.ground_object_placements` verbatim. It should require no change if `_initial_placement_for_plane` already returned the `hand_placed=True` pin and the descent never perturbs a pinned id — verify the pin's `hand_placed=True` survives into the built layout (the Task-1 guard now permits it). If any `_build_layout`/perturbation step reconstructs a mover `Placement` and drops `hand_placed`, fix it to preserve the flag for a pinned mover.

- [ ] **Step 5: Run → verify PASS + determinism.** `pytest tests/test_solver.py -k "pinned_mover or pinned_caddy" -v` → PASS. Then the canary: `pytest tests/test_solver_canaries.py -q` (serial) → the unpinned double-solve stays byte-identical.

- [ ] **Step 6: Lint/type + the safe test gate.** `ruff check src/hangarfit/solver.py tests/ && mypy src/hangarfit/ && make test` (the two-pass split incl. the serial canaries) → green. If `make test` is too broad for iteration, at minimum run `tests/test_solver*.py` + `tests/test_loader*.py` + `tests/test_towplanner*.py` + `tests/test_models.py`.

- [ ] **Step 7: Commit.**
```bash
git add src/hangarfit/solver.py tests/test_solver.py tests/test_solver_canaries.py
git commit -m "feat(solver): seat a pinned mover path-less at its pin; unpinned byte-identical (#912)"
```

---

### Task 5: ADR + CHANGELOG + PR

**Files:**
- Create: `docs/adr/0031-mover-pin.md` (a small new ADR; cross-link ADR-0025)
- Modify: `CHANGELOG.md`; and update the ADR-0025 status/links block to point at 0031 for the mover-pin amendment.

- [ ] **Step 1: Write the ADR.** `docs/adr/0031-mover-pin.md` — Status: Accepted; Context: movers were solver-placed only (#604), no hand-place; Decision: an optional `Scenario.mover_pins` pins a `placed_routed_mover` at a hand-authored pose, seated as a **path-less keep-out** (`hand_placed` generalized to movers) that retains its `placed_routed_mover` class, collision membership, rendering, and — for a `hard_door_mover` — the ADR-0026 egress gate; unpinned movers unchanged (ADR-0003); rejected alternatives (fixed_obstacle reuse; tow-routed pin). Reference it from ADR-0025's D-section as an amendment. Follow the repo's ADR format (read an existing ADR e.g. `docs/adr/0025-ground-object-taxonomy.md` for the template).

- [ ] **Step 2: CHANGELOG entry.** Under `## [Unreleased]` → `### Added`:
```markdown
- **Mover pin (hand-place a car/trailer)** — a scenario `placed_routed_mover` may now carry an optional pose (`ground_objects: [{object: <id>, x_m, y_m, heading_deg}]`), pinning it at a hand-chosen spot instead of letting the solver place it. A pinned mover is seated as a path-less keep-out the rest of the fill routes around, keeping its mover class, rendering, and (for the Caddy) the hard-door egress gate; an un-pinned mover is unchanged and byte-identical (ADR-0003). Backend for the drag-to-fix mover UX (the editor half follows). (#912, ADR-0031)
```

- [ ] **Step 3: Lint/type + docs sanity.** `ruff check src/ tests/ && mypy src/hangarfit/` (no code change here, but confirm still green) and skim the ADR renders.

- [ ] **Step 4: Commit + push + draft PR.**
```bash
git add docs/adr/0031-mover-pin.md docs/adr/0025-ground-object-taxonomy.md CHANGELOG.md
git commit -m "docs(adr): ADR-0031 mover pin + CHANGELOG (#912)"
git push -u origin feature/912-mover-pin-backend
```
Open a **draft** PR (base `develop`, body via `--body-file` with `Part of #912` — NOT `Closes`, since PR B closes it), assignee `DocGerd`, labels `enhancement`,`area:backend`, milestone 39 (via `gh api`).

- [ ] **Step 5: Review arc.** Final whole-branch review package (`review-package MERGE_BASE HEAD`, `MERGE_BASE = git merge-base develop HEAD`), dispatched to: `code-reviewer`; `silent-failure-hunter` (loader error paths); `type-design-analyzer` (`models.py` `mover_pins` + the relaxed guard); `determinism-guard` (`solver.py`/`towplanner.py` — the byte-identical-unpinned contract). Convert findings to inline threads on the reviewed commit, fix Critical/Important, reply + resolve, post a review-evidence summary, flip out of draft. **Do not merge — the user is the sole merger.**

---

## Self-Review (author checklist — done)

1. **Spec coverage:** §3.1 model (mover_pins + guard relax) → **T1** · §3.2 loader → **T2** · §3.3 path-less planner + egress → **T3** (path-less/keep-out) + **T4** (seat + egress test) · §7 tests → each task's TDD + the T4 `@serial` canary · §8 ADR + delivery → **T5**. Editor (§4) is explicitly PR B, not covered here (correct).
2. **Placeholder scan:** the test helper names (`_scenario`, `_layout_with_hand_placed_mover`, etc.) are flagged as "reuse the file's existing helpers" — implementers adapt to real fixtures; the discriminating assertions are concrete. The two recon-resolved unknowns (hand_placed generalization, egress-keys-off-pose) are now concrete tasks (T3) / a confirming test (T4), not open questions.
3. **Type consistency:** `Scenario.mover_pins: Mapping[str, Placement]` (T1) is consumed by the loader (T2 builds it), the solver (T4 reads it), and materialized as `Placement(..., hand_placed=True)` seated in `Layout.ground_object_placements` (T4) which T3's planner renders path-less — one coherent chain. `pinned_planes` extension (T4) and the guard relaxation (T1) match their consumers.
