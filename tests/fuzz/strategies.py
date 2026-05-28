"""Hypothesis strategies + run-helpers for fuzzing the hangarfit YAML loader.

Shared by the pytest property suite (``test_loader_fuzz.py``) and the Atheris
bridge harness (``atheris_loader_harness.py``) so input construction lives in
exactly one place. Each ``run_*`` helper encodes the loader contract: the
loader must either return normally (a model object or a fleet dict) or raise
``LoaderError``; any other exception propagates and is reported as a fuzz
finding.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml
from hypothesis import strategies as st

from hangarfit import loader
from hangarfit.loader import LoaderError
from hangarfit.models import Aircraft, Door, Hangar, MaintenanceBay, Part, Wheels

# --- valid in-memory fixtures used as fleet/hangar overrides for layout/scenario.
# Built directly with model constructors so they pass __post_init__; this lets
# the fuzzer concentrate on placement/constraint logic instead of re-fuzzing
# fleet/hangar path resolution.
VALID_HANGAR = Hangar(
    length_m=40.0,
    width_m=20.0,
    door=Door(center_x_m=10.0, width_m=8.0),
    maintenance_bay=MaintenanceBay(center_x_m=10.0, width_m=6.0, depth_m=5.0),
    clearance_m=0.3,
    wing_layer_clearance_m=0.2,
)
_FUSELAGE = Part(
    kind="fuselage_aft",
    length_m=6.0,
    width_m=1.2,
    offset_x_m=0.0,
    offset_y_m=0.0,
    angle_deg=0.0,
    z_bottom_m=0.0,
    z_top_m=2.0,
)
VALID_FLEET: dict[str, Aircraft] = {
    "p1": Aircraft(
        id="p1",
        name="Plane One",
        wing_position="high",
        gear="nosewheel",
        movement_mode="always_cart",
        turn_radius_m=None,
        measured=False,
        parts=(_FUSELAGE,),
        wheels=Wheels(main_offset_x_m=0.0, track_m=1.8, third_wheel_offset_x_m=2.0),
    ),
    "p2": Aircraft(
        id="p2",
        name="Plane Two",
        wing_position="low",
        gear="tailwheel",
        movement_mode="always_cart",
        turn_radius_m=None,
        measured=False,
        parts=(_FUSELAGE,),
        wheels=Wheels(main_offset_x_m=0.0, track_m=1.8, third_wheel_offset_x_m=-2.0),
    ),
}

# --- primitive adversarial strategies ---
# Only UTF-8-encodable characters: generated text is dumped to a YAML file and
# read back with encoding="utf-8"; lone surrogates would crash the writer, not
# the loader, producing false findings.
_safe_text = st.text(st.characters(codec="utf-8"), max_size=20)
_numbers = st.one_of(
    st.floats(allow_nan=True, allow_infinity=True),
    st.integers(min_value=-1000, max_value=1000),
)
# A scalar that might land where a number / bool / string is expected.
_scalars = st.one_of(st.none(), st.booleans(), _numbers, _safe_text)


def _enum_or_garbage(valid: list[str]) -> st.SearchStrategy[Any]:
    return st.one_of(st.sampled_from(valid), _safe_text, st.none(), st.integers())


# Plane ids: mostly real ids + near-misses so resolution / difflib paths run.
_plane_ids = st.one_of(
    st.sampled_from(["p1", "p2"]),
    st.sampled_from(["P1", "p3", "plane_one", ""]),
    _safe_text,
)


@st.composite
def _maybe_drop_keys(draw: Any, doc_strategy: st.SearchStrategy[Any]) -> Any:
    """Randomly omit a subset of a dict's keys to exercise missing-key guards,
    while usually leaving documents deep enough to reach inner loader logic."""
    doc = draw(doc_strategy)
    if isinstance(doc, dict) and doc and draw(st.booleans()):
        drop = draw(st.sets(st.sampled_from(sorted(doc)), max_size=len(doc)))
        return {k: v for k, v in doc.items() if k not in drop}
    return doc


# --- valid-biased primitives + well-formed generators ---
# The adversarial branches above test the loader's perimeter guards (type
# coercion, missing keys). These well-formed branches exist so the fuzzer
# also REACHES the deep logic — strut expansion and the model constructors
# (where model __post_init__ invariants + the loader's ValueError->LoaderError
# wrap live), which the all-noise distribution reaches ~0% of the time.
_finite = st.floats(min_value=-1e4, max_value=1e4, allow_nan=False, allow_infinity=False)
_positive = st.floats(min_value=0.1, max_value=1e3, allow_nan=False, allow_infinity=False)
# Bool fields, including the quoted-string YAML footgun _to_bool() rejects.
_bool_garbage = st.one_of(
    st.booleans(),
    st.sampled_from(["true", "false", "yes", "no", "1", "0"]),
    st.none(),
    st.integers(),
)


def _valid_part_doc(kind: str) -> st.SearchStrategy[Any]:
    """A part dict that satisfies Part.__post_init__ (positive dims, z_top>z_bottom>=0)."""
    return st.builds(
        lambda length, width, zb, dz: {
            "kind": kind,
            "length_m": length,
            "width_m": width,
            "z_bottom_m": zb,
            "z_top_m": zb + dz,
        },
        _positive,
        _positive,
        st.floats(min_value=0.0, max_value=5.0, allow_nan=False, allow_infinity=False),
        _positive,
    )


@st.composite
def _well_formed_aircraft(draw: Any, *, with_struts: bool) -> dict[str, Any]:
    """An aircraft dict that builds a valid Aircraft; with_struts also exercises
    _expand_struts (wing above the fuselage attach, strictly positive span)."""
    fus_z = draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False))
    parts: list[dict[str, Any]] = [draw(_valid_part_doc("fuselage"))]
    entry: dict[str, Any] = {
        "id": draw(
            st.one_of(
                st.sampled_from(["p1", "p2"]),
                st.text(st.characters(codec="utf-8"), min_size=1, max_size=8),
            )
        ),
        "name": draw(st.text(st.characters(codec="utf-8"), min_size=1, max_size=10)),
        "wing_position": draw(st.sampled_from(["high", "mid", "low"])),
        "gear": draw(st.sampled_from(["tailwheel", "nosewheel", "monowheel"])),
        "movement_mode": "always_cart",  # avoids the turn_radius requirement
        "measured": draw(st.booleans()),
        "parts": parts,
    }
    if with_struts:
        wing_zb = draw(
            st.floats(
                min_value=fus_z + 0.5, max_value=fus_z + 3.0, allow_nan=False, allow_infinity=False
            )
        )
        parts.append(
            {
                "kind": "wing",
                "length_m": draw(_positive),
                "width_m": draw(_positive),
                "z_bottom_m": wing_zb,  # strictly above fus_z -> passes _expand_struts guard
                "z_top_m": wing_zb + draw(_positive),
            }
        )
        fus_y = draw(st.floats(min_value=0.0, max_value=0.5, allow_nan=False, allow_infinity=False))
        wing_y = draw(
            st.floats(
                min_value=fus_y + 0.5, max_value=fus_y + 3.0, allow_nan=False, allow_infinity=False
            )
        )
        entry["struts"] = {
            "fuselage_attach_x_m": draw(_finite),
            "fuselage_attach_y_m": fus_y,
            "fuselage_attach_z_m": fus_z,
            "wing_attach_y_m": wing_y,  # > fus_y -> strictly positive span
            "width_m": draw(_positive),
        }
    return entry


def _well_formed_fleet_doc() -> st.SearchStrategy[Any]:
    return st.builds(
        lambda planes: {"aircraft": planes},
        st.lists(
            st.one_of(
                _well_formed_aircraft(with_struts=False),
                _well_formed_aircraft(with_struts=True),
            ),
            min_size=1,
            max_size=3,
        ),
    )


@st.composite
def _well_formed_hangar_doc(draw: Any) -> dict[str, Any]:
    """A hangar dict that builds a valid Hangar (door & bay fit, depth < length)."""
    width = draw(st.floats(min_value=10.0, max_value=50.0, allow_nan=False, allow_infinity=False))
    length = draw(st.floats(min_value=10.0, max_value=80.0, allow_nan=False, allow_infinity=False))
    dw = draw(st.floats(min_value=1.0, max_value=width, allow_nan=False, allow_infinity=False))
    dcx = draw(
        st.floats(min_value=dw / 2, max_value=width - dw / 2, allow_nan=False, allow_infinity=False)
    )
    bw = draw(st.floats(min_value=1.0, max_value=width, allow_nan=False, allow_infinity=False))
    bcx = draw(
        st.floats(min_value=bw / 2, max_value=width - bw / 2, allow_nan=False, allow_infinity=False)
    )
    depth = draw(
        st.floats(min_value=0.5, max_value=length - 0.5, allow_nan=False, allow_infinity=False)
    )
    return {
        "length_m": length,
        "width_m": width,
        "door": {"center_x_m": dcx, "width_m": dw},
        "maintenance_bay": {"center_x_m": bcx, "width_m": bw, "depth_m": depth},
        "clearance_m": draw(
            st.floats(min_value=0.0, max_value=2.0, allow_nan=False, allow_infinity=False)
        ),
        "wing_layer_clearance_m": draw(
            st.floats(min_value=0.0, max_value=2.0, allow_nan=False, allow_infinity=False)
        ),
    }


@st.composite
def _corrupt_one_field(draw: Any, doc_strategy: st.SearchStrategy[Any]) -> Any:
    """Take a well-formed dict and replace ONE field with an adversarial scalar,
    so the doc reaches deep but trips a model invariant -> exercises the
    loader's ValueError/TypeError -> LoaderError wrap (not just the perimeter)."""
    doc = draw(doc_strategy)
    if isinstance(doc, dict) and doc:
        key = draw(st.sampled_from(sorted(doc)))
        doc = dict(doc)
        doc[key] = draw(_scalars)
    return doc


