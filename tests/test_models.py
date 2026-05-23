"""Tests for hangarfit.models — construction, validation, immutability."""

from __future__ import annotations

import dataclasses

import pytest

from hangarfit.models import (
    Aircraft,
    CheckResult,
    Conflict,
    Door,
    Hangar,
    Layout,
    MaintenanceBay,
    Part,
    Placement,
    SearchConfig,
    StrutsSpec,
)


def _ok_part(
    kind: str = "fuselage",
    *,
    length_m: float = 7.0,
    width_m: float = 0.8,
    z_bottom_m: float = 0.0,
    z_top_m: float = 1.4,
) -> Part:
    return Part(
        kind=kind,
        length_m=length_m,
        width_m=width_m,
        offset_x_m=0.0,
        offset_y_m=0.0,
        angle_deg=0.0,
        z_bottom_m=z_bottom_m,
        z_top_m=z_top_m,
    )


def _ok_aircraft(
    plane_id: str = "test_plane",
    *,
    movement_mode: str = "always_own_gear",
    turn_radius_m: float | None = 5.0,
) -> Aircraft:
    return Aircraft(
        id=plane_id,
        name=f"Test Plane {plane_id}",
        wing_position="high",
        gear="tailwheel",
        movement_mode=movement_mode,  # type: ignore[arg-type]
        turn_radius_m=turn_radius_m,
        measured=False,
        parts=(_ok_part(),),
    )


def _ok_hangar() -> Hangar:
    return Hangar(
        length_m=25.0,
        width_m=18.0,
        door=Door(center_x_m=9.0, width_m=12.0),
        maintenance_bay=MaintenanceBay(center_x_m=9.0, width_m=4.0, depth_m=9.0),
        clearance_m=0.3,
        wing_layer_clearance_m=0.2,
    )


class TestPart:
    def test_valid_construction(self) -> None:
        p = _ok_part()
        assert p.kind == "fuselage"
        assert p.z_top_m > p.z_bottom_m

    @pytest.mark.parametrize("kind", ["", "fueslage", "Wing", "engine", "rotor"])
    def test_invalid_kind_rejected(self, kind: str) -> None:
        with pytest.raises(ValueError, match="Part.kind must be one of"):
            _ok_part(kind=kind)  # type: ignore[arg-type]

    @pytest.mark.parametrize("kind", ["fuselage", "wing", "strut", "tail"])
    def test_all_valid_kinds_accepted(self, kind: str) -> None:
        p = _ok_part(kind=kind)  # type: ignore[arg-type]
        assert p.kind == kind

    @pytest.mark.parametrize("length_m", [0.0, -1.0])
    def test_non_positive_length_rejected(self, length_m: float) -> None:
        with pytest.raises(ValueError, match="length_m must be positive"):
            _ok_part(length_m=length_m)

    @pytest.mark.parametrize("width_m", [0.0, -0.5])
    def test_non_positive_width_rejected(self, width_m: float) -> None:
        with pytest.raises(ValueError, match="width_m must be positive"):
            _ok_part(width_m=width_m)

    def test_negative_z_bottom_rejected(self) -> None:
        with pytest.raises(ValueError, match="z_bottom_m must be non-negative"):
            _ok_part(z_bottom_m=-0.1, z_top_m=1.0)

    @pytest.mark.parametrize(
        "z_bottom_m, z_top_m",
        [(1.0, 1.0), (2.0, 1.5)],
    )
    def test_z_top_must_exceed_z_bottom(self, z_bottom_m: float, z_top_m: float) -> None:
        with pytest.raises(ValueError, match="z_top_m must exceed z_bottom_m"):
            _ok_part(z_bottom_m=z_bottom_m, z_top_m=z_top_m)


