"""Collision checker for hangarfit.

Single public entry: :func:`check`. Given a fully-validated
:class:`~hangarfit.models.Layout`, runs three geometric rules and
returns a :class:`~hangarfit.models.CheckResult`.

The four invariants of a valid layout:

1. **Hangar bounds** — every world part vertex lies inside the hangar
   rectangle ``0 ≤ x ≤ hangar.width_m``, ``0 ≤ y ≤ hangar.length_m``.
2. **Maintenance bay** — if ``layout.maintenance_plane`` is set, the
   union of that plane's fuselage parts must have its centroid at
   ``y ≥ hangar.length_m − hangar.maintenance_bay.depth_m``.
3. **Pairwise parts overlap** — for every two parts from *different*
   aircraft, conflict iff both (a) their polygons are within
   ``hangar.clearance_m`` in plan view AND (b) their z-ranges are
   within ``hangar.wing_layer_clearance_m`` in height.
4. **Cart rule** — at most one ``cart_eligible`` plane has
   ``on_carts=True``. *NOT enforced here* — :class:`Layout.__post_init__`
   already rejects this at construction, so by the time a Layout
   reaches the checker the rule has been satisfied.

Conflict ``kind`` taxonomy for pairwise rules is the two part kinds
sorted alphabetically and joined by ``"_"`` with the ``"_overlap"``
suffix: ``wing + strut`` → ``"strut_wing_overlap"``,
``fuselage + wing`` → ``"fuselage_wing_overlap"``. The alphabetical
sort is what makes the kind deterministic regardless of which plane
is iterated first.
"""

from __future__ import annotations

from shapely.ops import unary_union

from .geometry import WorldPart, aircraft_parts_world, polygon_overlap
from .models import CheckResult, Conflict, Hangar, Layout


def check(layout: Layout) -> CheckResult:
    """Run all geometric checks and return a :class:`CheckResult`."""
    world_parts: dict[str, list[WorldPart]] = {
        p.plane_id: aircraft_parts_world(layout.fleet[p.plane_id], p)
        for p in layout.placements
    }
    conflicts: list[Conflict] = []
    conflicts.extend(_hangar_bounds_conflicts(world_parts, layout.hangar))
    conflicts.extend(_maintenance_conflicts(world_parts, layout))
    conflicts.extend(_pairwise_conflicts(world_parts, layout.hangar))
    return CheckResult(conflicts=tuple(conflicts))


def _hangar_bounds_conflicts(
    world_parts: dict[str, list[WorldPart]], hangar: Hangar
) -> list[Conflict]:
    """Flag any plane whose world parts have a vertex outside the hangar
    rectangle ``[0, width_m] × [0, length_m]``.

    Per the issue's settled design (memory: ``project_phase1_progress``)
    this uses **per-vertex bounds**, not ``polygon.contains(...)``. The
    Shapely contains check has touching-vs-overlap subtleties at the
    boundary that would either over- or under-report walls flush with
    the hangar edge. Per-vertex bounds give "every corner inside" with
    no surprise semantics.

    Emits one conflict per plane (the first out-of-bounds vertex
    encountered) so a plane sticking out at two corners doesn't
    duplicate-flag the same problem.
    """
    out: list[Conflict] = []
    for plane_id, parts in world_parts.items():
        bad = _first_out_of_bounds(parts, hangar)
        if bad is None:
            continue
        kind, x, y = bad
        out.append(
            Conflict.single(
                kind="hangar_bounds",
                plane=plane_id,
                detail=(
                    f"part {kind!r} vertex ({x:.3f}, {y:.3f}) outside hangar "
                    f"0..{hangar.width_m:g} x 0..{hangar.length_m:g}"
                ),
            )
        )
    return out


def _first_out_of_bounds(
    parts: list[WorldPart], hangar: Hangar
) -> tuple[str, float, float] | None:
    """Return ``(kind, x, y)`` of the first out-of-hangar vertex, or
    ``None`` if every vertex is inside the hangar rectangle."""
    for part in parts:
        coords = list(part.polygon.exterior.coords)[:-1]
        for x, y in coords:
            if not (0.0 <= x <= hangar.width_m and 0.0 <= y <= hangar.length_m):
                return part.kind, x, y
    return None


