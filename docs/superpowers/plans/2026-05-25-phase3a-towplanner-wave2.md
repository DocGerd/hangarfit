# Phase 3a Tow-Path Planner — Wave 2 (Module + solve integration) Implementation Plan

> **⚠️ Partially superseded during #197 implementation (2026-05-26).** The
> "Layout valid but un-towable → **fail whole solve**" decision below was
> **reversed to best-effort enrichment** when implementation revealed that the
> v1 planner cannot route *any* dense multi-plane fill — even the project's own
> `layouts/example.yaml`, the tight-hangar `solve_fresh_six_planes.yaml`, and a
> 6-plane fill in the *roomy* 30×25 m test hangar (`solve_pinned_one_plane.yaml`)
> are all un-towable (spike Risk #1 / ADR-0007: Dubins-only + bounded Hybrid-A* with
> documented false-negatives). Fail-whole would have made `hangarfit solve`
> return `no_feasible_plan` for essentially every realistic scenario, discarding
> valid static layouts on a heuristic's false negative. The shipped behaviour:
> `solve()` keeps every valid layout and sets `plans[i] = None` where the planner
> could not route it; the `no_feasible_plan` status was dropped; status stays
> search-driven; the blocking planes are recorded in
> `SolverDiagnostics.unroutable_planes`. A new `plan_paths: bool = True` param
> lets callers skip the (expensive) tow-planning. The CLI passes `plan_paths=False`
> (surfacing tow paths is #193). See the updated row 3 below and the #197 PR.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Wave 1 primitives into a working empty-hangar-fill planner — `plan_fill(target: Layout) -> MovesPlan` with a bounded deterministic order-retry loop (#196) — then have `hangarfit solve` return a bundled `(Layout, MovesPlan)` result (#197).

**Architecture:** Two sequential PRs off `develop`. **PR A (#196)** extends the Wave 1 `plan_dubins` zero-radius branch to handle cart translation (pivot→straight→pivot), adds a door-cone entry-pose helper, and adds `plan_fill` + a structured `NoFeasiblePlanError`. **PR B (#197)** threads `plan_fill` into `solver.solve`: a new `plans` field on `SolveResult` index-aligned with `layouts`, a new `"no_feasible_plan"` status that fires when *any* returned layout cannot be towed (per the locked "fail whole solve" decision), and minimal CLI status handling. The towplanner stays deterministic and closed-form (no RNG); `solve`'s existing RR-MC + seed contract (ADR-0003) is preserved end-to-end through the bundle.

**Tech Stack:** Python 3.12, frozen `@dataclass(slots=True)`, `math` (no numpy), `pytest`. Reuses Wave 1's `plan_dubins` / `back_first_order` / `path_first_conflict` / `DubinsArc.sample` and the existing `collisions.check` oracle unchanged.

---

## Locked design decisions (resolved with the user 2026-05-25)

These three forks were genuinely open after ADR-0007; the user chose, alternatives recorded so the choice is auditable ([[feedback_adversarial_design_review]]).

| Fork | Chosen | Rejected alternatives & why-not |
|---|---|---|
| **Cart door→slot traversal** | **Extend `plan_dubins` r=0** to emit pivot→straight→pivot when start/end positions differ (still one `DubinsArc`, `turn_radius_m=0`). `plan_fill` stays cart-agnostic, honoring ADR-0007's "one motion primitive, no cart special-case in the planner body". | (b) *Separate `_plan_cart_arc` helper* — keeps the Wave 1 primitive test untouched but pushes a `movement_mode` branch into the planner body, against the ADR driver. (c) *Defer carts* — the fleet has 3 `always_cart` planes (Falke, Wild Thing, Zlin Savage); realistic scenarios would hard-fail. |
| **`solve` result shape** | **Parallel `plans: tuple[MovesPlan, ...]` field** on `SolveResult`, index-aligned with `layouts`. Minimal glue (matches the issue's "~20 lines"); CLI keeps reading `.layouts`; backward-compatible (defaults to `()`). | *Bundle/`Candidate` type* (`layouts`→`candidates`) — higher cohesion but churns `SolveResult`, both CLI emit paths, the render/write helpers, and every existing solver test that builds or reads a `SolveResult`. |
| **Layout valid but un-towable** | ~~**Fail whole solve**~~ → **REVERSED to "keep layout, plan=None" during #197 (see banner at top).** Originally: if *any* returned layout's `plan_fill` raises `NoFeasiblePlanError`, `solve` returns `status="no_feasible_plan"` with empty `layouts`/`plans`. Implementation showed the v1 planner cannot route any dense fill, so fail-whole made `solve` near-useless; the rejected "keep layout, plan=None" alternative became the chosen one. | *Drop the layout* — silently discards work (still rejected). The "every returned layout is tow-able" guarantee is intentionally not offered in v1: an un-routable layout is still a valid static arrangement, and the planner's failure is advisory (spike Risk #8). |

**Consequence to carry into Wave 3 (#193 CLI):** because un-towable ⇒ whole-solve failure, the Wave 3 exit-code rule should read "exit non-zero when `status == no_feasible_plan`" — which already falls out of the existing `if not result.layouts: return 1` path (Task 6 keeps that intact). The spike's #193 wording ("non-zero exit when no feasible order exists for any candidate") is *tightened* by this decision; note it on #193 when Wave 3 is planned.

---

## Conventions you must honor (carried from Wave 1)

- **Heading convention (ADR-0002).** `heading_deg` is compass-style: from world `+y`, CW positive. The math-frame angle is `θ = compass_to_math_rad(heading) = radians(90 − heading)`. Positions pass through unchanged; only the heading converts. The Wave 1 `pose_at` integrator already encodes this — Task 1 adds segments it walks, no new trig.
- **Determinism (ADR-0003).** Everything in Wave 2 is closed-form or a total-order sort/scan — **no RNG in the towplanner.** Do not import `random` into `towplanner.py`. `solve`'s existing single `random.Random(seed)` is the only RNG and is untouched.
- **Frozen dataclasses.** Match the house style: `@dataclass(frozen=True, slots=True)`.
- **Auto-test hook.** Editing anything under `src/hangarfit/` or `tests/` triggers a PostToolUse `pytest` run automatically; you also run the targeted commands shown below.
- **Lint/type gates (CI parity).** After each task: `ruff check src/ tests/ && ruff format --check src/ tests/ && mypy src/hangarfit/`.
- **Segment-length asymmetry (Wave 1, load-bearing here).** For a turn `Segment`, `length_m` is **arc length in metres** when `turn_radius_m > 0` but **radians of pivot** when `turn_radius_m == 0`. Task 1's three-segment cart path mixes a radians-pivot, a metres-straight, and a radians-pivot inside one `turn_radius_m=0` arc — `pose_at`/`sample` already handle this. Do not "normalise" it away.

---

## File Structure

| File | Responsibility | Task / PR |
|---|---|---|
| `src/hangarfit/towplanner.py` (modify) | Extend `plan_dubins` r=0 branch; add `entry_pose()`, `NoFeasiblePlanError`, `plan_fill()` | Tasks 1–3 / PR A (#196) |
| `tests/test_towplanner_dubins.py` (modify) | Revise the "moved goal raises" test; add pivot-straight-pivot cases | Task 1 / PR A |
| `tests/test_towplanner_fill.py` (create) | `entry_pose` + `plan_fill` happy/cart/retry/bail/determinism | Tasks 2–3 / PR A |
| `src/hangarfit/models.py` (modify) | `SolveResult.plans`; `"no_feasible_plan"` `SolveStatus`; `SolverDiagnostics.unplannable_plane` | Task 4 / PR B (#197) |
| `src/hangarfit/solver.py` (modify) | Plan every returned layout; bundle or fail | Task 5 / PR B |
| `src/hangarfit/cli.py` (modify) | Minimal `no_feasible_plan` human/JSON message | Task 6 / PR B |
| `tests/test_models.py` (modify) | `SolveResult.plans` invariant; new-status invariant | Task 4 / PR B |
| `tests/test_solver.py` (modify) | bundle alignment; fail-whole-solve; determinism of bundle; regression diff | Task 5 / PR B |
| `tests/test_cli.py` (modify) | `no_feasible_plan` exit code + message | Task 6 / PR B |

`towplanner.py` grows from ~430 to ~520 lines — still one focused module.

---

# PR A — #196: Towplanner module + order-retry loop

Branch: `feature/196-towplanner-module-order-retry` off `develop`.

> **Review subagents for the PR A review pass:** `pr-review-toolkit:code-reviewer` (main), `pr-review-toolkit:silent-failure-hunter` (the retry loop has catch/bail logic). The `geometry-invariant-guard` is **not** required — PR A adds no new caller of `aircraft_parts_world`/`collisions.check` (it reuses Wave 1's `path_first_conflict`) and does not edit `geometry.py`/`collisions.py`.

---

## Task 1: Extend `plan_dubins` zero-radius branch to pivot→straight→pivot

**Files:**
- Modify: `src/hangarfit/towplanner.py` (the `turn_radius_m == 0.0` branch of `plan_dubins`, currently `towplanner.py:338-351`)
- Test: `tests/test_towplanner_dubins.py`

**Why:** A cart-borne plane has `effective_turn_radius_m() == 0`. Wave 1's r=0 branch only pivots in place and *raises* on any position change (pinned by `test_zero_radius_dubins_rejects_translation`, `tests/test_towplanner_dubins.py:233-236`). Decision-1 extends it: when start and end positions differ, the kinematically-faithful r→0 Dubins limit is **pivot to face the goal → drive straight → pivot to final heading**, all representable in one `DubinsArc(turn_radius_m=0, segments=(turn, S, turn))`. The pure-pivot case (positions equal) is preserved unchanged.

- [ ] **Step 1: Revise the now-wrong "raises on translation" test**

In `tests/test_towplanner_dubins.py`, find `test_zero_radius_dubins_rejects_translation` (around line 233). **Replace it** with a test asserting the new behaviour (the Wave 1 contract is deliberately reversed by Decision-1):

```python
def test_zero_radius_translation_is_pivot_straight_pivot() -> None:
    # Cart from origin facing +y, goal 5 m east facing +x.
    # Faithful r->0 Dubins limit: pivot to bearing (+x = compass 90),
    # drive straight 5 m, pivot to final heading (already 90 -> no 3rd pivot).
    start = Pose(0.0, 0.0, 0.0)
    end = Pose(5.0, 0.0, 90.0)
    arc = plan_dubins(start, end, turn_radius_m=0.0)
    assert arc.turn_radius_m == 0.0
    kinds = [s.kind for s in arc.segments]
    # First a pivot (R, since +90 compass delta), then the straight leg.
    assert kinds[0] in ("L", "R")
    assert "S" in kinds
    # The INTEGRATED endpoint must reach the goal pose (not the stored end).
    last = arc.pose_at(arc.length_m)
    assert last.x_m == pytest.approx(5.0, abs=1e-6)
    assert last.y_m == pytest.approx(0.0, abs=1e-6)
    assert _heading_close(last.heading_deg, 90.0)


def test_zero_radius_translation_collinear_is_pure_straight() -> None:
    # Already facing the goal and ending on the same heading: no pivots.
    start = Pose(0.0, 0.0, 0.0)
    end = Pose(0.0, 5.0, 0.0)
    arc = plan_dubins(start, end, turn_radius_m=0.0)
    assert [s.kind for s in arc.segments] == ["S"]
    assert arc.length_m == pytest.approx(5.0)


def test_zero_radius_translation_with_final_pivot() -> None:
    # Goal off-axis AND a final heading change -> all three legs present.
    start = Pose(0.0, 0.0, 0.0)
    end = Pose(3.0, 4.0, 200.0)
    arc = plan_dubins(start, end, turn_radius_m=0.0)
    assert len(arc.segments) == 3
    assert [s.kind for s in arc.segments][1] == "S"
    last = arc.pose_at(arc.length_m)
    assert last.x_m == pytest.approx(3.0, abs=1e-6)
    assert last.y_m == pytest.approx(4.0, abs=1e-6)
    assert _heading_close(last.heading_deg, 200.0)
```

`_heading_close` already exists in this file (Wave 1). The pure-pivot tests (`test_zero_radius_is_pivot_in_place`, `test_zero_radius_pivot_left_for_negative_delta`, `test_zero_radius_pivot_180_resolves_to_short_arc`) stay green untouched — Step 3 keeps the `dist≈0` path identical.

- [ ] **Step 2: Run to verify the revised tests fail**

Run: `pytest tests/test_towplanner_dubins.py -k zero_radius -v`
Expected: the three new `_translation_` tests FAIL (current code raises `ValueError("zero turn radius (cart pivot) requires start and end position to match")`); the three pure-pivot tests still PASS.

- [ ] **Step 3: Extend the r=0 branch**

In `src/hangarfit/towplanner.py`, replace the body of the `if turn_radius_m == 0.0:` branch in `plan_dubins` (currently `towplanner.py:338-351`). Keep the pure-pivot path for `dist ≈ 0`; add pivot→straight→pivot for translation:

```python
    if turn_radius_m == 0.0:
        dx = end.x_m - start.x_m
        dy = end.y_m - start.y_m
        dist = math.hypot(dx, dy)
        if dist <= 1e-9:
            # Pure pivot-in-place (positions coincide): a single turn segment
            # whose length_m encodes the short-arc heading change in radians.
            # Compass is CW-positive, so a positive delta is a right turn ("R")
            # in the math frame the integrator walks; sign pinned by
            # test_zero_radius_is_pivot_in_place.
            dtheta_deg = (end.heading_deg - start.heading_deg + 180.0) % 360.0 - 180.0
            kind: SegmentKind = "R" if dtheta_deg >= 0.0 else "L"
            return DubinsArc(start, end, 0.0, (Segment(kind, abs(math.radians(dtheta_deg))),))
        # Cart translation (ADR-0007 r->0 limit): pivot to the goal bearing,
        # drive straight, pivot to the final heading. Bearing as a compass
        # heading: math angle atan2(dy, dx) converted back to compass.
        bearing_deg = math_rad_to_compass(math.atan2(dy, dx))
        segs: list[Segment] = []
        seg1_deg = (bearing_deg - start.heading_deg + 180.0) % 360.0 - 180.0
        if abs(seg1_deg) > 1e-9:
            segs.append(Segment("R" if seg1_deg >= 0.0 else "L", abs(math.radians(seg1_deg))))
        segs.append(Segment("S", dist))
        seg3_deg = (end.heading_deg - bearing_deg + 180.0) % 360.0 - 180.0
        if abs(seg3_deg) > 1e-9:
            segs.append(Segment("R" if seg3_deg >= 0.0 else "L", abs(math.radians(seg3_deg))))
        return DubinsArc(start, end, 0.0, tuple(segs))
```

> **Correctness note (no new trig):** after the first pivot the integrator's math-frame `theta == compass_to_math_rad(bearing_deg)`, which simplifies to `atan2(dy, dx)` — so the `"S"` leg of length `dist` lands exactly on `end.(x, y)`. The two pivots reuse the *same* sign/length rule as the pure-pivot case. The `pose_at`/`sample` integrator (Wave 1) already walks `R`/`S`/`R` at `r=0` correctly: turns hold position and rotate `theta`, the straight advances along `theta`.

- [ ] **Step 4: Run to verify all zero-radius tests pass**

Run: `pytest tests/test_towplanner_dubins.py -k zero_radius -v`
Expected: PASS (all six). If a `_translation_` endpoint is off, the bug is the bearing conversion or a pivot sign — fix the code, not the test.

- [ ] **Step 5: Run the full Dubins suite (no general-case regression)**

Run: `pytest tests/test_towplanner_dubins.py -v`
Expected: PASS (all) — the `turn_radius_m > 0` general Dubins path is untouched.

- [ ] **Step 6: Lint + type-check, then commit**

```bash
ruff check src/ tests/ && ruff format src/ tests/ && mypy src/hangarfit/
git add src/hangarfit/towplanner.py tests/test_towplanner_dubins.py
git commit -m "feat(towplanner): zero-radius Dubins handles cart translation (pivot-straight-pivot) (#196)"
```

---

## Task 2: Door-cone entry-pose helper

**Files:**
- Modify: `src/hangarfit/towplanner.py` (add `entry_pose()`)
- Test: `tests/test_towplanner_fill.py` (create)

**Why:** Each plane's path starts inside the door interval at `y = 0` pointing into the hangar (spike Q6: "hard door, no apron"). The entry `x` is the target slot's `x` **clamped into the door interval** — a deterministic choice that keeps the approach as straight as the door allows (shorter Dubins arc than a fixed door-centre entry). *Alternative considered:* fixed `x = door.center_x_m` — simpler but forces every plane through one point, lengthening arcs and increasing cross-traffic in the retry loop. Clamping is still deterministic, so ADR-0003 holds.

- [ ] **Step 1: Write the failing entry-pose tests**

Create `tests/test_towplanner_fill.py`:

```python
import math

import pytest

from hangarfit.models import Door, Hangar, MaintenanceBay, Placement
from hangarfit.towplanner import Pose, entry_pose


def _hangar(width_m: float = 20.0, length_m: float = 30.0,
            door_center: float = 10.0, door_width: float = 6.0) -> Hangar:
    return Hangar(
        length_m=length_m,
        width_m=width_m,
        door=Door(center_x_m=door_center, width_m=door_width),
        maintenance_bay=MaintenanceBay(center_x_m=width_m / 2, width_m=2.0, depth_m=2.0),
        clearance_m=0.5,
        wing_layer_clearance_m=0.3,
    )


def _slot(pid: str, x: float, y: float, h: float = 0.0) -> Placement:
    return Placement(plane_id=pid, x_m=x, y_m=y, heading_deg=h, on_carts=False)


def test_entry_pose_is_at_front_pointing_in() -> None:
    h = _hangar()
    e = entry_pose(_slot("A", x=10.0, y=20.0), h)
    assert e.y_m == 0.0
    assert e.heading_deg == 0.0  # nose toward +y, into the hangar


def test_entry_x_equals_slot_x_when_inside_door() -> None:
    h = _hangar(door_center=10.0, door_width=6.0)  # door interval [7, 13]
    e = entry_pose(_slot("A", x=9.0, y=20.0), h)
    assert e.x_m == pytest.approx(9.0)


def test_entry_x_clamps_to_door_interval() -> None:
    h = _hangar(door_center=10.0, door_width=6.0)  # door interval [7, 13]
    assert entry_pose(_slot("A", x=2.0, y=20.0), h).x_m == pytest.approx(7.0)
    assert entry_pose(_slot("B", x=18.0, y=20.0), h).x_m == pytest.approx(13.0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_towplanner_fill.py -k entry -v`
Expected: FAIL — `ImportError: cannot import name 'entry_pose'`.

- [ ] **Step 3: Implement `entry_pose`**

Add to `src/hangarfit/towplanner.py` (import `Hangar` alongside the existing model imports; add to the `from hangarfit.models import ...` line):

```python
def entry_pose(target: Placement, hangar: Hangar) -> Pose:
    """Door-cone entry pose for a plane heading to ``target`` (spike Q6).

    The plane enters at the front boundary (``y = 0``) pointing straight into
    the hangar (``heading_deg = 0`` ⇒ nose toward ``+y``). The entry ``x`` is
    the target slot's ``x`` clamped into the door interval
    ``[center − width/2, center + width/2]`` — a deterministic choice that keeps
    the approach as straight as the door allows.
    """
    door = hangar.door
    half = door.width_m / 2.0
    x = min(max(target.x_m, door.center_x_m - half), door.center_x_m + half)
    return Pose(x_m=x, y_m=0.0, heading_deg=0.0)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_towplanner_fill.py -k entry -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Lint + type-check, then commit**

```bash
ruff check src/ tests/ && ruff format src/ tests/ && mypy src/hangarfit/
git add src/hangarfit/towplanner.py tests/test_towplanner_fill.py
git commit -m "feat(towplanner): door-cone entry-pose helper (#196)"
```

---

## Task 3: `NoFeasiblePlanError` + `plan_fill` order-retry loop

**Files:**
- Modify: `src/hangarfit/towplanner.py` (add `NoFeasiblePlanError`, `plan_fill`)
- Test: `tests/test_towplanner_fill.py`

**Algorithm (deterministic greedy next-feasible with a bounded swap budget — spike Q2):**
Walk `back_first_order(target.placements)`. Maintain `remaining` (in order) and `placed` (committed obstacles). Each iteration, scan `remaining` in order for the first plane whose Dubins path is conflict-free against a `Layout` of `placed`; commit it as a `Move`. Each *rejected* path attempt increments a swap counter; if it exceeds `K = 2 * n_planes`, bail with `NoFeasiblePlanError` naming the offending plane. "Skip to the next feasible plane" *is* the spike's swap, expressed as a deterministic scan. No RNG.

- [ ] **Step 1: Write the failing `plan_fill` tests**

Append to `tests/test_towplanner_fill.py`. The happy-path and cart tests use real geometry; the retry/bail tests `monkeypatch` `path_first_conflict` so the loop logic is tested independently of Dubins geometry:

```python
from hangarfit.models import Aircraft, Layout, Part
from hangarfit.towplanner import MovesPlan, NoFeasiblePlanError, plan_fill
import hangarfit.towplanner as tp


def _aircraft(pid: str, *, movement_mode: str = "always_own_gear",
              turn_radius_m: float | None = 6.0) -> Aircraft:
    # One small rectangular fuselage part; adapt kwargs to models.Aircraft/Part
    # if the constructor differs (see tests/test_geometry.py for the canonical
    # fixture). The id, movement_mode, and turn_radius_m fields are what matter.
    return Aircraft(
        id=pid,
        parts=(Part(name="fuselage", length_m=4.0, width_m=1.0,
                    offset_x_m=0.0, offset_y_m=0.0, layer=0),),
        movement_mode=movement_mode,
        turn_radius_m=turn_radius_m,
    )


def _layout(fleet: dict, hangar, *placements: Placement) -> Layout:
    return Layout(fleet=fleet, hangar=hangar, placements=tuple(placements))


def test_plan_fill_orders_deepest_first_and_plans_all() -> None:
    h = _hangar(width_m=20.0, length_m=30.0)
    fleet = {"A": _aircraft("A"), "B": _aircraft("B")}
    target = _layout(
        fleet, h,
        _slot("A", x=10.0, y=6.0),    # shallow
        _slot("B", x=10.0, y=24.0),   # deep
    )
    plan = plan_fill(target)
    assert isinstance(plan, MovesPlan)
    assert plan.target_layout is target
    # Deepest (B) is towed first.
    assert [m.plane_id for m in plan.moves] == ["B", "A"]
    # Every move ends at its target slot pose.
    for m in plan.moves:
        slot = next(p for p in target.placements if p.plane_id == m.plane_id)
        assert m.target_slot == Pose.from_placement(slot)


def test_plan_fill_plans_cart_plane_with_zero_radius_arc() -> None:
    h = _hangar(width_m=20.0, length_m=30.0)
    fleet = {"C": _aircraft("C", movement_mode="always_cart", turn_radius_m=None)}
    target = _layout(fleet, h, Placement("C", x_m=10.0, y_m=20.0, heading_deg=0.0, on_carts=True))
    plan = plan_fill(target)
    (move,) = plan.moves
    assert move.path.turn_radius_m == 0.0
    last = move.path.pose_at(move.path.length_m)
    assert last.x_m == pytest.approx(10.0, abs=1e-6)
    assert last.y_m == pytest.approx(20.0, abs=1e-6)


def test_plan_fill_is_deterministic() -> None:
    h = _hangar()
    fleet = {"A": _aircraft("A"), "B": _aircraft("B")}
    target = _layout(fleet, h, _slot("A", 10.0, 6.0), _slot("B", 10.0, 24.0))
    assert plan_fill(target) == plan_fill(target)


def test_plan_fill_swaps_past_a_conflicting_plane(monkeypatch) -> None:
    # Force the deepest plane (B, planned first) to conflict once so the loop
    # must skip to A, then succeed on B. We make path_first_conflict return a
    # conflict only for B and only while A is not yet placed.
    h = _hangar()
    fleet = {"A": _aircraft("A"), "B": _aircraft("B")}
    target = _layout(fleet, h, _slot("A", 10.0, 6.0), _slot("B", 10.0, 24.0))
    from hangarfit.models import Conflict

    def fake_conflict(arc, mover, *, mover_on_carts, placed, **kw):
        placed_ids = {p.plane_id for p in placed.placements}
        if mover.id == "B" and "A" not in placed_ids:
            return Conflict.single(kind="parts_overlap", plane="B", detail="forced")
        return None

    monkeypatch.setattr(tp, "path_first_conflict", fake_conflict)
    plan = plan_fill(target)
    # A gets placed first (B was skipped), then B becomes feasible.
    assert [m.plane_id for m in plan.moves] == ["A", "B"]


def test_plan_fill_bails_with_structured_error_after_budget(monkeypatch) -> None:
    h = _hangar()
    fleet = {"A": _aircraft("A"), "B": _aircraft("B")}
    target = _layout(fleet, h, _slot("A", 10.0, 6.0), _slot("B", 10.0, 24.0))
    from hangarfit.models import Conflict

    def always_conflict(arc, mover, *, mover_on_carts, placed, **kw):
        return Conflict.single(kind="parts_overlap", plane=mover.id, detail="forced")

    monkeypatch.setattr(tp, "path_first_conflict", always_conflict)
    with pytest.raises(NoFeasiblePlanError) as ei:
        plan_fill(target)
    assert ei.value.plane_id in {"A", "B"}
    assert ei.value.conflict is not None
```

> **Fixture adaptation note:** `_aircraft`/`Part` kwargs are illustrative. Before running, open `tests/test_geometry.py` and `src/hangarfit/models.py` for the real `Aircraft`/`Part` field names and required args (e.g. `is_low_wing`, `wing_type`, strut fields) and adapt `_aircraft`. The test *intent* — one own-gear plane and one cart plane in a roomy hangar — is what must hold.

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_towplanner_fill.py -k plan_fill -v`
Expected: FAIL — `ImportError: cannot import name 'NoFeasiblePlanError'` / `plan_fill`.

- [ ] **Step 3: Implement `NoFeasiblePlanError` and `plan_fill`**

Add to `src/hangarfit/towplanner.py` (the `Layout` import already exists; add `Move`/`MovesPlan` are defined in this module). Place after `path_first_conflict`:

```python
class NoFeasiblePlanError(Exception):
    """No collision-free entry order found within the retry budget (spike Q2).

    Carries the plane that could not be placed and the last conflict that
    blocked it, so the caller (and Wave 3's CLI) can name the offender.
    """

    def __init__(self, plane_id: str, conflict: Conflict) -> None:
        super().__init__(
            f"no feasible tow order: plane {plane_id!r} could not be placed "
            f"without collision ({conflict.kind})"
        )
        self.plane_id = plane_id
        self.conflict = conflict


def plan_fill(target: Layout) -> MovesPlan:
    """Plan a collision-free entry order + per-plane path for an empty-fill.

    Walks ``back_first_order`` (deepest slot first); for each plane computes the
    door-cone entry pose and the Dubins path to its slot (cart-borne planes use
    ``effective_turn_radius_m() == 0`` ⇒ a pivot-straight-pivot arc). A plane
    whose path conflicts with the already-placed subset is skipped in favour of
    the next feasible plane; after ``2 * n_planes`` rejected attempts the plan
    bails with :class:`NoFeasiblePlanError`. Deterministic (ADR-0003): the order,
    the Dubins primitive, and the skip rule are all RNG-free.
    """
    ordered = list(back_first_order(target.placements))
    fleet = target.fleet
    hangar = target.hangar
    budget = 2 * len(ordered)
    swaps = 0

    placed: list[Placement] = []
    moves: list[Move] = []
    last_conflict: Conflict | None = None

    while ordered:
        chosen: int | None = None
        chosen_arc: DubinsArc | None = None
        for idx, slot in enumerate(ordered):
            plane = fleet[slot.plane_id]
            arc = plan_dubins(
                entry_pose(slot, hangar),
                Pose.from_placement(slot),
                turn_radius_m=plane.effective_turn_radius_m(),
            )
            placed_layout = Layout(
                fleet=fleet,
                hangar=hangar,
                placements=tuple(placed),
                maintenance_plane=target.maintenance_plane,
            )
            conflict = path_first_conflict(
                arc, plane, mover_on_carts=slot.on_carts, placed=placed_layout
            )
            if conflict is None:
                chosen, chosen_arc = idx, arc
                break
            last_conflict = conflict
            swaps += 1
            if swaps > budget:
                raise NoFeasiblePlanError(slot.plane_id, conflict)
        if chosen is None:
            # Every remaining plane conflicts; bail on the first (deepest).
            assert last_conflict is not None
            raise NoFeasiblePlanError(ordered[0].plane_id, last_conflict)
        slot = ordered.pop(chosen)
        assert chosen_arc is not None
        moves.append(Move(slot.plane_id, Pose.from_placement(slot), chosen_arc))
        placed.append(slot)

    return MovesPlan(target_layout=target, moves=tuple(moves))
```

> **Edge note (`path_first_conflict` precondition):** the per-sample `Layout` it builds always includes the mover, and `mover.id` is in `fleet` because `placed ⊆ target.placements`. The `placed`-only `Layout` constructed here re-runs `Layout.__post_init__` each iteration — that is intentional (cart-cap / uniqueness stay enforced); a `ValueError` would be a real bug, not something to swallow.

> **Honest-failure note (out-of-bounds arcs):** an own-gear plane's Dubins arc may momentarily dip to `y < 0` (outside the hangar) for some slot geometries. `path_first_conflict` reuses `collisions.check`, which enforces hangar bounds — so such an arc surfaces as a conflict naming the mover and triggers a skip/bail. That is the intended v1 behaviour (spike Q3 honest weakness), not a bug to special-case.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_towplanner_fill.py -v`
Expected: PASS (all).

- [ ] **Step 5: Full towplanner suite + gates**

Run: `pytest tests/test_towplanner.py tests/test_towplanner_dubins.py tests/test_towplanner_motion.py tests/test_towplanner_fill.py -v && ruff check src/ tests/ && ruff format --check src/ tests/ && mypy src/hangarfit/`
Expected: all PASS, lint/type clean.

- [ ] **Step 6: Commit**

```bash
git add src/hangarfit/towplanner.py tests/test_towplanner_fill.py
git commit -m "feat(towplanner): plan_fill order-retry + NoFeasiblePlanError (#196)"
```

- [ ] **Step 7: Open PR A and run the review arc**

```bash
git push -u origin feature/196-towplanner-module-order-retry
gh pr create --base develop --title "feat(towplanner): module + order-retry loop (#196)" \
  --body "Closes #196

Extends plan_dubins r=0 to pivot-straight-pivot for cart translation (ADR-0007 'one motion primitive'); adds entry_pose, plan_fill, and NoFeasiblePlanError. Deterministic, no RNG. Decisions recorded in docs/superpowers/plans/2026-05-25-phase3a-towplanner-wave2.md."
```

Set assignee=DocGerd + `enhancement` label + milestone "Phase 3a — Tow-path planner v1 (empty-hangar fill)" via `gh api -X PATCH` ([[feedback_pr_metadata]]). Then run `/pr-review` with `pr-review-toolkit:code-reviewer` + `pr-review-toolkit:silent-failure-hunter`; convert findings to diff threads, fix/reply, resolve ([[feedback_resolve_review_threads]]); tell the user it's clean for final review. **Do not merge.**

---

# PR B — #197: `solve` integration (bundled Layout + MovesPlan)

Branch: `feature/197-solve-bundled-movesplan` off `develop` **after #196 merges** (it depends on `plan_fill`). Fetch + pull develop locally before branching ([[feedback_isolation_worktree_stale_base]]).

> **Review subagents for PR B:** `pr-review-toolkit:code-reviewer` (main), `pr-review-toolkit:type-design-analyzer` (**`models.py` changes** — new field + status, per CLAUDE.md), `pr-review-toolkit:silent-failure-hunter` (the fail-whole-solve path).

---

## Task 4: `SolveResult.plans`, `"no_feasible_plan"` status, diagnostics field

**Files:**
- Modify: `src/hangarfit/models.py` (`SolveStatus`, `SolverDiagnostics`, `SolveResult`)
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing model tests**

Append to `tests/test_models.py` (reuse the file's existing `Layout`/`SolverDiagnostics` builders — search for how it constructs a `SolveResult`/`SolverDiagnostics` and adapt):

```python
def test_solveresult_plans_must_align_with_layouts():
    # found/found_partial: len(plans) must equal len(layouts).
    diag = _make_diag()  # adapt: a valid SolverDiagnostics with seed set
    layout = _make_valid_layout()  # adapt: any valid Layout
    with pytest.raises(ValueError, match="plans"):
        SolveResult(status="found", layouts=(layout,), plans=(), diagnostics=diag)


def test_solveresult_no_feasible_plan_has_empty_layouts_and_plans():
    diag = _make_diag()
    sr = SolveResult(status="no_feasible_plan", layouts=(), plans=(), diagnostics=diag)
    assert sr.status == "no_feasible_plan"
    assert sr.layouts == ()
    assert sr.plans == ()


def test_solveresult_no_feasible_plan_rejects_layouts():
    diag = _make_diag()
    layout = _make_valid_layout()
    with pytest.raises(ValueError):
        SolveResult(status="no_feasible_plan", layouts=(layout,), plans=(), diagnostics=diag)


def test_solveresult_plans_defaults_empty_for_backward_compat():
    # Existing callers that build exhausted_budget/trivially_infeasible
    # results without plans keep working.
    diag = _make_diag()
    sr = SolveResult(status="exhausted_budget", layouts=(), diagnostics=diag)
    assert sr.plans == ()
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_models.py -k "solveresult and (plans or no_feasible)" -v`
Expected: FAIL — `TypeError: ... unexpected keyword 'plans'` and the new status not accepted.

- [ ] **Step 3: Extend the models**

In `src/hangarfit/models.py`:

3a. Add the status to the `SolveStatus` literal (`models.py:641`):

```python
SolveStatus = Literal[
    "found",
    "found_partial",
    "exhausted_budget",
    "trivially_infeasible",
    "no_feasible_plan",
]
```

3b. Add the offending-plane field to `SolverDiagnostics` (after `diversity_rejected_count`, `models.py:690`):

```python
    unplannable_plane: str | None = None
```

Extend its docstring with one line: *"``unplannable_plane`` names the plane that defeated the tow-path planner when ``status == 'no_feasible_plan'`` (ADR-0007 order-retry bail); ``None`` otherwise."*

3c. Add `plans` to `SolveResult` and extend its invariant. The class is `frozen=True, slots=True` — add the field with a default and import `MovesPlan` lazily to avoid a models→towplanner import cycle (towplanner imports models). Use a `TYPE_CHECKING` import + string annotation:

```python
# near the top of models.py, with the other typing imports
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from hangarfit.towplanner import MovesPlan
```

```python
@dataclass(frozen=True, slots=True)
class SolveResult:
    ...
    status: SolveStatus
    layouts: tuple[Layout, ...]
    diagnostics: SolverDiagnostics
    plans: tuple["MovesPlan", ...] = ()

    def __post_init__(self) -> None:
        if self.status in ("found", "found_partial") and not self.layouts:
            raise ValueError(f"SolveResult.status={self.status!r} requires at least one layout")
        if self.status in ("exhausted_budget", "trivially_infeasible", "no_feasible_plan") and self.layouts:
            raise ValueError(
                f"SolveResult.status={self.status!r} must have empty layouts, "
                f"got {len(self.layouts)}"
            )
        if self.status in ("found", "found_partial") and len(self.plans) != len(self.layouts):
            raise ValueError(
                f"SolveResult.plans (len {len(self.plans)}) must align with "
                f"layouts (len {len(self.layouts)}) for status={self.status!r}"
            )
```

> **Why a default + string annotation, not a hard import:** `towplanner` imports `models` (for `Layout`, `Conflict`, etc.), so a runtime `from hangarfit.towplanner import MovesPlan` at module top would be a circular import. `TYPE_CHECKING` keeps it type-only; the default `()` keeps every existing `SolveResult(...)` call site valid (backward-compat — Decision-2).

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_models.py -k "solveresult" -v && mypy src/hangarfit/`
Expected: PASS; mypy `Success`.

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/models.py tests/test_models.py
git commit -m "feat(models): SolveResult.plans + no_feasible_plan status (#197)"
```

---

## Task 5: Thread `plan_fill` into `solver.solve`

**Files:**
- Modify: `src/hangarfit/solver.py` (`solve`)
- Test: `tests/test_solver.py`

**Design:** after the accepted layouts are collected, plan each one in order. If any raises `NoFeasiblePlanError`, return `no_feasible_plan` (empty layouts/plans, offending plane in diagnostics). Otherwise attach the aligned `plans` tuple to the existing `found`/`found_partial` result. The pre-search-infeasible and exhausted-budget paths are untouched (they already carry no layouts).

- [ ] **Step 1: Write the failing solver tests**

Append to `tests/test_solver.py` (reuse the file's existing scenario fixtures — find the helper that builds a solvable `Scenario` and adapt):

```python
def test_solve_bundles_a_plan_per_layout(solvable_scenario):
    from hangarfit.towplanner import MovesPlan
    result = solve(solvable_scenario, budget_s=10.0, alternatives=1, seed=7)
    assert result.status in ("found", "found_partial")
    assert len(result.plans) == len(result.layouts)
    assert all(isinstance(p, MovesPlan) for p in result.plans)
    # Each plan targets its paired layout.
    for layout, plan in zip(result.layouts, result.plans):
        assert plan.target_layout is layout
        assert {m.plane_id for m in plan.moves} == {p.plane_id for p in layout.placements}


def test_solve_bundle_is_deterministic_for_a_seed(solvable_scenario):
    a = solve(solvable_scenario, budget_s=10.0, alternatives=1, seed=42)
    b = solve(solvable_scenario, budget_s=10.0, alternatives=1, seed=42)
    assert a.status == b.status
    assert a.layouts == b.layouts
    assert a.plans == b.plans  # towplanner is deterministic; same seed -> same bundle


def test_solve_fails_whole_when_a_layout_is_untowable(solvable_scenario, monkeypatch):
    # Force plan_fill to bail; solve must surface no_feasible_plan, not found.
    import hangarfit.solver as solver_mod
    from hangarfit.towplanner import NoFeasiblePlanError
    from hangarfit.models import Conflict

    def boom(target):
        raise NoFeasiblePlanError("A", Conflict.single(kind="parts_overlap", plane="A", detail="x"))

    monkeypatch.setattr(solver_mod, "plan_fill", boom)
    result = solve(solvable_scenario, budget_s=10.0, alternatives=1, seed=7)
    assert result.status == "no_feasible_plan"
    assert result.layouts == ()
    assert result.plans == ()
    assert result.diagnostics.unplannable_plane == "A"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_solver.py -k "bundle or untowable" -v`
Expected: FAIL — `result.plans` empty / `plan_fill` not imported in solver.

- [ ] **Step 3: Implement the integration**

In `src/hangarfit/solver.py`:

3a. Import the planner near the top (module-level is fine; `towplanner` imports `models`, not `solver`, so no cycle):

```python
from hangarfit.towplanner import NoFeasiblePlanError, plan_fill
```

3b. Replace the `if accepted_layouts:` return block (`solver.py:253-274`) so it plans before returning. Insert the planning loop just before constructing the `SolveResult`:

```python
    if accepted_layouts:
        status: SolveStatus = "found" if len(accepted_layouts) >= alternatives else "found_partial"
        # Tow-plan every layout we're about to return. Fail the whole solve if
        # any is un-towable (ADR-0007 / Decision-3): a valid static layout that
        # cannot be filled is not a usable answer.
        try:
            plans = tuple(plan_fill(layout) for layout in accepted_layouts)
        except NoFeasiblePlanError as e:
            return SolveResult(
                status="no_feasible_plan",
                layouts=(),
                plans=(),
                diagnostics=SolverDiagnostics(
                    restarts_attempted=restart_index,
                    wall_time_s=time.monotonic() - start,
                    best_partial=None,
                    best_partial_layout=None,
                    seed=resolved_seed,
                    diversity_impossible=diversity_impossible,
                    diversity_rejected_count=diversity_rejected_count,
                    unplannable_plane=e.plane_id,
                ),
            )
        return SolveResult(
            status=status,
            layouts=tuple(accepted_layouts),
            plans=plans,
            diagnostics=SolverDiagnostics(
                restarts_attempted=restart_index,
                wall_time_s=elapsed,
                best_partial=None,
                best_partial_layout=None,
                seed=resolved_seed,
                diversity_impossible=diversity_impossible,
                diversity_rejected_count=diversity_rejected_count,
            ),
        )
```

> Note `elapsed` is computed at `solver.py:251` before this block; the failure path recomputes `time.monotonic() - start` so the wall time still includes the planning attempt.

- [ ] **Step 4: Run the new tests**

Run: `pytest tests/test_solver.py -k "bundle or untowable" -v`
Expected: PASS.

- [ ] **Step 5: REGRESSION GUARD — run the *entire* existing solver suite**

This is the load-bearing step for Decision-3. `solve` now does extra work that can fail, so an existing fixture's valid-but-untowable layout would flip `found`→`no_feasible_plan`.

Run: `pytest tests/test_solver.py tests/test_cli.py -v`
Expected: PASS with **no test flipping to `no_feasible_plan`**.

If a previously-`found` test now reports `no_feasible_plan`:
1. Do **not** silence it. Inspect the fixture layout — is it genuinely un-towable (e.g. centre-clustered, pre-#145 geometry) or a planner bug?
2. If it's a real un-towable layout, that is **new information for the user** (the strict decision has teeth). Surface it: name the fixture + offending plane, and ask whether to (a) widen the fixture hangar, (b) re-spread the layout, or (c) revisit the strictness. Do not unilaterally relax the decision.
3. If it's a planner bug (e.g. entry-pose clamp produces an out-of-door arc that shouldn't conflict), fix the planner.

- [ ] **Step 6: Lint + type-check, then commit**

```bash
ruff check src/ tests/ && ruff format --check src/ tests/ && mypy src/hangarfit/
git add src/hangarfit/solver.py tests/test_solver.py
git commit -m "feat(solver): bundle MovesPlan per layout; fail-whole-solve when untowable (#197)"
```

---

## Task 6: Minimal CLI handling for `no_feasible_plan`

**Files:**
- Modify: `src/hangarfit/cli.py` (`_emit_solve_human`; check `_emit_solve_json`)
- Test: `tests/test_cli.py`

**Scope:** keep this minimal — rich `--render-paths` / structured per-segment messaging is Wave 3 (#193). PR B only needs `solve` not to mis-narrate the new status and to exit non-zero (which the existing `if not result.layouts: return 1` already does, since `no_feasible_plan` carries no layouts).

- [ ] **Step 1: Write the failing CLI test**

Append to `tests/test_cli.py` (reuse the file's CLI-invocation harness; find how it runs `cmd_solve`/`main` and captures output):

```python
def test_solve_no_feasible_plan_exits_nonzero_and_explains(tmp_path, monkeypatch, capsys):
    # Force solve() to report no_feasible_plan, assert CLI exit code + message.
    import hangarfit.cli as cli_mod
    from hangarfit.models import SolveResult, SolverDiagnostics

    diag = SolverDiagnostics(
        restarts_attempted=1, wall_time_s=0.1, best_partial=None,
        best_partial_layout=None, seed=7, unplannable_plane="ZULU",
    )
    fake = SolveResult(status="no_feasible_plan", layouts=(), plans=(), diagnostics=diag)
    monkeypatch.setattr(cli_mod, "solve", lambda *a, **k: fake, raising=False)
    # ... invoke the solve subcommand against any loadable scenario fixture ...
    rc = _run_solve_cli(tmp_path)  # adapt to the file's existing CLI runner
    out = capsys.readouterr().out
    assert rc == 1
    assert "no feasible" in out.lower()
    assert "ZULU" in out
```

> **Adaptation note:** `cmd_solve` imports `solve` *inside the function* (`cli.py:289`), so `monkeypatch.setattr(cli_mod, "solve", ...)` won't intercept it. Either (a) monkeypatch `hangarfit.solver.solve`, or (b) drive a real scenario crafted to be un-towable. Prefer (a) for a deterministic unit test; adapt to the test file's established pattern.

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_cli.py -k no_feasible -v`
Expected: FAIL — the human output doesn't mention "no feasible" / the offending plane.

- [ ] **Step 3: Add the `no_feasible_plan` branch to `_emit_solve_human`**

In `src/hangarfit/cli.py`, add a branch alongside the other early-return statuses in `_emit_solve_human` (after the `exhausted_budget` block, before the `found`/`found_partial` tail, `cli.py:215`):

```python
    if result.status == "no_feasible_plan":
        plane = result.diagnostics.unplannable_plane
        print(
            f"No feasible tow order found"
            + (f": plane {plane!r} could not be placed without collision." if plane else ".")
        )
        print("Hint: re-run with --spread, widen the hangar, or relax pins.")
        return
```

Then check `_emit_solve_json` (grep it in `cli.py`): if it switches on status or assumes layouts, add a minimal `no_feasible_plan` case so `--json` emits valid output (status + `unplannable_plane`) rather than crashing. Keep it small; full plan serialization is #193.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_cli.py -k no_feasible -v`
Expected: PASS (rc == 1, message names the plane).

- [ ] **Step 5: Full suite + gates**

Run: `pytest && ruff check src/ tests/ && ruff format --check src/ tests/ && mypy src/hangarfit/`
Expected: all PASS, clean. (Bare `pytest` excludes `slow`; that's fine for the gate.)

- [ ] **Step 6: Commit, push, open PR B**

```bash
git add src/hangarfit/cli.py tests/test_cli.py
git commit -m "feat(cli): report no_feasible_plan status (#197)"
git push -u origin feature/197-solve-bundled-movesplan
gh pr create --base develop --title "feat(towplanner): solve integration — bundled Layout + MovesPlan (#197)" \
  --body "Closes #197

solve() now tow-plans every returned layout and bundles MovesPlan alongside layouts (parallel plans field). Un-towable layout -> no_feasible_plan status (fail-whole-solve, per ADR-0007/Decision-3). CLI reports it and exits non-zero. Decisions in docs/superpowers/plans/2026-05-25-phase3a-towplanner-wave2.md."
```

Set metadata via `gh api -X PATCH`; run `/pr-review` with `code-reviewer` + `type-design-analyzer` + `silent-failure-hunter`; thread/fix/resolve; report clean. **Do not merge.**

---

## Self-Review

**Spec coverage:**
- #196 ("module + order-retry loop") → Tasks 1–3: the cart-translation primitive extension (Decision-1), `entry_pose`, and `plan_fill` with the `K = 2·n` bounded retry + `NoFeasiblePlanError` (spike Q2's swap rule as a deterministic next-feasible scan).
- #197 ("solve integration, bundled Layout + MovesPlan") → Tasks 4–6: `SolveResult.plans` parallel field (Decision-2), `no_feasible_plan` fail-whole-solve (Decision-3), CLI surface.
- ADR-0007 compliance: cart = one motion primitive (extended `plan_dubins`, no cart branch in `plan_fill` body); deterministic bundle preserves ADR-0003; `effective_turn_radius_m()` consumed (Wave 1) and now load-bearing.

**Placeholder scan:** No "TBD"/"handle edge cases". Two deliberate execution-time judgements, both TDD-gated: (1) the exact `Aircraft`/`Part` constructor kwargs in the fill tests (the file's real fixture is the source of truth — adapt, the *intent* is fixed); (2) the `_emit_solve_json` touch (grep-then-minimal — gated by the CLI test). Neither is a logic placeholder.

**Type consistency:** `Pose`, `DubinsArc`, `Segment`, `Move`, `MovesPlan`, `plan_dubins`, `back_first_order`, `path_first_conflict`, `entry_pose`, `plan_fill`, `NoFeasiblePlanError(plane_id, conflict)`, `effective_turn_radius_m()` are referenced consistently. `SolveResult.plans: tuple[MovesPlan, ...]` aligns with `layouts`; `SolverDiagnostics.unplannable_plane: str | None`; `SolveStatus` gains exactly `"no_feasible_plan"`. The `TYPE_CHECKING` import of `MovesPlan` in `models.py` is the one place the models→towplanner type reference is reconciled against the towplanner→models runtime import (no cycle).

**Determinism (ADR-0003) end-to-end:** `plan_fill` is RNG-free (total-order sort + deterministic scan + closed-form Dubins); `solve` plans in the fixed `accepted_layouts` order. `test_solve_bundle_is_deterministic_for_a_seed` pins same-seed → same `(layouts, plans)`. ✓

**Riskiest assumption, called out:** Decision-3 ("fail whole solve") makes `solve` newly able to fail on layouts it previously returned. Task 5 Step 5 is the explicit regression guard; if a fixture flips, the plan routes it to the user rather than silently relaxing the decision.
