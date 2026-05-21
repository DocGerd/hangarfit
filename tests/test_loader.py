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

    def test_bundled_data_files_exist(self) -> None:
        """Sentinel: if a bundled data file is renamed/deleted, every other
        test in this class will produce a confusing 'file not found' error.
        This test surfaces that scenario with a clearly intent-revealing
        failure first."""
        assert FLEET_YAML.exists(), f"bundled fleet file missing: {FLEET_YAML}"
        assert HANGAR_YAML.exists(), f"bundled hangar file missing: {HANGAR_YAML}"
        assert EXAMPLE_LAYOUT.exists(), f"bundled example layout missing: {EXAMPLE_LAYOUT}"

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
        to flip a flag when adding real measurements.

        Tripwire: when the first aircraft actually gets measured, this
        test should be flipped (assert per-aircraft expected state)
        rather than deleted."""
        fleet = load_fleet(FLEET_YAML)
        for aid, a in fleet.items():
            assert a.measured is False, f"{aid}: bundled data should be measured: false"

    def test_representative_values_pinned(self) -> None:
        """One representative dimension per aircraft (pinned). Guards
        against silent edits to fleet.yaml values that wouldn't fail
        any other test (count/shape/property tests would all still pass
        if e.g. the Husky's wingspan were typo'd from 10.7 to 1.07)."""
        fleet = load_fleet(FLEET_YAML)
        # Wingspan = width_m of the wing part.
        wing_widths = {
            aid: next(p.width_m for p in a.parts if p.kind == "wing")
            for aid, a in fleet.items()
        }
        # Loose ballpark check: every wingspan in [4, 20] m (sanity range
        # for the fleet). A decimal-point typo (10.7 → 1.07) is caught.
        for aid, w in wing_widths.items():
            assert 4.0 < w < 20.0, f"{aid}: wingspan {w} m is outside sanity range"
        # And pin a couple of specific values so single-plane edits surface.
        assert wing_widths["scheibe_falke"] == 16.6
        assert wing_widths["fuji"] == 9.4

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

    def test_aircraft_entry_not_a_mapping(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "f.yaml",
            """
aircraft:
  - just_a_string
""",
        )
        with pytest.raises(LoaderError, match="must be a mapping"):
            load_fleet(path)

    def test_aircraft_missing_top_level_field(self, tmp_path: Path) -> None:
        """An aircraft entry missing 'name' (or any other top-level required
        field) should produce a clear 'missing required field' message,
        not a bare quoted KeyError."""
        path = _write(
            tmp_path / "f.yaml",
            """
aircraft:
  - id: foo
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
""",
        )
        with pytest.raises(LoaderError, match="missing required field 'name'"):
            load_fleet(path)

    def test_aircraft_missing_id_uses_fallback(self, tmp_path: Path) -> None:
        """When the aircraft entry has no 'id', the loader uses '#<index>'
        as the identifier in the wrapped error message."""
        path = _write(
            tmp_path / "f.yaml",
            """
aircraft:
  - name: Anonymous
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
""",
        )
        with pytest.raises(LoaderError, match="aircraft '#0'.*missing required field 'id'"):
            load_fleet(path)

    def test_null_numeric_field_clear_error(self, tmp_path: Path) -> None:
        """`length_m:` (YAML null) used to leak as TypeError; now caught."""
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
        length_m:
        width_m: 0.8
        z_bottom_m: 0.0
        z_top_m: 1.5
""",
        )
        with pytest.raises(LoaderError, match="'parts\\[0\\].length_m'.*expected number, got null"):
            load_fleet(path)

    def test_quoted_bool_for_measured_rejected(self, tmp_path: Path) -> None:
        """`measured: "false"` (quoted) would silently be True via bool()."""
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
    measured: "false"
    parts:
      - kind: fuselage
        length_m: 7.0
        width_m: 0.8
        z_bottom_m: 0.0
        z_top_m: 1.5
