"""Reverse-teardown read-only feasibility probe (#667 Rung C).

The probe generalises the single-body :func:`egress_first_conflict` into a
whole-fill teardown: greedily extract every tow-routable aircraft slot -> door
against shrinking partial state and report whether a full teardown (<=> monotone
fill, by ADR-0010 reversibility) order EXISTS, plus the canonical
mutually-blocking residual core when it does not.

It is a **read-only diagnostic**: no plan output, no data-model change, no
production caller -> existing plans stay byte-identical (ADR-0003). These tests
pin (1) the real-geometry clear/determinism behaviour, (2) the greedy-peel
traversal + canonical stuck core via a monkeypatched egress seam (the same
isolate-the-loop pattern test_towplanner_fill.py uses for plan_fill), and
(3) that hand-placed bodies and ground objects are fixed obstacles, never
extraction targets.
"""

from pathlib import Path

import pytest

import hangarfit.towplanner as tp
from hangarfit.loader import load_layout
from hangarfit.models import (
    Aircraft,
    Conflict,
    Door,
    GroundObject,
    Hangar,
    Layout,
    MaintenanceBay,
    Part,
    Placement,
    Wheels,
)
from hangarfit.towplanner import TeardownProbeResult, reverse_teardown_probe

HERRENTEICH = Path(__file__).resolve().parent.parent / "examples" / "herrenteich"

_TAIL_WHEELS = Wheels(main_offset_x_m=0.20, track_m=1.8, third_wheel_offset_x_m=-2.0)


def _fuselage_box() -> Part:
    """A 1.0 m x 0.6 m fuselage box forward of the plane origin (mirrors the
    test_towplanner_fill.py fixture: a front-wall placement keeps every world
    vertex at y >= 0)."""
    return Part(
        kind="fuselage_aft",
        length_m=1.0,
        width_m=0.6,
        offset_x_m=0.5,
        offset_y_m=0.0,
        angle_deg=0.0,
        z_bottom_m=0.0,
        z_top_m=1.0,
    )


def _box_plane(plane_id: str, *, turn_radius_m: float = 4.0) -> Aircraft:
    return Aircraft(
        id=plane_id,
        name=f"Plane {plane_id}",
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_own_gear",
        turn_radius_m=turn_radius_m,
        measured=False,
        parts=(_fuselage_box(),),
        wheels=_TAIL_WHEELS,
    )


def _hangar(width_m: float = 20.0, length_m: float = 30.0) -> Hangar:
    return Hangar(
        length_m=length_m,
        width_m=width_m,
        door=Door(center_x_m=width_m / 2, width_m=6.0),
        maintenance_bay=MaintenanceBay(center_x_m=width_m / 2, width_m=2.0, depth_m=2.0),
        clearance_m=0.5,
        wing_layer_clearance_m=0.3,
    )


def _slot(pid: str, x: float, y: float, h: float = 0.0, on_carts: bool = False) -> Placement:
    return Placement(plane_id=pid, x_m=x, y_m=y, heading_deg=h, on_carts=on_carts)


def _ground_part() -> Part:
    return Part(
        kind="ground",
        length_m=2.0,
        width_m=2.0,
        offset_x_m=0.0,
        offset_y_m=0.0,
        angle_deg=0.0,
        z_bottom_m=0.0,
        z_top_m=1.5,
    )


# ── Real-geometry behaviour ──────────────────────────────────────────────────


def test_probe_clears_a_roomy_layout() -> None:
    """Two well-separated box planes in a roomy hangar both drive out -> a full
    teardown order exists (cleared, empty stuck core), and every extracted body
    appears once in the discovered order."""
    h = _hangar()
    fleet = {"a": _box_plane("a"), "b": _box_plane("b")}
    target = Layout(
        fleet=fleet, hangar=h, placements=(_slot("a", 6.0, 8.0), _slot("b", 14.0, 20.0))
    )

    result = reverse_teardown_probe(target)

    assert isinstance(result, TeardownProbeResult)
    assert result.cleared is True
    assert result.stuck == ()
    assert result.blocking == ()
    assert set(result.order) == {"a", "b"}
    assert len(result.order) == 2  # each body extracted exactly once


