"""Smoke tests for :mod:`hangarfit.visualize`.

Visual quality of the rendered PNG is reviewed by the user — these
tests only verify that the function produces a *valid* PNG and handles
every public-API code path without raising. Pixel content isn't
asserted because that's brittle to matplotlib / Pillow / fontconfig
version drift across systems.

What "valid PNG" means here: opens cleanly with PIL, format reports as
"PNG", non-zero dimensions. That bar catches truncated writes and
encoder failures (which an ``st_size > 0`` check misses), while staying
robust to rendering differences.

The orientation-regression test for ``nose_direction`` covers the
project's "determinant-(-1) trap" (see ``CLAUDE.md`` and
``test_geometry.py``) without needing to render anything.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from matplotlib.patches import Polygon as MplPolygon
from PIL import Image

from hangarfit.collisions import check
from hangarfit.loader import load_layout
from hangarfit.models import Aircraft, Placement
from hangarfit.visualize import (
    _BAY_LABEL_COLOR,
    _BAY_WALL_EDGE,
    _BAY_WALL_FACE,
    _CART_DECK_COLOR,
    _CART_PALLET_HALF_EXTENT_M,
    _CONFLICT_COLOR,
    _GLYPH_ZORDER,
    _PLACEHOLDER_BANNER,
    _WHEEL_COLOR,
    _draw_conflict_overlay,
    _draw_gear_glyph,
    _draw_maintenance_bay,
    _readout_text,
    nose_direction,
    render_layout,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parent.parent

SQRT2_2 = 0.7071067811865476  # math.sqrt(2) / 2


def _load(name: str):
    return load_layout(FIXTURES_DIR / f"{name}.yaml")


def _assert_valid_png(path: Path) -> None:
    """Open via PIL and confirm the file is a non-trivial PNG. Catches
    truncated writes (which ``st_size > 0`` does not) and encoder failures
    that leave bytes on disk but no decodable image."""
    assert path.exists(), f"renderer did not write {path}"
    with Image.open(path) as img:
        assert img.format == "PNG", f"expected PNG, got {img.format!r}"
        width, height = img.size
        assert width > 0 and height > 0, f"invalid image size {img.size}"


_HERRENTEICH = REPO_ROOT / "examples" / "herrenteich"


class TestGroundObjectRendering:
    """#606: fixed-obstacle keep-outs + mover bodies render in the 2D PNG."""

    def test_renders_layout_with_ground_objects(self, tmp_path: Path) -> None:
        """The Herrenteich full set (8 aircraft + fixed fuel trailer + VW Caddy +
        2 glider trailers) renders to a valid PNG."""
        layout = load_layout(_HERRENTEICH / "layout_full.yaml")
        assert layout.ground_object_placements, "fixture must carry ground objects"
        classes = {
            layout.ground_objects[gp.plane_id].object_class
            for gp in layout.ground_object_placements
        }
        assert classes == {"fixed_obstacle", "placed_routed_mover"}  # both classes exercised
        out = tmp_path / "full.png"
        render_layout(layout, out)
        _assert_valid_png(out)

    def test_aircraft_only_layout_renders(self, tmp_path: Path) -> None:
        """A layout with no ground objects renders fine — the new draw helpers are
        no-ops (inert-when-empty)."""
        layout = load_layout(_HERRENTEICH / "layout.yaml")
        assert not layout.ground_object_placements
        out = tmp_path / "ac_only.png"
        render_layout(layout, out)
        _assert_valid_png(out)

    def test_validator_accepts_a_ground_object_conflict(self) -> None:
        """A CheckResult whose conflict names a ground-object id must NOT be
        rejected as a cross-layout mismatch (the #606 validator widening)."""
        from hangarfit.models import CheckResult, Conflict
        from hangarfit.visualize import _validate_check_result_planes

        layout = load_layout(_HERRENTEICH / "layout_full.yaml")
        mover_id = next(gp.plane_id for gp in layout.ground_object_placements)
        result = CheckResult(
            conflicts=(Conflict.single(kind="ground_obstacle", plane=mover_id, detail="x"),)
        )
        _validate_check_result_planes(layout, result)  # must not raise

    def test_validator_still_rejects_a_truly_unknown_id(self) -> None:
        """The cross-layout guard still fires for an id in neither aircraft nor
        ground objects."""
        from hangarfit.models import CheckResult, Conflict
        from hangarfit.visualize import _validate_check_result_planes

        layout = load_layout(_HERRENTEICH / "layout_full.yaml")
        result = CheckResult(
            conflicts=(Conflict.single(kind="x", plane="not_a_real_body", detail="x"),)
        )
        with pytest.raises(ValueError, match="not placed in this layout"):
            _validate_check_result_planes(layout, result)


class TestRenderLayout:
    def test_produces_valid_png_for_valid_layout(self, tmp_path: Path) -> None:
        layout = _load("valid_two_separated")
        out = tmp_path / "valid.png"
        render_layout(layout, out)
        _assert_valid_png(out)

    def test_accepts_str_path_and_pathlib_path(self, tmp_path: Path) -> None:
        layout = _load("valid_two_separated")
        out = tmp_path / "as_str.png"
        render_layout(layout, str(out))
        _assert_valid_png(out)

    def test_renders_all_9_plane_layout(self, tmp_path: Path) -> None:
        """The all-9-planes acceptance layout exercises every wing_position
        (low + high), every movement_mode, and cantilever + strut-braced
        planes together. Bay state for this fixture is *open* (no
        maintenance plane) — the closed-bay overlay is exercised by
        :class:`TestConditionalBayRendering` below."""
        layout = _load("valid_all_nine_planes")
        out = tmp_path / "all_nine.png"
        render_layout(layout, out)
        _assert_valid_png(out)

    def test_conflict_overlay_runs_on_invalid_layout(self, tmp_path: Path) -> None:
        """When a CheckResult with conflicts is supplied, the renderer
        traverses the red-overlay branch. Guards against a regression that
        breaks the ``if not check_result.valid`` path while leaving the
        clean-result path working."""
        layout = _load("invalid_wing_wing_same_height")
        result = check(layout)
        assert not result.valid, "fixture precondition: layout should have conflicts"
        out = tmp_path / "conflict.png"
        render_layout(layout, out, check_result=result)
        _assert_valid_png(out)

    def test_clean_check_result_skips_overlay_branch(self, tmp_path: Path) -> None:
        """A CheckResult with zero conflicts must not trigger the red
        overlay branch — the guard is ``not check_result.valid``, so a
        valid result must hit the early-return path."""
        layout = _load("valid_two_separated")
        result = check(layout)
        assert result.valid, "fixture precondition: layout should be clean"
        out = tmp_path / "clean_with_result.png"
        render_layout(layout, out, check_result=result)
        _assert_valid_png(out)

    def test_conflict_overlay_carries_non_colour_redundancy(self) -> None:
        """Every conflict overdraw patch must signal "in conflict" through
        non-colour channels — a hatch pattern *and* a dashed stroke — in
        addition to the red edge, so the conflict reads on a B&W printout and
        for red-green colour-blind viewers (#326).

        Guards the accessibility invariant documented in arc42 §8 ("Visualizer
        colour accessibility") against a refactor that drops the hatch/dash and
        leaves only ``_CONFLICT_COLOR`` — a regression that would still render a
        plausible-looking PNG and so would pass every other test in this file.
        Mirrors :meth:`TestConditionalBayRendering
        .test_closed_bay_adds_hatched_red_patch_and_label`, the other
        non-colour-redundancy guard in this suite.
        """
        import matplotlib.colors

        layout = _load("invalid_wing_wing_same_height")
        result = check(layout)
        assert not result.valid, "fixture precondition: layout should have conflicts"
        ax = MagicMock()

        _draw_conflict_overlay(ax, layout, result)

        assert ax.add_patch.call_count >= 1, (
            "conflict overlay must redraw at least one offending part"
        )
        expected_edge = matplotlib.colors.to_rgba(_CONFLICT_COLOR)
        for call in ax.add_patch.call_args_list:
            patch = call.args[0]
            assert isinstance(patch, MplPolygon)
            assert patch.get_hatch(), (
                f"conflict patch must carry a hatch pattern; got {patch.get_hatch()!r}"
            )
            assert patch.get_linestyle() in ("--", "dashed"), (
                "conflict patch must use a dashed (non-solid) stroke; "
                f"got {patch.get_linestyle()!r}"
            )
            # The red edge is *retained* as the fast signal for colour-normal
            # viewers — the hatch/dash are additive, not a replacement.
            edge = patch.get_edgecolor()
            assert edge[:3] == expected_edge[:3], (
                f"conflict patch must keep the {_CONFLICT_COLOR} edge; got {edge!r}"
            )

    def test_title_is_optional(self, tmp_path: Path) -> None:
        layout = _load("valid_two_separated")
        out_no_title = tmp_path / "no_title.png"
        out_with_title = tmp_path / "with_title.png"
        render_layout(layout, out_no_title)
        render_layout(layout, out_with_title, title="Case 1 — two planes")
        _assert_valid_png(out_no_title)
        _assert_valid_png(out_with_title)

    def test_renders_layouts_example(self, tmp_path: Path) -> None:
        """Issue #6 explicitly names ``examples/layouts/example.yaml`` as the
        smoke-test target. The PR's other tests use fixtures; this one
        ensures the production example file stays renderable."""
        layout = load_layout(REPO_ROOT / "examples" / "layouts" / "example.yaml")
        out = tmp_path / "example.png"
        render_layout(layout, out)
        _assert_valid_png(out)


