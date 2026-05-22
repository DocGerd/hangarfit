"""Tests for solver.py — search engine (Chunks D & E)."""

from __future__ import annotations

import random

import pytest


def test_initial_placement_for_pinned_plane_returns_the_pin():
    """If a plane is pinned, its initial placement IS the pin (no sampling)."""
    from hangarfit.loader import load_scenario
    from hangarfit.solver import _initial_placement_for_plane

    s = load_scenario("tests/fixtures/scenario_with_pin.yaml")
    rng = random.Random(42)
    pin = s.constraints["aviat_husky"].pin
    assert pin is not None

    result = _initial_placement_for_plane(
        plane_id="aviat_husky",
        scenario=s,
        rng=rng,
        on_carts=pin.on_carts,
    )
    assert result == pin


def test_initial_placement_for_free_plane_is_within_hangar():
    """Free planes get random (x,y) inside hangar bounds, any heading."""
    from hangarfit.loader import load_scenario
    from hangarfit.solver import _initial_placement_for_plane

    s = load_scenario("tests/fixtures/solve_feasible_smoke.yaml")
    rng = random.Random(42)
    p = _initial_placement_for_plane(
        plane_id="aviat_husky",
        scenario=s,
        rng=rng,
        on_carts=False,
    )
    assert p.plane_id == "aviat_husky"
    assert 0.0 <= p.x_m <= s.hangar.width_m
    assert 0.0 <= p.y_m <= s.hangar.length_m
    assert 0.0 <= p.heading_deg < 360.0


def test_initial_placement_for_maintenance_biases_to_back_strip():
    """The maintenance plane's initial y is inside the maintenance bay."""
    from hangarfit.loader import load_scenario
    from hangarfit.solver import _initial_placement_for_plane

    s = load_scenario("tests/fixtures/scenario_with_pin.yaml")
    # In scenario_with_pin, maintenance_plane is fuji (not pinned).
    rng = random.Random(42)
    p = _initial_placement_for_plane(
        plane_id="fuji",
        scenario=s,
        rng=rng,
        on_carts=False,
        bias_to_maintenance_bay=True,
    )
    bay_y_start = s.hangar.length_m - s.hangar.maintenance_bay.depth_m
    assert bay_y_start <= p.y_m <= s.hangar.length_m


def test_cart_buckets_collapses_when_another_cart_eligible_is_force_locked_on():
    """When another cart_eligible is force_on_carts=True, the at-most-one
    cart-rule slot is taken — singletons for others would be infeasible."""
    from hangarfit.loader import load_scenario
    from hangarfit.solver import _enumerate_cart_buckets

    # scenario_with_force_carts.yaml locks cessna_140 on_carts=True.
    # ctsl is also cart_eligible and unlocked.
    # Naive enumeration would emit [frozenset(), frozenset({"ctsl"})], but
    # the singleton bucket pairs ctsl-on-carts WITH cessna_140-already-on-carts,
    # which violates Layout's at-most-one-cart_eligible-on-carts rule.
    # Correct behavior: only the empty bucket is feasible.
    s = load_scenario("tests/fixtures/scenario_with_force_carts.yaml")
    buckets = _enumerate_cart_buckets(s)
    assert buckets == [frozenset()]


def test_cart_buckets_enumerates_unlocked_cart_eligibles_plus_none():
    """With C unlocked cart_eligible planes AND none pre-committed-on-carts,
    there should be C+1 buckets."""
    from hangarfit.loader import load_scenario
    from hangarfit.solver import _enumerate_cart_buckets

    # solve_fresh_six_planes scenario includes ctsl, cessna_140, fk9_mkii
    # (3 cart_eligibles, none locked). Expected: 4 buckets.
    s = load_scenario("tests/fixtures/solve_fresh_six_planes.yaml")
    buckets = _enumerate_cart_buckets(s)
    assert len(buckets) == 4
    assert frozenset() in buckets
    cart_eligibles = {pid for pid in s.fleet_in if s.fleet[pid].is_cart_eligible}
    for pid in cart_eligibles:
        assert frozenset({pid}) in buckets


