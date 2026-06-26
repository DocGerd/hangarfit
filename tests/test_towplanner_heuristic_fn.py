from __future__ import annotations

import contextlib

from bench.se2_heuristic_probe import build_toy_nook, fine_grid
from hangarfit import towplanner
from hangarfit.towplanner import Pose, plan_path


def test_injected_heuristic_is_called_during_search() -> None:
    nook = build_toy_nook()
    calls: list[Pose] = []

    def recording_h(p: Pose) -> float:
        calls.append(p)
        return 0.0  # constant 0 ⇒ uniform-cost (Dijkstra), still valid via the oracle

    with fine_grid(), contextlib.suppress(towplanner.NoFeasiblePlanError):
        plan_path(
            nook.mover,
            nook.entry,
            nook.goal,
            hangar=nook.hangar,
            placed=nook.placed,
            mover_on_carts=nook.mover_on_carts,
            max_expansions=4000,
            heuristic="euclidean",
            heuristic_fn=recording_h,
        )
    assert calls, "injected heuristic_fn was never called — seam not wired"


def test_none_is_identical_to_default() -> None:
    """Passing heuristic_fn=None must equal not passing it (byte-identical seam)."""
    nook = build_toy_nook()
    s1: dict[str, object] = {}
    s2: dict[str, object] = {}
    with fine_grid():
        for stats, kwargs in ((s1, {}), (s2, {"heuristic_fn": None})):
            with contextlib.suppress(towplanner.NoFeasiblePlanError):
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
                    **kwargs,
                )
    assert s1["expansions"] == s2["expansions"]
    assert s1["found"] == s2["found"]
