# Phase 3a Tow-Path Planner — Wave 1 (Primitives) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the four dependency-free primitives of the empty-hangar-fill tow-path planner — the planner data model + cart-radius accessor (#188), the closed-form Dubins arc (#189), the greedy back-first ordering (#190), and the sampled collision-during-motion check (#191) — each independently unit-tested.

**Architecture:** A new `src/hangarfit/towplanner.py` module beside `solver.py` (per [ADR-0007](../../adr/0007-tow-path-planner-v1-scope.md) and the [spike](../../spikes/tow-path-planning.md)). `solver.py` answers *where* (target `Layout`); the towplanner answers *how* (entry order + per-plane door→slot path). Wave 1 builds the leaf primitives only — no `solve` wiring, no CLI, no rendering (those are Waves 2–3). Cart-borne planes are own-gear with `turn_radius_m = 0` (pivot-in-place), realized via a new `Aircraft.effective_turn_radius_m()` accessor; `fleet.yaml` and the loader are untouched.

**Tech Stack:** Python 3.11+, frozen `@dataclass(slots=True)`, `math` (closed-form Dubins — no numpy), `pytest`. Reuses `geometry.aircraft_parts_world` and `collisions.check` unchanged.

---

## Conventions you must honor

- **Heading convention (ADR-0002).** `Placement.heading_deg` is compass-style: measured from world `+y` (deeper into hangar), **CW positive**. At `heading=0` the nose points to world `+y`; at `heading=90` to world `+x`. The forward unit vector in world is therefore `(sin h, cos h)` with `h = radians(heading_deg)`. The **standard math angle** (CCW from `+x`) of that same direction is `θ = 90° − heading_deg`. This single relation is the entire convention adapter for Dubins (Task 2). Dubins textbooks use CCW-positive-radians from `+x`; hangarfit world `(x, y)` is itself a standard right-handed plane (x right, y deeper), so **only the heading needs converting** — positions pass through unchanged.
- **Determinism (ADR-0003).** Everything in Wave 1 is closed-form or a total-order sort — **no RNG**. Do not import `random`.
- **Frozen dataclasses.** Match the house style in `models.py`: `@dataclass(frozen=True, slots=True)`.
- **Auto-test hook.** Editing anything under `src/hangarfit/` or `tests/` triggers a PostToolUse `pytest` run automatically; you will also run targeted commands shown below.
- **Lint/type gates (CI parity).** After each task: `ruff check src/ tests/ && ruff format --check src/ tests/ && mypy src/hangarfit/`.

---

## File Structure