def _well_formed_layout_doc() -> st.SearchStrategy[Any]:
    """A layout that reaches Layout(...) construction: places real ids (p1/p2)
    with valid coords and on_carts=True (always_cart consistency). Sometimes
    names a maintenance plane (placed -> exercises the maintenance-in-placements
    LoaderError; unplaced -> valid)."""
    placement = st.builds(
        lambda pid, x, y, h: {"plane": pid, "x_m": x, "y_m": y, "heading_deg": h, "on_carts": True},
        st.sampled_from(["p1", "p2"]),
        _finite,
        _finite,
        _finite,
    )
    return st.builds(
        lambda places, maint: (
            {"placements": places, "maintenance": {"plane": maint}}
            if maint is not None
            else {"placements": places}
        ),
        st.lists(placement, max_size=2, unique_by=lambda p: p["plane"]),
        st.one_of(st.none(), st.sampled_from(["p1", "p2"])),
    )


def _well_formed_scenario_doc() -> st.SearchStrategy[Any]:
    """A scenario that reaches Scenario(...) construction: fleet_in of real ids,
    optionally a valid pin constraint."""
    pin = st.builds(
        lambda x, y, h: {"pin": {"x_m": x, "y_m": y, "heading_deg": h, "on_carts": True}},
        _finite,
        _finite,
        _finite,
    )
    return st.builds(
        lambda fin, cons: {"fleet_in": fin, "constraints": cons} if cons else {"fleet_in": fin},
        st.lists(st.sampled_from(["p1", "p2"]), min_size=1, max_size=2, unique=True),
        st.one_of(
            st.just({}),
            st.builds(lambda c: {"p1": c}, pin),
        ),
    )


# --- per-entry-point document strategies ---
def _part_docs() -> st.SearchStrategy[Any]:
    full = st.fixed_dictionaries(
        {
            "kind": _enum_or_garbage(["fuselage", "wing", "strut", "tail"]),
            "length_m": _scalars,
            "width_m": _scalars,
            "z_bottom_m": _scalars,
            "z_top_m": _scalars,
        },
        optional={"offset_x_m": _scalars, "offset_y_m": _scalars, "angle_deg": _scalars},
    )
    return st.one_of(_maybe_drop_keys(full), st.none(), _safe_text, st.integers())


def _struts_docs() -> st.SearchStrategy[Any]:
    full = st.fixed_dictionaries(
        {
            "fuselage_attach_x_m": _scalars,
            "fuselage_attach_y_m": _scalars,
            "fuselage_attach_z_m": _scalars,
            "wing_attach_y_m": _scalars,
            "width_m": _scalars,
        }
    )
    return st.one_of(_maybe_drop_keys(full), st.none(), _safe_text)


def fleet_documents() -> st.SearchStrategy[Any]:
    aircraft = st.fixed_dictionaries(
        {
            "id": st.one_of(_safe_text, st.sampled_from(["p1", "p2"]), st.integers()),
            "name": _safe_text,
            "wing_position": _enum_or_garbage(["high", "mid", "low"]),
            "gear": _enum_or_garbage(["tailwheel", "nosewheel", "monowheel"]),
            "movement_mode": _enum_or_garbage(["always_cart", "always_own_gear", "cart_eligible"]),
            "parts": st.lists(_part_docs(), max_size=4),
        },
        optional={
            "turn_radius_m": _scalars,
            "measured": _bool_garbage,
            "struts": _struts_docs(),
            "notes": _safe_text,
        },
    )
    top = st.fixed_dictionaries({"aircraft": st.lists(_maybe_drop_keys(aircraft), max_size=4)})
    return st.one_of(
        _maybe_drop_keys(top),
        _well_formed_fleet_doc(),
        st.builds(
            lambda planes: {"aircraft": planes},
            st.lists(
                _corrupt_one_field(_well_formed_aircraft(with_struts=True)), min_size=1, max_size=2
            ),
        ),
        st.none(),
        st.lists(st.integers()),
        _safe_text,
    )


def hangar_documents() -> st.SearchStrategy[Any]:
    door = st.fixed_dictionaries({"center_x_m": _scalars, "width_m": _scalars})
    bay = st.fixed_dictionaries({"center_x_m": _scalars, "width_m": _scalars, "depth_m": _scalars})
    top = st.fixed_dictionaries(
        {
            "length_m": _scalars,
            "width_m": _scalars,
            "door": st.one_of(_maybe_drop_keys(door), st.none(), _safe_text),
            "maintenance_bay": st.one_of(_maybe_drop_keys(bay), st.none(), _safe_text),
        },
        optional={"clearance_m": _scalars, "wing_layer_clearance_m": _scalars},
    )
    return st.one_of(
        _maybe_drop_keys(top),
        _well_formed_hangar_doc(),
        _corrupt_one_field(_well_formed_hangar_doc()),
        st.none(),
        _safe_text,
    )


def layout_documents() -> st.SearchStrategy[Any]:
    placement = st.fixed_dictionaries(
        {"plane": _plane_ids, "x_m": _scalars, "y_m": _scalars, "heading_deg": _scalars},
        optional={"on_carts": _bool_garbage},
    )
    maintenance = st.one_of(
        st.none(),
        st.builds(lambda p: {"plane": p}, _plane_ids),
        _safe_text,
    )
    top = st.fixed_dictionaries(
        {"placements": st.lists(_maybe_drop_keys(placement), max_size=4)},
        optional={"maintenance": maintenance},
    )
    return st.one_of(_maybe_drop_keys(top), _well_formed_layout_doc(), st.none(), _safe_text)


