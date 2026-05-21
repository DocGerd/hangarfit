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

import pytest
from PIL import Image

from hangarfit.collisions import check
from hangarfit.loader import load_layout
from hangarfit.visualize import nose_direction, render_layout

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
        (low + high), every movement_mode, cantilever + strut-braced planes,
        and the maintenance bay overlay together."""
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
        layout = _load("valid_maintenance_at_bay_boundary")
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
            "invalid_maintenance_position",
            "invalid_fuselage_fuselage",
            "invalid_strut_blocks_nesting",
        ],
    )
    def test_renders_invalid_fixtures_with_conflict_overlay(
        self, tmp_path: Path, fixture_name: str
    ) -> None:
        """All invalid fixtures (covering every Conflict.kind branch:
        hangar_bounds, maintenance_position, fuselage_fuselage_overlap,
        strut_wing_overlap) must render cleanly with conflict overlay."""
        layout = _load(fixture_name)
        result = check(layout)
        out = tmp_path / f"{fixture_name}.png"
        render_layout(layout, out, check_result=result)
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

    def test_check_result_with_unknown_plane_id_rejected(
        self, tmp_path: Path
    ) -> None:
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
