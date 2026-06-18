"""The cold-joint curriculum schedule (sub-project #4b, #607). PURE: no torch, no
disk IO. Owns the difficulty ladder, the competency-gated promotion rule, the
seeded per-episode object-set sampling, and the run history. The disk-touching
env builder lives in ml/stage_builder.py; the torch training loop in ml/train.py."""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
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

    # The default is the compound "valid_placed": credit fraction_placed only on episodes
    # whose final layout is collision-free, so a rung advances only when the agent places
    # the set AND validly. "fraction_placed" ignores validity (can promote on overlapping
    # parks); "valid_rate" ignores how much got placed. See should_promote.
    metric: Literal["fraction_placed", "valid_rate", "valid_placed"] = "valid_placed"
    window: int = 20  # most-recent completed episodes to average
    # advance when mean(metric over window) >= threshold. The metric is a rate in
    # [0, 1], so threshold > 1 means "never promote by competency" (always cap) and
    # threshold <= 0 means "promote as soon as the window fills" — both are valid levers.
    threshold: float = 0.9
    max_iters: int = 200  # safety cap: advance unconditionally after this many PPO iters

    def __post_init__(self) -> None:
        if self.window < 1:
            raise ValueError(f"PromotionPolicy.window must be >= 1, got {self.window}")
        if self.max_iters < 1:
            raise ValueError(f"PromotionPolicy.max_iters must be >= 1, got {self.max_iters}")


def should_promote(window: Sequence[EpisodeStat], policy: PromotionPolicy) -> bool:
    """Pure gate: True when the last ``policy.window`` episodes meet the threshold."""
    if len(window) < policy.window:
        return False
    recent = list(window)[-policy.window :]
    if policy.metric == "valid_rate":
        score = sum(1.0 for s in recent if s.valid) / len(recent)
    elif policy.metric == "valid_placed":
        # compound: credit fraction_placed only when the final layout is valid, so a rung
        # advances only when the agent places (most of) the set AND collision-free.
        score = sum(s.fraction_placed if s.valid else 0.0 for s in recent) / len(recent)
    else:  # fraction_placed
        score = sum(s.fraction_placed for s in recent) / len(recent)
    return score >= policy.threshold


def with_promotion_overrides(
    policy: PromotionPolicy,
    *,
    metric: Literal["fraction_placed", "valid_rate", "valid_placed"] | None = None,
    threshold: float | None = None,
    max_iters: int | None = None,
) -> PromotionPolicy:
    """Return ``policy`` with each non-None field overridden — the CLI plumbing for the
    #710 promotion-gate study (``--promotion-metric`` / ``--promotion-threshold`` /
    ``--max-iters-per-stage``). All-None returns an equal policy, so the CLI stays
    default-neutral when no override flag is passed."""
    if metric is not None:
        policy = replace(policy, metric=metric)
    if threshold is not None:
        policy = replace(policy, threshold=threshold)
    if max_iters is not None:
        policy = replace(policy, max_iters=max_iters)
    return policy


def episode_metrics(ep_stats: Sequence[EpisodeStat]) -> dict[str, float | int | None]:
    """Per-iteration scalar metrics over one PPO iteration's COMPLETED episodes. The three
    rates mirror ``should_promote`` exactly: ``valid_placed`` credits ``fraction_placed``
    only on episodes whose final layout is valid (the #710 compound mastery axis),
    ``valid_rate`` is the validity fraction, ``fraction_placed`` ignores validity. An empty
    iteration (no episode finished within the rollout) reports ``n_eps=0`` with ``None``
    rates — NOT 0.0 — so a short rollout is never mistaken for a genuine zero in the curve
    (mirrors train.py's NaN-when-empty ``mean_ep_reward`` convention)."""
    n = len(ep_stats)
    if n == 0:
        return {
            "n_eps": 0,
            "mean_ep_reward": None,
            "fraction_placed": None,
            "valid_rate": None,
            "valid_placed": None,
        }
    return {
        "n_eps": n,
        "mean_ep_reward": sum(s.total_reward for s in ep_stats) / n,
        "fraction_placed": sum(s.fraction_placed for s in ep_stats) / n,
        "valid_rate": sum(1.0 for s in ep_stats if s.valid) / n,
        "valid_placed": sum(s.fraction_placed if s.valid else 0.0 for s in ep_stats) / n,
    }


