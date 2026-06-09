# Design — nose-out parked heading (#263) + `tow_pivotable`

**Date:** 2026-06-07 · **Issue:** [#263](https://github.com/DocGerd/hangarfit/issues/263) · **ADR:** new **ADR-0022** · **Effort:** M, 1 PR (supervised) · **Status:** ratified design (this session) ready for implementation plan

---

## 1. Problem

Owners want a parked aircraft pointing **out** (nose toward the door at `y = 0`)
so it can taxi straight out. Under the ADR-0002 convention `heading_deg` is the
compass angle of the nose from world **+y** (deeper into the hangar), CW positive:
**`heading 0` = nose-IN** (toward +y), **`heading 180` = nose-OUT** (toward the
door at −y). "nose-out-ness" = the short-arc heading distance to `180`.

Today the RR-MC solver chooses each parked heading purely for **packing density**
(`_initial_placement_for_plane` seeds `heading = rng.random()*360`, perturbed by
the descent and the `_spread` post-pass), then hands it to the tow planner
verbatim (`Pose.from_placement`). **Nothing favours `heading ≈ 180`.**

## 2. Why this was blocked twice — and why it is now unblocked

#263 was deferred (2026-06-01, 2026-06-02) on a single open question: the
**entry-vs-exit objective**. The tow planner only ever plans the empty-hangar
**FILL = ENTRY** (door → parked slot); the parked heading is the *goal* of that
entry path. The fear was that a nose-out goal forces a wasteful ~180° loop or a
reverse-in during entry, *increasing* entry cost — trading exit ease for entry
cost.

**This is now empirically cleared by #480** (fewest-moves tow routing, shipped to
develop 2026-06-07, ADR-0010 amendment). #480 added a cusp-penalty cost model
(`length + CUSP_PENALTY·cusps`, `CUSP_PENALTY = 10`), a nose-out-gated rear-entry
cone, and a cost-aware start-seed analytic expansion. The planner now **backs a
nose-out plane in** (tail-first) at essentially zero extra entry cost rather than
looping. A direct probe on the roomy-3 fixture
(`tests/fixtures/solve_fresh_alternatives_three.yaml`, seed 1, spread on) routed
each plane at its solver heading vs. flipped 180°:

| plane | solver h → flipped h | off-nose-out (°) | Δ entry-path length | cusps |
|---|---|---|---|---|
| `aviat_husky` | 325.6 → 145.6 | 145.6 → **34.4** | **−0.00 m** | 1 → 1 |
| `ctsl` | 35.6 → 215.6 | 144.4 → **35.6** | **+0.17 m** | 0 → 0 (backs in, `RRR`) |
| `fuji` | 96.7 → 276.7 | 83.3 → 96.7 (*worse* — correctly NOT flipped) | — | — |