class TestStrutsSpec:
    def test_valid_construction(self) -> None:
        s = StrutsSpec(
            fuselage_attach_x_m=0.5,
            fuselage_attach_y_m=0.4,
            fuselage_attach_z_m=0.5,
            wing_attach_y_m=1.8,
            width_m=0.05,
        )
        assert s.width_m == 0.05

    def test_zero_width_rejected(self) -> None:
        with pytest.raises(ValueError, match="width_m must be positive"):
            StrutsSpec(
                fuselage_attach_x_m=0.0,
                fuselage_attach_y_m=0.4,
                fuselage_attach_z_m=0.5,
                wing_attach_y_m=1.8,
                width_m=0.0,
            )

    def test_zero_wing_attach_rejected(self) -> None:
        with pytest.raises(ValueError, match="wing_attach_y_m must be positive"):
            StrutsSpec(
                fuselage_attach_x_m=0.0,
                fuselage_attach_y_m=0.4,
                fuselage_attach_z_m=0.5,
                wing_attach_y_m=0.0,
                width_m=0.05,
            )

    def test_negative_fuselage_attach_y_rejected(self) -> None:
        with pytest.raises(ValueError, match="fuselage_attach_y_m must be non-negative"):
            StrutsSpec(
                fuselage_attach_x_m=0.0,
                fuselage_attach_y_m=-0.1,
                fuselage_attach_z_m=0.5,
                wing_attach_y_m=1.8,
                width_m=0.05,
            )

    def test_negative_fuselage_attach_z_rejected(self) -> None:
        with pytest.raises(ValueError, match="fuselage_attach_z_m must be non-negative"):
            StrutsSpec(
                fuselage_attach_x_m=0.0,
                fuselage_attach_y_m=0.4,
                fuselage_attach_z_m=-0.1,
                wing_attach_y_m=1.8,
                width_m=0.05,
            )

    def test_wing_attach_inboard_of_fuselage_rejected(self) -> None:
        """A strut must run outward; wing attach inside the fuselage attach is impossible."""
        with pytest.raises(ValueError, match="must be >="):
            StrutsSpec(
                fuselage_attach_x_m=0.0,
                fuselage_attach_y_m=0.8,
                fuselage_attach_z_m=0.5,
                wing_attach_y_m=0.4,  # inboard of fuselage attach — impossible
                width_m=0.05,
            )

    def test_wing_attach_equal_to_fuselage_attach_allowed(self) -> None:
        """Degenerate but legal: strut runs vertically (no outward component)."""
        s = StrutsSpec(
            fuselage_attach_x_m=0.0,
            fuselage_attach_y_m=0.8,
            fuselage_attach_z_m=0.5,
            wing_attach_y_m=0.8,
            width_m=0.05,
        )
        assert s.wing_attach_y_m == s.fuselage_attach_y_m


class TestAircraft:
    def test_valid_own_gear_construction(self) -> None:
        a = _ok_aircraft(movement_mode="always_own_gear", turn_radius_m=5.0)
        assert a.turn_radius_m == 5.0
        assert a.is_cart_eligible is False

    def test_valid_always_cart_no_turn_radius(self) -> None:
        a = _ok_aircraft(movement_mode="always_cart", turn_radius_m=None)
        assert a.turn_radius_m is None
        assert a.is_cart_eligible is False

    def test_cart_eligible_flag(self) -> None:
        a = _ok_aircraft(movement_mode="cart_eligible", turn_radius_m=4.0)
        assert a.is_cart_eligible is True

    def test_empty_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="id must be non-empty"):
            _ok_aircraft(plane_id="")

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="name must be non-empty"):
            Aircraft(
                id="x",
                name="",
                wing_position="high",
                gear="tailwheel",
                movement_mode="always_cart",
                turn_radius_m=None,
                measured=False,
                parts=(_ok_part(),),
            )

    def test_empty_parts_rejected(self) -> None:
        with pytest.raises(ValueError, match="parts must be non-empty"):
            Aircraft(
                id="x",
                name="x",
                wing_position="high",
                gear="tailwheel",
                movement_mode="always_cart",
                turn_radius_m=None,
                measured=False,
                parts=(),
            )

    def test_own_gear_requires_turn_radius(self) -> None:
        with pytest.raises(ValueError, match="turn_radius_m is required"):
            _ok_aircraft(movement_mode="always_own_gear", turn_radius_m=None)

    def test_cart_eligible_requires_turn_radius(self) -> None:
        with pytest.raises(ValueError, match="turn_radius_m is required"):
            _ok_aircraft(movement_mode="cart_eligible", turn_radius_m=None)

    @pytest.mark.parametrize(
        "movement_mode, turn_radius_m",
        [
            ("always_own_gear", 0.0),
            ("always_own_gear", -1.0),
            ("cart_eligible", 0.0),
            ("cart_eligible", -2.0),
        ],
    )
    def test_non_positive_turn_radius_rejected(
        self, movement_mode: str, turn_radius_m: float
    ) -> None:
        with pytest.raises(ValueError, match="turn_radius_m must be positive"):
            _ok_aircraft(movement_mode=movement_mode, turn_radius_m=turn_radius_m)

    def test_always_cart_ignores_turn_radius_value(self) -> None:
        """always_cart short-circuits turn_radius validation; even a nonsensical
        value is accepted because turn_radius is meaningless for cart-only planes.
        Pinning this asymmetry intentionally so a refactor of the conditional
        doesn't silently change behavior."""
        a = _ok_aircraft(movement_mode="always_cart", turn_radius_m=-5.0)
        assert a.turn_radius_m == -5.0

    def test_required_turn_radius_m_returns_float(self) -> None:
        a = _ok_aircraft(movement_mode="always_own_gear", turn_radius_m=5.5)
        assert a.required_turn_radius_m() == 5.5

    def test_required_turn_radius_m_raises_when_none(self) -> None:
        a = _ok_aircraft(movement_mode="always_cart", turn_radius_m=None)
        with pytest.raises(AssertionError, match="turn_radius_m is None"):
            a.required_turn_radius_m()

    @pytest.mark.parametrize("wing_position", ["", "middle", "High", "MID", "bottom"])
    def test_invalid_wing_position_rejected(self, wing_position: str) -> None:
        with pytest.raises(ValueError, match="wing_position must be one of"):
            Aircraft(
                id="x",
                name="X",
                wing_position=wing_position,  # type: ignore[arg-type]
                gear="tailwheel",
                movement_mode="always_cart",
                turn_radius_m=None,
                measured=False,
                parts=(_ok_part(),),
            )

    @pytest.mark.parametrize("gear", ["", "skis", "Tailwheel", "floats", "TRICYCLE"])
    def test_invalid_gear_rejected(self, gear: str) -> None:
        with pytest.raises(ValueError, match="gear must be one of"):
            Aircraft(
                id="x",
                name="X",
                wing_position="high",
                gear=gear,  # type: ignore[arg-type]
                movement_mode="always_cart",
                turn_radius_m=None,
                measured=False,
                parts=(_ok_part(),),
            )

    @pytest.mark.parametrize(
        "movement_mode", ["", "always_carts", "OWN_GEAR", "cart-eligible", "anywhere"]
    )
    def test_invalid_movement_mode_rejected(self, movement_mode: str) -> None:
        with pytest.raises(ValueError, match="movement_mode must be one of"):
            Aircraft(
                id="x",
                name="X",
                wing_position="high",
                gear="tailwheel",
                movement_mode=movement_mode,  # type: ignore[arg-type]
                turn_radius_m=5.0,
                measured=False,
                parts=(_ok_part(),),
            )


