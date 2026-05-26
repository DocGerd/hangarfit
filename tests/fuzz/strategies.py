"""Hypothesis strategies + run-helpers for fuzzing the hangarfit YAML loader.

Shared by the pytest property suite (``test_loader_fuzz.py``) and the Atheris
bridge harness (``atheris_loader_harness.py``) so input construction lives in
exactly one place. Each ``run_*`` helper encodes the loader contract: the
loader must either return a model or raise ``LoaderError``; any other
exception propagates and is reported as a fuzz finding.
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
            "measured": _scalars,
            "struts": _struts_docs(),
            "notes": _safe_text,
        },
    )
    top = st.fixed_dictionaries({"aircraft": st.lists(_maybe_drop_keys(aircraft), max_size=4)})
    return st.one_of(_maybe_drop_keys(top), st.none(), st.lists(st.integers()), _safe_text)


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
    return st.one_of(_maybe_drop_keys(top), st.none(), _safe_text)


def layout_documents() -> st.SearchStrategy[Any]:
    placement = st.fixed_dictionaries(
        {"plane": _plane_ids, "x_m": _scalars, "y_m": _scalars, "heading_deg": _scalars},
        optional={"on_carts": _scalars},
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
    return st.one_of(_maybe_drop_keys(top), st.none(), _safe_text)


def scenario_documents() -> st.SearchStrategy[Any]:
    pin = st.fixed_dictionaries(
        {"x_m": _scalars, "y_m": _scalars, "heading_deg": _scalars, "on_carts": _scalars}
    )
    constraint = st.fixed_dictionaries(
        {},
        optional={"pin": st.one_of(_maybe_drop_keys(pin), st.none()), "force_on_carts": _scalars},
    )
    top = st.fixed_dictionaries(
        {"fleet_in": st.lists(_plane_ids, max_size=4)},
        optional={
            "maintenance": st.one_of(st.none(), st.builds(lambda p: {"plane": p}, _plane_ids)),
            "constraints": st.dictionaries(_plane_ids, constraint, max_size=3),
        },
    )
    return st.one_of(_maybe_drop_keys(top), st.none(), _safe_text)


def raw_documents() -> st.SearchStrategy[Any]:
    """Raw parse-layer inputs: arbitrary text/bytes fed straight to a loader,
    exercising _read_yaml and the top-level-shape guards. st.binary() covers
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