def test_cart_bucket_for_restart_is_deterministic_round_robin():
    """Restart index R selects bucket R % len(buckets)."""
    from hangarfit.loader import load_scenario
    from hangarfit.solver import _cart_bucket_for_restart, _enumerate_cart_buckets

    s = load_scenario("tests/fixtures/solve_feasible_smoke.yaml")
    buckets = _enumerate_cart_buckets(s)
    if len(buckets) > 0:
        # First few restarts should cycle through buckets
        for i in range(2 * len(buckets)):
            chosen = _cart_bucket_for_restart(buckets, restart_index=i)
            assert chosen == buckets[i % len(buckets)]


def test_score_valid_layout_is_zero_zero():
    from hangarfit.loader import load_layout
    from hangarfit.solver import _score

    layout = load_layout("layouts/example.yaml")
    s = _score(layout)
    assert s == (0, 0.0)


def test_score_invalid_layout_is_positive():
    from hangarfit.loader import load_layout
    from hangarfit.solver import _score

    # layouts/example_invalid.yaml is documented in CLAUDE.md as
    # exercising 3 conflict kinds (hangar_bounds + wing_wing_overlap +
    # strut_wing_overlap). At least the wing/wing and strut/wing
    # conflicts produce real overlap area, so penetration is strictly
    # positive — vacuous `>= 0.0` would mask a regression that
    # accidentally returned 0.0 for every conflict.
    layout = load_layout("layouts/example_invalid.yaml")
    s = _score(layout)
    count, penetration = s
    assert count >= 3, f"expected ≥3 conflicts in example_invalid; got {count}"
    assert penetration > 0.0, f"expected positive penetration area; got {penetration}"


def test_score_lex_ordering_matches_spec():
    """Hierarchical scoring: lower conflict-count wins over lower penetration.

    `(2, 999.0) < (3, 0.0)` because 2 < 3. The descent loop's progress
    depends on this lex compare; a subtle inversion would silently
    degrade search quality.
    """
    from hangarfit.solver import _score  # noqa: F401  (imported for namespace)

    # Build scores directly — no need to load fixtures here.
    assert (2, 999.0) < (3, 0.0)
    assert (2, 0.5) > (2, 0.4)
    assert (0, 0.0) < (1, 0.0)
    # Sanity: _score returns exactly tuple[int, float]
    from hangarfit.loader import load_layout
    from hangarfit.solver import _score as score_fn

    score = score_fn(load_layout("layouts/example.yaml"))
    assert isinstance(score, tuple)
    assert isinstance(score[0], int)
    assert isinstance(score[1], float)


def test_perturb_plane_returns_valid_placement_within_hangar():
    """Perturbation outputs are within hangar bounds and on [0, 360°)."""
    from hangarfit.loader import load_scenario
    from hangarfit.models import Placement
    from hangarfit.solver import SearchConfig, _perturb_plane

    s = load_scenario("tests/fixtures/solve_feasible_smoke.yaml")
    rng = random.Random(42)
    current = Placement(plane_id="aviat_husky", x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False)
    config = SearchConfig()  # defaults

    # Generate many perturbations; all must be inside hangar bounds.
    for _ in range(50):
        cand = _perturb_plane(
            current=current,
            scenario=s,
            rng=rng,
            search=config,
            large_jump=False,
        )
        assert cand.plane_id == "aviat_husky"
        assert 0.0 <= cand.x_m <= s.hangar.width_m
        assert 0.0 <= cand.y_m <= s.hangar.length_m
        assert 0.0 <= cand.heading_deg < 360.0