class TestDoor:
    def test_valid_construction(self) -> None:
        d = Door(center_x_m=9.0, width_m=12.0)
        assert d.width_m == 12.0

    def test_zero_width_rejected(self) -> None:
        with pytest.raises(ValueError, match="width_m must be positive"):
            Door(center_x_m=9.0, width_m=0.0)

    def test_negative_center_rejected(self) -> None:
        with pytest.raises(ValueError, match="center_x_m must be non-negative"):
            Door(center_x_m=-1.0, width_m=12.0)


class TestMaintenanceBay:
    def test_valid_construction(self) -> None:
        m = MaintenanceBay(center_x_m=13.5, width_m=9.0, depth_m=9.0)
        assert m.depth_m == 9.0
        assert m.center_x_m == 13.5
        assert m.width_m == 9.0

    @pytest.mark.parametrize("depth_m", [0.0, -1.0])
    def test_non_positive_depth_rejected(self, depth_m: float) -> None:
        with pytest.raises(ValueError, match="depth_m must be positive"):
            MaintenanceBay(center_x_m=9.0, width_m=4.0, depth_m=depth_m)

    @pytest.mark.parametrize("center_x_m", [0.0, -1.0])
    def test_non_positive_center_x_rejected(self, center_x_m: float) -> None:
        with pytest.raises(ValueError, match="center_x_m must be positive"):
            MaintenanceBay(center_x_m=center_x_m, width_m=4.0, depth_m=9.0)

    @pytest.mark.parametrize("width_m", [0.0, -2.0])
    def test_non_positive_width_rejected(self, width_m: float) -> None:
        with pytest.raises(ValueError, match="width_m must be positive"):
            MaintenanceBay(center_x_m=9.0, width_m=width_m, depth_m=9.0)


