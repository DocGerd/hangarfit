"""PR3 (#593): the polygon-parts feature ships exactly ONE authored taper — the
Herrenteich Scheibe SF-25E wing — as the value proof (a tapered glider wingtip
nests where its bounding rectangle would falsely conflict). Every OTHER shipped
Part stays a rectangle (``local_vertices is None``), so the rest of both fleets
is byte-identical. The folded Stemme wing deliberately stays a rectangle
(folding != taper; spec section 5)."""

from __future__ import annotations

import pytest

from hangarfit.loader import load_fleet

_SHIPPED_FLEETS = ["data/fleet.yaml", "examples/herrenteich/fleet.yaml"]

# The single authored taper across all shipped fleets: (fleet, aircraft, part kind).
_TAPER = ("examples/herrenteich/fleet.yaml", "scheibe_falke", "wing")


@pytest.mark.parametrize("path", _SHIPPED_FLEETS)
def test_only_the_scheibe_wing_is_a_polygon(path: str) -> None:
    fleet = load_fleet(path)
    for ac in fleet.values():
        for part in ac.parts:
            if (path, ac.id, part.kind) == _TAPER:
                assert part.local_vertices is not None, (
                    "the Scheibe SF-25E wing must ship as a taper polygon (#593)"
                )
                assert len(part.local_vertices) == 6, (
                    "symmetric double-taper wing → a 6-vertex hexagon"
                )
            else:
                assert part.local_vertices is None, (
                    f"{path}:{ac.id} part {part.kind!r} unexpectedly carries a polygon "
                    f"footprint; only the Scheibe wing is authored as a taper"
                )


def test_folded_stemme_wing_stays_a_rectangle() -> None:
    # Folding is not a taper: a linear-taper polygon would fabricate a planform
    # that does not physically exist in the hangared (folded) config (spec section 5).
    fleet = load_fleet("examples/herrenteich/fleet.yaml")
    wing = next(p for p in fleet["stemme_s10"].parts if p.kind == "wing")
    assert wing.local_vertices is None
