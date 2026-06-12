"""Pure data models for hangarfit.

No I/O, no business logic. Every type here is a frozen dataclass with
slots, validates its invariants in ``__post_init__``, and uses tuples
instead of lists where collections appear (so the whole graph is
effectively immutable after construction).

The full coordinate convention and parts-model collision rule live in
``docs/architecture/08-crosscutting-concepts.md`` — this module just
encodes the types.
"""

from __future__ import annotations

import math
import typing
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, cast

from shapely import box, union_all
from shapely.geometry import LinearRing
from shapely.geometry.base import BaseGeometry

if TYPE_CHECKING:
    from hangarfit.towplanner import MovesPlan

WingPosition = Literal["high", "mid", "low"]
Gear = Literal["tailwheel", "nosewheel", "monowheel"]
MovementMode = Literal["always_cart", "always_own_gear", "cart_eligible"]
GroundObjectClass = Literal["fixed_obstacle", "placed_routed_mover"]
MoverMotionMode = Literal["steerable", "towed"]
# `tail` denotes the horizontal stabilizer specifically (a wide, usually-low
# overhangable surface — see metrics._OVERHANGABLE); `vertical_stabilizer` is the
# fin (thin, tall, never overhangable). Empennage split per ADR-0023.
# `ground` denotes a non-aircraft ground-object footprint (a solid keep-out/body,
# never overhangable — #601).
PartKind = Literal[
    "fuselage_front", "fuselage_aft", "wing", "strut", "tail", "vertical_stabilizer", "ground"
]

_VALID_PART_KINDS = frozenset(typing.get_args(PartKind))
_VALID_WING_POSITIONS = frozenset(typing.get_args(WingPosition))
_VALID_GEARS = frozenset(typing.get_args(Gear))
_VALID_MOVEMENT_MODES = frozenset(typing.get_args(MovementMode))
_VALID_GROUND_OBJECT_CLASSES = frozenset(typing.get_args(GroundObjectClass))
_VALID_MOVER_MOTION_MODES = frozenset(typing.get_args(MoverMotionMode))

# Below this absolute |2·signed-area| a ring is treated as degenerate
# (zero-area / collinear). 1e-9 m² is far tighter than any real part.
_RING_MIN_ABS_SIGNED_AREA = 1e-9
# Float slack for the local_vertices-within-bbox containment check.
_PART_BBOX_TOL_M = 1e-9

# A 2D point in a part's own (plane-local) frame; a polygon ring is a tuple of these.
Vertex = tuple[float, float]


def _canonicalize_ring(
    vertices: Sequence[Vertex],
) -> tuple[Vertex, ...]:
    """Canonicalize an author-supplied polygon ring to a deterministic form.

    Returns the ring OPEN (no closing duplicate), wound counter-clockwise,
    rotated so the lexicographically-smallest vertex is first. Two orderings
    of the SAME shape therefore produce a byte-identical tuple — the
    determinism contract (ADR-0003) for polygon parts, since the geometry
    layer never re-orients at solve time (Shapely preserves vertex order
    verbatim). Rejects non-finite, fewer-than-3-vertex, degenerate
    (zero-area / collinear), and self-intersecting rings.
    """
    pts = [(float(x), float(y)) for x, y in vertices]
    # Drop an explicit closing duplicate if the author supplied one.
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 3:
        raise ValueError(f"polygon ring needs >= 3 distinct vertices, got {len(pts)}")
    if not all(math.isfinite(x) and math.isfinite(y) for x, y in pts):
        raise ValueError(f"polygon ring has a non-finite vertex: {pts}")
    # Degeneracy + self-intersection gates, in this order so each case gets the
    # right error message:
    #   (a) max |cross-product| over consecutive triples == 0  -> "degenerate"
    #       (rejects FULLY collinear / zero-area rings; a redundant collinear
    #       vertex on an otherwise-valid ring is tolerated, not rejected).
    #   (b) Shapely LinearRing.is_simple is False                -> "self-intersects".
    #   (c) shoelace signed area: its SIGN drives the CCW flip below; the
    #       |area| < eps check is defense-in-depth, unreachable after (a)+(b).
    n = len(pts)
    max_cross = max(
        abs(
            (pts[(i + 1) % n][0] - pts[i][0]) * (pts[(i + 2) % n][1] - pts[i][1])
            - (pts[(i + 1) % n][1] - pts[i][1]) * (pts[(i + 2) % n][0] - pts[i][0])
        )
        for i in range(n)
    )
    if max_cross < _RING_MIN_ABS_SIGNED_AREA:
        raise ValueError(f"polygon ring is degenerate (near-zero area): {pts}")
    if not LinearRing(pts).is_simple:
        raise ValueError(f"polygon ring self-intersects: {pts}")
    # Signed area (shoelace) gives both the winding sign and a final degeneracy check.
    signed_area2 = sum(
        pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n)
    )
    # Defense-in-depth: unreachable for a simple, non-collinear polygon (both gated above).
    if abs(signed_area2) < _RING_MIN_ABS_SIGNED_AREA:  # pragma: no cover
        raise ValueError(f"polygon ring is degenerate (near-zero area): {pts}")
    # Force counter-clockwise (positive signed area).
    if signed_area2 < 0:
        pts = list(reversed(pts))
    # Rotate to the lexicographically-minimum start vertex.
    start = min(range(len(pts)), key=lambda i: pts[i])
    pts = pts[start:] + pts[:start]
    return tuple(pts)


@dataclass(frozen=True, slots=True)
class Part:
    """One oriented rectangle in plane-local coordinates with a height range.

    The universal collision unit. Fuselage segments, wing, and each strut
    are all represented as ``Part`` instances. See
    ``docs/architecture/08-crosscutting-concepts.md`` "The coordinate
    convention" for plane-local coordinates (``+x`` forward, ``+y`` right).

    ``kind`` is closed: ``"fuselage_front" | "fuselage_aft" | "wing" |
    "strut" | "tail" | "vertical_stabilizer"``. The fuselage is split
    front/aft so the collision rule can distinguish a wing over a cockpit
    (``fuselage_front`` — always a conflict in plan view, height ignored)
    from a wing over a tail (``fuselage_aft`` — today's two-clause z-gap
    rule); see ADR-0012. The empennage is two explicit surfaces (ADR-0023):
    ``tail`` (the horizontal stabilizer — wide, usually low) and
    ``vertical_stabilizer`` (the fin + rudder — thin, tall, rising into the
    wing layer so a wing nested over the centreline conflicts with it). See
    ``docs/architecture/08-crosscutting-concepts.md`` "The parts model".
    The legacy ``"fuselage"`` keyword is **not** a constructed kind: it
    survives only as a transient YAML keyword the loader auto-splits at the
    wing trailing-edge station (see :func:`hangarfit.loader._split_fuselage`).
    New kinds need only be added to the ``PartKind`` literal —
    ``_VALID_PART_KINDS`` is derived from it via ``get_args`` — after which
    the collision checker and visualizer must learn to key off them.
    """

    kind: PartKind
    length_m: float
    width_m: float
    offset_x_m: float
    offset_y_m: float
    angle_deg: float
    z_bottom_m: float
    z_top_m: float
    local_vertices: tuple[Vertex, ...] | None = None

    def __post_init__(self) -> None:
        if self.kind not in _VALID_PART_KINDS:
            raise ValueError(
                f"Part.kind must be one of {sorted(_VALID_PART_KINDS)}, got {self.kind!r}"
            )
        if self.length_m <= 0:
            raise ValueError(f"Part {self.kind!r}: length_m must be positive, got {self.length_m}")
        if self.width_m <= 0:
            raise ValueError(f"Part {self.kind!r}: width_m must be positive, got {self.width_m}")
        if self.z_bottom_m < 0:
            raise ValueError(
                f"Part {self.kind!r}: z_bottom_m must be non-negative, got {self.z_bottom_m}"
            )
        if self.z_top_m <= self.z_bottom_m:
            raise ValueError(
                f"Part {self.kind!r}: z_top_m must exceed z_bottom_m, "
                f"got z_bottom={self.z_bottom_m}, z_top={self.z_top_m}"
            )
        if self.local_vertices is not None:
            canonical = _canonicalize_ring(self.local_vertices)
            half_l = self.length_m / 2.0
            half_w = self.width_m / 2.0
            # The polygon is checked against the axis-aligned bbox in the part's
            # OWN (unrotated) frame. That is sufficient even when angle_deg != 0:
            # aircraft_parts_world rotates the polygon and the bbox together, and a
            # rigid rotation preserves the subset relation.
            for x, y in canonical:
                if abs(x) > half_l + _PART_BBOX_TOL_M or abs(y) > half_w + _PART_BBOX_TOL_M:
                    raise ValueError(
                        f"Part {self.kind!r}: local_vertices vertex ({x}, {y}) lies outside "
                        f"the length_m x width_m bbox (+/-{half_l} x +/-{half_w}); the polygon "
                        f"footprint must be a subset of the bounding box"
                    )
            object.__setattr__(self, "local_vertices", canonical)


@dataclass(frozen=True, slots=True)
class StrutsSpec:
    """Convenience block describing a strut-braced aircraft's struts.

    The loader / geometry layer expands one ``StrutsSpec`` into two
    mirrored strut ``Part`` instances (one per side). Stored on
    ``Aircraft.struts`` only for aircraft that actually have struts;
    cantilever aircraft leave it ``None``.

    The geometry: each strut runs from a fuselage attach point at
    height ``fuselage_attach_z_m`` outward and upward to a wing attach
    point at offset ``wing_attach_y_m`` (measured along the half-span)
    at the wing's underside height.
    """

    fuselage_attach_x_m: float
    fuselage_attach_y_m: float
    fuselage_attach_z_m: float
    wing_attach_y_m: float
    width_m: float

    def __post_init__(self) -> None:
        if self.width_m <= 0:
            raise ValueError(f"StrutsSpec: width_m must be positive, got {self.width_m}")
        if self.fuselage_attach_y_m < 0:
            raise ValueError(
                f"StrutsSpec: fuselage_attach_y_m must be non-negative "
                f"(outboard distance at fuselage side), got {self.fuselage_attach_y_m}"
            )
        if self.fuselage_attach_z_m < 0:
            raise ValueError(
                f"StrutsSpec: fuselage_attach_z_m must be non-negative, "
                f"got {self.fuselage_attach_z_m}"
            )
        if self.wing_attach_y_m <= 0:
            raise ValueError(
                f"StrutsSpec: wing_attach_y_m must be positive "
                f"(outboard distance on the wing), got {self.wing_attach_y_m}"
            )
        if self.wing_attach_y_m < self.fuselage_attach_y_m:
            raise ValueError(
                f"StrutsSpec: wing_attach_y_m ({self.wing_attach_y_m}) must be "
                f">= fuselage_attach_y_m ({self.fuselage_attach_y_m}); a strut "
                f"must run outward, not inward through the fuselage"
            )


