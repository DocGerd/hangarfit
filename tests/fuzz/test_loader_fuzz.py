"""Property tests for the YAML loader.

Invariant under test for every entry point: given any near-valid (or raw)
document, the loader must either return a model or raise ``LoaderError`` — never
a bare KeyError/AttributeError/IndexError/TypeError/ValueError/RecursionError.
The run-helpers in ``strategies`` swallow only ``LoaderError``; any other
exception propagates here and Hypothesis shrinks it to a minimal repro.

Runs under the ``ci`` profile by default (fast, every PR); the nightly workflow
sets HYPOTHESIS_PROFILE=nightly for a deep run.

Test groups
-----------
Original properties (every PR, ~50 examples each):
  test_load_fleet_never_crashes, test_load_hangar_never_crashes,
  test_load_layout_never_crashes, test_load_scenario_never_crashes,
  test_load_raw_input_never_crashes

Ref-resolution properties (#253 — closes the fleet:/hangar: branch gap):
  test_load_layout_via_ref_never_crashes
    → load_layout called WITHOUT fleet=/hangar= overrides; YAML references
      fleet.yaml / hangar.yaml by relative path.  Covers:
        • the relative-join: (path.parent / fleet_ref).resolve()
        • the "field required when no override" raise
  test_load_layout_conflict_never_crashes
    → YAML has 'fleet'/'hangar' fields AND override kwargs are passed.
      Covers the "set in YAML and override provided" conflict raise.
  test_load_scenario_via_ref_never_crashes / _conflict — same for load_scenario.

Targeted properties (#253 — closes rare loader error branches):
  test_fleet_aircraft_not_list_never_crashes   → load_fleet L90
  test_fleet_aircraft_entry_not_mapping_…      → load_fleet L95
  test_fleet_strut_without_wing_…              → _build_aircraft L555
  test_fleet_struts_missing_key_…              → _build_struts_spec L603
  test_fleet_strut_invalid_geometry_…          → _expand_struts L624/L631
  test_layout_placement_not_mapping_…          → _build_placement L664
  test_layout_maintenance_shape_…              → _extract_maintenance_plane L448-L465
  test_scenario_fleet_in_not_list_…            → load_scenario L291
  test_scenario_constraints_not_mapping_…      → load_scenario L352/L354
  test_scenario_constraint_non_dict_data_…     → _build_plane_constraint L411
  test_scenario_pin_shape_…                    → _build_plane_constraint L417/L423/L434
"""

from __future__ import annotations

from hypothesis import given

from tests.fuzz import strategies as s

# ---------------------------------------------------------------------------
# Original properties (unchanged from #143)
# ---------------------------------------------------------------------------


@given(s.fleet_documents())
def test_load_fleet_never_crashes(doc):
    s.run_fleet(doc)


@given(s.hangar_documents())
def test_load_hangar_never_crashes(doc):
    s.run_hangar(doc)


@given(s.layout_documents())
def test_load_layout_never_crashes(doc):
    s.run_layout(doc)


@given(s.scenario_documents())
def test_load_scenario_never_crashes(doc):
    s.run_scenario(doc)


@given(s.raw_documents())
def test_load_raw_input_never_crashes(doc):
    s.run_raw(doc)


# ---------------------------------------------------------------------------
# Ref-resolution properties (#253 — Gap 1)
#
# The existing helpers always pass valid fleet=/hangar= override kwargs, so
# the branches inside load_layout / load_scenario that read 'fleet:' /
# 'hangar:' YAML fields are never exercised.  These properties close that gap.
# ---------------------------------------------------------------------------


@given(s.layout_documents())
def test_load_layout_via_ref_never_crashes(doc):
    """load_layout WITHOUT override kwargs; YAML provides relative fleet/hangar refs.

    Reaches: relative-join (path.parent / fleet_ref).resolve().
    """
    s.run_layout_via_ref(doc)


@given(s.layout_documents())
def test_load_layout_no_fleet_ref_never_crashes(doc):
    """load_layout with no fleet= override AND no 'fleet:' in YAML.

    Reaches: load_layout L187 — "fleet field is required when no override".
    """
    s.run_layout_no_fleet_ref(doc)


@given(s.layout_documents())
def test_load_layout_no_hangar_ref_never_crashes(doc):
    """load_layout with valid fleet ref but no 'hangar:' in YAML and no hangar= override.

    Reaches: load_layout L200 — "hangar field is required when no override".
    """
    s.run_layout_no_hangar_ref(doc)


@given(s.layout_documents())
def test_load_layout_fleet_conflict_never_crashes(doc):
    """YAML has 'fleet:' field AND fleet= override kwarg supplied.

    Reaches: load_layout L192 — "fleet field is set but override also provided".
    """
    s.run_layout_fleet_conflict(doc)


