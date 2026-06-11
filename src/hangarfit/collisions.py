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

from .geometry import (
    WorldPart,
    aircraft_parts_world,
    axis_aligned_rect,
    cached_parts_world,
    polygon_overlap,
    polygon_overlap_area,
)
from .models import CheckResult, Conflict, GroundObject, Hangar, Layout


def check(layout: Layout) -> CheckResult:
    """Run all geometric checks and return a :class:`CheckResult`."""
    aircraft_parts: dict[str, list[WorldPart]] = {
        p.plane_id: cached_parts_world(layout.fleet[p.plane_id], p) for p in layout.placements
    }
    # Ground objects (#601): movers are placed bodies (pairwise); fixed obstacles
    # are keep-outs. Both reuse the aircraft world-transform (det-(-1), ADR-0002).
    mover_parts: dict[str, list[WorldPart]] = {}
    obstacle_parts: dict[str, list[WorldPart]] = {}
    for gp in layout.ground_object_placements:
        obj: GroundObject = layout.ground_objects[gp.plane_id]
        wparts = aircraft_parts_world(obj, gp)
        if obj.object_class == "fixed_obstacle":
            obstacle_parts[gp.plane_id] = wparts
        else:
            mover_parts[gp.plane_id] = wparts

    # Movers join the placed-body set for bounds-free pairwise collision; aircraft
    # come first so the no-ground-object case is byte-identical (same dict order):
    # with no ground objects mover_parts/obstacle_parts are empty, placed_bodies ==
    # aircraft_parts, _ground_obstacle_conflicts returns [], and the conflict order
    # + total_penetration_m2 match pre-#601. _hangar_bounds_conflicts and
    # _bay_intrusion_conflicts keep their aircraft-only input (ground-object bounds
    # checking is out of #601 scope — #604/#605).
    placed_bodies = {**aircraft_parts, **mover_parts}

    conflicts: list[Conflict] = []
    conflicts.extend(_hangar_bounds_conflicts(aircraft_parts, layout.hangar))
    conflicts.extend(_bay_intrusion_conflicts(aircraft_parts, layout))
    pairwise, total_penetration_m2 = _pairwise_conflicts(placed_bodies, layout.hangar)
    conflicts.extend(pairwise)
    conflicts.extend(_ground_obstacle_conflicts(placed_bodies, obstacle_parts, layout.hangar))
    return CheckResult(
        conflicts=tuple(conflicts),
        total_penetration_m2=total_penetration_m2,
    )


def _ground_obstacle_conflicts(
    placed_bodies: dict[str, list[WorldPart]],
    obstacle_parts: dict[str, list[WorldPart]],
    hangar: Hangar,
) -> list[Conflict]:
    """A fixed obstacle's footprint is a keep-out: any aircraft/mover part that
    conflicts with it (plan-view overlap within clearance AND z-gap rule, via
    :func:`_parts_conflict`) is a single-object ``ground_obstacle`` conflict
    naming the offending body and the obstacle (#601 / ADR-0025).

    Deterministic iteration: obstacles in dict-insertion order (manifest order),
    bodies in placement order. Empty ``obstacle_parts`` → ``[]`` (byte-identity)."""
    out: list[Conflict] = []
    for obstacle_id, obs_wparts in obstacle_parts.items():
        for body_id, body_wparts in placed_bodies.items():
            for op in obs_wparts:
                for bp in body_wparts:
                    if _parts_conflict(op, bp, hangar):
                        out.append(
                            Conflict.single(
                                kind="ground_obstacle",
                                plane=body_id,
                                detail=(
                                    f"part {bp.kind!r} of {body_id!r} overlaps fixed "
                                    f"obstacle {obstacle_id!r} part {op.kind!r}"
                                ),
                            )
                        )
    return out