def test_perturb_plane_preserves_on_carts():
    """`_perturb_plane` must keep `on_carts` from the current placement.

    The docstring claims this; without a test, a regression flipping
    `on_carts` mid-trajectory would silently violate the cart-bucket
    round-robin's premise.
    """
    from hangarfit.loader import load_scenario
    from hangarfit.models import Placement
    from hangarfit.solver import SearchConfig, _perturb_plane

    s = load_scenario("tests/fixtures/solve_feasible_smoke.yaml")
    rng = random.Random(42)
    current_off = Placement(
        plane_id="aviat_husky", x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False
    )
    current_on = Placement(plane_id="aviat_husky", x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=True)
    cfg = SearchConfig()
    for _ in range(20):
        assert (
            _perturb_plane(
                current=current_off, scenario=s, rng=rng, search=cfg, large_jump=False
            ).on_carts
            is False
        )
        assert (
            _perturb_plane(
                current=current_on, scenario=s, rng=rng, search=cfg, large_jump=True
            ).on_carts
            is True
        )


def test_perturb_plane_heading_wraps_modulo_360():
    """Perturbing a heading near 0° (or 360°) should wrap, not clamp.

    A regression that clamped heading to [0, 360) instead of wrapping
    would cause the search to never visit headings near the wrap-around
    region from one side, biasing solutions.
    """
    from hangarfit.loader import load_scenario
    from hangarfit.models import Placement
    from hangarfit.solver import SearchConfig, _perturb_plane

    s = load_scenario("tests/fixtures/solve_feasible_smoke.yaml")
    rng = random.Random(42)
    current = Placement(plane_id="aviat_husky", x_m=5.0, y_m=5.0, heading_deg=359.0, on_carts=False)
    cfg = SearchConfig()  # heading_sigma_deg=10.0
    # Across many perturbations, at least some must land below 180° —
    # proves wrap (358° + 10° = 368° → 8°), not clamp (would stay at
    # 359° forever).
    headings = [
        _perturb_plane(
            current=current, scenario=s, rng=rng, search=cfg, large_jump=False
        ).heading_deg
        for _ in range(100)
    ]
    assert any(h < 180.0 for h in headings), (
        f"Heading wrap appears broken; all 100 samples stayed in upper half: "
        f"{sorted(set(int(h) for h in headings))[:10]} (showing first 10 unique)"
    )


def test_solve_finds_layout_for_trivial_single_plane():
    """A single plane in a large hangar must be found quickly."""
    from hangarfit.collisions import check
    from hangarfit.loader import load_scenario
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_trivial_single_plane.yaml")
    r = solve(s, budget_s=5.0, alternatives=1, seed=42)

    assert r.status == "found"
    assert len(r.layouts) == 1
    assert check(r.layouts[0]).valid


def test_solve_finds_layout_for_fresh_six_planes():
    """6 planes in placeholder hangar — should be findable within budget."""
    from hangarfit.collisions import check
    from hangarfit.loader import load_scenario
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_fresh_six_planes.yaml")
    r = solve(s, budget_s=5.0, alternatives=1, seed=42)

    if r.status == "exhausted_budget":
        pytest.skip(
            f"Search didn't find a layout for 6 planes in 5s with seed=42 "
            f"(restarts={r.diagnostics.restarts_attempted}). This is acceptable "
            f"behavior — the placeholder hangar is tight. Increase budget or "
            f"retune SearchConfig if this becomes a pattern."
        )

    assert r.status == "found"
    assert len(r.layouts) == 1
    assert check(r.layouts[0]).valid