def _maintenance_conflicts(
    world_parts: dict[str, list[WorldPart]], layout: Layout
) -> list[Conflict]:
    """If the layout designates a maintenance plane, its fuselage centroid
    must lie in the back-most strip of the hangar.

    We union the fuselage *parts* (an aircraft has exactly one fuselage in
    practice, but the model permits multiple — taking the union keeps the
    centroid well-defined either way) and take the y of that union's
    centroid. The bay starts at ``y = hangar.length_m − bay.depth_m``;
    a centroid south of that line (closer to the door) is a violation.

    The threshold is strict ``<`` (not ``<=``): a fuselage centroid
    exactly on the bay boundary counts as parked in the bay. This
    matches the wording in ``CLAUDE.md`` ("must be parked in the back-most
    strip") and prevents a measurement-noise flake when the fuselage is
    tangent to the boundary.
    """
    if layout.maintenance_plane is None:
        return []
    fuselage_parts = [
        p for p in world_parts[layout.maintenance_plane] if p.kind == "fuselage"
    ]
    if not fuselage_parts:
        return []
    fuselage_union = unary_union([p.polygon for p in fuselage_parts])
    bay_start_y = layout.hangar.length_m - layout.hangar.maintenance_bay.depth_m
    centroid_y = fuselage_union.centroid.y
    if centroid_y < bay_start_y:
        return [
            Conflict.single(
                kind="maintenance_position",
                plane=layout.maintenance_plane,
                detail=(
                    f"fuselage centroid y={centroid_y:.3f} is forward of the "
                    f"maintenance bay (starts at y={bay_start_y:.3f})"
                ),
            )
        ]
    return []


def _pairwise_conflicts(
    world_parts: dict[str, list[WorldPart]], hangar: Hangar
) -> list[Conflict]:
    """For every pair of parts from *different* aircraft, emit a conflict
    iff both the plan-view-overlap rule and the z-overlap rule fire.

    The conflict ``kind`` is the two part kinds sorted **alphabetically**
    and joined by ``"_"`` with the ``"_overlap"`` suffix
    (``fuselage + wing`` → ``"fuselage_wing_overlap"``,
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
    """
    out: list[Conflict] = []
    ids = list(world_parts.keys())
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a_id, b_id = ids[i], ids[j]
            for pa in world_parts[a_id]:
                for pb in world_parts[b_id]:
                    if _parts_conflict(pa, pb, hangar):
                        out.append(_build_pairwise_conflict(pa, pb, a_id, b_id, hangar))
    return out


def _parts_conflict(pa: WorldPart, pb: WorldPart, hangar: Hangar) -> bool:
    """Return True iff the two parts conflict per the uniform rule.

    Plan-view: ``polygon_overlap`` with ``hangar.clearance_m`` (which
    splits semantics at zero — see :func:`hangarfit.geometry.polygon_overlap`).

    Height: the gap between the z-ranges is
    ``max(za_bottom, zb_bottom) − min(za_top, zb_top)`` — negative if the
    intervals strictly overlap, zero if they exactly touch, positive if
    they are separated. With ``clearance > 0`` we conflict on ``gap < clearance``
    (so a 0.1 m gap below a 0.2 m clearance still trips); with
    ``clearance == 0`` we conflict only on ``gap < 0`` (strict interior
    overlap, matching the polygon side's "touches isn't a conflict at
    zero clearance" rule).
    """
    if not polygon_overlap(pa.polygon, pb.polygon, clearance=hangar.clearance_m):
        return False
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
    return Conflict.pair(
        kind=kind,
        plane_a=a_id,
        plane_b=b_id,
        detail=(
            f"part {pa.kind!r} (z={pa.z_bottom_m:g}..{pa.z_top_m:g}) and "
            f"part {pb.kind!r} (z={pb.z_bottom_m:g}..{pb.z_top_m:g}) "
            f"within horizontal clearance {hangar.clearance_m:g} m "
            f"and z-gap {gap:g} m (< {hangar.wing_layer_clearance_m:g} m)"
        ),
    )
