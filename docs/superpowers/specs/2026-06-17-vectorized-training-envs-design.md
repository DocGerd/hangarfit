# Vectorized training envs (Sync + Subproc) — design

- **Date:** 2026-06-17
- **Issue:** #708 (sub-task of #698, epic #607)
- **Status:** Design (approved for planning)
- **Related:** `ml/train.py` (the single-stream loop this generalizes), `ml/ppo.py` (the buffer/GAE this extends), `ml/env.py` (`HangarFitEnv`), `ml/encoding.py` (`encode`), `ml/curriculum.py` (`stage_rng`/`sample_request`). Determinism is `ml-rl-guard` scope (training reproducibility/seeding).

## 1. Goal & scope

Give the cold-joint PPO training loop **throughput** by running N environments in parallel, so the #698 train-to-mastery run can reach the dense rungs in a reasonable CPU budget. The binding cost is **CPU shapely geometry**, split across `env.step` (motion/overlap/egress) and the encoder's **rasterization** (`encode`), together ~70%+ of step time; the NN forward is ~25% and is currently run **batch-1** per step. This work parallelizes the geometry+encoding across worker processes and batches the forward.

**In scope:** a `VectorEnv` abstraction (`SyncVectorEnv` + `SubprocVectorEnv`), an N-stream PPO buffer + per-env GAE, a vectorized collector, and an `n_envs` knob on `train_curriculum`/`train`.

