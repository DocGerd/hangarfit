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


def _to_float(value: Any, field_name: str) -> float:
    """Coerce a YAML scalar to ``float``, raising ``LoaderError`` with the
    field name on failure (rather than a bare ``TypeError`` from
    ``float(None)`` or ``ValueError`` from ``float("abc")``)."""
    if value is None:
        raise LoaderError(f"{field_name!r}: expected number, got null")
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise LoaderError(
            f"{field_name!r}: expected number, got {value!r} ({type(value).__name__})"
        ) from e


def _to_bool(value: Any, field_name: str) -> bool:
    """Coerce a YAML scalar to ``bool`` strictly. Rejects quoted strings
    (``"true"``, ``"false"``) and any non-bool value — those are the
    classic YAML silent-flip footgun (``bool("false")`` is ``True``)."""
    if not isinstance(value, bool):
        raise LoaderError(
            f"{field_name!r}: expected boolean (unquoted true/false), "
            f"got {value!r} ({type(value).__name__})"
        )
    return value


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
        ident = entry.get("id", f"#{i}") if isinstance(entry, dict) else f"#{i}"
        try:
            aircraft = _build_aircraft(entry)
        except (ValueError, KeyError, TypeError, LoaderError) as e:
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
            length_m=_to_float(raw["length_m"], "length_m"),
            width_m=_to_float(raw["width_m"], "width_m"),
            door=Door(
                center_x_m=_to_float(door_data["center_x_m"], "door.center_x_m"),
                width_m=_to_float(door_data["width_m"], "door.width_m"),
            ),
            maintenance_bay=MaintenanceBay(
                depth_m=_to_float(bay_data["depth_m"], "maintenance_bay.depth_m")
            ),
            clearance_m=_to_float(raw.get("clearance_m", 0.3), "clearance_m"),
            wing_layer_clearance_m=_to_float(
                raw.get("wing_layer_clearance_m", 0.2), "wing_layer_clearance_m"
            ),
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

    Path resolution: when the YAML supplies ``fleet:`` / ``hangar:``,
    those values are joined to the layout YAML's parent directory.
    Absolute paths in those fields override the join (pathlib's
    behavior — passing an absolute right-hand side to ``/`` discards
    the left), which is occasionally useful but also a footgun; prefer
    repo-relative paths.

    Conflict policy: if ``fleet`` / ``hangar`` overrides are supplied
    as kwargs **and** the YAML also has those fields, the loader
    raises :class:`LoaderError`. We refuse to silently let one source
    of truth shadow the other.
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
    elif "fleet" in raw:
        raise LoaderError(
            f"{path}: 'fleet' field is set in YAML but a fleet override was also "
            f"provided programmatically; remove one to disambiguate"
        )

    if hangar is None:
        hangar_ref = raw.get("hangar")
        if hangar_ref is None:
            raise LoaderError(
                f"{path}: 'hangar' field is required when no hangar override is provided"
            )
        hangar = load_hangar((path.parent / hangar_ref).resolve())
    elif "hangar" in raw:
        raise LoaderError(
            f"{path}: 'hangar' field is set in YAML but a hangar override was also "
            f"provided programmatically; remove one to disambiguate"
        )

    placements_data = raw.get("placements", [])
    if not isinstance(placements_data, list):
        raise LoaderError(f"{path}: 'placements' must be a list")
    try:
        placements = tuple(_build_placement(p) for p in placements_data)
    except (ValueError, KeyError, TypeError, LoaderError) as e:
        raise LoaderError(f"{path}: placement: {e}") from e

    maintenance_plane = _extract_maintenance_plane(raw, path)

    try:
        return Layout(
            fleet=fleet,
            hangar=hangar,
            placements=placements,
            maintenance_plane=maintenance_plane,
        )
    except ValueError as e:
        raise LoaderError(f"{path}: {e}") from e


def _extract_maintenance_plane(raw: dict, path: Path) -> str | None:
    """Pull ``maintenance_plane`` from the layout YAML, rejecting common
    typos (``maintenance: cessna_150`` with no nested ``plane:`` key)."""
    if "maintenance" not in raw:
        return None
    m = raw["maintenance"]
    if m is None:
        return None  # explicit `maintenance: ~` → no maintenance plane
    if not isinstance(m, dict):
        raise LoaderError(
            f"{path}: 'maintenance' must be a mapping with a 'plane' key, got {type(m).__name__}"
        )
    if "plane" not in m:
        raise LoaderError(f"{path}: 'maintenance' block present but lacks required 'plane' key")
    return m["plane"]


def _build_aircraft(entry: Any) -> Aircraft:
    if not isinstance(entry, dict):
        raise LoaderError(f"aircraft entry must be a mapping, got {type(entry).__name__}")

    required = ("id", "name", "wing_position", "gear", "movement_mode")
    for key in required:
        if key not in entry:
            raise LoaderError(f"missing required field {key!r}")

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

    turn_radius_raw = entry.get("turn_radius_m")
    turn_radius_m = None if turn_radius_raw is None else _to_float(turn_radius_raw, "turn_radius_m")

    return Aircraft(
        id=entry["id"],
        name=entry["name"],
        wing_position=entry["wing_position"],
        gear=entry["gear"],
        movement_mode=entry["movement_mode"],
        turn_radius_m=turn_radius_m,
        measured=_to_bool(entry.get("measured", False), "measured"),
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
        length_m=_to_float(data["length_m"], f"parts[{index}].length_m"),
        width_m=_to_float(data["width_m"], f"parts[{index}].width_m"),
        offset_x_m=_to_float(data.get("offset_x_m", 0.0), f"parts[{index}].offset_x_m"),
        offset_y_m=_to_float(data.get("offset_y_m", 0.0), f"parts[{index}].offset_y_m"),
        angle_deg=_to_float(data.get("angle_deg", 0.0), f"parts[{index}].angle_deg"),
        z_bottom_m=_to_float(data["z_bottom_m"], f"parts[{index}].z_bottom_m"),
        z_top_m=_to_float(data["z_top_m"], f"parts[{index}].z_top_m"),
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
        fuselage_attach_x_m=_to_float(data["fuselage_attach_x_m"], "struts.fuselage_attach_x_m"),
        fuselage_attach_y_m=_to_float(data["fuselage_attach_y_m"], "struts.fuselage_attach_y_m"),
        fuselage_attach_z_m=_to_float(data["fuselage_attach_z_m"], "struts.fuselage_attach_z_m"),
        wing_attach_y_m=_to_float(data["wing_attach_y_m"], "struts.wing_attach_y_m"),
        width_m=_to_float(data["width_m"], "struts.width_m"),
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
        Part(offset_y_m=midpoint, **common),  # right side
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
        x_m=_to_float(data["x_m"], "x_m"),
        y_m=_to_float(data["y_m"], "y_m"),
        heading_deg=_to_float(data["heading_deg"], "heading_deg"),
        on_carts=_to_bool(data.get("on_carts", False), "on_carts"),
    )


def _read_yaml(path: Path) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError as e:
        raise LoaderError(f"file not found: {path}") from e
    except yaml.YAMLError as e:
        raise LoaderError(f"{path}: YAML parse error: {e}") from e
