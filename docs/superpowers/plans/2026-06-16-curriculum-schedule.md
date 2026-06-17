# Curriculum Schedule (SP#4b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, competency-gated curriculum that ramps `DifficultyConfig` + hangar shape + clearance over the SP#4a PPO training core, so `python -m ml.train --schedule curriculum` climbs a multi-rung ladder instead of training one fixed stage.

**Architecture:** A new **pure, torch-free, disk-free** `ml/curriculum.py` (Stage ladder, promotion gate, seeded per-episode object sampling, history). A new **torch-free, disk-touching** `ml/stage_builder.py` (resolve a stage's fleet pool + build its env). A small backward-compatible `HangarFitEnv.reset(requested_ids=None)`. `ml/train.py` gains a `train_curriculum` loop + `--schedule` CLI and adapts `collect_rollout`/`train` to a richer per-episode stat. One policy/optimizer carries weights across rungs (transfer); only the env changes at promotion.

**Tech Stack:** Python 3.12, PyTorch (training only, gated by `pytest.importorskip("torch")`), pytest. Reuses `hangarfit.loader`, `HangarFitEnv` (SP#1), `ml.encoding` (SP#2), `ml.policy`/`ml.ppo` (SP#3/#4a).

**Spec:** `docs/superpowers/specs/2026-06-16-learned-backend-curriculum-schedule-design.md` (read it first).

---

## File structure

| File | Responsibility | torch? | disk? |
|---|---|---|---|
| `ml/curriculum.py` (create) | `Stage`, `PromotionPolicy`, `EpisodeStat`, `should_promote`, `stage_rng`, `sample_request`, `validate_ladder`, `CurriculumHistory`, `CurriculumSchedule`, `DEFAULT_LADDER` | no | no |
| `ml/stage_builder.py` (create) | `effective_fleet_ids(stage)`, `build_stage_env(stage)` | no | yes |
| `ml/env.py` (modify) | `reset(requested_ids=None)` override + validation | no | no |
| `ml/train.py` (modify) | extend `collect_rollout`, adapt `train`, add `train_curriculum`, `build_argparser`, `--schedule` in `main` | yes | yes |
| `tests/ml/test_curriculum.py` (create) | pure unit tests for `ml/curriculum.py` | no | no |
| `tests/ml/test_stage_builder.py` (create) | unit tests for `ml/stage_builder.py` | no | yes |
| `tests/ml/test_env.py` (modify) | `reset(requested_ids=…)` override tests | no | no |
| `tests/ml/test_train_curriculum.py` (create) | torch-gated: collect_rollout stats, curriculum canary, cap path, CLI parser | yes | yes |
| `CHANGELOG.md` (modify) | `[Unreleased]` entry | — | — |

**Conventions to mirror** (verified): torch-gated test files start with `torch = pytest.importorskip("torch")` then `# noqa: E402` imports (see `tests/ml/test_ppo.py`). `tests/ml/conftest.py` exposes `empty_hangar()` and plain helper builders. Repo-relative paths use `Path(__file__).resolve().parent.parent` (the repo root, since `ml/` sits at repo root — see `ml/train.py:36`). Run tests with `PYTHONPATH` already wired by the editable install; from the repo root a bare `pytest tests/ml/...` works.

---

### Task 1: `ml/curriculum.py` — `EpisodeStat`, `PromotionPolicy`, `should_promote`

**Files:**
- Create: `ml/curriculum.py`
- Test: `tests/ml/test_curriculum.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ml/test_curriculum.py
"""Pure unit tests for ml/curriculum.py — no torch, no disk."""

from __future__ import annotations

import pytest

from ml.curriculum import EpisodeStat, PromotionPolicy, should_promote


def test_should_promote_fires_when_windowed_mean_meets_threshold():
    pol = PromotionPolicy(metric="fraction_placed", window=2, threshold=0.5)
    window = [EpisodeStat(0.4, False, -1.0), EpisodeStat(0.8, True, 1.0)]  # mean 0.6 >= 0.5
    assert should_promote(window, pol) is True


def test_should_promote_false_below_threshold():
    pol = PromotionPolicy(metric="fraction_placed", window=2, threshold=0.9)
    window = [EpisodeStat(0.4, False, -1.0), EpisodeStat(0.8, True, 1.0)]  # mean 0.6 < 0.9
    assert should_promote(window, pol) is False


def test_should_promote_waits_for_full_window():
    pol = PromotionPolicy(window=3, threshold=0.0)
    assert should_promote([EpisodeStat(1.0, True, 1.0)], pol) is False  # only 1 < window 3


def test_should_promote_uses_last_window_only():
    pol = PromotionPolicy(metric="fraction_placed", window=2, threshold=0.95)
    # old low episodes must NOT drag down a recently-mastered window
    window = [EpisodeStat(0.0, False, 0.0), EpisodeStat(1.0, True, 0.0), EpisodeStat(1.0, True, 0.0)]
    assert should_promote(window, pol) is True  # last 2 both 1.0


def test_should_promote_valid_rate_metric():
    pol = PromotionPolicy(metric="valid_rate", window=2, threshold=1.0)
    assert should_promote([EpisodeStat(1.0, True, 0.0), EpisodeStat(0.5, False, 0.0)], pol) is False
    assert should_promote([EpisodeStat(0.1, True, 0.0), EpisodeStat(0.2, True, 0.0)], pol) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_curriculum.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ml.curriculum'` (or ImportError for the symbols).

- [ ] **Step 3: Write minimal implementation**

```python
# ml/curriculum.py
"""The cold-joint curriculum schedule (sub-project #4b, #607). PURE: no torch, no
disk IO. Owns the difficulty ladder, the competency-gated promotion rule, the
seeded per-episode object-set sampling, and the run history. The disk-touching
env builder lives in ml/stage_builder.py; the torch training loop in ml/train.py."""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from ml.types import DifficultyConfig


@dataclass(frozen=True, slots=True)
class EpisodeStat:
    """Per-completed-episode signal captured from the terminal StepInfo."""

    fraction_placed: float  # placed / total
    valid: bool  # StepInfo.valid (overlap == 0) at episode end
    total_reward: float  # sum of step rewards — keeps the 4a reward curve alive


@dataclass(frozen=True, slots=True)
class PromotionPolicy:
    """When to advance to the next (harder) rung."""

    metric: Literal["fraction_placed", "valid_rate"] = "fraction_placed"
    window: int = 20  # most-recent completed episodes to average
    threshold: float = 0.9  # advance when mean(metric over window) >= threshold
    max_iters: int = 200  # safety cap: advance unconditionally after this many PPO iters


def should_promote(window: Sequence[EpisodeStat], policy: PromotionPolicy) -> bool:
    """Pure gate: True when the last ``policy.window`` episodes meet the threshold."""
    if len(window) < policy.window:
        return False
    recent = list(window)[-policy.window :]
    if policy.metric == "valid_rate":
        score = sum(1.0 for s in recent if s.valid) / len(recent)
    else:
        score = sum(s.fraction_placed for s in recent) / len(recent)
    return score >= policy.threshold
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ml/test_curriculum.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/curriculum.py tests/ml/test_curriculum.py
git commit -m "feat(607): curriculum EpisodeStat + competency-gated should_promote"
```

---

### Task 2: `ml/curriculum.py` — `stage_rng` + `sample_request`

**Files:**
- Modify: `ml/curriculum.py`
- Test: `tests/ml/test_curriculum.py`

- [ ] **Step 1: Write the failing test** (append to `tests/ml/test_curriculum.py`)

```python
from ml.curriculum import sample_request, stage_rng  # add to the imports block


def test_sample_request_is_deterministic_for_equal_rngs():
    pool = ("a", "b", "c", "d", "e")
    assert sample_request(pool, 3, stage_rng(0, 0)) == sample_request(pool, 3, stage_rng(0, 0))


def test_sample_request_size_membership_no_dupes():
    pool = ("a", "b", "c", "d")
    got = sample_request(pool, 2, stage_rng(1, 0))
    assert len(got) == 2
    assert len(set(got)) == 2
    assert set(got) <= set(pool)


def test_sample_request_raises_when_n_exceeds_pool():
    with pytest.raises(ValueError):
        sample_request(("a", "b"), 3, stage_rng(0, 0))


def test_stage_rng_keyed_by_stage_index():
    # different ladder positions => different stream => (near-certainly) different draw
    assert stage_rng(0, 0).random() != stage_rng(0, 1).random()


def test_stage_rng_keyed_by_seed():
    assert stage_rng(0, 0).random() != stage_rng(1, 0).random()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_curriculum.py -q -k "sample_request or stage_rng"`
Expected: FAIL — ImportError for `sample_request` / `stage_rng`.

- [ ] **Step 3: Write minimal implementation** (append to `ml/curriculum.py`)

```python
_STAGE_RNG_STRIDE = 100003  # a prime, so (seed, stage_index) pairs don't collide


def stage_rng(seed: int, stage_index: int) -> random.Random:
    """A per-stage RNG isolated from torch's global stream. Seeded purely from
    integers (no str/bytes), so it is reproducible within a build regardless of
    PYTHONHASHSEED. Keyed by the rung's ladder position so a rung's episode
    sequence is independent of how many iterations earlier rungs took."""
    return random.Random(seed * _STAGE_RNG_STRIDE + stage_index)


def sample_request(pool: Sequence[str], n: int, rng: random.Random) -> tuple[str, ...]:
    """Draw a size-``n`` subset (in selection order) from an explicit id ``pool``.
    Pure over ``pool`` — no disk. ``rng.sample`` raises ValueError if ``n`` exceeds
    the pool, which is the loud failure we want."""
    return tuple(rng.sample(list(pool), n))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ml/test_curriculum.py -q`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/curriculum.py tests/ml/test_curriculum.py
git commit -m "feat(607): seeded stage_rng + pure sample_request"
```

---

### Task 3: `ml/curriculum.py` — `Stage` + `validate_ladder`

**Files:**
- Modify: `ml/curriculum.py`
- Test: `tests/ml/test_curriculum.py`

- [ ] **Step 1: Write the failing test** (append)

```python
from ml.curriculum import Stage, validate_ladder  # add to imports
from ml.types import DifficultyConfig  # add to imports


def _stage(name="s", n=1, hangar="data/hangar.yaml", fleet="data/fleet.yaml", ids=None, clearance=0.05):
    return Stage(
        name=name,
        difficulty=DifficultyConfig(max_objects=n, per_object_step_budget=40, total_step_budget=40),
        hangar_path=hangar,
        fleet_path=fleet,
        fleet_ids=ids,
        clearance_m=clearance,
    )


def test_validate_ladder_accepts_a_valid_ladder():
    validate_ladder((_stage(n=1), _stage(n=2)), encoder_max_objects=16)  # no raise


def test_validate_ladder_rejects_empty():
    with pytest.raises(ValueError):
        validate_ladder((), encoder_max_objects=16)


def test_validate_ladder_rejects_max_objects_over_encoder_cap():
    with pytest.raises(ValueError):
        validate_ladder((_stage(n=17),), encoder_max_objects=16)


def test_validate_ladder_rejects_none_or_nonpositive_max_objects():
    bad = Stage(
        name="bad",
        difficulty=DifficultyConfig(max_objects=None),
        hangar_path="data/hangar.yaml",
        fleet_path="data/fleet.yaml",
    )
    with pytest.raises(ValueError):
        validate_ladder((bad,), encoder_max_objects=16)


def test_stage_defaults():
    s = _stage()
    assert s.apron_depth_m == 8.0
    assert s.wing_layer_clearance_m is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_curriculum.py -q -k "ladder or stage_defaults"`
Expected: FAIL — ImportError for `Stage` / `validate_ladder`.

- [ ] **Step 3: Write minimal implementation** (append to `ml/curriculum.py`)

```python
@dataclass(frozen=True, slots=True)
class Stage:
    """One rung of the ladder. Holds fleet/hangar as repo-relative PATH STRINGS +
    scalar overrides — no loading happens here (that is stage_builder.py's job),
    so this module stays disk-free. DifficultyConfig.seed_anchor is left False;
    anchored spawning is a later rung, out of 4b scope."""

    name: str
    difficulty: DifficultyConfig
    hangar_path: str
    fleet_path: str
    fleet_ids: tuple[str, ...] | None = None  # explicit sampling pool; None = whole fleet (resolved in stage_builder)
    clearance_m: float | None = None  # override on the loaded hangar; None = file value
    wing_layer_clearance_m: float | None = None  # override; None = file value
    apron_depth_m: float = 8.0  # override; gives the env a spawn region (matches 4a)


def validate_ladder(ladder: Sequence[Stage], *, encoder_max_objects: int) -> None:
    """Pure invariant check (the disk-needing 'max_objects <= len(pool)' check lives
    in stage_builder.build_stage_env). Raises ValueError on the first violation."""
    if not ladder:
        raise ValueError("curriculum ladder is empty")
    for s in ladder:
        n = s.difficulty.max_objects
        if n is None or n < 1:
            raise ValueError(f"stage {s.name!r}: max_objects must be a positive int, got {n!r}")
        if n > encoder_max_objects:
            raise ValueError(
                f"stage {s.name!r}: max_objects {n} exceeds encoder capacity {encoder_max_objects}"
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ml/test_curriculum.py -q`
Expected: PASS (15 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/curriculum.py tests/ml/test_curriculum.py
git commit -m "feat(607): Stage dataclass + pure validate_ladder invariants"
```

---

### Task 4: `ml/curriculum.py` — `CurriculumHistory`, `CurriculumSchedule`, `DEFAULT_LADDER`

**Files:**
- Modify: `ml/curriculum.py`
- Test: `tests/ml/test_curriculum.py`

- [ ] **Step 1: Write the failing test** (append)

```python
from ml.curriculum import CurriculumHistory, CurriculumSchedule, DEFAULT_LADDER  # add to imports
from ml.encoding import EncoderConfig  # add to imports (numpy-only, no torch)


def test_curriculum_history_records_and_notes():
    h = CurriculumHistory()
    h.record("s0", 0, [EpisodeStat(0.5, False, -1.0)])
    h.note_promotion("s0", 3, by="competency")
    assert h.iterations == [("s0", 0, (EpisodeStat(0.5, False, -1.0),))]
    assert h.promotions == [("s0", 3, "competency")]


def test_curriculum_schedule_default_is_the_committed_ladder():
    sched = CurriculumSchedule.default()
    assert sched.stages == DEFAULT_LADDER
    assert isinstance(sched.policy, PromotionPolicy)


def test_default_ladder_has_five_named_rungs_spanning_three_dimensions():
    names = tuple(s.name for s in DEFAULT_LADDER)
    assert names == ("trivial", "pair-box", "trio-box", "trio-notch", "trio-notch-strict")
    # count ramps
    assert [s.difficulty.max_objects for s in DEFAULT_LADDER] == [1, 2, 2 + 1, 3, 3]
    # hangar shape changes at trio-notch
    assert DEFAULT_LADDER[2].hangar_path != DEFAULT_LADDER[3].hangar_path
    # clearance tightens at the final rung (lenient override -> file value)
    assert DEFAULT_LADDER[3].clearance_m == 0.05
    assert DEFAULT_LADDER[4].clearance_m is None  # inherits herrenteich file (0.10)


def test_default_ladder_passes_validation():
    validate_ladder(DEFAULT_LADDER, encoder_max_objects=EncoderConfig().max_objects)  # no raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_curriculum.py -q -k "history or schedule or default_ladder"`
Expected: FAIL — ImportError for `CurriculumHistory` / `CurriculumSchedule` / `DEFAULT_LADDER`.

- [ ] **Step 3: Write minimal implementation** (append to `ml/curriculum.py`)

```python
@dataclass
class CurriculumHistory:
    """Mutable, append-only record of training events — pure data, no torch, so CI
    can assert equality over it for the determinism canary."""

    iterations: list[tuple[str, int, tuple[EpisodeStat, ...]]] = field(default_factory=list)
    promotions: list[tuple[str, int, str]] = field(default_factory=list)

    def record(self, stage_name: str, it: int, ep_stats: Sequence[EpisodeStat]) -> None:
        self.iterations.append((stage_name, it, tuple(ep_stats)))

    def note_promotion(self, stage_name: str, it: int, *, by: str) -> None:
        self.promotions.append((stage_name, it, by))


@dataclass(frozen=True, slots=True)
class CurriculumSchedule:
    stages: tuple[Stage, ...]
    policy: PromotionPolicy

    @classmethod
    def default(cls) -> CurriculumSchedule:
        return cls(stages=DEFAULT_LADDER, policy=PromotionPolicy())


_BOX_HANGAR = "data/hangar.yaml"
_BOX_FLEET = "data/fleet.yaml"
_NOTCH_HANGAR = "examples/herrenteich/hangar.yaml"
_NOTCH_FLEET = "examples/herrenteich/fleet.yaml"
_LENIENT_CLEARANCE = 0.05  # below the herrenteich file value (0.10) so the clearance ramp truly tightens

DEFAULT_LADDER: tuple[Stage, ...] = (
    Stage(
        name="trivial",
        difficulty=DifficultyConfig(max_objects=1, per_object_step_budget=40, total_step_budget=40),
        hangar_path=_BOX_HANGAR,
        fleet_path=_BOX_FLEET,
        fleet_ids=("fuji",),
        clearance_m=_LENIENT_CLEARANCE,
    ),
    Stage(
        name="pair-box",
        difficulty=DifficultyConfig(max_objects=2, per_object_step_budget=60, total_step_budget=140),
        hangar_path=_BOX_HANGAR,
        fleet_path=_BOX_FLEET,
        clearance_m=_LENIENT_CLEARANCE,
    ),
    Stage(
        name="trio-box",
        difficulty=DifficultyConfig(max_objects=3, per_object_step_budget=60, total_step_budget=220),
        hangar_path=_BOX_HANGAR,
        fleet_path=_BOX_FLEET,
        clearance_m=_LENIENT_CLEARANCE,
    ),
    Stage(
        name="trio-notch",
        difficulty=DifficultyConfig(max_objects=3, per_object_step_budget=80, total_step_budget=260),
        hangar_path=_NOTCH_HANGAR,
        fleet_path=_NOTCH_FLEET,
        clearance_m=_LENIENT_CLEARANCE,
    ),
    Stage(
        name="trio-notch-strict",
        difficulty=DifficultyConfig(max_objects=3, per_object_step_budget=80, total_step_budget=260),
        hangar_path=_NOTCH_HANGAR,
        fleet_path=_NOTCH_FLEET,
        clearance_m=None,  # inherit the herrenteich file value (0.10) — the real strict rung
    ),
)
```

> Note: the test asserts `[1, 2, 2 + 1, 3, 3]` — `2 + 1` is just `3` written to read as "two then three". Keep the literal `3` in the code's `max_objects`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ml/test_curriculum.py -q`
Expected: PASS (19 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/curriculum.py tests/ml/test_curriculum.py
git commit -m "feat(607): CurriculumHistory, CurriculumSchedule, DEFAULT_LADDER"
```

---

### Task 5: `ml/stage_builder.py` — `effective_fleet_ids`

**Files:**
- Create: `ml/stage_builder.py`
- Test: `tests/ml/test_stage_builder.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ml/test_stage_builder.py
"""Unit tests for ml/stage_builder.py — touches disk (loader) but NO torch, so it
runs in the no-torch CI."""

from __future__ import annotations

from ml.curriculum import DEFAULT_LADDER, Stage
from ml.stage_builder import effective_fleet_ids
from ml.types import DifficultyConfig


def test_effective_fleet_ids_returns_explicit_pool_verbatim():
    s = Stage(
        name="x",
        difficulty=DifficultyConfig(max_objects=1),
        hangar_path="data/hangar.yaml",
        fleet_path="data/fleet.yaml",
        fleet_ids=("fuji", "aviat_husky"),
    )
    assert effective_fleet_ids(s) == ("fuji", "aviat_husky")


def test_effective_fleet_ids_resolves_whole_fleet_when_none():
    s = Stage(
        name="x",
        difficulty=DifficultyConfig(max_objects=1),
        hangar_path="data/hangar.yaml",
        fleet_path="data/fleet.yaml",
        fleet_ids=None,
    )
    ids = effective_fleet_ids(s)
    assert "fuji" in ids and "aviat_husky" in ids
    assert len(ids) >= 9  # the synthetic manifest lists 9 aircraft


def test_effective_fleet_ids_herrenteich_excludes_ground_objects():
    s = Stage(
        name="x",
        difficulty=DifficultyConfig(max_objects=1),
        hangar_path="examples/herrenteich/hangar.yaml",
        fleet_path="examples/herrenteich/fleet.yaml",
        fleet_ids=None,
    )
    ids = effective_fleet_ids(s)
    # load_fleet returns aircraft only; the 4 ground objects must NOT appear
    assert "vw_caddy" not in ids
    assert "maul_fuel_trailer" not in ids


def test_every_default_ladder_rung_pool_covers_max_objects():
    for s in DEFAULT_LADDER:
        pool = effective_fleet_ids(s)
        assert s.difficulty.max_objects <= len(pool)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_stage_builder.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ml.stage_builder'`.

- [ ] **Step 3: Write minimal implementation**

```python
# ml/stage_builder.py
"""Disk-touching, torch-free bridge between a curriculum Stage and a HangarFitEnv.
Split out of ml/train.py precisely because train.py imports torch at module level —
keeping these here lets their tests run in the no-torch CI."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from hangarfit.loader import load_fleet, load_hangar
from ml.curriculum import Stage
from ml.env import HangarFitEnv

_ROOT = Path(__file__).resolve().parent.parent  # repo root (ml/ sits at the root)


def effective_fleet_ids(stage: Stage) -> tuple[str, ...]:
    """The stage's sampling pool: its explicit ``fleet_ids`` if set, else the keys of
    ``load_fleet(stage.fleet_path)`` (aircraft only — a manifest's ground_objects load
    via a separate path and are never returned by load_fleet). The ONLY disk touch in
    the sampling chain; resolved once per rung by train_curriculum."""
    if stage.fleet_ids is not None:
        return stage.fleet_ids
    return tuple(load_fleet(str(_ROOT / stage.fleet_path)).keys())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ml/test_stage_builder.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/stage_builder.py tests/ml/test_stage_builder.py
git commit -m "feat(607): stage_builder.effective_fleet_ids pool resolver"
```

---

### Task 6: `ml/stage_builder.py` — `build_stage_env`

**Files:**
- Modify: `ml/stage_builder.py`
- Test: `tests/ml/test_stage_builder.py`

- [ ] **Step 1: Write the failing test** (append)

```python
import pytest  # add to imports

from ml.stage_builder import build_stage_env  # add to imports


def test_build_stage_env_applies_clearance_and_apron_overrides():
    s = Stage(
        name="x",
        difficulty=DifficultyConfig(max_objects=1, per_object_step_budget=40, total_step_budget=40),
        hangar_path="data/hangar.yaml",
        fleet_path="data/fleet.yaml",
        fleet_ids=("fuji",),
        clearance_m=0.05,
        apron_depth_m=8.0,
    )
    env = build_stage_env(s)
    assert env.hangar.clearance_m == 0.05
    assert env.hangar.apron_depth_m == 8.0
    assert env.difficulty.max_objects == 1
    assert "fuji" in env.fleet


def test_build_stage_env_strict_rung_inherits_file_clearance():
    strict = DEFAULT_LADDER[4]  # trio-notch-strict, clearance_m=None
    env = build_stage_env(strict)
    assert env.hangar.clearance_m == 0.10  # the herrenteich file value (#664)


def test_build_stage_env_raises_when_max_objects_exceeds_pool():
    s = Stage(
        name="toobig",
        difficulty=DifficultyConfig(max_objects=2),
        hangar_path="data/hangar.yaml",
        fleet_path="data/fleet.yaml",
        fleet_ids=("fuji",),  # pool of 1, but want 2
    )
    with pytest.raises(ValueError):
        build_stage_env(s)


def test_build_stage_env_every_default_rung_constructs():
    for s in DEFAULT_LADDER:
        env = build_stage_env(s)
        assert len(env.requested_ids) == s.difficulty.max_objects
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_stage_builder.py -q -k build_stage_env`
Expected: FAIL — ImportError for `build_stage_env`.

- [ ] **Step 3: Write minimal implementation** (append to `ml/stage_builder.py`)

```python
def build_stage_env(stage: Stage) -> HangarFitEnv:
    """Load the rung's hangar + fleet, apply the clearance/apron overrides, and build
    a HangarFitEnv whose difficulty is the stage's. The initial requested_ids is just
    the first ``max_objects`` of the pool — every episode resamples via
    env.reset(requested_ids=...). Raises if the pool can't supply max_objects."""
    hangar = load_hangar(str(_ROOT / stage.hangar_path))
    overrides: dict[str, float] = {"apron_depth_m": stage.apron_depth_m}
    if stage.clearance_m is not None:
        overrides["clearance_m"] = stage.clearance_m
    if stage.wing_layer_clearance_m is not None:
        overrides["wing_layer_clearance_m"] = stage.wing_layer_clearance_m
    hangar = replace(hangar, **overrides)

    fleet = load_fleet(str(_ROOT / stage.fleet_path))
    pool = effective_fleet_ids(stage)
    n = stage.difficulty.max_objects if stage.difficulty.max_objects is not None else len(pool)
    if n > len(pool):
        raise ValueError(
            f"stage {stage.name!r}: max_objects {n} exceeds fleet pool size {len(pool)}"
        )
    return HangarFitEnv(
        hangar=hangar,
        fleet=fleet,
        requested_ids=tuple(pool[:n]),
        difficulty=stage.difficulty,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ml/test_stage_builder.py -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/stage_builder.py tests/ml/test_stage_builder.py
git commit -m "feat(607): stage_builder.build_stage_env with override + pool guard"
```

---

### Task 7: `ml/env.py` — `reset(requested_ids=None)` override

**Files:**
- Modify: `ml/env.py:135-139` (the `reset` method)
- Test: `tests/ml/test_env.py`

- [ ] **Step 1: Write the failing test** (append to `tests/ml/test_env.py`)

```python
# add near the other imports at the top of tests/ml/test_env.py:
#   import pytest
#   from hangarfit.loader import load_fleet
#   from ml.env import HangarFitEnv
#   from ml.types import DifficultyConfig
#   from tests.ml.conftest import empty_hangar
# (match the file's existing import style; empty_hangar is a plain helper in conftest.py)


def _two_object_env():
    fleet = load_fleet("data/fleet.yaml")
    return HangarFitEnv(
        hangar=empty_hangar(),
        fleet=fleet,
        requested_ids=("fuji", "aviat_husky"),
        difficulty=DifficultyConfig(max_objects=2, per_object_step_budget=40, total_step_budget=80),
    )


def test_reset_none_is_equivalent_to_passing_the_same_ids():
    env = _two_object_env()
    obs_default = env.reset()
    obs_explicit = env.reset(requested_ids=("fuji", "aviat_husky"))
    assert obs_default == obs_explicit  # Observation is a frozen dataclass


def test_reset_override_changes_the_requested_set():
    env = _two_object_env()
    env.reset(requested_ids=("aviat_husky",))
    assert env.requested_ids == ("aviat_husky",)


def test_reset_rejects_unknown_id():
    env = _two_object_env()
    with pytest.raises(ValueError):
        env.reset(requested_ids=("nope",))


def test_reset_rejects_empty_requested_ids():
    env = _two_object_env()
    with pytest.raises(ValueError):
        env.reset(requested_ids=())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_env.py -q -k reset`
Expected: FAIL — `reset()` got an unexpected keyword argument `requested_ids` (and the validation tests fail).

- [ ] **Step 3: Write minimal implementation**

Replace `ml/env.py:135-139`:

```python
    def reset(self, requested_ids: tuple[str, ...] | None = None) -> Observation:
        if requested_ids is not None:
            if not requested_ids:
                raise ValueError("reset: requested_ids must be non-empty")
            known = set(self.fleet) | set(self.ground_objects)
            unknown = [i for i in requested_ids if i not in known]
            if unknown:
                raise ValueError(
                    f"reset: unknown requested ids {unknown} (known: {sorted(known)})"
                )
            self.requested_ids = requested_ids
        self._reset_state()
        self._spawn()
        self._prev_potential = self._potential()
        return self._observe()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ml/test_env.py -q`
Expected: PASS (existing env tests + 4 new).

- [ ] **Step 5: Commit**

```bash
git add ml/env.py tests/ml/test_env.py
git commit -m "feat(607): HangarFitEnv.reset(requested_ids) override (default byte-identical)"
```

---

### Task 8: `ml/train.py` — extend `collect_rollout`, adapt `train`

**Files:**
- Modify: `ml/train.py:53-94` (`collect_rollout`), `ml/train.py:97-131` (`train`), import block
- Test: `tests/ml/test_train_curriculum.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ml/test_train_curriculum.py
"""Torch-gated tests for the curriculum training loop + collect_rollout extension."""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")  # noqa: F841

from ml.curriculum import EpisodeStat, sample_request, stage_rng  # noqa: E402
from ml.encoding import EncoderConfig  # noqa: E402
from ml.policy import HangarFitPolicy  # noqa: E402
from ml.stage_builder import build_stage_env, effective_fleet_ids  # noqa: E402
from ml.train import collect_rollout, train  # noqa: E402
from ml.curriculum import DEFAULT_LADDER  # noqa: E402


def test_collect_rollout_returns_episode_stats_with_resampling():
    stage = DEFAULT_LADDER[1]  # pair-box, 2 objects
    env = build_stage_env(stage)
    policy = HangarFitPolicy()
    pool = effective_fleet_ids(stage)
    rng = stage_rng(0, 1)
    buf, stats = collect_rollout(
        env, policy, EncoderConfig(), 64, sample_request=lambda: sample_request(pool, 2, rng)
    )
    assert stats, "at least one episode should complete in 64 steps"
    assert all(isinstance(s, EpisodeStat) for s in stats)
    for s in stats:
        assert 0.0 <= s.fraction_placed <= 1.0


def test_trivial_train_still_runs_with_new_return_type():
    history = train(seed=0, iterations=1, rollout_len=32)
    assert len(history) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_train_curriculum.py -q`
Expected: FAIL — `collect_rollout()` got an unexpected keyword `sample_request` (and stats are floats, not `EpisodeStat`).

- [ ] **Step 3: Write minimal implementation**

In `ml/train.py`, add to the import block:

```python
from collections import deque  # noqa: F401  (used by train_curriculum in Task 9)
from ml.curriculum import (
    CurriculumHistory,
    CurriculumSchedule,
    EpisodeStat,
    PromotionPolicy,
    sample_request,
    should_promote,
    stage_rng,
)
from ml.stage_builder import build_stage_env, effective_fleet_ids
```

Replace `collect_rollout` (`ml/train.py:53-94`):

```python
def collect_rollout(
    env: HangarFitEnv,
    policy: HangarFitPolicy,
    encoder: EncoderConfig,
    rollout_len: int,
    *,
    sample_request: Callable[[], tuple[str, ...]] | None = None,
) -> tuple[RolloutBuffer, list[EpisodeStat]]:
    """Drive the env single-stream for `rollout_len` steps; return the buffer and the
    per-completed-episode stats (competency + reward sum). On each episode boundary,
    `sample_request()` (when given) picks the next episode's object subset; None keeps
    the env's fixed requested set (the 4a trivial path)."""
    buf = RolloutBuffer()
    bodies = _bodies(env)
    obs = env.reset()
    ep_reward, ep_stats = 0.0, []
    with torch.no_grad():
        while len(buf) < rollout_len:
            obs_t = encode(obs, env.hangar, bodies, encoder)
            out = policy(to_batch([obs_t]))
            kind, mag = sample_action(out)
            logprob, _ = factored_logprob_entropy(out, kind, mag)
            tr = obs.active.body.effective_turn_radius_m()  # type: ignore[union-attr]
            primitive = decode(int(kind), int(mag), turn_radius_m=tr)
            nxt, reward, done, info = env.step(primitive)
            buf.add(
                obs_t,
                kind_idx=int(kind),
                mag_idx=int(mag),
                logprob=float(logprob),
                value=float(out.value),
                reward=float(reward),
                done=bool(done),
            )
            ep_reward += float(reward)
            if done:
                # info.total = len(requested_ids) >= 1, so the division is safe.
                ep_stats.append(
                    EpisodeStat(
                        fraction_placed=info.placed / info.total,
                        valid=info.valid,
                        total_reward=ep_reward,
                    )
                )
                ep_reward = 0.0
                obs = env.reset(requested_ids=sample_request() if sample_request else None)
            else:
                obs = nxt
        # bootstrap value for a non-done tail
        if not buf.done[-1]:
            tail = encode(obs, env.hangar, bodies, encoder)
            buf.last_value = float(policy(to_batch([tail])).value)
    return buf, ep_stats
```

Add `Callable` to the existing `typing`/`collections.abc` imports if absent: `from collections.abc import Callable`.

In `train` (`ml/train.py:97-131`), change the mean-reward derivation so it reads `EpisodeStat.total_reward`. Replace the body of the loop's stat handling:

```python
    for it in range(iterations):
        buf, ep_stats = collect_rollout(env, policy, enc, rollout_len)
        metrics = ppo_update(policy, optimizer, buf, cfg)
        # NaN (not 0.0) when no episode finished within the rollout, so a short rollout
        # is not mistaken for a genuine zero-reward iteration in the curve.
        mean_r = (
            sum(s.total_reward for s in ep_stats) / len(ep_stats) if ep_stats else float("nan")
        )
        history.append(mean_r)
        if log:
            if ep_stats:
                reward_str = f"mean_ep_reward={mean_r:+.3f}  n_eps={len(ep_stats)}"
            else:
                reward_str = "mean_ep_reward=N/A (0 episodes)"
            print(
                f"iter {it:4d}  {reward_str}  "
                f"loss={metrics['loss']:+.3f}  entropy={metrics['entropy']:.3f}"
            )
    return history
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ml/test_train_curriculum.py -q && pytest tests/ml/test_ppo.py -q`
Expected: PASS (2 new + the ppo suite still green).

- [ ] **Step 5: Commit**

```bash
git add ml/train.py tests/ml/test_train_curriculum.py
git commit -m "feat(607): collect_rollout returns EpisodeStats + per-episode resampling"
```

---

### Task 9: `ml/train.py` — `train_curriculum` + `--schedule` CLI

**Files:**
- Modify: `ml/train.py` (add `train_curriculum`, `build_argparser`; rewrite `main`)
- Test: `tests/ml/test_train_curriculum.py`

- [ ] **Step 1: Write the failing test** (append)

```python
from dataclasses import replace  # add to imports

from ml.curriculum import CurriculumSchedule, DifficultyConfig, PromotionPolicy, Stage  # noqa: E402
from ml.train import build_argparser, train_curriculum  # noqa: E402


def _tiny_schedule(threshold: float):
    def stage(name, ids):
        return Stage(
            name=name,
            difficulty=DifficultyConfig(max_objects=1, per_object_step_budget=12, total_step_budget=12),
            hangar_path="data/hangar.yaml",
            fleet_path="data/fleet.yaml",
            fleet_ids=ids,
            clearance_m=0.05,
        )

    pol = PromotionPolicy(metric="fraction_placed", window=1, threshold=threshold, max_iters=2)
    return CurriculumSchedule(stages=(stage("t0", ("fuji",)), stage("t1", ("aviat_husky",))), policy=pol)


def test_train_curriculum_is_deterministic():
    sched = _tiny_schedule(threshold=-1.0)  # fraction_placed >= -1 always -> promote by competency
    h1 = train_curriculum(seed=0, schedule=sched, rollout_len=16)
    h2 = train_curriculum(seed=0, schedule=sched, rollout_len=16)
    assert h1.promotions == h2.promotions
    assert h1.iterations == h2.iterations


def test_train_curriculum_promotes_by_competency_then_advances():
    sched = _tiny_schedule(threshold=-1.0)
    h = train_curriculum(seed=0, schedule=sched, rollout_len=16)
    assert [p[0] for p in h.promotions] == ["t0", "t1"]
    assert all(p[2] == "competency" for p in h.promotions)


def test_train_curriculum_promotes_by_cap_when_unreachable():
    sched = _tiny_schedule(threshold=2.0)  # fraction_placed <= 1 < 2 -> never competency
    h = train_curriculum(seed=0, schedule=sched, rollout_len=16)
    assert all(p[2] == "cap" for p in h.promotions)


def test_argparser_schedule_defaults_to_curriculum():
    parser = build_argparser()
    assert parser.parse_args([]).schedule == "curriculum"
    assert parser.parse_args(["--schedule", "trivial"]).schedule == "trivial"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_train_curriculum.py -q -k "curriculum or argparser"`
Expected: FAIL — ImportError for `train_curriculum` / `build_argparser`.

- [ ] **Step 3: Write minimal implementation** (append to `ml/train.py`, and rewrite `main`)

```python
def train_curriculum(
    *,
    seed: int = 0,
    schedule: CurriculumSchedule | None = None,
    rollout_len: int = 512,
    ppo: PPOConfig | None = None,
    policy_kwargs: dict | None = None,
    encoder: EncoderConfig | None = None,
    log: bool = False,
) -> CurriculumHistory:
    """Climb the ladder: one policy/optimizer across rungs (transfer); per rung, run
    PPO until the competency gate fires or the per-stage cap is hit, then advance."""
    torch.manual_seed(seed)
    cfg = ppo or PPOConfig()
    enc = encoder or EncoderConfig()
    sched = schedule or CurriculumSchedule.default()
    pol = sched.policy
    policy = HangarFitPolicy(**(policy_kwargs or {}))
    optimizer = torch.optim.Adam(policy.parameters(), lr=cfg.lr)
    history = CurriculumHistory()
    for stage_index, stage in enumerate(sched.stages):
        env = build_stage_env(stage)
        pool = effective_fleet_ids(stage)
        n = stage.difficulty.max_objects if stage.difficulty.max_objects is not None else len(pool)
        rng = stage_rng(seed, stage_index)
        window: deque[EpisodeStat] = deque(maxlen=pol.window)
        for it in range(pol.max_iters):
            buf, ep_stats = collect_rollout(
                env, policy, enc, rollout_len, sample_request=lambda: sample_request(pool, n, rng)
            )
            ppo_update(policy, optimizer, buf, cfg)
            window.extend(ep_stats)
            history.record(stage.name, it, ep_stats)
            if log:
                mean_r = (
                    sum(s.total_reward for s in ep_stats) / len(ep_stats)
                    if ep_stats
                    else float("nan")
                )
                print(f"[{stage.name}] iter {it:4d}  mean_ep_reward={mean_r:+.3f}  n_eps={len(ep_stats)}")
            if should_promote(list(window), pol):
                history.note_promotion(stage.name, it, by="competency")
                break
        else:
            history.note_promotion(stage.name, pol.max_iters - 1, by="cap")
        if log:
            by = history.promotions[-1][2]
            print(f"[{stage.name}] promoted by {by}")
    return history


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Train the cold-joint policy (trivial stage or curriculum).")
    p.add_argument("--schedule", choices=["trivial", "curriculum"], default="curriculum")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--iterations", type=int, default=200, help="trivial: PPO iters")
    p.add_argument(
        "--max-iters-per-stage",
        type=int,
        default=None,
        help="curriculum: per-rung safety cap (default = schedule policy)",
    )
    p.add_argument("--rollout-len", type=int, default=1024)
    p.add_argument("--lr", type=float, default=3e-4)
    return p
```

Rewrite `main` (`ml/train.py:134-147`):

```python
def main() -> None:
    args = build_argparser().parse_args()
    if args.schedule == "trivial":
        train(
            seed=args.seed,
            iterations=args.iterations,
            rollout_len=args.rollout_len,
            ppo=PPOConfig(lr=args.lr),
            log=True,
        )
    else:
        sched = CurriculumSchedule.default()
        if args.max_iters_per_stage is not None:
            from dataclasses import replace

            sched = replace(sched, policy=replace(sched.policy, max_iters=args.max_iters_per_stage))
        train_curriculum(
            seed=args.seed,
            schedule=sched,
            rollout_len=args.rollout_len,
            ppo=PPOConfig(lr=args.lr),
            log=True,
        )
```

> The lambda `lambda: sample_request(pool, n, rng)` closes over `pool`/`n`/`rng`, which are reassigned each stage. This is safe because `collect_rollout` calls the lambda synchronously within the same stage iteration — the closure is never deferred across iterations.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ml/test_train_curriculum.py -q`
Expected: PASS (all curriculum + argparser tests).

- [ ] **Step 5: Commit**

```bash
git add ml/train.py tests/ml/test_train_curriculum.py
git commit -m "feat(607): train_curriculum ladder loop + --schedule CLI"
```

---

### Task 10: CHANGELOG + full verification

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the `[Unreleased]` entry**

Under the `[Unreleased]` heading's `### Added` (create the subsection if absent), add:

```markdown
- **Cold-joint RL curriculum schedule (#607 sub-project #4b).** `python -m ml.train
  --schedule curriculum` now climbs a competency-gated difficulty ladder (object
  count → hangar shape → clearance) instead of training a single fixed stage; the
  fixed trivial stage remains reachable via `--schedule trivial`. New pure
  `ml/curriculum.py` (Stage ladder, promotion gate, seeded object sampling) and
  torch-free `ml/stage_builder.py`; `HangarFitEnv.reset()` gains an optional
  `requested_ids` override (default unchanged).
```

- [ ] **Step 2: Run lint + format + types on the changed code**

Run:
```bash
ruff check ml/ tests/ml/
ruff format --check ml/ tests/ml/
mypy ml/curriculum.py ml/stage_builder.py ml/env.py ml/train.py
```
Expected: all clean. (If `ruff format --check` flags files, run `ruff format ml/ tests/ml/` and re-commit.)

- [ ] **Step 3: Run the full ml suite (pure tests run without torch; torch tests run if installed)**

Run: `pytest tests/ml/ -q`
Expected: PASS. Pure `test_curriculum.py` / `test_stage_builder.py` / `test_env.py` always run; `test_train_curriculum.py` runs when torch is installed, else SKIPs cleanly.

- [ ] **Step 4: Manual learn-validation (NOT a CI gate — report in the PR)**

Run (requires the `[train]` extra / local torch):
```bash
python -m ml.train --schedule curriculum --max-iters-per-stage 60 --rollout-len 1024
```
Expected: the log shows the agent climbing — per-rung `mean_ep_reward` rising and `promoted by competency` for at least the first ≥3 rungs (later rungs may promote `by cap`; that is acceptable for 4b and is logged, not silent). Capture the reward curve / promotion log for the PR body.

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(607): CHANGELOG for the curriculum schedule (sub-project #4b)"
```

---

## Self-review (run before execution)

**Spec coverage:**
- §4 module map → Tasks 1–9 create `curriculum.py` (1–4), `stage_builder.py` (5–6), env change (7), train wiring (8–9). ✓
- §5 Stage + ladder → Task 3 (Stage), Task 4 (DEFAULT_LADDER, 5 rungs, dimensions). ✓
- §5.1 invariants → Task 3 (`validate_ladder`: encoder cap, positive int), Task 6 (`max_objects ≤ pool`). ✓
- §6 promotion → Task 1 (`should_promote`), Task 9 (cap path via `for/else`). ✓
- §7.1 reset override → Task 7 (+ default byte-identical + validation). ✓
- §7.2 collect_rollout → Task 8 (EpisodeStat return, `total_reward`, resampling, `train` adaptation). ✓
- §7.3 train_curriculum → Task 9 (one policy across rungs, per-stage RNG, gate/cap). ✓
- §7.4 CLI → Task 9 (`--schedule` default curriculum, `trivial` preserved, `--max-iters-per-stage`). ✓
- §8 determinism → Task 9 (`test_train_curriculum_is_deterministic`). ✓
- §9 tests → pure (Tasks 1–6, 7), torch-gated canary (8–9). ✓
- §11 CHANGELOG + manual validation → Task 10. ✓

**Placeholder scan:** no TBD/TODO; every code step shows full code; every test shows assertions. ✓

**Type consistency:** `EpisodeStat(fraction_placed, valid, total_reward)` used identically in Tasks 1/8/9. `should_promote(window, policy)`, `sample_request(pool, n, rng)`, `stage_rng(seed, stage_index)`, `effective_fleet_ids(stage)`, `build_stage_env(stage)`, `train_curriculum(...) -> CurriculumHistory` consistent across tasks. `CurriculumHistory.record/note_promotion` signatures match Task 4 ↔ Task 9. ✓

---

## Execution Handoff

This plan implements **after spec PR #687 merges** (the impl branch must branch off `develop` so it carries the spec). The impl branch is `feature/607-rung6-curriculum-schedule-impl` (or reuse the spec branch if you prefer), with its own impl issue `Closes #<n>`. Two execution options follow (offered to the user separately).
