# Learned backend — Policy architecture (sub-project #3)

**Status:** Draft (design under review)
**Date:** 2026-06-16
**Scope:** Sub-project #3 of the learned-backend epic (#607) — the **policy network architecture ONLY**: a torch `nn.Module` (policy + value) that consumes the sub-project #2 `ObservationTensors` and emits a per-step action distribution + value, plus the pure discrete **action-space contract** it decodes into. **No PPO training loop, no curriculum schedule, no env rollouts** (→ sub-project #4).
**Builds on:** sub-project #1 (env + reward, #672) and sub-project #2 (observation tensorizer, #677 / spec `2026-06-16-learned-backend-observation-tensorizer-design.md`).
**Governing decomposition:** the cold-joint spec §3 (#3 = policy architecture + curriculum; #4 = training + eval). Per the network-only scoping decision (2026-06-16), the **curriculum schedule and PPO loop move to #4** — the env already exposes the `DifficultyConfig` knobs, and a schedule is untestable without a trainer to consume it.

---

## 1. Context & the action interface this must match

The built cold-joint env (#672) is an **autoregressive, per-step-primitive MDP** with a *fixed* object queue: each step the agent applies one `Primitive(kind, magnitude, gear)` or `Park` to the **active** object. The sub-project #2 tensorizer (#677) turns the env's `Observation` into `ObservationTensors` (a 7-channel raster, a `(16, 24)` token table + mask, an `active_index`, and a `(9,)` `legal_action_mask`).

> **Supersedes the stale epic text.** Epic #607's "policy architecture" paragraph describes a *selection head + coarse-pose head + feasibility head + deterministic refiner* — the **older proposer** the cold-joint spec §6 explicitly replaced. This design targets the **built per-step-primitive env**: no selection head (the queue is fixed), no coarse-pose/refiner. The policy acts on the **active object** and emits **one factored-discrete action per step**.

## 2. Goals & non-goals

**Goals**
- A pure **action-space contract** (`ml/action_space.py`, **no torch**) defining the factored-discrete action and decoding a sampled action into the exact `Primitive | Park` the env's `step()` consumes.
- A **policy + value `nn.Module`** (`ml/policy.py`) consuming `ObservationTensors` (batched) → `(kind_gear_logits, magnitude_bin_logits, value)`, with **hard action-masking** on movement legality.
- **Determinism**: a forward pass is bit-identical given weights + input (within-build canary).
- Tests: the action-space contract runs in normal CI (no torch); the network tests run wherever torch is installed (`importorskip`).

**Non-goals (deferred)**
- The **PPO training loop**, advantage estimation, optimizer, rollout collection → #4.
- The **curriculum schedule** (how `DifficultyConfig` ramps over training) → #4.
- **ONNX export, inference determinism contract, `solve --backend learned` wiring** → #5.
- A **dedicated torch CI job** + signed weights → #6.

## 3. Module structure

- **Create** `ml/action_space.py` — pure (numpy/stdlib, **no torch**). The factored-discrete action contract; reusable by the #4 trainer.
- **Create** `ml/policy.py` — the torch `nn.Module` + the `ObservationTensors → batched tensors` adapter.
- **Create** `tests/ml/test_action_space.py` (no torch; runs in CI) and `tests/ml/test_policy.py` (`pytest.importorskip("torch")`).
- **Modify** `pyproject.toml` — add the `[train]` optional-dependency extra (`torch`). **Modify** `ml/types.py` — retire the stale `geometry_oracle.bin_magnitude` docstring reference (that helper was never built; binning lives here now). **Modify** `CHANGELOG.md`.

## 4. Action space — `ml/action_space.py`

The action is **fully factored discrete**: a `(kind, gear)` index over the canonical 9 actions × a `K`-way magnitude bin.

**Single source of truth for the `(kind, gear)` order.** `ml/encoding.py` already defines `_CANONICAL_ACTIONS`, `ACTION_DIM` (9), and `PARK_INDEX` (8), and builds `legal_action_mask` in that order. `action_space` **reuses those constants** (imports them; the encoding module stays the owner) so the policy's kind-head index ↔ `legal_action_mask` index ↔ `decode()` are guaranteed aligned by construction.

**Magnitude bins** (`K = 5`, configurable). The env's `Primitive.magnitude` units depend on the motion (`ml/types.py`: *metres* for `S`/`T` and own-gear arcs; *radians of pivot* for a cart turn `L`/`R` at `turn_radius == 0`). So two decode tables:
- `TRANSLATION_BINS = (0.25, 0.5, 1.0, 2.0, 4.0)` metres — for `S`/`T` and own-gear `L`/`R`/`S` arcs (arc/segment length in metres).
- `PIVOT_BINS_DEG = (5.0, 15.0, 30.0, 45.0, 90.0)` degrees — for cart `L`/`R` pivots; decoded to **radians** (`math.radians`) because a cart pivot's magnitude is radians.

Because a bare `(kind, bin)` cannot distinguish a cart pivot from an own-gear arc (both are `L`/`R`), `decode()` takes the **active body's effective turn radius** — the same discriminator `legal_primitives` uses (`r == 0` ⇒ cart). The #4 rollout passes `active_body.effective_turn_radius_m()`.

```python
MAGNITUDE_DIM = 5  # == len(TRANSLATION_BINS) == len(PIVOT_BINS_DEG)

def decode(kind_gear_idx: int, mag_bin_idx: int, *, turn_radius_m: float) -> Primitive | Park:
    """Resolve a sampled factored action into the env's Primitive | Park.
    PARK_INDEX -> Park() (mag_bin_idx ignored). Otherwise (kind, gear) =
    _CANONICAL_ACTIONS[kind_gear_idx]; a cart pivot (kind in {'L','R'} and
    turn_radius_m == 0.0) -> magnitude = math.radians(PIVOT_BINS_DEG[mag_bin_idx]);
    everything else -> magnitude = TRANSLATION_BINS[mag_bin_idx] (metres)."""
```

The decode is **pure and deterministic**; it owns the bin→`Primitive.magnitude` mapping in exactly the units the env's `apply_primitive` expects.

## 5. Network — `ml/policy.py` (`HangarFitPolicy(nn.Module)`)

Consumes a **batched** `ObservationTensors`, acts on the **active object**:

```
raster (B,7,H,W) ──CNN──────────────────► g (B, D)              # conv stack + adaptive-avg-pool + flatten
tokens (B,16,24) ─Linear→concat(g per token)─► Transformer enc  # L self-attn layers, key_padding_mask = ~token_mask
                                              └► token_emb (B,16,D)
active_emb = gather(token_emb, active_index)        (B, D)
pooled     = masked_mean(token_emb, token_mask)     (B, D)
heads (MLP):
  kind head:  active_emb         → (B, 9)  logits, then masked_fill(~legal_action_mask, -inf)
  mag  head:  active_emb         → (B, K)  logits
  value head: concat(pooled, g)  → (B, 1)  scalar
return PolicyOutput(kind_gear_logits, magnitude_bin_logits, value)
```

- **Set-Transformer = plain multi-head self-attention.** With `≤16` tokens, `O(N²)` attention is trivial; ISAB/inducing points are unnecessary. **No positional encoding** → permutation-invariant over the object set (the `status` one-hot in the token already marks parked/active/unplaced, and `active_index` gathers the controlled object).
- **CNN context conditions the set.** The raster global vector `g` is concatenated to every token's projected features before the Transformer (simple, robust fusion; FiLM is an option but not v1).
- **`.act(obs)` convenience.** Samples a masked `(kind_gear, mag_bin)` from the (masked) categoricals and returns it with its log-prob and the decoded `Primitive | Park` (via `action_space.decode`). Used by the forward-pass tests now and the #4 rollout later. Sampling/log-prob/entropy helpers live here; the PPO *update* is #4.
- **Hyper-parameters** (`D`, `L`, heads, CNN channels) are constructor args with sensible defaults; tuned in #4.

`★ Insight ─────────────────────────────────────`
**Two kinds of "illegal" are handled in two different layers — by design.** *Movement-mode* illegality (an own-gear plane cannot strafe) is **hard-masked** on the kind logits via `legal_action_mask` → zero probability, because it's a hard fact about the body. *Collision* illegality (a legal primitive that penetrates a wall) stays **soft** — the policy may choose it and the env's graded-penetration reward punishes it. Hard-masking what's *physically impossible* while letting reward shape what's *merely bad* is exactly the cold-joint spec's gradient-toward-feasibility premise; collapsing both into one mechanism would either over-constrain exploration or remove the learning signal.
`─────────────────────────────────────────────────`

## 6. Observation → torch adapter
`to_batch(list[ObservationTensors]) -> dict[str, Tensor]` stacks the numpy fields into batched tensors (`raster`, `tokens`, `token_mask`, `active_index`, `legal_action_mask`). This is the **only** torch seam on the input side — `ml/encoding.py` stays torch-free numpy. A single-item convenience wraps a length-1 batch.

## 7. Determinism & the verifier relationship
- The forward pass is a deterministic function of (weights, input) in `eval()` mode (no dropout at inference). A test asserts **double-forward bit-identical** on fixed weights + seed — the learned-path **within-build** canary.
- The policy is **not** under ADR-0003 / `determinism-guard` (those guard `solver.py` / `towplanner.py`). The cross-machine = validity-only contract and ONNX export determinism are **sub-project #5** concerns.
- Safety is unchanged: nothing here is surfaced to a human; the deterministic verifier remains the final gate (sub-project #5 wiring).

## 8. Testing
**`tests/ml/test_action_space.py`** (no torch — runs in standard CI):
- `MAGNITUDE_DIM == len(TRANSLATION_BINS) == len(PIVOT_BINS_DEG)`; bin values.
- `decode(PARK_INDEX, anybin, turn_radius_m=…) -> Park` (bin ignored); a cart `L`/`R` (`turn_radius_m=0.0`) decodes to a `Primitive` whose magnitude is `math.radians(PIVOT_BINS_DEG[bin])`; an own-gear `L`/`R` (`turn_radius_m>0`) and any `S`/`T` decode to `TRANSLATION_BINS[bin]` metres; `kind`/`gear` match `_CANONICAL_ACTIONS`.
- index alignment: `action_space` reuses `encoding._CANONICAL_ACTIONS` / `ACTION_DIM` / `PARK_INDEX` (assert identity, so the contract can't drift).

**`tests/ml/test_policy.py`** (`pytest.importorskip("torch")` — runs where torch is installed; a dedicated torch CI job lands in #6):
- output shapes `(B,9)`, `(B,K)`, `(B,1)` for batched and single-item inputs (consistency).
- **masking**: illegal `(kind,gear)` slots are `-inf` logits → `0` probability after softmax; `.act()` only ever returns a legal `(kind,gear)` and a valid decoded `Primitive | Park`.
- determinism: double-forward in `eval()` → `torch.equal` on every output.
- gradients flow: a scalar loss over the outputs `.backward()` populates `.grad` on parameters.

## 9. Packaging
- `pyproject.toml`: add `[project.optional-dependencies] train = ["torch"]` — **contributor-only**, not in the default install, not in the wheel (`ml/` already excluded by `packages.find where=["src"]`). `[learned-infer]` (onnxruntime) is added later (#5).
- **CI**: `test_action_space.py` runs in the standard job (no torch). `test_policy.py` **skips** there (`importorskip`) and is covered by a **dedicated torch CI job added in sub-project #6** (alongside the extras + signed weights). Smallest CI impact now; the network is green locally and in the #6 job.

## 10. Workflow
File a GitHub issue *"#607 rung 4: policy network architecture (sub-project #3)"* (rung 1 = #670, rung 2 = #672, rung 3 = #676; Part of #607) before coding; branch off `develop` (after #677 merges, so `ml/encoding.py` is present); TDD; draft PR `Closes #<n>`; review arc (`code-reviewer` + `type-design-analyzer` for the new `PolicyOutput`/action types; **not** `geometry-invariant-guard`/`determinism-guard` — no geometry/solver/towplanner change); CHANGELOG entry.

## 11. Open questions (resolve in #4, not here)
- Final `D` / `L` / head sizes and CNN depth (tuned against training).
- Whether the magnitude head should be **conditioned on the chosen kind** (v1: independent `K`-way head, decoded per-kind) — revisit if training shows kind/magnitude coupling matters.
- Exact PPO hyper-parameters, advantage estimation, and the curriculum schedule (#4).
- Whether the value head benefits from the Reeds–Shepp closed-form distance as an auxiliary cost-to-go target (cold-joint spec §5) — a #4 experiment.
