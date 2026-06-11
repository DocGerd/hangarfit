"""Ground-object collision wiring (#601).

Task 9 wires ground objects into :func:`hangarfit.collisions.check`:

* a **fixed_obstacle** footprint is a keep-out — any aircraft/mover part
  conflicting with it emits a single-object ``ground_obstacle`` conflict;
* a **placed_routed_mover** joins the placed-body set and participates in
  pairwise collision exactly like an aircraft (its ``"ground"`` part shows up
  in a ``*_overlap`` kind);
* with **no** ground objects the result is byte-identical to pre-#601.

These tests build their own locals (no pytest fixtures): an aircraft via
:func:`tests.conftest.make_test_aircraft`, a hangar via the inline ``_hangar``
helper (copied from ``tests/test_collisions.py``), and a ``"ground"`` footprint
via the local ``_ground_part`` helper. The factory aircraft has wing z 2.0–2.2
and fuselage z 0–1.0, so a ground footprint with ``z_top_m >= 2.2`` placed under
the plane meets the wing layer and conflicts; a footprint placed in a far corner
is clear.
"""

from __future__ import annotations

from hangarfit.collisions import check
from hangarfit.models import (
    Door,
    GroundObject,
    Hangar,
    Layout,
    MaintenanceBay,
    Part,
    Placement,
)
from tests.conftest import make_test_aircraft


def _hangar(clearance: float = 0.3, wlc: float = 0.2) -> Hangar:
    return Hangar(
        length_m=40.0,
        width_m=40.0,
        door=Door(center_x_m=20.0, width_m=12.0),
        maintenance_bay=MaintenanceBay(center_x_m=20.0, width_m=8.0, depth_m=6.0),
        clearance_m=clearance,
        wing_layer_clearance_m=wlc,
    )


def _ground_part(length_m: float = 4.0, width_m: float = 2.0, z_top_m: float = 1.5) -> Part:
    return Part(
        kind="ground",
        length_m=length_m,
        width_m=width_m,
        offset_x_m=0.0,
        offset_y_m=0.0,
        angle_deg=0.0,
        z_bottom_m=0.0,
        z_top_m=z_top_m,
    )


def test_aircraft_over_fixed_obstacle_conflicts() -> None:
    # A fixed obstacle directly under an aircraft (footprint reaching the wing
    # layer) → a ground_obstacle conflict naming the body and the obstacle.
    hangar = _hangar()
    ac = make_test_aircraft(id="p1")
    obj = GroundObject(
        id="obstacle",
        name="o",
        parts=(_ground_part(z_top_m=3.0),),
        object_class="fixed_obstacle",
    )
    layout = Layout(
        fleet={ac.id: ac},
        hangar=hangar,
        placements=(Placement(plane_id=ac.id, x_m=6.0, y_m=6.0, heading_deg=0.0, on_carts=False),),
        ground_objects={obj.id: obj},
        ground_object_placements=(
            Placement(plane_id=obj.id, x_m=6.0, y_m=6.0, heading_deg=0.0, on_carts=False),
        ),
    )
    result = check(layout)
    kinds = {c.kind for c in result.conflicts}
    assert "ground_obstacle" in kinds
    assert any("obstacle" in "".join(c.planes) or "obstacle" in c.detail for c in result.conflicts)
    assert not result.valid


def test_mover_overlapping_aircraft_conflicts() -> None:
    # A mover overlapping the aircraft participates in pairwise collision; its
    # "ground" part shows up in a *_overlap conflict kind.
    hangar = _hangar()
    ac = make_test_aircraft(id="p1")
    mover = GroundObject(
        id="caddy",
        name="c",
        parts=(_ground_part(z_top_m=3.0),),
        object_class="placed_routed_mover",
        motion_mode="steerable",
        turn_radius_m=4.0,
    )
    layout = Layout(
        fleet={ac.id: ac},
        hangar=hangar,
        placements=(Placement(plane_id=ac.id, x_m=6.0, y_m=6.0, heading_deg=0.0, on_carts=False),),
        ground_objects={mover.id: mover},
        ground_object_placements=(
            Placement(plane_id=mover.id, x_m=6.0, y_m=6.0, heading_deg=0.0, on_carts=False),
        ),
    )
    result = check(layout)
    assert any(c.kind.endswith("_overlap") and "ground" in c.kind for c in result.conflicts)
    assert not result.valid


def test_separated_ground_object_is_valid() -> None:
    # A fixed obstacle in a far corner does not conflict with the aircraft.
    hangar = _hangar()
    ac = make_test_aircraft(id="p1")
    obj = GroundObject(
        id="obstacle",
        name="o",
        parts=(_ground_part(),),
        object_class="fixed_obstacle",
    )
    layout = Layout(
        fleet={ac.id: ac},
        hangar=hangar,
        placements=(
            Placement(plane_id=ac.id, x_m=20.0, y_m=20.0, heading_deg=0.0, on_carts=False),
        ),
        ground_objects={obj.id: obj},
        ground_object_placements=(
            # Far corner, no overlap with the plane at (20, 20).
            Placement(plane_id=obj.id, x_m=2.0, y_m=2.0, heading_deg=0.0, on_carts=False),
        ),
    )
    result = check(layout)
    assert result.valid
    assert result.conflicts == ()


