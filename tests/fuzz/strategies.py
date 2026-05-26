"""Hypothesis strategies + run-helpers for fuzzing the hangarfit YAML loader.

Shared by the pytest property suite (``test_loader_fuzz.py``) and the Atheris
bridge harness (``atheris_loader_harness.py``) so input construction lives in
exactly one place. Each ``run_*`` helper encodes the loader contract: the
loader must either return normally (a model object or a fleet dict) or raise
``LoaderError``; any other exception propagates and is reported as a fuzz
finding.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import yaml
from hypothesis import strategies as st

from hangarfit import loader
from hangarfit.loader import LoaderError
from hangarfit.models import Aircraft, Door, Hangar, MaintenanceBay, Part

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
    kind="fuselage",
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


# --- tagged union for the Atheris single-target harness ---
_RUNNERS = {
    "fleet": run_fleet,
    "hangar": run_hangar,
    "layout": run_layout,
    "scenario": run_scenario,
    "raw": run_raw,
}


def tagged_documents() -> st.SearchStrategy[tuple[str, Any]]:
    return st.one_of(
        st.tuples(st.just("fleet"), fleet_documents()),
        st.tuples(st.just("hangar"), hangar_documents()),
        st.tuples(st.just("layout"), layout_documents()),
        st.tuples(st.just("scenario"), scenario_documents()),
        st.tuples(st.just("raw"), raw_documents()),
    )


def run_tagged(tagged: tuple[str, Any]) -> None:
    tag, doc = tagged
    _RUNNERS[tag](doc)
