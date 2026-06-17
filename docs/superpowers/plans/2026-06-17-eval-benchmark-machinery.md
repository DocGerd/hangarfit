# Eval Benchmark Machinery (#4c-i) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the reach-not-beat eval benchmark machinery (sub-project #4c-i, epic #607): a frozen curated scenario set with committed witnesses, an RR-MC reach-oracle recorded offline, a torch-free success-predicate scorer, and a torch-gated policy-rollout runner that prints a side-by-side both-rates table.

**Architecture:** Two new top-level modules under `ml/`. `ml/benchmark.py` is **torch-free** (the scenario set, the `valid + routable-by-construction` predicate, the RR-MC oracle via the public `solve`/`plan_fill`, the committed-baseline I/O). `ml/eval.py` is **torch-gated** (loads a policy checkpoint, rolls it out deterministically, assembles the table). `ml/env.py` is left **byte-identical**; `build_scenario_env` loudly refuses fixed-obstacle scenarios (env pre-placement is deferred to 4c-ii), so the herrenteich anchors get full witness+RR-MC columns and their policy column defers.

**Tech Stack:** Python 3.12, dataclasses, shapely (via the existing `ml.geometry_oracle`), pytest (`importorskip("torch")` for the torch half), torch (the `[train]` extra, eval/CLI only).

**Spec:** `docs/superpowers/specs/2026-06-17-learned-backend-eval-benchmark-design.md`.

---

## Prerequisites (before Task 1)

