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
    _resolve_known_plane_id,
    _suggest_plane_id,
    load_fleet,
    load_hangar,
    load_layout,
    load_scenario,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FLEET_YAML = REPO_ROOT / "data" / "fleet.yaml"
HANGAR_YAML = REPO_ROOT / "data" / "hangar.yaml"
EXAMPLE_LAYOUT = REPO_ROOT / "layouts" / "example.yaml"


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# A minimal, otherwise-valid hangar YAML body. Tests append a `max_carts:`
# line (or omit it) to exercise the loader's coercion + default.
_MIN_HANGAR = (
    "length_m: 25.0\n"
    "width_m: 18.0\n"
    "door: {center_x_m: 9.0, width_m: 12.0}\n"
    "maintenance_bay: {center_x_m: 13.5, width_m: 9.0, depth_m: 9.0}\n"
)


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
            aid: next(p.width_m for p in a.parts if p.kind == "wing") for aid, a in fleet.items()
        }
        # Loose ballpark check: every wingspan in [4, 20] m (sanity range
        # for the fleet). A decimal-point typo (10.7 → 1.07) is caught.
        for aid, w in wing_widths.items():
            assert 4.0 < w < 20.0, f"{aid}: wingspan {w} m is outside sanity range"
        # And pin a couple of specific values so single-plane edits surface.
        assert wing_widths["scheibe_falke"] == 18.0
        assert wing_widths["fuji"] == 9.42

    def test_load_hangar(self) -> None:
        hangar = load_hangar(HANGAR_YAML)
        assert hangar.length_m == 25.0
        assert hangar.width_m == 18.0
        assert hangar.door.center_x_m == 9.0
        assert hangar.maintenance_bay.center_x_m == 13.5
        assert hangar.maintenance_bay.width_m == 9.0
        assert hangar.maintenance_bay.depth_m == 9.0
        assert hangar.max_carts == 1  # bundled data/hangar.yaml sets it explicitly

    def test_load_example_layout(self) -> None:
        layout = load_layout(EXAMPLE_LAYOUT)
        # Saturday-morning scenario: 3 planes out flying; scheibe_falke
        # in the (walled) maintenance bay so 5 planes appear in placements.
        # The bay occupant is named in ``maintenance.plane`` but excluded
        # from ``placements`` by Layout invariant.
        assert len(layout.placements) == 5
        assert layout.maintenance_plane == "scheibe_falke"
        assert "scheibe_falke" not in {p.plane_id for p in layout.placements}


class TestHangarMaxCarts:
    """Loader handling of the optional `max_carts` site scalar (#210)."""

    def test_absent_defaults_to_one(self, tmp_path: Path) -> None:
        """A hangar.yaml with no max_carts loads as 1 — the backward-compat
        guarantee (absence reproduces the original single-cart rule)."""
        path = _write(tmp_path / "h.yaml", _MIN_HANGAR)
        assert load_hangar(path).max_carts == 1

    def test_explicit_value(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "h.yaml", _MIN_HANGAR + "max_carts: 3\n")
        assert load_hangar(path).max_carts == 3

    @pytest.mark.parametrize("bad", ['"two"', "1.5", "true"])
    def test_non_int_rejected(self, tmp_path: Path, bad: str) -> None:
        """Strings, floats, and bools are rejected (no silent truncation or
        the bool-is-int footgun) with the field named."""
        path = _write(tmp_path / "h.yaml", _MIN_HANGAR + f"max_carts: {bad}\n")
        with pytest.raises(LoaderError, match="max_carts"):
            load_hangar(path)

    def test_negative_rejected(self, tmp_path: Path) -> None:
        """A negative count is rejected — the loader wraps the Hangar
        __post_init__ ValueError into a LoaderError."""
        path = _write(tmp_path / "h.yaml", _MIN_HANGAR + "max_carts: -1\n")
        with pytest.raises(LoaderError, match="max_carts must be non-negative"):
            load_hangar(path)

    def test_load_layout_override_loosens_cap_before_build(self) -> None:
        """The load_layout(max_carts=…) override is applied to the resolved
        hangar before the Layout is built, so a layout the data-file cap (1)
        would reject now loads — the path the CLI --max-carts flag uses."""
        fixture = REPO_ROOT / "tests" / "fixtures" / "invalid_cart_rule.yaml"
        # Two cart_eligible planes on carts: rejected under the default cap.
        with pytest.raises(LoaderError, match="cart_eligible"):
            load_layout(fixture)
        # With the override the Layout constructs and carries the new cap.
        layout = load_layout(fixture, max_carts=2)
        assert layout.hangar.max_carts == 2
        assert len(layout.placements) == 2

    def test_load_layout_negative_override_raises_loader_error(self) -> None:
        """A negative override is rejected as a LoaderError (not a raw
        ValueError from dataclasses.replace), preserving the exit-2 contract."""
        fixture = REPO_ROOT / "tests" / "fixtures" / "invalid_cart_rule.yaml"
        with pytest.raises(LoaderError, match="max_carts must be non-negative"):
            load_layout(fixture, max_carts=-1)

    def test_load_scenario_negative_override_raises_loader_error(self) -> None:
        fixture = REPO_ROOT / "tests" / "fixtures" / "solve_infeasible_two_cart_pins.yaml"
        with pytest.raises(LoaderError, match="max_carts must be non-negative"):
            load_scenario(fixture, max_carts=-1)


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
# Non-finite numeric field guard (_to_float rejects NaN / ±inf).
# ----------------------------------------------------------------------------


