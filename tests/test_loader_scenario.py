"""Tests for hangarfit.loader.load_scenario."""

from __future__ import annotations

import pytest

from hangarfit.loader import LoaderError


def test_load_scenario_minimal():
    from hangarfit.loader import load_scenario

    s = load_scenario("tests/fixtures/scenario_minimal.yaml")
    assert s.fleet_in == ("aviat_husky", "ctsl")
    assert s.maintenance_plane is None
    assert s.constraints == {}


def test_load_scenario_with_pin():
    from hangarfit.loader import load_scenario

    s = load_scenario("tests/fixtures/scenario_with_pin.yaml")
    assert s.maintenance_plane == "fuji"
    assert "aviat_husky" in s.constraints
    pin = s.constraints["aviat_husky"].pin
    assert pin is not None
    assert pin.plane_id == "aviat_husky"  # filled in by loader
    assert pin.x_m == 2.1
    assert pin.heading_deg == 0.0
    assert pin.on_carts is False


def test_load_scenario_with_force_carts():
    from hangarfit.loader import load_scenario

    s = load_scenario("tests/fixtures/scenario_with_force_carts.yaml")
    assert s.constraints["cessna_140"].force_on_carts is True


def test_load_scenario_rejects_unknown_plane():
    from hangarfit.loader import load_scenario

    with pytest.raises(LoaderError, match="unknown plane"):
        load_scenario("tests/fixtures/scenario_bad_unknown_plane.yaml")


def test_load_scenario_rejects_force_carts_conflict():
    from hangarfit.loader import load_scenario

    with pytest.raises(LoaderError, match="movement_mode"):
        load_scenario("tests/fixtures/scenario_bad_force_carts_conflict.yaml")


def test_load_scenario_rejects_double_fleet_source(tmp_path):
    """If YAML embeds `fleet:` AND a fleet override is passed, raise."""
    from hangarfit.loader import load_fleet, load_scenario

    fleet_obj = load_fleet("data/fleet.yaml")
    with pytest.raises(LoaderError, match="fleet"):
        load_scenario("tests/fixtures/scenario_minimal.yaml", fleet=fleet_obj)


def test_load_scenario_yaml_parse_error(tmp_path):
    from hangarfit.loader import load_scenario

    bad = tmp_path / "bad.yaml"
    bad.write_text("not: valid: yaml: at: all:\n  - [")
    with pytest.raises(LoaderError, match="YAML parse error"):
        load_scenario(bad)


def test_load_scenario_missing_fleet_in(tmp_path):
    from hangarfit.loader import load_scenario

    missing = tmp_path / "missing.yaml"
    missing.write_text("fleet: ../../data/fleet.yaml\nhangar: ../../data/hangar.yaml\n")
    with pytest.raises(LoaderError, match="fleet_in"):
        load_scenario(missing)
