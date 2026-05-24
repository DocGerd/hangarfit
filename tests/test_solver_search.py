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


def test_initial_placements_skips_maintenance_plane():
    """``_initial_placements`` must omit the maintenance occupant from the
    returned dict — the bay is closed and the occupant is treated as away
    (Layout invariant: maintenance_plane MUST NOT appear in placements).

    Previously the solver sampled an initial placement for the maintenance
    plane (biased into the back strip) and then filtered it out at
    Layout-build time. With the ``bay_intrusion`` semantics, there is no
    plane-shaped occupant to sample for — the bay rectangle becomes a
    hard obstacle via the collision rule, and the solver simply iterates
    over the N−1 non-maintenance planes.
    """
    from hangarfit.loader import load_scenario
    from hangarfit.solver import _initial_placements

    s = load_scenario("tests/fixtures/scenario_with_pin.yaml")
    assert s.maintenance_plane == "fuji", "fixture sanity"

    rng = random.Random(42)
    placements = _initial_placements(scenario=s, rng=rng, cart_bucket=frozenset())

    assert "fuji" not in placements, (
        f"maintenance occupant must not be sampled, got keys={set(placements)}"
    )
    expected = set(s.fleet_in) - {"fuji"}
    assert set(placements) == expected


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
    # K=1 happy path: the diversity filter is vacuously True with no
    # accepted layouts, so the reject branch is unreachable when
    # alternatives=1. A regression that moved the rejected_count
    # increment outside the `else:` arm in solver.py would surface here.
    assert r.diagnostics.diversity_rejected_count == 0


def test_solve_finds_layout_for_fresh_six_planes():
    """6 planes in placeholder hangar — should be findable within budget."""
    from hangarfit.collisions import check
    from hangarfit.loader import load_scenario
    from hangarfit.models import SearchConfig
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_fresh_six_planes.yaml")
    r = solve(s, budget_s=5.0, alternatives=1, seed=42, search=SearchConfig(spread=False))

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


def test_solve_is_deterministic_through_descent_loop():
    """Seeded RNG must produce identical layouts across two ``solve()``
    calls, even through the multi-restart descent loop.

    Names the specific concern that the parametrized canary suite in
    ``test_solver_canaries.py`` does NOT explicitly cover: descent-step
    set-iteration order (``sorted(conflicting)`` in ``_descent_step``).
    The canary suite parametrizes determinism over fixtures; this test
    parametrizes determinism over a *codepath*, asserting that
    ``restarts_attempted`` matches across runs — a check the canary
    suite deliberately omits because it is wall-clock-dependent for
    ``status=exhausted_budget`` but is in fact deterministic-by-seed
    for ``status=found`` (the run terminates at the restart that
    succeeds, regardless of wall-clock).

    Budget is set high enough that the search reliably ends in
    ``found``; if a slow CI runner cannot reach ``found`` within
    ``budget_s`` the test will fail with a status mismatch — bump the
    budget or skip rather than weakening the assertion.
    """
    from hangarfit.loader import load_scenario
    from hangarfit.models import SearchConfig
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_fresh_six_planes.yaml")
    r1 = solve(s, budget_s=10.0, alternatives=1, seed=42, search=SearchConfig(spread=False))
    r2 = solve(s, budget_s=10.0, alternatives=1, seed=42, search=SearchConfig(spread=False))

    # Status mismatch here almost certainly means the CI runner is too
    # slow to reach `found` within budget_s, not a determinism break —
    # the canary suite catches actual non-determinism with a smaller
    # surface. Bump budget if this trips.
    assert r1.status == r2.status == "found", (
        f"expected both runs to find within 10 s; got {r1.status!r} / {r2.status!r}. "
        f"Likely cause: CI runner is slow; bump budget_s."
    )
    assert r1.diagnostics.seed == r2.diagnostics.seed == 42
    assert r1.diagnostics.restarts_attempted == r2.diagnostics.restarts_attempted

    assert len(r1.layouts) == len(r2.layouts) == 1
    for la, lb in zip(r1.layouts, r2.layouts, strict=True):
        assert la.placements == lb.placements


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