class TestNonFiniteNumericFields:
    """yaml.safe_load parses .nan/.inf/-.inf into real Python floats.
    _to_float must reject them so they never reach geometry calculations
    (e.g. _wing_spar_x) where NaN comparisons silently return False."""

    def _fleet_with_wing_length(self, value_str: str) -> str:
        """Build a minimal fleet YAML with the given wing length_m literal."""
        return f"""\
aircraft:
  - id: foo
    name: Foo
    wing_position: high
    gear: tailwheel
    movement_mode: always_own_gear
    turn_radius_m: 5.0
    measured: false
    parts:
      - kind: fuselage
        length_m: 7.0
        width_m: 0.8
        z_bottom_m: 0.0
        z_top_m: 1.5
      - kind: wing
        length_m: {value_str}
        width_m: 9.0
        z_bottom_m: 2.0
        z_top_m: 2.3
"""

    def test_nan_wing_length_raises_loader_error(self, tmp_path: Path) -> None:
        """`length_m: .nan` parses to float('nan'); must not silently produce
        a NaN strut keep-out coordinate — LoaderError is required."""
        path = _write(tmp_path / "f.yaml", self._fleet_with_wing_length(".nan"))
        with pytest.raises(LoaderError, match="expected a finite number"):
            load_fleet(path)

    def test_inf_wing_length_raises_loader_error(self, tmp_path: Path) -> None:
        """`length_m: .inf` parses to float('inf'); must be rejected."""
        path = _write(tmp_path / "f.yaml", self._fleet_with_wing_length(".inf"))
        with pytest.raises(LoaderError, match="expected a finite number"):
            load_fleet(path)

    def test_neg_inf_wing_length_raises_loader_error(self, tmp_path: Path) -> None:
        """`length_m: -.inf` parses to float('-inf'); must be rejected."""
        path = _write(tmp_path / "f.yaml", self._fleet_with_wing_length("-.inf"))
        with pytest.raises(LoaderError, match="expected a finite number"):
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

    def test_strut_x_anchored_to_wing_spar_not_trailing_edge(self, tmp_path: Path) -> None:
        """Issue #282 — struts must sit on the wing-spar axis, NOT at the
        wing trailing edge (≈ the old ``fuselage_attach_x_m`` placeholder).

        The spar rule (fix option 1) anchors the strut's longitudinal
        station to the wing geometry: the front/main spar of a strut-braced
        high-wing sits near the quarter-chord, i.e. one quarter of the chord
        aft of the leading edge. In plane-local coords ``+x`` is forward, so
        the wing's leading edge is at ``offset_x_m + length_m/2`` and the
        spar at ``offset_x_m + length_m/4``.

        ``fuselage_attach_x_m`` is set here at the wing trailing edge
        (``offset_x_m - length_m/2``) to mirror the placeholder fleet data;
        the strut must NOT land there.
        """
        wing_offset_x = 0.5
        wing_chord = 1.6
        trailing_edge_x = wing_offset_x - wing_chord / 2.0  # -0.3
        expected_spar_x = wing_offset_x + wing_chord / 4.0  # +0.9
        path = _write(
            tmp_path / "f.yaml",
            f"""
aircraft:
  - id: braced
    name: Braced
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
        length_m: {wing_chord}
        width_m: 9.0
        offset_x_m: {wing_offset_x}
        z_bottom_m: 2.0
        z_top_m: 2.3
    struts:
      fuselage_attach_x_m: {trailing_edge_x}
      fuselage_attach_y_m: 0.4
      fuselage_attach_z_m: 0.5
      wing_attach_y_m: 1.8
      width_m: 0.05
""",
        )
        fleet = load_fleet(path)
        struts = [p for p in fleet["braced"].parts if p.kind == "strut"]
        assert len(struts) == 2
        for s in struts:
            assert s.offset_x_m == pytest.approx(expected_spar_x), (
                f"strut x={s.offset_x_m} should sit on the wing spar "
                f"(quarter-chord = {expected_spar_x}), not at the wing "
                f"trailing edge / fuselage_attach_x_m ({trailing_edge_x})"
            )
            assert s.offset_x_m != pytest.approx(trailing_edge_x), (
                f"strut x={s.offset_x_m} still at the wing trailing edge"
            )

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
maintenance_bay: {center_x_m: 13.5, width_m: 9, depth_m: 9}
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
maintenance_bay: {center_x_m: 13.5, width_m: 9, depth_m: 9}
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
maintenance_bay: {center_x_m: 13.5, width_m: 9, depth_m: 9}
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
maintenance_bay: {center_x_m: 13.5, width_m: 9, depth_m: 9}
""",
        )
        with pytest.raises(LoaderError, match="doesn't fit in hangar width"):
            load_hangar(path)

    @pytest.mark.parametrize(
        ("bay_yaml", "expected_match"),
        [
            # Right-wall overflow: center_x_m + width_m/2 = 15.0 + 4.5 = 19.5 > width_m=18.0.
            (
                "{center_x_m: 15.0, width_m: 9, depth_m: 9}",
                r"MaintenanceBay.*doesn't fit in hangar width",
            ),
            # Left-wall overflow: center_x_m - width_m/2 = 2.0 - 4.5 = -2.5 < 0.
            (
                "{center_x_m: 2.0, width_m: 9, depth_m: 9}",
                r"MaintenanceBay.*doesn't fit in hangar width",
            ),
            # Depth equals length: leaves no non-bay parking. Hangar.__post_init__
            # raises a *different* ValueError than the width-overflow branch.
            (
                "{center_x_m: 13.5, width_m: 9, depth_m: 25}",
                r"MaintenanceBay\.depth_m.*must be strictly less than Hangar\.length_m",
            ),
        ],
        ids=["right_wall_overflow", "left_wall_overflow", "depth_equals_length"],
    )
    def test_maintenance_bay_invariant_propagates_from_model(
        self, tmp_path: Path, bay_yaml: str, expected_match: str
    ) -> None:
        """Each ``Hangar.__post_init__`` invariant on the maintenance bay
        must wrap as ``LoaderError`` at the loader boundary (mirror of
        ``test_door_does_not_fit_propagates_from_model`` but parametrized
        over the two ``bay_left < 0 or bay_right > width_m`` cases plus
        the ``depth_m >= length_m`` branch — three failure modes from
        three distinct model-level ``raise ValueError`` sites).
        """
        path = _write(
            tmp_path / "h.yaml",
            f"""