@given(s.layout_documents())
def test_load_layout_hangar_conflict_never_crashes(doc):
    """YAML has valid fleet ref + 'hangar:' field AND hangar= override kwarg.

    Reaches: load_layout L205 — "hangar field is set but override also provided".
    """
    s.run_layout_hangar_conflict(doc)


@given(s.layout_documents())
def test_load_layout_conflict_never_crashes(doc):
    """YAML has 'fleet:' field AND fleet= override kwarg supplied (combined path)."""
    s.run_layout_conflict(doc)


@given(s.scenario_documents())
def test_load_scenario_via_ref_never_crashes(doc):
    """load_scenario WITHOUT override kwargs; YAML provides relative fleet/hangar refs.

    Reaches: relative-join path in load_scenario.
    """
    s.run_scenario_via_ref(doc)


@given(s.scenario_documents())
def test_load_scenario_no_fleet_ref_never_crashes(doc):
    """load_scenario with no fleet= override AND no 'fleet:' in YAML.

    Reaches: load_scenario L298 — "fleet field is required when no override".
    """
    s.run_scenario_no_fleet_ref(doc)


@given(s.scenario_documents())
def test_load_scenario_no_hangar_ref_never_crashes(doc):
    """load_scenario with valid fleet ref but no 'hangar:' and no hangar= override.

    Reaches: load_scenario L311 — "hangar field is required when no override".
    """
    s.run_scenario_no_hangar_ref(doc)


@given(s.scenario_documents())
def test_load_scenario_hangar_conflict_never_crashes(doc):
    """YAML has valid fleet ref + 'hangar:' AND hangar= override kwarg.

    Reaches: load_scenario L316 — "hangar field is set but override also provided".
    """
    s.run_scenario_hangar_conflict(doc)


@given(s.scenario_documents())
def test_load_scenario_conflict_never_crashes(doc):
    """YAML has 'fleet:' field AND fleet= override kwarg supplied (combined path)."""
    s.run_scenario_conflict(doc)


# ---------------------------------------------------------------------------
# Targeted properties (#253 — Gap 2: rare error branches)
# ---------------------------------------------------------------------------


@given(s.hangar_model_invariant_violation_documents())
def test_hangar_model_invariant_violation_never_crashes(doc):
    """Hangar structurally valid but violates model invariant → load_hangar L156 ValueError wrap."""
    s.run_hangar(doc)


@given(s.hangar_door_not_mapping_documents())
def test_hangar_door_not_mapping_never_crashes(doc):
    """Hangar 'door' is not a dict → load_hangar L118."""
    s.run_hangar(doc)


@given(s.hangar_bay_not_mapping_documents())
def test_hangar_bay_not_mapping_never_crashes(doc):
    """Hangar has valid door dict but maintenance_bay is not a dict → load_hangar L122."""
    s.run_hangar(doc)


@given(s.hangar_missing_required_field_documents())
def test_hangar_missing_required_field_never_crashes(doc):
    """Hangar YAML missing one top-level or sub-dict required key → load_hangar L129/L132/L135."""
    s.run_hangar(doc)


@given(s.fleet_duplicate_aircraft_id_documents())
def test_fleet_duplicate_aircraft_id_never_crashes(doc):
    """Two aircraft entries share the same id → load_fleet L104 ("duplicate aircraft id")."""
    s.run_fleet(doc)


@given(s.fleet_aircraft_not_list_documents())
def test_fleet_aircraft_not_list_never_crashes(doc):
    """'aircraft' key present but value is not a list → load_fleet L90."""
    s.run_fleet(doc)


@given(s.fleet_aircraft_entry_not_mapping_documents())
def test_fleet_aircraft_entry_not_mapping_never_crashes(doc):
    """'aircraft' is a list but an entry is not a dict → load_fleet L95."""
    s.run_fleet(doc)


@given(s.fleet_aircraft_missing_required_field_documents())
def test_fleet_aircraft_missing_required_field_never_crashes(doc):
    """Aircraft dict missing exactly one required key → _build_aircraft L542."""
    s.run_fleet(doc)


@given(s.fleet_parts_not_list_documents())
def test_fleet_parts_not_list_never_crashes(doc):
    """Aircraft 'parts' value is not a list or is empty → _build_aircraft L546."""
    s.run_fleet(doc)


@given(s.fleet_part_not_mapping_documents())
def test_fleet_part_not_mapping_never_crashes(doc):
    """Parts list entry is not a dict → _build_part L576."""
    s.run_fleet(doc)