def scenario_documents() -> st.SearchStrategy[Any]:
    pin = st.fixed_dictionaries(
        {"x_m": _scalars, "y_m": _scalars, "heading_deg": _scalars, "on_carts": _bool_garbage}
    )
    constraint = st.fixed_dictionaries(
        {},
        optional={
            "pin": st.one_of(_maybe_drop_keys(pin), st.none()),
            "force_on_carts": _bool_garbage,
        },
    )
    top = st.fixed_dictionaries(
        {"fleet_in": st.lists(_plane_ids, max_size=4)},
        optional={
            "maintenance": st.one_of(st.none(), st.builds(lambda p: {"plane": p}, _plane_ids)),
            "constraints": st.dictionaries(_plane_ids, constraint, max_size=3),
        },
    )
    return st.one_of(_maybe_drop_keys(top), _well_formed_scenario_doc(), st.none(), _safe_text)


def raw_documents() -> st.SearchStrategy[Any]:
    """Raw parse-layer inputs: arbitrary text/bytes fed straight to a loader,
    exercising the parse layer and the top-level-shape guards. st.binary() covers
    invalid-UTF-8 (guarded in the loader)."""
    return st.one_of(st.text(st.characters(codec="utf-8"), max_size=200), st.binary(max_size=200))


# --- run helpers: each encodes the loader contract (LoaderError is acceptable;
# anything else propagates as a finding) ---
def _write_yaml_tmp(doc: Any) -> Path:
    fd, name = tempfile.mkstemp(suffix=".yaml")
    os.close(fd)
    p = Path(name)
    p.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    return p


def _write_raw_tmp(doc: Any) -> Path:
    fd, name = tempfile.mkstemp(suffix=".yaml")
    os.close(fd)
    p = Path(name)
    if isinstance(doc, bytes):
        p.write_bytes(doc)
    else:
        p.write_text(str(doc), encoding="utf-8")
    return p


def _write_fleet_yaml(tmpdir: Path) -> Path:
    """Write a minimal valid fleet YAML to *tmpdir* and return its path.

    Used by the ref-resolving run-helpers so they can write a layout/scenario
    YAML that references the fleet by relative path — exercising
    ``load_fleet((path.parent / fleet_ref).resolve())`` inside ``load_layout``
    / ``load_scenario`` rather than the override-kwarg shortcut.
    """
    fleet_doc = {
        "aircraft": [
            {
                "id": "p1",
                "name": "Plane One",
                "wing_position": "high",
                "gear": "nosewheel",
                "movement_mode": "always_cart",
                "measured": False,
                "parts": [
                    {
                        "kind": "fuselage",
                        "length_m": 6.0,
                        "width_m": 1.2,
                        "z_bottom_m": 0.0,
                        "z_top_m": 2.0,
                    }
                ],
            },
            {
                "id": "p2",
                "name": "Plane Two",
                "wing_position": "low",
                "gear": "tailwheel",
                "movement_mode": "always_cart",
                "measured": False,
                "parts": [
                    {
                        "kind": "fuselage",
                        "length_m": 6.0,
                        "width_m": 1.2,
                        "z_bottom_m": 0.0,
                        "z_top_m": 2.0,
                    }
                ],
            },
        ]
    }
    p = tmpdir / "fleet.yaml"
    p.write_text(yaml.safe_dump(fleet_doc, allow_unicode=True), encoding="utf-8")
    return p


def _write_hangar_yaml(tmpdir: Path) -> Path:
    """Write a minimal valid hangar YAML to *tmpdir* and return its path."""
    hangar_doc = {
        "length_m": 40.0,
        "width_m": 20.0,
        "door": {"center_x_m": 10.0, "width_m": 8.0},
        "maintenance_bay": {"center_x_m": 10.0, "width_m": 6.0, "depth_m": 5.0},
        "clearance_m": 0.3,
        "wing_layer_clearance_m": 0.2,
    }
    p = tmpdir / "hangar.yaml"
    p.write_text(yaml.safe_dump(hangar_doc, allow_unicode=True), encoding="utf-8")
    return p


def run_fleet(doc: Any) -> None:
    p = _write_yaml_tmp(doc)
    try:
        loader.load_fleet(p)
    except LoaderError:
        pass
    finally:
        p.unlink(missing_ok=True)


def run_hangar(doc: Any) -> None:
    p = _write_yaml_tmp(doc)
    try:
        loader.load_hangar(p)
    except LoaderError:
        pass
    finally:
        p.unlink(missing_ok=True)


def run_layout(doc: Any) -> None:
    p = _write_yaml_tmp(doc)
    try:
        loader.load_layout(p, fleet=dict(VALID_FLEET), hangar=VALID_HANGAR)
    except LoaderError:
        pass
    finally:
        p.unlink(missing_ok=True)


def run_scenario(doc: Any) -> None:
    p = _write_yaml_tmp(doc)
    try:
        loader.load_scenario(p, fleet=dict(VALID_FLEET), hangar=VALID_HANGAR)
    except LoaderError:
        pass
    finally:
        p.unlink(missing_ok=True)


def run_raw(doc: Any) -> None:
    p = _write_raw_tmp(doc)
    try:
        loader.load_fleet(p)
    except LoaderError:
        pass
    finally:
        p.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Ref-resolving run-helpers
#
# The helpers above always pass valid fleet=/hangar= override kwargs, so the
# ref-resolution branches inside load_layout / load_scenario are never reached:
#   - "field required when no override" raise
#   - path.parent / fleet_ref relative join
#   - "set in YAML and override provided" conflict raise
#
# These helpers exercise those three branches by writing fleet/hangar files to
# a temp directory and referencing them by relative path in the layout/scenario
# YAML — mirroring the real on-disk usage pattern.
# ---------------------------------------------------------------------------


