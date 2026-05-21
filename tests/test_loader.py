"""Tests for hangarfit.loader.

The loader's main job, besides type translation, is **error message
quality** when YAML edits go wrong. Most tests focus on the failure
paths: which YAML problem produces which LoaderError text.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hangarfit.loader import (
    LoaderError,
    load_fleet,
    load_hangar,
    load_layout,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FLEET_YAML = REPO_ROOT / "data" / "fleet.yaml"
HANGAR_YAML = REPO_ROOT / "data" / "hangar.yaml"
EXAMPLE_LAYOUT = REPO_ROOT / "layouts" / "example.yaml"


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# ----------------------------------------------------------------------------
# Happy-path: load the real bundled data files.
# ----------------------------------------------------------------------------


class TestRealDataFiles:
    """Loading the actual data/ and layouts/ files in the repo."""

    def test_load_fleet(self) -> None:
        fleet = load_fleet(FLEET_YAML)
        assert set(fleet) == {
            "scheibe_falke",
            "aviat_husky",
            "fuji",
            "wild_thing",
            "zlin_savage",
            "cessna_140",
            "cessna_150",
            "ctsl",
            "fk9_mkii",
        }
        # Every fleet aircraft's id matches its dict key
        for k, a in fleet.items():
            assert a.id == k

    def test_strut_braced_planes_have_two_strut_parts_each(self) -> None:
        fleet = load_fleet(FLEET_YAML)
        strut_braced = {
            "aviat_husky",
            "wild_thing",
            "zlin_savage",
            "cessna_140",
            "cessna_150",
            "fk9_mkii",
        }
        for aid in strut_braced:
            struts = [p for p in fleet[aid].parts if p.kind == "strut"]
            assert len(struts) == 2, f"{aid} should have 2 strut parts, got {len(struts)}"

    def test_cantilever_planes_have_no_strut_parts(self) -> None:
        fleet = load_fleet(FLEET_YAML)
        cantilever = {"scheibe_falke", "fuji", "ctsl"}
        for aid in cantilever:
            assert all(p.kind != "strut" for p in fleet[aid].parts), (
                f"{aid} (cantilever) should have no strut parts"
            )

    def test_expanded_struts_are_mirrored(self) -> None:
        """For each strut-braced plane, the two strut parts should have
        equal-and-opposite offset_y_m (mirrored across the y=0 plane)."""
        fleet = load_fleet(FLEET_YAML)
        for aid in ("aviat_husky", "cessna_150", "fk9_mkii"):
            struts = [p for p in fleet[aid].parts if p.kind == "strut"]
            assert len(struts) == 2
            ys = sorted(p.offset_y_m for p in struts)
            assert ys[0] == -ys[1], f"{aid}: strut offsets not mirrored: {ys}"
            assert ys[1] > 0, f"{aid}: at least one strut should be on +y side"

    def test_strut_z_range_uses_wing_z_bottom(self) -> None:
        """Strut z_top should equal the wing's z_bottom_m for each plane."""
        fleet = load_fleet(FLEET_YAML)
        for aid, a in fleet.items():
            wing = next((p for p in a.parts if p.kind == "wing"), None)
            struts = [p for p in a.parts if p.kind == "strut"]
            if not struts:
                continue
            assert wing is not None
            for s in struts:
                assert s.z_top_m == wing.z_bottom_m, (
                    f"{aid} strut z_top={s.z_top_m} != wing z_bottom={wing.z_bottom_m}"
                )

    def test_all_placeholders_flagged(self) -> None:
        """Every aircraft in the bundled fleet.yaml should be flagged as
        unmeasured — a regression guard so we notice if someone forgets
        to flip a flag when adding real measurements."""
        fleet = load_fleet(FLEET_YAML)
        for aid, a in fleet.items():
            assert a.measured is False, f"{aid}: bundled data should be measured: false"

    def test_load_hangar(self) -> None:
        hangar = load_hangar(HANGAR_YAML)
        assert hangar.length_m == 25.0
        assert hangar.width_m == 18.0
        assert hangar.door.center_x_m == 9.0
        assert hangar.maintenance_bay.depth_m == 9.0

    def test_load_example_layout(self) -> None:
        layout = load_layout(EXAMPLE_LAYOUT)
        assert len(layout.placements) == 9
        assert layout.maintenance_plane == "cessna_150"


# ----------------------------------------------------------------------------
# File-level loader errors.
# ----------------------------------------------------------------------------


