# Learned Motion Policy Spike — Phase 0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a sampling-based SE(2) "complete-enough" tow-motion planner (the *teacher oracle*) and use it to answer the spike's Phase-0 GATE: can such a planner produce single-body insertion paths — and a **monotone insertion order** — for the real Herrenteich all-8 that uniform Hybrid-A* cannot find?

**Architecture:** A new isolated dev/CI-only subpackage `ml/motion/` built entirely on the **existing public** `hangarfit.towplanner` / `hangarfit.collisions` API. A goal-biased RRT with analytic goal-connection grows a tree of collision-free poses (validated by the production `path_first_conflict` oracle) and stitches a path. A greedy, core-parallel order-search drives it over the Herrenteich layout to emit the GATE verdict. No PyTorch in Phase 0 — this is the teacher, not the policy.

**Tech Stack:** Python 3.12, stdlib `random`/`math`/`concurrent.futures`/`multiprocessing`, the existing `hangarfit` package (shapely-backed collision oracle). No new third-party dependency.

## Global Constraints

- **Python 3.12 only** (ADR-0009). No version-range code.
- **`ml/` is a TOP-LEVEL dev/CI-only package**, never shipped in the wheel. New code lives under `ml/motion/`. The editable install does **not** put `ml/` on `sys.path` — run everything from the **repo root** (cwd = root) or with `PYTHONPATH=$PWD`.
- **Validity oracle = the product checker.** Path validity is `hangarfit.towplanner.path_first_conflict` (which calls `hangarfit.collisions.check`). Never a learned or re-derived surrogate (#694 contract; `ml-rl-guard`).
- **Determinism / seeding.** All randomness flows through a single `random.Random(seed)` passed in. Same `seed` + same scenario → identical path (the `ml-rl-guard` training-reproducibility invariant; here applied to the oracle).
- **Max CPU saturation.** Every multi-scenario stage runs on **all cores** via `ProcessPoolExecutor` with a **forkserver** context; thread-cap env vars (`OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `NUMEXPR_NUM_THREADS` = `"1"`) are set in the **parent before any worker import** (#758 pattern).
- **Lint/type clean.** `ruff check ml/`, `ruff format --check ml/`, and `mypy ml/` (run over the **whole** package, never a single file — `ml.*` uses `follow_imports="skip"`) must pass.
- **Tests** live under `tests/ml/motion/`, are torch-free (no `importorskip`), and are collected by the default `pytest` run.
- **Coordinate convention** (ADR-0002): `(0,0)` front-left; `+x` along the door wall (`hangar.width_m`); `+y` deeper into the hangar (`hangar.length_m`); `heading_deg` is compass — 0 at `+y`, **CW positive**.

---

## File Structure

| File | Responsibility |
|---|---|
| `ml/motion/__init__.py` | Subpackage marker. Empty. |
| `ml/motion/metric.py` | SE(2) distance metric + configuration sampler + `Bounds`. The reusable geometry of the search space. |
| `ml/motion/connect.py` | `connect_r0(start, goal) -> list[DubinsArc]`: the R=0 pivot–straight–pivot local connector (forward + reverse candidates), built from public `Segment`/`DubinsArc`. |
| `ml/motion/rrt.py` | `MotionPath`, `plan_rrt(...)`: goal-biased RRT with analytic goal-connection; all extensions validated by `path_first_conflict`. The teacher oracle core. |
| `ml/motion/route.py` | `route_into_slot(base, target, placed_ids, ...)`: door-entry-cone → slot wrapper over `plan_rrt`. |
| `ml/motion/phase0_herrenteich.py` | The Phase-0 driver: core-parallel greedy monotone order-search + hard-subset characterization + the GATE verdict. Runnable as `python -m ml.motion.phase0_herrenteich`. |
| `tests/ml/motion/__init__.py` | Test package marker. Empty. |
| `tests/ml/motion/test_metric.py` | Metric + sampler tests. |
| `tests/ml/motion/test_connect.py` | Connector reaches goal (forward/reverse/pure-rotation). |
| `tests/ml/motion/test_rrt.py` | RRT success on free space, determinism, bounded-failure. |
| `tests/ml/motion/test_route.py` | Routes a known-easy Herrenteich plane into its slot, oracle-verified. |
| `tests/ml/motion/test_phase0.py` | Order-search returns a structured result on a 2-plane mini-layout. |

**Why these boundaries:** `metric.py`, `connect.py`, `rrt.py` are the reusable oracle that survives into Phase 1 (the policy will imitate *its* paths). `route.py` and `phase0_herrenteich.py` are Phase-0-specific glue. Each file has one responsibility and is independently testable.

---

### Task 1: SE(2) metric, sampler, and search bounds

**Files:**
- Create: `ml/motion/__init__.py`
- Create: `ml/motion/metric.py`
- Create: `tests/ml/motion/__init__.py`
- Test: `tests/ml/motion/test_metric.py`

**Interfaces:**
- Consumes: `hangarfit.towplanner.Pose` (fields `x_m`, `y_m`, `heading_deg`); `hangarfit.models.Hangar` (fields `width_m`, `length_m`, `apron_depth_m`).
- Produces:
  - `se2_distance(a: Pose, b: Pose, *, w_ang: float = 2.0) -> float`
  - `class Bounds` (frozen) with `x_min, x_max, y_min, y_max: float` and classmethod `from_hangar(h: Hangar) -> Bounds`
  - `sample_config(b: Bounds, rng: random.Random) -> Pose`

- [ ] **Step 1: Create the empty package markers**

```bash
mkdir -p ml/motion tests/ml/motion
: > ml/motion/__init__.py
: > tests/ml/motion/__init__.py
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/ml/motion/test_metric.py
import random

from hangarfit.towplanner import Pose

from ml.motion.metric import Bounds, sample_config, se2_distance


def test_distance_zero_on_equal():
    p = Pose(1.0, 2.0, 30.0)
    assert se2_distance(p, p) == 0.0


def test_distance_symmetric():
    a, b = Pose(0.0, 0.0, 10.0), Pose(3.0, 4.0, 200.0)
    assert se2_distance(a, b) == se2_distance(b, a)


def test_distance_heading_wraps_short_way():
    # 359 deg vs 1 deg is 2 deg apart, not 358.
    near = se2_distance(Pose(0.0, 0.0, 359.0), Pose(0.0, 0.0, 1.0))
    assert near < se2_distance(Pose(0.0, 0.0, 0.0), Pose(0.0, 0.0, 90.0))


def test_sample_in_bounds_and_seeded_reproducible():
    b = Bounds(0.0, 10.0, -2.0, 20.0)
    poses1 = [sample_config(b, random.Random(7)) for _ in range(1)]
    poses2 = [sample_config(b, random.Random(7)) for _ in range(1)]
    p = poses1[0]
    assert b.x_min <= p.x_m <= b.x_max
    assert b.y_min <= p.y_m <= b.y_max
    assert 0.0 <= p.heading_deg < 360.0
    assert poses1[0] == poses2[0]  # same seed -> identical sample
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `PYTHONPATH=$PWD pytest tests/ml/motion/test_metric.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ml.motion.metric'`.

- [ ] **Step 4: Implement `metric.py`**

```python
# ml/motion/metric.py
"""SE(2) search-space geometry for the Phase-0 motion oracle (#607 spike).

Pure geometry over the existing compass-convention :class:`Pose`; no collision
knowledge lives here.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from hangarfit.models import Hangar
from hangarfit.towplanner import Pose

_W_ANG_M_PER_RAD = 2.0  # heading weight: 1 rad of turn ~ 2 m of travel in the metric


def se2_distance(a: Pose, b: Pose, *, w_ang: float = _W_ANG_M_PER_RAD) -> float:
    """Weighted SE(2) distance: Euclidean translation + ``w_ang`` * shortest
    angular gap (radians). Used only for nearest-neighbour selection."""
    dxy = math.hypot(a.x_m - b.x_m, a.y_m - b.y_m)
    dh = abs(((a.heading_deg - b.heading_deg + 180.0) % 360.0) - 180.0)
    return dxy + w_ang * math.radians(dh)


@dataclass(frozen=True, slots=True)
class Bounds:
    x_min: float
    x_max: float
    y_min: float
    y_max: float

    @classmethod
    def from_hangar(cls, h: Hangar) -> Bounds:
        # x spans the door wall; y spans door (0, minus the apron) to back wall.
        return cls(0.0, h.width_m, -h.apron_depth_m, h.length_m)


def sample_config(b: Bounds, rng: random.Random) -> Pose:
    """Uniformly sample a pose in ``b`` with a uniform heading."""
    return Pose(
        x_m=rng.uniform(b.x_min, b.x_max),
        y_m=rng.uniform(b.y_min, b.y_max),
        heading_deg=rng.uniform(0.0, 360.0),
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONPATH=$PWD pytest tests/ml/motion/test_metric.py -q`
Expected: PASS (4 passed).

- [ ] **Step 6: Lint, type-check, commit**

```bash
ruff check ml/ && ruff format ml/ && mypy ml/
git add ml/motion/__init__.py ml/motion/metric.py tests/ml/motion/__init__.py tests/ml/motion/test_metric.py
git commit -m "feat(motion): SE(2) metric, sampler, and search bounds for the Phase-0 oracle"
```

---

### Task 2: R=0 local connector (`connect_r0`)

**Files:**
- Create: `ml/motion/connect.py`
- Test: `tests/ml/motion/test_connect.py`

**Interfaces:**
- Consumes: `hangarfit.towplanner.Pose`, `Segment` (kinds `"L"/"S"/"R"`, `gear ∈ {1,-1}`, pivot `length_m` in **radians** at R=0), `DubinsArc` (`pose_at`, `length_m`).
- Produces: `connect_r0(start: Pose, goal: Pose) -> list[DubinsArc]` — a forward and a reverse-straight pivot–straight–pivot candidate (R=0). The caller collision-checks and picks.

**Background (read before implementing):** At `turn_radius_m == 0` the integrator (`DubinsArc.pose_at`, towplanner.py) treats an `"L"/"R"` segment's `length_m` as **radians of pivot-in-place** and `theta += sign*step` in the **math** frame (CCW-positive). Compass heading is CW-positive, so an *increase* in compass heading is a math-CCW *decrease* → a positive compass pivot delta is `"R"` (sign −1), a negative one is `"L"`. The tests pin this empirically.

- [ ] **Step 1: Write the failing tests**

```python
# tests/ml/motion/test_connect.py
import math

import pytest

from hangarfit.towplanner import Pose

from ml.motion.connect import connect_r0


def _close(p: Pose, q: Pose) -> bool:
    dh = abs(((p.heading_deg - q.heading_deg + 180.0) % 360.0) - 180.0)
    return math.isclose(p.x_m, q.x_m, abs_tol=1e-6) and math.isclose(
        p.y_m, q.y_m, abs_tol=1e-6
    ) and dh < 1e-6


@pytest.mark.parametrize(
    "start,goal",
    [
        (Pose(0.0, 0.0, 90.0), Pose(5.0, 0.0, 90.0)),     # drive +x, no turn
        (Pose(0.0, 0.0, 0.0), Pose(0.0, 5.0, 0.0)),       # drive +y, no turn
        (Pose(1.0, 1.0, 45.0), Pose(4.0, 7.0, 250.0)),    # general pose change
        (Pose(2.0, 3.0, 10.0), Pose(-2.0, -1.0, 300.0)),  # behind + turn
    ],
)
def test_every_candidate_reaches_goal(start, goal):
    cands = connect_r0(start, goal)
    assert len(cands) == 2  # forward + reverse
    for arc in cands:
        assert _close(arc.pose_at(arc.length_m), goal)


def test_pure_rotation_when_coincident():
    start, goal = Pose(3.0, 3.0, 0.0), Pose(3.0, 3.0, 120.0)
    cands = connect_r0(start, goal)
    assert _close(cands[0].pose_at(cands[0].length_m), goal)
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=$PWD pytest tests/ml/motion/test_connect.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ml.motion.connect'`.

- [ ] **Step 3: Implement `connect.py`**

```python
# ml/motion/connect.py
"""R=0 local connector for the Phase-0 motion oracle (#607 spike).

Builds pivot-in-place + straight + pivot moves between two poses as public
:class:`DubinsArc` objects. R=0 is faithful for the whole Herrenteich fleet
(every mover is ``always_cart`` or ``tow_pivotable`` -> ``effective_turn_radius_m
== 0``). Finite-radius connect is a Phase-1+ concern (use the existing
``plan_reeds_shepp`` then).
"""

from __future__ import annotations

import math

from hangarfit.towplanner import DubinsArc, Pose, Segment

_EPS = 1e-9


def _norm180(deg: float) -> float:
    return ((deg + 180.0) % 360.0) - 180.0


def _pivot(dpivot_deg: float) -> list[Segment]:
    if abs(dpivot_deg) < _EPS:
        return []
    # compass CW-positive delta -> "R" (math sign -1); negative -> "L". Pivot
    # length_m is RADIANS at R=0 (the integrator's cart pivot-in-place encoding).
    kind = "R" if dpivot_deg >= 0.0 else "L"
    return [Segment(kind, math.radians(abs(dpivot_deg)))]


def connect_r0(start: Pose, goal: Pose) -> list[DubinsArc]:
    """Forward and reverse-straight pivot-straight-pivot candidates (R=0)."""
    dx, dy = goal.x_m - start.x_m, goal.y_m - start.y_m
    dist = math.hypot(dx, dy)
    if dist < _EPS:
        segs = _pivot(_norm180(goal.heading_deg - start.heading_deg)) or [Segment("S", 0.0)]
        return [DubinsArc(start=start, end=goal, turn_radius_m=0.0, segments=tuple(segs))]

    bearing = math.degrees(math.atan2(dx, dy))  # compass: 0 at +y, CW positive
    forward = (
        _pivot(_norm180(bearing - start.heading_deg))
        + [Segment("S", dist)]
        + _pivot(_norm180(goal.heading_deg - bearing))
    )
    back = _norm180(bearing + 180.0)
    reverse = (
        _pivot(_norm180(back - start.heading_deg))
        + [Segment("S", dist, gear=-1)]
        + _pivot(_norm180(goal.heading_deg - back))
    )
    return [
        DubinsArc(start=start, end=goal, turn_radius_m=0.0, segments=tuple(forward)),
        DubinsArc(start=start, end=goal, turn_radius_m=0.0, segments=tuple(reverse)),
    ]
```

- [ ] **Step 4: Run to verify pass**

Run: `PYTHONPATH=$PWD pytest tests/ml/motion/test_connect.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Lint, type-check, commit**

```bash
ruff check ml/ && ruff format ml/ && mypy ml/
git add ml/motion/connect.py tests/ml/motion/test_connect.py
git commit -m "feat(motion): R=0 pivot-straight-pivot local connector (forward + reverse)"
```

---

### Task 3: Collision-checked extend + `MotionPath`

**Files:**
- Create: `ml/motion/rrt.py` (partial — the helpers; `plan_rrt` lands in Task 4)
- Test: `tests/ml/motion/test_rrt.py` (partial — extend/path tests)

**Interfaces:**
- Consumes: `connect_r0` (Task 2); `se2_distance` (Task 1); `hangarfit.towplanner.path_first_conflict(arc, mover, *, mover_on_carts, placed, ...) -> Conflict | None`; `hangarfit.models.Aircraft`, `Layout`.
- Produces:
  - `class MotionPath` (frozen) with `edges: tuple[DubinsArc, ...]` and `length_m: float`
  - `first_clear(start, target, mover, *, mover_on_carts, placed) -> DubinsArc | None`
  - `class _Node` (mutable) `pose: Pose, parent: int, edge: DubinsArc | None`
  - `_extend(tree: list[_Node], q_rand, mover, *, mover_on_carts, placed, step_m) -> int | None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/ml/motion/test_rrt.py
from hangarfit.loader import load_layout
from hangarfit.models import Layout
from hangarfit.towplanner import Pose

from ml.motion.rrt import MotionPath, first_clear


def _empty_placed(base: Layout) -> Layout:
    # Same hangar + fleet, but NOTHING placed -> free space.
    return Layout(
        fleet=base.fleet,
        hangar=base.hangar,
        placements=(),
        maintenance_plane=base.maintenance_plane,
        ground_objects=base.ground_objects,
        ground_object_placements=(),
    )


def test_first_clear_in_free_space_returns_arc():
    base = load_layout("examples/herrenteich/layout.yaml")
    placed = _empty_placed(base)
    mover = next(iter(base.fleet.values()))
    start = Pose(base.hangar.width_m / 2.0, 1.0, 0.0)
    goal = Pose(base.hangar.width_m / 2.0, 5.0, 0.0)
    arc = first_clear(start, goal, mover, mover_on_carts=False, placed=placed)
    assert arc is not None


def test_motionpath_length_sums_edges():
    base = load_layout("examples/herrenteich/layout.yaml")
    placed = _empty_placed(base)
    mover = next(iter(base.fleet.values()))
    arc = first_clear(
        Pose(5.0, 1.0, 0.0), Pose(5.0, 4.0, 0.0), mover, mover_on_carts=False, placed=placed
    )
    assert arc is not None
    path = MotionPath(edges=(arc,))
    assert path.length_m == arc.length_m
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=$PWD pytest tests/ml/motion/test_rrt.py -q`
Expected: FAIL — `ImportError: cannot import name 'MotionPath' from 'ml.motion.rrt'`.

- [ ] **Step 3: Implement the helpers in `rrt.py`**

```python
# ml/motion/rrt.py
"""Goal-biased RRT motion oracle for the Phase-0 spike (#607).

Grows a tree of collision-free poses from the entry pose; every extension is
validated by the production ``path_first_conflict`` oracle. After each
extension it attempts an analytic connect to the goal (the bidirectional
benefit without the swap bookkeeping). ``plan_rrt`` lands in Task 4.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from hangarfit.models import Aircraft, Layout
from hangarfit.towplanner import DubinsArc, Pose, path_first_conflict

from .connect import connect_r0
from .metric import se2_distance


@dataclass(frozen=True, slots=True)
class MotionPath:
    edges: tuple[DubinsArc, ...]

    @property
    def length_m(self) -> float:
        return math.fsum(e.length_m for e in self.edges)


@dataclass(slots=True)
class _Node:
    pose: Pose
    parent: int
    edge: DubinsArc | None  # incoming edge from `parent` (None at the root)


def first_clear(
    start: Pose,
    target: Pose,
    mover: Aircraft,
    *,
    mover_on_carts: bool,
    placed: Layout,
) -> DubinsArc | None:
    """The first ``connect_r0`` candidate (forward, then reverse) that is
    collision-free against ``placed`` per the production oracle, else ``None``."""
    for arc in connect_r0(start, target):
        if path_first_conflict(arc, mover, mover_on_carts=mover_on_carts, placed=placed) is None:
            return arc
    return None


def _extend(
    tree: list[_Node],
    q_rand: Pose,
    mover: Aircraft,
    *,
    mover_on_carts: bool,
    placed: Layout,
    step_m: float,
) -> int | None:
    """Grow ``tree`` one step toward ``q_rand``. Returns the new node index or
    ``None`` if the step is blocked."""
    i = min(range(len(tree)), key=lambda k: se2_distance(tree[k].pose, q_rand))
    q_near = tree[i].pose
    toward = connect_r0(q_near, q_rand)[0]  # forward candidate gives the direction
    q_new = toward.pose_at(min(step_m, toward.length_m))
    edge = first_clear(q_near, q_new, mover, mover_on_carts=mover_on_carts, placed=placed)
    if edge is None:
        return None
    tree.append(_Node(pose=q_new, parent=i, edge=edge))
    return len(tree) - 1
```

- [ ] **Step 4: Run to verify pass**

Run: `PYTHONPATH=$PWD pytest tests/ml/motion/test_rrt.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Lint, type-check, commit**

```bash
ruff check ml/ && ruff format ml/ && mypy ml/
git add ml/motion/rrt.py tests/ml/motion/test_rrt.py
git commit -m "feat(motion): collision-checked RRT extend + MotionPath (oracle helpers)"
```

---

### Task 4: `plan_rrt` — the oracle core

**Files:**
- Modify: `ml/motion/rrt.py` (add `plan_rrt` + `_reconstruct`)
- Test: `tests/ml/motion/test_rrt.py` (add planning tests)

**Interfaces:**
- Consumes: `_Node`, `_extend`, `first_clear`, `MotionPath` (Task 3); `Bounds`, `sample_config` (Task 1).
- Produces: `plan_rrt(mover, start, goal, *, hangar, placed, mover_on_carts, max_iter=4000, step_m=1.0, seed=0, goal_bias=0.1) -> MotionPath | None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/ml/motion/test_rrt.py  (append)
import random

from ml.motion.rrt import plan_rrt


def test_plan_rrt_trivial_free_space_succeeds_and_is_verified():
    base = load_layout("examples/herrenteich/layout.yaml")
    placed = _empty_placed(base)
    mover = next(iter(base.fleet.values()))
    start = Pose(base.hangar.width_m / 2.0, 0.5, 0.0)
    goal = Pose(base.hangar.width_m / 2.0, 6.0, 0.0)
    path = plan_rrt(mover, start, goal, hangar=base.hangar, placed=placed,
                    mover_on_carts=False, seed=1, max_iter=2000)
    assert path is not None
    # Re-verify EVERY edge with the production oracle (never trust the planner).
    for edge in path.edges:
        assert first_clear(edge.start, edge.end, mover, mover_on_carts=False, placed=placed)


def test_plan_rrt_is_seed_reproducible():
    base = load_layout("examples/herrenteich/layout.yaml")
    placed = _empty_placed(base)
    mover = next(iter(base.fleet.values()))
    start = Pose(3.0, 0.5, 0.0)
    goal = Pose(8.0, 7.0, 90.0)
    a = plan_rrt(mover, start, goal, hangar=base.hangar, placed=placed,
                 mover_on_carts=False, seed=42, max_iter=3000)
    b = plan_rrt(mover, start, goal, hangar=base.hangar, placed=placed,
                 mover_on_carts=False, seed=42, max_iter=3000)
    assert a is not None and b is not None
    assert len(a.edges) == len(b.edges)
    assert a.edges[-1].end == b.edges[-1].end


def test_plan_rrt_bounded_failure_returns_none():
    # Goal that the direct shot cannot reach (blocked by the other 7 placed),
    # with NO search iterations -> deterministic None, fast.
    base = load_layout("examples/herrenteich/layout.yaml")
    husky = [p for p in base.placements if p.plane_id == "aviat_husky"][0]
    placed = Layout(
        fleet=base.fleet, hangar=base.hangar,
        placements=tuple(p for p in base.placements if p.plane_id != "aviat_husky"),
        maintenance_plane=base.maintenance_plane, ground_objects=base.ground_objects,
        ground_object_placements=(),
    )
    mover = base.fleet["aviat_husky"]
    start = Pose(base.hangar.width_m / 2.0, -1.0, 0.0)
    goal = Pose(husky.x_m, husky.y_m, husky.heading_deg)
    path = plan_rrt(mover, start, goal, hangar=base.hangar, placed=placed,
                    mover_on_carts=husky.on_carts, seed=0, max_iter=0)
    assert path is None
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=$PWD pytest tests/ml/motion/test_rrt.py -q`
Expected: FAIL — `ImportError: cannot import name 'plan_rrt'`.

- [ ] **Step 3: Add `plan_rrt` + `_reconstruct` to `rrt.py`**

```python
# ml/motion/rrt.py  (append; add `import random` and Bounds/sample_config imports at top)
import random  # noqa: E402  (place with the other imports at the top of the file)

from .metric import Bounds, sample_config  # noqa: E402  (merge into the existing metric import)


def _reconstruct(tree: list[_Node], i_new: int, final_edge: DubinsArc) -> MotionPath:
    edges: list[DubinsArc] = [final_edge]
    i = i_new
    while tree[i].parent != -1:
        assert tree[i].edge is not None
        edges.append(tree[i].edge)
        i = tree[i].parent
    edges.reverse()
    return MotionPath(edges=tuple(edges))


def plan_rrt(
    mover: Aircraft,
    start: Pose,
    goal: Pose,
    *,
    hangar,
    placed: Layout,
    mover_on_carts: bool,
    max_iter: int = 4000,
    step_m: float = 1.0,
    seed: int = 0,
    goal_bias: float = 0.1,
) -> MotionPath | None:
    """Goal-biased RRT with analytic goal-connection. Returns an oracle-clean
    :class:`MotionPath` or ``None`` if no path is found within ``max_iter``."""
    # Easy case (the 5/8 that the analytic shot solves directly).
    direct = first_clear(start, goal, mover, mover_on_carts=mover_on_carts, placed=placed)
    if direct is not None:
        return MotionPath(edges=(direct,))

    rng = random.Random(seed)
    bounds = Bounds.from_hangar(hangar)
    tree: list[_Node] = [_Node(pose=start, parent=-1, edge=None)]
    for _ in range(max_iter):
        q_rand = goal if rng.random() < goal_bias else sample_config(bounds, rng)
        i_new = _extend(
            tree, q_rand, mover, mover_on_carts=mover_on_carts, placed=placed, step_m=step_m
        )
        if i_new is None:
            continue
        edge = first_clear(
            tree[i_new].pose, goal, mover, mover_on_carts=mover_on_carts, placed=placed
        )
        if edge is not None:
            return _reconstruct(tree, i_new, edge)
    return None
```

> **Implementer note:** merge the new `import random` and `from .metric import Bounds, sample_config` into the **existing** import block at the top of `rrt.py` (Task 3 already imports `se2_distance` from `.metric`); the inline `# noqa` markers above are only to show where they go. Add `hangar: Hangar` to the signature's type by importing `Hangar` from `hangarfit.models`.

- [ ] **Step 4: Run to verify pass**

Run: `PYTHONPATH=$PWD pytest tests/ml/motion/test_rrt.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Lint, type-check, commit**

```bash
ruff check ml/ && ruff format ml/ && mypy ml/
git add ml/motion/rrt.py tests/ml/motion/test_rrt.py
git commit -m "feat(motion): plan_rrt goal-biased RRT oracle core (analytic goal-connection)"
```

---

### Task 5: Door-cone → slot routing wrapper

**Files:**
- Create: `ml/motion/route.py`
- Test: `tests/ml/motion/test_route.py`

**Interfaces:**
- Consumes: `plan_rrt`, `MotionPath` (Task 4); `hangarfit.towplanner.entry_poses(target, hangar) -> tuple[Pose, ...]`; `hangarfit.models.Layout`, `Placement`, `Pose.from_placement`.
- Produces: `route_into_slot(base: Layout, target: Placement, placed_ids: set[str], *, seed=0, max_iter=4000, step_m=1.0) -> MotionPath | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/ml/motion/test_route.py
from hangarfit.loader import load_layout

from ml.motion.route import route_into_slot


def test_routes_a_known_easy_plane_into_its_slot():
    # The grid-resolution sweep showed scheibe_falke routes at every resolution;
    # the strictly-stronger RRT oracle must also solve it against the other 7.
    base = load_layout("examples/herrenteich/layout.yaml")
    target = [p for p in base.placements if p.plane_id == "scheibe_falke"][0]
    others = {p.plane_id for p in base.placements} - {"scheibe_falke"}
    path = route_into_slot(base, target, others, seed=0, max_iter=4000)
    assert path is not None
    assert path.edges  # non-empty
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=$PWD pytest tests/ml/motion/test_route.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ml.motion.route'`.

- [ ] **Step 3: Implement `route.py`**

```python
# ml/motion/route.py
"""Door-entry-cone -> slot routing wrapper over the Phase-0 RRT oracle (#607)."""

from __future__ import annotations

from hangarfit.models import Layout, Placement
from hangarfit.towplanner import Pose, entry_poses

from .rrt import MotionPath, plan_rrt


def route_into_slot(
    base: Layout,
    target: Placement,
    placed_ids: set[str],
    *,
    seed: int = 0,
    max_iter: int = 4000,
    step_m: float = 1.0,
) -> MotionPath | None:
    """Route ``target`` from a door-entry pose to its slot past the bodies in
    ``placed_ids`` (excluding ``target`` itself). Tries each entry-cone pose;
    returns the first oracle-clean path or ``None``."""
    placed = Layout(
        fleet=base.fleet,
        hangar=base.hangar,
        placements=tuple(
            p for p in base.placements if p.plane_id in placed_ids and p.plane_id != target.plane_id
        ),
        maintenance_plane=base.maintenance_plane,
        ground_objects=base.ground_objects,
        ground_object_placements=(),  # Phase 0: route against aircraft only
    )
    mover = base.fleet[target.plane_id]
    goal = Pose.from_placement(target)
    for entry in entry_poses(target, base.hangar):
        path = plan_rrt(
            mover,
            entry,
            goal,
            hangar=base.hangar,
            placed=placed,
            mover_on_carts=target.on_carts,
            seed=seed,
            max_iter=max_iter,
            step_m=step_m,
        )
        if path is not None:
            return path
    return None
```

- [ ] **Step 4: Run to verify pass**

Run: `PYTHONPATH=$PWD pytest tests/ml/motion/test_route.py -q`
Expected: PASS (1 passed). *(May take a few seconds — it runs the real oracle.)*

- [ ] **Step 5: Lint, type-check, commit**

```bash
ruff check ml/ && ruff format ml/ && mypy ml/
git add ml/motion/route.py tests/ml/motion/test_route.py
git commit -m "feat(motion): door-cone to slot routing wrapper over the RRT oracle"
```

---

### Task 6: Phase-0 Herrenteich driver + GATE verdict

**Files:**
- Create: `ml/motion/phase0_herrenteich.py`
- Test: `tests/ml/motion/test_phase0.py`

**Interfaces:**
- Consumes: `route_into_slot` (Task 5); `hangarfit.loader.load_layout`; `hangarfit.towplanner.back_first_order`; `hangarfit.models.Layout`, `Placement`.
- Produces:
  - `class MonotoneResult` (frozen): `order: tuple[str, ...]`, `stalled: tuple[str, ...]`, `found: bool`
  - `find_monotone_order(base: Layout, *, seed=0, max_iter=4000, workers=None) -> MonotoneResult`
  - `main() -> None` (the `python -m ml.motion.phase0_herrenteich` entry point printing the GATE verdict + hard-subset characterization).

- [ ] **Step 1: Write the failing test**

```python
# tests/ml/motion/test_phase0.py
from hangarfit.loader import load_layout
from hangarfit.models import Layout

from ml.motion.phase0_herrenteich import MonotoneResult, find_monotone_order


def _two_plane_sublayout(base: Layout) -> Layout:
    # Two easy, well-separated planes -> a monotone order must exist.
    keep = {"scheibe_falke", "ctsl"}
    return Layout(
        fleet=base.fleet,
        hangar=base.hangar,
        placements=tuple(p for p in base.placements if p.plane_id in keep),
        maintenance_plane=base.maintenance_plane,
        ground_objects=base.ground_objects,
        ground_object_placements=(),
    )


def test_order_search_returns_structured_result_on_mini_layout():
    base = load_layout("examples/herrenteich/layout.yaml")
    mini = _two_plane_sublayout(base)
    res = find_monotone_order(mini, seed=0, max_iter=3000, workers=2)
    assert isinstance(res, MonotoneResult)
    # Every plane is accounted for exactly once across order + stalled.
    assert set(res.order) | set(res.stalled) == {"scheibe_falke", "ctsl"}
    assert len(res.order) + len(res.stalled) == 2
    assert res.found == (len(res.stalled) == 0)
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=$PWD pytest tests/ml/motion/test_phase0.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ml.motion.phase0_herrenteich'`.

- [ ] **Step 3: Implement `phase0_herrenteich.py`**

```python
# ml/motion/phase0_herrenteich.py
"""Phase-0 GATE driver (#607 motion-policy spike).

Answers: can the RRT oracle find a MONOTONE single-body insertion order +
paths for the real Herrenteich all-8 that uniform Hybrid-A* cannot? Greedy:
at each step, route every not-yet-placed plane past the already-placed ones
(in parallel, all cores) and commit the first that succeeds, deterministic
tie-break by back-first rank. Prints the GATE verdict.
"""

from __future__ import annotations

# --- thread caps MUST be set before any numpy/shapely import in workers (#758) ---
import os

for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

from hangarfit.loader import load_layout
from hangarfit.models import Layout
from hangarfit.towplanner import back_first_order

from .route import route_into_slot

_HARD_SUBSET = ("aviat_husky", "cessna_140", "fk9_mkii")


@dataclass(frozen=True, slots=True)
class MonotoneResult:
    order: tuple[str, ...]
    stalled: tuple[str, ...]

    @property
    def found(self) -> bool:
        return len(self.stalled) == 0


def _try_route(args: tuple[Layout, str, frozenset[str], int, int]) -> tuple[str, bool]:
    base, pid, placed_ids, seed, max_iter = args
    target = next(p for p in base.placements if p.plane_id == pid)
    path = route_into_slot(base, target, set(placed_ids), seed=seed, max_iter=max_iter)
    return pid, path is not None


def find_monotone_order(
    base: Layout, *, seed: int = 0, max_iter: int = 4000, workers: int | None = None
) -> MonotoneResult:
    rank = {p.plane_id: i for i, p in enumerate(back_first_order(base.placements))}
    remaining = sorted((p.plane_id for p in base.placements), key=lambda pid: rank[pid])
    placed: list[str] = []
    ctx = mp.get_context("forkserver")
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as ex:
        while remaining:
            placed_ids = frozenset(placed)
            jobs = [(base, pid, placed_ids, seed, max_iter) for pid in remaining]
            results = dict(ex.map(_try_route, jobs))
            # commit the routable plane of lowest back-first rank (deterministic).
            routable = [pid for pid in remaining if results[pid]]
            if not routable:
                break  # stall
            chosen = min(routable, key=lambda pid: rank[pid])
            placed.append(chosen)
            remaining.remove(chosen)
    return MonotoneResult(order=tuple(placed), stalled=tuple(remaining))


def main() -> None:
    base = load_layout("examples/herrenteich/layout.yaml")
    res = find_monotone_order(base, seed=0, max_iter=8000)
    print("=== Phase-0 GATE: Herrenteich all-8 monotone single-body routing ===")
    print(f"insertion order found: {res.found}")
    print(f"  order  : {list(res.order)}")
    print(f"  stalled: {list(res.stalled)}")
    if res.found:
        print("VERDICT: GATE 0 PASS -> proceed to Phase 1 (BC dataset from this oracle).")
        print(f"  (hard subset {_HARD_SUBSET} all placed in a monotone order)")
    else:
        hard_stalled = [p for p in res.stalled if p in _HARD_SUBSET]
        print("VERDICT: GATE 0 STALL -> single-body routing insufficient for:")
        print(f"  {res.stalled}  (hard-subset members stalled: {hard_stalled})")
        print("  => the gap is MULTI-BODY (move-aside / interlock), not ML-feasibility.")
        print("     Re-scope per spec GATE 0; do NOT proceed to Phase 1 as specified.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify pass**

Run: `PYTHONPATH=$PWD pytest tests/ml/motion/test_phase0.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Run the actual Phase-0 GATE experiment (the spike deliverable)**

Run: `PYTHONPATH=$PWD python -m ml.motion.phase0_herrenteich`
Expected: prints the order-search result and one of the two VERDICT branches. **This is the GATE-0 evidence** — record the printed verdict in the spike write-up. (It may run for minutes; it saturates all cores.)

- [ ] **Step 6: Lint, type-check, full test sweep, commit**

```bash
ruff check ml/ && ruff format ml/ && mypy ml/
PYTHONPATH=$PWD pytest tests/ml/motion/ -q
git add ml/motion/phase0_herrenteich.py tests/ml/motion/test_phase0.py
git commit -m "feat(motion): Phase-0 Herrenteich GATE driver (parallel monotone order-search)"
```

---

## Self-Review (completed)

- **Spec coverage (Phase 0 only).** Spec §6 Phase 0(a) monotone-order existence → Task 6 `find_monotone_order` + `main`. §6 Phase 0(b) "solves procedural hard scenarios" → deferred to the Phase 1 plan (Phase 0 here targets the *real* Herrenteich case, which is the decisive GATE evidence; procedural scenarios belong with the dataset generator in Phase 1). §5 oracle component → Tasks 1–4. §5.2 product-checker validity → `first_clear`/`path_first_conflict` everywhere; no surrogate. §8 CPU/parallelism → Task 6 forkserver + BLAS-cap. §7 determinism → seeded `random.Random`, `test_plan_rrt_is_seed_reproducible`. **Phases 1–2 (BC dataset, encoder, policy, DAgger, eval) are intentionally a separate plan, written after GATE 0 passes** — their concrete shape (demo format, oracle final choice) depends on this phase's outcome (spec §11 Q1).
- **Placeholder scan.** No TBD/TODO; every code step shows complete code; the one "Implementer note" in Task 4 is an import-merge instruction with the exact lines, not a placeholder.
- **Type consistency.** `MotionPath.edges: tuple[DubinsArc, ...]`, `first_clear(...) -> DubinsArc | None`, `_extend(...) -> int | None`, `plan_rrt(...) -> MotionPath | None`, `route_into_slot(...) -> MotionPath | None`, `find_monotone_order(...) -> MonotoneResult` — names/types are used identically across Tasks 3→6. `connect_r0 -> list[DubinsArc]` consumed by `first_clear`. `Bounds`/`sample_config`/`se2_distance` from Task 1 used in Task 4.

> **Scope note (deliberate):** This plan is **Phase 0 of 2**. It produces working, testable software on its own (a sampling-based motion oracle + the GATE-0 verdict) and is the de-risking gate for the whole spike. The Phase 1–2 plan (imitation dataset → encoder → policy → DAgger → held-out eval) is authored only **if GATE 0 passes**.
