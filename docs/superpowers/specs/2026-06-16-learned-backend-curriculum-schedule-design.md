# Learned backend — Curriculum schedule (sub-project #4b)

**Date:** 2026-06-16
**Epic:** #607 (cold-joint learned backend). **Builds on:** SP#1 env (#672), SP#2 tensorizer (#677), SP#3 policy (#681), **SP#4a training core (#685)** — all merged to `develop`.

**Scope:** Sub-project **#4b** of the learned-backend epic — the **curriculum schedule ONLY**: a deterministic, competency-gated ramp of `DifficultyConfig` (+ hangar shape + clearance) layered over the existing PPO training core, plus the wiring that lets `python -m ml.train` climb a multi-rung ladder instead of training one fixed stage. **No reach-not-beat eval benchmark (→ 4c), no ONNX / `--backend learned` wiring (→ #5), no full-task mastery / final hyper-parameter tuning.**

**Decomposition reminder:** SP#4 ("training + evaluation harness") was split (2026-06-16) into **4a training core (#685, done)** → **4b curriculum schedule (this doc)** → **4c eval / reach-not-beat benchmark**. Each is its own spec → plan → PR.

---

## 1. Goal & the one-sentence pitch

Give the from-scratch PPO agent a **staged difficulty ramp** — start where 4a left off (one object, loose box hangar) and progressively widen to more objects, the real L-shaped notched hangar, and a stricter clearance budget — **advancing each rung only once the agent has demonstrably mastered it**, so the harder regimes become learnable instead of a sparse-reward wall.

**Acceptance is "the agent climbs the ladder," not "the agent solves the real task."** Mastering the full 8-object Herrenteich layout and the reach-not-beat metric are 4c + tuning. 4b ships the *machinery* + a *demonstrated multi-rung climb*.

### Why a curriculum at all
The env's reward (spec §5) is graded-lexicographic with potential-based shaping, which gives a dense per-primitive gradient — but the **terminal objective** (fraction of the set parked validly) gets sparser and the **credit-assignment horizon** gets longer as the object count and geometric tightness grow. A from-scratch agent thrown straight at "park 8 aircraft in an L-shaped hangar at real clearance" sees almost no successful episodes to learn from. The curriculum makes each increment small enough that the agent reaches competence, then carries that competence into the next harder regime (transfer).

---

## 2. What 4b delivers / does NOT deliver

**Delivers**
- A new **pure, torch-free, disk-IO-free** module `ml/curriculum.py`: the `Stage` ladder, the `PromotionPolicy`, the seeded per-episode `sample_request`, and the `should_promote` gate. CI-testable without the `[train]` extra (same tier as `encoding.py` / `action_space.py`).
- A small **backward-compatible env change**: `HangarFitEnv.reset(requested_ids=None)` so an episode can be driven over a freshly-sampled object subset; `None` reproduces 4a byte-for-byte.
- **`train.py` wiring**: `build_stage_env(stage)`, a `collect_rollout` extension that resamples per episode and returns per-episode competency stats, and a `train_curriculum(...)` loop that climbs the ladder.
- A **CLI** surface: `python -m ml.train --schedule {trivial,curriculum}` (default `curriculum`).
- **Tests** (CI no-torch units + a torch-gated within-build canary) and a **manual learn-validation** reward curve reported in the PR.

**Does NOT deliver (explicit non-goals, deferred)**
- The **reach-not-beat** acceptance metric + curated dense RR-MC-missed scenarios → **4c**.
- Full **8-object Herrenteich mastery** and final **PPO / reward-weight tuning sweeps** → 4c + woven tuning.
- **Vectorized / parallel envs** for throughput → a later perf concern (noted, not built).
- **Ground-object rungs** (Caddy hard-door egress, fuel-trailer keep-out as a curriculum dimension) → later. The env *supports* ground objects; the 4b ladder samples **aircraft only** so every rung stays learnable.
- **ONNX export, `solve --backend learned`, cross-machine determinism, packaging** → #5 / #6.

---