@dataclass(frozen=True, slots=True)
class Wheels:
    """Plane-local wheel positions for one aircraft.

    Origin is the per-aircraft anchor that ``Placement.x_m / y_m`` refers to —
    the same origin every other Part offset is measured from. Each main wheel
    sits at ``(main_offset_x_m, ±track_m/2)``; the third (nose or tail) wheel,
    if present, sits at ``(third_wheel_offset_x_m, 0)``.

    ``track_m`` and ``third_wheel_offset_x_m`` are both ``None`` for monowheel
    aircraft (only the central main wheel is modelled; outriggers stay
    render-only via the wing footprint). For tricycle and tailwheel aircraft,
    both fields are required — the loader enforces this against ``gear``.
    """

    main_offset_x_m: float
    track_m: float | None
    third_wheel_offset_x_m: float | None

    def __post_init__(self) -> None:
        if not math.isfinite(self.main_offset_x_m):
            raise ValueError(f"Wheels.main_offset_x_m must be finite, got {self.main_offset_x_m!r}")
        if (self.track_m is None) != (self.third_wheel_offset_x_m is None):
            # XOR: both present (tricycle/tailwheel) or both absent (monowheel).
            if self.track_m is None:
                raise ValueError(
                    "Wheels.third_wheel_offset_x_m requires track_m to also be set "
                    "(both present for tricycle/tailwheel, both None for monowheel)"
                )
            raise ValueError(
                "Wheels.track_m requires third_wheel_offset_x_m to also be set "
                "(both present for tricycle/tailwheel, both None for monowheel)"
            )
        if self.track_m is not None:
            if not math.isfinite(self.track_m):
                raise ValueError(f"Wheels.track_m must be finite, got {self.track_m!r}")
            if self.track_m <= 0.0:
                raise ValueError(f"Wheels.track_m must be positive, got {self.track_m!r}")
        if self.third_wheel_offset_x_m is not None and not math.isfinite(
            self.third_wheel_offset_x_m
        ):
            raise ValueError(
                f"Wheels.third_wheel_offset_x_m must be finite, got {self.third_wheel_offset_x_m!r}"
            )

    @property
    def positions(self) -> tuple[tuple[float, float], ...]:
        """Plane-local ``(x, y)`` of every wheel.

        Returns 1 entry for monowheel (``(main_offset_x_m, 0)``) or 3 entries
        for tricycle/tailwheel (two mains at ``(main_offset_x_m, ±track_m/2)``
        then the third wheel at ``(third_wheel_offset_x_m, 0)``). The order is
        stable: mains first (``+y`` then ``-y``), then the third wheel.
        """
        if self.track_m is None:
            return ((self.main_offset_x_m, 0.0),)
        # __post_init__'s XOR rule guarantees third_wheel_offset_x_m is set whenever
        # track_m is set; cast makes that invariant visible to mypy without an assert
        # (which would get stripped under -O).
        third_x = cast(float, self.third_wheel_offset_x_m)
        half_track = self.track_m / 2.0
        return (
            (self.main_offset_x_m, half_track),
            (self.main_offset_x_m, -half_track),
            (third_x, 0.0),
        )

    @property
    def wheelbase_m(self) -> float | None:
        """``abs(third_wheel_offset_x_m - main_offset_x_m)``, or ``None`` for monowheel."""
        if self.third_wheel_offset_x_m is None:
            return None
        return abs(self.third_wheel_offset_x_m - self.main_offset_x_m)


@dataclass(frozen=True, slots=True)
class Aircraft:
    """One plane in the fleet.

    ``parts`` is the single source of truth for geometry — the collision
    checker and visualizer key off it directly. There is **no** ``struts``
    field on the constructed ``Aircraft``: ``StrutsSpec`` is a transient
    YAML-schema-only type that the loader (#3) expands into mirrored
    strut ``Part`` instances and folds into ``parts`` before constructing
    the ``Aircraft``. This keeps the parts model the unambiguous canonical
    geometric representation (no risk of strut volume being double-counted
    once from ``struts`` and again from a strut ``Part``).

    ``turn_radius_m`` is required for any non-``always_cart`` aircraft
    (the future Dubins-path planner needs it for own-gear motion).
    For ``always_cart`` it may be ``None`` (or any value — it is ignored).

    ``wheels`` is the canonical source of per-aircraft wheel positions
    (ADR-0013). The loader populates it from a required per-aircraft
    ``wheels:`` block in ``fleet.yaml`` and rejects any entry missing it.
    Consumers (visualize, tow-path planner) read positions exclusively
    through :meth:`Wheels.positions`.
    """

    id: str
    name: str
    wing_position: WingPosition
    gear: Gear
    movement_mode: MovementMode
    turn_radius_m: float | None
    measured: bool
    parts: tuple[Part, ...]
    wheels: Wheels
    notes: str = ""
    tow_pivotable: bool = False
    """(#263 / ADR-0022) When True, this plane is planned with the pivot-in-place
    *towing* motion: :meth:`effective_turn_radius_m` returns ``0.0`` so the tow
    planner routes it with the zero-radius cart-pivot fan (no new motion
    primitive). Models a free-castering tailwheel or a tail-down nose-lift pivot.
    Orthogonal to ``movement_mode`` — a flagged own-gear plane stays
    ``on_carts=False`` and the cart-pool accounting is untouched. The declared
    ``turn_radius_m`` is retained (powered-taxi semantics); only the tow radius is
    overridden. Default ``False``."""

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Aircraft.id must be non-empty")
        if not self.name:
            raise ValueError(f"Aircraft {self.id!r}: name must be non-empty")
        if not self.parts:
            raise ValueError(f"Aircraft {self.id!r}: parts must be non-empty")
        if self.wing_position not in _VALID_WING_POSITIONS:
            raise ValueError(
                f"Aircraft {self.id!r}: wing_position must be one of "
                f"{sorted(_VALID_WING_POSITIONS)}, got {self.wing_position!r}"
            )
        if self.gear not in _VALID_GEARS:
            raise ValueError(
                f"Aircraft {self.id!r}: gear must be one of "
                f"{sorted(_VALID_GEARS)}, got {self.gear!r}"
            )
        if self.movement_mode not in _VALID_MOVEMENT_MODES:
            raise ValueError(
                f"Aircraft {self.id!r}: movement_mode must be one of "
                f"{sorted(_VALID_MOVEMENT_MODES)}, got {self.movement_mode!r}"
            )
        if self.movement_mode != "always_cart":
            if self.turn_radius_m is None:
                raise ValueError(
                    f"Aircraft {self.id!r}: turn_radius_m is required when "
                    f"movement_mode={self.movement_mode!r}"
                )
            if self.turn_radius_m <= 0:
                raise ValueError(
                    f"Aircraft {self.id!r}: turn_radius_m must be positive, "
                    f"got {self.turn_radius_m}"
                )

    @property
    def is_cart_eligible(self) -> bool:
        return self.movement_mode == "cart_eligible"

    def required_turn_radius_m(self) -> float:
        """Return ``turn_radius_m`` narrowed to ``float`` (never ``None``).

        Use when calling code statically knows the aircraft is not
        ``always_cart`` and therefore must have a turn radius (Dubins
        planner, etc.). Raises ``AssertionError`` if the invariant has
        been broken (which ``__post_init__`` should prevent).
        """
        if self.turn_radius_m is None:
            raise AssertionError(
                f"Aircraft {self.id!r}: turn_radius_m is None "
                f"(movement_mode={self.movement_mode!r}); caller assumed "
                f"a turn radius was available."
            )
        return self.turn_radius_m

    def effective_turn_radius_m(self) -> float:
        """Turn radius for path planning: ``0.0`` for cart-borne *or*
        ``tow_pivotable`` planes (a pivot-in-place), else the own-gear
        ``required_turn_radius_m()``.

        This is the accessor the tow-path planner consumes (ADR-0007): a
        cart-borne plane is modelled as own-gear with a zero turn radius, and a
        ``tow_pivotable`` plane (free-castering / nose-lift, #263) likewise pivots
        in place when towed. Unlike :meth:`required_turn_radius_m`, this never
        raises — callers that legitimately handle carts (the Dubins planner) use
        this one.
        """
        if self.movement_mode == "always_cart" or self.tow_pivotable:
            return 0.0
        return self.required_turn_radius_m()


@dataclass(frozen=True, slots=True)
class GroundObject:
    """A non-aircraft object on the hangar floor (#601 / ADR-0025).

    Two classes, set by ``object_class`` (derived by the loader from the
    catalog ``type:`` — ``fixed_obstacle`` → ``"fixed_obstacle"``;
    ``car``/``trailer`` → ``"placed_routed_mover"``):

    * **fixed_obstacle** — a placed-but-immovable keep-out (e.g. a fuel
      trailer at the door). Carries no motion. Its world footprint is a
      keep-out for aircraft/mover parts; the tow planner routes around it.
    * **placed_routed_mover** — a placed body that is itself routed (a
      self-driving ``steerable`` car, a ``towed`` trailer). Participates in
      pairwise collision like an aircraft. ``motion_mode`` is carried here;
      the actual route search lands in #602 (this type only carries the field).

    Geometry reuses the parts model: ``parts`` is a tuple of ``Part`` with
    ``kind="ground"`` (a solid footprint, never overhangable), transformed to
    world coords by the *same* :func:`hangarfit.geometry.aircraft_parts_world`
    path aircraft use. ``turn_radius_m`` is static catalog data carried for
    #602's routing; it is unused in #601.
    """

    id: str
    name: str
    parts: tuple[Part, ...]
    object_class: GroundObjectClass
    motion_mode: MoverMotionMode | None = None
    turn_radius_m: float | None = None
    measured: bool = False
    hard_door_mover: bool = False

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("GroundObject.id must be non-empty")
        if not self.name:
            raise ValueError(f"GroundObject {self.id!r}: name must be non-empty")
        if not self.parts:
            raise ValueError(f"GroundObject {self.id!r}: parts must be non-empty")
        for p in self.parts:
            if p.kind != "ground":
                raise ValueError(
                    f"GroundObject {self.id!r}: all parts must be kind 'ground' "
                    f"(a solid footprint), got {p.kind!r}"
                )
        if self.object_class not in _VALID_GROUND_OBJECT_CLASSES:
            raise ValueError(
                f"GroundObject {self.id!r}: object_class must be one of "
                f"{sorted(_VALID_GROUND_OBJECT_CLASSES)}, got {self.object_class!r}"
            )
        if self.object_class == "fixed_obstacle":
            if self.motion_mode is not None:
                raise ValueError(
                    f"GroundObject {self.id!r}: a fixed_obstacle must not carry a "
                    f"motion_mode (got {self.motion_mode!r}) — it never moves"
                )
            if self.turn_radius_m is not None:
                raise ValueError(
                    f"GroundObject {self.id!r}: a fixed_obstacle must not carry a "
                    f"turn_radius_m — it never moves"
                )
            if self.hard_door_mover:
                raise ValueError(
                    f"GroundObject {self.id!r}: a fixed_obstacle must not set "
                    f"hard_door_mover=True — only a placed_routed_mover may be a "
                    f"hard-door (egress) mover"
                )
        else:  # placed_routed_mover
            if self.motion_mode not in _VALID_MOVER_MOTION_MODES:
                raise ValueError(
                    f"GroundObject {self.id!r}: a placed_routed_mover requires a "
                    f"motion_mode in {sorted(_VALID_MOVER_MOTION_MODES)}, "
                    f"got {self.motion_mode!r}"
                )
            # Only movers may carry a turn radius (fixed_obstacle rejects any
            # non-None above), and when present it must be positive.
            if self.turn_radius_m is not None and self.turn_radius_m <= 0:
                raise ValueError(
                    f"GroundObject {self.id!r}: turn_radius_m must be positive, "
                    f"got {self.turn_radius_m}"
                )
            if self.motion_mode == "steerable" and self.turn_radius_m is None:
                raise ValueError(
                    f"GroundObject {self.id!r}: a steerable mover requires a "
                    f"positive turn_radius_m (a self-driven car has a turning "
                    f"circle; it never pivots in place)"
                )
            if self.motion_mode == "towed" and self.turn_radius_m is not None:
                raise ValueError(
                    f"GroundObject {self.id!r}: a towed mover must not carry a "
                    f"turn_radius_m — it moves as a free-swivel cart (radius 0.0). "
                    f"A positive-radius towed trailer is a deliberate future change "
                    f"(relax this guard + define the semantics first); got "
                    f"{self.turn_radius_m}"
                )

    def effective_turn_radius_m(self) -> float:
        """Turn radius the tow planner consumes (ADR-0010, 2026-06-12 amendment).

        Data-driven, mirroring :meth:`Aircraft.effective_turn_radius_m`: returns
        ``turn_radius_m`` when present, else ``0.0``. ``__post_init__`` co-determines
        the two fields — a ``steerable`` mover must carry a (positive) radius and a
        ``towed`` mover must not — so a steerable car returns its radius (-> own-gear
        Reeds-Shepp, six-primitive fan) and a towed trailer returns ``0.0`` (->
        free-swivel cart, four-primitive reverse-capable fan, the ground-crew
        hand-positioning model). Only ever called on movers; never raises."""
        if self.turn_radius_m is not None:
            return self.turn_radius_m
        return 0.0