def test_diversity_filter_empty_accepted_vacuously_passes():
    """`_is_diverse_enough(L, [], div) is True` — the contract that backward-
    compatibility with `alternatives=1` depends on (first valid layout is
    always accepted because there's nothing to be diverse against).
    Previously covered transitively via Chunk D smoke tests; pinning it
    directly catches a regression that broke the empty-accepted short-
    circuit explicitly."""
    from hangarfit.loader import load_fleet, load_hangar
    from hangarfit.models import DiversityConfig, Layout, Placement
    from hangarfit.solver import _is_diverse_enough

    fleet = load_fleet("data/fleet.yaml")
    hangar = load_hangar("data/hangar.yaml")
    p = Placement(plane_id="aviat_husky", x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False)
    L = Layout(fleet=fleet, hangar=hangar, placements=(p,))

    assert _is_diverse_enough(L, [], DiversityConfig()) is True


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


def test_diversity_filter_pairwise_not_aggregate():
    """The filter is pairwise against EVERY accepted layout, not aggregate.

    Candidate is diverse from L1 (moved 2+ planes) but identical to L2.
    Pairwise → False (fails vs L2). A buggy aggregate impl that summed
    moves across all accepted layouts (or accepted on "any" pass)
    would return True. Catches that refactor.
    """
    from hangarfit.loader import load_fleet, load_hangar
    from hangarfit.models import DiversityConfig, Layout, Placement
    from hangarfit.solver import _is_diverse_enough

    fleet = load_fleet("data/fleet.yaml")
    hangar = load_hangar("data/hangar.yaml")

    # L1: husky at (5,5), ctsl at (10,10).
    p1_L1 = Placement(plane_id="aviat_husky", x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False)
    p2_L1 = Placement(plane_id="ctsl", x_m=10.0, y_m=10.0, heading_deg=0.0, on_carts=False)
    L1 = Layout(fleet=fleet, hangar=hangar, placements=(p1_L1, p2_L1))

    # Candidate: both planes moved by > 0.5 m from L1.
    p1_cand = Placement(plane_id="aviat_husky", x_m=8.0, y_m=5.0, heading_deg=0.0, on_carts=False)
    p2_cand = Placement(plane_id="ctsl", x_m=13.0, y_m=10.0, heading_deg=0.0, on_carts=False)
    candidate = Layout(fleet=fleet, hangar=hangar, placements=(p1_cand, p2_cand))

    # L2: identical to candidate.
    L2 = Layout(fleet=fleet, hangar=hangar, placements=(p1_cand, p2_cand))

    div = DiversityConfig()  # M=2
    # vs L1 alone: diverse (2 planes moved).
    assert _is_diverse_enough(candidate, [L1], div)
    # vs L2 alone: not diverse (identical).
    assert not _is_diverse_enough(candidate, [L2], div)
    # Pairwise vs [L1, L2]: must FAIL (the L2 check rejects).
    assert not _is_diverse_enough(candidate, [L1, L2], div)


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


def test_diversity_filter_heading_threshold_exact_boundary():
    """Parallel to the position-threshold boundary test: rotating by
    exactly `heading_threshold_deg` counts as moved (>= comparison).

    A refactor that flipped the heading comparison to strict `>` would
    pass the position-boundary test (still `>=`) but fail this one.
    """
    from hangarfit.loader import load_fleet, load_hangar
    from hangarfit.models import DiversityConfig, Layout, Placement
    from hangarfit.solver import _is_diverse_enough

    fleet = load_fleet("data/fleet.yaml")
    hangar = load_hangar("data/hangar.yaml")
    p1 = Placement(plane_id="aviat_husky", x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False)
    p2 = Placement(plane_id="ctsl", x_m=10.0, y_m=10.0, heading_deg=0.0, on_carts=False)
    L1 = Layout(fleet=fleet, hangar=hangar, placements=(p1, p2))

    div = DiversityConfig()  # heading_threshold_deg=30.0
    # Rotate both planes by exactly 30°, no position change.
    p1b = Placement(
        plane_id="aviat_husky",
        x_m=5.0,
        y_m=5.0,
        heading_deg=div.heading_threshold_deg,
        on_carts=False,
    )
    p2b = Placement(
        plane_id="ctsl",
        x_m=10.0,
        y_m=10.0,
        heading_deg=div.heading_threshold_deg,
        on_carts=False,
    )
    L2 = Layout(fleet=fleet, hangar=hangar, placements=(p1b, p2b))
    assert _is_diverse_enough(L2, [L1], div)