class TestHangar:
    def test_valid_construction(self) -> None:
        h = _ok_hangar()
        assert h.length_m == 25.0

    @pytest.mark.parametrize("length_m", [0.0, -5.0])
    def test_non_positive_length_rejected(self, length_m: float) -> None:
        with pytest.raises(ValueError, match="length_m must be positive"):
            Hangar(
                length_m=length_m,
                width_m=18.0,
                door=Door(center_x_m=9.0, width_m=12.0),
                maintenance_bay=MaintenanceBay(center_x_m=9.0, width_m=4.0, depth_m=9.0),
                clearance_m=0.3,
                wing_layer_clearance_m=0.2,
            )

    @pytest.mark.parametrize("width_m", [0.0, -3.0])
    def test_non_positive_width_rejected(self, width_m: float) -> None:
        with pytest.raises(ValueError, match="width_m must be positive"):
            Hangar(
                length_m=25.0,
                width_m=width_m,
                door=Door(center_x_m=9.0, width_m=12.0)
                if width_m > 12
                else Door(center_x_m=1.0, width_m=0.5),
                maintenance_bay=MaintenanceBay(center_x_m=9.0, width_m=4.0, depth_m=9.0),
                clearance_m=0.3,
                wing_layer_clearance_m=0.2,
            )

    def test_negative_clearance_rejected(self) -> None:
        with pytest.raises(ValueError, match="clearance_m must be non-negative"):
            Hangar(
                length_m=25.0,
                width_m=18.0,
                door=Door(center_x_m=9.0, width_m=12.0),
                maintenance_bay=MaintenanceBay(center_x_m=9.0, width_m=4.0, depth_m=9.0),
                clearance_m=-0.1,
                wing_layer_clearance_m=0.2,
            )

    def test_zero_clearance_allowed(self) -> None:
        h = Hangar(
            length_m=25.0,
            width_m=18.0,
            door=Door(center_x_m=9.0, width_m=12.0),
            maintenance_bay=MaintenanceBay(center_x_m=9.0, width_m=4.0, depth_m=9.0),
            clearance_m=0.0,
            wing_layer_clearance_m=0.0,
        )
        assert h.clearance_m == 0.0

    def test_door_overflows_left(self) -> None:
        with pytest.raises(ValueError, match="doesn't fit in hangar width"):
            Hangar(
                length_m=25.0,
                width_m=18.0,
                door=Door(center_x_m=4.0, width_m=12.0),  # left edge at -2
                maintenance_bay=MaintenanceBay(center_x_m=9.0, width_m=4.0, depth_m=9.0),
                clearance_m=0.3,
                wing_layer_clearance_m=0.2,
            )

    def test_door_overflows_right(self) -> None:
        with pytest.raises(ValueError, match="doesn't fit in hangar width"):
            Hangar(
                length_m=25.0,
                width_m=18.0,
                door=Door(center_x_m=15.0, width_m=12.0),  # right edge at 21
                maintenance_bay=MaintenanceBay(center_x_m=9.0, width_m=4.0, depth_m=9.0),
                clearance_m=0.3,
                wing_layer_clearance_m=0.2,
            )

    def test_maintenance_bay_too_deep(self) -> None:
        with pytest.raises(ValueError, match="must be strictly less than"):
            Hangar(
                length_m=25.0,
                width_m=18.0,
                door=Door(center_x_m=9.0, width_m=12.0),
                maintenance_bay=MaintenanceBay(center_x_m=9.0, width_m=4.0, depth_m=30.0),
                clearance_m=0.3,
                wing_layer_clearance_m=0.2,
            )

    def test_maintenance_bay_equal_to_length_rejected(self) -> None:
        """Bay-depth == hangar-length leaves zero parking area; rejected."""
        with pytest.raises(ValueError, match="must be strictly less than"):
            Hangar(
                length_m=25.0,
                width_m=18.0,
                door=Door(center_x_m=9.0, width_m=12.0),
                maintenance_bay=MaintenanceBay(center_x_m=9.0, width_m=4.0, depth_m=25.0),
                clearance_m=0.3,
                wing_layer_clearance_m=0.2,
            )

    def test_door_flush_with_left_wall_allowed(self) -> None:
        """Door's left edge exactly at x=0 is a legal boundary."""
        h = Hangar(
            length_m=25.0,
            width_m=18.0,
            door=Door(center_x_m=6.0, width_m=12.0),
            maintenance_bay=MaintenanceBay(center_x_m=9.0, width_m=4.0, depth_m=9.0),
            clearance_m=0.3,
            wing_layer_clearance_m=0.2,
        )
        assert h.door.center_x_m == 6.0

    def test_door_flush_with_right_wall_allowed(self) -> None:
        """Door's right edge exactly at x=width_m is a legal boundary."""
        h = Hangar(
            length_m=25.0,
            width_m=18.0,
            door=Door(center_x_m=12.0, width_m=12.0),
            maintenance_bay=MaintenanceBay(center_x_m=9.0, width_m=4.0, depth_m=9.0),
            clearance_m=0.3,
            wing_layer_clearance_m=0.2,
        )
        assert h.door.center_x_m == 12.0

    def test_maintenance_bay_overflows_left(self) -> None:
        with pytest.raises(ValueError, match="MaintenanceBay.*doesn't fit in hangar width"):
            Hangar(
                length_m=25.0,
                width_m=18.0,
                door=Door(center_x_m=9.0, width_m=12.0),
                # bay center at 4, width 10 → left edge at -1
                maintenance_bay=MaintenanceBay(center_x_m=4.0, width_m=10.0, depth_m=9.0),
                clearance_m=0.3,
                wing_layer_clearance_m=0.2,
            )

    def test_maintenance_bay_overflows_right(self) -> None:
        with pytest.raises(ValueError, match="MaintenanceBay.*doesn't fit in hangar width"):
            Hangar(
                length_m=25.0,
                width_m=18.0,
                door=Door(center_x_m=9.0, width_m=12.0),
                # bay center at 15, width 10 → right edge at 20 > width=18
                maintenance_bay=MaintenanceBay(center_x_m=15.0, width_m=10.0, depth_m=9.0),
                clearance_m=0.3,
                wing_layer_clearance_m=0.2,
            )

    def test_maintenance_bay_flush_with_left_wall_allowed(self) -> None:
        """Bay's left edge exactly at x=0 is a legal boundary."""
        h = Hangar(
            length_m=25.0,
            width_m=18.0,
            door=Door(center_x_m=9.0, width_m=12.0),
            maintenance_bay=MaintenanceBay(center_x_m=4.0, width_m=8.0, depth_m=9.0),
            clearance_m=0.3,
            wing_layer_clearance_m=0.2,
        )
        assert h.maintenance_bay.center_x_m == 4.0

    def test_maintenance_bay_flush_with_right_wall_allowed(self) -> None:
        """Bay's right edge exactly at x=width_m is a legal boundary."""
        h = Hangar(
            length_m=25.0,
            width_m=18.0,
            door=Door(center_x_m=9.0, width_m=12.0),
            maintenance_bay=MaintenanceBay(center_x_m=14.0, width_m=8.0, depth_m=9.0),
            clearance_m=0.3,
            wing_layer_clearance_m=0.2,
        )
        assert h.maintenance_bay.center_x_m == 14.0


