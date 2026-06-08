"""Tow-path planner honours the structural notch (ADR-0018, #529).

A plane being towed may not pass through (or overhang) an always-on
structural notch — the same office-corner keep-out the static checker enforces
(#528). The planner honours it in three places, mirroring the maintenance bay:

- the final-path oracle (:func:`path_first_conflict`) inherits it for the mover
  via the merged ``collisions.check`` — the mover-bounds skip is
  ``hangar_bounds``-only, so a distinct ``structural_notch`` conflict surfaces;
- the fast in-search check (:func:`_motion_clear`, rule D) rejects a pose whose
  footprint overlaps a notch (polygon overlap, so an *edge* crossing the notch
  with no vertex inside is caught too);
- the geodesic heuristic (:func:`_build_grid_heuristic`) routes around notch
  cells.

A hangar with no notch is unaffected (empty ``notch_boxes`` ⇒ every check is a
no-op), pinned by :func:`test_no_notch_is_inert`.
"""

from __future__ import annotations

from hangarfit.models import (
    Aircraft,
    Door,
    Hangar,
    Layout,
    MaintenanceBay,
    Part,
    StructuralNotch,
    Wheels,
)
from hangarfit.towplanner import (
    _GRID_XY_M,
    Pose,
    _build_grid_heuristic,
    _build_obstacles,
    _motion_clear,
    path_first_conflict,
    plan_dubins,
)

_TAIL_WHEELS = Wheels(main_offset_x_m=0.20, track_m=1.8, third_wheel_offset_x_m=-2.0)

# Back-right notch in a 20x20 hangar: x in [14, 20], y in [14, 20].
_NOTCH = (14.0, 14.0, 20.0, 20.0)


def _box_plane(plane_id: str = "B") -> Aircraft:
    """A minimal own-gear plane: one 1.0 x 0.6 m fuselage box mounted forward
    (``offset_x_m = 0.5``) so a ``y = 0`` entry pose keeps every vertex at
    ``y >= 0`` (no spurious front-wall ``hangar_bounds``)."""
    return Aircraft(
        id=plane_id,
        name=f"Plane {plane_id}",
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_own_gear",
        turn_radius_m=4.0,
        measured=False,
        parts=(
            Part(
                kind="fuselage_aft",
                length_m=1.0,
                width_m=0.6,
                offset_x_m=0.5,
                offset_y_m=0.0,
                angle_deg=0.0,
                z_bottom_m=0.0,
                z_top_m=1.0,
            ),
        ),
        wheels=_TAIL_WHEELS,
    )


def _hangar(*, with_notch: bool) -> Hangar:
    notches = (StructuralNotch(*_NOTCH),) if with_notch else ()
    return Hangar(
        length_m=20.0,
        width_m=20.0,
        door=Door(center_x_m=10.0, width_m=6.0),
        maintenance_bay=MaintenanceBay(center_x_m=10.0, width_m=4.0, depth_m=2.0),
        clearance_m=0.5,
        wing_layer_clearance_m=0.5,
        structural_notches=notches,
    )


def _placed(hangar: Hangar, mover: Aircraft) -> Layout:
    """Empty placed layout (only the mover in the fleet) so the only possible
    conflicts are hangar bounds (skipped for the mover) and the notch."""
    return Layout(fleet={mover.id: mover}, hangar=hangar, placements=())


# ── final-path oracle (path_first_conflict) ──────────────────────────────────


def test_tow_path_into_notch_is_blamed_on_mover() -> None:
    """A tow ending with the footprint inside the office notch returns a
    ``structural_notch`` conflict naming the mover (the mover-bounds skip is
    ``hangar_bounds``-only, so this distinct kind is not swallowed)."""
    mover = _box_plane()
    placed = _placed(_hangar(with_notch=True), mover)
    # Goal (17, 17, 0): box world footprint x in [16.7, 17.3], y in [17, 18] —
    # squarely inside the notch [14,20] x [14,20].
    arc = plan_dubins(Pose(8.0, 0.0, 0.0), Pose(17.0, 17.0, 0.0), turn_radius_m=4.0)
    conflict = path_first_conflict(arc, mover, mover_on_carts=False, placed=placed)
    assert conflict is not None
    assert conflict.kind == "structural_notch"
    assert mover.id in conflict.planes


def test_tow_path_clear_of_notch_is_none() -> None:
    """The same hangar, a tow straight up the clear x = 8 corridor (far from the
    back-right notch) returns no conflict."""
    mover = _box_plane()
    placed = _placed(_hangar(with_notch=True), mover)
    arc = plan_dubins(Pose(8.0, 0.0, 0.0), Pose(8.0, 12.0, 0.0), turn_radius_m=4.0)
    assert path_first_conflict(arc, mover, mover_on_carts=False, placed=placed) is None


# ── fast in-search check (_motion_clear, rule D) ─────────────────────────────


def test_motion_clear_rejects_pose_overhanging_notch() -> None:
    mover = _box_plane()
    hangar = _hangar(with_notch=True)
    obstacles = _build_obstacles(_placed(hangar, mover), mover_id=mover.id)
    assert len(obstacles.notch_boxes) == 1
    in_notch = Pose(17.0, 17.0, 0.0)  # footprint inside the notch
    clear = Pose(8.0, 8.0, 0.0)  # mid-floor, no notch, no other planes
    assert _motion_clear(mover, in_notch, obstacles, hangar) is False
    assert _motion_clear(mover, clear, obstacles, hangar) is True


# ── geodesic heuristic (_build_grid_heuristic) ───────────────────────────────


def test_grid_heuristic_blocks_notch_cells() -> None:
    mover = _box_plane()
    hangar = _hangar(with_notch=True)
    obstacles = _build_obstacles(_placed(hangar, mover), mover_id=mover.id)
    field = _build_grid_heuristic(Pose(8.0, 2.0, 0.0), obstacles, hangar)
    # A cell whose centre is deep inside the notch is blocked (absent from the
    # reachable cost-to-go field).
    notch_cell = (round(17.0 / _GRID_XY_M), round(17.0 / _GRID_XY_M))
    assert notch_cell not in field
    # A clear floor cell is reachable.
    clear_cell = (round(8.0 / _GRID_XY_M), round(8.0 / _GRID_XY_M))
    assert clear_cell in field


# ── inert when no notch (byte-identical pre-notch routing) ───────────────────


def test_no_notch_is_inert() -> None:
    mover = _box_plane()
    hangar = _hangar(with_notch=False)
    obstacles = _build_obstacles(_placed(hangar, mover), mover_id=mover.id)
    assert obstacles.notch_boxes == ()
    # The same pose the notch rejects is clear when no notch is configured.
    assert _motion_clear(mover, Pose(17.0, 17.0, 0.0), obstacles, hangar) is True
    arc = plan_dubins(Pose(8.0, 0.0, 0.0), Pose(17.0, 17.0, 0.0), turn_radius_m=4.0)
    assert (
        path_first_conflict(arc, mover, mover_on_carts=False, placed=_placed(hangar, mover)) is None
    )