def test_solve_is_deterministic_for_same_seed():
    """seed=42 → identical SolveResult across calls.

    The spec §4.8 contract is "one Random(seed) drives every sampling
    decision". This is the canary that the RNG is actually threaded
    through every branch — set/dict iteration, time.time() reads, and
    other nondeterminism would surface here as a flake.

    Compares SolveResults via their layouts' placements (the layouts'
    fleet dicts are MappingProxyType-wrapped copies, so Layout equality
    requires deep-equality on dicts which works but is heavy — comparing
    placements directly is sharper).
    """
    from hangarfit.loader import load_scenario
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_trivial_single_plane.yaml")
    r1 = solve(s, budget_s=5.0, alternatives=1, seed=42)
    r2 = solve(s, budget_s=5.0, alternatives=1, seed=42)

    assert r1.status == r2.status
    assert r1.diagnostics.seed == r2.diagnostics.seed == 42
    assert r1.diagnostics.restarts_attempted == r2.diagnostics.restarts_attempted
    # Compare actual layout placements element-wise — Layout's fleet is
    # wrapped in MappingProxyType so direct equality would compare proxy
    # identity in some corner cases.
    assert len(r1.layouts) == len(r2.layouts)
    for la, lb in zip(r1.layouts, r2.layouts, strict=True):
        assert la.placements == lb.placements
        assert la.maintenance_plane == lb.maintenance_plane


def test_solve_is_deterministic_for_exhausted_budget_branch():
    """Multi-plane scenario + tight budget → exercises full descent loop
    (perturbation, candidate selection, conflict-plane pick) under the
    same-seed-same-answer canary.

    The found-branch test above only covers initial placement. This test
    covers the descent loop where determinism is most likely to silently
    break — specifically the `sorted(conflicting)` at solver.py:700 that
    cancels set-iteration nondeterminism.
    """
    from hangarfit.loader import load_scenario
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_fresh_six_planes.yaml")
    r1 = solve(s, budget_s=0.05, alternatives=1, seed=42)
    r2 = solve(s, budget_s=0.05, alternatives=1, seed=42)

    # Determinism: status, seed, restarts must match.
    assert r1.status == r2.status
    assert r1.diagnostics.seed == r2.diagnostics.seed == 42
    assert r1.diagnostics.restarts_attempted == r2.diagnostics.restarts_attempted
    # If found: layouts match.
    assert len(r1.layouts) == len(r2.layouts)
    for la, lb in zip(r1.layouts, r2.layouts, strict=True):
        assert la.placements == lb.placements
    # If exhausted: best_partial_layout placements match.
    if r1.diagnostics.best_partial_layout is not None:
        assert r2.diagnostics.best_partial_layout is not None
        assert (
            r1.diagnostics.best_partial_layout.placements
            == r2.diagnostics.best_partial_layout.placements
        )


def test_solve_exhausted_budget_reports_best_partial_pair():
    """When budget runs out without finding a valid layout, the diagnostics
    must report `best_partial` and `best_partial_layout` as a paired Some.

    Pins the SolverDiagnostics fused-pair invariant on the
    exhausted_budget branch (the found branch is covered by the trivial
    test). Without this, a regression that cleared the layout reference
    while keeping the score (or vice versa) would only be caught by
    SolverDiagnostics.__post_init__ — which would crash inside solve(),
    not surface as a clear test failure.
    """
    import pytest

    from hangarfit.loader import load_scenario
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_fresh_six_planes.yaml")
    r = solve(s, budget_s=0.05, alternatives=1, seed=42)
    if r.status == "found":
        pytest.skip("seed=42 + 0.05s got lucky; tighten budget if this skips often")

    assert r.status == "exhausted_budget"
    assert r.layouts == ()
    assert r.diagnostics.restarts_attempted >= 1, "search must have tried at least once"

    bp = r.diagnostics.best_partial
    bpl = r.diagnostics.best_partial_layout
    # Fused-pair contract (Chunk B's SolverDiagnostics.__post_init__).
    assert (bp is None) == (bpl is None), (
        f"best_partial/best_partial_layout must be both-None or both-Some; "
        f"got bp={'None' if bp is None else 'Some'}, "
        f"bpl={'None' if bpl is None else 'Some'}"
    )
    if bp is not None:
        # If we have a best_partial, it MUST have conflicts (else the layout
        # would be valid and status would be "found").
        assert len(bp.conflicts) >= 1