class TestPlacement:
    def test_valid_construction(self) -> None:
        p = Placement(plane_id="x", x_m=1.0, y_m=2.0, heading_deg=45.0, on_carts=False)
        assert p.heading_deg == 45.0

    def test_empty_plane_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="plane_id must be non-empty"):
            Placement(plane_id="", x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=False)


class TestLayout:
    def _fleet_of(self, *aircraft: Aircraft) -> dict[str, Aircraft]:
        return {a.id: a for a in aircraft}

    def test_valid_construction(self) -> None:
        a = _ok_aircraft("foo", movement_mode="always_own_gear", turn_radius_m=5.0)
        layout = Layout(
            fleet=self._fleet_of(a),
            hangar=_ok_hangar(),
            placements=(
                Placement(plane_id="foo", x_m=5.0, y_m=10.0, heading_deg=0.0, on_carts=False),
            ),
        )
        assert layout.maintenance_plane is None
        assert len(layout.placements) == 1

    def test_unknown_plane_id_rejected(self) -> None:
        a = _ok_aircraft("foo", movement_mode="always_own_gear", turn_radius_m=5.0)
        with pytest.raises(ValueError, match="unknown plane_id 'bar'"):
            Layout(
                fleet=self._fleet_of(a),
                hangar=_ok_hangar(),
                placements=(
                    Placement(
                        plane_id="bar",
                        x_m=0.0,
                        y_m=0.0,
                        heading_deg=0.0,
                        on_carts=False,
                    ),
                ),
            )

    def test_duplicate_placement_rejected(self) -> None:
        a = _ok_aircraft("foo", movement_mode="always_own_gear", turn_radius_m=5.0)
        with pytest.raises(ValueError, match="Duplicate placement"):
            Layout(
                fleet=self._fleet_of(a),
                hangar=_ok_hangar(),
                placements=(
                    Placement(
                        plane_id="foo",
                        x_m=0.0,
                        y_m=0.0,
                        heading_deg=0.0,
                        on_carts=False,
                    ),
                    Placement(
                        plane_id="foo",
                        x_m=5.0,
                        y_m=5.0,
                        heading_deg=0.0,
                        on_carts=False,
                    ),
                ),
            )

    def test_always_cart_requires_on_carts_true(self) -> None:
        a = _ok_aircraft("foo", movement_mode="always_cart", turn_radius_m=None)
        with pytest.raises(ValueError, match="must have on_carts=True"):
            Layout(
                fleet=self._fleet_of(a),
                hangar=_ok_hangar(),
                placements=(
                    Placement(
                        plane_id="foo",
                        x_m=0.0,
                        y_m=0.0,
                        heading_deg=0.0,
                        on_carts=False,
                    ),
                ),
            )

    def test_always_own_gear_rejects_on_carts_true(self) -> None:
        a = _ok_aircraft("foo", movement_mode="always_own_gear", turn_radius_m=5.0)
        with pytest.raises(ValueError, match="must have on_carts=False"):
            Layout(
                fleet=self._fleet_of(a),
                hangar=_ok_hangar(),
                placements=(
                    Placement(plane_id="foo", x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=True),
                ),
            )

    def test_cart_rule_allows_one_cart_eligible_on_carts(self) -> None:
        a = _ok_aircraft("foo", movement_mode="cart_eligible", turn_radius_m=4.0)
        b = _ok_aircraft("bar", movement_mode="cart_eligible", turn_radius_m=4.0)
        layout = Layout(
            fleet=self._fleet_of(a, b),
            hangar=_ok_hangar(),
            placements=(
                Placement(plane_id="foo", x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=True),
                Placement(plane_id="bar", x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False),
            ),
        )
        assert len(layout.placements) == 2

    def test_cart_rule_rejects_two_cart_eligible_on_carts(self) -> None:
        a = _ok_aircraft("foo", movement_mode="cart_eligible", turn_radius_m=4.0)
        b = _ok_aircraft("bar", movement_mode="cart_eligible", turn_radius_m=4.0)
        with pytest.raises(ValueError, match="At most one cart_eligible"):
            Layout(
                fleet=self._fleet_of(a, b),
                hangar=_ok_hangar(),
                placements=(
                    Placement(plane_id="foo", x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=True),
                    Placement(plane_id="bar", x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=True),
                ),
            )

    def test_maintenance_plane_must_be_in_fleet(self) -> None:
        a = _ok_aircraft("foo", movement_mode="always_own_gear", turn_radius_m=5.0)
        with pytest.raises(ValueError, match="not in fleet"):
            Layout(
                fleet=self._fleet_of(a),
                hangar=_ok_hangar(),
                placements=(
                    Placement(
                        plane_id="foo",
                        x_m=0.0,
                        y_m=0.0,
                        heading_deg=0.0,
                        on_carts=False,
                    ),
                ),
                maintenance_plane="ghost",
            )

    def test_maintenance_plane_must_NOT_be_in_placements(self) -> None:
        """The occupant is treated as 'away' — it must not also appear placed.

        This is the post-#103 invariant flip: previously the maintenance
        plane was required to be placed (in the back strip); now it must
        be absent from ``placements`` because the bay closure removes
        it from the parking problem.
        """
        a = _ok_aircraft("foo", movement_mode="always_own_gear", turn_radius_m=5.0)
        b = _ok_aircraft("bar", movement_mode="always_own_gear", turn_radius_m=5.0)
        with pytest.raises(ValueError, match="must NOT be in placements"):
            Layout(
                fleet=self._fleet_of(a, b),
                hangar=_ok_hangar(),
                placements=(
                    Placement(
                        plane_id="foo",
                        x_m=0.0,
                        y_m=0.0,
                        heading_deg=0.0,
                        on_carts=False,
                    ),
                    Placement(
                        plane_id="bar",
                        x_m=5.0,
                        y_m=5.0,
                        heading_deg=0.0,
                        on_carts=False,
                    ),
                ),
                maintenance_plane="bar",
            )

    def test_maintenance_plane_happy_path(self) -> None:
        """maintenance_plane in fleet, NOT in placements — the new valid shape."""
        a = _ok_aircraft("foo", movement_mode="always_own_gear", turn_radius_m=5.0)
        b = _ok_aircraft("bar", movement_mode="always_own_gear", turn_radius_m=5.0)
        layout = Layout(
            fleet=self._fleet_of(a, b),
            hangar=_ok_hangar(),
            placements=(
                Placement(plane_id="foo", x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=False),
            ),
            maintenance_plane="bar",
        )
        assert layout.maintenance_plane == "bar"

    def test_fleet_key_must_match_aircraft_id(self) -> None:
        a = _ok_aircraft("real_id", movement_mode="always_own_gear", turn_radius_m=5.0)
        with pytest.raises(ValueError, match="does not match its Aircraft.id"):
            Layout(
                fleet={"wrong_key": a},
                hangar=_ok_hangar(),
                placements=(),
            )

    def test_fleet_is_read_only_after_construction(self) -> None:
        """Layout.__post_init__ wraps fleet in MappingProxyType so that
        cross-reference invariants stay valid for the object's lifetime."""
        a = _ok_aircraft("foo", movement_mode="always_own_gear", turn_radius_m=5.0)
        layout = Layout(
            fleet=self._fleet_of(a),
            hangar=_ok_hangar(),
            placements=(
                Placement(plane_id="foo", x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=False),
            ),
        )
        with pytest.raises(TypeError):
            layout.fleet["foo"] = a  # type: ignore[index]
        with pytest.raises(TypeError):
            del layout.fleet["foo"]  # type: ignore[attr-defined]

    def test_fleet_caller_mutation_does_not_leak(self) -> None:
        """The dict the caller passes in is copied before being wrapped, so
        post-construction mutations to the caller's dict don't leak."""
        a = _ok_aircraft("foo", movement_mode="always_own_gear", turn_radius_m=5.0)
        caller_dict = self._fleet_of(a)
        layout = Layout(
            fleet=caller_dict,
            hangar=_ok_hangar(),
            placements=(
                Placement(plane_id="foo", x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=False),
            ),
        )
        del caller_dict["foo"]
        assert "foo" in layout.fleet

    def test_always_cart_planes_do_not_count_against_cart_limit(self) -> None:
        """Two always_cart + one cart_eligible on carts must be allowed:
        the limit is 'at most one cart_eligible on carts', not 'at most
        one anything on carts'."""
        a = _ok_aircraft("a", movement_mode="always_cart", turn_radius_m=None)
        b = _ok_aircraft("b", movement_mode="always_cart", turn_radius_m=None)
        c = _ok_aircraft("c", movement_mode="cart_eligible", turn_radius_m=4.0)
        layout = Layout(
            fleet=self._fleet_of(a, b, c),
            hangar=_ok_hangar(),
            placements=(
                Placement(plane_id="a", x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=True),
                Placement(plane_id="b", x_m=2.0, y_m=0.0, heading_deg=0.0, on_carts=True),
                Placement(plane_id="c", x_m=4.0, y_m=0.0, heading_deg=0.0, on_carts=True),
            ),
        )
        assert len(layout.placements) == 3

    def test_cart_rule_allows_zero_cart_eligible_on_carts(self) -> None:
        a = _ok_aircraft("foo", movement_mode="cart_eligible", turn_radius_m=4.0)
        b = _ok_aircraft("bar", movement_mode="cart_eligible", turn_radius_m=4.0)
        layout = Layout(
            fleet=self._fleet_of(a, b),
            hangar=_ok_hangar(),
            placements=(
                Placement(plane_id="foo", x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=False),
                Placement(plane_id="bar", x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False),
            ),
        )
        assert len(layout.placements) == 2

    def test_empty_layout_valid(self) -> None:
        """A Layout with an empty fleet and no placements is legal (degenerate)."""
        layout = Layout(fleet={}, hangar=_ok_hangar(), placements=())
        assert layout.placements == ()
        assert len(layout.fleet) == 0