def test_probe_is_deterministic() -> None:
    """Same layout -> equal verdict (value equality via frozen-dataclass __eq__;
    id-sorted peel + RNG-free routing make the result reproducible)."""
    h = _hangar()
    fleet = {"a": _box_plane("a"), "b": _box_plane("b")}
    target = Layout(
        fleet=fleet, hangar=h, placements=(_slot("a", 6.0, 8.0), _slot("b", 14.0, 20.0))
    )

    assert reverse_teardown_probe(target) == reverse_teardown_probe(target)


def test_probe_does_not_extract_hand_placed_bodies() -> None:
    """A hand-placed (dolly) body is a fixed obstacle throughout, never an
    extraction target — it appears in neither the order nor the stuck core."""
    h = _hangar()
    fleet = {"a": _box_plane("a"), "g": _box_plane("g")}
    target = Layout(
        fleet=fleet,
        hangar=h,
        placements=(
            _slot("a", 6.0, 8.0),
            Placement("g", x_m=14.0, y_m=20.0, heading_deg=0.0, on_carts=False, hand_placed=True),
        ),
    )

    result = reverse_teardown_probe(target)

    assert "g" not in result.order
    assert "g" not in result.stuck
    assert "a" in result.order  # the only routable body did get extracted


def test_probe_does_not_extract_ground_objects() -> None:
    """Ground objects (cars/trailers) are fixed obstacles, not aircraft to tow
    out — they are never extraction targets."""
    h = _hangar()
    ac = _box_plane("a")
    obj = GroundObject(id="car", name="car", parts=(_ground_part(),), object_class="fixed_obstacle")
    target = Layout(
        fleet={ac.id: ac},
        hangar=h,
        placements=(_slot("a", 6.0, 8.0),),
        ground_objects={obj.id: obj},
        ground_object_placements=(_slot("car", 14.0, 3.0),),
    )

    result = reverse_teardown_probe(target)

    assert "car" not in result.order
    assert "car" not in result.stuck


# ── Greedy-peel traversal (loop logic isolated via the egress seam) ──────────
# Mirrors test_towplanner_fill.py: monkeypatch the per-candidate routing seam so
# the traversal is exercised without any geometry search or budget.


def _scripted(monkeypatch, decide) -> None:
    """Replace the probe's per-body egress seam with ``decide(plane_id, present)``
    -> Conflict | None, where ``present`` is the id set still parked (excluding the
    body being extracted)."""

    def fake(placement, placed, hangar, fleet, *, heuristic, max_expansions):
        present = frozenset(p.plane_id for p in placed.placements)
        return decide(placement.plane_id, present)

    monkeypatch.setattr(tp, "_aircraft_egress_conflict", fake)


def test_probe_peels_in_rounds_freeing_blocked_bodies(monkeypatch) -> None:
    """``b`` cannot egress while ``a`` is still parked; ``a`` is always clear. The
    peel extracts ``a`` first, which frees ``b`` -> a full order exists."""
    h = _hangar()
    fleet = {"a": _box_plane("a"), "b": _box_plane("b")}
    target = Layout(
        fleet=fleet, hangar=h, placements=(_slot("a", 6.0, 8.0), _slot("b", 14.0, 20.0))
    )

    def decide(pid: str, present: frozenset[str]) -> Conflict | None:
        if pid == "b" and "a" in present:
            return Conflict.single(kind="teardown_egress", plane="b", detail="blocked by a")
        return None

    _scripted(monkeypatch, decide)
    result = reverse_teardown_probe(target)

    assert result.cleared is True
    assert result.order == ("a", "b")  # a peeled first, then b
    assert result.stuck == ()


