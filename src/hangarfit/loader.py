"""YAML → typed-model loader for hangarfit.

Thin adapter layer between user-authored YAML files and the dataclasses
in :mod:`hangarfit.models`. The loader's main job, besides translating
types, is **error message quality** — a 250-line ``fleet.yaml`` is
miserable to debug if errors only say "ValueError on line ?". Every
exception raised here is a :class:`LoaderError` with the file path
and (where it makes sense) the aircraft id or field name prepended.

The loader also performs the build-time geometry that makes the
parts-model schema readable. Two high-level YAML constructs expand into
canonical low-level :class:`~hangarfit.models.Part` instances before the
:class:`Aircraft` is constructed:

1. Each strut-braced aircraft's ``struts:`` block expands into two
   mirrored strut Parts (one per side).
2. Each ``kind: fuselage`` part auto-splits into a ``fuselage_front`` +
   ``fuselage_aft`` pair at the wing trailing-edge station (ADR-0012). The
   constructed ``PartKind`` set has no ``fuselage`` member — it is a
   transient YAML keyword only. An aircraft with a ``fuselage`` part but no
   ``wing`` part is rejected: there is nothing to derive the break from.

After loading, the aircraft's ``parts`` tuple is the single source of
truth for geometry — there is no separate ``struts`` field on the
:class:`Aircraft`, and no ``fuselage`` kind survives the load.

Plane ids are **case-sensitive** and are not normalised. When a layout or
scenario names an id that does not match the fleet exactly, the loader
rejects it at parse time with a ``did you mean 'X'?`` suggestion (a
case-insensitive match, else a ``difflib`` near match) rather than letting
a mis-cased id slip through to a late, generic model-invariant error.
"""

from __future__ import annotations

import dataclasses
import difflib
import math
from collections.abc import Collection, Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

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
    StructuralNotch,
    StrutsSpec,
    Wheels,
)
from .towplanner import derive_apron_depth


class LoaderError(Exception):
    """Raised when a YAML file is malformed or fails model validation."""


def _to_float(value: Any, field_name: str) -> float:
    """Coerce a YAML scalar to ``float``, raising ``LoaderError`` with the
    field name on failure (rather than a bare ``TypeError`` from
    ``float(None)`` or ``ValueError`` from ``float("abc")``).

    Non-finite results (NaN, ±inf) are also rejected: ``yaml.safe_load``
    parses ``.nan``/``.inf``/``-.inf`` into real Python floats, and
    downstream consumers (e.g. ``_wing_spar_x``) silently propagate them
    into geometry calculations, where NaN comparisons always return False
    and inf values produce nonsensical coordinates.

    ``bool`` is rejected too: it is an ``int`` subclass, so ``float(True)`` is a
    silent ``1.0`` — the same YAML footgun ``_to_int`` / ``_to_bool`` guard
    against (``yes``/``on`` coerce to ``True`` under YAML 1.1). Without this a
    ``priority: true`` (#441) — or any ``true`` fat-fingered into a numeric
    field — would parse to a plausible-but-wrong number instead of erroring.
    """
    if value is None:
        raise LoaderError(f"{field_name!r}: expected number, got null")
    if isinstance(value, bool):
        raise LoaderError(f"{field_name!r}: expected number, got {value!r} (bool)")
    try:
        result = float(value)
    except (TypeError, ValueError) as e:
        raise LoaderError(
            f"{field_name!r}: expected number, got {value!r} ({type(value).__name__})"
        ) from e
    if not math.isfinite(result):
        raise LoaderError(f"{field_name!r}: expected a finite number, got {value!r}")
    return result


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


