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


def test_load_scenario_rejects_maintenance_plane_not_in_fleet_in(tmp_path):
    """Loader-path: maintenance.plane not in fleet_in raises LoaderError with
    an actionable message that includes the path, the bad plane id, the
    actual fleet_in list, and a fix hint.  This mirrors the boundary-check
    added for load_layout in PR #105 (closes #177).
    """
    import shutil

    from hangarfit.loader import load_scenario

    # Write the bad scenario adjacent to root data files so relative refs
    # resolve correctly from tmp_path.
    (tmp_path / "data").mkdir()
    shutil.copy("data/fleet.yaml", tmp_path / "data" / "fleet.yaml")
    shutil.copy("data/hangar.yaml", tmp_path / "data" / "hangar.yaml")
    bad = tmp_path / "bad_maintenance.yaml"
    bad.write_text(
        "fleet: data/fleet.yaml\n"
        "hangar: data/hangar.yaml\n"
        "fleet_in: [aviat_husky, ctsl]\n"
        "maintenance:\n"
        "  plane: ghost\n"
    )
    with pytest.raises(LoaderError) as exc_info:
        load_scenario(bad)
    msg = str(exc_info.value)
    # Must name the bad plane id
    assert "ghost" in msg, f"message should name the bad plane id; got: {msg!r}"
    # Must include the file path
    assert str(bad) in msg, f"message should include the file path; got: {msg!r}"
    # Must show the actual fleet_in list so the user knows what is valid
    assert "aviat_husky" in msg, f"message should list valid fleet_in planes; got: {msg!r}"
    # Must include a fix hint (actionable suffix)
    assert "either add it to fleet_in or fix the plane id" in msg, (
        f"message should include actionable fix hint; got: {msg!r}"
    )


def test_scenario_post_init_backstop_still_fires_on_direct_construction():
    """Direct-construction path: Scenario.__post_init__ still raises ValueError
    when maintenance_plane is not in fleet_in, bypassing the loader guard.
    This ensures the programmatic backstop is not removed.
    """
    from hangarfit.loader import load_fleet, load_hangar
    from hangarfit.models import Scenario

    fleet = load_fleet("data/fleet.yaml")
    hangar = load_hangar("data/hangar.yaml")
    with pytest.raises(ValueError, match="maintenance_plane"):
        Scenario(
            fleet=fleet,
            hangar=hangar,
            fleet_in=("aviat_husky", "ctsl"),
            maintenance_plane="ghost",  # not in fleet_in and not in fleet
        )