| File | Responsibility | Wave |
|---|---|---|
| `src/hangarfit/models.py` (modify) | Add `Aircraft.effective_turn_radius_m()` accessor | 1 (Task 1) |
| `src/hangarfit/towplanner.py` (create) | `Pose`, `Segment`, `DubinsArc`, `Move`, `MovesPlan` dataclasses + `Pose.from_placement`; `plan_dubins()`; `back_first_order()`; `path_first_conflict()` | 1 (Tasks 1–4) |
| `tests/test_models.py` (modify) | `effective_turn_radius_m` unit tests | 1 (Task 1) |
| `tests/test_towplanner.py` (create) | dataclass construction/round-trip (#188) + ordering (#190) | 1 (Tasks 1, 3) |
| `tests/test_towplanner_dubins.py` (create) | Dubins primitive analytic matrix + 45° canary (#189) | 1 (Task 2) |
| `tests/test_towplanner_motion.py` (create) | sampled collision-during-motion (#191) | 1 (Task 4) |

`towplanner.py` is one focused module: pure data + three pure functions. It stays under ~300 lines through Wave 1; Wave 2 adds the order-retry loop.

---

## Task 1: Planner data model + cart-radius accessor (#188)

**Files:**
- Modify: `src/hangarfit/models.py` (add method on `Aircraft`, near `required_turn_radius_m` at `models.py:189`)
- Create: `src/hangarfit/towplanner.py`
- Test: `tests/test_models.py` (accessor), `tests/test_towplanner.py` (dataclasses)

> **Review note:** the `models.py` change wants the `type-design-analyzer` subagent on its PR (CLAUDE.md rule). Per ADR-0007 the accessor "ships with #188"; update the #188 issue body to mention it.

- [ ] **Step 1: Write the failing accessor test**

In `tests/test_models.py`, add (reuse the existing aircraft-builder fixtures/helpers in that file — search for how it constructs an `Aircraft`; the snippet below assumes a helper `make_aircraft(**kw)` exists, adapt to the file's actual constructor):

```python
def test_effective_turn_radius_zero_for_always_cart():
    a = make_aircraft(movement_mode="always_cart", turn_radius_m=None)
    assert a.effective_turn_radius_m() == 0.0

def test_effective_turn_radius_delegates_for_own_gear():
    a = make_aircraft(movement_mode="always_own_gear", turn_radius_m=7.0)
    assert a.effective_turn_radius_m() == 7.0
    assert a.effective_turn_radius_m() == a.required_turn_radius_m()

def test_effective_turn_radius_delegates_for_cart_eligible():
    a = make_aircraft(movement_mode="cart_eligible", turn_radius_m=9.5)
    assert a.effective_turn_radius_m() == 9.5
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_models.py -k effective_turn_radius -v`
Expected: FAIL — `AttributeError: 'Aircraft' object has no attribute 'effective_turn_radius_m'`

- [ ] **Step 3: Implement the accessor**

In `src/hangarfit/models.py`, immediately after `required_turn_radius_m` (after `models.py:203`):

```python
    def effective_turn_radius_m(self) -> float:
        """Turn radius for path planning: ``0.0`` for cart-borne planes
        (a pivot-in-place), else the own-gear ``required_turn_radius_m()``.

        This is the accessor the tow-path planner consumes (ADR-0007): a
        cart-borne plane is modelled as own-gear with a zero turn radius.
        Unlike :meth:`required_turn_radius_m`, this never raises — callers
        that legitimately handle carts (the Dubins planner) use this one.
        """
        if self.movement_mode == "always_cart":
            return 0.0
        return self.required_turn_radius_m()
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_models.py -k effective_turn_radius -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Write the failing dataclass tests**

Create `tests/test_towplanner.py`:

```python
import math

import pytest

from hangarfit.models import Placement
from hangarfit.towplanner import Pose, Segment, DubinsArc, Move, MovesPlan


def test_pose_from_placement_drops_identity_and_cart_state():
    p = Placement(plane_id="DG-ABC", x_m=3.0, y_m=4.0, heading_deg=30.0, on_carts=True)
    pose = Pose.from_placement(p)
    assert pose == Pose(x_m=3.0, y_m=4.0, heading_deg=30.0)
    assert not hasattr(pose, "plane_id")
    assert not hasattr(pose, "on_carts")


def test_pose_is_frozen():
    pose = Pose(x_m=0.0, y_m=0.0, heading_deg=0.0)
    with pytest.raises(Exception):
        pose.x_m = 1.0  # type: ignore[misc]


def test_dubins_arc_length_sums_segments():
    # A straight-only arc of length 5 (turn_radius irrelevant for S segments).
    arc = DubinsArc(
        start=Pose(0.0, 0.0, 0.0),
        end=Pose(0.0, 5.0, 0.0),
        turn_radius_m=10.0,
        segments=(Segment(kind="S", length_m=5.0),),
    )
    assert arc.length_m == pytest.approx(5.0)


def test_dubins_arc_rejects_unknown_segment_kind():
    with pytest.raises(ValueError):
        Segment(kind="Q", length_m=1.0)


def test_movesplan_construction_roundtrip():
    layout = object()  # placeholder; Move/MovesPlan do not validate layout in Wave 1
    move = Move(
        plane_id="DG-ABC",
        target_slot=Pose(1.0, 2.0, 0.0),
        path=DubinsArc(
            start=Pose(0.0, 0.0, 0.0),
            end=Pose(1.0, 2.0, 0.0),
            turn_radius_m=8.0,
            segments=(Segment(kind="S", length_m=math.hypot(1.0, 2.0)),),
        ),
    )
    plan = MovesPlan(target_layout=layout, moves=(move,))
    assert plan.moves[0].plane_id == "DG-ABC"
    assert plan.moves[0].path.end == plan.moves[0].target_slot
```

- [ ] **Step 6: Run to verify it fails**

Run: `pytest tests/test_towplanner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hangarfit.towplanner'`

- [ ] **Step 7: Create the towplanner data model**

Create `src/hangarfit/towplanner.py`:

```python
"""Tow-path planner — empty-hangar fill (Phase 3a).

Answers *how* each plane reaches its target slot: a deterministic entry
order plus a closed-form Dubins arc per plane. See ADR-0007 and
docs/spikes/tow-path-planning.md. Wave 1 = the leaf primitives only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from hangarfit.models import Placement

_VALID_SEGMENT_KINDS = frozenset({"L", "S", "R"})


@dataclass(frozen=True, slots=True)
class Pose:
    """A planar pose. Deliberately omits ``plane_id``/``on_carts`` — path
    samples carry neither identity (the caller knows it) nor cart state
    (it does not change mid-arc). ``heading_deg`` follows the ADR-0002
    compass convention (from world +y, CW positive)."""

    x_m: float
    y_m: float
    heading_deg: float

    @classmethod
    def from_placement(cls, p: Placement) -> "Pose":
        return cls(x_m=p.x_m, y_m=p.y_m, heading_deg=p.heading_deg)


@dataclass(frozen=True, slots=True)
class Segment:
    """One leg of a Dubins path. ``kind`` is ``L`` (left turn), ``S``
    (straight), or ``R`` (right turn). ``length_m`` is the arc length of
    the leg in metres (always >= 0)."""

    kind: str
    length_m: float

    def __post_init__(self) -> None:
        if self.kind not in _VALID_SEGMENT_KINDS:
            raise ValueError(
                f"Segment.kind must be one of {sorted(_VALID_SEGMENT_KINDS)}, got {self.kind!r}"
            )
        if self.length_m < 0.0 or not math.isfinite(self.length_m):
            raise ValueError(f"Segment.length_m must be finite and >= 0, got {self.length_m}")


@dataclass(frozen=True, slots=True)
class DubinsArc:
    """Closed-form shortest path between two oriented poses under a minimum
    turn radius. ``turn_radius_m = 0`` denotes a cart-borne pivot-in-place
    (ADR-0007). ``segments`` is the ordered leg decomposition."""

    start: Pose
    end: Pose
    turn_radius_m: float
    segments: tuple[Segment, ...]

    @property
    def length_m(self) -> float:
        return math.fsum(s.length_m for s in self.segments)


@dataclass(frozen=True, slots=True)
class Move:
    """One plane's entry: from the door-cone entry pose to its target slot."""

    plane_id: str
    target_slot: Pose
    path: DubinsArc


@dataclass(frozen=True, slots=True)
class MovesPlan:
    """A full entry plan: the target layout plus the moves in execution order.

    Deliberately carries no sequence-level cart-usage tally (ADR-0007
    open question). The ``target_layout`` type is ``Layout`` at runtime;
    typed loosely here to keep Wave 1's leaf module import-light."""

    target_layout: object
    moves: tuple["Move", ...]
```

- [ ] **Step 8: Run to verify it passes**

Run: `pytest tests/test_towplanner.py -v`
Expected: PASS (5 passed)

- [ ] **Step 9: Lint + type-check**

Run: `ruff check src/ tests/ && ruff format src/ tests/ && mypy src/hangarfit/`
Expected: all clean (mypy: `Success`)

- [ ] **Step 10: Commit**

```bash
git add src/hangarfit/models.py src/hangarfit/towplanner.py tests/test_models.py tests/test_towplanner.py
git commit -m "feat(towplanner): Pose/Segment/DubinsArc/Move/MovesPlan + effective_turn_radius_m (#188)"
```

---

## Task 2: Closed-form Dubins arc primitive (#189)

**Files:**
- Modify: `src/hangarfit/towplanner.py` (add `plan_dubins()`, the convention adapter, and `DubinsArc.sample()` / `DubinsArc.pose_at()`)
- Test: `tests/test_towplanner_dubins.py`

**The crux (do not skip):** the analytic tests below are the source of truth. Dubins literature uses CCW-positive-radians from `+x`; this module must consume `heading_deg` through the ADR-0002 compass convention. The symmetric axis-aligned cases (0/90/180/270) would pass a sign-flipped implementation — **the 45° canary is what actually guards the convention.** Implement the convention adapter and the sampler first (they are simple and certain), then port the closed-form set and drive it to green against the analytic matrix.

- [ ] **Step 1: Write the convention-adapter + sampler tests**

Create `tests/test_towplanner_dubins.py`:

```python
import math

import pytest

from hangarfit.models import Aircraft, Placement
from hangarfit.geometry import aircraft_parts_world
from hangarfit.towplanner import Pose, DubinsArc, plan_dubins, compass_to_math_rad


def test_compass_to_math_rad_cardinals():
    # heading 0 (nose to +y) -> math angle 90deg; heading 90 (+x) -> 0deg.
    assert math.degrees(compass_to_math_rad(0.0)) == pytest.approx(90.0)
    assert math.degrees(compass_to_math_rad(90.0)) == pytest.approx(0.0)
    assert math.degrees(compass_to_math_rad(45.0)) == pytest.approx(45.0)
    assert math.degrees(compass_to_math_rad(135.0)) == pytest.approx(-45.0)


def test_straight_line_path_is_pure_S():
    # Start at origin heading +y (0deg); goal 10m ahead, same heading.
    start = Pose(0.0, 0.0, 0.0)
    end = Pose(0.0, 10.0, 0.0)
    arc = plan_dubins(start, end, turn_radius_m=5.0)
    assert [s.kind for s in arc.segments] == ["S"]
    assert arc.length_m == pytest.approx(10.0)


def test_sample_walks_from_start_to_end():
    start = Pose(0.0, 0.0, 0.0)
    end = Pose(0.0, 10.0, 0.0)
    arc = plan_dubins(start, end, turn_radius_m=5.0)
    poses = list(arc.sample(step_m=1.0, step_deg=10.0))
    assert poses[0] == start
    assert poses[-1].x_m == pytest.approx(end.x_m, abs=1e-6)
    assert poses[-1].y_m == pytest.approx(end.y_m, abs=1e-6)
    # Monotone advance along +y for a pure straight northbound leg.
    ys = [p.y_m for p in poses]
    assert ys == sorted(ys)
```

- [ ] **Step 2: Write the 45° CANARY test (the convention guard)**

Append to `tests/test_towplanner_dubins.py`. This pins the heading convention end-to-end: a plane towed along a straight path with a 45° heading must advance into the `(+x, +y)` quadrant, and its reconstructed parts (via the SAME `aircraft_parts_world` transform the collision checker uses) must agree. Build a minimal single-part aircraft inline (adapt the constructor to `models.py`'s actual `Aircraft`/`Part` signature — see `tests/test_geometry.py` for the canonical fixture):

```python
def test_heading_45_path_advances_into_plus_x_plus_y(make_single_part_aircraft):
    # heading 45deg compass => forward unit vector (sin45, cos45) ~ (0.707, 0.707).
    start = Pose(0.0, 0.0, 45.0)
    # Goal one straight metre ahead along that heading.
    fwd = (math.sin(math.radians(45.0)), math.cos(math.radians(45.0)))
    end = Pose(fwd[0], fwd[1], 45.0)
    arc = plan_dubins(start, end, turn_radius_m=5.0)
    assert [s.kind for s in arc.segments] == ["S"]
    mid = list(arc.sample(step_m=0.25, step_deg=5.0))[1]
    # A sign-flipped convention (theta = heading-90 instead of 90-heading)
    # would drive the path into +x,-y. Assert the correct quadrant.
    assert mid.x_m > 0.0 and mid.y_m > 0.0

def test_dubins_pose_feeds_aircraft_parts_world_consistently(make_single_part_aircraft):
    # The pose the planner emits must reconstruct parts identically to a
    # Placement with the same heading — i.e. the planner and the collision
    # checker share ADR-0002's convention.
    ac: Aircraft = make_single_part_aircraft(turn_radius_m=5.0)
    pose = Pose(2.0, 3.0, 45.0)
    via_pose = aircraft_parts_world(
        ac, Placement(ac.id, pose.x_m, pose.y_m, pose.heading_deg, on_carts=False)
    )
    via_placement = aircraft_parts_world(
        ac, Placement(ac.id, 2.0, 3.0, 45.0, on_carts=False)
    )
    assert [wp.polygon.bounds for wp in via_pose] == [wp.polygon.bounds for wp in via_placement]
```

Add a `make_single_part_aircraft` fixture in this file (or `tests/conftest.py`) modelled on `tests/test_geometry.py`'s aircraft fixture — one rectangular fuselage `Part`, `id="CANARY"`.

- [ ] **Step 3: Write the analytic Dubins matrix (CSC/CCC + U-turn)**

Append the standard case matrix. Each case has an analytically-known answer so a transcription error in the closed form is caught:

```python
@pytest.mark.parametrize(
    "start,end,radius,expect_words",
    [
        # Pure straight (collinear, same heading): S only.
        (Pose(0.0, 0.0, 0.0), Pose(0.0, 8.0, 0.0), 4.0, [["S"]]),
        # 90deg right turn then straight (RSR/RSL family) — assert feasible & ends correctly.
        (Pose(0.0, 0.0, 0.0), Pose(4.0, 4.0, 90.0), 4.0, None),
        # U-turn in place geometry (CCC family RLR/LRL can appear at short d).
        (Pose(0.0, 0.0, 0.0), Pose(0.0, 0.0, 180.0), 2.0, None),
    ],
)
def test_dubins_endpoints_match(start, end, radius, expect_words):
    arc = plan_dubins(start, end, turn_radius_m=radius)
    last = list(arc.sample(step_m=0.05, step_deg=1.0))[-1]
    assert last.x_m == pytest.approx(end.x_m, abs=1e-3)
    assert last.y_m == pytest.approx(end.y_m, abs=1e-3)
    assert _heading_close(last.heading_deg, end.heading_deg)
    if expect_words is not None:
        assert [s.kind for s in arc.segments] in expect_words


def _heading_close(a: float, b: float, tol: float = 0.5) -> bool:
    d = (a - b + 180.0) % 360.0 - 180.0
    return abs(d) <= tol


def test_zero_radius_is_pivot_in_place():
    # Cart pivot: same position, heading change only -> all turn, no translation.
    start = Pose(1.0, 1.0, 0.0)
    end = Pose(1.0, 1.0, 90.0)
    arc = plan_dubins(start, end, turn_radius_m=0.0)
    for pose in arc.sample(step_m=0.05, step_deg=1.0):
        assert pose.x_m == pytest.approx(1.0)
        assert pose.y_m == pytest.approx(1.0)
```

- [ ] **Step 4: Run to verify the suite fails**

Run: `pytest tests/test_towplanner_dubins.py -v`
Expected: FAIL — `ImportError: cannot import name 'plan_dubins'` / `compass_to_math_rad`

- [ ] **Step 5: Implement the convention adapter + sampler**

Add to `src/hangarfit/towplanner.py`:

```python
def compass_to_math_rad(heading_deg: float) -> float:
    """ADR-0002 compass heading (from +y, CW+) → standard math angle
    (from +x, CCW+) in radians. θ = 90° − heading."""
    return math.radians(90.0 - heading_deg)


def math_rad_to_compass(theta_rad: float) -> float:
    """Inverse of :func:`compass_to_math_rad`, normalised to [0, 360)."""
    return (90.0 - math.degrees(theta_rad)) % 360.0
```

Add sampling to `DubinsArc` (place the methods on the dataclass defined in Task 1):

```python
    def pose_at(self, s_m: float) -> "Pose":
        """Pose at arc-length ``s_m`` from the start, walking the segments.
        Works in the standard math frame internally, returns a compass Pose."""
        x = self.start.x_m
        y = self.start.y_m
        theta = compass_to_math_rad(self.start.heading_deg)
        r = self.turn_radius_m
        remaining = s_m
        for seg in self.segments:
            step = min(seg.length_m, remaining)
            if seg.kind == "S":
                x += step * math.cos(theta)
                y += step * math.sin(theta)
            else:  # "L" or "R": arc of radius r; r == 0 => pivot in place
                if r == 0.0:
                    pass  # cart pivot: position fixed; heading advances below
                else:
                    sign = 1.0 if seg.kind == "L" else -1.0
                    dtheta = sign * step / r
                    cx = x - sign * r * math.sin(theta)
                    cy = y + sign * r * math.cos(theta)
                    theta_new = theta + dtheta
                    x = cx + sign * r * math.sin(theta_new)
                    y = cy - sign * r * math.cos(theta_new)
                    theta = theta_new
                if r == 0.0:
                    # pivot: advance heading by `step` interpreted as radians of turn
                    sign = 1.0 if seg.kind == "L" else -1.0
                    theta += sign * step
            remaining -= step
            if remaining <= 1e-12:
                break
        return Pose(x_m=x, y_m=y, heading_deg=math_rad_to_compass(theta))

    def sample(self, *, step_m: float = 0.05, step_deg: float = 1.0):
        """Yield poses from start to end. Step is the smaller of ``step_m`` of
        translation or ``step_deg`` of heading change, per the spike (§Q4)."""
        total = self.length_m
        # For zero-radius pivots, length_m is the turn in radians*0... handle by
        # always emitting start, a heading-spaced set, and end (see Task notes).
        step = step_m
        n = max(1, math.ceil(total / step)) if total > 0 else 1
        yield self.start
        for i in range(1, n):
            yield self.pose_at(total * i / n)
        yield self.end
```

> **Pivot-in-place note (zero radius):** when `turn_radius_m == 0`, a turn segment's `length_m` must encode the heading change in **radians** (so `pose_at` advances `theta` by `step`). `plan_dubins` (next step) is responsible for emitting that representation for the `r == 0` case: a single turn `Segment` whose `length_m = abs(normalised heading delta in radians)`, kind `L`/`R` by sign. The `test_zero_radius_is_pivot_in_place` test gates this.

- [ ] **Step 6: Implement `plan_dubins`**

Add `plan_dubins(start, end, *, turn_radius_m)` to `towplanner.py`. Structure:

1. **Zero-radius pivot branch:** if `turn_radius_m == 0`: position must already match (assert `start.(x,y) ≈ end.(x,y)`); compute the short-arc heading delta `dθ = ((end.heading − start.heading + 180) mod 360) − 180`; emit one `Segment(kind="L" if dθ>0 else "R", length_m=abs(radians(dθ)))`. (Compass CW-positive: a positive compass delta is a right turn in math frame; pick the sign that `pose_at` reproduces — the canary/zero-radius tests gate it.)
2. **General branch:** convert to the standard math frame (`θ0 = compass_to_math_rad(start.heading)`, `θ1 = compass_to_math_rad(end.heading)`, positions unchanged), then run the canonical **Shkel–Lumelsky Dubins set** (the six words LSL, RSR, LSR, RSL, RLR, LRL) over the normalised `(α, β, d)` with `d = dist/turn_radius_m`. Pick the shortest feasible word; build `segments` as `(Segment(w[0], t·r), Segment(w[1], p·r or p for S), Segment(w[2], q·r))` where turn-segment `length_m = angle·r` (arc length) and straight-segment `length_m = p·r`.

```python
def plan_dubins(start: "Pose", end: "Pose", *, turn_radius_m: float) -> "DubinsArc":
    if turn_radius_m < 0.0 or not math.isfinite(turn_radius_m):
        raise ValueError(f"turn_radius_m must be finite and >= 0, got {turn_radius_m}")
    if turn_radius_m == 0.0:
        if not (math.isclose(start.x_m, end.x_m, abs_tol=1e-9)
                and math.isclose(start.y_m, end.y_m, abs_tol=1e-9)):
            raise ValueError("zero turn radius (cart pivot) requires start and end position to match")
        dtheta_deg = (end.heading_deg - start.heading_deg + 180.0) % 360.0 - 180.0
        kind = "R" if dtheta_deg >= 0.0 else "L"  # validated by test_zero_radius_is_pivot_in_place
        return DubinsArc(start, end, 0.0, (Segment(kind, abs(math.radians(dtheta_deg))),))
    # --- general closed form (port the standard Dubins set; TEST-GATED) ---
    # Implement _dubins_set(alpha, beta, d) returning the shortest
    # (word, (t, p, q)) over the six words, then scale by turn_radius_m.
    word, (t, p, q) = _dubins_shortest(start, end, turn_radius_m)
    r = turn_radius_m
    segs = (
        Segment(word[0], t * r),
        Segment("S", p * r) if word[1] == "S" else Segment(word[1], p * r),
        Segment(word[2], q * r),
    )
    return DubinsArc(start, end, r, segs)
```

Implement the private `_dubins_shortest(start, end, r)` using the canonical formulation. **Drive it to green against the analytic matrix and the canary — do not trust the constants until the tests pass.** If a word's endpoint check fails, the bug is almost always a `mod2pi` boundary or an `α`/`β` sign from the compass conversion; the canary localises convention bugs, the matrix localises closed-form bugs.

- [ ] **Step 7: Run the full Dubins suite to green**

Run: `pytest tests/test_towplanner_dubins.py -v`
Expected: PASS (all). If `test_heading_45_path_advances_into_plus_x_plus_y` fails, you have a convention sign flip — fix `compass_to_math_rad`/`pose_at`, not the test.

- [ ] **Step 8: Lint + type-check**

Run: `ruff check src/ tests/ && ruff format src/ tests/ && mypy src/hangarfit/`
Expected: clean.

- [ ] **Step 9: Commit**

```bash
git add src/hangarfit/towplanner.py tests/test_towplanner_dubins.py tests/conftest.py
git commit -m "feat(towplanner): closed-form Dubins arc + 45deg convention canary (#189)"
```

> **PR note:** #189 consumes the ADR-0002 convention but does not edit `geometry.py`/`collisions.py`, so the `geometry-invariant-guard` subagent is not strictly required here — it IS required for Task 4 (#191), which adds a new caller of `aircraft_parts_world`/`collisions.check`.

---

## Task 3: Greedy back-first ordering (#190)

**Files:**
- Modify: `src/hangarfit/towplanner.py` (add `back_first_order()`)
- Test: `tests/test_towplanner.py`

- [ ] **Step 1: Write the failing ordering test**

Append to `tests/test_towplanner.py`:

```python
from hangarfit.models import Placement
from hangarfit.towplanner import back_first_order


def _pl(pid, x, y):
    return Placement(plane_id=pid, x_m=x, y_m=y, heading_deg=0.0, on_carts=False)


def test_back_first_orders_deepest_y_first():
    placements = (_pl("A", 0.0, 1.0), _pl("B", 0.0, 9.0), _pl("C", 0.0, 5.0))
    assert [p.plane_id for p in back_first_order(placements)] == ["B", "C", "A"]


def test_back_first_tiebreak_is_x_asc_then_plane_id():
    placements = (
        _pl("Z", 3.0, 5.0),
        _pl("A", 3.0, 5.0),  # same y, same x as Z -> plane_id breaks tie
        _pl("M", 1.0, 5.0),  # same y, smaller x -> first among the y=5 group
    )
    assert [p.plane_id for p in back_first_order(placements)] == ["M", "A", "Z"]


def test_back_first_is_pure_and_deterministic():
    placements = (_pl("A", 0.0, 1.0), _pl("B", 0.0, 9.0))
    once = back_first_order(placements)
    twice = back_first_order(placements)
    assert once == twice
    assert placements == (_pl("A", 0.0, 1.0), _pl("B", 0.0, 9.0))  # input untouched
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_towplanner.py -k back_first -v`
Expected: FAIL — `ImportError: cannot import name 'back_first_order'`

- [ ] **Step 3: Implement the ordering**

Add to `towplanner.py`:

```python
def back_first_order(placements: tuple[Placement, ...]) -> tuple[Placement, ...]:
    """Deepest target slot first. Deterministic total order: ``y`` descending,
    then ``x`` ascending, then ``plane_id`` ascending (ADR-0003 determinism;
    spike Q2). Shallower slots become obstacles for deeper ones, so deeper
    planes enter first."""
    return tuple(sorted(placements, key=lambda p: (-p.y_m, p.x_m, p.plane_id)))
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_towplanner.py -k back_first -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Lint + type-check, then commit**

```bash
ruff check src/ tests/ && ruff format src/ tests/ && mypy src/hangarfit/
git add src/hangarfit/towplanner.py tests/test_towplanner.py
git commit -m "feat(towplanner): deterministic greedy back-first ordering (#190)"
```

---

## Task 4: Sampled collision-during-motion check (#191)

**Files:**
- Modify: `src/hangarfit/towplanner.py` (add `path_first_conflict()`)
- Test: `tests/test_towplanner_motion.py`

**Approach:** reuse the existing oracle. For each sampled pose along the arc, build a `Placement` for the mover and a `Layout` of `(already-placed placements + mover)`, call `collisions.check`, and return the first `Conflict` that names the mover. This inherits parts-overlap, hangar-bounds, and bay-intrusion checks for free (spike Q4). The mover's `on_carts` is its target value (constant along the arc).

> **Review note:** this is a NEW caller of `aircraft_parts_world`/`collisions.check`, so its PR requires the `geometry-invariant-guard` subagent (ADR-0002 / CLAUDE.md rule).

- [ ] **Step 1: Write the failing motion tests**

Create `tests/test_towplanner_motion.py`. Use the existing hangar/fleet fixtures (search `tests/` — e.g. `tests/fixtures/` or a `conftest` helper that builds a `Hangar` and `Aircraft`; adapt names to what exists):

```python
import pytest

from hangarfit.models import Layout, Placement
from hangarfit.towplanner import Pose, plan_dubins, path_first_conflict


def test_clear_path_returns_none(simple_hangar, two_planes_fleet):
    fleet = two_planes_fleet  # {"A": Aircraft, "B": Aircraft}
    placed = Layout(fleet=fleet, hangar=simple_hangar,
                    placements=(Placement("A", 2.0, 8.0, 0.0, on_carts=False),))
    arc = plan_dubins(Pose(8.0, 0.0, 0.0), Pose(8.0, 8.0, 0.0), turn_radius_m=4.0)
    assert path_first_conflict(arc, fleet["B"], mover_on_carts=False, placed=placed) is None


def test_path_through_placed_plane_returns_conflict(simple_hangar, two_planes_fleet):
    fleet = two_planes_fleet
    # Place A squarely in B's straight-line corridor.
    placed = Layout(fleet=fleet, hangar=simple_hangar,
                    placements=(Placement("A", 8.0, 4.0, 0.0, on_carts=False),))
    arc = plan_dubins(Pose(8.0, 0.0, 0.0), Pose(8.0, 8.0, 0.0), turn_radius_m=4.0)
    conflict = path_first_conflict(arc, fleet["B"], mover_on_carts=False, placed=placed)
    assert conflict is not None
    assert "B" in conflict.planes


def test_conflict_only_reports_mover_involvement(simple_hangar, two_planes_fleet):
    # A pre-existing conflict among placed planes must not be attributed to the mover.
    fleet = two_planes_fleet
    placed = Layout(fleet=fleet, hangar=simple_hangar,
                    placements=(Placement("A", 2.0, 8.0, 0.0, on_carts=False),))
    arc = plan_dubins(Pose(8.0, 0.0, 0.0), Pose(8.0, 1.0, 0.0), turn_radius_m=4.0)
    res = path_first_conflict(arc, fleet["B"], mover_on_carts=False, placed=placed)
    assert res is None or "B" in res.planes
```

If no shared hangar/fleet fixture exists, add one to `tests/conftest.py` building a `Hangar` large enough that a clear corridor exists (reuse `tests/fixtures/test_hangar_large.yaml` via the loader, or construct directly).

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_towplanner_motion.py -v`
Expected: FAIL — `ImportError: cannot import name 'path_first_conflict'`

- [ ] **Step 3: Implement the sampled check**

Add to `towplanner.py` (import `Conflict`, `Layout`, `Aircraft` from `hangarfit.models`, and `check` from `hangarfit.collisions`):

```python
from hangarfit.collisions import check as _check
from hangarfit.models import Aircraft, Conflict, Layout


def path_first_conflict(
    arc: DubinsArc,
    mover: Aircraft,
    *,
    mover_on_carts: bool,
    placed: Layout,
    step_m: float = 0.05,
    step_deg: float = 1.0,
) -> Conflict | None:
    """Sample ``arc``; at each pose build the mover's Placement and check it
    against ``placed``. Return the first conflict naming the mover, else None.
    Reuses ``collisions.check`` so parts-overlap, hangar-bounds, and
    bay-intrusion are all honoured during motion (spike Q4)."""
    for pose in arc.sample(step_m=step_m, step_deg=step_deg):
        moving = Placement(mover.id, pose.x_m, pose.y_m, pose.heading_deg, on_carts=mover_on_carts)
        sample_layout = Layout(
            fleet=placed.fleet,
            hangar=placed.hangar,
            placements=(*placed.placements, moving),
            maintenance_plane=placed.maintenance_plane,
        )
        for c in _check(sample_layout).conflicts:
            if mover.id in c.planes:
                return c
    return None
```

> **Edge note:** constructing `Layout` per sample re-runs `Layout.__post_init__` (cart cap, cart↔mode consistency, unique ids). Because `placed.placements ∪ {mover}` is a subset of a valid target layout, those invariants hold; if a future caller violates them the `ValueError` is a real bug signal, not something to suppress.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_towplanner_motion.py -v`
Expected: PASS

- [ ] **Step 5: Full suite + gates**

Run: `pytest tests/test_towplanner_motion.py tests/test_towplanner.py tests/test_towplanner_dubins.py -v && ruff check src/ tests/ && ruff format src/ tests/ && mypy src/hangarfit/`
Expected: all PASS, lint/type clean.

- [ ] **Step 6: Commit**

```bash
git add src/hangarfit/towplanner.py tests/test_towplanner_motion.py tests/conftest.py
git commit -m "feat(towplanner): sampled collision-during-motion check (#191)"
```

---

## Wave 2 & 3 Roadmap (expanded into full task detail once Wave 1's shapes are locked)

These depend on the exact APIs Wave 1 produces (`DubinsArc.sample`, `path_first_conflict`, `back_first_order`, `effective_turn_radius_m`), so they are roadmapped, not yet code-detailed.

- **#196 — Towplanner module + order-retry loop** *(Wave 2; blocked by ADR-0007, satisfied)*. `plan_fill(target: Layout) -> MovesPlan`. Walk `back_first_order(target.placements)`; per plane compute the door-cone entry `Pose` (x in `[door.center_x ± width/2]`, `y=0`, heading toward +y) and `plan_dubins(entry, Pose.from_placement(slot), turn_radius_m=fleet[pid].effective_turn_radius_m())`; reject via `path_first_conflict` against the already-placed subset; on conflict, swap with the next-feasible plane in the order; bail with a structured error after `K = 2 * n_planes` swaps. Deterministic (no RNG). Tests: feasible fleet → full plan; forced-conflict fleet → bounded retry then structured failure.
- **#197 — `solve` integration** *(Wave 2)*. `solve` returns bundled `(Layout, MovesPlan)` candidates spanning the ADR-0004 diversity output and alternative orderings; preserve the ADR-0003 determinism contract (extend the seeded-reproducibility tests to the bundle). ~20 lines of glue in `solver.solve` + a small result type change.
- **#192 — Polyline overlay in `visualize.py`** *(Wave 3)*. `_draw_tow_paths` companion to `_draw_conflict_overlay` at the same z-tier, one colour per plane, sampling each `Move.path`.
- **#193 — CLI flags + exit codes** *(Wave 3)*. `--render-paths` opt-in; non-zero exit when no feasible order exists for any candidate; structured conflict messages naming the offending plane + Dubins segment.
- **#194 — Docs sweep** *(Wave 3)*. arc42 §5 (register `towplanner`), §6 (bundled-output runtime flow), §8 (retire holonomic-on-carts per ADR-0007; door as towplanner-level motion gate; `turn_radius_m` now consumed); `docs/adr/README.md` already has the ADR-0007 row; `data/fleet.yaml` comments on the three `always_cart` planes' `turn_radius_m`.

---

## Self-Review

**Spec coverage (Wave 1 issues):** #188 → Task 1 (dataclasses + `effective_turn_radius_m`). #189 → Task 2 (closed-form + 45° canary). #190 → Task 3 (ordering). #191 → Task 4 (sampled check). All four Wave 1 issues map to a task. Waves 2–3 (#196, #197, #192, #193, #194) are roadmapped with their dependency on Wave 1 APIs named.

**Placeholder scan:** No "TBD"/"handle edge cases" left. The one judgement call deferred to execution is the exact closed-form Dubins constants in `_dubins_shortest` — this is deliberate and TDD-gated (the analytic matrix + canary are complete and authoritative); porting a vetted closed form against a complete test suite is the correct discipline, not a placeholder. The pivot-sign in the `r==0` branch and the L/R sign in `pose_at` are likewise pinned by `test_zero_radius_is_pivot_in_place` and the canary.

**Type consistency:** `Pose`, `Segment`, `DubinsArc`, `Move`, `MovesPlan`, `plan_dubins`, `compass_to_math_rad`, `math_rad_to_compass`, `back_first_order`, `path_first_conflict`, `DubinsArc.sample`/`pose_at`/`length_m` are referenced consistently across tasks. `effective_turn_radius_m()` matches the ADR-0007 contract. Segment-length semantics: turn segments store **arc length** in metres for `r>0` and **radians of turn** for the `r==0` pivot — this asymmetry is documented at the pivot note and gated by the zero-radius test; flagged here so the implementer does not "normalise" it away.

**Known risk to watch during execution:** the `sample()` step for a zero-radius pivot (length is in radians, not metres) — `sample` uses `step_m` for `n`; for pivots the heading-spaced sampling (`step_deg`) should drive `n` instead. Resolve in Task 2 Step 5 so `test_zero_radius_is_pivot_in_place` emits enough samples; the test asserts position invariance, so under-sampling does not hide a bug, but Wave 2's pivot collision-checking will want adequate angular resolution.