def test_probe_reports_mutually_blocking_core(monkeypatch) -> None:
    """When no remaining body can egress against the rest, the peel stalls and
    reports the canonical residual core plus one blocking conflict per body."""
    h = _hangar()
    fleet = {"a": _box_plane("a"), "b": _box_plane("b")}
    target = Layout(
        fleet=fleet, hangar=h, placements=(_slot("a", 6.0, 8.0), _slot("b", 14.0, 20.0))
    )

    def decide(pid: str, present: frozenset[str]) -> Conflict | None:
        return Conflict.single(kind="teardown_egress", plane=pid, detail="wedged")

    _scripted(monkeypatch, decide)
    result = reverse_teardown_probe(target)

    assert result.cleared is False
    assert result.order == ()
    assert result.stuck == ("a", "b")  # id-sorted residual core
    assert tuple(c.planes[0] for c in result.blocking) == ("a", "b")
    assert all(c.kind == "teardown_egress" for c in result.blocking)


def test_probe_peel_order_is_id_sorted_within_a_round(monkeypatch) -> None:
    """All bodies egress in one round -> the discovered order is id-sorted
    (deterministic), regardless of placement order in the layout."""
    h = _hangar()
    fleet = {k: _box_plane(k) for k in ("b", "a", "c")}
    target = Layout(
        fleet=fleet,
        hangar=h,
        # deliberately NOT id-sorted in the layout
        placements=(_slot("c", 14.0, 20.0), _slot("a", 6.0, 8.0), _slot("b", 10.0, 14.0)),
    )

    _scripted(monkeypatch, lambda pid, present: None)  # everyone always clear
    result = reverse_teardown_probe(target)

    assert result.cleared is True
    assert result.order == ("a", "b", "c")


def test_probe_passes_obstacles_to_the_seam_in_deterministic_order(monkeypatch) -> None:
    """The still-parked obstacles handed to the egress seam must be id-sorted, not
    set-iteration order: iterating a set of plane-id strings varies with
    PYTHONHASHSEED across processes, which would make the blocking-conflict details
    non-byte-identical (ADR-0003). Layout order is deliberately non-sorted so the
    contract is observable."""
    h = _hangar()
    fleet = {k: _box_plane(k) for k in ("d", "a", "c", "b")}
    target = Layout(
        fleet=fleet,
        hangar=h,
        placements=(
            _slot("d", 14.0, 20.0),
            _slot("a", 6.0, 8.0),
            _slot("c", 10.0, 14.0),
            _slot("b", 8.0, 10.0),
        ),
    )
    seen: list[tuple[str, ...]] = []

    def fake(placement, placed, hangar, fleet, *, heuristic, max_expansions):
        seen.append(tuple(p.plane_id for p in placed.placements))
        return None  # everyone clear -> one round, one check per body

    monkeypatch.setattr(tp, "_aircraft_egress_conflict", fake)
    reverse_teardown_probe(target)

    # First body checked is the id-min ("a"); its obstacles are the rest, id-sorted.
    assert seen[0] == ("b", "c", "d")


# ── Result type invariants + exhaustion-mode diagnostics ─────────────────────


def test_result_cleared_is_derived_from_stuck() -> None:
    """`cleared` is a derived property (no representable illegal state where
    `cleared` disagrees with `stuck`), mirroring CheckResult.valid."""
    c = Conflict.single(kind="teardown_egress", plane="a", detail="x")
    assert TeardownProbeResult(order=("a",), stuck=(), blocking=()).cleared is True
    assert TeardownProbeResult(order=(), stuck=("a",), blocking=(c,)).cleared is False


def test_result_rejects_blocking_stuck_misalignment() -> None:
    """The result enforces blocking <-> stuck positional alignment at construction
    (house style: Conflict/CheckResult validate in __post_init__)."""
    c_a = Conflict.single(kind="teardown_egress", plane="a", detail="x")
    with pytest.raises(ValueError):  # len mismatch
        TeardownProbeResult(order=(), stuck=("a",), blocking=())
    with pytest.raises(ValueError):  # wrong plane in blocking
        TeardownProbeResult(order=(), stuck=("b",), blocking=(c_a,))