def _hangar_bounds_conflicts(
    world_parts: dict[str, list[WorldPart]], hangar: Hangar
) -> list[Conflict]:
    """Flag any world part that is not fully inside the hangar floor.

    Two regimes, selected by whether the hangar has a structural notch:

    * **No notch (the common case)** — the floor is the plain rectangle
      ``[0, width_m] × [0, length_m]`` and a part is in-bounds iff every
      vertex lies inside it. Uses **per-vertex bounds**, not
      ``polygon.contains(...)``: the Shapely contains check has
      touching-vs-overlap subtleties at the boundary that would over- or
      under-report walls flush with the hangar edge; per-vertex bounds give
      "every corner inside", with a vertex exactly at ``x = 0`` or
      ``x = hangar.width_m`` counting as inside. This path is byte-identical
      to the pre-notch checker.

    * **With one or more** :class:`~hangarfit.models.StructuralNotch` — the
      floor is the L-shaped :attr:`~hangarfit.models.Hangar.floor_polygon`
      (outer rectangle minus the notches) and a part is in-bounds iff
      ``floor.covers(part.polygon)``. ``covers`` (not ``contains``) keeps the
      same boundary-inclusive semantics — a part flush with an outer wall
      *or* with a notch edge is inside — while correctly rejecting a part that
      overhangs a notch, **including a thin part whose edge crosses a notch
      with neither endpoint inside it** (the per-vertex test's blind spot,
      ADR-0018). A notch overhang is reported as a ``structural_notch``
      conflict; escaping the outer rectangle stays ``hangar_bounds``.

    Emits one conflict per offending **part** (not per plane): a
    ``hangar_bounds`` conflict names the first out-of-bounds vertex; a
    ``structural_notch`` conflict names the overhung notch (the edge-crossing
    case has no vertex inside to name). Reporting per-part means a plane whose
    wing sticks out the front *and* tail sticks out the back surfaces both,
    instead of having one masked behind the other.
    """
    floor = hangar.floor_polygon
    out: list[Conflict] = []
    for plane_id, parts in world_parts.items():
        for part in parts:
            bad = _first_out_of_bounds_vertex(part, hangar)
            if bad is not None:
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
                continue
            # Inside the outer rectangle but possibly overhanging a notch. Only
            # reached when the hangar has notches (``floor`` is then non-None);
            # the ``covers`` test also catches edge-crossings with no vertex inside.
            if floor is not None and not floor.covers(part.polygon):
                out.append(
                    Conflict.single(
                        kind="structural_notch",
                        plane=plane_id,
                        detail=_notch_intrusion_detail(part, hangar),
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


def _notch_intrusion_detail(part: WorldPart, hangar: Hangar) -> str:
    """Human-readable detail for a ``structural_notch`` conflict, naming the
    first notch whose rectangle the part's bounding box overlaps.

    The part has already failed ``floor.covers`` while sitting inside the outer
    rectangle, so it must overhang *some* notch — and a true overhang always
    overlaps that notch's AABB, so the scan finds it. The trailing generic
    ``return`` is an unreachable defensive default. (With multiple notches whose
    boxes overlap the part, the broad-phase names the first match, which is fine
    for a human-readable message.)"""
    pxmin, pymin, pxmax, pymax = _part_aabb(part)
    for n in hangar.structural_notches:
        if pxmin < n.x_max_m and pxmax > n.x_min_m and pymin < n.y_max_m and pymax > n.y_min_m:
            return (
                f"part {part.kind!r} overhangs structural notch "
                f"(no floor at x ∈ [{n.x_min_m:g}, {n.x_max_m:g}], "
                f"y ∈ [{n.y_min_m:g}, {n.y_max_m:g}])"
            )
    return f"part {part.kind!r} overhangs a structural notch"


def _bay_intrusion_conflicts(
    world_parts: dict[str, list[WorldPart]], layout: Layout
) -> list[Conflict]:
    """When the bay is closed (``layout.maintenance_plane is not None``),
    flag any non-occupant part that either has a vertex strictly inside the bay
    rectangle OR whose edge crosses it with no vertex inside.

    The bay is the axis-aligned rectangle anchored to the back wall:

    - ``x ∈ (center_x_m − width_m/2, center_x_m + width_m/2)``
    - ``y ∈ (length_m − depth_m, length_m]``

    The per-vertex test (:func:`_first_vertex_in_bay`) is the primary gate —
    strict ``<`` on the left/right/front edges, inclusive back edge (the hangar
    wall, per :func:`_first_out_of_bounds_vertex`). To close the per-vertex test's
    edge-crossing blind spot (a thin part skewering the bay with both endpoints
    outside — the ADR-0018 ``floor.covers`` fix, mirrored here, #551), a
    **polygon-level** :func:`polygon_overlap` test is consulted *additively* when
    no vertex is inside. ``polygon_overlap`` (clearance 0) requires genuine
    *interior* overlap, so a part merely flush with a bay edge (Shapely
    ``touches``) is still outside. Keeping the per-vertex test as the gate (rather
    than replacing it with the polygon test) keeps every existing verdict
    byte-identical (ADR-0003): the polygon test only ever *adds* the edge-crossing
    case, never removes a conflict the per-vertex test already raised.

    The maintenance occupant is absent from placements by Layout
    invariant, so it should not appear in ``world_parts``. A defensive
    ``continue`` still skips it explicitly — if a future bug ever
    let the occupant leak in (a hand-built Layout that bypassed
    construction, a solver regression), the silent nonsense conflict
    "the occupant intrudes into its own bay" would otherwise be
    indistinguishable from a real intrusion.

    Emits one ``bay_intrusion`` conflict per offending **part** — citing the
    first strictly-inside vertex when one exists, or ``edge crosses`` when only
    the polygon overlaps (matches the per-part granularity of
    :func:`_hangar_bounds_conflicts`).
    """
    if layout.maintenance_plane is None:
        return []
    bay = layout.hangar.maintenance_bay
    x_min = bay.center_x_m - bay.width_m / 2
    x_max = bay.center_x_m + bay.width_m / 2
    y_min = layout.hangar.length_m - bay.depth_m
    bay_rect = axis_aligned_rect(x_min, y_min, x_max, layout.hangar.length_m)
    out: list[Conflict] = []
    for plane_id, parts in world_parts.items():
        if plane_id == layout.maintenance_plane:
            continue
        for part in parts:
            # The per-vertex test stays the PRIMARY gate so every existing verdict is
            # byte-identical (ADR-0003). It intentionally counts a vertex beyond the
            # back wall (y > length_m) — that candidate is out of bounds and already
            # flagged by _hangar_bounds_conflicts, but the min-conflicts solver steers
            # by conflict COUNT, so dropping that redundant conflict would shift the
            # search trajectory. The polygon-overlap test is therefore an ADDITIVE
            # catch, consulted only when NO vertex is inside: it flags the ADR-0018
            # blind spot — a thin part whose EDGE crosses the bay with no vertex inside
            # (#551). ``polygon_overlap`` (clearance 0) requires genuine *interior*
            # overlap, so a part merely flush with a bay edge (Shapely ``touches``) is
            # still outside, preserving the strict-``<`` boundary semantics.
            bad = _first_vertex_in_bay(part, x_min, x_max, y_min)
            if bad is not None:
                where = f"vertex ({bad[0]:.3f}, {bad[1]:.3f}) inside"
            elif polygon_overlap(bay_rect, part.polygon):
                where = "edge crosses"
            else:
                continue
            out.append(
                Conflict.single(
                    kind="bay_intrusion",
                    plane=plane_id,
                    detail=(
                        f"part {part.kind!r} {where} "
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
    the hangar's outer wall — see :func:`_bay_intrusion_conflicts`).

    This is the primary intrusion gate (#551 adds an additive polygon-overlap
    catch for the edge-crossing case on top of it). When it returns a vertex,
    the caller renders it in the detail; when it returns ``None`` but the polygon
    still overlaps the bay, the caller renders "edge crosses" instead."""
    for x, y in list(part.polygon.exterior.coords)[:-1]:
        if x_min < x < x_max and y > y_min:
            return x, y
    return None


def _part_aabb(part: WorldPart) -> tuple[float, float, float, float]:
    """Axis-aligned plan-view bounding box ``(xmin, ymin, xmax, ymax)`` of a
    part's polygon. The 4-tuple unpack also asserts the expected arity.

    Precomputed once per part by :func:`_pairwise_conflicts` for the #454
    broad-phase (mirrors :func:`hangarfit.towplanner._aabb`; kept local to avoid
    a ``collisions → towplanner`` import cycle)."""
    xmin, ymin, xmax, ymax = part.polygon.bounds
    return xmin, ymin, xmax, ymax


def _aabbs_separated_beyond_clearance(
    box_a: tuple[float, float, float, float],
    box_b: tuple[float, float, float, float],
    clearance: float,
) -> bool:
    """Broad-phase reject (#454): whether two parts' axis-aligned bounding boxes
    are separated by **strictly more** than ``clearance`` on some axis.

    A per-axis box gap is a provable lower bound on the true polygon
    edge-to-edge distance (each polygon is contained in its AABB), so when this
    returns ``True`` the polygon distance also exceeds ``clearance`` and
    :func:`_parts_conflict` is *guaranteed* to return ``False`` — the exact
    (and costly) shapely predicate can be skipped. It therefore NEVER skips a
    pair that would actually conflict, so the filtered :func:`_pairwise_conflicts`
    returns a byte-identical :class:`~hangarfit.models.CheckResult` (ADR-0003).

    The strict ``>`` is load-bearing at ``clearance == 0``: a touching box edge
    (gap ``0``) is *not* skipped, so the exact ``intersects``-and-not-``touches``
    rule still decides it. (With ``>=`` and ``clearance == 0`` every pair would
    be skipped — wrong.) This mirrors the x/y AABB filter in
    :func:`hangarfit.towplanner._motion_clear`; its z-prefilter is deliberately
    NOT copied — ``_parts_conflict``'s own z-clause is cheap float arithmetic,
    and that prefilter carries a documented divergence trap (a small positive
    z-gap in ``[0, wing_layer_clearance_m)`` must not be skipped)."""
    ax_min, ay_min, ax_max, ay_max = box_a
    bx_min, by_min, bx_max, by_max = box_b
    return (
        ax_min - bx_max > clearance
        or bx_min - ax_max > clearance
        or ay_min - by_max > clearance
        or by_min - ay_max > clearance
    )


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

    A plan-view AABB broad-phase (#454) rejects clearly-separated part
    pairs before the exact :func:`_parts_conflict` predicate. Each part's
    bounding box is computed once (not once per pair); a pair whose boxes
    are more than ``clearance_m`` apart on any axis cannot conflict (the
    box gap lower-bounds the true polygon distance — see
    :func:`_aabbs_separated_beyond_clearance`), so the verdict and the
    ``total_penetration_m2`` accumulation are byte-identical to the
    unfiltered loop (ADR-0003).
    """
    out: list[Conflict] = []
    total_penetration_m2 = 0.0
    ids = list(world_parts.keys())
    clearance = hangar.clearance_m
    # #454 broad-phase: precompute each part's AABB ONCE (not once per pair) so a
    # cheap per-axis box-gap reject can skip the costly exact predicate for
    # clearly-separated pairs. The reject is a provable lower-bound test (see
    # _aabbs_separated_beyond_clearance), so the conflict set and the
    # total_penetration_m2 accumulation order are unchanged — byte-identical
    # CheckResult (ADR-0003).
    aabbs: dict[str, list[tuple[float, float, float, float]]] = {
        pid: [_part_aabb(p) for p in parts] for pid, parts in world_parts.items()
    }
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a_id, b_id = ids[i], ids[j]
            a_boxes, b_boxes = aabbs[a_id], aabbs[b_id]
            for pa, box_a in zip(world_parts[a_id], a_boxes, strict=True):
                for pb, box_b in zip(world_parts[b_id], b_boxes, strict=True):
                    if _aabbs_separated_beyond_clearance(box_a, box_b, clearance):
                        continue  # provably no conflict — skip the exact predicate
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

    NB ``vertical_stabilizer`` (the fin) is deliberately NOT here (ADR-0023):
    it keeps the uniform two-clause rule, so a wing conflicts with a fin only
    when it both overlaps the thin centreline fin in plan view AND their
    z-bands meet — i.e. wing-over-tail nesting stays legal iff the wing clears
    the fin laterally. The fin earns no z-drop because, unlike a cockpit, the
    obstruction is geometric (lateral clearance), not categorical.
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