def format_iter_log(stage_name: str, it: int, ep_stats: Sequence[EpisodeStat]) -> str:
    """One-line per-iteration progress log carrying the full ``episode_metrics`` — in
    particular ``valid_placed``, the #710 mastery axis — so a ``python -u`` curriculum run
    is monitorable mid-flight (the CLI previously logged only ``mean_ep_reward``). An empty
    iteration reports ``n_eps=0`` with no rates, so a no-episode rollout is not misread as a
    genuine zero."""
    m = episode_metrics(ep_stats)
    if m["n_eps"] == 0:
        return f"[{stage_name}] iter {it:4d}  n_eps=0 (no episode completed)"
    return (
        f"[{stage_name}] iter {it:4d}  "
        f"mean_ep_reward={m['mean_ep_reward']:+.3f}  "
        f"valid_placed={m['valid_placed']:.3f}  "
        f"valid_rate={m['valid_rate']:.3f}  "
        f"fraction_placed={m['fraction_placed']:.3f}  "
        f"n_eps={m['n_eps']}"
    )


def history_metric_records(history: CurriculumHistory) -> list[dict[str, object]]:
    """One JSONL-ready record per recorded training iteration: ``stage``, ``iter`` index,
    and the ``episode_metrics`` over that iteration's completed episodes. Pure over the
    history — the #710 per-rung ``valid_placed`` learning curve the CLI dumps via
    ``--metrics-out``."""
    return [
        {"stage": stage, "iter": it, **episode_metrics(ep_stats)}
        for stage, it, ep_stats in history.iterations
    ]


_STAGE_RNG_STRIDE = 100003  # a prime, so (seed, stage_index) pairs don't collide
_WORKER_RNG_STRIDE = 1000003  # a prime distinct from _STAGE_RNG_STRIDE (per-worker offset)


def stage_rng(seed: int, stage_index: int, worker_index: int = 0) -> random.Random:
    """A per-stage (and, for vectorized training, per-worker) RNG isolated from torch's
    global stream. ``worker_index=0`` reproduces the legacy single-stream value exactly
    (the +0 term), so the n_envs=1 path is byte-identical; worker_index>0 derives a
    distinct, collision-free stream."""
    return random.Random(seed * _STAGE_RNG_STRIDE + stage_index + worker_index * _WORKER_RNG_STRIDE)


def sample_request(pool: Sequence[str], n: int, rng: random.Random) -> tuple[str, ...]:
    """Draw a size-``n`` subset (in selection order) from an explicit id ``pool``.
    Pure over ``pool`` — no disk. ``rng.sample`` raises ValueError if ``n`` exceeds
    the pool, which is the loud failure we want."""
    return tuple(rng.sample(list(pool), n))


@dataclass(frozen=True, slots=True)
class Stage:
    """One rung of the ladder. Holds fleet/hangar (and, for a #712 seed-anchor rung, the
    witness layout) as repo-relative PATH STRINGS + scalar overrides — no loading happens
    here (that is stage_builder.py's job), so this module stays disk-free."""

    name: str
    difficulty: DifficultyConfig
    hangar_path: str
    fleet_path: str
    # explicit sampling pool; None = whole fleet (resolved in stage_builder)
    fleet_ids: tuple[str, ...] | None = None
    clearance_m: float | None = None  # override on the loaded hangar; None = file value
    wing_layer_clearance_m: float | None = None  # override; None = file value
    apron_depth_m: float = 8.0  # override; gives the env a spawn region (matches 4a)
    # #712 seed-anchor rung: repo-relative path to a committed witness layout. When set, the
    # rung's sampling pool IS the witness's objects (Q2) and the env pre-parks a
    # ``difficulty.seed_anchor_k``-prefix of them at their witness poses. None (default) =>
    # no anchoring (an empty-start rung) => byte-identical to the pre-#712 ladder.
    anchor_layout_path: str | None = None

    def __post_init__(self) -> None:
        # Disk-free scalar invariants (the disk-needing max_objects <= len(pool) check
        # stays in stage_builder.build_stage_env). Negative clearances/apron are illegal.
        for name in ("clearance_m", "wing_layer_clearance_m"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"Stage {self.name!r}: {name} must be >= 0, got {value}")
        if self.apron_depth_m < 0:
            raise ValueError(
                f"Stage {self.name!r}: apron_depth_m must be >= 0, got {self.apron_depth_m}"
            )


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


