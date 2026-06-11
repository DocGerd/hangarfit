"""End-to-end integration: load a real layout YAML with ground objects through
``load_layout``, run ``check``, and assert the verdict (#601).

Proves the data flows model → loader → collisions without any kwarg injection:
the entire path goes through YAML files on disk.
"""

import shutil
import textwrap
from pathlib import Path

from hangarfit.collisions import check
from hangarfit.loader import load_layout

# ---------------------------------------------------------------------------
# Repo-relative paths for the real catalog files we copy into tmp_path.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[1]  # tests/ → repo root
_DATA_CATALOG = _REPO_ROOT / "data" / "catalog"
_FIXTURE_CATALOG = _REPO_ROOT / "tests" / "fixtures" / "catalog"


def _build_world(tmp_path) -> None:  # type: ignore[type-arg]
    """Populate *tmp_path* with a self-contained world:

    - ``catalog/cessna_150.yaml``   (real aircraft catalog entry)
    - ``catalog/fixture_fuel_trailer.yaml``  (fixed_obstacle, 5×2m, z 0–2.0)
    - ``catalog/fixture_caddy.yaml``         (car/mover, 4.5×1.8m, z 0–1.8)
    - ``fleet.yaml``  — points at the three catalog files above
    - ``hangar.yaml`` — a roomy 40×40 m placeholder hangar
    - ``layout.yaml`` — cessna clear at (20,20), fuel trailer at (5,5), caddy
      at (5,15); nothing overlaps → ``check`` returns **valid**.
    - ``layout_clash.yaml`` — same, but the fuel trailer is placed at (20,20),
      co-located with the cessna; their parts overlap in XY and Z → ``check``
      returns **invalid** with a ``ground_obstacle`` conflict.
    """
    catalog_dir = tmp_path / "catalog"
    catalog_dir.mkdir()

    # -- Aircraft ----------------------------------------------------------------
    shutil.copy(
        _DATA_CATALOG / "cessna_150.yaml",
        catalog_dir / "cessna_150.yaml",
    )

    # -- Ground objects ----------------------------------------------------------
    shutil.copy(
        _FIXTURE_CATALOG / "fixture_fuel_trailer.yaml",
        catalog_dir / "fixture_fuel_trailer.yaml",
    )
    shutil.copy(
        _FIXTURE_CATALOG / "fixture_caddy.yaml",
        catalog_dir / "fixture_caddy.yaml",
    )

    # -- fleet.yaml --------------------------------------------------------------
    (tmp_path / "fleet.yaml").write_text(
        textwrap.dedent(
            """\
            aircraft:
              - catalog/cessna_150.yaml
            ground_objects:
              - catalog/fixture_fuel_trailer.yaml
              - catalog/fixture_caddy.yaml
            """
        )
    )

    # -- hangar.yaml -------------------------------------------------------------
    # A 40×40 m square hangar — generous enough that any valid single-aircraft
    # placement stays well inside the boundary.
    (tmp_path / "hangar.yaml").write_text(
        textwrap.dedent(
            """\
            length_m: 40.0
            width_m: 40.0
            door:
              center_x_m: 20.0
              width_m: 12.0
            maintenance_bay:
              center_x_m: 20.0
              width_m: 8.0
              depth_m: 6.0
            clearance_m: 0.3
            wing_layer_clearance_m: 0.2
            """
        )
    )

    # -- layout.yaml (clear) -----------------------------------------------------
    # Cessna 150 at world (20, 20) heading 0 (nose into hangar, +y direction).
    #   Fuselage center ≈ (19.67, 20), spans x∈[19.22,20.12] y∈[16.72,23.28] z 0–1.5
    #   Wing center ≈ (20.77, 20), spans x∈[15.68,25.86] y∈[19.25,20.75] z 2.0–2.3
    # Fuel trailer at (5, 5): center (5,5), spans x∈[2.5,7.5] y∈[2.5,7.5] — far clear.
    # Caddy at (5, 15): center (5,15), spans x∈[2.75,7.25] y∈[13.1,16.9] — also clear.
    (tmp_path / "layout.yaml").write_text(
        textwrap.dedent(
            """\
            fleet: fleet.yaml
            hangar: hangar.yaml
            placements:
              - plane: cessna_150
                x_m: 20.0
                y_m: 20.0
                heading_deg: 0.0
                on_carts: false
            ground_objects:
              - object: fixture_fuel_trailer
                x_m: 5.0
                y_m: 5.0
                heading_deg: 0.0
              - object: fixture_caddy
                x_m: 5.0
                y_m: 15.0
                heading_deg: 0.0
            """
        )
    )

    # -- layout_clash.yaml (ground_obstacle clash) --------------------------------
    # Fuel trailer placed at (20, 20) — same as the cessna.
    # Cessna fuselage: x∈[19.22,20.12] y∈[16.72,23.28] z 0–1.5
    # Fuel trailer 5×2 at (20,20) heading 0: x∈[19,21] y∈[17.5,22.5] z 0–2.0
    # XY overlap is clear; z ranges 0–1.5 (fuselage) ∩ 0–2.0 (trailer) = 0–1.5 → conflict.
    (tmp_path / "layout_clash.yaml").write_text(
        textwrap.dedent(
            """\
            fleet: fleet.yaml
            hangar: hangar.yaml
            placements:
              - plane: cessna_150
                x_m: 20.0
                y_m: 20.0
                heading_deg: 0.0
                on_carts: false
            ground_objects:
              - object: fixture_fuel_trailer
                x_m: 20.0
                y_m: 20.0
                heading_deg: 0.0
              - object: fixture_caddy
                x_m: 5.0
                y_m: 15.0
                heading_deg: 0.0
            """
        )
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_ground_objects_end_to_end(tmp_path) -> None:  # type: ignore[type-arg]
    """Full path: YAML files → load_layout → check, with and without a clash."""
    _build_world(tmp_path)

    # ---- valid case -----------------------------------------------------------
    layout = load_layout(tmp_path / "layout.yaml")

    # Both ground objects resolved from the manifest
    assert "fixture_fuel_trailer" in layout.ground_objects, (
        "fixture_fuel_trailer was not resolved by load_layout"
    )
    assert "fixture_caddy" in layout.ground_objects, "fixture_caddy was not resolved by load_layout"
    # Two placements were parsed
    assert len(layout.ground_object_placements) == 2

    result = check(layout)
    assert result.valid, (
        f"Expected a valid layout (aircraft and ground objects placed clear of each "
        f"other), but check returned invalid with conflicts: {result.conflicts}"
    )

    # ---- clash case -----------------------------------------------------------
    layout2 = load_layout(tmp_path / "layout_clash.yaml")

    result2 = check(layout2)
    assert not result2.valid, (
        "Expected an invalid layout when the aircraft overlaps the fuel trailer, "
        "but check returned valid"
    )
    assert any(c.kind == "ground_obstacle" for c in result2.conflicts), (
        f"Expected a 'ground_obstacle' conflict, got: {result2.conflicts}"
    )
