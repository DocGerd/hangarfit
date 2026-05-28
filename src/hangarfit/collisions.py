"""Collision checker for hangarfit.

Single public entry: :func:`check`. Given a fully-validated
:class:`~hangarfit.models.Layout`, returns a
:class:`~hangarfit.models.CheckResult`.

The full set of layout invariants is four — three checked here, one
upstream:

1. **Hangar bounds** (checked here) — every world part vertex lies
   inside the hangar rectangle ``0 ≤ x ≤ hangar.width_m``,
   ``0 ≤ y ≤ hangar.length_m``.
2. **Maintenance bay intrusion** (checked here) — when
   ``layout.maintenance_plane`` is set, the bay rectangle becomes a
   hard keep-out for every other plane's parts. The bay is the
   axis-aligned rectangle anchored to the back wall:
   ``x ∈ (center_x_m − width_m/2, center_x_m + width_m/2)``,
   ``y ∈ (length_m − depth_m, length_m]``. A vertex strictly inside
   that rectangle is a ``bay_intrusion`` conflict on the owning plane.
   The occupant has no entry in ``world_parts`` (absent from
   placements by Layout invariant), so it never appears as the
   subject of an intrusion conflict against itself.
3. **Pairwise parts overlap** (checked here) — for every two parts from
   *different* aircraft, conflict iff both (a) their polygons are within
   ``hangar.clearance_m`` in plan view AND (b) their z-ranges are within
   ``hangar.wing_layer_clearance_m`` in height. **One exception:** a
   ``wing`` within plan-view clearance of another plane's
   ``fuselage_front`` (cockpit) is a HARD conflict regardless of the
   height gap — there is no nesting height at which a wing over a cockpit
   is acceptable (ADR-0012, D1). ``wing × fuselage_aft`` (tail) keeps the
   uniform two-clause z-gap rule.
4. **Cart rule** (upstream, *not here*) — at most ``hangar.max_carts``
   ``cart_eligible`` planes have ``on_carts=True``.
   :class:`Layout.__post_init__` rejects violations at construction, so by
   the time a Layout reaches the checker this rule has been satisfied.

Conflict ``kind`` taxonomy for pairwise rules is the two part kinds
sorted alphabetically and joined by ``"_"`` with the ``"_overlap"``
suffix: ``wing + strut`` → ``"strut_wing_overlap"``,
``fuselage_aft + wing`` → ``"fuselage_aft_wing_overlap"``,
``fuselage_front + wing`` → ``"fuselage_front_wing_overlap"``,
``fuselage_aft + fuselage_aft`` → ``"fuselage_aft_fuselage_aft_overlap"``.
``"fuselage_aft"`` sorts before ``"fuselage_front"`` sorts before
``"wing"``, so the taxonomy stays deterministic with no special-casing.
The alphabetical sort is what makes the kind deterministic regardless of
which plane is iterated first.
"""

from __future__ import annotations

from .geometry import WorldPart, aircraft_parts_world, polygon_overlap, polygon_overlap_area
from .models import CheckResult, Conflict, Hangar, Layout


def check(layout: Layout) -> CheckResult:
    """Run all geometric checks and return a :class:`CheckResult`."""
    world_parts: dict[str, list[WorldPart]] = {
        p.plane_id: aircraft_parts_world(layout.fleet[p.plane_id], p) for p in layout.placements
    }
    conflicts: list[Conflict] = []
    conflicts.extend(_hangar_bounds_conflicts(world_parts, layout.hangar))
    conflicts.extend(_bay_intrusion_conflicts(world_parts, layout))
    pairwise, total_penetration_m2 = _pairwise_conflicts(world_parts, layout.hangar)
    conflicts.extend(pairwise)
    return CheckResult(
        conflicts=tuple(conflicts),
        total_penetration_m2=total_penetration_m2,
    )