def run_layout_via_ref(doc: Any) -> None:
    """Write fleet + hangar to a temp dir; inject relative refs into *doc*;
    call load_layout with no override kwargs.

    Covers:
    - the relative-join path: ``(path.parent / fleet_ref).resolve()``
    - (see run_layout_no_fleet_ref / run_layout_no_hangar_ref for the
      "field required" raise paths)
    """
    tmpdir = Path(tempfile.mkdtemp(suffix="-fuzz-ref"))
    try:
        _write_fleet_yaml(tmpdir)
        _write_hangar_yaml(tmpdir)
        # Ensure doc is a dict; inject relative refs (relative names, no path
        # component) so (path.parent / "fleet.yaml").resolve() resolves correctly.
        if isinstance(doc, dict):
            ref_doc = dict(doc)
            ref_doc["fleet"] = "fleet.yaml"
            ref_doc["hangar"] = "hangar.yaml"
        else:
            # Non-dict: will fail the top-level isinstance check → LoaderError
            ref_doc = doc
        main_path = tmpdir / "layout.yaml"
        main_path.write_text(yaml.safe_dump(ref_doc, allow_unicode=True), encoding="utf-8")
        with contextlib.suppress(LoaderError):
            loader.load_layout(main_path)  # no fleet= / hangar= override
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def run_layout_no_fleet_ref(doc: Any) -> None:
    """Call load_layout with NO fleet= override AND a YAML that omits 'fleet:'.

    Covers: load_layout L187 — "fleet field is required when no override".
    The YAML must have 'hangar:' so the loader reaches the fleet check before
    raising for hangar.
    """
    tmpdir = Path(tempfile.mkdtemp(suffix="-fuzz-nofleet"))
    try:
        _write_hangar_yaml(tmpdir)
        if isinstance(doc, dict):
            ref_doc = {k: v for k, v in doc.items() if k not in ("fleet",)}
            ref_doc["hangar"] = "hangar.yaml"
        else:
            ref_doc = doc
        main_path = tmpdir / "layout.yaml"
        main_path.write_text(yaml.safe_dump(ref_doc, allow_unicode=True), encoding="utf-8")
        with contextlib.suppress(LoaderError):
            loader.load_layout(main_path)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def run_layout_no_hangar_ref(doc: Any) -> None:
    """Call load_layout with NO hangar= override AND a YAML that has 'fleet:'
    but omits 'hangar:'.

    Covers: load_layout L200 — "hangar field is required when no override".
    Fleet resolves successfully first; then hangar_ref is None → raises.
    """
    tmpdir = Path(tempfile.mkdtemp(suffix="-fuzz-nohangar"))
    try:
        _write_fleet_yaml(tmpdir)
        if isinstance(doc, dict):
            ref_doc = {k: v for k, v in doc.items() if k not in ("hangar",)}
            ref_doc["fleet"] = "fleet.yaml"
        else:
            ref_doc = doc
        main_path = tmpdir / "layout.yaml"
        main_path.write_text(yaml.safe_dump(ref_doc, allow_unicode=True), encoding="utf-8")
        with contextlib.suppress(LoaderError):
            loader.load_layout(main_path)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def run_layout_fleet_conflict(doc: Any) -> None:
    """Supply a layout YAML with a 'fleet:' field AND pass fleet= override kwarg.

    Covers: load_layout L192 — "fleet field is set but override also provided".
    """
    tmpdir = Path(tempfile.mkdtemp(suffix="-fuzz-fleet-conflict"))
    try:
        if isinstance(doc, dict):
            conflict_doc = dict(doc)
            conflict_doc["fleet"] = "fleet.yaml"
            conflict_doc.pop("hangar", None)  # avoid hangar conflict in same call
        else:
            conflict_doc = doc
        main_path = tmpdir / "layout.yaml"
        main_path.write_text(yaml.safe_dump(conflict_doc, allow_unicode=True), encoding="utf-8")
        with contextlib.suppress(LoaderError):
            loader.load_layout(main_path, fleet=dict(VALID_FLEET))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def run_layout_hangar_conflict(doc: Any) -> None:
    """Supply a layout YAML with a 'hangar:' field AND pass hangar= override kwarg,
    but supply a valid fleet ref so fleet resolves OK first.

    Covers: load_layout L205 — "hangar field is set but override also provided".
    Fleet must resolve (no fleet= override, valid fleet ref) → then hangar
    conflict fires.
    """
    tmpdir = Path(tempfile.mkdtemp(suffix="-fuzz-hangar-conflict"))
    try:
        _write_fleet_yaml(tmpdir)
        if isinstance(doc, dict):
            conflict_doc = dict(doc)
            conflict_doc["fleet"] = "fleet.yaml"
            conflict_doc["hangar"] = "hangar.yaml"
        else:
            conflict_doc = doc
        main_path = tmpdir / "layout.yaml"
        main_path.write_text(yaml.safe_dump(conflict_doc, allow_unicode=True), encoding="utf-8")
        # No fleet= override (so fleet resolves from file) + hangar= override
        # while YAML also has 'hangar:' → conflict raise.
        with contextlib.suppress(LoaderError):
            loader.load_layout(main_path, hangar=VALID_HANGAR)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def run_layout_conflict(doc: Any) -> None:
    """Pass a layout YAML that contains both 'fleet' and 'hangar' fields AND also
    supply fleet= override kwarg — exercises the fleet conflict raise.
    """
    tmpdir = Path(tempfile.mkdtemp(suffix="-fuzz-conflict"))
    try:
        if isinstance(doc, dict):
            conflict_doc = dict(doc)
            conflict_doc["fleet"] = "fleet.yaml"
            conflict_doc["hangar"] = "hangar.yaml"
        else:
            conflict_doc = doc
        main_path = tmpdir / "layout.yaml"
        main_path.write_text(yaml.safe_dump(conflict_doc, allow_unicode=True), encoding="utf-8")
        # Supply fleet= override while YAML also has 'fleet:' → conflict error.
        with contextlib.suppress(LoaderError):
            loader.load_layout(main_path, fleet=dict(VALID_FLEET))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def run_scenario_via_ref(doc: Any) -> None:
    """Write fleet + hangar to a temp dir; inject relative refs into *doc*;
    call load_scenario with no override kwargs.

    Covers the same ref-resolution branches as run_layout_via_ref, but
    for load_scenario (which also has the fleet_in required-field check before
    the ref-resolution code, so the doc must carry fleet_in to reach those
    branches).
    """
    tmpdir = Path(tempfile.mkdtemp(suffix="-fuzz-ref"))
    try:
        _write_fleet_yaml(tmpdir)
        _write_hangar_yaml(tmpdir)
        if isinstance(doc, dict):
            ref_doc = dict(doc)
            ref_doc["fleet"] = "fleet.yaml"
            ref_doc["hangar"] = "hangar.yaml"
            # Ensure fleet_in is present so we reach the ref-resolution code.
            if "fleet_in" not in ref_doc:
                ref_doc["fleet_in"] = ["p1"]
        else:
            ref_doc = doc
        main_path = tmpdir / "scenario.yaml"
        main_path.write_text(yaml.safe_dump(ref_doc, allow_unicode=True), encoding="utf-8")
        with contextlib.suppress(LoaderError):
            loader.load_scenario(main_path)  # no fleet= / hangar= override
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def run_scenario_no_fleet_ref(doc: Any) -> None:
    """Call load_scenario with NO fleet= override AND a YAML that omits 'fleet:'.

    Covers: load_scenario L298 — "fleet field is required when no override".
    """
    tmpdir = Path(tempfile.mkdtemp(suffix="-fuzz-nofleet"))
    try:
        _write_hangar_yaml(tmpdir)
        if isinstance(doc, dict):
            ref_doc = {k: v for k, v in doc.items() if k not in ("fleet",)}
            ref_doc["hangar"] = "hangar.yaml"
            if "fleet_in" not in ref_doc:
                ref_doc["fleet_in"] = ["p1"]
        else:
            ref_doc = doc
        main_path = tmpdir / "scenario.yaml"
        main_path.write_text(yaml.safe_dump(ref_doc, allow_unicode=True), encoding="utf-8")
        with contextlib.suppress(LoaderError):
            loader.load_scenario(main_path)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def run_scenario_no_hangar_ref(doc: Any) -> None:
    """Call load_scenario with NO hangar= override AND a YAML that has 'fleet:'
    but omits 'hangar:'.

    Covers: load_scenario L311 — "hangar field is required when no override".
    """
    tmpdir = Path(tempfile.mkdtemp(suffix="-fuzz-nohangar"))
    try:
        _write_fleet_yaml(tmpdir)
        if isinstance(doc, dict):
            ref_doc = {k: v for k, v in doc.items() if k not in ("hangar",)}
            ref_doc["fleet"] = "fleet.yaml"
            if "fleet_in" not in ref_doc:
                ref_doc["fleet_in"] = ["p1"]
        else:
            ref_doc = doc
        main_path = tmpdir / "scenario.yaml"
        main_path.write_text(yaml.safe_dump(ref_doc, allow_unicode=True), encoding="utf-8")
        with contextlib.suppress(LoaderError):
            loader.load_scenario(main_path)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def run_scenario_hangar_conflict(doc: Any) -> None:
    """Supply a scenario YAML with 'fleet:' and 'hangar:' fields + hangar= override kwarg.
    Fleet resolves from file (no fleet= override); hangar conflict fires.

    Covers: load_scenario L316 — "hangar field is set but override also provided".
    """
    tmpdir = Path(tempfile.mkdtemp(suffix="-fuzz-hangar-conflict"))
    try:
        _write_fleet_yaml(tmpdir)
        if isinstance(doc, dict):
            conflict_doc = dict(doc)
            conflict_doc["fleet"] = "fleet.yaml"
            conflict_doc["hangar"] = "hangar.yaml"
            if "fleet_in" not in conflict_doc:
                conflict_doc["fleet_in"] = ["p1"]
        else:
            conflict_doc = doc
        main_path = tmpdir / "scenario.yaml"
        main_path.write_text(yaml.safe_dump(conflict_doc, allow_unicode=True), encoding="utf-8")
        with contextlib.suppress(LoaderError):
            loader.load_scenario(main_path, hangar=VALID_HANGAR)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def run_scenario_conflict(doc: Any) -> None:
    """Supply a scenario YAML with 'fleet:' field AND pass fleet= override kwarg.

    Covers: load_scenario L303 — "fleet field is set but override also provided".
    """
    tmpdir = Path(tempfile.mkdtemp(suffix="-fuzz-conflict"))
    try:
        if isinstance(doc, dict):
            conflict_doc = dict(doc)
            conflict_doc["fleet"] = "fleet.yaml"
            conflict_doc.pop("hangar", None)  # avoid hangar conflict
            if "fleet_in" not in conflict_doc:
                conflict_doc["fleet_in"] = ["p1"]
        else:
            conflict_doc = doc
        main_path = tmpdir / "scenario.yaml"
        main_path.write_text(yaml.safe_dump(conflict_doc, allow_unicode=True), encoding="utf-8")
        with contextlib.suppress(LoaderError):
            loader.load_scenario(main_path, fleet=dict(VALID_FLEET))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Targeted document strategies for rare error branches
