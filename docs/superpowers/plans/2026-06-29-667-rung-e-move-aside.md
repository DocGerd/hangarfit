# Rung E — Move-aside repair core (#667) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the empty-hangar fill planner resolve a mutual block by temporarily relocating an already-parked aircraft to an apron-out staging pose, routing the stuck aircraft, then returning the displaced one — producing a valid **multi-leg** `MovesPlan` (and a faithfully-animated `view`) — while keeping every non-shuffle layout's plan **byte-identical** (ADR-0003).

**Architecture:** A **two-phase** order search in `_plan_fill` (`towplanner.py`). *Phase 1* is today's non-displacing backtracking DFS, run to completion, **byte-identical**. *Phase 2* runs **only if phase 1 fully deadlocks** (returns `None`, not a budget/backtrack-cap raise), with a fresh expansion budget: it re-runs the DFS with `allow_displace=True`, so at a dead-end level it picks a displaceable body **D** (deepest-first), enumerates **apron-out lateral** staging poses, routes three legs (D→staging, S→final, staging→D-final) via the existing `plan_path` single-start mode, then recurses. Move-aside is **gated on `hangar.apron_depth_m > 0`** (it relocates a body *outside* the door; the scenario hangar must model that apron — `--apron-depth auto|N` or `hangar.yaml`). A scene `_timeline` change lays legs in **global execution order when a multi-leg body is present** so the shuffle animates faithfully (single-leg plans keep the existing per-body path → byte-identical). The only other consumer fixes are two dev/CI `ml/` routed-leg counters.

**Tech Stack:** Python 3.12, frozen-slots dataclasses, pytest, esbuild (viewer — *not rebuilt*; no `.ts` change). No new deps. The tow module stays RNG-free.

## Global Constraints