class TestFleetLoaderErrors:
    def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(LoaderError, match="file not found"):
            load_fleet(tmp_path / "nonexistent.yaml")

    def test_malformed_yaml(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "bad.yaml", "aircraft: [unclosed")
        with pytest.raises(LoaderError, match="YAML parse error"):
            load_fleet(path)

    def test_missing_top_level_aircraft(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "f.yaml", "fleet: []\n")
        with pytest.raises(LoaderError, match="must contain 'aircraft'"):
            load_fleet(path)

    def test_aircraft_not_a_list(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "f.yaml", "aircraft: not_a_list\n")
        with pytest.raises(LoaderError, match="'aircraft' must be a list"):
            load_fleet(path)

    def test_duplicate_aircraft_id(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "f.yaml",
            _fleet_yaml(_aircraft_entry("foo"), _aircraft_entry("foo")),
        )
        with pytest.raises(LoaderError, match="duplicate aircraft id 'foo'"):
            load_fleet(path)

    def test_aircraft_missing_parts(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "f.yaml",
            """
aircraft:
  - id: foo
    name: Foo
    wing_position: high
    gear: tailwheel
    movement_mode: always_own_gear
    turn_radius_m: 5.0
""",
        )
        with pytest.raises(LoaderError, match="aircraft 'foo': 'parts' must be a non-empty list"):
            load_fleet(path)

    def test_part_missing_required_field(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "f.yaml",
            """
aircraft:
  - id: foo
    name: Foo
    wing_position: high
    gear: tailwheel
    movement_mode: always_own_gear
    turn_radius_m: 5.0
    parts:
      - kind: fuselage
        length_m: 7.0
""",
        )
        with pytest.raises(LoaderError, match="missing required field 'width_m'"):
            load_fleet(path)

    def test_invalid_part_kind_propagated_from_model(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "f.yaml",
            """
aircraft:
  - id: foo
    name: Foo
    wing_position: high
    gear: tailwheel
    movement_mode: always_own_gear
    turn_radius_m: 5.0
    parts:
      - kind: fueslage
        length_m: 7.0
        width_m: 0.8
        z_bottom_m: 0.0
        z_top_m: 1.5
""",
        )
        with pytest.raises(LoaderError, match="kind must be one of"):
            load_fleet(path)

    def test_model_validation_error_includes_aircraft_id(self, tmp_path: Path) -> None:
        """Aircraft.__post_init__ ValueError should be re-raised as LoaderError
        with the aircraft id prepended for navigation."""
        path = _write(
            tmp_path / "f.yaml",
            """
aircraft:
  - id: bad_husky
    name: Bad Husky
    wing_position: high
    gear: tailwheel
    movement_mode: always_own_gear
    # turn_radius_m missing — required for always_own_gear
    parts:
      - kind: fuselage
        length_m: 7.0
        width_m: 0.8
        z_bottom_m: 0.0
        z_top_m: 1.5
""",
        )
        with pytest.raises(LoaderError, match="aircraft 'bad_husky'.*turn_radius_m is required"):
            load_fleet(path)


# ----------------------------------------------------------------------------
# Strut expansion.
# ----------------------------------------------------------------------------


class TestStrutExpansion:
    def test_struts_without_wing_part_rejected(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "f.yaml",
            """
aircraft:
  - id: weird
    name: Weird
    wing_position: high
    gear: tailwheel
    movement_mode: always_own_gear
    turn_radius_m: 5.0
    parts:
      - kind: fuselage
        length_m: 7.0
        width_m: 0.8
        z_bottom_m: 0.0
        z_top_m: 1.5
    struts:
      fuselage_attach_x_m: 0.5
      fuselage_attach_y_m: 0.4
      fuselage_attach_z_m: 0.5
      wing_attach_y_m: 1.8
      width_m: 0.05
""",
        )
        with pytest.raises(LoaderError, match="requires a part of kind 'wing'"):
            load_fleet(path)

    def test_struts_with_low_wing_rejected(self, tmp_path: Path) -> None:
        """A wing whose z_bottom is below or equal to the strut's fuselage
        attach height makes no geometric sense — the strut would point
        downward. Loader catches it with a clear message."""
        path = _write(
            tmp_path / "f.yaml",
            """
aircraft:
  - id: low_wing_with_struts
    name: Bogus
    wing_position: low
    gear: nosewheel
    movement_mode: always_own_gear
    turn_radius_m: 5.0
    parts:
      - kind: fuselage
        length_m: 7.0
        width_m: 0.8
        z_bottom_m: 0.0
        z_top_m: 1.5
      - kind: wing
        length_m: 1.5
        width_m: 9.0
        z_bottom_m: 0.4
        z_top_m: 0.6
    struts:
      fuselage_attach_x_m: 0.5
      fuselage_attach_y_m: 0.4
      fuselage_attach_z_m: 0.5
      wing_attach_y_m: 1.8
      width_m: 0.05
""",
        )
        with pytest.raises(LoaderError, match="Struts only make sense when the wing is above"):
            load_fleet(path)

    def test_strut_span_must_be_positive(self, tmp_path: Path) -> None:
        """StrutsSpec allows wing_attach_y_m == fuselage_attach_y_m (degenerate
        boundary), but the loader requires strict span > 0 to build a usable Part."""
        path = _write(
            tmp_path / "f.yaml",
            """
aircraft:
  - id: degenerate
    name: Degenerate
    wing_position: high
    gear: tailwheel
    movement_mode: always_own_gear
    turn_radius_m: 5.0
    parts:
      - kind: fuselage
        length_m: 7.0
        width_m: 0.8
        z_bottom_m: 0.0
        z_top_m: 1.5
      - kind: wing
        length_m: 1.5
        width_m: 9.0
        z_bottom_m: 2.0
        z_top_m: 2.3
    struts:
      fuselage_attach_x_m: 0.5
      fuselage_attach_y_m: 0.8
      fuselage_attach_z_m: 0.5
      wing_attach_y_m: 0.8
      width_m: 0.05
""",
        )
        with pytest.raises(LoaderError, match="zero outboard span"):
            load_fleet(path)