#
# Each targets a specific loader branch that the probabilistic fuzz rarely
# reaches. These are _corrupt_one_field-style in spirit: start from a
# well-formed document, then introduce the exact condition that triggers the
# branch under test.
# ---------------------------------------------------------------------------


def hangar_model_invariant_violation_documents() -> st.SearchStrategy[Any]:
    """Hangar YAML is structurally complete but violates a model invariant
    (negative/zero numeric field) → load_hangar L156 (ValueError → LoaderError wrap).

    All fields are present and parseable as floats; the failure happens inside
    Hangar.__post_init__ or Door/MaintenanceBay.__post_init__.
    """
    return st.one_of(
        # Hangar.length_m <= 0
        st.just(
            {
                "length_m": -1.0,
                "width_m": 20.0,
                "door": {"center_x_m": 10.0, "width_m": 8.0},
                "maintenance_bay": {"center_x_m": 10.0, "width_m": 6.0, "depth_m": 5.0},
            }
        ),
        # Door.width_m <= 0
        st.just(
            {
                "length_m": 40.0,
                "width_m": 20.0,
                "door": {"center_x_m": 10.0, "width_m": -1.0},
                "maintenance_bay": {"center_x_m": 10.0, "width_m": 6.0, "depth_m": 5.0},
            }
        ),
        # Bay depth >= length
        st.just(
            {
                "length_m": 5.0,
                "width_m": 20.0,
                "door": {"center_x_m": 10.0, "width_m": 8.0},
                "maintenance_bay": {"center_x_m": 10.0, "width_m": 6.0, "depth_m": 5.0},
            }
        ),
    )


def hangar_door_not_mapping_documents() -> st.SearchStrategy[Any]:
    """Hangar YAML has length_m/width_m but door is not a dict
    → load_hangar L118 ("door must be a mapping").
    """
    return st.builds(
        lambda v: {
            "length_m": 40.0,
            "width_m": 20.0,
            "door": v,
            "maintenance_bay": {"center_x_m": 10.0, "width_m": 6.0, "depth_m": 5.0},
        },
        st.one_of(st.none(), st.integers(), _safe_text, st.booleans()),
    )


def hangar_bay_not_mapping_documents() -> st.SearchStrategy[Any]:
    """Hangar YAML has a valid door dict but maintenance_bay is not a dict
    → load_hangar L122 ("maintenance_bay must be a mapping").

    load_hangar checks door first (L116-118) then maintenance_bay (L121-125).
    To reach L122 we must pass the door check (door IS a dict) while
    maintenance_bay is not a dict.
    """
    return st.builds(
        lambda v: {
            "length_m": 40.0,
            "width_m": 20.0,
            "door": {"center_x_m": 10.0, "width_m": 8.0},
            "maintenance_bay": v,
        },
        st.one_of(st.none(), st.integers(), _safe_text, st.booleans()),
    )


def hangar_missing_required_field_documents() -> st.SearchStrategy[Any]:
    """Hangar YAML is otherwise valid but missing one top-level required key
    ('length_m' or 'width_m') or one required key inside door/maintenance_bay.

    Covers load_hangar L129, L132, L135.
    """
    door_keys = ["center_x_m", "width_m"]
    bay_keys = ["center_x_m", "width_m", "depth_m"]

    @st.composite
    def _build(draw: Any) -> dict[str, Any]:
        full = {
            "length_m": 40.0,
            "width_m": 20.0,
            "door": {"center_x_m": 10.0, "width_m": 8.0},
            "maintenance_bay": {"center_x_m": 10.0, "width_m": 6.0, "depth_m": 5.0},
        }
        which = draw(st.sampled_from(["top_length", "top_width", "door_key", "bay_key"]))
        doc = {k: v for k, v in full.items()}
        if which == "top_length":
            del doc["length_m"]
        elif which == "top_width":
            del doc["width_m"]
        elif which == "door_key":
            key = draw(st.sampled_from(door_keys))
            doc["door"] = {k: v for k, v in full["door"].items() if k != key}
        else:
            key = draw(st.sampled_from(bay_keys))
            doc["maintenance_bay"] = {k: v for k, v in full["maintenance_bay"].items() if k != key}
        return doc

    return _build()


def fleet_parts_not_list_documents() -> st.SearchStrategy[Any]:
    """Aircraft dict has 'parts' but the value is not a list (or is empty)
    → _build_aircraft L546 ("parts must be a non-empty list").
    """
    return st.builds(
        lambda v: {
            "aircraft": [
                {
                    "id": "p1",
                    "name": "Parts-non-list",
                    "wing_position": "high",
                    "gear": "nosewheel",
                    "movement_mode": "always_cart",
                    "measured": False,
                    "parts": v,
                }
            ]
        },
        st.one_of(
            st.none(),
            st.integers(),
            _safe_text,
            st.just([]),  # empty list → not parts_data falsy
            st.just({}),
        ),
    )


def fleet_part_not_mapping_documents() -> st.SearchStrategy[Any]:
    """A parts list entry is not a dict → _build_part L576 ("parts[i] must be a mapping")."""
    non_dict = st.one_of(st.none(), st.integers(), _safe_text, st.booleans())
    return st.builds(
        lambda v: {
            "aircraft": [
                {
                    "id": "p1",
                    "name": "Part-non-dict",
                    "wing_position": "high",
                    "gear": "nosewheel",
                    "movement_mode": "always_cart",
                    "measured": False,
                    "parts": [v],
                }
            ]
        },
        non_dict,
    )


def layout_always_cart_on_carts_false_documents() -> st.SearchStrategy[Any]:
    """Place an always_cart plane with on_carts=False.

    The loader does not pre-check this constraint; Layout.__post_init__
    catches it and raises ValueError, which is then wrapped at L250-251.
    """
    return st.just(
        {
            "placements": [
                {
                    "plane": "p1",
                    "x_m": 0.0,
                    "y_m": 0.0,
                    "heading_deg": 0.0,
                    "on_carts": False,  # always_cart plane → Layout ValueError
                }
            ]
        }
    )


def fleet_duplicate_aircraft_id_documents() -> st.SearchStrategy[Any]:
    """Two aircraft entries share the same id → load_fleet L104 ("duplicate aircraft id")."""
    plane = {
        "id": "p1",
        "name": "Plane",
        "wing_position": "high",
        "gear": "nosewheel",
        "movement_mode": "always_cart",
        "measured": False,
        "parts": [
            {"kind": "fuselage", "length_m": 6.0, "width_m": 1.2, "z_bottom_m": 0.0, "z_top_m": 2.0}
        ],
    }
    return st.just({"aircraft": [plane, plane]})


def fleet_aircraft_not_list_documents() -> st.SearchStrategy[Any]:
    """'aircraft' present but not a list → line 90 in load_fleet."""
    return st.builds(lambda v: {"aircraft": v}, st.one_of(_scalars, st.just({})))