def _hangar_bounds_conflicts(
    world_parts: dict[str, list[WorldPart]], hangar: Hangar
) -> list[Conflict]:
    """Flag any world part with a vertex outside the hangar rectangle
    ``[0, width_m] × [0, length_m]``.

    Uses **per-vertex bounds**, not ``polygon.contains(...)``. The Shapely
    contains check has touching-vs-overlap subtleties at the boundary that
    would either over- or under-report walls flush with the hangar edge.
    Per-vertex bounds give "every corner inside" with no surprise semantics
    — a vertex exactly at ``x = 0`` or ``x = hangar.width_m`` counts as inside.

    Emits one conflict per offending **part** (not per plane), with the
    first out-of-bounds vertex of that part. Reporting per-part means a
    plane whose wing sticks out the front *and* tail sticks out the back
    surfaces both, instead of having one masked behind the other.
    """
    out: list[Conflict] = []
    for plane_id, parts in world_parts.items():
        for part in parts:
            bad = _first_out_of_bounds_vertex(part, hangar)
            if bad is None:
                continue
            x, y = bad
            out.append(
                Conflict.single(
                    kind="hangar_bounds",
                    plane=plane_id,
                    detail=(
                        f"part {part.kind!r} vertex ({x:.3f}, {y:.3f}) outside hangar "
                        f"0..{hangar.width_m:g} x 0..{hangar.length_m:g}"
                    ),
                )
            )
    return out


def _first_out_of_bounds_vertex(part: WorldPart, hangar: Hangar) -> tuple[float, float] | None:
    """Return ``(x, y)`` of the first vertex of ``part`` outside the
    hangar rectangle, or ``None`` if every vertex is inside."""
    for x, y in list(part.polygon.exterior.coords)[:-1]:
        if not (0.0 <= x <= hangar.width_m and 0.0 <= y <= hangar.length_m):
            return x, y
    return None


def _bay_intrusion_conflicts(
    world_parts: dict[str, list[WorldPart]], layout: Layout
) -> list[Conflict]:
    """When the bay is closed (``layout.maintenance_plane is not None``),
    flag any non-occupant part with a vertex strictly inside the bay
    rectangle.

    The bay is the axis-aligned rectangle anchored to the back wall:

    - ``x ∈ (center_x_m − width_m/2, center_x_m + width_m/2)``
    - ``y ∈ (length_m − depth_m, length_m]``

    The three interior edges (left, right, front) use strict ``<``: a
    vertex sitting on any of those edges counts as outside. The bay's
    back edge coincides with the hangar's back wall, which is why
    there is no separate ``y < length_m`` test — a vertex at
    ``y = length_m`` sits on the hangar's outer wall (still inside the
    hangar per :func:`_hangar_bounds_conflicts`) and is correctly
    treated as inside the closed bay. This mirrors the convention in
    :func:`_first_out_of_bounds_vertex` where the hangar boundary is
    inclusive.

    The maintenance occupant is absent from placements by Layout
    invariant, so it should not appear in ``world_parts``. A defensive
    ``continue`` still skips it explicitly — if a future bug ever
    let the occupant leak in (a hand-built Layout that bypassed
    construction, a solver regression), the silent nonsense conflict
    "the occupant intrudes into its own bay" would otherwise be
    indistinguishable from a real intrusion.

    Emits one ``bay_intrusion`` conflict per offending **part**, with
    the first-violating vertex (matches the per-part granularity of
    :func:`_hangar_bounds_conflicts`).
    """
    if layout.maintenance_plane is None:
        return []
    bay = layout.hangar.maintenance_bay
    x_min = bay.center_x_m - bay.width_m / 2
    x_max = bay.center_x_m + bay.width_m / 2
    y_min = layout.hangar.length_m - bay.depth_m
    out: list[Conflict] = []
    for plane_id, parts in world_parts.items():
        if plane_id == layout.maintenance_plane:
            continue
        for part in parts:
            bad = _first_vertex_in_bay(part, x_min, x_max, y_min)
            if bad is None:
                continue
            x, y = bad
            out.append(
                Conflict.single(
                    kind="bay_intrusion",
                    plane=plane_id,
                    detail=(
                        f"part {part.kind!r} vertex ({x:.3f}, {y:.3f}) inside "
                        f"closed maintenance bay (x ∈ ({x_min:g}, {x_max:g}), "
                        f"y ∈ ({y_min:g}, {layout.hangar.length_m:g}); "
                        f"occupant={layout.maintenance_plane!r})"
                    ),
                )
            )
    return out