- **ADR-0003 byte-identity:** same scenario + seed → bit-identical `MovesPlan` and scene bytes. Any layout that does **not** trigger a shuffle must produce exactly what it produces today. **Two hinges:** (1) move-aside lives in *phase 2*, reached only when the *whole-fill* phase-1 DFS deadlocks (verify-before-displace at the fill level, not the per-DFS-level tail); (2) the scene global-order pass is gated on a multi-leg body actually being present.
- **Move-aside requires `hangar.apron_depth_m > 0`.** No apron ⇒ `_try_move_aside` returns `None` (today's bail). This keeps every leg's geometry self-consistent with `target.hangar`: `plan_path`'s exact oracle `path_first_conflict` derives its motion-bounds hangar from `placed.hangar`, so the `placed` Layout for **every** leg must be built with the scenario `hangar` — and that hangar must already carry the apron, never a fabricated one.
- **No RNG / no wall-clock** in `towplanner.py`. Determinism is structural.
- **Tie-break keys:** which body to displace = `back_first_order` total order `(-y_m, +x_m, +plane_id)`; staging-pose enumeration = fixed grid, **y-outer (deepest first), x-outer (lateral, off-to-side), heading-inner**, exact-float `seen` set used **only to dedup, never to order**; route choice = first-feasible-in-enumeration-order wins; never iterate a `set` of plane-ids for emit order (membership only).
- **Hand-placed bodies are never displaceable** (`Placement.hand_placed`).
- **#668 Conflict-naming:** any bail names the **stuck body S**, never the displaced D — `raise NoFeasiblePlanError(bail.planes[0], bail)` with `bail` = the stuck body's conflict.
- **#844 cross-machine:** move-aside is **same-machine-byte-bound** only (documented). The deepest-apron-pose lever hardens D's open-apron legs, but the tight leg (S threading the near-door corridor past D@staging) routes against the scenario hangar and remains libm-sensitive; keep move-aside **out of any cross-machine determinism canary**.
- **Staging `target_slot` is transient** (apron-out, `y < 0`) and is **never** added to `target_layout.placements`. It lives only inside a `Move(leg_index=...)`.
- **Budget accounting:** every `plan_path` call (aside, S-entry, return, and every failed attempt) folds `stats["expansions"]` into `total_used`. Phase 2 gets a **fresh** budget (reset `total_used`); phase 2 runs only after a *cheap* phase-1 deadlock, so worst-case disprove cost stays bounded (verify in Task 8's bench gate).
- **Files Rung E may touch:** `src/hangarfit/towplanner.py`, `src/hangarfit/scene.py`, `ml/benchmark.py`, `ml/reach_rate.py`, `tests/test_towplanner.py`, `tests/test_towplanner_fill.py`, `tests/test_scene.py`, `tests/ml/test_*`, `bench/`, `CHANGELOG.md`, `docs/spikes/`. **Do not** edit `visualize.py`, `viewer/` (the Rung-D seam already handles multi-leg; no `.ts`/`viewer.js` change), `geometry.py`, `collisions.py`, `models.py`.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/hangarfit/towplanner.py` | `MovesPlan.__post_init__` (F1); `_staging_poses` helper; `_FillStep` internal type; two-phase order search + `_try_move_aside` (+ `allow_displace` on `_place_rest`); new constant `_MAX_FILL_DISPLACEMENTS` + `plan_fill` `max_displacements` kwarg. |
| `src/hangarfit/scene.py` | `_timeline` lays legs in global execution order **iff** a multi-leg body is present (else unchanged). |
| `ml/benchmark.py`, `ml/reach_rate.py` | Count distinct routed `plane_id`s, not routed legs. |
| `tests/test_towplanner.py` | F1 invariant accept/reject units. |
| `tests/test_towplanner_fill.py` | `_staging_poses` unit; `_FillStep` regression; move-aside force-displacement unit (monkeypatch); geometry integration fixture + multi-leg validity validator; `max_displacements=0` inert byte-identity; two-phase byte-identity; double-solve. |
| `tests/test_scene.py` | interleaved multi-leg timeline test; single-leg byte-identity stays green. |
| `tests/ml/test_benchmark.py`, `tests/ml/test_reach_rate.py` | multi-leg routed-count regression. |
| `bench/`, `CHANGELOG.md`, `docs/spikes/` | re-baseline + user entry + measured before/after + same-machine note. |

**Mutual recursion:** `_try_move_aside` and `_place_rest` are both nested in `_plan_fill` and call each other; define `_place_rest` first, then `_try_move_aside`, then run the two phases. Name resolution at call time makes the forward reference safe.

---

### Task 1: F1 — `MovesPlan` aggregate leg-index invariant

**Files:** Modify `src/hangarfit/towplanner.py:270-278`; Test `tests/test_towplanner.py`.

**Interfaces:** Produces `MovesPlan.__post_init__` — *for each `plane_id`, the `leg_index` values of its **routed** (`path is not None`) moves are distinct.* Deferred `path=None` legs exempt; no `target_slot ∈ placements` check.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_towplanner.py  (reuse this file's _layout()/_arc helpers; add minimal ones if absent)
import pytest
from hangarfit.towplanner import Move, MovesPlan, Pose

def test_movesplan_rejects_duplicate_routed_leg_index_for_a_plane(_layout, _arc):
    with pytest.raises(ValueError, match="leg_index"):
        MovesPlan(target_layout=_layout, moves=(
            Move("fk9", Pose(0.0, 5.0, 0.0), _arc, leg_index=0),
            Move("fk9", Pose(1.0, 6.0, 0.0), _arc, leg_index=0),
        ))

def test_movesplan_allows_routed_plus_deferred_same_leg_index(_layout, _arc):
    MovesPlan(target_layout=_layout, moves=(
        Move("fuji", Pose(0.0, 5.0, 0.0), _arc, leg_index=0),
        Move("fuji", Pose(0.0, 5.0, 0.0), None, leg_index=0),
    ))

def test_movesplan_allows_distinct_routed_legs_same_plane(_layout, _arc):
    MovesPlan(target_layout=_layout, moves=(
        Move("ctsl", Pose(0.0, -3.0, 180.0), _arc, leg_index=0),
        Move("ctsl", Pose(0.0, 5.0, 0.0), _arc, leg_index=1),
    ))
```

- [ ] **Step 2: Run to verify the reject test fails**
Run: `pytest tests/test_towplanner.py -k movesplan -v` → reject test FAILS; allow tests already PASS.

- [ ] **Step 3: Implement**

```python
# src/hangarfit/towplanner.py — inside class MovesPlan, after the fields
    def __post_init__(self) -> None:
        # #667 Rung E (type-design F1): a plane's ROUTED legs must carry distinct
        # leg_index so scene._timeline's sorted(..., key=leg_index) is well-defined.
        # Deferred (path=None) legs are exempt — build_moves_plan (ml/infer.py) and
        # placed-routed movers legitimately emit a routed + a deferred leg both at
        # leg_index=0; mover/deferred target_slots also need not be in placements,
        # so NO target_slot membership check is added.
        seen_routed: dict[str, set[int]] = {}
        for m in self.moves:
            if m.path is None:
                continue
            legs = seen_routed.setdefault(m.plane_id, set())
            if m.leg_index in legs:
                raise ValueError(
                    f"MovesPlan: plane {m.plane_id!r} has duplicate routed "
                    f"leg_index {m.leg_index}"
                )
            legs.add(m.leg_index)
```

- [ ] **Step 4: Run** `pytest tests/test_towplanner.py tests/test_scene.py tests/ml/test_infer.py -q` → PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(667): Rung E — MovesPlan routed-leg-index uniqueness invariant (F1)"`

---

### Task 2: Fix `ml/` routed-leg counters (count planes, not legs)

**Files:** Modify `ml/benchmark.py:299`, `ml/reach_rate.py:312`; Test `tests/ml/test_benchmark.py`, `tests/ml/test_reach_rate.py`.

> A multi-leg plan has routed *legs* > planes, so `sum(...) == n_total` silently flips to a false negative. Must land in this PR (dev/CI-only, but `plan_fill` is the exact Rung-E producer these call).

- [ ] **Step 1: Write the failing test** (analogous in both modules)

```python
# tests/ml/test_benchmark.py
def test_rrmc_verdict_counts_planes_not_legs(monkeypatch):
    import ml.benchmark as bm
    from hangarfit.towplanner import Move, MovesPlan, Pose
    plan = MovesPlan(target_layout=_FAKE_LAYOUT, moves=(
        Move("a", Pose(0, -3, 180), _ARC, leg_index=0),   # staging
        Move("a", Pose(0, 5, 0), _ARC, leg_index=1),       # final
        Move("b", Pose(1, 4, 0), _ARC, leg_index=0),
    ))
    monkeypatch.setattr(bm, "plan_fill", lambda *a, **k: plan)
    verdict = bm._rrmc_verdict(_SCENARIO_WITH_2_PLACEABLE, _RESULT_VALID_LAYOUT)
    assert verdict.n_routed == 2 and verdict.reached is True
```

- [ ] **Step 2: Run** `pytest tests/ml/test_benchmark.py -k counts_planes -v` → FAIL (`n_routed == 3`).
- [ ] **Step 3: Implement**

```python
# ml/benchmark.py:299
    n_routed = len({m.plane_id for m in plan.moves if m.path is not None})
```
```python
# ml/reach_rate.py:312
        if len({m.plane_id for m in plan.moves if m.path is not None}) == n_total:
```

- [ ] **Step 4: Run** `pytest tests/ml/test_benchmark.py tests/ml/test_reach_rate.py -q` → PASS.
- [ ] **Step 5: Commit** `git commit -m "fix(667): Rung E — ml/ reach counters count distinct planes not routed legs"`

---

### Task 3: `_staging_poses` helper (apron-out, lateral, deepest-first)

**Files:** Modify `src/hangarfit/towplanner.py` (add a module-level helper after `entry_pose`, ~line 1194); Test `tests/test_towplanner_fill.py`.

**Interfaces:** Produces `_staging_poses(target: Placement, hangar: Hangar) -> tuple[Pose, ...]` — apron-out (`y<0`) nose-out **lateral** staging candidates for a displaced body, ordered **y-outer (deepest first), x-outer (off-to-side first), heading-inner**, exact-float `seen` dedup. Empty if `apron_depth_m <= 0`.

> **Lateral, not in front of the door.** Door-clamped staging parks D *in the corridor S must enter through* — the viability paradox pushed deeper. D must roll *off to the side* on the apron. x-samples span the apron width (left-of-door, right-of-door, door-centre fallback), clamped to `[0, width]`; infeasible/out-of-bounds candidates are rejected by the routing legs, so no static pre-filter is needed here.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_towplanner_fill.py
from hangarfit.towplanner import _staging_poses

def test_staging_poses_are_apron_out_lateral_deepest_first_nose_out():
    h = _hangar(apron_depth_m=6.0)              # this file's _hangar() helper; door narrower than width
    slot = _slot("d", x=4.0, y=8.0, heading=0.0)
    poses = _staging_poses(slot, h)
    assert poses
    assert all(p.y_m < 0.0 for p in poses)                       # outside the door
    assert poses[0].y_m == -6.0                                  # deepest first (#844 margin)
    assert any(p.x_m < h.door.center_x_m - h.door.width_m / 2 or  # lateral, off the door
               p.x_m > h.door.center_x_m + h.door.width_m / 2 for p in poses)
    assert all(135.0 <= p.heading_deg <= 225.0 for p in poses)   # nose-out cone
    assert _staging_poses(slot, h) == poses                      # deterministic
    assert len({(p.x_m, p.y_m, p.heading_deg) for p in poses}) == len(poses)

def test_staging_poses_empty_without_apron():
    assert _staging_poses(_slot("d", 4.0, 8.0, 0.0), _hangar(apron_depth_m=0.0)) == ()
```

- [ ] **Step 2: Run** `pytest tests/test_towplanner_fill.py -k staging_poses -v` → FAIL (`ImportError`).
- [ ] **Step 3: Implement**

```python
# src/hangarfit/towplanner.py — after entry_pose() (~line 1194)
def _staging_poses(target: Placement, hangar: Hangar) -> tuple[Pose, ...]:
    """Apron-out (y<0) nose-out LATERAL staging candidates for a displaced body
    (#667 Rung E).

    Reuses entry_poses' apron y-samples but parks the body OFF TO THE SIDE of the
    door (x spans the apron width, not just the door opening) so it does not jam the
    corridor the stuck body must enter through — the real club shuffle rolls a plane
    laterally aside, not straight out the door. Headings are nose-out
    (_REVERSE_CONE_HEADINGS, ~180°) so the body parks fully outside. Ordered
    y-OUTER (deepest apron first → #844 cost margin), x-OUTER (off-to-side first),
    heading-inner; the `seen` set dedups only, never orders (ADR-0003 tie-break 2).
    Empty if no apron. Infeasible/out-of-bounds candidates are dropped later by the
    routing legs (path_first_conflict), so no static pre-filter here.
    """
    depth = hangar.apron_depth_m
    if depth <= 0.0:
        return ()
    door = hangar.door
    half = door.width_m / 2.0
    lo = door.center_x_m - half
    hi = door.center_x_m + half
    width = hangar.width_m

    def _clamp(x: float) -> float:
        return min(max(x, 0.0), width)

    # Lateral x: midpoint of the left apron strip, midpoint of the right strip, then
    # the door centre as a fallback. Off-to-side first (x-outer), so D clears S's
    # door swath before a centre pose is tried.
    x_left = _clamp(lo / 2.0)
    x_right = _clamp((hi + width) / 2.0)
    x_centre = _clamp(door.center_x_m)
    x_samples = (x_left, x_right, x_centre)
    y_samples = (-depth, -depth / 2.0)  # deepest first (#844 margin)

    seen: set[tuple[float, float, float]] = set()
    poses: list[Pose] = []
    for y in y_samples:  # outer: deepest apron first
        for x in x_samples:  # middle: off-to-side first, door-centre last
            for h in _REVERSE_CONE_HEADINGS:  # inner: nose-out cone
                key = (x, y, h)
                if key in seen:
                    continue
                seen.add(key)
                poses.append(Pose(x_m=x, y_m=y, heading_deg=h))
    return tuple(poses)
```

- [ ] **Step 4: Run** `pytest tests/test_towplanner_fill.py -k staging_poses -v` → PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(667): Rung E — apron-out lateral staging-pose enumeration"`

---

### Task 4: `_FillStep` result-type refactor (byte-identical, no behavior change)

**Files:** Modify `src/hangarfit/towplanner.py` — add `_FillStep` near `MovesPlan` (~line 279); change `_place_rest` success path (1948-1950) + materialization loop (1984-1998). Existing fill tests are the regression net.

**Interfaces:** Produces `_FillStep(moves: tuple[Move, ...], committed: tuple[Placement, ...], apron_fallback_planes: tuple[str, ...])`. A normal step: `moves=(Move(slot.plane_id, Pose.from_placement(slot), arc),)` (default `leg_index=0`), `committed=(slot,)`, `apron_fallback_planes=(slot.plane_id,) if apron_fb else ()`.

> Pure refactor; the materialized `MovesPlan`/`placed`/`apron_dropped_out` must be **identical** to today.

- [ ] **Step 1: Add the type**

```python
# src/hangarfit/towplanner.py — after MovesPlan (~line 279)
@dataclass(frozen=True, slots=True)
class _FillStep:
    """One committed unit of an order-search result (#667 Rung E). A normal
    placement emits one Move + commits one Placement. A move-aside emits THREE moves
    (displaced-aside, stuck-final, displaced-return) and commits ONE placement (the
    stuck body; the displaced body is already in `placed`). `apron_fallback_planes`
    names committed bodies that towed via the y=0 door-line fallback despite an apron
    (#503 diagnostic). Internal to _plan_fill; never exposed."""
    moves: tuple[Move, ...]
    committed: tuple[Placement, ...]
    apron_fallback_planes: tuple[str, ...]
```

- [ ] **Step 2: Change `_place_rest`'s return annotation + success path**

```python
    def _place_rest(
        placed_list: list[Placement], rest: list[Placement]
    ) -> list[_FillStep] | None:
        ...
            sub = _place_rest(placed_list + [slot], rest[:idx] + rest[idx + 1 :])
            if sub is not None:
                step = _FillStep(
                    moves=(Move(slot.plane_id, Pose.from_placement(slot), arc),),
                    committed=(slot,),
                    apron_fallback_planes=(slot.plane_id,) if apron_fb else (),
                )
                return [step, *sub]
            backtracks_used += 1
            ...
```

- [ ] **Step 3: Change materialization** (replace 1984-1998)

```python
    for step in result:
        moves.extend(step.moves)
        placed.extend(step.committed)
        if apron_dropped_out is not None:
            for pid in step.apron_fallback_planes:
                apron_dropped_out.append(
                    ApronShallowDrop(
                        plane_id=pid,
                        min_depth_m=_plane_fore_aft_length_m(fleet[pid]),
                    )
                )
```

- [ ] **Step 4: Run the byte-identity net** `pytest tests/test_towplanner_fill.py tests/test_solver_towplanner.py -q` → PASS (esp. `test_plan_fill_is_deterministic`, `..._inert_with_no_hand_placed_body`, `..._backtracks_order_...`, `..._backtrack_cap_...`).
- [ ] **Step 5: Commit** `git commit -m "refactor(667): Rung E — _place_rest returns _FillStep units (byte-identical)"`

---

### Task 5: Two-phase order search + move-aside core

**Files:** Modify `src/hangarfit/towplanner.py` — `_MAX_FILL_DISPLACEMENTS` constant; `plan_fill`/`_plan_fill` `max_displacements` kwarg; nonlocals; `allow_displace` on `_place_rest`; `_try_move_aside`; the two-phase driver replacing the single `result = _place_rest(...)` call (1969). Test `tests/test_towplanner_fill.py`.

**Interfaces:** Consumes `_FillStep` (T4), `_staging_poses` (T3). Produces `_MAX_FILL_DISPLACEMENTS = 16`; `plan_fill(..., max_displacements: int | None = None)` (`None` ⇒ constant; `0` ⇒ disabled). Move-aside emits a `_FillStep` with `moves=(Move(D, staging, aside, leg_index=1), Move(S, S_final, s_arc, leg_index=0), Move(D, D_final, ret, leg_index=2))`, `committed=(S_slot,)`.

- [ ] **Step 1: Write the failing tests (monkeypatched `plan_path`)**

```python
def test_plan_fill_resolves_mutual_block_via_move_aside(monkeypatch):
    target = _two_plane_mutual_block_layout()    # apron_depth_m>0; S deep, D in the only corridor
    s_id, d_id = "stuck", "blocker"

    def fake_plan_path(mover, entry, goal, *, hangar, placed, mover_on_carts, **kw):
        stats = kw.get("stats")
        if stats is not None:
            stats["expansions"] = 1
        present = {p.plane_id for p in placed.placements}
        moving = getattr(mover, "id", None)
        if goal.y_m < 0.0 or entry.y_m < 0.0:        # D's aside/return legs (apron) succeed
            return _fake_arc(entry, goal)
        if moving == s_id and d_id in present:        # S blocked while D present
            raise _forced_infeasible(s_id)
        return _fake_arc(entry, goal)                 # D's original placement + S-when-D-absent

    monkeypatch.setattr(tp, "plan_path", fake_plan_path)
    plan = tp.plan_fill(target)
    legs = {(m.plane_id, m.leg_index) for m in plan.moves if m.path is not None}
    assert {(s_id, 0), (d_id, 0), (d_id, 1), (d_id, 2)} <= legs
    order = [(m.plane_id, m.leg_index) for m in plan.moves if m.path is not None]
    assert order.index((d_id, 1)) < order.index((s_id, 0)) < order.index((d_id, 2))

def test_plan_fill_max_displacements_zero_is_inert(monkeypatch):
    target = _two_plane_mutual_block_layout()
    monkeypatch.setattr(tp, "plan_path", _fake_that_needs_displacement())
    with pytest.raises(tp.NoFeasiblePlanError) as exc:
        tp.plan_fill(target, max_displacements=0)
    assert exc.value.plane_id == "stuck"             # #668: names the STUCK body

def test_plan_fill_move_aside_skipped_without_apron(monkeypatch):
    target = _two_plane_mutual_block_layout(apron_depth_m=0.0)
    monkeypatch.setattr(tp, "plan_path", _fake_that_needs_displacement())
    with pytest.raises(tp.NoFeasiblePlanError):     # no apron ⇒ move-aside cannot fire
        tp.plan_fill(target)
```

- [ ] **Step 2: Run** `pytest tests/test_towplanner_fill.py -k "move_aside or max_displacements_zero" -v` → FAIL.

- [ ] **Step 3: Constant + kwarg + nonlocals**

```python
# near _MAX_FILL_BACKTRACKS (~line 2116)
_MAX_FILL_DISPLACEMENTS = 16  # GLOBAL cap on committed move-aside recursions (#667).
# Each (S,D,staging) combo whose three legs all route and which RECURSES counts once
# (whether or not it ultimately succeeds): a monotonic, non-decremented global ceiling
# that terminates the move-aside search independent of the per-plane expansion budget.
# Per-path cycle-safety + clean leg_index come from `displaced_planes` (a body is
# displaced at most once per DFS path); depth is structurally 1 (a shuffle's legs are
# direct plan_path calls, never a nested order search). 0 ⇒ move-aside disabled
# (byte-identical to pre-Rung-E).
```

Thread `max_displacements` through `plan_fill` (signature + the `_plan_fill(..., max_displacements=max_displacements)` call) and `_plan_fill` (signature). Near the other budgets (~1848):

```python
    disp_cap = _MAX_FILL_DISPLACEMENTS if max_displacements is None else max_displacements
    displacements_used = 0          # monotonic global counter (never decremented)
    displaced_planes: set[str] = set()  # per-path membership — add before recurse, discard on fail
```

Add `displacements_used` to `_place_rest`'s and `_try_move_aside`'s `nonlocal` (sets are mutated in place, so `displaced_planes` needs no `nonlocal`).

- [ ] **Step 4: Add `allow_displace` to `_place_rest` + the tail hook**

Change the signature to `def _place_rest(placed_list, rest, allow_displace: bool = False)`, propagate it on the recursive call (`_place_rest(placed_list + [slot], rest[:idx] + rest[idx + 1 :], allow_displace=allow_displace)`), and replace the final `return None` (1967) with:

```python
        if allow_displace:
            ms = _try_move_aside(placed_list, rest)
            if ms is not None:
                return ms
        return None
```

- [ ] **Step 5: Add `_try_move_aside`** (immediately after `_place_rest`)

```python
    def _try_move_aside(
        placed_list: list[Placement], rest: list[Placement]
    ) -> list[_FillStep] | None:
        """Depth-1 move-aside (#667 Rung E), phase 2 only. For the deepest stuck body
        S, displace a committed body D (deepest-first, never hand-placed, never twice
        per DFS path) to an apron-out lateral staging pose, route S past D@staging,
        return D to its slot, then recurse. First feasible (S, D, staging) in
        deterministic order wins. Requires hangar.apron_depth_m > 0. Returns the step
        list or None."""
        nonlocal total_used, displacements_used
        if displacements_used >= disp_cap or hangar.apron_depth_m <= 0.0:
            return None

        def _layout_of(placements: list[Placement]) -> Layout:
            # All legs use the SCENARIO `hangar` (which carries the apron): plan_path's
            # path_first_conflict derives motion bounds from placed.hangar, so a y<0
            # staging sweep is only exempt when placed.hangar.apron_depth_m > 0.
            return Layout(
                fleet=fleet, hangar=hangar, placements=tuple(placements),
                maintenance_plane=target.maintenance_plane,
                ground_objects=target.ground_objects,
                ground_object_placements=fixed_obstacle_placements,
            )

        def _route(
            mover: Aircraft, start: Pose, goal: Pose, *,
            placed_obs: Layout, on_carts: bool,
        ) -> DubinsArc | None:
            # Single-start plan_path wrapper; charges expansions to total_used on
            # success OR failure; never touches deepest_conflict (the phase-2 main
            # loop's capture is the #668 actionable reason).
            nonlocal total_used
            remaining = total_budget - total_used
            if remaining <= 0:
                return None
            stats: dict[str, object] = {}
            try:
                arc = plan_path(
                    mover, start, goal, hangar=hangar, placed=placed_obs,
                    mover_on_carts=on_carts, heuristic=heuristic,
                    max_expansions=min(budget, remaining), stats=stats,
                )
            except NoFeasiblePlanError:
                exp = stats.get("expansions", 0)
                total_used += exp if isinstance(exp, int) else 0
                return None
            exp = stats.get("expansions", 0)
            total_used += exp if isinstance(exp, int) else 0
            return arc

        for s_slot in rest:  # rest is back_first_order: deepest stuck body first
            displaceable = back_first_order(
                tuple(p for p in placed_list
                      if not p.hand_placed and p.plane_id not in displaced_planes)
            )
            for d_slot in displaceable:
                without_d = [p for p in placed_list if p.plane_id != d_slot.plane_id]
                d_aircraft = fleet[d_slot.plane_id]
                for staging in _staging_poses(d_slot, hangar):
                    aside = _route(  # leg 1 — D: final -> staging (S not yet in)
                        d_aircraft, Pose.from_placement(d_slot), staging,
                        placed_obs=_layout_of(without_d), on_carts=d_slot.on_carts,
                    )
                    if aside is None:
                        continue
                    d_at_staging = Placement(
                        d_slot.plane_id, staging.x_m, staging.y_m,
                        staging.heading_deg, d_slot.on_carts, d_slot.hand_placed,
                    )
                    # S: door -> final, against others + D@staging.
                    remaining = total_budget - total_used
                    if remaining <= 0:
                        return None
                    s_cone = entry_poses(s_slot, hangar)
                    s_stats: dict[str, object] = {}
                    try:
                        s_arc = plan_path(
                            fleet[s_slot.plane_id], s_cone[0],
                            Pose.from_placement(s_slot), hangar=hangar,
                            placed=_layout_of([*without_d, d_at_staging]),
                            mover_on_carts=s_slot.on_carts, entries=s_cone,
                            heuristic=heuristic,
                            max_expansions=min(budget, remaining), stats=s_stats,
                        )
                    except NoFeasiblePlanError:
                        exp = s_stats.get("expansions", 0)
                        total_used += exp if isinstance(exp, int) else 0
                        continue
                    exp = s_stats.get("expansions", 0)
                    total_used += exp if isinstance(exp, int) else 0
                    s_apron_fb = s_stats.get("apron_fallback") is True
                    ret = _route(  # leg 2 — D: staging -> final, against others + S@final
                        d_aircraft, staging, Pose.from_placement(d_slot),
                        placed_obs=_layout_of([*without_d, s_slot]),
                        on_carts=d_slot.on_carts,
                    )
                    if ret is None:
                        continue
                    # All three legs feasible — commit and recurse for the rest.
                    if displacements_used >= disp_cap:
                        return None
                    displacements_used += 1
                    displaced_planes.add(d_slot.plane_id)
                    new_rest = [p for p in rest if p.plane_id != s_slot.plane_id]
                    sub = _place_rest(placed_list + [s_slot], new_rest, allow_displace=True)
                    if sub is not None:
                        step = _FillStep(
                            moves=(
                                Move(d_slot.plane_id, staging, aside, leg_index=1),
                                Move(s_slot.plane_id, Pose.from_placement(s_slot),
                                     s_arc, leg_index=0),
                                Move(d_slot.plane_id, Pose.from_placement(d_slot),
                                     ret, leg_index=2),
                            ),
                            committed=(s_slot,),
                            apron_fallback_planes=(s_slot.plane_id,) if s_apron_fb else (),
                        )
                        return [step, *sub]
                    displaced_planes.discard(d_slot.plane_id)  # per-path undo; counter stays
        return None
```

- [ ] **Step 6: Replace the single search call with the two-phase driver** (at ~1969)

```python
    # Phase 1: today's non-displacing DFS, byte-identical. May RAISE on budget /
    # backtrack-cap exhaustion (propagates → no phase 2, so an un-routable fill's
    # disprove cost stays 1×). Returns None only on an IN-BUDGET deadlock.
    result = _place_rest(placed, ordered, allow_displace=False)
    if result is None:
        # Phase 2 (#667 Rung E): the whole-fill order search deadlocked within budget
        # — enable move-aside with a FRESH expansion budget. Reached only here, so any
        # layout phase 1 solves is byte-identical (ADR-0003). Phase 2 runs only after a
        # cheap phase-1 deadlock, bounding worst-case disprove cost.
        total_used = 0
        backtracks_used = 0
        deepest_conflict = None
        result = _place_rest(placed, ordered, allow_displace=True)
    if result is None:
        bail = deepest_conflict or Conflict.single(
            kind="no_feasible_path",
            plane=ordered[0].plane_id,
            detail="no feasible tow order (every placement order deadlocks)",
        )
        raise NoFeasiblePlanError(bail.planes[0], bail)
```

(Delete the old `result = _place_rest(placed, ordered)` + its `if result is None:` raise — folded above.)

- [ ] **Step 7: Run** `pytest tests/test_towplanner_fill.py tests/test_towplanner.py tests/test_solver_towplanner.py -q` → PASS (move-aside resolves the block; `max_displacements=0` and no-apron both bail naming the stuck body; all inert/determinism/backtracking tests green).
- [ ] **Step 8: Commit** `git commit -m "feat(667): Rung E — two-phase order search + depth-1 move-aside repair"`

---

### Task 6: Scene `_timeline` — global execution order when multi-leg

**Files:** Modify `src/hangarfit/scene.py` `_timeline` (~249-331); Test `tests/test_scene.py`.

> **Why required:** today `_timeline` lays a body's legs contiguously then the next body, so a move-aside `view` plays D-in→aside→**return**, then S — and `affineAt` parks D at its final slot during S's entry, rendering **S driving through D's parked slot**. Faithful animation needs legs in global execution order. **Gated on a multi-leg body being present** so every single-leg plan (inert + backtracked) keeps today's per-body path → byte-identical (ADR-0003). Viewer needs **no** change (`affineAt` already rests a body at its staging pose between non-contiguous legs; recon-confirmed). `SCHEMA` stays `hangarfit.scene/v2` (no new keys).

- [ ] **Step 1: Write the failing + regression tests**

```python
# tests/test_scene.py
def test_timeline_interleaves_legs_in_global_execution_order():
    # plan tuple order: D-aside(leg1), S(leg0), D-return(leg2)  → segments must follow it
    plan = _moveaside_plan()   # build a MovesPlan whose .moves is [D@stg, S@final, D@final]
    tl, finals = _timeline(plan.target_layout, plan, tow_speed_mps=1.0)
    segs = tl["segments"]
    seq = [(s["plane_id"], s.get("leg_index")) for s in segs]
    assert seq == [("D", 1), ("S", 0), ("D", 2)]                 # interleaved, tuple order
    assert segs[0]["end_s"] == segs[1]["start_s"]                # contiguous global clock
    assert finals["D"] == segs[2]["samples"][-1]                 # D ends at its slot, not staging

def test_timeline_single_leg_unchanged_byte_identical():
    # the existing single-leg fixtures must produce byte-identical segments (no regression)
    ...  # keep/extend the existing test_scene single-leg assertions
```

The existing `test_timeline_multi_leg_emits_one_segment_per_leg_in_leg_order` (one body, 2 consecutive legs) stays green: with one multi-leg body and no other body between its legs in the tuple, the global-order path emits them contiguously — same result.

- [ ] **Step 2: Run** `pytest tests/test_scene.py -k "interleave or multi_leg or single_leg" -v` → interleave test FAILS (legs grouped per body today).

- [ ] **Step 3: Implement** — in `_timeline`, after building `moves_by_id`, detect multi-leg and branch:

```python
    multi_leg_present = any(
        sum(1 for m in legs if m.path is not None) > 1 for legs in moves_by_id.values()
    )
    if multi_leg_present:
        # #667 Rung E: lay routed legs in GLOBAL execution order (moves tuple order)
        # so a move-aside shuffle animates faithfully (D-aside → S enters → D returns).
        # Gated on a multi-leg body being present, so single-leg plans keep the
        # back_first_order path below → byte-identical (ADR-0003). finals still take
        # each routed leg's samples[-1] in tuple order, so a body's last leg (its
        # return-to-slot) sets its final pose.
        for m in moves_plan.moves:
            if m.path is None:
                continue
            _append_one_leg(m)   # emit one segment for this leg, advancing the shared t
    else:
        for placement in back_first_order(layout.placements):
            _append_segment(placement.plane_id, record_final=True)
        for gp in sorted(layout.ground_object_placements, key=lambda p: p.plane_id):
            _append_segment(gp.plane_id, record_final=False)
```

Factor the per-leg segment body out of `_append_segment` into a small `_append_one_leg(move)` helper (duration clamp, `seg` dict with `leg_index` key only when `multi_leg_present`, `t += dur`, `finals[move.plane_id] = samples[-1]`), and have the existing `_append_segment` call it per leg so the single-leg path is unchanged. Keep `samples`/`start_s`/`end_s`/`leg_index` shapes identical to today.

- [ ] **Step 4: Run** `pytest tests/test_scene.py tests/test_viewer.py -q` → PASS (interleave test green; single-leg + existing multi-leg byte-identity green).
- [ ] **Step 5: Headless viewer sanity** (optional but recommended): render the Task-7 fixture with `view`, screenshot headless (the CLAUDE.md swiftshader recipe), confirm S enters while D sits on the apron and no transform banner.
- [ ] **Step 6: Commit** `git commit -m "feat(667): Rung E — scene timeline lays legs in global execution order for shuffles"`

---

### Task 7: Geometry integration fixture + multi-leg validity validator + double-solve byte-identity

**Files:** Test `tests/test_towplanner_fill.py`.

**Interfaces:** Real `plan_fill`/`plan_path`/`path_first_conflict`; an apron>0 fixture that **forces** a real shuffle; `_assert_plan_legs_valid(plan, target)` replaying legs in tuple order with a live pose map.

- [ ] **Step 1: Write the validator + fixture test**

```python
def _assert_plan_legs_valid(plan, target):
    """Every routed leg is collision-free against the world state at execution time.
    Fixture-scoped: aircraft-only, apron>0 (target.hangar carries the apron the
    planner used). Replays plan.moves in tuple order with a live {plane_id: Pose}; a
    staging leg's target_slot (y<0) is transient → only the motion oracle applies."""
    from hangarfit.towplanner import path_first_conflict
    fleet = target.fleet
    current: dict[str, object] = {}
    on_carts = {p.plane_id: p.on_carts for p in target.placements}
    for m in plan.moves:
        if m.path is None:
            continue
        others = [
            Placement(pid, pose.x_m, pose.y_m, pose.heading_deg, on_carts.get(pid, False))
            for pid, pose in current.items() if pid != m.plane_id
        ]
        obstacles = Layout(
            fleet=fleet, hangar=target.hangar, placements=tuple(others),
            maintenance_plane=target.maintenance_plane,
            ground_objects=target.ground_objects,
            ground_object_placements=tuple(
                gp for gp in target.ground_object_placements
                if target.ground_objects[gp.plane_id].object_class == "fixed_obstacle"
            ),
        )
        assert path_first_conflict(
            m.path, fleet[m.plane_id],
            mover_on_carts=on_carts.get(m.plane_id, False), placed=obstacles,
        ) is None, f"leg {m.plane_id} #{m.leg_index} collides at execution time"
        current[m.plane_id] = m.target_slot

def test_move_aside_geometry_fixture_routes_and_is_valid():
    target = _corridor_block_fixture()   # apron_depth_m>0; D between door and S's deep slot
    plan = plan_fill(target)
    assert {m.plane_id for m in plan.moves if m.path is not None} == {p.plane_id for p in target.placements}
    assert any(m.leg_index > 0 for m in plan.moves), "fixture must force a shuffle"
    _assert_plan_legs_valid(plan, target)

def test_move_aside_plan_is_byte_identical_double_solve():
    target = _corridor_block_fixture()
    assert plan_fill(target) == plan_fill(target)
```

`_corridor_block_fixture()`: a hangar with `apron_depth_m > 0` and a **door narrower than the hangar width** (so lateral apron room exists beside the door), two box planes where D sits squarely in the only door→deep-slot corridor; with D rolled to a side-apron pose the corridor opens for S. Build with this file's `_box_plane`/`_hangar`/`_slot` helpers. **Verify by hand** the static all-bodies layout is valid (`hangarfit check`), and that a lateral deepest staging pose provably clears S's door swath (the medium-finding caveat — tune door width / D depth until it does).

- [ ] **Step 2: Run** `pytest tests/test_towplanner_fill.py -k move_aside_geometry -v`. If the plan is single-leg, tighten the corridor until move-aside is the only resolution; if S can't thread past D@staging, widen the door / shallow D so a lateral pose clears it.
- [ ] **Step 3:** If the validator surfaces a real bug, fix it under systematic-debugging, re-run.
- [ ] **Step 4: Run** `pytest tests/test_towplanner_fill.py -q && pytest tests/test_solver_canaries.py -q -m ""` → PASS.
- [ ] **Step 5: Commit** `git commit -m "test(667): Rung E — move-aside geometry fixture + multi-leg validity + byte-identity"`

---

### Task 8: Re-baseline the bench ceiling + CHANGELOG + spike

**Files:** `bench/regimes.py`, `bench/profile_pipeline.py`, `CHANGELOG.md`, `docs/spikes/herrenteich-routing-ceiling-baseline.md`.

- [ ] **Step 1: Re-run the Rung B witness baseline with `--apron-depth auto`** (move-aside needs an apron). Run `python -m bench.profile_pipeline --heavy` and record routed-body counts / timing vs Rung B. **Confirm the disprove wall-clock stays under the perf gate** (the two-phase fresh budget can add cost only for in-budget deadlocks; Herrenteich is budget-bound → phase-1 raise → no phase 2 → 1× cost). If a regime regresses past the gate, cap phase-2's budget (a separate constant) and re-measure.
- [ ] **Step 2:** Append a "Rung E (move-aside)" section to the routing-ceiling spike (measured before/after; the same-machine-byte-bound #844 note). CHANGELOG `[Unreleased] ### Added`:

```markdown
- Tow-path fill planner can resolve a mutual block by temporarily relocating a
  parked aircraft to an apron-out staging pose and returning it ("move-aside"),
  producing a valid multi-leg plan (and a faithfully animated `view`) where a
  monotone fill has none — requires a staging apron (`--apron-depth`/hangar apron)
  and is bounded depth-1 (#667 Rung E). Layouts that don't need a shuffle are
  byte-identical (ADR-0003).
```

(Write **milestone N** bare in CHANGELOG prose; `#667` issue refs are correct.)

- [ ] **Step 3: Verification gate** `make test && ruff check src/ tests/ && ruff format --check src/ tests/ && mypy src/hangarfit/ && mypy ml/` → all green.
- [ ] **Step 4: Commit** `git commit -m "docs(667): Rung E — re-baseline routing ceiling + CHANGELOG"`

---

## Review & Guard Plan (after the branch is green)

Open a **draft** PR (`Closes #<rung-E-issue>`, base `develop`), run the review arc — one inline thread per finding, fix, resolve, re-review if non-trivial:

- **`determinism-guard`** — MANDATORY (`towplanner.py`). Double-solve diff; confirm two-phase inert byte-identity + `max_displacements`-scoped determinism. **State move-aside is same-machine-byte-bound only** (keep it out of cross-machine canaries).
- **`scene-schema-guard`** — MANDATORY now (`scene.py` touched). Confirm `build_scene` byte-identical for single-leg plans; `SCHEMA` unchanged; the global-order path additive.
- **`pr-review-toolkit:code-reviewer`** — main pass.
- **`pr-review-toolkit:silent-failure-hunter`** — the `try/except NoFeasiblePlanError` swallow points in `_try_move_aside`/`_route` (each must charge budget, not hide a real error).
- **`pr-review-toolkit:type-design-analyzer`** — `MovesPlan.__post_init__` + `_FillStep`.
- **`ml-rl-guard`** — the `ml/benchmark.py`/`ml/reach_rate.py` counters.
- `geometry-invariant-guard` is **not** triggered (no `geometry.py`/`collisions.py` change) — note explicitly.

---

## Self-Review

**Adversarial-review findings (all incorporated):**
- F1 inert-byte-identity hole (deep move-aside preempting shallow backtrack) → **two-phase** (Task 5 Step 6). ✓
- F2 `plan_path` `placed.hangar` mismatch → **require apron>0 + scenario hangar throughout** (Task 5 `_layout_of`; Task 3 gate). ✓
- F3 wrong `view` animation → **scene global-execution-order pass** (Task 6). ✓
- Staging in front of the door → **lateral x-samples** (Task 3). ✓
- Cap mislabel → **monotonic global, documented**; `displaced_planes` per-path (Task 5 Step 3). ✓
- #844 tight S-leg → **same-machine-byte-bound note** (Global Constraints + Task 8). ✓
- Unannotated nested defs → **annotated** `_route`/`_layout_of` (Task 5). ✓
- Unused `field` import → **not added** (no `replace` needed once the local-apron fabrication is dropped). ✓
- Validator scope → **documented fixture-scoped** (Task 7). ✓
- In-budget deadlock 2× cost → phase 2 only after a *cheap* phase-1 deadlock; **bench-verified** (Task 8). ✓

**Spec coverage (§4 Rung E):** move-aside in `_place_rest` ✓; depth-1 (structural + `displaced_planes`) ✓; apron-out staging ✓; separate cap (`_MAX_FILL_DISPLACEMENTS`/`max_displacements`) ✓; #668 stuck-body naming ✓; spread excludes staging legs (solver spread runs on `placements`, never `MovesPlan.moves` — recon-confirmed, no code change) ✓; multi-leg model reused (leg_index 0/1/2) ✓; acceptance (valid multi-leg fixture + every leg collision-free/in-bounds; byte-identity; bench ceiling; bounded cost) ✓.

**Honest ceiling caveat (carry into the PR body):** move-aside may still not seat the real `fk9↔cessna` cm-scale parallel-park (intrinsic ~97k-expansion plateau; Herrenteich is budget-bound so phase 1 raises and phase 2 never runs there). The bar is the **capability** (synthetic + small-geometry fixtures) + byte-identity + a re-baselined ceiling, **not** a guaranteed all-8 route.