def fleet_aircraft_entry_not_mapping_documents() -> st.SearchStrategy[Any]:
    """'aircraft' is a list but an entry is not a dict → line 95 in load_fleet."""
    non_dict = st.one_of(st.none(), st.integers(), _safe_text, st.booleans())
    return st.builds(lambda v: {"aircraft": [v]}, non_dict)


def fleet_strut_without_wing_documents() -> st.SearchStrategy[Any]:
    """Aircraft has a 'struts' block but no part with kind='wing'
    → line 555: "struts block requires a part of kind 'wing'".

    We provide a valid struts dict but only include a fuselage part (no wing).
    """

    @st.composite
    def _build(draw: Any) -> dict[str, Any]:
        return {
            "aircraft": [
                {
                    "id": "p1",
                    "name": "Strut-no-wing",
                    "wing_position": "high",
                    "gear": "nosewheel",
                    "movement_mode": "always_cart",
                    "measured": False,
                    "parts": [
                        # only a fuselage — no wing part, so _expand_struts can't find it
                        {
                            "kind": "fuselage",
                            "length_m": draw(_positive),
                            "width_m": draw(_positive),
                            "z_bottom_m": 0.0,
                            "z_top_m": draw(_positive),
                        }
                    ],
                    "struts": {
                        "fuselage_attach_x_m": 0.0,
                        "fuselage_attach_y_m": 0.0,
                        "fuselage_attach_z_m": 0.5,
                        "wing_attach_y_m": 2.0,
                        "width_m": draw(_positive),
                    },
                }
            ]
        }

    return _build()


def fleet_struts_missing_key_documents() -> st.SearchStrategy[Any]:
    """Aircraft has a 'struts' block that omits one required struts key
    → line 603 in _build_struts_spec."""
    struts_keys = [
        "fuselage_attach_x_m",
        "fuselage_attach_y_m",
        "fuselage_attach_z_m",
        "wing_attach_y_m",
        "width_m",
    ]

    @st.composite
    def _build(draw: Any) -> dict[str, Any]:
        full_struts = {
            "fuselage_attach_x_m": 0.0,
            "fuselage_attach_y_m": 0.0,
            "fuselage_attach_z_m": 0.5,
            "wing_attach_y_m": 2.0,
            "width_m": 0.05,
        }
        # Drop exactly one key so the missing-key check fires.
        drop_key = draw(st.sampled_from(struts_keys))
        partial_struts = {k: v for k, v in full_struts.items() if k != drop_key}
        return {
            "aircraft": [
                {
                    "id": "p1",
                    "name": "Strut-missing-key",
                    "wing_position": "high",
                    "gear": "nosewheel",
                    "movement_mode": "always_cart",
                    "measured": False,
                    "parts": [
                        {
                            "kind": "wing",
                            "length_m": 8.0,
                            "width_m": 1.0,
                            "z_bottom_m": 1.5,
                            "z_top_m": 2.0,
                        }
                    ],
                    "struts": partial_struts,
                }
            ]
        }

    return _build()


def fleet_strut_invalid_geometry_documents() -> st.SearchStrategy[Any]:
    """Aircraft with a 'struts' block that violates the geometry guards in _expand_struts:

    - _z_guard_violation:    wing.z_bottom_m <= fuselage_attach_z_m → _expand_struts L624
    - _span_guard_violation: wing_attach_y_m == fuselage_attach_y_m (span == 0) → L631

    The two variants are kept separate so each deterministically targets its own branch.

    Span-guard design note:
        StrutsSpec.__post_init__ (models.py L113) rejects wing_attach_y_m <= 0, and
        L118 rejects wing_attach_y_m < fuselage_attach_y_m.  L631 is therefore only
        reachable when wing_attach_y_m == fuselage_attach_y_m > 0 (span == 0 but both
        values pass the model pre-checks).  Drawing a single y value and assigning it
        to BOTH fields makes this deterministic — every draw reaches L631, not only the
        boundary case where Hypothesis happens to pick the inclusive upper-bound float.
    """

    @st.composite
    def _z_guard_violation(draw: Any) -> dict[str, Any]:
        """wing.z_bottom_m <= fuselage_attach_z_m → _expand_struts L624."""
        z = draw(st.floats(min_value=0.5, max_value=5.0, allow_nan=False, allow_infinity=False))
        return {
            "aircraft": [
                {
                    "id": "p1",
                    "name": "Z-guard-violation",
                    "wing_position": "high",
                    "gear": "nosewheel",
                    "movement_mode": "always_cart",
                    "measured": False,
                    "parts": [
                        {
                            "kind": "wing",
                            "length_m": 8.0,
                            "width_m": 1.0,
                            "z_bottom_m": z,  # wing bottom at z
                            "z_top_m": z + 0.5,
                        }
                    ],
                    "struts": {
                        "fuselage_attach_x_m": 0.0,
                        "fuselage_attach_y_m": 0.0,
                        "fuselage_attach_z_m": z,  # same as wing bottom → not strictly above
                        "wing_attach_y_m": 2.0,
                        "width_m": 0.05,
                    },
                }
            ]
        }

    @st.composite
    def _span_guard_violation(draw: Any) -> dict[str, Any]:
        """wing_attach_y_m == fuselage_attach_y_m (zero span) → _expand_struts L631.

        Both y values are drawn as the SAME float so the span is always exactly 0.
        This bypasses StrutsSpec.__post_init__ (which only rejects wing_attach_y_m < 0
        or wing_attach_y_m < fuselage_attach_y_m) and deterministically reaches L631
        on every invocation rather than only when Hypothesis happens to pick the
        inclusive upper bound.
        """
        y = draw(st.floats(min_value=0.1, max_value=3.0, allow_nan=False, allow_infinity=False))
        return {
            "aircraft": [
                {
                    "id": "p1",
                    "name": "Span-guard-violation",
                    "wing_position": "high",
                    "gear": "nosewheel",
                    "movement_mode": "always_cart",
                    "measured": False,
                    "parts": [
                        {
                            "kind": "wing",
                            "length_m": 8.0,
                            "width_m": 1.0,
                            "z_bottom_m": 2.0,
                            "z_top_m": 2.5,
                        }
                    ],
                    "struts": {
                        "fuselage_attach_x_m": 0.0,
                        "fuselage_attach_y_m": y,  # same value as wing_attach_y_m
                        "fuselage_attach_z_m": 0.5,
                        "wing_attach_y_m": y,  # == fuselage_attach_y_m → span == 0 → L631
                        "width_m": 0.05,
                    },
                }
            ]
        }

    return st.one_of(_z_guard_violation(), _span_guard_violation())


def fleet_aircraft_missing_required_field_documents() -> st.SearchStrategy[Any]:
    """Aircraft dict is missing exactly one required key
    → _build_aircraft L542 ("missing required field").

    Note: load_fleet first checks isinstance(entry, dict) at L94 (→ L95),
    so _build_aircraft is always called with a dict. L542 is therefore
    reachable through normal load_fleet when an aircraft dict lacks a field.
    """
    required = ["id", "name", "wing_position", "gear", "movement_mode"]

    @st.composite
    def _build(draw: Any) -> dict[str, Any]:
        drop_key = draw(st.sampled_from(required))
        full = {
            "id": "p1",
            "name": "Test Plane",
            "wing_position": "high",
            "gear": "nosewheel",
            "movement_mode": "always_cart",
            "measured": False,
            "parts": [
                {
                    "kind": "fuselage",
                    "length_m": 6.0,
                    "width_m": 1.2,
                    "z_bottom_m": 0.0,
                    "z_top_m": 2.0,
                }
            ],
        }
        return {"aircraft": [{k: v for k, v in full.items() if k != drop_key}]}

    return _build()