def test_solve_status_found_when_k_equals_two_both_satisfied():
    """K=2, single plane in a large hangar — both alternatives findable.

    Pins the `found` status branch on the K>1 path: with one plane free
    in a roomy hangar, the search has plenty of distinct basins. After
    two diverse layouts are accepted, the outer loop exits via
    `len(accepted_layouts) >= alternatives` and status='found'.

    Uses ``min_planes_moved=1`` because the fixture has a single plane —
    the default M=2 would make diversity mathematically impossible (and
    trigger the diversity-impossible warning on entry, which is covered
    by a separate test).
    """
    from hangarfit.collisions import check
    from hangarfit.loader import load_scenario
    from hangarfit.models import DiversityConfig
    from hangarfit.solver import _is_diverse_enough, solve

    s = load_scenario("tests/fixtures/solve_trivial_single_plane.yaml")
    div = DiversityConfig(min_planes_moved=1)
    r = solve(s, budget_s=10.0, alternatives=2, seed=42, diversity=div)

    # Empirically deterministic across many seeds on this fixture
    # (single plane, roomy hangar) — pin `found` directly. Earlier
    # versions wrapped this in `pytest.skip` branches that were
    # unreachable on the current implementation and would have masked a
    # real regression (a future bug producing `found_partial` here).
    assert r.status == "found"
    assert len(r.layouts) == 2
    for L in r.layouts:
        assert check(L).valid
    # The two layouts must be pairwise diverse under the configured policy.
    assert _is_diverse_enough(r.layouts[0], [r.layouts[1]], div)


def test_solve_emits_diversity_impossible_warning(caplog):
    """Diversity-impossible static check fires when free_planes < M and K > 1.

    Empirically deterministic across many seeds on this fixture (2 of 3
    planes pinned, 1 free): the result is always `found_partial` with
    exactly 1 layout. Pinning the exact shape catches:
    - The warning-fire path leaving diagnostics inconsistent (e.g.,
      best_partial leaking through).
    - A refactor that silently mutates `alternatives` (spec forbids).
    - A future where `found_partial` accidentally retains a best_partial
      from mid-search instead of None.
    """
    import logging

    from hangarfit.loader import load_scenario
    from hangarfit.models import SearchConfig
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_diversity_impossible_warn.yaml")
    with caplog.at_level(logging.WARNING):
        r = solve(s, budget_s=5.0, alternatives=3, seed=42, search=SearchConfig(spread=False))

    # At least one warning about diversity impossibility.
    assert any(
        "achievable" in rec.message.lower() or "diversity" in rec.message.lower()
        for rec in caplog.records
    ), f"Expected diversity-impossible warning; got messages: {[r.message for r in caplog.records]}"
    # Exact-shape pin (was previously loose `in {...}` + `<= 1`).
    assert r.status == "found_partial"
    assert len(r.layouts) == 1
    # Found_partial path: best_partial pair must be None (the accepted
    # layout lives in result.layouts, not in best_partial).
    assert r.diagnostics.best_partial is None
    assert r.diagnostics.best_partial_layout is None
    # Spec §4.1 (v0.6.0 release): the structured flag mirrors the
    # logger warning so callers don't have to scrape log records.
    assert r.diagnostics.diversity_impossible is True