The ADR-0010 #480 amendment pins the same result: a nose-out goal's in-hangar
swept turn drops **162° → <45°** with `expansions = 0`. So the historical
objection no longer holds: a nose-out flip is **gap-neutral** (position fixed) and
now **entry-cost-neutral** (post-#480 reverse/back-in).

**#480 shipped the routing half** (make a nose-out slot cheap to *reach*). #263 is
**only the solver-preference half** (make the solver *prefer* to pick nose-out
slots). Do **not** re-implement the rear cone / cusp cost / cost-aware expansion —
they exist.

## 3. Decisions (this session)

1. **Default ON** (`--no-nose-out` to disable), mirroring `--no-spread` / ADR-0008.
2. **Flip gate = "strictly more nose-out".** Flip iff
   `short_arc(flipped, 180) < short_arc(current, 180)` **and** the layout stays
   valid. Because the flip is the *exact antipode* and
   `short_arc(antipode,180) = 180 − short_arc(h,180)`, this is **identical** to
   "only flip into the nose-out hemisphere (`short_arc(flipped,180) < 90`)" — the
   two framings converge to one rule: *flip iff the current heading sits in the
   nose-in hemisphere*. (No near-sideways special-casing; a 95°-off plane flips to
   85°-off, both rules agree.)
3. **Include `tow_pivotable`** in this PR (the coupled flag from the 2026-05-29
   issue comment). Note its original "pivot beats the arc loop" rationale is now
   *weaker* (#480's reverse already beats the loop), but it remains a realistic
   per-plane towing capability and is the cheap pivot form of a nose-out flip for
   free-castering / nose-lift types.

## 4. Architecture

Two orthogonal, independently-testable units.

### 4a. `_nose_out` solver post-pass (the core of #263)

A **pure, RNG-free** transform inserted in `solver.py`'s valid-basin handler,
**after** `_spread` and **before** the `Layout` rebuild (between solver.py:304 and
:307), mirroring `_spread`'s discipline so the appended basin and its
`_spread_quality(min_gap, energy)` reflect the flipped headings:

```python
def _nose_out(
    placements: dict[str, Placement],
    scenario: Scenario,
    search: SearchConfig,
    *,
    pinned_planes: frozenset[str],
) -> tuple[dict[str, Placement], int]:
    """RNG-free 180° flip toward nose-out (heading 180). Returns the (possibly
    mutated) placements and the count of flips applied. Soft: never breaks
    validity, never moves a plane, never un-parks one."""
```

Call site (gated on a new `search.nose_out`, **independent of `search.spread`**):

```python
if current_score == (0, 0.0):
    if search.spread:
        placements = _spread(...)
    if search.nose_out:
        placements, n_flips = _nose_out(
            placements, scenario, search, pinned_planes=pinned_planes
        )
    # ... Layout rebuild, _spread_quality, pool.append (carry n_flips) ...
```

Algorithm (O(planes), no RNG, single deterministic sweep):

1. `movable = sorted(pid for pid in placements if pid not in pinned_planes)` —
   same deterministic ordering as `_spread` (solver.py:1113).
2. For each `pid` in `movable`, in order:
   - Resolve the per-plane preference: `c = scenario.constraints.get(pid)`;
     `want = c.nose_out if (c and c.nose_out is not None) else search.nose_out`.
     `want is False` ⇒ skip (the legitimate nose-*in* exemption).
   - `cur = placements[pid].heading_deg`; `flip = (cur + 180.0) % 360.0`.
   - Gate: only if `_heading_delta_short_arc(flip, 180.0) <
     _heading_delta_short_arc(cur, 180.0)` (strictly more nose-out).
   - Build a trial dict with `pid` flipped (zero displacement; `on_carts`
     preserved), build a trial `Layout`, accept iff `_score(trial) == (0, 0.0)`.
   - **Apply-and-revalidate against the CURRENT (possibly already-flipped) set** —
     each accepted flip mutates `placements` before the next plane is considered,
     so two individually-valid-but-jointly-invalid flips can never both land.
3. Return `(placements, n_flips)`.

Reuses the existing zero-displacement antipodal flip arithmetic
(`(h + 180.0) % 360.0`, solver.py:1455) and `_heading_delta_short_arc`
(solver.py:1582-1600). **Takes no `rng`** — it must not consume any RNG draw, or
it shifts the seeded stream for every subsequent restart and breaks the ADR-0003
byte-identical contract.

Because `nose_out` is a separate `SearchConfig` field, it **persists through the
`solve()` spread→no-spread fallback** (`replace(effective_search, spread=False)`,
solver.py:150-171 flips only `spread`): nose-out still applies on the no-spread
retry. This is intended — flipping a plane's nose toward the door (the entry cone
the planner threads from) can only help, not hurt, tow-routability.

### 4b. `tow_pivotable` Aircraft flag

A per-plane `tow_pivotable: bool = False` on `Aircraft`. A flagged plane is
planned with the pivot-in-place tow motion by having `effective_turn_radius_m()`
return `0.0`, reusing the existing `always_cart` zero-radius cart-pivot machinery
(`_plan_cart`). `towplanner.py` is **untouched** — `plan_path` already reads
`mover.effective_turn_radius_m()` (towplanner.py:2007) and dispatches `r == 0` to
the cart fan, so there is **no new motion primitive** and the determinism surface
is unchanged.

```python
def effective_turn_radius_m(self) -> float:
    if self.movement_mode == "always_cart" or self.tow_pivotable:
        return 0.0
    return self.required_turn_radius_m()
```

- **Orthogonal to `movement_mode`** (NOT a new mode). A flagged own-gear plane
  stays `on_carts = False`; `Layout` cart invariants and cart-pool accounting are
  untouched.
- **No `__post_init__` relaxation needed:** the flagged fleet planes
  (`aviat_husky` own-gear r=5.0, `ctsl`/`fk9_mkii` cart-eligible r=4.0) all carry a
  real positive `turn_radius_m`, so the existing guard (models.py:267-277) is
  satisfied; `effective_turn_radius_m()` simply overrides the *tow* radius to 0.
- **Fleet flags:** set `tow_pivotable: true` for `aviat_husky`, `fk9_mkii`, `ctsl`
  in `data/fleet.yaml` (free-castering tailwheel / tail-down nose-lift). Leave
  `cessna_140` `false` (a club data call). Datum-pivot approximation holds (flagged
  planes' main gear ≤ 0.5 m from datum).
- **Blast radius (to measure during TDD):** flagging these three changes their
  *tow paths* (arc → pivot) in every non-stubbed `plan_fill`/`plan_path` fixture
  that routes them — not just nose-out cases. The determinism *contract* holds
  (static fleet property ⇒ same input → same output), but pinned-path/path-quality
  golden tests for these planes will need re-baselining. If the measured blast
  radius is large or risky, surface it before mass-regenerating.

## 5. Models changes (`models.py`)

| Type | Field | Default | Notes |
|---|---|---|---|
| `PlaneConstraint` | `nose_out: bool \| None` | `None` | Tri-state. `None` = follow global `SearchConfig.nose_out`; `True` = prefer-out; `False` = never flip (nose-in exemption). Mirrors the `force_on_carts: bool \| None` idiom (models.py:633) but with **different None-semantics** ("follow global", not "free") — document this divergence (type-design-analyzer will flag it). |
| `SearchConfig` | `nose_out: bool` | `True` | Plain `bool` (NOT `bool \| None`): respects the documented sentinel policy (None reserved for `max_restarts`/`spread_stall_restarts`). The global default the constraint tri-state defers to. |
| `SolverDiagnostics` | `nose_out_flips: tuple[int, ...]` | `()` | Per-layout flip count, index-aligned with `SolveResult.layouts`, mirroring `min_pairwise_gap_m`. Add the matching length guard in `SolveResult.__post_init__` (models.py:1014-1021). Advisory/RNG-free. |
| `Aircraft` | `tow_pivotable: bool` | `False` | After the other defaulted field (`notes`), so it doesn't precede a non-defaulted field. |

## 6. Loader / CLI wiring

- **`loader._build_plane_constraint`** (loader.py:510-574): parse `nose_out`
  copying the `force_on_carts` tri-state idiom (loader.py:564-566) —
  `raw = data.get("nose_out"); nose_out = _to_bool(raw, "nose_out") if raw is not None else None`.
- **`loader._build_aircraft`** (loader.py:795-878): parse `tow_pivotable` copying
  the `measured` bool precedent (loader.py:872) —
  `tow_pivotable=_to_bool(entry.get("tow_pivotable", False), "tow_pivotable")`.
- **`cli` `--no-nose-out`** (mirror `--no-spread` at cli.py:246-252):
  `action="store_false", dest="nose_out", default=True`. Add to **both** the
  `solve` parser and the `view` parser (view `--solve` routes through `solve()`).
  Thread into `SearchConfig(spread=..., nose_out=args.nose_out, ...)` at
  cli.py:633-637 and the view solve site (cli.py:1028-1036). **`solve`-scoped**:
  nose-out is a solver-time concern; `view` layout-mode (no `--solve`) calls
  `plan_fill` directly and needs no nose-out knob (the planner already routes
  whatever heading it is given optimally via #480).
- **`--json`** (cli.py:940-983): add `nose_out_flips` to the diagnostics payload,
  additive (no schema bump), mirroring `apron_shallow_drops` (cli.py:977-980).
  **No stderr warning** — a flip is good news, not a degradation (contrast #509's
  apron-shallow warning).
- **`--no-nose-out` disables only the solver post-pass.** The #480 rear-entry cone
  in `towplanner.py` stays geometry-gated: with the post-pass off the solver simply
  won't produce nose-out targets, so the cone never fires — no planner change
  needed. Ownership stays clean (solver = preference, planner = cheap routing).

## 7. Determinism plan (ADR-0003)

- `_nose_out` is **RNG-free** ⇒ byte-identical run-to-run **even with the feature
  ON** (strictly stronger than `_spread`, which only guarantees byte-identity when
  off). Determinism is structural: `sorted(...)` plane-id iteration, deterministic
  antipodal arithmetic, deterministic `_score` accept.
- `nose_out` runs independently of `spread`. The existing canaries pass
  `SearchConfig(spread=False)` and would now *also* exercise `nose_out=True`
  (default), changing their output. **Pin the canaries with explicit
  `nose_out=False`** so the pure-RNG contract test stays unambiguous; add a
  **new** `nose_out=True` canary bounded by **`max_restarts`** (not `budget_s`) —
  spread/post-pass work under a wall-clock budget is allowed to drift across
  machines (#267), so a budget-bound nose-out canary would be flaky and must be
  `@pytest.mark.serial` if used at all; prefer the `max_restarts`-bound one.
- **Cross-process canary:** copy `test_apron_movesplan_byte_identical_across_processes`
  (the `PYTHONHASHSEED`-varied subprocess pattern) to pin the one new `sorted(...)`
  iteration — an in-process `==` cannot catch a `set`-iteration-order leak.
- `tow_pivotable` is a static fleet property ⇒ same input → same output; no
  determinism risk (the model field is inert; it changes *which* path is optimal,
  deterministically).
- **`determinism-guard` agent amendment** (`.claude/agents/determinism-guard.md`):
  add `_nose_out` to the solver.py mechanisms list — "RNG-free deterministic
  post-pass; verify movable-plane iteration stays `sorted(...)` and no `rng.*`
  leaks in"; note `tow_pivotable` is a static-data radius override with no
  determinism impact.

## 8. Test plan (TDD, RED first)

**`tests/test_solver_nose_out.py`** (new):
- nose-in plane flips to ≈180 (the real benefit).
- already-nose-out plane is a no-op (gate is strict `<`).
- exactly-sideways (`h=90`/`h=270`) not flipped (strict `<`, equal distance).
- a plane whose only valid heading is nose-in stays nose-in (soft-not-hard pin).
- pinned planes never flip.
- `PlaneConstraint(nose_out=False)` excludes a plane even with global ON.
- with `SearchConfig(nose_out=False)`, only planes with `constraint.nose_out=True`
  flip.
- determinism: `test_nose_out_is_rng_free` (two `nose_out=True` runs byte-identical
  via `.placements` tuple equality); `nose_out=False` byte-identical to the
  pre-feature golden; cross-process `PYTHONHASHSEED`-varied variant.
- diagnostics: `nose_out_flips` count matches applied flips, per-layout aligned.
- **avoid the det-−1 sign trap:** use a distinguishing (non-axis-aligned) assertion
  on the flipped heading, not a value that passes under an inverted sign.

**`tests/test_solver_canaries.py` / `test_solver_search.py`:** add explicit
`nose_out=False` to the existing byte-identical canaries; add one
`max_restarts`-bound `nose_out=True` canary.

**`tow_pivotable` tests** (extend `tests/test_models*.py` + a towplanner test):
- `effective_turn_radius_m()` returns 0.0 for a flagged own-gear plane, real radius
  when unflagged.
- loader default `False`; loader parses `tow_pivotable: true`.
- a flagged plane yields a pivot-style path (delegates to `_plan_cart`).
- a **path-quality** test: a 180° nose-out flip plans shorter as a pivot than as a
  forward arc loop (the measured ~15% in open space) — keep ≥1 **non-slow** test
  per new path (coverage two-pass, #492).
- **Do NOT** assert "dense fills become routable" — falsified in the issue's
  characterization.

**Loader/CLI tests:** `--no-nose-out` sets `SearchConfig.nose_out=False`; per-plane
YAML `nose_out:` round-trips; `--json` includes `nose_out_flips`.

**Acceptance:** a roomy fixture (model on `solve_fresh_alternatives_three.yaml` in
the 30×25 `test_hangar_large.yaml`) where the rendered parked heading lands within
90° of nose-out and the tow plan is no longer than the nose-in equivalent.
Re-baseline **only headings** of nose-out-affected fixtures (positions/status/
min-gap must NOT change), plus `tow_pivotable` tow-path goldens for the three
flagged planes.

## 9. Docs

- **ADR-0022** (Proposed): "Nose-out parked heading (soft 180° flip post-pass) +
  `tow_pivotable` towing motion." Mirror ADR-0008's structure (soft, post-validity,
  determinism claim). Cover: the RNG-free flip post-pass (default ON, tri-state
  per-plane override, strict-more-nose-out gate ≡ nose-out-hemisphere), the
  entry-vs-exit resolution via #480, the `tow_pivotable` orthogonal-flag /
  datum-pivot-approximation / towing-vs-powered-taxi rationale, and the
  alternatives rejected (descent fusion — perturbs RNG; planner goal-pose — splits
  ownership). Add to `docs/adr/README.md`.
- **arc42 §8** (`docs/architecture/08-crosscutting-concepts.md`): extend the "Soft
  preferences" section (≈line 526) with the nose-out preference next to spread;
  note `tow_pivotable` in the parts/motion model.
- **CHANGELOG** `[Unreleased] ### Added`: the `_nose_out` preference + `--no-nose-out`
  + per-plane `nose_out:` + `tow_pivotable`.

## 10. File list

`solver.py`, `models.py`, `loader.py`, `cli.py`, `data/fleet.yaml`,
`docs/adr/0022-nose-out-parked-heading.md`, `docs/adr/README.md`,
`docs/architecture/08-crosscutting-concepts.md`, `CHANGELOG.md`,
`.claude/agents/determinism-guard.md`, `tests/test_solver_nose_out.py` (new) +
canary/loader/models/cli/towplanner test edits + (likely) a roomy nose-out solve
fixture.

## 11. Review guards

`determinism-guard` (mandatory — solver.py), `type-design-analyzer` (models.py —
the tri-state `nose_out` and `tow_pivotable`), `silent-failure-hunter` (loader),
`comment-analyzer` (ADR + docstrings), `pr-review-toolkit:code-reviewer` (main
pass). **No** `geometry-invariant-guard` (no geometry math changes).

## 12. Out of scope

- An arbitrary nose-out *angle* (the flip reaches only the antipode `{h, h+180}`;
  in-vs-out is the stated benefit).
- Dense-fill towability (a corridor/wingspan-aware packing or #336 RRT-Connect
  concern — falsified for `tow_pivotable`).
- Main-gear-offset pivot (datum-pivot approximation is sufficient for the flagged
  planes; defer).
- Exit-path planning (the planner plans entry only; exit ease is achieved purely
  by the parked heading).