@dataclass(slots=True)
class CurriculumHistory:
    """Mutable, append-only record of training events — pure data, no torch, so CI
    can assert equality over it for the determinism canary. Mutate only via
    ``record`` / ``note_promotion`` so the append-only + canary-equality contract
    holds (the lists are public for read/equality, not for outside mutation)."""

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
# Committed seed-anchor witness for the box rungs (#712). A valid 2-object box layout; the
# pair-anchored rung pre-parks a k-prefix of it. A dev/CI-only fixture (all of ml/ is
# dev/CI-only, never shipped), validated by tests/ml/test_stage_builder.py::test_witness_box_*.
_WITNESS_BOX = "tests/fixtures/ml/witness_box.yaml"
_NOTCH_HANGAR = "examples/herrenteich/hangar.yaml"
_NOTCH_FLEET = "examples/herrenteich/fleet.yaml"
_LENIENT_CLEARANCE = (
    0.05  # below the herrenteich file value (0.10) so the clearance ramp truly tightens
)

# Opt-in #714 rung (wired via with_solo_box_rung / --solo-box-rung), NOT part of DEFAULT_LADDER.
# max_objects=1 like trivial, but the WHOLE-fleet pool (fleet_ids omitted) instead of trivial's
# single ("fuji",) — decouples the count jump (1->2) from the sampling-pool jump at the
# trivial->pair-box boundary so single-object competency transfers to arbitrary fleet objects.
_SOLO_BOX_STAGE = Stage(
    name="solo-box",
    difficulty=DifficultyConfig(max_objects=1, per_object_step_budget=60, total_step_budget=60),
    hangar_path=_BOX_HANGAR,
    fleet_path=_BOX_FLEET,
    clearance_m=_LENIENT_CLEARANCE,
)

# Opt-in #712 rung (wired via with_pair_anchored_rung / --seed-anchor), NOT in DEFAULT_LADDER.
# Two objects, but ONE is pre-parked at a committed-witness pose (seed_anchor_k=1) and the agent
# only drives the other in — scaffolding 2-object joint discovery with a valid 1-object start
# before the empty-start pair-box (k=0). The pool IS the witness's objects (anchor_layout_path
# set => stage_builder pins it), so the per-episode seeded permutation makes k=1 a seeded-random
# single-object anchor. Only one object is driven, so the budget matches solo-box's 60/60.
_PAIR_ANCHORED_STAGE = Stage(
    name="pair-anchored",
    difficulty=DifficultyConfig(
        max_objects=2, seed_anchor_k=1, per_object_step_budget=60, total_step_budget=60
    ),
    hangar_path=_BOX_HANGAR,
    fleet_path=_BOX_FLEET,
    anchor_layout_path=_WITNESS_BOX,
    clearance_m=_LENIENT_CLEARANCE,
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


def with_solo_box_rung(schedule: CurriculumSchedule) -> CurriculumSchedule:
    """Return ``schedule`` with the opt-in ``solo-box`` rung inserted immediately after the
    ``trivial`` rung — the #714 ``--solo-box-rung`` lever. solo-box keeps max_objects=1 but
    draws from the whole fleet, decoupling the count jump (1->2) from the sampling-pool jump
    (single-fuji -> whole-fleet) at the trivial->pair-box boundary so single-object competency
    transfers. Only the ladder changes (the promotion policy is preserved). The default ladder
    is left untouched, so default runs stay byte-identical (this is opt-in)."""
    stages = schedule.stages
    try:
        after = next(i for i, s in enumerate(stages) if s.name == "trivial") + 1
    except StopIteration:
        raise ValueError(
            "with_solo_box_rung: schedule has no 'trivial' rung to insert after"
        ) from None
    return replace(schedule, stages=stages[:after] + (_SOLO_BOX_STAGE,) + stages[after:])


def with_pair_anchored_rung(schedule: CurriculumSchedule) -> CurriculumSchedule:
    """Return ``schedule`` with the opt-in ``pair-anchored`` rung inserted immediately BEFORE
    the ``pair-box`` rung — the #712 ``--seed-anchor`` lever. pair-anchored pre-parks 1 of its
    2 objects at a committed-witness pose (k=1) and the agent drives the other in, so 2-object
    joint discovery is scaffolded by a valid 1-object start before the empty-start pair-box
    (k=0). Only the ladder changes (the promotion policy is preserved). The default ladder is
    left untouched, so default runs stay byte-identical (this is opt-in)."""
    stages = schedule.stages
    try:
        before = next(i for i, s in enumerate(stages) if s.name == "pair-box")
    except StopIteration:
        raise ValueError(
            "with_pair_anchored_rung: schedule has no 'pair-box' rung to insert before"
        ) from None
    return replace(schedule, stages=stages[:before] + (_PAIR_ANCHORED_STAGE,) + stages[before:])
