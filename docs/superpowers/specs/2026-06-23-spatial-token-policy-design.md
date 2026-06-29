# Spatial-token cross-attention policy — fixing the spatially-blind global pool (#736 lever)

**Status:** Draft (design under review)
**Date:** 2026-06-23
**Issue:** #809 (impl). Epic #607 (learned backend); tracking #736 (training-improvement backlog).
**Scope:** `ml/policy.py` + `tests/ml/` **only**. No `ml/encoding.py`, no `SCHEMA_VERSION` bump, no `#752` wire-format change, no scene/v2 contact. `ml-rl-guard` is the reviewer.
**Builds on:** the policy architecture (sub-project #3, spec `2026-06-16-learned-backend-policy-architecture-design.md`) and the observation tensorizer (sub-project #2). Selected by a 3-variant adversarial design panel (2026-06-23).

---

## 1. The flaw this fixes

`HangarFitPolicy.forward()` (`ml/policy.py`) runs a 3×stride-2 CNN over the 7-channel
occupancy raster `(B, 7, 192, 96)` → feature map `(B, 64, 24, 12)`, then
**`AdaptiveAvgPool2d(1)` global-average-pools it to a single `(B, 64)` vector `g`**,
projects it to `d_model`, and **broadcasts that one vector to every object token**
before the transformer. So the only spatial signal reaching the decision heads is a
scalar *how full is the hangar*, never *where the free gaps are*. The transformer reasons
over per-object **tokens** (each carrying its own pose `nx, ny, sinθ, cosθ` + dims) fused
with that broadcast global vector — it must infer free space by mentally rasterizing object
poses.

This spatial blindness is invisible on easy rungs (1–2 objects, acres of room) but is the
**wall on dense rungs**: a 3rd object must thread a specific gap near the office notch.
It fits the observed trio-notch plateau (dense-rung valid-placement `vp` stuck ~0.33) and
both failure modes — piling into an unseen occupied cell, and place-nothing-new
(no confident gap → never commits).

## 2. Goals & non-goals

**Goals**
- Give the policy a **spatial** read of the raster: object tokens **cross-attend to free-space
  cells** instead of receiving one broadcast global summary.
- **Flag-gated, default-neutral.** With the flag off, the net is bit-identical to today
  (0 new params, identical module-registration order) so the existing equivalence/determinism
  canaries need no re-baseline.
- **Critic too.** The value head must see WHERE, not only the indicted global mean.
- Tests: shape contract on the new sequence path; flag-off byte-identity vs current
  `HangarFitPolicy`; flag-on finite + reproducible; a short PPO smoke proving the on-path
  trains without NaN.

**Non-goals (deferred to a supervised follow-up)**
- The **full trio-notch mastery training run** — pre-registered WIN: dense-rung `vp` clears
  the ~0.33 ceiling. Out of scope for the implementation PR; it is a multi-hour GPU run.
- Any **1 m (`48×24`) resolution escalation** — pre-registered as the *next* lever, taken
  only if the 2 m tap's reach-rate stalls (§7).
- Any `ml/encoding.py` / observation-schema change.

## 3. Why spatial tokens (and not the alternatives)

A 3-variant adversarial panel (2026-06-23) scored each candidate then tried to refute it.

| Variant | Leverage holds after refutation? | Fatal flaws | Schema impact |
|---|---|---|---|
| **Spatial-token cross-attention** | **yes** | **none found** | none |
| Per-object grid-sample | **no** (refuted) | deep 2 m/cell map + ~15–30 m receptive field can't resolve a ~1-plane-width gap; needs a *different, bigger* shallow-map design; critic stays blind; zero-init gate has zero gradient | none |
| Numpy free-space rays | narrowly | senses current pose not post-action pose; critic gets it ~1/N diluted; between-ray false-blocked teaches avoidance; new NaN-token surface | `SCHEMA_VERSION 1→2` |

Spatial tokens is the only variant that **removes the pool from the decision path** rather
than working around it, and the only one the skeptic could not break. Full panel detail lives
in the issue #809 thread / session transcript.

## 4. Architecture — `ml/policy.py`

A new constructor flag selects the path. `HangarFitPolicy.__init__` gains
`spatial_tokens: bool = False`.

### 4.1 Flag OFF — bit-identical to today
Build today's exact modules and run today's exact `forward()`:
`self.cnn = nn.Sequential(*convs, nn.AdaptiveAvgPool2d(1), nn.Flatten())`, `cnn_proj`,
`token_proj`, `fuse`, `encoder`, `kind_head`, `mag_head`, `value_head` unchanged. **No new
parameters are registered**, and module-registration order is unchanged, so the
construction-RNG stream and every `state_dict` key match today. This is the default.

### 4.2 Flag ON — spatial tokens
`__init__` instead builds:
- `self.cnn_backbone = nn.Sequential(*convs)` — keeps the feature **map** `(B, 64, 24, 12)`
  (drops the pool+flatten).
- `self.spatial_proj = nn.Linear(cnn_channels[-1], d_model)` — projects each spatial cell to D.
- A **fixed (non-learned) sin/cos 2D positional encoding** for the `24×12` grid, registered as
  a non-persistent buffer (zero parameters). *Chosen over a learned PE specifically so the
  on-branch adds the minimum parameter surface and introduces no extra construction-RNG draw;
  a learned PE would also be valid but is not needed for v1.*
- `cnn_proj`, `token_proj`, `fuse`, `encoder`, `kind_head`, `mag_head` as today.
- `self.value_head` widened to accept `cat(pooled_obj, g, pooled_spatial)` — input
  `3 * d_model` instead of `2 * d_model` (§4.4).

### 4.3 `forward()` data flow (flag ON)
```
feat   = cnn_backbone(raster)                       # (B, 64, 24, 12)
g      = cnn_proj(feat.mean(dim=(2, 3)))            # (B, D) — == old AdaptiveAvgPool2d(1)+Flatten, exactly
sp     = spatial_proj(feat.flatten(2).mT) + pos_2d  # (B, 288, D) spatial tokens (288 = 24*12)
tok    = token_proj(tokens)                         # (B, 16, D)
g_b    = g.unsqueeze(1).expand(-1, 16, -1)
fused_obj = fuse(cat([tok, g_b], dim=-1))           # (B, 16, D) — object stream unchanged
seq    = cat([fused_obj, sp], dim=1)                # (B, 304, D)
pad    = cat([~token_mask, zeros(B, 288, bool)], 1) # spatial tokens always valid
emb    = encoder(seq, src_key_padding_mask=pad)     # (B, 304, D)
emb_obj = emb[:, :16, :]                            # object rows only
active_emb = emb_obj.gather(1, active_index…)        # (B, D) — heads consume this
pooled_obj  = masked_mean(emb_obj, token_mask)       # (B, D) — object-only (as today)
pooled_spat = emb[:, 16:, :].mean(dim=1)             # (B, D) — spatial summary for the critic
kind/mag heads on active_emb                         # unchanged
value = value_head(cat([pooled_obj, g, pooled_spat]))# (B,) — critic now sees WHERE
```
`g = feat.mean(dim=(2,3))` is provably equal to `AdaptiveAvgPool2d(1)` + `Flatten` over the
same `feat`, so the global pathway is preserved exactly; the spatial pathway is purely additive.

`feat.flatten(2)` is **row-major** over the `24×12` grid (cell index `= row*12 + col`); the
fixed `pos_2d` PE **must** be built in that same order so each spatial token's positional code
matches the cell it carries. A shape/order test pins this (§8).

### 4.4 Critic-summary fold-in (decision: included in v1)
All three panel reviewers independently flagged that `value_head(cat(pooled, g))` leaves the
**critic globally blind** — `g` is the indicted global mean, and the actor-only fix reaches the
critic only indirectly. The notch wall is partly a marginal-commitment **economics** problem,
which the critic gates, so a WHERE-blind critic under-credits the threading move. v1 therefore
feeds `pooled_spatial` (the masked-mean of the spatial-token encoder outputs) into the on-branch
value head. *Alternative considered: defer to a follow-up lever — rejected because it is ~3 LOC
behind the same flag and the critic is where the economics signal must land.*

## 5. Determinism & guard contract (`ml-rl-guard`)

- **Reproducibility/seeding.** No new RNG in `forward()`. The fixed sin/cos PE adds no draw.
  Within-build bit-identity holds; cross-process torch-CPU nondeterminism is pre-existing and
  unchanged.
- **4c-ii default-neutrality.** Flag off registers **zero** new params and runs today's exact
  forward → byte-identical. The byte-identity oracle (Sync == Subproc) and the fixed-action
  reward-stream diff need **no re-baseline** for the default.
- **Flag-on is a deliberate new-architecture re-baseline** (expected for a plateau lever). The
  flag is **persisted in the checkpoint and validated on load** — the two branches have
  different `state_dict` key-sets, so loading a flag-off checkpoint into a flag-on policy (or
  vice-versa) must raise loudly, not silently partial-load.
- **Numeric silent-failure.** A NaN in `feat` would now propagate through 288 tokens; the
  existing advantage-finiteness assert in the PPO update backstops, and the spatial path adds no
  new division/log. The masked-mean `.clamp(min=1.0)` zero-guard stays on the **object** rows
  (the 288 spatial rows are excluded from `pooled_obj`, so they cannot mask-leak into the value
  semantics).
- **Validity = the product checker.** Untouched — this is observation-consumption plumbing, no
  reward / oracle change, so the #694 contract holds by construction.

## 6. Edge cases

- **Terminal-state forward (panel-flagged).** With 288 always-valid spatial rows, a fully-padded
  object row no longer NaNs — it attends to spatial tokens and returns a *finite-but-meaningless*
  embedding. `act()` already rejects `active_index < 0`, so sampling is safe. **Implementation
  obligation:** verify in `ml/ppo.py` that no `forward()` is taken over a terminal observation
  for value-bootstrap (the intrinsic-horizon bootstrap should use 0, not a forward); add a test
  pinning that contract. If a terminal value-forward *is* taken, gate it explicitly.
- **Padding mask shape.** `src_key_padding_mask` grows from `(B, 16)` to `(B, 304)`; the spatial
  block is all-valid (`False` = attend). The `active_index` gather and `pooled_obj` operate on
  `emb[:, :16]`, so they are unaffected by the appended rows.

## 7. Resolution: 2 m tap first, 1 m escalation pre-registered (decision)

The `24×12` feature map is **2 m per cell** (`192/24 = 8` rows, `96/12 = 8` cols, ×0.25 m).
A ~1-plane-width notch gap (~1.5–2 m) is at/below that pitch, so the 2 m tap resolves *which
2 m neighbourhood is free*, not *is this exact slot threadable*. The **diagnosed** failure,
though, is coverage-minimum / abandonment — the policy not *aiming* the 3rd object at the right
region — which the 2 m tap can plausibly fix. So:

- **v1 uses the 2 m (`24×12`) tap.** Cheapest; the rollout box is shapely-bound (~61%), so the
  ~19× sequence growth (16 → 304 tokens) is tolerable.
- **Pre-registered escalation:** a 1 m (`48×24`, ~1152-token, ~1300× attention term) tap, taken
  **only if** the trio-notch reach-rate stalls on the 2 m tap. Not built in v1.
- **Pre-registered WIN (follow-up training run):** dense-rung `vp` clears the ~0.33 ceiling.

## 8. Module structure & tests

- **Modify** `ml/policy.py` — the flag, the on-branch `__init__` modules, the on-branch
  `forward()`, the fixed sin/cos 2D PE helper, `import torch.nn.functional as F` if needed.
- **Modify** `tests/ml/test_policy.py`:
  - **Equivalence (canary-protecting):** a flag-off `HangarFitPolicy(spatial_tokens=False)`
    forward is byte-identical to the current net given the same seed/weights.
  - **Shape contract:** flag-on forward produces the documented `(B, ACTION_DIM)`,
    `(B, MAGNITUDE_DIM)`, `(B,)` outputs over the 304-length sequence; `PolicyOutput`
    asserts pass.
  - **`g`-equivalence:** `feat.mean(dim=(2,3))` equals the old `AdaptiveAvgPool2d(1)+Flatten`
    on a fixed `feat`.
  - **Determinism:** flag-on forward is finite and bit-identical across two calls with a fixed
    seed in `eval()`.
  - **Checkpoint-flag guard:** loading a flag-off `state_dict` into a flag-on policy raises.
  - **Terminal contract:** the §6 obligation — a test pinning that no terminal value-forward
    is taken (or that it is explicitly gated).
- **CHANGELOG.md** — a `[Unreleased]` entry only **if** the project conventionally logs `ml/`
  training knobs. `ml/` is dev/CI-only (never shipped in the wheel), so this may instead follow
  the dev-tooling no-entry policy; verify against recent `ml/` knob PRs at implementation time
  rather than assuming an entry is required.
- **Possibly modify** `ml/train.py` / the PPO config — thread a `--spatial-tokens` CLI flag to
  the policy constructor (default off, per 4c-ii). Kept minimal; the A/B is flag-on vs flag-off.

## 9. CLI / training-knob surface

A single new **default-neutral** knob `--spatial-tokens` (off by default), threaded from the
train CLI → `PPOConfig`/policy constructor → `HangarFitPolicy(spatial_tokens=…)`. Off reproduces
today bit-for-bit; on selects the new architecture. This matches the existing 4c-ii knob
convention (every knob off ⇒ byte-identical).

## 10. Open questions / risks

- **Does the 2 m tap actually lift abandonment?** Unknown until the follow-up training run; the
  design is explicitly staged so a stall escalates to the 1 m tap rather than re-opening the
  architecture choice.
- **Spatial tokens dominating attention** (288 keys vs 16 object queries) could wash out the
  object-token gradient. Mitigation if observed: scale or gate the spatial contribution; not
  pre-built in v1.
- **Critic widening** changes the value-head input width on the on-branch only; confirm the
  GAE/value-loss path in `ml/ppo.py` is agnostic to the value-head internals (it consumes the
  scalar value, so it should be).