length_m: 25.0
width_m: 18.0
door: {{center_x_m: 9.0, width_m: 12.0}}
maintenance_bay: {bay_yaml}
""",
        )
        with pytest.raises(LoaderError, match=expected_match):
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

    @pytest.mark.parametrize("missing_key", ["center_x_m", "width_m", "depth_m"])
    def test_missing_maintenance_bay_field(self, tmp_path: Path, missing_key: str) -> None:
        all_fields = {"center_x_m": 13.5, "width_m": 9, "depth_m": 9}
        del all_fields[missing_key]
        bay_yaml = ", ".join(f"{k}: {v}" for k, v in all_fields.items())
        path = _write(
            tmp_path / "h.yaml",
            f"""
length_m: 25.0
width_m: 18.0
door: {{center_x_m: 9, width_m: 12}}
maintenance_bay: {{{bay_yaml}}}
""",
        )
        with pytest.raises(
            LoaderError, match=f"missing required field 'maintenance_bay.{missing_key}'"
        ):
            load_hangar(path)

    def test_clearance_defaults_applied(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "h.yaml",
            """
length_m: 25.0
width_m: 18.0
door: {center_x_m: 9.0, width_m: 12.0}
maintenance_bay: {center_x_m: 13.5, width_m: 9, depth_m: 9}
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
maintenance_bay: {center_x_m: 13.5, width_m: 9, depth_m: 9}
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
        with pytest.raises(LoaderError, match="unknown plane id 'ghost'"):
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
maintenance_bay: {center_x_m: 13.5, width_m: 9, depth_m: 9}
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
        with pytest.raises(LoaderError, match=r"At most 1 cart_eligible"):
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
        _write(
            tmp_path / "fleet.yaml",
            _minimal_aircraft_yaml("foo", movement_mode="always_own_gear", turn_radius_m=5.0),
        )
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
        with pytest.raises(
            LoaderError, match="'maintenance' block present but lacks required 'plane'"
        ):
            load_layout(layout_path)

    def test_maintenance_occupant_also_in_placements_rejected_with_actionable_error(
        self, tmp_path: Path
    ) -> None:
        """A layout YAML that names a ``maintenance.plane`` and also lists
        that plane under ``placements`` is rejected at load time with an
        actionable error.

        The bay occupant is treated as away — absent from ``placements`` by
        Layout invariant (#103). The loader catches this combination
        explicitly so YAML authors get a directly-actionable message
        ("Remove it from placements") rather than the bubbled Layout
        invariant text. Layout's invariant remains the programmatic
        backstop for non-loader callers.

        The parenthetical "(or fix the plane id if it doesn't match an
        aircraft in the fleet)" steers users toward the right root cause
        in the typo'd-id case, where naive "remove the row" advice would
        be a two-step debug (remove row → hit "not in fleet" → realise
        the id was wrong all along).
        """
        self._minimal_fleet_and_hangar(tmp_path)
        layout_path = _write(
            tmp_path / "layout.yaml",
            """
fleet: fleet.yaml
hangar: hangar.yaml
maintenance: {plane: foo}
placements:
  - {plane: foo, x_m: 5, y_m: 5, heading_deg: 0, on_carts: false}
""",
        )
        with pytest.raises(LoaderError) as exc_info:
            load_layout(layout_path)
        msg = str(exc_info.value)
        assert "'foo'" in msg, f"error must name the offending plane: {msg}"
        assert "placements" in msg, f"error must point at placements: {msg}"
        assert "Remove it from placements" in msg, (
            f"error must include actionable suffix; got: {msg}"
        )
        assert "fix the plane id" in msg, f"error must include the typo'd-id hint; got: {msg}"

    def test_maintenance_occupant_appearing_among_other_placements_rejected(
        self, tmp_path: Path
    ) -> None:
        """The loop-not-just-first-row form of the check: when the
        occupant appears alongside *other* valid placements, the loader
        still catches it. Guards against a buggy short-circuit like
        ``placements[0].plane_id == maintenance_plane`` that would happen
        to pass the single-plane fixture above.
        """
        _write(
            tmp_path / "fleet.yaml",
            _fleet_yaml(
                _aircraft_entry("foo", movement_mode="always_own_gear", turn_radius_m=5.0),
                _aircraft_entry("bar", movement_mode="always_own_gear", turn_radius_m=5.0),
            ),
        )
        _write(
            tmp_path / "hangar.yaml",
            """
length_m: 25.0
width_m: 18.0
door: {center_x_m: 9.0, width_m: 12.0}
maintenance_bay: {center_x_m: 13.5, width_m: 9, depth_m: 9}
""",
        )
        # `bar` is the maintenance occupant. The realistic real-world bug
        # shape: user has a valid layout for `foo` and accidentally adds a
        # row for the maintenance plane as the second placement.
        layout_path = _write(
            tmp_path / "layout.yaml",
            """
fleet: fleet.yaml
hangar: hangar.yaml
maintenance: {plane: bar}
placements:
  - {plane: foo, x_m: 5, y_m: 5, heading_deg: 0, on_carts: false}
  - {plane: bar, x_m: 10, y_m: 10, heading_deg: 0, on_carts: false}
""",
        )
        with pytest.raises(LoaderError, match="maintenance_plane 'bar' is named in placements"):
            load_layout(layout_path)

    def test_maintenance_plane_null_rejected(self, tmp_path: Path) -> None:
        """`maintenance: {plane: ~}` used to silently return None, disabling
        the maintenance feature.  It should raise with an actionable message."""
        self._minimal_fleet_and_hangar(tmp_path)
        layout_path = _write(
            tmp_path / "layout.yaml",
            """
fleet: fleet.yaml
hangar: hangar.yaml
placements:
  - {plane: foo, x_m: 5, y_m: 5, heading_deg: 0, on_carts: false}
maintenance:
  plane: ~
""",
        )
        with pytest.raises(LoaderError, match="'maintenance.plane' is null"):
            load_layout(layout_path)

    def test_maintenance_plane_non_string_rejected(self, tmp_path: Path) -> None:
        """`maintenance: {plane: 42}` (int) used to pass through silently,
        then fail with a confusing 'not in fleet' message at Layout construction."""
        self._minimal_fleet_and_hangar(tmp_path)
        layout_path = _write(
            tmp_path / "layout.yaml",
            """
fleet: fleet.yaml
hangar: hangar.yaml
placements:
  - {plane: foo, x_m: 5, y_m: 5, heading_deg: 0, on_carts: false}
maintenance:
  plane: 42
""",
        )
        with pytest.raises(LoaderError) as exc_info:
            load_layout(layout_path)
        msg = str(exc_info.value)
        assert "must be a string aircraft id" in msg
        assert "42" in msg
        assert "int" in msg

    def test_maintenance_plane_empty_string_rejected(self, tmp_path: Path) -> None:
        """`maintenance: {plane: ""}` used to slip through silently."""
        self._minimal_fleet_and_hangar(tmp_path)
        layout_path = _write(
            tmp_path / "layout.yaml",
            """
fleet: fleet.yaml
hangar: hangar.yaml
placements:
  - {plane: foo, x_m: 5, y_m: 5, heading_deg: 0, on_carts: false}
maintenance:
  plane: ""
""",
        )
        with pytest.raises(LoaderError) as exc_info:
            load_layout(layout_path)
        msg = str(exc_info.value)
        assert "must be non-empty" in msg
        assert "either remove the 'maintenance' block entirely" in msg
        assert "supply a valid aircraft id" in msg

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
        with pytest.raises(
            LoaderError, match="'hangar' field is set in YAML but a hangar override"
        ):
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
    return _fleet_yaml(
        _aircraft_entry(plane_id, movement_mode=movement_mode, turn_radius_m=turn_radius_m)
    )


