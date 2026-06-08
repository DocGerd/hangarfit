"""Read-only layout metrics for honest, actionable render annotations (#401).

Pure functions over a :class:`~hangarfit.models.Layout`: whether any *placed*
aircraft is on placeholder (unmeasured) data, the tightest plan-view inter-plane
gap, and the smallest vertical clearance where one plane's wing passes over
another plane's tail / aft fuselage. None of this enters the collision model — it
only annotates the 2D PNG and the 3D viewer so an operator can judge a tight
arrangement and know when the data is illustrative. Leaf consumer of the core
types, like :mod:`hangarfit.visualize` and :mod:`hangarfit.scene`.
"""

from __future__ import annotations

import math

from hangarfit import collisions
from hangarfit.geometry import WorldPart, aircraft_parts_world
from hangarfit.models import CheckResult, Layout

# Parts a wing may legally overhang: a wingtip may overhang a low-winger's tail
# (the horizontal stabilizer) or aft fuselage, never its cockpit (ADR-0012). The
# vertical clearance there is the actionable "how close did that overhang come"
# number an operator should eyeball. vertical_stabilizer (the fin) is deliberately
# absent: a wing over a fin is a conflict, not an overhang (ADR-0023).
_OVERHANGABLE = frozenset({"tail", "fuselage_aft"})

# The single source of the honesty-banner wording, shared by the 2D PNG
# (visualize) and the 3D viewer so they never drift (#401).
PLACEHOLDER_BANNER = "PLACEHOLDER DATA — illustrative only, not for real parking"


def has_placeholder_data(layout: Layout) -> bool:
    """True iff any *placed* aircraft is unmeasured (``measured is False``).

    Drives the "PLACEHOLDER DATA" honesty banner (#401, #79). There is no hangar
    ``measured`` flag today, so the signal is fleet-driven; once every placed
    aircraft is ``measured: true`` the banner disappears. A layout with no
    placements is vacuously not-placeholder — nothing is drawn, so there is no
    illustrative arrangement to caveat.
    """
    return any(not layout.fleet[p.plane_id].measured for p in layout.placements)


def layout_is_valid(layout: Layout, check_result: CheckResult | None = None) -> bool:
    """Whether the layout is collision-free, for gating render annotations (#401).

    Trusts a supplied :class:`CheckResult`; otherwise runs :func:`collisions.check`
    itself. Annotations (the quality readouts) must never imply a validity that was
    never verified — ``check_result is None`` means "caller did not check", **not**
    "valid", so we determine it here rather than assuming the layout is fine.
    """
    if check_result is not None:
        return check_result.valid
    return collisions.check(layout).valid


def _world_by_plane(layout: Layout) -> tuple[list[str], dict[str, list[WorldPart]]]:
    """Per-plane world parts, keyed by id (sorted) — the shared geometry both
    metrics walk. Routed through the production ``aircraft_parts_world`` oracle."""
    by_id = {p.plane_id: p for p in layout.placements}
    ids = sorted(by_id)
    world = {pid: aircraft_parts_world(layout.fleet[pid], by_id[pid]) for pid in ids}
    return ids, world


def min_pairwise_gap_m(layout: Layout) -> float | None:
    """Tightest plan-view gap (m) between any two planes' parts; ``None`` for a
    layout with fewer than two planes.

    Mirrors the solver's ``_spread_quality`` min-gap (ADR-0008) but works on *any*
    layout — the solver only records ``min_pairwise_gap_m`` for results it
    produced, whereas ``check`` / ``view`` operate on hand-authored layouts.
    """
    ids, world = _world_by_plane(layout)
    if len(ids) < 2:
        return None
    best = math.inf
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            for pa in world[ids[i]]:
                for pb in world[ids[j]]:
                    best = min(best, pa.polygon.distance(pb.polygon))
    # isfinite guard (symmetry with min_wing_over_tail_clearance_m): with ≥2
    # planes and the non-empty-parts invariant best is always finite, but never
    # let an inf leak into the scene JSON — json would emit a bare `Infinity` that
    # breaks the viewer's JSON.parse.
    return best if math.isfinite(best) else None


def min_wing_over_tail_clearance_m(layout: Layout) -> float | None:
    """Smallest vertical clearance (m) where one plane's wing passes over another
    plane's tail / aft fuselage, measured only where their plan-view footprints
    overlap and the wing is above. ``None`` when no such overhang exists.

    This is the tightest "is a wing about to clip a tail" margin — exactly the
    vertical near-miss an operator cannot see in a top-down PNG and should eyeball
    before parking. It is a clearance on a *valid* layout (an actual overlap with
    insufficient z is a collision the checker already rejects).
    """
    ids, world = _world_by_plane(layout)
    best = math.inf
    for a in ids:
        wings = [wp for wp in world[a] if wp.kind == "wing"]
        if not wings:
            continue
        for b in ids:
            if b == a:
                continue
            lowers = [wp for wp in world[b] if wp.kind in _OVERHANGABLE]
            for wing in wings:
                for low in lowers:
                    gap = wing.z_bottom_m - low.z_top_m
                    if gap >= 0.0 and wing.polygon.intersects(low.polygon):
                        best = min(best, gap)
    return best if math.isfinite(best) else None
