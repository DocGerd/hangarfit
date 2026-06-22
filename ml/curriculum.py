"""The cold-joint curriculum schedule (sub-project #4b, #607). PURE: no torch, no
disk IO. Owns the difficulty ladder, the competency-gated promotion rule, the
seeded per-episode object-set sampling, and the run history. The disk-touching
env builder lives in ml/stage_builder.py; the torch training loop in ml/train.py."""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from functools import partial
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
    # most-recent ITERATIONS to average (each one rollout's honest full-rollout metric mean).
    # NOT episodes — the #742 fix; see should_promote. A small window suffices because the
    # per-iteration mean is already low-variance (it averages a whole rollout's episodes).
    window: int = 3
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


def window_score(window: Sequence[EpisodeStat], metric: str) -> float:
    """Mean of the promotion ``metric`` over ``window`` (0.0 if empty). The single source of
    the competency score: ``should_promote`` thresholds against it and the auto-budget
    controller (#734) fits its slope over it, so the gate and the budget watch the SAME
    trajectory. ``valid_placed`` (the default) credits ``fraction_placed`` only when the
    final layout is valid; ``valid_rate`` is the validity fraction; ``fraction_placed``
    ignores validity."""
    if not window:
        return 0.0
    if metric == "valid_rate":
        return sum(1.0 for s in window if s.valid) / len(window)
    if metric == "valid_placed":
        return sum(s.fraction_placed if s.valid else 0.0 for s in window) / len(window)
    return sum(s.fraction_placed for s in window) / len(window)  # fraction_placed


def should_promote(history: Sequence[float], policy: PromotionPolicy) -> bool:
    """Pure competency gate over the per-ITERATION honest-metric series. True when the mean of
    the last ``policy.window`` iteration scores meets ``policy.threshold``.

    Each element of ``history`` is one PPO iteration's full-rollout metric mean —
    ``window_score(ep_stats, policy.metric)`` over the WHOLE rollout, the same honest
    ``valid_placed`` signal the ``--metrics-out`` JSONL and ``ml.gate`` report. This is the
    #742 fix: the gate previously thresholded the last ``policy.window`` *episodes* of a
    ``deque(maxlen=window)`` — i.e. only the tail of the latest rollout (~20 of ~250 episodes),
    a far noisier estimator that false-positive-promoted on a lucky autocorrelated streak.
    Reading the per-iteration mean over a window of iterations removes that bias. ``policy.metric``
    is applied upstream when the series is built, so this gate consults only window + threshold."""
    if len(history) < policy.window:
        return False
    recent = list(history)[-policy.window :]
    return sum(recent) / len(recent) >= policy.threshold


def theil_sen_slope(values: Sequence[float]) -> float:
    """Robust trend slope: the median of all pairwise slopes over (index, value). Returns
    0.0 for fewer than two points. Resists a single outlier iteration that an OLS fit would
    chase — the windowed-mean metric series is noisy, and a spike must not read as 'climbing'."""
    n = len(values)
    if n < 2:
        return 0.0
    slopes = sorted((values[j] - values[i]) / (j - i) for i in range(n) for j in range(i + 1, n))
    m = len(slopes)
    mid = m // 2
    return slopes[mid] if m % 2 else 0.5 * (slopes[mid - 1] + slopes[mid])


@dataclass(frozen=True, slots=True)
class BudgetController:
    """Slope-aware early-stop for a curriculum rung (#734). PURE — a function of the
    per-iteration windowed-metric series + config (mirrors ``should_promote``'s purity).

    ``should_stop`` is True once the rung has PLATEAUED: the trend over each of the last
    ``plateau_patience`` trailing ``slope_window``-sized windows is non-positive
    (slope <= ``eps``) AND the recent level has cleared ``min_level`` (the #743 floor-guard —
    a curve flat at the FLOOR is a warmup, not a converged plateau, and slope alone cannot tell
    the two apart since both are ~0). The caller separately enforces the competency gate
    (``should_promote``) and the hard ceiling (the loop bound ``max_iters``). Wired into
    train.py behind ``--auto-budget`` (default off), so the fixed-``max_iters`` path stays
    byte-identical (4c-ii default-neutrality)."""

    min_iters: int = 30  # no stop decision before this many iterations
    slope_window: int = 15  # trailing points each slope is fit over
    plateau_patience: int = 4  # consecutive non-positive-slope windows => plateau
    max_iters: int = 1000  # hard ceiling (the loop bound when auto-budget is on)
    eps: float = 1e-4  # slope <= eps counts as flat/non-positive
    # #743 floor-guard: the recent slope_window's mean must clear this before any plateau-stop —
    # distinguishes flat-at-floor (warmup, not started) from flat-at-ceiling (converged below the
    # competency threshold). 0.0 disables the guard (pure slope-only stopping). The default is a
    # small valid_placed level: below it the rung has effectively not begun to learn.
    min_level: float = 0.05

    def __post_init__(self) -> None:
        if self.min_iters < 1:
            raise ValueError(f"BudgetController.min_iters must be >= 1, got {self.min_iters}")
        if self.slope_window < 2:
            raise ValueError(f"BudgetController.slope_window must be >= 2, got {self.slope_window}")
        if self.plateau_patience < 1:
            raise ValueError(
                f"BudgetController.plateau_patience must be >= 1, got {self.plateau_patience}"
            )
        if self.max_iters < 1:
            raise ValueError(f"BudgetController.max_iters must be >= 1, got {self.max_iters}")
        if self.min_level < 0:
            raise ValueError(f"BudgetController.min_level must be >= 0, got {self.min_level}")

    def should_stop(self, history: Sequence[float]) -> bool:
        """True iff the rung has plateaued: every one of the last ``plateau_patience``
        trailing ``slope_window``-sized windows has slope <= ``eps`` AND the most recent
        ``slope_window``'s mean is >= ``min_level`` (the floor-guard). False until there are
        enough points (>= ``min_iters``, and enough to form the trailing windows)."""
        n = len(history)
        if n < self.min_iters:
            return False
        if n < self.slope_window + self.plateau_patience - 1:
            return False
        # Floor-guard: a flat curve still near the floor is a warmup, not a converged plateau.
        recent = history[-self.slope_window :]
        if sum(recent) / len(recent) < self.min_level:
            return False
        for k in range(self.plateau_patience):
            end = n - k
            window = history[end - self.slope_window : end]
            if theil_sen_slope(window) > self.eps:
                return False
        return True