def _first_vertex_in_bay(
    part: WorldPart, x_min: float, x_max: float, y_min: float
) -> tuple[float, float] | None:
    """Return ``(x, y)`` of the first vertex of ``part`` strictly inside
    the bay rectangle, or ``None`` if every vertex is outside. Strict
    ``<`` on left/right/front edges; no back-edge test (back edge is
    the hangar's outer wall — see :func:`_bay_intrusion_conflicts`)."""
    for x, y in list(part.polygon.exterior.coords)[:-1]:
        if x_min < x < x_max and y > y_min:
            return x, y
    return None


def _pairwise_conflicts(
    world_parts: dict[str, list[WorldPart]], hangar: Hangar
) -> tuple[list[Conflict], float]:
    """For every pair of parts from *different* aircraft, emit a conflict
    per :func:`_parts_conflict` (the uniform two-clause rule, plus the
    ``wing × fuselage_front`` cockpit exception that drops the height clause).

    The conflict ``kind`` is the two part kinds sorted **alphabetically**
    and joined by ``"_"`` with the ``"_overlap"`` suffix
    (``fuselage_front + wing`` → ``"fuselage_front_wing_overlap"``,
    ``strut + wing`` → ``"strut_wing_overlap"``). The sort makes the
    kind deterministic regardless of plane iteration order — without it,
    ``(plane_a.wing, plane_b.strut)`` and ``(plane_b.strut, plane_a.wing)``
    would yield different kinds for the same physical conflict.

    Same-aircraft pairs are skipped: a strut-braced plane's own strut
    "overlaps" its own wing by design (the strut runs *up to* the wing's
    underside), so comparing within an aircraft would always trip.

    Plane pairs are iterated as ``(i, j)`` with ``i < j`` on the
    placement order, so the conflict's ``(plane_a, plane_b)`` matches the
    layout's placement order — a small affordance for stable test output.

    When a plane's wing overlaps **multiple** struts of another plane
    (canonical case: a low wing reaching across both struts of a strut-
    braced high wing), one conflict is emitted **per part pair**. Two
    ``strut_wing_overlap`` conflicts with identical ``(plane_a, plane_b)``
    is the intended shape — these are two distinct geometric collisions
    (one per physical strut), not a duplicate. The ``detail`` strings
    differ via their z-ranges and the gap computation.

    Returns a ``(conflicts, total_penetration_m2)`` tuple. The second
    component accumulates the shapely ``intersection().area`` (via
    :func:`hangarfit.geometry.polygon_overlap_area`) of every pairwise
    conflict — used as Phase 2a's secondary scoring key to break
    plateaus in the integer conflict-count metric. Clearance-only
    conflicts (polygons within ``clearance_m`` but not actually
    intersecting) contribute 0, matching the spec's "two planes
    overlapping" framing.
    """
    out: list[Conflict] = []
    total_penetration_m2 = 0.0
    ids = list(world_parts.keys())
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a_id, b_id = ids[i], ids[j]
            for pa in world_parts[a_id]:
                for pb in world_parts[b_id]:
                    if _parts_conflict(pa, pb, hangar):
                        out.append(_build_pairwise_conflict(pa, pb, a_id, b_id, hangar))
                        total_penetration_m2 += polygon_overlap_area(pa.polygon, pb.polygon)
    return out, total_penetration_m2


