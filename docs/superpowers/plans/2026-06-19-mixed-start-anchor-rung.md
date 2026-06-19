# Mixed-start anchor rung — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `pair-mixed` curriculum rung where each episode randomly starts anchored (k=1) or empty (k=0), so empty-start episodes stay in the training mix throughout and the policy stops collapsing to the place-nothing pole on the empty-start `pair-box` rung.

**Architecture:** A per-episode `seed_anchor_k` override on `HangarFitEnv.reset`, fed by an `EpisodeStart(requested_ids, seed_anchor_k)` record that the per-episode sampler produces. For a mixed rung the sampler draws `k ∈ {1,0}` ~ Bernoulli(`anchor_prob`) **from the existing per-worker `stage_rng` stream** (so Sync≡Subproc stays byte-identical). A new opt-in `with_mixed_anchor_rung` builder + `--mixed-anchor` flag inserts the rung between `pair-anchored` and `pair-box`, reusing the existing `witness_box` fixture.

**Tech Stack:** Python 3.12, pytest, dataclasses, `random.Random`. The `ml/` package is dev/CI-only (epic #607); modules touched here (`ml/env.py`, `ml/curriculum.py`, `ml/types.py`, `ml/vector_env.py`) are **torch-free** and must stay so.

## Global Constraints

- **Run from the repo root** — `ml/` is a TOP-LEVEL package; the editable install does not put it on `sys.path`. Use `pytest tests/ml/...` from the repo root (cwd = repo root).
- **Do NOT `pip install ".[train]"`** — it clobbers the user's GPU torch in `~/.local`. These tasks need no torch (the modules are torch-free; tests here don't import torch).
- **Determinism contract (ml-rl-guard):** `--mixed-anchor` absent AND `anchor_prob is None` ⇒ output byte-identical to pre-change. With a mixed rung active, a fixed `(seed, schedule, device=cpu)` stays reproducible and `Sync ≡ Subproc` byte-identical — the per-episode k MUST be drawn from the existing per-worker `stage_rng` stream with a FIXED draw order (ids first, then k). Never introduce a new RNG.
- **Keep modules torch-free:** `ml/env.py`, `ml/curriculum.py`, `ml/types.py`, `ml/vector_env.py` must not import torch.
- **Two-pass coverage:** keep ≥ 1 non-`@slow` test per new code path.
- **Lint/type:** `ruff check ml/ tests/ml/` + `ruff format --check` + `mypy ml/` must pass. The PostToolUse hook auto-runs ruff + `pytest tests/ml/` after each `ml/*.py` edit.
- **Per-PR process:** this branch is `feature/712-mixed-start-anchor-rung` off `develop`. PR body gets `Closes #<issue>` (see Execution Handoff note on the issue).

---

### Task 1: Per-episode `seed_anchor_k` override on the env

**Files:**
- Modify: `ml/env.py` — `reset` (line 218) + `_reset_state` (line 64)
- Test: `tests/ml/test_env.py`

**Interfaces:**
- Produces: `HangarFitEnv.reset(self, requested_ids=None, *, seed_anchor_k: int | None = None) -> Observation` — when `seed_anchor_k` is not None it overrides `self.difficulty.seed_anchor_k` for that episode; `None` ⇒ unchanged behavior.

- [ ] **Step 1: Write the failing test**