# ----------------------------------------------------------------------------
# _suggest_plane_id helper.
# ----------------------------------------------------------------------------


class TestPlaneIdSuggestion:
    """Unit tests for the _suggest_plane_id near-match helper."""

    def test_casefold_match_suggests_canonical_with_note(self) -> None:
        assert _suggest_plane_id("Foo", ["foo"]) == (
            "; did you mean 'foo'? (plane ids are case-sensitive)"
        )

    def test_all_caps_case_diff_still_suggests(self) -> None:
        # difflib alone scores 'FOO' vs 'foo' at 0.0 (SequenceMatcher is
        # case-sensitive); the casefold pass is what rescues this.
        assert "did you mean 'foo'?" in _suggest_plane_id("FOO", ["foo"])

    def test_typo_suggests_difflib_match(self) -> None:
        assert _suggest_plane_id("cesna_150", ["cessna_150", "cessna_140"]) == (
            "; did you mean 'cessna_150'?"
        )

    def test_novel_id_no_suggestion(self) -> None:
        assert _suggest_plane_id("zzz", ["foo", "bar"]) == ""

    def test_ambiguous_casefold_falls_through_to_no_suggestion(self) -> None:
        # Two reasons combine to yield "" for THIS input: (1) 'foo' and 'Foo'
        # share a casefold so the casefold pass is ambiguous and skipped;
        # (2) difflib then also misses, because 'FOO' differs from both only
        # by case across all three chars, scoring 0.0 — below the 0.6 cutoff.
        # A *smaller* case diff can still get a difflib hit after an ambiguous
        # casefold — see test_ambiguous_casefold_can_still_difflib_suggest.
        assert _suggest_plane_id("FOO", ["foo", "Foo"]) == ""

    def test_ambiguous_casefold_can_still_difflib_suggest(self) -> None:
        # 'bar' and 'BAR' both fold to 'bar' → casefold pass ambiguous, skipped.
        # But 'Bar' vs 'bar' differs in only one of three chars (ratio 0.667 >
        # 0.6), so difflib still suggests — without the case-sensitivity note.
        result = _suggest_plane_id("Bar", ["bar", "BAR"])
        assert "did you mean 'bar'?" in result
        assert "case-sensitive" not in result

    def test_non_str_valid_members_do_not_crash(self) -> None:
        # A malformed fleet.yaml can carry unquoted numeric/bool ids (int/bool
        # fleet keys). The helper must degrade to "" — never AttributeError on
        # .casefold(). (#176 silent-failure regression guard.)
        assert _suggest_plane_id("ghost", [1, 2.5, True]) == ""

    def test_non_str_candidate_does_not_crash(self) -> None:
        assert _suggest_plane_id(1, ["foo", "bar"]) == ""  # type: ignore[arg-type]

    def test_exact_match_returns_empty(self) -> None:
        assert _suggest_plane_id("foo", ["foo", "bar"]) == ""


