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
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from hangarfit.towplanner import MovesPlan

WingPosition = Literal["high", "mid", "low"]
Gear = Literal["tailwheel", "nosewheel", "monowheel"]
MovementMode = Literal["always_cart", "always_own_gear", "cart_eligible"]
PartKind = Literal["fuselage_front", "fuselage_aft", "wing", "strut", "tail"]

_VALID_PART_KINDS = frozenset(typing.get_args(PartKind))
_VALID_WING_POSITIONS = frozenset(typing.get_args(WingPosition))
_VALID_GEARS = frozenset(typing.get_args(Gear))
_VALID_MOVEMENT_MODES = frozenset(typing.get_args(MovementMode))


@dataclass(frozen=True, slots=True)
class Part:
    """One oriented rectangle in plane-local coordinates with a height range.

    The universal collision unit. Fuselage segments, wing, and each strut
    are all represented as ``Part`` instances. See
    ``docs/architecture/08-crosscutting-concepts.md`` "The coordinate
    convention" for plane-local coordinates (``+x`` forward, ``+y`` right).

    ``kind`` is closed: ``"fuselage_front" | "fuselage_aft" | "wing" |
    "strut" | "tail"``. The fuselage is split front/aft so the collision
    rule can distinguish a wing over a cockpit (``fuselage_front`` — always
    a conflict in plan view, height ignored) from a wing over a tail
    (``fuselage_aft`` — today's two-clause z-gap rule); see ADR-0012 and
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
        """Turn radius for path planning: ``0.0`` for cart-borne planes
        (a pivot-in-place), else the own-gear ``required_turn_radius_m()``.

        This is the accessor the tow-path planner consumes (ADR-0007): a
        cart-borne plane is modelled as own-gear with a zero turn radius.
        Unlike :meth:`required_turn_radius_m`, this never raises — callers
        that legitimately handle carts (the Dubins planner) use this one.
        """
        if self.movement_mode == "always_cart":
            return 0.0
        return self.required_turn_radius_m()


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
    """

    length_m: float
    width_m: float
    door: Door
    maintenance_bay: MaintenanceBay
    clearance_m: float
    wing_layer_clearance_m: float
    max_carts: int = 1

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

    The bay-closure rule (no other plane's parts may cross into the
    closed bay rectangle) is a geometric check; it lives in the
    collision checker alongside the other geometric rules.

    On construction, ``fleet`` is wrapped in ``MappingProxyType`` so
    that the cross-reference invariants stay valid for the lifetime of
    the ``Layout`` (a plain ``dict`` field, even on a frozen dataclass,
    can be mutated through ``layout.fleet["x"] = …``).
    """

    fleet: Mapping[str, Aircraft]
    hangar: Hangar
    placements: tuple[Placement, ...]
    maintenance_plane: str | None = None

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

        object.__setattr__(self, "fleet", MappingProxyType(dict(self.fleet)))


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
    """

    pin: Placement | None = None
    force_on_carts: bool | None = None
    priority: float | None = None


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
    - fleet and constraints are wrapped in MappingProxyType (same pattern as Layout)

    See spec §3.2 for the rationale.
    """

    fleet: Mapping[str, Aircraft]
    hangar: Hangar
    fleet_in: tuple[str, ...]
    maintenance_plane: str | None = None
    constraints: Mapping[str, PlaneConstraint] = field(default_factory=lambda: MappingProxyType({}))

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

        object.__setattr__(self, "fleet", MappingProxyType(dict(self.fleet)))
        # Always copy+wrap constraints — mirrors the unconditional pattern on
        # fleet above (and on Layout.fleet). Skipping the copy when the caller
        # passes a pre-wrapped MappingProxyType would let the caller leak
        # mutations through their retained reference to the underlying dict.
        object.__setattr__(self, "constraints", MappingProxyType(dict(self.constraints)))


SolveStatus = Literal[
    "found",
    "found_partial",
    "exhausted_budget",
    "trivially_infeasible",
]


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

    ``min_pairwise_gap_m`` is index-aligned with :attr:`SolveResult.layouts`:
    the achieved minimum plan-view gap (m) between any two planes in that
    returned layout — the quality the best-of-all-basins spread selection
    maximizes (#267, ADR-0008). ``math.inf`` for a layout with <2 planes
    (no pairs). ``valid_basins_found`` is the number of valid spread-polished
    basins the search collected before selection — how much choice best-of-all
    had. Both are advisory.

    ``spread_fallback_applied`` is ``True`` when :func:`hangarfit.solver.solve`
    re-solved with the inter-plane spread post-pass disabled and substituted
    that tighter, tow-routable arrangement because the spread layout(s) came
    back valid but un-routable under ``plan_paths=True`` (the ADR-0016 / #280
    fallback, promoted into the library in #402 / F5). Always present (``False``
    in the normal no-swap case) so non-interactive consumers can rely on it.
    Advisory: the swap changes *which* valid layout is returned, never *whether*
    it is valid — it does not affect ``status``.
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

    ``None`` is the only "opt out" sentinel in :class:`SearchConfig` /
    :class:`DiversityConfig` / :class:`SolverDiagnostics`; all other
    numeric fields require a concrete positive value. Pass ``None``
    explicitly to disable the cap; passing ``0`` is rejected (it would
    skip the search loop entirely)."""

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
    sentinel is warranted — ``None`` stays the *only* sentinel in this dataclass
    (on ``max_restarts``)."""

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