In `tests/ml/test_env.py` (reuse the module's existing env-construction helpers / fixtures for a 2-object anchored box env; mirror the existing `seed_anchor_k` tests already there):

```python
def test_reset_seed_anchor_k_override_parks_nothing(anchored_pair_env):
    # anchored_pair_env: difficulty.seed_anchor_k == 1, witness poses for both ids.
    env = anchored_pair_env
    env.reset(requested_ids=("fuji", "aviat_husky"), seed_anchor_k=0)
    assert env._parked == []          # override 0 wins over difficulty's k=1
    assert env._queue == ["fuji", "aviat_husky"]

def test_reset_seed_anchor_k_override_parks_prefix(anchored_pair_env):
    env = anchored_pair_env
    env.reset(requested_ids=("fuji", "aviat_husky"), seed_anchor_k=1)
    assert len(env._parked) == 1      # first id pre-parked at its witness pose
    assert env._queue == ["aviat_husky"]

def test_reset_without_override_uses_difficulty_default(anchored_pair_env):
    env = anchored_pair_env           # difficulty.seed_anchor_k == 1
    env.reset(requested_ids=("fuji", "aviat_husky"))
    assert len(env._parked) == 1      # regression: today's behavior unchanged
```

If `tests/ml/test_env.py` has no `anchored_pair_env` fixture, build the env inline the way the existing `seed_anchor_k` tests in that file do (load `tests/fixtures/ml/witness_box.yaml` via `stage_builder.witness_placements` on `_PAIR_ANCHORED_STAGE`, or construct `HangarFitEnv(..., anchor_placements=..., difficulty=DifficultyConfig(max_objects=2, seed_anchor_k=1))`).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_env.py -k seed_anchor_k_override -v`
Expected: FAIL — `reset() got an unexpected keyword argument 'seed_anchor_k'`.

- [ ] **Step 3: Implement the override**

In `ml/env.py`, change `_reset_state` to accept an override (default preserves today):

```python
def _reset_state(self, *, seed_anchor_k_override: int | None = None) -> None:
    n = self.difficulty.max_objects
    requested = list(self.requested_ids if n is None else self.requested_ids[:n])
    k = self.difficulty.seed_anchor_k if seed_anchor_k_override is None else seed_anchor_k_override
    if k:
        if k < 0 or k >= len(requested):
            raise ValueError(
                f"seed_anchor_k={k} must satisfy 0 <= k < the requested set size "
                f"{len(requested)} (at least one object must be left to drive in)"
            )
        missing = [i for i in requested[:k] if i not in self._anchor_by_id]
        if missing:
            raise ValueError(
                f"seed_anchor_k={k} but no witness pose for anchored ids {missing} "
                f"(known witness ids: {sorted(self._anchor_by_id)})"
            )
    self._parked: list[Placement] = [self._anchor_by_id[i] for i in requested[:k]]
    self._queue: list[str] = requested[k:]
    self._parked_version = 0
    self._score_cache = None
    self._obstacles_cache = None
    self._active_id = None
    self._active_pose = None
    self._prev_gear = None
    self._steps_this_object = 0
    self._steps_total = 0
    self._prev_potential = 0.0
```

(Preserve the existing `# 712 seed-anchor` comment block above `k = ...`.)

And thread it through `reset` (line 218):

```python
def reset(
    self,
    requested_ids: tuple[str, ...] | None = None,
    *,
    seed_anchor_k: int | None = None,
) -> Observation:
    if requested_ids is not None:
        # ... unchanged validation + max_objects truncation ...
        n = self.difficulty.max_objects
        self.requested_ids = requested_ids if n is None else requested_ids[:n]
    self._reset_state(seed_anchor_k_override=seed_anchor_k)
    self._spawn()
    self._prev_potential = self._potential()
    return self._observe()
```

`__init__`'s call to `self._reset_state()` stays as-is (no override).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/ml/test_env.py -v`
Expected: PASS (new override tests + all existing env tests).

- [ ] **Step 5: Commit**

```bash
git add ml/env.py tests/ml/test_env.py
git commit -m "feat(712): per-episode seed_anchor_k override on env.reset"
```

---

### Task 2: `EpisodeStart` record + plain/mixed samplers

**Files:**
- Modify: `ml/curriculum.py` — add after `sample_request` (line 156)
- Test: `tests/ml/test_curriculum.py`

**Interfaces:**
- Consumes: `sample_request(pool, n, rng) -> tuple[str, ...]` (line 152), `stage_rng` (line 144).
- Produces:
  - `EpisodeStart(requested_ids: tuple[str, ...], seed_anchor_k: int | None = None)` — frozen dataclass.
  - `plain_start(pool, n, rng) -> EpisodeStart` — `EpisodeStart(sample_request(pool, n, rng), None)`.
  - `sample_mixed_start(pool, n, rng, *, seed_anchor_k: int, anchor_prob: float) -> EpisodeStart` — draws ids then `k = seed_anchor_k if rng.random() < anchor_prob else 0`.

- [ ] **Step 1: Write the failing test**

In `tests/ml/test_curriculum.py`:

```python
import random
from ml.curriculum import EpisodeStart, plain_start, sample_mixed_start

def test_plain_start_wraps_sample_request_with_no_anchor():
    rng = random.Random(0)
    s = plain_start(("fuji", "aviat_husky"), 2, rng)
    assert isinstance(s, EpisodeStart)
    assert set(s.requested_ids) == {"fuji", "aviat_husky"}
    assert s.seed_anchor_k is None

def test_mixed_start_draws_k_deterministically():
    pool = ("fuji", "aviat_husky")
    a = [sample_mixed_start(pool, 2, random.Random(7), seed_anchor_k=1, anchor_prob=0.5)
         for _ in range(1)]
    b = [sample_mixed_start(pool, 2, random.Random(7), seed_anchor_k=1, anchor_prob=0.5)
         for _ in range(1)]
    assert a[0] == b[0]                      # same seed -> identical (ids AND k)
    assert a[0].seed_anchor_k in (0, 1)

def test_mixed_start_k_is_zero_or_seed_anchor_k_by_prob():
    pool = ("fuji", "aviat_husky")
    rng = random.Random(0)
    ks = [sample_mixed_start(pool, 2, rng, seed_anchor_k=1, anchor_prob=p).seed_anchor_k
          for p in (0.0,) * 50]
    assert set(ks) == {0}                    # prob 0 -> always empty start
    rng = random.Random(0)
    ks = [sample_mixed_start(pool, 2, rng, seed_anchor_k=1, anchor_prob=p).seed_anchor_k
          for p in (1.0,) * 50]
    assert set(ks) == {1}                    # prob 1 -> always anchored

def test_mixed_start_mixture_fraction_near_anchor_prob():
    pool = ("fuji", "aviat_husky")
    rng = random.Random(123)
    draws = [sample_mixed_start(pool, 2, rng, seed_anchor_k=1, anchor_prob=0.5).seed_anchor_k
             for _ in range(2000)]
    frac_anchored = sum(1 for k in draws if k == 1) / len(draws)
    assert 0.45 <= frac_anchored <= 0.55     # ~0.5 mixture
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_curriculum.py -k "plain_start or mixed_start" -v`
Expected: FAIL — `ImportError: cannot import name 'EpisodeStart'`.

- [ ] **Step 3: Implement the record + samplers**

In `ml/curriculum.py`, immediately after `sample_request` (line 156):

```python
@dataclass(frozen=True, slots=True)
class EpisodeStart:
    """Per-episode start spec produced by the sampler, consumed by env.reset. ``seed_anchor_k``
    None => the env uses ``difficulty.seed_anchor_k`` (plain rung); an int => per-episode
    override (the #712 mixed-start rung's seeded k draw)."""

    requested_ids: tuple[str, ...]
    seed_anchor_k: int | None = None


def plain_start(pool: Sequence[str], n: int, rng: random.Random) -> EpisodeStart:
    """A non-anchored episode start: the requested ids only (k left to the env default).
    The id draw is identical to ``sample_request`` (no extra rng draw), so a plain rung's
    rng consumption is byte-identical to the pre-change ladder."""
    return EpisodeStart(sample_request(pool, n, rng), None)


def sample_mixed_start(
    pool: Sequence[str],
    n: int,
    rng: random.Random,
    *,
    seed_anchor_k: int,
    anchor_prob: float,
) -> EpisodeStart:
    """A mixed-start episode: draw the requested ids, THEN draw k = ``seed_anchor_k`` with
    probability ``anchor_prob`` else 0, from the SAME rng (fixed draw order: ids then k), so
    Sync and Subproc workers on the same stream stay byte-identical. ``anchor_prob`` is
    P(k=seed_anchor_k) per episode."""
    ids = sample_request(pool, n, rng)
    k = seed_anchor_k if rng.random() < anchor_prob else 0
    return EpisodeStart(ids, k)
```

(`dataclass`, `replace`, `random`, `Sequence` are already imported in this module.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/ml/test_curriculum.py -k "plain_start or mixed_start" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ml/curriculum.py tests/ml/test_curriculum.py
git commit -m "feat(712): EpisodeStart record + plain/mixed episode samplers"
```

---

### Task 3: Thread `EpisodeStart` through the worker + single-env collector

**Files:**
- Modify: `ml/vector_env.py` — `_EnvWorker.__init__`/`reset`/`step` (lines 28-66)
- Modify: `ml/train.py` — `collect_rollout` (line 146) signature + episode-boundary reset
- Test: `tests/ml/test_vector_env.py`

**Interfaces:**
- Consumes: `EpisodeStart` (Task 2), `HangarFitEnv.reset(..., seed_anchor_k=...)` (Task 1).
- Produces: `_EnvWorker(env, encoder, next_request: Callable[[], EpisodeStart] | None)` — `reset`/`step` unpack the record into `env.reset(requested_ids=..., seed_anchor_k=...)`. `collect_rollout(..., sample_request: Callable[[], EpisodeStart] | None)`.

- [ ] **Step 1: Write the failing test**

In `tests/ml/test_vector_env.py` (follow the module's existing `_EnvWorker`/`SyncVectorEnv` construction patterns):

```python
from ml.curriculum import EpisodeStart

def test_worker_reset_passes_seed_anchor_k_from_episode_start(make_anchored_worker):
    # make_anchored_worker: an _EnvWorker over a 2-object anchored box env whose
    # next_request returns EpisodeStart(("fuji","aviat_husky"), seed_anchor_k=0).
    worker = make_anchored_worker(lambda: EpisodeStart(("fuji", "aviat_husky"), 0))
    worker.reset()
    assert worker._env._parked == []          # k=0 from the record -> nothing pre-parked

def test_worker_reset_anchors_when_episode_start_k_is_one(make_anchored_worker):
    worker = make_anchored_worker(lambda: EpisodeStart(("fuji", "aviat_husky"), 1))
    worker.reset()
    assert len(worker._env._parked) == 1      # k=1 from the record -> one pre-parked
```

If no `make_anchored_worker` fixture exists, construct the `_EnvWorker` inline as the existing tests in this file do (build the anchored env via `stage_builder.build_stage_env(_PAIR_ANCHORED_STAGE)` + an `EncoderConfig`).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_vector_env.py -k episode_start -v`
Expected: FAIL — worker passes a bare tuple, `_parked` not as asserted (or AttributeError on `.requested_ids`).

- [ ] **Step 3: Update the worker + collector**

In `ml/vector_env.py`:

```python
from ml.curriculum import EpisodeStart, EpisodeStat  # extend the existing curriculum import
...
    def __init__(
        self,
        env: HangarFitEnv,
        encoder: EncoderConfig,
        next_request: Callable[[], EpisodeStart] | None,
    ) -> None:
        ...

    def reset(self) -> ObservationTensors:
        start = self._next_request() if self._next_request is not None else None
        obs = self._env.reset(
            requested_ids=start.requested_ids if start else None,
            seed_anchor_k=start.seed_anchor_k if start else None,
        )
        return self._encode(obs)
```

And in `_EnvWorker.step`, the on-`done` auto-reset block (lines 64-65):

```python
        if done:
            ep = EpisodeStat(
                fraction_placed=info.placed / info.total,
                valid=info.valid,
                total_reward=0.0,
            )
            start = self._next_request() if self._next_request is not None else None
            sem = self._env.reset(
                requested_ids=start.requested_ids if start else None,
                seed_anchor_k=start.seed_anchor_k if start else None,
            )
```

In `ml/train.py` `collect_rollout`, change the type and the episode-boundary reset (line 146):

```python
def collect_rollout(
    env: HangarFitEnv,
    policy: HangarFitPolicy,
    encoder: EncoderConfig,
    rollout_len: int,
    *,
    sample_request: Callable[[], EpisodeStart] | None = None,
) -> tuple[RolloutBuffer, list[EpisodeStat]]:
    ...
            if done:
                ...
                start = sample_request() if sample_request else None
                obs = env.reset(
                    requested_ids=start.requested_ids if start else None,
                    seed_anchor_k=start.seed_anchor_k if start else None,
                )
```

Add `EpisodeStart` to the existing `from ml.curriculum import ...` in `ml/train.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/ml/test_vector_env.py tests/ml/test_train_curriculum.py -v`
Expected: PASS. (These still build samplers via `partial(sample_request, ...)` returning tuples — they break until Task 6 switches call sites to `make_episode_sampler`. If a pre-existing test constructs a worker with a tuple-returning `next_request`, update it to return `EpisodeStart` or defer that test's run to after Task 6. Do NOT change production call sites here.)

> Note: production wiring (`partial(sample_request, ...)` → `make_episode_sampler`) lands in Task 6. To keep Task 3 green in isolation, any existing test that passes a tuple-returning sampler into `_EnvWorker`/`collect_rollout` must wrap it as `EpisodeStart`. Grep: `rg "next_request|sample_request=" tests/ml`.

- [ ] **Step 5: Commit**

```bash
git add ml/vector_env.py ml/train.py tests/ml/test_vector_env.py
git commit -m "feat(712): thread EpisodeStart(seed_anchor_k) through worker + collector"
```

---

### Task 4: `DifficultyConfig.anchor_prob` + ladder validation

**Files:**
- Modify: `ml/types.py` — `DifficultyConfig` (line 77)
- Modify: `ml/curriculum.py` — `validate_ladder` (line 193)
- Test: `tests/ml/test_curriculum.py`

**Interfaces:**
- Produces: `DifficultyConfig.anchor_prob: float | None = None` (`None` ⇒ not a mixed rung). `validate_ladder` rejects `anchor_prob ∉ [0,1]` and a mixed rung lacking `anchor_layout_path`.

- [ ] **Step 1: Write the failing test**

In `tests/ml/test_curriculum.py`:

```python
import pytest
from ml.curriculum import Stage, validate_ladder, _BOX_HANGAR, _BOX_FLEET, _WITNESS_BOX
from ml.types import DifficultyConfig

def _mixed_stage(anchor_prob, anchor_path=_WITNESS_BOX):
    return Stage(
        name="pair-mixed",
        difficulty=DifficultyConfig(max_objects=2, seed_anchor_k=1, anchor_prob=anchor_prob),
        hangar_path=_BOX_HANGAR,
        fleet_path=_BOX_FLEET,
        anchor_layout_path=anchor_path,
    )

def test_validate_ladder_accepts_valid_mixed_rung():
    validate_ladder([_mixed_stage(0.5)], encoder_max_objects=8)  # no raise

@pytest.mark.parametrize("p", [-0.1, 1.1])
def test_validate_ladder_rejects_anchor_prob_out_of_range(p):
    with pytest.raises(ValueError, match="anchor_prob"):
        validate_ladder([_mixed_stage(p)], encoder_max_objects=8)

def test_validate_ladder_rejects_mixed_rung_without_witness():
    with pytest.raises(ValueError, match="anchor_layout_path"):
        validate_ladder([_mixed_stage(0.5, anchor_path=None)], encoder_max_objects=8)
```

(If `_BOX_HANGAR`/`_WITNESS_BOX` are private, import them as the existing curriculum tests do, or inline the literal paths `"data/hangar.yaml"` / `"tests/fixtures/ml/witness_box.yaml"`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_curriculum.py -k "mixed_rung or anchor_prob" -v`
Expected: FAIL — `DifficultyConfig` has no `anchor_prob` (TypeError) or validate doesn't raise.

- [ ] **Step 3: Implement the field + validation**

In `ml/types.py`, add to `DifficultyConfig` (after `seed_anchor_k`):

```python
    # #712 mixed-start rung: when set, each episode draws k = seed_anchor_k with this
    # probability else 0 (drawn from the curriculum's seeded stream), keeping empty-start
    # episodes in the training mix. None => fixed-k rung (use seed_anchor_k as-is) =>
    # byte-identical to the pre-change env. anchor_prob is P(k = seed_anchor_k) per episode.
    anchor_prob: float | None = None
```

In `ml/curriculum.py` `validate_ladder`, inside the `for s in ladder:` loop (after the existing `seed_anchor_k` guards, ~line 215):

```python
        ap = s.difficulty.anchor_prob
        if ap is not None:
            if not (0.0 <= ap <= 1.0):
                raise ValueError(
                    f"stage {s.name!r}: anchor_prob must be in [0, 1], got {ap}"
                )
            if s.anchor_layout_path is None:
                raise ValueError(
                    f"stage {s.name!r}: a mixed-start rung (anchor_prob set) needs an "
                    f"anchor_layout_path (the witness the per-episode k draws from)"
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/ml/test_curriculum.py -k "mixed_rung or anchor_prob" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ml/types.py ml/curriculum.py tests/ml/test_curriculum.py
git commit -m "feat(712): DifficultyConfig.anchor_prob + mixed-rung ladder validation"
```

---

### Task 5: `pair-mixed` rung + `with_mixed_anchor_rung` builder

**Files:**
- Modify: `ml/curriculum.py` — add `_PAIR_MIXED_STAGE` (near `_PAIR_ANCHORED_STAGE`, line 284) + `with_mixed_anchor_rung` (after `with_pair_anchored_rung`, line 366)
- Test: `tests/ml/test_curriculum.py`

**Interfaces:**
- Consumes: `CurriculumSchedule`, `with_pair_anchored_rung`, `DEFAULT_LADDER`.
- Produces: `with_mixed_anchor_rung(schedule: CurriculumSchedule) -> CurriculumSchedule` — inserts `_PAIR_MIXED_STAGE` immediately BEFORE `pair-box`.

- [ ] **Step 1: Write the failing test**

In `tests/ml/test_curriculum.py`:

```python
from ml.curriculum import (
    CurriculumSchedule, DEFAULT_LADDER, with_pair_anchored_rung, with_mixed_anchor_rung,
)

def test_with_mixed_anchor_rung_inserts_before_pair_box():
    sched = with_mixed_anchor_rung(CurriculumSchedule.default())
    names = [s.name for s in sched.stages]
    assert "pair-mixed" in names
    assert names.index("pair-mixed") == names.index("pair-box") - 1

def test_mixed_rung_sits_between_pair_anchored_and_pair_box():
    sched = with_mixed_anchor_rung(with_pair_anchored_rung(CurriculumSchedule.default()))
    names = [s.name for s in sched.stages]
    assert names.index("pair-anchored") < names.index("pair-mixed") < names.index("pair-box")

def test_mixed_rung_config_is_two_object_anchor_prob_half():
    sched = with_mixed_anchor_rung(CurriculumSchedule.default())
    rung = next(s for s in sched.stages if s.name == "pair-mixed")
    assert rung.difficulty.max_objects == 2
    assert rung.difficulty.seed_anchor_k == 1
    assert rung.difficulty.anchor_prob == 0.5
    assert rung.anchor_layout_path is not None    # reuses witness_box

def test_default_ladder_untouched_by_mixed_builder():
    before = tuple(s.name for s in DEFAULT_LADDER)
    with_mixed_anchor_rung(CurriculumSchedule.default())
    assert tuple(s.name for s in DEFAULT_LADDER) == before   # no mutation

def test_with_mixed_anchor_rung_raises_without_pair_box():
    sched = CurriculumSchedule(stages=(DEFAULT_LADDER[0],))  # trivial only
    with pytest.raises(ValueError, match="pair-box"):
        with_mixed_anchor_rung(sched)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_curriculum.py -k mixed_anchor_rung -v`
Expected: FAIL — `cannot import name 'with_mixed_anchor_rung'`.

- [ ] **Step 3: Implement the rung + builder**

In `ml/curriculum.py`, after `_PAIR_ANCHORED_STAGE` (line 284):

```python
# Opt-in #712 mixed-start rung (wired via with_mixed_anchor_rung / --mixed-anchor), NOT in
# DEFAULT_LADDER. Two objects; each episode randomly starts anchored (k=1) or empty (k=0) with
# probability anchor_prob, drawn from the curriculum's seeded stream — empty-start episodes stay
# in the training mix so the policy does not collapse to place-nothing on the empty-start
# pair-box. Reuses the pair-anchored witness (no new fixture). Budget matches pair-box's 2-object
# drive (an empty-start episode drives both objects).
_PAIR_MIXED_STAGE = Stage(
    name="pair-mixed",
    difficulty=DifficultyConfig(
        max_objects=2,
        seed_anchor_k=1,
        anchor_prob=0.5,
        per_object_step_budget=60,
        total_step_budget=140,
    ),
    hangar_path=_BOX_HANGAR,
    fleet_path=_BOX_FLEET,
    anchor_layout_path=_WITNESS_BOX,
    clearance_m=_LENIENT_CLEARANCE,
)
```

After `with_pair_anchored_rung` (line 366):

```python
def with_mixed_anchor_rung(schedule: CurriculumSchedule) -> CurriculumSchedule:
    """Return ``schedule`` with the opt-in ``pair-mixed`` rung inserted immediately BEFORE the
    ``pair-box`` rung — the #712 ``--mixed-anchor`` lever. Each episode randomly starts anchored
    (k=1) or empty (k=0) with probability ``anchor_prob`` (drawn from the seeded stream), so
    empty-start episodes stay in the training mix and the policy does not collapse to the
    place-nothing pole on the empty-start pair-box. Apply AFTER ``with_pair_anchored_rung`` so
    pair-mixed lands between pair-anchored and pair-box. Only the ladder changes (policy
    preserved); the default ladder is untouched, so default runs stay byte-identical."""
    stages = schedule.stages
    try:
        before = next(i for i, s in enumerate(stages) if s.name == "pair-box")
    except StopIteration:
        raise ValueError(
            "with_mixed_anchor_rung: schedule has no 'pair-box' rung to insert before"
        ) from None
    return replace(schedule, stages=stages[:before] + (_PAIR_MIXED_STAGE,) + stages[before:])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/ml/test_curriculum.py -k mixed_anchor_rung -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ml/curriculum.py tests/ml/test_curriculum.py
git commit -m "feat(712): pair-mixed rung + with_mixed_anchor_rung builder"
```

---

### Task 6: Wire the sampler selection + `--mixed-anchor` CLI

**Files:**
- Modify: `ml/curriculum.py` — add `make_episode_sampler` (after the samplers from Task 2)
- Modify: `ml/train.py` — `collect_rollout` call site (line 402 `next_request`), `_build_stage_worker` (line 225 `wnext`), argparse `--mixed-anchor`, `main()` schedule assembly
- Test: `tests/ml/test_curriculum.py`, `tests/ml/test_train_curriculum.py`

**Interfaces:**
- Consumes: `plain_start`, `sample_mixed_start` (Task 2), `with_mixed_anchor_rung` (Task 5).
- Produces: `make_episode_sampler(stage: Stage, pool: Sequence[str], n: int, rng: random.Random) -> Callable[[], EpisodeStart]` — returns a mixed sampler when `stage.difficulty.anchor_prob is not None`, else the plain sampler. `--mixed-anchor` flag toggles `with_mixed_anchor_rung`.

- [ ] **Step 1: Write the failing test**

In `tests/ml/test_curriculum.py`:

```python
import random
from ml.curriculum import make_episode_sampler, _PAIR_MIXED_STAGE, _SOLO_BOX_STAGE

def test_make_episode_sampler_plain_for_non_mixed_stage():
    # solo-box has anchor_prob None -> plain sampler -> seed_anchor_k None, byte-identical ids.
    pool = ("fuji",)
    rng1, rng2 = random.Random(3), random.Random(3)
    s = make_episode_sampler(_SOLO_BOX_STAGE, pool, 1, rng1)()
    from ml.curriculum import sample_request
    assert s.seed_anchor_k is None
    assert s.requested_ids == sample_request(pool, 1, rng2)   # same rng draw

def test_make_episode_sampler_mixed_for_mixed_stage_varies_k():
    pool = ("fuji", "aviat_husky")
    rng = random.Random(5)
    sampler = make_episode_sampler(_PAIR_MIXED_STAGE, pool, 2, rng)
    ks = {sampler().seed_anchor_k for _ in range(200)}
    assert ks == {0, 1}                       # mixture draws both
```

In `tests/ml/test_train_curriculum.py` (CLI), mirror the existing `--seed-anchor` / `--solo-box-rung` argparse tests:

```python
def test_mixed_anchor_flag_inserts_pair_mixed(monkeypatch):
    # Reuse this module's helper that builds the schedule from argv as the seed-anchor
    # test does; assert "pair-mixed" appears only when --mixed-anchor is passed.
    sched_off = build_schedule_from_args(["--schedule", "curriculum"])
    sched_on = build_schedule_from_args(["--schedule", "curriculum", "--seed-anchor", "--mixed-anchor"])
    assert "pair-mixed" not in [s.name for s in sched_off.stages]
    names = [s.name for s in sched_on.stages]
    assert names.index("pair-anchored") < names.index("pair-mixed") < names.index("pair-box")
```

(If `tests/ml/test_train_curriculum.py` has no `build_schedule_from_args` helper, follow exactly how the existing `--seed-anchor` test exercises the flag — e.g. calling `main`/`build_argparser` + the schedule-assembly path — and assert on the resulting stage names.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_curriculum.py -k make_episode_sampler tests/ml/test_train_curriculum.py -k mixed_anchor -v`
Expected: FAIL — `cannot import name 'make_episode_sampler'` / unrecognized argument `--mixed-anchor`.

- [ ] **Step 3a: Add `make_episode_sampler`**

In `ml/curriculum.py`, after `sample_mixed_start` (Task 2):

```python
def make_episode_sampler(
    stage: Stage, pool: Sequence[str], n: int, rng: random.Random
) -> Callable[[], EpisodeStart]:
    """Build this stage's per-episode start sampler. A mixed-start rung (anchor_prob set)
    gets ``sample_mixed_start`` (seeded per-episode k draw); every other rung gets
    ``plain_start`` (byte-identical id draw, k left to the env default). Both bind pool/n/rng
    by value via partial (no late-binding closure trap)."""
    ap = stage.difficulty.anchor_prob
    if ap is not None:
        return partial(
            sample_mixed_start, pool, n, rng,
            seed_anchor_k=stage.difficulty.seed_anchor_k, anchor_prob=ap,
        )
    return partial(plain_start, pool, n, rng)
```

Ensure `Callable` and `partial` are imported in `ml/curriculum.py` (add to the existing `from collections.abc import ...` / `from functools import partial` imports if absent).

- [ ] **Step 3b: Switch the two production call sites to `make_episode_sampler`**

In `ml/train.py`:
- Line 402: `next_request = partial(sample_request, pool, n, rng)` → `next_request = make_episode_sampler(stage, pool, n, rng)`.
- In `_build_stage_worker` (line 225): `wnext = partial(sample_request, pool, n, wrng)` → `wnext = make_episode_sampler(stage, pool, n, wrng)`.

Update the `from ml.curriculum import ...` block in `ml/train.py` to import `make_episode_sampler` (and drop the now-unused `sample_request` import if nothing else uses it — check with `rg "sample_request" ml/train.py`).

- [ ] **Step 3c: Add the `--mixed-anchor` flag + schedule wiring**

In `ml/train.py` argparse (next to `--seed-anchor`):

```python
    p.add_argument(
        "--mixed-anchor",
        action="store_true",
        help="curriculum: insert the opt-in #712 'pair-mixed' rung before pair-box (each "
        "episode randomly starts anchored k=1 or empty k=0 by a fixed probability), keeping "
        "empty-start episodes in the training mix so the policy does not collapse to "
        "place-nothing. Apply with --seed-anchor so pair-mixed lands between pair-anchored "
        "and pair-box.",
    )
```

In `main()` schedule assembly, AFTER the `--seed-anchor` insertion (so pair-mixed lands after pair-anchored):

```python
    if args.seed_anchor:
        schedule = with_pair_anchored_rung(schedule)
    if args.mixed_anchor:
        schedule = with_mixed_anchor_rung(schedule)
```

Import `with_mixed_anchor_rung` in `ml/train.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run from repo root:
```bash
pytest tests/ml/test_curriculum.py tests/ml/test_train_curriculum.py tests/ml/test_vector_env.py -v
mypy ml/
ruff check ml/ tests/ml/
```
Expected: PASS / clean. (The Task 3 note about tuple-returning test samplers is now moot — production builds `EpisodeStart` via `make_episode_sampler`.)

- [ ] **Step 5: Commit**

```bash
git add ml/curriculum.py ml/train.py tests/ml/test_curriculum.py tests/ml/test_train_curriculum.py
git commit -m "feat(712): make_episode_sampler + --mixed-anchor CLI wiring"
```

---

### Task 7: Docs — `ml/README.md` flag + recipe, CHANGELOG

**Files:**
- Modify: `ml/README.md` — flag table + a "Mixed-anchor gate recipe" section
- Modify: `CHANGELOG.md` — `[Unreleased]` (line 5)

**Interfaces:** none (docs).

- [ ] **Step 1: Add the flag to the `ml/README.md` table**

Add a row beside the `--seed-anchor` row:

```markdown
| `--mixed-anchor` | off | Insert an opt-in `pair-mixed` rung **before** `pair-box`: each episode randomly starts anchored (k=1) or empty (k=0) with probability `anchor_prob=0.5`, drawn from the curriculum's seeded stream. Keeps empty-start episodes in the training mix so the policy does not collapse to the place-nothing pole on the empty-start `pair-box`. Apply WITH `--seed-anchor`. Curriculum-only. (#712 follow-up) |
```

And append `--mixed-anchor` to the opt-in-levers line near the `--seed-anchor`/`--solo-box-rung` list.

- [ ] **Step 2: Add the gate recipe section**

After the "Pair-anchored gate recipe" section, add:

```markdown
### Mixed-anchor gate recipe (#712 follow-up, step 2)

The #712 cap-80 pre-check confirmed k=1 masters but the empty-start `pair-box` still collapses
to place-nothing (the k=1→k=0 start-state cliff). The `pair-mixed` rung keeps empty-start
episodes in the training mix so the policy bridges the cliff.

\`\`\`bash
# Same #714 economics + --seed-anchor, plus --mixed-anchor (pair-mixed before pair-box).
# cap 80 so each rung clears the 40-iter entropy warmup into exploitation.
python -u -m ml.train --schedule curriculum --device cuda --n-envs 16 \
  --rollout-len 512 --max-iters-per-stage 80 \
  --promotion-metric valid_placed --promotion-threshold 0.9 \
  --r-valid-park 30.0 --r-unplaced-penalty 8.0 --dense-slot-potential \
  --entropy-start 0.05 --entropy-end 0.005 --entropy-anneal-iters 40 \
  --normalize-returns --validity-conditional-terminal --solo-box-rung \
  --seed-anchor --mixed-anchor \
  --metrics-out metrics-seed0-mixed.jsonl --checkpoint-out ck-seed0-mixed.pt --seed 0
\`\`\`

WIN: `pair-mixed` lifts and ideally promotes by competency, AND the downstream all-empty
`pair-box` no longer collapses (lifts off 0.000). Read `valid_placed`, not `valid_rate`.
\`\`\`
```

(Use real backticks for the code fence in the actual file — the `\`` above is escaping for this plan only.)

- [ ] **Step 3: Add the CHANGELOG entry**

Under `## [Unreleased]` (line 5), in the appropriate `### Added` subsection:

```markdown
- **Mixed-start anchor curriculum rung (`--mixed-anchor`, #712 follow-up).** An opt-in
  `pair-mixed` rung where each episode randomly starts anchored (k=1) or empty (k=0), keeping
  empty-start episodes in the training mix to bridge the k=1→k=0 start-state cliff that
  collapsed the empty-start `pair-box` to place-nothing. Default-off ⇒ byte-identical training.
```

- [ ] **Step 4: Verify docs build / no broken refs**

Run: `rg -n "mixed-anchor|pair-mixed" ml/README.md CHANGELOG.md`
Expected: the new rows/sections present.

- [ ] **Step 5: Commit**

```bash
git add ml/README.md CHANGELOG.md
git commit -m "docs(712): document --mixed-anchor flag, gate recipe, CHANGELOG"
```

---

## Final verification (after all tasks)

- [ ] Full ml/ suite + lint + types from repo root:
```bash
pytest tests/ml/ -v
mypy ml/
ruff check ml/ tests/ml/ && ruff format --check ml/ tests/ml/
```
- [ ] **Determinism smoke:** `--mixed-anchor` absent ⇒ schedule + a short CPU run are byte-identical to pre-change (the `ml-rl-guard` byte-identity equivalence test; no checkpoint-hash comparison — torch CPU is nondeterministic across processes, so prove equivalence via the fixed-action reward-stream / k-draw-sequence diff).
- [ ] **`ml-rl-guard` review pass** on the diff (seeding/reproducibility, knob default-neutrality — `anchor_prob=None`/flag-absent neutral; validity = product checker; numeric/GAE guards untouched).

## Spec coverage check

- §4.1 env override → Task 1. §4.2 EpisodeStart + mixed sampler → Tasks 2, 3. §4.3 pair-mixed rung + builder + `anchor_prob` field + CLI → Tasks 4, 5, 6. §4.4 validation → Task 4. §5 component boundaries → Tasks 1-6. §6 tests → every task's Step 1 + Final verification. §7 out-of-scope (ramp/trio/notch) → intentionally not implemented. §8 determinism → Global Constraints + Final verification.