def fleet_struts_not_mapping_documents() -> st.SearchStrategy[Any]:
    """Aircraft has 'struts' key but its value is not a dict
    → _build_aircraft L551 ("struts must be a mapping").
    """
    non_dict_struts = st.one_of(st.integers(), _safe_text, st.booleans(), st.lists(st.integers()))
    return st.builds(
        lambda v: {
            "aircraft": [
                {
                    "id": "p1",
                    "name": "Struts-non-dict",
                    "wing_position": "high",
                    "gear": "nosewheel",
                    "movement_mode": "always_cart",
                    "measured": False,
                    "parts": [
                        {
                            "kind": "fuselage",
                            "length_m": 6.0,
                            "width_m": 1.2,
                            "z_bottom_m": 0.0,
                            "z_top_m": 2.0,
                        }
                    ],
                    "struts": v,
                }
            ]
        },
        non_dict_struts,
    )


def fleet_part_missing_required_field_documents() -> st.SearchStrategy[Any]:
    """A part dict is missing exactly one required key
    → _build_part L580 ("parts[i] missing required field").
    """
    required = ["kind", "length_m", "width_m", "z_bottom_m", "z_top_m"]

    @st.composite
    def _build(draw: Any) -> dict[str, Any]:
        drop_key = draw(st.sampled_from(required))
        full_part = {
            "kind": "fuselage",
            "length_m": 6.0,
            "width_m": 1.2,
            "z_bottom_m": 0.0,
            "z_top_m": 2.0,
        }
        partial_part = {k: v for k, v in full_part.items() if k != drop_key}
        return {
            "aircraft": [
                {
                    "id": "p1",
                    "name": "Part-missing-key",
                    "wing_position": "high",
                    "gear": "nosewheel",
                    "movement_mode": "always_cart",
                    "measured": False,
                    "parts": [partial_part],
                }
            ]
        }

    return _build()


def layout_placements_not_list_documents() -> st.SearchStrategy[Any]:
    """'placements' key is present but its value is not a list
    → load_layout L212 ("placements must be a list").
    """
    return st.builds(
        lambda v: {"placements": v},
        st.one_of(st.integers(), _safe_text, st.just({}), st.booleans()),
    )


def layout_maintenance_plane_in_placements_documents() -> st.SearchStrategy[Any]:
    """The maintenance plane is also listed in placements
    → load_layout L236-L241 pre-check (and Layout.__post_init__ backstop).

    This exercises the loader-level "maintenance plane must NOT be placed"
    check that surfaces a more actionable error than the bare model invariant.
    """
    placement = {"plane": "p1", "x_m": 0.0, "y_m": 0.0, "heading_deg": 0.0, "on_carts": True}
    return st.just(
        {
            "placements": [placement],
            "maintenance": {"plane": "p1"},
        }
    )


def layout_placement_not_mapping_documents() -> st.SearchStrategy[Any]:
    """'placements' list contains a non-dict entry
    → line 664 in _build_placement."""
    non_dict = st.one_of(st.none(), st.integers(), _safe_text, st.booleans())
    return st.builds(
        lambda v: {"placements": [v]},
        non_dict,
    )


def layout_maintenance_shape_documents() -> st.SearchStrategy[Any]:
    """Various malformed 'maintenance' block shapes to exercise
    _extract_maintenance_plane branches (lines 448, 452, 455, 460, 465).

    Covers:
    - maintenance is a non-dict/non-None scalar       → line 448
    - maintenance is a dict but lacks 'plane' key     → line 452
    - maintenance.plane is None                       → line 455
    - maintenance.plane is not a string               → line 460
    - maintenance.plane is an empty string            → line 465
    """
    # Non-dict, non-None scalar (e.g. a bare string — the common author mistake)
    non_dict_maintenance = st.builds(
        lambda v: {"placements": [], "maintenance": v},
        st.one_of(
            st.integers(),
            st.floats(allow_nan=False, allow_infinity=False),
            _safe_text.filter(lambda s: s != ""),  # non-empty strings are the classic typo
        ),
    )
    # Dict without 'plane' key
    no_plane_key = st.just({"placements": [], "maintenance": {"aircraft": "p1"}})
    # maintenance.plane = null
    plane_is_null = st.just({"placements": [], "maintenance": {"plane": None}})
    # maintenance.plane is a non-string (int/float/bool)
    plane_non_string = st.builds(
        lambda v: {"placements": [], "maintenance": {"plane": v}},
        st.one_of(st.integers(), st.booleans()),
    )
    # maintenance.plane is an empty string
    plane_empty = st.just({"placements": [], "maintenance": {"plane": ""}})
    return st.one_of(
        non_dict_maintenance,
        no_plane_key,
        plane_is_null,
        plane_non_string,
        plane_empty,
    )


def layout_case_insensitive_near_miss_documents() -> st.SearchStrategy[Any]:
    """Placement plane id is a case-insensitive but not exact match for a valid id
    → _suggest_plane_id L499 (case-insensitive branch).

    'P1' and 'P2' match p1/p2 under casefold but are not in VALID_FLEET
    (which uses lowercase ids), so the case-insensitive suggestion fires at L499.
    """
    return st.builds(
        lambda pid: {
            "placements": [
                {"plane": pid, "x_m": 0.0, "y_m": 0.0, "heading_deg": 0.0, "on_carts": True}
            ]
        },
        st.sampled_from(["P1", "P2"]),
    )


def layout_difflib_near_miss_documents() -> st.SearchStrategy[Any]:
    """Placement plane id is a difflib-close but NOT case-insensitive match
    for a valid id → _suggest_plane_id L502 (difflib branch).

    'p1a' and 'pl1' score above 0.6 similarity to 'p1' with difflib but are
    not case-insensitive matches, so the case-insensitive branch (L499) is
    skipped and difflib fires at L502.
    """
    near_miss_ids = st.sampled_from(["p1a", "pl1", "p1_", "pp1"])
    return st.builds(
        lambda pid: {
            "placements": [
                {"plane": pid, "x_m": 0.0, "y_m": 0.0, "heading_deg": 0.0, "on_carts": True}
            ]
        },
        near_miss_ids,
    )


def scenario_fleet_in_not_list_documents() -> st.SearchStrategy[Any]:
    """'fleet_in' is present but not a list → line 291 in load_scenario."""
    return st.builds(
        lambda v: {"fleet_in": v},
        st.one_of(st.none(), st.integers(), _safe_text, st.just({})),
    )


def scenario_constraints_not_mapping_documents() -> st.SearchStrategy[Any]:
    """'constraints' is present but not a dict → lines 352/354 in load_scenario.

    Includes explicit None (``constraints: ~`` → L352 sets it back to {}) and
    non-dict values (list/int/str → L354 raises LoaderError).
    """
    return st.builds(
        lambda v: {"fleet_in": ["p1"], "constraints": v},
        st.one_of(
            st.none(),  # explicit YAML null → L352: constraints_raw = {}
            st.integers(),
            _safe_text,
            st.lists(st.integers(), max_size=3),
        ),
    )


def scenario_constraint_non_dict_data_documents() -> st.SearchStrategy[Any]:
    """'constraints' is a valid dict but a value is not a mapping
    → line 411 in _build_plane_constraint."""
    non_dict_val = st.one_of(st.integers(), _safe_text, st.none(), st.booleans())
    return st.builds(
        lambda v: {"fleet_in": ["p1"], "constraints": {"p1": v}},
        non_dict_val,
    )


