"""Rung B (#667): the bench harness's witness-layout routing mode + the
Herrenteich routing-ceiling regimes.

The Herrenteich all-eight is **statically valid** (``hangarfit check`` exits 0)
but ``solve`` provably cannot reproduce it (``examples/herrenteich/scenario.yaml``
header), and the dense nest does not fully tow-route (the ``fk9↔cessna`` wall,
spikes 2026-06). So the routing-ceiling baseline that Rungs C–E are graded
against must route the *known-valid witness layout* directly — not a solve
output. This module covers that bench-only ``Regime.layout`` mode and the two
witness regimes.

The witness mechanism is exercised against a small, fast, fully-routable layout
(``valid_left_side_nesting.yaml``, 2 bodies, ~0.5 s) so the contract is verified
without the slow all-eight grind; the slow all-eight determinism guard is marked
``@pytest.mark.slow``.
"""

from __future__ import annotations

import pytest

from bench.harness import run_regime
from bench.profile_pipeline import _SPEED_CEILING_S
from bench.regimes import FIXTURES, REGIMES, Regime, regime_by_key

_FAST_WITNESS = FIXTURES / "valid_left_side_nesting.yaml"


def _fast_witness_regime() -> Regime:
    return Regime(
        key="_test_fast_witness",
        description="fast fully-routable witness (mechanism test)",
        layout=_FAST_WITNESS,
        n_planes=2,
        heavy=True,
    )


# ── Regime contract: exactly one of scenario / layout ────────────────────────


def test_regime_requires_exactly_one_source() -> None:
    """A regime drives EITHER a solve (``scenario``) OR a direct witness route
    (``layout``) — never both, never neither. The mis-specified cases raise
    loudly so a typo'd regime cannot silently route nothing (silent-failure)."""
    with pytest.raises(ValueError, match="exactly one"):
        Regime(key="neither", description="no source", n_planes=1)
    with pytest.raises(ValueError, match="exactly one"):
        Regime(
            key="both",
            description="two sources",
            scenario=FIXTURES / "solve_trivial_single_plane.yaml",
            layout=_FAST_WITNESS,
            n_planes=1,
        )


# ── Witness routing mode in run_regime ───────────────────────────────────────


def test_witness_regime_skips_solve_and_routes_layout() -> None:
    """A witness regime loads the layout directly and routes it — no solve.

    Placement is skipped entirely (``placement_s == 0.0``, ``restarts_done == 0``)
    and exactly one layout (the witness) is routed. The fast witness fully routes,
    so it scores valid + path-valid.
    """
    result = run_regime(_fast_witness_regime())
    assert result.restarts_done == 0
    assert result.placement_s == 0.0
    assert result.n_layouts == 1
    assert result.n_routed == 1
    assert result.layouts_valid is True
    assert result.paths_valid is True


def test_witness_regime_is_deterministic() -> None:
    """Routing the witness twice yields a byte-identical digest (ADR-0003)."""
    assert run_regime(_fast_witness_regime()).deterministic is True


# ── Herrenteich witness regimes are registered + gated ───────────────────────


@pytest.mark.parametrize(
    ("key", "layout_name", "n_planes"),
    [
        ("herrenteich_all_eight", "layout.yaml", 8),
        ("herrenteich_today", "layout_today.yaml", 9),
    ],
)
def test_herrenteich_witness_regime_registered(key: str, layout_name: str, n_planes: int) -> None:
    """Both Herrenteich witness regimes exist, are witness-mode (``layout`` set,
    ``scenario`` unset), heavy (excluded from the default fast gate), and carry a
    documentary speed ceiling."""
    regime = regime_by_key(key)
    assert regime.scenario is None
    assert regime.layout is not None
    assert regime.layout.name == layout_name
    assert regime.n_planes == n_planes
    assert regime.heavy is True
    assert key in _SPEED_CEILING_S


def test_herrenteich_regimes_are_excluded_from_fast_gate() -> None:
    """The witness regimes are heavy, so the default fast `--gate` (fast set
    only) never grinds the multi-minute all-eight route in CI."""
    from bench.regimes import FAST_REGIMES

    fast_keys = {r.key for r in FAST_REGIMES}
    assert "herrenteich_all_eight" not in fast_keys
    assert "herrenteich_today" not in fast_keys
    # but they are part of the full set (run under --heavy)
    all_keys = {r.key for r in REGIMES}
    assert {"herrenteich_all_eight", "herrenteich_today"} <= all_keys


@pytest.mark.slow
def test_herrenteich_all_eight_routing_ceiling_baseline() -> None:
    """The #667 routing-ceiling BASELINE, as a regression guard (~130 s — slow).

    The hand-authored all-eight witness is statically valid, but the dense fill
    does **not** tow-route within the bounded budget — it exhausts the cap and
    bails (measured 2026-06-27). The route is RNG-free, so the verdict is
    deterministic. This anchors the wall move-aside (Rung E) must raise: when
    Rung E lands, ``n_routed`` is expected to flip to 1 and this assertion is
    updated as the deliberate grading signal (spec §4 Rung E acceptance 3).

    @pytest.mark.slow is INTENTIONAL: ``run_regime`` routes the all-8 twice (the
    determinism check), ~130 s total, so it is excluded from the default `pytest`
    run and from CI (``addopts = -m 'not slow'``).
    """
    result = run_regime(regime_by_key("herrenteich_all_eight"))
    assert result.layouts_valid is True, "witness layout must be statically valid"
    assert result.placement_s == 0.0, "witness routes directly — no placement"
    assert result.deterministic is True, "RNG-free routing must be deterministic"
    assert result.n_routed == 0, (
        "BASELINE: the dense all-8 does not route within budget (the #667 wall). "
        "When move-aside (Rung E) raises the ceiling, flip this to 1."
    )
    assert result.notes and result.notes[0].startswith("un-routable:")