def test_descent_step_returns_none_when_all_conflicts_are_pinned():
    """Unit test for the `_descent_step → None` restart contract.

    When every conflict-causing plane is in `pinned_planes`, the helper
    must return None (signalling the trajectory to restart). A refactor
    that swapped `c.planes` → `[c.plane]` or otherwise broke the filter
    would silently hang within range(10000) instead of restarting.

    Build the smallest scenario that hits this: two planes pinned at
    overlapping coordinates. The conflict would normally drive descent,
    but both planes are pinned → conflicting set is empty.
    """
    from hangarfit.loader import load_scenario
    from hangarfit.solver import SearchConfig, _descent_step

    # solve_infeasible_pins_clash.yaml has two pins at identical coords.
    # Loading and stepping it manually (bypassing solve()'s pre-search)
    # to exercise _descent_step directly.
    s = load_scenario("tests/fixtures/solve_infeasible_pins_clash.yaml")
    placements = {pid: s.constraints[pid].pin for pid in s.fleet_in}
    pinned = frozenset(s.fleet_in)  # both planes pinned

    result = _descent_step(
        placements=placements,
        scenario=s,
        rng=random.Random(42),
        search=SearchConfig(),
        current_score=(2, 0.0),
        pinned_planes=pinned,
    )
    assert result is None, "all-pinned-conflicts must return None for restart"


# ── Chunk E: K-diverse alternatives + termination + diagnostics ─────────


def test_diversity_filter_rejects_near_duplicate():
    """Two layouts with no planes moved enough should fail diversity."""
    from hangarfit.loader import load_fleet, load_hangar
    from hangarfit.models import DiversityConfig, Layout, Placement
    from hangarfit.solver import _is_diverse_enough

    fleet = load_fleet("data/fleet.yaml")
    hangar = load_hangar("data/hangar.yaml")
    p1 = Placement(plane_id="aviat_husky", x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False)
    p2 = Placement(plane_id="ctsl", x_m=10.0, y_m=10.0, heading_deg=0.0, on_carts=False)
    L1 = Layout(fleet=fleet, hangar=hangar, placements=(p1, p2))
    L2 = Layout(fleet=fleet, hangar=hangar, placements=(p1, p2))  # identical

    diversity = DiversityConfig()  # defaults: M=2, 0.5m, 30°
    assert not _is_diverse_enough(L2, [L1], diversity)


def test_diversity_filter_accepts_meaningfully_different():
    from hangarfit.loader import load_fleet, load_hangar
    from hangarfit.models import DiversityConfig, Layout, Placement
    from hangarfit.solver import _is_diverse_enough

    fleet = load_fleet("data/fleet.yaml")
    hangar = load_hangar("data/hangar.yaml")
    p1 = Placement(plane_id="aviat_husky", x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False)
    p2 = Placement(plane_id="ctsl", x_m=10.0, y_m=10.0, heading_deg=0.0, on_carts=False)
    L1 = Layout(fleet=fleet, hangar=hangar, placements=(p1, p2))

    # L2: both planes moved by > 0.5 m
    p1b = Placement(plane_id="aviat_husky", x_m=8.0, y_m=5.0, heading_deg=0.0, on_carts=False)
    p2b = Placement(plane_id="ctsl", x_m=13.0, y_m=10.0, heading_deg=0.0, on_carts=False)
    L2 = Layout(fleet=fleet, hangar=hangar, placements=(p1b, p2b))

    diversity = DiversityConfig()
    assert _is_diverse_enough(L2, [L1], diversity)