@dataclass(frozen=True, slots=True)
class Door:
    center_x_m: float
    width_m: float

    def __post_init__(self) -> None:
        if self.width_m <= 0:
            raise ValueError(f"Door.width_m must be positive, got {self.width_m}")
        if self.center_x_m < 0:
            raise ValueError(f"Door.center_x_m must be non-negative, got {self.center_x_m}")


@dataclass(frozen=True, slots=True)
class MaintenanceBay:
    """A back-anchored partial-width rectangle reserved for maintenance.

    The bay always touches the hangar's back wall; its y-extent is
    ``[hangar.length_m - depth_m, hangar.length_m]``. Its x-extent is
    centered on ``center_x_m`` with width ``width_m`` (same idiom as
    :class:`Door`). The bay-fits-in-hangar check
    (``center_x_m ± width_m/2 ∈ [0, hangar.width_m]``) lives in
    :class:`Hangar` because it needs the hangar width.
    """

    center_x_m: float
    width_m: float
    depth_m: float

    def __post_init__(self) -> None:
        # ``center_x_m`` follows :class:`Door`'s precedent (non-negative);
        # the spec interval ``center_x_m ± width_m/2 ∈ [0, hangar.width_m]``
        # admits ``center_x_m == width_m/2`` (left edge flush with x=0),
        # so the local guard is non-negativity, not strict positivity.
        # The bay-fits-in-hangar check on :class:`Hangar` enforces the
        # full interval.
        if self.center_x_m < 0:
            raise ValueError(
                f"MaintenanceBay.center_x_m must be non-negative, got {self.center_x_m}"
            )
        if self.width_m <= 0:
            raise ValueError(f"MaintenanceBay.width_m must be positive, got {self.width_m}")
        if self.depth_m <= 0:
            raise ValueError(f"MaintenanceBay.depth_m must be positive, got {self.depth_m}")


@dataclass(frozen=True, slots=True)
class StructuralNotch:
    """An always-on rectangular keep-out cut from the hangar floor.

    Unlike :class:`MaintenanceBay` — a *state-gated* operational
    reservation that only bites when a plane occupies it — a structural
    notch is a **permanent absence of floor**: e.g. the Airfield
    Herrenteich hangar's back-right office annex, which makes the real
    building L-shaped rather than rectangular (ADR-0018). It is subtracted
    from the hangar's derived :attr:`Hangar.floor_polygon`, so the
    collision checker rejects any part that overhangs it — including a thin
    part whose *edge* crosses the notch with neither endpoint inside it (a
    case the legacy per-vertex bounds test missed).

    Axis-aligned, in hangar coordinates: the rectangle
    ``[x_min_m, x_max_m] × [y_min_m, y_max_m]``. The notch-fits-in-hangar
    check (``x_max_m ≤ width_m``, ``y_max_m ≤ length_m``) lives on
    :class:`Hangar` because it needs the hangar dimensions.
    """

    x_min_m: float
    y_min_m: float
    x_max_m: float
    y_max_m: float

    def __post_init__(self) -> None:
        if self.x_min_m < 0 or self.y_min_m < 0:
            raise ValueError(
                f"StructuralNotch corners must be non-negative, got "
                f"min=({self.x_min_m}, {self.y_min_m})"
            )
        if self.x_max_m <= self.x_min_m:
            raise ValueError(
                f"StructuralNotch.x_max_m ({self.x_max_m}) must be greater than "
                f"x_min_m ({self.x_min_m})"
            )
        if self.y_max_m <= self.y_min_m:
            raise ValueError(
                f"StructuralNotch.y_max_m ({self.y_max_m}) must be greater than "
                f"y_min_m ({self.y_min_m})"
            )