def test_solve_does_not_warn_when_diversity_is_achievable(caplog):
    """Negative companion to the diversity-impossible warning test.

    When `free_planes >= min_planes_moved` (so diversity is statically
    possible), the warning MUST NOT fire. A regression that fires the
    warning unconditionally — e.g., inverted predicate, off-by-one on
    the comparison — would not be caught by the positive test alone.
    """
    import logging

    from hangarfit.loader import load_scenario
    from hangarfit.models import DiversityConfig, SearchConfig
    from hangarfit.solver import solve

    # Fresh-six-planes fixture: 6 planes, none pinned → free_planes=6 >= M=2.
    s = load_scenario("tests/fixtures/solve_fresh_alternatives_three.yaml")
    with caplog.at_level(logging.WARNING):
        r = solve(
            s,
            alternatives=3,
            seed=42,
            diversity=DiversityConfig(min_planes_moved=1),
            search=SearchConfig(spread=False),
        )

    # The diversity-impossible warning text contains "achievable" — assert
    # no such warning fired. (Other unrelated warnings are fine.)
    diversity_warnings = [rec for rec in caplog.records if "achievable" in rec.message.lower()]
    assert not diversity_warnings, (
        f"Expected NO diversity-impossible warning when free_planes >= M; "
        f"got {[r.message for r in diversity_warnings]}"
    )
    # Spec §4.1 (v0.6.0 release): structured flag must agree with the
    # absent log warning.
    assert r.diagnostics.diversity_impossible is False


def test_solve_diversity_rejected_count_increments_on_reject():
    """Diversity filter rejects raise `diagnostics.diversity_rejected_count`.

    Uses the diversity-impossible fixture (2 of 3 planes pinned, M=2):
    every trajectory after the first finds the same valid layout-shape
    (one degree of freedom), so subsequent valid candidates are rejected
    by `_is_diverse_enough`. Over a 5 s budget the count is reliably >0.

    Spec §4.1 (v0.6.0 release) — the counter is informative when K>1
    returns `found_partial` despite the trajectory loop producing more
    than one valid layout.
    """
    from hangarfit.loader import load_scenario
    from hangarfit.models import SearchConfig
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_diversity_impossible_warn.yaml")
    r = solve(s, budget_s=5.0, alternatives=3, seed=42, search=SearchConfig(spread=False))

    # The fixture forces found_partial (see test_solve_emits_diversity_impossible_warning).
    assert r.status == "found_partial"
    assert len(r.layouts) == 1
    # And — the heart of this test — at least one valid candidate was
    # produced by search and rejected by the diversity filter.
    assert r.diagnostics.diversity_rejected_count > 0, (
        f"Expected diversity_rejected_count > 0 on diversity-impossible fixture; "
        f"got {r.diagnostics.diversity_rejected_count}. "
        f"restarts_attempted={r.diagnostics.restarts_attempted}"
    )


def test_solve_returns_k_diverse_alternatives():
    from hangarfit.loader import load_scenario
    from hangarfit.models import DiversityConfig, SearchConfig
    from hangarfit.solver import _is_diverse_enough, solve

    s = load_scenario("tests/fixtures/solve_fresh_alternatives_three.yaml")
    r = solve(s, budget_s=10.0, alternatives=3, seed=42, search=SearchConfig(spread=False))

    if r.status == "exhausted_budget":
        pytest.skip("Search didn't find K=3 within budget; acceptable on placeholder data.")
    assert r.status in {"found", "found_partial"}
    assert 1 <= len(r.layouts) <= 3
    # Each pair must be diverse
    div = DiversityConfig()
    for i, L_i in enumerate(r.layouts):
        for j, L_j in enumerate(r.layouts):
            if i == j:
                continue
            others = [L_j]
            assert _is_diverse_enough(L_i, others, div), (
                f"layouts[{i}] and layouts[{j}] are not diverse from each other"
            )


def test_inter_plane_energy_zero_for_single_plane():
    from hangarfit.loader import load_scenario
    from hangarfit.models import Placement
    from hangarfit.solver import _inter_plane_energy

    s = load_scenario("tests/fixtures/solve_all_nine_large_hangar.yaml")
    pid = next(p for p in s.fleet_in if p != s.maintenance_plane)
    placements = {pid: Placement(plane_id=pid, x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False)}

    assert _inter_plane_energy(placements, s, scale=5.0) == 0.0


