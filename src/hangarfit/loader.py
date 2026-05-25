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

import difflib
from collections.abc import Collection, Iterable
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
    PlaneConstraint,
    Scenario,
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
        raise LoaderError(
            f"{path}: 'maintenance_bay' must be a mapping with "
            f"'center_x_m', 'width_m', and 'depth_m'"
        )

    for key in ("length_m", "width_m"):
        if key not in raw:
            raise LoaderError(f"{path}: missing required field {key!r}")
    for key in ("center_x_m", "width_m"):
        if key not in door_data:
            raise LoaderError(f"{path}: missing required field 'door.{key}'")
    for key in ("center_x_m", "width_m", "depth_m"):
        if key not in bay_data:
            raise LoaderError(f"{path}: missing required field 'maintenance_bay.{key}'")

    try:
        return Hangar(
            length_m=_to_float(raw["length_m"], "length_m"),
            width_m=_to_float(raw["width_m"], "width_m"),
            door=Door(
                center_x_m=_to_float(door_data["center_x_m"], "door.center_x_m"),
                width_m=_to_float(door_data["width_m"], "door.width_m"),
            ),
            maintenance_bay=MaintenanceBay(
                center_x_m=_to_float(bay_data["center_x_m"], "maintenance_bay.center_x_m"),
                width_m=_to_float(bay_data["width_m"], "maintenance_bay.width_m"),
                depth_m=_to_float(bay_data["depth_m"], "maintenance_bay.depth_m"),
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

    for p in placements:
        _resolve_known_plane_id(p.plane_id, fleet, role="placement", path=path)
    if maintenance_plane is not None:
        _resolve_known_plane_id(maintenance_plane, fleet, role="maintenance.plane", path=path)

    # Pre-Layout boundary check for the most common YAML-author mistake:
    # naming the bay occupant in ``placements``. ``Layout.__post_init__``
    # catches this too, but with a generic invariant message; raise here
    # with an actionable suffix so the YAML author knows exactly what to
    # edit. DO NOT remove the Layout invariant — it's the only line of
    # defense for callers that construct Layouts directly in code
    # (tests, solver internals, REPL exploration). This loader check is
    # a UX improvement for the YAML path, not a replacement.
    if maintenance_plane is not None:
        for p in placements:
            if p.plane_id == maintenance_plane:
                raise LoaderError(
                    f"{path}: maintenance_plane {maintenance_plane!r} is named in "
                    f"placements; an aircraft in maintenance is treated as away and "
                    f"must NOT be placed. Remove it from placements (or fix the plane "
                    f"id if it doesn't match an aircraft in the fleet)."
                )

    try:
        return Layout(
            fleet=fleet,
            hangar=hangar,
            placements=placements,
            maintenance_plane=maintenance_plane,
        )
    except ValueError as e:
        raise LoaderError(f"{path}: {e}") from e


def load_scenario(
    path: Path | str,
    *,
    fleet: dict[str, Aircraft] | None = None,
    hangar: Hangar | None = None,
) -> Scenario:
    """Load a scenario YAML into a validated :class:`Scenario`.

    Path resolution and override-conflict policy mirror :func:`load_layout`:
    ``fleet:`` and ``hangar:`` YAML refs are resolved relative to the
    scenario file's directory (with the absolute-path-overrides-join
    behaviour of :class:`pathlib.Path`), and passing ``fleet=`` or
    ``hangar=`` as a kwarg while the YAML *also* sets the corresponding
    field raises ``LoaderError`` rather than letting one source of truth
    silently shadow the other.

    Scenario YAML schema:

    ``fleet_in: [plane_id, ...]`` is required and must be non-empty.
    ``maintenance: {plane: plane_id}`` is optional.
    ``constraints`` is a mapping of ``plane_id -> {pin?, force_on_carts?}``;
    see :func:`_build_plane_constraint` for the pin schema (notably:
    ``pin.plane_id`` is taken from the constraint key, NOT repeated under
    the pin block).
    """
    path = Path(path)
    raw = _read_yaml(path)
    if not isinstance(raw, dict):
        raise LoaderError(f"{path}: top-level must be a mapping")

    # fleet_in (required) — checked before fleet/hangar are loaded so that
    # a missing required field is reported as such, rather than as a
    # downstream "file not found" when the fleet path is bogus.
    if "fleet_in" not in raw:
        raise LoaderError(f"{path}: missing required field 'fleet_in'")
    fleet_in_raw = raw["fleet_in"]
    if not isinstance(fleet_in_raw, list):
        raise LoaderError(f"{path}: 'fleet_in' must be a list")
    fleet_in = tuple(str(x) for x in fleet_in_raw)

    # fleet / hangar — same pattern as load_layout
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

    # maintenance (optional, same shape as load_layout)
    maintenance_plane = _extract_maintenance_plane(raw, path)

    # Pre-Scenario boundary check: surface the YAML-author error with a
    # path prefix here instead of relying on Scenario.__post_init__'s
    # bare ValueError bubbling through the except ValueError → LoaderError
    # wrap below (which would drop the actionable fleet_in hint).
    if maintenance_plane is not None and maintenance_plane not in fleet_in:
        raise LoaderError(
            f"{path}: maintenance_plane {maintenance_plane!r} is not in fleet_in "
            f"{list(fleet_in)}; either add it to fleet_in or fix the plane id."
        )

    # constraints (optional). `or {}` is wrong here — it collapses every
    # falsy YAML value (including `constraints: []` or `constraints: 0`)
    # to `{}` before the isinstance check, silently treating shape bugs
    # as "no constraints". Use explicit None-handling so the isinstance
    # check actually fires for non-dict shapes.
    constraints_raw = raw.get("constraints", {})
    if constraints_raw is None:  # explicit YAML null: `constraints:`
        constraints_raw = {}
    if not isinstance(constraints_raw, dict):
        raise LoaderError(f"{path}: 'constraints' must be a mapping")
    constraints: dict[str, PlaneConstraint] = {}
    for plane_id, cdata in constraints_raw.items():
        try:
            constraints[plane_id] = _build_plane_constraint(plane_id, cdata)
        except (ValueError, KeyError, TypeError, LoaderError) as e:
            raise LoaderError(f"{path}: constraint {plane_id!r}: {e}") from e

    try:
        return Scenario(
            fleet=fleet,
            hangar=hangar,
            fleet_in=fleet_in,
            maintenance_plane=maintenance_plane,
            constraints=constraints,
        )
    except ValueError as e:
        raise LoaderError(f"{path}: {e}") from e


def _build_plane_constraint(plane_id: str, data: Any) -> PlaneConstraint:
    """Build a :class:`PlaneConstraint` from one entry in a scenario YAML
    ``constraints:`` block.

    YAML schema accepted:

    .. code-block:: yaml

        constraints:
          <plane_id>:
            pin: { x_m: <float>, y_m: <float>, heading_deg: <float>, on_carts: <bool> }
            force_on_carts: <bool>

    Both ``pin`` and ``force_on_carts`` are optional. Omitting both
    yields a "free" constraint (the solver may place the plane anywhere
    within physical / cart-rule limits).

    **Implicit ``pin.plane_id``** — the loader fills :attr:`Placement.plane_id`
    from the constraint key, so authors don't repeat the plane id under
    the ``pin`` block. The Scenario invariant
    ``pin.plane_id == constraint key`` only matters when constructing
    :class:`Scenario` directly in Python.

    **Required ``pin.on_carts``** — there is no sensible default, so the
    YAML must spell it out explicitly. ``always_cart`` and
    ``always_own_gear`` planes have a single legal value;
    ``cart_eligible`` planes have two and a silent default would silently
    pick the wrong one for users who didn't mean it.
    """
    if not isinstance(data, dict):
        raise LoaderError(f"must be a mapping, got {type(data).__name__}")

    pin_data = data.get("pin")
    pin: Placement | None = None
    if pin_data is not None:
        if not isinstance(pin_data, dict):
            raise LoaderError(f"'pin' must be a mapping, got {type(pin_data).__name__}")
        # pin's plane_id is filled in from the constraint key (the YAML schema
        # doesn't repeat it — the user already keys it under the plane).
        required = ("x_m", "y_m", "heading_deg", "on_carts")
        for key in required:
            if key not in pin_data:
                raise LoaderError(f"'pin' missing required field {key!r}")
        pin = Placement(
            plane_id=plane_id,
            x_m=_to_float(pin_data["x_m"], "pin.x_m"),
            y_m=_to_float(pin_data["y_m"], "pin.y_m"),
            heading_deg=_to_float(pin_data["heading_deg"], "pin.heading_deg"),
            on_carts=_to_bool(pin_data["on_carts"], "pin.on_carts"),
        )

    force_on_carts = data.get("force_on_carts")
    if force_on_carts is not None:
        force_on_carts = _to_bool(force_on_carts, "force_on_carts")

    return PlaneConstraint(pin=pin, force_on_carts=force_on_carts)


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
    plane = m["plane"]
    if plane is None:
        raise LoaderError(
            f"{path}: 'maintenance.plane' is null; either remove the 'maintenance' "
            f"block entirely or name an aircraft id"
        )
    if not isinstance(plane, str):
        raise LoaderError(
            f"{path}: 'maintenance.plane' must be a string aircraft id, "
            f"got {plane!r} ({type(plane).__name__})"
        )
    if not plane:
        raise LoaderError(
            f"{path}: 'maintenance.plane' must be non-empty; "
            f"either remove the 'maintenance' block entirely or supply a valid aircraft id"
        )
    return plane


def _suggest_plane_id(candidate: str, valid_ids: Iterable[str]) -> str:
    """Return a '; did you mean X?' fragment for a near-miss id, or '' if none.

    Two passes, because ``difflib`` alone misses the headline case:
    ``SequenceMatcher`` is case-sensitive, so ``'FOO'`` vs ``'foo'`` scores
    0.0 and would yield no suggestion.

    1. Case-insensitive exact match: if exactly one valid id equals the
       candidate under ``casefold()`` (and isn't the candidate itself),
       suggest it with the case-sensitivity note. If two valid ids share a
       casefold (only possible for a fleet that deliberately uses
       case-distinct ids), the pass is ambiguous and is skipped.
    2. ``difflib.get_close_matches(n=1, cutoff=0.6)`` for genuine typos.
    """
    valid = list(valid_ids)
    folded = candidate.casefold()
    ci_matches = [v for v in valid if v.casefold() == folded and v != candidate]
    if len(ci_matches) == 1:
        return f"; did you mean {ci_matches[0]!r}? (plane ids are case-sensitive)"
    close = difflib.get_close_matches(candidate, valid, n=1, cutoff=0.6)
    if close and close[0] != candidate:
        return f"; did you mean {close[0]!r}?"
    return ""


def _resolve_known_plane_id(
    candidate: str,
    valid_ids: Collection[str],
    *,
    role: str,
    path: Path,
    fix_hint: str = "",
) -> None:
    """Raise :class:`LoaderError` if ``candidate`` is not in ``valid_ids``.

    The message is ``"{path}: {role} references unknown plane id
    {candidate!r}{tail}"`` where ``tail`` is, in priority order: a
    ``_suggest_plane_id`` fragment when there is a near match, else
    ``"; " + fix_hint`` when ``fix_hint`` is set, else empty. A near-match
    suggestion always wins over ``fix_hint`` — naming the likely-intended
    id beats generic guidance.

    This is an earlier, friendlier front door to the unknown-id checks in
    ``Layout``/``Scenario.__post_init__``; those invariants are kept as the
    backstop for callers that bypass the loader.
    """
    if candidate in valid_ids:
        return
    tail = _suggest_plane_id(candidate, valid_ids)
    if not tail and fix_hint:
        tail = f"; {fix_hint}"
    raise LoaderError(f"{path}: {role} references unknown plane id {candidate!r}{tail}")


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
    return [
        Part(  # right side
            kind="strut",
            length_m=spec.width_m,
            width_m=strut_span,
            offset_x_m=spec.fuselage_attach_x_m,
            offset_y_m=midpoint,
            angle_deg=0.0,
            z_bottom_m=spec.fuselage_attach_z_m,
            z_top_m=wing.z_bottom_m,
        ),
        Part(  # left side
            kind="strut",
            length_m=spec.width_m,
            width_m=strut_span,
            offset_x_m=spec.fuselage_attach_x_m,
            offset_y_m=-midpoint,
            angle_deg=0.0,
            z_bottom_m=spec.fuselage_attach_z_m,
            z_top_m=wing.z_bottom_m,
        ),
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