@dataclass(frozen=True, slots=True)
class Hangar:
    """The hangar floor plan.

    Coordinates: ``(0, 0)`` at the front-left corner, ``+x`` along the
    door wall, ``+y`` deeper into the hangar. See
    ``docs/architecture/08-crosscutting-concepts.md`` "The coordinate
    convention".

    ``max_carts`` is the number of spare carts available to the
    ``cart_eligible`` pool — a property of the site's equipment, not of
    any airframe. It bounds how many ``cart_eligible`` planes may sit on
    carts in a single :class:`Layout` (``always_cart`` planes get their
    own carts and never draw from this pool). Defaults to ``1``, which
    reproduces the original hard-coded single-cart rule.

    ``apron_depth_m`` is the depth (in metres) of the staging apron in front
    of the door — the ``y ∈ [−apron_depth_m, 0)`` region from which the tow
    planner slides each plane in through the door (ADR-0021). A property of
    the *site*, like ``max_carts``. Defaults to ``0`` (absent ⇒ ``0``), which
    reproduces the no-apron model byte-for-byte: the planner only uses it when
    it is positive. The loader resolves the opt-in ``auto`` keyword to a
    fleet-derived depth before construction, so this field is always a plain
    resolved float.

    ``structural_notches`` is an optional tuple of always-on rectangular
    keep-outs cut from the floor (ADR-0018) — e.g. the real Herrenteich
    hangar's back-right office annex, which makes the building L-shaped. When
    present, they are subtracted from the derived :attr:`floor_polygon`, which
    the collision checker uses for containment. Defaults to empty, in which
    case ``floor_polygon`` is ``None`` and the checker keeps its fast,
    byte-identical per-vertex rectangle test — so a hangar with no notch
    behaves exactly as before. Each notch is validated to fit inside the
    rectangle and to leave some floor; coherence *between* keep-outs (a notch
    overlapping the door opening, the maintenance bay, or another notch) is the
    author's responsibility and is **not** validated — overlaps are geometrically
    harmless (the floor is a set difference) but may indicate a data error.
    """

    length_m: float
    width_m: float
    door: Door
    maintenance_bay: MaintenanceBay
    clearance_m: float
    wing_layer_clearance_m: float
    max_carts: int = 1
    apron_depth_m: float = 0.0
    structural_notches: tuple[StructuralNotch, ...] = ()
    # #643: a SEPARATE, tighter clearance applied only during tow MOTION — a
    # mover threading PAST a parked body is hand-cleared far closer than the
    # parked spacing (a spotter watches the wingtips), so the parked
    # ``clearance_m`` over-constrains the maneuver. ``None`` (the default) ⇒ the
    # motion clearance IS the parked clearance, so a hangar without these fields
    # plans byte-identically to today (ADR-0003). Consumed via ``motion_hangar``.
    motion_clearance_m: float | None = None
    motion_wing_layer_clearance_m: float | None = None
    # Derived L-shaped floor outline (outer rectangle minus the notches),
    # computed once in __post_init__ and cached here. ``None`` when there are
    # no notches (the common case) so the checker stays on its rectangle path.
    # Excluded from equality/hash/repr (a Shapely geometry is not hashable and
    # is fully determined by the other fields).
    _floor_polygon: BaseGeometry | None = field(init=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.length_m <= 0:
            raise ValueError(f"Hangar.length_m must be positive, got {self.length_m}")
        if self.width_m <= 0:
            raise ValueError(f"Hangar.width_m must be positive, got {self.width_m}")
        if self.clearance_m < 0:
            raise ValueError(f"Hangar.clearance_m must be non-negative, got {self.clearance_m}")
        if self.wing_layer_clearance_m < 0:
            raise ValueError(
                f"Hangar.wing_layer_clearance_m must be non-negative, "
                f"got {self.wing_layer_clearance_m}"
            )
        if self.max_carts < 0:
            raise ValueError(f"Hangar.max_carts must be non-negative, got {self.max_carts}")
        if self.apron_depth_m < 0:
            raise ValueError(f"Hangar.apron_depth_m must be non-negative, got {self.apron_depth_m}")
        if self.motion_clearance_m is not None and self.motion_clearance_m < 0:
            raise ValueError(
                f"Hangar.motion_clearance_m must be non-negative, got {self.motion_clearance_m}"
            )
        if (
            self.motion_wing_layer_clearance_m is not None
            and self.motion_wing_layer_clearance_m < 0
        ):
            raise ValueError(
                f"Hangar.motion_wing_layer_clearance_m must be non-negative, "
                f"got {self.motion_wing_layer_clearance_m}"
            )
        door_left = self.door.center_x_m - self.door.width_m / 2
        door_right = self.door.center_x_m + self.door.width_m / 2
        if door_left < 0 or door_right > self.width_m:
            raise ValueError(
                f"Door (center={self.door.center_x_m}, width={self.door.width_m}) "
                f"doesn't fit in hangar width {self.width_m}"
            )
        bay_left = self.maintenance_bay.center_x_m - self.maintenance_bay.width_m / 2
        bay_right = self.maintenance_bay.center_x_m + self.maintenance_bay.width_m / 2
        if bay_left < 0 or bay_right > self.width_m:
            raise ValueError(
                f"MaintenanceBay (center={self.maintenance_bay.center_x_m}, "
                f"width={self.maintenance_bay.width_m}) "
                f"doesn't fit in hangar width {self.width_m}"
            )
        if self.maintenance_bay.depth_m >= self.length_m:
            raise ValueError(
                f"MaintenanceBay.depth_m={self.maintenance_bay.depth_m} "
                f"must be strictly less than Hangar.length_m={self.length_m} "
                f"(otherwise no non-bay parking area remains)"
            )
        for notch in self.structural_notches:
            if notch.x_max_m > self.width_m or notch.y_max_m > self.length_m:
                raise ValueError(
                    f"StructuralNotch (x ∈ [{notch.x_min_m:g}, {notch.x_max_m:g}], "
                    f"y ∈ [{notch.y_min_m:g}, {notch.y_max_m:g}]) doesn't fit in hangar "
                    f"{self.width_m:g} x {self.length_m:g}"
                )
        # Derive the floor outline once. Only build the (more expensive) polygon
        # when there is something to subtract; otherwise leave it ``None`` and
        # let the checker stay on the fast rectangle path.
        floor: BaseGeometry | None = None
        if self.structural_notches:
            floor = box(0.0, 0.0, self.width_m, self.length_m).difference(
                union_all(
                    [
                        box(n.x_min_m, n.y_min_m, n.x_max_m, n.y_max_m)
                        for n in self.structural_notches
                    ]
                )
            )
            # A notch (or union) covering the whole floor would otherwise leave an
            # empty polygon that ``covers`` rejects for *every* part — a baffling
            # "all planes out of bounds" instead of a clear construction error.
            if floor.is_empty:
                raise ValueError(
                    "structural_notches leave no usable hangar floor "
                    f"(outer {self.width_m:g} x {self.length_m:g} fully covered)"
                )
        object.__setattr__(self, "_floor_polygon", floor)

    @property
    def floor_polygon(self) -> BaseGeometry | None:
        """The hangar floor as a Shapely polygon (outer rectangle minus any
        :class:`StructuralNotch`), or ``None`` when there are no notches.

        ``None`` is the signal to the collision checker that the floor is a
        plain rectangle and the fast per-vertex bounds test applies; a non-None
        value is the L-shaped outline against which parts are tested with
        ``covers`` (ADR-0018)."""
        return self._floor_polygon

    def motion_hangar(self) -> Hangar:
        """The hangar as seen by the tow-MOTION collision checks (#643).

        Returns ``self`` when no motion clearance is set, so the plan is
        byte-identical to a hangar without these fields (ADR-0003). Otherwise
        returns a plain parked-style hangar whose ``clearance_m`` /
        ``wing_layer_clearance_m`` ARE the (tighter) motion values — so the
        per-sampled-pose ``collisions.check`` the tow planner runs applies the
        motion margin, while the static parked check keeps the original
        spacing. The motion fields are folded into the clearances and cleared on
        the returned hangar (it is itself a parked-style hangar)."""
        if self.motion_clearance_m is None and self.motion_wing_layer_clearance_m is None:
            return self
        return replace(
            self,
            clearance_m=(
                self.clearance_m if self.motion_clearance_m is None else self.motion_clearance_m
            ),
            wing_layer_clearance_m=(
                self.wing_layer_clearance_m
                if self.motion_wing_layer_clearance_m is None
                else self.motion_wing_layer_clearance_m
            ),
            motion_clearance_m=None,
            motion_wing_layer_clearance_m=None,
        )


@dataclass(frozen=True, slots=True)
class Placement:
    """A single aircraft's position and orientation in the hangar."""

    plane_id: str
    x_m: float
    y_m: float
    heading_deg: float
    on_carts: bool

    def __post_init__(self) -> None:
        if not self.plane_id:
            raise ValueError("Placement.plane_id must be non-empty")


# ── Proxy-aware pickling for frozen slots dataclasses (#545/#544) ─────────
#
# Scenario and Layout both wrap mapping field(s) in MappingProxyType for
# immutability, but a mappingproxy is not picklable
# (``TypeError: cannot pickle 'mappingproxy' object``). Both must cross the
# ProcessPool boundary for #544's parallel restarts — Scenario as the worker
# input, Layout (inside the returned candidates) as the result. These two
# helpers are the single implementation both types share, so the wrap list and
# the unwrap list cannot drift between them. Each type names its proxy fields
# in a ``_PROXY_FIELDS`` ClassVar (also the source of truth for the
# construction-time wrap in __post_init__).
#
# We iterate over ``__slots__`` (``slots=True`` ⇒ no ``__dict__``) rather than
# hand-listing fields, so a future field round-trips transparently instead of
# being silently dropped on the wire — and if that future field is itself
# unpicklable it fails *loudly* here rather than corrupting a worker.
#
# Security: this is an in-process trust boundary — it only round-trips objects
# we built ourselves across our own pool, never deserializing external/
# untrusted data, so the usual pickle-RCE caveat does not apply.


class _ProxyPicklable(typing.Protocol):
    """A frozen slots dataclass (Scenario, Layout) that wraps the mapping fields
    named in ``_PROXY_FIELDS`` in MappingProxyType. Typing the shared helpers
    against this expresses their real precondition — and makes ``_PROXY_FIELDS``
    the single source the helpers read, so it can't be passed inconsistently."""

    __slots__: tuple[str, ...]
    _PROXY_FIELDS: typing.ClassVar[tuple[str, ...]]


def _proxy_aware_getstate(obj: _ProxyPicklable) -> dict[str, typing.Any]:
    """``__getstate__`` for a frozen slots dataclass with MappingProxyType
    fields: capture every slot, then unwrap the proxy fields to plain dicts."""
    state: dict[str, typing.Any] = {name: getattr(obj, name) for name in obj.__slots__}
    for name in obj._PROXY_FIELDS:
        state[name] = dict(state[name])  # unwrap mappingproxy → plain dict
    return state


def _proxy_aware_setstate(obj: _ProxyPicklable, state: dict[str, typing.Any]) -> None:
    """``__setstate__`` counterpart: restore every slot, re-wrapping the proxy
    fields so the immutability contract survives the round-trip."""
    for name, value in state.items():
        if name in obj._PROXY_FIELDS:
            value = MappingProxyType(dict(value))  # re-wrap on the far side
        object.__setattr__(obj, name, value)


@dataclass(frozen=True, slots=True)
class Layout:
    """A complete candidate layout: hangar + fleet + placements.

    Validates **cross-reference invariants** between placements and the
    fleet:

    - every placement's ``plane_id`` exists in ``fleet``,
    - the fleet dict's keys equal their ``Aircraft.id`` (no key/id drift),
    - no duplicate placements,
    - the cart rule (at most ``hangar.max_carts`` ``cart_eligible`` planes
      on carts; ``always_cart`` planes are exempt from this pool),
    - ``always_cart`` ↔ ``on_carts=True`` consistency,
    - ``always_own_gear`` ↔ ``on_carts=False`` consistency,
    - the maintenance plane (if set) is in the fleet **and is NOT in
      placements** (the occupant is treated as "away" — physically
      absent from the parking problem).

    Ground objects (#601 / ADR-0025) live in two parallel fields that
    mirror ``fleet`` / ``placements``:

    - ``ground_objects`` — a map of :class:`GroundObject` keyed by id (keys
      equal their ``GroundObject.id``, like ``fleet``),
    - ``ground_object_placements`` — the placed poses (``plane_id`` carries
      the ground-object id, no duplicates).

    Their cross-reference invariants are validated alongside the fleet's:
    every ground-object placement resolves to a known object, and the
    ground-object ids are **disjoint** from the fleet aircraft ids so a
    placement id resolves unambiguously to exactly one object.

    The bay-closure rule (no other plane's parts may cross into the
    closed bay rectangle) is a geometric check; it lives in the
    collision checker alongside the other geometric rules.

    On construction, ``fleet`` and ``ground_objects`` are wrapped in
    ``MappingProxyType`` so that the cross-reference invariants stay valid
    for the lifetime of the ``Layout`` (a plain ``dict`` field, even on a
    frozen dataclass, can be mutated through ``layout.fleet["x"] = …``).
    """

    fleet: Mapping[str, Aircraft]
    hangar: Hangar
    placements: tuple[Placement, ...]
    maintenance_plane: str | None = None
    ground_objects: Mapping[str, GroundObject] = field(default_factory=dict)
    ground_object_placements: tuple[Placement, ...] = ()

    # The mapping fields wrapped in MappingProxyType for immutability — single
    # source of truth for the construction-time wrap (__post_init__) and the
    # pickle unwrap/re-wrap (__getstate__/__setstate__, #544 ProcessPool
    # boundary). See _proxy_aware_getstate. (ClassVar ⇒ not a dataclass field.)
    _PROXY_FIELDS: typing.ClassVar[tuple[str, ...]] = ("fleet", "ground_objects")

    def __post_init__(self) -> None:
        for k, a in self.fleet.items():
            if a.id != k:
                raise ValueError(
                    f"fleet key {k!r} does not match its Aircraft.id "
                    f"({a.id!r}); fleet keys must equal their aircraft id"
                )

        seen: set[str] = set()
        for p in self.placements:
            if p.plane_id not in self.fleet:
                raise ValueError(
                    f"Placement references unknown plane_id {p.plane_id!r} "
                    f"(fleet has: {sorted(self.fleet)})"
                )
            if p.plane_id in seen:
                raise ValueError(f"Duplicate placement for plane_id {p.plane_id!r}")
            seen.add(p.plane_id)

            plane = self.fleet[p.plane_id]
            if plane.movement_mode == "always_cart" and not p.on_carts:
                raise ValueError(
                    f"Placement for {p.plane_id!r}: must have on_carts=True "
                    f"(movement_mode=always_cart)"
                )
            if plane.movement_mode == "always_own_gear" and p.on_carts:
                raise ValueError(
                    f"Placement for {p.plane_id!r}: must have on_carts=False "
                    f"(movement_mode=always_own_gear)"
                )

        cart_count = sum(
            1
            for p in self.placements
            if p.on_carts and self.fleet[p.plane_id].movement_mode == "cart_eligible"
        )
        if cart_count > self.hangar.max_carts:
            raise ValueError(
                f"At most {self.hangar.max_carts} cart_eligible plane(s) may have "
                f"on_carts=True (got {cart_count}); the cart inventory is set by "
                f"hangar.max_carts"
            )

        if self.maintenance_plane is not None:
            if self.maintenance_plane not in self.fleet:
                raise ValueError(f"maintenance_plane {self.maintenance_plane!r} not in fleet")
            if self.maintenance_plane in seen:
                raise ValueError(
                    f"maintenance_plane {self.maintenance_plane!r} must NOT be in "
                    f"placements when in maintenance (occupant is treated as away)"
                )

        # Ground objects (#601): keys equal their id; placements resolve;
        # ids disjoint from the fleet so a placement id resolves unambiguously.
        for k, obj in self.ground_objects.items():
            if obj.id != k:
                raise ValueError(
                    f"ground_objects key {k!r} does not match its GroundObject.id "
                    f"({obj.id!r}); keys must equal their ground-object id"
                )
        fleet_ids = set(self.fleet)
        ground_ids = set(self.ground_objects)
        clash = fleet_ids & ground_ids
        if clash:
            raise ValueError(
                f"ground-object id(s) {sorted(clash)} collide with fleet aircraft ids; "
                f"ids must be disjoint so a placement resolves to exactly one object"
            )
        seen_ground: set[str] = set()
        for gp in self.ground_object_placements:
            if gp.plane_id not in self.ground_objects:
                raise ValueError(
                    f"ground_object_placement references unknown id {gp.plane_id!r} "
                    f"(ground_objects has: {sorted(self.ground_objects)})"
                )
            if gp.plane_id in seen_ground:
                raise ValueError(f"Duplicate ground_object_placement for id {gp.plane_id!r}")
            seen_ground.add(gp.plane_id)

        for name in self._PROXY_FIELDS:
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))

    # Picklable across the #544 ProcessPool boundary — Layout rides back inside
    # the returned candidates. See _proxy_aware_getstate for the rationale.
    def __getstate__(self) -> dict[str, typing.Any]:
        return _proxy_aware_getstate(self)

    def __setstate__(self, state: dict[str, typing.Any]) -> None:
        _proxy_aware_setstate(self, state)


