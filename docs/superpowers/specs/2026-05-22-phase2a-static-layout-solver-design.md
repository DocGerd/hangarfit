# Phase 2a — Static layout solver, design

**Status:** draft 2026-05-22 (awaiting review)
**Tracks:** (to be assigned to a new issue once spec is approved) — first slice of Phase 2 ("planner / search / optimization") that CLAUDE.md flagged as out of scope for Phase 1.
**Author:** Claude (Opus 4.7), brainstormed with @DocGerd
**Depends on:** [#79 — real Herrenteich hangar dimensions](https://github.com/DocGerd/hangarfit/issues/79) (not a hard blocker for implementation, but a hard blocker for trusting solver outputs against operational reality).

A solver that takes a *fleet subset + hard constraints* and returns *up to K diverse valid layouts*, built on top of the Phase 1 collision substrate. Covers both **fresh-layout** ("fit these planes") and **minimal-edit repair** ("baseline broke, fix it") via the same engine — repair is implemented as pinning the unchanged planes.

---

## 1. Goals & non-goals

**Goals**

- One library function `solver.solve(scenario)` and one CLI subcommand `hangarfit solve` that turn a `Scenario` (fleet subset + maintenance plane + per-plane constraints) into a `SolveResult` (up to K diverse `Layout`s plus diagnostics).
- Fresh-layout mode and minimal-edit repair mode share the same search engine; repair is just "pin everything that didn't change."
- Continuous placement (any `x_m`, `y_m`, any `heading_deg`) — no grid, no axis-aligned restriction.
- Honest failure modes: when the solver gives up, the user gets `status`, `best_partial`, and the conflict list, not a black-box "didn't work."
- Reproducible given a seed.
- Tests use the existing fixture-driven pattern; no new test infrastructure.

**Non-goals (Phase 2a)**

- **No move-sequence planning** (Phase 2b's territory). 2a returns end-state layouts; it doesn't reason about pulling planes out the door.
- **No cross-run hangar state persistence** (Phase 2c).
- **No soft constraints / preferences** — only hard constraints (pin, force_on_carts, maintenance plane).
- **No region constraints** or **heading-only locks** without position locks. Pin is the only positional constraint primitive; richer constraint language is a v1.1+ addition.
- **No completeness guarantee.** When the solver returns "no layout found in budget," that is a *probabilistic* claim, not a proof of infeasibility — except for the three `trivially_infeasible` cases enumerated in §4.1, which are literal impossibilities.
- **No CLI flag for `SearchConfig` hyperparameters** (`N`, `K_stall`, perturbation thresholds) — those are programmatic only, exposed via the `solve()` kwarg. Surfacing them on the CLI is v1.1.
- **No structured min-conflicts moves** that use the conflict's separation axis as a direction. Requires extending `Conflict` to carry penetration geometry — a Phase 1 surgery deferred to v1.1.

---

## 2. Summary of design decisions

A high-level index of the choices the spec encodes. Sections 3–6 contain the full adversarial review of each.

| # | Decision | Choice | Confidence |
|---|---|---|---|
| 1 | Scenario shape | Fresh + repair dispatched by input; repair = "pin everything unchanged" | high |
| 2 | Placement model | Continuous `(x, y)` + any `heading_deg` | high |
| 3 | Constraint set | Standard: maintenance plane, per-plane `pin` (full Placement), per-plane `force_on_carts` | high |
| 4 | Output cardinality | Up to K diverse alternatives (`--alternatives N`, default 1) | high |
| 5 | Diversity metric | Edit-count: ≥2 planes moved >0.5 m OR >30° between any two accepted layouts | high |
| 6 | Algorithm family | Random-restart hill climbing with min-conflicts descent (RR-MC) | high |
| 7 | Scoring function | Hierarchical `(conflict_count, total_penetration_m2)` | medium-high (requires Phase 1 `CheckResult` extension — see §7) |
| 8 | Cart assignment | Round-robin over `{no-carts, plane_i-on-carts}` buckets across restarts | high |
| 9 | Pre-search infeasibility | Three literal-impossibility checks (per-plane bbox > hangar, Σ areas > hangar, pin self-collision via `check()`) | high |
| 10 | `--render` PATTERN with K>1 | Require `{i}` placeholder; loud failure if absent | medium-high |
| 11 | Exit code for `found_partial` | Default 0 (forgiving); `--strict-k` flag opts into exit 1 | high |
| 12 | Layout YAML output | `--write-yaml PATTERN` flag (parallel to `--render`) | high |
| 13 | Testing | Fixture-driven (`tests/fixtures/solve_*.yaml`), 12-fixture v1 matrix, `pytest.mark.slow` for long tests | high |
| 14 | Determinism | Bit-for-bit on a small "canary" fixture subset; outcome-equality everywhere else | medium-high |

---

## 3. Types and module layout (Section A)

### 3.1 New module

`src/hangarfit/solver.py` — public `solve()` entry point + internal RR-MC search loop + diversity filter. Flat module, no `solver/` package (matches Phase 1 convention of `loader.py`, `geometry.py`, `collisions.py`, etc.).

### 3.2 New types in `models.py`

All frozen + slots; `__post_init__` invariant checks (matches Phase 1 style).

```python
@dataclass(frozen=True, slots=True)
class PlaneConstraint:
    """Per-plane HARD constraints for a Scenario.

    All fields optional — a constraint with everything None means 'free'.
    """
    pin: Placement | None = None              # locks plane to exact placement
    force_on_carts: bool | None = None         # True / False / None (unrestricted)


@dataclass(frozen=True, slots=True)
class Scenario:
    """Solver input.

    Cross-reference invariants validated in __post_init__:
    - every fleet_in id exists in fleet
    - maintenance_plane (if set) is in fleet_in
    - constraints.keys() ⊆ set(fleet_in)
    - for each (plane_id, constraint): constraint.pin.plane_id == plane_id (if pin set)
    - force_on_carts is consistent with movement_mode:
        force_on_carts=True  → plane must NOT be always_own_gear
        force_on_carts=False → plane must NOT be always_cart
    - pin.on_carts is consistent with movement_mode (same rules — a pin sets all of
      x_m / y_m / heading_deg / on_carts, so its on_carts must match the plane's mode)
    - if both a pin and force_on_carts are set for the same plane, they must agree
      on on_carts (otherwise the constraint set is self-contradictory)
    - fleet dict is wrapped in MappingProxyType (same pattern as Layout)

    Default-value note (implementation): the `constraints` default below is shown as
    a literal `MappingProxyType({})` for readability, but the actual implementation
    must use `dataclasses.field(default_factory=lambda: MappingProxyType({}))` to
    avoid sharing the same proxy instance across all default-constructed Scenarios.
    """
    fleet: Mapping[str, Aircraft]
    hangar: Hangar
    fleet_in: tuple[str, ...]
    maintenance_plane: str | None = None
    constraints: Mapping[str, PlaneConstraint] = MappingProxyType({})


SolveStatus = Literal[
    "found",                 # all K alternatives found
    "found_partial",         # 1 ≤ found < K within budget
    "exhausted_budget",      # 0 valid layouts; best_partial available
    "trivially_infeasible",  # provably no solution per §4.1
]


@dataclass(frozen=True, slots=True)
class SolverDiagnostics:
    restarts_attempted: int
    wall_time_s: float
    best_partial: CheckResult | None       # lowest-conflict CheckResult seen
    best_partial_layout: Layout | None     # the matching Layout (so it can be rendered)
    seed: int                              # actually-used seed (None resolved to entropy)


@dataclass(frozen=True, slots=True)
class SolveResult:
    status: SolveStatus
    layouts: tuple[Layout, ...]            # 0..K elements
    diagnostics: SolverDiagnostics


@dataclass(frozen=True, slots=True)
class DiversityConfig:
    min_planes_moved: int = 2
    position_threshold_m: float = 0.5
    heading_threshold_deg: float = 30.0


@dataclass(frozen=True, slots=True)
class SearchConfig:
    """Solver hyperparameters. v1 defaults are guesses; tune with real data."""
    candidates_per_iter: int = 8
    k_stall: int = 50
    pos_sigma_m: float = 0.5
    heading_sigma_deg: float = 10.0
```

### 3.3 Public API

```python
def solve(
    scenario: Scenario,
    *,
    budget_s: float = 30.0,
    alternatives: int = 1,
    seed: int | None = None,
    diversity: DiversityConfig = DiversityConfig(),
    search: SearchConfig = SearchConfig(),
) -> SolveResult: ...
```

**Note:** `strict_k` is a CLI-only concept (it controls exit-code translation from
`SolveResult.status`). The library API does not need it — callers inspect
`result.status` directly and decide how to react. The CLI implements `--strict-k`
by mapping `found_partial → exit 1` after calling `solve()` with no extra flag.

**Default-factory note (implementation):** the `diversity` and `search` defaults shown above as `DiversityConfig()` / `SearchConfig()` are literal calls for readability. Both Configs are frozen + slots + immutable, so sharing a single default instance across calls is *theoretically* safe. The implementation may use either (a) `None` sentinels resolved at function entry, or (b) `field(default_factory=...)` if the kwargs are wrapped in a dataclass — both are fine; whichever keeps the linter quiet wins.

### 3.4 Loader extension

`loader.py` gains `load_scenario(path, *, fleet=None, hangar=None) -> Scenario` — mirrors `load_layout` exactly (same conflict policy: refuse if both YAML-embedded ref and programmatic override are supplied; same path-resolution rules).

Scenario YAML schema:

```yaml
fleet: ../data/fleet.yaml
hangar: ../data/hangar.yaml
fleet_in: [aviat_husky, fuji, ctsl, scheibe_falke, fk9_mkii, cessna_140]
maintenance:
  plane: scheibe_falke
constraints:
  aviat_husky:
    pin: { x_m: 2.1, y_m: 14.3, heading_deg: 0, on_carts: false }
  cessna_140:
    force_on_carts: true
```

### 3.5 Reuse of Phase 1 invariants

The solver builds a candidate `Layout` from `Scenario + (placements it sampled)` on every iteration. `Layout.__post_init__` then validates cross-reference invariants (cart rule, movement_mode consistency, fleet/placement key match, maintenance plane in fleet) for **free** — the solver doesn't re-encode those rules; it relies on the existing dataclass to throw `ValueError` if it ever proposes something illegal. This means a bug in the solver that violates the cart rule can never produce a returned `Layout` — `Layout.__post_init__` rejects it first.

---

## 4. Algorithm internals (Section B)

The algorithm family is **RR-MC**: many independent random-initialized trajectories, each performing min-conflicts greedy descent until either (a) a valid layout is found and added to the alternatives pool, or (b) the trajectory plateaus and is restarted. The full alternatives reasoning + adversarial review is preserved here because the user explicitly values seeing the decisions survive scrutiny (see [[feedback_adversarial_design_review]] in author memory).

### 4.1 Pre-search infeasibility checks

Before the first restart, run three literal-impossibility tests in order. If any fires, return `SolveResult(status="trivially_infeasible", layouts=(), diagnostics=...)`.

1. **Per-plane bounding box vs. hangar.** For each plane in `fleet_in`, compute a coarse plane-local bbox by taking the maximum `length_m` and maximum `width_m` over all of the plane's `Parts` (ignoring per-part offsets — see §7.1 / §10.3 for why this is a *lower bound* on the true outline, which is safe as a literal-infeasibility gate but not as a tight feasibility check). Then reject if `bbox_length > max(hangar.length_m, hangar.width_m)` *or* `bbox_width > max(hangar.length_m, hangar.width_m)` — using `max(...)` for both because the plane can be rotated into either orientation. Catches typos like a 200 m wingspan.
2. **Σ areas vs. hangar floor area.** Sum each plane's bbox area; reject if `> hangar.length_m × hangar.width_m`. (No margin — only flag *literal* infeasibility.)
3. **Pin self-collision.** Build a `Layout` containing only the pinned planes (skipping all unpinned ones); call `collisions.check()`. Any conflict → infeasible, return the conflict list as `diagnostics.best_partial` so the user sees which two pins clash, or which pin lies outside the hangar (caught as a `hangar_bounds` conflict).

   **Important construction detail:** the pin-only Layout is built with `maintenance_plane=None` regardless of the scenario's `maintenance_plane`. `Layout.__post_init__` enforces "maintenance_plane is placed if set" — but at this stage we're skipping all unpinned planes, so the maintenance plane (if not itself pinned) would be missing and trigger that invariant. The pre-search check is *only* about pin-vs-pin self-collision and pin-vs-hangar bounds; maintenance-position is irrelevant here (no maintenance plane → no maintenance-position rule).

**Decisions challenged.** Original design had a 0.95 margin on step 2 ("Σ areas > 0.95 × hangar area"). Adversarial review rejected the magic number — false positives are worse than missing a soft "probably won't fit" warning, since step 2 is a *defensible* check only when it represents a literal impossibility. The 0.95 was a heuristic without empirical backing; dropped.

### 4.2 Initial placement (per restart)

For each plane in `fleet_in`:

- **Pinned planes** → placement is `constraint.pin` verbatim. Never moves during the trajectory.
- **Maintenance plane** (if not pinned) → biased initial: `(x_m, y_m)` uniform in the back maintenance-bay strip (depth = `hangar.maintenance_bay.depth_m`), `heading_deg` uniform on `[0°, 360°)`.
- **Other free planes** → `(x_m, y_m)` uniform inside the hangar interior with a margin equal to the plane's max-extent bbox / 2 (to reduce initial bounds violations), `heading_deg` uniform on `[0°, 360°)`.

**Cart assignment is fixed at restart time, round-robin** over the unlocked buckets. With C cart_eligible planes that are not locked by `force_on_carts`, the buckets are `{none, plane_1_on_carts, plane_2_on_carts, …, plane_C_on_carts}` — i.e., `C + 1` buckets. Restart number R picks bucket `R mod (C+1)`. Round-robin guarantees every cart configuration is sampled at least once within a small restart budget, beating the random-per-restart alternative (which can leave buckets unsampled).

**Decisions challenged.** Original design had **random** per-restart cart picks. Adversarial review changed to **round-robin** for guaranteed coverage at the same compute cost. Maintenance-plane biased initial survived: alternatives ("no bias", "auto-pin maintenance to bay center", "pin + heading-only search") all lose either flexibility or impose unwarranted structure.

### 4.3 Descent step (min-conflicts perturbation)

Given a current candidate layout with `score > (0, 0)`:

1. Build the conflicting-plane set `S = (⋃ c.planes for c in check_result.conflicts) − pinned_planes`. If `S` is empty → trajectory is stuck (all conflicts involve only pinned planes, which can't move); abort restart.
2. Pick one plane from `S` uniformly at random.
3. Generate `N = candidates_per_iter` candidate perturbations for that plane (default `N = 8`):
   - **6 small Gaussian nudges**: `(dx, dy) ~ N(0, pos_sigma_m)`, `dh ~ N(0, heading_sigma_deg)`.
   - **1 large jump**: re-sample `(x_m, y_m)` uniformly over the hangar (with the same margin as initial placement); `heading_deg` uniform on `[0°, 360°)`.
   - **1 heading-only 180° flip**: keep `(x_m, y_m)`, set `heading_deg = current + 180°`. Domain heuristic for "nose-wrong-way" cases.
4. For each candidate, build a tentative `Layout` (cart assignment unchanged) and score it via `_score(layout)` (§4.4). Select the candidate with the lowest `score`. Tie-break by **smallest displacement** from the current state (smooth trajectory).
5. Accept iff `new_score ≤ current_score` (greedy `≤` allows plateau traversal — strict `<` is too aggressive on integer-valued conflict counts).

**Decisions challenged.** The perturbation mix (`6 + 1 + 1`) is a heuristic; alternatives include "8 Gaussian only" (too local), "4 + 4" (more exploration, more noise), "structured moves from conflict geometry" (more principled but requires `Conflict.penetration_axis` — deferred to v1.1). Default `N = 8` is a guess; should be tuned with measured `check()` cost. The 180° flip is a domain bet that may never fire usefully — harmless if it doesn't.

### 4.4 Scoring function

`_score(layout: Layout) -> tuple[int, float]` returns `(conflict_count, total_penetration_m2)`.

- **Primary key** `conflict_count = len(check_result.conflicts)`. Integer.
- **Secondary key** `total_penetration_m2 = sum over conflicts of overlap_area(part_a, part_b)`. Computed via shapely `polygon.intersection(other).area` for each pairwise conflict. Conflicts that reference only a single plane (per `Conflict.planes` having length 1 — currently `maintenance_position`, `maintenance_no_fuselage`, `hangar_bounds`) contribute 0 to the sum: they describe a different kind of invalid (a plane in the wrong place rather than two planes overlapping) and have no second polygon to intersect against.
- Lexicographic comparison: lower-`conflict_count` wins; ties broken by lower-`total_penetration_m2`.
- `score == (0, 0.0)` means valid.

**Phase 1 surgery required** (see §7): extend `CheckResult` with `total_penetration_m2: float = 0.0` (backward-compatible default) and have `collisions.check()` populate it.

**Decisions challenged.** The original spec used pure conflict count for v1 simplicity, with a deferred-instrumentation plan to upgrade if plateau pathology showed up in real data. Adversarial review surfaced that integer-count metrics are well-known to plateau on packing problems — the cost of `(count, depth)` is modest (small Phase 1 extension; one shapely call per conflict) and the algorithmic gain is real (smooth secondary signal for descent direction). User chose hierarchical.

### 4.5 Restart trigger

Restart the current trajectory whenever any of:

- `score == (0, 0.0)` → valid! Pass to the diversity filter (§4.6), then restart fresh.
- `k_stall = 50` consecutive iterations without `score` strictly decreasing (in lexicographic order).
- Conflicting-plane set `S` is empty (all conflicts involve pinned planes only — locally unsolvable).

Pin assignments survive across restarts; everything else re-randomizes.

### 4.6 Diversity filter

When a trajectory yields a valid layout `L`:

- For each already-accepted layout `L'` in `accepted_layouts`:
  - `n_moved(L, L') := |{ plane p : ||L[p].xy − L'[p].xy|| ≥ position_threshold_m  OR  |L[p].heading − L'[p].heading| (normalized to [0, 180°]) ≥ heading_threshold_deg }|`
  - If `n_moved(L, L') < min_planes_moved` for any `L'` → `L` is too similar; **reject** (don't add).
- Else → accept; append to `accepted_layouts`.
- If `len(accepted_layouts) == alternatives` → terminate search; return `status="found"`.

**Heading delta normalization**: `min(|a − b|, 360 − |a − b|)` — i.e., the shorter arc, so that `0°` and `359°` are 1° apart, not 359° apart.

**Diversity-impossible detection.** If `(|fleet_in| − |pinned_planes|) < min_planes_moved` AND `alternatives > 1`, the user cannot mathematically get more than one accepted layout: every candidate `L'` after the first will share too many planes with `L` to pass the `n_moved ≥ min_planes_moved` test. Solver logs a warning at `solve()` entry (`"requested K alternatives but only 1 is achievable (M of N planes pinned)"`) but does **not** mutate the target `K` — search runs to budget, and the natural outcome is `found_partial` with one accepted layout. This is honest and avoids the API question of "what does it mean if the solver returned `found` for K=3 but actually only found 1 because it downgraded?"

### 4.7 Termination

Search ends when EITHER:

- `len(accepted_layouts) == alternatives` → `status = "found"`.
- Wall time exceeds `budget_s` → `status` derived from accepted count:
  - 0 accepted → `"exhausted_budget"` (diagnostics carries best partial).
  - `0 < n < alternatives` → `"found_partial"`.
  - `n == alternatives` → `"found"` (we hit budget exactly while filling — equivalent to the first condition by milliseconds).

### 4.8 Reproducibility

`solver.py` instantiates one `random.Random(seed)`. Every sampling decision (initial placement, perturbation, candidate selection, restart bucket index) pulls from it. `seed=None` resolves to `secrets.randbits(32)` at `solve()` entry; the resolved seed is recorded in `SolverDiagnostics.seed`.

Cart-assignment round-robin uses a non-random counter (the restart index) — it's deterministic by construction.

---

## 5. CLI surface (Section C)

### 5.1 Subcommand

```
hangarfit solve SCENARIO
                [--budget SEC]
                [--alternatives N]
                [--seed S]
                [--render PATTERN]
                [--write-yaml PATTERN]
                [--strict-k]
                [--json]
                [--fleet PATH]
                [--hangar PATH]
```

- `SCENARIO` — positional, required. Path to the scenario YAML.
- `--budget SEC` — float seconds, default 30.0. Wall-clock budget for the whole solve.
- `--alternatives N` — int, default 1. K diverse layouts to return (see §4.6).
- `--seed S` — int, optional. None resolves to entropy; resolved value recorded in diagnostics.
- `--render PATTERN` — write top-down PNGs. **Must contain `{i}` placeholder** if `--alternatives > 1`; loud error otherwise. For K=1, `{i}` is optional (substituted with `1` if present).
- `--write-yaml PATTERN` — write a layout YAML per returned layout. Same `{i}` placeholder rule.
- `--strict-k` — if `found_partial`, exit 1 instead of 0. Default off.
- `--json` — switch human stdout to JSON (schema `hangarfit.solve/v1`).
- `--fleet`, `--hangar` — same semantics as `check`'s overrides: refuse if scenario YAML also embeds the ref.

### 5.2 Exit codes

| Code | Meaning |
|---|---|
| 0 | Found ≥ 1 valid layout (`status` in `{"found", "found_partial"}`). `--strict-k` makes `found_partial` → 1. |
| 1 | No valid layout found (`status` in `{"exhausted_budget", "trivially_infeasible"}`). |
| 2 | Loader / usage error (LoaderError, argparse error, IO error during render/yaml write). |

`--strict-k` only flips 0→1 for `found_partial`. `found` stays 0; `exhausted_budget` / `trivially_infeasible` stay 1.

### 5.3 Stdout (human)

```
$ hangarfit solve scenario.yaml --alternatives 3 --render out_{i}.png
Found 3 layouts in 4.2s (seed=42, 47 restarts).
  #1: 6 planes placed; 0 conflicts; score=(0, 0.0)
  #2: 5 of 6 planes shifted vs #1 (avg shift 2.1 m)
  #3: 6 of 6 planes shifted vs #1 (avg shift 3.4 m), 5 of 6 vs #2
Wrote out_1.png, out_2.png, out_3.png
```

For `exhausted_budget`:

```
$ hangarfit solve scenario.yaml --budget 5
No valid layout found in 5.0s (seed=2731415, 12 restarts).
Best partial had 2 conflicts:
  - wing_wing_overlap [aviat_husky, ctsl]: closest distance 0.04 m (threshold 0.30 m)
  - hangar_bounds [scheibe_falke]: extends 1.20 m past back wall
Hint: increase --budget, or relax pins.
```

For `trivially_infeasible`:

```
$ hangarfit solve scenario.yaml
Trivially infeasible: fleet footprint exceeds hangar floor area.
  Σ plane bbox areas: 412.3 m²
  hangar floor area:  450.0 m²
  utilization:        91.6% (effective max with clearances: ~85%)
```

### 5.4 Stdout (JSON, schema `hangarfit.solve/v1`)

```json
{
  "schema": "hangarfit.solve/v1",
  "scenario": "scenario.yaml",
  "status": "found",
  "layouts": [
    {
      "placements": [
        { "plane": "aviat_husky", "x_m": 2.1, "y_m": 14.3, "heading_deg": 0.0, "on_carts": false },
        ...
      ],
      "maintenance_plane": "scheibe_falke"
    },
    ...
  ],
  "diagnostics": {
    "restarts_attempted": 47,
    "wall_time_s": 4.211,
    "seed": 42,
    "best_partial": null,
    "best_partial_layout": null
  }
}
```

`best_partial` and `best_partial_layout` are populated only for non-found statuses. The `best_partial` payload mirrors `hangarfit.check/v1`'s `conflicts` structure.

### 5.5 Loader behavior (`load_scenario`)

| Step | Behavior |
|---|---|
| YAML parse error | `LoaderError(f"{path}: YAML parse error: ...")` — same as Phase 1 |
| Missing required key (`fleet_in`, `maintenance`, etc.) | `LoaderError(f"{path}: missing required field 'X'")` |
| `fleet:` / `hangar:` both embedded and overridden | `LoaderError` — refuse to silently choose one |
| Path resolution for `fleet:` / `hangar:` | Resolved relative to scenario YAML's parent directory (same as `load_layout`) |
| `Scenario.__post_init__` raises `ValueError` | Wrap in `LoaderError(f"{path}: {e}")` |

Strict coercion via existing `_to_float` / `_to_bool` for all scalar fields.

### 5.6 Error handling matrix

| Trigger | Where caught | User sees | Exit |
|---|---|---|---|
| Invalid YAML | `LoaderError` from `load_scenario` | `error: <path>: YAML parse error: ...` | 2 |
| Unknown plane_id in `fleet_in` | `Scenario.__post_init__` → `LoaderError` | `error: <path>: fleet_in references unknown plane 'X'` | 2 |
| Pin outside hangar | Pre-search check (§4.1.3); status `trivially_infeasible` | `Trivially infeasible: pin for plane 'X' is outside hangar` | 1 |
| Two pins clash | Same as above | `Trivially infeasible: pins for 'X' and 'Y' overlap` | 1 |
| Diversity impossible | Logged warning at solve() entry; search continues to budget | `Warning: requested 3 alternatives but only 1 is achievable (5 of 6 planes pinned). Expect status=found_partial.` | 0 (or 1 with `--strict-k`) |
| Budget exhausted, 0 valid | Status `exhausted_budget`, `best_partial` populated | Conflict list from best partial | 1 |
| Budget exhausted, partial K | Status `found_partial` | Layouts written, summary noted | 0 (or 1 with `--strict-k`) |
| Render IO error | `OSError` caught in `cmd_solve` | `error: render failed: ...` | 2 |
| `--render` PATTERN lacks `{i}` and K>1 | argparse-level check or early validation in `cmd_solve` | `error: --render PATTERN must contain '{i}' when --alternatives > 1` | 2 |

---

## 6. Testing strategy (Section D)

### 6.1 Fixture format

YAML fixtures in `tests/fixtures/`, naming convention `solve_<class>_<slug>.yaml` paralleling existing `valid_*` / `invalid_*` layout fixtures. New fixture classes: `solve_fresh_*`, `solve_repair_*`, `solve_infeasible_*`, `solve_pinned_*`, `solve_cart_*`. Authored via the existing `/new-fixture` scaffolding skill.

### 6.2 Property assertions (every fixture test)

Every solve test asserts:

1. `result.status` matches the expected enum value.
2. For every layout in `result.layouts`, `collisions.check(layout).valid is True`. (Re-checking via Phase 1 is the independent validation that solver correctness rests on.)
3. `result.diagnostics.seed` is populated (`isinstance(int)`, not None).
4. If `result.status in {"exhausted_budget", "trivially_infeasible"}`: `result.diagnostics.best_partial is not None`.
5. If `len(result.layouts) > 1`: every pair `(L_i, L_j)` satisfies the diversity rule (`n_moved >= min_planes_moved`).
6. If `result.status == "trivially_infeasible"`: `result.diagnostics.wall_time_s < 0.5` (pre-search check ran, no actual search burned).

### 6.3 Determinism

A small canary fixture subset (3–5 scenarios) is tested for **bit-for-bit equality** across runs with the same seed: `solve(s, seed=42) == solve(s, seed=42)`. The canary set is explicitly fragile — any deliberate algorithm tweak requires updating the expected outputs, which is correct behavior (loud signal on accidental determinism breaks).

Outcome-equality (same status, same K modulo ordering) is asserted on the broader fixture matrix where bit-for-bit would be too brittle.

### 6.4 CI budget

Default per-fixture solve budget = **5 seconds**. Acceptable: 12 fixtures × 5 s = 60 s for the solver suite. Any test that needs > 5 s gets `@pytest.mark.slow` and is excluded from CI default (run on demand via `pytest -m slow`).

### 6.5 v1 fixture matrix

| Fixture slug | Scenario summary | Expected outcome |
|---|---|---|
| `solve_trivial_single_plane` | 1 plane, no constraints, large hangar | `found`, K=1 |
| `solve_fresh_six_planes` | 6 planes, maintenance plane set | `found`, K=1 |
| `solve_fresh_alternatives_three` | 6 planes, `alternatives=3` | `found`, K=3, mutual diversity |
| `solve_pinned_one_plane` | 6 planes, 1 pinned | `found`, pinned unchanged |
| `solve_repair_minimal_edit` | 6 planes, 5 pinned to baseline | `found`, only the unpinned plane is in a different position vs. baseline |
| `solve_infeasible_pins_clash` | 2 pins overlapping | `trivially_infeasible`, diagnostic names clashing pair |
| `solve_infeasible_too_big` | 9 planes in placeholder hangar | `trivially_infeasible` via Σ areas |
| `solve_force_carts_lock` | Cessna 140 forced `on_carts: true` | `found`, returned layout respects lock |
| `solve_force_carts_conflict` | `always_cart` plane forced `on_carts: false` | `LoaderError` at scenario load |
| `solve_diversity_impossible_warn` | 5 of 6 pinned, `alternatives=3` | `found_partial` K=1, warning emitted (capture via `caplog`) |
| `solve_maintenance_bay_required` | Maintenance plane set, no pin | `found`, maintenance plane centroid in bay strip |
| `solve_all_nine_large_hangar` | All 9 + `test_hangar_large.yaml` | `found`, valid |

12 fixtures. New regressions drop in as fixtures #13, #14, … exactly like Phase 1.

### 6.6 What is NOT in v1 tests

Explicitly out of scope: performance regression tests (CI noise too high), stochastic robustness (run-with-`seed=None` × N), multi-process / threading, visual PNG regression, Hypothesis-style generated scenarios. All deferred to future work as concrete needs surface.

---

## 7. Phase 1 changes required

**Module touched:** `src/hangarfit/models.py`, `src/hangarfit/collisions.py`.

### 7.1 `CheckResult` extension

Add one field, default 0.0:

```python
@dataclass(frozen=True, slots=True)
class CheckResult:
    conflicts: tuple[Conflict, ...] = ()
    total_penetration_m2: float = 0.0

    @property
    def valid(self) -> bool:
        return len(self.conflicts) == 0
```

`valid` continues to be derived from `conflicts`. `total_penetration_m2` is informational only — the existing validity contract is unchanged.

### 7.2 `collisions.check()` populates penetration

For each conflict with two planes (`len(c.planes) == 2`), compute the overlap area between the two specific conflicting `Part` polygons via shapely `polygon.intersection(other).area` and add it to the running sum. Conflicts with a single plane (`len(c.planes) == 1` — currently `maintenance_position`, `maintenance_no_fuselage`, `hangar_bounds`) contribute 0 to the sum: they describe "a plane in the wrong place" rather than "two planes overlapping" and have no second polygon to intersect against. The total is stored in `CheckResult.total_penetration_m2`.

The implementation must remember **which `Part` polygons** were the conflicting pair for each pairwise conflict, since `Conflict.planes` only records the *aircraft* IDs, not which specific parts overlapped. Easiest path: during the pairwise sweep in `check()`, when a conflict fires, the two `Part` polygons are already in scope — accumulate `intersection.area` at the same site, before constructing the `Conflict`.

### 7.3 JSON schema backward compatibility

`hangarfit.check/v1` schema currently emits `{ "valid": bool, "conflicts": [...] }`. The new field is *not* emitted by default (zero changes to existing JSON output); a follow-up v1.1 of the schema could surface it if needed. New solver JSON schema (`hangarfit.solve/v1`) uses the field internally for scoring but doesn't expose it in user output (would only confuse).

### 7.4 Performance cost

Adds `O(|conflicts|)` shapely intersection-area calls per `check()`. Shapely is already in the dependency set and intersection-area is fast (microseconds for the polygon sizes here). No measurable cost expected for the typical 0–10 conflicts per call.

### 7.5 Test impact

Existing Phase 1 `check()` fixture tests that assert exact `CheckResult` equality will still pass because the default `total_penetration_m2=0.0` matches their `CheckResult()` constructors. Tests that construct `CheckResult` manually (golden tests in `test_collisions.py`) may need to add the new field — backward-compatible default minimizes churn.

---

## 8. Open questions and dependencies

| Item | Status | Impact |
|---|---|---|
| Real Herrenteich hangar dimensions (issue #79) | Open | Solver outputs are illustrative until this lands — but implementation can proceed against placeholders. |
| Real aircraft measurements (every `measured: false` in fleet.yaml) | Open | Same as above. |
| `SearchConfig` hyperparameter defaults (N=8, K_stall=50, sigmas) | Guess | Tune empirically once we have a few real scenarios solved. v1 ships the guesses; v1.1 may revise. |
| Budget default (30 s) | Guess | Same. |
| `DiversityConfig` defaults (M=2, 0.5 m, 30°) | Guess | Operator feedback once solver is in use. |

---

## 9. Out of scope (v1.1+ candidates)

- **Structured min-conflicts moves.** Use the conflict's penetration axis as a direction for perturbations. Requires extending `Conflict` to carry an axis vector. Higher-quality moves; better convergence on hard scenarios.
- **Soft constraints / preferences.** Quality metrics like "minimize depth of plane X" (for early-rollout-tomorrow scenarios). Layered on top of `(count, penetration)` scoring as a tertiary key.
- **Region constraints.** Per-plane rectangle "must be inside this box."
- **Heading-only locks** (without position pin).
- **Adjacency / non-adjacency constraints.**
- **CLI flags for `SearchConfig` hyperparameters.**
- **Hypothesis-style property tests** with a `Scenario` generator.

These all sit on top of v1 without requiring v1 changes — additive.

---

## 10. Implementation sequencing (to be detailed by `superpowers:writing-plans` skill)

Anticipated rough ordering — the writing-plans skill will produce the actual checklist:

1. Phase 1 surgery — extend `CheckResult` + `collisions.check()` for penetration depth (§7).
2. New types in `models.py` — `PlaneConstraint`, `Scenario`, `SolveStatus`, `SolverDiagnostics`, `SolveResult`, `DiversityConfig`, `SearchConfig`.
3. `loader.load_scenario` + YAML schema tests.
4. `solver.py` skeleton: `solve()` signature, pre-search infeasibility checks (§4.1), short-circuit returns.
5. Initial placement (§4.2) + cart-assignment round-robin.
6. Descent step + scoring (§4.3, §4.4).
7. Restart trigger + diversity filter (§4.5, §4.6).
8. Termination + diagnostics population (§4.7).
9. CLI subcommand `hangarfit solve` (§5).
10. v1 fixture matrix (§6.5).
11. Determinism canary fixtures (§6.3).