# ----------------------------------------------------------------------------
# _resolve_known_plane_id gate.
# ----------------------------------------------------------------------------


class TestResolveKnownPlaneId:
    """Unit tests for the _resolve_known_plane_id loader gate."""

    def test_known_id_does_not_raise(self) -> None:
        assert (
            _resolve_known_plane_id("foo", ["foo", "bar"], role="placement", path=Path("x.yaml"))
            is None
        )

    def test_case_mismatch_raises_with_suggestion(self) -> None:
        with pytest.raises(LoaderError) as exc:
            _resolve_known_plane_id("Foo", ["foo"], role="placement", path=Path("x.yaml"))
        msg = str(exc.value)
        assert "x.yaml" in msg
        assert "placement references unknown plane id 'Foo'" in msg
        assert "did you mean 'foo'?" in msg
        assert "case-sensitive" in msg

    def test_novel_id_with_fix_hint_shows_hint(self) -> None:
        with pytest.raises(LoaderError) as exc:
            _resolve_known_plane_id(
                "ghost",
                ["foo"],
                role="maintenance.plane",
                path=Path("s.yaml"),
                fix_hint="either add it to fleet_in ['foo'] or fix the plane id",
            )
        msg = str(exc.value)
        assert "maintenance.plane references unknown plane id 'ghost'" in msg
        assert "either add it to fleet_in ['foo'] or fix the plane id" in msg
        assert "did you mean" not in msg

    def test_novel_id_no_hint_is_bare(self) -> None:
        with pytest.raises(LoaderError) as exc:
            _resolve_known_plane_id("zzz", ["foo"], role="placement", path=Path("x.yaml"))
        msg = str(exc.value)
        assert "unknown plane id 'zzz'" in msg
        assert "did you mean" not in msg
        assert msg.rstrip().endswith("'zzz'")

    def test_near_match_suggestion_beats_fix_hint(self) -> None:
        # Docstring invariant: when there IS a near match, the suggestion
        # wins and the (generic) fix_hint is suppressed.
        with pytest.raises(LoaderError) as exc:
            _resolve_known_plane_id(
                "Foo",
                ["foo"],
                role="maintenance.plane",
                path=Path("s.yaml"),
                fix_hint="either add it to fleet_in ['foo'] or fix the plane id",
            )
        msg = str(exc.value)
        assert "did you mean 'foo'?" in msg
        assert "either add it to fleet_in" not in msg


