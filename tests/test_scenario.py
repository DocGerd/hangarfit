"""Tests for the Scenario / PlaneConstraint dataclasses in models.py."""

from __future__ import annotations

from hangarfit.models import (
    Placement,
    PlaneConstraint,
)

# ── PlaneConstraint ─────────────────────────────────────────────────────


def test_plane_constraint_default_is_free():
    """A constraint with no fields set means 'free'."""
    c = PlaneConstraint()
    assert c.pin is None
    assert c.force_on_carts is None


def test_plane_constraint_can_carry_pin():
    p = Placement(plane_id="aviat_husky", x_m=2.1, y_m=14.3, heading_deg=0.0, on_carts=False)
    c = PlaneConstraint(pin=p)
    assert c.pin == p


def test_plane_constraint_can_carry_force_on_carts():
    c = PlaneConstraint(force_on_carts=True)
    assert c.force_on_carts is True
    assert c.pin is None
