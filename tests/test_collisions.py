"""Golden tests for ``hangarfit.collisions.check``.

Each fixture lives in ``tests/fixtures/{name}.yaml`` and uses the
bundled ``data/fleet.yaml`` / ``data/hangar.yaml``; tests load via
:func:`hangarfit.loader.load_layout` and exercise
:func:`hangarfit.collisions.check`.

The 12 cases match the issue body. Cases 6–8 are the strut-aware canaries
that justify the parts model — if those regress, the checker has dropped
to bbox-style logic and downstream layouts will be silently wrong.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hangarfit.collisions import check
from hangarfit.loader import LoaderError, load_layout
from hangarfit.models import CheckResult

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _load(name: str):
    return load_layout(FIXTURES_DIR / f"{name}.yaml")


def _conflict_kinds(result: CheckResult) -> set[str]:
    return {c.kind for c in result.conflicts}


class TestHangarBounds:
    """Case 11 — every world part vertex must lie inside the hangar rectangle."""

    def test_case_11_wing_extends_past_left_wall(self) -> None:
        result = check(_load("invalid_hangar_bounds"))
        assert not result.valid
        assert "hangar_bounds" in _conflict_kinds(result), (
            f"expected hangar_bounds conflict, got {result.conflicts!r}"
        )

    def test_vertex_at_hangar_wall_exactly_is_valid(self) -> None:
        """The bounds check is inclusive (``0 <= x <= width_m``). A vertex
        landing exactly at the wall must not trip the rule. Guards
        against a future tightening to strict ``<``."""
        result = check(_load("valid_wall_vertex"))
        assert result.valid, (
            f"vertex at x=0 must pass the inclusive bounds check, "
            f"got conflicts: {result.conflicts!r}"
        )


class TestPairwiseOverlap:
    """Cases 2-5 — pairwise parts-overlap conflicts.

    The collision rule (``CLAUDE.md``) is: two parts from *different*
    aircraft conflict iff they overlap both in plan view (within
    horizontal clearance) AND in height (within vertical clearance).
    """

    def test_case_2_wing_wing_overlap_same_height(self) -> None:
        result = check(_load("invalid_wing_wing_same_height"))
        assert not result.valid
        assert "wing_wing_overlap" in _conflict_kinds(result), (
            f"expected wing_wing_overlap, got {result.conflicts!r}"
        )

    def test_case_3_high_wing_over_low_fuselage_aft_z_disjoint_valid(self) -> None:
        """Plan-view overlap with the AFT fuselage at z-disjoint must NOT
        trigger a conflict.

        Reframed for #50 / ADR-0012: the fuselage front/aft split makes
        *where* over the fuselage matter. A wing over the AFT fuselage / tail
        keeps the uniform two-clause rule (clearance in BOTH plan view AND
        height), so this stays valid — it is the headline positive control
        for the split. (A wing over the FRONT / cockpit is a hard conflict
        regardless of z — see ``test_wing_over_cockpit_*`` below.)

        A bbox-style implementation would flag this; the parts model rule
        lets it pass.
        """
        result = check(_load("valid_high_over_low_aft_z_disjoint"))
        assert result.valid, f"unexpected conflicts: {result.conflicts}"

    def test_case_4_fuselage_wing_overlap_alphabetical_kind(self) -> None:
        """Heterogeneous-kind pair: alphabetical sort must yield
        ``fuselage_{aft,front}_wing_overlap`` (NOT ``wing_fuselage_*``)
        regardless of plane iteration order.

        Re-pinned for #50 / ADR-0012: the fuselage split replaces the old
        single ``fuselage_wing_overlap`` kind with the two segment-specific
        kinds. Fuji's low wing overlaps scheibe's fuselage at a colliding
        height; the overlap now spans both scheibe segments, so both
        ``fuselage_front_wing_overlap`` and ``fuselage_aft_wing_overlap``
        fire. Still invalid; the legacy un-split kind must NOT appear."""
        result = check(_load("invalid_fuselage_wing_overlap"))
        assert not result.valid
        kinds = _conflict_kinds(result)
        assert "fuselage_front_wing_overlap" in kinds, (
            f"expected fuselage_front_wing_overlap, got {result.conflicts!r}"
        )
        assert "fuselage_aft_wing_overlap" in kinds, (
            f"expected fuselage_aft_wing_overlap, got {result.conflicts!r}"
        )
        # Alphabetical-order guard: no reversed forms, and the retired
        # un-split kind must never reappear.
        assert "wing_fuselage_front_overlap" not in kinds
        assert "wing_fuselage_aft_overlap" not in kinds
        assert "fuselage_wing_overlap" not in kinds, (
            f"retired un-split kind leaked into conflicts: {result.conflicts!r}"
        )

    def test_case_5_fuselage_fuselage_overlap(self) -> None:
        """Two fuselages overlapping → still invalid, but the kind set + count
        change with the front/aft split.

        Re-pinned for #50 / ADR-0012: ctsl and fuji fuselages overlap. The
        single legacy ``fuselage_fuselage_overlap`` splits into the
        segment-pair kinds — here the overlap zone spans ctsl's aft × fuji's
        front and ctsl's aft × fuji's aft, so two conflicts fire
        (``fuselage_aft_fuselage_aft_overlap`` and
        ``fuselage_aft_fuselage_front_overlap``). The *verdict* is unchanged
        (two overlapping fuselages is a conflict regardless of segment, since
        they share a z-band); only the taxonomy + count move. The retired
        un-split kind must never reappear."""
        result = check(_load("invalid_fuselage_fuselage"))
        assert not result.valid
        assert _conflict_kinds(result) == {
            "fuselage_aft_fuselage_aft_overlap",
            "fuselage_aft_fuselage_front_overlap",
        }, f"expected segment-pair fuselage kinds, got {result.conflicts!r}"
        assert len(result.conflicts) == 2, (
            f"expected exactly 2 conflicts, got {len(result.conflicts)}: {result.conflicts!r}"
        )
        assert "fuselage_fuselage_overlap" not in _conflict_kinds(result), (
            f"retired un-split kind leaked into conflicts: {result.conflicts!r}"
        )


class TestWingOverFuselageSegment:
    """#50 / ADR-0012 — the fuselage front/aft split and its D1 rule.

    A wing over another plane's ``fuselage_front`` (cockpit) is a HARD
    conflict regardless of the height gap (D1: z ignored). A wing over its
    ``fuselage_aft`` (tail) keeps the uniform two-clause z-gap rule. The two
    fixtures differ only in which segment the overlap lands on (the cleanest
    front/aft demonstration), mirroring the case-7/8 left/right idiom.
    """

    def test_wing_over_cockpit_is_hard_conflict_despite_z_gap(self) -> None:
        """Wing over ``fuselage_front`` at a z-DISJOINT height (the OLD rule
        would have passed it) must still fire exactly one
        ``fuselage_front_wing_overlap`` — the canary that D1 bites."""
        result = check(_load("invalid_wing_over_cockpit"))
        assert not result.valid
        assert _conflict_kinds(result) == {"fuselage_front_wing_overlap"}, (
            f"expected exactly fuselage_front_wing_overlap, got {result.conflicts!r}"
        )
        assert len(result.conflicts) == 1, (
            f"expected exactly one conflict, got {result.conflicts!r}"
        )

    def test_wing_over_tail_at_same_height_is_valid(self) -> None:
        """The same wing over ``fuselage_aft`` at the same z-disjoint height
        is valid — the aft region keeps the z-gap rule."""
        result = check(_load("valid_wing_over_tail"))
        assert result.valid, (
            f"wing over fuselage_aft at z-disjoint height must be valid, "
            f"got conflicts: {result.conflicts!r}"
        )

    def test_fuselage_front_wing_kind_is_alphabetical(self) -> None:
        """The conflict kind is the two part kinds sorted alphabetically:
        ``fuselage_front`` < ``wing`` ⇒ ``fuselage_front_wing_overlap``, NOT
        the reversed ``wing_fuselage_front_overlap`` (mirrors case-4's
        alphabetical-order guard)."""
        result = check(_load("invalid_wing_over_cockpit"))
        kinds = _conflict_kinds(result)
        assert "fuselage_front_wing_overlap" in kinds
        assert "wing_fuselage_front_overlap" not in kinds, (
            f"non-alphabetical kind leaked into conflicts: {result.conflicts!r}"
        )


class TestValidLayouts:
    """Cases 1 & 12 — layouts that must produce zero conflicts.

    Case 1 is a minimal positive control: two cantilever high-wings
    parked with comfortable separation. If this fails, the checker is
    emitting false positives somewhere fundamental.

    Case 12 is the full-fleet acceptance smoke test — all 9 placeholder
    aircraft placed plausibly in the placeholder hangar.
    """

    def test_case_1_two_high_wings_well_separated(self) -> None:
        result = check(_load("valid_two_separated"))
        assert result.valid, f"two-plane layout must be clean, got conflicts: {result.conflicts!r}"

    def test_case_12_all_nine_planes_valid(self) -> None:
        """Phase 1 acceptance smoke test: all 9 placeholder aircraft fit.

        Uses a test-only larger hangar (``test_hangar_large.yaml``) because
        the placeholder ``data/hangar.yaml`` dimensions don't accommodate
        the placeholder fleet's strut-bracing constraints. See the comment
        block at the top of ``valid_all_nine_planes.yaml`` for the
        full rationale.
        """
        result = check(_load("valid_all_nine_planes"))
        assert result.valid, f"9-plane layout must be clean, got conflicts: {result.conflicts!r}"


class TestCartRule:
    """Case 10 — cart rule rejection happens at *load time*, not in ``check()``.

    ``Layout.__post_init__`` enforces "at most one cart_eligible plane has
    on_carts=True" so by the time a Layout reaches the collision checker
    the rule has already been satisfied. The case-10 fixture violates the
    rule deliberately and the test asserts the loader rejects it before
    the checker can run.
    """

    def test_case_10_two_cart_eligible_on_carts_rejected_at_load(self) -> None:
        with pytest.raises(LoaderError, match=r"At most 1 cart_eligible"):
            _load("invalid_cart_rule")


class TestStrutCanary:
    """Cases 6-8 — the strut-aware canary trio.

    These cases distinguish the parts model from a naïve bbox model:
    a strut occupies a thin column at the wing's underside height band,
    so a plane's wing can nest under another plane's wing only where
    there is no strut in plan view. If the checker regresses to a
    single-bbox-per-aircraft model, case 6 stops reporting a conflict
    (the strut disappears from the world) and cases 7/8 flip to
    reporting wing/wing as a conflict (z-disjoint nesting is lost).
    """

    def test_case_6_strut_blocks_under_wing_nesting(self) -> None:
        result = check(_load("invalid_strut_blocks_nesting"))
        assert not result.valid
        kinds = _conflict_kinds(result)
        assert "strut_wing_overlap" in kinds, (
            f"strut canary failed: expected strut_wing_overlap, got {result.conflicts!r}"
        )
        assert "wing_strut_overlap" not in kinds, (
            f"non-alphabetical kind leaked into conflicts: {result.conflicts!r}"
        )

    def test_case_7_strut_free_right_side_nesting_valid(self) -> None:
        result = check(_load("valid_right_side_nesting"))
        assert result.valid, (
            f"right-side nesting must be valid (z-disjoint), got conflicts: {result.conflicts!r}"
        )

    def test_case_8_strut_free_left_side_nesting_valid(self) -> None:
        result = check(_load("valid_left_side_nesting"))
        assert result.valid, (
            f"left-side nesting must be valid (z-disjoint), got conflicts: {result.conflicts!r}"
        )


class TestBayIntrusion:
    """The ``bay_intrusion`` perimeter rule fires when the bay is closed
    (``layout.maintenance_plane is not None``) and any non-occupant part
    has a vertex strictly inside the bay rectangle. Strict ``<`` on the
    left, right, and front edges; the back edge coincides with the
    hangar wall and uses the same inclusive convention as
    :func:`_hangar_bounds_conflicts`.
    """

    def _build_layout(
        self,
        *,
        bay_open: bool,
        intruder_offset_x_m: float = 0.0,
        intruder_offset_y_m: float = 0.0,
    ):
        """Construct a minimal 1-plane layout with one wing-only intruder
        positioned by `intruder_offset_*` relative to a known reference."""
        from hangarfit.models import (
            Aircraft,
            Door,
            Hangar,
            Layout,
            MaintenanceBay,
            Part,
            Placement,
            Wheels,
        )

        # 1×1 m wing at z=2..2.3 — a single-rectangle part lets us reason
        # about its world vertices directly.
        small_wing = Aircraft(
            id="probe",
            name="Probe",
            wing_position="high",
            gear="tailwheel",
            movement_mode="always_own_gear",
            turn_radius_m=5.0,
            measured=False,
            parts=(
                Part(
                    kind="wing",
                    length_m=1.0,
                    width_m=1.0,
                    offset_x_m=0.0,
                    offset_y_m=0.0,
                    angle_deg=0.0,
                    z_bottom_m=2.0,
                    z_top_m=2.3,
                ),
            ),
            wheels=Wheels(main_offset_x_m=0.0, track_m=1.8, third_wheel_offset_x_m=-2.0),
        )
        # Bay: x ∈ (10, 18), y ∈ (16, 25].
        hangar = Hangar(
            length_m=25.0,
            width_m=18.0,
            door=Door(center_x_m=9.0, width_m=12.0),
            maintenance_bay=MaintenanceBay(center_x_m=14.0, width_m=8.0, depth_m=9.0),
            clearance_m=0.3,
            wing_layer_clearance_m=0.2,
        )
        # Anchor: bay's deepest-interior point (x=14, y=20). The intruder
        # is centered there; callers shift via the offset args.
        probe = Placement(
            plane_id="probe",
            x_m=14.0 + intruder_offset_x_m,
            y_m=20.0 + intruder_offset_y_m,
            heading_deg=0.0,
            on_carts=False,
        )
        # Add an "occupant" aircraft to the fleet so a non-None
        # maintenance_plane is legal. It is NEVER in placements — the
        # Layout invariant + our test reads exclusively on bay rule.
        occupant = Aircraft(
            id="occupant",
            name="Occupant",
            wing_position="high",
            gear="monowheel",
            movement_mode="always_cart",
            turn_radius_m=None,
            measured=False,
            parts=(
                Part(
                    # fuselage_aft is a valid constructed kind (the legacy
                    # un-split "fuselage" is loader-only now — #50/ADR-0012);
                    # this occupant is only used for its bay geometry, so the
                    # segment kind is immaterial here.
                    kind="fuselage_aft",
                    length_m=1.0,
                    width_m=1.0,
                    offset_x_m=0.0,
                    offset_y_m=0.0,
                    angle_deg=0.0,
                    z_bottom_m=0.0,
                    z_top_m=1.0,
                ),
            ),
            wheels=Wheels(main_offset_x_m=0.0, track_m=None, third_wheel_offset_x_m=None),
        )
        return Layout(
            fleet={"probe": small_wing, "occupant": occupant},
            hangar=hangar,
            placements=(probe,),
            maintenance_plane=None if bay_open else "occupant",
        )

    def test_open_bay_never_fires(self) -> None:
        """When ``maintenance_plane is None``, the bay rectangle is just
        floor; a part centered deep in the would-be bay must pass."""
        layout = self._build_layout(bay_open=True)
        result = check(layout)
        assert "bay_intrusion" not in _conflict_kinds(result), (
            f"open bay must not fire bay_intrusion, got {result.conflicts!r}"
        )

    def test_closed_bay_flags_deep_intrusion(self) -> None:
        layout = self._build_layout(bay_open=False)
        result = check(layout)
        assert "bay_intrusion" in _conflict_kinds(result), (
            f"part centered inside closed bay must fire bay_intrusion, got {result.conflicts!r}"
        )

    def test_left_edge_vertex_strictly_outside_passes(self) -> None:
        """Bay x_min=10. A 1×1 wing centered at (9.5, 20) has its
        right edge at x=10 exactly — on the boundary, not strictly
        inside. Must pass."""
        layout = self._build_layout(
            bay_open=False, intruder_offset_x_m=-4.5
        )  # center at x=9.5, right vertex at x=10
        result = check(layout)
        assert "bay_intrusion" not in _conflict_kinds(result), (
            f"vertex on x_min edge counts as outside; got {result.conflicts!r}"
        )

    def test_left_edge_sub_epsilon_inside_fires(self) -> None:
        """One µm past the left edge must trip; locks in strict ``<``."""
        layout = self._build_layout(
            bay_open=False, intruder_offset_x_m=-4.499999
        )  # right vertex at x = 10.000001 (just inside)
        result = check(layout)
        assert "bay_intrusion" in _conflict_kinds(result), (
            f"vertex 1 µm inside left edge must fire; got {result.conflicts!r}"
        )

    def test_front_edge_vertex_on_boundary_passes(self) -> None:
        """Bay y_min=16. A 1×1 wing centered at (14, 15.5) has its
        rear edge at y=16 exactly — on the boundary, not strictly
        inside. Must pass."""
        layout = self._build_layout(bay_open=False, intruder_offset_y_m=-4.5)  # rear vertex at y=16
        result = check(layout)
        assert "bay_intrusion" not in _conflict_kinds(result), (
            f"vertex on y_min edge counts as outside; got {result.conflicts!r}"
        )

    def test_front_edge_sub_epsilon_inside_fires(self) -> None:
        """One µm past the front edge must trip; locks in strict ``<``."""
        layout = self._build_layout(
            bay_open=False, intruder_offset_y_m=-4.499999
        )  # rear vertex at y = 16.000001
        result = check(layout)
        assert "bay_intrusion" in _conflict_kinds(result), (
            f"vertex 1 µm inside front edge must fire; got {result.conflicts!r}"
        )

    def test_back_edge_at_hangar_wall_counts_as_inside(self) -> None:
        """The bay's back edge coincides with the hangar's outer wall.
        A vertex at y=length_m=25 sits on the hangar boundary
        (inclusive per hangar_bounds) AND must be treated as inside
        the closed bay (no separate back-edge test)."""
        # Wing centered at (14, 24.5) → rear-most vertex at y=25
        # (exactly on the hangar back wall), front vertex at y=24.
        # Both are strictly inside the bay (y > 16 and within x range).
        layout = self._build_layout(bay_open=False, intruder_offset_y_m=4.5)
        result = check(layout)
        assert "bay_intrusion" in _conflict_kinds(result), (
            f"vertex on the hangar back wall must count as inside the "
            f"closed bay; got {result.conflicts!r}"
        )

    def test_corner_vertex_strictly_inside_fires(self) -> None:
        """An intruder straddling the bay's front-left corner: the
        center is at (11, 16.5), so the part's front-left vertex
        (10.5, 16) sits exactly on the front edge (passes the strict
        check) while the rear-left vertex (10.5, 17) is strictly
        inside both ``x_min < x < x_max`` and ``y > y_min``. The
        per-part check must find the strictly-inside vertex even when
        another vertex of the same part is tangent.
        """
        layout = self._build_layout(
            bay_open=False, intruder_offset_x_m=-3.0, intruder_offset_y_m=-3.5
        )
        result = check(layout)
        assert "bay_intrusion" in _conflict_kinds(result)

    def test_retired_conflict_kinds_never_emitted(self) -> None:
        """The legacy ``maintenance_position`` and
        ``maintenance_no_fuselage`` kinds are retired. The new rule
        must never emit them."""
        layout = self._build_layout(bay_open=False)
        result = check(layout)
        kinds = _conflict_kinds(result)
        assert "maintenance_position" not in kinds
        assert "maintenance_no_fuselage" not in kinds

    # ── End-to-end fixture goldens (loader → checker contract) ──────────

    def test_fixture_bay_closed_no_intruder_valid(self) -> None:
        """Closed bay, all placed planes well clear of the bay rectangle."""
        result = check(_load("valid_bay_closed_no_intruder"))
        assert result.valid, f"clear layout must pass with closed bay, got {result.conflicts!r}"

    def test_fixture_bay_open_planes_in_back_strip_valid(self) -> None:
        """Open bay (maintenance_plane=None) — a plane parked inside the
        area that WOULD be the bay must still pass; the rule is a no-op."""
        layout = _load("valid_bay_open_planes_in_back_strip")
        assert layout.maintenance_plane is None
        result = check(layout)
        assert result.valid, (
            f"open bay must not fire bay_intrusion against a plane in "
            f"the back strip, got {result.conflicts!r}"
        )

    def test_fixture_partial_width_bay_plane_in_side_aisle_valid(self) -> None:
        """Closed bay, plane parked in the side aisle (back strip, but
        outside the bay x range). Asserts partial-width semantics — the
        side aisle remains usable even when the bay is closed.

        Beyond ``result.valid``, this also pins the geometric intent:
        at least one part must have a vertex with ``y > y_min`` (i.e.
        in the back strip) AND ``x < x_min`` (in the side aisle). A
        future fixture drift that pushed the plane forward of the
        back strip would still pass ``result.valid`` but would no
        longer exercise the partial-width path.
        """
        from hangarfit.geometry import aircraft_parts_world

        layout = _load("valid_partial_width_bay_plane_in_side_aisle")
        result = check(layout)
        assert result.valid, (
            f"side-aisle layout must pass with closed partial-width bay, got {result.conflicts!r}"
        )
        bay = layout.hangar.maintenance_bay
        x_min = bay.center_x_m - bay.width_m / 2
        y_min = layout.hangar.length_m - bay.depth_m
        in_side_aisle = any(
            vx < x_min and vy > y_min
            for placement in layout.placements
            for wp in aircraft_parts_world(layout.fleet[placement.plane_id], placement)
            for vx, vy in list(wp.polygon.exterior.coords)[:-1]
        )
        assert in_side_aisle, (
            "fixture must place at least one vertex in the side aisle "
            "(x < x_min and y > y_min); otherwise it no longer exercises "
            "the partial-width semantics it documents"
        )

    def test_fixture_bay_intrusion_wingtip_invalid(self) -> None:
        """A wingtip vertex strictly inside the closed bay must trip
        exactly one ``bay_intrusion`` conflict."""
        result = check(_load("invalid_bay_intrusion_wingtip"))
        assert not result.valid
        assert _conflict_kinds(result) == {"bay_intrusion"}, (
            f"expected exactly bay_intrusion, got {result.conflicts!r}"
        )
        assert len(result.conflicts) == 1, (
            f"expected exactly one offending part, got {result.conflicts!r}"
        )

    def test_fixture_part_vertex_on_bay_edge_valid(self) -> None:
        """A vertex landing exactly on a bay edge (``x == x_min``) must
        NOT trip the rule. Guards the strict-inequality semantics at
        the fixture level (the synthetic unit tests do the same at the
        function level).

        Beyond ``result.valid``, this also pins the on-edge property:
        at least one part must have a vertex with ``abs(x - x_min)``
        below floating-point tolerance. If a future fixture drift
        moved the plane off the edge by a few cm, ``result.valid``
        would still hold but the fixture would no longer guard the
        strict-vs-non-strict boundary case.
        """
        import math

        from hangarfit.geometry import aircraft_parts_world

        layout = _load("valid_part_vertex_on_bay_edge")
        result = check(layout)
        assert result.valid, f"vertex on bay edge must count as outside; got {result.conflicts!r}"
        bay = layout.hangar.maintenance_bay
        x_min = bay.center_x_m - bay.width_m / 2
        on_edge = any(
            math.isclose(vx, x_min, abs_tol=1e-9)
            for placement in layout.placements
            for wp in aircraft_parts_world(layout.fleet[placement.plane_id], placement)
            for vx, _vy in list(wp.polygon.exterior.coords)[:-1]
        )
        assert on_edge, (
            "fixture must place at least one part vertex at x = x_min "
            "exactly; otherwise it no longer exercises the strict-< edge "
            "case it documents"
        )

    def test_defensive_skip_protects_against_occupant_leak(self) -> None:
        """If the maintenance occupant ever leaks into ``world_parts``
        (would require a Layout-invariant bypass), the rule must skip
        it rather than emit "occupant intrudes into its own bay"
        nonsense. Exercises the defensive ``if plane_id ==
        layout.maintenance_plane: continue`` guard by calling the
        private rule with a hand-built world_parts dict whose key is
        the maintenance plane id.
        """
        from hangarfit.collisions import _bay_intrusion_conflicts
        from hangarfit.geometry import aircraft_parts_world
        from hangarfit.models import Placement

        # A valid (occupant-absent) Layout to supply the bay geometry
        # and maintenance_plane name.
        layout = self._build_layout(bay_open=False)
        # Hand-build world_parts WITH the occupant — bypassing the
        # Layout invariant that would normally make this state
        # unreachable. Place the occupant deep inside the bay so its
        # fuselage vertex would trigger an intrusion if the skip
        # weren't there.
        occupant_placement = Placement(
            plane_id="occupant", x_m=14.0, y_m=20.0, heading_deg=0.0, on_carts=True
        )
        leaked_world_parts = {
            "occupant": aircraft_parts_world(layout.fleet["occupant"], occupant_placement)
        }
        conflicts = _bay_intrusion_conflicts(leaked_world_parts, layout)
        assert conflicts == [], (
            f"defensive skip failed: occupant emitted bay_intrusion against "
            f"its own bay; got {conflicts!r}"
        )


class TestTotalPenetration:
    """Behavioral tests for ``CheckResult.total_penetration_m2``.

    Penetration is the summed shapely ``intersection().area`` across pairwise
    conflicts, used by the Phase 2a solver as a smooth tie-breaker on top of
    the integer conflict count. These tests pin:

    1. the exact value for a single-pair overlap (axis-aligned, deterministic),
    2. the sum semantic across multiple pair-collisions in one layout,
    3. the zero-on-valid-layout contract,
    4. the "single-plane conflicts contribute 0" rule from
       :func:`hangarfit.collisions._pairwise_conflicts`'s docstring.
    """

    def test_exact_value_for_wing_wing_plus_cockpit_overlaps(self) -> None:
        """Deterministic penetration total for ``invalid_wing_wing_same_height``.

        Golden re-baselined 4.0373 → 5.54045 m² for #50 / ADR-0012. The two
        close, same-height high-wings overlap wing-to-wing AND each wing now
        crosses the OTHER plane's ``fuselage_front`` (cockpit) — a hard
        conflict the split introduced (rule changed by #50 — re-pinned). The
        old comment "the only conflict is the wing-wing one" no longer holds:
        when two high-wings' wings cross, at least one passes over the other's
        cockpit. The accumulator (``+=`` over every pairwise conflict's
        intersection area) is what this still pins."""
        layout = _load("invalid_wing_wing_same_height")
        result = check(layout)

        assert not result.valid
        assert result.total_penetration_m2 == pytest.approx(5.54045, abs=1e-4)

    def test_sums_across_multiple_pair_conflicts(self) -> None:
        """5 pairwise conflicts in ``invalid_strut_blocks_nesting`` should
        sum to the deterministic 2.85 m² total — pins the ``+=``
        accumulator semantic against future refactors to ``=``, ``max``,
        or ``mean``.

        Golden history:
          - 1.4305 m² originally.
          - 1.35 m² for issue #282 (struts moved to the wing-spar
            quarter-chord), 3 conflicts (1 fuselage_wing + 2 strut_wing).
          - 2.85 m², 5 conflicts for #50 / ADR-0012 (rule changed by #50 —
            re-pinned): the single ``fuselage_wing_overlap`` splits into
            ``fuselage_front_wing_overlap`` (×2) + ``fuselage_aft_wing_overlap``
            (×1) as Fuji's wing crosses both of Cessna's fuselage segments,
            plus the unchanged 2 strut_wing. The canary's intent (struts block
            the nesting) is preserved; the summed intersection area grows
            because the front-segment overlaps now count even though they are
            z-disjoint (D1)."""
        layout = _load("invalid_strut_blocks_nesting")
        result = check(layout)

        assert len(result.conflicts) == 5
        assert result.total_penetration_m2 == pytest.approx(2.85, abs=1e-4)

    def test_zero_for_valid_layout(self) -> None:
        layout = _load("valid_two_separated")
        result = check(layout)

        assert result.valid
        assert result.total_penetration_m2 == 0.0

    def test_single_plane_conflicts_contribute_zero(self) -> None:
        layout = _load("invalid_hangar_bounds")
        result = check(layout)

        assert not result.valid
        # Every conflict here is single-plane (hangar_bounds).
        assert all(len(c.planes) == 1 for c in result.conflicts)
        assert result.total_penetration_m2 == 0.0


def _exact_pairwise_no_broadphase(
    world_parts: dict[str, list],
    hangar,  # noqa: ANN001 (test-local reference)
):
    """Reference implementation of the pre-#454 pairwise loop: every cross-plane
    part pair goes straight to the exact predicate with NO AABB broad-phase.

    Mirrors :func:`hangarfit.collisions._pairwise_conflicts` minus the #454
    filter, so any divergence between this and the filtered production loop is
    precisely an over- or under-skip by the broad-phase.
    """
    from hangarfit.collisions import _build_pairwise_conflict, _parts_conflict
    from hangarfit.geometry import polygon_overlap_area

    out = []
    pen = 0.0
    ids = list(world_parts.keys())
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a_id, b_id = ids[i], ids[j]
            for pa in world_parts[a_id]:
                for pb in world_parts[b_id]:
                    if _parts_conflict(pa, pb, hangar):
                        out.append(_build_pairwise_conflict(pa, pb, a_id, b_id, hangar))
                        pen += polygon_overlap_area(pa.polygon, pb.polygon)
    return out, pen


class TestBroadPhaseEquivalence:
    """#454 — the AABB broad-phase pre-filter in ``_pairwise_conflicts`` is a
    pure optimization: a per-axis box gap is a provable lower bound on the true
    polygon edge-to-edge distance, so it skips only pairs the exact predicate
    would also reject. ``_pairwise_conflicts`` must therefore stay byte-identical
    to the unfiltered exact loop — same ``Conflict`` tuple, same
    ``total_penetration_m2`` float (ADR-0003).

    Each scenario is checked against :func:`_exact_pairwise_no_broadphase` on the
    *same* ``world_parts`` (so the only variable is the filter). The 35° heading
    cases are load-bearing: an oriented rectangle's AABB is strictly larger than
    the rectangle (ADR-0002's non-axis-aligned requirement), so the filter must
    NOT skip a pair whose boxes overlap while the polygons do not actually
    conflict — it must defer to the exact predicate — and must still catch a
    genuine overlap whose boxes are close. The clearance-gap case pins the
    threshold: a pair with a sub-clearance box gap must survive the filter.
    """

    @staticmethod
    def _hangar(clearance: float = 0.3, wlc: float = 0.2):
        from hangarfit.models import Door, Hangar, MaintenanceBay

        return Hangar(
            length_m=40.0,
            width_m=40.0,
            door=Door(center_x_m=20.0, width_m=12.0),
            maintenance_bay=MaintenanceBay(center_x_m=20.0, width_m=8.0, depth_m=6.0),
            clearance_m=clearance,
            wing_layer_clearance_m=wlc,
        )

    @staticmethod
    def _wing_plane(pid: str):
        """A single thin high-wing (0.5 m chord × 4 m span) — a one-rectangle
        part whose 35°-rotated AABB is much larger than the rectangle itself,
        which is what stresses the broad-phase."""
        from hangarfit.models import Part

        wing = Part(
            kind="wing",
            length_m=0.5,
            width_m=4.0,
            offset_x_m=0.0,
            offset_y_m=0.0,
            angle_deg=0.0,
            z_bottom_m=2.0,
            z_top_m=2.2,
        )
        from tests.conftest import make_test_aircraft

        return make_test_aircraft(id=pid, name=pid, parts=(wing,))

    def _world_parts(self, placements):
        from hangarfit.geometry import cached_parts_world
        from hangarfit.models import Placement

        fleet = {pid: self._wing_plane(pid) for pid, *_ in placements}
        placed = [
            Placement(plane_id=pid, x_m=x, y_m=y, heading_deg=h, on_carts=False)
            for pid, x, y, h in placements
        ]
        return {p.plane_id: cached_parts_world(fleet[p.plane_id], p) for p in placed}

    # (name, [(plane_id, x_m, y_m, heading_deg), ...])
    _SCENARIOS = [
        # Axis-aligned wings overlapping wing-to-wing.
        ("axis_overlap", [("a", 20.0, 20.0, 0.0), ("b", 20.0, 20.1, 0.0)]),
        # Sub-clearance gap (0.10 m < 0.30 m): the box gap must NOT trip the
        # filter — a clearance-only conflict the exact predicate still flags.
        ("axis_clearance_gap", [("a", 20.0, 20.0, 0.0), ("b", 20.0, 20.6, 0.0)]),
        # Far apart: the broad-phase skips both pairs; reference agrees (clear).
        ("axis_far", [("a", 8.0, 8.0, 0.0), ("b", 32.0, 32.0, 0.0)]),
        # 35°-rotated wings genuinely overlapping (boxes close, polygons overlap).
        ("angled_overlap", [("a", 20.0, 20.0, 35.0), ("b", 20.3, 20.4, 35.0)]),
        # 35°-rotated wings whose enlarged AABBs overlap while the thin oriented
        # rectangles miss each other entirely (polygons well clear, far beyond
        # clearance) — the filter must DEFER to the exact predicate here (boxes
        # overlap, so no skip), and the exact predicate then returns no conflict.
        ("angled_aabb_overlap_polys_clear", [("a", 20.0, 20.0, 35.0), ("b", 22.5, 22.5, 35.0)]),
    ]

    @pytest.mark.parametrize("name,placements", _SCENARIOS, ids=[s[0] for s in _SCENARIOS])
    def test_filtered_matches_exact_loop(self, name: str, placements) -> None:
        from hangarfit.collisions import _pairwise_conflicts

        hangar = self._hangar()
        world_parts = self._world_parts(placements)
        got_conflicts, got_pen = _pairwise_conflicts(world_parts, hangar)
        ref_conflicts, ref_pen = _exact_pairwise_no_broadphase(world_parts, hangar)

        assert got_conflicts == ref_conflicts, f"{name}: broad-phase changed the conflict set"
        # Exact float identity — the penetration accumulator must be bit-for-bit
        # equal (it is the solver's secondary scoring key; ADR-0003).
        assert got_pen == ref_pen, f"{name}: broad-phase changed total_penetration_m2"

    def test_scenarios_exercise_both_paths(self) -> None:
        """Guard against a vacuous suite: the scenarios must produce at least one
        genuine conflict (so an over-skipping filter would be caught) and at
        least one clean pair (so the skip path is actually taken)."""
        hangar = self._hangar()
        conflict_counts = [
            len(_exact_pairwise_no_broadphase(self._world_parts(pl), hangar)[0])
            for _, pl in self._SCENARIOS
        ]
        assert sum(1 for n in conflict_counts if n > 0) >= 2, conflict_counts
        assert sum(1 for n in conflict_counts if n == 0) >= 1, conflict_counts

    def test_all_layout_fixtures_match_exact_loop(self) -> None:
        """Sweep every loadable layout fixture (struts, low-wing, multi-part,
        nesting) and assert the filtered pairwise loop is byte-identical to the
        unfiltered reference on each — the broadest regression net for #454."""
        from hangarfit.collisions import _pairwise_conflicts
        from hangarfit.geometry import cached_parts_world

        loaded = 0
        had_conflict = 0
        for path in sorted(FIXTURES_DIR.glob("*.yaml")):
            try:
                layout = load_layout(path)
            except LoaderError:
                continue  # scenario fixtures and the like are not layouts
            loaded += 1
            world_parts = {
                p.plane_id: cached_parts_world(layout.fleet[p.plane_id], p)
                for p in layout.placements
            }
            got = _pairwise_conflicts(world_parts, layout.hangar)
            ref = _exact_pairwise_no_broadphase(world_parts, layout.hangar)
            assert got[0] == ref[0], f"{path.name}: broad-phase changed the conflict set"
            assert got[1] == ref[1], f"{path.name}: broad-phase changed total_penetration_m2"
            if got[0]:
                had_conflict += 1
        assert loaded >= 5, f"expected to sweep several layout fixtures, loaded {loaded}"
        assert had_conflict >= 1, "sweep never exercised a conflicting pair — net is too weak"


class TestEmpennage:
    """ADR-0023 / #518: the empennage as explicit tail surfaces.

    The collision predicate is unchanged — these lock that honest tail
    z-extents produce the physically correct verdict: a fin in the wing layer
    blocks a nest that passes over it; a wing that clears the fin still nests;
    a realistic-width tailplane clips a neighbour's low part. Built from
    synthetic inline aircraft (not the placeholder fleet) so the geometry is
    fully controlled. Heading 0 maps plane-local +x -> world +y and plane-local
    +y -> world +x (det(-1), ADR-0002); offsets below are chosen against that.
    """

    def _layout(
        self,
        *,
        nester_xy: tuple[float, float],
        nester_kind: str = "wing",
        nester_z: tuple[float, float] = (2.0, 2.3),
        nester_size: tuple[float, float] = (4.0, 4.0),
    ):
        from hangarfit.models import (
            Aircraft,
            Door,
            Hangar,
            Layout,
            MaintenanceBay,
            Part,
            Placement,
            Wheels,
        )

        # HOST parked at world (10, 10): low aft fuselage (z 0..1.5), a low wide
        # tailplane (`tail`, z 1.2..1.5, world x 8.5..11.5 / y 6.7..7.7), and a
        # tall thin centreline fin (`vertical_stabilizer`, z 1.5..2.4 reaching
        # into the 2.0..2.3 wing band, world x ~9.93..10.08 / y 6.6..7.8).
        host = Aircraft(
            id="host",
            name="Host",
            wing_position="high",
            gear="tailwheel",
            movement_mode="always_own_gear",
            turn_radius_m=5.0,
            measured=False,
            parts=(
                Part(
                    kind="fuselage_aft",
                    length_m=3.0,
                    width_m=0.85,
                    offset_x_m=-1.5,
                    offset_y_m=0.0,
                    angle_deg=0.0,
                    z_bottom_m=0.0,
                    z_top_m=1.5,
                ),
                Part(
                    kind="tail",
                    length_m=1.0,
                    width_m=3.0,
                    offset_x_m=-2.8,
                    offset_y_m=0.0,
                    angle_deg=0.0,
                    z_bottom_m=1.2,
                    z_top_m=1.5,
                ),
                Part(
                    kind="vertical_stabilizer",
                    length_m=1.2,
                    width_m=0.15,
                    offset_x_m=-2.8,
                    offset_y_m=0.0,
                    angle_deg=0.0,
                    z_bottom_m=1.5,
                    z_top_m=2.4,
                ),
            ),
            wheels=Wheels(main_offset_x_m=0.0, track_m=1.8, third_wheel_offset_x_m=-3.0),
        )
        nx, ny = nester_xy
        nl, nw = nester_size
        nester = Aircraft(
            id="nester",
            name="Nester",
            wing_position="high",
            gear="tailwheel",
            movement_mode="always_own_gear",
            turn_radius_m=5.0,
            measured=False,
            parts=(
                Part(
                    kind=nester_kind,  # type: ignore[arg-type]
                    length_m=nl,
                    width_m=nw,
                    offset_x_m=0.0,
                    offset_y_m=0.0,
                    angle_deg=0.0,
                    z_bottom_m=nester_z[0],
                    z_top_m=nester_z[1],
                ),
            ),
            wheels=Wheels(main_offset_x_m=0.0, track_m=1.8, third_wheel_offset_x_m=-3.0),
        )
        hangar = Hangar(
            length_m=40.0,
            width_m=20.0,
            door=Door(center_x_m=10.0, width_m=12.0),
            maintenance_bay=MaintenanceBay(center_x_m=10.0, width_m=8.0, depth_m=9.0),
            clearance_m=0.3,
            wing_layer_clearance_m=0.2,
        )
        return Layout(
            fleet={"host": host, "nester": nester},
            hangar=hangar,
            placements=(
                Placement(plane_id="host", x_m=10.0, y_m=10.0, heading_deg=0.0, on_carts=False),
                Placement(plane_id="nester", x_m=nx, y_m=ny, heading_deg=0.0, on_carts=False),
            ),
            maintenance_plane=None,
        )

    def test_fin_blocks_wing_nesting(self) -> None:
        """#520 safety case: a wing footprint passing OVER the host's centreline
        fin (fin z_top 2.4 in the wing band) conflicts — silently valid today."""
        result = check(self._layout(nester_xy=(10.0, 7.0)))
        assert not result.valid
        assert _conflict_kinds(result) == {"vertical_stabilizer_wing_overlap"}, result.conflicts

    def test_wing_clears_fin_laterally_is_valid(self) -> None:
        """#520 nuance: a wing whose footprint passes OUTBOARD of the thin
        centreline fin (no plan-view overlap with it) still nests over the
        host's low tailplane at a disjoint height -> valid."""
        result = check(self._layout(nester_xy=(13.0, 7.0)))
        assert result.valid, result.conflicts

    def test_wide_tailplane_clips_neighbour_low_part(self) -> None:
        """#519: a neighbour's low part (fuselage_aft at z 0..1.5) overlapping
        the host's realistic ~3 m tailplane in plan view at a shared z-band
        conflicts — free space under the old fuselage-tube-width model."""
        result = check(
            self._layout(
                nester_xy=(8.3, 7.2),
                nester_kind="fuselage_aft",
                nester_z=(0.0, 1.5),
                nester_size=(1.5, 1.5),
            )
        )
        assert not result.valid
        assert "fuselage_aft_tail_overlap" in _conflict_kinds(result), result.conflicts