# ----------------------------------------------------------------------------
# load_layout unknown/mis-cased plane id integration tests.
# ----------------------------------------------------------------------------


class TestUnknownPlaneIdLayout:
    """Loader-boundary unknown/mis-cased plane id rejection for layouts."""

    def _fleet_and_hangar(self, dir_: Path) -> None:
        _write(
            dir_ / "fleet.yaml",
            _minimal_aircraft_yaml("foo", movement_mode="always_own_gear", turn_radius_m=5.0),
        )
        _write(
            dir_ / "hangar.yaml",
            """
length_m: 25.0
width_m: 18.0
door: {center_x_m: 9.0, width_m: 12.0}
maintenance_bay: {center_x_m: 13.5, width_m: 9, depth_m: 9}
""",
        )

    def test_miscased_placement_id_suggests_canonical(self, tmp_path: Path) -> None:
        self._fleet_and_hangar(tmp_path)
        layout = _write(
            tmp_path / "layout.yaml",
            """
fleet: fleet.yaml
hangar: hangar.yaml
placements:
  - {plane: Foo, x_m: 5, y_m: 5, heading_deg: 0, on_carts: false}
""",
        )
        with pytest.raises(LoaderError) as exc:
            load_layout(layout)
        msg = str(exc.value)
        assert "placement references unknown plane id 'Foo'" in msg
        assert "did you mean 'foo'?" in msg
        assert "case-sensitive" in msg

    def test_miscased_maintenance_id_suggests_canonical(self, tmp_path: Path) -> None:
        self._fleet_and_hangar(tmp_path)
        layout = _write(
            tmp_path / "layout.yaml",
            """
fleet: fleet.yaml
hangar: hangar.yaml
placements: []
maintenance: {plane: Foo}
""",
        )
        with pytest.raises(LoaderError) as exc:
            load_layout(layout)
        msg = str(exc.value)
        assert "maintenance.plane references unknown plane id 'Foo'" in msg
        assert "did you mean 'foo'?" in msg

    def test_novel_placement_id_no_false_suggestion(self, tmp_path: Path) -> None:
        self._fleet_and_hangar(tmp_path)
        layout = _write(
            tmp_path / "layout.yaml",
            """
fleet: fleet.yaml
hangar: hangar.yaml
placements:
  - {plane: zzz, x_m: 5, y_m: 5, heading_deg: 0, on_carts: false}
""",
        )
        with pytest.raises(LoaderError) as exc:
            load_layout(layout)
        msg = str(exc.value)
        assert "unknown plane id 'zzz'" in msg
        assert "did you mean" not in msg

    def test_non_str_fleet_id_unknown_ref_is_clean_loadererror(self, tmp_path: Path) -> None:
        # Regression (#176): an unquoted numeric fleet id (`id: 1` → int fleet
        # key) plus an unknown *string* placement id must raise a clean
        # LoaderError, not an AttributeError from .casefold() in the suggester.
        # (The "1" passed here round-trips through YAML to an int key.)
        _write(
            tmp_path / "fleet.yaml",
            _minimal_aircraft_yaml("1", movement_mode="always_own_gear", turn_radius_m=5.0),
        )
        _write(
            tmp_path / "hangar.yaml",
            """
length_m: 25.0
width_m: 18.0
door: {center_x_m: 9.0, width_m: 12.0}
maintenance_bay: {center_x_m: 13.5, width_m: 9, depth_m: 9}
""",
        )
        layout = _write(
            tmp_path / "layout.yaml",
            """
fleet: fleet.yaml
hangar: hangar.yaml
placements:
  - {plane: ghost, x_m: 5, y_m: 5, heading_deg: 0, on_carts: false}
""",
        )
        with pytest.raises(LoaderError, match="unknown plane id 'ghost'"):
            load_layout(layout)


def test_load_fleet_rejects_non_utf8_file(tmp_path: Path) -> None:
    """A file with invalid UTF-8 bytes must surface as LoaderError, not a
    bare UnicodeDecodeError leaking out of _read_yaml."""
    bad = tmp_path / "fleet.yaml"
    bad.write_bytes(b"\xff\xfe\x00bad bytes not utf-8")
    with pytest.raises(LoaderError, match="UTF-8"):
        load_fleet(bad)
