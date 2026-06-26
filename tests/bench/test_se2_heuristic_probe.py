from __future__ import annotations

import contextlib
import math
from collections import defaultdict

import pytest

from bench.se2_heuristic_probe import build_se2_field, build_toy_nook, fine_grid, make_field_h
from hangarfit import towplanner
from hangarfit.towplanner import Pose, plan_path


@pytest.mark.slow
def test_toy_nook_requires_search_at_fine_grid() -> None:
    """The toy must be HARD: the analytic Reeds–Shepp shot must not solve it
    trivially (expansions > 0), so the heuristic actually drives expansion order."""
    # @pytest.mark.slow is INTENTIONAL: the fine-grid (0.25 m/10°) A* run takes
    # ~34 s by nature (that slowness IS the headroom the SE(2) heuristic must
    # collapse). Because it is excluded from the default `pytest` run and from CI
    # (`addopts = -m 'not slow'`), this search-forcing calibration is NOT
    # auto-verified: if the toy nook geometry (_CX/_CY, hangar dims, or door)
    # changes, RE-RUN this by hand (`pytest -m slow tests/bench/...`) to confirm
    # the goal still genuinely requires search (expansions > 0).
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


def test_se2_field_is_heading_aware() -> None:
    """The SE(2) cost-to-go field must assign different costs to the same (x,y)
    position at different headings — the core property the position-only ``grid``
    heuristic lacks.

    The PRIMARY assertion is STRUCTURAL: we inspect the field dict itself and
    check that at least one spatial cell ``(ix, iy)`` carries ≥2 heading bins
    with different cost values.  This avoids a fixture-dependent flake: the
    alternative approach of asserting ``h(rotated_goal) > 0`` would silently
    pass via the euclidean fallback (which returns 0 at the goal's own x,y) if
    the rotated pose happens to be unreachable (e.g. a 90° pivot collides with
    the tucked obstacle).  The structural form is strictly stronger — it
    directly proves "same position, different heading ⇒ different cost-to-go"
    across the entire field, not just at one specific pose.
    """
    nook = build_toy_nook()
    r = nook.mover.effective_turn_radius_m()
    obstacles = towplanner._build_obstacles(nook.placed, mover_id=nook.mover.id)
    with fine_grid():
        field = build_se2_field(
            nook.mover,
            nook.goal,
            obstacles,
            nook.hangar.motion_hangar(),
            r,
            mover_on_carts=nook.mover_on_carts,
        )
        h = make_field_h(field, nook.goal)

        # Goal cell must be cost 0.
        assert h(nook.goal) == 0.0

        # Structural heading-awareness: find at least one (ix,iy) group with
        # ≥2 distinct heading bins that have different cost-to-go values.
        by_xy: dict[tuple[int, int], list[float]] = defaultdict(list)
        for (ix, iy, _), cost in field.items():
            by_xy[(ix, iy)].append(cost)
        found_heading_sensitive = any(
            max(costs) > min(costs) for costs in by_xy.values() if len(costs) >= 2
        )
        assert found_heading_sensitive, (
            "SE(2) field is NOT heading-aware: no (x,y) cell has different "
            "cost-to-go values for different headings"
        )

        # Rotated-goal check: only assert > 0 when _cell is actually in the
        # field (if the pose is unreachable, the absence is correct — not a bug).
        rotated = Pose(nook.goal.x_m, nook.goal.y_m, (nook.goal.heading_deg + 90.0) % 360.0)
        if towplanner._cell(rotated) in field:
            assert h(rotated) > 0.0, (
                "rotated goal pose is in the field but has cost 0 — "
                "heading is not being discriminated"
            )

        # Far-away pose absent from the field → euclidean fallback (finite, > 0).
        far = Pose(nook.goal.x_m + 50.0, nook.goal.y_m, nook.goal.heading_deg)
        assert math.isfinite(h(far)) and h(far) > 0.0
