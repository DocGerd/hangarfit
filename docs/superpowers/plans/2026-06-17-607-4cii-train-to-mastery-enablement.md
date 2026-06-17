# 4c-ii Train-to-Mastery Enablement + Knobs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unblock the learned-backend benchmark's policy column on the real Herrenteich anchors (env fixed-obstacle support), fix the #694 maintenance-bay oracle divergence, and add four default-neutral knobs that let the policy escape the place-nothing/place-invalid local optimum — all without touching `src/` or the deterministic solver.

**Architecture:** All work lives in the top-level torch-free-where-possible `ml/` package (never in the wheel). The deterministic `collisions.check` is consumed read-only as the single source of validity truth via a new shared `ml/geometry_oracle.layout_valid()` helper. The four knobs (`r_valid_park`, `dense_slot_potential`, per-rung entropy anneal, std-only return normalization) all default to a neutral value that reproduces today's training byte-identically; only the #694 fix changes behavior at defaults (a deliberate bugfix).

**Tech Stack:** Python 3.12, shapely (geometry oracle), PyTorch (`[train]` extra — ppo/train tests use `importorskip`), pytest. Hangarfit deterministic core (`collisions.check`, `towplanner` motion primitives) reused as the reward oracle only.

## Global Constraints

- **(A) Apron-entry realism:** `reset()`/`_spawn()` are NEVER modified. Every object the policy places is driven in from the apron. No teleport / mid-hangar spawn.
- **(B) Solver-independence:** `solve()` / RR-MC / Hybrid-A* is NEVER invoked in the training loop. No anchor/witness/label/start-state from the deterministic search. The `dense_slot_potential` query is a pure `state → scalar` shapely query — never a placement search or nester.
- **Default-neutral knobs:** All seven new config fields default neutral; knobs-off ⇒ reward scalar + PPO update bit-identical to the post-#694 code. The #694 fix itself is NOT neutral (it changes behavior on bay-clipping layouts by design).
- **No `src/` changes.** Only files under `ml/`, `tests/ml/`, `docs/`, `CHANGELOG.md`. `collisions.check` is read-only.
- **No `--no-verify`, no force-push.** The PostToolUse hook runs ruff+pytest after `ml/`/`tests/` edits; the Stop hook runs mypy. Fix root causes.
- **Lint/type:** `ruff check ml/ tests/ml/`, `ruff format ml/ tests/ml/`, `mypy ml/` (or the project's configured `mypy` target) must pass.
- **`seed_anchor` stays the unused `False` placeholder** in `DifficultyConfig`. Do not wire it.
- Branch: `feature/693-train-to-mastery-enablement` (already created; spec committed at `f37bca8`). PR body: `Closes #693`, `Closes #694`.

---

## File structure

| File | Responsibility | Action |
|---|---|---|
| `ml/geometry_oracle.py` | Add `layout_valid()` (shared validity); gate `intrusion_area_m2` bay term; add `active_misfit_m2()` | Modify |
| `ml/types.py` | `RewardWeights`: add `r_valid_park`, `dense_slot_potential` | Modify |
| `ml/reward.py` | `RewardContext.park_valid`; `potential(active_misfit_m2=...)`; `step_reward` bonus | Modify |
| `ml/env.py` | `_fixed` list + `_layout()` union; delegate `_layout_valid` to oracle; `park_valid` + bay-gated intrusion in Park; dense potential | Modify |
| `ml/benchmark.py` | `_layout_valid` → call-through; `build_scenario_env` pre-places fixed obstacles | Modify |
| `ml/ppo.py` | `PPOConfig` fields; `entropy_coef_at()`; `ReturnNormalizer`; `ppo_update` optional normalizer | Modify |
| `ml/train.py` | Thread `RewardWeights`; per-rung entropy wiring; normalizer; CLI flags | Modify |
| `tests/ml/test_geometry_oracle.py` | bay-gate, layout_valid, active_misfit, #694 regression | Modify |
| `tests/ml/test_reward.py` | r_valid_park gate + ordering; potential neutrality | Modify |
| `tests/ml/test_env.py` | fixed-obstacle union, terminal_fraction, #694 integration | Modify |
| `tests/ml/test_benchmark.py` | build_scenario_env accepts anchors | Modify |
| `tests/ml/test_ppo.py` | entropy schedule fn; return normalizer | Modify |
| `tests/ml/test_train_curriculum.py` | per-rung entropy; defaults neutral | Modify |
| `CHANGELOG.md`, `ml/README.md` | `[Unreleased]` entry + knob docs | Modify |

**Run tests with:** `pytest tests/ml/ -q` (torch tests `importorskip`; torch is installed CPU locally per project notes). Single test: `pytest tests/ml/test_x.py::test_y -v`.

---

## Task 1: #694 — unify validity through one shared oracle helper

**Files:**
- Modify: `ml/geometry_oracle.py` (add `layout_valid`; gate `intrusion_area_m2` bay term)
- Modify: `ml/env.py` (`_layout_valid` delegates; Park-branch intrusion uses `bay_closed=False`)
- Modify: `ml/benchmark.py` (`_layout_valid` becomes a call-through)
- Test: `tests/ml/test_geometry_oracle.py`, `tests/ml/test_env.py`

**Interfaces:**
- Produces: `geometry_oracle.layout_valid(layout: Layout) -> bool` (= `check(layout).valid and not egress_blocked(layout)`); `intrusion_area_m2(body, placement, hangar, *, bay_closed: bool = False) -> float`.
- Consumes: `hangarfit.collisions.check` (`CheckResult.valid` property; `.conflicts`), `geometry_oracle.egress_blocked`.

- [ ] **Step 1: Write the failing test for the bay gate**

In `tests/ml/test_geometry_oracle.py` add (reuse `_fuji`, `empty_hangar` from conftest):

```python
from shapely.geometry import box
from hangarfit.geometry import aircraft_parts_world
from hangarfit.models import Placement
from ml import geometry_oracle as go


def _bay_area_for(body, placement, hangar):
    bay = hangar.maintenance_bay
    bay_poly = box(bay.center_x_m - bay.width_m / 2, hangar.length_m - bay.depth_m,
                   bay.center_x_m + bay.width_m / 2, hangar.length_m)
    return sum(wp.polygon.intersection(bay_poly).area for wp in aircraft_parts_world(body, placement))


def test_intrusion_bay_term_gated_on_bay_closed():
    fleet = _fuji()
    hangar = empty_hangar()
    body = fleet["fuji"]
    bay = hangar.maintenance_bay
    # Park inside the bay rectangle (centroid at the bay centre, one body-length in).
    pl = Placement(plane_id="fuji", x_m=bay.center_x_m,
                   y_m=hangar.length_m - bay.depth_m / 2, heading_deg=0.0, on_carts=False)
    overlap_area = _bay_area_for(body, pl, hangar)
    assert overlap_area > 0.0, "fixture must actually clip the bay; adjust y if not"
    open_intr = go.intrusion_area_m2(body, pl, hangar, bay_closed=False)
    closed_intr = go.intrusion_area_m2(body, pl, hangar, bay_closed=True)
    assert closed_intr - open_intr == pytest.approx(overlap_area, abs=1e-6)
```

Add `import pytest` if absent.

- [ ] **Step 2: Run it — expect FAIL** (`bay_closed` is not a parameter yet)

Run: `pytest tests/ml/test_geometry_oracle.py::test_intrusion_bay_term_gated_on_bay_closed -v`
Expected: FAIL — `TypeError: intrusion_area_m2() got an unexpected keyword argument 'bay_closed'`.

- [ ] **Step 3: Gate the bay term in `intrusion_area_m2`**

In `ml/geometry_oracle.py`, change the signature and the bay loop:

```python
def intrusion_area_m2(
    body: Aircraft | GroundObject,
    placement: Placement,
    hangar: Hangar,
    *,
    bay_closed: bool = False,
) -> float:
    """Footprint area (m²) outside the hangar floor or inside a CLOSED maintenance bay.

    Mirrors collisions.check's ADR-0006 rule: the maintenance bay is a keep-out ONLY when
    it is closed (``layout.maintenance_plane is not None``). The env never sets a maintenance
    occupant, so it always passes ``bay_closed=False`` and the bay term vanishes — fixing the
    #694 over-strict-inert-bay divergence on the reward gradient. Out-of-floor (walls/notch via
    ``floor_polygon``) is always counted. The front apron (``y < 0``) counts as intrusion for a
    PARKED pose (it is a motion region, not a parked-validity region)."""
    floor = hangar.floor_polygon
    if floor is None:
        floor = box(0.0, 0.0, hangar.width_m, hangar.length_m)
    bay = hangar.maintenance_bay
    bay_poly = box(
        bay.center_x_m - bay.width_m / 2.0,
        hangar.length_m - bay.depth_m,
        bay.center_x_m + bay.width_m / 2.0,
        hangar.length_m,
    )
    total = 0.0
    for wp in aircraft_parts_world(body, placement):
        poly = wp.polygon
        total += poly.difference(floor).area  # outside the floor (walls/notch)
        if bay_closed:
            total += poly.intersection(bay_poly).area  # inside a CLOSED maintenance bay
    return total
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/ml/test_geometry_oracle.py::test_intrusion_bay_term_gated_on_bay_closed -v`
Expected: PASS.

- [ ] **Step 5: Write the failing test for `layout_valid` + the #694 integration regression**

```python
from pathlib import Path
from hangarfit.collisions import check
from hangarfit.loader import load_layout

_ROOT = Path(__file__).resolve().parents[2]  # repo root


def test_layout_valid_matches_product_checker_plus_egress():
    layout = single_object_layout(x_m=5.0, y_m=5.0)  # a clean, valid placement
    assert go.layout_valid(layout) == (check(layout).valid and not go.egress_blocked(layout))
    assert go.layout_valid(layout) is True


def test_layout_full_witness_is_valid_694_regression():
    # The committed herrenteich_full witness was WRONGLY rejected by the old env oracle
    # (inert maintenance bay over-enforced, #694). It is valid per the product checker.
    layout = load_layout(str(_ROOT / "examples/herrenteich/layout_full.yaml"))
    assert check(layout).valid, "precondition: witness valid per collisions.check"
    assert go.layout_valid(layout) is True
```

- [ ] **Step 6: Run — expect FAIL** (`layout_valid` not defined)

Run: `pytest tests/ml/test_geometry_oracle.py -k "layout_valid or 694" -v`
Expected: FAIL — `AttributeError: module 'ml.geometry_oracle' has no attribute 'layout_valid'`.

- [ ] **Step 7: Add `layout_valid` to `ml/geometry_oracle.py`**

Add to `__all__` (`"layout_valid"`) and define (place near `egress_blocked`):

```python
def layout_valid(layout: Layout) -> bool:
    """Whole-layout validity per the PRODUCT deterministic checker (== ``hangarfit check``):
    collisions.check reports no conflicts (overlap + hangar bounds/notch + CONDITIONAL
    maintenance bay + ground-obstacle keep-outs) AND no Caddy hard-door egress violation
    (ADR-0026). The single source of validity truth shared by the env gate, the r_valid_park
    bonus gate, and the benchmark — so the bonus and the promotion metric can never disagree.
    Replaces the env's old hand-rolled overlap+intrusion+egress, which over-enforced the inert
    maintenance bay (#694)."""
    return check(layout).valid and not egress_blocked(layout)
```

- [ ] **Step 8: Run — expect PASS**

Run: `pytest tests/ml/test_geometry_oracle.py -k "layout_valid or 694" -v`
Expected: PASS.

- [ ] **Step 9: Delegate `env._layout_valid` to the oracle; bay-gate the Park intrusion**

In `ml/env.py`, replace `_layout_valid` (lines ~255-269) with:

```python
    def _layout_valid(self) -> bool:
        """Whole-layout validity == the product checker (the prime directive's final gate),
        via the shared ``geometry_oracle.layout_valid``. Reward terms read ctx, not this, so
        this is gate/reporting only. (Was hand-rolled overlap+intrusion+egress that
        over-enforced the inert maintenance bay — #607 SP#4c-ii / #694.)"""
        return go.layout_valid(self._layout())
```

In the Park branch (line ~178), pass `bay_closed=False` (the env never closes the bay):

```python
            intrusion = go.intrusion_area_m2(body, pl, self.hangar, bay_closed=False)
```

- [ ] **Step 10: Write the env-level #694 integration test**

In `tests/ml/test_env.py`:

```python
def test_env_layout_valid_delegates_to_product_checker():
    from ml import geometry_oracle as go
    env = HangarFitEnv(hangar=empty_hangar(), fleet=_fuji(), requested_ids=("fuji",))
    env.reset()
    assert env._layout_valid() == go.layout_valid(env._layout())
```

- [ ] **Step 11: Run the full Task-1 test set + lint/type**

Run: `pytest tests/ml/test_geometry_oracle.py tests/ml/test_env.py -q`
Then: `ruff check ml/ tests/ml/ && ruff format --check ml/ tests/ml/ && mypy ml/`
Expected: all PASS. (If a 4b golden reward/history fixture asserted bay-clipping behavior, it now changes — re-baseline it and note the diff as the #694 correction.)

- [ ] **Step 12: Commit**

```bash
git add ml/geometry_oracle.py ml/env.py ml/benchmark.py tests/ml/test_geometry_oracle.py tests/ml/test_env.py
git commit -m "fix(607): unify env validity via shared layout_valid oracle; gate bay on ADR-0006 (#694)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

(Also make `benchmark._layout_valid` a call-through in this commit — see Step 13.)

- [ ] **Step 13: Make `benchmark._layout_valid` a call-through (single source of truth)**

In `ml/benchmark.py`, replace the body of `_layout_valid` with:

```python
def _layout_valid(layout: Layout) -> bool:
    """Valid per the PRODUCT deterministic checker — delegates to the shared
    geometry_oracle.layout_valid so the env gate, the policy scorer, and witness_valid are
    judged by one identical predicate (collisions.check + Caddy egress; the inert maintenance
    bay is conditional, #694)."""
    return go.layout_valid(layout)
```

Run: `pytest tests/ml/test_benchmark.py -q` — expect PASS (existing benchmark validity tests unchanged in behavior). Amend the commit or commit separately.

---

## Task 2: Fixed-obstacle env support

**Files:**
- Modify: `ml/env.py` (constructor `fixed_placements`; `self._fixed`; `_layout()` union)
- Modify: `ml/benchmark.py` (`build_scenario_env` pre-places fixed obstacles)
- Test: `tests/ml/test_env.py`, `tests/ml/test_benchmark.py`

**Interfaces:**
- Consumes: `Scenario.fixed_obstacle_placements: tuple[Placement, ...]`, `Scenario.ground_object_defs`, `Scenario.mover_ids`/`placeable_ids` (benchmark already uses these).
- Produces: `HangarFitEnv(..., fixed_placements: tuple[Placement, ...] = ())`; `env._fixed: list[Placement]`; `_layout()` includes fixed obstacles.

- [ ] **Step 1: Write the failing test — fixed obstacle is in `_layout()` but not `_parked`, terminal_fraction uncorrupted**

In `tests/ml/test_env.py` (reuse `_fuji`, `empty_hangar`; build a `GroundObject` fixed obstacle):

```python
from hangarfit.models import GroundObject, Placement


def _fuel_trailer() -> GroundObject:
    # Minimal fixed obstacle; mirrors the catalog maul_fuel_trailer shape closely enough.
    return GroundObject(id="fuel", length_m=4.0, width_m=2.0, height_m=2.0,
                        object_class="fixed_obstacle")


def test_fixed_obstacle_in_layout_not_parked_and_fraction_uncorrupted():
    fleet = _fuji()
    fuel = _fuel_trailer()
    fixed = (Placement(plane_id="fuel", x_m=2.0, y_m=10.0, heading_deg=0.0, on_carts=False),)
    env = HangarFitEnv(
        hangar=empty_hangar(), fleet=fleet, requested_ids=("fuji",),
        ground_objects={"fuel": fuel}, fixed_placements=fixed,
    )
    env.reset()
    # The fixed obstacle is present in the scene from step 0...
    layout = env._layout()
    assert "fuel" in {gp.plane_id for gp in layout.ground_object_placements}
    # ...but it is NOT counted as a parked (driven-in) object.
    assert env._fixed == list(fixed)
    assert all(p.plane_id != "fuel" for p in env._parked)
    # terminal_fraction denominator is the requested (driven) set only -> 1 here, not 2.
    # Drive fuji nowhere and Park it: fraction = 1/1 even with the fixed obstacle present.
    from ml.types import Park
    _obs, _r, done, info = env.step(Park())
    assert done and info.total == 1 and info.placed == 1
```

If `GroundObject`'s required fields differ, mirror an existing GroundObject construction in `tests/` (grep `GroundObject(` under `tests/`).

- [ ] **Step 2: Run — expect FAIL** (`fixed_placements` unknown kwarg)

Run: `pytest tests/ml/test_env.py::test_fixed_obstacle_in_layout_not_parked_and_fraction_uncorrupted -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'fixed_placements'`.

- [ ] **Step 3: Add `fixed_placements` to the env constructor + `_fixed`; union in `_layout()`**

In `ml/env.py` `__init__`, add the parameter and store it (after `ground_objects`):

```python
        ground_objects: Mapping[str, GroundObject] | None = None,
        fixed_placements: tuple[Placement, ...] = (),
        difficulty: DifficultyConfig | None = None,
        weights: RewardWeights | None = None,
    ) -> None:
        self.hangar = hangar
        self.fleet = dict(fleet)
        self.ground_objects = dict(ground_objects or {})
        self.requested_ids = requested_ids
        self._fixed: list[Placement] = list(fixed_placements)
        self.difficulty = difficulty or DifficultyConfig()
        self.weights = weights or RewardWeights()
        self._reset_state()
```

Change `_layout()` to union parked + fixed:

```python
    def _layout(self) -> Layout:
        """The scene of FROZEN objects: driven-in (parked) PLUS pre-placed fixed obstacles
        (immovable keep-outs). The active object is not yet in it. Fixed obstacles are NOT in
        ``_parked`` (so terminal_fraction is uncorrupted) but ARE in the scene so overlap /
        egress / motion-clearance see them."""
        frozen = self._parked + self._fixed
        frozen_ids = [p.plane_id for p in frozen]
        ac = {pid: self.fleet[pid] for pid in frozen_ids if pid in self.fleet}
        go_ids = [pid for pid in frozen_ids if pid in self.ground_objects]
        return Layout(
            fleet=ac or {next(iter(self.fleet)): next(iter(self.fleet.values()))},
            hangar=self.hangar,
            placements=tuple(p for p in frozen if p.plane_id in self.fleet),
            ground_objects={pid: self.ground_objects[pid] for pid in go_ids},
            ground_object_placements=tuple(p for p in frozen if p.plane_id in self.ground_objects),
        )
```

Note: `_fixed` must survive `_reset_state()` (it is set in `__init__`, not in `_reset_state`, so it persists across resets — correct, it is scenario-level).

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/ml/test_env.py::test_fixed_obstacle_in_layout_not_parked_and_fraction_uncorrupted -v`
Expected: PASS.

- [ ] **Step 5: Write the failing test — a placed body overlapping a fixed obstacle is invalid**

```python
def test_placed_body_overlapping_fixed_obstacle_is_invalid():
    fleet = _fuji()
    fuel = _fuel_trailer()
    # Place the fuel obstacle exactly where we will park the fuji -> guaranteed overlap.
    fixed = (Placement(plane_id="fuel", x_m=9.0, y_m=10.0, heading_deg=0.0, on_carts=False),)
    env = HangarFitEnv(hangar=empty_hangar(), fleet=fleet, requested_ids=("fuji",),
                       ground_objects={"fuel": fuel}, fixed_placements=fixed)
    env.reset()
    env._active_pose = type(env._active_pose)(x_m=9.0, y_m=10.0, heading_deg=0.0)
    from ml.types import Park
    env.step(Park())
    assert env._layout_valid() is False  # fuji parts overlap the fixed fuel obstacle
```

- [ ] **Step 6: Run — expect PASS** (already works via `_layout()` union + `layout_valid`)

Run: `pytest tests/ml/test_env.py::test_placed_body_overlapping_fixed_obstacle_is_invalid -v`
Expected: PASS. (If FAIL, the overlap fixture didn't actually overlap — adjust coordinates so the fuji footprint covers the fuel obstacle.)

- [ ] **Step 7: Write the failing test — `build_scenario_env` accepts a fixed-obstacle anchor**

In `tests/ml/test_benchmark.py`, replace the old "raises NotImplementedError" assertion with:

```python
from ml.benchmark import BENCH_SET, build_scenario_env


def test_build_scenario_env_preplaces_fixed_obstacles_for_anchors():
    anchor = next(s for s in BENCH_SET if s.name == "herrenteich_full")
    env = build_scenario_env(anchor)  # must NOT raise
    env.reset()
    layout = env._layout()
    # The fuel trailer (fixed_obstacle) is pre-placed in the scene from step 0...
    go_ids = {gp.plane_id for gp in layout.ground_object_placements}
    assert "maul_fuel_trailer" in go_ids
    # ...and is NOT in the driven queue (requested set excludes fixed obstacles).
    assert "maul_fuel_trailer" not in env.requested_ids
```

(Grep the old test name asserting `NotImplementedError` / `pytest.raises` on `build_scenario_env` and delete it.)

- [ ] **Step 8: Run — expect FAIL** (`build_scenario_env` still raises)

Run: `pytest tests/ml/test_benchmark.py::test_build_scenario_env_preplaces_fixed_obstacles_for_anchors -v`
Expected: FAIL — `NotImplementedError: ... carries fixed obstacle(s)`.

- [ ] **Step 9: Replace the raise with pre-placement in `build_scenario_env`**

In `ml/benchmark.py`, replace the `fixed`/`if fixed: raise NotImplementedError(...)` block and the env construction with:

```python
    fixed_ids = [
        gid for gid in sc.ground_objects
        if sc.ground_object_defs[gid].object_class == "fixed_obstacle"
    ]
    placeable = sc.placeable_ids
    # Movers (placed_routed_mover) are driven in; fixed obstacles are pre-placed keep-outs.
    movers = {gid: sc.ground_object_defs[gid] for gid in sc.mover_ids}
    fixed_defs = {gid: sc.ground_object_defs[gid] for gid in fixed_ids}
    per_object = 120
    difficulty = DifficultyConfig(
        max_objects=len(placeable),
        per_object_step_budget=per_object,
        total_step_budget=per_object * max(1, len(placeable)),
    )
    hangar = replace(sc.hangar, apron_depth_m=8.0)
    return HangarFitEnv(
        hangar=hangar,
        fleet=sc.fleet,
        requested_ids=placeable,
        ground_objects={**movers, **fixed_defs},
        fixed_placements=sc.fixed_obstacle_placements,
        difficulty=difficulty,
    )
```

Verify `sc.placeable_ids` excludes fixed-obstacle ids (it is aircraft + movers). If `placeable_ids` includes fixed obstacles, subtract `fixed_ids` from `requested_ids` explicitly:
`requested_ids = tuple(i for i in placeable if i not in set(fixed_ids))`. (Confirm from `models.Scenario.placeable_ids` definition during implementation; adjust the docstring accordingly.) Update `build_scenario_env`'s docstring to describe pre-placement instead of the raise.

- [ ] **Step 10: Run — expect PASS + lint/type**

Run: `pytest tests/ml/test_benchmark.py tests/ml/test_env.py -q && ruff check ml/ tests/ml/ && mypy ml/`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add ml/env.py ml/benchmark.py tests/ml/test_env.py tests/ml/test_benchmark.py
git commit -m "feat(607): env fixed-obstacle pre-placement; benchmark policy column unblocked (#693)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Reward knobs — `r_valid_park` + `dense_slot_potential`

**Files:**
- Modify: `ml/types.py` (`RewardWeights.r_valid_park`, `.dense_slot_potential`)
- Modify: `ml/reward.py` (`RewardContext.park_valid`; `potential(active_misfit_m2=...)`; `step_reward` bonus)
- Modify: `ml/geometry_oracle.py` (`active_misfit_m2`)
- Modify: `ml/env.py` (set `park_valid`; compute dense potential)
- Test: `tests/ml/test_reward.py`, `tests/ml/test_geometry_oracle.py`, `tests/ml/test_env.py`

**Interfaces:**
- Produces: `RewardWeights.r_valid_park: float = 0.0`, `RewardWeights.dense_slot_potential: bool = False`; `RewardContext.park_valid: bool = False`; `potential(*, remaining_overlap_m2, active_dist_to_slot_m, unplaced, active_misfit_m2: float = 0.0)`; `geometry_oracle.active_misfit_m2(body, pose, parked_layout, hangar) -> float`.
- Consumes: `geometry_oracle.layout_valid` (Task 1) for the `park_valid` gate.

- [ ] **Step 1: Write the failing test — `r_valid_park=0.0` is byte-identical; bonus paid iff `park_valid`**

In `tests/ml/test_reward.py`:

```python
from ml.reward import RewardContext, step_reward
from ml.types import RewardWeights


def _ctx(**kw):
    base = dict(overlap_m2=0.0, intrusion_m2=0.0, swept_intrusion_m2=0.0, egress_blocked=False,
               move_cost=0.0, min_gap_m=0.0, seq_deviation=0.0, region_match=0.0,
               prev_potential=0.0, potential=0.0, terminal_fraction=None)
    base.update(kw)
    return RewardContext(**base)


def test_r_valid_park_default_zero_is_byte_identical():
    # park_valid defaults False and r_valid_park defaults 0.0 -> no change.
    ctx = _ctx()
    assert step_reward(ctx, RewardWeights()) == step_reward(ctx, RewardWeights(r_valid_park=0.0))


def test_r_valid_park_paid_only_when_park_valid():
    w = RewardWeights(r_valid_park=2.0)
    valid = _ctx(park_valid=True)
    invalid = _ctx(park_valid=False)
    assert step_reward(valid, w) - step_reward(invalid, w) == pytest.approx(2.0)


def test_r_valid_park_cannot_make_an_overlapping_park_profitable():
    # Ordering invariant: bonus << w_col * smallest meaningful overlap, and only paid on valid.
    w = RewardWeights(r_valid_park=2.0)
    overlapping = _ctx(overlap_m2=0.05, park_valid=False)  # invalid -> no bonus, big penalty
    assert step_reward(overlapping, w) < 0.0
    assert w.r_valid_park < w.w_col * 0.05
```

`RewardContext` needs a `park_valid` field with a default for `_ctx` to omit it.

- [ ] **Step 2: Run — expect FAIL** (`r_valid_park` / `park_valid` unknown)

Run: `pytest tests/ml/test_reward.py -k r_valid_park -v`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'r_valid_park'` (or `park_valid`).

- [ ] **Step 3: Add the fields + the bonus term**

`ml/types.py` — add to `RewardWeights` (after `gamma`):

```python
    r_valid_park: float = 0.0  # bonus paid in Park ONLY when the layout is valid (basin escape)
    dense_slot_potential: bool = False  # add an in-hangar nearest-free-pocket shaping term
```

`ml/reward.py` — add to `RewardContext` (after `terminal_fraction`):

```python
    park_valid: bool = False  # this Park left the whole layout valid (per layout_valid); Park steps only
```

`ml/reward.py` — in `step_reward`, add the bonus:

```python
    terminal = w.r_terminal * ctx.terminal_fraction if ctx.terminal_fraction is not None else 0.0
    shaping = w.gamma * ctx.potential - ctx.prev_potential
    valid_park = w.r_valid_park if ctx.park_valid else 0.0
    return hard + movement + soft + terminal + shaping + valid_park
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/ml/test_reward.py -k r_valid_park -v`
Expected: PASS.

- [ ] **Step 5: Wire `park_valid` in the env Park branch**

In `ml/env.py` Park branch, after computing `placed_layout` and before building `ctx`, compute validity and pass it:

```python
            placed_layout = self._layout()
            overlap = go.overlap_area_m2(placed_layout)
            intrusion = go.intrusion_area_m2(body, pl, self.hangar, bay_closed=False)
            egress = go.egress_blocked(placed_layout)
            park_valid = go.layout_valid(placed_layout)
            ...
            ctx = RewardContext(
                ...
                terminal_fraction=terminal_fraction,
                park_valid=park_valid,
            )
```

(The movement branch leaves `park_valid` at its `False` default — bonus is a Park-only term.)

- [ ] **Step 6: Write the failing test — `active_misfit_m2`**

In `tests/ml/test_geometry_oracle.py`:

```python
from ml.types import Pose


def test_active_misfit_zero_in_clean_pocket_and_positive_when_overlapping():
    fleet = _fuji()
    hangar = empty_hangar()
    body = fleet["fuji"]
    empty = single_object_layout(x_m=5.0, y_m=5.0)  # one parked body near (5,5)
    # Clean pocket far from the parked body, well inside the floor -> misfit 0.
    clean = Pose(x_m=14.0, y_m=20.0, heading_deg=0.0)
    assert go.active_misfit_m2(body, clean, empty, hangar) == pytest.approx(0.0, abs=1e-9)
    # Right on top of the parked body -> positive misfit.
    on_top = Pose(x_m=5.0, y_m=5.0, heading_deg=0.0)
    assert go.active_misfit_m2(body, on_top, empty, hangar) > 0.0


def test_active_misfit_never_invokes_search(monkeypatch):
    import hangarfit.solver as solver
    monkeypatch.setattr(solver, "solve", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("active_misfit_m2 must not call solve()")))
    fleet = _fuji()
    go.active_misfit_m2(fleet["fuji"], Pose(x_m=5.0, y_m=5.0, heading_deg=0.0),
                        single_object_layout(x_m=12.0, y_m=20.0), empty_hangar())
```

Adjust `single_object_layout` coordinates if the default parked body sits where `clean` is.

- [ ] **Step 7: Run — expect FAIL** (`active_misfit_m2` not defined)

Run: `pytest tests/ml/test_geometry_oracle.py -k active_misfit -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 8: Implement `active_misfit_m2` (pure geometry, no search)**

In `ml/geometry_oracle.py` add to `__all__` (`"active_misfit_m2"`) and define:

```python
def active_misfit_m2(
    body: Aircraft | GroundObject,
    pose: Pose,
    parked_layout: Layout,
    hangar: Hangar,
) -> float:
    """Coarse, monotone 'how bad is parking the active body HERE' — for the
    dense_slot_potential shaping term. Pure state→scalar shapely query: the active
    footprint's overlap with parked bodies PLUS its in-hangar (y≥0) out-of-floor area.
    0.0 in a clean pocket; grows with intrusion. The apron (y<0) is EXCLUDED (the object
    legitimately starts there; the door-ingress term handles entry). NEVER calls solve()/a
    nester — that would re-import a search's reachable-distribution bias (constraint B)."""
    floor = hangar.floor_polygon
    if floor is None:
        floor = box(0.0, 0.0, hangar.width_m, hangar.length_m)
    upper = box(-1.0e6, 0.0, 1.0e6, hangar.length_m + 1.0e6)  # y >= 0 half-plane
    pl = Placement(plane_id="__active__", x_m=pose.x_m, y_m=pose.y_m,
                   heading_deg=pose.heading_deg, on_carts=False)
    obstacle_parts = [
        wp for p in parked_layout.placements
        for wp in aircraft_parts_world(parked_layout.fleet[p.plane_id], p)
    ] + [
        wp for gp in parked_layout.ground_object_placements
        for wp in aircraft_parts_world(parked_layout.ground_objects[gp.plane_id], gp)
    ]
    total = 0.0
    for wp in aircraft_parts_world(body, pl):
        poly = wp.polygon
        total += poly.difference(floor).intersection(upper).area  # in-hangar wall/notch intrusion
        for op in obstacle_parts:
            if poly.intersects(op.polygon):
                total += poly.intersection(op.polygon).area
    return total
```

Import `Pose` at the top of `geometry_oracle.py` if not already imported (it imports from `hangarfit.towplanner`).

- [ ] **Step 9: Run — expect PASS**

Run: `pytest tests/ml/test_geometry_oracle.py -k active_misfit -v`
Expected: PASS.

- [ ] **Step 10: Write the failing test — `dense_slot_potential` neutrality + effect**

In `tests/ml/test_reward.py`:

```python
from ml.reward import potential


def test_potential_active_misfit_default_zero_is_byte_identical():
    a = potential(remaining_overlap_m2=1.0, active_dist_to_slot_m=2.0, unplaced=3)
    b = potential(remaining_overlap_m2=1.0, active_dist_to_slot_m=2.0, unplaced=3, active_misfit_m2=0.0)
    assert a == b


def test_potential_active_misfit_lowers_potential():
    base = potential(remaining_overlap_m2=0.0, active_dist_to_slot_m=0.0, unplaced=0)
    worse = potential(remaining_overlap_m2=0.0, active_dist_to_slot_m=0.0, unplaced=0, active_misfit_m2=5.0)
    assert worse < base  # higher misfit -> lower potential (Φ is negative cost)
```

- [ ] **Step 11: Run — expect FAIL** (`active_misfit_m2` not a `potential` param)

Run: `pytest tests/ml/test_reward.py -k potential_active_misfit -v`
Expected: FAIL — `TypeError`.

- [ ] **Step 12: Extend `potential()` and compute it in the env**

`ml/reward.py`:

```python
def potential(
    *,
    remaining_overlap_m2: float,
    active_dist_to_slot_m: float,
    unplaced: int,
    active_misfit_m2: float = 0.0,
) -> float:
    """Shaping potential Φ(s) (spec §5). Higher is better. Φ is the NEGATIVE of the costs.
    ``active_misfit_m2`` (the dense_slot_potential in-hangar term) defaults 0.0 → byte-identical
    when the knob is off. Policy-invariant per Ng–Harada–Russell — cannot be reward-hacked."""
    return -(remaining_overlap_m2 + active_dist_to_slot_m + float(unplaced) + active_misfit_m2)
```

`ml/env.py` `_potential()`:

```python
    def _potential(self) -> float:
        layout = self._layout()
        remaining_overlap = go.overlap_area_m2(layout) if (self._parked or self._fixed) else 0.0
        misfit = 0.0
        if self.weights.dense_slot_potential and self._active_pose is not None and self._active_id is not None:
            misfit = go.active_misfit_m2(self._body(self._active_id), self._active_pose, layout, self.hangar)
        return potential(
            remaining_overlap_m2=remaining_overlap,
            active_dist_to_slot_m=self._active_dist_to_slot_m(),
            unplaced=len(self._queue) + (1 if self._active_id is not None else 0),
            active_misfit_m2=misfit,
        )
```

Note the `or self._fixed` guard: a fixed obstacle alone means `_layout()` is non-trivial, so overlap must be measured even before the first park.

- [ ] **Step 13: Run — expect PASS + the full Task-3 suite + lint/type**

Run: `pytest tests/ml/test_reward.py tests/ml/test_geometry_oracle.py tests/ml/test_env.py -q && ruff check ml/ tests/ml/ && mypy ml/`
Expected: PASS.

- [ ] **Step 14: Commit**

```bash
git add ml/types.py ml/reward.py ml/geometry_oracle.py ml/env.py tests/ml/test_reward.py tests/ml/test_geometry_oracle.py tests/ml/test_env.py
git commit -m "feat(607): r_valid_park bonus + dense_slot_potential shaping knobs (default-neutral) (#693)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Optimizer knobs — entropy anneal + std-only return normalization

**Files:**
- Modify: `ml/ppo.py` (`PPOConfig` fields; `entropy_coef_at`; `ReturnNormalizer`; `ppo_update` optional normalizer)
- Modify: `ml/train.py` (thread `RewardWeights`; per-rung entropy wiring; normalizer; CLI flags)
- Test: `tests/ml/test_ppo.py`, `tests/ml/test_train_curriculum.py`

**Interfaces:**
- Produces: `PPOConfig.{entropy_coef_start: float|None=None, entropy_coef_end: float|None=None, entropy_anneal_iters: int=0, normalize_returns: bool=False, return_norm_eps: float=1e-8}`; `ppo.entropy_coef_at(iteration: int, *, base: float, start: float|None, end: float|None, anneal_iters: int) -> float`; `ppo.ReturnNormalizer(eps: float, warmup: int)` with `.normalize(rewards: Tensor) -> Tensor`; `ppo_update(policy, optimizer, buffer, config, *, normalizer: ReturnNormalizer | None = None)`.
- Consumes: `RewardWeights` (Task 3) threaded into `build_stage_env`/`build_trivial_env`.

- [ ] **Step 1: Write the failing test — `entropy_coef_at` schedule**

In `tests/ml/test_ppo.py` (these are pure; no torch needed for the schedule fn, but the module imports torch — keep the existing `importorskip("torch")` at the top of the file):

```python
from ml.ppo import entropy_coef_at


def test_entropy_coef_constant_when_off():
    # start None -> constant base regardless of iteration.
    assert entropy_coef_at(0, base=0.01, start=None, end=None, anneal_iters=0) == 0.01
    assert entropy_coef_at(50, base=0.01, start=None, end=None, anneal_iters=0) == 0.01


def test_entropy_coef_linear_anneal_boundaries_and_monotone():
    f = lambda it: entropy_coef_at(it, base=0.01, start=0.05, end=0.005, anneal_iters=40)
    assert f(0) == pytest.approx(0.05)
    assert f(40) == pytest.approx(0.005)
    assert f(100) == pytest.approx(0.005)  # clamped past the window
    assert f(10) > f(30)  # monotone non-increasing
    assert f(20) == pytest.approx(0.05 + (0.005 - 0.05) * 0.5)
```

- [ ] **Step 2: Run — expect FAIL** (`entropy_coef_at` not defined)

Run: `pytest tests/ml/test_ppo.py -k entropy_coef -v`
Expected: FAIL — `ImportError`/`AttributeError`.

- [ ] **Step 3: Add `PPOConfig` fields + `entropy_coef_at`**

`ml/ppo.py` `PPOConfig` (append fields):

```python
    entropy_coef_start: float | None = None  # high->low anneal start; None = fixed entropy_coef
    entropy_coef_end: float | None = None     # anneal end; consulted only when start is set
    entropy_anneal_iters: int = 0             # iters over which to anneal; 0 = no schedule
    normalize_returns: bool = False           # std-only reward normalization before GAE
    return_norm_eps: float = 1e-8             # numerical floor on the running std
```

Add the pure schedule fn:

```python
def entropy_coef_at(
    iteration: int, *, base: float, start: float | None, end: float | None, anneal_iters: int
) -> float:
    """Per-iteration entropy coefficient. Constant ``base`` when no schedule is configured
    (``start is None`` or ``anneal_iters <= 0``); else a linear ``start``→``end`` ramp over
    ``anneal_iters`` iterations, clamped at ``end`` past the window. Monotone non-increasing
    when start >= end (the intended high→low warmup)."""
    if start is None or anneal_iters <= 0:
        return base
    finish = end if end is not None else start
    if iteration >= anneal_iters:
        return finish
    frac = iteration / anneal_iters
    return start + (finish - start) * frac
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/ml/test_ppo.py -k entropy_coef -v`
Expected: PASS.

- [ ] **Step 5: Write the failing test — `ReturnNormalizer` (identity off/warmup; std-only; eps floor)**

```python
import torch
from ml.ppo import ReturnNormalizer


def test_return_normalizer_identity_during_warmup():
    norm = ReturnNormalizer(eps=1e-8, warmup=1000)
    r = torch.tensor([1.0, -100.0, 50.0])
    out = norm.normalize(r)
    assert torch.equal(out, r)  # still warming up -> identity


def test_return_normalizer_std_only_scales_without_mean_shift():
    norm = ReturnNormalizer(eps=1e-8, warmup=0)
    r = torch.tensor([2.0, -2.0, 4.0, -4.0])  # mean 0
    out = norm.normalize(r)
    # std-only: divided by running std, NO mean subtraction -> sign preserved, ratios preserved.
    assert torch.all(torch.sign(out) == torch.sign(r))
    assert out[2] / out[0] == pytest.approx(r[2] / r[0])


def test_return_normalizer_eps_floor_finite_on_zero_variance():
    norm = ReturnNormalizer(eps=1e-8, warmup=0)
    out = norm.normalize(torch.zeros(4))
    assert torch.isfinite(out).all()
```

- [ ] **Step 6: Run — expect FAIL** (`ReturnNormalizer` not defined)

Run: `pytest tests/ml/test_ppo.py -k return_normalizer -v`
Expected: FAIL.

- [ ] **Step 7: Implement `ReturnNormalizer` (running std, Welford, std-only, warmup-to-identity)**

`ml/ppo.py`:

```python
class ReturnNormalizer:
    """Std-only reward normalizer (cleanrl convention: NO mean-subtraction) with a running
    variance (Welford) and warmup-to-identity. Divides the reward stream by the running std
    so −w_col collision spikes and +r_terminal sit on a scale the value head can fit, letting
    GAE propagate terminal credit through the drive-in. Identity until ``warmup`` samples seen
    and identity-equivalent at zero variance (eps floor). Std-only preserves the relative
    ordering of shaped rewards."""

    def __init__(self, *, eps: float = 1e-8, warmup: int = 256) -> None:
        self.eps = eps
        self.warmup = warmup
        self._count = 0
        self._mean = 0.0
        self._m2 = 0.0

    def _update(self, rewards: Tensor) -> None:
        for r in rewards.tolist():
            self._count += 1
            delta = r - self._mean
            self._mean += delta / self._count
            self._m2 += delta * (r - self._mean)

    def normalize(self, rewards: Tensor) -> Tensor:
        self._update(rewards)
        if self._count < self.warmup or self._count < 2:
            return rewards
        var = self._m2 / self._count
        std = max(var, 0.0) ** 0.5
        return rewards / (std + self.eps)
```

- [ ] **Step 8: Run — expect PASS**

Run: `pytest tests/ml/test_ppo.py -k return_normalizer -v`
Expected: PASS.

- [ ] **Step 9: Write the failing test — `ppo_update` applies the normalizer only when on; off = identity**

```python
def test_ppo_update_normalizer_off_does_not_touch_rewards(monkeypatch):
    # When normalize_returns is False, compute_gae sees the raw rewards (normalizer ignored).
    import ml.ppo as ppo
    seen = {}
    real_gae = ppo.compute_gae
    def spy(rewards, *a, **k):
        seen["rewards"] = rewards.clone()
        return real_gae(rewards, *a, **k)
    monkeypatch.setattr(ppo, "compute_gae", spy)
    # ... build a tiny buffer with known rewards via the existing test helper in this file ...
    # (reuse whatever minimal-buffer helper test_ppo.py already uses for ppo_update tests)
```

Look at the existing `ppo_update` test in `tests/ml/test_ppo.py` and mirror its buffer-construction helper; assert `seen["rewards"]` equals the raw buffer rewards when `normalize_returns=False`, and differs when `True` with a non-degenerate `ReturnNormalizer` passed.

- [ ] **Step 10: Run — expect FAIL** (normalizer not wired into `ppo_update`)

Run: `pytest tests/ml/test_ppo.py -k normalizer_off -v`
Expected: FAIL (or the spy shows rewards unchanged because the param doesn't exist yet — make the test assert the new `normalizer=` kwarg exists).

- [ ] **Step 11: Wire the normalizer into `ppo_update`**

`ml/ppo.py` — change the signature and apply before GAE:

```python
def ppo_update(
    policy: HangarFitPolicy,
    optimizer: torch.optim.Optimizer,
    buffer: RolloutBuffer,
    config: PPOConfig,
    *,
    normalizer: ReturnNormalizer | None = None,
) -> dict[str, float]:
    data = buffer.batch()
    rewards = data["reward"]
    if config.normalize_returns and normalizer is not None:
        rewards = normalizer.normalize(rewards)
    advantages, returns = compute_gae(
        rewards, data["value"], data["done"], buffer.last_value,
        gamma=config.gamma, lam=config.lam,
    )
    ...
```

(Default `normalizer=None` ⇒ existing callers + behavior unchanged ⇒ byte-identical.)

- [ ] **Step 12: Run — expect PASS**

Run: `pytest tests/ml/test_ppo.py -q`
Expected: PASS.

- [ ] **Step 13: Write the failing test — per-rung entropy re-warm + RewardWeights threading + defaults neutral**

In `tests/ml/test_train_curriculum.py`:

```python
def test_entropy_coef_at_rewarms_per_stage():
    # A schedule keyed on the PER-STAGE iteration re-warms each rung (iter resets to 0).
    from ml.ppo import entropy_coef_at
    cfg_start, cfg_end, anneal = 0.05, 0.005, 40
    # stage 1 iter 0 and stage 2 iter 0 must BOTH be the high start (re-warm), not decayed.
    assert entropy_coef_at(0, base=0.01, start=cfg_start, end=cfg_end, anneal_iters=anneal) == pytest.approx(0.05)


def test_build_stage_env_threads_reward_weights():
    from ml.stage_builder import build_stage_env
    from ml.curriculum import CurriculumSchedule
    from ml.types import RewardWeights
    stage = CurriculumSchedule.default().stages[0]
    env = build_stage_env(stage, weights=RewardWeights(r_valid_park=2.0))
    assert env.weights.r_valid_park == 2.0
```

- [ ] **Step 14: Run — expect FAIL** (`build_stage_env` has no `weights` param)

Run: `pytest tests/ml/test_train_curriculum.py -k "rewarms or threads_reward" -v`
Expected: FAIL — `TypeError: build_stage_env() got an unexpected keyword argument 'weights'`.

- [ ] **Step 15: Thread `RewardWeights` through stage/trivial env builders + wire the entropy schedule per stage + create the normalizer**

`ml/stage_builder.py` — add `weights` param:

```python
def build_stage_env(stage: Stage, *, weights: RewardWeights | None = None) -> HangarFitEnv:
    ...
    return HangarFitEnv(
        hangar=hangar, fleet=fleet, requested_ids=tuple(pool[:n]),
        difficulty=stage.difficulty, weights=weights,
    )
```

Import `RewardWeights` from `ml.types`. Do the same for `build_trivial_env(seed, *, weights=None)` in `ml/train.py`.

`ml/train.py` `train_curriculum` — create one normalizer per run and apply the per-stage entropy schedule:

```python
    from dataclasses import replace as _replace
    from ml.ppo import ReturnNormalizer, entropy_coef_at
    normalizer = ReturnNormalizer(eps=cfg.return_norm_eps) if cfg.normalize_returns else None
    ...
    for stage_index, stage in enumerate(sched.stages):
        env = build_stage_env(stage, weights=weights)   # weights is a new train_curriculum param
        ...
        for it in range(pol.max_iters):
            it_cfg = _replace(cfg, entropy_coef=entropy_coef_at(
                it, base=cfg.entropy_coef, start=cfg.entropy_coef_start,
                end=cfg.entropy_coef_end, anneal_iters=cfg.entropy_anneal_iters))
            buf, ep_stats = collect_rollout(env, policy, enc, rollout_len, sample_request=next_request)
            ppo_update(policy, optimizer, buf, it_cfg, normalizer=normalizer)
            ...
```

Add a `weights: RewardWeights | None = None` parameter to `train_curriculum` (and `train`), defaulting to `None` → `RewardWeights()` (neutral). Apply the same per-iteration entropy schedule + normalizer in `train` (the trivial path), keyed on its own `it`.

- [ ] **Step 16: Run — expect PASS**

Run: `pytest tests/ml/test_train_curriculum.py tests/ml/test_ppo.py -q`
Expected: PASS.

- [ ] **Step 17: Add the CLI flags**

`ml/train.py` `build_argparser` — add:

```python
    p.add_argument("--r-valid-park", type=float, default=0.0)
    p.add_argument("--dense-slot-potential", action="store_true")
    p.add_argument("--entropy-start", type=float, default=None)
    p.add_argument("--entropy-end", type=float, default=None)
    p.add_argument("--entropy-anneal-iters", type=int, default=0)
    p.add_argument("--normalize-returns", action="store_true")
```

`main()` — build the configs from the flags and pass them:

```python
    weights = RewardWeights(r_valid_park=args.r_valid_park, dense_slot_potential=args.dense_slot_potential)
    ppo_cfg = PPOConfig(
        lr=args.lr, entropy_coef_start=args.entropy_start, entropy_coef_end=args.entropy_end,
        entropy_anneal_iters=args.entropy_anneal_iters, normalize_returns=args.normalize_returns,
    )
```

Pass `ppo=ppo_cfg, weights=weights` to `train(...)`/`train_curriculum(...)`. Import `RewardWeights` in `train.py`.

- [ ] **Step 18: Smoke-run the CLI at defaults (1 trivial iter) to prove wiring**

Run: `python -m ml.train --schedule trivial --iterations 1 --rollout-len 64`
Expected: runs to completion, prints one `iter 0 ...` line, no error. (Confirms flags + threading + normalizer-off path work.)

- [ ] **Step 19: Lint/type + commit**

Run: `ruff check ml/ tests/ml/ && ruff format --check ml/ tests/ml/ && mypy ml/`

```bash
git add ml/ppo.py ml/train.py ml/stage_builder.py tests/ml/test_ppo.py tests/ml/test_train_curriculum.py
git commit -m "feat(607): per-rung entropy anneal + std-only return normalization knobs (#693)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Validation run + docs

**Files:**
- Modify: `CHANGELOG.md` (`[Unreleased]`), `ml/README.md` (knob docs)
- No code; produces the PR-body validation evidence.

- [ ] **Step 1: Run the control vs treatment A/B on rungs 1–3**

Control:
```bash
python -m ml.train --schedule curriculum --max-iters-per-stage 30 --seed 0 --rollout-len 1024 2>&1 | tee /tmp/4cii-control.log
```
Treatment:
```bash
python -m ml.train --schedule curriculum --max-iters-per-stage 30 --seed 0 --rollout-len 1024 \
  --r-valid-park 2.0 --dense-slot-potential --entropy-start 0.05 --entropy-end 0.005 \
  --entropy-anneal-iters 40 --normalize-returns 2>&1 | tee /tmp/4cii-treatment.log
```
(The default ladder's rungs 4–5 may run long on CPU; if needed, cap the comparison to rungs 1–3 by editing the schedule locally or reading only the rung-1..3 lines from the logs.)

- [ ] **Step 2: Extract the signals**

Compare across control vs treatment: per-rung mean `valid_placed` (does treatment climb and hold where control stalls near 0?); `terminal_fraction` rising off ~0 (escapes place-nothing); the `fraction_placed − valid_placed` gap shrinking (escapes place-invalid); `hard_overlap` trending toward 0 (bonus not buying invalid parks); entropy starting higher and decaying. Record the numbers for the PR body. If `valid_placed` rises while `fraction_placed ≫ valid_placed` persists, the bonus gate is leaking — STOP and debug before claiming success.

- [ ] **Step 3: Write the CHANGELOG `[Unreleased]` entry**

Add under `[Unreleased]` in `CHANGELOG.md` (match the existing entry style):

```markdown
### Added
- Learned backend (#607, 4c-ii): cold-joint RL env fixed-obstacle support (pre-placed
  immovable keep-outs) — unblocks the eval benchmark's policy column on the Herrenteich
  anchors; and four default-neutral basin-escape knobs (`r_valid_park`, `dense_slot_potential`,
  per-rung entropy anneal, std-only return normalization). Training defaults are unchanged.

### Fixed
- Learned backend env validity now matches the product `collisions.check` (no longer
  over-enforces the inert maintenance bay) via a single shared `layout_valid` oracle (#694).
```

- [ ] **Step 4: Document the knobs in `ml/README.md`**

Add a short "Training knobs (4c-ii)" section listing each flag, its default (neutral), and the recommended treatment value, plus the one-line A/B validation command. Note that the real run to mastery + statistical reach-rate are deferred (still in #693).

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md ml/README.md
git commit -m "docs(607): 4c-ii CHANGELOG + ml/README knob docs; A/B validation in PR body (#693)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 6: Full suite + open the draft PR**

Run: `pytest tests/ml/ -q && ruff check ml/ tests/ml/ && ruff format --check ml/ tests/ml/ && mypy ml/`
Then push and open the PR as a DRAFT:
```bash
git push -u origin feature/693-train-to-mastery-enablement
gh pr create --draft --base develop --title "feat(607): 4c-ii train-to-mastery enablement + knobs" \
  --body "Closes #693
Closes #694
... (summary + the A/B validation numbers from Step 2) ..."
```
Then run the review arc (`/pr-review`) before flipping out of draft. Do NOT merge.

---

## Self-review (against the spec)

**Spec coverage:**
- §2 Goal 1 (fixed-obstacle) → Task 2. ✓
- §2 Goal 2 (four knobs) → `r_valid_park`/`dense_slot_potential` Task 3; entropy anneal + return norm Task 4. ✓
- §2 Goal 3 (#694 unify) → Task 1. ✓
- §2 Goal 4 (validate) → Task 5. ✓
- §3 (A)/(B) constraints → `reset()`/`_spawn()` untouched (all tasks); `active_misfit` no-search test (Task 3 Step 6); no `src/` change. ✓
- §4.1–4.7 component edits → mapped 1:1 to Tasks 1–4. ✓
- §5 default-neutral → byte-identical tests (Task 3 Steps 1/10; Task 4 normalizer-off/entropy-off). ✓
- §6 testing list → covered across Tasks 1–4 test steps. ✓
- §8 verify-first items → resolved before planning (Scenario.fixed_obstacle_placements confirmed; check honors notch via floor.covers; PPOConfig non-frozen → `replace`). ✓

**Placeholder scan:** No TBD/TODO; every code step shows the code. The two "mirror the existing helper" notes (Task 4 Step 9; GroundObject construction in Task 2 Step 1) point at concrete existing patterns to copy, not invented APIs.

**Type consistency:** `layout_valid`, `intrusion_area_m2(..., bay_closed=)`, `active_misfit_m2`, `RewardWeights.r_valid_park/.dense_slot_potential`, `RewardContext.park_valid`, `potential(active_misfit_m2=)`, `PPOConfig` fields, `entropy_coef_at`, `ReturnNormalizer.normalize`, `ppo_update(normalizer=)`, `build_stage_env(weights=)` — names are consistent across all tasks and match the spec.