class TestRendererHandlesEdgeCases:
    def test_layout_with_maintenance_plane_renders(self, tmp_path: Path) -> None:
        """A layout naming a maintenance plane (occupant absent from
        placements) still renders. The walled-rect dispatch is exercised
        directly in :class:`TestConditionalBayRendering` below.
        """
        layout = load_layout(REPO_ROOT / "examples" / "layouts" / "example.yaml")
        assert layout.maintenance_plane is not None  # sanity: the case we care about
        out = tmp_path / "maintenance.png"
        render_layout(layout, out)
        _assert_valid_png(out)

    def test_wall_vertex_layout_renders(self, tmp_path: Path) -> None:
        """A plane with a vertex exactly at the hangar wall — the renderer
        clamps view to ``[-1, width_m + 1]`` so the vertex stays visible."""
        layout = _load("valid_wall_vertex")
        out = tmp_path / "wall_vertex.png"
        render_layout(layout, out)
        _assert_valid_png(out)

    @pytest.mark.parametrize(
        "fixture_name",
        [
            "invalid_hangar_bounds",
            "invalid_fuselage_fuselage",
            "invalid_strut_blocks_nesting",
        ],
    )
    def test_renders_invalid_fixtures_with_conflict_overlay(
        self, tmp_path: Path, fixture_name: str
    ) -> None:
        """All invalid fixtures (covering hangar_bounds,
        fuselage_fuselage_overlap, strut_wing_overlap conflict kinds)
        must render cleanly with conflict overlay."""
        layout = _load(fixture_name)
        result = check(layout)
        out = tmp_path / f"{fixture_name}.png"
        render_layout(layout, out, check_result=result)
        _assert_valid_png(out)


class TestConditionalBayRendering:
    """The bay rectangle's rendering depends on ``Layout.maintenance_plane``:

    - ``None`` → bay is not drawn (just normal floor).
    - non-``None`` → bay rect is filled with a hatched wall style and an
      ``IN MAINTENANCE: <plane_id>`` label is centered inside.
    """

    def test_open_bay_skips_drawing(self) -> None:
        """``layout.maintenance_plane is None`` → ``_draw_maintenance_bay``
        is a no-op. No bay rect, no label."""
        layout = _load("valid_bay_open_planes_in_back_strip")
        assert layout.maintenance_plane is None  # fixture sanity
        ax = MagicMock()

        _draw_maintenance_bay(ax, layout)

        ax.add_patch.assert_not_called()
        ax.text.assert_not_called()

    def test_closed_bay_adds_hatched_red_patch_and_label(self) -> None:
        """``layout.maintenance_plane is not None`` →
        ``_draw_maintenance_bay`` adds exactly one Polygon patch (hatched,
        with the bay-wall facecolor) and exactly one text whose body
        begins with ``IN MAINTENANCE:`` and names the occupant.
        """
        import matplotlib.colors

        layout = _load("valid_bay_closed_no_intruder")
        assert layout.maintenance_plane == "scheibe_falke"  # fixture sanity
        ax = MagicMock()

        _draw_maintenance_bay(ax, layout)

        ax.add_patch.assert_called_once()
        patch = ax.add_patch.call_args.args[0]
        assert isinstance(patch, MplPolygon)
        assert patch.get_hatch(), (
            f"closed-bay patch must carry a hatch pattern; got {patch.get_hatch()!r}"
        )
        # Pinning the facecolor against the module constant catches a
        # regression that swaps the bay fill to ``_CONFLICT_COLOR``: both
        # would render as "red box" and lose the bay-vs-conflict
        # distinction the module's visualize.py comment calls load-bearing.
        face = patch.get_facecolor()
        expected = matplotlib.colors.to_rgba(_BAY_WALL_FACE, alpha=face[3])
        assert face == expected, f"closed-bay facecolor must be _BAY_WALL_FACE; got {face!r}"

        # The edge + label colours are #418 changes (brand ink, not the old bay
        # red / white). Pin them at the DRAW path too: the constant-level parity
        # test (test_bay_wall_aligns_*) can't catch a stale _draw_maintenance_bay
        # wiring, the exact regression the banner draw-path guard exists to stop.
        edge = patch.get_edgecolor()
        assert edge == matplotlib.colors.to_rgba(_BAY_WALL_EDGE, alpha=edge[3]), (
            f"closed-bay edgecolor must be _BAY_WALL_EDGE; got {edge!r}"
        )

        ax.text.assert_called_once()
        # ax.text is positional (x, y, s, **kwargs) — the third arg is the label.
        label_string = ax.text.call_args.args[2]
        assert label_string.startswith("IN MAINTENANCE:"), (
            f"label must start with 'IN MAINTENANCE:'; got {label_string!r}"
        )
        assert "scheibe_falke" in label_string, (
            f"label must name the occupant; got {label_string!r}"
        )
        assert ax.text.call_args.kwargs["color"] == _BAY_LABEL_COLOR, (
            f"bay label must use _BAY_LABEL_COLOR; got {ax.text.call_args.kwargs.get('color')!r}"
        )

    def test_closed_bay_patch_uses_partial_width_back_strip_geometry(self) -> None:
        """The bay rect drawn must match the partial-width back-anchored
        rectangle defined by ``MaintenanceBay.center_x_m`` / ``width_m`` /
        ``depth_m`` — both axes pinned.

        Guards against two regression classes after #103's model expansion:

        - x-axis: re-shading the full ``[0, hangar.width_m]`` back strip
          (the pre-#103 behavior). The fixture's bay is right-flush
          (``max(xs) == hangar.width_m``), so the ``min(xs) > 0`` strict
          guard is the one that catches the regression on this fixture.
        - y-axis: swapping ``length_m - depth_m`` for ``0`` (door wall)
          or ``length_m - depth_m / 2`` — the keep-out region would slide
          to the wrong wall, render visually wrong, yet leave x-axis
          assertions green.
        """
        layout = _load("valid_bay_closed_no_intruder")
        hangar = layout.hangar
        bay = hangar.maintenance_bay
        ax = MagicMock()

        _draw_maintenance_bay(ax, layout)

        patch = ax.add_patch.call_args.args[0]
        # Closed polygon: matplotlib-style 5 vertices (last = first).
        xs = [v[0] for v in patch.get_xy()]
        ys = [v[1] for v in patch.get_xy()]

        assert 0 < min(xs) < bay.center_x_m, (
            f"min(xs)={min(xs)} suggests full-width back-strip regression"
        )
        assert min(xs) == bay.center_x_m - bay.width_m / 2
        assert max(xs) == bay.center_x_m + bay.width_m / 2

        assert min(ys) == hangar.length_m - bay.depth_m
        assert max(ys) == hangar.length_m

    def test_open_bay_layout_produces_valid_png(self, tmp_path: Path) -> None:
        """End-to-end smoke: the open-bay fixture renders a valid PNG."""
        layout = _load("valid_bay_open_planes_in_back_strip")
        out = tmp_path / "open_bay.png"
        render_layout(layout, out)
        _assert_valid_png(out)

    def test_closed_bay_layout_produces_valid_png(self, tmp_path: Path) -> None:
        """End-to-end smoke: the closed-bay fixture renders a valid PNG."""
        layout = _load("valid_bay_closed_no_intruder")
        out = tmp_path / "closed_bay.png"
        render_layout(layout, out)
        _assert_valid_png(out)

    def test_closed_bay_with_conflict_overlay_renders(self, tmp_path: Path) -> None:
        """Combined closed-bay × conflict-overlay smoke. Guards against a
        z-order regression where the bay patch obscures the red conflict
        overlay or vice versa. The closed-bay fixture has a clean layout,
        so we synthesize a non-clean ``CheckResult`` on one of its placed
        planes — the renderer only needs *some* conflict to traverse the
        overlay branch."""
        from hangarfit.models import CheckResult, Conflict

        layout = _load("valid_bay_closed_no_intruder")
        assert layout.maintenance_plane is not None  # fixture sanity
        placed_pid = layout.placements[0].plane_id
        synthetic = CheckResult(
            conflicts=(
                Conflict.single(
                    kind="hangar_bounds",
                    plane=placed_pid,
                    detail="synthetic — exercise overlay-on-closed-bay path",
                ),
            )
        )

        out = tmp_path / "closed_bay_with_conflict.png"
        render_layout(layout, out, check_result=synthetic)
        _assert_valid_png(out)