def with_promotion_overrides(
    policy: PromotionPolicy,
    *,
    metric: Literal["fraction_placed", "valid_rate", "valid_placed"] | None = None,
    threshold: float | None = None,
    max_iters: int | None = None,
    window: int | None = None,
) -> PromotionPolicy:
    """Return ``policy`` with each non-None field overridden — the CLI plumbing for the
    #710 promotion-gate study (``--promotion-metric`` / ``--promotion-threshold`` /
    ``--max-iters-per-stage``) and the #742 ``--promotion-window`` (recent ITERATIONS to
    average). All-None returns an equal policy, so the CLI stays default-neutral when no
    override flag is passed."""
    if metric is not None:
        policy = replace(policy, metric=metric)
    if threshold is not None:
        policy = replace(policy, threshold=threshold)
    if max_iters is not None:
        policy = replace(policy, max_iters=max_iters)
    if window is not None:
        policy = replace(policy, window=window)
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
            sample_mixed_start,
            pool,
            n,
            rng,
            seed_anchor_k=stage.difficulty.seed_anchor_k,
            anchor_prob=ap,
        )
    return partial(plain_start, pool, n, rng)


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
        # #712 seed-anchor: catch a misconfigured anchored rung here (the pre-flight gate)
        # rather than lazily at the first env reset, deep inside the training loop.
        k = s.difficulty.seed_anchor_k
        if k < 0:
            raise ValueError(f"stage {s.name!r}: seed_anchor_k must be >= 0, got {k}")
        if k >= n:
            raise ValueError(
                f"stage {s.name!r}: seed_anchor_k {k} must be < max_objects {n} "
                f"(at least one object must be left to drive in)"
            )
        ap = s.difficulty.anchor_prob
        if ap is not None:
            if not (0.0 <= ap <= 1.0):
                raise ValueError(f"stage {s.name!r}: anchor_prob must be in [0, 1], got {ap}")
            if s.anchor_layout_path is None:
                raise ValueError(
                    f"stage {s.name!r}: a mixed-start rung (anchor_prob set) needs an "
                    f"anchor_layout_path (the witness the per-episode k draws from)"
                )
            if n < 2:
                raise ValueError(
                    f"stage {s.name!r}: a mixed-start rung (anchor_prob set) needs "
                    f"max_objects >= 2 (room for both a k=1 and a k=0 draw), got {n}"
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


def truncate_after_rung(schedule: CurriculumSchedule, rung_name: str) -> CurriculumSchedule:
    """Return ``schedule`` with every rung AFTER ``rung_name`` dropped (the named rung is kept
    as the last stage) — the #722 ``--stop-after-rung`` lever. A pure ``stages[:idx+1]``
    truncation that mirrors the ``with_*_rung`` grafts: only the ladder changes (the promotion
    policy is preserved), so a sweep can train just up to (and including) a chosen rung instead
    of grinding on through the rest of the ladder. Truncating at the LAST rung is a no-op on the
    stages, so a run that names the final rung stays byte-identical (in training output) to no
    truncation. Raises
    ValueError (loud, not a leaked StopIteration) when ``rung_name`` is not in the schedule, so a
    typo'd rung fails before the run rather than silently disabling the cap. Apply AFTER the
    ``with_*_rung`` grafts so a name they introduce (e.g. ``pair-mixed``) is in scope."""
    stages = schedule.stages
    try:
        idx = next(i for i, s in enumerate(stages) if s.name == rung_name)
    except StopIteration:
        raise ValueError(
            f"truncate_after_rung: schedule has no rung named {rung_name!r} "
            f"(have {[s.name for s in stages]})"
        ) from None
    return replace(schedule, stages=stages[: idx + 1])
