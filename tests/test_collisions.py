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

    def test_case_3_high_wing_over_low_fuselage_z_disjoint_valid(self) -> None:
        """Plan-view overlap with z-disjoint must NOT trigger a conflict.

        A bbox-style implementation would flag this; the parts model
        rule (clearance in BOTH plan view AND height) lets it pass.
        """
        result = check(_load("valid_high_over_low_z_disjoint"))
        assert result.valid, f"unexpected conflicts: {result.conflicts}"

    def test_case_4_fuselage_wing_overlap_alphabetical_kind(self) -> None:
        """Heterogeneous-kind pair: alphabetical sort must yield
        ``fuselage_wing_overlap`` (NOT ``wing_fuselage_overlap``) regardless
        of plane iteration order."""
        result = check(_load("invalid_fuselage_wing_overlap"))
        assert not result.valid
        kinds = _conflict_kinds(result)
        assert "fuselage_wing_overlap" in kinds, (
            f"expected alphabetical fuselage_wing_overlap, got {result.conflicts!r}"
        )
        assert "wing_fuselage_overlap" not in kinds, (
            f"non-alphabetical kind leaked into conflicts: {result.conflicts!r}"
        )

    def test_case_5_fuselage_fuselage_overlap(self) -> None:
        """Single-conflict fixture: the *only* conflict expected is the
        fuselage-fuselage overlap. Asserting on the exact conflict count
        catches future regressions that emit phantom extras (e.g. a
        same-aircraft pair leak, or double emission from iteration-order
        confusion). Other invalid fixtures (case 6 especially) emit
        multiple legitimate conflicts; this case is engineered to
        exercise the no-extras property."""
        result = check(_load("invalid_fuselage_fuselage"))
        assert not result.valid
        assert _conflict_kinds(result) == {"fuselage_fuselage_overlap"}, (
            f"expected exactly fuselage_fuselage_overlap, got {result.conflicts!r}"
        )
        assert len(result.conflicts) == 1, (
            f"expected exactly 1 conflict, got {len(result.conflicts)}: {result.conflicts!r}"
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
        with pytest.raises(LoaderError, match="At most one cart_eligible"):
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
                    kind="fuselage",
                    length_m=1.0,
                    width_m=1.0,
                    offset_x_m=0.0,
                    offset_y_m=0.0,
                    angle_deg=0.0,
                    z_bottom_m=0.0,
                    z_top_m=1.0,
                ),
            ),
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
        """An intruder at the front-left corner of the bay: its
        front-left vertex (10.5, 16.5) is strictly inside both
        x_min < x < x_max and y > y_min."""
        layout = self._build_layout(
            bay_open=False, intruder_offset_x_m=-3.0, intruder_offset_y_m=-3.5
        )  # center at (11, 16.5), front-left vertex at (10.5, 16)
        result = check(layout)
        # Front-left vertex (10.5, 16) has y on edge — passes. But
        # rear-left vertex (10.5, 17) is strictly inside.
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

    def test_exact_value_for_single_wing_wing_overlap(self) -> None:
        layout = _load("invalid_wing_wing_same_height")
        result = check(layout)

        assert not result.valid
        assert result.total_penetration_m2 == pytest.approx(4.0373, abs=1e-4)

    def test_sums_across_multiple_pair_conflicts(self) -> None:
        """3 pairwise conflicts in ``invalid_strut_blocks_nesting`` should
        sum to the deterministic 1.4305 m² total — pins the ``+=``
        accumulator semantic against future refactors to ``=``, ``max``,
        or ``mean``."""
        layout = _load("invalid_strut_blocks_nesting")
        result = check(layout)

        assert len(result.conflicts) == 3
        assert result.total_penetration_m2 == pytest.approx(1.4305, abs=1e-4)

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
