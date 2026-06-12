from pathlib import Path

import pytest

from hangarfit.loader import LoaderError, load_scenario

FIX = Path(__file__).parent / "fixtures"
FLEET = (FIX / "fleet_region_demo.yaml").resolve()
HANGAR = (FIX / "hangar_region_demo.yaml").resolve()


def test_scenario_loads_mover_with_region_preference():
    s = load_scenario(FIX / "scenario_region_demo.yaml")
    assert "glider_trailer_1" in s.mover_ids
    assert s.region_preferences["glider_trailer_1"].side == "right"
    assert s.region_preferences["glider_trailer_1"].weight == 1.5


def test_scenario_loads_fixed_obstacle_pose():
    s = load_scenario(FIX / "scenario_region_demo.yaml")
    ids = {p.plane_id for p in s.fixed_obstacle_placements}
    assert "maul_fuel_trailer" in ids
    fp = next(p for p in s.fixed_obstacle_placements if p.plane_id == "maul_fuel_trailer")
    assert (fp.x_m, fp.y_m, fp.heading_deg) == (2.5, 3.0, 0.0)


def test_scenario_bare_string_mover_no_pref():
    s = load_scenario(FIX / "scenario_region_demo_caddy.yaml")
    # vw_caddy is listed as a bare string-or-no-pref mover entry
    assert "vw_caddy" in s.mover_ids


def test_scenario_left_variant_loads_with_left_side():
    s = load_scenario(FIX / "scenario_region_demo_left.yaml")
    assert s.region_preferences["glider_trailer_1"].side == "left"


def _write(tmp_path, go_block: str) -> Path:
    p = tmp_path / "bad.yaml"
    p.write_text(
        f"fleet: {FLEET}\nhangar: {HANGAR}\n"
        f"fleet_in: [fuji, cessna_150]\nground_objects:\n{go_block}"
    )
    return p


def test_fixed_obstacle_requires_pose(tmp_path):
    with pytest.raises(LoaderError):
        load_scenario(_write(tmp_path, "  - object: maul_fuel_trailer\n"))  # no pose


def test_mover_forbids_pose(tmp_path):
    with pytest.raises(LoaderError):
        load_scenario(
            _write(
                tmp_path,
                "  - object: glider_trailer_1\n    x_m: 5.0\n    y_m: 5.0\n    heading_deg: 0.0\n",
            )
        )


def test_fixed_obstacle_forbids_region_preference(tmp_path):
    with pytest.raises(LoaderError):
        load_scenario(
            _write(
                tmp_path,
                "  - object: maul_fuel_trailer\n    x_m: 2.0\n    y_m: 3.0\n"
                "    heading_deg: 0.0\n    region_preference: {side: right, weight: 1.0}\n",
            )
        )


def test_unknown_object_rejected(tmp_path):
    with pytest.raises(LoaderError):
        load_scenario(_write(tmp_path, "  - object: ghost_trailer\n"))


def test_region_preference_non_string_side_rejected(tmp_path):
    with pytest.raises(LoaderError):
        load_scenario(
            _write(
                tmp_path,
                "  - object: glider_trailer_1\n"
                "    region_preference: {side: [left, right], weight: 1.0}\n",
            )
        )


def test_region_preference_invalid_side_string_rejected(tmp_path):
    with pytest.raises(LoaderError):
        load_scenario(
            _write(
                tmp_path,
                "  - object: glider_trailer_1\n"
                "    region_preference: {side: sideways, weight: 1.0}\n",
            )
        )
