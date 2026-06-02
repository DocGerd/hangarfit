"""Property-based (Hypothesis) invariants for the geometry transform, the
collision checker, and the heading short-arc helper (#355, Part A).

The example-based suite (``tests/test_geometry.py``) pins specific hand-picked
headings — the off-by-90°/determinant-−1 trap is caught there at 45°/135°.
*These* tests pin the **structural** invariants over randomized inputs, so a
regression is caught at headings nobody thought to enumerate:

- the plane-local→world map (``local_to_world`` / ``aircraft_parts_world``,
  ADR-0002) is an **area-preserving isometry whose linear part has
  determinant −1** at *every* heading, and is translation-equivariant;
- :func:`hangarfit.collisions.check` is **order-independent** — the conflict
  multiset and ``total_penetration_m2`` do not depend on placement iteration
  order (the ``kind`` taxonomy is alphabetised precisely to guarantee this);
- :func:`hangarfit.solver._heading_delta_short_arc` obeys its documented
  algebra (range ``[0, 180]``, symmetry, 360°-periodicity).

They complement — not replace — the worked-example tests. Hypothesis is
already a dev dependency (used today only by the loader fuzz suite); these put
it to work on the highest-consequence math in the project. ``deadline=None``
is set explicitly so the suite never depends on the ``tests/fuzz`` conftest
profiles being loaded, and a per-example deadline can't flake on a cold CI
runner doing shapely work.
"""

from __future__ import annotations

import math
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from hangarfit.collisions import check as check_layout
from hangarfit.geometry import aircraft_parts_world, local_to_world
from hangarfit.loader import load_layout
from hangarfit.models import Aircraft, Layout, Part, Placement, Wheels
from hangarfit.solver import _heading_delta_short_arc

REPO_ROOT = Path(__file__).resolve().parent.parent

# Bounded, well-conditioned float strategies. Headings deliberately range well
# outside [0, 360) to exercise the sin/cos wrap and the helper's ``% 360``.
headings = st.floats(min_value=-1080.0, max_value=1080.0, allow_nan=False, allow_infinity=False)
coords = st.floats(min_value=-50.0, max_value=50.0, allow_nan=False, allow_infinity=False)
local_coords = st.floats(min_value=-30.0, max_value=30.0, allow_nan=False, allow_infinity=False)
dims = st.floats(min_value=0.1, max_value=20.0, allow_nan=False, allow_infinity=False)
part_angles = st.floats(min_value=-360.0, max_value=360.0, allow_nan=False, allow_infinity=False)


def _placement(x: float, y: float, h: float) -> Placement:
    return Placement(plane_id="probe", x_m=x, y_m=y, heading_deg=h, on_carts=False)


def _probe_aircraft(part: Part) -> Aircraft:
    return Aircraft(
        id="probe",
        name="Probe",
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_own_gear",
        turn_radius_m=5.0,
        measured=False,
        parts=(part,),
        wheels=Wheels(main_offset_x_m=0.0, track_m=1.8, third_wheel_offset_x_m=-2.0),
    )


# ----------------------------------------------------------------------------
# local_to_world — the single canonical per-point transform (#293, ADR-0002).
# ----------------------------------------------------------------------------