## 3. Design decisions (settled in brainstorming 2026-06-16)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| D1 | Rung scope | **Machinery + demonstrated ramp** | Mirrors 4a's minimal, mechanics-first precedent; full-task mastery is 4c. |
| D2 | Promotion | **Competency-gated + max-iters safety cap** | Standard curriculum design; directly demonstrates mastery-then-promote; still deterministic per seed. |
| D3 | Object set | **Seeded per-episode random subset** | Domain randomization on object identity → the §7 "generalize across fleets" goal; one new *seedable* env RNG; reward stays RNG-free. |
| D4 | CLI default | **`--schedule curriculum` default**, `--schedule trivial` preserves 4a's command | The curriculum is now the point; the validated 4a command stays reachable byte-identically. |
| D5 | Ladder | **5 named rungs committed** (§5); scalar values (N, clearance, budgets) tunable during validation | Concrete enough to plan + test; the machinery accepts any ordered `tuple[Stage,...]`. |

---

## 4. Architecture & module boundaries

```
ml/curriculum.py   (PURE: no torch, no disk IO)
  Stage(frozen)                       — one rung: difficulty + hangar/fleet refs + overrides
  PromotionPolicy(frozen)             — metric, window K, threshold θ, max_iters cap
  EpisodeStat(frozen)                 — (fraction_placed, valid) captured from terminal StepInfo
  DEFAULT_LADDER: tuple[Stage, ...]   — the committed 5-rung ramp (§5)
  sample_request(pool, n, rng) -> tuple[str, ...]  — seeded size-n subset from an EXPLICIT id pool (pure; no disk)
  should_promote(window, policy) -> bool          — pure gate over the last-K stats
  CurriculumSchedule(frozen)          — (stages, policy); CurriculumSchedule.default() wraps DEFAULT_LADDER + a default PromotionPolicy
  CurriculumHistory                   — mutable, append-only recorder of per-(stage,iter) EpisodeStats + promotion events (record(), note_promotion(stage, iter, by=...)); pure data, no torch, lives here so CI can assert over it

ml/env.py     (torch-free already)
  reset(requested_ids: tuple[str, ...] | None = None)   — NEW optional override; None = 4a byte-identical

ml/stage_builder.py   (disk IO, NO torch — so it is genuinely CI-testable without the [train] extra)
  effective_fleet_ids(stage) -> tuple[str, ...]  — DISK pool resolver: stage.fleet_ids if set,
                                                   else tuple(load_fleet(stage.fleet_path).keys())
  build_stage_env(stage) -> HangarFitEnv         — load_hangar/load_fleet + dataclasses.replace overrides
  (split out of train.py precisely BECAUSE train.py imports torch at module level — keeping these here
   lets their tests run in the no-torch CI rather than importorskip-ing the torch extra.)

ml/train.py   (torch)
  collect_rollout(..., sample_request=None) -> (RolloutBuffer, list[EpisodeStat])   — extended
  train_curriculum(*, seed, schedule, ...) -> CurriculumHistory   — the ladder loop (imports
                                              build_stage_env/effective_fleet_ids from stage_builder)
  train(...)   — the 4a trivial trainer, adapted to the new list[EpisodeStat] return
  main()   — gains --schedule {trivial,curriculum}
```

**Boundary rule:** `curriculum.py` stays pure — it holds fleet/hangar references as **path strings + scalar override fields** inside `Stage`, never loading them. All disk IO (the `load_hangar` / `load_fleet` calls) and all torch live in `train.py`. This keeps the schedule logic unit-testable in CI without the `[train]` extra and without fixture files, exactly as `encoding.py` is.

---

## 5. The `Stage` model and the committed ladder

```python
@dataclass(frozen=True, slots=True)
class Stage:
    name: str
    difficulty: DifficultyConfig          # existing knobs: max_objects, per_object_step_budget, total_step_budget
                                          # (DifficultyConfig.seed_anchor also exists but is OUT of 4b scope — anchored
                                          #  spawning near a known-valid anchor is a later rung; the ladder leaves it False)
    hangar_path: str                      # repo-relative; resolved against the repo root in build_stage_env
    fleet_path: str                       # repo-relative manifest to sample from
    fleet_ids: tuple[str, ...] | None = None   # optional allow-list to sample WITHIN (None = whole fleet)
    clearance_m: float | None = None      # override applied to the loaded hangar (None = file value)
    wing_layer_clearance_m: float | None = None   # paired override (None = file value)
    apron_depth_m: float = 8.0            # matches 4a's build_trivial_env apron
```