def test_separated_mover_is_valid() -> None:
    # A mover in a far corner does not conflict either (pairwise, no overlap).
    hangar = _hangar()
    ac = make_test_aircraft(id="p1")
    mover = GroundObject(
        id="caddy",
        name="c",
        parts=(_ground_part(),),
        object_class="placed_routed_mover",
        motion_mode="steerable",
        turn_radius_m=4.0,
    )
    layout = Layout(
        fleet={ac.id: ac},
        hangar=hangar,
        placements=(
            Placement(plane_id=ac.id, x_m=20.0, y_m=20.0, heading_deg=0.0, on_carts=False),
        ),
        ground_objects={mover.id: mover},
        ground_object_placements=(
            Placement(plane_id=mover.id, x_m=2.0, y_m=2.0, heading_deg=0.0, on_carts=False),
        ),
    )
    assert check(layout).valid


def test_empty_ground_objects_byte_identical() -> None:
    """A valid layout with EMPTY ground-object fields is byte-identical to the
    pre-#601 result: valid, no conflicts, and the same total penetration.

    The reference values are computed from the SAME aircraft-only layout (the
    ground-object fields are constructed empty), so this pins that the new
    ``check`` wiring is inert when no ground objects are present: ``placed_bodies
    == aircraft_parts`` (same dict order) and ``_ground_obstacle_conflicts``
    returns ``[]``.
    """
    hangar = _hangar()
    ac = make_test_aircraft(id="p1")
    placements = (Placement(plane_id=ac.id, x_m=10.0, y_m=10.0, heading_deg=0.0, on_carts=False),)

    # Reference: the layout WITHOUT touching ground-object fields at all.
    reference = Layout(fleet={ac.id: ac}, hangar=hangar, placements=placements)
    ref_result = check(reference)

    # The same layout, but with the ground-object fields explicitly empty.
    with_empty = Layout(
        fleet={ac.id: ac},
        hangar=hangar,
        placements=placements,
        ground_objects={},
        ground_object_placements=(),
    )
    result = check(with_empty)

    assert result.valid
    assert result.conflicts == ()
    assert result.conflicts == ref_result.conflicts
    assert result.total_penetration_m2 == ref_result.total_penetration_m2


def test_ground_object_out_of_bounds_flagged() -> None:
    """A ground object straddling the hangar wall is a hangar_bounds conflict
    (#605 — #601 left ground objects un-bounds-checked)."""
    hangar = _hangar()  # 40x40, no notch
    obj = GroundObject(
        id="trailer",
        name="t",
        parts=(_ground_part(length_m=4.0, width_m=2.0),),
        object_class="placed_routed_mover",
        motion_mode="towed",
    )
    layout = Layout(
        fleet={},
        hangar=hangar,
        placements=(),
        ground_objects={obj.id: obj},
        # centred on x=0.5 with a 2 m width → half the footprint is at x<0.
        ground_object_placements=(
            Placement(plane_id=obj.id, x_m=0.5, y_m=20.0, heading_deg=0.0, on_carts=False),
        ),
    )
    result = check(layout)
    kinds = {c.kind for c in result.conflicts}
    assert "hangar_bounds" in kinds
    assert any("trailer" in "".join(c.planes) for c in result.conflicts)


def test_ground_object_in_notch_flagged() -> None:
    """A ground object inside a structural notch is a structural_notch conflict."""
    from hangarfit.models import StructuralNotch

    hangar = Hangar(
        length_m=40.0,
        width_m=40.0,
        door=Door(center_x_m=20.0, width_m=12.0),
        maintenance_bay=MaintenanceBay(center_x_m=20.0, width_m=8.0, depth_m=6.0),
        clearance_m=0.3,
        wing_layer_clearance_m=0.2,
        structural_notches=(
            StructuralNotch(x_min_m=30.0, y_min_m=30.0, x_max_m=40.0, y_max_m=40.0),
        ),
    )
    obj = GroundObject(
        id="caddy",
        name="c",
        parts=(_ground_part(length_m=3.0, width_m=2.0),),
        object_class="placed_routed_mover",
        motion_mode="steerable",
    )
    layout = Layout(
        fleet={},
        hangar=hangar,
        placements=(),
        ground_objects={obj.id: obj},
        ground_object_placements=(
            Placement(plane_id=obj.id, x_m=35.0, y_m=35.0, heading_deg=0.0, on_carts=False),
        ),
    )
    kinds = {c.kind for c in check(layout).conflicts}
    assert "structural_notch" in kinds


def test_fixed_obstacle_out_of_bounds_flagged() -> None:
    """A fixed obstacle is bounds-checked too (not just movers)."""
    hangar = _hangar()
    obj = GroundObject(
        id="fuel",
        name="f",
        parts=(_ground_part(length_m=4.0, width_m=2.0),),
        object_class="fixed_obstacle",
    )
    layout = Layout(
        fleet={},
        hangar=hangar,
        placements=(),
        ground_objects={obj.id: obj},
        ground_object_placements=(
            Placement(plane_id=obj.id, x_m=39.7, y_m=20.0, heading_deg=0.0, on_carts=False),
        ),
    )
    assert "hangar_bounds" in {c.kind for c in check(layout).conflicts}