class TestLocalToWorldInvariants:
    @settings(max_examples=200, deadline=None)
    @given(u=local_coords, v=local_coords, x=coords, y=coords, h=headings, dx=coords, dy=coords)
    def test_translation_equivariant(
        self, u: float, v: float, x: float, y: float, h: float, dx: float, dy: float
    ) -> None:
        """Shifting the placement origin by ``(dx, dy)`` shifts the world image
        by exactly ``(dx, dy)`` — the translation is applied after the linear
        part, independent of heading."""
        wx0, wy0 = local_to_world(u, v, _placement(x, y, h))
        wx1, wy1 = local_to_world(u, v, _placement(x + dx, y + dy, h))
        assert math.isclose(wx1, wx0 + dx, rel_tol=1e-9, abs_tol=1e-9)
        assert math.isclose(wy1, wy0 + dy, rel_tol=1e-9, abs_tol=1e-9)

    @settings(max_examples=200, deadline=None)
    @given(u1=local_coords, v1=local_coords, u2=local_coords, v2=local_coords, h=headings)
    def test_linear_part_is_an_isometry(
        self, u1: float, v1: float, u2: float, v2: float, h: float
    ) -> None:
        """A rotation-composed-with-reflection preserves distances: the world
        distance between two points equals their plane-local distance, at any
        heading. (Placement at the origin isolates the linear part.)"""
        p = _placement(0.0, 0.0, h)
        wx1, wy1 = local_to_world(u1, v1, p)
        wx2, wy2 = local_to_world(u2, v2, p)
        d_local = math.hypot(u1 - u2, v1 - v2)
        d_world = math.hypot(wx1 - wx2, wy1 - wy2)
        assert math.isclose(d_world, d_local, rel_tol=1e-9, abs_tol=1e-9)

    @settings(max_examples=200, deadline=None)
    @given(h=headings)
    def test_linear_part_determinant_is_minus_one(self, h: float) -> None:
        """🔬 The determinant-−1 guard, pinned over *all* headings rather than
        the hand-picked ones in test_geometry.py.

        With the placement at the origin, the world images of the plane-local
        basis vectors ``(1, 0)`` and ``(0, 1)`` are the columns of the linear
        map. Its determinant must be exactly −1 (a pure rotation would give
        +1) — the algebraic statement of the "do not 'fix' it to a det=+1
        rotation" warning in ADR-0002.
        """
        p = _placement(0.0, 0.0, h)
        ax, ay = local_to_world(1.0, 0.0, p)  # image of plane-local +forward
        bx, by = local_to_world(0.0, 1.0, p)  # image of plane-local +right
        det = ax * by - ay * bx
        assert math.isclose(det, -1.0, rel_tol=1e-9, abs_tol=1e-9)


# ----------------------------------------------------------------------------
# aircraft_parts_world — the part-level transform built on local_to_world.
# ----------------------------------------------------------------------------


class TestAircraftPartsWorldInvariants:
    @settings(max_examples=150, deadline=None)
    @given(
        length=dims,
        width=dims,
        angle=part_angles,
        ox=local_coords,
        oy=local_coords,
        x=coords,
        y=coords,
        h=headings,
    )
    def test_area_preserved_at_any_heading(
        self,
        length: float,
        width: float,
        angle: float,
        ox: float,
        oy: float,
        x: float,
        y: float,
        h: float,
    ) -> None:
        """A det-magnitude-1 map preserves area: the transformed part polygon
        has area ``length × width`` regardless of placement heading, the
        part's own in-plane ``angle_deg``, or its offset."""
        part = Part(
            kind="wing",
            length_m=length,
            width_m=width,
            offset_x_m=ox,
            offset_y_m=oy,
            angle_deg=angle,
            z_bottom_m=1.0,
            z_top_m=2.0,
        )
        [world] = aircraft_parts_world(_probe_aircraft(part), _placement(x, y, h))
        assert math.isclose(world.polygon.area, length * width, rel_tol=1e-9, abs_tol=1e-9)

    @settings(max_examples=150, deadline=None)
    @given(
        length=dims,
        width=dims,
        angle=part_angles,
        x=coords,
        y=coords,
        h=headings,
        dx=coords,
        dy=coords,
    )
    def test_polygon_translation_equivariant(
        self,
        length: float,
        width: float,
        angle: float,
        x: float,
        y: float,
        h: float,
        dx: float,
        dy: float,
    ) -> None:
        """Translating the placement translates every world vertex by the same
        vector (centroid is a faithful proxy, being affine-equivariant)."""
        part = Part(
            kind="fuselage_aft",
            length_m=length,
            width_m=width,
            offset_x_m=0.0,
            offset_y_m=0.0,
            angle_deg=angle,
            z_bottom_m=0.0,
            z_top_m=1.0,
        )
        ac = _probe_aircraft(part)
        [w0] = aircraft_parts_world(ac, _placement(x, y, h))
        [w1] = aircraft_parts_world(ac, _placement(x + dx, y + dy, h))
        assert math.isclose(w1.polygon.centroid.x, w0.polygon.centroid.x + dx, abs_tol=1e-6)
        assert math.isclose(w1.polygon.centroid.y, w0.polygon.centroid.y + dy, abs_tol=1e-6)