`clearance_m` ramps difficulty by overriding the loaded `Hangar` via `dataclasses.replace` — **no new `DifficultyConfig` field**; clearance is already a `Hangar` property read through `collisions.check`. **Clearance direction = easy→hard:** a *smaller* required gap is lenient (parts may sit close and still be valid); a *larger* required gap is strict (parts must be well-separated to be valid), so the ramp increases `clearance_m` toward the real value.

> **Real clearance reference (verify before pinning values).** `examples/herrenteich/hangar.yaml` carries `clearance_m: 0.10` (horizontal) / `wing_layer_clearance_m: 0.15` since the #664 recalibration (its prior 0.20 was "too loose to reproduce reality"); `data/hangar.yaml` (box) carries the looser placeholder `clearance_m: 0.3`. So the ladder's lenient rungs deliberately **override to ~0.05** — genuinely below the 0.10 real value — and only the final rung tightens to the herrenteich file value (0.10), giving a real easy→hard ramp (0.05 → 0.10) instead of the two coinciding.

**`DEFAULT_LADDER`** — order *object count → hangar shape → clearance* (env design §7). Rung 0 **is** 4a's trivial stage (continuity):

| # | `name` | hangar | clearance (illustrative) | N objects | fleet source | new dimension |
|---|---|---|---|---|---|---|
| 0 | `trivial` | box (`data/hangar.yaml`) | lenient (~0.05) | 1 | synthetic `data/fleet.yaml` | baseline = 4a |
| 1 | `pair-box` | box | lenient (~0.05) | 2 | synthetic | **count** |
| 2 | `trio-box` | box | lenient (~0.05) | 3 | synthetic | **count** |
| 3 | `trio-notch` | L-notch (`examples/herrenteich/hangar.yaml`) | lenient (~0.05) | 3 | herrenteich (aircraft only) | **hangar shape** |
| 4 | `trio-notch-strict` | L-notch | real strict (herrenteich file, 0.10) | 3 | herrenteich | **clearance** |

Five rungs spanning all three dimensions (≥3 rungs / ≥2 dimensions, with margin). **Scalar values (N, clearance, step budgets) are a tuning surface** finalized during manual learn-validation; only the *shape* of the ladder is committed here.

> **Note on rung 0 vs 4a.** 4a's `_TRIVIAL_DIFFICULTY` uses `data/hangar.yaml`'s file clearance (0.3) and `requested_ids=("fuji",)`. Rung 0 may lower the clearance to a lenient value to give the agent an easier launch; the exact value is tuned. The 4a single-stage command remains reachable via `--schedule trivial`, which keeps `build_trivial_env` exactly as-is (byte-identical).

