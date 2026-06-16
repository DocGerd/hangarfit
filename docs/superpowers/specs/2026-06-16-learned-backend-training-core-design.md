# Learned backend — Training core (sub-project #4a)

**Status:** Draft (design under review)
**Date:** 2026-06-16
**Scope:** Sub-project **#4a** of the learned-backend epic (#607) — the **PPO training core ONLY**: a roll-your-own (cleanrl-style) PPO that drives `HangarFitEnv` + `HangarFitPolicy` directly, plus a runnable `python -m ml.train` entry that trains on the **fixed trivial curriculum stage**. **No curriculum schedule (→ 4b), no eval/reach-not-beat benchmark (→ 4c).**
**Builds on:** sub-project #1 (env + reward, #672), #2 (tensorizer `ml/encoding.py`, #677), #3 (policy `ml/policy.py` + action `ml/action_space.py`, #681).
**Decomposition:** the cold-joint spec §3 lists "training + evaluation harness" as one sub-project; it is split (2026-06-16) into **4a training core** (this doc) → **4b curriculum schedule** → **4c eval/benchmark**, each its own spec→plan→PR.

---

## 1. Context

The cold-joint env (#672) is an autoregressive per-step-primitive MDP; the tensorizer (#677) turns its `Observation` into `ObservationTensors`; the policy (#681) is a `torch` `nn.Module` emitting a masked `(kind,gear)` head + a `K`-way magnitude head + a value head, with `.act()` (masked sampling + joint log-prob + `action_space.decode`). What is missing is the **trainer** that closes the loop: collect rollouts, estimate advantages, and update the policy by PPO. This sub-project adds exactly that, at the smallest useful scope — enough to answer *"does the cold-joint formulation learn the trivial case?"*.

**Decision (2026-06-16):** roll-your-own cleanrl-style PPO, **not** Stable-Baselines3. Our setup (custom CNN+Transformer policy, a *factored per-step-masked* action over a *dict* observation) fits an off-the-shelf `ActorCriticPolicy` poorly; SB3 + `sb3-contrib` would require a `gymnasium.Env` adapter and heavy custom-policy/features-extractor glue. A compact PPO drives our env + policy directly and keeps the `[train]` extra to just `torch`.

## 2. Goals & non-goals

**Goals**
- A compact, **testable** PPO (`ml/ppo.py`): rollout buffer, GAE-λ, clipped-surrogate update — reusing the policy's masked two-head logits, joint log-prob, and value.
- A runnable `python -m ml.train` (`ml/train.py`) that trains a `HangarFitPolicy` on the **fixed trivial stage** and logs a reward curve.
- **Seedable / within-build deterministic** (ADR-0027): a fixed seed → reproducible run.
- CI-test the PPO **mechanics** (fast, deterministic); validate **learning** by a manual offline run whose reward curve is reported.

**Non-goals (deferred)**
- The **curriculum schedule** (ramping `DifficultyConfig`) → 4b. 4a hardcodes the trivial stage.
- The **eval / reach-not-beat benchmark** → 4c.
- **Vectorized / parallel envs** — single-env first; a documented later knob.
- **ONNX export, `solve --backend learned` wiring, weight checkpointing/signing** → #5/#6.
- A `gymnasium.Env` subclass — not needed for a roll-your-own trainer (driven envs use `reset()/step()` directly).
- Reward-weight *tuning* — the env's `RewardWeights` are a knob the trainer accepts; finding good values is experimental work, not a deliverable module.

## 3. Module structure
- **Create** `ml/ppo.py` — `RolloutBuffer`, `compute_gae`, `PPOConfig`, `ppo_update` (the PPO machinery; the testable seam).
- **Create** `ml/train.py` — the `python -m ml.train` entry: build env + policy, run the loop, log the reward curve. Holds the trivial-stage config and the rollout-collection orchestration.
- **Create** `tests/ml/test_ppo.py` (`pytest.importorskip("torch")`).
- **Modify** `CHANGELOG.md`.

No `pyproject.toml` change (torch already in `[train]`). `ml/` stays out of the wheel.

## 4. Rollout — drive `HangarFitEnv` directly (single env)
Per step: `obs = encoding.encode(env_observation, hangar, bodies, cfg)` → `policy.act(obs, turn_radius_m=active.effective_turn_radius_m())` returns `((kind_idx, mag_idx), joint_logprob, decoded)` and the step value (from a forward pass; `act` is extended or paired with a value read) → `env.step(decoded)`. Store `(obs, kind_idx, mag_idx, joint_logprob, value, reward, done)` in the `RolloutBuffer`. On `done` (set complete / per-object or global budget), `env.reset()` and continue until `rollout_len` (default 2048) transitions are collected. The active body's `turn_radius_m` (for `decode`) comes from the env's active object.

> **Value at collection.** `policy.act()` currently returns only the action + log-prob. The trainer needs the **value** for the same forward pass. 4a either (a) extends `act()` to also return `value`, or (b) calls `forward(to_batch([obs]))` once and derives both the sampled action and the value from that single `PolicyOutput` (preferred — one forward, no recompute). The bit-identical-masking + sampling logic stays in one place.

## 5. The conditionally-factored action (PPO correctness)
The action is factored — a `(kind,gear)` categorical × a magnitude-bin categorical — but **magnitude only applies to movement primitives**: for **PARK** (`kind_idx == PARK_INDEX`) the sampled magnitude bin is meaningless (`action_space.decode` ignores it). Therefore:

```
joint_logprob = kind_logprob + (kind_idx != PARK_INDEX) * mag_logprob
joint_entropy = kind_entropy + (mean over non-PARK steps of) mag_entropy
```

Including `mag_logprob` for PARK steps would inject a spurious policy gradient on a decision the env never consumes. This resolves the concern the #3 type-design review deferred to #4. The PPO ratio uses this PARK-gated joint log-prob, recomputed under the current policy during the update from the stored observation + **stored `legal_action_mask`** (so the kind categorical is masked identically to collection time).

## 6. GAE + PPO update (`ml/ppo.py`)
- `compute_gae(rewards, values, dones, *, gamma=0.99, lam=0.95)` → advantages + returns, `done`-masked at episode boundaries (bootstrap from the last value when truncated by budget). Advantages normalized per batch.
- `ppo_update(policy, optimizer, buffer, config)`: for `config.epochs` (default 4) over shuffled minibatches — clipped surrogate `min(ratio·A, clip(ratio, 1±ε)·A)` (ε=0.2) + value loss (MSE to returns, optionally clipped) + `−entropy_coef·entropy`; gradient-clip; Adam (lr 3e-4). `PPOConfig` holds all knobs with defaults.

## 7. Determinism & seeding (ADR-0027 within-build)
A master `seed` seeds `torch.manual_seed`, the action-sampling generator, network init, and minibatch shuffling. The env reward is RNG-free (spec §8). So a fixed seed → a **bit-identical** short run (tested). The trainer is **not** under ADR-0003 / `determinism-guard` (those guard `solver.py`/`towplanner.py`); this within-build canary is the learned-path equivalent. Cross-machine reproducibility (float/EP variance) is explicitly out of scope (a #5 concern).

## 8. `ml/train.py` — the runnable entry
`python -m ml.train [--seed S --iterations N --rollout-len L --lr ...]`:
- builds a `HangarFitEnv` on the **fixed trivial stage** — `DifficultyConfig` with a single requested object, a loose clearance hangar, and a small per-object/global budget (the easiest curriculum rung: drive one object in from the apron and park it);
- builds a `HangarFitPolicy` (default hyper-params);
- runs `iterations` of {collect `rollout_len` → `compute_gae` → `ppo_update`}, logging per-iteration **mean episode reward, mean episode length, policy/value/entropy losses** to stdout, and returns the reward history (so a test can assert it ran and a human can read the curve).

Single-env; vectorization noted as a later knob. Checkpoint save/load is minimal-or-deferred (a `--save PATH` torch.save is acceptable but not required for 4a).

## 9. Testing
**CI (`tests/ml/test_ppo.py`, `importorskip` torch — fast + deterministic):**
- `compute_gae` matches a **hand-computed** toy trajectory (known rewards/values/dones → known advantages/returns).
- `RolloutBuffer` stores/returns the expected shapes; the PARK-gated joint-logprob helper excludes magnitude for PARK and includes it otherwise.
- a seeded `ppo_update` on a small collected batch **runs, changes parameters, and yields finite** policy/value/entropy losses; loss over a few epochs on a *fixed* batch **decreases** (overfit smoke, bounded to ~1–2 s).
- **seed-reproducibility**: two short seeded runs (a few iterations on the trivial stage, tiny rollout) produce **identical** reward histories / final parameters.

**Manual learn-validation (NOT CI):** run `python -m ml.train` on the trivial stage for enough iterations to see the reward curve; **report it in the PR** (does the agent learn to park the single object?). The reach-not-beat benchmark over dense RR-MC-missed scenarios is **4c**.

## 10. Reuse map
| need | reuse |
|---|---|
| env transition + reward | `ml.env.HangarFitEnv.reset()/step()` |
| observation → tensors | `ml.encoding.encode` + `ml.policy.to_batch` |
| action sampling + value + log-prob | `ml.policy.HangarFitPolicy` (`.act()` / `forward`) |
| sampled action → env primitive | `ml.action_space.decode(..., turn_radius_m=...)` |
| `PARK_INDEX` / action dims | `ml.encoding.PARK_INDEX` / `ml.action_space` |

## 11. Workflow
File a GitHub issue *"#607 rung 5: PPO training core (sub-project #4a)"* (rung 1=#670, 2=#672, 3=#676, 4=#680; Part of #607) before coding; branch off `develop`; TDD; draft PR `Closes #<n>`; review arc (`code-reviewer` + `silent-failure-hunter` for the rollout/GAE edge handling; **not** `determinism-guard`/`geometry-invariant-guard` — no solver/towplanner/geometry change); CHANGELOG entry; report the manual training reward curve in the PR.

## 12. Open questions (resolve in 4b/4c, not here)
- The **curriculum schedule** — how/when `DifficultyConfig` ramps (object count, hangar shape, clearance) → 4b.
- **Vectorized envs** for throughput once single-env learning is confirmed → 4b/perf.
- Final **PPO hyper-parameters + reward weights** (tuned against measured training) → woven through 4a/4b experimentally.
- The **reach-not-beat** acceptance metric + curated dense scenarios → 4c.
- **Checkpoint format** for handing weights to ONNX export → #5.