# ----------------------------------------------------------------------------
# _heading_delta_short_arc — pure angular helper (solver spec §4.6).
# ----------------------------------------------------------------------------


class TestHeadingDeltaShortArc:
    @settings(max_examples=300, deadline=None)
    @given(a=headings, b=headings)
    def test_range_and_symmetry(self, a: float, b: float) -> None:
        """Result is always in [0, 180] and symmetric in its arguments."""
        d = _heading_delta_short_arc(a, b)
        assert 0.0 <= d <= 180.0 + 1e-9
        assert math.isclose(d, _heading_delta_short_arc(b, a), rel_tol=1e-9, abs_tol=1e-9)

    @settings(max_examples=300, deadline=None)
    @given(a=headings, b=headings, ka=st.integers(-5, 5), kb=st.integers(-5, 5))
    def test_360_periodic_in_each_argument(self, a: float, b: float, ka: int, kb: int) -> None:
        """Adding a whole turn to either argument leaves the short arc
        unchanged — the contract that keeps the diversity filter from
        mis-reading 1° vs 359° as a 358° move."""
        base = _heading_delta_short_arc(a, b)
        shifted = _heading_delta_short_arc(a + 360.0 * ka, b + 360.0 * kb)
        assert math.isclose(base, shifted, rel_tol=1e-9, abs_tol=1e-7)

    @settings(max_examples=200, deadline=None)
    @given(a=headings)
    def test_self_is_zero_and_antipode_is_180(self, a: float) -> None:
        assert math.isclose(_heading_delta_short_arc(a, a), 0.0, abs_tol=1e-9)
        assert math.isclose(_heading_delta_short_arc(a, a + 180.0), 180.0, abs_tol=1e-7)


# ----------------------------------------------------------------------------
# collisions.check — order independence.
# ----------------------------------------------------------------------------

_BASE_LAYOUT = load_layout(REPO_ROOT / "tests" / "fixtures" / "valid_all_nine_planes.yaml")
_N = len(_BASE_LAYOUT.placements)
_perturb = st.floats(min_value=-4.0, max_value=4.0, allow_nan=False, allow_infinity=False)


class TestCheckOrderIndependence:
    @settings(max_examples=75, deadline=None)
    @given(
        perturb=st.lists(st.tuples(_perturb, _perturb, headings), min_size=_N, max_size=_N),
        perm=st.permutations(list(range(_N))),
    )
    def test_check_invariant_under_placement_reordering(
        self, perturb: list[tuple[float, float, float]], perm: list[int]
    ) -> None:
        """🔬 The conflict multiset and total penetration are independent of the
        order placements are listed in. The ``kind`` taxonomy is alphabetised
        in ``_pairwise_conflicts`` specifically so that swapping plane_a/plane_b
        cannot change a conflict's kind — this pins that guarantee end-to-end.

        Planes are nudged by Hypothesis-drawn offsets so examples span the full
        range from fully-disjoint (zero conflicts) to heavily-overlapping; the
        invariant must hold across all of them. Reordering preserves on_carts
        and the maintenance plane, so every constructed Layout stays valid.
        """
        moved = tuple(
            Placement(
                plane_id=p.plane_id,
                x_m=p.x_m + dx,
                y_m=p.y_m + dy,
                heading_deg=p.heading_deg + dh,
                on_carts=p.on_carts,
            )
            for p, (dx, dy, dh) in zip(_BASE_LAYOUT.placements, perturb, strict=True)
        )
        reordered = tuple(moved[i] for i in perm)

        def _result(placements: tuple[Placement, ...]) -> tuple[int, list[str], float]:
            layout = Layout(
                fleet=_BASE_LAYOUT.fleet,
                hangar=_BASE_LAYOUT.hangar,
                placements=placements,
                maintenance_plane=_BASE_LAYOUT.maintenance_plane,
            )
            res = check_layout(layout)
            return (
                len(res.conflicts),
                sorted(c.kind for c in res.conflicts),
                res.total_penetration_m2,
            )

        n0, kinds0, pen0 = _result(moved)
        n1, kinds1, pen1 = _result(reordered)
        assert n0 == n1
        assert kinds0 == kinds1
        assert math.isclose(pen0, pen1, rel_tol=1e-9, abs_tol=1e-9)