def test_result_rejects_order_stuck_overlap() -> None:
    """order and stuck must be disjoint (a body is either extracted or stuck)."""
    c = Conflict.single(kind="teardown_egress", plane="a", detail="x")
    with pytest.raises(ValueError):
        TeardownProbeResult(order=("a",), stuck=("a",), blocking=(c,))


def test_egress_conflict_detail_flags_exhaustion_mode(monkeypatch) -> None:
    """The teardown_egress conflict detail records WHICH exhaustion mode bailed
    (budget vs space), so a STUCK core can self-certify search-efficiency (budget)
    vs a discretization lock (space) from its structured output, not eyeballing."""
    h = _hangar()
    ac = _box_plane("a")
    placement = _slot("a", 6.0, 8.0)
    placed = Layout(fleet={"a": ac}, hangar=h, placements=())

    def make_fake(*, budget: bool):
        def fake_plan_path(*args, stats=None, **kwargs):
            if stats is not None:
                stats.update(budget_exhausted=budget, space_exhausted=not budget)
            raise tp.NoFeasiblePlanError(
                "a", Conflict.single(kind="no_feasible_path", plane="a", detail="x")
            )

        return fake_plan_path

    monkeypatch.setattr(tp, "plan_path", make_fake(budget=True))
    c_budget = tp._aircraft_egress_conflict(
        placement, placed, h, {"a": ac}, heuristic="grid", max_expansions=8000
    )
    assert c_budget is not None and "budget-exhausted" in c_budget.detail

    monkeypatch.setattr(tp, "plan_path", make_fake(budget=False))
    c_space = tp._aircraft_egress_conflict(
        placement, placed, h, {"a": ac}, heuristic="grid", max_expansions=8000
    )
    assert c_space is not None and "space-exhausted" in c_space.detail

    # If plan_path ever raised without populating stats, default loudly rather than
    # silently mislabel the bail as space-exhausted (decoupled from plan_path's
    # write-stats-before-raise invariant).
    def fake_no_stats(*args, stats=None, **kwargs):
        raise tp.NoFeasiblePlanError(
            "a", Conflict.single(kind="no_feasible_path", plane="a", detail="x")
        )

    monkeypatch.setattr(tp, "plan_path", fake_no_stats)
    c_unknown = tp._aircraft_egress_conflict(
        placement, placed, h, {"a": ac}, heuristic="grid", max_expansions=8000
    )
    assert c_unknown is not None and "unknown-exhaustion" in c_unknown.detail


# ── Real dense witness (slow integration; excluded from the default set) ──────


@pytest.mark.slow
def test_probe_on_herrenteich_witness_partitions_the_towable_fleet() -> None:
    """The probe runs on the real all-8 Herrenteich witness and returns a
    well-formed verdict: the discovered order and the stuck core PARTITION the
    tow-routable aircraft, ``cleared`` is exactly "nothing stuck", and there is
    one blocking conflict per stuck body. Verdict-agnostic (the specific CLEAR/
    STUCK outcome lives in the spike, which can shift with grid/clearance/budget)
    — this only guards the structural contract on real dense geometry. Capped at
    a small per-plane budget so the slow lane stays affordable: the partition
    invariants hold for ANY verdict, so the cap trades the spike's authoritative
    8000-budget verdict for runtime without weakening the contract under test."""
    target = load_layout(HERRENTEICH / "layout.yaml")
    extractable = {p.plane_id for p in target.placements if not p.hand_placed}

    result = reverse_teardown_probe(target, max_expansions=600)

    # The probe ran end-to-end on real dense geometry and `order ∪ stuck` cover the
    # full towable fleet. This is the partition's *completeness* leg — the one
    # structural invariant the type can't self-enforce (it needs the input Layout);
    # `__post_init__` already guarantees disjointness, blocking<->stuck alignment,
    # and cleared <=> empty-stuck, so asserting those here would be tautological.
    assert isinstance(result, TeardownProbeResult)
    assert set(result.order) | set(result.stuck) == extractable