**Out of scope (stays in #698 or later):** the actual mastery run + knob/`PromotionPolicy` sweep + statistical reach-rate; shared-memory IPC (pickle-over-Pipe for v1); the backward/start-state curriculum.

## 2. Current state (verified against the code)

- `ml/train.py::collect_rollout` drives ONE `HangarFitEnv` for `rollout_len` steps: per step it `encode`s the obs, calls `policy(to_batch([obs_t]))` (**batch 1**), `sample_action`, `env.step`, and appends to a `RolloutBuffer`. On `done` it records an `EpisodeStat` and `env.reset(requested_ids=sample_request())`.
- `ml/ppo.py::RolloutBuffer` is a flat single-env transition list; `compute_gae` runs over the flat sequence, resetting the λ-recursion at each `done`; `ppo_update` flattens to minibatches over `T`.
- `ml/env.py::HangarFitEnv` is **torch-free** (numpy + shapely + `hangarfit.*`). `step` returns `(Observation, reward, done, StepInfo)`; `StepInfo.valid` runs `go.layout_valid` (a `check`). `reset(requested_ids=…)` re-seeds the episode object set.
- `ml/encoding.py::encode(obs, hangar, bodies, config)` is pure numpy+shapely (rasterization via `shapely.contains_xy`) — **also a major shapely cost**, currently run in the main loop.
- `ml/curriculum.py`: `stage_rng(seed, stage_index) -> random.Random`; `sample_request(pool, n, rng) -> tuple[str, …]` draws the next episode's object set; `should_promote(window, policy)` gates promotion on `mean(metric over window) >= threshold` (default metric `valid_placed`, window 20, threshold 0.9, `max_iters` 200).

## 3. Architecture

```
                 main process (ALL torch)                         N worker processes (torch-FREE)
  ┌───────────────────────────────────────────────┐      ┌────────────────────────────────────────┐
  │ collect_rollout_vec:                           │      │ HangarFitEnv  +  encode()                │
  │   obs_batch (N ObservationTensors)             │ act  │   step(action) → Observation             │
  │   → policy(to_batch) [batch N]  → sample N     │─────▶│   encode(obs)  → ObservationTensors      │
  │   → VecRolloutBuffer (T,N)                      │◀─────│   (auto-reset on done; per-worker RNG)   │
  │ per-env GAE → ppo_update (flatten T·N)         │ obs  │   returns (ObsTensors, r, done, StepInfo)│
  └───────────────────────────────────────────────┘      └────────────────────────────────────────┘
       SyncVectorEnv runs the worker body in-process (serial); SubprocVectorEnv runs it in N subprocesses.
```

### 3.1 `ml/vector_env.py` (new)

A small `VectorEnv` interface with two implementations and a shared **worker body**:

- **Worker body** `step_and_encode(env, action, encoder) -> (ObservationTensors, float, bool, StepInfo, EpisodeStat | None)`: apply `action` via `env.step`; on `done` record the finished episode's `EpisodeStat`, `env.reset(requested_ids=<next from this worker's RNG>)`, and encode the NEW initial obs (gym-style **auto-reset** — the returned tensors are always a live obs to act on next step); else encode the stepped obs. Returns the terminal `EpisodeStat` (or `None`) so the collector can window competency. **Torch-free.**
- **`SyncVectorEnv(env_fns, encoder, seeds)`** — constructs N envs in-process; `reset()`/`step(actions)` loop the worker body serially. The **byte-identical reference + test oracle**; needs no `multiprocessing`, runs in CI.
- **`SubprocVectorEnv(env_fns, encoder, seeds)`** — spawns N worker processes (`multiprocessing` `spawn` context for cross-platform determinism), each holding one env + its seeded RNG. Parent↔worker over a `Pipe` (pickle); commands `step(action)` / `reset` / `close`. `step(actions)` scatters N actions, gathers N results **in worker-index order**. Workers do step+encode; only `ObservationTensors`/reward/done/`StepInfo`/`EpisodeStat` cross the boundary. Context-manager (`__enter__`/`__exit__`) closes workers; a dead worker surfaces a loud error (no silent hang).
- Interface (both): `reset() -> list[ObservationTensors]`; `step(actions: Sequence[Action]) -> VecStep` where `VecStep = (obs: list[ObservationTensors], rewards: list[float], dones: list[bool], infos: list[StepInfo], ep_stats: list[EpisodeStat | None])`; `num_envs: int`; `close()`.

### 3.2 `ml/ppo.py` — N-stream buffer + per-env GAE

- **`VecRolloutBuffer(num_envs)`**: stores transitions as `T` rows of width `N` (lists of length N per field): `obs[t]` (N `ObservationTensors`), `kind_idx`/`mag_idx`/`logprob`/`value`/`reward` (N each), `done` (N bools); plus `last_value: list[float]` (per-env bootstrap). `batch()` flattens to `T·N` for the update (obs via `to_batch` over all `T·N`).
- **Per-env GAE**: compute advantages/returns **per env column** (each env's `done` resets its own λ-recursion and bootstrap), then flatten. Reuse `compute_gae` per column (N calls on length-`T` slices) — keeps the verified single-stream GAE as the kernel. `last_value[i]` bootstraps env i's non-done tail.
- `VecRolloutBuffer.batch()` returns the **same dict shape** as `RolloutBuffer.batch()` (flat `T·N` tensors keyed `raster`/`tokens`/…/`kind_idx`/`reward`/`done`), so **`ppo_update` is unchanged** — it already shuffles a flat set into minibatches. The only PPO change is `compute_gae` invoked per-env-column before the flatten. The legacy `RolloutBuffer` stays for the `n_envs=1` path.

### 3.3 `ml/train.py` — vectorized collector + `n_envs` knob

- **`collect_rollout_vec(vec_env, policy, encoder, rollout_len) -> (VecRolloutBuffer, list[EpisodeStat])`**: `obs = vec_env.reset()`; for `rollout_len` steps: `to_batch(obs)` → `policy(batch)` [batch N] → `sample_action` (batched) → `vec_env.step(actions)` → append the N transitions; collect `EpisodeStat`s as episodes finish. Bootstrap `last_value` from a final batched forward on the non-done tail.
- **`train_curriculum(..., n_envs: int = 1)`** (and `train`): **`n_envs == 1` keeps the legacy `collect_rollout` path UNTOUCHED** (zero behavior change — trivially byte-identical, since it is literally the same code); only **`n_envs > 1`** routes to `collect_rollout_vec` over a `VectorEnv`. A `--n-envs` CLI flag and a `--vec-backend {sync,subproc}` selector (default `subproc` for `n_envs>1`). A test asserts `SyncVectorEnv` at `N=1` reproduces the legacy curve — proving the vec path *reduces correctly* — but the default production path does not depend on it.
- The competency window (`should_promote`) consumes the collector's `EpisodeStat`s exactly as today; per-rung promotion is unchanged.

## 4. Determinism (the contract — ml-rl-guard scope)

- **Workers are torch-free** → the only torch is the main-process policy forward + action sampling (single-thread, deterministic within a build). The env step + encode are deterministic numpy/shapely. Therefore:
  - **`SyncVectorEnv(seed, N)` ≡ `SubprocVectorEnv(seed, N)` byte-identical** — same env RNG seeding + same worker-index aggregation order + the same main-process torch draws.
  - **`n_envs=1` (Sync) ≡ legacy single-stream byte-identical** — the regression anchor. The `N=1` collector consumes the torch action-RNG and the episode-sampling RNG in the same order as `collect_rollout`.
  - **Not identical across different N** — a different batch size changes the torch-RNG draw pattern; expected and documented.
- **Per-worker seeding:** worker i's env-episode RNG derives from `(seed, stage_index, worker_index=i)` (extend `stage_rng` to take a `worker_index`, defaulting to the legacy single-stream stream for `n_envs=1`). The main-process `torch.manual_seed(seed)` is unchanged.
- This is a **new, weaker-than-ADR-0003** reproducibility tier for training (training was never ADR-0003-bound; that contract is the solver's). It is documented here and guarded by `ml-rl-guard`, not `determinism-guard`.

## 5. Risks & mitigations

| Risk | Mitigation |
|---|---|
| `n_envs=1` not byte-identical to legacy (subtle RNG-order drift) | Make the `N=1` path consume RNG in the exact legacy order; a byte-identity test is the gate. If a genuine, documented divergence is unavoidable, escalate before downgrading to equivalence-only. |
| Subproc pickling overhead (≈0.5 MB raster/env/step) dominates | Measure Subproc-vs-Sync throughput; if pickling dominates, shared-memory is the follow-up (out of scope for v1). The geometry parallelization is expected to dominate the pickling cost. |
| Worker death / hang | `spawn` context, context-manager close, and a loud error on a broken pipe / dead worker (no silent hang); a watchdog/timeout on `step` gather. |
| `multiprocessing` + CI | All correctness logic lives in `SyncVectorEnv` (no subprocesses) so CI tests the N-stream logic without spawning; a small Subproc smoke is `@slow`/guarded. |
| Curriculum competency semantics drift | `should_promote`/window unchanged; `EpisodeStat`s flow from the collector exactly as today (just from N streams). |

## 6. Testing (TDD)

1. **`N=1` byte-identity to legacy:** `train_curriculum(n_envs=1, seed=S)` produces the identical per-iteration loss/metric history as the legacy path at seed S (a few iters, trivial stage).
2. **`Sync(N) ≡ Subproc(N)` byte-identity:** same `(seed, n_envs)` → identical history (workers torch-free).
3. **Per-env GAE ≡ N single-env `compute_gae`:** a `VecRolloutBuffer` with hand-built `(T,N)` transitions yields per-column advantages/returns equal to N independent `compute_gae` calls.
4. **Auto-reset correctness:** a worker that hits `done` mid-rollout returns a live (reset) obs next step and a non-None `EpisodeStat` at the boundary; `unplaced`/active invariants hold.
5. **Throughput smoke (`@slow`):** on a multi-object stage, `SubprocVectorEnv(N=k)` completes a fixed step budget faster than `SyncVectorEnv(N=k)` (a wall-clock lower-bound assertion, generous margin).
6. **Worker-failure path:** a worker raising surfaces a loud error from `step`, not a hang.
7. `ruff`/`mypy ml/` clean; `ml-rl-guard` invariants (validity=product checker, knob default-neutrality, reproducibility) preserved — this change is orthogonal to the reward/knobs.

## 7. Acceptance criteria

Mirrors issue #708:
- [ ] `SyncVectorEnv` `n_envs=1` byte-identical to legacy single-stream training.
- [ ] `Sync(seed,N) ≡ Subproc(seed,N)` byte-identical.
- [ ] `VecRolloutBuffer` per-env GAE ≡ N independent `compute_gae`.
- [ ] `train_curriculum(n_envs=K)` trains; Subproc-faster-than-Sync throughput smoke passes.
- [ ] `ruff`/`mypy ml/` clean; ml-rl-guard invariants preserved.
- [ ] CHANGELOG `[Unreleased]` entry; `ml/README.md` documents `--n-envs`/`--vec-backend`.