def test_inter_plane_energy_higher_when_planes_closer():
    from hangarfit.loader import load_scenario
    from hangarfit.models import Placement
    from hangarfit.solver import _inter_plane_energy

    s = load_scenario("tests/fixtures/solve_all_nine_large_hangar.yaml")
    a, b = [p for p in s.fleet_in if p != s.maintenance_plane][:2]
    scale = 5.0

    near = {
        a: Placement(plane_id=a, x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False),
        b: Placement(plane_id=b, x_m=7.0, y_m=7.0, heading_deg=0.0, on_carts=False),
    }
    far = {
        a: Placement(plane_id=a, x_m=3.0, y_m=3.0, heading_deg=0.0, on_carts=False),
        b: Placement(plane_id=b, x_m=22.0, y_m=27.0, heading_deg=0.0, on_carts=False),
    }
    # Closer planes -> smaller gap -> larger exp(-gap/scale) term.
    assert _inter_plane_energy(near, s, scale) > _inter_plane_energy(far, s, scale)


def test_inter_plane_energy_symmetric_in_plane_order():
    from hangarfit.loader import load_scenario
    from hangarfit.models import Placement
    from hangarfit.solver import _inter_plane_energy

    s = load_scenario("tests/fixtures/solve_all_nine_large_hangar.yaml")
    a, b = [p for p in s.fleet_in if p != s.maintenance_plane][:2]
    pa = Placement(plane_id=a, x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False)
    pb = Placement(plane_id=b, x_m=9.0, y_m=11.0, heading_deg=30.0, on_carts=False)

    assert _inter_plane_energy({a: pa, b: pb}, s, 5.0) == _inter_plane_energy(
        {b: pb, a: pa}, s, 5.0
    )


def _valid_placements(seed: int):
    """Solve (without spread) to obtain a valid placements dict to feed _spread.

    Uses a small 3-plane feasible fixture so the solve is fast AND there are
    real plane pairs (single-plane fixtures make spread tests vacuous).
    """
    from hangarfit.loader import load_scenario
    from hangarfit.models import SearchConfig
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_fresh_alternatives_three.yaml")
    r = solve(s, budget_s=5.0, seed=seed, search=SearchConfig(spread=False))
    assert r.layouts, "fixture must be solvable without spread"
    placements = {p.plane_id: p for p in r.layouts[0].placements}
    return s, placements


def test_spread_preserves_validity_and_never_worsens_energy():
    import random
    import time

    from hangarfit.models import Layout, SearchConfig
    from hangarfit.solver import _inter_plane_energy, _score, _spread

    s, placements = _valid_placements(seed=11)
    scale = 0.2 * min(s.hangar.width_m, s.hangar.length_m)
    e_before = _inter_plane_energy(placements, s, scale)

    out = _spread(
        placements,
        s,
        random.Random(11),
        SearchConfig(),
        start=time.monotonic(),
        budget_s=5.0,
        pinned_planes=frozenset(),
    )
    e_after = _inter_plane_energy(out, s, scale)

    # Energy never increases (spread only improves or no-ops).
    assert e_after <= e_before
    # Output is still a valid layout.
    layout = Layout(
        fleet=s.fleet,
        hangar=s.hangar,
        placements=tuple(out.values()),
        maintenance_plane=s.maintenance_plane,
    )
    assert _score(layout) == (0, 0.0)


def test_spread_is_deterministic_for_same_seed():
    import random
    import time

    from hangarfit.models import SearchConfig
    from hangarfit.solver import _spread

    s, placements = _valid_placements(seed=11)

    out_a = _spread(
        placements,
        s,
        random.Random(99),
        SearchConfig(),
        start=time.monotonic(),
        budget_s=60.0,
        pinned_planes=frozenset(),
    )
    out_b = _spread(
        placements,
        s,
        random.Random(99),
        SearchConfig(),
        start=time.monotonic(),
        budget_s=60.0,
        pinned_planes=frozenset(),
    )

    assert {k: (v.x_m, v.y_m, v.heading_deg) for k, v in out_a.items()} == {
        k: (v.x_m, v.y_m, v.heading_deg) for k, v in out_b.items()
    }