class TestNoseDirection:
    """Regression-test the determinant-(-1) trap from ``CLAUDE.md`` for the
    visualizer's nose arrow.

    The orientation-correctness of the rendered arrow is the single
    biggest risk a future refactor of ``_annotate_plane`` carries — a
    textbook CCW rotation would swap ``dx`` and ``dy`` and the arrows
    would silently point the wrong way at non-axis-aligned headings.
    These tests catch that without rendering anything.

    Mirrors the structure of ``test_geometry.py::TestAircraftPartsWorld``.
    """

    def test_heading_zero_nose_to_plus_y(self) -> None:
        dx, dy = nose_direction(0.0)
        assert abs(dx - 0.0) < 1e-9
        assert abs(dy - 1.0) < 1e-9

    def test_heading_90_nose_to_plus_x(self) -> None:
        dx, dy = nose_direction(90.0)
        assert abs(dx - 1.0) < 1e-9
        assert abs(dy - 0.0) < 1e-9

    def test_heading_45_into_plus_x_plus_y_quadrant(self) -> None:
        """At heading 45°, nose points into the (+x, +y) quadrant with
        equal magnitude. A textbook CCW rotation also produces
        (cos 45°, sin 45°) = (√2/2, √2/2), so this case alone doesn't
        distinguish — see the 135° test."""
        dx, dy = nose_direction(45.0)
        assert abs(dx - SQRT2_2) < 1e-9
        assert abs(dy - SQRT2_2) < 1e-9

    def test_heading_135_distinguishes_correct_from_ccw(self) -> None:
        """The canonical regression catch from ``CLAUDE.md``: at heading
        135° a textbook CCW rotation would send the nose to
        ``(-√2/2, +√2/2)``; the correct transform sends it to
        ``(+√2/2, -√2/2)``. If this test fails, the visualizer has
        regressed to a pure rotation somewhere."""
        dx, dy = nose_direction(135.0)
        assert abs(dx - SQRT2_2) < 1e-9
        assert abs(dy - (-SQRT2_2)) < 1e-9


class TestRendererValidatesInputs:
    """The renderer is a debugging tool. Silently rendering a clean PNG
    for a malformed input is the precise failure mode this tool exists
    to prevent."""

    def test_check_result_with_unknown_plane_id_rejected(self, tmp_path: Path) -> None:
        """If the caller hands the renderer a CheckResult that references
        a plane not in the layout (e.g., a CheckResult from a different
        layout), reject loudly rather than rendering a clean PNG."""
        from hangarfit.models import CheckResult, Conflict

        layout = _load("valid_two_separated")
        bogus = CheckResult(
            conflicts=(
                Conflict.single(
                    kind="hangar_bounds",
                    plane="not_in_layout",
                    detail="synthetic, not from this layout",
                ),
            )
        )
        with pytest.raises(ValueError, match="not placed in this layout"):
            render_layout(layout, tmp_path / "should_not_write.png", check_result=bogus)

    def test_unknown_part_kind_raises(self) -> None:
        """``_draw_part`` raises on any unknown ``PartKind`` rather than
        silently rendering it as a generic shape. Guards the "PartKind
        grew; the renderer didn't" failure mode."""
        from unittest.mock import MagicMock

        from shapely.geometry import Polygon

        from hangarfit.geometry import WorldPart
        from hangarfit.visualize import _draw_part

        bogus_part = WorldPart(
            polygon=Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            z_bottom_m=0.0,
            z_top_m=1.0,
            plane_id="probe",
            kind="unknown_kind",  # type: ignore[arg-type]
        )
        with pytest.raises(ValueError, match="unhandled part kind"):
            _draw_part(MagicMock(), bogus_part, "#000000")