def scenario_pin_shape_documents() -> st.SearchStrategy[Any]:
    """Pin field is present but not a mapping → line 417 in _build_plane_constraint.

    Also exercises 'pin' missing a required field (line 423) and
    'force_on_carts' _to_bool coercion (line 434).
    """
    # pin is a non-dict scalar
    pin_non_dict = st.builds(
        lambda v: {"fleet_in": ["p1"], "constraints": {"p1": {"pin": v}}},
        st.one_of(st.integers(), _safe_text, st.booleans()),
    )
    # pin is a dict but missing one required key
    required_pin_keys = ["x_m", "y_m", "heading_deg", "on_carts"]

    @st.composite
    def _pin_missing_key(draw: Any) -> dict[str, Any]:
        full_pin = {"x_m": 0.0, "y_m": 0.0, "heading_deg": 0.0, "on_carts": True}
        drop = draw(st.sampled_from(required_pin_keys))
        partial_pin = {k: v for k, v in full_pin.items() if k != drop}
        return {"fleet_in": ["p1"], "constraints": {"p1": {"pin": partial_pin}}}

    # force_on_carts is not a bool (exercises _to_bool at line 434)
    force_non_bool = st.builds(
        lambda v: {"fleet_in": ["p1"], "constraints": {"p1": {"force_on_carts": v}}},
        st.one_of(st.integers(), _safe_text, st.none()),
    )
    return st.one_of(pin_non_dict, _pin_missing_key(), force_non_bool)


# --- tagged union for the Atheris single-target harness ---
_RUNNERS = {
    "fleet": run_fleet,
    "hangar": run_hangar,
    "layout": run_layout,
    "scenario": run_scenario,
    "raw": run_raw,
    # ref-resolving helpers
    "layout_via_ref": run_layout_via_ref,
    "layout_no_fleet_ref": run_layout_no_fleet_ref,
    "layout_no_hangar_ref": run_layout_no_hangar_ref,
    "layout_fleet_conflict": run_layout_fleet_conflict,
    "layout_hangar_conflict": run_layout_hangar_conflict,
    "layout_conflict": run_layout_conflict,
    "scenario_via_ref": run_scenario_via_ref,
    "scenario_no_fleet_ref": run_scenario_no_fleet_ref,
    "scenario_no_hangar_ref": run_scenario_no_hangar_ref,
    "scenario_hangar_conflict": run_scenario_hangar_conflict,
    "scenario_conflict": run_scenario_conflict,
    # targeted: hangar error branches
    "hangar_model_invariant_violation": run_hangar,
    "hangar_door_not_mapping": run_hangar,
    "hangar_bay_not_mapping": run_hangar,
    "hangar_missing_required_field": run_hangar,
    # targeted: rare fleet error branches
    "fleet_duplicate_aircraft_id": run_fleet,
    "fleet_aircraft_not_list": run_fleet,
    "fleet_aircraft_entry_not_mapping": run_fleet,
    "fleet_aircraft_missing_required_field": run_fleet,
    "fleet_parts_not_list": run_fleet,
    "fleet_part_not_mapping": run_fleet,
    "fleet_struts_not_mapping": run_fleet,
    "fleet_strut_without_wing": run_fleet,
    "fleet_struts_missing_key": run_fleet,
    "fleet_strut_invalid_geometry": run_fleet,
    "fleet_part_missing_required_field": run_fleet,
    # targeted: rare layout error branches
    "layout_placements_not_list": run_layout,
    "layout_maintenance_plane_in_placements": run_layout,
    "layout_always_cart_on_carts_false": run_layout,
    "layout_placement_not_mapping": run_layout,
    "layout_maintenance_shape": run_layout,
    "layout_case_insensitive_near_miss": run_layout,
    "layout_difflib_near_miss": run_layout,
    # targeted: rare scenario error branches
    "scenario_fleet_in_not_list": run_scenario,
    "scenario_constraints_not_mapping": run_scenario,
    "scenario_constraint_non_dict_data": run_scenario,
    "scenario_pin_shape": run_scenario,
}


def tagged_documents() -> st.SearchStrategy[tuple[str, Any]]:
    return st.one_of(
        # original strategies
        st.tuples(st.just("fleet"), fleet_documents()),
        st.tuples(st.just("hangar"), hangar_documents()),
        st.tuples(st.just("layout"), layout_documents()),
        st.tuples(st.just("scenario"), scenario_documents()),
        st.tuples(st.just("raw"), raw_documents()),
        # ref-resolving helpers
        st.tuples(st.just("layout_via_ref"), layout_documents()),
        st.tuples(st.just("layout_no_fleet_ref"), layout_documents()),
        st.tuples(st.just("layout_no_hangar_ref"), layout_documents()),
        st.tuples(st.just("layout_fleet_conflict"), layout_documents()),
        st.tuples(st.just("layout_hangar_conflict"), layout_documents()),
        st.tuples(st.just("layout_conflict"), layout_documents()),
        st.tuples(st.just("scenario_via_ref"), scenario_documents()),
        st.tuples(st.just("scenario_no_fleet_ref"), scenario_documents()),
        st.tuples(st.just("scenario_no_hangar_ref"), scenario_documents()),
        st.tuples(st.just("scenario_hangar_conflict"), scenario_documents()),
        st.tuples(st.just("scenario_conflict"), scenario_documents()),
        # targeted: hangar error branches
        st.tuples(
            st.just("hangar_model_invariant_violation"),
            hangar_model_invariant_violation_documents(),
        ),
        st.tuples(st.just("hangar_door_not_mapping"), hangar_door_not_mapping_documents()),
        st.tuples(st.just("hangar_bay_not_mapping"), hangar_bay_not_mapping_documents()),
        st.tuples(
            st.just("hangar_missing_required_field"), hangar_missing_required_field_documents()
        ),
        # targeted: rare fleet error branches
        st.tuples(st.just("fleet_duplicate_aircraft_id"), fleet_duplicate_aircraft_id_documents()),
        st.tuples(st.just("fleet_aircraft_not_list"), fleet_aircraft_not_list_documents()),
        st.tuples(
            st.just("fleet_aircraft_entry_not_mapping"),
            fleet_aircraft_entry_not_mapping_documents(),
        ),
        st.tuples(
            st.just("fleet_aircraft_missing_required_field"),
            fleet_aircraft_missing_required_field_documents(),
        ),
        st.tuples(st.just("fleet_parts_not_list"), fleet_parts_not_list_documents()),
        st.tuples(st.just("fleet_part_not_mapping"), fleet_part_not_mapping_documents()),
        st.tuples(st.just("fleet_struts_not_mapping"), fleet_struts_not_mapping_documents()),
        st.tuples(st.just("fleet_strut_without_wing"), fleet_strut_without_wing_documents()),
        st.tuples(st.just("fleet_struts_missing_key"), fleet_struts_missing_key_documents()),
        st.tuples(
            st.just("fleet_strut_invalid_geometry"), fleet_strut_invalid_geometry_documents()
        ),
        st.tuples(
            st.just("fleet_part_missing_required_field"),
            fleet_part_missing_required_field_documents(),
        ),
        # targeted: rare layout error branches
        st.tuples(st.just("layout_placements_not_list"), layout_placements_not_list_documents()),
        st.tuples(
            st.just("layout_maintenance_plane_in_placements"),
            layout_maintenance_plane_in_placements_documents(),
        ),
        st.tuples(
            st.just("layout_always_cart_on_carts_false"),
            layout_always_cart_on_carts_false_documents(),
        ),
        st.tuples(
            st.just("layout_placement_not_mapping"), layout_placement_not_mapping_documents()
        ),
        st.tuples(st.just("layout_maintenance_shape"), layout_maintenance_shape_documents()),
        st.tuples(
            st.just("layout_case_insensitive_near_miss"),
            layout_case_insensitive_near_miss_documents(),
        ),
        st.tuples(st.just("layout_difflib_near_miss"), layout_difflib_near_miss_documents()),
        # targeted: rare scenario error branches
        st.tuples(st.just("scenario_fleet_in_not_list"), scenario_fleet_in_not_list_documents()),
        st.tuples(
            st.just("scenario_constraints_not_mapping"),
            scenario_constraints_not_mapping_documents(),
        ),
        st.tuples(
            st.just("scenario_constraint_non_dict_data"),
            scenario_constraint_non_dict_data_documents(),
        ),
        st.tuples(st.just("scenario_pin_shape"), scenario_pin_shape_documents()),
    )


def run_tagged(tagged: tuple[str, Any]) -> None:
    tag, doc = tagged
    _RUNNERS[tag](doc)
