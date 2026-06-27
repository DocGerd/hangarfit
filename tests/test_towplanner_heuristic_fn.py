from __future__ import annotations

import contextlib

from bench.se2_heuristic_probe import build_toy_nook
from hangarfit import towplanner
from hangarfit.towplanner import Pose, plan_path

# These guard the seam itself (wired + default-neutral), which is independent of
# both grid resolution and search depth — so they run at the DEPLOYED grid under a
# SMALL expansion cap (the per-expansion motion-clear sampling, not the grid, is the
# cost; 4000 fine-grid expansions take ~35-64 s). A few dozen expansions prove the
# seam is wired and byte-identical while keeping these in the fast default/CI suite;
# the fine-grid headroom measurement lives in the @slow bench tests.
_SEAM_CAP = 64


def test_injected_heuristic_is_called_during_search() -> None:
    nook = build_toy_nook()
    calls: list[Pose] = []

    def recording_h(p: Pose) -> float:
        calls.append(p)
        return 0.0  # constant 0 ⇒ uniform-cost (Dijkstra), still valid via the oracle

    with contextlib.suppress(towplanner.NoFeasiblePlanError):
        plan_path(
            nook.mover,
            nook.entry,
            nook.goal,
            hangar=nook.hangar,
            placed=nook.placed,
            mover_on_carts=nook.mover_on_carts,
            max_expansions=_SEAM_CAP,
            heuristic="euclidean",
            heuristic_fn=recording_h,
        )
    assert calls, "injected heuristic_fn was never called — seam not wired"


def test_none_is_identical_to_default() -> None:
    """Passing heuristic_fn=None must equal not passing it (byte-identical seam)."""
    nook = build_toy_nook()
    s1: dict[str, object] = {}
    s2: dict[str, object] = {}
    for stats, kwargs in ((s1, {}), (s2, {"heuristic_fn": None})):
        with contextlib.suppress(towplanner.NoFeasiblePlanError):
            plan_path(
                nook.mover,
                nook.entry,
                nook.goal,
                hangar=nook.hangar,
                placed=nook.placed,
                mover_on_carts=nook.mover_on_carts,
                max_expansions=_SEAM_CAP,
                heuristic="grid",
                stats=stats,
                **kwargs,
            )
    # Non-vacuous: the deployed-grid search expands >0 nodes, so this compares a real
    # search trace, not two no-ops.
    assert s1["expansions"] == s2["expansions"]
    assert isinstance(s1["expansions"], int) and s1["expansions"] > 0
    assert s1["found"] == s2["found"]
