"""Visualize tests for wheel glyph placement (#322).

The renderer reads wheel positions from ``aircraft.wheels.positions`` (ADR-0013)
rather than reconstructing them from heuristic fuselage fractions. These tests
pin the count of wheel discs per gear type and guard that the cart path
(``on_carts=True``) does not draw the plane's own gear.
"""

from __future__ import annotations

import pytest

from hangarfit.models import Placement, Wheels
from hangarfit.visualize import _draw_gear_glyph
from tests.conftest import make_test_aircraft


@pytest.fixture
def fake_ax(monkeypatch: pytest.MonkeyPatch) -> list[tuple[float, float]]:
    """Capture every ``(wx, wy)`` passed to ``_add_wheel``."""
    from hangarfit import visualize

    captured: list[tuple[float, float]] = []

    def fake_add(ax: object, wx: float, wy: float) -> None:
        captured.append((wx, wy))
        return None

    monkeypatch.setattr(visualize, "_add_wheel", fake_add)
    return captured


class TestOwnGearWheelPositions:
    def test_nosewheel_three_wheels(self, fake_ax: list[tuple[float, float]]) -> None:
        a = make_test_aircraft(
            gear="nosewheel",
            wheels=Wheels(main_offset_x_m=-0.10, track_m=1.80, third_wheel_offset_x_m=2.50),
        )
        placement = Placement(plane_id=a.id, x_m=10.0, y_m=5.0, heading_deg=0.0, on_carts=False)
        _draw_gear_glyph(None, placement, a)
        assert len(fake_ax) == 3

    def test_monowheel_single_wheel(self, fake_ax: list[tuple[float, float]]) -> None:
        a = make_test_aircraft(
            gear="monowheel",
            turn_radius_m=None,
            movement_mode="always_cart",
            wheels=Wheels(main_offset_x_m=0.0, track_m=None, third_wheel_offset_x_m=None),
        )
        # Force on_carts=False to exercise the own-gear path even though an
        # always_cart monowheel is physically unusual.
        placement = Placement(plane_id=a.id, x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=False)
        _draw_gear_glyph(None, placement, a)
        assert len(fake_ax) == 1

    def test_tailwheel_three_wheels(self, fake_ax: list[tuple[float, float]]) -> None:
        a = make_test_aircraft(
            gear="tailwheel",
            wheels=Wheels(main_offset_x_m=0.20, track_m=1.80, third_wheel_offset_x_m=-3.40),
        )
        placement = Placement(plane_id=a.id, x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=False)
        _draw_gear_glyph(None, placement, a)
        assert len(fake_ax) == 3

    def test_wheel_world_coords_reflect_positions(self, fake_ax: list[tuple[float, float]]) -> None:
        """At heading 0 with no rotation, captured world coords are derivable
        from the plane-local wheel positions via local_to_world."""
        from hangarfit.geometry import local_to_world

        wheels = Wheels(main_offset_x_m=-0.10, track_m=1.80, third_wheel_offset_x_m=2.50)
        a = make_test_aircraft(gear="nosewheel", wheels=wheels)
        placement = Placement(plane_id=a.id, x_m=10.0, y_m=5.0, heading_deg=0.0, on_carts=False)
        _draw_gear_glyph(None, placement, a)
        expected = [local_to_world(u, v, placement) for u, v in wheels.positions]
        assert fake_ax == expected


class TestCartGlyphPerWheel:
    def test_on_carts_takes_cart_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """on_carts=True dispatches to the cart path, not the bare own-gear loop."""
        from hangarfit import visualize

        called: list[str] = []
        monkeypatch.setattr(visualize, "_add_wheel", lambda *a, **k: called.append("wheel"))
        monkeypatch.setattr(visualize, "_draw_cart_glyph", lambda *a, **k: called.append("cart"))

        a = make_test_aircraft(
            gear="nosewheel",
            movement_mode="cart_eligible",
            wheels=Wheels(main_offset_x_m=-0.10, track_m=1.80, third_wheel_offset_x_m=2.50),
        )
        placement = Placement(plane_id=a.id, x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=True)
        _draw_gear_glyph(None, placement, a)

        # Cart path taken; bare own-gear wheels NOT drawn directly (the cart
        # path's per-wheel discs are internal to the patched-out
        # _draw_cart_glyph, so none leak through here).
        assert called == ["cart"]

    @pytest.mark.parametrize(
        ("gear", "wheels", "expected"),
        [
            (
                "nosewheel",
                Wheels(main_offset_x_m=-0.10, track_m=1.80, third_wheel_offset_x_m=2.50),
                3,
            ),
            (
                "tailwheel",
                Wheels(main_offset_x_m=0.20, track_m=1.80, third_wheel_offset_x_m=-3.40),
                3,
            ),
            (
                "monowheel",
                Wheels(main_offset_x_m=0.0, track_m=None, third_wheel_offset_x_m=None),
                1,
            ),
        ],
    )
    def test_one_pallet_per_wheel_position(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gear: str,
        wheels: Wheels,
        expected: int,
    ) -> None:
        """#321: the cart glyph draws one pallet per wheel position — equal to
        ``len(aircraft.wheels.positions)`` — not a single body-sized rectangle."""
        from hangarfit import visualize

        pallets: list[tuple[float, float]] = []
        monkeypatch.setattr(
            visualize,
            "_add_cart_pallet",
            lambda ax, u, v, placement: pallets.append((u, v)),
        )
        # Wheels are drawn through _add_wheel; stub it to keep this focused.
        monkeypatch.setattr(visualize, "_add_wheel", lambda *a, **k: None)

        movement_mode = "always_cart" if gear == "monowheel" else "cart_eligible"
        turn_radius = None if gear == "monowheel" else 4.0
        a = make_test_aircraft(
            gear=gear,  # type: ignore[arg-type]
            movement_mode=movement_mode,
            turn_radius_m=turn_radius,
            wheels=wheels,
        )
        placement = Placement(plane_id=a.id, x_m=3.0, y_m=2.0, heading_deg=30.0, on_carts=True)
        _draw_gear_glyph(None, placement, a)

        assert len(pallets) == expected == len(a.wheels.positions)
        # Pallets are centred on the wheel positions, not on the body origin.
        assert pallets == list(a.wheels.positions)
