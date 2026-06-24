# Ego-centric (relative) coordinate encoder — design

**Status:** Brainstorming output (design approved; pending spec review → implementation plan).

**Issue:** [#827](https://github.com/DocGerd/hangarfit/issues/827) — "ego-centric relative coordinate
encoder lever — ADR-0028 re-open trigger #2", under epic #607.

**Decision context:** [ADR-0028](../../adr/0028-learned-backend-train-to-mastery-resolved-negative.md)
banked the dense train-to-mastery program (epic #607) *resolved-negative* (PR #826, closing #736) and
named two falsifiable re-open triggers. This spec implements the structural change behind **trigger #2**.

---

## 1. Motivation

The five gate-run levers (#794 scaffold, #810 representation, #813 reward, #817 exploration,
#823 backplay) all KILLed at the same `vp≈0.333` *place-one-then-abstain* fixed point on the dense
`trio-notch` rung. The measure-first probe localized the wall to **cold-start drive-and-pack of the
marginal object into a sparse, clearance-invariant slot** (φ=1 cold-start completion `vp=0.000`,
P(triple)≈2e-3 flat-in-clearance).

Every object pose token currently carries an **absolute world pose** (`ml/encoding.py:213-262`):
`_norm_pos` maps each object's world `(x, y)` into `[-1, 1]` over the 24×48 m raster window and
`_token_row` writes `row[18..21] = (nx, ny, sinθ, cosθ)` — never re-centered on anything. The policy
must therefore re-learn "slot the marginal object into the gap" separately at every absolute
`(x, y, θ)` it can occupy. An **ego-centric (relative) frame** — expressing other objects' poses
relative to the active object being placed — makes that skill **position- and heading-invariant**,
the single structurally-untested representation axis ADR-0028 names.

**Why this is consistent with the action space (verified).** All eight movement primitives
(`S/L/R/T × fwd/rev`, `ml/encoding.py:26-35`) are **body-frame**: straight drives along the current
heading (`x += step·cosθ; y += step·sinθ`, `towplanner.py:159-160`), strafe is perpendicular, arcs
turn about a laterally-offset centre. PARK freezes the driven pose. No action names a world axis → a
**full SE(2) ego rotation** of the observation is consistent with how the policy acts (observation
frame ≡ action frame). The det-−1 convention (ADR-0002) governs part-polygon geometry, **not** the
kinematic integrator, so the ego rotation is a plain 2D rotation with no sign-flip trap.

**Distinct from the refuted #810 lever.** #810 (`--spatial-tokens`) changed how the **raster
occupancy map** (`RASTER_CHANNELS=7`, a separate tensor) is pooled/attended. This lever changes the
**coordinate frame of the per-object pose tokens** — a different axis. The raster is untouched.

---

## 2. Scope (Option A — encoder + cheap gate first)

**In scope:**
- The ego-centric **augment** encoder change behind a default-neutral flag (`--relative-encoder`).
- `SCHEMA_VERSION` bump + canary re-baseline.
- Gate: `trio-notch` ladder, OFF (control) vs ON (treatment), 2 seeds, GPU — the same protocol as the
  five prior levers (apples-to-apples).

**Out of scope (YAGNI / deferred):**
- **Approach C** (pairwise SE(2) attention-bias in `policy.py`) — escalation only, if this lever shows
  partial-but-insufficient signal.
- **Reach-rate-vs-RR-MC harness** (extend #711) — the real ADR-0028 trigger-#1 criterion; runs as a
  follow-up **only if** the trio-notch gate shows promise. Trio-notch is the fail-fast proxy.
- **Action-space changes** — unnecessary; body-frame actions are already ego-compatible.
- **Replace / pure-ego frames** — rejected in favour of augment (see §3).

---

## 3. Design decisions

| Decision | Choice | Rationale |
|---|---|---|
| Relative frame | **Ego-centric, active-object-anchored, full SE(2)** | Body-frame actions make rotation free and strictly better; invariance to active `(x,y,θ)` is exactly the cold-start wall. |
| Token treatment | **Augment** — keep absolute `row[18..21]`, **add** relative cols | Information-preserving, lowest-variance probe; network learns which frame to use. (Replace/pure-ego risk losing absolute notch-alignment signal.) |
| Locus | `EncoderConfig` (writes cols) **+** `policy_kwargs` (sizes `token_proj`) | Augment grows `TOKEN_DIM`, so `token_proj`'s input dim grows → a network-shape change (the #810 template), not purely encoder-only. |
| `TOKEN_DIM` | 24 (OFF) / **28** (ON) | Four new columns; cols 22,23 stay reserved (region_side / seq_order). |
| Schema | `SCHEMA_VERSION` = **2** (ON) / 1 (OFF) | OFF byte-identical (stamps 1, same as today); a checkpoint's frame is unambiguous. |

**Accepted caveats (documented, not blockers):**
- **C1 — network-shape change.** Unlike the clean encoder-only path, augment touches
  `policy.token_proj` input dim → a `state_dict`-shape change, flag persisted in `policy_kwargs`,
  mismatched `--load` raises (the `policy.py:466-471` pattern). OFF remains byte-identical.
- **C2 — no masquerade defeat.** Absolute coords remain, so a BC/DAgger policy could still memorize
  witness world-coords. Acceptable here (the trio-notch gate is RL-from-scratch, no imitation), but a
  caveat for any future trigger-#1 use that pairs this encoder with imitation.

---

## 4. Detailed encoding (the ON path)

Four new per-token columns `row[24..27]`, expressing object `i`'s pose **in the active object's body
frame** (`θ_a = radians(heading_deg_active)`):

```
Δx = x_i − x_active ;  Δy = y_i − y_active        # world-frame delta
fwd_i   =  Δx·sinθ_a + Δy·cosθ_a                   # forward axis  = (sinθ_a, cosθ_a)
right_i =  Δx·cosθ_a − Δy·sinθ_a                   # right axis    = (cosθ_a, −sinθ_a)
Δθ      = θ_i − θ_active
row[24], row[25] = fwd_i / pos_ref_m, right_i / pos_ref_m     # NOT window-normalized
row[26], row[27] = sin(Δθ), cos(Δθ)
```

- **Basis** `forward=(sinθ_a, cosθ_a)`, `right=(cosθ_a, −sinθ_a)` (world x,y) is the integrator's own
  forward axis for the `S` primitive — keeps observation and dynamics in one frame.
- **Normalization** uses `pos_ref_m` (=20.0, already the dimension/turn-radius scale), **not** the
  48 m window — intra-hangar deltas are small and window-normalizing would crush their range.
- **Active object self-relative** = `(0, 0, sin0, cos0) = (0, 0, 0, 1)`.
- **Unplaced objects** (`pose=None`) → `row[24..27] = 0` (as for the absolute cols).
- **Absolute cols `row[18..21]`** are written **identically** in both modes (augment, not replace).

> **Basis handedness (preempting an invariant-guard false-positive).** The `(forward, right)` basis has
> **determinant −1** — it is the project's compass-convention frame (heading from +y, CW-positive; "right
> of north" = east = +x), the same convention the kinematic integrator uses. This is *not* the ADR-0002
> det-−1 part-polygon trap and is *not* a bug: the relative encoding is invariant under proper rigid scene
> motions (**SE(2)**) because the basis and the deltas **co-rotate with the active object** —
> `(fwd_i, right_i)` depends only on `|Δ|` and the angle of `Δ` relative to the active heading, both
> preserved by any rigid motion regardless of basis handedness. The det-−1 is a fixed coordinate choice,
> not a scene reflection (the encoding is intentionally *not* invariant under scene reflections — a
> mirrored hangar is a different problem). The §6 invariance test must apply the *same* rotation
> convention to headings and positions.

---

## 5. Flag-gating, schema & checkpoint compat

- **Flag:** `--relative-encoder` (`ml/train.py`), `action="store_true"`, default off. Help text mirrors
  `--spatial-tokens`: "opt-in ego-centric augment encoder (trigger #2); default off = byte-identical;
  a deliberate representation re-baseline when on; persisted in the checkpoint."
- **Threading:** sets `EncoderConfig.ego_centric=True` (encoder writes the cols + stamps schema 2)
  **and** passes `relative_encoder=True` into `policy_kwargs` (so `token_proj=Linear(28, d_model)`).
  Only `True` is ever threaded → absent key ⇒ default `False` ⇒ byte-identical OFF (the #810 pattern,
  `train.py:1187-1196`).
- **Checkpoint:** `policy_kwargs` is persisted (`train.py:499-501`); a conflicting `--load` raises
  (`policy.py:466-471`). The encoder flag must also be reconstructed for inference/eval consumers
  (`ml/eval.py`, ONNX export) — the loaded `policy_kwargs` is the source of truth.
- **Schema stamp:** `encode()`/`encode_dynamic()` stamp `2 if config.ego_centric else 1`;
  `vector_env.py:238` forwards `obs.schema_version` verbatim (already correct).

---

## 6. Determinism / canary re-baseline (TDD)

**Re-baseline (OFF path must stay byte-identical; ON path is the new contract):**
- `tests/ml/test_encoding.py::test_schema_version_and_dims_constants` — `TOKEN_DIM`/`SCHEMA_VERSION`
  now config-dependent; assert OFF=(24,1), ON=(28,2).
- `::test_tokens_status_type_pose_and_padding` — value + shape re-baseline for the ON path.
- `::test_encode_full_shapes_and_meta` — schema/shape under the flag.

**New tests (write first, RED → GREEN):**
1. **Transform correctness** — a hand-built 2-object layout with known poses → hand-computed
   `(fwd, right, sinΔθ, cosΔθ)` for the parked object in the active frame.
2. **SE(2) invariance property** — apply a random rigid transform (translate + rotate) to the *whole*
   layout including the active object → `row[24..27]` for every token is **unchanged** (within tol),
   while `row[18..21]` (absolute) **change**. This is the lever's defining property.
3. **OFF byte-identity** — `encode(ego_off)` bit-identical to the pre-change golden; `TOKEN_DIM==24`,
   `schema==1`.
4. **Active self-relative** = `(0,0,0,1)`; **unplaced** relative cols = 0.
5. **Policy round-trip** — `to_batch` + `forward` accept `TOKEN_DIM=28` when the policy is built with
   `relative_encoder=True`; shape mismatch raises a clear error on `--load` against an OFF checkpoint.

**Guards:** `ml-rl-guard` (4c-ii default-neutrality: OFF byte-identical; the new knob is neutral by
default) + `determinism-guard` (training reproducibility under the flag).

---

## 7. Files touched

| File | Change |
|---|---|
| `ml/encoding.py` | `EncoderConfig.ego_centric` field; `_token_row` writes `row[24..27]`; `TOKEN_DIM`/`SCHEMA` config-dependent; a `_norm_delta` helper. |
| `ml/policy.py` | `token_proj` input dim from a `relative_encoder` kwarg; persisted in `policy_kwargs`. |
| `ml/train.py` | `--relative-encoder` argparse; thread to `EncoderConfig` + `policy_kwargs`. |
| `ml/vector_env.py` | ensure `EncoderConfig.ego_centric` reaches workers (schema forward already correct). |
| `ml/eval.py`, ONNX export | reconstruct the encoder flag from loaded `policy_kwargs`. |
| `tests/ml/test_encoding.py`, `test_policy.py` | re-baseline + new tests (§6). |
| `docs/architecture/ml-observation-schema.md` | document schema 2 / the four ego columns. |
| `CHANGELOG.md` | `[Unreleased]` entry. |
| `ml/README.md` | note the opt-in flag under the 4c-ii knob table. |

---

## 8. Gate methodology (pre-registered)

- **Rung:** `trio-notch` ladder (the dense rung where all five levers KILLed).
- **Arms:** OFF (control) vs `--relative-encoder` (treatment), **2 seeds**, GPU.
- **Metric:** windowed-final valid-placed `vp` (the honest per-iteration `valid_placed`, the #742/#743
  gate metric), plus first-park `fp`.
- **WIN:** breaks the `vp≈0.333` place-one-then-abstain fixed point / reaches competency
  (`vp` materially > 0.333, ideally → mastery tier).
- **KILL:** `vp` stays ≈0.333 → the coordinate frame is *not* the wall; record as the 6th refuted lever
  in `ml/README.md` + auto-memory, and the encoder stays opt-in infra (do not re-run on notch).
- **Confound watch:** check `epochs_run` parity between arms (the #816 lesson) and that the OFF arm
  reproduces the established control baseline before trusting the contrast.

**Re-open linkage.** A trio-notch WIN is necessary but not sufficient: the real ADR-0028 trigger-#1
criterion is **reach-rate beating RR-MC on witness-absent scenarios**. That measurement (extend #711)
is the follow-up gated on a promising trio-notch result.