# ----------------------------------------------------------------------------
# Hangar loader.
# ----------------------------------------------------------------------------


class TestHangarLoaderErrors:
    def test_missing_length(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "h.yaml",
            """
width_m: 18.0
door: {center_x_m: 9, width_m: 12}
maintenance_bay: {depth_m: 9}
""",
        )
        with pytest.raises(LoaderError, match="missing required field 'length_m'"):
            load_hangar(path)

    def test_missing_door_block(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "h.yaml",
            """
length_m: 25.0
width_m: 18.0
maintenance_bay: {depth_m: 9}
""",
        )
        with pytest.raises(LoaderError, match="'door' must be a mapping"):
            load_hangar(path)

    def test_missing_door_center_x(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "h.yaml",
            """
length_m: 25.0
width_m: 18.0
door: {width_m: 12}
maintenance_bay: {depth_m: 9}
""",
        )
        with pytest.raises(LoaderError, match="missing required field 'door.center_x_m'"):
            load_hangar(path)

    def test_door_does_not_fit_propagates_from_model(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "h.yaml",
            """
length_m: 25.0
width_m: 18.0
door: {center_x_m: 1.0, width_m: 12.0}
maintenance_bay: {depth_m: 9}
""",
        )
        with pytest.raises(LoaderError, match="doesn't fit in hangar width"):
            load_hangar(path)

    def test_clearance_defaults_applied(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "h.yaml",
            """
length_m: 25.0
width_m: 18.0
door: {center_x_m: 9.0, width_m: 12.0}
maintenance_bay: {depth_m: 9}
""",
        )
        h = load_hangar(path)
        assert h.clearance_m == 0.3
        assert h.wing_layer_clearance_m == 0.2


# ----------------------------------------------------------------------------
# Layout loader: path resolution + cross-reference errors.
# ----------------------------------------------------------------------------


