# Geometry-cut (training throughput) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate redundant per-step world-geometry recomputation in the `ml/` PPO rollout loop, byte-identical to training output, so each training iteration is faster.

**Architecture:** Add one single-pass scorer (`score_layout` → `LayoutScore`) and a `build_obstacles` wrapper to `ml/geometry_oracle.py`; give `swept_intrusion_m2` an optional pre-built `obstacles=`. In `ml/env.py`, add an episode cache keyed on a `_parked_version` counter (bumped on Park, reset on `reset()`) so the frozen parked-set score + obstacles are computed once per parked-set version and reused across the drive-in. No `src/hangarfit/` change; no `StepInfo` semantic change.

**Tech Stack:** Python 3.12, PyTorch (CPU), shapely, pytest. Spec: `docs/superpowers/specs/2026-06-17-geometry-cut-training-throughput-design.md`.

## Global Constraints

- **Byte-identical training output.** Rewards, advantages, gradients, and saved `state_dict` must be bit-for-bit unchanged. Every cached value must equal the freshly-computed value exactly. (Verified by the equivalence tests + a manual end-to-end hash in Task 6.)
- **`StepInfo` semantics unchanged.** `valid` keeps meaning "validity of the frozen `_parked + _fixed` set" — the cache just serves it cheaply.
- **No `src/hangarfit/` change.** The deterministic verifier is ground truth; only `ml/` is touched.
- **`ml/` runs from repo root** (top-level package; not installed). torch is the `[train]` extra (CPU). Tests: `python -m pytest tests/ml/...`.
- **Style:** `ruff check ml/ tests/` + `ruff format --check` + `mypy ml/` clean before each commit (the PostToolUse hook runs ruff + scoped `pytest tests/ml/` automatically on `ml/*.py` edits).
- **Commit trailer:** end every commit message with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

## File Structure

- `ml/geometry_oracle.py` (modify) — add `LayoutScore` dataclass, `score_layout()`, `build_obstacles()` wrapper; add optional `obstacles=` to `swept_intrusion_m2`.
- `ml/env.py` (modify) — add `_parked_version`, `_score_cache`, `_obstacles_cache`; add `_parked_score()`, `_parked_obstacles()`; rewire `_potential()`, `_layout_valid()`, the Park + movement branches of `step()`.
- `tests/ml/test_geometry_oracle.py` (modify) — `score_layout` equivalence; `swept_intrusion_m2(obstacles=)` equivalence; `build_obstacles` equivalence.
- `tests/ml/test_env.py` (modify) — `_parked_version` invariants; cache-equals-fresh across an episode; obstacles-cache stability; reward-sequence determinism (already present, must still pass).

---

## Task 0: Capture pre-change baseline (verification anchor)

**Files:** none (produces `/tmp/geomcut_baseline.sha` + a recorded profile number).

- [ ] **Step 1: Hash a baseline checkpoint from the current (pre-change) code**

Run:
```bash
python -m ml.train --schedule trivial --iterations 5 --seed 0 \
  --r-valid-park 2.0 --dense-slot-potential \
  --entropy-start 0.05 --entropy-end 0.005 --entropy-anneal-iters 40 \
  --normalize-returns --save /tmp/geomcut_baseline.pt
sha256sum /tmp/geomcut_baseline.pt | tee /tmp/geomcut_baseline.sha
```
Expected: a `.sha` file with one hash line. Keep it; Task 6 asserts the post-change build reproduces it.

- [ ] **Step 2: Record the baseline profile**

Run: `python3 /tmp/profile_rollout.py 2>&1 | grep "function calls"`
Expected: ~`24.2 seconds` for 512 steps. Note the number for the Task 6 comparison. (No commit — verification artifact only.)

---

## Task 1: `score_layout` + `LayoutScore` (one check + one egress)

**Files:**
- Modify: `ml/geometry_oracle.py`
- Test: `tests/ml/test_geometry_oracle.py`

**Interfaces:**
- Produces: `LayoutScore(penetration_m2: float, collisions_valid: bool, egress_blocked: bool)` (frozen) and `score_layout(layout: Layout) -> LayoutScore`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/ml/test_geometry_oracle.py` (imports at top of file as needed):
```python
from hangarfit.collisions import check
from ml import geometry_oracle as go
from tests.ml.conftest import single_object_layout, two_object_layout


