"""Loader tests for the wheels: block (#322)."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from hangarfit.loader import LoaderError, load_fleet


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# Reusable per-test body. Tests can append a `    wheels:` block (note the
# 4-space indent so it sits at the same level as `parts:`).
_NOSEWHEEL_BODY = dedent(
    """\
    aircraft:
      - id: testplane
        name: "Test Plane"
        wing_position: high
        gear: nosewheel
        movement_mode: always_own_gear
        turn_radius_m: 4.0
        measured: false
        parts:
          - kind: fuselage
            length_m: 6.0
            width_m: 0.8
            offset_x_m: 0.0
            offset_y_m: 0.0
            z_bottom_m: 0.0
            z_top_m: 1.4
          - kind: wing
            length_m: 1.2
            width_m: 9.0
            offset_x_m: 0.5
            offset_y_m: 0.0
            z_bottom_m: 2.0
            z_top_m: 2.2
    """
)


def _with_wheels_block(extra_yaml: str) -> str:
    """Append ``extra_yaml`` (already 4-space indented) to the body."""
    return _NOSEWHEEL_BODY + extra_yaml


class TestWheelsLoadingHappyPath:
    def test_nosewheel_loads(self, tmp_path: Path) -> None:
        body = _with_wheels_block(
            "    wheels:\n"
            "      main_offset_x_m: -0.10\n"
            "      track_m: 1.80\n"
            "      third_wheel_offset_x_m: 2.50\n"
        )
        fleet = load_fleet(_write(tmp_path / "f.yaml", body))
        a = fleet["testplane"]
        assert a.wheels is not None
        assert a.wheels.main_offset_x_m == -0.10
        assert a.wheels.track_m == 1.80
        assert a.wheels.third_wheel_offset_x_m == 2.50

    def test_no_wheels_block_yields_none_for_now(self, tmp_path: Path) -> None:
        """Transitional (Task 3): missing wheels: block leaves Aircraft.wheels == None.

        Task 5 flips this to raise LoaderError.
        """
        fleet = load_fleet(_write(tmp_path / "f.yaml", _NOSEWHEEL_BODY))
        assert fleet["testplane"].wheels is None


class TestWheelsLoadingErrorPaths:
    def test_nosewheel_missing_track(self, tmp_path: Path) -> None:
        body = _with_wheels_block(
            "    wheels:\n      main_offset_x_m: -0.10\n      third_wheel_offset_x_m: 2.50\n"
        )
        with pytest.raises(LoaderError, match=r"wheels.*track_m"):
            load_fleet(_write(tmp_path / "f.yaml", body))

    def test_nosewheel_missing_third_wheel(self, tmp_path: Path) -> None:
        body = _with_wheels_block(
            "    wheels:\n      main_offset_x_m: -0.10\n      track_m: 1.80\n"
        )
        with pytest.raises(LoaderError, match=r"wheels.*third_wheel_offset_x_m"):
            load_fleet(_write(tmp_path / "f.yaml", body))

    def test_monowheel_with_track_rejected(self, tmp_path: Path) -> None:
        body = (
            _NOSEWHEEL_BODY.replace("gear: nosewheel", "gear: monowheel") + "    wheels:\n"
            "      main_offset_x_m: 0.0\n"
            "      track_m: 1.80\n"
            "      third_wheel_offset_x_m: 2.50\n"
        )
        with pytest.raises(LoaderError, match=r"monowheel.*track_m"):
            load_fleet(_write(tmp_path / "f.yaml", body))

    def test_nosewheel_third_wheel_behind_mains_rejected(self, tmp_path: Path) -> None:
        body = _with_wheels_block(
            "    wheels:\n"
            "      main_offset_x_m: 0.0\n"
            "      track_m: 1.80\n"
            "      third_wheel_offset_x_m: -2.50\n"  # WRONG sign for nosewheel
        )
        with pytest.raises(LoaderError, match=r"nosewheel.*forward"):
            load_fleet(_write(tmp_path / "f.yaml", body))

    def test_tailwheel_third_wheel_forward_of_mains_rejected(self, tmp_path: Path) -> None:
        body = (
            _NOSEWHEEL_BODY.replace("gear: nosewheel", "gear: tailwheel").replace(
                "turn_radius_m: 4.0", "turn_radius_m: 5.0"
            )
            + "    wheels:\n"
            "      main_offset_x_m: 0.20\n"
            "      track_m: 1.80\n"
            "      third_wheel_offset_x_m: 3.00\n"  # WRONG sign for tailwheel
        )
        with pytest.raises(LoaderError, match=r"tailwheel.*aft"):
            load_fleet(_write(tmp_path / "f.yaml", body))

    def test_unknown_keys_rejected(self, tmp_path: Path) -> None:
        body = _with_wheels_block(
            "    wheels:\n"
            "      main_offset_x_m: -0.10\n"
            "      track_m: 1.80\n"
            "      third_wheel_offset_x_m: 2.50\n"
            "      bogus_field: 1.0\n"
        )
        with pytest.raises(LoaderError, match=r"unknown.*bogus_field"):
            load_fleet(_write(tmp_path / "f.yaml", body))

    def test_non_positive_track_wraps_to_loader_error(self, tmp_path: Path) -> None:
        """Wheels.__post_init__ raises ValueError on track_m<=0; loader wraps it."""
        body = _with_wheels_block(
            "    wheels:\n"
            "      main_offset_x_m: -0.10\n"
            "      track_m: 0.0\n"
            "      third_wheel_offset_x_m: 2.50\n"
        )
        with pytest.raises(LoaderError, match=r"track_m must be positive"):
            load_fleet(_write(tmp_path / "f.yaml", body))


class TestMonowheelHappyPath:
    """Monowheel construction path was previously only exercised via error tests."""

    def test_monowheel_loads_with_only_main_offset(self, tmp_path: Path) -> None:
        body = (
            _NOSEWHEEL_BODY.replace("gear: nosewheel", "gear: monowheel").replace(
                "movement_mode: always_own_gear", "movement_mode: always_cart"
            )
            + "    turn_radius_m: null\n"  # always_cart requires null
            + "    wheels:\n"
            "      main_offset_x_m: 0.0\n"
        )
        # Strip the inline turn_radius_m: 4.0 line that came from _NOSEWHEEL_BODY —
        # we override it above to null for always_cart.
        body = body.replace("    turn_radius_m: 4.0\n", "")
        fleet = load_fleet(_write(tmp_path / "f.yaml", body))
        a = fleet["testplane"]
        assert a.wheels is not None
        assert a.wheels.main_offset_x_m == 0.0
        assert a.wheels.track_m is None
        assert a.wheels.third_wheel_offset_x_m is None
        assert a.wheels.positions == ((0.0, 0.0),)
        assert a.wheels.wheelbase_m is None