def _to_int(value: Any, field_name: str) -> int:
    """Coerce a YAML scalar to ``int`` strictly. Rejects ``bool`` (it is an
    ``int`` subclass, so ``True`` would silently read as ``1``) and any
    non-int value (floats, strings) so a fractional or mistyped count fails
    loudly with the field name rather than silently truncating
    (``int(1.5) == 1``)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise LoaderError(
            f"{field_name!r}: expected integer, got {value!r} ({type(value).__name__})"
        )
    return value


def _reject_unknown_top_level_keys(
    raw: Mapping[str, Any], allowed: frozenset[str], *, path: Path, block: str
) -> None:
    """Reject misspelled/unknown top-level keys in a hangar/layout/scenario file
    (#516) — the same silent-failure class the ``aircraft:`` allowlist
    (:data:`_ALLOWED_AIRCRAFT_KEYS`, #513) closes one layer down. Without it a
    typo'd top-level key (e.g. ``apron_dpeth_m:``) is silently dropped to its
    default. Raised with the ``{path}:`` prefix every top-level loader already uses.

    Each ``allowed`` set is exactly the keys its loader reads — keep the two in
    sync; the ``test_all_allowed_*_keys_load`` completeness guards fail loudly if
    a real key is ever dropped from an allowlist (the too-strict direction).
    """
    unknown = set(raw) - allowed
    if unknown:
        raise LoaderError(
            f"{path}: unknown {block} key(s) {sorted(unknown)}; allowed: {sorted(allowed)}"
        )


# A fleet manifest references per-object CATALOG files (#595). Each catalog file
# carries a `type:` discriminator (default 'aircraft') routing to a per-type
# builder. Only 'aircraft' is registered here; non-aircraft objects (fuel
# trailer, glider trailer, rescue vehicle) arrive in Stage A (#600).
_DEFAULT_OBJECT_TYPE = "aircraft"

# Operational flags a fleet manifest entry may override on a catalog object.
# Geometry is STATIC — never override-able (edit the catalog file). Keep tight.
_ALLOWED_MANIFEST_OVERRIDE_KEYS = frozenset({"movement_mode", "tow_pivotable"})


def _build_catalog_object(raw: Any, *, source: Path) -> Aircraft:
    """Dispatch a catalog object on its ``type:`` discriminator to the per-type
    builder. ``type:`` is stripped before the builder runs, so the aircraft
    allowlist (:data:`_ALLOWED_AIRCRAFT_KEYS`, which has no ``type`` member) is
    unchanged. An unregistered type is reserved for Stage A (#600)."""
    if not isinstance(raw, dict):
        raise LoaderError(f"{source}: catalog object must be a mapping, got {type(raw).__name__}")
    obj_type = raw.get("type", _DEFAULT_OBJECT_TYPE)
    if obj_type != _DEFAULT_OBJECT_TYPE:
        raise LoaderError(
            f"{source}: object type {obj_type!r} not yet supported (non-aircraft "
            f"objects arrive in Stage A, #600); known types: ['aircraft']"
        )
    entry = {k: v for k, v in raw.items() if k != "type"}
    return _build_aircraft(entry)


def _parse_manifest_entry(entry: Any, *, index: int, path: Path) -> tuple[str, dict[str, Any]]:
    """Normalise a fleet-manifest entry to ``(ref_path, overrides)``.

    - ``"catalog/x.yaml"``           -> ``(path, {})``           bare reference
    - ``{ref: p, movement_mode: …}`` -> ``(p, {allowed flags})`` reference + overrides
    - ``{id|name|parts|…}`` (no ref) -> rejected: the dropped inline-aircraft form
    """
    if isinstance(entry, str):
        return entry, {}
    if isinstance(entry, dict):
        if "ref" not in entry:
            raise LoaderError(
                f"{path}: aircraft[{index}] is an inline aircraft mapping, which is no "
                f"longer supported (#595). Move the aircraft to a catalog file "
                f"(e.g. data/catalog/<id>.yaml with `type: aircraft`) and reference it: "
                f"`- catalog/<id>.yaml` (or `- {{ref: catalog/<id>.yaml, movement_mode: …}}` "
                f"to override a per-fleet flag)."
            )
        ref = entry["ref"]
        if not isinstance(ref, str):
            raise LoaderError(
                f"{path}: aircraft[{index}].ref must be a path string, got {type(ref).__name__}"
            )
        overrides = {k: v for k, v in entry.items() if k != "ref"}
        unknown = set(overrides) - _ALLOWED_MANIFEST_OVERRIDE_KEYS
        if unknown:
            raise LoaderError(
                f"{path}: aircraft[{index}] override key(s) {sorted(unknown)} not allowed; "
                f"only per-fleet operational flags may be overridden "
                f"({sorted(_ALLOWED_MANIFEST_OVERRIDE_KEYS)}) — geometry is static, edit "
                f"the catalog file instead"
            )
        return ref, overrides
    raise LoaderError(
        f"{path}: aircraft[{index}] must be a catalog reference (a path string or a "
        f"{{ref: path, …}} mapping), got {type(entry).__name__}"
    )


def load_fleet(path: Path | str) -> dict[str, Aircraft]:
    """Load a fleet **manifest** into a dict keyed by :attr:`Aircraft.id`.

    A fleet file is a thin manifest: a top-level ``aircraft:`` list whose entries
    reference per-object **catalog** files by path (resolved relative to the
    manifest's directory, #595). Each entry is either a path string
    (``catalog/x.yaml``) or a ``{ref: <path>, <flag overrides>}`` mapping that
    adds a per-fleet operational-flag override (``movement_mode``,
    ``tow_pivotable``) on top of the shared static definition. Geometry is static
    and never override-able.

    Each referenced catalog file carries a ``type:`` discriminator (default
    ``aircraft``); the loader dispatches on it to a per-type builder. Manifest
    **list order is preserved** -> deterministic ``dict`` insertion (ADR-0003).

    Inline aircraft definitions are not supported (#595): an inline mapping under
    ``aircraft:`` is rejected with a hint to move it to a catalog file.
    """
    path = Path(path)
    raw = _read_yaml(path)
    if not isinstance(raw, dict) or "aircraft" not in raw:
        raise LoaderError(f"{path}: top-level mapping must contain 'aircraft' list")
    aircraft_list = raw["aircraft"]
    if not isinstance(aircraft_list, list):
        raise LoaderError(f"{path}: 'aircraft' must be a list")

    manifest_dir = path.parent
    fleet: dict[str, Aircraft] = {}
    for i, entry in enumerate(aircraft_list):
        # Every entry is a catalog reference (a path string or {ref, overrides});
        # inline aircraft mappings are rejected by _parse_manifest_entry (#595).
        ref, overrides = _parse_manifest_entry(entry, index=i, path=path)
        catalog_path = (manifest_dir / ref).resolve()
        if not catalog_path.is_file():
            raise LoaderError(
                f"{path}: aircraft[{i}] references catalog file {ref!r} which does "
                f"not exist (resolved to {catalog_path})"
            )
        obj_raw = _read_yaml(catalog_path)
        if isinstance(obj_raw, dict) and overrides:
            obj_raw = {**obj_raw, **overrides}
        try:
            aircraft = _build_catalog_object(obj_raw, source=catalog_path)
        except (ValueError, KeyError, TypeError, LoaderError) as e:
            raise LoaderError(f"{path}: aircraft[{i}] ({ref}): {e}") from e
        if aircraft.id in fleet:
            raise LoaderError(f"{path}: duplicate aircraft id {aircraft.id!r}")
        fleet[aircraft.id] = aircraft
    return fleet


def _resolve_apron_depth(
    value: Any, fleet: Mapping[str, Aircraft] | None, *, field_name: str = "apron_depth_m"
) -> float:
    """Resolve a raw apron-depth value to a float (ADR-0021).

    The literal ``"auto"`` derives a fleet-based depth via
    :func:`~hangarfit.towplanner.derive_apron_depth`; any other value is coerced
    with :func:`_to_float`. ``"auto"`` without a fleet is a hard error — it cannot
    be derived from nothing — so authoring ``apron_depth_m: auto`` in a bare
    ``hangar.yaml`` and loading it without a fleet (e.g. :func:`load_hangar`
    directly) fails loudly rather than silently defaulting.
    """
    if isinstance(value, str) and value.strip().lower() == "auto":
        if not fleet:
            raise LoaderError(
                f"{field_name!r}: 'auto' needs a fleet to derive the depth from. "
                f"Use it in a scenario/layout (whose fleet resolves it), pass --fleet "
                f"alongside a --hangar override, or author a numeric depth instead."
            )
        return derive_apron_depth(fleet)
    return _to_float(value, field_name)


# Strict top-level-key allowlist for a hangar.yaml (#516). Mirrors the `aircraft:`
# (:data:`_ALLOWED_AIRCRAFT_KEYS`) guard, extended to the hangar file's top level.
# The nested `door`/`maintenance_bay` blocks need no allowlist — all their keys are
# required, so a typo there already fails loudly as "missing required field"; the
# `structural_notches` rectangles carry their own per-entry allowlist (below). Keep
# in sync with the keys read in :func:`load_hangar`; ``test_all_allowed_hangar_keys_load``
# guards the too-strict direction.
_ALLOWED_HANGAR_KEYS = frozenset(
    {
        "length_m",
        "width_m",
        "door",
        "maintenance_bay",
        "structural_notches",
        "clearance_m",
        "wing_layer_clearance_m",
        "max_carts",
        "apron_depth_m",
    }
)


def load_hangar(path: Path | str, *, fleet: Mapping[str, Aircraft] | None = None) -> Hangar:
    """Load ``hangar.yaml`` into a :class:`Hangar`.

    ``fleet`` is only consulted to resolve an ``apron_depth_m: auto`` value
    (ADR-0021) into a concrete fleet-derived depth; a numeric ``apron_depth_m``
    (or its absence) needs no fleet. The scenario/layout loaders pass the fleet
    they have already resolved; a bare ``load_hangar`` call has none, so
    ``apron_depth_m: auto`` is rejected there.
    """
    path = Path(path)
    raw = _read_yaml(path)
    if not isinstance(raw, dict):
        raise LoaderError(f"{path}: top-level must be a mapping")
    _reject_unknown_top_level_keys(raw, _ALLOWED_HANGAR_KEYS, path=path, block="hangar")

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

    # Optional always-on floor keep-outs (ADR-0018). Absent — or an explicit
    # null (`structural_notches:` with no value) — ⇒ rectangular hangar, unchanged
    # behaviour (mirrors load_scenario's None→{} handling for `constraints:`).
    # Each entry is an axis-aligned rectangle in hangar coords.
    notches_data = raw.get("structural_notches")
    if notches_data is None:
        notches_data = []
    if not isinstance(notches_data, list):
        raise LoaderError(f"{path}: 'structural_notches' must be a list of rectangles")
    _notch_keys = ("x_min_m", "y_min_m", "x_max_m", "y_max_m")
    _allowed_notch_keys = frozenset(_notch_keys)
    for i, notch in enumerate(notches_data):
        if not isinstance(notch, dict):
            raise LoaderError(
                f"{path}: structural_notches[{i}] must be a mapping with {', '.join(_notch_keys)}"
            )
        # Reject unknown keys loudly — a misspelled coord must not be silently
        # dropped (same discipline as _ALLOWED_AIRCRAFT_KEYS / _parse_wheels).
        unknown = set(notch) - _allowed_notch_keys
        if unknown:
            raise LoaderError(
                f"{path}: structural_notches[{i}] has unknown key(s) {sorted(unknown)}; "
                f"allowed: {list(_notch_keys)}"
            )
        for key in _notch_keys:
            if key not in notch:
                raise LoaderError(f"{path}: missing required field 'structural_notches[{i}].{key}'")

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
            max_carts=_to_int(raw.get("max_carts", 1), "max_carts"),
            apron_depth_m=_resolve_apron_depth(raw.get("apron_depth_m", 0.0), fleet),
            structural_notches=tuple(
                StructuralNotch(
                    x_min_m=_to_float(notch["x_min_m"], f"structural_notches[{i}].x_min_m"),
                    y_min_m=_to_float(notch["y_min_m"], f"structural_notches[{i}].y_min_m"),
                    x_max_m=_to_float(notch["x_max_m"], f"structural_notches[{i}].x_max_m"),
                    y_max_m=_to_float(notch["y_max_m"], f"structural_notches[{i}].y_max_m"),
                )
                for i, notch in enumerate(notches_data)
            ),
        )
    except (ValueError, TypeError) as e:
        raise LoaderError(f"{path}: {e}") from e


# Strict top-level-key allowlist for a layout.yaml (#516). Keep in sync with the
# keys read in :func:`load_layout`; ``test_all_allowed_layout_keys_load`` guards the
# too-strict direction. ``fleet``/``hangar`` are optional in the file (they may arrive
# as kwargs instead); the file-vs-kwarg conflict is handled separately, below.
_ALLOWED_LAYOUT_KEYS = frozenset({"fleet", "hangar", "placements", "maintenance"})


def load_layout(
    path: Path | str,
    *,
    fleet: dict[str, Aircraft] | None = None,
    hangar: Hangar | None = None,
    max_carts: int | None = None,
    apron_depth: float | Literal["auto"] | None = None,
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
    _reject_unknown_top_level_keys(raw, _ALLOWED_LAYOUT_KEYS, path=path, block="layout")

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
        hangar = load_hangar((path.parent / hangar_ref).resolve(), fleet=fleet)
    elif "hangar" in raw:
        raise LoaderError(
            f"{path}: 'hangar' field is set in YAML but a hangar override was also "
            f"provided programmatically; remove one to disambiguate"
        )

    # A ``--max-carts`` override (CLI) reaches ``Layout.__post_init__`` via the
    # hangar it reads. Apply it to the resolved hangar *before* the Layout is
    # built, so a loosening override is honoured instead of being rejected at
    # construction against the data-file cap. ``replace`` re-runs
    # ``Hangar.__post_init__``, so a negative override is rejected — wrap that
    # ValueError into a LoaderError to keep the exit-2 contract (a raw
    # ValueError would crash the CLI with a traceback). See ADR-0007
    # (cart-inventory amendment) / #210.
    if max_carts is not None:
        try:
            hangar = dataclasses.replace(hangar, max_carts=max_carts)
        except ValueError as e:
            raise LoaderError(f"{path}: {e}") from e

    # A ``--apron-depth`` override (CLI) similarly reaches the planner via the
    # hangar. ``"auto"`` derives from the resolved fleet; a number is coerced.
    # ``replace`` re-runs ``Hangar.__post_init__`` so a negative value is wrapped
    # into a LoaderError, not a raw ValueError. See ADR-0021 / #412.
    if apron_depth is not None:
        resolved_apron = _resolve_apron_depth(apron_depth, fleet, field_name="apron_depth_m")
        try:
            hangar = dataclasses.replace(hangar, apron_depth_m=resolved_apron)
        except ValueError as e:
            raise LoaderError(f"{path}: {e}") from e

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


# Strict top-level-key allowlist for a scenario.yaml (#516). Keep in sync with the
# keys read in :func:`load_scenario`; ``test_all_allowed_scenario_keys_load`` guards
# the too-strict direction. Per-plane ``constraints`` entries carry their own
# allowlist (:data:`_ALLOWED_CONSTRAINT_KEYS`).
_ALLOWED_SCENARIO_KEYS = frozenset({"fleet_in", "fleet", "hangar", "maintenance", "constraints"})


def load_scenario(
    path: Path | str,
    *,
    fleet: dict[str, Aircraft] | None = None,
    hangar: Hangar | None = None,
    max_carts: int | None = None,
    apron_depth: float | Literal["auto"] | None = None,
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
    _reject_unknown_top_level_keys(raw, _ALLOWED_SCENARIO_KEYS, path=path, block="scenario")

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
        hangar = load_hangar((path.parent / hangar_ref).resolve(), fleet=fleet)
    elif "hangar" in raw:
        raise LoaderError(
            f"{path}: 'hangar' field is set in YAML but a hangar override was also "
            f"provided programmatically; remove one to disambiguate"
        )

    # A ``--max-carts`` override (CLI) is applied to the resolved hangar here;
    # the solver builds every candidate Layout from ``scenario.hangar``, so the
    # cart cap each Layout enforces is this overridden value. ``replace``
    # re-runs ``Hangar.__post_init__``; wrap a negative-value ValueError into a
    # LoaderError to keep the exit-2 contract. See ADR-0007 (cart-inventory
    # amendment) / #210.
    if max_carts is not None:
        try:
            hangar = dataclasses.replace(hangar, max_carts=max_carts)
        except ValueError as e:
            raise LoaderError(f"{path}: {e}") from e

    # A ``--apron-depth`` override (CLI) reaches the planner via ``scenario.hangar``.
    # ``"auto"`` derives from the resolved fleet; a number is coerced. ``replace``
    # re-runs ``Hangar.__post_init__`` so a negative value is wrapped into a
    # LoaderError, not a raw ValueError. See ADR-0021 / #412.
    if apron_depth is not None:
        resolved_apron = _resolve_apron_depth(apron_depth, fleet, field_name="apron_depth_m")
        try:
            hangar = dataclasses.replace(hangar, apron_depth_m=resolved_apron)
        except ValueError as e:
            raise LoaderError(f"{path}: {e}") from e

    for pid in fleet_in:
        _resolve_known_plane_id(pid, fleet, role="fleet_in entry", path=path)

    # maintenance (optional, same shape as load_layout)
    maintenance_plane = _extract_maintenance_plane(raw, path)

    # Shared "did you mean / else add it to fleet_in" guidance for the two
    # ids validated against fleet_in (maintenance plane + constraint keys).
    # Built once so the two call sites can't drift apart during message tuning.
    fleet_in_fix_hint = f"either add it to fleet_in {sorted(fleet_in)} or fix the plane id"

    # Pre-Scenario boundary check: surface the YAML-author error with a
    # path prefix here instead of relying on Scenario.__post_init__'s
    # bare ValueError bubbling through the except ValueError → LoaderError
    # wrap below (which would drop the actionable fleet_in hint).
    if maintenance_plane is not None:
        _resolve_known_plane_id(
            maintenance_plane,
            fleet_in,
            role="maintenance.plane",
            path=path,
            fix_hint=fleet_in_fix_hint,
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
        _resolve_known_plane_id(
            plane_id,
            fleet_in,
            role="constraints key",
            path=path,
            fix_hint=fleet_in_fix_hint,
        )
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


_ALLOWED_CONSTRAINT_KEYS = frozenset({"pin", "force_on_carts", "priority", "nose_out"})


def _build_plane_constraint(plane_id: str, data: Any) -> PlaneConstraint:
    """Build a :class:`PlaneConstraint` from one entry in a scenario YAML
    ``constraints:`` block.

    YAML schema accepted:

    .. code-block:: yaml

        constraints:
          <plane_id>:
            pin: { x_m: <float>, y_m: <float>, heading_deg: <float>, on_carts: <bool> }
            force_on_carts: <bool>
            priority: <float>   # soft, >= 0 (#441)
            nose_out: <bool>    # soft tri-state; omit ⇒ follow global (#263)

    ``pin``, ``force_on_carts`` and ``priority`` are all optional. Omitting all
    yields a "free" constraint (the solver may place the plane anywhere
    within physical / cart-rule limits). ``priority`` is a soft spread weight
    (see :class:`~hangarfit.models.PlaneConstraint`); its range is validated by
    ``Scenario.__post_init__``.

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

    # Strict unknown-key allowlist (mirrors the `wheels:` block). Without it a
    # misspelled key is silently dropped — harmless for pin/force_on_carts/priority
    # (whose absence means "free"), but for `nose_out` a silent drop INVERTS intent:
    # its None means "follow the global SearchConfig.nose_out" (default ON), so a
    # fat-fingered nose-IN exemption (`nose_out: false`) would silently flip the
    # plane nose-OUT (#263). Reject loudly instead.
    unknown = set(data) - _ALLOWED_CONSTRAINT_KEYS
    if unknown:
        raise LoaderError(
            f"unknown constraint key(s) {sorted(unknown)}; "
            f"allowed: {sorted(_ALLOWED_CONSTRAINT_KEYS)}"
        )

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

    # Soft per-plane spread weight (#441). Range/finiteness is enforced by
    # Scenario.__post_init__ (alongside pin/force_on_carts validation).
    priority = data.get("priority")
    if priority is not None:
        priority = _to_float(priority, "priority")

    # Per-plane nose-out override (#263). Tri-state: None ⇒ follow the global
    # SearchConfig.nose_out; True ⇒ prefer-out; False ⇒ never flip (nose-IN
    # exemption). Strict bool coercion, like force_on_carts.
    nose_out = data.get("nose_out")
    if nose_out is not None:
        nose_out = _to_bool(nose_out, "nose_out")

    return PlaneConstraint(
        pin=pin, force_on_carts=force_on_carts, priority=priority, nose_out=nose_out
    )


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
       case-distinct ids), the pass is ambiguous and is skipped — in which
       case difflib may still return a suggestion (without the note) for a
       near-enough candidate.
    2. ``difflib.get_close_matches(n=1, cutoff=0.6)`` for genuine typos.

    Inputs are coerced to ``str`` defensively: callers pass fleet keys /
    ``fleet_in`` entries that *should* be str, but a malformed ``fleet.yaml``
    can carry an unquoted numeric/bool id (e.g. ``id: 1`` → ``int``) that
    survives loading. This helper only produces a best-effort hint, so a
    non-str id must degrade to "no suggestion", never an ``AttributeError``.
    """
    cand = str(candidate)
    valid = [str(v) for v in valid_ids]
    folded = cand.casefold()
    ci_matches = [v for v in valid if v.casefold() == folded and v != cand]
    if len(ci_matches) == 1:
        return f"; did you mean {ci_matches[0]!r}? (plane ids are case-sensitive)"
    close = difflib.get_close_matches(cand, valid, n=1, cutoff=0.6)
    if close and close[0] != cand:
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


_WHEELS_KEYS_BY_GEAR: dict[str, frozenset[str]] = {
    "monowheel": frozenset({"main_offset_x_m"}),
    "nosewheel": frozenset({"main_offset_x_m", "track_m", "third_wheel_offset_x_m"}),
    "tailwheel": frozenset({"main_offset_x_m", "track_m", "third_wheel_offset_x_m"}),
}


def _parse_wheels(
    entry: Mapping[str, Any] | None,
    gear: str,
) -> Wheels:
    """Parse a ``wheels:`` block into a :class:`Wheels`.

    Raises ``LoaderError`` when ``entry`` is ``None`` — every aircraft
    must declare a ``wheels:`` block (ADR-0013).

    Validates that the key set exactly matches ``_WHEELS_KEYS_BY_GEAR[gear]``
    and that nose-vs-tail sign rules hold for tricycle/tailwheel gear.

    Errors are raised without an ``aircraft 'id': ...`` prefix — the outer
    ``load_fleet`` loop wraps every per-aircraft error with that attribution
    (see :func:`load_fleet`), so adding it here would double-decorate.
    """
    if entry is None:
        raise LoaderError("wheels: block is required (see fleet.yaml header for the schema)")

    if not isinstance(entry, Mapping):
        # YAML can hand us a list/scalar/string here (e.g. a mis-indented
        # block). Guard before .keys() so the user gets an attributed
        # LoaderError, not a bare AttributeError that escapes load_fleet's
        # catch tuple — mirrors the isinstance(dict) guard on 'struts'.
        raise LoaderError(f"wheels: block must be a mapping, got {type(entry).__name__}")

    if gear not in _WHEELS_KEYS_BY_GEAR:
        raise LoaderError(f"wheels: unsupported gear {gear!r}")

    expected = _WHEELS_KEYS_BY_GEAR[gear]
    seen = frozenset(entry.keys())
    missing = expected - seen
    unknown = seen - expected
    if missing:
        raise LoaderError(
            f"wheels: block missing required key(s) for gear={gear!r}: {sorted(missing)}"
        )
    if unknown:
        if gear == "monowheel" and unknown & {"track_m", "third_wheel_offset_x_m"}:
            raise LoaderError(
                f"wheels: monowheel block must not set track_m or "
                f"third_wheel_offset_x_m (got {sorted(unknown)})"
            )
        raise LoaderError(f"wheels: block has unknown key(s): {sorted(unknown)}")

    main_offset_x_m = _to_float(entry["main_offset_x_m"], "wheels.main_offset_x_m")
    if gear == "monowheel":
        # _to_float already rejects non-finite; Wheels.__post_init__'s only other
        # ValueError path for monowheel is also a finiteness check, so no wrap needed.
        return Wheels(main_offset_x_m=main_offset_x_m, track_m=None, third_wheel_offset_x_m=None)

    track_m = _to_float(entry["track_m"], "wheels.track_m")
    third = _to_float(entry["third_wheel_offset_x_m"], "wheels.third_wheel_offset_x_m")
    # Sign rule:
    #   nosewheel → third (nose) must be forward of mains (greater x)
    #   tailwheel → third (tail) must be aft of mains    (lesser x)
    if gear == "nosewheel" and third <= main_offset_x_m:
        raise LoaderError(
            f"wheels: nosewheel third_wheel_offset_x_m must be forward of mains "
            f"(greater than main_offset_x_m={main_offset_x_m}); got {third}"
        )
    if gear == "tailwheel" and third >= main_offset_x_m:
        raise LoaderError(
            f"wheels: tailwheel third_wheel_offset_x_m must be aft of mains "
            f"(less than main_offset_x_m={main_offset_x_m}); got {third}"
        )
    try:
        # Wheels.__post_init__ still validates `track_m > 0` — wrap so the loader
        # surfaces a LoaderError rather than leaking ValueError to the caller.
        return Wheels(
            main_offset_x_m=main_offset_x_m,
            track_m=track_m,
            third_wheel_offset_x_m=third,
        )
    except ValueError as exc:
        raise LoaderError(f"wheels: {exc}") from exc


# Plausibility band for turn_radius_m against the wheel-derived wheelbase.
# Deliberately loose (0.5×–5×): a sanity guard against a fat-fingered radius or
# wheel coordinate, NOT a derivation. turn_radius_m stays an empirical value
# (ADR-0013). Most fleet entries are still ``measured: false`` estimates, so a
# tight band would produce false positives.
_WHEELBASE_BAND_LOW = 0.5
_WHEELBASE_BAND_HIGH = 5.0


def _validate_wheels_vs_turn_radius(aircraft: Aircraft) -> None:
    """Raise ``LoaderError`` if turn_radius_m is implausible vs the wheelbase.

    Skipped whenever ``turn_radius_m`` is ``None`` (every always_cart entry
    today, though the model permits a non-None value there too — which would
    then be checked) and for monowheel (no ``wheelbase_m``). Cart-eligible
    planes carry a real radius and wheelbase, so they ARE checked. The message
    carries no ``aircraft 'id'`` prefix — the
    ``load_fleet`` loop wraps per-aircraft errors with that attribution, so
    adding it here would double-decorate (mirrors :func:`_parse_wheels`).
    See ADR-0013 for the rationale on the loose band.
    """
    if aircraft.turn_radius_m is None:
        return
    wheelbase = aircraft.wheels.wheelbase_m
    if wheelbase is None:
        return
    low = _WHEELBASE_BAND_LOW * wheelbase
    high = _WHEELBASE_BAND_HIGH * wheelbase
    r = aircraft.turn_radius_m
    if not (low <= r <= high):
        raise LoaderError(
            f"turn_radius_m={r} is implausible given wheelbase={wheelbase:.2f}m "
            f"(expected {low:.2f}..{high:.2f}). Either fix the wheel positions "
            f"or fix turn_radius_m."
        )


# Strict unknown-key allowlist for an `aircraft:` entry (#513). Mirrors the
# `wheels:` (:func:`_parse_wheels`) and constraint-key (:data:`_ALLOWED_CONSTRAINT_KEYS`)
# guards. These are the YAML *schema* keys — note `struts` is a YAML-only convenience
# block (expanded into `parts` by :func:`_expand_struts`), NOT an Aircraft field, so the
# allowlist is the accepted-input set, not the dataclass fields. Without it a misspelled
# key is silently dropped to its default: e.g. `tow_pivot: true` (the #511/#263 widening)
# parses as `tow_pivotable=False`, silently denying the pivot capability the author tried
# to grant. Keep in sync with the keys read in :func:`_build_aircraft`;
# ``test_all_allowed_aircraft_keys_load`` guards the too-strict direction.
_ALLOWED_AIRCRAFT_KEYS = frozenset(
    {
        "id",
        "name",
        "wing_position",
        "gear",
        "movement_mode",
        "turn_radius_m",
        "measured",
        "parts",
        "wheels",
        "notes",
        "struts",
        "tow_pivotable",
    }
)


def _build_aircraft(entry: Any) -> Aircraft:
    if not isinstance(entry, dict):
        raise LoaderError(f"aircraft entry must be a mapping, got {type(entry).__name__}")

    # Reject unknown/misspelled keys loudly (#513) before any field is read, so a
    # typo'd required key (e.g. `nam:`) surfaces as the offending key rather than a
    # downstream "missing required field" for the key the author thought they spelled.
    unknown = set(entry) - _ALLOWED_AIRCRAFT_KEYS
    if unknown:
        raise LoaderError(
            f"unknown aircraft key(s) {sorted(unknown)}; allowed: {sorted(_ALLOWED_AIRCRAFT_KEYS)}"
        )

    required = ("id", "name", "wing_position", "gear", "movement_mode")
    for key in required:
        if key not in entry:
            raise LoaderError(f"missing required field {key!r}")

    parts_data = entry.get("parts")
    if not isinstance(parts_data, list) or not parts_data:
        raise LoaderError("'parts' must be a non-empty list")
    # First pass: build every part as a canonical Part. A ``kind: fuselage``
    # entry is NOT a constructed PartKind (ADR-0012) — it is field-validated
    # under a placeholder kind (any valid non-fuselage kind works; we use
    # ``"fuselage_aft"`` so the ordinary z-gap invariants apply), then held
    # aside for the front/aft split below. The placeholder is replaced
    # wholesale by ``_split_fuselage`` and never reaches the constructed
    # Aircraft. If the box is malformed, rename the placeholder back to the
    # user-authored ``fuselage`` in the message — they never typed
    # ``"fuselage_aft"``, so naming it would be a debugging dead-end (#50 review).
    parts: list[Part] = []
    fuselage_markers: list[tuple[Part, int]] = []
    for i, p in enumerate(parts_data):
        if isinstance(p, dict) and p.get("kind") == "fuselage":
            try:
                marker = _build_part({**p, "kind": "fuselage_aft"}, i)
            except ValueError as e:
                raise ValueError(str(e).replace("'fuselage_aft'", "'fuselage'")) from e
            fuselage_markers.append((marker, i))
        else:
            parts.append(_build_part(p, i))

    # The fuselage front/aft break and the strut spar axis are both derived
    # from the wing chord. If an aircraft declares multiple wings (unusual:
    # split-wing / twin-boom) the FIRST wins — a deliberate, test-pinned
    # convention (``test_first_wing_part_drives_strut_z_top``) that this split
    # now also rides on. (#50 review flagged that wing order now also affects
    # the front/aft cut, hence the collision verdict; first-wins is intentional
    # and consistent with the existing strut rule — revisit only if a real
    # multi-wing airframe is ever added.)
    wing = next((p for p in parts if p.kind == "wing"), None)

    if "struts" in entry:
        if not isinstance(entry["struts"], dict):
            raise LoaderError("'struts' must be a mapping")
        spec = _build_struts_spec(entry["struts"])
        if wing is None:
            raise LoaderError("'struts' block requires a part of kind 'wing'")
        parts.extend(_expand_struts(spec, wing))

    # Second pass: auto-split each legacy ``kind: fuselage`` part into a
    # front/aft pair at the wing trailing-edge station (ADR-0012). Mirrors the
    # ``struts:`` expansion idiom — a high-level YAML convenience expanded into
    # canonical Parts, with the parts tuple as the single source of truth. The
    # no-wing rejection fires only for a well-formed fuselage (field errors
    # above take precedence) since the break station is derived from the wing.
    for fuselage_part, findex in fuselage_markers:
        if wing is None:
            raise LoaderError(
                f"parts[{findex}] kind 'fuselage' requires a part of kind 'wing' "
                f"on the same aircraft: the front/aft section break is derived "
                f"from the wing trailing-edge station. Either add a wing part or "
                f"declare explicit 'fuselage_front'/'fuselage_aft' parts."
            )
        parts.extend(_split_fuselage(fuselage_part, wing))

    turn_radius_raw = entry.get("turn_radius_m")
    turn_radius_m = None if turn_radius_raw is None else _to_float(turn_radius_raw, "turn_radius_m")

    aircraft = Aircraft(
        id=entry["id"],
        name=entry["name"],
        wing_position=entry["wing_position"],
        gear=entry["gear"],
        movement_mode=entry["movement_mode"],
        turn_radius_m=turn_radius_m,
        measured=_to_bool(entry.get("measured", False), "measured"),
        tow_pivotable=_to_bool(entry.get("tow_pivotable", False), "tow_pivotable"),
        parts=tuple(parts),
        notes=entry.get("notes", ""),
        wheels=_parse_wheels(entry.get("wheels"), entry["gear"]),
    )
    _validate_wheels_vs_turn_radius(aircraft)
    return aircraft


# Strict unknown-key allowlist for a `parts[i]` entry, mirroring
# _ALLOWED_AIRCRAFT_KEYS. `planform` is a YAML-only convenience block expanded
# into Part.local_vertices by _build_planform (NOT a Part field). Without this,
# a typo like `planfrm:` would be silently dropped and the wing would stay a
# rectangle with no error.
_ALLOWED_PART_KEYS = frozenset(
    {
        "kind",
        "length_m",
        "width_m",
        "offset_x_m",
        "offset_y_m",
        "angle_deg",
        "z_bottom_m",
        "z_top_m",
        "planform",
    }
)


def _build_planform(
    data: Any, span_m: float, length_m: float, index: int
) -> tuple[tuple[float, float], ...]:
    """Expand a parametrized symmetric double-taper wing into part-own vertices.

    Convention (ADR-0024): no sweep, root kink at y=0. In the part's own frame
    ``+x`` is the chord (forward = leading edge), and ``width_m`` is the span
    running along ``+-y``. Produces a 6-vertex hexagon; the chord at the root
    (y=0) is ``root_chord_m`` and at each tip (y=+-span/2) is ``tip_chord_m``.
    Part.__post_init__ canonicalizes the ring and enforces the bbox subset.
    """
    if not isinstance(data, dict):
        raise LoaderError(f"parts[{index}].planform must be a mapping")
    required = ("root_chord_m", "tip_chord_m")
    for key in required:
        if key not in data:
            raise LoaderError(f"parts[{index}].planform missing required field {key!r}")
    unknown = set(data) - set(required)
    if unknown:
        raise LoaderError(f"parts[{index}].planform has unknown key(s) {sorted(unknown)}")
    root = _to_float(data["root_chord_m"], f"parts[{index}].planform.root_chord_m")
    tip = _to_float(data["tip_chord_m"], f"parts[{index}].planform.tip_chord_m")
    if root <= 0 or tip <= 0:
        raise LoaderError(
            f"parts[{index}].planform chords must be positive, got root={root}, tip={tip}"
        )
    if tip > root:
        raise LoaderError(
            f"parts[{index}].planform tip_chord_m ({tip}) must not exceed root_chord_m "
            f"({root}) — a wing does not taper outward"
        )
    if root > length_m:
        raise LoaderError(
            f"parts[{index}].planform root_chord_m ({root}) must not exceed the part "
            f"length_m ({length_m}); the planform must fit the part bbox"
        )
    half_span = span_m / 2.0
    hr = root / 2.0
    ht = tip / 2.0
    return (
        (hr, 0.0),
        (ht, half_span),
        (-ht, half_span),
        (-hr, 0.0),
        (-ht, -half_span),
        (ht, -half_span),
    )


def _build_part(data: Any, index: int) -> Part:
    if not isinstance(data, dict):
        raise LoaderError(f"parts[{index}] must be a mapping")
    unknown = set(data) - _ALLOWED_PART_KEYS
    if unknown:
        raise LoaderError(
            f"parts[{index}] has unknown key(s) {sorted(unknown)}; "
            f"allowed: {sorted(_ALLOWED_PART_KEYS)}"
        )
    required = ("kind", "length_m", "width_m", "z_bottom_m", "z_top_m")
    for key in required:
        if key not in data:
            raise LoaderError(f"parts[{index}] missing required field {key!r}")
    if "planform" in data and data["kind"] != "wing":
        raise LoaderError(
            f"parts[{index}]: planform: is only valid on a kind 'wing' part, "
            f"got kind {data['kind']!r}"
        )
    width_m = _to_float(data["width_m"], f"parts[{index}].width_m")
    length_m = _to_float(data["length_m"], f"parts[{index}].length_m")
    local_vertices = None
    if "planform" in data:
        local_vertices = _build_planform(data["planform"], width_m, length_m, index)
    return Part(
        kind=data["kind"],
        length_m=length_m,
        width_m=width_m,
        offset_x_m=_to_float(data.get("offset_x_m", 0.0), f"parts[{index}].offset_x_m"),
        offset_y_m=_to_float(data.get("offset_y_m", 0.0), f"parts[{index}].offset_y_m"),
        angle_deg=_to_float(data.get("angle_deg", 0.0), f"parts[{index}].angle_deg"),
        z_bottom_m=_to_float(data["z_bottom_m"], f"parts[{index}].z_bottom_m"),
        z_top_m=_to_float(data["z_top_m"], f"parts[{index}].z_top_m"),
        local_vertices=local_vertices,
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
    # Reject unknown/misspelled nested keys (#513), mirroring the `wheels:` block
    # (:func:`_parse_wheels`). All struts keys are required, so a typo of one fails
    # loudly above as "missing"; this additionally catches a misspelled near-duplicate
    # (e.g. `wing_atttach_y_m:` alongside a correct key) that would otherwise be dropped.
    unknown = set(data) - set(required)
    if unknown:
        raise LoaderError(f"'struts' block has unknown key(s): {sorted(unknown)}")
    return StrutsSpec(
        fuselage_attach_x_m=_to_float(data["fuselage_attach_x_m"], "struts.fuselage_attach_x_m"),
        fuselage_attach_y_m=_to_float(data["fuselage_attach_y_m"], "struts.fuselage_attach_y_m"),
        fuselage_attach_z_m=_to_float(data["fuselage_attach_z_m"], "struts.fuselage_attach_z_m"),
        wing_attach_y_m=_to_float(data["wing_attach_y_m"], "struts.wing_attach_y_m"),
        width_m=_to_float(data["width_m"], "struts.width_m"),
    )


# Fraction of the wing chord, measured aft of the leading edge, at which the
# main (front) wing spar — and therefore the strut's wing attachment — sits.
# A lift strut on a strut-braced high-wing foots to the front/main spar, which
# on a typical light aircraft is near the quarter-chord. See issue #282.
_SPAR_CHORD_FRACTION = 0.25
assert 0.0 < _SPAR_CHORD_FRACTION < 1.0, (
    "spar chord fraction must lie strictly inside the chord (0,1)"
)


def _wing_spar_x(wing: Part) -> float:
    """Longitudinal (plane-local x) station of the wing's main spar.

    In plane-local coords ``+x`` is forward (toward the nose), so the wing's
    chord runs along x: it spans ``[offset_x_m - length_m/2, offset_x_m +
    length_m/2]`` with the **leading edge** at the forward (``+x``) end
    (``offset_x_m + length_m/2``) and the **trailing edge** aft
    (``offset_x_m - length_m/2``). The main spar sits ``_SPAR_CHORD_FRACTION``
    of the chord aft of the leading edge:

        spar_x = leading_edge − fraction·chord
               = (offset_x_m + length_m/2) − fraction·length_m
               = offset_x_m + (0.5 − fraction)·length_m

    With the default quarter-chord fraction this is ``offset_x_m +
    length_m/4`` — forward of the wing centre, never at the trailing edge.
    """
    return wing.offset_x_m + (0.5 - _SPAR_CHORD_FRACTION) * wing.length_m


def _expand_struts(spec: StrutsSpec, wing: Part) -> list[Part]:
    """Build two mirrored strut Parts from a StrutsSpec + wing.

    Each strut is modelled in plan view as a thin oriented rectangle
    running outboard from the fuselage attach (y = ``fuselage_attach_y_m``)
    to the wing attach (y = ``wing_attach_y_m``). Its longitudinal (x)
    station is anchored to the **wing spar axis** (:func:`_wing_spar_x`),
    NOT to ``spec.fuselage_attach_x_m`` — in the placeholder fleet the
    latter sits at the wing trailing edge, ~0.6–0.7 m aft of the spar,
    which mis-places the strut keep-out the collision checker consumes
    (issue #282). The strut stays spanwise (``angle_deg=0``); the
    fix does not rake it (that is the heavier fix option 2). The strut's
    z-range is ``[fuselage_attach_z_m, wing.z_bottom_m]`` — i.e. from the
    lower fuselage attach point up to the wing underside.
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
    spar_x = _wing_spar_x(wing)
    return [
        Part(  # right side
            kind="strut",
            length_m=spec.width_m,
            width_m=strut_span,
            offset_x_m=spar_x,
            offset_y_m=midpoint,
            angle_deg=0.0,
            z_bottom_m=spec.fuselage_attach_z_m,
            z_top_m=wing.z_bottom_m,
        ),
        Part(  # left side
            kind="strut",
            length_m=spec.width_m,
            width_m=strut_span,
            offset_x_m=spar_x,
            offset_y_m=-midpoint,
            angle_deg=0.0,
            z_bottom_m=spec.fuselage_attach_z_m,
            z_top_m=wing.z_bottom_m,
        ),
    ]


def _wing_trailing_edge_x(wing: Part) -> float:
    """Plane-local x station of the wing **trailing** edge.

    In plane-local coords ``+x`` is forward (toward the nose), so the wing
    chord spans ``[offset_x_m − length_m/2, offset_x_m + length_m/2]`` with the
    leading edge forward (``+x``) and the trailing edge aft. The fuselage
    front/aft section break is anchored here (ADR-0012): everything forward of
    the wing trailing edge — the cockpit and main spar — is ``fuselage_front``;
    the cabin-aft tube and empennage are ``fuselage_aft``. This is the same
    "anchor geometry to the wing chord, not a hand-typed station" precedent the
    strut spar axis uses (:func:`_wing_spar_x`, #282).
    """
    return wing.offset_x_m - wing.length_m / 2.0


def _split_fuselage(fuselage: Part, wing: Part) -> list[Part]:
    """Split one full fuselage :class:`Part` into a front/aft pair.

    ``fuselage`` is the already-field-validated full-fuselage box (built from
    a ``kind: fuselage`` YAML entry under a placeholder kind in
    :func:`_build_aircraft`). The break station is derived from the aircraft's
    own ``wing`` part — the wing trailing edge
    ``x_break = wing.offset_x_m − wing.length_m/2`` (:func:`_wing_trailing_edge_x`,
    ADR-0012). There is no ``wing_root_x_m`` YAML field; the break is always
    derived.

    The split is **area-conserving**: both segments inherit the source
    fuselage's ``width_m``, ``z_bottom_m``, ``z_top_m``, ``angle_deg`` and
    ``offset_y_m``; they abut at ``x_break`` and their union reconstitutes the
    original footprint exactly (no gap, no overlap). Let the source fuselage
    span ``x ∈ [c − L/2, c + L/2]``:

    - ``fuselage_front`` spans ``[x_break, c + L/2]`` (nose side),
    - ``fuselage_aft`` spans ``[c − L/2, x_break]`` (tail side),

    each with ``offset_x_m`` at the midpoint of its span and ``length_m`` its
    span width.

    Raises :class:`LoaderError` if the derived break does not lie strictly
    inside the fuselage span (the wing trailing edge is forward of the nose or
    aft of the tail) — a degenerate split that would produce a zero- or
    negative-length segment.
    """
    if fuselage.local_vertices is not None:
        raise LoaderError(
            "a fuselage part may not carry a polygon footprint (local_vertices); "
            "polygon footprints are wing-only"
        )
    c = fuselage.offset_x_m
    half_len = fuselage.length_m / 2.0
    nose_x = c + half_len  # forward tip (+x)
    tail_x = c - half_len  # aft tip (−x)
    x_break = _wing_trailing_edge_x(wing)

    if not (tail_x < x_break < nose_x):
        raise LoaderError(
            f"kind 'fuselage': derived front/aft section break x={x_break:g} "
            f"(wing trailing edge) must lie strictly inside the fuselage span "
            f"[{tail_x:g}, {nose_x:g}]. The break must be strictly inside the "
            f"span (a break at or beyond a tip would yield a zero-length "
            f"segment); check the wing offset_x_m / length_m or declare "
            f"explicit 'fuselage_front'/'fuselage_aft' parts."
        )

    front_len = nose_x - x_break
    aft_len = x_break - tail_x
    return [
        Part(
            kind="fuselage_front",
            length_m=front_len,
            width_m=fuselage.width_m,
            offset_x_m=(x_break + nose_x) / 2.0,
            offset_y_m=fuselage.offset_y_m,
            angle_deg=fuselage.angle_deg,
            z_bottom_m=fuselage.z_bottom_m,
            z_top_m=fuselage.z_top_m,
        ),
        Part(
            kind="fuselage_aft",
            length_m=aft_len,
            width_m=fuselage.width_m,
            offset_x_m=(tail_x + x_break) / 2.0,
            offset_y_m=fuselage.offset_y_m,
            angle_deg=fuselage.angle_deg,
            z_bottom_m=fuselage.z_bottom_m,
            z_top_m=fuselage.z_top_m,
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
    except UnicodeDecodeError as e:
        raise LoaderError(f"{path}: file is not valid UTF-8: {e}") from e
    except yaml.YAMLError as e:
        raise LoaderError(f"{path}: YAML parse error: {e}") from e