def test_score_layout_matches_separate_calls_valid():
    layout = single_object_layout(x_m=9.0, y_m=12.0)
    s = go.score_layout(layout)
    assert s.penetration_m2 == go.overlap_area_m2(layout)
    assert s.collisions_valid == check(layout).valid
    assert s.egress_blocked == go.egress_blocked(layout)
    assert (s.collisions_valid and not s.egress_blocked) == go.layout_valid(layout)


def test_score_layout_matches_separate_calls_overlapping():
    # Both bodies parked at the same spot -> guaranteed overlap -> invalid.
    layout, _active, _aid = two_object_layout(parked_y_m=12.0, active_y_m=12.0)
    s = go.score_layout(layout)
    assert s.penetration_m2 == go.overlap_area_m2(layout)
    assert s.penetration_m2 > 0.0
    assert s.collisions_valid == check(layout).valid
    assert s.collisions_valid is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/ml/test_geometry_oracle.py -k score_layout -q`
Expected: FAIL — `AttributeError: module 'ml.geometry_oracle' has no attribute 'score_layout'`.

- [ ] **Step 3: Implement**

In `ml/geometry_oracle.py`: add `from dataclasses import dataclass` (top imports), append `"LayoutScore"` and `"score_layout"` to `__all__`, and add near `layout_valid`:
```python
@dataclass(frozen=True, slots=True)
class LayoutScore:
    """One-pass score of a frozen layout: graded penetration + the two validity gates,
    from a single ``check`` + single ``egress_blocked``. ``layout_valid`` ==
    ``collisions_valid and not egress_blocked``."""

    penetration_m2: float
    collisions_valid: bool
    egress_blocked: bool