class TestConflict:
    def test_one_plane_conflict(self) -> None:
        c = Conflict(kind="maintenance_position", planes=("foo",), detail="not in back zone")
        assert len(c.planes) == 1

    def test_two_plane_conflict(self) -> None:
        c = Conflict(
            kind="wing_strut_overlap",
            planes=("cessna_150", "aviat_husky"),
            detail="wing crosses right strut",
        )
        assert len(c.planes) == 2

    def test_empty_kind_rejected(self) -> None:
        with pytest.raises(ValueError, match="kind must be non-empty"):
            Conflict(kind="", planes=("foo",), detail="x")

    def test_empty_planes_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least one plane id"):
            Conflict(kind="x", planes=(), detail="x")

    def test_too_many_planes_rejected(self) -> None:
        with pytest.raises(ValueError, match="must have 1 or 2 entries"):
            Conflict(kind="x", planes=("a", "b", "c"), detail="x")

    def test_empty_plane_id_in_planes_rejected(self) -> None:
        with pytest.raises(ValueError, match="entries must be non-empty"):
            Conflict(kind="x", planes=("foo", ""), detail="x")

    def test_duplicate_planes_rejected(self) -> None:
        """A pairwise conflict can't list the same plane on both sides."""
        with pytest.raises(ValueError, match="must be distinct"):
            Conflict(kind="x", planes=("foo", "foo"), detail="x")

    def test_single_factory(self) -> None:
        c = Conflict.single(kind="maintenance_position", plane="foo", detail="x")
        assert c.kind == "maintenance_position"
        assert c.planes == ("foo",)

    def test_pair_factory(self) -> None:
        c = Conflict.pair(
            kind="wing_strut_overlap",
            plane_a="foo",
            plane_b="bar",
            detail="x",
        )
        assert c.planes == ("foo", "bar")

    def test_pair_factory_rejects_self_pair(self) -> None:
        """Factory still goes through __post_init__, so the distinct check fires."""
        with pytest.raises(ValueError, match="must be distinct"):
            Conflict.pair(kind="x", plane_a="foo", plane_b="foo", detail="x")