@dataclass(frozen=True, slots=True)
class Conflict:
    """One reason a layout is invalid.

    ``planes`` carries 1 or 2 *distinct, non-empty* aircraft IDs
    depending on the rule that fired (single-plane rules like
    ``hangar_bounds`` or ``bay_intrusion`` cite one plane; pairwise
    rules like ``wing_strut_overlap`` cite two). Use
    ``Conflict.single()`` / ``Conflict.pair()`` at call sites to make
    the arity explicit.
    """

    kind: str
    planes: tuple[str, ...]
    detail: str

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValueError("Conflict.kind must be non-empty")
        if not self.planes:
            raise ValueError("Conflict.planes must have at least one plane id")
        if len(self.planes) > 2:
            raise ValueError(f"Conflict.planes must have 1 or 2 entries, got {len(self.planes)}")
        if any(not pid for pid in self.planes):
            raise ValueError(f"Conflict.planes entries must be non-empty, got {self.planes}")
        if len(set(self.planes)) != len(self.planes):
            raise ValueError(f"Conflict.planes entries must be distinct, got {self.planes}")

    @classmethod
    def single(cls, kind: str, plane: str, detail: str) -> Conflict:
        """Factory for a single-aircraft conflict (e.g. ``hangar_bounds`` or ``bay_intrusion``)."""
        return cls(kind=kind, planes=(plane,), detail=detail)

    @classmethod
    def pair(cls, kind: str, plane_a: str, plane_b: str, detail: str) -> Conflict:
        """Factory for a pairwise conflict (e.g. ``wing_strut_overlap``)."""
        return cls(kind=kind, planes=(plane_a, plane_b), detail=detail)


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Result of running the collision checker against a Layout.

    ``valid`` is a derived property — there is no way to construct a
    ``CheckResult`` that claims to be valid while carrying conflicts.

    ``total_penetration_m2`` is the summed shapely-``intersection().area``
    across pairwise conflicts (length-2 ``Conflict.planes``) — used by
    the Phase 2a solver as a smooth secondary scoring key to break
    plateaus in the integer ``len(conflicts)`` metric. Single-plane
    conflicts (``hangar_bounds``, ``bay_intrusion``) contribute 0. The
    validity contract is unchanged: ``valid`` is still derived from
    ``conflicts`` only.
    """

    conflicts: tuple[Conflict, ...] = ()
    total_penetration_m2: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.total_penetration_m2):
            raise ValueError(
                f"total_penetration_m2 must be finite, got {self.total_penetration_m2!r}"
            )
        if self.total_penetration_m2 < 0.0:
            raise ValueError(
                f"total_penetration_m2 must be >= 0.0, got {self.total_penetration_m2}"
            )

    @property
    def valid(self) -> bool:
        return len(self.conflicts) == 0


RegionSide = Literal["left", "right"]
_VALID_REGION_SIDES: frozenset[str] = frozenset(("left", "right"))


@dataclass(frozen=True, slots=True)
class RegionPreference:
    """A soft per-object preference to align a placed body to one hangar wall (#604).

    ``side`` is the preferred wall in the x-axis (``"left"`` ≡ ``x → 0``,
    ``"right"`` ≡ ``x → hangar.width_m``); ``weight`` is a non-negative, finite
    soft importance (``0.0`` is permitted and inert). Realized as the RNG-free
    ``solver._region_energy`` term folded into the ``_spread`` hill-climb,
    secondary to ``min_pairwise_gap_m`` and never overriding the hard validity
    gate (ADR-0008 amended). Modeled on :attr:`PlaneConstraint.priority`'s
    soft-weight validation (#441).

    The shape differs intentionally from that precedent: ``weight`` is a plain
    ``float`` (not ``float | None``) because a body either has a
    ``Scenario.region_preferences`` map entry or none — map-presence already
    encodes the optional/neutral distinction that ``priority``'s ``| None``
    carries inline.
    """

    side: RegionSide
    weight: float

    def __post_init__(self) -> None:
        if self.side not in _VALID_REGION_SIDES:
            raise ValueError(
                f"RegionPreference.side must be one of {sorted(_VALID_REGION_SIDES)}, "
                f"got {self.side!r}"
            )
        if not math.isfinite(self.weight):
            raise ValueError(f"RegionPreference.weight={self.weight!r} must be finite")
        if self.weight < 0.0:
            raise ValueError(
                f"RegionPreference.weight={self.weight!r} must be >= 0.0 (a soft weight)"
            )


@dataclass(frozen=True, slots=True)
class PlaneConstraint:
    """Per-plane constraints for a Scenario.

    ``pin`` and ``force_on_carts`` are HARD constraints; ``priority`` is a SOFT
    preference. All optional — a constraint with everything None means 'free'
    (the solver may place the plane anywhere within physical / cart-rule
    limits). See spec §3.2 for the rationale.

    ``priority`` (#441) is a non-negative soft importance weight (``None`` ≡ the
    neutral ``0.0``): the spread post-pass weights each plane-pair's repulsion
    energy by ``(1 + priority_i)·(1 + priority_j)``, so a higher-priority plane
    is pushed to more clearance. With every ``priority`` unset the weights are
    all ``1.0`` and the search is byte-identical to the pre-#441 behaviour
    (ADR-0003). It never overrides a hard ``pin``. Groundwork for the future
    interactive editor (#442), which exports per-plane priorities.

    It is ``float | None`` (not ``float = 0.0`` like ``SearchConfig.back_bias_weight``)
    to stay consistent with its optional siblings here (``pin``/``force_on_carts``,
    where ``None`` means 'free') and to let #442 distinguish 'user never set a
    priority' from an explicit ``0.0`` on round-trip; both collapse to weight
    ``1.0`` in the solver today.

    ``nose_out`` (#263 / ADR-0022) is the per-plane override of the global
    :attr:`SearchConfig.nose_out` preference. **Its ``None`` semantics differ from
    its optional siblings:** ``pin``/``force_on_carts`` ``None`` means 'free/unset',
    but ``nose_out`` ``None`` means 'follow the global ``SearchConfig.nose_out``'.
    ``True`` ⇒ always prefer nose-out for this plane; ``False`` ⇒ never flip it —
    the legitimate nose-IN exemption (e.g. a low-wing tucked under a high-wing's
    tail). Soft: it only re-orients, never overrides validity or a hard ``pin``.
    """

    pin: Placement | None = None
    force_on_carts: bool | None = None
    priority: float | None = None
    nose_out: bool | None = None


@dataclass(frozen=True, slots=True)
class Scenario:
    """Solver input for Phase 2a.

    Cross-reference invariants validated in __post_init__:

    - fleet_in is non-empty
    - fleet_in has no duplicate entries
    - every fleet_in id exists in fleet
    - maintenance_plane (if set) is in fleet_in
    - constraints.keys() ⊆ set(fleet_in)
    - for each (plane_id, constraint): constraint.pin.plane_id == plane_id (if pin set)
    - force_on_carts is consistent with movement_mode:
        force_on_carts=True  → plane must NOT be always_own_gear
        force_on_carts=False → plane must NOT be always_cart
    - pin.on_carts is consistent with movement_mode (same rules)
    - if both a pin and force_on_carts are set, their on_carts must agree
    - maintenance_plane (if set) must not also carry a pin or force_on_carts in
      constraints (the occupant is treated as away — those constraints would be
      incoherent and would be silently ignored by the solver)
    - region_preferences.keys() ⊆ placeable bodies (fleet_in ∪ placed_routed_mover ids)
    - fixed_obstacle_placements entries reference distinct fixed_obstacle ground
      objects (in ground_object_defs)
    - fleet and constraints are wrapped in MappingProxyType (same pattern as Layout)

    A fixed_obstacle may appear in ``ground_objects`` WITHOUT a matching
    ``fixed_obstacle_placements`` entry — its pose then comes from a hand-authored
    layout (the ``check``/``view`` path), not the solver. The scenario LOADER
    requires a pose for any fixed_obstacle authored in a SOLVE scenario's
    ``ground_objects:`` block (#604), so the silent-keep-out-drop that a hard
    coverage invariant would guard against cannot arise via authored scenarios;
    for programmatic construction it remains the caller's responsibility
    (consistent with the #605 ground_objects model). Hence this is a documented
    contract, not an enforced __post_init__ invariant.

    See spec §3.2 for the rationale.
    """

    fleet: Mapping[str, Aircraft]
    hangar: Hangar
    fleet_in: tuple[str, ...]
    maintenance_plane: str | None = None
    constraints: Mapping[str, PlaneConstraint] = field(default_factory=lambda: MappingProxyType({}))
    ground_objects: tuple[str, ...] = ()
    ground_object_defs: Mapping[str, GroundObject] = field(
        default_factory=lambda: MappingProxyType({})
    )
    fixed_obstacle_placements: tuple[Placement, ...] = ()
    region_preferences: Mapping[str, RegionPreference] = field(
        default_factory=lambda: MappingProxyType({})
    )

    # The mapping fields wrapped in MappingProxyType for immutability. This is
    # the single source of truth for both the construction-time wrap (in
    # __post_init__) and the pickle unwrap/re-wrap (in __getstate__/__setstate__,
    # #545) — listing them once means the two cannot silently drift. (ClassVar so
    # the dataclass excludes it from the field set and __slots__.)
    _PROXY_FIELDS: typing.ClassVar[tuple[str, ...]] = (
        "fleet",
        "constraints",
        "ground_object_defs",
        "region_preferences",
    )

    def __post_init__(self) -> None:
        # fleet_in must be non-empty (otherwise there's nothing to solve;
        # downstream helpers like the sum-of-areas infeasibility check
        # also do `fleet_in[0]` which would IndexError on empty input).
        if not self.fleet_in:
            raise ValueError("Scenario.fleet_in must be non-empty")

        # fleet_in has no duplicates (one Husky can't park in two places).
        # Mirror of the placements seen-set check in Layout.__post_init__.
        if len(set(self.fleet_in)) != len(self.fleet_in):
            raise ValueError(f"Scenario.fleet_in has duplicate entries: {self.fleet_in}")

        # fleet_in references real planes
        for pid in self.fleet_in:
            if pid not in self.fleet:
                raise ValueError(
                    f"Scenario.fleet_in references unknown plane {pid!r}; "
                    f"fleet has: {sorted(self.fleet)}"
                )

        fleet_in_set = set(self.fleet_in)

        # maintenance_plane in fleet_in
        if self.maintenance_plane is not None and self.maintenance_plane not in fleet_in_set:
            raise ValueError(
                f"Scenario.maintenance_plane {self.maintenance_plane!r} must be in fleet_in"
            )

        # constraint keys ⊆ fleet_in
        for key in self.constraints:
            if key not in fleet_in_set:
                raise ValueError(f"Scenario.constraints has key {key!r} not in fleet_in")

        # maintenance_plane cannot carry a pin or force_on_carts constraint.
        # The maintenance occupant is treated as away (absent from placements),
        # so any pin or force_on_carts on it would be silently ignored by the
        # solver.  Reject early so the user gets a clear error rather than a
        # layout that ignored their constraint.
        if (
            self.maintenance_plane is not None
            and self.maintenance_plane in self.constraints
            and (
                self.constraints[self.maintenance_plane].pin is not None
                or self.constraints[self.maintenance_plane].force_on_carts is not None
            )
        ):
            raise ValueError(
                f"Scenario.maintenance_plane {self.maintenance_plane!r} cannot also "
                f"carry a pin or force_on_carts constraint — the maintenance occupant "
                f"is treated as away (absent from placements)."
            )

        # per-constraint validation
        for plane_id, constraint in self.constraints.items():
            plane = self.fleet[plane_id]

            if constraint.pin is not None:
                if constraint.pin.plane_id != plane_id:
                    raise ValueError(
                        f"Scenario.constraints[{plane_id!r}].pin.plane_id is "
                        f"{constraint.pin.plane_id!r}; must equal the constraint key"
                    )

                # pin.on_carts consistency with movement_mode
                if plane.movement_mode == "always_cart" and not constraint.pin.on_carts:
                    raise ValueError(
                        f"Scenario.constraints[{plane_id!r}].pin.on_carts=False "
                        f"contradicts movement_mode={plane.movement_mode!r}"
                    )
                if plane.movement_mode == "always_own_gear" and constraint.pin.on_carts:
                    raise ValueError(
                        f"Scenario.constraints[{plane_id!r}].pin.on_carts=True "
                        f"contradicts movement_mode={plane.movement_mode!r}"
                    )

            if constraint.force_on_carts is not None:
                # force_on_carts consistency with movement_mode
                if constraint.force_on_carts is True and plane.movement_mode == "always_own_gear":
                    raise ValueError(
                        f"Scenario.constraints[{plane_id!r}].force_on_carts=True "
                        f"contradicts movement_mode={plane.movement_mode!r}"
                    )
                if constraint.force_on_carts is False and plane.movement_mode == "always_cart":
                    raise ValueError(
                        f"Scenario.constraints[{plane_id!r}].force_on_carts=False "
                        f"contradicts movement_mode={plane.movement_mode!r}"
                    )

            # pin and force_on_carts must agree if both set
            if (
                constraint.pin is not None
                and constraint.force_on_carts is not None
                and constraint.pin.on_carts != constraint.force_on_carts
            ):
                raise ValueError(
                    f"Scenario.constraints[{plane_id!r}]: pin.on_carts="
                    f"{constraint.pin.on_carts} and force_on_carts="
                    f"{constraint.force_on_carts} disagree (contradictory)"
                )

            # priority (#441): a non-negative, finite soft weight. A negative
            # weight would invert the spread repulsion (nonsensical), and a
            # non-finite one would poison the energy sum.
            if constraint.priority is not None:
                if not math.isfinite(constraint.priority):
                    raise ValueError(
                        f"Scenario.constraints[{plane_id!r}].priority="
                        f"{constraint.priority!r} must be finite"
                    )
                if constraint.priority < 0.0:
                    raise ValueError(
                        f"Scenario.constraints[{plane_id!r}].priority="
                        f"{constraint.priority!r} must be >= 0.0 (a soft importance weight)"
                    )

        # Ground objects (#601): defs keys must equal their GroundObject.id;
        # every id in ground_objects must resolve in ground_object_defs.
        for k, obj in self.ground_object_defs.items():
            if obj.id != k:
                raise ValueError(
                    f"Scenario.ground_object_defs key {k!r} != GroundObject.id ({obj.id!r})"
                )
        for gid in self.ground_objects:
            if gid not in self.ground_object_defs:
                raise ValueError(
                    f"Scenario.ground_objects references unknown ground_object {gid!r}; "
                    f"defs have: {sorted(self.ground_object_defs)}"
                )

        # Region preferences (#604): every key must reference a placeable body —
        # an aircraft in fleet_in or a placed_routed_mover ground object.
        placeable = set(self.fleet_in) | {
            gid
            for gid in self.ground_objects
            if self.ground_object_defs[gid].object_class == "placed_routed_mover"
        }
        for rid in self.region_preferences:
            if rid not in placeable:
                raise ValueError(
                    f"Scenario.region_preferences references {rid!r} which is not a "
                    f"placeable body (aircraft or placed_routed_mover); "
                    f"placeable ids: {sorted(placeable)}"
                )
        seen_fixed: set[str] = set()
        for p in self.fixed_obstacle_placements:
            if p.plane_id not in self.ground_object_defs:
                raise ValueError(
                    f"Scenario.fixed_obstacle_placements references unknown ground "
                    f"object {p.plane_id!r}"
                )
            if self.ground_object_defs[p.plane_id].object_class != "fixed_obstacle":
                raise ValueError(
                    f"Scenario.fixed_obstacle_placements[{p.plane_id!r}] is not a fixed_obstacle"
                )
            if p.plane_id in seen_fixed:
                raise ValueError(f"Duplicate fixed_obstacle_placement for {p.plane_id!r}")
            seen_fixed.add(p.plane_id)

        # Wrap the mapping fields in MappingProxyType so a frozen Scenario can't
        # be mutated through e.g. ``scenario.fleet["x"] = …``. Always copy+wrap
        # (even an already-wrapped MappingProxyType arg): skipping the copy would
        # let a caller leak mutations through their retained reference to the
        # underlying dict. Driven off _PROXY_FIELDS so this wrap list and the
        # pickle unwrap list stay a single source of truth.
        for name in self._PROXY_FIELDS:
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))

    @property
    def mover_ids(self) -> tuple[str, ...]:
        """Placed-routed-mover ids active in this scenario, in ``ground_objects`` order.

        These are the ground objects the solver PLACES + routes (vs fixed
        obstacles, authored static keep-outs in :attr:`fixed_obstacle_placements`).
        Empty ⇒ the solver is aircraft-only and byte-identical to pre-#604 (ADR-0003).

        Note this exposes a DIFFERENT mover ordering than
        :attr:`placeable_ids`: ``mover_ids`` follows ``ground_objects``
        (declaration) order, whereas ``placeable_ids`` sorts the movers. Callers
        must not index-align one against the other (the solver re-sorts via
        ``sorted(scenario.mover_ids)`` at the use site)."""
        return tuple(
            gid
            for gid in self.ground_objects
            if self.ground_object_defs[gid].object_class == "placed_routed_mover"
        )

    @property
    def placeable_ids(self) -> tuple[str, ...]:
        """Aircraft (``fleet_in``) then sorted mover ids — the unified search bodies.
        With no movers this is exactly ``fleet_in`` (ADR-0003).

        The mover ordering here (``fleet_in + sorted(mover_ids)``) differs from
        :attr:`mover_ids`' ``ground_objects`` (declaration) order, so callers
        must not index-align the two."""
        return self.fleet_in + tuple(sorted(self.mover_ids))

    # Picklable across the #544 ProcessPool boundary — the worker input. See
    # _proxy_aware_getstate for the rationale (shared with Layout, #545/#544).
    def __getstate__(self) -> dict[str, typing.Any]:
        return _proxy_aware_getstate(self)

    def __setstate__(self, state: dict[str, typing.Any]) -> None:
        _proxy_aware_setstate(self, state)


@dataclass(frozen=True, slots=True)
class ApronShallowDrop:
    """One plane that tows via the ``y = 0`` door line despite an apron being set,
    because the apron is too shallow for its footprint (#503 / ADR-0021).

    Plan-inert diagnostics produced by :func:`hangarfit.towplanner.plan_fill`:
    when ``hangar.apron_depth_m > 0`` but every apron start pose was filtered for
    a plane (its fore-aft footprint overflows the apron south bound), the plane
    falls back to the door-line start and shows no slide-in — silent in the
    :class:`~hangarfit.towplanner.MovesPlan`. This records that drop so the CLI
    can warn at the boundary.

    ``min_depth_m`` is the plane's fore-aft footprint extent — a *conservative
    (sufficient) upper bound* on the apron depth needed to engage it, NOT the
    exact minimum: the true per-plane engagement gate is ≈ ``2·min(fore, aft)``
    of the footprint about its reference, so a deeper-than-necessary suggestion
    is always safe. RNG-free, so it does not affect determinism (ADR-0003)."""

    plane_id: str
    min_depth_m: float

    def __post_init__(self) -> None:
        if not self.plane_id:
            raise ValueError("ApronShallowDrop.plane_id must be non-empty")
        if not math.isfinite(self.min_depth_m) or self.min_depth_m < 0.0:
            raise ValueError(
                f"ApronShallowDrop.min_depth_m must be finite and >= 0, got {self.min_depth_m!r}"
            )


SolveStatus = Literal[
    "found",
    "found_partial",
    "exhausted_budget",
    "trivially_infeasible",
]


class RegionAlignment(typing.NamedTuple):
    """A placed body's achieved wall alignment for a returned layout (#604).

    ``alignment`` is 0–1 with 1.0 meaning the body sits exactly at its preferred
    wall. A ``tuple`` subclass, so it is byte-identical under pickle/equality/repr
    (determinism-neutral) and ``dict(layout_alignments)`` still yields
    ``{body_id: alignment}``."""

    body_id: str
    alignment: float


@dataclass(frozen=True, slots=True)
class SolverDiagnostics:
    """Per-solve diagnostic information.

    ``best_partial`` and ``best_partial_layout`` are a fused pair — the
    lowest-conflict :class:`CheckResult` seen and the matching
    :class:`Layout` so it can be rendered. They MUST both be set or both
    be ``None``; carrying one without the other is a self-inconsistent
    state that would crash downstream rendering (Chunk F's CLI).

    ``seed`` is the *actually-used* seed — ``None`` resolved to entropy
    on entry to :func:`hangarfit.solver.solve` is recorded here so a run
    can be replayed exactly.

    ``diversity_impossible`` is ``True`` iff the static
    ``K > 1 ∧ free_planes < min_planes_moved`` precondition fires on
    :func:`hangarfit.solver.solve` entry (spec §4.1 of the v0.6.0
    solver-polish release design). It mirrors the existing logger
    warning as a structured, machine-readable signal so callers don't
    have to scrape log records.

    ``diversity_rejected_count`` is the number of pool candidates the
    diversity gate turned away during best-of-all selection (#267) —
    examined in best-spread order until ``alternatives`` were chosen, so
    candidates beyond the quota are never examined or counted. ``0`` is
    the healthy default and is always the value for ``alternatives == 1``
    (selection stops after the first, vacuous pick). When ``K>1`` returns
    ``found_partial`` a non-zero value shows the diversity gate turned
    away otherwise-valid basins.

    ``diversity_impossible`` and ``diversity_rejected_count`` are
    **advisory**: structured mirrors of log warnings / search
    instrumentation. Callers must not gate on them for status-level
    decisions; that's ``status``'s job. They exist so dashboards and
    tests can read the same signals as the logger without scraping log
    records.

    ``unroutable_planes`` is a flat, advisory list of the blocking plane
    ids for the layouts the tow planner could not route, in
    returned-layout order. There is one entry per ``None`` in
    :attr:`SolveResult.plans`, but the tuple is **compacted** (only the
    failing layouts contribute) — it is *not* positionally indexable to
    ``plans``/``layouts``; to attribute a failure to a specific layout,
    read the ``None`` positions in ``plans``. Empty when every returned
    layout was tow-planned, or when tow-planning was not attempted
    (``solve(..., plan_paths=False)``). Advisory: the v2 planner
    (Reeds–Shepp arcs + bounded Hybrid-A* — #222/#261 under ADR-0007 +
    ADR-0010) has documented false-negatives, so an un-routable layout is
    still a valid static arrangement — the entry flags a planning gap, not
    an invalid layout.

    ``unroutable_movers`` is the ground-object counterpart (#627/#612): a flat,
    deduped, advisory tuple of the **mover** ids (cars / trailers) that could not
    be routed and so kept a best-effort ``Move(path=None)`` instead of aborting
    the fill (ADR-0007 #197). Unlike ``unroutable_planes``, a None-path mover does
    **not** make the whole layout's plan ``None`` (the plan is still returned with
    the mover drawn as a static body), which is exactly why it needs its own
    advisory list rather than reusing ``unroutable_planes``. Empty when every
    mover routed, when there are no movers, or when tow-planning was not attempted.
    Advisory / RNG-free ⇒ ADR-0003-safe.

    ``min_pairwise_gap_m`` is index-aligned with :attr:`SolveResult.layouts`:
    the achieved minimum plan-view gap (m) between any two planes in that
    returned layout — the quality the best-of-all-basins spread selection
    maximizes (#267, ADR-0008). ``math.inf`` for a layout with <2 planes
    (no pairs). ``valid_basins_found`` is the number of valid spread-polished
    basins the search collected before selection — how much choice best-of-all
    had. Both are advisory.

    ``nose_out_flips`` is index-aligned with :attr:`SolveResult.layouts`: the
    number of nose-out heading flips the RNG-free ``_nose_out`` post-pass applied
    to that returned layout (#263, ADR-0022). ``0`` for a layout where no plane was
    flipped (or with ``nose_out`` disabled). Advisory / RNG-free.

    ``region_alignment`` is index-aligned with :attr:`SolveResult.layouts`: for each
    returned layout, a tuple of :class:`RegionAlignment` ``(body_id, alignment)`` pairs
    (sorted by id) for the bodies carrying a :class:`RegionPreference`, where
    ``alignment`` is 0–1 with 1.0
    meaning the body sits exactly at its preferred wall (#604, ADR-0008 amended).
    Empty when no scenario region preferences are set. Advisory / RNG-free.

    ``spread_fallback_applied`` is ``True`` when :func:`hangarfit.solver.solve`
    re-solved with the inter-plane spread post-pass disabled and substituted
    that tighter, tow-routable arrangement because the spread layout(s) came
    back valid but un-routable under ``plan_paths=True`` (the ADR-0016 / #280
    fallback, promoted into the library in #402 / F5). Always present (``False``
    in the normal no-swap case) so non-interactive consumers can rely on it.
    Advisory: the swap changes *which* valid layout is returned, never *whether*
    it is valid — it does not affect ``status``.

    ``spread_stall_applied`` is ``True`` when the spread-ON restart loop stopped
    early on pool stagnation rather than running to ``budget_s`` /
    ``search.max_restarts`` — i.e. ``search.spread_stall_restarts`` consecutive
    restarts failed to improve the selected set's maximin gap by
    ``search.spread_stall_epsilon_m`` (the opt-in F7 / #404 early-exit). Always
    ``False`` under the default ``spread_stall_restarts=None`` and on the
    ``spread=False`` fast path. Because the early-exit arms only after a complete
    selection exists, a ``True`` value always accompanies a ``found`` result.
    Advisory: it changes *when* the search stops, never *whether* the returned
    layout is valid — it does not affect ``status``.

    ``apron_shallow_drops`` is a flat, advisory tuple of :class:`ApronShallowDrop`
    for the planes that towed via the ``y = 0`` door line — showing no slide-in —
    because the site's apron (``hangar.apron_depth_m > 0``) was too shallow for
    their footprint (#503 / ADR-0021). It collects the drops of *every* returned
    layout's tow plan, in returned-layout-then-move order; a plane may appear more
    than once (once per layout it is dropped in), so the CLI dedups by plane id
    before warning. Empty when no returned layout had a too-shallow drop, or when
    tow-planning was not attempted (``solve(..., plan_paths=False)``) / no apron is
    set. Crucially it carries only the *returned* result's drops: the discarded
    spread-fallback pass (#280 / F5) never contributes, since its diagnostics are
    not the ones returned. Advisory: the drop is observational — the layout is
    still valid and the plan still routes (via the door line), so it does not
    affect ``status``. RNG-free ⇒ ADR-0003-safe.
    """

    restarts_attempted: int
    wall_time_s: float
    best_partial: CheckResult | None
    best_partial_layout: Layout | None
    seed: int
    diversity_impossible: bool = False
    diversity_rejected_count: int = 0
    unroutable_planes: tuple[str, ...] = ()
    min_pairwise_gap_m: tuple[float, ...] = ()
    valid_basins_found: int = 0
    spread_fallback_applied: bool = False
    spread_stall_applied: bool = False
    apron_shallow_drops: tuple[ApronShallowDrop, ...] = ()
    nose_out_flips: tuple[int, ...] = ()
    region_alignment: tuple[tuple[RegionAlignment, ...], ...] = ()
    unroutable_movers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (self.best_partial is None) != (self.best_partial_layout is None):
            raise ValueError(
                "SolverDiagnostics.best_partial and best_partial_layout must "
                "both be set or both be None"
            )
        if self.restarts_attempted < 0:
            raise ValueError(
                f"SolverDiagnostics.restarts_attempted must be >= 0, got {self.restarts_attempted}"
            )
        if not math.isfinite(self.wall_time_s) or self.wall_time_s < 0.0:
            raise ValueError(
                f"SolverDiagnostics.wall_time_s must be finite and >= 0, got {self.wall_time_s!r}"
            )
        if self.diversity_rejected_count < 0:
            raise ValueError(
                f"SolverDiagnostics.diversity_rejected_count must be >= 0, "
                f"got {self.diversity_rejected_count}"
            )
        if self.valid_basins_found < 0:
            raise ValueError(
                f"SolverDiagnostics.valid_basins_found must be >= 0, got {self.valid_basins_found}"
            )
        if any(math.isnan(g) or g < 0.0 for g in self.min_pairwise_gap_m):
            raise ValueError(
                "SolverDiagnostics.min_pairwise_gap_m entries must be non-negative "
                f"(math.inf allowed for <2-plane layouts), got {self.min_pairwise_gap_m!r}"
            )
        if any(n < 0 for n in self.nose_out_flips):
            raise ValueError(
                "SolverDiagnostics.nose_out_flips entries must be >= 0, "
                f"got {self.nose_out_flips!r}"
            )
        for layout_alignments in self.region_alignment:
            for ra in layout_alignments:
                if math.isnan(ra.alignment) or ra.alignment < 0.0 or ra.alignment > 1.0:
                    raise ValueError(
                        "SolverDiagnostics.region_alignment values must be in "
                        f"[0.0, 1.0], got {ra.alignment!r}"
                    )


@dataclass(frozen=True, slots=True)
class SolveResult:
    """Public output of :func:`hangarfit.solver.solve`.

    ``layouts`` is 0..K valid Layouts, with K matching the caller's
    ``alternatives`` request. The ``status`` field disambiguates partial
    runs (see spec §4.7); status and ``layouts`` emptiness must agree:

    - ``"found"`` / ``"found_partial"`` → at least one layout
    - ``"exhausted_budget"`` / ``"trivially_infeasible"`` → zero layouts

    ``plans`` is index-aligned with ``layouts``: ``plans[i]`` is the
    :class:`~hangarfit.towplanner.MovesPlan` for ``layouts[i]``, or
    ``None`` when the tow planner could not route that layout
    (best-effort enrichment — see ADR-0007 + ADR-0010). It is also
    all-``None`` when tow-planning was skipped
    (``solve(..., plan_paths=False)``). A ``None`` entry does **not**
    invalidate the corresponding layout: the static arrangement remains
    valid; only its tow plan is unavailable. For statuses with empty
    ``layouts`` the field is always ``()``.
    """

    status: SolveStatus
    layouts: tuple[Layout, ...]
    diagnostics: SolverDiagnostics
    plans: tuple[MovesPlan | None, ...] = ()

    def __post_init__(self) -> None:
        if self.status in ("found", "found_partial") and not self.layouts:
            raise ValueError(f"SolveResult.status={self.status!r} requires at least one layout")
        _empty_statuses = ("exhausted_budget", "trivially_infeasible")
        if self.status in _empty_statuses and self.layouts:
            raise ValueError(
                f"SolveResult.status={self.status!r} must have empty layouts, "
                f"got {len(self.layouts)}"
            )
        # plans is index-aligned with layouts for EVERY status: empty-layout
        # statuses therefore require empty plans too, and this subsumes the
        # found/found_partial cardinality check (layouts is already forced
        # non-empty for those by the first guard). Entries may be None
        # (best-effort: the tow planner couldn't route that layout), but the
        # length must still match.
        if len(self.plans) != len(self.layouts):
            raise ValueError(
                f"SolveResult.plans length ({len(self.plans)}) must equal "
                f"layouts length ({len(self.layouts)}) (status={self.status!r})"
            )
        if self.diagnostics.min_pairwise_gap_m and len(self.diagnostics.min_pairwise_gap_m) != len(
            self.layouts
        ):
            raise ValueError(
                "SolveResult.diagnostics.min_pairwise_gap_m, when populated, must be "
                f"index-aligned with layouts: got {len(self.diagnostics.min_pairwise_gap_m)} "
                f"gaps for {len(self.layouts)} layouts"
            )
        if self.diagnostics.nose_out_flips and len(self.diagnostics.nose_out_flips) != len(
            self.layouts
        ):
            raise ValueError(
                "SolveResult.diagnostics.nose_out_flips, when populated, must be "
                f"index-aligned with layouts: got {len(self.diagnostics.nose_out_flips)} "
                f"counts for {len(self.layouts)} layouts"
            )
        if self.diagnostics.region_alignment and len(self.diagnostics.region_alignment) != len(
            self.layouts
        ):
            raise ValueError(
                "SolverDiagnostics.region_alignment length must match layouts when populated"
            )


@dataclass(frozen=True, slots=True)
class DiversityConfig:
    """Diversity-filter thresholds (see spec §4.6)."""

    min_planes_moved: int = 2
    position_threshold_m: float = 0.5
    heading_threshold_deg: float = 30.0

    def __post_init__(self) -> None:
        if self.min_planes_moved < 1:
            raise ValueError(
                f"DiversityConfig.min_planes_moved must be >= 1 "
                f"(zero makes diversity vacuous), got {self.min_planes_moved}"
            )
        if self.position_threshold_m <= 0.0:
            raise ValueError(
                f"DiversityConfig.position_threshold_m must be positive, "
                f"got {self.position_threshold_m}"
            )
        if not (0.0 <= self.heading_threshold_deg <= 180.0):
            raise ValueError(
                f"DiversityConfig.heading_threshold_deg must be in [0, 180] "
                f"(shorter arc), got {self.heading_threshold_deg}"
            )


@dataclass(frozen=True, slots=True)
class SearchConfig:
    """Solver hyperparameters (see spec §4.3, §4.5).

    v1 defaults are guesses; tune with real data.

    ``max_restarts`` is the v0.6.0 solver-polish addition (spec §4.2 of
    ``docs/superpowers/specs/2026-05-22-v0.6.0-solver-polish-release-design.md``).
    ``None`` preserves the pre-v0.6.0 wall-clock-only termination behavior.
    When set, it acts as an *upper-bound counter* on the outer restart
    loop in addition to ``budget_s``: whichever gate trips first wins.
    Useful for cross-machine-deterministic exhaustion canaries that
    can't rely on wall-clock budget cutoffs.
    """

    candidates_per_iter: int = 8
    k_stall: int = 50
    pos_sigma_m: float = 0.5
    heading_sigma_deg: float = 10.0
    max_restarts: int | None = None
    """Hard cap on the outer restart loop. ``None`` (default) preserves
    the pre-v0.6.0 wall-clock-only termination behavior. When set, must
    be ``>= 1``; serves as an upper-bound counter in addition to
    ``solve(..., budget_s=...)`` — whichever gate trips first wins.
    Useful for cross-machine-deterministic exhaustion canaries that
    can't rely on wall-clock budget cutoffs.

    ``None`` is the only kind of **opt-out** sentinel in
    :class:`SearchConfig` / :class:`DiversityConfig` /
    :class:`SolverDiagnostics`: it means "disabled" on ``max_restarts`` and on
    ``spread_stall_restarts``. (``spread_scale_m``'s ``None`` is *not* an
    opt-out — it selects the adaptive default scale rather than disabling
    anything.) Every other numeric field requires a concrete positive value.
    Pass ``None`` explicitly to disable the cap; passing ``0`` is rejected (it
    would skip the search loop entirely)."""

    spread: bool = True
    """When True (default), ``solve()`` runs a post-pass spread phase on each
    valid layout that maximizes inter-plane separation (minimizes the
    repulsion energy ``Σ exp(−gap/scale)``) while preserving validity. Set
    False to skip it entirely — the RNG stream is then byte-identical to the
    pre-spread solver, so determinism goldens written before this feature
    still hold. See ADR-0008 and
    ``docs/superpowers/specs/2026-05-24-inter-plane-spread-design.md``."""

    spread_scale_m: float | None = None
    """Length scale (metres) of the spread repulsion kernel ``exp(−gap/scale)``.
    ``None`` (default) ⇒ adaptive ``0.2 × min(hangar.width_m, hangar.length_m)``,
    keeping the kernel sensitive across hangar sizes. When set explicitly,
    must be ``> 0``."""

    back_bias_weight: float = 0.0
    """Strength of the back-of-hangar fill bias folded into the spread post-pass
    (#320, ADR-0008 amendment). ``0.0`` (default) ⇒ pure inter-plane spread — the
    pre-#320 behaviour, so the raw spread mechanism and the determinism canaries
    (which run ``spread=False``) stay byte-unchanged. When ``> 0`` the spread
    hill-climb additionally minimizes a secondary term
    ``B = Σ (hangar.length_m − y_p) / hangar.length_m`` that rewards parking deep
    (large ``y``), so free space accumulates at the door end rather than
    mid-hangar; the ``<2 planes`` no-op guard is also relaxed so a lone plane is
    still pulled to the back wall. ``min_pairwise_gap_m`` remains the primary
    basin-selection key — this only re-ranks candidates *within* a basin's
    hill-climb. The CLI enables it by default (``--no-back-fill`` opts out).

    This is a **dimensionless relative weight** balancing the back-bias term
    (normalized to ``[0, 1]`` per plane) against the inter-plane spread energy
    (a sum of ``O(1)`` ``exp`` terms); ``~1.0`` is the tuned operating point, and
    large values subordinate spread to back-fill (collapsing gaps), so keep it a
    *secondary* term. **No effect when ``spread=False``** — the bias is folded
    into the spread post-pass, which only runs under ``spread=True`` (a
    ``spread=False, back_bias_weight>0`` config is a silent no-op by design; the
    CLI never builds one). Modeled as ``float = 0.0`` rather than ``float |
    None`` deliberately: ``0.0`` is the exact identity of ``weight · B``
    (contributes nothing), not a degenerate value, so no distinct opt-out
    sentinel is warranted — ``None``-means-disabled (on ``max_restarts`` and
    ``spread_stall_restarts``) stays the only kind of opt-out sentinel in this
    dataclass; see ``max_restarts``."""

    spread_stall_restarts: int | None = None
    """(F7 / #404) Opt-in early-exit for the spread-ON restart loop. ``None``
    (default) preserves the run-to-budget collect-every-basin behaviour, so the
    determinism canaries (which run ``spread=False``) and the byte-identical
    default are untouched. When set (must be ``>= 1``), ``solve()`` stops the
    spread-ON loop once this many *consecutive* restarts fail to improve the
    selected set's maximin plan-view gap by at least ``spread_stall_epsilon_m``.
    The counter is armed only *after* a complete (``>= alternatives``) selection
    exists — so a hard scenario still gets the full budget to find its first
    answer and the early-exit only trims the polish-the-incumbent tail. The stop
    depends solely on the seed-fixed restart sequence + an integer counter (never
    wall-clock), so the selected layout is identical for a given seed across
    machines: this *narrows* the #267 timing scope rather than widening it
    (ADR-0003). No effect when ``spread=False`` (that path already first-valid
    early-exits) — a ``spread=False`` config with this set is a silent no-op by
    design, *not* an error, mirroring ``back_bias_weight``.

    Calibrated from the F6 benchmark (``bench.profile_pipeline``): on the
    canonical ``roomy_three_spread_on`` regime the maximin gap reaches ~96 % of
    its 30-restart value by restart 3 then plateaus for ~17 restarts, so
    ``spread_stall_restarts=5`` with the default epsilon stops at restart 7
    (~4x fewer restarts) while keeping the near-best separation. See ADR-0008's
    F7 amendment."""

    spread_stall_epsilon_m: float = 0.05
    """(F7 / #404) Minimum maximin-gap improvement (metres) that counts as
    progress for ``spread_stall_restarts``. A concrete positive value, never an
    opt-out sentinel (that role is reserved for ``None`` on ``max_restarts`` /
    ``spread_stall_restarts`` — see ``max_restarts``). Default ``0.05`` m
    (5 cm): on the F6 ``roomy_three_spread_on`` regime the only post-plateau gains
    are a negligible +0.048 m bump and a real +0.396 m late basin, so 5 cm treats
    the former as noise while a genuinely better-separated basin still resets the
    counter. Inert unless ``spread_stall_restarts`` is set; validated ``> 0``
    regardless so the field is always a usable threshold."""

    nose_out: bool = True
    """(#263 / ADR-0022) When True (default), ``solve()`` runs the RNG-free
    ``_nose_out`` post-pass on each valid basin (after ``_spread``): it flips a
    movable plane's parked heading 180° toward nose-out (heading 180, toward the
    door) when that is strictly more nose-out AND keeps the layout valid. A soft
    preference — never overrides validity, never moves a plane, never un-parks one
    (ADR-0008's discipline). **RNG-free ⇒ byte-identical determinism holds even
    with it ON** (strictly stronger than ``spread``); set False for the
    pre-feature heading behaviour. Runs independently of ``spread`` (a placement
    concern), so it also applies on the ``spread=False`` fast path and the
    spread→no-spread fallback. Per-plane override via
    :attr:`PlaneConstraint.nose_out`. A plain ``bool`` (not ``bool | None``):
    ``None`` stays reserved as the disable sentinel for
    ``max_restarts``/``spread_stall_restarts`` (see ``max_restarts``)."""

    def __post_init__(self) -> None:
        if self.candidates_per_iter < 1:
            raise ValueError(
                f"SearchConfig.candidates_per_iter must be >= 1 "
                f"(descent picks one per iter), got {self.candidates_per_iter}"
            )
        if self.k_stall < 1:
            raise ValueError(
                f"SearchConfig.k_stall must be >= 1 "
                f"(zero would restart on every iter), got {self.k_stall}"
            )
        if self.pos_sigma_m <= 0.0:
            raise ValueError(
                f"SearchConfig.pos_sigma_m must be positive "
                f"(zero freezes the trajectory), got {self.pos_sigma_m}"
            )
        if self.heading_sigma_deg <= 0.0:
            raise ValueError(
                f"SearchConfig.heading_sigma_deg must be positive, got {self.heading_sigma_deg}"
            )
        if self.max_restarts is not None and self.max_restarts < 1:
            raise ValueError(
                f"SearchConfig.max_restarts must be >= 1 when set "
                f"(pass ``None`` to disable the restart cap; ``0`` would "
                f"skip the search loop entirely), got {self.max_restarts}"
            )
        if self.spread_scale_m is not None and self.spread_scale_m <= 0.0:
            raise ValueError(
                f"SearchConfig.spread_scale_m must be positive when set "
                f"(pass None for the adaptive default), got {self.spread_scale_m}"
            )
        if self.back_bias_weight < 0.0:
            raise ValueError(
                f"SearchConfig.back_bias_weight must be >= 0 "
                f"(0 disables the back-of-hangar fill bias), got {self.back_bias_weight}"
            )
        if self.spread_stall_restarts is not None and self.spread_stall_restarts < 1:
            raise ValueError(
                f"SearchConfig.spread_stall_restarts must be >= 1 when set "
                f"(pass ``None`` to disable the spread-stagnation early-exit; "
                f"``0`` would break before any restart), got {self.spread_stall_restarts}"
            )
        if self.spread_stall_epsilon_m <= 0.0:
            raise ValueError(
                f"SearchConfig.spread_stall_epsilon_m must be positive "
                f"(metres of maximin-gap improvement that counts as progress), "
                f"got {self.spread_stall_epsilon_m}"
            )
