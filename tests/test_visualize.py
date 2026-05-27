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
    _BAY_WALL_FACE,
    _CART_DECK_COLOR,
    _GLYPH_ZORDER,
    _WHEEL_COLOR,
    _draw_gear_glyph,
    _draw_maintenance_bay,
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

    def test_title_is_optional(self, tmp_path: Path) -> None:
        layout = _load("valid_two_separated")
        out_no_title = tmp_path / "no_title.png"
        out_with_title = tmp_path / "with_title.png"
        render_layout(layout, out_no_title)
        render_layout(layout, out_with_title, title="Case 1 — two planes")
        _assert_valid_png(out_no_title)
        _assert_valid_png(out_with_title)

    def test_renders_layouts_example(self, tmp_path: Path) -> None:
        """Issue #6 explicitly names ``layouts/example.yaml`` as the
        smoke-test target. The PR's other tests use fixtures; this one
        ensures the production example file stays renderable."""
        layout = load_layout(REPO_ROOT / "layouts" / "example.yaml")
        out = tmp_path / "example.png"
        render_layout(layout, out)
        _assert_valid_png(out)


class TestRendererHandlesEdgeCases:
    def test_layout_with_maintenance_plane_renders(self, tmp_path: Path) -> None:
        """A layout naming a maintenance plane (occupant absent from
        placements) still renders. The walled-rect dispatch is exercised
        directly in :class:`TestConditionalBayRendering` below.
        """
        layout = load_layout(REPO_ROOT / "layouts" / "example.yaml")
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

        ax.text.assert_called_once()
        # ax.text is positional (x, y, s, **kwargs) — the third arg is the label.
        label_string = ax.text.call_args.args[2]
        assert label_string.startswith("IN MAINTENANCE:"), (
            f"label must start with 'IN MAINTENANCE:'; got {label_string!r}"
        )
        assert "scheibe_falke" in label_string, (
            f"label must name the occupant; got {label_string!r}"
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
        * ``on_carts=True`` (any gear) → 1 deck Polygon + 4 corner wheel circles

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
        return Aircraft(
            id="probe",
            name="Probe",
            wing_position="high",
            gear=gear,  # type: ignore[arg-type]
            movement_mode=movement_mode,  # type: ignore[arg-type]
            turn_radius_m=turn_radius,
            measured=False,
            parts=(fuselage_front, fuselage_aft),
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

    def test_on_carts_adds_deck_polygon_plus_four_wheel_circles(self) -> None:
        """Cart glyph: 1 deck Polygon + 4 corner Circle patches = 5 total."""
        from matplotlib.patches import Circle
        from matplotlib.patches import Polygon as MplPolygon

        aircraft = self._aircraft("tailwheel", movement_mode="always_cart")
        placement = self._placement(on_carts=True)
        ax = MagicMock()

        _draw_gear_glyph(ax, placement, aircraft)

        assert ax.add_patch.call_count == 5, (
            f"cart glyph expects 5 patches (1 deck + 4 wheels); got {ax.add_patch.call_count}"
        )
        patches = [c.args[0] for c in ax.add_patch.call_args_list]
        polygon_count = sum(1 for p in patches if isinstance(p, MplPolygon))
        circle_count = sum(1 for p in patches if isinstance(p, Circle))
        assert polygon_count == 1, f"expected 1 deck Polygon; got {polygon_count}"
        assert circle_count == 4, f"expected 4 corner Circle wheels; got {circle_count}"

    def test_on_carts_deck_uses_cart_deck_color(self) -> None:
        """The cart deck Polygon must use _CART_DECK_COLOR as its facecolor (RGB channels).

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

        deck = next(
            c.args[0] for c in ax.add_patch.call_args_list if isinstance(c.args[0], MplPolygon)
        )
        expected_rgb = matplotlib.colors.to_rgba(_CART_DECK_COLOR)[:3]
        actual_rgb = deck.get_facecolor()[:3]
        assert actual_rgb == pytest.approx(expected_rgb, abs=1e-3), (
            f"cart deck RGB must match _CART_DECK_COLOR; got {actual_rgb!r} vs {expected_rgb!r}"
        )

    def test_on_carts_suppresses_own_gear_wheels(self) -> None:
        """``on_carts=True`` must draw the cart glyph, not own-gear wheels.

        For a nosewheel plane: own-gear would add 3 circles, cart adds 1
        polygon + 4 circles.  The distinguishing check is the polygon count.
        """
        from matplotlib.patches import Polygon as MplPolygon

        aircraft = self._aircraft("nosewheel", movement_mode="cart_eligible")
        placement = self._placement(on_carts=True)
        ax = MagicMock()

        _draw_gear_glyph(ax, placement, aircraft)

        patches = [c.args[0] for c in ax.add_patch.call_args_list]
        polygon_count = sum(1 for p in patches if isinstance(p, MplPolygon))
        assert polygon_count == 1, (
            f"on_carts=True must use cart glyph (1 deck polygon); got {polygon_count} polygons"
        )
        assert ax.add_patch.call_count == 5, (
            f"cart glyph expects 5 patches total; got {ax.add_patch.call_count}"
        )

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
        """Cart deck corners must rotate with the heading via the world transform.

        At heading 90°, the local +x (forward) axis maps to world +x.  The
        deck's forward-half corner (positive local-u) must have a world-x
        coordinate greater than the placement's x — confirming the rotation
        applied rather than a static axis-aligned rectangle.
        """
        from matplotlib.patches import Polygon as MplPolygon

        aircraft = self._aircraft("nosewheel", movement_mode="always_cart")
        placement = self._placement(on_carts=True, heading_deg=90.0)
        ax = MagicMock()

        _draw_gear_glyph(ax, placement, aircraft)

        deck = next(
            c.args[0] for c in ax.add_patch.call_args_list if isinstance(c.args[0], MplPolygon)
        )
        # At heading 90° the forward (+u) axis maps to world +x.
        # Some deck corners have +u > 0 → their world-x must exceed placement.x_m.
        xs = [v[0] for v in deck.get_xy()]
        assert max(xs) > placement.x_m, (
            f"cart deck must extend in +x at heading 90°; "
            f"max x={max(xs)}, placement.x={placement.x_m}"
        )

    # ── no fuselage defensive path ─────────────────────────────────────────────

    def test_no_fuselage_part_is_a_noop(self) -> None:
        """If an aircraft has no fuselage part (defensive path), no patches are added."""
        from hangarfit.models import Aircraft, Part

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
        )
        placement = self._placement(on_carts=False)
        ax = MagicMock()

        _draw_gear_glyph(ax, placement, aircraft)

        ax.add_patch.assert_not_called()