class TestDrawPartHandlesTailKind:
    """``PartKind`` includes ``"tail"`` but no fleet plane has a tail
    part today. Cover the branch by constructing a synthetic tail Part
    so future fleet additions don't regress silently."""

    def test_tail_kind_renders_without_exception(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock

        from shapely.geometry import Polygon

        from hangarfit.geometry import WorldPart
        from hangarfit.visualize import _draw_part

        tail_part = WorldPart(
            polygon=Polygon([(0, 0), (1, 0), (1, 0.5), (0, 0.5)]),
            z_bottom_m=0.0,
            z_top_m=1.5,
            plane_id="probe",
            kind="tail",
        )
        ax = MagicMock()
        _draw_part(ax, tail_part, "#3498db")
        ax.add_patch.assert_called_once()


class TestDrawPartHandlesVerticalStabilizer:
    """``vertical_stabilizer`` (the fin, #520/ADR-0023) renders via its own
    branch in ``_draw_part``, not the fail-loud ``else``. The fin rises into /
    above the wing layer, so it is drawn opaque on top as a height cue."""

    def test_vertical_stabilizer_kind_renders_without_exception(self) -> None:
        from unittest.mock import MagicMock

        from shapely.geometry import Polygon

        from hangarfit.geometry import WorldPart
        from hangarfit.visualize import _draw_part

        fin_part = WorldPart(
            polygon=Polygon([(0, 0), (0.15, 0), (0.15, 1.2), (0, 1.2)]),
            z_bottom_m=1.5,
            z_top_m=2.4,
            plane_id="probe",
            kind="vertical_stabilizer",
        )
        ax = MagicMock()
        _draw_part(ax, fin_part, "#0079B5")
        ax.add_patch.assert_called_once()


class TestDrawPartExhaustiveOverClosedKinds:
    """Every member of the closed ``PartKind`` set must reach a real
    ``_draw_part`` branch — not the fail-loud ``else``. This converts the
    "PartKind grew; the renderer didn't" footgun into a test failure the day a
    seventh kind is added without a render branch (the membership-set analogue
    of the ``else: raise`` guard; ADR-0023 review)."""

    def test_every_part_kind_renders_without_raising(self) -> None:
        from unittest.mock import MagicMock

        from shapely.geometry import Polygon

        from hangarfit.geometry import WorldPart
        from hangarfit.models import _VALID_PART_KINDS
        from hangarfit.visualize import _draw_part

        square = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        for kind in sorted(_VALID_PART_KINDS):
            part = WorldPart(
                polygon=square,
                z_bottom_m=0.0,
                z_top_m=1.0,
                plane_id="probe",
                kind=kind,  # type: ignore[arg-type]
            )
            ax = MagicMock()
            _draw_part(ax, part, "#0079B5")  # must not raise for any closed kind
            ax.add_patch.assert_called_once()


class TestDrawTowPaths:
    """`_draw_tow_paths` overlays each plane's tow path as a polyline, one
    colour per plane, at the conflict-overlay z-tier (#192). Companion to
    ``_draw_conflict_overlay``.
    """

    @staticmethod
    def _vertical_move(plane_id: str, x0: float, y0: float, length: float):
        """A trivial straight (S-leg) move from (x0,y0) heading +y, so the
        sampled polyline is the vertical segment (x0, y0)→(x0, y0+length)."""
        from hangarfit.towplanner import DubinsArc, Move, Pose, Segment

        start = Pose(x_m=x0, y_m=y0, heading_deg=0.0)
        end = Pose(x_m=x0, y_m=y0 + length, heading_deg=0.0)
        arc = DubinsArc(start=start, end=end, turn_radius_m=1.0, segments=(Segment("S", length),))
        return Move(plane_id=plane_id, target_slot=end, path=arc)

    def _plan(self, *moves):
        from hangarfit.towplanner import MovesPlan

        return MovesPlan(target_layout=MagicMock(), moves=tuple(moves))

    def test_draws_one_polyline_per_move(self) -> None:
        from hangarfit.visualize import _draw_tow_paths

        plan = self._plan(
            self._vertical_move("a", 0.0, 0.0, 5.0),
            self._vertical_move("b", 2.0, 0.0, 4.0),
        )
        ax = MagicMock()
        _draw_tow_paths(ax, plan)
        assert ax.plot.call_count == 2

    def test_one_colour_per_distinct_plane(self) -> None:
        from hangarfit.visualize import _draw_tow_paths

        plan = self._plan(
            self._vertical_move("a", 0.0, 0.0, 5.0),
            self._vertical_move("b", 2.0, 0.0, 4.0),
        )
        ax = MagicMock()
        _draw_tow_paths(ax, plan)
        colours = [c.kwargs["color"] for c in ax.plot.call_args_list]
        assert len(set(colours)) == 2, f"distinct planes must get distinct colours, got {colours}"

    def test_same_plane_keeps_one_colour(self) -> None:
        # "one colour per plane": two moves for the same plane id share a colour.
        from hangarfit.visualize import _draw_tow_paths

        plan = self._plan(
            self._vertical_move("a", 0.0, 0.0, 5.0),
            self._vertical_move("a", 1.0, 0.0, 3.0),
        )
        ax = MagicMock()
        _draw_tow_paths(ax, plan)
        colours = [c.kwargs["color"] for c in ax.plot.call_args_list]
        assert colours[0] == colours[1]

    def test_mover_path_uses_mover_fill_colour(self) -> None:
        """#651: a placed-routed mover's 2D path is drawn in the mover body colour
        (_MOVER_FILL), so it reads as a ground vehicle; aircraft keep the palette."""
        from hangarfit.models import GroundObject, Part
        from hangarfit.visualize import _MOVER_FILL, _draw_tow_paths

        layout = MagicMock()
        layout.ground_objects = {
            "trailer": GroundObject(
                id="trailer",
                name="Glider trailer",
                parts=(Part("ground", 6.0, 2.0, 0.0, 0.0, 0.0, 0.0, 2.0),),
                object_class="placed_routed_mover",
                motion_mode="towed",
            )
        }
        plan = self._plan(
            self._vertical_move("a", 0.0, 0.0, 5.0),  # aircraft
            self._vertical_move("trailer", 2.0, 0.0, 4.0),  # mover
        )
        ax = MagicMock()
        _draw_tow_paths(ax, plan, layout)
        by_label = {c.kwargs["label"]: c.kwargs["color"] for c in ax.plot.call_args_list}
        assert by_label["trailer"] == _MOVER_FILL
        assert by_label["a"] != _MOVER_FILL  # aircraft keeps a distinct palette colour

    def test_mover_does_not_shift_aircraft_palette(self) -> None:
        """#651: adding a mover must not change any aircraft's path colour — the
        palette is assigned over aircraft ids only, so the mapping is stable
        whether or not a mover is present (defends the determinism comment)."""
        from hangarfit.models import GroundObject, Part
        from hangarfit.visualize import _draw_tow_paths

        aircraft = (
            self._vertical_move("a", 0.0, 0.0, 5.0),
            self._vertical_move("b", 2.0, 0.0, 4.0),
        )
        ax0 = MagicMock()
        _draw_tow_paths(ax0, self._plan(*aircraft))  # baseline: no mover, no layout
        base = {c.kwargs["label"]: c.kwargs["color"] for c in ax0.plot.call_args_list}

        layout = MagicMock()
        layout.ground_objects = {
            "trailer": GroundObject(
                id="trailer",
                name="Glider trailer",
                parts=(Part("ground", 6.0, 2.0, 0.0, 0.0, 0.0, 0.0, 2.0),),
                object_class="placed_routed_mover",
                motion_mode="towed",
            )
        }
        ax1 = MagicMock()
        _draw_tow_paths(
            ax1, self._plan(*aircraft, self._vertical_move("trailer", 1.0, 0.0, 3.0)), layout
        )
        with_mover = {c.kwargs["label"]: c.kwargs["color"] for c in ax1.plot.call_args_list}
        assert with_mover["a"] == base["a"]
        assert with_mover["b"] == base["b"]

    def test_without_layout_all_paths_use_aircraft_palette(self) -> None:
        """Backward-compat: with no layout (the pre-#651 call), every path is treated
        as an aircraft and drawn from the palette — never the mover fill."""
        from hangarfit.visualize import _MOVER_FILL, _draw_tow_paths

        plan = self._plan(
            self._vertical_move("a", 0.0, 0.0, 5.0),
            self._vertical_move("b", 2.0, 0.0, 4.0),
        )
        ax = MagicMock()
        _draw_tow_paths(ax, plan)  # no layout arg
        colours = [c.kwargs["color"] for c in ax.plot.call_args_list]
        assert _MOVER_FILL not in colours
        assert len(set(colours)) == 2

    def test_colour_assignment_is_order_independent(self) -> None:
        # Sorting plane ids before assigning colours makes the mapping
        # deterministic regardless of move order (ADR-0003 spirit).
        from hangarfit.visualize import _draw_tow_paths

        ax1, ax2 = MagicMock(), MagicMock()
        _draw_tow_paths(
            ax1,
            self._plan(
                self._vertical_move("a", 0.0, 0.0, 5.0),
                self._vertical_move("b", 2.0, 0.0, 4.0),
            ),
        )
        _draw_tow_paths(
            ax2,
            self._plan(
                self._vertical_move("b", 2.0, 0.0, 4.0),
                self._vertical_move("a", 0.0, 0.0, 5.0),
            ),
        )
        # Map plane->colour via the per-call label, then compare the mappings.
        m1 = {c.kwargs["label"]: c.kwargs["color"] for c in ax1.plot.call_args_list}
        m2 = {c.kwargs["label"]: c.kwargs["color"] for c in ax2.plot.call_args_list}
        assert m1 == m2

    def test_empty_plan_is_noop(self) -> None:
        from hangarfit.visualize import _draw_tow_paths

        ax = MagicMock()
        _draw_tow_paths(ax, self._plan())
        ax.plot.assert_not_called()

    def test_polyline_traces_sampled_path_endpoints(self) -> None:
        from hangarfit.visualize import _draw_tow_paths

        ax = MagicMock()
        _draw_tow_paths(ax, self._plan(self._vertical_move("a", 0.0, 0.0, 5.0)))
        xs, ys = ax.plot.call_args.args[0], ax.plot.call_args.args[1]
        assert (xs[0], ys[0]) == pytest.approx((0.0, 0.0))
        assert (xs[-1], ys[-1]) == pytest.approx((0.0, 5.0))

    def test_paths_sit_at_overlay_z_tier(self) -> None:
        # Same z-tier as the conflict overlay (zorder 5) so paths read on top
        # of aircraft (zorder 1-4) per spike Q7.
        from hangarfit.visualize import _draw_tow_paths

        ax = MagicMock()
        _draw_tow_paths(ax, self._plan(self._vertical_move("a", 0.0, 0.0, 5.0)))
        assert ax.plot.call_args.kwargs["zorder"] >= 5

    def test_render_layout_with_moves_plan_produces_valid_png(self, tmp_path: Path) -> None:
        layout = _load("valid_two_separated")
        plan = self._plan(
            self._vertical_move("a", 3.0, 1.0, 6.0),
            self._vertical_move("b", 8.0, 1.0, 5.0),
        )
        out = tmp_path / "with_paths.png"
        render_layout(layout, out, moves_plan=plan)
        _assert_valid_png(out)

    @staticmethod
    def _curved_move(plane_id: str):
        """A genuinely curved (multi-segment CSC) move built via the real
        ``plan_dubins``, so ``sample()`` yields interior poses — exercises the
        non-straight branch the vertical S-leg helper cannot reach."""
        from hangarfit.towplanner import Move, Pose, plan_dubins

        start = Pose(x_m=0.0, y_m=0.0, heading_deg=0.0)
        goal = Pose(x_m=5.0, y_m=0.0, heading_deg=180.0)
        arc = plan_dubins(start, goal, turn_radius_m=1.5)
        return Move(plane_id=plane_id, target_slot=goal, path=arc)

    def test_curved_path_samples_interior_points(self) -> None:
        # A turning path must be drawn as the full sampled polyline, not just
        # its two endpoints — guards a regression that collapses sample() to
        # [start, end] (which would silently flatten every curve).
        from hangarfit.visualize import _draw_tow_paths

        ax = MagicMock()
        _draw_tow_paths(ax, self._plan(self._curved_move("a")))
        xs = ax.plot.call_args.args[0]
        assert len(xs) > 2, f"curved path should sample interior points, got {len(xs)}"

    def test_label_is_the_plane_id(self) -> None:
        # The matplotlib label carries the plane id (a future-legend hook);
        # pin it directly rather than only relying on it as a test proxy.
        from hangarfit.visualize import _draw_tow_paths

        ax = MagicMock()
        _draw_tow_paths(ax, self._plan(self._vertical_move("husky", 0.0, 0.0, 5.0)))
        assert ax.plot.call_args.kwargs["label"] == "husky"

    def test_colour_cycle_wraps_beyond_palette(self) -> None:
        # 9 planes > 8-colour palette: must not raise (the i % len cycle), and
        # exactly one colour repeats (the 9th reuses the first). Guards against
        # a regression to direct indexing (IndexError on a 9-plane fleet).
        from hangarfit.visualize import _TOW_PATH_COLORS, _draw_tow_paths

        moves = [self._vertical_move(f"p{i}", float(i), 0.0, 3.0) for i in range(9)]
        ax = MagicMock()
        _draw_tow_paths(ax, self._plan(*moves))
        assert ax.plot.call_count == 9
        colours = [c.kwargs["color"] for c in ax.plot.call_args_list]
        assert len(set(colours)) == len(_TOW_PATH_COLORS)  # 8 distinct, one reused

    def test_render_layout_with_both_check_result_and_moves_plan(self, tmp_path: Path) -> None:
        # The docstring promises check_result and moves_plan are independent —
        # render an invalid layout with BOTH overlays and confirm a valid PNG.
        layout = _load("invalid_wing_wing_same_height")
        result = check(layout)
        assert not result.valid  # fixture precondition
        plan = self._plan(self._vertical_move("a", 3.0, 1.0, 6.0))
        out = tmp_path / "both_overlays.png"
        render_layout(layout, out, check_result=result, moves_plan=plan)
        _assert_valid_png(out)


class TestGearGlyph:
    """Tests for the landing-gear wheel and cart/dolly glyph added in #281.

    Strategy:
    - End-to-end smoke: render the four-plane fixture and assert a valid PNG.
    - Unit: call ``_draw_gear_glyph`` on a mock axis and assert the correct
      number and type of patches are added for each gear configuration:
        * ``nosewheel`` + ``on_carts=False`` → 3 wheel circles (1 nose + 2 mains)
        * ``tailwheel`` + ``on_carts=False`` → 3 wheel circles (2 mains + 1 tail)
        * ``monowheel`` + ``on_carts=False`` → 1 wheel circle
        * ``on_carts=True`` (#321) → one small pallet Polygon + one wheel circle
          per wheel position (3 of each for tricycle/tailwheel, 1 each for
          monowheel); never a single body-sized deck rectangle

    Patch counting is feasible here (unlike pixel tests) because the glyph
    functions make a bounded, deterministic number of ``ax.add_patch`` calls.
    """

    # ── helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _placement(*, on_carts: bool, heading_deg: float = 0.0) -> Placement:
        return Placement(
            plane_id="probe",
            x_m=5.0,
            y_m=5.0,
            heading_deg=heading_deg,
            on_carts=on_carts,
        )

    @staticmethod
    def _aircraft(gear: str, movement_mode: str = "always_own_gear") -> Aircraft:
        """Minimal synthetic Aircraft with a split front/aft fuselage.

        The fuselage is the 7 m box (x ∈ [-3.5, 3.5]) split at x = 0.5 into a
        ``fuselage_front`` (x ∈ [0.5, 3.5]) and ``fuselage_aft``
        (x ∈ [-3.5, 0.5]) pair, exactly as the loader does (#50/ADR-0012). This
        exercises ``_draw_gear_glyph``'s full-span reconstruction from BOTH
        segments — the must-fix breakage where the old glyph looked for a
        single ``kind == "fuselage"`` part and silently stopped drawing wheels
        after the split.
        """
        from hangarfit.models import Part  # Part not used elsewhere in this file

        fuselage_front = Part(
            kind="fuselage_front",
            length_m=3.0,  # x ∈ [0.5, 3.5]
            width_m=1.0,
            offset_x_m=2.0,
            offset_y_m=0.0,
            angle_deg=0.0,
            z_bottom_m=0.0,
            z_top_m=1.5,
        )
        fuselage_aft = Part(
            kind="fuselage_aft",
            length_m=4.0,  # x ∈ [-3.5, 0.5]
            width_m=1.0,
            offset_x_m=-1.5,
            offset_y_m=0.0,
            angle_deg=0.0,
            z_bottom_m=0.0,
            z_top_m=1.5,
        )
        turn_radius = None if movement_mode == "always_cart" else 5.0
        from tests.conftest import default_wheels_for

        return Aircraft(
            id="probe",
            name="Probe",
            wing_position="high",
            gear=gear,  # type: ignore[arg-type]
            movement_mode=movement_mode,  # type: ignore[arg-type]
            turn_radius_m=turn_radius,
            measured=False,
            parts=(fuselage_front, fuselage_aft),
            wheels=default_wheels_for(gear),  # type: ignore[arg-type]
        )

    # ── end-to-end smoke ───────────────────────────────────────────────────────

    def test_gear_glyph_smoke_produces_valid_png(self, tmp_path: Path) -> None:
        """Render the four-plane gear-glyph fixture and assert a valid PNG.

        The fixture covers: nosewheel own-gear (ctsl), tailwheel own-gear
        (aviat_husky), nosewheel own-gear low-wing (fuji), and tailwheel
        on-carts (cessna_140 at 45° heading to exercise the rotation path).
        """
        layout = _load("valid_gear_glyph_smoke")
        out = tmp_path / "gear_glyph_smoke.png"
        render_layout(layout, out)
        _assert_valid_png(out)

    # ── nosewheel own-gear ──────────────────────────────────────────────────────

    def test_nosewheel_own_gear_adds_three_wheel_patches(self) -> None:
        """Tricycle gear: 1 nose wheel + 2 main wheels = 3 Circle patches."""
        from matplotlib.patches import Circle

        aircraft = self._aircraft("nosewheel")
        placement = self._placement(on_carts=False)
        ax = MagicMock()

        _draw_gear_glyph(ax, placement, aircraft)

        assert ax.add_patch.call_count == 3, (
            f"nosewheel gear expects 3 patches; got {ax.add_patch.call_count}"
        )
        for call in ax.add_patch.call_args_list:
            assert isinstance(call.args[0], Circle), (
                f"nosewheel patches must all be Circle; got {type(call.args[0])!r}"
            )

    def test_nosewheel_wheel_color_is_wheel_constant(self) -> None:
        """All nosewheel discs must use _WHEEL_COLOR."""
        aircraft = self._aircraft("nosewheel")
        placement = self._placement(on_carts=False)
        ax = MagicMock()

        _draw_gear_glyph(ax, placement, aircraft)

        for call in ax.add_patch.call_args_list:
            patch = call.args[0]
            assert patch.get_facecolor() is not None  # trivially true; guards future None-return
            # We compare the string passed at construction time via the Circle's
            # internal storage — use get_edgecolor which we set == facecolor.
            # The simplest check: reconstruct what the code set.
            import matplotlib.colors

            expected = matplotlib.colors.to_rgba(_WHEEL_COLOR)
            assert patch.get_facecolor() == pytest.approx(expected, abs=1e-3), (
                f"wheel facecolor mismatch: {patch.get_facecolor()} vs {expected}"
            )

    # ── tailwheel own-gear ─────────────────────────────────────────────────────

    def test_tailwheel_own_gear_adds_three_wheel_patches(self) -> None:
        """Taildragger: 2 main wheels + 1 tailwheel = 3 Circle patches."""
        from matplotlib.patches import Circle

        aircraft = self._aircraft("tailwheel")
        placement = self._placement(on_carts=False)
        ax = MagicMock()

        _draw_gear_glyph(ax, placement, aircraft)

        assert ax.add_patch.call_count == 3, (
            f"tailwheel gear expects 3 patches; got {ax.add_patch.call_count}"
        )
        for call in ax.add_patch.call_args_list:
            assert isinstance(call.args[0], Circle), (
                f"tailwheel patches must all be Circle; got {type(call.args[0])!r}"
            )

    # ── monowheel own-gear ─────────────────────────────────────────────────────

    def test_monowheel_own_gear_adds_one_wheel_patch(self) -> None:
        """Monowheel: single centred main wheel = 1 Circle patch."""
        from matplotlib.patches import Circle

        aircraft = self._aircraft("monowheel")
        placement = self._placement(on_carts=False)
        ax = MagicMock()

        _draw_gear_glyph(ax, placement, aircraft)

        assert ax.add_patch.call_count == 1, (
            f"monowheel gear expects 1 patch; got {ax.add_patch.call_count}"
        )
        assert isinstance(ax.add_patch.call_args.args[0], Circle)

    def test_monowheel_wheel_placed_at_gear_origin(self) -> None:
        """Monowheel disc must be centred on the gear/cart origin (placement x, y)."""
        aircraft = self._aircraft("monowheel")
        placement = self._placement(on_carts=False, heading_deg=0.0)
        ax = MagicMock()

        _draw_gear_glyph(ax, placement, aircraft)

        circle = ax.add_patch.call_args.args[0]
        cx, cy = circle.center
        assert cx == pytest.approx(placement.x_m, abs=1e-6)
        assert cy == pytest.approx(placement.y_m, abs=1e-6)

    # ── cart glyph ─────────────────────────────────────────────────────────────

    def test_on_carts_draws_one_pallet_and_wheel_per_wheel_position(self) -> None:
        """#321: cart glyph draws one small pallet Polygon + one wheel Circle per
        wheel position. A tailwheel plane has 3 wheels → 3 pallets + 3 wheels = 6
        patches (never a single body-sized deck rectangle)."""
        from matplotlib.patches import Circle
        from matplotlib.patches import Polygon as MplPolygon

        aircraft = self._aircraft("tailwheel", movement_mode="always_cart")
        n = len(aircraft.wheels.positions)
        assert n == 3, "tailwheel fixture should expose 3 wheel positions"
        placement = self._placement(on_carts=True)
        ax = MagicMock()

        _draw_gear_glyph(ax, placement, aircraft)

        patches = [c.args[0] for c in ax.add_patch.call_args_list]
        polygon_count = sum(1 for p in patches if isinstance(p, MplPolygon))
        circle_count = sum(1 for p in patches if isinstance(p, Circle))
        assert polygon_count == n, f"expected {n} pallet Polygons; got {polygon_count}"
        assert circle_count == n, f"expected {n} wheel Circles; got {circle_count}"
        assert ax.add_patch.call_count == 2 * n, (
            f"cart glyph expects {2 * n} patches ({n} pallets + {n} wheels); "
            f"got {ax.add_patch.call_count}"
        )

    def test_on_carts_monowheel_draws_single_pallet(self) -> None:
        """#321: a monowheel cart-borne plane draws exactly 1 pallet + 1 wheel."""
        from matplotlib.patches import Circle
        from matplotlib.patches import Polygon as MplPolygon

        aircraft = self._aircraft("monowheel", movement_mode="always_cart")
        assert len(aircraft.wheels.positions) == 1
        placement = self._placement(on_carts=True)
        ax = MagicMock()

        _draw_gear_glyph(ax, placement, aircraft)

        patches = [c.args[0] for c in ax.add_patch.call_args_list]
        assert sum(1 for p in patches if isinstance(p, MplPolygon)) == 1
        assert sum(1 for p in patches if isinstance(p, Circle)) == 1
        assert ax.add_patch.call_count == 2

    def test_on_carts_pallet_is_not_body_sized(self) -> None:
        """#321 regression: each pallet is small (per-wheel), not a body-spanning
        rectangle. Pallet world extent must be far below the ~7 m fuselage span —
        bounded by the pallet diagonal (2·√2·half-extent)."""
        import math

        from matplotlib.patches import Polygon as MplPolygon

        aircraft = self._aircraft("tailwheel", movement_mode="always_cart")
        placement = self._placement(on_carts=True)
        ax = MagicMock()

        _draw_gear_glyph(ax, placement, aircraft)

        max_pallet_span = 2.0 * math.sqrt(2.0) * _CART_PALLET_HALF_EXTENT_M
        for call in ax.add_patch.call_args_list:
            patch = call.args[0]
            if not isinstance(patch, MplPolygon):
                continue
            xy = patch.get_xy()
            xs = [p[0] for p in xy]
            ys = [p[1] for p in xy]
            assert (max(xs) - min(xs)) <= max_pallet_span + 1e-9
            assert (max(ys) - min(ys)) <= max_pallet_span + 1e-9

    def test_on_carts_pallet_uses_cart_deck_color(self) -> None:
        """Each cart pallet Polygon must use _CART_DECK_COLOR as its facecolor
        (RGB channels).

        The alpha channel is set independently via the ``alpha`` kwarg, so
        ``get_facecolor()`` returns ``(r, g, b, alpha)`` — we compare only
        the RGB triplet to avoid brittleness against the exact alpha value.
        """
        import matplotlib.colors
        from matplotlib.patches import Polygon as MplPolygon

        aircraft = self._aircraft("nosewheel", movement_mode="always_cart")
        placement = self._placement(on_carts=True)
        ax = MagicMock()

        _draw_gear_glyph(ax, placement, aircraft)

        pallets = [
            c.args[0] for c in ax.add_patch.call_args_list if isinstance(c.args[0], MplPolygon)
        ]
        assert pallets, "expected at least one pallet Polygon"
        expected_rgb = matplotlib.colors.to_rgba(_CART_DECK_COLOR)[:3]
        for pallet in pallets:
            actual_rgb = pallet.get_facecolor()[:3]
            assert actual_rgb == pytest.approx(expected_rgb, abs=1e-3), (
                f"pallet RGB must match _CART_DECK_COLOR; got {actual_rgb!r} vs {expected_rgb!r}"
            )

    def test_on_carts_suppresses_bare_own_gear_wheels(self) -> None:
        """``on_carts=True`` must draw the cart glyph (pallet + wheel per
        position), not the bare own-gear wheels.

        For a nosewheel plane: bare own-gear would add 3 circles and 0 polygons,
        whereas the cart adds one pallet polygon per wheel.  The distinguishing
        check is that polygons are present (one per wheel)."""
        from matplotlib.patches import Polygon as MplPolygon

        aircraft = self._aircraft("nosewheel", movement_mode="cart_eligible")
        n = len(aircraft.wheels.positions)
        placement = self._placement(on_carts=True)
        ax = MagicMock()

        _draw_gear_glyph(ax, placement, aircraft)

        patches = [c.args[0] for c in ax.add_patch.call_args_list]
        polygon_count = sum(1 for p in patches if isinstance(p, MplPolygon))
        assert polygon_count == n, (
            f"on_carts=True must draw one pallet polygon per wheel; "
            f"got {polygon_count} polygons for {n} wheels"
        )
        assert ax.add_patch.call_count == 2 * n

    def test_glyph_zorder_above_wing_layer(self) -> None:
        """All gear/cart patches must have zorder > 1 (the wing layer) so wings
        cannot paint over the glyph.  This guards the regression where wheel
        and cart-deck patches were erroneously set to zorder=1 (same as wings),
        letting the wing overwrite the cart deck centered at the fuselage origin.
        """
        from matplotlib.patches import Circle
        from matplotlib.patches import Polygon as MplPolygon

        # Test both own-gear (wheels) and cart paths.
        for gear, on_carts, movement_mode in [
            ("nosewheel", False, "always_own_gear"),
            ("tailwheel", True, "always_cart"),
        ]:
            aircraft = self._aircraft(gear, movement_mode=movement_mode)
            placement = self._placement(on_carts=on_carts)
            ax = MagicMock()

            _draw_gear_glyph(ax, placement, aircraft)

            for call in ax.add_patch.call_args_list:
                patch = call.args[0]
                assert isinstance(patch, (Circle, MplPolygon))
                assert patch.get_zorder() > 1, (
                    f"{type(patch).__name__} zorder={patch.get_zorder()} "
                    f"must be > 1 (wing layer) — gear glyphs must not be "
                    f"painted over by wings"
                )
        # Also verify the constant itself sits at the documented 1.5.
        assert _GLYPH_ZORDER == 1.5, (
            f"_GLYPH_ZORDER must be 1.5 (between wings=1 and fuselage=2); got {_GLYPH_ZORDER}"
        )

    def test_cart_glyph_rotates_with_heading(self) -> None:
        """Cart pallet corners must rotate with the heading via the world
        transform.

        At heading 90°, the local +x (forward) axis maps to world +x. The
        forward-most pallet (the nose wheel at local +u) must have a world-x
        coordinate greater than the placement's x — confirming the rotation
        applied rather than a static axis-aligned rectangle.
        """
        from matplotlib.patches import Polygon as MplPolygon

        aircraft = self._aircraft("nosewheel", movement_mode="always_cart")
        placement = self._placement(on_carts=True, heading_deg=90.0)
        ax = MagicMock()

        _draw_gear_glyph(ax, placement, aircraft)

        pallets = [
            c.args[0] for c in ax.add_patch.call_args_list if isinstance(c.args[0], MplPolygon)
        ]
        assert pallets, "expected at least one pallet Polygon"
        # At heading 90° the forward (+u) axis maps to world +x. The nose-wheel
        # pallet (largest +u) must reach world-x beyond placement.x_m.
        all_xs = [pt[0] for pallet in pallets for pt in pallet.get_xy()]
        assert max(all_xs) > placement.x_m, (
            f"cart pallets must extend in +x at heading 90°; "
            f"max x={max(all_xs)}, placement.x={placement.x_m}"
        )

    # ── no fuselage defensive path ─────────────────────────────────────────────

    def test_wheels_are_fuselage_independent(self) -> None:
        """Post-ADR-0013 wheel placement reads ``aircraft.wheels.positions`` and
        no longer depends on the fuselage parts — an aircraft with only a wing
        still draws its three gear wheels from the canonical data."""
        from hangarfit.models import Aircraft, Part, Wheels

        wing = Part(
            kind="wing",
            length_m=10.0,
            width_m=1.5,
            offset_x_m=0.0,
            offset_y_m=0.0,
            angle_deg=0.0,
            z_bottom_m=1.5,
            z_top_m=2.0,
        )
        aircraft = Aircraft(
            id="probe",
            name="Probe",
            wing_position="high",
            gear="nosewheel",
            movement_mode="always_own_gear",
            turn_radius_m=5.0,
            measured=False,
            parts=(wing,),
            wheels=Wheels(main_offset_x_m=0.0, track_m=1.8, third_wheel_offset_x_m=2.0),
        )
        placement = self._placement(on_carts=False)
        ax = MagicMock()

        _draw_gear_glyph(ax, placement, aircraft)

        # Three wheels (two mains + nose) drawn from wheels.positions.
        assert ax.add_patch.call_count == 3


class TestBrandPalette:
    """Pin the DocGerdSoft brand palette lifted into ``visualize`` (handoff
    Deliverable 4). Guards a regression that silently drifts a hex away from
    the authoritative handoff "Drop-in" block, and that the per-plane colour
    keying still draws every part of every placed plane.
    """

    def test_planes_palette_is_the_handoff_nine(self) -> None:
        from hangarfit.visualize import PLANES

        assert PLANES == [
            "#0079B5",
            "#D55E00",
            "#009E73",
            "#B45CA6",
            "#B37903",
            "#108FAA",
            "#4C4C9E",
            "#8A542D",
            "#5E646B",
        ]

    def test_planes_dark_palette_is_the_handoff_nine(self) -> None:
        from hangarfit.visualize import PLANES_DARK

        assert PLANES_DARK == [
            "#3FA3D6",
            "#E8794A",
            "#33B894",
            "#CE7EC0",
            "#D29A2E",
            "#3FB6CE",
            "#8585C9",
            "#BC8154",
            "#9AA0A8",
        ]

    def test_status_map_matches_handoff_drop_in(self) -> None:
        # Key is "wall" per the authoritative README drop-in (NOT "datum").
        from hangarfit.visualize import STATUS

        assert STATUS == {
            "valid": "#0F7C72",
            "conflict": "#C8442C",
            "maint": "#7B63A3",
            "wall": "#3B4046",
        }

    def test_conflict_colour_is_sourced_from_status(self) -> None:
        from hangarfit.visualize import _CONFLICT_COLOR, STATUS

        assert _CONFLICT_COLOR == STATUS["conflict"] == "#C8442C"

    def test_bay_wall_aligns_to_brand_maint_and_ink_tokens(self) -> None:
        """#418: the 2D maintenance-bay fill is the brand ``maint`` violet (shared
        with the 3D bay), the edge + label are the brand ink, and the hatch is
        retained — so the bay reads on-token, stays legible on the lighter violet,
        and never rests on hue alone. Catches a regression back to the old
        off-system bay red (#922b21), which collided with the conflict red.
        """
        from hangarfit import brand

        assert brand.BAY_WALL_FACE == brand.STATUS["maint"] == "#7B63A3"
        assert brand.BAY_WALL_EDGE == brand.INK_EDGE == "#14161A"
        assert brand.BAY_LABEL_COLOR == brand.INK_EDGE
        assert brand.BAY_WALL_HATCH, "the bay hatch must be retained (never hue alone)"

    def test_plane_parts_drawn_per_part_with_ink_outline(self) -> None:
        """Each placed plane's parts are still drawn one ``_draw_part`` call
        per part, and solid parts carry the brand ink outline (#14161A) so
        identity never rests on hue alone.
        """
        import matplotlib.colors

        from hangarfit.geometry import aircraft_parts_world
        from hangarfit.visualize import _INK_EDGE, _draw_aircraft

        layout = _load("valid_two_separated")
        expected_parts = sum(
            len(aircraft_parts_world(layout.fleet[p.plane_id], p)) for p in layout.placements
        )
        ax = MagicMock()

        _draw_aircraft(ax, layout)

        # One add_patch per part, plus the per-wheel gear glyphs. At minimum,
        # every part must have produced a patch.
        assert ax.add_patch.call_count >= expected_parts
        ink = matplotlib.colors.to_rgba(_INK_EDGE)
        # At least one solid (fuselage/strut) part must be stroked in the ink.
        edges = [c.args[0].get_edgecolor() for c in ax.add_patch.call_args_list]
        assert any(e[:3] == ink[:3] for e in edges), (
            "at least one plane part must carry the _INK_EDGE outline"
        )


class TestHonestyAnnotations:
    """#401: placeholder banner constant + actionable readouts."""

    def test_readout_text_reports_gap_and_clearance(self):
        s = _readout_text(_load("valid_left_side_nesting"))
        assert "tightest inter-plane gap" in s
        assert "smallest wing-over-tail clearance" in s

    def test_banner_constant_is_explicit_about_placeholder(self):
        assert "PLACEHOLDER DATA" in _PLACEHOLDER_BANNER
        assert "not for real parking" in _PLACEHOLDER_BANNER

    def test_render_with_placeholder_data_produces_png(self, tmp_path):
        # The shipped fleet is measured: false, so this exercises the banner +
        # readout draw paths end-to-end and must still produce a valid PNG.
        out = tmp_path / "placeholder.png"
        render_layout(_load("valid_left_side_nesting"), out)
        _assert_valid_png(out)

    def test_placeholder_banner_uses_warning_amber_with_ink_text(self) -> None:
        """#418: the 2D placeholder banner is the brand ``warning`` amber with dark
        ink text — the *same* signal as the 3D honesty banner (cross-surface
        parity), single-sourced via ``brand.WARNING`` so 2D and 3D can't drift.
        Catches a regression back to the old off-system red (#b00020).
        """
        from hangarfit import brand

        assert brand.WARNING == "#D6A23E"
        assert brand.PLACEHOLDER_BANNER_BG_2D == brand.WARNING == brand.PLACEHOLDER_BANNER_BG
        assert brand.PLACEHOLDER_BANNER_TEXT_2D == brand.INK_EDGE == "#14161A"

    def test_placeholder_banner_draw_wires_brand_tokens(self) -> None:
        """The draw path actually passes those tokens into ``fig.text`` (bbox
        facecolor + text colour), so the cross-surface parity can't silently
        regress inside the renderer while the constants stay correct.
        """
        from unittest.mock import MagicMock

        from hangarfit import brand
        from hangarfit.visualize import _draw_placeholder_banner

        fig = MagicMock()
        _draw_placeholder_banner(fig)

        fig.text.assert_called_once()
        kwargs = fig.text.call_args.kwargs
        assert kwargs["color"] == brand.PLACEHOLDER_BANNER_TEXT_2D
        assert kwargs["bbox"]["facecolor"] == brand.PLACEHOLDER_BANNER_BG_2D


class TestDrawEgressLanes:
    """`_draw_egress_lanes` overlays each hard-door mover's drive-out corridor as a
    translucent amber 'keep clear' polyline (#652)."""

    @staticmethod
    def _arc(x0: float, y0: float, length: float):
        from hangarfit.towplanner import DubinsArc, Pose, Segment

        start = Pose(x_m=x0, y_m=y0, heading_deg=0.0)
        end = Pose(x_m=x0, y_m=y0 + length, heading_deg=0.0)
        return DubinsArc(start=start, end=end, turn_radius_m=1.0, segments=(Segment("S", length),))

    def test_empty_is_noop(self) -> None:
        from hangarfit.visualize import _draw_egress_lanes

        ax = MagicMock()
        _draw_egress_lanes(ax, {})
        ax.plot.assert_not_called()

    def test_draws_corridor_in_egress_colour(self) -> None:
        from hangarfit import brand
        from hangarfit.visualize import _draw_egress_lanes

        ax = MagicMock()
        _draw_egress_lanes(ax, {"caddy": self._arc(5.0, 0.0, 6.0)})
        assert ax.plot.call_count == 1
        kw = ax.plot.call_args.kwargs
        assert kw["color"] == brand.EGRESS_LANE_COLOR
        # The "keep clear, below the route" visual encoding (dashed amber, drawn
        # under the solid tow paths at zorder 5) — the load-bearing semantics.
        assert kw["linestyle"] == (0, (6, 3))  # dashed
        assert kw["alpha"] == brand.EGRESS_LANE_ALPHA
        assert kw["lw"] == brand.EGRESS_LANE_LINEWIDTH
        assert kw["zorder"] == 4.5  # below the tow-path overlay (5)
        xs, ys = ax.plot.call_args.args[0], ax.plot.call_args.args[1]
        assert (xs[0], ys[0]) == pytest.approx((5.0, 0.0))
        assert (xs[-1], ys[-1]) == pytest.approx((5.0, 6.0))

    def test_multiple_corridors_sorted_by_id(self) -> None:
        from hangarfit.visualize import _draw_egress_lanes

        ax = MagicMock()
        _draw_egress_lanes(
            ax, {"zeta": self._arc(1.0, 0.0, 2.0), "alpha": self._arc(2.0, 0.0, 2.0)}
        )
        labels = [c.kwargs["label"] for c in ax.plot.call_args_list]
        assert labels == ["egress:alpha", "egress:zeta"]  # id-sorted, deterministic
