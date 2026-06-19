# Mixed-start anchor rung — design (#712 follow-up)

**Date:** 2026-06-19
**Epic:** #607 (learned backend). **Builds on:** #712 seed-anchor start-state graft (PR #716).
**Status:** Design — awaiting user review before implementation.

---

## 1. Problem

The #712 seed-anchor graft inserts an opt-in `pair-anchored` rung: one of a 2-object
witness's objects is pre-parked at a committed-valid pose (`seed_anchor_k=1`) and the
agent drives the other in. Two seed-0 runs on the box hangar settle the question of what
it does and does not buy:

| Rung | gate (cap 25) | cheap pre-check (cap 80) |
|---|---|---|
| `pair-anchored` (k=1) | saturated ~0.65, promoted by cap | **mastered 0.94 → promoted by competency** |
| `pair-box` (k=0, empty start) | collapsed to `valid_placed` 0.000 | **still collapsed to 0.000** |

Findings:

1. **k=1 can truly master.** The gate's ~0.65 "ceiling" was under-training: the per-rung
   entropy warmup is 40 iters (`train.py` resets `it` to 0 each stage) but the gate capped
   stages at 25, so no 2-object rung ever reached the low-entropy exploitation phase. Raising
   the cap to 80 let `pair-anchored` reach 0.94 and promote by competency — the first 2-object
   rung ever to do so.
2. **k=1 mastery does not transfer to k=0.** Whether k=1 is mediocre (gate) or mastered
   (pre-check), the empty-start `pair-box` collapses to the **place-nothing absorbing state**:
   once `fraction_placed → 0`, every episode ends in a trivially-valid empty layout, so there
   is no gradient pointing back toward placement, and the collapse happens *during* the
   high-entropy window — later low entropy only sharpens it.

**Root cause (confirmed):** a start-state *distribution-shift cliff*. Training the anchored
rung is 100% "one object already present"; evaluation/`pair-box` is 100% "empty hangar". The
policy learns "committing is only safe when a valid anchor is already present" and, off that
distribution, banks the degenerate empty-layout optimum.

`--device cuda` is non-deterministic across runs, so the numbers above are read qualitatively
(only `trivial` iter-0 = 0.018 byte-matched across runs — the seeded init before any GPU
update). The *direction* (k=1 masters, k=0 collapses) reproduced across both runs.

## 2. Goal & success criteria

Give the agent a start-state distribution that includes empty-start (k=0) episodes
**throughout** the rung, so the value function never goes out-of-distribution on the
first-from-empty placement and the place-nothing pole stops being an inescapable basin.

**Success:** an empty-start 2-object layout is reached from a cold start —
- the new mixed rung's `valid_placed` lifts and ideally promotes by competency (≥ 0.9 over
  the window), and
- the downstream all-empty `pair-box` rung **no longer collapses** (lifts off 0.000;
  ideally promotes by competency).

**Non-goals (this change):** trio/N-object generalization; the continuous-ramp variant (§7);
mastering the full fleet. Those are follow-ups gated on this working for the pair.

## 3. Why a fixed mixture, not a ramp

By the time the curriculum reaches the failed rung the agent has **already mastered both
sub-skills**: `solo-box` taught "place object 1 into empty space" (≥ 0.9) and `pair-anchored`
taught "place object 2 validly next to object 1" (0.94). A k=0 empty-start episode is exactly
those two skills *chained*: place obj 1 → the state now looks like a k=1 start → place obj 2.
The only thing never practiced is the **chaining**.

- A **continuous ramp** (start `p(k=0)≈0`, increase) front-loads k=1 episodes — but the agent
  does not need more k=1 practice. It *starves* the agent of the k=0 chaining practice it
  actually lacks, exactly when it has the most exploration budget to use it.
