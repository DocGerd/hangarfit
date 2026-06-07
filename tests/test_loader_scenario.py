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
    # Names the bad plane id
    assert "ghost" in msg, f"message should name the bad plane id; got: {msg!r}"
    # Includes the file path
    assert str(bad) in msg, f"message should include the file path; got: {msg!r}"
    # Enumerates the valid fleet_in so the user knows what is valid
    assert "aviat_husky" in msg, f"message should list valid fleet_in planes; got: {msg!r}"
    # Actionable guidance (the sorted fleet_in list is inserted, so assert the two halves)
    assert "either add it to fleet_in" in msg, f"missing add-to-fleet_in guidance; got: {msg!r}"
    assert "or fix the plane id" in msg, f"missing fix-the-id guidance; got: {msg!r}"
    # 'ghost' has no near match → no false suggestion
    assert "did you mean" not in msg, f"'ghost' should not get a suggestion; got: {msg!r}"


def test_load_scenario_rejects_null_maintenance_plane(tmp_path):
    """Loader-path: ``maintenance: {plane: ~}`` (YAML null) raises LoaderError
    from ``load_scenario`` (closes #184).

    ``_extract_maintenance_plane`` is shared by ``load_layout`` and
    ``load_scenario``, but the null / non-string / empty-string rejections
    are exercised in ``test_loader.py`` only through the ``load_layout``
    entry point.  This test pins the symmetric guarantee for the
    ``load_scenario`` caller so a future refactor that diverged the two
    entry points (e.g. inlining per-caller logic, or a per-caller override)
    would be caught.

    Scope is one representative shape: the null case is the most
    user-visible (issue #184).  The shared validator's other branches
    (non-string, empty) are already covered via ``load_layout`` — re-testing
    them through a second entry point would be redundant once this test
    proves ``load_scenario`` routes through the validator at all.

    Note the null guard fires *inside* ``_extract_maintenance_plane`` and so
    precedes the ``maintenance_plane not in fleet_in`` boundary check that
    ``test_load_scenario_rejects_maintenance_plane_not_in_fleet_in`` covers;
    the two tests guard different lines on the same path.
    """
    import shutil

    from hangarfit.loader import load_scenario

    # Write the scenario adjacent to copied root data files so the relative
    # fleet:/hangar: refs resolve from tmp_path (mirrors the not-in-fleet_in test).
    (tmp_path / "data").mkdir()
    shutil.copy("data/fleet.yaml", tmp_path / "data" / "fleet.yaml")
    shutil.copy("data/hangar.yaml", tmp_path / "data" / "hangar.yaml")
    bad = tmp_path / "null_maintenance.yaml"
    bad.write_text(
        "fleet: data/fleet.yaml\n"
        "hangar: data/hangar.yaml\n"
        "fleet_in: [aviat_husky, ctsl]\n"
        "maintenance:\n"
        "  plane: ~\n"
    )
    with pytest.raises(LoaderError, match="'maintenance.plane' is null"):
        load_scenario(bad)


def _stage_scenario(tmp_path, body: str):
    """Write a scenario YAML next to copied real data files; return its path."""
    import shutil

    (tmp_path / "data").mkdir(exist_ok=True)
    shutil.copy("data/fleet.yaml", tmp_path / "data" / "fleet.yaml")
    shutil.copy("data/hangar.yaml", tmp_path / "data" / "hangar.yaml")
    p = tmp_path / "scenario.yaml"
    p.write_text("fleet: data/fleet.yaml\nhangar: data/hangar.yaml\n" + body)
    return p


def test_load_scenario_miscased_fleet_in_entry_suggests(tmp_path):
    from hangarfit.loader import load_scenario

    p = _stage_scenario(tmp_path, "fleet_in: [Aviat_husky, ctsl]\n")
    with pytest.raises(LoaderError) as exc:
        load_scenario(p)
    msg = str(exc.value)
    assert "fleet_in entry references unknown plane id 'Aviat_husky'" in msg
    assert "did you mean 'aviat_husky'?" in msg


def test_load_scenario_miscased_maintenance_suggests(tmp_path):
    from hangarfit.loader import load_scenario

    p = _stage_scenario(tmp_path, "fleet_in: [aviat_husky, ctsl]\nmaintenance:\n  plane: Ctsl\n")
    with pytest.raises(LoaderError) as exc:
        load_scenario(p)
    msg = str(exc.value)
    assert "maintenance.plane references unknown plane id 'Ctsl'" in msg
    assert "did you mean 'ctsl'?" in msg


def test_load_scenario_miscased_constraint_key_suggests(tmp_path):
    from hangarfit.loader import load_scenario

    p = _stage_scenario(
        tmp_path,
        "fleet_in: [aviat_husky, ctsl]\nconstraints:\n  Ctsl:\n    force_on_carts: false\n",
    )
    with pytest.raises(LoaderError) as exc:
        load_scenario(p)
    msg = str(exc.value)
    assert "constraints key references unknown plane id 'Ctsl'" in msg
    assert "did you mean 'ctsl'?" in msg