def score_layout(layout: Layout) -> LayoutScore:
    """Single-pass replacement for calling ``overlap_area_m2`` + ``layout_valid`` +
    ``egress_blocked`` separately (each re-runs ``check``/rebuilds world parts)."""
    cr = check(layout)
    return LayoutScore(
        penetration_m2=cr.total_penetration_m2,
        collisions_valid=cr.valid,
        egress_blocked=egress_blocked(layout),
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/ml/test_geometry_oracle.py -k score_layout -q` → PASS.
Then `ruff check ml/ tests/ml && mypy ml/` → clean.

- [ ] **Step 5: Commit**

```bash
git add ml/geometry_oracle.py tests/ml/test_geometry_oracle.py
git commit -m "perf(704): add score_layout (one check+egress per layout)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `build_obstacles` wrapper + `swept_intrusion_m2(obstacles=)`

**Files:**
- Modify: `ml/geometry_oracle.py`
- Test: `tests/ml/test_geometry_oracle.py`

**Interfaces:**
- Produces: `build_obstacles(parked_layout: Layout, mover_id: str) -> ObstaclesT` (thin wrapper over `towplanner._build_obstacles`), and `swept_intrusion_m2(..., obstacles: ObstaclesT | None = None)`.
- `ObstaclesT` = the return type of `towplanner._build_obstacles` (read `src/hangarfit/towplanner.py` for the exact class name — it has a `.world_parts` attribute; import it for the annotation).

- [ ] **Step 1: Write the failing test**

Add to `tests/ml/test_geometry_oracle.py`:
```python
def test_swept_intrusion_prebuilt_obstacles_matches_default():
    from hangarfit.towplanner import Pose

    from ml.types import Primitive

    layout, active, aid = two_object_layout(parked_y_m=12.0, active_y_m=6.0)
    start = Pose(x_m=5.0, y_m=6.0, heading_deg=0.0)
    _end, swept = go.apply_primitive(
        start, Primitive(kind="S", magnitude=6.0, gear=1),
        turn_radius_m=active.effective_turn_radius_m(),
    )
    default = go.swept_intrusion_m2(active, swept, parked_layout=layout, active_id=aid)
    obstacles = go.build_obstacles(layout, aid)
    passed = go.swept_intrusion_m2(
        active, swept, parked_layout=layout, active_id=aid, obstacles=obstacles
    )
    assert passed == default
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/ml/test_geometry_oracle.py -k swept_intrusion_prebuilt -q`
Expected: FAIL — `AttributeError: ... has no attribute 'build_obstacles'`.

- [ ] **Step 3: Implement**

In `ml/geometry_oracle.py`: append `"build_obstacles"` to `__all__`. Add:
```python
def build_obstacles(parked_layout: Layout, mover_id: str) -> ObstaclesT:
    """Frozen-parked obstacle set for swept-path clearance (the mover is excluded).
    Exposed so the env can build it once per parked-set version and reuse it."""
    return _build_obstacles(parked_layout, mover_id=mover_id)
```
(Define `ObstaclesT` by importing the concrete type from `hangarfit.towplanner` — see the Interfaces note.) Change the `swept_intrusion_m2` signature + first line:
```python
def swept_intrusion_m2(
    body: Aircraft | GroundObject,
    swept: tuple[Pose, ...],
    *,
    parked_layout: Layout,
    active_id: str,
    obstacles: ObstaclesT | None = None,
) -> float:
    ...
    obstacles = obstacles if obstacles is not None else _build_obstacles(parked_layout, mover_id=active_id)
    hangar = parked_layout.hangar
    ...  # rest unchanged
```
(Delete the old `obstacles = _build_obstacles(...)` line that the new conditional replaces.)

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/ml/test_geometry_oracle.py -q` → PASS. `ruff check ml/ tests/ml && mypy ml/` → clean.

- [ ] **Step 5: Commit**

```bash
git add ml/geometry_oracle.py tests/ml/test_geometry_oracle.py
git commit -m "perf(704): build_obstacles wrapper + swept_intrusion obstacles= param

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `_parked_version` counter

**Files:**
- Modify: `ml/env.py`
- Test: `tests/ml/test_env.py`

**Interfaces:**
- Produces: `HangarFitEnv._parked_version: int` — 0 after `reset()`, +1 on each Park.

- [ ] **Step 1: Write the failing test**

Add to `tests/ml/test_env.py` (reuses `_two_object_env`, `DifficultyConfig`, `Park` already imported):
```python
def test_parked_version_zero_then_bumps_on_park_then_resets():
    env = _two_object_env(
        difficulty=DifficultyConfig(max_objects=2, per_object_step_budget=40, total_step_budget=80)
    )
    env.reset()
    assert env._parked_version == 0
    env.step(Park())  # park object 1 -> version bumps (validity irrelevant)
    assert env._parked_version == 1
    env.reset()
    assert env._parked_version == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/ml/test_env.py -k parked_version -q`
Expected: FAIL — `AttributeError: 'HangarFitEnv' object has no attribute '_parked_version'`.

- [ ] **Step 3: Implement**

In `ml/env.py` `_reset_state` (the block initializing `_parked` etc.), add:
```python
        self._parked_version = 0
```
In `step()`'s Park branch, right after `self._parked.append(pl)`:
```python
            self._parked_version += 1
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/ml/test_env.py -k parked_version -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add ml/env.py tests/ml/test_env.py
git commit -m "perf(704): _parked_version counter (Park-bumped, reset-cleared)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `_parked_score()` cache + wire `_potential`/`_layout_valid`/Park branch

**Files:**
- Modify: `ml/env.py`
- Test: `tests/ml/test_env.py`

**Interfaces:**
- Consumes: `go.LayoutScore`, `go.score_layout` (Task 1); `self._parked_version` (Task 3).
- Produces: `HangarFitEnv._parked_score() -> go.LayoutScore`.

- [ ] **Step 1: Write the failing test**

Add to `tests/ml/test_env.py` (add `from ml import geometry_oracle as go` if not already imported — it is):
```python
def test_parked_score_cache_equals_fresh_each_step():
    env = _two_object_env(
        difficulty=DifficultyConfig(max_objects=2, per_object_step_budget=40, total_step_budget=80)
    )
    env.reset()
    fwd = Primitive(kind="S", magnitude=1.0, gear=1)
    actions = [fwd, fwd, fwd, Park(), fwd, fwd]  # drive+park obj1, then drive obj2
    for a in actions:
        _, _, done, _ = env.step(a)
        if done:
            break
        # After a non-terminal step the cache must equal a fresh score of the parked set.
        assert env._parked_score() == go.score_layout(env._layout())


def test_parked_score_empty_set_is_trivial():
    env = _env()
    env.reset()
    s = env._parked_score()  # nothing parked yet
    assert s == go.LayoutScore(0.0, True, False)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/ml/test_env.py -k parked_score -q`
Expected: FAIL — no attribute `_parked_score`.

- [ ] **Step 3: Implement**

In `ml/env.py` `_reset_state`, add alongside the version line:
```python
        self._score_cache: tuple[int, go.LayoutScore] | None = None
```
Add the accessor method:
```python
    def _parked_score(self) -> go.LayoutScore:
        """Cached score of the frozen ``_layout()`` (parked + fixed). Recomputed only when
        the parked set changes (``_parked_version`` bump). Empty set short-circuits to the
        trivial valid score without calling ``check``."""
        if not (self._parked or self._fixed):
            return go.LayoutScore(0.0, True, False)
        if self._score_cache is not None and self._score_cache[0] == self._parked_version:
            return self._score_cache[1]
        score = go.score_layout(self._layout())
        self._score_cache = (self._parked_version, score)
        return score
```
Rewire `_potential()`:
```python
        remaining_overlap = self._parked_score().penetration_m2
```
(replaces the `go.overlap_area_m2(layout) if (self._parked or self._fixed) else 0.0` line; `layout = self._layout()` is still needed for the `dense_slot_potential` misfit branch — keep it.)

Rewire `_layout_valid()`:
```python
        s = self._parked_score()
        return s.collisions_valid and not s.egress_blocked
```
Rewire the Park branch — replace the three separate oracle calls. After `self._parked.append(pl)` and `self._parked_version += 1` (Task 3):
```python
            score = self._parked_score()          # one check + one egress, cached
            overlap = score.penetration_m2
            egress = score.egress_blocked
            intrusion = go.intrusion_area_m2(body, pl, self.hangar, bay_closed=False)
            park_valid = score.collisions_valid and not score.egress_blocked
```
(Delete the old `overlap = go.overlap_area_m2(placed_layout)`, `egress = go.egress_blocked(placed_layout)`, `park_valid = go.layout_valid(placed_layout)` lines, and the now-unused `placed_layout = self._layout()` if nothing else uses it.)

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/ml/test_env.py -q` → PASS (incl. the pre-existing `test_reward_is_rng_free_for_a_fixed_action_sequence` and `test_partial_budget_stop_includes_terminal_fraction_reward` — these guard byte-identity of the reward). `ruff check ml/ tests/ml && mypy ml/` → clean.

- [ ] **Step 5: Commit**

```bash
git add ml/env.py tests/ml/test_env.py
git commit -m "perf(704): episode-cache the frozen parked LayoutScore

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `_parked_obstacles()` cache + wire `swept_intrusion`

**Files:**
- Modify: `ml/env.py`
- Test: `tests/ml/test_env.py`

**Interfaces:**
- Consumes: `go.build_obstacles`, `go.swept_intrusion_m2(obstacles=)` (Task 2); `self._parked_version` (Task 3).
- Produces: `HangarFitEnv._parked_obstacles(active_id: str) -> ObstaclesT`.

- [ ] **Step 1: Write the failing test**

Add to `tests/ml/test_env.py`:
```python
def test_parked_obstacles_cache_is_stable_per_version_and_active():
    env = _two_object_env(
        difficulty=DifficultyConfig(max_objects=2, per_object_step_budget=40, total_step_budget=80)
    )
    env.reset()
    env.step(Park())  # park obj1; active is now obj2
    aid = env._active_id
    assert aid is not None
    o1 = env._parked_obstacles(aid)
    o2 = env._parked_obstacles(aid)
    assert o1 is o2  # same (version, active_id) -> cached object reused
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/ml/test_env.py -k parked_obstacles -q`
Expected: FAIL — no attribute `_parked_obstacles`.

- [ ] **Step 3: Implement**

In `_reset_state`:
```python
        self._obstacles_cache: tuple[int, str, go.ObstaclesT] | None = None
```
(Import/alias `ObstaclesT` in `env.py` the same way Task 2 exposed it, or reference it via `go` if re-exported — re-export `ObstaclesT` from `geometry_oracle` for a clean `go.ObstaclesT`.) Add the accessor:
```python
    def _parked_obstacles(self, active_id: str) -> "go.ObstaclesT":
        """Cached frozen-parked obstacle set for swept-path clearance. Keyed on
        (parked_version, active_id) — the mover is excluded from obstacles, and the
        active object changes per driven body."""
        c = self._obstacles_cache
        if c is not None and c[0] == self._parked_version and c[1] == active_id:
            return c[2]
        obs = go.build_obstacles(self._layout(), active_id)
        self._obstacles_cache = (self._parked_version, active_id, obs)
        return obs
```
Rewire the movement branch's swept call:
```python
        swept_intr = go.swept_intrusion_m2(
            body, swept, parked_layout=parked_layout, active_id=active_id,
            obstacles=self._parked_obstacles(active_id),
        )
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/ml/test_env.py -q` → PASS (again incl. the reward-determinism test). `ruff check ml/ tests/ml && mypy ml/` → clean.

- [ ] **Step 5: Commit**

```bash
git add ml/env.py tests/ml/test_env.py
git commit -m "perf(704): episode-cache the frozen parked obstacle set for swept_intrusion

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Verify byte-identity + re-profile (report in PR)

**Files:** none (verification + PR notes).

- [ ] **Step 1: Reproduce the baseline checkpoint hash**

Run (same command + seed as Task 0):
```bash
python -m ml.train --schedule trivial --iterations 5 --seed 0 \
  --r-valid-park 2.0 --dense-slot-potential \
  --entropy-start 0.05 --entropy-end 0.005 --entropy-anneal-iters 40 \
  --normalize-returns --save /tmp/geomcut_after.pt
sha256sum /tmp/geomcut_after.pt
diff <(cut -d' ' -f1 /tmp/geomcut_baseline.sha) <(sha256sum /tmp/geomcut_after.pt | cut -d' ' -f1) && echo "BYTE-IDENTICAL"
```
Expected: `BYTE-IDENTICAL` (the state_dict hash matches Task 0). If it differs, a cut diverged — STOP and debug before opening the PR.

- [ ] **Step 2: Re-profile and record the win**

Run: `python3 /tmp/profile_rollout.py 2>&1 | grep "function calls"`
Compare to the Task 0 number (~24.2 s). Record both in the PR body (e.g., "512-step rollout: 24.2 s → X.X s, −NN%").

- [ ] **Step 3: Full ml/ suite + lint/type gate**

Run: `python -m pytest tests/ml/ -q && ruff check ml/ tests/ && ruff format --check ml/ tests/ && mypy ml/`
Expected: all green.

---

## Self-Review

**1. Spec coverage:**
- §4 Component 1 (`score_layout`) → Task 1. ✓
- §4 Component 2 (`swept_intrusion_m2` obstacles) → Task 2. ✓
- §4 Component 3 (version counter + score cache + obstacle cache + rewiring) → Tasks 3, 4, 5. ✓
- §6 equivalence units (score≡separate, swept w/ obstacles ≡ default, cache≡fresh) → Tasks 1, 2, 4. ✓
- §6 cache invariants (version 0 / +1 per Park / reset) → Task 3. ✓
- §6 reward-sequence byte-identity → covered by the pre-existing `test_reward_is_rng_free_for_a_fixed_action_sequence` + `test_partial_budget_stop_includes_terminal_fraction_reward` (asserted still-passing in Tasks 4 & 5) + the manual hash in Task 6. ✓
- §6 manual end-to-end hash + re-profile → Tasks 0 & 6. ✓
- §7 per-instance caches (no global state) → caches are env-instance attributes set in `_reset_state`. ✓

**2. Placeholder scan:** The only deferred name is `ObstaclesT` (the concrete return type of `towplanner._build_obstacles`), resolved by a one-line read of `src/hangarfit/towplanner.py` noted in Task 2's Interfaces — not a logic placeholder.

**3. Type consistency:** `LayoutScore(penetration_m2, collisions_valid, egress_blocked)` is constructed identically in Task 1 (definition), Task 4 (`_parked_score`), and the empty-set short-circuit. `_parked_version` (int), `_score_cache` (`tuple[int, LayoutScore] | None`), `_obstacles_cache` (`tuple[int, str, ObstaclesT] | None`) are consistent across Tasks 3–5. `go.build_obstacles(layout, mover_id)` / `go.swept_intrusion_m2(..., obstacles=)` signatures match between Task 2 (def) and Task 5 (call).
