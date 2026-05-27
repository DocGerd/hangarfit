# Bound-Aware Tow-Path Planner (Hybrid-A*) Implementation Plan

> **⚠️ Acceptance criterion revised by #197 (2026-05-26).** This plan assumed
> that with `plan_path` in place the solver fixture matrix would return to
> `found` **with bundled, exact-validated plans** (see the "Definition of done"
> / final acceptance steps). That proved too optimistic: even bound-aware
> Hybrid-A* cannot route dense multi-plane fills in v1 (the 6-plane fixtures and
> `layouts/example.yaml` are un-towable; spike Risk #1). #197 therefore reversed
> the "fail-whole-solve / `no_feasible_plan`" decision to **best-effort
> enrichment** — the matrix returns `found` with `plans[i] = None` where the
> planner can't route a layout, and the `no_feasible_plan` status was dropped.
> Hybrid-A* is still a real win (it routes turned/wide-wing single cases that
> single-Dubins could not); dense-fill routing remains v2 (RRT-Connect). Treat
> any `no_feasible_plan` reference below as historical.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-shot `plan_dubins` call in `plan_fill` with a deterministic Hybrid-A* search (`plan_path`) that finds an in-bounds, obstacle-free multi-segment tow path from the door-cone entry pose to the target slot, so `solve` returns genuinely tow-able layouts (#222, unblocks #197).

**Architecture:** Hybrid-A* over continuous `(x, y, heading)` state with a fixed `{L, S, R}` (own-gear) / pivot+straight (cart) primitive fan, `(x, y, θ)` grid binning, a Euclidean admissible heuristic, and an analytic-expansion shortcut (try a direct Dubins-to-goal at every node). The existing front-gap-exempt `path_first_conflict` is the per-edge validity oracle. The found path is emitted as a single `DubinsArc` with N segments — no `Move`/`MovesPlan` change. Performance is staged: build correct-but-simple first (Tasks 1–4, oracle = `path_first_conflict`), then a fast precomputed-obstacle `_motion_clear` with an exact-oracle safety net (Task 5), gated by a fixture-matrix performance test (Task 6).

**Tech Stack:** Python 3.12, `heapq`, frozen `@dataclass(slots=True)`, `math` (no numpy), Shapely (only via the existing `collisions.check` / `aircraft_parts_world`), `pytest`. RNG-free (ADR-0003).

**Design spec:** `docs/superpowers/specs/2026-05-25-bound-aware-towpath-planner-design.md`.

---

## Setup (before Task 1)

Branch off the latest `develop`, carrying the already-written front-gap exemption (currently uncommitted in the `feature/197-solve-bundled-movesplan` working tree: the `_mover_motion_bounds_conflict` helper + `path_first_conflict` change in `src/hangarfit/towplanner.py` and the two new tests in `tests/test_towplanner_motion.py`). If those edits are NOT present in your working tree, Task 1 reproduces them in full.

```bash
git switch develop && git pull --ff-only
git switch -c feature/222-bound-aware-towpath-planner
```

`python` is not on PATH — use bare `pytest` / `mypy` / `ruff`. A PostToolUse hook auto-runs pytest on edits under `src/hangarfit/` or `tests/`; that is expected.

---

## Conventions you must honor

- **Determinism (ADR-0003):** no RNG in the towplanner. The priority queue is tie-broken by a monotonic insertion counter; the primitive fan has a fixed order `(L, S, R)`. Same inputs → byte-identical `DubinsArc`.
- **Heading convention (ADR-0002):** poses use compass headings; integration goes through the existing `DubinsArc.pose_at` (it converts via `compass_to_math_rad`). Do not add new trig.
- **Cart = own-gear with `r = 0` (ADR-0007):** carts pivot in place; one motion model, no cart special-case in the search body beyond the primitive fan.
- **Front-gap exemption:** the mover may occupy `y < 0` in front of the door during transit; side/back walls, bay, and placed-plane overlap stay enforced (Task 1).
- **Gates after each task:** `ruff check src/ tests/ && ruff format --check src/ tests/ && mypy src/hangarfit/`.
- **Module:** everything lives in `src/hangarfit/towplanner.py` (cohesive with the primitives it reuses: `Pose`, `Segment`, `DubinsArc`, `plan_dubins`, `path_first_conflict`, `entry_pose`, `back_first_order`, `plan_fill`).

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `src/hangarfit/towplanner.py` (modify) | Front-gap exemption; tuning constants; `_primitives`, `_step_pose`, `_seg_cost`, `_cell`; `plan_path` Hybrid-A*; `_motion_clear` + precomputed obstacles; `plan_fill` uses `plan_path` | 1–5 |
| `tests/test_towplanner_motion.py` (modify) | Front-gap exemption tests | 1 |
| `tests/test_towplanner_search.py` (create) | `plan_path`: clear shot, wide-wing maneuver, boxed-in bail, determinism, in-bounds invariant, canary | 3, 5 |
| `tests/test_towplanner_fill.py` (modify) | `plan_fill` happy path on realistic (origin-spanning) geometry | 4 |
| `tests/test_solver_search.py`, `tests/test_solver_towplanner.py`, `tests/test_cli*.py` (verify/adjust) | Fixture matrix returns to `found` with exact-validated bundled plans | 6 |
| `tests/test_towplanner_perf.py` (create) | `slow`-marked: `solve` over the fixture matrix completes within budget | 6 |

`towplanner.py` grows to ~700 lines; acceptable for one cohesive module. A future split (`towplanner_search.py`) is possible but deferred (would need a lazy import to avoid a cycle).

---

## Task 1: Front-gap exemption for the in-transit mover

**Files:**
- Modify: `src/hangarfit/towplanner.py` (add `from hangarfit.geometry import aircraft_parts_world`; add `_mover_motion_bounds_conflict`; change `path_first_conflict`)
- Test: `tests/test_towplanner_motion.py`

**Why:** `entry_pose` puts the plane reference at `y = 0`, so a fuselage centered on the reference has its rear half at `y < 0` (the plane straddles the door while being towed in). `path_first_conflict` samples the start pose first and reused the static `collisions.check`, which rejects any `y < 0` vertex — so every plane failed on its first sample. The front gap must be exempt for the mover during motion; side/back walls stay enforced.

- [ ] **Step 1: Add the failing tests** (if not already present from the prior working tree)

In `tests/test_towplanner_motion.py`, add a helper and two tests:

```python
def _spanning_fuselage() -> Part:
    """A 4.0 m fuselage centered on the plane origin. At a ``y = 0`` entry pose
    (heading 0) its rear half sits at ``y < 0`` — the plane straddles the door
    line, exactly as a plane being towed *through* the door does. Unlike the
    forward-mounted :func:`_fuselage_box`, this exercises the front-door gap."""
    return Part(
        kind="fuselage",
        length_m=4.0,
        width_m=0.6,
        offset_x_m=0.0,
        offset_y_m=0.0,
        angle_deg=0.0,
        z_bottom_m=0.0,
        z_top_m=1.0,
    )


def _spanning_plane(plane_id: str, *, turn_radius_m: float = 4.0) -> Aircraft:
    """An own-gear plane whose fuselage spans the origin (rear protrudes to
    ``y < 0`` at a ``y = 0`` entry pose)."""
    return Aircraft(
        id=plane_id,
        name=f"Spanning {plane_id}",
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_own_gear",
        turn_radius_m=turn_radius_m,
        measured=False,
        parts=(_spanning_fuselage(),),
    )


def test_front_door_protrusion_is_exempt_for_mover(simple_hangar: Hangar) -> None:
    # Spans origin -> y = -2 at the y = 0 entry pose; front gap exempt during
    # motion (#222). The static oracle WOULD flag the rear vertex; this is the
    # regression the universal-no_feasible_plan bug was hiding behind.
    fleet = {"B": _spanning_plane("B")}
    placed = Layout(fleet=fleet, hangar=simple_hangar, placements=())
    arc = plan_dubins(Pose(10.0, 0.0, 0.0), Pose(10.0, 10.0, 0.0), turn_radius_m=4.0)
    assert path_first_conflict(arc, fleet["B"], mover_on_carts=False, placed=placed) is None


def test_side_wall_still_enforced_for_mover_during_motion(simple_hangar: Hangar) -> None:
    # Exemption removes ONLY the front (y < 0) boundary. heading 90 => nose +x,
    # so the 4 m fuselage spans x in [-2, 2] at px = 0; rear vertex x = -2 < 0
    # with y ~ 5 >= 0 -> still a side-wall conflict.
    fleet = {"B": _spanning_plane("B")}
    placed = Layout(fleet=fleet, hangar=simple_hangar, placements=())
    arc = plan_dubins(Pose(0.0, 5.0, 90.0), Pose(2.0, 5.0, 90.0), turn_radius_m=4.0)
    conflict = path_first_conflict(arc, fleet["B"], mover_on_carts=False, placed=placed)
    assert conflict is not None
    assert conflict.kind == "hangar_bounds"
    assert "B" in conflict.planes
```

- [ ] **Step 2: Run to verify the regression test fails**

Run: `pytest tests/test_towplanner_motion.py -k "front_door_protrusion or side_wall_still" -v`
Expected: `test_front_door_protrusion_is_exempt_for_mover` FAILS (`hangar_bounds` on `vertex (10.300, -2.000)`); the side-wall test PASSES.

- [ ] **Step 3: Add the import and the motion-bounds helper**

In `src/hangarfit/towplanner.py`, after `from hangarfit.collisions import check as _check`:

```python
from hangarfit.geometry import aircraft_parts_world
```

Add this helper just before `def path_first_conflict`:

```python
def _mover_motion_bounds_conflict(
    mover: Aircraft, placement: Placement, hangar: Hangar
) -> Conflict | None:
    """First side/back-wall bounds violation for a plane *in transit*, else ``None``.

    **Front-gap exemption (#222):** a plane being towed through the door
    legitimately protrudes in front of it (``y < 0`` — the conceptual apron,
    spike Q6). So — unlike the static :func:`hangarfit.collisions.check` oracle,
    which forbids ``y < 0`` — the front wall is NOT enforced on the mover
    mid-motion. The side walls (``0 ≤ x ≤ width``) and the back wall
    (``y ≤ length``) still are; the mover's final slot is itself a valid static
    placement, so full bounds hold at rest. Reuses the canonical
    :func:`~hangarfit.geometry.aircraft_parts_world` transform rather than
    re-deriving geometry — the determinant-(-1) trap lives there (ADR-0002).
    """
    for world_part in aircraft_parts_world(mover, placement):
        for x, y in list(world_part.polygon.exterior.coords)[:-1]:
            # Static rule is `0<=x<=width and 0<=y<=length`; only relaxation is
            # dropping the `0<=y` front-wall lower bound.
            if x < 0.0 or x > hangar.width_m or y > hangar.length_m:
                return Conflict.single(
                    kind="hangar_bounds",
                    plane=mover.id,
                    detail=(
                        f"part {world_part.kind!r} vertex ({x:.3f}, {y:.3f}) "
                        f"outside hangar side/back walls during tow "
                        f"(0..{hangar.width_m:g} x ..{hangar.length_m:g})"
                    ),
                )
    return None
```

- [ ] **Step 4: Wire it into `path_first_conflict`**

Replace the sampling loop body so the mover's hangar bounds go through the relaxed helper and the oracle's mover `hangar_bounds` verdict is skipped:

```python
    for pose in arc.sample(step_m=step_m, step_deg=step_deg):
        moving = Placement(mover.id, pose.x_m, pose.y_m, pose.heading_deg, on_carts=mover_on_carts)
        # Mover hangar bounds: front-gap-exempt (a plane towed in straddles the
        # door at y < 0). Side/back walls still bite.
        bounds_conflict = _mover_motion_bounds_conflict(mover, moving, placed.hangar)
        if bounds_conflict is not None:
            return bounds_conflict
        sample_layout = Layout(
            fleet=placed.fleet,
            hangar=placed.hangar,
            placements=(*placed.placements, moving),
            maintenance_plane=placed.maintenance_plane,
        )
        for conflict in _check(sample_layout).conflicts:
            if mover.id not in conflict.planes:
                continue
            # Mover bounds handled by the relaxed rule above — skip the oracle's
            # mover hangar_bounds so a legitimate door protrusion is not blamed.
            if conflict.kind == "hangar_bounds":
                continue
            return conflict
    return None
```

Also update the `path_first_conflict` docstring to note the front-gap exemption (the oracle is reused for parts-overlap and bay-intrusion, but mover hangar bounds use the relaxed `_mover_motion_bounds_conflict`).

- [ ] **Step 5: Run to verify all motion tests pass**

Run: `pytest tests/test_towplanner_motion.py -v`
Expected: PASS (all, including the existing back-wall `test_hangar_bounds_during_motion_names_mover` and the two new tests).

- [ ] **Step 6: Gates + commit**

```bash
ruff check src/ tests/ && ruff format src/ tests/ && mypy src/hangarfit/
git add src/hangarfit/towplanner.py tests/test_towplanner_motion.py
git commit -m "fix(towplanner): front-gap exemption for in-transit mover bounds (#222)

$(printf 'Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>')"
```

---

## Task 2: Motion-primitive fan + search helpers

**Files:**
- Modify: `src/hangarfit/towplanner.py` (tuning constants; `_primitives`, `_step_pose`, `_seg_cost`, `_cell`)
- Test: `tests/test_towplanner_search.py` (create)

**Why:** Hybrid-A* expands a fixed fan of short motion primitives from each pose. These pure helpers are unit-testable in isolation before the search wires them together.

- [ ] **Step 1: Write the failing helper tests**

Create `tests/test_towplanner_search.py`:

```python
import math

import pytest

from hangarfit.towplanner import (
    Pose,
    Segment,
    _cell,
    _primitives,
    _seg_cost,
    _step_pose,
)


def test_primitives_own_gear_are_left_straight_right_in_order() -> None:
    segs = _primitives(turn_radius_m=4.0)
    assert [s.kind for s in segs] == ["L", "S", "R"]
    # Each is a positive-length short step.
    assert all(s.length_m > 0.0 for s in segs)


def test_primitives_cart_are_pivot_straight_pivot_in_order() -> None:
    segs = _primitives(turn_radius_m=0.0)
    assert [s.kind for s in segs] == ["L", "S", "R"]
    # Pivots encode radians; the straight encodes metres.
    assert segs[1].kind == "S"


def test_step_pose_straight_advances_along_heading() -> None:
    # heading 0 => +y. A straight step moves +y by its length.
    p = _step_pose(Pose(3.0, 1.0, 0.0), Segment("S", 0.5), turn_radius_m=4.0)
    assert p.x_m == pytest.approx(3.0, abs=1e-9)
    assert p.y_m == pytest.approx(1.5, abs=1e-9)
    assert p.heading_deg == pytest.approx(0.0, abs=1e-9)


def test_step_pose_cart_pivot_rotates_in_place() -> None:
    # r == 0 turn: position held, heading changes by the pivot radians.
    seg = Segment("R", math.radians(15.0))
    p = _step_pose(Pose(3.0, 1.0, 0.0), seg, turn_radius_m=0.0)
    assert p.x_m == pytest.approx(3.0, abs=1e-9)
    assert p.y_m == pytest.approx(1.0, abs=1e-9)
    # Compass CW-positive: an "R" pivot of +15 deg increases the compass heading.
    assert p.heading_deg == pytest.approx(15.0, abs=1e-6)


def test_seg_cost_counts_translation_plus_turn_penalty() -> None:
    # Straight: pure translation, no turn penalty.
    assert _seg_cost(Segment("S", 2.0), turn_radius_m=4.0) == pytest.approx(2.0)
    # r>0 turn of arc length L: translation L + penalty * (L / r) radians.
    c = _seg_cost(Segment("L", 2.0), turn_radius_m=4.0)
    assert c == pytest.approx(2.0 + 0.1 * (2.0 / 4.0))


def test_cell_bins_pose_into_grid() -> None:
    # Same 0.5 m / 15 deg cell for nearby poses; different for far ones.
    assert _cell(Pose(3.01, 1.02, 1.0)) == _cell(Pose(2.99, 0.98, 2.0))
    assert _cell(Pose(3.0, 1.0, 0.0)) != _cell(Pose(9.0, 9.0, 180.0))
    # Heading wraps: 359 deg and 1 deg share the 0-bin? No — bin width 15 deg,
    # 359 -> bin 24 % 24 = 0, 1 -> bin 0. They share bin 0.
    assert _cell(Pose(3.0, 1.0, 359.0))[2] == _cell(Pose(3.0, 1.0, 1.0))[2]
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_towplanner_search.py -v`
Expected: FAIL — `ImportError: cannot import name '_primitives'` (etc.).

- [ ] **Step 3: Implement the constants and helpers**

In `src/hangarfit/towplanner.py`, add `import heapq` to the stdlib imports. After `path_first_conflict` / `_mover_motion_bounds_conflict`, add:

```python
# ── Hybrid-A* tow-path search (spike Q3 v2, #222) ───────────────────────────
# Deterministic search over Dubins motion primitives. Tuning constants; see the
# design spec. All RNG-free (ADR-0003).
_GRID_XY_M = 0.5  # (x, y) cell size for state binning
_GRID_DEG = 15.0  # heading cell size; 360 / 15 = 24 heading bins
_HEADING_BINS = round(360.0 / _GRID_DEG)
_TURN_PENALTY = 0.1  # per-radian g-cost penalty to prefer straighter paths
_MAX_EXPANSIONS = 8000  # node-expansion budget per plane before bailing


def _primitives(turn_radius_m: float) -> tuple[Segment, ...]:
    """The fixed motion-primitive fan, in deterministic order (L, S, R).

    Own-gear (``r > 0``): a left arc, a straight, and a right arc, each of
    length ``step`` (metres) chosen so a turn changes heading by ~one heading
    cell. Cart (``r == 0``): a left pivot and a right pivot of ``_GRID_DEG``
    radians (length_m encodes radians, ADR-0007), plus a straight of ``step``.
    """
    if turn_radius_m == 0.0:
        dtheta = math.radians(_GRID_DEG)
        return (Segment("L", dtheta), Segment("S", _GRID_XY_M), Segment("R", dtheta))
    step = max(_GRID_XY_M, turn_radius_m * math.radians(_GRID_DEG))
    return (Segment("L", step), Segment("S", step), Segment("R", step))


def _step_pose(pose: Pose, seg: Segment, turn_radius_m: float) -> Pose:
    """Integrate one primitive segment from ``pose`` (reuses ``DubinsArc.pose_at``).

    The temporary arc's ``end`` is a placeholder — ``pose_at`` integrates from
    ``start`` and never reads ``end``.
    """
    return DubinsArc(pose, pose, turn_radius_m, (seg,)).pose_at(seg.length_m)


def _seg_cost(seg: Segment, turn_radius_m: float) -> float:
    """g-cost of one segment: translation metres + a small per-radian turn penalty.

    Straight: ``length_m`` metres, no turn. Turn ``r > 0``: arc length
    ``length_m`` metres plus penalty over ``length_m / r`` radians. Pivot
    ``r == 0``: no translation, penalty over ``length_m`` radians.
    """
    if seg.kind == "S":
        return seg.length_m
    if turn_radius_m > 0.0:
        return seg.length_m + _TURN_PENALTY * (seg.length_m / turn_radius_m)
    return _TURN_PENALTY * seg.length_m  # cart pivot: length_m is radians


def _cell(pose: Pose) -> tuple[int, int, int]:
    """Bin a pose into the search grid: ``(x, y)`` rounded to ``_GRID_XY_M`` and
    heading rounded to ``_GRID_DEG`` (wrapped into ``_HEADING_BINS``)."""
    return (
        round(pose.x_m / _GRID_XY_M),
        round(pose.y_m / _GRID_XY_M),
        round(pose.heading_deg / _GRID_DEG) % _HEADING_BINS,
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_towplanner_search.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Gates + commit**

```bash
ruff check src/ tests/ && ruff format src/ tests/ && mypy src/hangarfit/
git add src/hangarfit/towplanner.py tests/test_towplanner_search.py
git commit -m "feat(towplanner): Hybrid-A* motion primitives + search helpers (#222)

$(printf 'Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>')"
```

---

## Task 3: `plan_path` Hybrid-A* search core

**Files:**
- Modify: `src/hangarfit/towplanner.py` (add `_SearchNode`, `plan_path`)
- Test: `tests/test_towplanner_search.py`

**Why:** This is the search itself. It uses `path_first_conflict` (correct, not yet optimized) as the per-edge validity oracle; Task 5 adds the fast checker. The analytic expansion (try a direct Dubins-to-goal at each popped node) preserves today's one-shot behaviour for unobstructed planes and only does maneuvering work when the direct shot is blocked.

- [ ] **Step 1: Write the failing search tests**

Append to `tests/test_towplanner_search.py`:

```python
from hangarfit.models import Aircraft, Door, Hangar, Layout, MaintenanceBay, Part, Placement
from hangarfit.towplanner import NoFeasiblePlanError, path_first_conflict, plan_path


def _hangar(width_m: float = 20.0, length_m: float = 25.0) -> Hangar:
    return Hangar(
        length_m=length_m,
        width_m=width_m,
        door=Door(center_x_m=width_m / 2, width_m=10.0),
        maintenance_bay=MaintenanceBay(center_x_m=width_m / 2, width_m=2.0, depth_m=2.0),
        clearance_m=0.3,
        wing_layer_clearance_m=0.2,
    )


def _winged_plane(pid: str, *, span_m: float = 10.0, turn_radius_m: float = 5.0) -> Aircraft:
    """A high-wing plane: a fuselage centered on the origin plus a wide wing."""
    return Aircraft(
        id=pid,
        name=f"Winged {pid}",
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_own_gear",
        turn_radius_m=turn_radius_m,
        measured=False,
        parts=(
            Part(kind="fuselage", length_m=6.0, width_m=0.9, offset_x_m=0.0,
                 offset_y_m=0.0, angle_deg=0.0, z_bottom_m=0.0, z_top_m=1.4),
            Part(kind="wing", length_m=1.4, width_m=span_m, offset_x_m=-0.5,
                 offset_y_m=0.0, angle_deg=0.0, z_bottom_m=1.4, z_top_m=1.8),
        ),
    )


def test_plan_path_clear_straight_is_a_single_analytic_shot() -> None:
    # Slot straight ahead, same heading: the start node's analytic Dubins shot
    # is clear, so the search returns immediately with that arc's endpoint.
    h = _hangar()
    plane = _winged_plane("A", span_m=6.0)
    placed = Layout(fleet={"A": plane}, hangar=h, placements=())
    entry = Pose(10.0, 0.0, 0.0)
    goal = Pose(10.0, 12.0, 0.0)
    arc = plan_path(plane, entry, goal, hangar=h, placed=placed, mover_on_carts=False)
    last = arc.pose_at(arc.length_m)
    assert last.x_m == pytest.approx(10.0, abs=1e-6)
    assert last.y_m == pytest.approx(12.0, abs=1e-6)
    assert path_first_conflict(arc, plane, mover_on_carts=False, placed=placed) is None


def test_plan_path_finds_inbounds_path_when_direct_shot_sweeps_a_wing_out() -> None:
    # Wide wing + a turned final heading: the direct shortest Dubins shot sweeps
    # the wing outside a wall, so the search must maneuver. The returned path
    # must be exact-oracle clean and land on the goal.
    h = _hangar(width_m=18.0, length_m=25.0)
    plane = _winged_plane("A", span_m=10.0, turn_radius_m=5.0)
    placed = Layout(fleet={"A": plane}, hangar=h, placements=())
    entry = Pose(8.0, 0.0, 0.0)
    goal = Pose(8.0, 8.0, 234.0)
    arc = plan_path(plane, entry, goal, hangar=h, placed=placed, mover_on_carts=False)
    last = arc.pose_at(arc.length_m)
    assert last.x_m == pytest.approx(8.0, abs=1e-3)
    assert last.y_m == pytest.approx(8.0, abs=1e-3)
    assert abs(((last.heading_deg - 234.0 + 180.0) % 360.0) - 180.0) < 0.5
    # In-bounds invariant: the full path passes the exact (front-gap-exempt) oracle.
    assert path_first_conflict(arc, plane, mover_on_carts=False, placed=placed) is None


def test_plan_path_is_deterministic() -> None:
    h = _hangar(width_m=18.0)
    plane = _winged_plane("A", span_m=10.0)
    placed = Layout(fleet={"A": plane}, hangar=h, placements=())
    entry, goal = Pose(8.0, 0.0, 0.0), Pose(8.0, 8.0, 234.0)
    a = plan_path(plane, entry, goal, hangar=h, placed=placed, mover_on_carts=False)
    b = plan_path(plane, entry, goal, hangar=h, placed=placed, mover_on_carts=False)
    assert a.segments == b.segments


def test_plan_path_bails_when_boxed_in() -> None:
    # A goal whose slot is itself jammed against a wall such that no in-bounds
    # approach exists within the budget -> NoFeasiblePlanError naming the mover.
    h = _hangar(width_m=12.0, length_m=12.0)
    plane = _winged_plane("A", span_m=11.0, turn_radius_m=5.0)  # span ~ hangar width
    placed = Layout(fleet={"A": plane}, hangar=h, placements=())
    entry = Pose(6.0, 0.0, 0.0)
    goal = Pose(6.0, 6.0, 90.0)  # wing along x spans nearly the whole width while turning
    with pytest.raises(NoFeasiblePlanError) as ei:
        plan_path(plane, entry, goal, hangar=h, placed=placed, mover_on_carts=False)
    assert ei.value.plane_id == "A"
```

> **Adaptation note:** the exact `span_m`/hangar values for the "boxed-in" test may need a small tweak so the case is genuinely infeasible within `_MAX_EXPANSIONS` — adjust the span up or the hangar down until it raises, keeping the *intent* (a plane that cannot be maneuvered in). The clear-shot and wide-wing tests are the load-bearing ones.

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_towplanner_search.py -k plan_path -v`
Expected: FAIL — `ImportError: cannot import name 'plan_path'`.

- [ ] **Step 3: Implement `_SearchNode` and `plan_path`**

In `src/hangarfit/towplanner.py`, after the helpers from Task 2:

```python
@dataclass(slots=True)
class _SearchNode:
    """A Hybrid-A* node: a pose, its g-cost from start, and the parent link +
    the primitive segment that produced it (for path reconstruction)."""

    pose: Pose
    g: float
    seg: Segment | None
    parent: "_SearchNode | None"


def _reconstruct_segments(node: _SearchNode) -> list[Segment]:
    """Segments from the start pose to ``node``, in travel order."""
    out: list[Segment] = []
    cur: _SearchNode | None = node
    while cur is not None and cur.seg is not None:
        out.append(cur.seg)
        cur = cur.parent
    out.reverse()
    return out


def plan_path(
    mover: Aircraft,
    entry: Pose,
    goal: Pose,
    *,
    hangar: Hangar,
    placed: Layout,
    mover_on_carts: bool,
    max_expansions: int = _MAX_EXPANSIONS,
) -> DubinsArc:
    """Deterministic Hybrid-A* tow path from ``entry`` to ``goal`` (#222).

    Searches continuous ``(x, y, heading)`` with the fixed primitive fan
    (:func:`_primitives`), grid-binned via :func:`_cell`, an admissible Euclidean
    heuristic, and an analytic-expansion shortcut: at every popped node a direct
    ``plan_dubins`` shot to ``goal`` is tried first, so an unobstructed plane
    finishes in one arc. Edge/shot validity is the front-gap-exempt
    :func:`path_first_conflict`. The result is a single :class:`DubinsArc` whose
    segments concatenate the chosen primitives + the final analytic arc (all
    share ``turn_radius_m``). Raises :class:`NoFeasiblePlanError` when no
    in-bounds path is found within ``max_expansions``. RNG-free (ADR-0003):
    fixed primitive order + a monotonic counter tie-break.
    """
    r = mover.effective_turn_radius_m()
    start = _SearchNode(entry, 0.0, None, None)
    counter = 0
    open_heap: list[tuple[float, int, _SearchNode]] = [(math.hypot(goal.x_m - entry.x_m, goal.y_m - entry.y_m), counter, start)]
    best_g: dict[tuple[int, int, int], float] = {_cell(entry): 0.0}
    last_conflict: Conflict | None = None
    expansions = 0

    while open_heap:
        _, _, node = heapq.heappop(open_heap)
        ckey = _cell(node.pose)
        # Stale heap entry (a cheaper path to this cell was found after pushing).
        if best_g.get(ckey, math.inf) < node.g - 1e-9:
            continue

        # Analytic expansion: try to close to the goal directly.
        final_arc = plan_dubins(node.pose, goal, turn_radius_m=r)
        conflict = path_first_conflict(final_arc, mover, mover_on_carts=mover_on_carts, placed=placed)
        if conflict is None:
            segs = tuple(_reconstruct_segments(node)) + final_arc.segments
            return DubinsArc(entry, goal, r, segs or (Segment("S", 0.0),))
        last_conflict = conflict

        if expansions >= max_expansions:
            break
        expansions += 1

        # Primitive expansion (fixed order L, S, R for determinism).
        for seg in _primitives(r):
            child_pose = _step_pose(node.pose, seg, r)
            edge = DubinsArc(node.pose, child_pose, r, (seg,))
            edge_conflict = path_first_conflict(edge, mover, mover_on_carts=mover_on_carts, placed=placed)
            if edge_conflict is not None:
                last_conflict = edge_conflict
                continue
            child_g = node.g + _seg_cost(seg, r)
            child_key = _cell(child_pose)
            if child_g < best_g.get(child_key, math.inf) - 1e-9:
                best_g[child_key] = child_g
                counter += 1
                h = math.hypot(goal.x_m - child_pose.x_m, goal.y_m - child_pose.y_m)
                heapq.heappush(open_heap, (child_g + h, counter, _SearchNode(child_pose, child_g, seg, node)))

    raise NoFeasiblePlanError(
        mover.id,
        last_conflict
        if last_conflict is not None
        else Conflict.single(kind="hangar_bounds", plane=mover.id, detail="no in-bounds tow path found"),
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_towplanner_search.py -v`
Expected: PASS (all). If the wide-wing test times out or the boxed-in test does not raise, adjust the per-test geometry per the adaptation note — do not weaken `plan_path`.

- [ ] **Step 5: Add a determinism canary**

Append to `tests/test_towplanner_search.py` a canary that pins a concrete maneuvering path so an accidental tie-break/order change is caught:

```python
def test_plan_path_canary_pins_a_known_maneuver() -> None:
    # Pins the segment-kind sequence + length for a fixed maneuvering case, in
    # the spirit of the 45-degree Dubins canary (#189). If this changes, a
    # tie-break or primitive-order regression is the likely cause — investigate
    # before updating the expected values.
    h = _hangar(width_m=18.0, length_m=25.0)
    plane = _winged_plane("A", span_m=10.0, turn_radius_m=5.0)
    placed = Layout(fleet={"A": plane}, hangar=h, placements=())
    arc = plan_path(plane, Pose(8.0, 0.0, 0.0), Pose(8.0, 8.0, 234.0),
                    hangar=h, placed=placed, mover_on_carts=False)
    kinds = "".join(s.kind for s in arc.segments)
    # Record the ACTUAL value produced by the first green run, then assert it:
    assert kinds == "<FILL FROM FIRST GREEN RUN>"
    assert arc.length_m == pytest.approx(<FILL FROM FIRST GREEN RUN>, abs=1e-6)
```

After the first green run, replace the two `<FILL …>` placeholders with the observed `kinds` string and `length_m` (print them once, paste them in). This is the one place a value is filled from a verified run — it is a regression pin, not a logic placeholder.

- [ ] **Step 6: Run + gates + commit**

Run: `pytest tests/test_towplanner_search.py -v`
Expected: PASS (all, including the filled canary).

```bash
ruff check src/ tests/ && ruff format src/ tests/ && mypy src/hangarfit/
git add src/hangarfit/towplanner.py tests/test_towplanner_search.py
git commit -m "feat(towplanner): Hybrid-A* plan_path search core (#222)

$(printf 'Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>')"
```

---

## Task 4: Integrate `plan_path` into `plan_fill`

**Files:**
- Modify: `src/hangarfit/towplanner.py` (`plan_fill` uses `plan_path`)
- Test: `tests/test_towplanner_fill.py`

**Why:** `plan_fill` currently calls `plan_dubins(entry, slot)` + a single `path_first_conflict` check. Replace that with `plan_path`, which searches for an in-bounds path; feasibility ⇔ `plan_path` returns an arc (it raises `NoFeasiblePlanError` only when truly stuck, which the scan treats as "this plane not feasible right now").

- [ ] **Step 1: Add a realistic-geometry happy-path test**

In `tests/test_towplanner_fill.py`, add a test using an origin-spanning fuselage (the existing `_aircraft`/`_fuselage_box` is mounted forward and masks the front-gap case; a realistic plane spans the origin):

```python
def test_plan_fill_routes_origin_spanning_planes() -> None:
    from hangarfit.models import Part
    from hangarfit.towplanner import path_first_conflict

    def winged(pid: str) -> Aircraft:
        return Aircraft(
            id=pid, name=pid, wing_position="high", gear="tailwheel",
            movement_mode="always_own_gear", turn_radius_m=5.0, measured=False,
            parts=(Part(kind="fuselage", length_m=6.0, width_m=0.9, offset_x_m=0.0,
                        offset_y_m=0.0, angle_deg=0.0, z_bottom_m=0.0, z_top_m=1.4),),
        )

    h = _hangar(width_m=20.0, length_m=30.0)
    fleet = {"A": winged("A"), "B": winged("B")}
    target = _layout(fleet, h, _slot("A", 8.0, 8.0, 20.0), _slot("B", 12.0, 22.0, 0.0))
    plan = plan_fill(target)
    assert {m.plane_id for m in plan.moves} == {"A", "B"}
    # Deepest first.
    assert plan.moves[0].plane_id == "B"
    # Every move's path is exact-oracle clean against the planes placed before it.
    placed: list = []
    for m in plan.moves:
        from hangarfit.models import Layout as _L
        pl = _L(fleet=fleet, hangar=h, placements=tuple(placed), maintenance_plane=target.maintenance_plane)
        assert path_first_conflict(m.path, fleet[m.plane_id], mover_on_carts=False, placed=pl) is None
        placed.append(next(p for p in target.placements if p.plane_id == m.plane_id))
```

(If `_hangar`/`_slot`/`_layout` helpers differ in the file, adapt to the file's real helpers; intent: two origin-spanning own-gear planes in a roomy hangar are both routed.)

- [ ] **Step 2: Run to verify it fails (or errors) before integration**

Run: `pytest tests/test_towplanner_fill.py -k routes_origin_spanning -v`
Expected: FAIL — current `plan_fill` uses the single-shot `plan_dubins` whose arc trips bounds, so the plane is deemed infeasible and `plan_fill` raises `NoFeasiblePlanError`.

- [ ] **Step 3: Switch `plan_fill` to `plan_path`**

In `plan_fill`, replace the per-candidate arc computation + conflict check. The current loop body builds `arc = plan_dubins(...)` then `conflict = path_first_conflict(arc, ...)`. Replace with:

```python
        for idx, slot in enumerate(ordered):
            plane = fleet[slot.plane_id]
            placed_layout = Layout(
                fleet=fleet,
                hangar=hangar,
                placements=tuple(placed),
                maintenance_plane=target.maintenance_plane,
            )
            try:
                arc = plan_path(
                    plane,
                    entry_pose(slot, hangar),
                    Pose.from_placement(slot),
                    hangar=hangar,
                    placed=placed_layout,
                    mover_on_carts=slot.on_carts,
                )
            except NoFeasiblePlanError as exc:
                # This plane cannot be routed against the current obstacles; try
                # the next candidate. Remember its conflict for the bail message.
                if deepest_conflict is None:
                    deepest_conflict = exc.conflict
                continue
            chosen, chosen_arc = idx, arc
            break
```

The surrounding `while ordered:` / `chosen is None` bail logic and `back_first_order` are unchanged. (Remove the now-unused single-shot `plan_dubins(...)` + `path_first_conflict(...)` lines this replaces.)

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_towplanner_fill.py -v`
Expected: PASS (all). Existing forward-mounted-box tests still pass; the new origin-spanning test now passes.

- [ ] **Step 5: Run the whole towplanner suite**

Run: `pytest tests/test_towplanner.py tests/test_towplanner_dubins.py tests/test_towplanner_motion.py tests/test_towplanner_fill.py tests/test_towplanner_search.py -v`
Expected: PASS (all).

- [ ] **Step 6: Gates + commit**

```bash
ruff check src/ tests/ && ruff format src/ tests/ && mypy src/hangarfit/
git add src/hangarfit/towplanner.py tests/test_towplanner_fill.py
git commit -m "feat(towplanner): plan_fill routes via Hybrid-A* plan_path (#222)

$(printf 'Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>')"
```

---

## Task 5: Performance — precomputed-obstacle `_motion_clear` + exact-oracle safety net

**Files:**
- Modify: `src/hangarfit/towplanner.py` (`_motion_clear`, obstacle precompute; `plan_path` uses it for edge/shot checks; final exact validation)
- Test: `tests/test_towplanner_search.py`

**Why:** `plan_path` currently rebuilds a `Layout` and runs the full Shapely `collisions.check` for every sampled pose of every edge — too slow once real layouts force heavy maneuvering. Precompute each plane-search's obstacle geometry once and use a fast bounds+AABB+lazy-Shapely check during search, then validate the final concatenated arc once with the exact oracle so any divergence is caught, never shipped.

- [ ] **Step 1: Write the equivalence + safety-net tests**

Append to `tests/test_towplanner_search.py`:

```python
def test_motion_clear_matches_path_first_conflict_verdict_on_samples() -> None:
    # The fast checker and the exact oracle must agree (clear vs conflict) on a
    # set of probe poses: clear interior, side-wall clip, overlap with a placed
    # plane, and a front-door protrusion (clear, because front-gap-exempt).
    from hangarfit.towplanner import _build_obstacles, _motion_clear
    h = _hangar(width_m=18.0, length_m=25.0)
    a = _winged_plane("A", span_m=8.0)
    b = _winged_plane("B", span_m=8.0)
    placed = Layout(fleet={"A": a, "B": b}, hangar=h,
                    placements=(Placement("A", 4.0, 10.0, 0.0, on_carts=False),))
    obstacles = _build_obstacles(placed, mover_id="B")
    probes = [
        Pose(12.0, 12.0, 0.0),   # clear interior
        Pose(0.0, 12.0, 90.0),   # wing pokes past x<0 side wall
        Pose(4.0, 10.0, 0.0),    # right on top of placed A -> overlap
        Pose(12.0, 0.0, 0.0),    # front protrusion (y<0) -> clear (exempt)
    ]
    for pose in probes:
        fast = _motion_clear(b, pose, obstacles, h)  # True iff clear
        mp = Placement("B", pose.x_m, pose.y_m, pose.heading_deg, on_carts=False)
        exact = path_first_conflict(
            DubinsArc(pose, pose, 5.0, (Segment("S", 0.0),)), b,
            mover_on_carts=False, placed=placed,
        ) is None
        assert fast == exact, f"divergence at {pose}: fast={fast} exact={exact}"


def test_plan_path_result_always_passes_the_exact_oracle() -> None:
    # The safety net: whatever the search used, the returned arc is exact-clean.
    h = _hangar(width_m=18.0, length_m=25.0)
    plane = _winged_plane("A", span_m=10.0, turn_radius_m=5.0)
    placed = Layout(fleet={"A": plane}, hangar=h, placements=())
    arc = plan_path(plane, Pose(8.0, 0.0, 0.0), Pose(8.0, 8.0, 234.0),
                    hangar=h, placed=placed, mover_on_carts=False)
    assert path_first_conflict(arc, plane, mover_on_carts=False, placed=placed) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_towplanner_search.py -k "motion_clear or always_passes" -v`
Expected: FAIL — `ImportError: cannot import name '_build_obstacles'` / `_motion_clear`.

- [ ] **Step 3: Implement the fast checker + precompute**

Add to `src/hangarfit/towplanner.py`. `_Obstacles` holds the placed planes' world polygons (with z-layer + plane id) and the bay rectangle; `_build_obstacles` computes it once per plane-search; `_motion_clear` does front-gap-exempt bounds → mover-AABB vs obstacle-AABB pre-filter → Shapely intersection honouring the parts-model layer/clearance rule → bay test.

```python
@dataclass(frozen=True, slots=True)
class _Obstacles:
    """Precomputed static geometry for one plane-search: placed planes' world
    parts and the (optional) maintenance-bay keep-out rectangle."""

    world_parts: tuple[WorldPart, ...]
    bay_xmin: float
    bay_xmax: float
    bay_ymin: float
    bay_ymax: float
    bay_active: bool


def _build_obstacles(placed: Layout, *, mover_id: str) -> _Obstacles:
    """Compute the static obstacle set once (placed planes don't move while one
    plane is routed). The bay is a keep-out only when a maintenance plane is set
    (mirrors ``collisions._bay_intrusion_conflicts``)."""
    parts: list[WorldPart] = []
    for placement in placed.placements:
        if placement.plane_id == mover_id:
            continue
        parts.extend(aircraft_parts_world(placed.fleet[placement.plane_id], placement))
    bay = placed.hangar.maintenance_bay
    active = placed.maintenance_plane is not None
    return _Obstacles(
        world_parts=tuple(parts),
        bay_xmin=bay.center_x_m - bay.width_m / 2,
        bay_xmax=bay.center_x_m + bay.width_m / 2,
        bay_ymin=placed.hangar.length_m - bay.depth_m,
        bay_ymax=placed.hangar.length_m,
        bay_active=active,
    )


def _aabb(poly: "Polygon") -> tuple[float, float, float, float]:
    xs = [c[0] for c in poly.exterior.coords]
    ys = [c[1] for c in poly.exterior.coords]
    return min(xs), min(ys), max(xs), max(ys)


def _motion_clear(mover: Aircraft, pose: Pose, obstacles: _Obstacles, hangar: Hangar) -> bool:
    """Fast 'mover clear at this pose?' check mirroring the oracle's verdict.

    (a) front-gap-exempt bounds; (b) mover-AABB vs obstacle-AABB pre-filter;
    (c) Shapely overlap on AABB-overlapping pairs honouring the z-layer +
    clearance rule from :func:`hangarfit.collisions`; (d) bay keep-out. Returns
    ``True`` iff clear. The exact oracle is still the authority on the final
    path (Step 4); this only speeds the search.
    """
    placement = Placement(mover.id, pose.x_m, pose.y_m, pose.heading_deg, on_carts=False)
    mover_parts = aircraft_parts_world(mover, placement)

    # (a) bounds (front-gap exempt): reuse the canonical helper.
    if _mover_motion_bounds_conflict(mover, placement, hangar) is not None:
        return False

    clearance = hangar.clearance_m
    for mp in mover_parts:
        mx0, my0, mx1, my1 = _aabb(mp.polygon)
        # (d) bay keep-out (any mover part vertex strictly inside the active bay).
        if obstacles.bay_active:
            for x, y in list(mp.polygon.exterior.coords)[:-1]:
                if (obstacles.bay_xmin < x < obstacles.bay_xmax) and (obstacles.bay_ymin < y <= obstacles.bay_ymax):
                    return False
        # (b)+(c) overlap vs placed parts.
        for op in obstacles.world_parts:
            ox0, oy0, ox1, oy1 = _aabb(op.polygon)
            # z-layer separation: parts whose [z_bottom, z_top] do not overlap
            # (beyond wing_layer_clearance) can never collide (parts model).
            if mp.z_bottom_m >= op.z_top_m or op.z_bottom_m >= mp.z_top_m:
                continue
            # AABB pre-filter (inflate by clearance so near-misses get Shapely).
            if mx1 + clearance < ox0 or ox1 + clearance < mx0 or my1 + clearance < oy0 or oy1 + clearance < my0:
                continue
            if mp.polygon.distance(op.polygon) < clearance:
                return False
    return True
```

> **Correctness note:** `_motion_clear` must replicate exactly the rules `collisions.check` applies between the mover and static planes — the same `clearance_m` gap, the same z-layer overlap test (`wing_layer_clearance_m` for wing-over-wing layering), and the same bay-edge inclusivity. Verify each against `src/hangarfit/collisions.py` while implementing; the equivalence test (Step 1) is the guard. If a rule is subtle (e.g. strut nesting), prefer matching the oracle's behaviour over inventing a shortcut.

Add `from shapely.geometry import Polygon` and `from hangarfit.geometry import WorldPart, aircraft_parts_world` to the imports as needed (check what `geometry` exports; `WorldPart` is a dataclass there).

- [ ] **Step 4: Use the fast checker in `plan_path`, keep the exact-oracle safety net**

In `plan_path`, build obstacles once and validate edges/shots with `_motion_clear` for speed; before returning the final arc, validate it once with `path_first_conflict` (the exact oracle):

- At the top: `obstacles = _build_obstacles(placed, mover_id=mover.id)`.
- Replace the analytic-expansion validity: sample `final_arc` and require every sample `_motion_clear`; if all clear, **then** run the exact `path_first_conflict(final_arc, …)` once — only return if it is also `None` (safety net). Concretely:

```python
        final_arc = plan_dubins(node.pose, goal, turn_radius_m=r)
        if all(_motion_clear(mover, p, obstacles, hangar) for p in final_arc.sample()):
            if path_first_conflict(final_arc, mover, mover_on_carts=mover_on_carts, placed=placed) is None:
                segs = tuple(_reconstruct_segments(node)) + final_arc.segments
                return DubinsArc(entry, goal, r, segs or (Segment("S", 0.0),))
```

- Replace the per-primitive edge validity with `all(_motion_clear(mover, p, obstacles, hangar) for p in edge.sample())` instead of `path_first_conflict(edge, …)`.

(The `last_conflict` diagnostic can be a generic "no in-bounds tow path" when only `_motion_clear` is used on edges; the analytic path keeps the exact conflict when its safety-net check fails.)

- [ ] **Step 5: Run the search tests (equivalence, safety net, determinism, canary)**

Run: `pytest tests/test_towplanner_search.py -v`
Expected: PASS (all). If `test_motion_clear_matches_…` fails, `_motion_clear` diverges from the oracle — fix `_motion_clear` to match `collisions.check`, do not weaken the test.

- [ ] **Step 6: Gates + commit**

```bash
ruff check src/ tests/ && ruff format src/ tests/ && mypy src/hangarfit/
git add src/hangarfit/towplanner.py tests/test_towplanner_search.py
git commit -m "perf(towplanner): fast _motion_clear with exact-oracle safety net in plan_path (#222)

$(printf 'Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>')"
```

---

## Task 6: Integration acceptance — solver fixture matrix + performance gate

**Files:**
- Verify/adjust: `tests/test_solver_search.py`, `tests/test_solver_towplanner.py`, `tests/test_solver_fixture_matrix.py`, `tests/test_solver_spread.py`, `tests/test_cli.py`, `tests/test_cli_solve.py`
- Create: `tests/test_towplanner_perf.py`

**Why:** The real acceptance criterion: with `plan_path` in place, the solver fixture matrix (currently failing with `no_feasible_plan`) returns to `found` with bundled, exact-validated plans — and within budget.

- [ ] **Step 1: Run the full solver + CLI suites**

Run: `pytest tests/test_solver_search.py tests/test_solver_towplanner.py tests/test_solver_fixture_matrix.py tests/test_solver_spread.py tests/test_solver_canaries.py tests/test_solver_infeasibility.py tests/test_cli.py tests/test_cli_solve.py -v`
Expected: PASS. Previously-`found` tests are `found` again; `test_solve_bundles_a_plan_per_layout` passes with `MovesPlan`s whose `path` is exact-oracle clean.

If a specific fixture still reports `no_feasible_plan`, inspect it: is the plane genuinely boxed in (then it is a correct strict-Decision-3 failure — confirm with the user before changing any fixture), or did `plan_path` hit `_MAX_EXPANSIONS` (then raise the budget or coarsen the grid, re-measure)? Do not edit fixtures to force green without confirming the layout is genuinely un-towable.

- [ ] **Step 2: Add the performance gate test**

Create `tests/test_towplanner_perf.py`:

```python
import time

import pytest

from hangarfit.loader import load_scenario
from hangarfit.solver import solve


@pytest.mark.slow
@pytest.mark.parametrize(
    "scenario",
    [
        "tests/fixtures/solve_feasible_smoke.yaml",
        "tests/fixtures/solve_fresh_six_planes.yaml",
    ],
)
def test_solve_with_planning_completes_within_budget(scenario: str) -> None:
    s = load_scenario(scenario)
    t0 = time.monotonic()
    result = solve(s, budget_s=10.0, alternatives=1, seed=7)
    elapsed = time.monotonic() - t0
    # Planning must not blow the wall-clock far past the search budget.
    assert elapsed < 30.0, f"{scenario}: planning took {elapsed:.1f}s"
    assert result.status in ("found", "found_partial", "no_feasible_plan")
```

(Adjust the scenario paths/threshold to the repo's real fixtures; `30.0` is a generous ceiling — tighten once measured. The intent: planning does not run away.)

- [ ] **Step 3: Run the perf test**

Run: `pytest tests/test_towplanner_perf.py -m slow -v`
Expected: PASS within the ceiling. If it exceeds it, tune `_GRID_XY_M` / `_GRID_DEG` / `_MAX_EXPANSIONS` (coarser grid, lower budget) or strengthen `_motion_clear`'s pre-filters; re-measure. If it cannot be made to fit, escalate — this is the make-or-break signal noted in the spec.

- [ ] **Step 4: Full suite + gates**

Run: `pytest && ruff check src/ tests/ && ruff format --check src/ tests/ && mypy src/hangarfit/`
Expected: all PASS (bare `pytest` excludes `slow`), lint/type clean.

- [ ] **Step 5: Commit**

```bash
git add tests/test_towplanner_perf.py
git commit -m "test(towplanner): fixture-matrix acceptance + perf gate for plan_path (#222)

$(printf 'Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>')"
```

- [ ] **Step 6: Open PR and run the review arc**

```bash
git push -u origin feature/222-bound-aware-towpath-planner
gh pr create --base develop --title "feat(towplanner): bound-aware Hybrid-A* tow-path planner (#222)" \
  --body "Closes #222

Replaces the single-shot Dubins path in plan_fill with a deterministic Hybrid-A* search (plan_path) that finds in-bounds, obstacle-free multi-segment tow paths, so solve returns genuinely tow-able layouts. Front-gap exemption for the in-transit mover; RNG-free (ADR-0003); path emitted as one DubinsArc (no Move/MovesPlan change); fast _motion_clear with an exact-oracle safety net; perf-gated on the fixture matrix. Unblocks #197. Design: docs/superpowers/specs/2026-05-25-bound-aware-towpath-planner-design.md."
```

Set assignee=DocGerd + `enhancement` label + milestone "Phase 3a — Tow-path planner v1 (empty-hangar fill)" via `gh api -X PATCH` ([[feedback_pr_metadata]]). Run `/pr-review` with `pr-review-toolkit:code-reviewer` (main), `pr-review-toolkit:silent-failure-hunter` (the search has catch/bail + a fast-vs-exact divergence surface), and the `geometry-invariant-guard` (**`plan_path`/`_motion_clear` are new callers of `aircraft_parts_world`** — guard the determinant-(-1) trap, even though `geometry.py` itself is untouched). Convert findings to diff threads, fix/reply, resolve ([[feedback_resolve_review_threads]]); report clean. **Do not merge.**

---

## Self-Review

**Spec coverage (spec §3–§5):** algorithm (Tasks 2–3), front-gap exemption (Task 1), single-`DubinsArc` representation + `plan_fill` integration (Tasks 3–4), performance design with `_motion_clear` + exact-oracle net + node budget (Task 5), failure semantics / `NoFeasiblePlanError` (Task 3 + 4), testing — clear shot / wide-wing / boxed-in / determinism / in-bounds invariant / canary / fixture-matrix acceptance / perf gate (Tasks 3, 5, 6). ✓

**Placeholder scan:** the only intentional fill-from-run is the determinism canary's expected `kinds`/`length_m` (Task 3 Step 5), which the engineer fills from the first verified green run — a regression pin, not a logic gap. The adaptation notes (boxed-in geometry, perf threshold, fixture paths) are TDD-gated execution judgements with fixed intent, not vague requirements.

**Type consistency:** `plan_path(mover, entry, goal, *, hangar, placed, mover_on_carts, max_expansions)` is used consistently in Tasks 3–4 and the tests; `_primitives(turn_radius_m)`, `_step_pose(pose, seg, turn_radius_m)`, `_seg_cost(seg, turn_radius_m)`, `_cell(pose)`, `_build_obstacles(placed, *, mover_id)`, `_motion_clear(mover, pose, obstacles, hangar)`, `_SearchNode(pose, g, seg, parent)`, `_Obstacles(...)` match across tasks. The emitted path is a single `DubinsArc(entry, goal, r, segments)` everywhere; `Move.path` stays `DubinsArc` (no model change).

**Determinism (ADR-0003):** no RNG; fixed primitive order `(L, S, R)`; heap tie-break by monotonic `counter`; the canary pins it. ✓

**Riskiest assumption, called out:** `_motion_clear` faithfully mirroring `collisions.check` (Task 5) and the search fitting the budget (Task 6). The equivalence test guards the former; the perf gate + escalation note guard the latter.
