"""Catalog dispatch + manifest-reference loader behaviour (#595)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from hangarfit.loader import LoaderError, load_fleet

REPO_ROOT = Path(__file__).resolve().parent.parent


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


def test_unknown_type_is_stage_a_error(tmp_path: Path) -> None:
    cat = tmp_path / "catalog"
    cat.mkdir()
    _write(cat / "trailer.yaml", {"type": "ground_object", "id": "t1"})
    manifest = _write(tmp_path / "fleet.yaml", {"aircraft": ["catalog/trailer.yaml"]})
    with pytest.raises(LoaderError, match=r"not yet supported.*Stage A"):
        load_fleet(manifest)


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
