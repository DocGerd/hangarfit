# Learned backend — reach-not-beat eval benchmark machinery (sub-project #4c-i)

**Issue:** #690 (Design: eval / reach-not-beat benchmark + train-to-mastery, learned-backend sub-project #4c). Part of epic #607.
**Date:** 2026-06-17
**Status:** Design (pending spec review)

---

## 1. Scope

Sub-project **#4c-i** of the cold-joint learned-backend epic (#607). #690 bundles two
independent deliverables; per the 2026-06-17 brainstorm they are **sliced**:

- **#4c-i (THIS spec): the reach-not-beat evaluation benchmark MACHINERY** — a clean,
  deterministic, mergeable PR. The scenario set, the success predicate, the RR-MC baseline
  reach-oracle, the policy-rollout scorer, and the side-by-side both-rates report.
- **#4c-ii (separate, later): train-to-mastery** — escaping the place-nothing local
  optimum (reward / exploration / `seed_anchor` warmup tuning), the actual long training
  run, larger sampled scenario sets, and the *headline policy reach numbers*. Open-ended /
  experimental; **not** in 4c-i.

**Decomposition reminder:** SP#4 ("training + evaluation harness") was split into
**4a training core (#685, merged)** → **4b curriculum schedule (#689, merged)** → **4c
eval + train-to-mastery**; 4c is itself now split into **4c-i machinery (this doc)** →
**4c-ii mastery**.

**Out of scope (explicit):**
- The place-nothing reward/exploration fixes + the real training run + statistical
  reach-*rates* → **4c-ii**.
- **Env support for pre-placed FIXED obstacles** (the fuel-trailer keep-out) — needed to
  *roll out the policy* on the herrenteich anchors, and needed for *training* on ground-object
  scenarios — → **4c-ii**. 4c-i leaves `ml/env.py` untouched (§5.5, D11) and `build_scenario_env`
  loudly refuses fixed-obstacle scenarios. The anchors' witness + RR-MC columns are fully
  populated in 4c-i; only their *policy* column defers.
- ONNX export, `solve --backend learned` wiring, cross-machine inference determinism → **#5**.
- `[train]`-extra torch CI lane + packaging + signed weights → **#6**.

---

## 2. Acceptance — an existence-DEMONSTRATION, not a rate

Acceptance for 4c-i is **"the machinery exists, is deterministic, and is exercised on real
data,"** framed honestly as an existence-demonstration:

> *"The learned policy reaches ≥1 valid+routable layout where the recorded RR-MC→tow
> pipeline reaches 0, at a pre-registered budget."*

A small curated set **cannot** carry a statistically significant reach-rate; we do not claim
one. Population-level rates over a larger sampled set are **4c-ii**. 4c-i ships the
*apparatus* + a *demonstrated* both-rates table; the live policy numbers are an
offline/manual artifact (no torch CI lane until #6).

---

## 3. Why this shape (the deliberated decision)

A four-lens panel (benchmark rigor, codebase-fit, CI/determinism, maintainability)
**unanimously** chose a **hybrid** over the three candidate framings, and the user adopted
it as-is (2026-06-17).

`"RR-MC missed"` is only a sound, non-circular, reproducible claim if you can show a skeptic
**both halves**:

1. **A valid+routable layout the deterministic checker accepts** — proof the arrangement is
   *reachable* (kills the "maybe it's just infeasible" confound). Supplied by a committed
   **witness layout** per anchor scenario.
2. **The real RR-MC→tow pipeline scoring 0 reach at a budget fixed before you look** —
   measured by the *actual* `solve`+`plan_fill` pipeline, pre-registered and recorded.

- **Option A (pure curated existence-proof set)** proves reachability but is statistically
  thin and mildly circular if curated *as* "where RR-MC fails."
- **Option B (pure shared set, report both rates)** gives the clean non-pre-filtered
  comparison but cannot distinguish *"RR-MC missed"* from *"scenario infeasible"* without a
  reachability witness.
- **Option C (parametric density sweep)** selects scenarios *on the comparator* (circular),
  needs a clearance-knob generator the `Regime` dataclass lacks, and drives the pipeline into
  the slow near-infeasible regime (>200 s timeouts). **Rejected by all four lenses.**

The **hybrid = A-anchored, B-reported**: a small *frozen* set of real curated scenarios,
each with a committed witness layout the checker accepts, scored with side-by-side
both-rates reporting, reusing the existing `bench/harness` pipeline as the RR-MC reach-oracle,
with the expensive RR-MC baseline **recorded offline as committed data**.

### Decisions table

| # | Decision | Choice | Why |
|---|---|---|---|
| D1 | Scenario set | **Hybrid: A-anchored, B-reported** | Only framing where "RR-MC missed" is non-circular *and* non-vacuous; reuses in-tree layouts + `bench/harness`. |
| D2 | Reachability proof | **Committed witness layout + checker-pass test** | Proves the arrangement exists; the checker-pass **never rots** (unlike an "RR-MC fails" assertion). |
| D3 | RR-MC baseline | **Recorded OFFLINE, committed as a SHA-stamped fixture** | RR-MC route timed >200 s on herrenteich-class fills; keeps CI cheap + wall-clock-free. Re-measurable, flip-detectable. |
| D4 | RR-MC budget | **Pinned `(max_restarts, tow_max_expansions, seed)` + `budget_s=inf`** | The default 30 s wall-clock would make "missed" machine-dependent → non-reproducible. |
| D5 | "Missed" claim | **NOT a hard CI gate**; assert only witness validity. Optional `@slow` NON-required drift canary WARNs on a flip | A hard `RR-MC reach==0` gate celebrates-then-breaks when the solver improves; a baseline flip is a *win*, surfaced not blocked. |
| D6 | Success gate (the "reached it" test) | **valid + routable-by-construction** | Final layout passes the deterministic checker **and** every drive-in leg had a clear swept path. |
| D7 | Policy eval action selection | **argmax (`act(deterministic=True)` in `eval()` mode)** | No RNG → reproducible reach verdicts. |
| D8 | Checkpoint | **Minimal torch `state_dict` `.pt`** (`train.py --save`, `eval.py --checkpoint`) | Enough for the offline run + #5; ONNX is #5. |
| D9 | Controls | **1–2 easy scenarios where RR-MC DOES route** | Shows the baseline isn't strawmanned and the policy isn't trading one failure mode for another. |
| D10 | Train/eval disjointness | **Eval anchors held OUT of the 4c-ii curriculum sampling pool** | Prevents train-on-test. (`DEFAULT_LADDER` already separates box rungs from herrenteich rungs.) |
| D11 | Fixed-obstacle scenarios in 4c-i | **`build_scenario_env` LOUDLY REFUSES** (raises) a scenario with `fixed_obstacle_placements`; env-side pre-placement deferred to 4c-ii | Pre-seeding fixed obstacles correctly needs a separate `_fixed` list in the env (a naive `_parked` seed corrupts `terminal_fraction = len(_parked)/total`). Keeping `env.py` untouched makes 4c-i a clean machinery PR; refusing (not silently dropping) avoids scoring the policy on an easier scenario than RR-MC faces. The policy column for those anchors fills in 4c-ii. |

---

## 4. The success predicate (the crux)

A scenario is **"reached"** by an agent iff, over a single deterministic rollout:

```
reached  ==  parked_all  AND  final_layout_valid  AND  max_swept_intrusion_over_episode == 0
```

- `parked_all` — every requested object was committed (`Park`ed), not left unplaced.
- `final_layout_valid` — the terminal layout passes the **product deterministic checker**
  (the prime-directive final gate, == `hangarfit check`): `not collisions.check(layout).conflicts`
  (overlap + hangar bounds/notch + **conditional** maintenance bay + ground-obstacle keep-outs)
  **+** no Caddy hard-door egress violation (ADR-0026). **Implementation note (resolved during
  build):** this uses `collisions.check`, **not** the env's `_layout_valid`/`valid_placed` (the
  policy *training* gate), because the env oracle's `intrusion_area_m2` over-strictly enforces an
  **inert placeholder** maintenance bay — it would wrongly reject `layout_full` (the herrenteich
  bay is explicitly inert). The benchmark therefore judges witnesses, RR-MC, and the policy all
  by `collisions.check` (apples-to-apples + matching what RR-MC's solver enforces); the env-oracle
  divergence is tracked as **#694** (a 4c-ii env fix).
- `max_swept_intrusion_over_episode == 0` — **the routable-by-construction gate.** The env
  *penalizes* swept intrusion (`geometry_oracle.swept_intrusion_m2`) but does **not**
  hard-stop the move, so a policy could reach a valid *final* layout via a path that clips a
  parked obstacle. That must **not** count as routable. We therefore accumulate the per-leg
  swept intrusion over the episode and require it to be exactly 0.

The RR-MC side is scored by the **same geometry** (`bench/harness` → `solve` + `plan_fill`,
which `ml/geometry_oracle` re-exports), so the head-to-head is apples-to-apples; the policy's
only structural edge is choosing the pose *sequence* jointly rather than routing to RR-MC's
fixed poses by monotonic drive-in — which **is** the #607 thesis (memory #606/#634: k=4
fails all orderings for drive-in to fixed poses), not a scoring bias.

---

## 5. Architecture & components (all new, under `ml/`)

### 5.1 `ml/benchmark.py` — torch-free core

Stays import-light (no torch; may import `hangarfit.*`, `bench.*`, `ml.env`, `ml.geometry_oracle`)
so it loads in the no-torch CI lane.

- `BenchScenario` (frozen dataclass): `name: str`, `scenario_path: str` (repo-relative
  solver-input YAML), `witness_path: str | None` (repo-relative witness layout; `None` only
  permitted for controls that RR-MC routes), `kind: Literal["anchor", "control"]`,
  `max_restarts: int`, `tow_max_expansions: int`, `seed: int`. `__post_init__` validates the
  scalar invariants (positive budgets; an `anchor` requires a `witness_path`).
- `BENCH_SET: tuple[BenchScenario, ...]` — the frozen curated set (§6).
- `build_scenario_env(scenario) -> HangarFitEnv` — load the scenario input + hangar, set
  `requested_ids` = `scenario.placeable_ids` (the aircraft `fleet_in` + the *sorted*
  `placed_routed_mover` ids — `models.py`; pick this over `mover_ids` so the drive-in order
  is deterministic and not declaration-order-dependent), and build a `HangarFitEnv` with an
  apron depth for drive-in. (Reuses `stage_builder`-style loading; clearances come from the
  scenario's hangar file.) **Raises `NotImplementedError`** with a 4c-ii pointer if the
  scenario carries any `fixed_obstacle_placements` — the env cannot yet pre-place an immovable
  keep-out, and silently dropping it would score the policy on an easier scenario than RR-MC
  faces (D11, §5.5).
- `witness_valid(scenario) -> bool` — load the witness layout, return True iff
  `overlap_area_m2 == 0` **and** every parked body's `intrusion_area_m2 == 0` **and not**
  `egress_blocked` (reuses `ml.geometry_oracle`). The never-rots reachability proof.
- `score_episode(env, actions: Sequence[Action]) -> ReachVerdict` — **torch-free**: replay an
  explicit action sequence through `env.step`, accumulating `max_swept_intrusion` as the
  running **max** of each step's `StepInfo.terms["hard_swept"]` (the only swept field;
  `StepInfo` has no top-level swept attribute, and a `Park` step forces `hard_swept` to 0.0),
  and `final_valid` from the terminal step's `StepInfo.valid`; then evaluate the success
  predicate (§4). Returns `ReachVerdict(reached, parked, total, final_valid,
  max_swept_intrusion, reason)`.
- `rrmc_reach(scenario) -> RrmcVerdict` — the RR-MC reach-oracle. Loads the scenario via the
  public `hangarfit.loader.load_scenario`, runs `solve(..., budget_s=inf,
  SearchConfig(max_restarts=scenario.max_restarts, ...))`, then `plan_fill(...,
  max_total_expansions=scenario.tow_max_expansions)` (catching `NoFeasiblePlanError`), and
  applies the same `valid + routable` predicate to RR-MC's output. Reuses the **public**
  `hangarfit.solver.solve` / `hangarfit.towplanner.plan_fill` (the same functions
  `bench/harness` drives — they are `hangarfit`'s public surface, not bench's). `bench/harness`
  exposes its bounded + effectively-infinite-budget + `NoFeasiblePlanError`-aware wrapper logic
  only via `_`-prefixed helpers (`_solve_placement`/`_route_layout`); factor that into a small
  **public** helper to share it rather than **importing bench privates**. Returns
  `RrmcVerdict(reached, n_routed, n_total, ...)`.
- Baseline fixture I/O: `load_baseline() -> dict[name, RrmcVerdict-as-record]`,
  `record_baseline()` (writes the committed fixture). `assemble_table(baseline,
  policy_verdicts) -> BenchReport` — the side-by-side both-rates structure.

### 5.2 `ml/eval.py` — torch-gated runner

- `load_policy(checkpoint_path, *, policy_kwargs) -> HangarFitPolicy` — construct +
  `load_state_dict` + `.eval()`.
- `policy_reach(scenario, policy) -> ReachVerdict` — build the env, roll out with
  `policy.act(..., deterministic=True)` (argmax, no RNG), collect the executed actions, feed
  them through `score_episode`. (Decode magnitude bins → `Primitive` via `ml.action_space`,
  exactly as `collect_rollout` does.)
- `run_benchmark(policy) -> BenchReport` — for each scenario assemble `{name, kind,
  rrmc_reached (from fixture), policy_reached (live)}`; print the table.
- `main()` CLI: `python -m ml.eval --checkpoint PATH` prints the both-rates table.

### 5.3 `ml/train.py` — checkpoint save

Add `--save PATH` → `torch.save(policy.state_dict(), path)` after training. Minimal; the
load side lives in `ml/eval.py`. (No behavioural change to existing training paths.)

### 5.4 Data files

- **Witnesses (reuse existing):** `examples/herrenteich/layout.yaml` (all-8),
  `layout_today.yaml` (9 ac + 3 GO, #665), `layout_full.yaml` (fishbone 7+4, #659).
- **Scenario inputs:** `examples/herrenteich/scenario.yaml` (↔ all-8 witness) and
  `scenario_demo.yaml` (the easy control) already exist. **Author** `scenario_today.yaml`
  and `scenario_full.yaml` so each anchor witness has a matching solver-input sibling. A
  torch-free test asserts each scenario's **movable id-set** (`fleet_in` + `placed_routed_mover`
  ids — the Caddy is a placed-routed mover, so it *is* included) **exactly matches** its
  witness layout's movable placements, with **`fixed_obstacle` placements excluded on both
  sides** (a witness layout contains the fuel-trailer keep-out, which is not a solver input).
  This pins the comparison so the test can't spuriously fail on the fixed obstacle, and the
  input + witness can never silently drift.
- **GO-free control input:** the policy-rollout control reuses an existing GO-free box
  fixture (e.g. `tests/fixtures/scenario_minimal.yaml`, verified to carry no
  `fixed_obstacle_placements`) — or a small authored box scenario — so `build_scenario_env`
  accepts it and a real policy can be driven through `score_episode` (§5.5).
- **Baseline fixture:** `tests/fixtures/ml/bench_baseline.json` (committed) — per-scenario
  `{reached: bool, max_restarts, tow_max_expansions, seed, repo_sha, recorded_at}`.

### 5.5 Env constraint: fixed obstacles deferred to 4c-ii (no `env.py` change in 4c-i)

`HangarFitEnv._reset_state` starts with `_parked = []` — nothing is pre-placed. Every
herrenteich anchor, however, includes a **fixed** fuel-trailer keep-out
(`Scenario.fixed_obstacle_placements`, which the solver pre-places as an immovable obstacle).
To roll the policy out on those scenarios the env would need to seed that keep-out as an
obstacle the agent must avoid — and it must do so in a **separate `_fixed` list** unioned only
into `_layout()`, **not** into `_parked` (a naive `_parked` seed would make
`info.placed = len(_parked)` and `terminal_fraction = len(_parked)/len(requested_ids)` count
the obstacle, pushing the fraction past 1.0 and silently corrupting the competency signal).
That is a real, guard-worthy `env.py` change, and GO-env support is needed for *training* on
ground-object scenarios anyway — so it belongs with **4c-ii**, not the machinery PR.

**4c-i therefore leaves `ml/env.py` byte-identical.** `build_scenario_env` refuses
fixed-obstacle scenarios loudly (D11). The policy-rollout scorer is exercised on a **GO-free
control** (a box trivial/pair scenario, or `scenario_demo.yaml` if it carries no fixed
obstacle), which is enough to validate `policy_reach` → `score_episode` end-to-end with a real
policy. The herrenteich anchors keep full **witness + RR-MC** columns; their **policy** column
reads *"deferred → 4c-ii"* until 4c-ii adds the env pre-placement + trains.

---

## 6. The curated set (frozen for 4c-i)

| Scenario | Witness layout | kind | Columns populated in 4c-i | Why |
|---|---|---|---|---|
| `herrenteich_all8` | `layout.yaml` (all-8) | anchor | witness + RR-MC; **policy → 4c-ii** | The canonical dense fill; RR-MC→tow misses it (the #599 wall). Has a fixed fuel trailer → policy rollout deferred (§5.5). |
| `herrenteich_today` | `layout_today.yaml` (9 ac + 3 GO) | anchor | witness + RR-MC; **policy → 4c-ii** | The **real** club layout (#665) — the strongest existence proof; product solver can't generate it. |
| `herrenteich_full` | `layout_full.yaml` (7 ac + 4 GO fishbone) | anchor | witness + RR-MC; **policy → 4c-ii** | Adds the Caddy-egress + dual-trailer dimension. |
| `herrenteich_demo` | n/a (RR-MC routes it) | control | RR-MC + policy *iff* GO-free | RR-MC **does** route it → shows the baseline working, not strawmanned. Used as the policy-rollout control **only if** it carries no fixed obstacle (else a box rung is used). |
| box trivial/pair rung | n/a (RR-MC routes it) | control | RR-MC + policy | GO-free → the **policy-rollout exercise** for 4c-i + the cheap non-slow control for the two-pass coverage rule. |

A `control` has no witness layout: RR-MC reaching it *is* the reachability proof. Only
`anchor`s need a committed witness. The exact `(max_restarts, tow_max_expansions, seed)` per
scenario are pinned as named constants in `ml/benchmark.py` with a comment marking them
**pre-registered** (frozen before measurement). Anchors are marked `@slow`; ≥1 control stays
non-slow.

---

## 7. Data flow

```
BenchScenario
  ├─ witness layout (committed) ─→ witness_valid()       [CI, torch-free]  → proves reachable
  ├─ RR-MC pipeline (OFFLINE)    ─→ committed fixture      → reach_rrmc (DATA, not live in CI)
  └─ policy rollout (argmax)     ─→ score_episode()        [offline / torch] → reach_policy
                                         ↓
                            assemble_table → side-by-side both-rates report
```

---

## 8. Testing

**torch-free (runs in the no-torch CI lane):**
- `witness_valid()` returns True for every `anchor` witness — the never-rots reachability
  proof.
- `score_episode()` on a **trivial** scenario with a hand-authored **winning** action
  sequence (drive in + park clear → `reached == True`) and a **losing** one (park with
  overlap, or a path that clips an obstacle → `reached == False`, exercising each predicate
  clause incl. the swept-intrusion gate).
- scenario↔witness id-set match test for every anchor: input movable id-set
  (`fleet_in` + `placed_routed_mover` ids) == witness movable placements, **fixed-obstacle
  placements excluded on both sides**, so input and witness can't silently drift.
- `build_scenario_env` **raises** on a fixed-obstacle scenario (the D11 loud refusal — proves
  no silent obstacle-drop).
- baseline fixture schema + `load_baseline()` round-trip; `assemble_table` shape.
- **≥1 non-slow** control test (two-pass coverage rule — keep a non-`@slow` path through the
  new code).
- **`@slow`, NON-required drift canary:** re-derive ONE RR-MC verdict via `rrmc_reach` and
  **WARN** (never fail) if it flips vs the committed fixture — surfaces curation rot without
  a flaky required gate. (Heavy RR-MC anchors are `@slow`.)

**torch-gated (`importorskip("torch")`):**
- `policy_reach` smoke on a tiny **untrained** policy over the GO-free control scenario:
  asserts it runs end-to-end and returns a `ReachVerdict` (NOT that it reaches — untrained
  won't). This is the only place a real policy drives `score_episode` in 4c-i.
- checkpoint save (`train.py --save`) / load (`eval.load_policy`) round-trip preserves
  parameters.

---

## 9. Determinism & CI cost

- Eval rollout is argmax → **no RNG**; same policy + scenario → identical verdict.
- RR-MC baseline pinned on `(max_restarts, tow_max_expansions, seed)` + `budget_s=inf`,
  recorded **offline** and committed as data — CI carries **no** slow RR-MC route and **no**
  wall-clock gate (it timed >200 s on herrenteich-class fills; the WSL2 box has documented
  wall-clock-canary flakiness — `docs/dev/test-flakes-and-ci-gotchas.md`).
- The whole machinery PR runs in the **no-torch lane** (`benchmark.py` is torch-free; the
  torch parts are `importorskip`-gated). Live policy reach numbers are an offline/manual
  artifact until the torch CI lane lands (#6) — **called out so reviewers don't expect a
  CI-published policy number yet.**

---

## 10. Risks & mitigations

- **Curation staleness** — a curated "RR-MC misses these" set rots if the solver later
  improves. *Mitigation (D5):* assert only witness validity (never rots); the RR-MC number is
  a timestamped, budget-pinned, SHA-stamped data point, re-measurable; a flip is a celebrated
  win surfaced by the non-required drift canary, never a CI break.
- **Tiny-n / no statistical power** — *Mitigation (§2):* framed honestly as an
  existence-demonstration, not a rate; rates → 4c-ii. Controls included so the baseline is
  shown working.
- **Routability-definition asymmetry** (env per-step swept clearance vs `plan_fill`
  drive-in) — *Mitigation (§4):* the *shared* success predicate is the deterministic checker
  + clear swept paths; the geometry primitives are byte-identical (`geometry_oracle`
  re-exports `towplanner` internals); the structural difference (joint pose choice vs
  drive-in-to-fixed-poses) **is** the hypothesis under test, stated explicitly.
- **Pre-registration discipline** — tuning the budget/set *after* seeing results silently
  re-introduces circularity. *Mitigation (D4):* pin budgets as named constants commented
  "pre-registered"; version the set; forbid post-hoc edits in 4c-i.
- **Overfitting-the-benchmark in 4c-ii** — *Mitigation (D10):* hold the eval anchors out of
  the 4c-ii curriculum sampling pool.
- **Witness authoring correctness** — a witness that doesn't actually pass the checker would
  make the proof vacuous. *Mitigation:* the witnesses are existing in-tree layouts already
  shown to pass `hangarfit check`; the `witness_valid()` CI test re-proves it on every run.
- **Silent obstacle-drop** — building an env for a fixed-obstacle scenario without
  representing the keep-out would score the policy on an *easier* scenario than RR-MC faces
  (an invalid comparison + a silent failure). *Mitigation (D11, §5.5):* `build_scenario_env`
  **raises** on any `fixed_obstacle_placements`; env pre-placement + those anchors' policy
  column land in 4c-ii.
- **bench/harness coupling** — depending on `bench`'s `_`-prefixed internals is fragile.
  *Mitigation (§5.1):* call the **public** `solve`/`plan_fill`; if needed factor a small
  public helper into `bench/harness` rather than importing privates.

---

## 11. Implementation notes (for the plan)

- File a GitHub **impl issue** *"#607 rung 7: eval benchmark machinery (sub-project #4c-i)"*
  (and a sibling tracking issue for **4c-ii train-to-mastery**, so #690 can be retired or
  re-scoped to the umbrella) **before** coding; branch `feature/607-rung7-eval-benchmark` off
  `develop`; TDD; draft PR `Closes #<impl-n>` (`Refs #690`).
- **Review arc:** `code-reviewer` (main pass) + `silent-failure-hunter` (the success
  predicate edge handling, the `NoFeasiblePlanError` path, the baseline-flip drift logic,
  the witness-load failure path) + `type-design-analyzer` (the new `BenchScenario` /
  `ReachVerdict` / `RrmcVerdict` types). **Not** `determinism-guard` / `geometry-invariant-guard`
  — no `solver.py` / `towplanner.py` / `geometry.py` change, and **`ml/env.py` is untouched**
  (the env GO-pre-placement is deferred to 4c-ii, §5.5/D11), so the 4b reset-byte-identical
  guarantee is unaffected.
- **CHANGELOG** `[Unreleased]` entry (user-facing dev surface: `python -m ml.eval` + the
  benchmark + `train.py --save`).
- Record the RR-MC baseline once (offline) and commit the fixture **in the impl PR**; include
  the both-rates table (with whatever policy column is available — likely "untrained → 0"
  until 4c-ii) in the PR body.
- Keep `benchmark.py` torch-free (no `import torch`, no `ml.policy` / `ml.ppo` import) so the
  no-torch CI tests load it cleanly.

## 12. Open questions (resolve in 4c-ii / tuning, not here)

- The exact pre-registered `(max_restarts, tow_max_expansions, seed)` per anchor — chosen
  during the offline baseline-recording run so each anchor's RR-MC verdict is stable, then
  frozen.
- Whether to add more dense herrenteich-subset anchors once the first set is validated —
  candidate for 4c-ii, not committed here.
- The larger *sampled* scenario set + statistical reach-rate methodology → 4c-ii.