@given(s.fleet_struts_not_mapping_documents())
def test_fleet_struts_not_mapping_never_crashes(doc):
    """Aircraft has 'struts' key with a non-dict value → _build_aircraft L551."""
    s.run_fleet(doc)


@given(s.fleet_strut_without_wing_documents())
def test_fleet_strut_without_wing_never_crashes(doc):
    """Aircraft has 'struts' but no wing part → _build_aircraft L555."""
    s.run_fleet(doc)


@given(s.fleet_struts_missing_key_documents())
def test_fleet_struts_missing_key_never_crashes(doc):
    """Aircraft 'struts' dict is missing a required key → _build_struts_spec L603."""
    s.run_fleet(doc)


@given(s.fleet_strut_invalid_geometry_documents())
def test_fleet_strut_invalid_geometry_never_crashes(doc):
    """Strut geometry violates z-ordering or positive-span guards → _expand_struts L624/L631."""
    s.run_fleet(doc)


@given(s.fleet_part_missing_required_field_documents())
def test_fleet_part_missing_required_field_never_crashes(doc):
    """Part dict missing exactly one required key → _build_part L580."""
    s.run_fleet(doc)


@given(s.layout_placements_not_list_documents())
def test_layout_placements_not_list_never_crashes(doc):
    """'placements' value is not a list → load_layout L212."""
    s.run_layout(doc)


@given(s.layout_maintenance_plane_in_placements_documents())
def test_layout_maintenance_plane_in_placements_never_crashes(doc):
    """Maintenance plane also listed in placements → load_layout pre-check L236-L241."""
    s.run_layout(doc)


@given(s.layout_always_cart_on_carts_false_documents())
def test_layout_always_cart_on_carts_false_never_crashes(doc):
    """always_cart plane placed with on_carts=False → Layout.__post_init__ ValueError → L250-251."""
    s.run_layout(doc)


@given(s.layout_placement_not_mapping_documents())
def test_layout_placement_not_mapping_never_crashes(doc):
    """'placements' list contains a non-dict entry → _build_placement L664."""
    s.run_layout(doc)


@given(s.layout_maintenance_shape_documents())
def test_layout_maintenance_shape_never_crashes(doc):
    """Various malformed 'maintenance' block shapes → _extract_maintenance_plane L448-L465."""
    s.run_layout(doc)


@given(s.layout_case_insensitive_near_miss_documents())
def test_layout_case_insensitive_near_miss_never_crashes(doc):
    """Placement id is a case-insensitive match for a valid id → _suggest_plane_id L499."""
    s.run_layout(doc)


@given(s.layout_difflib_near_miss_documents())
def test_layout_difflib_near_miss_never_crashes(doc):
    """Placement id is a difflib near-match (not case-fold) → _suggest_plane_id L502."""
    s.run_layout(doc)


@given(s.scenario_fleet_in_not_list_documents())
def test_scenario_fleet_in_not_list_never_crashes(doc):
    """'fleet_in' is not a list → load_scenario L291."""
    s.run_scenario(doc)


@given(s.scenario_constraints_not_mapping_documents())
def test_scenario_constraints_not_mapping_never_crashes(doc):
    """'constraints' is null or not a mapping → load_scenario L352/L354."""
    s.run_scenario(doc)


@given(s.scenario_constraint_non_dict_data_documents())
def test_scenario_constraint_non_dict_data_never_crashes(doc):
    """Constraint value is not a mapping → _build_plane_constraint L411."""
    s.run_scenario(doc)


@given(s.scenario_pin_shape_documents())
def test_scenario_pin_shape_never_crashes(doc):
    """Pin is not a mapping, missing required key, or force_on_carts not bool
    → _build_plane_constraint L417/L423/L434."""
    s.run_scenario(doc)


# ---------------------------------------------------------------------------
# Branches documented as example-test-only
#
# The following loader branches are NOT exercised by this fuzz suite because
# they are only reachable via direct Python API calls — the YAML entry-point
# path has an upstream guard that prevents the input from ever reaching them:
#
# _build_aircraft L537: "aircraft entry must be a mapping, got ..."
#   - Shadowed by load_fleet L94 which checks isinstance(entry, dict) BEFORE
#     calling _build_aircraft(entry). _build_aircraft is only called on dicts.
#   - Covered by direct-call tests in tests/test_loader.py.
#
# _read_yaml L683: "file not found: {path}"
#   - All run-helpers write the YAML file to disk before calling the loader.
#     A file-not-found error can only happen if the temp file is deleted
#     between writing and reading, which doesn't occur in normal fuzz flow.
#   - Covered by tests/test_loader.py::test_load_fleet_missing_file (or similar).
# ---------------------------------------------------------------------------
