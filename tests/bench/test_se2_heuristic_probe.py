from __future__ import annotations

import contextlib

import pytest

from bench.se2_heuristic_probe import build_toy_nook, fine_grid
from hangarfit import towplanner
from hangarfit.towplanner import plan_path


@pytest.mark.slow
def test_toy_nook_requires_search_at_fine_grid() -> None:
    """The toy must be HARD: the analytic Reeds–Shepp shot must not solve it
    trivially (expansions > 0), so the heuristic actually drives expansion order."""
    nook = build_toy_nook()
    stats: dict[str, object] = {}
    with fine_grid(), contextlib.suppress(towplanner.NoFeasiblePlanError):
        # budget-exhausting is fine; expansions>0 is what we assert
        plan_path(
            nook.mover,
            nook.entry,
            nook.goal,
            hangar=nook.hangar,
            placed=nook.placed,
            mover_on_carts=nook.mover_on_carts,
            max_expansions=4000,
            heuristic="grid",
            stats=stats,
        )
    assert isinstance(stats["expansions"], int)
    assert stats["expansions"] > 0, "toy nook solved by the analytic shot — too easy, retune"


def test_toy_nook_goal_is_clear_of_the_parked_obstacle() -> None:
    """The mover at its goal pose must not collide with the parked plane —
    otherwise the fixture is invalid, not hard."""
    nook = build_toy_nook()
    obstacles = towplanner._build_obstacles(nook.placed, mover_id=nook.mover.id)
    assert towplanner._motion_clear(
        nook.mover, nook.goal, obstacles, nook.hangar.motion_hangar()
    ), "mover-at-goal conflicts with the parked obstacle — invalid fixture"
