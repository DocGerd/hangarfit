"""Guards the real Airfield Herrenteich dataset (`herrenteich/`).

These are real (DWG-measured hangar + published-spec fleet) files kept
separate from the synthetic `data/` placeholders. The dataset's promise is
that all eight usual occupants fit at once; this regression test pins the
hangar dimensions and asserts the bundled layout still passes the real
part-based collision checker, so a later edit to any of the three files
cannot silently break it.
"""

from pathlib import Path

from hangarfit import collisions
from hangarfit.geometry import aircraft_parts_world
from hangarfit.loader import load_fleet, load_hangar, load_layout

REPO_ROOT = Path(__file__).resolve().parent.parent
HERRENTEICH = REPO_ROOT / "herrenteich"

# Real office NOTCH (back-right corner): non-floor space the rectangular hangar
# model does NOT enforce (hangar.yaml + spike #424). Derived from the rectangle
# dims: [width-2.36, width] x [length-9.10, length].
NOTCH = (12.72, 22.66, 15.08, 31.76)  # x_min, y_min, x_max, y_max

USUAL_OCCUPANTS = {
    "cessna_140",
    "ctsl",
    "wild_thing",
    "aviat_husky",
    "scheibe_falke",
    "fk9_mkii",
    "stemme_s10",
    "zlin_savage",
}


def test_hangar_dimensions() -> None:
    hangar = load_hangar(HERRENTEICH / "hangar.yaml")
    # Real DWG figures (rounded to cm). Door fits inside the width with the
    # asymmetric jambs (0.55 m + 13.46 m + 1.07 m = 15.08 m).
    assert hangar.width_m == 15.08
    assert hangar.length_m == 31.76
    assert hangar.door.width_m == 13.46
    assert hangar.door.center_x_m == 7.28


def test_fleet_roster() -> None:
    fleet = load_fleet(HERRENTEICH / "fleet.yaml")
    assert set(fleet) == USUAL_OCCUPANTS
    # Stemme is hangared wings-folded: the wing part carries the folded span,
    # which is what lets a 23 m glider fit a 15 m hangar.
    stemme_span = next(p.width_m for p in fleet["stemme_s10"].parts if p.kind == "wing")
    assert stemme_span == 11.4


def test_everyone_home_layout_is_valid() -> None:
    """All eight occupants parked at once must pass the real checker."""
    layout = load_layout(HERRENTEICH / "layout.yaml")
    assert {p.plane_id for p in layout.placements} == USUAL_OCCUPANTS
    result = collisions.check(layout)
    assert result.conflicts == (), (
        f"herrenteich/layout.yaml is no longer valid: {[c.kind for c in result.conflicts]}"
    )


def test_layout_clears_unmodelled_office_notch() -> None:
    """No plane may sit in the back-right office notch.

    ``collisions.check`` only validates the bounding rectangle, so a layout
    edit could drift a plane into the (real, non-floor) office corner and the
    validity test above would stay green. Pin the clearance the rectangular
    model cannot see (#424).
    """
    layout = load_layout(HERRENTEICH / "layout.yaml")
    x0, y0, x1, y1 = NOTCH
    for placement in layout.placements:
        parts = aircraft_parts_world(layout.fleet[placement.plane_id], placement)
        for part in parts:
            for x, y in part.polygon.exterior.coords:
                assert not (x0 <= x <= x1 and y0 <= y <= y1), (
                    f"{placement.plane_id} {part.kind} vertex ({x:.2f}, {y:.2f}) "
                    f"is inside the unmodelled office notch — non-floor space"
                )
