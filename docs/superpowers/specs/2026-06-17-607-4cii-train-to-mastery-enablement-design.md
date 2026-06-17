# 2026-06-17 — #607 sub-project 4c-ii (#693): train-to-mastery **enablement + knobs**

**Status:** design (brainstormed; agent-team-deliberated). Successor to the 4c-i eval
benchmark (PR #695, merged). Epic [#607](https://github.com/DocGerd/hangarfit/issues/607),
issue [#693](https://github.com/DocGerd/hangarfit/issues/693). Also `Closes #694`.

## 1. Context

The cold-joint RL learned backend is fully built through 4c-i: env → tensorizer → policy →
PPO core (#685) → curriculum ladder (#689) → reach-not-beat benchmark (#695). The benchmark
already records the RR-MC baseline — the 3 dense Herrenteich anchors are *missed*, the demo
control is *reached* — but **the policy's own column on the anchors is empty**:
`benchmark.build_scenario_env()` raises `NotImplementedError` on any scenario containing a
`fixed_obstacle`, because the env cannot pre-place immovable keep-outs (fuel trailer / glider
trailers / Caddy static keeps).

Training *learns* on the trivial rung (4a) but collapses into a **place-nothing / place-invalid
local optimum** on the harder rungs: with `w_col = 100` hard-overlap penalty and no explicit
place-nothing cost, the reward-greedy policy wanders until the step budget expires and parks
nothing (or parks invalidly); `valid_placed` caps (4b). There is no entropy schedule, no
exploration tooling, and the soft reward terms are zeroed.

Issue #693 is deliberately two-natured: a clean **engineering** deliverable (fixed-obstacle
support) plus open-ended **RL research** (the real run to mastery, statistical reach-rate).
This spec covers the agreed slice — **"enablement + knobs"**: ship the env enablement and the
exploration/reward knobs as **default-neutral** capabilities, validate they move the metric on
the easy rungs with short in-session CPU runs, and **defer** the multi-hour real run and the
statistical reach-rate methodology to a follow-up.

## 2. Goals / Non-goals

### Goals
1. **Env fixed-obstacle support** — pre-place immovable keep-outs into a new `_fixed` list,
   unioned into `_layout()` but kept **out of `_parked`** (so `terminal_fraction` is not
   corrupted). Replace `build_scenario_env`'s `NotImplementedError` with pre-placement, so the
   benchmark's policy column populates on the real Herrenteich anchors.
2. **Four default-neutral basin-escape knobs** (the agent-team recommendation):
   `r_valid_park`, `dense_slot_potential`, an **entropy-coefficient anneal** (per-rung
   re-warmed), and **std-only return normalization**.
3. **Fix #694** by *unifying* validity: hoist one shared `layout_valid(layout)` oracle helper
   (= product `collisions.check` reports no conflicts **and** not egress-blocked) and route the
   env gate, the `r_valid_park` bonus gate, and the benchmark through it. Separately gate the
   reward's graded intrusion term on the same ADR-0006 bay rule so the gradient stops
   over-penalizing the inert bay.
4. **Validate** the knobs on rungs 1–3 with short fixed-seed CPU A/B runs; document the result
   in the PR body.

### Non-goals (deferred to the #693 "real-run" follow-up)
- The **multi-hour real training run to mastery** and `PromotionPolicy` θ/window/`max_iters`
  tuning on the dense rungs.
- **Statistical reach-rate methodology** (larger *sampled* scenario sets; 4c-i is an
  existence-demonstration only). Multi-alternative RR-MC routing (the `rrmc_reach`
  `alternatives=1` note) stays deferred.
- **Vectorized envs** for throughput.
- The **backward/start-state curriculum** (pre-park k objects from a witness) — the team's
  *runner-up*; it brushes apron-realism (its k>0 episodes contain poses not driven in that
  episode) and carries a `terminal_fraction` denominator-coupling hazard. It is the first thing
  to graft in the real-run PR **if** the pure-reward set escapes place-nothing but not the
  dense anchors.

### Explicitly rejected (do not implement)
- **`seed_anchor` in any form** — both the solver/witness *source* (violates
  solver-independence) and the mid-hangar *teleport* (violates apron-entry / routability-by-
  construction) are dropped. `DifficultyConfig.seed_anchor` stays the unused `False` placeholder.
- The **`w_col` anneal / saturating-penalty / asymmetric reward-clip** levers (policy-non-
  invariant; reward-hacking surfaces).
- A **Skip action** / action-space growth (invasive `ACTION_DIM`/`SCHEMA_VERSION` change with
  determinism-canary risk, for benefit the chosen set already covers).

## 3. The two hard constraints (every design choice honors both)

- **(A) Apron-entry realism / routability-by-construction.** The physical apron (ADR-0021) stays
  the entry for everything the policy learns to place. No teleport, no mid-hangar spawn. Every
  recommended lever is paid *only in the Park branch* (after the object was driven in) or is a
  pure transform of the scalar reward / sampling temperature — structurally incapable of
  injecting a start state. `reset()`/`_spawn()` are **untouched**.
- **(B) Solver-independence.** No anchor, witness, label, or start-state is ever sourced from
  the deterministic search. The only deterministic-geometry code touched is the permitted reward
  **oracle** (`collisions.check` / parts transform / motion primitives / egress) and the final
  gate. `solve()` is never invoked in the training loop. The `dense_slot_potential` free-space
  query MUST be a pure `state → scalar` shapely query — **never** a placement search or nester
  (that would re-import a search's reachable-distribution bias).

## 4. Design — component by component

All new config fields default to a **neutral** value that reproduces today's training
**byte-identically** (the 4a/4b determinism canary must still pass at all-defaults).

### 4.1 `ml/types.py` — `RewardWeights`
Add:
- `r_valid_park: float = 0.0` — bounded per-valid-park bonus. Recommended train value `2.0`
  (≪ `w_col = 100` and ≪ `r_terminal = 50`).
- `dense_slot_potential: bool = False` — toggles the in-hangar shaping term.

The existing ordering invariant (any hard term dominates the soft sum) is extended: a test
asserts `r_valid_park` is paid **only** when the layout is valid, and `r_valid_park < w_col ×
(smallest meaningful overlap)` so a marginally-overlapping park can never be net-profitable.

### 4.2 `ml/reward.py`
- `RewardContext` gains `park_valid: bool = False` (whether *this* park left the whole layout
  valid — meaningful only on Park steps) and the potential already arrives precomputed via
  `ctx.potential`.
- `potential(...)` gains `active_misfit_m2: float = 0.0`, added to the summed cost:
  `Φ = −(remaining_overlap_m2 + active_dist_to_slot_m + unplaced + active_misfit_m2)`.
  Default `0.0` ⇒ byte-identical. It enters reward **only** as `γΦ′ − Φ`, so it stays
  Ng–Harada–Russell **policy-invariant** (the optimum is unchanged).
- `step_reward(...)` adds `bonus = w.r_valid_park if ctx.park_valid else 0.0`. With
  `r_valid_park = 0.0` and `park_valid = False` defaults, the scalar is bit-identical to today.

### 4.3 `ml/geometry_oracle.py`
- **New `layout_valid(layout: Layout) -> bool`** = `not check(layout).conflicts and not
  egress_blocked(layout)`. This is the single shared product-checker validity helper. Hoisted
  from `benchmark._layout_valid` (which becomes a thin re-export / call-through), and called by
  the env gate and the `r_valid_park` gate too — so the bonus gate and the promotion metric can
  **never disagree** on "valid". Fixes #694 for the gate by construction (the conditional bay
  rule lives inside `check`).
- **`intrusion_area_m2(body, placement, hangar, *, bay_closed: bool = False)`** — the bay term
  (`poly.intersection(bay_poly).area`) is added **only when `bay_closed`**, mirroring
  collisions.py `_bay_intrusion_conflicts` (ADR-0006: bay is a keep-out only when
  `layout.maintenance_plane is not None`). The out-of-floor (walls/notch via `floor_polygon`)
  term is unchanged. The env's layouts never set a maintenance occupant, so the env always
  passes `bay_closed=False` → the bay over-penalty in the **reward gradient** disappears too
  (not just the validity gate). This is the #694 fix on the reward side.
- **New `active_misfit_m2(body, pose, parked_layout, hangar) -> float`** (for
  `dense_slot_potential`) — a pure geometry query: the active body's footprint overlap against
  parked bodies + its out-of-floor area for the in-bounds region (apron `y < 0` **excluded**,
  since the object legitimately starts there and the door-ingress term handles entry). `0.0`
  when the active pose sits in a clean pocket; grows monotonically as it intrudes. **No search,
  no nester, no `solve()`** — a no-import / call-count guard test enforces this.

### 4.4 `ml/env.py`
- **Constructor:** add `fixed_placements: tuple[Placement, ...] = ()`. Store `self._fixed =
  list(fixed_placements)`. The fixed objects' `GroundObject` defs are passed in `ground_objects`
  (so `_body` resolves them); their ids are **not** in `requested_ids`/the queue.
- **`_layout()`:** build the scene from `self._parked + self._fixed` (partitioning each placement
  by fleet-vs-ground_objects membership as today). Overlap / egress / motion-clearance now see
  the fixed keep-outs.
- **`_layout_valid()`:** delegate to `go.layout_valid(self._layout())`. (Removes the hand-rolled
  overlap+intrusion+egress; #694 gone.) Fixed obstacles need no bounds re-check — they are given;
  a placed body colliding with one is caught by `check`'s pairwise overlap.
- **Park branch:** compute `park_valid = go.layout_valid(placed_layout)` and pass it into
  `RewardContext`; the graded intrusion term uses `go.intrusion_area_m2(body, pl, hangar,
  bay_closed=False)`.
- **`_potential()`:** when `self.weights.dense_slot_potential`, compute `active_misfit_m2` for
  the active object at its current pose and pass it to `potential(...)`; else `0.0` (unchanged).
- **`terminal_fraction`** = `len(self._parked) / len(self.requested_ids)`. Because `requested_ids`
  excludes fixed obstacles and `_fixed` is never appended to `_parked`, the denominator is
  uncorrupted (the issue's explicit warning).

### 4.5 `ml/ppo.py` — `PPOConfig` + helpers
Add fields (all neutral by default):
- `entropy_coef_start: float | None = None`, `entropy_coef_end: float | None = None`,
  `entropy_anneal_iters: int = 0`. `None`/`0` ⇒ use the fixed `entropy_coef = 0.01`, no schedule.
- `normalize_returns: bool = False`, `return_norm_eps: float = 1e-8`.

Add a **pure** schedule fn `entropy_coef_at(iteration, *, base, start, end, anneal_iters) -> float`
(linear high→low; constant `base` when `start is None`/`start == end`/`anneal_iters == 0`;
clamped at `end` past the window; monotone non-increasing in between).

Add a **std-only** return normalizer (a small running-std accumulator; **no mean-subtraction**,
cleanrl convention) applied to the reward stream **before** `compute_gae` when
`normalize_returns`. **Warmup-to-identity** until N samples; floor `sqrt(var)` by
`return_norm_eps`. std-only scaling preserves the relative ordering of shaped rewards
(bounding the PBRS-transient concern). `normalize_returns=False` ⇒ reward stream untouched ⇒
byte-identical.

### 4.6 `ml/train.py`
- Thread `RewardWeights` and `PPOConfig` through env/stage construction (today the env uses
  default `RewardWeights`; the stage builder must accept and forward the weights).
- **Entropy schedule wiring (per-rung re-warm):** each curriculum stage has its own iteration
  counter; before each `ppo_update`, set the iteration's coefficient via `entropy_coef_at(...)`
  keyed on the **per-stage** iteration (so a schedule tuned for rung 1 does not reach ~0 entropy
  by rung 3 and re-collapse exploration). If `PPOConfig` is frozen, apply via
  `dataclasses.replace`.
- **CLI flags** (all default to the neutral config): `--r-valid-park`, `--dense-slot-potential`,
  `--entropy-start`, `--entropy-end`, `--entropy-anneal-iters`, `--normalize-returns`.

### 4.7 `ml/benchmark.py`
- `build_scenario_env`: **replace** the `NotImplementedError` with pre-placement — partition the
  scenario's ground objects by `object_class`, pass `fixed_obstacle` placements (with their poses
  from the scenario's fixed-obstacle placement entries) as `fixed_placements` + their defs in
  `ground_objects`, and keep movers in the queue as today. The herrenteich anchors then build a
  rollable env and the policy column populates.
- `_layout_valid` becomes a call-through to `go.layout_valid` (single source of truth).

## 5. Determinism, the default-neutral contract, and the #694 behavior change

Two distinct claims — keep them separate:

- **The four knobs are default-neutral.** All seven new config fields default to neutral; with
  the knobs off, the reward scalar and PPO update are bit-identical to the post-#694 code.
- **The #694 fix is a deliberate bugfix, NOT neutral.** Gating the bay term changes the
  reward gradient *and* the validity verdict on any layout that clips the (inert) maintenance bay
  — by design, that is the bug being fixed. So training at all-defaults differs from the
  *pre-#694* code on bay-clipping layouts. This is correct (it aligns the env with
  `collisions.check`), but it means **any committed golden training/reward fixture from 4a/4b that
  involves a bay-clipping layout must be re-baselined** in this PR, with the diff called out as
  the #694 correction (verify which fixtures are affected at implementation start).
- **The determinism canary still holds.** The 4a/4b canary asserts *run-to-run* byte-identity
  (same seed → same output within a build), not identity to a previous commit — so it passes
  unchanged. A test asserts the four knobs at defaults leave the reward/`CurriculumHistory`
  bit-identical *relative to the post-#694 baseline*.
- The learned path is **not** under ADR-0003 / `determinism-guard` (epic contract). These changes
  do not touch `src/hangarfit/solver.py` or `towplanner.py`; there is **no `src/` change at all**
  — the #694 fix lives entirely in `ml/geometry_oracle.py` (the env-side bay gate), and the
  product `collisions.check` is consumed read-only.

## 6. Testing

Torch-free where possible (the `[train]` extra / torch CI lane is epic rung #6; ppo tests use
`importorskip`).
- **reward:** `r_valid_park=0.0` ⇒ bit-identical; bonus paid **iff** `park_valid`; ordering
  invariant (`r_valid_park < w_col × smallest_meaningful_overlap`); `potential` byte-identical at
  `active_misfit_m2=0.0`; telescoping check that Φ stays a pure function of state.
- **geometry_oracle:** `intrusion_area_m2` bay term gated (`bay_closed=False` drops it, `True`
  counts it); **#694 regression** — a layout clipping the *inert* bay is `layout_valid` (matches
  `collisions.check`); `active_misfit_m2` is `0` in a free pocket, monotone in intrusion, and
  **never calls `solve()`/a nester** (import/call-count guard).
- **env:** fixed obstacle unioned into `_layout()`, **not** in `_parked`; `terminal_fraction`
  uncorrupted with a fixed obstacle present; a placed body overlapping a fixed obstacle is caught;
  #694 regression at env level.
- **ppo:** `entropy_coef_at` boundaries / monotonicity / constant-when-off; `normalize_returns`
  identity when off and during warmup; **std-only** (no mean subtracted); constant-σ
  scalar-equivalence (gradient direction preserved up to a positive scalar); `return_norm_eps`
  floor (finite at `var=0`).
- **benchmark:** `build_scenario_env` now **accepts** the fixed-obstacle anchors (replaces the
  old `NotImplementedError` test); `eval.policy_reach` populates the anchor policy column.
- **train_curriculum:** entropy per-rung re-warm; all-defaults byte-identical.

## 7. Validation (in-session, documented in the PR body — not CI)

Two short fixed-seed CPU runs on rungs 1–3 of `DEFAULT_LADDER` (trivial / pair-box / trio-box —
loose hangars, small budgets), via `python -m ml.train --schedule curriculum
--max-iters-per-stage 30`:
- **CONTROL** = all knobs at defaults.
- **TREATMENT** = `r_valid_park=2.0`, `dense_slot_potential=True`,
  `entropy_start=0.05 / entropy_end=0.005 / entropy_anneal_iters≈40`, `normalize_returns=True`.

**Primary signal** (the exact 4b mastery metric that capped under the basin): mean `valid_placed`
climbs and holds in treatment where control stalls near 0 — a clean win is rung-1 `valid_placed`
reaching the 0.9 promotion threshold with `history.promotions` registering `by="competency"`
within the 30-iter cap. **Leading indicators:** (1) `terminal_fraction` rises off ~0 (escapes
place-nothing); (2) the `fraction_placed − valid_placed` gap shrinks (escapes place-invalid);
(3) `hard_overlap` term mean trends to 0, **not** up (the bonus is not buying invalid parks —
if `valid_placed` rises but `fraction_placed ≫ valid_placed` persists, the gate is leaking:
**fail the knob, do not ship**); (4) entropy starts higher and decays (schedule wired); (5)
value-loss flatter with RetNorm on. Expect minutes per rung; the multi-hour run is deferred.

## 8. Implementation order (subagent-friendly chunks)

1. **#694 unification** — `go.layout_valid` + `intrusion_area_m2` bay gate; env `_layout_valid`
   delegates; benchmark `_layout_valid` call-through; #694 regression tests. (`Closes #694`.)
2. **Fixed-obstacle env support** — `_fixed`, `_layout()` union, `build_scenario_env`
   pre-placement; env + benchmark tests; verify the anchor policy column rolls out.
3. **Reward knobs** — `r_valid_park` (+ `park_valid` plumbing) and `dense_slot_potential`
   (+ `active_misfit_m2`); reward + oracle tests.
4. **PPO/optimizer knobs** — entropy schedule fn + per-rung wiring; std-only return normalizer;
   ppo + train tests; CLI flags.
5. **Validation run + docs** — A/B run, CHANGELOG `[Unreleased]` entry, `ml/README` knob docs,
   PR body with the validation result.

**Verify-first at implementation start:** (a) the exact field name on the `load_scenario` result
that carries fixed-obstacle placement poses (the benchmark comment references
`fixed_obstacle_placements`); (b) that `collisions.check`'s `hangar_bounds` conflict honors the
L-shaped `floor_polygon` (notch) so delegating the env gate to `check` does not lose notch
enforcement; (c) whether `PPOConfig` is frozen (drives `replace` vs in-place for the per-iter
entropy coef).

## 9. References
- Epic [#607](https://github.com/DocGerd/hangarfit/issues/607); issue
  [#693](https://github.com/DocGerd/hangarfit/issues/693); bug
  [#694](https://github.com/DocGerd/hangarfit/issues/694).
- Cold-joint env design: `docs/superpowers/specs/2026-06-12-learned-backend-cold-joint-rl-env-design.md`.
- 4c-i eval benchmark: `docs/superpowers/specs/2026-06-17-learned-backend-eval-benchmark-design.md` (PR #695).
- ADR-0003 (determinism scope), ADR-0006 (bay-intrusion rule), ADR-0008 (spread/region soft term),
  ADR-0010 (motion primitives), ADR-0021 (staging apron), ADR-0025 (ground-object taxonomy),
  ADR-0026 (Caddy hard-door egress).
- Agent-team deliberation (this session): 4 approaches × adversarial critique → synthesis;
  dropped seed_anchor; runner-up = backward curriculum (deferred).
