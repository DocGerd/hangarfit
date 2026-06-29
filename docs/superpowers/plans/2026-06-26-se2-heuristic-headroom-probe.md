# SE(2) heading-aware heuristic — headroom probe (Step 0) Implementation Plan

> **⚠ REFUTED (2026-06-27) — executed; gate returned NO-GO.** All four tasks were implemented
> (the toy fixture, the `heuristic_fn` seam, the backward-SE(2) field, the probe driver). The
> gate ran on the real fk9↔cessna pair and measured **NO-GO** (grid 96 949 vs se2 108 991
> expansions, 0.89×): the cost is an intrinsic A\* plateau, so the heuristic class is dead.
> Step 1 (promoting the field into `towplanner`) was **not** built. The seam + the dev-only
> `bench/se2_heuristic_probe.py` are retained as the reproducible refutation record. Result +
> reasoning: [`docs/spikes/herrenteich-fk9-cessna-lateral-shuffle.md`](../../spikes/herrenteich-fk9-cessna-lateral-shuffle.md) § "Step-0 result".

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure — for ~1–2 h, no ML — whether a heading-aware SE(2) cost-to-go heuristic collapses Hybrid-A* expansions on the fk9↔cessna nook enough to justify building it (the spec's **Step 0 gate**).

**Architecture:** Add one tiny additive heuristic-injection seam to `plan_path` (default-OFF, byte-identical). In the dev-only `bench/` package, prototype an exact **backward-SE(2) Dijkstra** cost-to-go field over the real primitive lattice, build a fast **synthetic toy nook** from the real fk9/cessna geometry, and run `plan_path` three ways at fine grid (today's position-only `grid` heuristic vs the injected SE(2) field vs `euclidean`), comparing `stats["expansions"]`. The probe **emits a GO / PARTIAL / NO-GO verdict** — that verdict, not a merge, is the deliverable.

**Tech Stack:** Python 3.12, stdlib `heapq`/`math`/`contextlib`, the existing `hangarfit.towplanner` + `hangarfit.loader` public/internal API. No torch, no new deps.

**This plan delivers the gate only.** On **GO**, Step 1 (promote the field into a first-class `heuristic="se2"` mode, validate on the real isolated pair under the 16 k budget, determinism canary) gets its **own** plan, informed by the probe's findings — see [the design spec §4](../specs/2026-06-26-heading-aware-cost-to-go-design.md). On **NO-GO**, the heuristic class is dead and we fall to the spec's §6 kill-branch (cache the witness / document manual insertion).

## Global Constraints

- **Determinism (ADR-0003).** The only production change (Task 2) is an *additive optional* `plan_path` parameter defaulting to `None`; the `None` path must be **byte-identical** to today's code. The `determinism-guard` subagent reviews it. The probe itself is dev-only and never ships.
- **`bench/` is dev/CI-only.** New code lives in `bench/se2_heuristic_probe.py`; `packages.find` uses `where = ["src"]`, so `bench/` is never in the wheel. Run via `python -m bench.se2_heuristic_probe`.
- **Validity oracle = product checker.** Any path the probe accepts as "found" is the one `plan_path` already exact-oracle-validated via `path_first_conflict` (`towplanner.py:2670`). Heuristic admissibility is *not* required for the probe — the safety net guarantees a found path is valid regardless of heuristic quality. We measure expansions + found, not optimality.
- **Fine-grid monkeypatch discipline.** The probe runs at `0.25 m / 10°` by patching the module constants `_GRID_XY_M`, `_GRID_DEG`, **and** `_HEADING_BINS` (the last is computed once at import from `_GRID_DEG`, so it must be patched too) inside a `try/finally` that restores them. Never leave them patched.
- **Do NOT run the real ~40-min fk9/cessna/husky case in Step 0.** The toy nook must route in seconds; the real isolated-pair run is Step 1's efficacy gate.
- **Coordinate convention:** `(0,0)` front-left; `+x` along the door wall; `+y` deeper into the hangar; heading compass (0 at `+y`, CW-positive). `Pose(x_m=, y_m=, heading_deg=)`, `Segment(kind, length_m, gear=1)`.

---

### Task 1: Synthetic toy nook fixture (fast, faithful-enough, search-forcing)

Build the toy in code from the **real** fk9/cessna geometry (faithful nook shape) placed in a **small** synthetic hangar (fast search). Calibrate that it genuinely requires search.

**Files:**
- Create: `bench/se2_heuristic_probe.py`
- Test: `tests/bench/test_se2_heuristic_probe.py` (create; `tests/bench/__init__.py` if absent)

**Interfaces:**
- Consumes: `hangarfit.loader.load_layout`, `hangarfit.models.{Hangar,Door,MaintenanceBay,Placement,Layout}`, `hangarfit.towplanner.{Pose,plan_path,_build_obstacles,_motion_clear,_SEARCH_STEP_M,_SEARCH_STEP_DEG}`.
- Produces: `build_toy_nook() -> ToyNook` where `ToyNook` is a frozen dataclass with fields `mover: Aircraft`, `entry: Pose`, `goal: Pose`, `hangar: Hangar`, `placed: Layout`, `mover_on_carts: bool`. Also `fine_grid()` context manager and `_GOAL_REL` constant (the real fk9−cessna relative pose).

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_se2_heuristic_probe.py
from __future__ import annotations

import math

from bench.se2_heuristic_probe import build_toy_nook, fine_grid
from hangarfit import towplanner
from hangarfit.towplanner import plan_path


def test_toy_nook_requires_search_at_fine_grid() -> None:
    """The toy must be HARD: the analytic Reeds–Shepp shot must not solve it
    trivially (expansions > 0), so the heuristic actually drives expansion order."""
    nook = build_toy_nook()
    stats: dict[str, object] = {}
    with fine_grid():
        try:
            plan_path(
                nook.mover,
                nook.entry,
                nook.goal,
                hangar=nook.hangar,
                placed=nook.placed,
                mover_on_carts=nook.mover_on_carts,
                max_expansions=4000,
                heuristic="grid",
                stats=stats,
            )
        except towplanner.NoFeasiblePlanError:
            pass  # budget-exhausting is fine; expansions>0 is what we assert
    assert isinstance(stats["expansions"], int)
    assert stats["expansions"] > 0, "toy nook solved by the analytic shot — too easy, retune"


def test_toy_nook_goal_is_clear_of_the_parked_obstacle() -> None:
    """The mover at its goal pose must not collide with the parked plane —
    otherwise the fixture is invalid, not hard."""
    nook = build_toy_nook()
    obstacles = towplanner._build_obstacles(nook.placed, mover_id=nook.mover.id)
    assert towplanner._motion_clear(
        nook.mover, nook.goal, obstacles, nook.hangar.motion_hangar()
    ), "mover-at-goal conflicts with the parked obstacle — invalid fixture"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/bench/test_se2_heuristic_probe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bench.se2_heuristic_probe'` (or `ImportError` for `build_toy_nook`).

- [ ] **Step 3: Write the fixture builder**

Create `bench/se2_heuristic_probe.py`:

```python
"""SE(2) heading-aware heuristic — Step-0 headroom probe (DEV/CI-ONLY, #840).

Not shipped in the wheel (top-level `bench/`, `where=["src"]`). Measures whether a
heading-aware SE(2) cost-to-go heuristic collapses Hybrid-A* expansions on the
fk9↔cessna nook vs today's position-only `grid` heuristic. Run:

    python -m bench.se2_heuristic_probe
"""

from __future__ import annotations

import contextlib
import math
from collections.abc import Iterator
from dataclasses import dataclass

from hangarfit import towplanner
from hangarfit.loader import load_layout
from hangarfit.models import Aircraft, Door, Hangar, Layout, MaintenanceBay, Placement
from hangarfit.towplanner import Pose

# Fine-grid resolution for the probe (the witness found the real path at 0.25 m/10°).
_FINE_XY_M = 0.25
_FINE_DEG = 10.0

# The real Herrenteich fk9↔cessna goal poses define the binding nook geometry.
_HERRENTEICH_LAYOUT = "examples/herrenteich/layout.yaml"


@contextlib.contextmanager
def fine_grid() -> Iterator[None]:
    """Run the towplanner at the fine 0.25 m/10° grid, restoring the deployed
    constants afterwards. `_HEADING_BINS` is import-time-derived from `_GRID_DEG`,
    so it must be patched too."""
    saved = (towplanner._GRID_XY_M, towplanner._GRID_DEG, towplanner._HEADING_BINS)
    try:
        towplanner._GRID_XY_M = _FINE_XY_M
        towplanner._GRID_DEG = _FINE_DEG
        towplanner._HEADING_BINS = round(360.0 / _FINE_DEG)
        yield
    finally:
        (towplanner._GRID_XY_M, towplanner._GRID_DEG, towplanner._HEADING_BINS) = saved


@dataclass(frozen=True, slots=True)
class ToyNook:
    mover: Aircraft
    entry: Pose
    goal: Pose
    hangar: Hangar
    placed: Layout
    mover_on_carts: bool


def _real_pair() -> tuple[Aircraft, Aircraft, Placement, Placement]:
    """Load the real fk9_mkii + cessna_140 aircraft and their Herrenteich goal
    placements (the binding nook geometry)."""
    layout = load_layout(_HERRENTEICH_LAYOUT)
    fleet = dict(layout.fleet)
    by_id = {p.plane_id: p for p in layout.placements}
    return fleet["fk9_mkii"], fleet["cessna_140"], by_id["fk9_mkii"], by_id["cessna_140"]


def build_toy_nook() -> ToyNook:
    """A SMALL hangar holding the real cessna_140 (parked obstacle) with the real
    fk9_mkii routing to a goal at the REAL fk9−cessna relative pose — so the
    binding parallel-park geometry is preserved but the arena is small enough that
    fine-grid A* runs in seconds. Calibrated values; tune `_CX/_CY/hangar dims` if
    the calibration tests fail (see tests)."""
    fk9, cessna, fk9_p, cessna_p = _real_pair()
    # Real relative pose (preserves the mutual-block geometry; translation only).
    rel_x = fk9_p.x_m - cessna_p.x_m
    rel_y = fk9_p.y_m - cessna_p.y_m
    # Park the cessna here in the small hangar; fk9's goal is offset by the real rel.
    _CX, _CY = 7.0, 9.0
    hangar = Hangar(
        length_m=16.0,
        width_m=14.0,
        door=Door(center_x_m=7.0, width_m=12.0),
        maintenance_bay=MaintenanceBay(center_x_m=7.0, width_m=0.0, depth_m=0.0),
        clearance_m=0.15,
        wing_layer_clearance_m=0.15,
        max_carts=0,
    )
    cessna_place = Placement("cessna_140", _CX, _CY, cessna_p.heading_deg, on_carts=False)
    placed = Layout(
        fleet={"cessna_140": cessna, "fk9_mkii": fk9},
        hangar=hangar,
        placements=(cessna_place,),
    )
    goal = Pose(x_m=_CX + rel_x, y_m=_CY + rel_y, heading_deg=fk9_p.heading_deg)
    entry = Pose(x_m=hangar.door.center_x_m, y_m=0.0, heading_deg=0.0)
    return ToyNook(
        mover=fk9, entry=entry, goal=goal, hangar=hangar, placed=placed, mover_on_carts=False
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/bench/test_se2_heuristic_probe.py -v`
Expected: PASS. If `test_toy_nook_requires_search_at_fine_grid` fails with `expansions == 0`, the analytic shot solved it — tuck the goal harder (raise `_CY`, narrow the door) and re-run. If `test_toy_nook_goal_is_clear_of_the_parked_obstacle` fails, the translation overlaps — adjust `_CX/_CY` so the real relative offset places fk9-goal clear of the parked cessna.

- [ ] **Step 5: Commit**

```bash
git add bench/se2_heuristic_probe.py tests/bench/
git commit -m "feat(840): toy nook fixture for the SE(2) heuristic headroom probe (Step 0)"
```

---

### Task 2: Heuristic-injection seam in `plan_path` (additive, byte-identical when OFF)

**Files:**
- Modify: `src/hangarfit/towplanner.py` (the `plan_path` signature ~line 2418-2429, the `_h` block ~line 2512-2524)
- Test: `tests/test_towplanner_heuristic_fn.py` (create)

**Interfaces:**
- Consumes: nothing new.
- Produces: `plan_path(..., heuristic_fn: Callable[[Pose], float] | None = None)`. When `None`, behaviour is identical to today. When provided, `heuristic_fn` overrides the cost-to-go estimate at both `_h` call sites (`towplanner.py:2623`, `:2713`). `stats` and the exact-oracle safety net are unchanged.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_towplanner_heuristic_fn.py
from __future__ import annotations

from bench.se2_heuristic_probe import build_toy_nook, fine_grid
from hangarfit import towplanner
from hangarfit.towplanner import Pose, plan_path


def test_injected_heuristic_is_called_during_search() -> None:
    nook = build_toy_nook()
    calls: list[Pose] = []

    def recording_h(p: Pose) -> float:
        calls.append(p)
        return 0.0  # constant 0 ⇒ uniform-cost (Dijkstra), still valid via the oracle

    with fine_grid():
        try:
            plan_path(
                nook.mover, nook.entry, nook.goal,
                hangar=nook.hangar, placed=nook.placed,
                mover_on_carts=nook.mover_on_carts,
                max_expansions=4000, heuristic="euclidean",
                heuristic_fn=recording_h,
            )
        except towplanner.NoFeasiblePlanError:
            pass
    assert calls, "injected heuristic_fn was never called — seam not wired"


def test_none_is_identical_to_default() -> None:
    """Passing heuristic_fn=None must equal not passing it (byte-identical seam)."""
    nook = build_toy_nook()
    s1: dict[str, object] = {}
    s2: dict[str, object] = {}
    with fine_grid():
        for stats, kwargs in ((s1, {}), (s2, {"heuristic_fn": None})):
            try:
                plan_path(
                    nook.mover, nook.entry, nook.goal,
                    hangar=nook.hangar, placed=nook.placed,
                    mover_on_carts=nook.mover_on_carts,
                    max_expansions=4000, heuristic="grid", stats=stats, **kwargs,
                )
            except towplanner.NoFeasiblePlanError:
                pass
    assert s1["expansions"] == s2["expansions"]
    assert s1["found"] == s2["found"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_towplanner_heuristic_fn.py -v`
Expected: FAIL with `TypeError: plan_path() got an unexpected keyword argument 'heuristic_fn'`.

- [ ] **Step 3: Add the seam**

In `src/hangarfit/towplanner.py`, add the parameter to the `plan_path` signature (after `heuristic: Literal["euclidean", "grid"] = "euclidean",`, before `stats:`):

```python
    heuristic_fn: Callable[[Pose], float] | None = None,
```

Then, immediately AFTER the existing `if heuristic == "grid": ... else: ...` block that defines `_h` (after line 2524, before `counter = 0`), add:

```python
    # Dev/test seam (#840): an explicit heuristic_fn overrides the cost-to-go
    # estimate (used by the SE(2) heading-aware headroom probe). Default None ⇒
    # the `heuristic` Literal's `_h` above is used unchanged ⇒ byte-identical
    # (ADR-0003): the determinism canaries never pass heuristic_fn.
    if heuristic_fn is not None:
        _h = heuristic_fn
```

Confirm `Callable` is imported at the top of the file (it is used elsewhere; if not, add `from collections.abc import Callable`). Update the `plan_path` docstring's `heuristic` paragraph (~line 2473) with one sentence noting the `heuristic_fn` dev/test override and its default-None byte-identity.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_towplanner_heuristic_fn.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Run the existing determinism canaries to confirm byte-identity**

Run: `python -m pytest tests/test_solver_canaries.py tests/test_towplanner_determinism.py -v` (use the actual towplanner determinism test module names present in `tests/`; if unsure, run `python -m pytest tests/ -k "determinism or canary" -v`).
Expected: PASS — the default-None path is unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/hangarfit/towplanner.py tests/test_towplanner_heuristic_fn.py
git commit -m "feat(840): additive heuristic_fn injection seam in plan_path (default-OFF, byte-identical)"
```

---

### Task 3: Backward-SE(2) Dijkstra cost-to-go field (the prototype heuristic)

**Files:**
- Modify: `bench/se2_heuristic_probe.py`
- Test: `tests/bench/test_se2_heuristic_probe.py` (extend)

**Interfaces:**
- Consumes: `towplanner.{_primitives,_step_pose,_seg_cost,_cell,_motion_clear,_build_obstacles,DubinsArc,_SEARCH_STEP_M,_SEARCH_STEP_DEG}`, `ToyNook`.
- Produces: `build_se2_field(mover, goal, obstacles, motion_hangar, r, *, mover_on_carts, max_cells=300_000) -> dict[tuple[int,int,int], float]` and `make_field_h(field, goal) -> Callable[[Pose], float]`.

**Algorithm note (why this is the heading-aware field):** A backward uniform-cost flood from `goal` over `_cell`-binned poses, using the real primitive fan (`_primitives`, inverse-closed: the fan contains both gears, so expanding *from* the goal with each primitive enumerates poses from which a single forward primitive reaches the goal at equal `_seg_cost`). Costs are gear-agnostic (#480), so the field is the per-cell minimal **cusp-free, obstacle-aware** cost-to-go — a heading-aware guide (two poses at the same `(x,y)` but different heading get different values, which the position-only `grid` field cannot). Cusp penalties are omitted (keeps it a lower bound); the probe doesn't need provable admissibility — `plan_path`'s exact oracle validates any returned path regardless.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/bench/test_se2_heuristic_probe.py
from bench.se2_heuristic_probe import build_se2_field, make_field_h
from hangarfit import towplanner


def test_se2_field_is_heading_aware() -> None:
    """Two poses at the same (x,y) but opposite heading get DIFFERENT field values
    (the whole point — the position-only grid field cannot tell them apart)."""
    nook = build_toy_nook()
    r = nook.mover.effective_turn_radius_m()
    obstacles = towplanner._build_obstacles(nook.placed, mover_id=nook.mover.id)
    with fine_grid():
        field = build_se2_field(
            nook.mover, nook.goal, obstacles, nook.hangar.motion_hangar(), r,
            mover_on_carts=nook.mover_on_carts,
        )
        h = make_field_h(field, nook.goal)
        # Goal cell is cost 0.
        assert h(nook.goal) == 0.0
        # Same position as goal, heading rotated 90° ⇒ a pivot is needed ⇒ > 0.
        rotated = Pose(nook.goal.x_m, nook.goal.y_m, (nook.goal.heading_deg + 90.0) % 360.0)
        assert h(rotated) > 0.0
        # Far-away pose absent from the field falls back to euclidean (finite, > 0).
        far = Pose(nook.goal.x_m + 50.0, nook.goal.y_m, nook.goal.heading_deg)
        assert h(far) > 0.0 and math.isfinite(h(far))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/bench/test_se2_heuristic_probe.py::test_se2_field_is_heading_aware -v`
Expected: FAIL with `ImportError` (`build_se2_field` not defined).

- [ ] **Step 3: Implement the field**

Append to `bench/se2_heuristic_probe.py`:

```python
import heapq
from collections.abc import Callable

from hangarfit.models import Aircraft, GroundObject  # GroundObject for the type union


def build_se2_field(
    mover: Aircraft | GroundObject,
    goal: Pose,
    obstacles: object,  # towplanner._Obstacles (private)
    motion_hangar: Hangar,
    r: float,
    *,
    mover_on_carts: bool,
    max_cells: int = 300_000,
) -> dict[tuple[int, int, int], float]:
    """Backward-SE(2) Dijkstra cost-to-go field from `goal` over the `_cell`
    lattice using the real primitive fan (see the algorithm note in the plan)."""
    field: dict[tuple[int, int, int], float] = {towplanner._cell(goal): 0.0}
    counter = 0
    heap: list[tuple[float, int, Pose]] = [(0.0, counter, goal)]
    prims = towplanner._primitives(r, lateral=mover_on_carts)
    while heap and len(field) < max_cells:
        d, _, pose = heapq.heappop(heap)
        if d > field.get(towplanner._cell(pose), math.inf) + 1e-12:
            continue  # stale
        for seg in prims:
            nxt = towplanner._step_pose(pose, seg, r)
            edge = towplanner.DubinsArc(pose, nxt, r, (seg,))
            if not all(
                towplanner._motion_clear(mover, p, obstacles, motion_hangar)
                for p in edge.sample(
                    step_m=towplanner._SEARCH_STEP_M, step_deg=towplanner._SEARCH_STEP_DEG
                )
            ):
                continue
            nd = d + towplanner._seg_cost(seg, r)
            nkey = towplanner._cell(nxt)
            if nd < field.get(nkey, math.inf) - 1e-9:
                field[nkey] = nd
                counter += 1
                heapq.heappush(heap, (nd, counter, nxt))
    return field


def make_field_h(
    field: dict[tuple[int, int, int], float], goal: Pose
) -> Callable[[Pose], float]:
    """Heuristic lookup: field value at the pose's `_cell`, else euclidean fallback
    (mirrors plan_path's grid-heuristic fallback so no pose is ever un-expandable)."""

    def _h(p: Pose) -> float:
        v = field.get(towplanner._cell(p))
        if v is None:
            return math.hypot(goal.x_m - p.x_m, goal.y_m - p.y_m)
        return v

    return _h
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/bench/test_se2_heuristic_probe.py::test_se2_field_is_heading_aware -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bench/se2_heuristic_probe.py tests/bench/test_se2_heuristic_probe.py
git commit -m "feat(840): backward-SE(2) Dijkstra cost-to-go field prototype (Step 0)"
```

---

### Task 4: The probe driver + GO/PARTIAL/NO-GO verdict + confirmatory divergence map

**Files:**
- Modify: `bench/se2_heuristic_probe.py` (add `run_probe()` + `main()`)
- Test: `tests/bench/test_se2_heuristic_probe.py` (extend — structural assertions only; the *verdict* is human-read)

**Interfaces:**
- Consumes: everything above.
- Produces: `run_probe(budget: int = 16_000) -> ProbeResult` (frozen dataclass: `exp_grid:int|None`, `found_grid:bool`, `exp_se2:int|None`, `found_se2:bool`, `exp_euclid:int|None`, `found_euclid:bool`, `ratio:float`, `verdict:str`) and a `main()` that prints the table + verdict + the heuristic-divergence map.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/bench/test_se2_heuristic_probe.py
from bench.se2_heuristic_probe import ProbeResult, run_probe


def test_run_probe_emits_a_verdict() -> None:
    """The probe runs all three heuristics on the toy nook and classifies. Use a
    small budget so the test stays fast; we assert STRUCTURE, not the outcome
    (the outcome is the research finding, read by a human)."""
    result = run_probe(budget=4000)
    assert isinstance(result, ProbeResult)
    assert result.verdict in {"GO", "PARTIAL", "NO-GO"}
    # The SE(2) field must at least be exercised (an int expansion count when it ran).
    assert result.exp_se2 is None or isinstance(result.exp_se2, int)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/bench/test_se2_heuristic_probe.py::test_run_probe_emits_a_verdict -v`
Expected: FAIL with `ImportError` (`run_probe`/`ProbeResult` not defined).

- [ ] **Step 3: Implement the driver**

Append to `bench/se2_heuristic_probe.py`:

```python
@dataclass(frozen=True, slots=True)
class ProbeResult:
    exp_grid: int | None
    found_grid: bool
    exp_se2: int | None
    found_se2: bool
    exp_euclid: int | None
    found_euclid: bool
    ratio: float  # exp_grid / exp_se2 (inf if se2 found at 0 / grid didn't find)
    verdict: str


def _run_one(nook: ToyNook, budget: int, heuristic: str, h_fn: object) -> tuple[int, bool]:
    stats: dict[str, object] = {}
    try:
        plan_path(
            nook.mover, nook.entry, nook.goal,
            hangar=nook.hangar, placed=nook.placed,
            mover_on_carts=nook.mover_on_carts,
            max_expansions=budget, heuristic=heuristic, heuristic_fn=h_fn, stats=stats,
        )
        return int(stats["expansions"]), True  # type: ignore[arg-type]
    except towplanner.NoFeasiblePlanError:
        return int(stats["expansions"]), False  # type: ignore[arg-type]


def run_probe(budget: int = 16_000) -> ProbeResult:
    nook = build_toy_nook()
    r = nook.mover.effective_turn_radius_m()
    with fine_grid():
        obstacles = towplanner._build_obstacles(nook.placed, mover_id=nook.mover.id)
        field = build_se2_field(
            nook.mover, nook.goal, obstacles, nook.hangar.motion_hangar(), r,
            mover_on_carts=nook.mover_on_carts,
        )
        h_se2 = make_field_h(field, nook.goal)
        exp_grid, found_grid = _run_one(nook, budget, "grid", None)
        exp_se2, found_se2 = _run_one(nook, budget, "euclidean", h_se2)
        exp_euclid, found_euclid = _run_one(nook, budget, "euclidean", None)

    # Ratio: how many fewer expansions the SE(2) field needed vs the deployed grid.
    if found_se2 and exp_se2 == 0:
        ratio = math.inf
    elif found_se2 and not found_grid:
        ratio = math.inf  # se2 found within budget where grid exhausted
    elif found_se2 and found_grid and exp_se2 > 0:
        ratio = exp_grid / exp_se2
    else:
        ratio = 1.0  # se2 did not find ⇒ no headroom demonstrated

    if found_se2 and ratio >= 50.0:
        verdict = "GO"
    elif found_se2 and ratio >= 5.0:
        verdict = "PARTIAL"
    else:
        verdict = "NO-GO"
    return ProbeResult(
        exp_grid, found_grid, exp_se2, found_se2, exp_euclid, found_euclid, ratio, verdict
    )


def _divergence_map(nook: ToyNook, field: dict[tuple[int, int, int], float]) -> str:
    """Confirmatory: at a few poses near the goal column, show grid-h (position-only)
    vs se2-h (heading-aware). The mechanism is confirmed if mis-oriented poses get a
    small grid-h but a large se2-h (grid is blind to the needed pivot)."""
    h_se2 = make_field_h(field, nook.goal)
    lines = ["pose (dx,dy,dhead)      grid_h   se2_h"]
    for dh in (0.0, 45.0, 90.0, 135.0, 180.0):
        p = Pose(nook.goal.x_m, nook.goal.y_m, (nook.goal.heading_deg + dh) % 360.0)
        grid_h = math.hypot(nook.goal.x_m - p.x_m, nook.goal.y_m - p.y_m)  # position-only = 0 here
        lines.append(f"  (0,0,{dh:5.0f})          {grid_h:6.2f}   {h_se2(p):6.2f}")
    return "\n".join(lines)


def main() -> None:
    result = run_probe()
    print("=== SE(2) heading-aware heuristic — Step-0 headroom probe (#840) ===")
    print(f"  grid (position-only):  expansions={result.exp_grid}  found={result.found_grid}")
    print(f"  se2  (heading-aware):  expansions={result.exp_se2}  found={result.found_se2}")
    print(f"  euclidean (baseline):  expansions={result.exp_euclid}  found={result.found_euclid}")
    print(f"  ratio (grid/se2) = {result.ratio:.1f}")
    print(f"  VERDICT: {result.verdict}  "
          f"(GO ≥50× · PARTIAL 5–50× · NO-GO <5×; see the spec §4 gate)")
    nook = build_toy_nook()
    r = nook.mover.effective_turn_radius_m()
    with fine_grid():
        obstacles = towplanner._build_obstacles(nook.placed, mover_id=nook.mover.id)
        field = build_se2_field(
            nook.mover, nook.goal, obstacles, nook.hangar.motion_hangar(), r,
            mover_on_carts=nook.mover_on_carts,
        )
        print("\n--- confirmatory heuristic-divergence map (same xy, rotating heading) ---")
        print(_divergence_map(nook, field))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/bench/test_se2_heuristic_probe.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Run the probe and READ the verdict**

Run: `python -m bench.se2_heuristic_probe`
Expected: a table of expansion counts for grid / se2 / euclidean, a ratio, a `VERDICT:` line, and the divergence map. **This output is the deliverable.** Record the numbers and the verdict in the spike record (next step). If the run is slow (>~2 min), the toy fixture is too large — shrink the hangar / budget and re-run (Task 1 calibration).

- [ ] **Step 6: Record the verdict + commit**

Append a short "Step-0 result" section to `docs/spikes/herrenteich-fk9-cessna-lateral-shuffle.md` with the three expansion counts, the ratio, the verdict, and the divergence-map snippet (whatever the run produced — GO, PARTIAL, or NO-GO; record it honestly).

```bash
git add bench/se2_heuristic_probe.py tests/bench/test_se2_heuristic_probe.py docs/spikes/herrenteich-fk9-cessna-lateral-shuffle.md
git commit -m "feat(840): SE(2) heuristic headroom probe driver + Step-0 verdict"
```

---

## After Step 0 (handoff)

- **GO / PARTIAL** → write the **Step 1** plan (promote `build_se2_field` into `towplanner` as `_build_se2_heuristic` + a first-class `heuristic="se2"` mode; validate on the *real isolated* fk9↔cessna pair under `_MAX_FILL_EXPANSIONS = 16 000`; add the determinism double-solve canary; then the all-8 + the §8 husky-ordering guard). PARTIAL means the Step-1 real-pair efficacy run (route under 16 k) is the true arbiter — see spec §4.
- **NO-GO** → the heuristic class is dead; fall to spec §6 (cache the proven 39-min witness `MovesPlan` re-validated by `path_first_conflict`, or document the pair as manual-insertion). Do **not** proceed to Step 1.

## Self-Review (spec coverage)

- Spec §4 Step 0 (synthetic toy nook, monkeypatch fine grid, three-way `plan_path` expansion compare, GO/PARTIAL/NO-GO gate, confirmatory frontier/divergence log) → Tasks 1–4. ✓
- Spec §7 determinism constraint (additive default-OFF seam, canaries byte-identical) → Task 2 Steps 3+5. ✓
- Spec §3.1 dissent (intrinsic-plateau NO-GO) → encoded in the verdict bands + the "After Step 0" NO-GO branch. ✓
- Spec §4 Step 1 / §8 husky guard → explicitly deferred to a GO-contingent follow-up plan (this plan delivers the gate only). ✓
- Placeholder scan: every code step carries complete, runnable code; calibration uncertainty (toy dims) is handled by explicit calibration *tests* with concrete retune instructions, not left as "TBD". ✓
- Type consistency: `ToyNook`/`ProbeResult` fields, `build_se2_field`/`make_field_h`/`run_probe`/`fine_grid` signatures consistent across tasks; `heuristic_fn` name identical in Task 2 and Task 4. ✓
