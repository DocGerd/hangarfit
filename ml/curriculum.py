"""The cold-joint curriculum schedule (sub-project #4b, #607). PURE: no torch, no
disk IO. Owns the difficulty ladder, the competency-gated promotion rule, the
seeded per-episode object-set sampling, and the run history. The disk-touching
env builder lives in ml/stage_builder.py; the torch training loop in ml/train.py."""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal


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
