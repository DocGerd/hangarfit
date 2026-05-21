"""YAML → typed-model loader for hangarfit.

Thin adapter layer between user-authored YAML files and the dataclasses
in :mod:`hangarfit.models`. The loader's main job, besides translating
types, is **error message quality** — a 250-line ``fleet.yaml`` is
miserable to debug if errors only say "ValueError on line ?". Every
exception raised here is a :class:`LoaderError` with the file path
and (where it makes sense) the aircraft id or field name prepended.

The loader also performs the one piece of build-time geometry that
makes the parts-model schema readable: it expands each strut-braced
aircraft's high-level ``struts:`` block into two mirrored strut
:class:`~hangarfit.models.Part` instances. After loading, the
aircraft's ``parts`` tuple is the single source of truth for geometry
— there is no separate ``struts`` field on the :class:`Aircraft`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import (
    Aircraft,
    Door,
    Hangar,
    Layout,
    MaintenanceBay,
    Part,
    Placement,
    StrutsSpec,
)


class LoaderError(Exception):
    """Raised when a YAML file is malformed or fails model validation."""


def load_fleet(path: Path | str) -> dict[str, Aircraft]:
    """Load ``fleet.yaml`` into a dict keyed by :attr:`Aircraft.id`.

    Expands the optional ``struts:`` block on each aircraft into
    mirrored strut Parts (one per side), folded into the final
    ``parts`` tuple.
    """
    path = Path(path)
    raw = _read_yaml(path)
    if not isinstance(raw, dict) or "aircraft" not in raw:
        raise LoaderError(f"{path}: top-level mapping must contain 'aircraft' list")
    aircraft_list = raw["aircraft"]
    if not isinstance(aircraft_list, list):
        raise LoaderError(f"{path}: 'aircraft' must be a list")

    fleet: dict[str, Aircraft] = {}
    for i, entry in enumerate(aircraft_list):
        if not isinstance(entry, dict):
            raise LoaderError(
                f"{path}: aircraft entry #{i} must be a mapping, got {type(entry).__name__}"
            )
        ident = entry.get("id", f"#{i}")
        try:
            aircraft = _build_aircraft(entry)
        except (ValueError, KeyError, LoaderError) as e:
            raise LoaderError(f"{path}: aircraft {ident!r}: {e}") from e
        if aircraft.id in fleet:
            raise LoaderError(f"{path}: duplicate aircraft id {aircraft.id!r}")
        fleet[aircraft.id] = aircraft
    return fleet


def load_hangar(path: Path | str) -> Hangar:
    """Load ``hangar.yaml`` into a :class:`Hangar`."""
    path = Path(path)
    raw = _read_yaml(path)
    if not isinstance(raw, dict):
        raise LoaderError(f"{path}: top-level must be a mapping")

    door_data = raw.get("door")
    if not isinstance(door_data, dict):
        raise LoaderError(f"{path}: 'door' must be a mapping with 'center_x_m' and 'width_m'")

    bay_data = raw.get("maintenance_bay")
    if not isinstance(bay_data, dict):
        raise LoaderError(f"{path}: 'maintenance_bay' must be a mapping with 'depth_m'")

    for key in ("length_m", "width_m"):
        if key not in raw:
            raise LoaderError(f"{path}: missing required field {key!r}")
    for key in ("center_x_m", "width_m"):
        if key not in door_data:
            raise LoaderError(f"{path}: missing required field 'door.{key}'")
    if "depth_m" not in bay_data:
        raise LoaderError(f"{path}: missing required field 'maintenance_bay.depth_m'")

    try:
        return Hangar(
            length_m=float(raw["length_m"]),
            width_m=float(raw["width_m"]),
            door=Door(
                center_x_m=float(door_data["center_x_m"]),
                width_m=float(door_data["width_m"]),
            ),
            maintenance_bay=MaintenanceBay(depth_m=float(bay_data["depth_m"])),
            clearance_m=float(raw.get("clearance_m", 0.3)),
            wing_layer_clearance_m=float(raw.get("wing_layer_clearance_m", 0.2)),
        )
    except (ValueError, TypeError) as e:
        raise LoaderError(f"{path}: {e}") from e


def load_layout(
    path: Path | str,
    *,
    fleet: dict[str, Aircraft] | None = None,
    hangar: Hangar | None = None,
) -> Layout:
    """Load a layout YAML.

    If ``fleet`` and ``hangar`` are provided, the YAML's ``fleet:`` and
    ``hangar:`` fields are ignored. Otherwise, those fields are
    interpreted as paths **relative to the layout YAML's parent
    directory** and loaded.
    """
    path = Path(path)
    raw = _read_yaml(path)
    if not isinstance(raw, dict):
        raise LoaderError(f"{path}: top-level must be a mapping")

    if fleet is None:
        fleet_ref = raw.get("fleet")
        if fleet_ref is None:
            raise LoaderError(
                f"{path}: 'fleet' field is required when no fleet override is provided"
            )
        fleet = load_fleet((path.parent / fleet_ref).resolve())

    if hangar is None:
        hangar_ref = raw.get("hangar")
        if hangar_ref is None:
            raise LoaderError(
                f"{path}: 'hangar' field is required when no hangar override is provided"
            )
        hangar = load_hangar((path.parent / hangar_ref).resolve())

    placements_data = raw.get("placements", [])
    if not isinstance(placements_data, list):
        raise LoaderError(f"{path}: 'placements' must be a list")
    try:
        placements = tuple(_build_placement(p) for p in placements_data)
    except (ValueError, KeyError, TypeError) as e:
        raise LoaderError(f"{path}: placement: {e}") from e

    maintenance_plane: str | None = None
    if isinstance(raw.get("maintenance"), dict):
        maintenance_plane = raw["maintenance"].get("plane")

    try:
        return Layout(
            fleet=fleet,
            hangar=hangar,
            placements=placements,
            maintenance_plane=maintenance_plane,
        )
    except ValueError as e:
        raise LoaderError(f"{path}: {e}") from e


def _build_aircraft(entry: dict[str, Any]) -> Aircraft:
    parts_data = entry.get("parts")
    if not isinstance(parts_data, list) or not parts_data:
        raise LoaderError("'parts' must be a non-empty list")
    parts = [_build_part(p, i) for i, p in enumerate(parts_data)]

    if "struts" in entry:
        if not isinstance(entry["struts"], dict):
            raise LoaderError("'struts' must be a mapping")
        spec = _build_struts_spec(entry["struts"])
        wing = next((p for p in parts if p.kind == "wing"), None)
        if wing is None:
            raise LoaderError("'struts' block requires a part of kind 'wing'")
        parts.extend(_expand_struts(spec, wing))

    return Aircraft(
        id=entry["id"],
        name=entry["name"],
        wing_position=entry["wing_position"],
        gear=entry["gear"],
        movement_mode=entry["movement_mode"],
        turn_radius_m=entry.get("turn_radius_m"),
        measured=bool(entry.get("measured", False)),
        parts=tuple(parts),
        notes=entry.get("notes", ""),
    )


def _build_part(data: Any, index: int) -> Part:
    if not isinstance(data, dict):
        raise LoaderError(f"parts[{index}] must be a mapping")
    required = ("kind", "length_m", "width_m", "z_bottom_m", "z_top_m")
    for key in required:
        if key not in data:
            raise LoaderError(f"parts[{index}] missing required field {key!r}")
    return Part(
        kind=data["kind"],
        length_m=float(data["length_m"]),
        width_m=float(data["width_m"]),
        offset_x_m=float(data.get("offset_x_m", 0.0)),
        offset_y_m=float(data.get("offset_y_m", 0.0)),
        angle_deg=float(data.get("angle_deg", 0.0)),
        z_bottom_m=float(data["z_bottom_m"]),
        z_top_m=float(data["z_top_m"]),
    )


def _build_struts_spec(data: dict[str, Any]) -> StrutsSpec:
    required = (
        "fuselage_attach_x_m",
        "fuselage_attach_y_m",
        "fuselage_attach_z_m",
        "wing_attach_y_m",
        "width_m",
    )
    for key in required:
        if key not in data:
            raise LoaderError(f"'struts' missing required field {key!r}")
    return StrutsSpec(
        fuselage_attach_x_m=float(data["fuselage_attach_x_m"]),
        fuselage_attach_y_m=float(data["fuselage_attach_y_m"]),
        fuselage_attach_z_m=float(data["fuselage_attach_z_m"]),
        wing_attach_y_m=float(data["wing_attach_y_m"]),
        width_m=float(data["width_m"]),
    )


def _expand_struts(spec: StrutsSpec, wing: Part) -> list[Part]:
    """Build two mirrored strut Parts from a StrutsSpec + wing.

    Each strut is modelled in plan view as a thin oriented rectangle
    running outboard from the fuselage attach (y = ``fuselage_attach_y_m``)
    to the wing attach (y = ``wing_attach_y_m``), at the same x as the
    fuselage attach point. The strut's z-range is
    ``[fuselage_attach_z_m, wing.z_bottom_m]`` — i.e. from the lower
    fuselage attach point up to the wing underside.
    """
    if wing.z_bottom_m <= spec.fuselage_attach_z_m:
        raise LoaderError(
            f"strut z_top (wing.z_bottom_m={wing.z_bottom_m}) must be above "
            f"strut z_bottom (fuselage_attach_z_m={spec.fuselage_attach_z_m}). "
            f"Struts only make sense when the wing is above the fuselage attach point."
        )
    strut_span = spec.wing_attach_y_m - spec.fuselage_attach_y_m
    if strut_span <= 0:
        raise LoaderError(
            f"strut would have zero outboard span "
            f"(wing_attach_y_m={spec.wing_attach_y_m}, "
            f"fuselage_attach_y_m={spec.fuselage_attach_y_m}); "
            f"loader requires a strictly positive span"
        )
    midpoint = (spec.fuselage_attach_y_m + spec.wing_attach_y_m) / 2.0
    common = {
        "kind": "strut",
        "length_m": spec.width_m,
        "width_m": strut_span,
        "offset_x_m": spec.fuselage_attach_x_m,
        "angle_deg": 0.0,
        "z_bottom_m": spec.fuselage_attach_z_m,
        "z_top_m": wing.z_bottom_m,
    }
    return [
        Part(offset_y_m=midpoint, **common),   # right side
        Part(offset_y_m=-midpoint, **common),  # left side
    ]


def _build_placement(data: Any) -> Placement:
    if not isinstance(data, dict):
        raise LoaderError(f"placement must be a mapping, got {type(data).__name__}")
    required = ("plane", "x_m", "y_m", "heading_deg")
    for key in required:
        if key not in data:
            raise LoaderError(f"placement missing required field {key!r}")
    return Placement(
        plane_id=data["plane"],
        x_m=float(data["x_m"]),
        y_m=float(data["y_m"]),
        heading_deg=float(data["heading_deg"]),
        on_carts=bool(data.get("on_carts", False)),
    )


def _read_yaml(path: Path) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError as e:
        raise LoaderError(f"file not found: {path}") from e
    except yaml.YAMLError as e:
        raise LoaderError(f"{path}: YAML parse error: {e}") from e