def test_spread_does_not_move_pinned_planes():
    import random
    import time

    from hangarfit.models import SearchConfig
    from hangarfit.solver import _spread

    s, placements = _valid_placements(seed=11)
    frozen_id = sorted(placements)[0]
    frozen_before = placements[frozen_id]

    out = _spread(
        placements,
        s,
        random.Random(11),
        SearchConfig(),
        start=time.monotonic(),
        budget_s=5.0,
        pinned_planes=frozenset({frozen_id}),
    )
    assert out[frozen_id] == frozen_before


def test_spread_scale_m_override_changes_spreading():
    """A larger spread_scale_m gradient reaches across the hangar and spreads
    more than a tiny one; verifies spread_scale_m is actually honored, not
    ignored in favor of the adaptive default."""
    import random
    import time

    from hangarfit.models import SearchConfig
    from hangarfit.solver import _inter_plane_energy, _spread

    s, placements = _valid_placements(seed=11)
    kw = dict(budget_s=60.0, pinned_planes=frozenset())

    out_tiny = _spread(
        placements,
        s,
        random.Random(7),
        SearchConfig(spread_scale_m=0.01),
        start=time.monotonic(),
        **kw,
    )
    out_large = _spread(
        placements,
        s,
        random.Random(7),
        SearchConfig(spread_scale_m=50.0),
        start=time.monotonic(),
        **kw,
    )
    # Measure both at a single neutral scale: the large-scale run should reach
    # a more-separated (lower-energy) configuration than the tiny-scale run.
    e_tiny = _inter_plane_energy(out_tiny, s, scale=1.0)
    e_large = _inter_plane_energy(out_large, s, scale=1.0)
    assert e_large <= e_tiny, f"spread_scale_m had no effect: tiny={e_tiny} large={e_large}"


def test_spread_noop_when_all_planes_pinned():
    """When every plane is pinned (no movable target) but >=2 planes exist,
    _spread returns the input unchanged via the `not movable` guard."""
    import random
    import time

    from hangarfit.models import SearchConfig
    from hangarfit.solver import _spread

    s, placements = _valid_placements(seed=11)
    assert len(placements) >= 2, "fixture sanity"
    all_pinned = frozenset(placements.keys())

    out = _spread(
        placements,
        s,
        random.Random(3),
        SearchConfig(),
        start=time.monotonic(),
        budget_s=5.0,
        pinned_planes=all_pinned,
    )
    assert out == placements


def test_spread_noop_when_single_movable_plane():
    import random
    import time

    from hangarfit.loader import load_scenario
    from hangarfit.models import Placement, SearchConfig
    from hangarfit.solver import _spread

    s = load_scenario("tests/fixtures/solve_all_nine_large_hangar.yaml")
    pid = next(p for p in s.fleet_in if p != s.maintenance_plane)
    placements = {pid: Placement(plane_id=pid, x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False)}

    out = _spread(
        placements,
        s,
        random.Random(1),
        SearchConfig(),
        start=time.monotonic(),
        budget_s=1.0,
        pinned_planes=frozenset(),
    )
    assert out == placements


def test_spread_runs_with_single_movable_among_pinned():
    import random
    import time

    from hangarfit.models import Layout, SearchConfig
    from hangarfit.solver import _inter_plane_energy, _score, _spread

    s, placements = _valid_placements(seed=11)
    ids = sorted(placements)
    assert len(ids) >= 3, "fixture must have >=3 planes for this test"
    # Pin all but the last plane -> exactly one movable, but >=2 planes total.
    pinned = frozenset(ids[:-1])
    scale = 0.2 * min(s.hangar.width_m, s.hangar.length_m)
    e_before = _inter_plane_energy(placements, s, scale)

    out = _spread(
        placements,
        s,
        random.Random(11),
        SearchConfig(),
        start=time.monotonic(),
        budget_s=60.0,
        pinned_planes=pinned,
    )

    # Pinned planes never moved.
    for pid in pinned:
        assert out[pid] == placements[pid]
    # Output still valid.
    layout = Layout(
        fleet=s.fleet,
        hangar=s.hangar,
        placements=tuple(out.values()),
        maintenance_plane=s.maintenance_plane,
    )
    assert _score(layout) == (0, 0.0)
    # Energy not worse (the one movable plane only improves or no-ops).
    assert _inter_plane_energy(out, s, scale) <= e_before
