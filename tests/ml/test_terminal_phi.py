"""#732: PBRS must force Φ(terminal) = 0 so the undiscounted return carries no spurious
−Φ(terminal) bias (Ng–Harada–Russell policy-invariance).

`StepInfo.terms["shaping"]` is the undiscounted `ctx.potential − ctx.prev_potential`
the env feeds the reward. With the fix, the terminal step sets `ctx.potential = 0`, so
that term must equal `−Φ(prev)` on *both* terminal paths — and it must be non-vacuous:
Φ(prev) is genuinely nonzero on the non-clean terminals (budget exhaustion / invalid
completion) that the curriculum cares about distinguishing.
"""

from __future__ import annotations

import pytest

from ml.env import HangarFitEnv
from ml.types import DifficultyConfig, Park, Primitive
from tests.ml.conftest import _fuji, empty_hangar


def test_terminal_phi_zeroed_on_budget_exhaustion():
    """A movement that exhausts the per-object budget terminates with an object still
    pending (Φ(prev) ≠ 0); the terminal shaping must be exactly −Φ(prev)."""
    diff = DifficultyConfig(max_objects=1, per_object_step_budget=1, total_step_budget=10)
    env = HangarFitEnv(
        hangar=empty_hangar(), fleet=_fuji(), requested_ids=("fuji",), difficulty=diff
    )
    env.reset()
    phi_prev = env._prev_potential
    assert abs(phi_prev) > 1e-9  # active object pending → Φ ≠ 0 (non-vacuous)

    _obs, _r, done, info = env.step(Primitive(kind="S", magnitude=1.0, gear=1))

    assert done and "budget" in info.reason
    assert info.terms["shaping"] == pytest.approx(-phi_prev)


def test_terminal_phi_zeroed_on_invalid_completion():
    """Parking the last object onto the first (a piled, invalid completion) leaves
    residual overlap → without the fix Φ(terminal) ≠ 0. The terminal shaping must still
    be exactly −Φ(prev)."""
    diff = DifficultyConfig(max_objects=2, per_object_step_budget=20, total_step_budget=40)
    env = HangarFitEnv(
        hangar=empty_hangar(),
        fleet=_fuji(),
        requested_ids=("fuji", "aviat_husky"),
        difficulty=diff,
    )
    env.reset()
    env.step(Park())  # park object 1 at the door spawn → not done, spawn object 2
    phi_prev = env._prev_potential
    assert abs(phi_prev) > 1e-9  # object 2 still active → Φ ≠ 0

    _obs, _r, done, info = env.step(Park())  # park object 2 onto object 1 → done, invalid

    assert done and not info.valid  # piled completion is invalid
    assert info.terms["shaping"] == pytest.approx(-phi_prev)


def test_clean_valid_completion_phi_zero_is_a_noop():
    """On a genuinely VALID in-hangar completion Φ(terminal) = 0 *naturally* (no active
    object, nothing unplaced, no overlap), so forcing it to 0 is a true no-op: the terminal
    shaping is −Φ(prev) exactly as it would be without the fix. Contrast the two non-clean
    terminals above, where Φ(terminal) was nonzero pre-fix."""
    diff = DifficultyConfig(max_objects=1, per_object_step_budget=40, total_step_budget=80)
    env = HangarFitEnv(
        hangar=empty_hangar(), fleet=_fuji(), requested_ids=("fuji",), difficulty=diff
    )
    env.reset()
    for _ in range(10):  # drive the object in past the door (y >= 0) so the parked layout is valid
        env.step(Primitive(kind="S", magnitude=1.0, gear=1))
    phi_prev = env._prev_potential
    _obs, _r, done, info = env.step(Park())
    assert done and info.valid  # the point: a genuinely clean, valid completion
    assert info.terms["shaping"] == pytest.approx(-phi_prev)