def _is_wing_over_cockpit(pa: WorldPart, pb: WorldPart) -> bool:
    """Whether the unordered part pair is ``wing`` × ``fuselage_front``.

    Order-independent so the predicate is symmetric in ``(pa, pb)`` (the
    pairwise loop iterates one ordering only, but a symmetric helper is
    robust to a future reorder). This is the one pair whose height clause is
    dropped — see :func:`_parts_conflict` and ADR-0012 (D1).
    """
    kinds = {pa.kind, pb.kind}
    return kinds == {"wing", "fuselage_front"}


def _parts_conflict(pa: WorldPart, pb: WorldPart, hangar: Hangar) -> bool:
    """Return True iff the two parts conflict.

    Plan-view: ``polygon_overlap`` with ``hangar.clearance_m`` (which
    splits semantics at zero — see :func:`hangarfit.geometry.polygon_overlap`).
    Every conflict requires plan-view overlap; the pairs differ only in the
    **height clause**.

    **The cockpit exception (ADR-0012, D1).** A ``wing`` within plan-view
    clearance of another plane's ``fuselage_front`` (cockpit) is a HARD
    conflict — the height gap is **ignored**. A wing over a cockpit blocks
    the canopy / prop arc / pilot ingress at *any* nesting height, so there
    is no z-gap that makes it acceptable. This is the only pair that drops
    the height clause.

    **Every other pair** (including ``wing × fuselage_aft`` — a wing over
    the tail) keeps the uniform two-clause rule. Height: the gap between the
    z-ranges is ``max(za_bottom, zb_bottom) − min(za_top, zb_top)`` —
    negative if the intervals strictly overlap, zero if they exactly touch,
    positive if they are separated. With ``clearance > 0`` we conflict on
    ``gap < clearance`` (so a 0.1 m gap below a 0.2 m clearance still trips);
    with ``clearance == 0`` we conflict only on ``gap < 0`` (strict interior
    overlap, matching the polygon side's "touches isn't a conflict at zero
    clearance" rule).
    """
    if not polygon_overlap(pa.polygon, pb.polygon, clearance=hangar.clearance_m):
        return False
    if _is_wing_over_cockpit(pa, pb):
        # z ignored: plan-view overlap alone is the conflict (D1).
        return True
    gap = max(pa.z_bottom_m, pb.z_bottom_m) - min(pa.z_top_m, pb.z_top_m)
    if hangar.wing_layer_clearance_m > 0:
        return gap < hangar.wing_layer_clearance_m
    return gap < 0


def _build_pairwise_conflict(
    pa: WorldPart, pb: WorldPart, a_id: str, b_id: str, hangar: Hangar
) -> Conflict:
    kinds_sorted = sorted([pa.kind, pb.kind])
    kind = f"{kinds_sorted[0]}_{kinds_sorted[1]}_overlap"
    gap = max(pa.z_bottom_m, pb.z_bottom_m) - min(pa.z_top_m, pb.z_top_m)
    if _is_wing_over_cockpit(pa, pb):
        # Cockpit exception (D1): the conflict fired on plan-view overlap
        # alone, so the height clause is reported as ignored rather than as a
        # threshold the z-gap fell under (which would be misleading when the
        # wing genuinely clears the cockpit in height).
        height_clause = f"z-gap {gap:g} m IGNORED (wing over fuselage_front / cockpit)"
    else:
        height_clause = f"z-gap {gap:g} m (< {hangar.wing_layer_clearance_m:g} m)"
    return Conflict.pair(
        kind=kind,
        plane_a=a_id,
        plane_b=b_id,
        detail=(
            f"part {pa.kind!r} (z={pa.z_bottom_m:g}..{pa.z_top_m:g}) and "
            f"part {pb.kind!r} (z={pb.z_bottom_m:g}..{pb.z_top_m:g}) "
            f"within horizontal clearance {hangar.clearance_m:g} m "
            f"and {height_clause}"
        ),
    )
