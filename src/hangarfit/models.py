"""Pure data models for hangarfit.

No I/O, no business logic. Every type here is a frozen dataclass with
slots, validates its invariants in ``__post_init__``, and uses tuples
instead of lists where collections appear (so the whole graph is
effectively immutable after construction).

The full coordinate convention and parts-model collision rule live in
``CLAUDE.md`` at the repo root — this module just encodes the types.
"""

from __future__ import annotations

import typing
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

WingPosition = Literal["high", "mid", "low"]
Gear = Literal["tailwheel", "nosewheel", "monowheel"]
MovementMode = Literal["always_cart", "always_own_gear", "cart_eligible"]
PartKind = Literal["fuselage", "wing", "strut", "tail"]

_VALID_PART_KINDS = frozenset(typing.get_args(PartKind))
_VALID_WING_POSITIONS = frozenset(typing.get_args(WingPosition))
_VALID_GEARS = frozenset(typing.get_args(Gear))
_VALID_MOVEMENT_MODES = frozenset(typing.get_args(MovementMode))


@dataclass(frozen=True, slots=True)
class Part:
    """One oriented rectangle in plane-local coordinates with a height range.

    The universal collision unit. Fuselage, wing, and each strut are all
    represented as ``Part`` instances. See ``CLAUDE.md`` for the
    plane-local coordinate convention (``+x`` forward, ``+y`` right).

    ``kind`` is closed: ``"fuselage" | "wing" | "strut" | "tail"``. New
    kinds must be added to ``PartKind`` and the matching ``_VALID_PART_KINDS``
    set above; the collision checker and visualizer key off these values.
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
    """

    id: str
    name: str
    wing_position: WingPosition
    gear: Gear
    movement_mode: MovementMode
    turn_radius_m: float | None
    measured: bool
    parts: tuple[Part, ...]
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
    """The back-most strip of the hangar that doubles as the maintenance bay."""

    depth_m: float

    def __post_init__(self) -> None:
        if self.depth_m <= 0:
            raise ValueError(f"MaintenanceBay.depth_m must be positive, got {self.depth_m}")


@dataclass(frozen=True, slots=True)
class Hangar:
    """The hangar floor plan.

    Coordinates: ``(0, 0)`` at the front-left corner, ``+x`` along the
    door wall, ``+y`` deeper into the hangar. See ``CLAUDE.md``.
    """

    length_m: float
    width_m: float
    door: Door
    maintenance_bay: MaintenanceBay
    clearance_m: float
    wing_layer_clearance_m: float

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
        door_left = self.door.center_x_m - self.door.width_m / 2
        door_right = self.door.center_x_m + self.door.width_m / 2
        if door_left < 0 or door_right > self.width_m:
            raise ValueError(
                f"Door (center={self.door.center_x_m}, width={self.door.width_m}) "
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
    - the cart rule (at most one ``cart_eligible`` plane on carts),
    - ``always_cart`` ↔ ``on_carts=True`` consistency,
    - ``always_own_gear`` ↔ ``on_carts=False`` consistency,
    - the maintenance plane (if set) is in the fleet and is placed.

    The maintenance plane's **position** rule ("must be parked in the
    back-most strip of the hangar") is *not* enforced here — it depends
    on placement geometry and the hangar's maintenance-bay depth, so it
    lives in the collision checker (#5) alongside the other geometric
    rules.

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
        if cart_count > 1:
            raise ValueError(
                f"At most one cart_eligible plane may have on_carts=True (got {cart_count})"
            )

        if self.maintenance_plane is not None:
            if self.maintenance_plane not in self.fleet:
                raise ValueError(f"maintenance_plane {self.maintenance_plane!r} not in fleet")
            if self.maintenance_plane not in seen:
                raise ValueError(f"maintenance_plane {self.maintenance_plane!r} is not placed")

        object.__setattr__(self, "fleet", MappingProxyType(dict(self.fleet)))


@dataclass(frozen=True, slots=True)
class Conflict:
    """One reason a layout is invalid.

    ``planes`` carries 1 or 2 *distinct, non-empty* aircraft IDs
    depending on the rule that fired (layout-wide rules like
    ``maintenance_position`` cite one plane; pairwise rules like
    ``wing_strut_overlap`` cite two). Use ``Conflict.single()`` /
    ``Conflict.pair()`` at call sites to make the arity explicit.
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
        """Factory for a single-aircraft conflict (e.g. ``maintenance_position``)."""
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
    conflicts (``maintenance_position``, ``maintenance_no_fuselage``,
    ``hangar_bounds``) contribute 0. The validity contract is unchanged:
    ``valid`` is still derived from ``conflicts`` only.
    """

    conflicts: tuple[Conflict, ...] = ()
    total_penetration_m2: float = 0.0

    @property
    def valid(self) -> bool:
        return len(self.conflicts) == 0
