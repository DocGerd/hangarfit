"""PR1 guarantee: the polygon-parts feature ships PLUMBING only. No shipped
fleet aircraft is authored as a polygon yet, so every shipped Part keeps
local_vertices=None and the real fleets stay byte-identical. The Scheibe taper
lands in a later PR once the viewer renders N-gon (#548 stack)."""

from __future__ import annotations

import pytest

from hangarfit.loader import load_fleet

_SHIPPED_FLEETS = ["data/fleet.yaml", "examples/herrenteich/fleet.yaml"]


@pytest.mark.parametrize("path", _SHIPPED_FLEETS)
def test_shipped_fleet_has_no_polygon_parts(path: str) -> None:
    fleet = load_fleet(path)
    for ac in fleet.values():
        for part in ac.parts:
            assert part.local_vertices is None, (
                f"{path}:{ac.id} part {part.kind!r} unexpectedly carries a polygon "
                f"footprint; PR1 must keep shipped fleets byte-identical"
            )