### 5.1 Hard invariants the ladder must satisfy (enforced + tested)
**Definition — *effective fleet ids* of a rung:** `stage.fleet_ids` when set, else the keys of `load_fleet(stage.fleet_path)`. `load_fleet` yields **aircraft only** (a manifest's `ground_objects:` section loads via a separate path and is never returned by `load_fleet`), so "aircraft only" (§2) holds automatically — no extra filter needed, even for `herrenteich/fleet.yaml` which also lists 4 ground objects. This resolution is the **only disk touch** and lives in `stage_builder.py`'s `effective_fleet_ids(stage)` (resolved **once per rung**); the pure `sample_request(pool, n, rng)` in `curriculum.py` then draws from that already-resolved pool, so `curriculum.py` stays disk-free and unit-testable over a literal id list.

1. **Encoder capacity:** every rung's `difficulty.max_objects ≤ EncoderConfig.max_objects` (the token-tensor height). `DifficultyConfig.max_objects` caps the *requested* set; `EncoderConfig.max_objects` is the *fixed token capacity* — the curriculum is the component responsible for never letting the first exceed the second (the `_tokens` `ValueError` guard already names "the curriculum").
2. **Sampleable:** `difficulty.max_objects ≤ len(effective fleet ids)` for each rung (can't request more objects than the rung's fleet offers).
3. **Non-empty, ordered:** `DEFAULT_LADDER` is a non-empty tuple; rungs are climbed in tuple order.

---

## 6. Promotion policy — competency-gated + cap

```python
@dataclass(frozen=True, slots=True)
class EpisodeStat:
    fraction_placed: float   # placed / total from the terminal StepInfo
    valid: bool              # StepInfo.valid (overlap == 0) at episode end
    total_reward: float      # sum of step rewards over the episode — keeps the 4a reward curve
                             # alive now that collect_rollout returns EpisodeStats instead of a bare
                             # ep_rewards list; EpisodeStat stays the single source for the per-iter log

@dataclass(frozen=True, slots=True)
class PromotionPolicy:
    metric: Literal["fraction_placed", "valid_rate"] = "fraction_placed"
    window: int = 20          # number of most-recent COMPLETED episodes to average
    threshold: float = 0.9    # advance when mean(metric over window) >= θ
    max_iters: int = 200      # safety cap: advance unconditionally after this many PPO iters on the rung

def should_promote(window: Sequence[EpisodeStat], policy: PromotionPolicy) -> bool:
    if len(window) < policy.window:
        return False
    recent = window[-policy.window:]
    if policy.metric == "valid_rate":
        score = mean(1.0 if s.valid else 0.0 for s in recent)
    else:  # fraction_placed
        score = mean(s.fraction_placed for s in recent)
    return score >= policy.threshold
```

- **Competency signal is free.** The env already emits, on each completed episode's terminal `StepInfo`, the fields `placed`, `total`, `valid`. `fraction_placed = placed / total`. `collect_rollout` captures these per finished episode (4a discards `_info`). No new geometry.
- **Window semantics.** The window is a rolling buffer of the most recent *completed* episodes **within the current rung** (reset on promotion). `should_promote` returns `False` until at least `window` episodes have completed on the rung.
- **Safety cap.** `train_curriculum` advances unconditionally once `iters_on_stage >= policy.max_iters`, so a rung the agent can't master (or a mis-tuned θ) can't hang the run. The cap firing is **logged** (so a "promoted by cap, not by mastery" event is visible, never silent).
- **Per-rung policy override (optional).** `Stage` may carry an optional `policy_override: PromotionPolicy | None` for rungs that need a different θ / cap; `None` uses the schedule's default policy. (Included only if a rung needs it during tuning; otherwise one policy for all rungs.)

---

## 7. Env & train wiring

### 7.1 `env.reset(requested_ids=None)`
```python
def reset(self, requested_ids: tuple[str, ...] | None = None) -> Observation:
    if requested_ids is not None:
        self.requested_ids = requested_ids   # overrides this episode's set + terminal-fraction denominator
    self._reset_state()
    return self._observe()
```
- `None` ⇒ unchanged from 4a (the existing fixed `self.requested_ids`), so every 4a path and the within-build canary stay **byte-identical**.
- When provided, the override flows through `_reset_state` (which builds `self._queue` from `self.requested_ids`) and through every `len(self.requested_ids)` terminal-fraction denominator — so a 3-object sampled episode scores out of 3 correctly.
- Validation (**net-new** guard, not preserving existing behavior): when `requested_ids` is provided it must be non-empty and every id must resolve in `fleet ∪ ground_objects`, else a loud `ValueError`. Today an unknown id surfaces later as a `KeyError` from `_body()` mid-`step`; this moves the failure to `reset` and makes it explicit (consistent with 4a's silent-failure hardening).

### 7.2 `collect_rollout` extension
```python
def collect_rollout(env, policy, encoder, rollout_len, *, sample_request=None):
    ...
    if done:
        # info.total = len(requested_ids) ≥ 1 by the §7.1 non-empty invariant, so the division is safe.
        ep_stats.append(EpisodeStat(
            fraction_placed=info.placed / info.total, valid=info.valid, total_reward=ep_reward))
        obs = env.reset(requested_ids=sample_request() if sample_request else None)
    ...
    return buf, ep_stats   # was: buf, ep_rewards
```
- `sample_request=None` keeps 4a's fixed-set behavior; the curriculum loop passes `lambda: sample_request(stage, rng)`.
- Return type widens from `list[float]` (mean-reward) to `list[EpisodeStat]`. `ep_reward` is the per-episode reward accumulator 4a already maintains (`train.py:83-85`); it is now carried on `EpisodeStat.total_reward` so the per-iteration `mean_ep_reward` log derives from the **same** stats object as the competency gate — `EpisodeStat` stays the single source, and 4a's reward-curve logging is preserved without a new buffer-reduction helper.

### 7.3 `train_curriculum`
```python
def train_curriculum(*, seed, schedule=CurriculumSchedule.default(), ppo=None, ...):
    torch.manual_seed(seed)
    policy = HangarFitPolicy(...)
    optimizer = Adam(...)
    history = CurriculumHistory()
    for stage_index, stage in enumerate(schedule.stages):
        env = build_stage_env(stage)
        pool = effective_fleet_ids(stage)         # disk resolve, ONCE per rung
        n = stage.difficulty.max_objects          # samples-per-episode size
        rng = stage_rng(seed, stage_index)        # isolated from torch's global stream; keyed by ladder position
        window: deque[EpisodeStat] = deque(maxlen=policy.window)
        for it in range(policy.max_iters):
            buf, ep_stats = collect_rollout(env, policy, enc, rollout_len,
                                            sample_request=lambda: sample_request(pool, n, rng))
            ppo_update(policy, optimizer, buf, cfg)
            window.extend(ep_stats); history.record(stage, it, ep_stats)
            if should_promote(list(window), policy):
                history.note_promotion(stage, it, by="competency"); break
        else:
            history.note_promotion(stage, policy.max_iters, by="cap")
    return history
```
- **Single policy/optimizer across rungs** (the whole point of a curriculum is transfer): the network carries learned weights from rung to rung; only the env (hangar/fleet/clearance) changes at promotion.
- `build_stage_env` loads the rung's hangar+fleet **once per rung** (not per episode) and applies `clearance_m` / `apron_depth_m` overrides via `dataclasses.replace`; per-episode only the *requested subset* resamples (cheap, no disk).

### 7.4 CLI
`python -m ml.train` gains `--schedule {trivial,curriculum}` (default **`curriculum`** → `train_curriculum`; `trivial` → 4a's `train` on `build_trivial_env`, unchanged). Existing flags (`--seed`, `--iterations`, `--rollout-len`, `--lr`) carry over; `--iterations` maps to the per-rung `max_iters` for the curriculum schedule (or a dedicated `--max-iters-per-stage`; resolved in implementation).

---

## 8. Determinism (the within-build canary, extended)

One master `seed` drives:
1. `torch.manual_seed(seed)` — network init + minibatch shuffle (4a behavior, unchanged).
2. A **per-stage object-sampling RNG**, `stage_rng(seed, stage_index)` (keyed by the rung's position in the ladder, from `enumerate`) — a `random.Random` (or `numpy.Generator`) **isolated** from torch's global stream, used only by `sample_request`. Seeding by stage position makes each rung's episode sequence reproducible and independent of how many iterations the previous rung took.
3. Action sampling stays on torch's **global** RNG (4a behavior — no per-call `Generator` threaded through `.act()`).

Because (a) the env reward is RNG-free (spec §8), (b) object sampling is seeded, and (c) promotion is a **pure function of seeded rollout stats**, a fixed `seed` + the deterministic stage/episode order ⇒ a **bit-identical run** — *even though the per-rung iteration count is now data-dependent* (a stage promotes after whatever number of iterations the seeded stats dictate; that number is itself deterministic for a fixed seed).

This trainer is **not** under ADR-0003 / `determinism-guard` (those guard `solver.py` / `towplanner.py`; no solver/towplanner/geometry code changes here). The within-build seeded canary is the learned-path equivalent, as established in 4a. Cross-machine reproducibility (float / execution-provider variance) is explicitly out of scope (a #5/#6 concern).

---

## 9. Testing & acceptance

### 9.1 CI unit tests (no torch — `ml/curriculum.py` + the env change)
- **Ladder invariants:** `DEFAULT_LADDER` non-empty; every rung satisfies `max_objects ≤ EncoderConfig.max_objects` and `max_objects ≤ len(effective fleet ids)`; difficulty is non-decreasing along the dimensions it ramps (sanity).
- **`sample_request(pool, n, rng)`:** over a literal id pool — seeded determinism (two RNGs seeded alike → identical successive draws); correct size (`= n`); membership ⊆ `pool`; no duplicates within a draw; raises on `n > len(pool)`.
- **`effective_fleet_ids(stage)` + `build_stage_env(stage)`** (in `stage_builder.py`, torch-free → runs in the no-torch CI): `effective_fleet_ids` returns `stage.fleet_ids` verbatim when set, else the `load_fleet(stage.fleet_path)` keys (aircraft only — a herrenteich stage with `fleet_ids=None` excludes the 4 ground objects); `build_stage_env` applies the `clearance_m`/`apron_depth_m` overrides via `replace` and constructs a `HangarFitEnv` whose `difficulty` is the stage's.
- **`should_promote`:** returns `False` until `window` episodes accumulate; fires exactly when the windowed mean ≥ θ; honors both `metric` variants; a sub-threshold window never promotes.
- **`env.reset(requested_ids=…)`:** override sets the queue + terminal-fraction denominator; `reset(None)` is byte-identical to the pre-4b `reset()` (a regression guard so 4a stays untouched).

### 9.2 Torch-gated test (`importorskip("torch")`; CI torch job lands in #6)
- **Curriculum canary:** a short seeded `train_curriculum` over a tiny 2-rung schedule with a low θ / tiny rollout advances **≥1 promotion** and produces **identical** `CurriculumHistory` across two runs with the same seed (the extended within-build determinism canary).
- **Promotion-by-cap path:** a schedule with an unreachable θ promotes by the `max_iters` cap and logs `by="cap"` (so the safety path is exercised).

### 9.3 Manual learn-validation (NOT a CI gate — reported in the PR)
Run `python -m ml.train --schedule curriculum` long enough to **observe the agent climbing ≥3 rungs**: report the per-rung reward curve + the promotion log (which rungs promoted by competency vs by cap). Success for 4b = *the agent demonstrably advances through multiple rungs with rising competency*, not full-task mastery. (The reach-not-beat benchmark over dense RR-MC-missed scenarios is 4c.)

---

## 10. Risks & mitigations
- **A rung is too hard → always promotes by cap.** Mitigated by the cap (run never hangs) + the `by="cap"` log surfacing it; the fix is a tuning adjustment (smaller increment / lower θ) during validation, not a code change. The committed ladder keeps increments small (one dimension per rung) specifically to avoid this.
- **Per-episode env reconstruction cost.** Avoided: the hangar/fleet load once per *rung*; only the requested subset resamples per episode (a tuple of ids, no disk).
- **Return-type widening of `collect_rollout`** could break 4a callers. Mitigated: `sample_request` defaults to `None`; the `--schedule trivial` path and the 4a tests are kept green (and the reset-byte-identical test guards it).
- **Two `max_objects` confusion** (`DifficultyConfig` vs `EncoderConfig`). Mitigated by the §5.1 invariant test that ties them and a comment at the `Stage` definition.

---

## 11. Implementation notes (for the plan)
- File a GitHub issue *"#607 rung 6: curriculum schedule (sub-project #4b)"* (rungs 1=#670, 2=#672, 3=#676, 4=#680, 5=#684; Part of #607) **before** coding; branch `feature/607-rung6-curriculum-schedule` off `develop`; TDD; draft PR `Closes #<n>`.
- **Review arc:** `code-reviewer` (main pass) + `silent-failure-hunter` (the promotion / sampling / reset-override edge handling + the cap-vs-competency logging). **Not** `determinism-guard` / `geometry-invariant-guard` (no solver/towplanner/geometry change). Consider `type-design-analyzer` since `Stage` / `PromotionPolicy` / `EpisodeStat` are new types.
- **CHANGELOG** `[Unreleased]` entry (user-facing: the `--schedule` CLI surface + curriculum training).
- Report the **manual curriculum reward curve** in the PR.
- Keep `curriculum.py` import-light (no torch, no `hangarfit.loader`) so the CI no-torch tests load it cleanly.

## 12. Open questions (resolve in 4c / tuning, not here)
- Final per-rung scalar values (N, clearance, budgets) and the promotion θ / window / cap — tuned against measured training during manual validation, then frozen.
- Whether to add a 6th rung that introduces a ground object (fuel-trailer keep-out) once aircraft-only rungs are validated — candidate for 4c or a 4b follow-up, not committed here.
- Vectorized envs for throughput once single-env curriculum learning is confirmed → perf follow-up.