class TestCheckResult:
    def test_empty_is_valid(self) -> None:
        r = CheckResult()
        assert r.valid is True
        assert r.conflicts == ()

    def test_with_conflict_is_invalid(self) -> None:
        c = Conflict(kind="x", planes=("foo",), detail="x")
        r = CheckResult(conflicts=(c,))
        assert r.valid is False
        assert len(r.conflicts) == 1

    def test_default_total_penetration_is_zero(self) -> None:
        """Default-constructed CheckResult has total_penetration_m2 == 0.0."""
        result = CheckResult()
        assert result.total_penetration_m2 == 0.0
        assert result.valid is True

    def test_total_penetration_field_is_independent_of_validity(self) -> None:
        """Explicit penetration is preserved; validity is conflict-derived only."""
        result = CheckResult(total_penetration_m2=2.5)
        assert result.total_penetration_m2 == 2.5
        assert result.valid is True  # derived from conflicts, not penetration

    def test_total_penetration_rejects_nan(self) -> None:
        """NaN would silently corrupt Phase 2a's lexicographic sort key."""
        with pytest.raises(ValueError, match="must be finite"):
            CheckResult(total_penetration_m2=float("nan"))

    def test_total_penetration_rejects_negative(self) -> None:
        """A summed area is non-negative by construction; reject anything else."""
        with pytest.raises(ValueError, match="must be >= 0.0"):
            CheckResult(total_penetration_m2=-0.1)