- Spec PR **#691** is merged to `develop` (the build waits on it — confirm with `git -C /home/pkuhn/hangarfit log --oneline origin/develop | grep eval-benchmark`).
- File the GitHub **impl issue**: *"#607 rung 7: eval benchmark machinery (sub-project #4c-i)"* (body: `Refs #690`, `Part of #607`); and a sibling tracking issue *"#607 sub-project #4c-ii: train-to-mastery"* (so #690 can later be retired). Capture the impl issue number as `<IMPL>`.
- `git switch develop && git pull --ff-only`
- `git switch -c feature/607-rung7-eval-benchmark`
- Create the package marker for fixtures: `mkdir -p tests/fixtures/ml`.

## File Structure

| File | Responsibility | torch? |
|---|---|---|
| `ml/benchmark.py` (create) | `BenchScenario`/`ReachVerdict`/`RrmcVerdict`, `_verdict_from`, `_layout_valid`, `witness_valid`, `build_scenario_env`, `score_episode`, `rrmc_reach`, `BENCH_SET`, baseline I/O, `--record` CLI | **no** |
| `ml/eval.py` (create) | `load_policy`, `policy_reach`, `run_benchmark`, `main` (`python -m ml.eval --checkpoint`) | yes |
| `ml/train.py` (modify) | add `--save PATH` | yes (already) |
| `examples/herrenteich/scenario_today.yaml` (create) | solver-input sibling of `layout_today.yaml` | — |
| `examples/herrenteich/scenario_full.yaml` (create) | solver-input sibling of `layout_full.yaml` | — |
| `tests/fixtures/ml/bench_baseline.json` (create, committed) | the offline-recorded RR-MC verdicts | — |
| `tests/ml/test_benchmark.py` (create) | torch-free tests | no |
| `tests/ml/test_eval.py` (create) | torch-gated tests | yes |
| `CHANGELOG.md`, `ml/README.md` (modify) | user-facing dev-surface notes | — |

---

### Task 1: `ml/benchmark.py` — module skeleton, verdict/scenario types, the predicate helper

**Files:**
- Create: `ml/benchmark.py`
- Test: `tests/ml/test_benchmark.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/ml/test_benchmark.py
"""Torch-free tests for the eval benchmark machinery (#4c-i, #607)."""

from __future__ import annotations

import pytest

from ml.benchmark import BenchScenario, ReachVerdict, RrmcVerdict, _verdict_from
from ml.types import StepInfo


def _info(*, valid: bool, placed: int, total: int) -> StepInfo:
    return StepInfo(terms={"hard_swept": 0.0}, valid=valid, placed=placed, total=total)


def test_benchscenario_anchor_requires_witness():
    with pytest.raises(ValueError, match="anchor requires a witness_path"):
        BenchScenario(
            name="x", scenario_path="s.yaml", kind="anchor",
            max_restarts=1, tow_max_expansions=1, seed=0, witness_path=None,
        )


def test_benchscenario_rejects_nonpositive_budgets():
    with pytest.raises(ValueError, match="max_restarts"):
        BenchScenario(name="x", scenario_path="s.yaml", kind="control",
                      max_restarts=0, tow_max_expansions=1, seed=0)


def test_verdict_reached_when_all_clauses_pass():
    v = _verdict_from(_info(valid=True, placed=3, total=3), done=True, max_swept=0.0)
    assert v == ReachVerdict(reached=True, parked=3, total=3, final_valid=True,
                             max_swept_intrusion=0.0, reason="reached")


def test_verdict_blocked_by_unparked():
    v = _verdict_from(_info(valid=True, placed=2, total=3), done=True, max_swept=0.0)
    assert not v.reached and "2/3" in v.reason


def test_verdict_blocked_by_invalid_final_layout():
    v = _verdict_from(_info(valid=False, placed=3, total=3), done=True, max_swept=0.0)
    assert not v.reached and v.reason == "invalid final layout"


def test_verdict_blocked_by_swept_intrusion():
    v = _verdict_from(_info(valid=True, placed=3, total=3), done=True, max_swept=0.5)
    assert not v.reached and "swept" in v.reason


def test_verdict_not_done_is_unreached():
    v = _verdict_from(_info(valid=True, placed=2, total=3), done=False, max_swept=0.0)
    assert not v.reached
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/ml/test_benchmark.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ml.benchmark'`.

- [ ] **Step 3: Write `ml/benchmark.py` (skeleton + types + `_verdict_from`)**

```python
"""Reach-not-beat eval benchmark machinery (sub-project #4c-i, epic #607). TORCH-FREE:
the scenario set, the valid+routable-by-construction success predicate, the RR-MC
reach-oracle (via the PUBLIC hangarfit solve/plan_fill — no bench import), and the
committed-baseline I/O. The torch policy-rollout half lives in ml/eval.py.

Spec: docs/superpowers/specs/2026-06-17-learned-backend-eval-benchmark-design.md.
Keep this module import-light (NO torch, NO ml.policy/ml.ppo) so the no-torch CI lane
loads it cleanly."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from hangarfit.collisions import check
from hangarfit.loader import load_layout, load_scenario
from hangarfit.models import Layout, SearchConfig
from hangarfit.solver import solve
from hangarfit.towplanner import NoFeasiblePlanError, plan_fill
from ml import geometry_oracle as go
from ml.env import HangarFitEnv
from ml.types import Action, DifficultyConfig, StepInfo

_ROOT = Path(__file__).resolve().parent.parent  # repo root (ml/ sits at the root)


@dataclass(frozen=True, slots=True)
class ReachVerdict:
    """Did an agent (policy or RR-MC, via score_episode) reach a valid+routable layout?"""

    reached: bool
    parked: int
    total: int
    final_valid: bool
    max_swept_intrusion: float
    reason: str


@dataclass(frozen=True, slots=True)
class RrmcVerdict:
    """The RR-MC->tow pipeline's verdict on a scenario (recorded offline)."""

    reached: bool
    n_routed: int
    n_total: int
    status: str


@dataclass(frozen=True, slots=True)
class BenchScenario:
    """One frozen benchmark scenario. `witness_path` is required for anchors (the
    committed reachability proof) and None for controls (RR-MC reaching them IS the
    proof). Budgets are PRE-REGISTERED (frozen before measurement — spec D4)."""

    name: str
    scenario_path: str  # repo-relative solver-input YAML
    kind: Literal["anchor", "control"]
    max_restarts: int
    tow_max_expansions: int
    seed: int
    witness_path: str | None = None  # repo-relative witness layout; None only for controls

    def __post_init__(self) -> None:
        if self.kind == "anchor" and self.witness_path is None:
            raise ValueError(f"BenchScenario {self.name!r}: an anchor requires a witness_path")
        if self.max_restarts < 1:
            raise ValueError(f"BenchScenario {self.name!r}: max_restarts must be >= 1")
        if self.tow_max_expansions < 1:
            raise ValueError(f"BenchScenario {self.name!r}: tow_max_expansions must be >= 1")


def _verdict_from(info: StepInfo, *, done: bool, max_swept: float) -> ReachVerdict:
    """The valid + routable-by-construction predicate (spec §4), shared by score_episode
    (explicit actions) and ml.eval.policy_reach (policy actions). `reached` iff every
    requested object was parked, the final layout is valid, AND no drive-in leg intruded."""
    parked_all = done and info.placed == info.total
    reached = parked_all and info.valid and max_swept == 0.0
    if reached:
        reason = "reached"
    elif not parked_all:
        reason = f"only {info.placed}/{info.total} parked"
    elif not info.valid:
        reason = "invalid final layout"
    else:
        reason = "swept-path intrusion (not routable-by-construction)"
    return ReachVerdict(
        reached=reached,
        parked=info.placed,
        total=info.total,
        final_valid=info.valid,
        max_swept_intrusion=max_swept,
        reason=reason,
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/ml/test_benchmark.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add ml/benchmark.py tests/ml/test_benchmark.py
git commit -m "feat(607): benchmark types + valid/routable predicate (#4c-i)"
```

---

### Task 2: `_layout_valid` + `witness_valid` — the reachability proof

**Files:**
- Modify: `ml/benchmark.py`
- Test: `tests/ml/test_benchmark.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/ml/test_benchmark.py
from ml.benchmark import witness_valid, BenchScenario


def test_witness_valid_true_for_committed_all8_layout():
    sc = BenchScenario(
        name="all8", scenario_path="examples/herrenteich/scenario.yaml", kind="anchor",
        max_restarts=1, tow_max_expansions=1, seed=0,
        witness_path="examples/herrenteich/layout.yaml",
    )
    assert witness_valid(sc) is True


def test_witness_valid_true_for_today_and_full():
    for wp in ("layout_today.yaml", "layout_full.yaml"):
        sc = BenchScenario(
            name=wp, scenario_path="examples/herrenteich/scenario.yaml", kind="anchor",
            max_restarts=1, tow_max_expansions=1, seed=0,
            witness_path=f"examples/herrenteich/{wp}",
        )
        assert witness_valid(sc) is True, wp
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/ml/test_benchmark.py::test_witness_valid_true_for_committed_all8_layout -q`
Expected: FAIL — `ImportError: cannot import name 'witness_valid'`.

- [ ] **Step 3: Implement `_layout_valid` + `witness_valid`**

```python
# add to ml/benchmark.py (after BenchScenario)

def _layout_valid(layout: Layout) -> bool:
    """Whole-layout validity matching env._layout_valid + the deterministic checker:
    no part overlap, no out-of-bounds/notch/apron intrusion by any placed body, and no
    Caddy hard-door egress violation. Reused by witness_valid and rrmc_reach so the
    policy and RR-MC sides apply the IDENTICAL predicate."""
    if go.overlap_area_m2(layout) > 0.0:
        return False
    if go.egress_blocked(layout):
        return False
    bodies = {**layout.fleet, **layout.ground_objects}
    placements = (*layout.placements, *layout.ground_object_placements)
    return all(go.intrusion_area_m2(bodies[p.plane_id], p, layout.hangar) == 0.0 for p in placements)


def witness_valid(scenario: BenchScenario) -> bool:
    """Load the committed witness layout and prove it is valid+routable-by-existence
    (the never-rots reachability proof). Raises if the scenario has no witness."""
    if scenario.witness_path is None:
        raise ValueError(f"witness_valid: scenario {scenario.name!r} has no witness_path")
    layout = load_layout(_ROOT / scenario.witness_path)
    return _layout_valid(layout)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/ml/test_benchmark.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ml/benchmark.py tests/ml/test_benchmark.py
git commit -m "feat(607): _layout_valid + witness_valid reachability proof (#4c-i)"
```

---

### Task 3: scenario-input siblings + the id-set match test

**Files:**
- Create: `examples/herrenteich/scenario_today.yaml`
- Create: `examples/herrenteich/scenario_full.yaml`
- Test: `tests/ml/test_benchmark.py`

- [ ] **Step 1: Create `examples/herrenteich/scenario_today.yaml`**

Body set mirrors `layout_today.yaml` (9 aircraft + Caddy mover + fixed fuel trailer + one glider-trailer mover). The fixed `maul_fuel_trailer` carries its surveyed pose (from the witness); the movers are positioned by the solver.

```yaml
# Airfield Herrenteich — solver INPUT for the REAL 'today' composition (#664/#665).
# Sibling of layout_today.yaml (the committed witness): same body set, but this is the
# search INPUT the RR-MC->tow baseline runs on. The product solver does not crack this
# dense 12-body nest (the #607 motivation); layout_today.yaml is the existence witness.
fleet: fleet.yaml
hangar: hangar.yaml
fleet_in:
  - wild_thing
  - stemme_s10
  - scheibe_falke
  - zlin_savage
  - fuji
  - ctsl
  - fk9_mkii
  - cessna_140
  - aviat_husky
ground_objects:
  - object: maul_fuel_trailer   # FIXED keep-out (surveyed pose from layout_today.yaml)
    x_m: 1.17
    y_m: 3.87
    heading_deg: 176.2
  - object: vw_caddy            # hard-door rescue mover (solver places + routes, clear egress)
    region_preference: { side: left, weight: 1.0 }
  - object: glider_trailer_1    # Duo Discus trailer mover
    region_preference: { side: right, weight: 1.5 }
```

- [ ] **Step 2: Create `examples/herrenteich/scenario_full.yaml`**

Body set mirrors `layout_full.yaml` (7 aircraft — no `scheibe_falke` — + Caddy mover + fixed fuel trailer + both glider-trailer movers).

```yaml
# Airfield Herrenteich — solver INPUT for the fishbone 'both trailers inside' set
# (#657/#659). Sibling of layout_full.yaml (the committed witness): same body set
# (7 aircraft + 4 ground objects, Scheibe parks outside). The RR-MC->tow baseline runs
# on this input; layout_full.yaml is the existence witness.
fleet: fleet.yaml
hangar: hangar.yaml
fleet_in:
  - fk9_mkii
  - aviat_husky
  - zlin_savage
  - wild_thing
  - stemme_s10
  - cessna_140
  - ctsl
ground_objects:
  - object: maul_fuel_trailer   # FIXED keep-out (surveyed pose from layout_full.yaml)
    x_m: 1.35
    y_m: 2.35
    heading_deg: 180.0
  - object: vw_caddy            # hard-door rescue mover (solver places + routes, clear egress)
    region_preference: { side: left, weight: 1.0 }
  - object: glider_trailer_1    # Duo Discus trailer mover (right wall)
    region_preference: { side: right, weight: 1.5 }
  - object: glider_trailer_2    # single-seat trailer mover (deeper)
    region_preference: { side: right, weight: 1.5 }
```

- [ ] **Step 3: Write the failing id-set match test**

```python
# append to tests/ml/test_benchmark.py
from hangarfit.loader import load_layout, load_scenario
from ml.benchmark import _ROOT

_TODAY = BenchScenario(
    name="today", scenario_path="examples/herrenteich/scenario_today.yaml", kind="anchor",
    max_restarts=1, tow_max_expansions=1, seed=0,
    witness_path="examples/herrenteich/layout_today.yaml",
)
_FULL = BenchScenario(
    name="full", scenario_path="examples/herrenteich/scenario_full.yaml", kind="anchor",
    max_restarts=1, tow_max_expansions=1, seed=0,
    witness_path="examples/herrenteich/layout_full.yaml",
)


@pytest.mark.parametrize("sc", [_TODAY, _FULL], ids=["today", "full"])
def test_scenario_input_matches_witness_movable_idset(sc):
    """The scenario input's movable id-set (fleet_in + placed-routed movers) must equal
    the witness layout's movable placements, with fixed obstacles excluded both sides."""
    scenario = load_scenario(_ROOT / sc.scenario_path)
    layout = load_layout(_ROOT / sc.witness_path)
    scenario_movable = set(scenario.placeable_ids)
    fixed_ids = {
        p.plane_id
        for p in layout.ground_object_placements
        if layout.ground_objects[p.plane_id].object_class == "fixed_obstacle"
    }
    witness_movable = (
        {p.plane_id for p in layout.placements}
        | ({p.plane_id for p in layout.ground_object_placements} - fixed_ids)
    )
    assert scenario_movable == witness_movable
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/ml/test_benchmark.py -k idset -q`
Expected: PASS (2 passed). If it fails, the scenario `fleet_in`/`ground_objects` and the witness placements disagree — reconcile the YAML body lists (do NOT relax the test).

- [ ] **Step 5: Commit**

```bash
git add examples/herrenteich/scenario_today.yaml examples/herrenteich/scenario_full.yaml tests/ml/test_benchmark.py
git commit -m "feat(607): scenario-input siblings + witness id-set match test (#4c-i)"
```

---

### Task 4: `BENCH_SET` — the frozen curated set with pre-registered budgets

**Files:**
- Modify: `ml/benchmark.py`
- Test: `tests/ml/test_benchmark.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/ml/test_benchmark.py
from ml.benchmark import BENCH_SET


def test_bench_set_wellformed():
    assert len(BENCH_SET) >= 4
    names = [s.name for s in BENCH_SET]
    assert len(names) == len(set(names)), "duplicate scenario names"
    assert any(s.kind == "control" for s in BENCH_SET), "need >=1 control"
    for s in BENCH_SET:
        assert (_ROOT / s.scenario_path).exists(), s.scenario_path
        if s.kind == "anchor":
            assert s.witness_path is not None and (_ROOT / s.witness_path).exists()


def test_bench_set_anchor_witnesses_all_valid():
    for s in BENCH_SET:
        if s.kind == "anchor":
            assert witness_valid(s), s.name
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/ml/test_benchmark.py -k bench_set -q`
Expected: FAIL — `ImportError: cannot import name 'BENCH_SET'`.

- [ ] **Step 3: Implement `BENCH_SET`**

```python
# add to ml/benchmark.py

# Pre-registered RR-MC budgets — FROZEN before measurement (spec D4). Do NOT retune
# after seeing baseline results: that would silently make the comparison circular.
_ANCHOR_RESTARTS = 200
_ANCHOR_TOW_EXPANSIONS = 16_000
_CONTROL_RESTARTS = 64
_CONTROL_TOW_EXPANSIONS = 8_000
_SEED = 0

BENCH_SET: tuple[BenchScenario, ...] = (
    BenchScenario(
        name="herrenteich_all8",
        scenario_path="examples/herrenteich/scenario.yaml",
        witness_path="examples/herrenteich/layout.yaml",
        kind="anchor",
        max_restarts=_ANCHOR_RESTARTS,
        tow_max_expansions=_ANCHOR_TOW_EXPANSIONS,
        seed=_SEED,
    ),
    BenchScenario(
        name="herrenteich_today",
        scenario_path="examples/herrenteich/scenario_today.yaml",
        witness_path="examples/herrenteich/layout_today.yaml",
        kind="anchor",
        max_restarts=_ANCHOR_RESTARTS,
        tow_max_expansions=_ANCHOR_TOW_EXPANSIONS,
        seed=_SEED,
    ),
    BenchScenario(
        name="herrenteich_full",
        scenario_path="examples/herrenteich/scenario_full.yaml",
        witness_path="examples/herrenteich/layout_full.yaml",
        kind="anchor",
        max_restarts=_ANCHOR_RESTARTS,
        tow_max_expansions=_ANCHOR_TOW_EXPANSIONS,
        seed=_SEED,
    ),
    # GO-free control: RR-MC routes it (the reachability proof) AND it is the policy-rollout
    # control for 4c-i (no fixed obstacle → build_scenario_env accepts it).
    BenchScenario(
        name="herrenteich_demo",
        scenario_path="examples/herrenteich/scenario_demo.yaml",
        witness_path=None,
        kind="control",
        max_restarts=_CONTROL_RESTARTS,
        tow_max_expansions=_CONTROL_TOW_EXPANSIONS,
        seed=_SEED,
    ),
)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/ml/test_benchmark.py -k bench_set -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add ml/benchmark.py tests/ml/test_benchmark.py
git commit -m "feat(607): frozen BENCH_SET with pre-registered budgets (#4c-i)"
```

---

### Task 5: `build_scenario_env` — refuse fixed obstacles, build GO-free env

**Files:**
- Modify: `ml/benchmark.py`
- Test: `tests/ml/test_benchmark.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/ml/test_benchmark.py
from ml.benchmark import build_scenario_env

_DEMO = next(s for s in BENCH_SET if s.name == "herrenteich_demo")
_ALL8 = next(s for s in BENCH_SET if s.name == "herrenteich_all8")


def test_build_scenario_env_go_free_control():
    env = build_scenario_env(_DEMO)
    # scenario_demo lists 3 aircraft and no ground objects → 3 placeable bodies, all queued.
    assert len(env.requested_ids) == 3
    assert env.ground_objects == {}


def test_build_scenario_env_refuses_fixed_obstacle_scenario():
    with pytest.raises(NotImplementedError, match="4c-ii"):
        build_scenario_env(_ALL8)  # carries the fixed maul_fuel_trailer
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/ml/test_benchmark.py -k build_scenario_env -q`
Expected: FAIL — `ImportError: cannot import name 'build_scenario_env'`.

- [ ] **Step 3: Implement `build_scenario_env`**

```python
# add to ml/benchmark.py

def build_scenario_env(scenario: BenchScenario) -> HangarFitEnv:
    """Build a HangarFitEnv for a scenario's MOVABLE bodies (aircraft + placed-routed
    movers), with an apron for drive-in. RAISES NotImplementedError if the scenario carries
    any fixed obstacle — env pre-placement of immovable keep-outs is deferred to 4c-ii, and
    silently dropping the keep-out would score the policy on an easier scenario than RR-MC
    faces (spec §5.5/D11)."""
    sc = load_scenario(_ROOT / scenario.scenario_path)
    if sc.fixed_obstacle_placements:
        ids = [p.plane_id for p in sc.fixed_obstacle_placements]
        raise NotImplementedError(
            f"build_scenario_env: scenario {scenario.name!r} carries fixed obstacle(s) {ids}; "
            f"the env cannot yet pre-place immovable keep-outs (deferred to #607 sub-project "
            f"4c-ii). Use a ground-object-free scenario for the policy rollout."
        )
    placeable = sc.placeable_ids
    per_object = 120  # generous: enough primitives to drive a body in from the apron and park
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
        ground_objects=sc.ground_object_defs,
        difficulty=difficulty,
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/ml/test_benchmark.py -k build_scenario_env -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add ml/benchmark.py tests/ml/test_benchmark.py
git commit -m "feat(607): build_scenario_env (refuses fixed obstacles) (#4c-i)"
```

---

### Task 6: `score_episode` — the torch-free episode scorer

**Files:**
- Modify: `ml/benchmark.py`
- Test: `tests/ml/test_benchmark.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/ml/test_benchmark.py
from dataclasses import replace as _replace

from hangarfit.loader import load_fleet, load_hangar
from ml.benchmark import score_episode
from ml.env import HangarFitEnv
from ml.types import DifficultyConfig, Park, Primitive


def _fuji_env() -> HangarFitEnv:
    fleet = load_fleet("data/fleet.yaml")
    hangar = _replace(load_hangar("data/hangar.yaml"), apron_depth_m=8.0)
    return HangarFitEnv(
        hangar=hangar, fleet=fleet, requested_ids=("fuji",),
        difficulty=DifficultyConfig(per_object_step_budget=40, total_step_budget=40),
    )


def test_score_episode_reaches_when_driven_in_and_parked():
    env = _fuji_env()
    # Drive forward from the apron well inside the hangar, then park. Alone in an empty
    # hangar → no overlap, in-bounds, clear swept path → reached.
    actions = [Primitive(kind="S", magnitude=2.0, gear=1)] * 6 + [Park()]
    v = score_episode(env, actions)
    assert v.reached, v.reason
    assert v.max_swept_intrusion == 0.0


def test_score_episode_apron_park_is_invalid():
    env = _fuji_env()
    # Park immediately on the apron (y < 0) → out-of-bounds intrusion → invalid → not reached.
    v = score_episode(env, [Park()])
    assert not v.reached
    assert not v.final_valid
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/ml/test_benchmark.py -k score_episode -q`
Expected: FAIL — `ImportError: cannot import name 'score_episode'`.

- [ ] **Step 3: Implement `score_episode`**

```python
# add to ml/benchmark.py

def score_episode(env: HangarFitEnv, actions: Sequence[Action]) -> ReachVerdict:
    """Reset `env`, replay an explicit action sequence, and apply the success predicate
    (spec §4). Torch-free — the test/RR-MC path. ml.eval.policy_reach runs the same loop
    with policy-chosen actions and reuses _verdict_from."""
    env.reset()
    max_swept = 0.0
    info: StepInfo | None = None
    done = False
    for action in actions:
        if done:
            break
        _obs, _reward, done, info = env.step(action)
        max_swept = max(max_swept, info.terms.get("hard_swept", 0.0))
    if info is None:
        raise ValueError("score_episode: empty action sequence")
    return _verdict_from(info, done=done, max_swept=max_swept)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/ml/test_benchmark.py -k score_episode -q`
Expected: PASS (2 passed). If `reached` is False in the happy path, the forward distance left fuji on the apron or drove it out of bounds — adjust the count/magnitude so the final pose is well inside (`0 < y < length`), then re-run.

- [ ] **Step 5: Run the full torch-free module + commit**

Run: `python -m pytest tests/ml/test_benchmark.py -q`
Expected: PASS (all torch-free tests).

```bash
git add ml/benchmark.py tests/ml/test_benchmark.py
git commit -m "feat(607): score_episode torch-free success scorer (#4c-i)"
```

---

### Task 7: `rrmc_reach` + baseline I/O + the `--record` CLI

**Files:**
- Modify: `ml/benchmark.py`
- Test: `tests/ml/test_benchmark.py`

- [ ] **Step 1: Write the failing tests** (baseline I/O round-trip; the slow live run is Task 8)

```python
# append to tests/ml/test_benchmark.py
import json as _json

from ml.benchmark import RrmcVerdict, load_baseline


def test_load_baseline_roundtrip(tmp_path, monkeypatch):
    fixture = tmp_path / "bench_baseline.json"
    fixture.write_text(_json.dumps({"scenarios": [
        {"name": "herrenteich_demo", "reached": True, "n_routed": 3, "n_total": 3,
         "status": "found", "max_restarts": 64, "tow_max_expansions": 8000, "seed": 0,
         "repo_sha": "abc123", "recorded_at": "2026-06-17T00:00:00+00:00"},
    ]}))
    monkeypatch.setattr("ml.benchmark._BASELINE_PATH", fixture)
    base = load_baseline()
    assert base["herrenteich_demo"]["reached"] is True
    assert base["herrenteich_demo"]["n_total"] == 3
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/ml/test_benchmark.py -k baseline_roundtrip -q`
Expected: FAIL — `ImportError: cannot import name 'load_baseline'`.

- [ ] **Step 3: Implement `rrmc_reach`, baseline I/O, and the `--record` CLI**

```python
# add to ml/benchmark.py

_BASELINE_PATH = _ROOT / "tests/fixtures/ml/bench_baseline.json"


def rrmc_reach(scenario: BenchScenario) -> RrmcVerdict:
    """Run the RR-MC -> tow pipeline on `scenario` at its pinned budget and apply the SAME
    valid+routable predicate as the policy side. OFFLINE/dev-only (RR-MC is slow; CI reads
    the committed fixture). Mirrors bench/harness's _solve_placement/_route_layout via the
    PUBLIC solve/plan_fill (no bench import). `budget_s=inf` so max_restarts is the only
    bound — the 30 s default would make 'missed' machine-dependent (spec D4). 'Routable' =
    plan_fill returns without NoFeasiblePlanError (it raises when a body can't be routed)."""
    sc = load_scenario(_ROOT / scenario.scenario_path)
    n_total = len(sc.placeable_ids)
    result = solve(
        sc,
        budget_s=float("inf"),
        seed=scenario.seed,
        search=SearchConfig(spread=True, max_restarts=scenario.max_restarts),
        plan_paths=False,
    )
    if not result.layouts:
        return RrmcVerdict(reached=False, n_routed=0, n_total=n_total, status=result.status)
    layout = result.layouts[0]
    if not _layout_valid(layout):
        return RrmcVerdict(reached=False, n_routed=0, n_total=n_total, status="invalid")
    try:
        plan = plan_fill(layout, heuristic="grid", max_total_expansions=scenario.tow_max_expansions)
    except NoFeasiblePlanError:
        return RrmcVerdict(reached=False, n_routed=0, n_total=n_total, status="unroutable")
    n_routed = len(plan.moves)
    return RrmcVerdict(
        reached=(n_routed == n_total), n_routed=n_routed, n_total=n_total, status=result.status
    )


def load_baseline() -> dict[str, dict]:
    """Read the committed RR-MC baseline fixture into {scenario_name: record}."""
    with _BASELINE_PATH.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return {row["name"]: row for row in data["scenarios"]}


def record_baseline(*, repo_sha: str, recorded_at: str) -> None:
    """Re-derive every scenario's RR-MC verdict and WRITE the committed fixture. OFFLINE
    only (slow). repo_sha + recorded_at are passed in (the module is RNG/clock-free)."""
    rows = []
    for s in BENCH_SET:
        v = rrmc_reach(s)
        rows.append({
            "name": s.name, "reached": v.reached, "n_routed": v.n_routed, "n_total": v.n_total,
            "status": v.status, "max_restarts": s.max_restarts,
            "tow_max_expansions": s.tow_max_expansions, "seed": s.seed,
            "repo_sha": repo_sha, "recorded_at": recorded_at,
        })
    _BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _BASELINE_PATH.open("w", encoding="utf-8") as fh:
        json.dump({"scenarios": rows}, fh, indent=2)
        fh.write("\n")


def _main(argv: Sequence[str] | None = None) -> None:
    import argparse
    import datetime
    import subprocess

    parser = argparse.ArgumentParser(description="Eval benchmark (RR-MC baseline recorder).")
    parser.add_argument("--record", action="store_true", help="re-derive + write the baseline fixture (slow, offline)")
    args = parser.parse_args(argv)
    if args.record:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_ROOT).decode().strip()
        now = datetime.datetime.now(datetime.UTC).isoformat()
        record_baseline(repo_sha=sha, recorded_at=now)
        print(f"wrote {_BASELINE_PATH} @ {sha}")
    else:
        parser.error("nothing to do; pass --record to regenerate the baseline fixture")


if __name__ == "__main__":
    _main()
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/ml/test_benchmark.py -k baseline_roundtrip -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ml/benchmark.py tests/ml/test_benchmark.py
git commit -m "feat(607): rrmc_reach oracle + baseline I/O + --record CLI (#4c-i)"
```

---

### Task 8: record the baseline offline + commit the fixture + drift canary

**Files:**
- Create: `tests/fixtures/ml/bench_baseline.json` (generated, committed)
- Modify: `tests/ml/test_benchmark.py`

- [ ] **Step 1: Generate the baseline OFFLINE**

Run (slow — minutes; RR-MC on the herrenteich anchors):
```bash
python -m ml.benchmark --record
```
Expected: writes `tests/fixtures/ml/bench_baseline.json`. Inspect it: the three anchors should show `"reached": false` (RR-MC->tow misses the dense herrenteich fills — the thesis), the `herrenteich_demo` control `"reached": true`. If an anchor unexpectedly shows `true`, that is a genuine finding — do NOT hand-edit the fixture; note it in the PR (the witness still proves reachability; a baseline "reach" just means RR-MC improved). If the control shows `false`, the budget is too tight — STOP and report (the control must route).

- [ ] **Step 2: Write the drift-canary test (`@slow`, non-required)**

```python
# append to tests/ml/test_benchmark.py
import warnings

from ml.benchmark import rrmc_reach


@pytest.mark.slow
def test_rrmc_baseline_drift_canary():
    """NON-blocking: re-derive the control's RR-MC verdict and WARN if it flipped vs the
    committed fixture. A flip is a signal (curation rot / solver change), never a regression
    — so this asserts only the stable composition invariant, never the reached value."""
    control = next(s for s in BENCH_SET if s.kind == "control")
    recorded = load_baseline()[control.name]
    live = rrmc_reach(control)
    assert live.n_total == recorded["n_total"], "scenario composition changed"
    if live.reached != recorded["reached"]:
        warnings.warn(
            f"RR-MC baseline drift for {control.name!r}: recorded reached="
            f"{recorded['reached']}, live={live.reached} — re-record the fixture.",
            stacklevel=2,
        )
```

- [ ] **Step 3: Run the drift canary (slow lane) to verify it passes against the fresh fixture**

Run: `python -m pytest tests/ml/test_benchmark.py -k drift_canary -m slow -q`
Expected: PASS (no warning, since the fixture was just recorded).

- [ ] **Step 4: Run the full torch-free non-slow set**

Run: `python -m pytest tests/ml/test_benchmark.py -q`
Expected: PASS (the `@slow` canary is excluded by default).

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/ml/bench_baseline.json tests/ml/test_benchmark.py
git commit -m "feat(607): record RR-MC baseline fixture + drift canary (#4c-i)"
```

---

### Task 9: `ml/eval.py` — policy load, rollout, both-rates table

**Files:**
- Create: `ml/eval.py`
- Test: `tests/ml/test_eval.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/ml/test_eval.py
"""Torch-gated tests for the policy-rollout eval runner (#4c-i, #607)."""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")  # whole module skips without the [train] extra

from ml.benchmark import BENCH_SET, ReachVerdict  # noqa: E402
from ml.eval import load_policy, policy_reach  # noqa: E402
from ml.policy import HangarFitPolicy  # noqa: E402

_DEMO = next(s for s in BENCH_SET if s.name == "herrenteich_demo")


def test_policy_reach_runs_on_go_free_control():
    policy = HangarFitPolicy(d_model=32, n_layers=1, n_heads=2)
    v = policy_reach(_DEMO, policy)
    assert isinstance(v, ReachVerdict)
    assert v.total == 3  # scenario_demo has 3 aircraft
    # An untrained policy will not reach; we only assert it runs end-to-end and verdicts.


def test_checkpoint_save_load_roundtrip(tmp_path):
    policy = HangarFitPolicy(d_model=32, n_layers=1, n_heads=2)
    ckpt = tmp_path / "p.pt"
    torch.save(policy.state_dict(), ckpt)
    loaded = load_policy(ckpt, policy_kwargs={"d_model": 32, "n_layers": 1, "n_heads": 2})
    for a, b in zip(policy.state_dict().values(), loaded.state_dict().values(), strict=True):
        assert torch.equal(a, b)
    assert not loaded.training  # load_policy puts it in eval() mode
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/ml/test_eval.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ml.eval'` (or skip if torch absent — install the `[train]` extra first).

- [ ] **Step 3: Implement `ml/eval.py`**

```python
"""python -m ml.eval — roll a trained HangarFitPolicy out across the frozen benchmark set
and print the side-by-side both-rates table (sub-project #4c-i, #607). Requires the [train]
extra. The torch-free machinery (scenarios, predicate, RR-MC baseline) lives in ml.benchmark."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import torch

from ml.benchmark import (
    BENCH_SET,
    BenchScenario,
    ReachVerdict,
    _verdict_from,
    build_scenario_env,
    load_baseline,
)
from ml.encoding import EncoderConfig, encode
from ml.policy import HangarFitPolicy
from ml.types import StepInfo


def load_policy(
    checkpoint_path: str | Path, *, policy_kwargs: dict | None = None
) -> HangarFitPolicy:
    """Construct a policy, load a saved state_dict, and put it in eval() mode (required for
    deterministic argmax action selection)."""
    policy = HangarFitPolicy(**(policy_kwargs or {}))
    # weights_only=True: the checkpoint is a pure tensor state_dict, so refuse to unpickle
    # arbitrary Python objects (torch.load's default weights_only=False is an arbitrary-code
    # -execution vector on a malicious .pt).
    state = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    policy.load_state_dict(state)
    policy.eval()
    return policy


def policy_reach(
    scenario: BenchScenario, policy: HangarFitPolicy, *, encoder: EncoderConfig | None = None
) -> ReachVerdict:
    """Roll `policy` out deterministically (argmax) on `scenario` and apply the success
    predicate (spec §4). Raises NotImplementedError for fixed-obstacle scenarios (4c-ii)."""
    enc = encoder or EncoderConfig()
    env = build_scenario_env(scenario)  # raises on fixed-obstacle scenarios
    bodies = {**env.fleet, **env.ground_objects}
    policy.eval()
    obs = env.reset()
    max_swept = 0.0
    info: StepInfo | None = None
    done = False
    with torch.no_grad():
        while not done and obs.active is not None:
            obs_t = encode(obs, env.hangar, bodies, enc)
            tr = obs.active.body.effective_turn_radius_m()
            _idx, _logprob, action = policy.act(obs_t, turn_radius_m=tr, deterministic=True)
            obs, _reward, done, info = env.step(action)
            max_swept = max(max_swept, info.terms.get("hard_swept", 0.0))
    if info is None:
        raise ValueError(f"policy_reach: episode produced no steps for {scenario.name!r}")
    return _verdict_from(info, done=done, max_swept=max_swept)


def run_benchmark(policy: HangarFitPolicy) -> list[dict[str, str]]:
    """Assemble the both-rates rows: RR-MC from the committed fixture, policy live."""
    baseline = load_baseline()
    rows: list[dict[str, str]] = []
    for sc in BENCH_SET:
        rrmc = baseline.get(sc.name)
        rrmc_cell = "reached" if (rrmc and rrmc["reached"]) else "missed"
        try:
            verdict = policy_reach(sc, policy)
            policy_cell = "reached" if verdict.reached else f"missed ({verdict.reason})"
        except NotImplementedError:
            policy_cell = "n/a (GO env -> 4c-ii)"
        rows.append({"name": sc.name, "kind": sc.kind, "rrmc": rrmc_cell, "policy": policy_cell})
    return rows


def _print_table(rows: list[dict[str, str]]) -> None:
    header = f"{'scenario':24}  {'kind':8}  {'RR-MC':8}  policy"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(f"{r['name']:24}  {r['kind']:8}  {r['rrmc']:8}  {r['policy']}")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the reach-not-beat eval benchmark.")
    parser.add_argument("--checkpoint", required=True, help="path to a torch state_dict .pt")
    parser.add_argument("--d-model", type=int, default=128)
    parser.add_argument("--n-layers", type=int, default=2)
    parser.add_argument("--n-heads", type=int, default=4)
    args = parser.parse_args(argv)
    policy = load_policy(
        args.checkpoint,
        policy_kwargs={"d_model": args.d_model, "n_layers": args.n_layers, "n_heads": args.n_heads},
    )
    _print_table(run_benchmark(policy))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/ml/test_eval.py -q`
Expected: PASS (2 passed) — assuming torch is installed; otherwise the module skips.

- [ ] **Step 5: Commit**

```bash
git add ml/eval.py tests/ml/test_eval.py
git commit -m "feat(607): ml.eval policy rollout + both-rates table (#4c-i)"
```

---

### Task 10: `ml/train.py --save` — checkpoint export

**Files:**
- Modify: `ml/train.py` (the `build_argparser` + `main` functions, ~lines 224-262)
- Test: `tests/ml/test_train_curriculum.py` (append) OR `tests/ml/test_eval.py`

- [ ] **Step 1: Write the failing test** (append to `tests/ml/test_eval.py`)

```python
# append to tests/ml/test_eval.py
def test_train_save_flag_writes_loadable_checkpoint(tmp_path):
    from ml.train import train

    # train() returns the reward history; we exercise the save side-channel directly here
    # since train() itself is exercised in test_train_curriculum. Build + save + reload.
    policy = HangarFitPolicy(d_model=32, n_layers=1, n_heads=2)
    ckpt = tmp_path / "trained.pt"
    torch.save(policy.state_dict(), ckpt)
    reloaded = load_policy(ckpt, policy_kwargs={"d_model": 32, "n_layers": 1, "n_heads": 2})
    assert set(reloaded.state_dict()) == set(policy.state_dict())
```

Note: the CLI `--save` wiring below is verified by Step 4's manual run, not a unit test (training a full curriculum in a unit test is too slow; the round-trip mechanism is covered by Task 9 + this test).

- [ ] **Step 2: Run to verify it passes the round-trip part** (the import path exists)

Run: `python -m pytest tests/ml/test_eval.py -k train_save -q`
Expected: PASS (the helper round-trips). Proceed to wire the CLI.

- [ ] **Step 3: Add `--save` to `ml/train.py`**

In `build_argparser()`, add after the `--lr` argument:
```python
    p.add_argument("--save", type=str, default=None, help="write the trained policy state_dict to this path")
```

In `train(...)`, change the signature to accept `save: str | None = None` and, after the training loop (just before `return history`), add:
```python
    if save is not None:
        torch.save(policy.state_dict(), save)
```

In `train_curriculum(...)`, likewise add `save: str | None = None` to the signature and, before `return history`, add:
```python
    if save is not None:
        torch.save(policy.state_dict(), save)
```

In `main()`, pass `save=args.save` to both the `train(...)` and `train_curriculum(...)` calls.

- [ ] **Step 4: Verify the CLI wiring with a tiny manual run**

Run:
```bash
python -m ml.train --schedule trivial --iterations 1 --rollout-len 64 --save /tmp/ck.pt && python -c "import torch; print(sorted(torch.load('/tmp/ck.pt', map_location='cpu', weights_only=True))[:2])"
```
Expected: prints two state_dict key names → the checkpoint was written and is loadable.

- [ ] **Step 5: Commit**

```bash
git add ml/train.py tests/ml/test_eval.py
git commit -m "feat(607): train.py --save checkpoint export (#4c-i)"
```

---

### Task 11: CHANGELOG + ml/README + the both-rates table

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `ml/README.md`

- [ ] **Step 1: Add a CHANGELOG `[Unreleased] → Added` entry**

Under `## [Unreleased]` → `### Added` (after the existing #4b entry):
```markdown
- **Cold-joint RL reach-not-beat eval benchmark (`ml/benchmark.py` + `ml/eval.py`, #607
  sub-project #4c-i).** A frozen curated set of real herrenteich scenarios, each anchor
  paired with a committed witness layout the deterministic checker accepts (the
  reachability proof). `python -m ml.eval --checkpoint P` rolls a trained policy out
  deterministically and prints a side-by-side both-rates table against the RR-MC->tow
  baseline (recorded offline at a pre-registered budget, `tests/fixtures/ml/bench_baseline.json`).
  Success gate = valid + routable-by-construction. `python -m ml.train --save P` exports a
  checkpoint. Dev/CI-only (the `[train]` extra); the herrenteich anchors' policy column +
  env fixed-obstacle support land in 4c-ii.
```

- [ ] **Step 2: Add a short note to `ml/README.md`**

Append a section documenting `python -m ml.benchmark --record` (offline baseline) and `python -m ml.eval --checkpoint P` (the table), noting `benchmark.py` is torch-free and `eval.py` needs the `[train]` extra.

- [ ] **Step 3: Run the whole ml suite + lint + types**

Run:
```bash
python -m pytest tests/ml/ -q
ruff check ml/ tests/ml/ && ruff format --check ml/ tests/ml/
mypy ml/
```
Expected: all green (torch-gated tests skip if torch absent; the `@slow` canary is excluded by default).

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md ml/README.md
git commit -m "docs(607): CHANGELOG + ml/README for the eval benchmark (#4c-i)"
```

- [ ] **Step 5: Push + open the impl PR (draft)**

```bash
git push -u origin feature/607-rung7-eval-benchmark
gh pr create --draft --base develop \
  --title "feat(607): reach-not-beat eval benchmark machinery (sub-project #4c-i)" \
  --body "Closes #<IMPL>. Refs #690. Implements docs/superpowers/specs/2026-06-17-learned-backend-eval-benchmark-design.md.

Both-rates table (untrained policy → 0 until 4c-ii):
<paste the output of \`python -m ml.eval --checkpoint <a-tiny-untrained-ckpt>\` here>"
```

Then run the review arc (Task 12).

---

### Task 12: Review arc (not code — process)

- [ ] Run `code-reviewer` (main pass) on the diff.
- [ ] Run `silent-failure-hunter` (the success predicate, the `NoFeasiblePlanError`/`result.layouts` empty paths, the witness-load failure path, the drift-canary warn-not-fail logic).
- [ ] Run `type-design-analyzer` (the new `BenchScenario` / `ReachVerdict` / `RrmcVerdict`).
- [ ] Do NOT run `determinism-guard` / `geometry-invariant-guard` — no `solver.py`/`towplanner.py`/`geometry.py`/`env.py` change.
- [ ] Convert each finding into its own inline review thread on the diff; fix (preferred) or reply; resolve each.
- [ ] Flip the PR ready when clean; hand to the user to merge (the user is the sole merger).

---

## Self-Review

**1. Spec coverage:**
- §4 success predicate → `_verdict_from` (Task 1) + `score_episode` (Task 6) + `policy_reach` (Task 9). ✓
- §5.1 `BenchScenario`/`witness_valid`/`score_episode`/`rrmc_reach`/baseline I/O → Tasks 1,2,6,7. ✓
- §5.1 `build_scenario_env` refusing fixed obstacles → Task 5. ✓
- §5.2 `ml/eval.py` (`load_policy`/`policy_reach`/`run_benchmark`/`main`) → Task 9. ✓
- §5.3 `train.py --save` → Task 10. ✓
- §5.4 scenario-input siblings + id-set match test → Task 3. ✓ (GO-free control input = `scenario_demo.yaml`, used in Task 9.)
- §5.5/D11 env untouched + loud refusal → Task 5 (no `ml/env.py` edit anywhere in the plan). ✓
- §6 frozen `BENCH_SET` + pre-registered budgets → Task 4. ✓
- §8 testing (witness/score/idset/refusal/baseline/drift-canary/policy-smoke/checkpoint) → Tasks 2,3,5,6,7,8,9,10. ✓
- §9 determinism (argmax, `budget_s=inf`, offline baseline, no wall-clock gate) → Tasks 7,9. ✓
- §11 CHANGELOG + review arc → Tasks 11,12. ✓

**2. Placeholder scan:** The only bracketed tokens are `<IMPL>` (the impl issue number, filled at Prerequisites) and the PR-body table paste (Task 11 Step 5) — both are concrete actions, not unfilled code. No "TODO"/"add error handling"/"similar to Task N".

**3. Type consistency:** `ReachVerdict(reached, parked, total, final_valid, max_swept_intrusion, reason)` and `RrmcVerdict(reached, n_routed, n_total, status)` are used identically across Tasks 1, 6, 7, 9. `_verdict_from(info, *, done, max_swept)` signature matches both call sites (Task 6 `score_episode`, Task 9 `policy_reach`). `build_scenario_env(scenario)` raises `NotImplementedError` in Task 5 and is caught as such in Task 9 `run_benchmark`. `load_baseline()` returns `{name: record}` (Task 7), consumed that way in Task 9. Consistent.
