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
from hangarfit.loader import load_fleet, load_hangar, load_layout

REPO_ROOT = Path(__file__).resolve().parent.parent
HERRENTEICH = REPO_ROOT / "herrenteich"

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