- A **fixed mixture** (each episode is k=1 or k=0 with constant probability) gives k=0
  chaining practice from iter 0, while the interleaved k=1 successes keep a live
  "placing-is-good / here-is-what-valid-looks-like" gradient that pulls the k=0 behavior off
  the place-nothing pole.

Fixed mixture is therefore both **simpler to implement** (no per-iteration parent→worker
channel; see §4) and **better-motivated**. The ramp remains a documented fallback (§7).

## 4. Design

### 4.1 Per-episode k override on the env

`HangarFitEnv.reset` gains an optional per-episode override:

```python
def reset(self, *, requested_ids=None, seed_anchor_k=None) -> Observation: ...
```

`_reset_state` uses `seed_anchor_k` when provided, else falls back to
`self.difficulty.seed_anchor_k` (today's behavior). The existing `_reset_state` guards
(`0 <= k < len(requested)`, every anchored id has a witness pose) apply to the effective k.
Default `None` ⇒ **byte-identical to pre-change**.

### 4.2 Episode-start record

The per-episode sampler currently returns a bare `tuple[str, ...]` of requested ids. It
becomes a small immutable record so a rung can also carry the per-episode k:

```python
@dataclass(frozen=True, slots=True)
class EpisodeStart:
    requested_ids: tuple[str, ...]
    seed_anchor_k: int | None = None   # None ⇒ env uses difficulty.seed_anchor_k
```

- Non-mixed rungs: the sampler returns `EpisodeStart(ids, None)` ⇒ unchanged behavior.
- Mixed rungs: the sampler draws `k ∈ {1, 0}` ~ Bernoulli(`anchor_prob`) **from the same
  per-worker `stage_rng` stream** that already draws the requested-id permutation, then
  returns `EpisodeStart(ids, k)`.

Drawing k from the existing seeded stream is the determinism keystone: Sync and Subproc
workers with the same `worker_index` consume the stream identically, so
**Sync ≡ Subproc stays byte-identical** and a fixed `(seed, schedule)` stays reproducible —
the contracts the `ml-rl-guard` enforces. Draw order is fixed (ids first, then k) so the
stream is stable.

### 4.3 The `pair-mixed` rung

A new stage between `pair-anchored` and `pair-box`:

```
trivial → solo-box → pair-anchored(k=1) → pair-mixed(k∈{0,1}) → pair-box(k=0) → trio-box → …
```

- `max_objects=2`, `anchor_layout_path=witness_box.yaml` (**reuses the existing witness — no
  new fixture**), `clearance` matching `pair-anchored`.
- `DifficultyConfig.anchor_prob: float | None = None` carries the mixture probability
  (`None` ⇒ not a mixed rung; the env uses the fixed `seed_anchor_k`). It sits next to
  `seed_anchor_k` on `DifficultyConfig`. `anchor_prob` is `P(k=1)` per episode; the
  `pair-mixed` rung sets it to **0.5**. For the pair scope the draw is binary over
  `{0, seed_anchor_k=1}`; the N-object generalization (§7) widens it beyond Bernoulli.
- Inserted by an opt-in builder `with_mixed_anchor_rung(schedule)` mirroring
  `with_pair_anchored_rung` / `with_solo_box_rung`, gated by a new `--mixed-anchor` CLI flag.
  `DEFAULT_LADDER` is untouched, so the default schedule stays byte-identical.
- `pair-box` is **kept** after it as the graduation check that the bridge held.

Promotion uses the same `valid_placed` gate; on the mixed rung the metric averages over both
k=1 and k=0 episodes, so promotion ⇒ the agent is solving the empty-start episodes too (it
already solves k=1 trivially).

### 4.4 Validation guards

- `validate_ladder`: if `anchor_prob is not None` it must be in `[0, 1]`, and the stage must
  set `anchor_layout_path` (a mixed rung needs a witness) and `max_objects >= 2` (room for
  both a k=1 and a k=0 draw). `seed_anchor_k` on a mixed rung is the *max* k the mixture may
  draw (here 1); keep the existing `0 <= seed_anchor_k < max_objects` guard.
- Env `_reset_state`: existing per-episode guards already cover the drawn k.

## 5. Components & boundaries

| Unit | Responsibility | Depends on |
|---|---|---|
| `HangarFitEnv.reset` / `_reset_state` | honor a per-episode `seed_anchor_k` override | `DifficultyConfig`, witness poses |
| `EpisodeStart` record | carry `(requested_ids, seed_anchor_k)` from sampler → reset | — |
| mixed sampler (`sample_request` variant) | draw ids + (for mixed rungs) k from one seeded stream | `stage_rng` |
| `_EnvWorker.reset` / `.step` | pass `EpisodeStart` fields into `env.reset` | sampler, env |
| `collect_rollout` (single-env) | same, on episode boundary | sampler, env |
| `DifficultyConfig.anchor_prob` + `with_mixed_anchor_rung` + `--mixed-anchor` | define + insert the rung | curriculum, train CLI |
| `validate_ladder` | reject misconfigured mixed rungs | — |

No vec-protocol change: workers still receive only `reset`/`step`/`close`; the mixture
probability is constant for the rung, baked into the worker's sampler at build time.

## 6. Testing (TDD)

Write tests first, all under `tests/ml/`:

1. **Env override** — `reset(seed_anchor_k=0)` on a stage whose `difficulty.seed_anchor_k=1`
   parks nothing; `reset(seed_anchor_k=1)` parks the prefix. `reset()` with no override is
   byte-identical to today (regression).
2. **EpisodeStart plumbing** — sampler returns the record; worker/collector pass it through;
   non-mixed rung returns `seed_anchor_k=None`.
3. **Mixed sampler determinism** — same `(seed, anchor_prob)` ⇒ identical `(ids, k)` sequence;
   Sync ≡ Subproc k-draw sequence byte-identical (equivalence unit, per the repo's
   torch-CPU-nondeterminism note — no checkpoint-hash comparison).
4. **Mixture statistics** — over many seeded draws at `anchor_prob=0.5`, the empirical k=1
   fraction is ~0.5 (with a tolerance), and at `0.0`/`1.0` it is all-k=0 / all-k=1.
5. **`validate_ladder`** — rejects `anchor_prob` outside `[0,1]`, and a mixed rung missing
   `anchor_layout_path`.
6. **Ladder construction** — `with_mixed_anchor_rung` inserts `pair-mixed` between
   `pair-anchored` and `pair-box`; `DEFAULT_LADDER` and the no-flag schedule are unchanged
   (byte-identity guard).
7. **`--mixed-anchor` CLI** — toggles the insertion; absent ⇒ identical schedule to today.

`ml-rl-guard` must pass (training reproducibility/seeding, knob default-neutrality,
validity = product checker, numeric/GAE guards). `ruff` + `mypy ml/` clean. Keep ≥ 1
non-slow test per new path (two-pass coverage).

## 7. Out of scope / follow-ups

- **Continuous ramp.** `anchor_prob` ramping per-iteration (true anneal) needs a per-iter
  parent→worker broadcast (`set_anchor_prob`) on both vec envs + a train-loop schedule. Build
  only if the fixed mixture underperforms.
- **Trio / N-object.** Generalize the mixture to `k ∈ {0,…,max-1}` with a 3-object witness
  (`witness_trio.yaml`, all prefixes valid) once the pair works.
- **Notch witness.** A mixed rung on the herrenteich notch hangar.

## 8. Determinism contract (explicit)

- `--mixed-anchor` absent and `anchor_prob = None` ⇒ byte-identical to pre-change output.
- With the rung active, the run stays reproducible for a fixed `(seed, schedule, device=cpu)`
  and `Sync ≡ Subproc` byte-identical, because the per-episode k is drawn from the existing
  per-worker `stage_rng` stream with a fixed draw order. CUDA remains non-deterministic by
  design (unchanged).