""",
        )
        with pytest.raises(LoaderError, match="'measured': expected boolean"):
            load_fleet(path)

    def test_invalid_movement_mode_caught(self, tmp_path: Path) -> None:
        """Typo in movement_mode used to leak silently past the Layout cart
        rule (Aircraft.__post_init__ now validates the Literal set)."""
        path = _write(
            tmp_path / "f.yaml",
            """
aircraft:
  - id: foo
    name: Foo
    wing_position: high
    gear: tailwheel
    movement_mode: always_carts
    turn_radius_m: 5.0
    parts:
      - kind: fuselage
        length_m: 7.0
        width_m: 0.8
        z_bottom_m: 0.0
        z_top_m: 1.5
""",
        )
        with pytest.raises(LoaderError, match="movement_mode must be one of"):
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

    def test_first_wing_part_drives_strut_z_top(self, tmp_path: Path) -> None:
        """If an aircraft has multiple wing parts (unusual: split-wing, twin
        booms), the strut z_top is inferred from the FIRST wing part. Pin
        this behavior so refactoring to last-wins or raising would surface."""
        path = _write(
            tmp_path / "f.yaml",
            """
aircraft:
  - id: split_wing
    name: Split Wing
    wing_position: high
    gear: tailwheel
    movement_mode: always_own_gear
    turn_radius_m: 5.0
    parts:
      - kind: wing
        length_m: 1.5
        width_m: 5.0
        z_bottom_m: 2.0
        z_top_m: 2.3
      - kind: wing
        length_m: 1.5
        width_m: 5.0
        z_bottom_m: 2.5
        z_top_m: 2.8
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
        fleet = load_fleet(path)
        struts = [p for p in fleet["split_wing"].parts if p.kind == "strut"]
        assert len(struts) == 2
        # First wing (z_bottom=2.0) wins, NOT the second wing (z_bottom=2.5).
        assert all(s.z_top_m == 2.0 for s in struts)

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

    def test_top_level_not_a_mapping(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "h.yaml", "- a\n- b\n")
        with pytest.raises(LoaderError, match="top-level must be a mapping"):
            load_hangar(path)

    def test_missing_maintenance_bay_block(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "h.yaml",
            """
length_m: 25.0
width_m: 18.0
door: {center_x_m: 9, width_m: 12}
""",
        )
        with pytest.raises(LoaderError, match="'maintenance_bay' must be a mapping"):
            load_hangar(path)

    def test_missing_maintenance_bay_depth(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "h.yaml",
            """
length_m: 25.0
width_m: 18.0
door: {center_x_m: 9, width_m: 12}
maintenance_bay: {}
""",
        )
        with pytest.raises(LoaderError, match="missing required field 'maintenance_bay.depth_m'"):
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
        """When fleet/hangar are supplied as args AND the YAML omits those
        fields, the overrides are used. (When BOTH are present, the loader
        refuses to disambiguate — see test_override_and_yaml_ref_conflict_*.)"""
        fleet_path, hangar_path = self._minimal_fleet_and_hangar(tmp_path)
        layout_path = _write(
            tmp_path / "layout.yaml",
            """
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

    def test_top_level_not_a_mapping(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "layout.yaml", "- a\n- b\n")
        with pytest.raises(LoaderError, match="top-level must be a mapping"):
            load_layout(path)

    def test_placements_not_a_list(self, tmp_path: Path) -> None:
        self._minimal_fleet_and_hangar(tmp_path)
        layout_path = _write(
            tmp_path / "layout.yaml",
            """
fleet: fleet.yaml
hangar: hangar.yaml
placements: not_a_list
""",
        )
        with pytest.raises(LoaderError, match="'placements' must be a list"):
            load_layout(layout_path)

    def test_placement_entry_not_a_mapping(self, tmp_path: Path) -> None:
        self._minimal_fleet_and_hangar(tmp_path)
        layout_path = _write(
            tmp_path / "layout.yaml",
            """
fleet: fleet.yaml
hangar: hangar.yaml
placements:
  - just_a_string
""",
        )
        with pytest.raises(LoaderError, match="placement.*must be a mapping"):
            load_layout(layout_path)

    def test_quoted_bool_for_on_carts_rejected(self, tmp_path: Path) -> None:
        """The canonical YAML footgun: `on_carts: "false"` (quoted) used to
        silently become True via bool('false')."""
        self._minimal_fleet_and_hangar(tmp_path)
        layout_path = _write(
            tmp_path / "layout.yaml",
            """
fleet: fleet.yaml
hangar: hangar.yaml
placements:
  - {plane: foo, x_m: 5, y_m: 5, heading_deg: 0, on_carts: "false"}
""",
        )
        with pytest.raises(LoaderError, match="'on_carts': expected boolean"):
            load_layout(layout_path)

    def test_maintenance_shorthand_rejected(self, tmp_path: Path) -> None:
        """Typo `maintenance: cessna_150` (no nested `plane:` key) used to
        silently produce maintenance_plane=None."""
        self._minimal_fleet_and_hangar(tmp_path)
        layout_path = _write(
            tmp_path / "layout.yaml",
            """
fleet: fleet.yaml
hangar: hangar.yaml
placements:
  - {plane: foo, x_m: 5, y_m: 5, heading_deg: 0, on_carts: false}
maintenance: foo
""",
        )
        with pytest.raises(LoaderError, match="'maintenance' must be a mapping"):
            load_layout(layout_path)

    def test_maintenance_block_missing_plane_key(self, tmp_path: Path) -> None:
        self._minimal_fleet_and_hangar(tmp_path)
        layout_path = _write(
            tmp_path / "layout.yaml",
            """
fleet: fleet.yaml
hangar: hangar.yaml
placements:
  - {plane: foo, x_m: 5, y_m: 5, heading_deg: 0, on_carts: false}
maintenance:
  comment: forgot the plane key
""",
        )
        with pytest.raises(LoaderError, match="'maintenance' block present but lacks required 'plane'"):
            load_layout(layout_path)

    def test_override_and_yaml_ref_conflict_for_fleet(self, tmp_path: Path) -> None:
        """If the layout YAML has `fleet:` AND the caller passes a fleet
        override, the loader refuses to silently shadow one with the other."""
        fleet_path, hangar_path = self._minimal_fleet_and_hangar(tmp_path)
        layout_path = _write(
            tmp_path / "layout.yaml",
            """
fleet: fleet.yaml
hangar: hangar.yaml
placements: []
""",
        )
        with pytest.raises(LoaderError, match="'fleet' field is set in YAML but a fleet override"):
            load_layout(layout_path, fleet=load_fleet(fleet_path))

    def test_override_and_yaml_ref_conflict_for_hangar(self, tmp_path: Path) -> None:
        fleet_path, hangar_path = self._minimal_fleet_and_hangar(tmp_path)
        layout_path = _write(
            tmp_path / "layout.yaml",
            """
fleet: fleet.yaml
hangar: hangar.yaml
placements: []
""",
        )
        with pytest.raises(LoaderError, match="'hangar' field is set in YAML but a hangar override"):
            load_layout(layout_path, hangar=load_hangar(hangar_path))

    def test_helper_composition_yields_two_aircraft(self, tmp_path: Path) -> None:
        """Pin the helper composition: `_fleet_yaml(entry_a, entry_b)`
        produces a fleet with TWO aircraft (PyYAML's last-key-wins
        regression would have produced one — the original bug)."""
        path = _write(
            tmp_path / "f.yaml",
            _fleet_yaml(
                _aircraft_entry("a"),
                _aircraft_entry("b"),
            ),
        )
        fleet = load_fleet(path)
        assert set(fleet) == {"a", "b"}


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