class TestLayoutLoader:
    def _minimal_fleet_and_hangar(self, dir_: Path) -> tuple[Path, Path]:
        fleet = _write(
            dir_ / "fleet.yaml",
            _minimal_aircraft_yaml("foo", movement_mode="always_own_gear", turn_radius_m=5.0),
        )
        hangar = _write(
            dir_ / "hangar.yaml",
            """
length_m: 25.0
width_m: 18.0
door: {center_x_m: 9.0, width_m: 12.0}
maintenance_bay: {depth_m: 9}
""",
        )
        return fleet, hangar

    def test_happy_path(self, tmp_path: Path) -> None:
        self._minimal_fleet_and_hangar(tmp_path)
        layout_path = _write(
            tmp_path / "layout.yaml",
            """
fleet: fleet.yaml
hangar: hangar.yaml
placements:
  - plane: foo
    x_m: 5.0
    y_m: 5.0
    heading_deg: 0
    on_carts: false
""",
        )
        layout = load_layout(layout_path)
        assert len(layout.placements) == 1
        assert layout.placements[0].plane_id == "foo"

    def test_unknown_plane_reference_propagates(self, tmp_path: Path) -> None:
        self._minimal_fleet_and_hangar(tmp_path)
        layout_path = _write(
            tmp_path / "layout.yaml",
            """
fleet: fleet.yaml
hangar: hangar.yaml
placements:
  - plane: ghost
    x_m: 5.0
    y_m: 5.0
    heading_deg: 0
""",
        )
        with pytest.raises(LoaderError, match="unknown plane_id 'ghost'"):
            load_layout(layout_path)

    def test_cart_rule_violation_propagates(self, tmp_path: Path) -> None:
        # Two cart_eligible planes, both on_carts=true
        _write(
            tmp_path / "fleet.yaml",
            _fleet_yaml(
                _aircraft_entry("a", movement_mode="cart_eligible", turn_radius_m=4.0),
                _aircraft_entry("b", movement_mode="cart_eligible", turn_radius_m=4.0),
            ),
        )
        _write(
            tmp_path / "hangar.yaml",
            """
length_m: 25.0
width_m: 18.0
door: {center_x_m: 9.0, width_m: 12.0}
maintenance_bay: {depth_m: 9}
""",
        )
        layout_path = _write(
            tmp_path / "layout.yaml",
            """
fleet: fleet.yaml
hangar: hangar.yaml
placements:
  - {plane: a, x_m: 5, y_m: 5, heading_deg: 0, on_carts: true}
  - {plane: b, x_m: 10, y_m: 5, heading_deg: 0, on_carts: true}
""",
        )
        with pytest.raises(LoaderError, match="At most one cart_eligible"):
            load_layout(layout_path)

    def test_fleet_and_hangar_overrides(self, tmp_path: Path) -> None:
        """When fleet/hangar are supplied as args, the YAML's references are ignored."""
        fleet_path, hangar_path = self._minimal_fleet_and_hangar(tmp_path)
        # Layout YAML references nonexistent files — they should be ignored.
        layout_path = _write(
            tmp_path / "layout.yaml",
            """
fleet: does-not-exist.yaml
hangar: also-does-not-exist.yaml
placements:
  - {plane: foo, x_m: 5, y_m: 5, heading_deg: 0, on_carts: false}
""",
        )
        layout = load_layout(
            layout_path,
            fleet=load_fleet(fleet_path),
            hangar=load_hangar(hangar_path),
        )
        assert "foo" in layout.fleet

    def test_layout_missing_fleet_ref(self, tmp_path: Path) -> None:
        layout_path = _write(
            tmp_path / "layout.yaml",
            """
hangar: hangar.yaml
placements: []
""",
        )
        with pytest.raises(LoaderError, match="'fleet' field is required"):
            load_layout(layout_path)

    def test_layout_missing_hangar_ref(self, tmp_path: Path) -> None:
        _write(tmp_path / "fleet.yaml", _minimal_aircraft_yaml("foo", movement_mode="always_own_gear", turn_radius_m=5.0))
        layout_path = _write(
            tmp_path / "layout.yaml",
            """
fleet: fleet.yaml
placements: []
""",
        )
        with pytest.raises(LoaderError, match="'hangar' field is required"):
            load_layout(layout_path)

    def test_placement_missing_required_field(self, tmp_path: Path) -> None:
        self._minimal_fleet_and_hangar(tmp_path)
        layout_path = _write(
            tmp_path / "layout.yaml",
            """
fleet: fleet.yaml
hangar: hangar.yaml
placements:
  - {plane: foo, x_m: 5, y_m: 5}
""",
        )
        with pytest.raises(LoaderError, match="missing required field 'heading_deg'"):
            load_layout(layout_path)


# ----------------------------------------------------------------------------
# YAML helpers
# ----------------------------------------------------------------------------


def _aircraft_entry(
    plane_id: str,
    *,
    movement_mode: str = "always_own_gear",
    turn_radius_m: float | None = 5.0,
) -> str:
    """One aircraft entry for a fleet YAML (no top-level 'aircraft:' key).
    Compose multiple entries via :func:`_fleet_yaml`."""
    radius_yaml = (
        f"turn_radius_m: {turn_radius_m}" if turn_radius_m is not None else "turn_radius_m: null"
    )
    return f"""\
  - id: {plane_id}
    name: "Plane {plane_id}"
    wing_position: high
    gear: tailwheel
    movement_mode: {movement_mode}
    {radius_yaml}
    measured: false
    parts:
      - kind: fuselage
        length_m: 7.0
        width_m: 0.8
        z_bottom_m: 0.0
        z_top_m: 1.5
      - kind: wing
        length_m: 1.5
        width_m: 9.0
        z_bottom_m: 2.0
        z_top_m: 2.3
"""


def _fleet_yaml(*entries: str) -> str:
    """Wrap one or more aircraft entries in a fleet YAML document."""
    return "aircraft:\n" + "".join(entries)


def _minimal_aircraft_yaml(
    plane_id: str,
    *,
    movement_mode: str = "always_own_gear",
    turn_radius_m: float | None = 5.0,
) -> str:
    """Convenience: build a complete single-aircraft fleet YAML."""
    return _fleet_yaml(_aircraft_entry(plane_id, movement_mode=movement_mode, turn_radius_m=turn_radius_m))