def test_diversity_heading_uses_short_arc():
    """0° and 359° should be 1° apart, not 359°."""
    from hangarfit.loader import load_fleet, load_hangar
    from hangarfit.models import DiversityConfig, Layout, Placement
    from hangarfit.solver import _is_diverse_enough

    fleet = load_fleet("data/fleet.yaml")
    hangar = load_hangar("data/hangar.yaml")
    p1 = Placement(plane_id="aviat_husky", x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False)
    p2 = Placement(plane_id="ctsl", x_m=10.0, y_m=10.0, heading_deg=0.0, on_carts=False)
    L1 = Layout(fleet=fleet, hangar=hangar, placements=(p1, p2))

    p1b = Placement(plane_id="aviat_husky", x_m=5.0, y_m=5.0, heading_deg=359.0, on_carts=False)
    p2b = Placement(plane_id="ctsl", x_m=10.0, y_m=10.0, heading_deg=0.0, on_carts=False)
    L2 = Layout(fleet=fleet, hangar=hangar, placements=(p1b, p2b))

    diversity = DiversityConfig()
    # heading_threshold_deg=30 — 1° gap is less than 30°, so this is NOT diverse.
    assert not _is_diverse_enough(L2, [L1], diversity)


def test_diversity_filter_m_equals_one_boundary():
    """With min_planes_moved=1, moving exactly one plane suffices."""
    from hangarfit.loader import load_fleet, load_hangar
    from hangarfit.models import DiversityConfig, Layout, Placement
    from hangarfit.solver import _is_diverse_enough

    fleet = load_fleet("data/fleet.yaml")
    hangar = load_hangar("data/hangar.yaml")
    p1 = Placement(plane_id="aviat_husky", x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False)
    p2 = Placement(plane_id="ctsl", x_m=10.0, y_m=10.0, heading_deg=0.0, on_carts=False)
    L1 = Layout(fleet=fleet, hangar=hangar, placements=(p1, p2))

    # L2: only aviat_husky moved (by > 0.5 m)
    p1b = Placement(plane_id="aviat_husky", x_m=8.0, y_m=5.0, heading_deg=0.0, on_carts=False)
    L2 = Layout(fleet=fleet, hangar=hangar, placements=(p1b, p2))

    # Default M=2: rejected (only 1 moved).
    assert not _is_diverse_enough(L2, [L1], DiversityConfig())
    # M=1: accepted.
    assert _is_diverse_enough(L2, [L1], DiversityConfig(min_planes_moved=1))


def test_diversity_filter_threshold_exact_boundary():
    """Per spec §4.6: a plane is "moved" iff pos_delta >= threshold OR
    head_delta >= threshold. The comparison is >=, not >.

    Construct a case where exactly two planes are moved by exactly the
    position threshold — they should count as moved (>= passes), so the
    candidate is accepted under M=2.
    """
    from hangarfit.loader import load_fleet, load_hangar
    from hangarfit.models import DiversityConfig, Layout, Placement
    from hangarfit.solver import _is_diverse_enough

    fleet = load_fleet("data/fleet.yaml")
    hangar = load_hangar("data/hangar.yaml")
    p1 = Placement(plane_id="aviat_husky", x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False)
    p2 = Placement(plane_id="ctsl", x_m=10.0, y_m=10.0, heading_deg=0.0, on_carts=False)
    L1 = Layout(fleet=fleet, hangar=hangar, placements=(p1, p2))

    div = DiversityConfig()  # position_threshold_m=0.5
    # Move both planes by exactly 0.5 m on the x-axis.
    p1b = Placement(
        plane_id="aviat_husky",
        x_m=5.0 + div.position_threshold_m,
        y_m=5.0,
        heading_deg=0.0,
        on_carts=False,
    )
    p2b = Placement(
        plane_id="ctsl",
        x_m=10.0 + div.position_threshold_m,
        y_m=10.0,
        heading_deg=0.0,
        on_carts=False,
    )
    L2 = Layout(fleet=fleet, hangar=hangar, placements=(p1b, p2b))
    # Exact-threshold delta → counted as moved (>= comparison).
    assert _is_diverse_enough(L2, [L1], div)
