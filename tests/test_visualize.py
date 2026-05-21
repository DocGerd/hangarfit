"""Smoke tests for :mod:`hangarfit.visualize`.

Visual quality of the rendered PNG is reviewed by the user — these
tests only verify that the function runs without exception, produces a
non-empty file, and handles the conflict-overlay path. The image itself
isn't asserted because pixel-level checks are brittle to matplotlib /
Pillow version drift.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hangarfit.collisions import check
from hangarfit.loader import load_layout
from hangarfit.visualize import render_layout

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _load(name: str):
    return load_layout(FIXTURES_DIR / f"{name}.yaml")


class TestRenderLayout:
    def test_produces_non_empty_png_for_valid_layout(self, tmp_path: Path) -> None:
        layout = _load("valid_two_separated")
        out = tmp_path / "valid.png"
        render_layout(layout, out)
        assert out.exists(), "renderer did not write the output file"
        assert out.stat().st_size > 0, "renderer wrote an empty file"

    def test_accepts_str_path_and_pathlib_path(self, tmp_path: Path) -> None:
        layout = _load("valid_two_separated")
        out = tmp_path / "as_str.png"
        render_layout(layout, str(out))
        assert out.exists()

    def test_renders_all_9_plane_layout(self, tmp_path: Path) -> None:
        """The all-9-planes acceptance layout exercises every wing_position
        (low + high), every movement_mode, cantilever + strut-braced planes,
        and the maintenance bay overlay together."""
        layout = _load("valid_all_nine_planes")
        out = tmp_path / "all_nine.png"
        render_layout(layout, out)
        assert out.exists()
        assert out.stat().st_size > 0

    def test_conflict_overlay_runs_on_invalid_layout(self, tmp_path: Path) -> None:
        """If a CheckResult with conflicts is supplied, the renderer must
        not crash on the red-overlay pass."""
        layout = _load("invalid_wing_wing_same_height")
        result = check(layout)
        assert not result.valid, "fixture precondition: layout should have conflicts"
        out = tmp_path / "conflict.png"
        render_layout(layout, out, check_result=result)
        assert out.exists()
        assert out.stat().st_size > 0

    def test_clean_check_result_is_indistinguishable_from_no_check_result(
        self, tmp_path: Path
    ) -> None:
        """Supplying a CheckResult with zero conflicts should not crash
        and shouldn't draw the overlay (no red highlight)."""
        layout = _load("valid_two_separated")
        result = check(layout)
        assert result.valid, "fixture precondition: layout should be clean"
        out = tmp_path / "clean_with_result.png"
        render_layout(layout, out, check_result=result)
        assert out.exists()

    def test_title_is_optional(self, tmp_path: Path) -> None:
        layout = _load("valid_two_separated")
        out_no_title = tmp_path / "no_title.png"
        out_with_title = tmp_path / "with_title.png"
        render_layout(layout, out_no_title)
        render_layout(layout, out_with_title, title="Case 1 — two planes")
        assert out_no_title.exists()
        assert out_with_title.exists()


class TestRendererHandlesEdgeCases:
    def test_layout_with_maintenance_plane_renders(self, tmp_path: Path) -> None:
        layout = _load("valid_maintenance_at_bay_boundary")
        out = tmp_path / "maintenance.png"
        render_layout(layout, out)
        assert out.exists()

    def test_wall_vertex_layout_renders(self, tmp_path: Path) -> None:
        """A plane with a vertex exactly at the hangar wall — the renderer
        clamps view to ``[-1, width_m + 1]`` so the vertex stays visible."""
        layout = _load("valid_wall_vertex")
        out = tmp_path / "wall_vertex.png"
        render_layout(layout, out)
        assert out.exists()

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
        assert out.exists()
        assert out.stat().st_size > 0
