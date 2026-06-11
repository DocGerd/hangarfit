"""Catalog dispatch + manifest-reference loader behaviour (#595)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from hangarfit.loader import (
    LoaderError,
    _build_catalog_object,
    _read_yaml,
    load_fleet,
    load_ground_objects,
)
from hangarfit.models import GroundObject

REPO_ROOT = Path(__file__).resolve().parent.parent
_CAT = Path(__file__).parent / "fixtures" / "catalog"


def _build(name: str) -> object:
    p = _CAT / name
    return _build_catalog_object(_read_yaml(p), source=p)


def _aircraft_doc(aid: str = "p1", **overrides: Any) -> dict[str, Any]:
    """A minimal valid aircraft catalog dict (no `type` → defaults to aircraft)."""
    doc: dict[str, Any] = {
        "id": aid,
        "name": f"Plane {aid}",
        "wing_position": "high",
        "gear": "monowheel",
        "movement_mode": "always_cart",
        "turn_radius_m": 5.0,  # present so a cart_eligible override stays valid
        "measured": False,
        "parts": [
            {
                "kind": "fuselage",
                "length_m": 6.0,
                "width_m": 1.0,
                "offset_x_m": 0.0,
                "offset_y_m": 0.0,
                "z_bottom_m": 0.0,
                "z_top_m": 1.5,
            },
            {
                "kind": "wing",
                "length_m": 1.2,
                "width_m": 9.0,
                "offset_x_m": 0.5,
                "offset_y_m": 0.0,
                "z_bottom_m": 1.9,
                "z_top_m": 2.1,
            },
        ],
        "wheels": {"main_offset_x_m": 0.0},
    }
    doc.update(overrides)
    return doc


def _write(path: Path, obj: Any) -> Path:
    path.write_text(yaml.safe_dump(obj, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _catalog(tmp_path: Path, aid: str, **overrides: Any) -> str:
    """Write a catalog file; return its manifest-relative ref ('catalog/<aid>.yaml')."""
    cat = tmp_path / "catalog"
    cat.mkdir(exist_ok=True)
    _write(cat / f"{aid}.yaml", {"type": "aircraft", **_aircraft_doc(aid, **overrides)})
    return f"catalog/{aid}.yaml"


def test_string_ref_builds_aircraft(tmp_path: Path) -> None:
    ref = _catalog(tmp_path, "p1")
    manifest = _write(tmp_path / "fleet.yaml", {"aircraft": [ref]})
    fleet = load_fleet(manifest)
    assert set(fleet) == {"p1"}
    assert fleet["p1"].movement_mode == "always_cart"


def test_type_omitted_defaults_to_aircraft(tmp_path: Path) -> None:
    cat = tmp_path / "catalog"
    cat.mkdir()
    _write(cat / "p1.yaml", _aircraft_doc("p1"))  # NO `type:` key
    manifest = _write(tmp_path / "fleet.yaml", {"aircraft": ["catalog/p1.yaml"]})
    assert set(load_fleet(manifest)) == {"p1"}


def test_unknown_catalog_type_lists_known_types() -> None:
    p = _CAT / "fixture_bogus_type.yaml"  # type: spaceship + a minimal ground part
    with pytest.raises(LoaderError, match="unknown catalog type 'spaceship'.*known types"):
        _build_catalog_object(_read_yaml(p), source=p)


def test_type_key_does_not_trip_aircraft_allowlist(tmp_path: Path) -> None:
    # `type: aircraft` must be stripped before _build_aircraft sees the dict.
    ref = _catalog(tmp_path, "p1")  # writes type: aircraft
    manifest = _write(tmp_path / "fleet.yaml", {"aircraft": [ref]})
    load_fleet(manifest)  # must NOT raise "unknown aircraft key(s) ['type']"


def test_flag_override_applies(tmp_path: Path) -> None:
    _catalog(tmp_path, "p1")  # catalog default movement_mode = always_cart
    manifest = _write(
        tmp_path / "fleet.yaml",
        {"aircraft": [{"ref": "catalog/p1.yaml", "movement_mode": "cart_eligible"}]},
    )
    fleet = load_fleet(manifest)
    assert fleet["p1"].movement_mode == "cart_eligible"


def test_geometry_override_rejected(tmp_path: Path) -> None:
    _catalog(tmp_path, "p1")
    manifest = _write(
        tmp_path / "fleet.yaml",
        {"aircraft": [{"ref": "catalog/p1.yaml", "parts": []}]},
    )
    with pytest.raises(LoaderError, match="not allowed"):
        load_fleet(manifest)


def test_missing_ref_file_errors(tmp_path: Path) -> None:
    manifest = _write(tmp_path / "fleet.yaml", {"aircraft": ["catalog/nope.yaml"]})
    with pytest.raises(LoaderError, match="does not exist"):
        load_fleet(manifest)


def test_invalid_ref_path_errors(tmp_path: Path) -> None:
    # A ref pathlib can't construct (embedded null byte) → loud LoaderError, not a
    # bare ValueError escaping the loader (fuzz-found via #595).
    manifest = _write(tmp_path / "fleet.yaml", {"aircraft": ["\x00"]})
    with pytest.raises(LoaderError, match="invalid catalog reference"):
        load_fleet(manifest)


def test_duplicate_id_across_refs(tmp_path: Path) -> None:
    cat = tmp_path / "catalog"
    cat.mkdir()
    _write(cat / "a.yaml", {"type": "aircraft", **_aircraft_doc("dup")})
    _write(cat / "b.yaml", {"type": "aircraft", **_aircraft_doc("dup")})
    manifest = _write(tmp_path / "fleet.yaml", {"aircraft": ["catalog/a.yaml", "catalog/b.yaml"]})
    with pytest.raises(LoaderError, match="duplicate aircraft id 'dup'"):
        load_fleet(manifest)


def test_manifest_order_preserved(tmp_path: Path) -> None:
    refs = [_catalog(tmp_path, aid) for aid in ("c", "a", "b")]
    manifest = _write(tmp_path / "fleet.yaml", {"aircraft": refs})
    assert list(load_fleet(manifest)) == ["c", "a", "b"]


def test_empty_catalog_file_errors(tmp_path: Path) -> None:
    # An existing-but-empty catalog file parses to None → loud "must be a mapping",
    # not a silent skip (the _read_yaml-returns-None path).
    cat = tmp_path / "catalog"
    cat.mkdir()
    (cat / "empty.yaml").write_text("", encoding="utf-8")
    manifest = _write(tmp_path / "fleet.yaml", {"aircraft": ["catalog/empty.yaml"]})
    with pytest.raises(LoaderError, match="must be a mapping"):
        load_fleet(manifest)


def test_inline_aircraft_mapping_rejected(tmp_path: Path) -> None:
    # Post-#595 contract: an inline aircraft mapping under `aircraft:` is rejected.
    manifest = _write(tmp_path / "fleet.yaml", {"aircraft": [_aircraft_doc("p1")]})
    with pytest.raises(LoaderError, match="no longer supported"):
        load_fleet(manifest)


def test_data_fleet_loads_after_migration() -> None:
    # The shipped manifest still resolves to the same ids (guards the migration).
    fleet = load_fleet(REPO_ROOT / "data" / "fleet.yaml")
    assert "scheibe_falke" in fleet and "fk9_mkii" in fleet


# --- Ground objects: per-type catalog builders (#601) -----------------------


def test_fixed_obstacle_loads() -> None:
    obj = _build("fixture_fuel_trailer.yaml")
    assert isinstance(obj, GroundObject)
    assert obj.object_class == "fixed_obstacle"
    assert obj.motion_mode is None
    assert obj.parts[0].kind == "ground"


def test_car_loads_with_steerable_default() -> None:
    obj = _build("fixture_caddy.yaml")
    assert isinstance(obj, GroundObject)
    assert obj.object_class == "placed_routed_mover"
    assert obj.motion_mode == "steerable"  # car default
    assert obj.turn_radius_m == 5.0


def test_trailer_loads_with_towed_default() -> None:
    obj = _build("fixture_glider_trailer.yaml")
    assert isinstance(obj, GroundObject)
    assert obj.object_class == "placed_routed_mover"
    assert obj.motion_mode == "towed"  # trailer default


def test_mover_motion_mode_override() -> None:
    # a trailer authored with motion_mode: steerable keeps the override.
    # A steerable mover requires a turn_radius_m (#602 guard), so author one.
    raw = {
        "type": "trailer",
        "id": "t1",
        "name": "Override trailer",
        "parts": [
            {
                "kind": "ground",
                "length_m": 1.0,
                "width_m": 1.0,
                "offset_x_m": 0.0,
                "offset_y_m": 0.0,
                "z_bottom_m": 0.0,
                "z_top_m": 1.0,
            }
        ],
        "motion_mode": "steerable",
        "turn_radius_m": 5.0,
    }
    obj = _build_catalog_object(raw, source=Path("inline"))
    assert isinstance(obj, GroundObject)
    assert obj.motion_mode == "steerable"


def test_fixed_obstacle_rejects_motion_key() -> None:
    bad = {
        "type": "fixed_obstacle",
        "id": "x",
        "name": "x",
        "parts": [
            {
                "kind": "ground",
                "length_m": 1,
                "width_m": 1,
                "offset_x_m": 0,
                "offset_y_m": 0,
                "z_bottom_m": 0,
                "z_top_m": 1,
            }
        ],
        "motion_mode": "towed",
    }
    with pytest.raises(LoaderError, match="unknown fixed_obstacle key"):
        _build_catalog_object(bad, source=Path("inline"))


def test_ground_object_rejects_aircraft_part_kind() -> None:
    bad = {
        "type": "car",
        "id": "x",
        "name": "x",
        "parts": [
            {
                "kind": "wing",
                "length_m": 1,
                "width_m": 1,
                "offset_x_m": 0,
                "offset_y_m": 0,
                "z_bottom_m": 0,
                "z_top_m": 1,
            }
        ],
    }
    with pytest.raises(LoaderError, match="not allowed on a ground object"):
        _build_catalog_object(bad, source=Path("inline"))


# --- Task 5: manifest ground_objects: + load_ground_objects (#601) -----------


def test_load_ground_objects_resolves_manifest() -> None:
    gobjs = load_ground_objects(_CAT / "fixture_ground_manifest.yaml")
    assert set(gobjs) == {"fixture_fuel_trailer", "fixture_caddy", "fixture_glider_trailer"}
    assert gobjs["fixture_caddy"].object_class == "placed_routed_mover"


def test_load_ground_objects_absent_key_is_empty(tmp_path: Path) -> None:
    m = tmp_path / "m.yaml"
    m.write_text("aircraft: []\n")
    assert load_ground_objects(m) == {}


def test_load_fleet_rejects_ground_object_under_aircraft(tmp_path: Path) -> None:
    # A ground-object ref listed under aircraft: must fail loudly.
    import shutil

    shutil.copy(_CAT / "fixture_fuel_trailer.yaml", tmp_path / "ft.yaml")
    m = tmp_path / "m.yaml"
    m.write_text("aircraft:\n  - ft.yaml\n")
    with pytest.raises(LoaderError, match="aircraft|not an aircraft|ground"):
        load_fleet(m)


def test_load_ground_objects_invalid_motion_mode_raises_loader_error(tmp_path: Path) -> None:
    # A car/trailer catalog entry with an invalid motion_mode value must raise
    # LoaderError through the public load_ground_objects path.
    # The model raises ValueError; load_ground_objects wraps it to LoaderError.
    cat = tmp_path / "catalog"
    cat.mkdir()
    bad_catalog = {
        "type": "trailer",
        "id": "bad_trailer",
        "name": "Bad trailer",
        "parts": [
            {
                "kind": "ground",
                "length_m": 2.0,
                "width_m": 1.0,
                "offset_x_m": 0.0,
                "offset_y_m": 0.0,
                "z_bottom_m": 0.0,
                "z_top_m": 1.0,
            }
        ],
        "motion_mode": "jetpack",
    }
    _write(cat / "bad_trailer.yaml", bad_catalog)
    manifest = _write(
        tmp_path / "fleet.yaml",
        {"ground_objects": ["catalog/bad_trailer.yaml"]},
    )
    with pytest.raises(LoaderError, match="jetpack|motion_mode"):
        load_ground_objects(manifest)
