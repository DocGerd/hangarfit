"""#614 — load_scenario parses the top-level ``door_order:`` key."""

import pytest

from hangarfit.loader import LoaderError, load_scenario


def test_load_scenario_door_order():
    s = load_scenario("tests/fixtures/scenario_door_order.yaml")
    assert s.door_order == ("ctsl", "aviat_husky")


def test_load_scenario_no_door_order_is_none():
    s = load_scenario("tests/fixtures/scenario_minimal.yaml")
    assert s.door_order is None  # absent ⇒ inert (byte-identical, ADR-0003)


def test_load_scenario_door_order_unknown_id_rejected():
    with pytest.raises(LoaderError, match="door_order"):
        load_scenario("tests/fixtures/scenario_door_order_bad.yaml")
