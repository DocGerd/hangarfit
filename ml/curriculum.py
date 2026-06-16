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
    # explicit sampling pool; None = whole fleet (resolved in stage_builder)
    fleet_ids: tuple[str, ...] | None = None
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
_LENIENT_CLEARANCE = (
    0.05  # below the herrenteich file value (0.10) so the clearance ramp truly tightens
)

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
        difficulty=DifficultyConfig(
            max_objects=2, per_object_step_budget=60, total_step_budget=140
        ),
        hangar_path=_BOX_HANGAR,
        fleet_path=_BOX_FLEET,
        clearance_m=_LENIENT_CLEARANCE,
    ),
    Stage(
        name="trio-box",
        difficulty=DifficultyConfig(
            max_objects=3, per_object_step_budget=60, total_step_budget=220
        ),
        hangar_path=_BOX_HANGAR,
        fleet_path=_BOX_FLEET,
        clearance_m=_LENIENT_CLEARANCE,
    ),
    Stage(
        name="trio-notch",
        difficulty=DifficultyConfig(
            max_objects=3, per_object_step_budget=80, total_step_budget=260
        ),
        hangar_path=_NOTCH_HANGAR,
        fleet_path=_NOTCH_FLEET,
        clearance_m=_LENIENT_CLEARANCE,
    ),
    Stage(
        name="trio-notch-strict",
        difficulty=DifficultyConfig(
            max_objects=3, per_object_step_budget=80, total_step_budget=260
        ),
        hangar_path=_NOTCH_HANGAR,
        fleet_path=_NOTCH_FLEET,
        clearance_m=None,  # inherit the herrenteich file value (0.10) — the real strict rung
    ),
)
