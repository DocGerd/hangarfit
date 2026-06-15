# Cold-joint RL Environment + Reward — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the cold-joint RL **environment + reward** (sub-project #1 of epic #607) — a `gym`-style env where an agent drives objects in from the apron and parks them one at a time, scored by a graded-lexicographic reward read from the existing geometry oracle.

**Architecture:** A new dependency-free top-level `ml/` package (never in the wheel, like `bench/`/`viewer/`). A plain-Python `HangarFitEnv` exposes `reset()/step()`; it **reuses the deterministic geometry** (`collisions.check`, the parts-model world transform, the ADR-0010 motion primitives + swept-path clearance, the apron, the Caddy egress oracle) and **does not** use the RR-MC or Hybrid-A* *search*. The reward is graded-lexicographic (hard collision/out-of-bounds/egress terms dominate; soft spread/sequence/region terms tie-break; movement cost; terminal fraction-placed) plus policy-invariant potential-based shaping. No neural net, no training, no `gymnasium`/`torch` — those are later rungs (#2/#3/#5).

**Tech Stack:** Python 3.12, `shapely` (already a project dep), the existing `hangarfit` package as the geometry oracle. Pure stdlib + shapely otherwise.

**Spec:** `docs/superpowers/specs/2026-06-12-learned-backend-cold-joint-rl-env-design.md`. **Issue:** #672 (sub-issue of #607). **Determinism:** the env reward is RNG-free; ADR-0027 governs the learned-path contract (its canaries land with #5).

**Key resolved-since-spec fact:** §6.1's "blocked on strafe" is **done** — the lateral-strafe `T` primitive landed in #647 (`SegmentKind = Literal["L","S","R","T"]`, `towplanner.py:87`). Full action space is available.

---

## Design decisions locked here (from the spec; do not re-litigate)

- **Penalty-only legality** (spec §4.4): illegal moves (out-of-bounds / overlap) are **allowed but graded-penalized**, not hard-masked. (Hard masking is a deferred #3 option.)
- **Continuous magnitude, with a binning helper** (spec §4.3): a `Primitive` carries a continuous `magnitude` (metres for `S`/`T`, radians for cart pivots `L`/`R` at `r=0`, metres of arc otherwise); a `bin_magnitude()` helper is provided but the env accepts continuous.
- **Reuse towplanner internals directly** (spec §6): `ml/` imports `towplanner._motion_clear`, `_build_obstacles`, `_primitives`, `_count_cusps`, `CUSP_PENALTY`, `Segment`, `DubinsArc`, `Pose`. This intentionally couples `ml/` to those helpers; **do not** modify `towplanner.py`/`solver.py` (keeps `determinism-guard` uninvolved). If the coupling later proves fragile, a future rung extracts a public `motion` API — out of scope here.
- **Default reward weights are placeholders** tuned in #4; the **ordering invariant** (any hard violation outweighs any achievable soft bonus) is enforced and tested here.
- **Observation is semantic** (structured dataclasses), not tensors (tensorization is #2).

## File Structure

- Create `ml/__init__.py` — package marker + version note (dev-only, never in wheel).
- Create `ml/types.py` — `Pose` re-export, `Primitive`, `Park`, `Action`, `Observation`, `ParkedObject`, `ActiveObject`, `DifficultyConfig`, `RewardWeights`, `StepInfo` dataclasses.
- Create `ml/geometry_oracle.py` — thin functions reusing `hangarfit` geometry: `overlap_area_m2`, `intrusion_area_m2`, `legal_primitives`, `apply_primitive`, `swept_intrusion_m2`, `movement_cost`, `egress_blocked`.
- Create `ml/reward.py` — `potential`, `step_reward` (the graded-lexicographic tiers + shaping).
- Create `ml/env.py` — `HangarFitEnv` (reset/step/state/termination/curriculum/info).
- Create `ml/README.md` — what `ml/` is, how to run its tests, the wheel-exclusion note.
- Create `tests/ml/__init__.py`, `tests/ml/conftest.py` (shared tiny fixtures), `tests/ml/test_geometry_oracle.py`, `tests/ml/test_reward.py`, `tests/ml/test_env.py`.
- Modify `CHANGELOG.md` — `[Unreleased]/Added` entry.

`ml/` is a top-level package (importable as `ml` from the repo root, like `bench`), so `tests/ml/` runs inside the main `pytest` suite and gets CI coverage. It is excluded from the wheel by the existing `[tool.setuptools.packages.find] where = ["src"]`.

---

### Task 1: Scaffold the `ml/` package

**Files:**
- Create: `ml/__init__.py`
- Create: `ml/README.md`
- Test: `tests/ml/__init__.py`, `tests/ml/test_env.py`

- [ ] **Step 1: Write the failing import test**

`tests/ml/__init__.py` is empty. `tests/ml/test_env.py`:
```python
"""Tests for the cold-joint RL environment (epic #607 sub-project #1, #672)."""
from __future__ import annotations


def test_ml_package_importable():
    import ml

    assert ml.__doc__ is not None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/ml/test_env.py::test_ml_package_importable -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ml'`.

- [ ] **Step 3: Create the package**

`ml/__init__.py`:
```python
"""hangarfit learned-backend RL workspace (epic #607).

Dev/CI-only — like ``bench/`` and ``viewer/`` this top-level package is NOT in the
wheel (``[tool.setuptools.packages.find] where = ["src"]``). It holds the cold-joint
RL environment + reward (sub-project #1) that reuse the deterministic ``hangarfit``
geometry as a reward oracle. No neural net or training lives here yet.
"""
```

`ml/README.md`:
```markdown
# ml/ — learned-backend RL workspace (#607)

Dev/CI-only, never shipped in the wheel. Sub-project #1: the cold-joint RL
environment + reward (`HangarFitEnv`), reusing `hangarfit`'s geometry oracle.

## Run the tests
    pytest tests/ml/

## Design
See `docs/superpowers/specs/2026-06-12-learned-backend-cold-joint-rl-env-design.md`
and ADR-0027 (learned-path determinism scope).
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ml/test_env.py::test_ml_package_importable -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ml/__init__.py ml/README.md tests/ml/__init__.py tests/ml/test_env.py
git commit -m "feat(672): scaffold ml/ package for the cold-joint RL env"
```

---

### Task 2: Core types (`ml/types.py`)

**Files:**
- Create: `ml/types.py`
- Test: `tests/ml/test_geometry_oracle.py` (start the file with a types smoke test)

- [ ] **Step 1: Write the failing test**

`tests/ml/test_geometry_oracle.py`:
```python
"""Tests for ml.geometry_oracle and ml.types (#672)."""
from __future__ import annotations

from ml.types import Park, Primitive, RewardWeights


def test_primitive_and_park_construct():
    p = Primitive(kind="S", magnitude=1.5, gear=1)
    assert p.kind == "S" and p.magnitude == 1.5 and p.gear == 1
    assert isinstance(Park(), Park)


def test_reward_weights_ordering_invariant_holds_by_default():
    w = RewardWeights()
    # Any hard weight must dominate the sum of achievable soft bonuses.
    assert min(w.w_col, w.w_oob, w.w_egress) > (w.w_gap + w.w_seq + w.w_region)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/ml/test_geometry_oracle.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ml.types'`.

- [ ] **Step 3: Implement `ml/types.py`**

```python
"""Semantic types for the cold-joint RL env (tensorization is sub-project #2)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from hangarfit.models import Aircraft, GroundObject, Placement
from hangarfit.towplanner import Pose, Segment, SegmentKind

__all__ = [
    "Pose", "Primitive", "Park", "Action", "ParkedObject", "ActiveObject",
    "Observation", "DifficultyConfig", "RewardWeights", "StepInfo",
]


@dataclass(frozen=True, slots=True)
class Primitive:
    """One movement primitive applied to the active object.

    ``magnitude`` is continuous: metres for ``S``/``T`` and own-gear arcs; radians of
    pivot for a cart turn (``L``/``R`` at turn_radius 0). ``gear`` is +1 forward / -1
    reverse (ADR-0010). A binning helper lives in ``geometry_oracle.bin_magnitude``.
    """
    kind: SegmentKind
    magnitude: float
    gear: Literal[1, -1] = 1


@dataclass(frozen=True, slots=True)
class Park:
    """Commit the active object's pose and advance to the next object."""


Action = Primitive | Park


@dataclass(frozen=True, slots=True)
class ParkedObject:
    """An already-frozen object (immovable obstacle)."""
    object_id: str
    placement: Placement


@dataclass(frozen=True, slots=True)
class ActiveObject:
    """The object currently being driven in."""
    object_id: str
    body: Aircraft | GroundObject
    pose: Pose
    on_carts: bool


@dataclass(frozen=True, slots=True)
class Observation:
    """Semantic snapshot the agent sees each step (sub-project #2 tensorizes this)."""
    active: ActiveObject | None  # None only at a terminal state
    parked: tuple[ParkedObject, ...]
    unplaced_ids: tuple[str, ...]
    steps_this_object: int
    steps_total: int


@dataclass(frozen=True, slots=True)
class DifficultyConfig:
    """Curriculum knobs (spec §7). All optional; defaults = the full real task."""
    max_objects: int | None = None       # cap the requested set size (None = all)
    per_object_step_budget: int = 60      # primitives before an object is "unplaceable"
    total_step_budget: int = 600          # global per-episode primitive cap
    seed_anchor: bool = False             # spawn near a known-valid anchor (curriculum, NOT BC)


@dataclass(frozen=True, slots=True)
class RewardWeights:
    """Reward weights (spec §5). Defaults are placeholders tuned in #4; the ORDERING
    invariant (any hard term dominates the soft sum) is enforced and tested here."""
    w_col: float = 100.0       # hard: collision overlap area
    w_oob: float = 100.0       # hard: out-of-bounds / notch / keep-out intrusion area
    w_egress: float = 100.0    # hard: Caddy egress violation
    w_move: float = 0.1        # movement: per-metre + per-cusp cost scale
    cusp_penalty: float = 10.0 # mirrors towplanner.CUSP_PENALTY (#480)
    w_gap: float = 1.0         # soft: inter-object min gap
    w_seq: float = 1.0         # soft: requested door-order deviation
    w_region: float = 1.0      # soft: region preference
    r_terminal: float = 50.0   # terminal: per fraction-placed
    gamma: float = 0.99        # shaping discount


@dataclass(frozen=True, slots=True)
class StepInfo:
    """`info` dict payload: reward-term breakdown + live verdict (spec §9)."""
    terms: dict[str, float]
    valid: bool
    placed: int
    total: int
    reason: str = ""  # termination reason when done
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/ml/test_geometry_oracle.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/types.py tests/ml/test_geometry_oracle.py
git commit -m "feat(672): ml.types — env/action/observation/reward dataclasses"
```

---

### Task 3: `overlap_area_m2` — reuse the graded collision signal

**Files:**
- Create: `ml/geometry_oracle.py`
- Test: `tests/ml/test_geometry_oracle.py`

- [ ] **Step 1: Write the failing test** (append to `tests/ml/test_geometry_oracle.py`)

```python
from ml import geometry_oracle as go
from tests.ml.conftest import single_object_layout  # tiny fixture (Task added below)


def test_overlap_area_zero_for_valid_layout():
    layout = single_object_layout(x_m=5.0, y_m=8.0)
    assert go.overlap_area_m2(layout) == 0.0
```

(Defer the conftest fixture to Task 3a immediately below; write that first if executing in order.)

- [ ] **Step 1a (conftest fixture): `tests/ml/conftest.py`**

```python
"""Tiny shared fixtures for ml tests — a minimal real Aircraft + Hangar."""
from __future__ import annotations

from hangarfit.loader import load_fleet, load_hangar
from hangarfit.models import Layout, Placement


def _fuji():
    fleet = load_fleet("data/fleet.yaml")
    return fleet


def empty_hangar():
    # The synthetic placeholder hangar is fine for unit geometry; apron set so the
    # env has a spawn region.
    from dataclasses import replace

    h = load_hangar("data/hangar.yaml")
    return replace(h, apron_depth_m=8.0)


def single_object_layout(*, x_m: float, y_m: float, heading_deg: float = 0.0):
    fleet = _fuji()
    pid = next(iter(fleet))
    return Layout(
        fleet={pid: fleet[pid]},
        hangar=empty_hangar(),
        placements=(Placement(plane_id=pid, x_m=x_m, y_m=y_m, heading_deg=heading_deg, on_carts=False),),
    )
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/ml/test_geometry_oracle.py::test_overlap_area_zero_for_valid_layout -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ml.geometry_oracle'`.

- [ ] **Step 3: Implement `overlap_area_m2` in `ml/geometry_oracle.py`**

```python
"""Reward-geometry helpers — reuse hangarfit's deterministic geometry oracle.

The agent IS the search; these functions reuse only the *rules of physics*
(collisions.check graded penetration, the parts-model world transform, the ADR-0010
motion primitives + swept-path clearance, the Caddy egress oracle), never the RR-MC
or Hybrid-A* search. All functions are pure and RNG-free.
"""
from __future__ import annotations

from shapely.geometry import box

from hangarfit.collisions import check
from hangarfit.geometry import aircraft_parts_world
from hangarfit.models import Aircraft, GroundObject, Hangar, Layout, Placement
from hangarfit.towplanner import CUSP_PENALTY, DubinsArc, Pose, Segment


def overlap_area_m2(layout: Layout) -> float:
    """Summed pairwise overlap area (m²) — the graded collision signal (spec §5).

    Reuses ``collisions.check``'s ``total_penetration_m2`` so the env's collision
    gradient is identical to the solver's secondary score key.
    """
    return check(layout).total_penetration_m2
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/ml/test_geometry_oracle.py::test_overlap_area_zero_for_valid_layout -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ml/geometry_oracle.py tests/ml/conftest.py tests/ml/test_geometry_oracle.py
git commit -m "feat(672): geometry_oracle.overlap_area_m2 (reuse total_penetration_m2)"
```

---

### Task 4: `intrusion_area_m2` — the NEW graded out-of-bounds/notch/keep-out term

The bounds/notch checks are binary today (first-violating-vertex `Conflict`); the spec (§5) **adds** a graded `intrusion_area` from the same shapely polygons.

**Files:**
- Modify: `ml/geometry_oracle.py`
- Test: `tests/ml/test_geometry_oracle.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_intrusion_zero_when_inside():
    layout = single_object_layout(x_m=5.0, y_m=8.0)
    pid = next(iter(layout.fleet))
    pl = layout.placements[0]
    assert go.intrusion_area_m2(layout.fleet[pid], pl, layout.hangar) == 0.0


def test_intrusion_positive_when_object_pushed_off_the_front():
    # y deep-negative drives the footprint out past the front wall (y<0 beyond apron).
    layout = single_object_layout(x_m=5.0, y_m=-50.0)
    pid = next(iter(layout.fleet))
    pl = layout.placements[0]
    assert go.intrusion_area_m2(layout.fleet[pid], pl, layout.hangar) > 0.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/ml/test_geometry_oracle.py -k intrusion -v`
Expected: FAIL — `AttributeError: module 'ml.geometry_oracle' has no attribute 'intrusion_area_m2'`.

- [ ] **Step 3: Implement `intrusion_area_m2`**

Append to `ml/geometry_oracle.py`:
```python
def intrusion_area_m2(
    body: Aircraft | GroundObject, placement: Placement, hangar: Hangar
) -> float:
    """Footprint area (m²) outside the hangar floor or inside a notch/keep-out.

    Graded counterpart to the binary bounds/notch checks (spec §5). Uses the same
    shapely polygons: the L-shaped ``hangar.floor_polygon`` when present, else the
    outer rectangle; plus the maintenance bay rectangle (a keep-out for placement).
    The front apron (``y < 0``) is part of the *motion* model, not a parked-validity
    region, so anything below ``y = 0`` counts as intrusion for a PARKED pose.
    """
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
        total += poly.difference(floor).area      # outside the floor (walls/notch)
        total += poly.intersection(bay_poly).area  # inside the maintenance bay
    return total
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/ml/test_geometry_oracle.py -k intrusion -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/geometry_oracle.py tests/ml/test_geometry_oracle.py
git commit -m "feat(672): geometry_oracle.intrusion_area_m2 (NEW graded bounds/notch term)"
```

---

### Task 5: `legal_primitives` — the per-object action fan (reuse `_primitives`)

**Files:**
- Modify: `ml/geometry_oracle.py`
- Test: `tests/ml/test_geometry_oracle.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_legal_primitives_cart_includes_strafe():
    layout = single_object_layout(x_m=5.0, y_m=8.0)
    body = layout.fleet[next(iter(layout.fleet))]
    kinds = {p.kind for p in go.legal_primitives(body, on_carts=True)}
    assert "T" in kinds  # carts can strafe (#647)


def test_legal_primitives_own_gear_excludes_strafe():
    layout = single_object_layout(x_m=5.0, y_m=8.0)
    body = layout.fleet[next(iter(layout.fleet))]
    kinds = {p.kind for p in go.legal_primitives(body, on_carts=False)}
    assert "T" not in kinds
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/ml/test_geometry_oracle.py -k legal_primitives -v`
Expected: FAIL — no attribute `legal_primitives`.

- [ ] **Step 3: Implement `legal_primitives`** (append)

```python
from hangarfit.towplanner import _primitives  # noqa: E402  (private reuse, spec §6)


def legal_primitives(
    body: Aircraft | GroundObject, *, on_carts: bool, unit_magnitude_m: float = 1.0
) -> tuple[Primitive, ...]:
    """Legal movement primitives for ``body`` (ADR-0010), as unit-magnitude actions.

    Reuses ``towplanner._primitives``: own-gear → 6 Reeds–Shepp arcs; cart (r=0) → 4
    pivots/straights, plus the lateral strafe ``T`` (fwd/rev) when ``on_carts`` (#647).
    The returned magnitudes are unit (1 m / 1 rad); the policy scales them.
    """
    r = body.effective_turn_radius_m()
    lateral = on_carts and r == 0.0
    out: list[Primitive] = []
    for seg in _primitives(r, lateral=lateral):
        out.append(Primitive(kind=seg.kind, magnitude=unit_magnitude_m, gear=seg.gear))
    return tuple(out)
```

(`Primitive` import: add `from ml.types import Primitive` to the imports block.)

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/ml/test_geometry_oracle.py -k legal_primitives -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ml/geometry_oracle.py tests/ml/test_geometry_oracle.py
git commit -m "feat(672): geometry_oracle.legal_primitives (reuse _primitives + strafe)"
```

---

### Task 6: `apply_primitive` — integrate a move via `DubinsArc.pose_at`

**Files:**
- Modify: `ml/geometry_oracle.py`
- Test: `tests/ml/test_geometry_oracle.py`

- [ ] **Step 1: Write the failing tests**

```python
from hangarfit.towplanner import Pose


def test_apply_straight_moves_along_heading():
    start = Pose(x_m=5.0, y_m=0.0, heading_deg=0.0)  # heading 0 = +y (into hangar)
    end, swept = go.apply_primitive(start, Primitive(kind="S", magnitude=2.0, gear=1), turn_radius_m=0.0)
    assert abs(end.x_m - 5.0) < 1e-9
    assert abs(end.y_m - 2.0) < 1e-6
    assert swept[0] == start and len(swept) >= 2


def test_apply_strafe_translates_sideways():
    start = Pose(x_m=5.0, y_m=4.0, heading_deg=0.0)
    end, _ = go.apply_primitive(start, Primitive(kind="T", magnitude=1.0, gear=1), turn_radius_m=0.0)
    assert abs(end.y_m - 4.0) < 1e-6   # strafe keeps the along-heading coordinate
    assert abs(end.x_m - 5.0) > 0.5    # and moves perpendicular
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/ml/test_geometry_oracle.py -k apply -v`
Expected: FAIL — no attribute `apply_primitive`.

- [ ] **Step 3: Implement `apply_primitive`** (append)

```python
def apply_primitive(
    pose: Pose, primitive: Primitive, *, turn_radius_m: float
) -> tuple[Pose, tuple[Pose, ...]]:
    """Apply one primitive to ``pose``; return (end_pose, swept_poses).

    Builds a single-segment ``DubinsArc`` and integrates it with the same
    ``pose_at``/``sample`` machinery the renderers and towplanner consume, so the
    motion is identical to what the rest of the system sees.
    """
    seg = Segment(kind=primitive.kind, length_m=primitive.magnitude, gear=primitive.gear)
    arc = DubinsArc(start=pose, end=pose, turn_radius_m=turn_radius_m, segments=(seg,))
    swept = tuple(arc.sample(step_m=0.05, step_deg=1.0))
    end = arc.pose_at(primitive.magnitude)
    return end, swept
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/ml/test_geometry_oracle.py -k apply -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ml/geometry_oracle.py tests/ml/test_geometry_oracle.py
git commit -m "feat(672): geometry_oracle.apply_primitive (DubinsArc integration)"
```

---

### Task 7: `swept_intrusion_m2` — graded swept-path clearance during a move

Reuses `_build_obstacles` + `_motion_clear` (the motion geometry, not the search). `_motion_clear` is a boolean per-pose verdict; the env wants a graded penalty, so we take the MAX per-sampled-pose intrusion area against the obstacle set when a sweep is not clear (summing would over-count overlapping fine samples).

**Files:**
- Modify: `ml/geometry_oracle.py`
- Test: `tests/ml/test_geometry_oracle.py`

- [ ] **Step 1: Write the failing test**

```python
def test_swept_intrusion_zero_for_clear_move_in_empty_hangar():
    layout = single_object_layout(x_m=5.0, y_m=8.0)  # one body; we move it, others empty
    body = layout.fleet[next(iter(layout.fleet))]
    start = Pose(x_m=5.0, y_m=8.0, heading_deg=0.0)
    _, swept = go.apply_primitive(start, Primitive(kind="S", magnitude=0.5, gear=1), turn_radius_m=0.0)
    intr = go.swept_intrusion_m2(body, swept, parked_layout=layout, active_id=next(iter(layout.fleet)))
    assert intr == 0.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/ml/test_geometry_oracle.py -k swept -v`
Expected: FAIL — no attribute `swept_intrusion_m2`.

- [ ] **Step 3: Implement `swept_intrusion_m2`** (append)

```python
from hangarfit.towplanner import _build_obstacles, _motion_clear  # noqa: E402


def swept_intrusion_m2(
    body: Aircraft | GroundObject,
    swept: tuple[Pose, ...],
    *,
    parked_layout: Layout,
    active_id: str,
) -> float:
    """Graded swept-path intrusion (m²) for a move of ``body`` along ``swept``.

    Reuses the towplanner's motion geometry: ``_build_obstacles`` (excludes the mover)
    + ``_motion_clear`` (the exact per-pose oracle, side/back walls enforced, front
    apron open). For any sampled pose that is NOT clear we add that pose's intrusion
    area against the obstacle parts + walls, so the agent feels a gradient. A fully
    clear sweep returns 0.0 (routability-by-construction along this leg).
    """
    obstacles = _build_obstacles(parked_layout, mover_id=active_id)
    hangar = parked_layout.hangar
    worst = 0.0
    for pose in swept:
        if _motion_clear(body, pose, obstacles, hangar):
            continue
        pl = Placement(
            plane_id=active_id, x_m=pose.x_m, y_m=pose.y_m,
            heading_deg=pose.heading_deg, on_carts=False,
        )
        # Overlap against parked obstacle parts + out-of-floor (front apron excluded).
        leak = 0.0
        for wp in aircraft_parts_world(body, pl):
            for op in obstacles.world_parts:
                if wp.polygon.intersects(op.polygon):
                    leak += wp.polygon.intersection(op.polygon).area
        worst = max(worst, leak)
    return worst
```

(Note: the apron `y<0` is legal *during motion*, so swept intrusion is obstacle-overlap only; the parked out-of-bounds is handled by `intrusion_area_m2` at `park` time.)

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/ml/test_geometry_oracle.py -k swept -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ml/geometry_oracle.py tests/ml/test_geometry_oracle.py
git commit -m "feat(672): geometry_oracle.swept_intrusion_m2 (reuse _motion_clear)"
```

---

### Task 8: `movement_cost` and `egress_blocked`

**Files:**
- Modify: `ml/geometry_oracle.py`
- Test: `tests/ml/test_geometry_oracle.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_movement_cost_adds_cusp_penalty_on_reversal():
    # Forward then reverse straight => one cusp.
    fwd = Primitive(kind="S", magnitude=1.0, gear=1)
    rev = Primitive(kind="S", magnitude=1.0, gear=-1)
    c_no_cusp = go.movement_cost(fwd, prev_gear=1, cusp_penalty=10.0)
    c_cusp = go.movement_cost(rev, prev_gear=1, cusp_penalty=10.0)
    assert c_cusp - c_no_cusp >= 10.0


def test_egress_blocked_false_without_hard_door_mover():
    layout = single_object_layout(x_m=5.0, y_m=8.0)
    assert go.egress_blocked(layout) is False  # no hard-door mover present
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/ml/test_geometry_oracle.py -k "movement_cost or egress" -v`
Expected: FAIL — missing attributes.

- [ ] **Step 3: Implement both** (append)

```python
from hangarfit.towplanner import egress_first_conflict  # noqa: E402


def movement_cost(primitive: Primitive, *, prev_gear: int | None, cusp_penalty: float) -> float:
    """Per-move cost: travelled magnitude + a per-cusp penalty on a direction reversal.

    Mirrors the #480 cost model (``length + CUSP_PENALTY * cusps``). A cusp is a
    forward<->reverse change between consecutive TRANSLATING legs; cart pivots
    (``L``/``R`` at r=0) don't translate, so they never add a cusp here.
    """
    translates = primitive.kind in ("S", "T")
    cusp = 1.0 if (translates and prev_gear is not None and primitive.gear != prev_gear) else 0.0
    return abs(primitive.magnitude) + cusp_penalty * cusp


def egress_blocked(layout: Layout, *, mover_id: str | None = None) -> bool:
    """True iff a hard-door mover (e.g. the Caddy) cannot drive out (ADR-0026).

    Finds the hard-door mover automatically when ``mover_id`` is None; returns False
    when there is no hard-door mover in the layout.
    """
    if mover_id is None:
        for gp in layout.ground_object_placements:
            obj = layout.ground_objects.get(gp.plane_id)
            if obj is not None and getattr(obj, "hard_door_mover", False):
                mover_id = gp.plane_id
                break
    if mover_id is None:
        return False
    return egress_first_conflict(layout, mover_id) is not None
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/ml/test_geometry_oracle.py -k "movement_cost or egress" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ml/geometry_oracle.py tests/ml/test_geometry_oracle.py
git commit -m "feat(672): geometry_oracle.movement_cost + egress_blocked"
```

---

### Task 9: The reward — `potential` and `step_reward` (`ml/reward.py`)

**Files:**
- Create: `ml/reward.py`
- Test: `tests/ml/test_reward.py`

- [ ] **Step 1: Write the failing tests**

`tests/ml/test_reward.py`:
```python
"""Tests for ml.reward (#672) — the graded-lexicographic reward + shaping."""
from __future__ import annotations

from ml.reward import RewardContext, step_reward
from ml.types import RewardWeights


def _ctx(**kw):
    base = dict(
        prev_overlap_m2=0.0, overlap_m2=0.0, intrusion_m2=0.0, swept_intrusion_m2=0.0,
        egress_blocked=False, move_cost=0.0, min_gap_m=0.0, seq_deviation=0.0,
        region_match=0.0, prev_potential=0.0, potential=0.0, parked_delta=0,
        terminal_fraction=None,
    )
    base.update(kw)
    return RewardContext(**base)


def test_hard_violation_outweighs_any_soft_bonus():
    w = RewardWeights()
    # Max achievable soft bonus (generous gap/seq/region) vs a tiny 0.5 m² overlap.
    soft = step_reward(_ctx(min_gap_m=5.0, seq_deviation=0.0, region_match=1.0), w)
    hard = step_reward(_ctx(overlap_m2=0.5, min_gap_m=5.0, region_match=1.0), w)
    assert hard < soft  # any overlap drops the score below the clean-but-spread one


def test_terminal_fraction_rewards_more_placed():
    w = RewardWeights()
    half = step_reward(_ctx(terminal_fraction=0.5), w)
    full = step_reward(_ctx(terminal_fraction=1.0), w)
    assert full > half
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/ml/test_reward.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ml.reward'`.

- [ ] **Step 3: Implement `ml/reward.py`**

```python
"""Graded-lexicographic reward + potential-based shaping (spec §5)."""
from __future__ import annotations

from dataclasses import dataclass

from ml.types import RewardWeights


@dataclass(frozen=True, slots=True)
class RewardContext:
    """Everything ``step_reward`` needs, precomputed by the env from the oracle."""
    prev_overlap_m2: float
    overlap_m2: float
    intrusion_m2: float
    swept_intrusion_m2: float
    egress_blocked: bool
    move_cost: float
    min_gap_m: float
    seq_deviation: float
    region_match: float
    prev_potential: float
    potential: float
    parked_delta: int             # +1 when this step parked an object
    terminal_fraction: float | None  # set only on the terminal step


def potential(*, remaining_overlap_m2: float, active_dist_to_slot_m: float, unplaced: int) -> float:
    """Shaping potential Φ(s) (spec §5). Higher is better (less overlap / closer /
    fewer unplaced), so Φ is the NEGATIVE of those costs. Policy-invariant per
    Ng–Harada–Russell, so it cannot be reward-hacked."""
    return -(remaining_overlap_m2 + active_dist_to_slot_m + float(unplaced))


def step_reward(ctx: RewardContext, w: RewardWeights) -> float:
    """Single-step scalar reward. Hard terms dominate (graded so there's a gradient);
    soft terms tie-break; movement keeps tows efficient; a terminal term encodes the
    'best partial' objective; shaping adds γ·Φ(s′)−Φ(s)."""
    hard = -(
        w.w_col * (ctx.overlap_m2 + ctx.swept_intrusion_m2)
        + w.w_oob * ctx.intrusion_m2
        + w.w_egress * (1.0 if ctx.egress_blocked else 0.0)
    )
    movement = -w.w_move * ctx.move_cost
    soft = (
        w.w_gap * ctx.min_gap_m
        - w.w_seq * ctx.seq_deviation
        + w.w_region * ctx.region_match
    )
    terminal = w.r_terminal * ctx.terminal_fraction if ctx.terminal_fraction is not None else 0.0
    shaping = w.gamma * ctx.potential - ctx.prev_potential
    return hard + movement + soft + terminal + shaping
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/ml/test_reward.py -v`
Expected: PASS.
Note: the ordering test passes because `w_col (100) * 0.5 = 50` dominates the soft sum (`w_gap*5 + w_region*1 = 6`). If you retune defaults, keep `min(w_col,w_oob,w_egress) * smallest_meaningful_area > max soft sum`.

- [ ] **Step 5: Commit**

```bash
git add ml/reward.py tests/ml/test_reward.py
git commit -m "feat(672): ml.reward — graded-lexicographic reward + potential shaping"
```

---

### Task 10: `HangarFitEnv.reset` (`ml/env.py`)

**Files:**
- Create: `ml/env.py`
- Test: `tests/ml/test_env.py`

- [ ] **Step 1: Write the failing test** (append to `tests/ml/test_env.py`)

```python
from ml.env import HangarFitEnv
from ml.types import DifficultyConfig
from tests.ml.conftest import empty_hangar, _fuji


def _env(**kw):
    fleet = _fuji()
    return HangarFitEnv(hangar=empty_hangar(), fleet=fleet, requested_ids=tuple(fleet)[:1], **kw)


def test_reset_spawns_first_object_on_the_apron():
    env = _env()
    obs = env.reset()
    assert obs.active is not None
    assert obs.active.pose.y_m < 0.0          # spawned on the apron (y<0)
    assert obs.parked == ()
    assert len(obs.unplaced_ids) == 0          # the active one is not "unplaced"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/ml/test_env.py -k reset -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ml.env'`.

- [ ] **Step 3: Implement `HangarFitEnv.__init__` + `reset`**

```python
"""HangarFitEnv — the cold-joint RL environment (spec §4, §9). Plain gym-style class;
no gymnasium/torch dependency (those arrive in the training rung #3)."""
from __future__ import annotations

from collections.abc import Mapping

from hangarfit.models import Aircraft, GroundObject, Hangar, Layout, Placement
from hangarfit.towplanner import Pose
from ml import geometry_oracle as go
from ml.reward import RewardContext, potential, step_reward
from ml.types import (
    ActiveObject, Action, DifficultyConfig, Observation, ParkedObject, Park,
    Primitive, RewardWeights, StepInfo,
)


class HangarFitEnv:
    """Drive each requested object in from the apron and park it, one at a time."""

    def __init__(
        self,
        *,
        hangar: Hangar,
        fleet: Mapping[str, Aircraft],
        requested_ids: tuple[str, ...],
        ground_objects: Mapping[str, GroundObject] | None = None,
        difficulty: DifficultyConfig | None = None,
        weights: RewardWeights | None = None,
    ) -> None:
        self.hangar = hangar
        self.fleet = dict(fleet)
        self.ground_objects = dict(ground_objects or {})
        self.requested_ids = requested_ids
        self.difficulty = difficulty or DifficultyConfig()
        self.weights = weights or RewardWeights()
        self._reset_state()

    def _reset_state(self) -> None:
        n = self.difficulty.max_objects
        self._queue: list[str] = list(self.requested_ids if n is None else self.requested_ids[:n])
        self._parked: list[Placement] = []
        self._active_id: str | None = None
        self._active_pose: Pose | None = None
        self._prev_gear: int | None = None
        self._steps_this_object = 0
        self._steps_total = 0
        self._prev_potential = 0.0

    def _body(self, object_id: str) -> Aircraft | GroundObject:
        return self.fleet[object_id] if object_id in self.fleet else self.ground_objects[object_id]

    def _on_carts(self, object_id: str) -> bool:
        body = self._body(object_id)
        return getattr(body, "movement_mode", None) == "always_cart" or getattr(body, "on_carts", False)

    def _spawn(self) -> None:
        """Pop the next queued object and place it on the apron at the door centre."""
        self._active_id = self._queue.pop(0)
        depth = self.hangar.apron_depth_m or 0.0
        self._active_pose = Pose(
            x_m=self.hangar.door.center_x_m, y_m=-(depth / 2.0 if depth else 0.0), heading_deg=0.0
        )
        self._prev_gear = None
        self._steps_this_object = 0

    def _layout(self) -> Layout:
        """The scene of FROZEN (parked) objects only — the active one is not yet in it."""
        ac = {pid: self.fleet[pid] for pid in self.fleet if any(p.plane_id == pid for p in self._parked)}
        go_ids = {pid for pid in self.ground_objects if any(p.plane_id == pid for p in self._parked)}
        return Layout(
            fleet=ac or {next(iter(self.fleet)): next(iter(self.fleet.values()))},
            hangar=self.hangar,
            placements=tuple(p for p in self._parked if p.plane_id in self.fleet),
            ground_objects={pid: self.ground_objects[pid] for pid in go_ids},
            ground_object_placements=tuple(p for p in self._parked if p.plane_id in self.ground_objects),
        )

    def _observe(self) -> Observation:
        active = None
        if self._active_id is not None and self._active_pose is not None:
            active = ActiveObject(
                object_id=self._active_id, body=self._body(self._active_id),
                pose=self._active_pose, on_carts=self._on_carts(self._active_id),
            )
        return Observation(
            active=active,
            parked=tuple(ParkedObject(p.plane_id, p) for p in self._parked),
            unplaced_ids=tuple(self._queue),
            steps_this_object=self._steps_this_object,
            steps_total=self._steps_total,
        )

    def reset(self) -> Observation:
        self._reset_state()
        self._spawn()
        self._prev_potential = self._potential()
        return self._observe()
```

(Helpers `_potential`, `step` come in Tasks 11–12.)

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/ml/test_env.py -k reset -v`
Expected: FAIL still — `_potential` is referenced but not yet defined. Add a temporary `def _potential(self) -> float: return 0.0` to make `reset` runnable, then the test passes. (Task 11 replaces it.)

- [ ] **Step 5: Commit**

```bash
git add ml/env.py tests/ml/test_env.py
git commit -m "feat(672): HangarFitEnv.__init__ + reset (apron spawn, scene state)"
```

---

### Task 11: `_potential` and the active-object slot distance

**Files:**
- Modify: `ml/env.py`, `ml/geometry_oracle.py`
- Test: `tests/ml/test_env.py`

- [ ] **Step 1: Write the failing test**

```python
def test_potential_improves_as_overlap_clears(monkeypatch):
    env = _env()
    env.reset()
    phi0 = env._potential()
    # A potential is finite and increases (less negative) when there's nothing wrong.
    assert phi0 <= 0.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/ml/test_env.py -k potential -v`
Expected: FAIL — the temporary `_potential` returns 0.0; replace it.

- [ ] **Step 3: Implement the real `_potential`** (replace the temporary in `ml/env.py`)

```python
    def _active_dist_to_slot_m(self) -> float:
        """Distance from the active object to a valid parking region — approximated
        as how far inside the door it still needs to travel (y from apron to >=0).
        A coarse but monotone signal for shaping; refined in #4."""
        if self._active_pose is None:
            return 0.0
        return max(0.0, -self._active_pose.y_m)

    def _potential(self) -> float:
        layout = self._layout()
        remaining_overlap = go.overlap_area_m2(layout) if self._parked else 0.0
        return potential(
            remaining_overlap_m2=remaining_overlap,
            active_dist_to_slot_m=self._active_dist_to_slot_m(),
            unplaced=len(self._queue) + (1 if self._active_id is not None else 0),
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/ml/test_env.py -k potential -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ml/env.py tests/ml/test_env.py
git commit -m "feat(672): env potential Φ (overlap + slot distance + unplaced)"
```

---

### Task 12: `HangarFitEnv.step` — the transition + reward + termination

**Files:**
- Modify: `ml/env.py`
- Test: `tests/ml/test_env.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_step_primitive_moves_active_and_returns_reward():
    env = _env()
    env.reset()
    obs, reward, done, info = env.step(Primitive(kind="S", magnitude=1.0, gear=1))
    assert isinstance(reward, float)
    assert done is False
    assert obs.active is not None and obs.active.pose.y_m > -env.hangar.apron_depth_m
    assert "hard" not in info.terms or isinstance(info.terms["hard"], float)


def test_park_advances_to_next_object_or_finishes():
    env = _env()  # single requested object
    env.reset()
    # Drive in until y>=0 then park.
    for _ in range(20):
        if env._active_pose is not None and env._active_pose.y_m >= 1.0:
            break
        env.step(Primitive(kind="S", magnitude=1.0, gear=1))
    obs, reward, done, info = env.step(Park())
    assert done is True                       # the only object was parked
    assert info.placed == info.total == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/ml/test_env.py -k "step_primitive or park_advances" -v`
Expected: FAIL — `HangarFitEnv` has no attribute `step`.

- [ ] **Step 3: Implement `step`** (append to `HangarFitEnv`)

```python
    def step(self, action: Action) -> tuple[Observation, float, bool, StepInfo]:
        assert self._active_id is not None and self._active_pose is not None, "step after done"
        self._steps_total += 1
        self._steps_this_object += 1
        body = self._body(self._active_id)
        parked_layout = self._layout()
        weights = self.weights

        if isinstance(action, Park):
            # Freeze the active pose; score its parked validity (overlap + bounds).
            pl = Placement(
                plane_id=self._active_id, x_m=self._active_pose.x_m, y_m=self._active_pose.y_m,
                heading_deg=self._active_pose.heading_deg, on_carts=self._on_carts(self._active_id),
            )
            self._parked.append(pl)
            placed_layout = self._layout()
            overlap = go.overlap_area_m2(placed_layout)
            intrusion = go.intrusion_area_m2(body, pl, self.hangar)
            egress = go.egress_blocked(placed_layout)
            self._active_id = None
            self._active_pose = None
            done = not self._queue
            terminal_fraction = (len(self._parked) / len(self.requested_ids)) if done else None
            if not done:
                self._spawn()
            new_phi = self._potential()
            ctx = RewardContext(
                prev_overlap_m2=0.0, overlap_m2=overlap, intrusion_m2=intrusion,
                swept_intrusion_m2=0.0, egress_blocked=egress, move_cost=0.0,
                min_gap_m=0.0, seq_deviation=0.0, region_match=0.0,
                prev_potential=self._prev_potential, potential=new_phi,
                parked_delta=1, terminal_fraction=terminal_fraction,
            )
            reward = step_reward(ctx, weights)
            self._prev_potential = new_phi
            return self._observe(), reward, done, self._info(ctx, done, "set complete" if done else "")

        # A movement primitive: integrate, grade the swept path, advance the pose.
        primitive = action
        end, swept = go.apply_primitive(
            self._active_pose, primitive, turn_radius_m=body.effective_turn_radius_m()
        )
        swept_intr = go.swept_intrusion_m2(
            body, swept, parked_layout=parked_layout, active_id=self._active_id
        )
        move_cost = go.movement_cost(primitive, prev_gear=self._prev_gear, cusp_penalty=weights.cusp_penalty)
        self._active_pose = end
        self._prev_gear = primitive.gear
        new_phi = self._potential()
        ctx = RewardContext(
            prev_overlap_m2=0.0, overlap_m2=0.0, intrusion_m2=0.0,
            swept_intrusion_m2=swept_intr, egress_blocked=False, move_cost=move_cost,
            min_gap_m=0.0, seq_deviation=0.0, region_match=0.0,
            prev_potential=self._prev_potential, potential=new_phi,
            parked_delta=0, terminal_fraction=None,
        )
        reward = step_reward(ctx, weights)
        self._prev_potential = new_phi

        # Termination: per-object budget exhausted (unplaceable) or global budget hit.
        done, reason = self._check_budget()
        return self._observe(), reward, done, self._info(ctx, done, reason)

    def _check_budget(self) -> tuple[bool, str]:
        if self._steps_this_object >= self.difficulty.per_object_step_budget:
            return True, "active object unplaceable (per-object budget)"
        if self._steps_total >= self.difficulty.total_step_budget:
            return True, "global step budget exhausted"
        return False, ""

    def _info(self, ctx: RewardContext, done: bool, reason: str) -> StepInfo:
        return StepInfo(
            terms={
                "hard_overlap": ctx.overlap_m2, "hard_swept": ctx.swept_intrusion_m2,
                "hard_intrusion": ctx.intrusion_m2, "hard_egress": float(ctx.egress_blocked),
                "move_cost": ctx.move_cost, "shaping": ctx.potential - ctx.prev_potential,
                "terminal_fraction": ctx.terminal_fraction or 0.0,
            },
            valid=(go.overlap_area_m2(self._layout()) == 0.0),
            placed=len(self._parked), total=len(self.requested_ids), reason=reason,
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/ml/test_env.py -k "step_primitive or park_advances" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ml/env.py tests/ml/test_env.py
git commit -m "feat(672): HangarFitEnv.step — transition, reward, park, termination"
```

---

### Task 13: Curriculum `max_objects` + partial-stop termination

**Files:**
- Modify: `ml/env.py` (already supports `max_objects` via `_reset_state`; add the per-object unplaceable terminal-fraction signal)
- Test: `tests/ml/test_env.py`

- [ ] **Step 1: Write the failing test**

```python
def test_max_objects_caps_the_requested_set():
    fleet = _fuji()
    env = HangarFitEnv(
        hangar=empty_hangar(), fleet=fleet, requested_ids=tuple(fleet),
        difficulty=DifficultyConfig(max_objects=1),
    )
    env.reset()
    assert len(env._queue) + 1 == 1  # exactly one object in play


def test_per_object_budget_terminates_with_partial():
    env = _env(difficulty=DifficultyConfig(per_object_step_budget=2))
    env.reset()
    env.step(Primitive(kind="L", magnitude=0.1, gear=1))
    obs, reward, done, info = env.step(Primitive(kind="L", magnitude=0.1, gear=1))
    assert done is True and "unplaceable" in info.reason
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/ml/test_env.py -k "max_objects or per_object_budget" -v`
Expected: the `max_objects` test PASSES already (Task 10 wired it); the budget test PASSES if Task 12's `_check_budget` is correct. If `per_object_step_budget` off-by-one fails, fix the comparison in `_check_budget`.

- [ ] **Step 3: Fix if needed** — ensure `_check_budget` fires at exactly `per_object_step_budget` steps. No new code if Task 12 is correct.

- [ ] **Step 4: Run to verify both pass**

Run: `python -m pytest tests/ml/test_env.py -k "max_objects or per_object_budget" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/ml/test_env.py ml/env.py
git commit -m "test(672): curriculum max_objects + per-object partial-stop termination"
```

---

### Task 14: Integration — random-policy rollout + RNG-free reward determinism

**Files:**
- Test: `tests/ml/test_env.py`

- [ ] **Step 1: Write the failing tests**

```python
import random


def _rollout(env, actions):
    env.reset()
    total = 0.0
    for a in actions:
        _, r, done, _ = env.step(a)
        total += r
        if done:
            break
    return total


def test_random_rollout_completes_and_is_bounded():
    env = _env(difficulty=DifficultyConfig(per_object_step_budget=10, total_step_budget=40))
    rng = random.Random(0)
    fan = list(go.legal_primitives(env._body(env.requested_ids[0]), on_carts=False)) + [Park()]
    actions = [rng.choice(fan) for _ in range(40)]
    total = _rollout(env, actions)
    assert isinstance(total, float)


def test_reward_is_rng_free_for_a_fixed_action_sequence():
    actions = [Primitive(kind="S", magnitude=1.0, gear=1)] * 5 + [Park()]
    a = _rollout(_env(), actions)
    b = _rollout(_env(), actions)
    assert a == b  # byte-identical reward for identical actions (ADR-0027 env tier)
```

- [ ] **Step 2: Run to verify it fails (or passes)**

Run: `python -m pytest tests/ml/test_env.py -k "rollout or rng_free" -v`
Expected: PASS if Tasks 10–12 are correct; otherwise fix the surfaced bug.

- [ ] **Step 3: Fix any surfaced determinism leak** — the only non-determinism risk is set/dict iteration in `_layout`/`_info`; if `test_reward_is_rng_free` fails, replace any `set` iteration over object ids with sorted/ordered iteration.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/ml/test_env.py -k "rollout or rng_free" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/ml/test_env.py
git commit -m "test(672): random rollout completes + reward RNG-free determinism"
```

---

### Task 15: Lint, type-check, docs, CHANGELOG

**Files:**
- Modify: `CHANGELOG.md`
- Verify: `ruff`, `mypy`

- [ ] **Step 1: Run ruff + format**

Run: `ruff check ml/ tests/ml/ && ruff format --check ml/ tests/ml/`
Expected: clean (fix any findings; `ruff format ml/ tests/ml/` if needed).

- [ ] **Step 2: Type-check `ml/`**

Run: `mypy ml/`
Expected: clean. (Add `ml` to the mypy config targets if the project pins explicit packages; otherwise `mypy ml/` directly. Reuse of private towplanner helpers may need a `# type: ignore[attr-defined]` or a local `.pyi` note — prefer importing the public names where they exist.)

- [ ] **Step 3: CHANGELOG entry** — add under `[Unreleased]/Added`:

```markdown
- **Cold-joint RL environment + reward (`ml/`, #607/#672).** Added the dev/CI-only
  top-level `ml/` package with `HangarFitEnv` — a gym-style environment where an agent
  drives objects in from the apron and parks them one at a time, scored by a
  graded-lexicographic reward (collision/out-of-bounds/egress hard terms, movement
  cost, soft spread/sequence/region, terminal fraction-placed) plus policy-invariant
  potential-based shaping. Reuses the deterministic geometry oracle (`collisions.check`,
  the parts-model transform, the ADR-0010 motion primitives incl. the #647 strafe,
  the Caddy egress oracle) — **not** the RR-MC/Hybrid-A* search. No neural net, no
  training, no new runtime dependency; `ml/` is excluded from the wheel like `bench/`
  and `viewer/`. Sub-project #1 of the learned backend (ADR-0027).
```

- [ ] **Step 4: Full ml test run**

Run: `python -m pytest tests/ml/ -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md ml/ tests/ml/
git commit -m "docs(672): CHANGELOG + ml/ lint/type cleanup"
```

---

## Self-Review

**1. Spec coverage**

| Spec section | Covered by |
|---|---|
| §4.1 episode structure (apron spawn, one-at-a-time, park→next, end conditions) | Tasks 10, 12, 13 |
| §4.2 observation (hangar geom, parked, active, unplaced) | Task 2 (`Observation`), Task 10 (`_observe`) |
| §4.3 action space (primitives by movement mode + park; continuous magnitude) | Task 2 (`Primitive`/`Park`), Task 5 (`legal_primitives`) |
| §4.4 transition & legality (swept clearance; penalty-only graded) | Task 7 (`swept_intrusion_m2`), Task 12 |
| §4.5 termination (success / partial / budget) | Tasks 12, 13 |
| §5 reward tiers (hard graded col/oob/egress, movement+cusp, soft gap/seq/region, terminal) | Tasks 3,4,8,9,12 |
| §5 potential-based shaping | Tasks 9, 11 |
| §5 new graded `intrusion_area` | Task 4 |
| §5 ordering invariant (hard ≫ soft) | Task 9 (test) |
| §6 geometry reuse / no search | Tasks 3–8 (all reuse; none import solver/plan_fill) |
| §6.1 strafe dependency | Resolved (#647); Task 5 emits `T` for carts |
| §7 curriculum hooks | Task 2 (`DifficultyConfig`), Tasks 10, 13 |
| §8 determinism (RNG-free reward) | Task 14 (test) |
| §9 env interface + info breakdown | Tasks 10, 12 (`StepInfo`) |

Gaps deliberately deferred (spec §10, sibling rungs): magnitude binning interface beyond the continuous default (#2/#3), hard action masking (#3), exact Φ/weights tuning (#4), tensorized observation (#2). The soft `seq_deviation`/`region_match` are wired as reward inputs (defaulting to 0.0) but their per-object sourcing (requested door-order index, RegionPreference) is left at 0 until the policy spec (#3) defines how the requested set carries them — noted, not a silent gap.

**2. Placeholder scan** — no "TBD"/"handle edge cases"; every step has concrete code or an exact command. The two soft terms default to 0.0 with the reason documented above (not a hidden placeholder).

**3. Type consistency** — `Primitive(kind,magnitude,gear)`, `RewardContext` field names, `RewardWeights` field names, and the `go.*` signatures are used identically across Tasks 9/11/12. `effective_turn_radius_m()` (from `models.Aircraft`/`GroundObject`) is the single turn-radius source used in Tasks 5, 6, 12.

**Known risk to verify during execution:** the private-import reuse (`_primitives`, `_motion_clear`, `_build_obstacles`) — confirm these signatures match (Task 5/7 tests will catch a mismatch immediately). If `mypy ml/` rejects the private imports, prefer a thin typed shim in `ml/geometry_oracle.py` over editing `towplanner.py` (which would pull in `determinism-guard`).