class TestFrozenBehavior:
    """Cross-cutting: every public dataclass should be frozen."""

    def test_part_is_frozen(self) -> None:
        p = _ok_part()
        with pytest.raises(dataclasses.FrozenInstanceError):
            p.kind = "wing"  # type: ignore[misc]

    def test_aircraft_is_frozen(self) -> None:
        a = _ok_aircraft()
        with pytest.raises(dataclasses.FrozenInstanceError):
            a.measured = True  # type: ignore[misc]

    def test_hangar_is_frozen(self) -> None:
        h = _ok_hangar()
        with pytest.raises(dataclasses.FrozenInstanceError):
            h.length_m = 30.0  # type: ignore[misc]

    def test_layout_is_frozen(self) -> None:
        a = _ok_aircraft("foo", movement_mode="always_own_gear", turn_radius_m=5.0)
        layout = Layout(
            fleet={a.id: a},
            hangar=_ok_hangar(),
            placements=(
                Placement(plane_id="foo", x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=False),
            ),
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            layout.maintenance_plane = "foo"  # type: ignore[misc]


class TestSearchConfig:
    """Construction + post_init invariants for ``SearchConfig`` (spec §4.2 of
    the v0.6.0 solver-polish release adds ``max_restarts``)."""

    def test_max_restarts_default_is_none(self) -> None:
        """``None`` preserves the pre-v0.6.0 wall-clock-only termination."""
        sc = SearchConfig()
        assert sc.max_restarts is None

    def test_max_restarts_positive_accepted(self) -> None:
        sc = SearchConfig(max_restarts=1)
        assert sc.max_restarts == 1
        sc = SearchConfig(max_restarts=42)
        assert sc.max_restarts == 42

    def test_max_restarts_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_restarts"):
            SearchConfig(max_restarts=0)

    def test_max_restarts_negative_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_restarts"):
            SearchConfig(max_restarts=-1)
