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
    struts: StrutsSpec | None = None,
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
        struts=struts,
    )


def _ok_hangar() -> Hangar:
    return Hangar(
        length_m=25.0,
        width_m=18.0,
        door=Door(center_x_m=9.0, width_m=12.0),
        maintenance_bay=MaintenanceBay(depth_m=9.0),
        clearance_m=0.3,
        wing_layer_clearance_m=0.2,
    )


class TestPart:
    def test_valid_construction(self) -> None:
        p = _ok_part()
        assert p.kind == "fuselage"
        assert p.z_top_m > p.z_bottom_m

    def test_empty_kind_rejected(self) -> None:
        with pytest.raises(ValueError, match="kind must be non-empty"):
            _ok_part(kind="")

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
    def test_z_top_must_exceed_z_bottom(
        self, z_bottom_m: float, z_top_m: float
    ) -> None:
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

    @pytest.mark.parametrize("turn_radius_m", [0.0, -1.0])
    def test_non_positive_turn_radius_rejected(self, turn_radius_m: float) -> None:
        with pytest.raises(ValueError, match="turn_radius_m must be positive"):
            _ok_aircraft(movement_mode="always_own_gear", turn_radius_m=turn_radius_m)


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
        m = MaintenanceBay(depth_m=9.0)
        assert m.depth_m == 9.0

    @pytest.mark.parametrize("depth_m", [0.0, -1.0])
    def test_non_positive_depth_rejected(self, depth_m: float) -> None:
        with pytest.raises(ValueError, match="depth_m must be positive"):
            MaintenanceBay(depth_m=depth_m)


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
                maintenance_bay=MaintenanceBay(depth_m=9.0),
                clearance_m=0.3,
                wing_layer_clearance_m=0.2,
            )

    def test_negative_clearance_rejected(self) -> None:
        with pytest.raises(ValueError, match="clearance_m must be non-negative"):
            Hangar(
                length_m=25.0,
                width_m=18.0,
                door=Door(center_x_m=9.0, width_m=12.0),
                maintenance_bay=MaintenanceBay(depth_m=9.0),
                clearance_m=-0.1,
                wing_layer_clearance_m=0.2,
            )

    def test_zero_clearance_allowed(self) -> None:
        h = Hangar(
            length_m=25.0,
            width_m=18.0,
            door=Door(center_x_m=9.0, width_m=12.0),
            maintenance_bay=MaintenanceBay(depth_m=9.0),
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
                maintenance_bay=MaintenanceBay(depth_m=9.0),
                clearance_m=0.3,
                wing_layer_clearance_m=0.2,
            )

    def test_door_overflows_right(self) -> None:
        with pytest.raises(ValueError, match="doesn't fit in hangar width"):
            Hangar(
                length_m=25.0,
                width_m=18.0,
                door=Door(center_x_m=15.0, width_m=12.0),  # right edge at 21
                maintenance_bay=MaintenanceBay(depth_m=9.0),
                clearance_m=0.3,
                wing_layer_clearance_m=0.2,
            )

    def test_maintenance_bay_too_deep(self) -> None:
        with pytest.raises(ValueError, match="exceeds Hangar.length_m"):
            Hangar(
                length_m=25.0,
                width_m=18.0,
                door=Door(center_x_m=9.0, width_m=12.0),
                maintenance_bay=MaintenanceBay(depth_m=30.0),
                clearance_m=0.3,
                wing_layer_clearance_m=0.2,
            )


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
                    Placement(plane_id="bar", x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=False),
                ),
            )

    def test_duplicate_placement_rejected(self) -> None:
        a = _ok_aircraft("foo", movement_mode="always_own_gear", turn_radius_m=5.0)
        with pytest.raises(ValueError, match="Duplicate placement"):
            Layout(
                fleet=self._fleet_of(a),
                hangar=_ok_hangar(),
                placements=(
                    Placement(plane_id="foo", x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=False),
                    Placement(plane_id="foo", x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False),
                ),
            )

    def test_always_cart_requires_on_carts_true(self) -> None:
        a = _ok_aircraft("foo", movement_mode="always_cart", turn_radius_m=None)
        with pytest.raises(ValueError, match="must have on_carts=True"):
            Layout(
                fleet=self._fleet_of(a),
                hangar=_ok_hangar(),
                placements=(
                    Placement(plane_id="foo", x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=False),
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
                    Placement(plane_id="foo", x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=False),
                ),
                maintenance_plane="ghost",
            )

    def test_maintenance_plane_must_be_placed(self) -> None:
        a = _ok_aircraft("foo", movement_mode="always_own_gear", turn_radius_m=5.0)
        b = _ok_aircraft("bar", movement_mode="always_own_gear", turn_radius_m=5.0)
        with pytest.raises(ValueError, match="is not placed"):
            Layout(
                fleet=self._fleet_of(a, b),
                hangar=_ok_hangar(),
                placements=(
                    Placement(plane_id="foo", x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=False),
                ),
                maintenance_plane="bar",
            )


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

    def test_valid_tracks_conflicts_addition(self) -> None:
        c1 = Conflict(kind="x", planes=("foo",), detail="x")
        c2 = Conflict(kind="y", planes=("bar", "baz"), detail="y")
        r0 = CheckResult()
        r1 = CheckResult(conflicts=(c1,))
        r2 = CheckResult(conflicts=(c1, c2))
        assert (r0.valid, r1.valid, r2.valid) == (True, False, False)
        assert (len(r0.conflicts), len(r1.conflicts), len(r2.conflicts)) == (0, 1, 2)


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