def test_load_scenario_typo_fleet_in_entry_suggests_via_difflib(tmp_path):
    """A genuine (non-case) typo in a fleet_in entry is caught with a difflib
    near-match suggestion — the other new tests cover only the casefold path."""
    from hangarfit.loader import load_scenario

    p = _stage_scenario(tmp_path, "fleet_in: [aviat_huksy, ctsl]\n")
    with pytest.raises(LoaderError) as exc:
        load_scenario(p)
    msg = str(exc.value)
    assert "fleet_in entry references unknown plane id 'aviat_huksy'" in msg
    assert "did you mean 'aviat_husky'?" in msg


# ── #441: soft PlaneConstraint.priority in scenario YAML ─────────────────


def test_load_scenario_with_priority(tmp_path):
    from hangarfit.loader import load_scenario

    p = _stage_scenario(
        tmp_path,
        "fleet_in: [aviat_husky, ctsl]\nconstraints:\n  aviat_husky:\n    priority: 2.5\n",
    )
    s = load_scenario(p)
    assert s.constraints["aviat_husky"].priority == 2.5


def test_load_scenario_priority_null_is_none(tmp_path):
    from hangarfit.loader import load_scenario

    p = _stage_scenario(
        tmp_path,
        "fleet_in: [aviat_husky]\nconstraints:\n  aviat_husky:\n    priority: null\n",
    )
    s = load_scenario(p)
    assert s.constraints["aviat_husky"].priority is None


def test_load_scenario_rejects_negative_priority(tmp_path):
    from hangarfit.loader import load_scenario

    p = _stage_scenario(
        tmp_path,
        "fleet_in: [aviat_husky]\nconstraints:\n  aviat_husky:\n    priority: -1.0\n",
    )
    with pytest.raises(LoaderError, match="priority"):
        load_scenario(p)


def test_load_scenario_rejects_non_finite_priority(tmp_path):
    from hangarfit.loader import load_scenario

    p = _stage_scenario(
        tmp_path,
        "fleet_in: [aviat_husky]\nconstraints:\n  aviat_husky:\n    priority: .nan\n",
    )
    with pytest.raises(LoaderError, match="finite"):
        load_scenario(p)


@pytest.mark.parametrize("yaml_bool", ["true", "false", "yes", "on"])
def test_load_scenario_rejects_bool_priority(tmp_path, yaml_bool):
    """`bool` is an int subclass, so float(True)==1.0 would silently parse a
    fat-fingered `priority: true`/`yes` as a plausible-but-wrong weight. Rejected
    at _to_float, like the _to_int/_to_bool siblings (silent-failure review)."""
    from hangarfit.loader import load_scenario

    p = _stage_scenario(
        tmp_path,
        f"fleet_in: [aviat_husky]\nconstraints:\n  aviat_husky:\n    priority: {yaml_bool}\n",
    )
    with pytest.raises(LoaderError, match="bool"):
        load_scenario(p)


# ── #263: per-plane PlaneConstraint.nose_out tri-state in scenario YAML ───


def test_load_scenario_nose_out_absent_is_none(tmp_path):
    from hangarfit.loader import load_scenario

    p = _stage_scenario(
        tmp_path,
        "fleet_in: [aviat_husky]\nconstraints:\n  aviat_husky:\n    priority: 1.0\n",
    )
    s = load_scenario(p)
    assert s.constraints["aviat_husky"].nose_out is None


@pytest.mark.parametrize("yaml_bool, expected", [("true", True), ("false", False)])
def test_load_scenario_nose_out_parsed(tmp_path, yaml_bool, expected):
    from hangarfit.loader import load_scenario

    p = _stage_scenario(
        tmp_path,
        f"fleet_in: [aviat_husky]\nconstraints:\n  aviat_husky:\n    nose_out: {yaml_bool}\n",
    )
    s = load_scenario(p)
    assert s.constraints["aviat_husky"].nose_out is expected


@pytest.mark.parametrize("typo", ["nose-out", "noseout", "nose_in", "tow_pivotable"])
def test_load_scenario_rejects_unknown_constraint_key(tmp_path, typo):
    """A misspelled constraint key (e.g. `nose-out`) must be REJECTED, not
    silently dropped — for `nose_out` a silent drop inverts the user's nose-IN
    exemption (the field's None means 'follow global', which defaults ON). Mirrors
    the strict `wheels:` allowlist; #263 silent-failure-hunter HIGH finding."""
    from hangarfit.loader import load_scenario

    p = _stage_scenario(
        tmp_path,
        f"fleet_in: [aviat_husky]\nconstraints:\n  aviat_husky:\n    {typo}: false\n",
    )
    with pytest.raises(LoaderError, match="unknown constraint key"):
        load_scenario(p)


def test_load_scenario_rejects_quoted_bool_nose_out(tmp_path):
    """`nose_out: "true"` (quoted) would silently be truthy via bool(); rejected
    by the strict _to_bool, like force_on_carts/measured."""
    from hangarfit.loader import load_scenario

    p = _stage_scenario(
        tmp_path,
        'fleet_in: [aviat_husky]\nconstraints:\n  aviat_husky:\n    nose_out: "true"\n',
    )
    with pytest.raises(LoaderError, match="nose_out"):
        load_scenario(p)


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
