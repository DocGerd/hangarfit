# Learned-backend ONNX export + `solve --backend learned` inference — design

- **Date:** 2026-06-17
- **Issue:** #706 (epic #607, sub-project #5)
- **Status:** Design (approved for planning)
- **Governing decisions:** [ADR-0027](../../adr/0027-learned-backend-determinism-scope.md) (proposer/verifier determinism scope split); the cold-joint env spec [`2026-06-12-learned-backend-cold-joint-rl-env-design.md`](2026-06-12-learned-backend-cold-joint-rl-env-design.md).

## 1. Goal & scope

Make `solve --backend learned` **actually produce a `SolveResult`** from a trained
checkpoint, with **no torch in the user-facing inference path**, behind the unchanged
deterministic verifier. Concretely:

1. Export a trained `HangarFitPolicy` (`ml/policy.py`) to ONNX.
2. Run inference with onnxruntime + numpy (no torch), driving the existing cold-joint
   env (`ml/env.py`) rollout to a terminal layout.
3. Return the same `SolveResult` shape (poses + a tow `MovesPlan`) the deterministic
   `solve()` returns, so every downstream consumer (render / `view` / `--write-yaml`)
   stays backend-agnostic.

**Weights-agnostic by design.** Export round-trip, onnxruntime wiring, the
`SolveResult` adapter, and the clean fallbacks are all testable **today** against the
tiny untrained checkpoint the eval benchmark already uses. Model *quality* gates the
acceptance verdict (#7 / #698), **not** this rung's plumbing. An untrained policy that
proposes garbage yields an honest no-layout `SolveResult`, never a traceback — because
the verifier is the final gate.

### Out of scope (deferred to later rungs)

- Wheel-shippable inference (relocating the torch-free inference core into
  `src/hangarfit/`), a CI lane that installs the extras, and signed Release-asset
  weights → **#6 (packaging)**.
- The reach-not-beat *acceptance run* against a converged model → **#7** / #698.
- Recording richer per-leg tow metadata beyond what `MovesPlan`/`Move` already carry.

## 2. Current state (verified against the code)

- **Seam is live.** `cli.py` (`--backend {rrmc,learned}`, default `rrmc`) dispatches to
  `learned.solve_learned(scenario, *, budget_s, alternatives, seed, plan_paths)`
  (`src/hangarfit/learned.py`), which today always raises
  `LearnedBackendUnavailableError`. Landed in #670.
- **Policy forward is a clean per-step function.** `HangarFitPolicy.act()` runs **one**
  `forward()` per env step → `argmax` over the `kind_gear` (9) and `magnitude` (5) heads
  → `action_space.decode(kind, mag, turn_radius)` → a `Primitive | Park`. No in-`act()`
  refiner, no autoregressive sub-loop. The value head is the critic — **unused at
  inference**.
- **Dependency direction is one-way `ml → src`.** Nothing under `src/hangarfit/` imports
  `ml.*`. `ml/env.py`, `ml/encoding.py`, `ml/geometry_oracle.py`, `ml/types.py`,
  `ml/action_space.py` are **torch-free** (numpy at most). Only `ml/policy.py`,
  `ml/ppo.py`, `ml/train.py` import torch.
- **`ml/` is excluded from the wheel** (`[tool.setuptools.packages.find] where=["src"]`).
  The only optional extra today is `train = ["torch"]`; there is no `learned-infer`/
  onnxruntime.
- **The motion vocabulary already matches.** `ml.types.Primitive(kind: SegmentKind,
  magnitude: float, gear: ±1)` and `towplanner.Segment(kind: SegmentKind, length_m:
  float, gear: ±1)` are the same vocabulary (S/L/R/T; metres, or **radians** for a cart
  pivot at `turn_radius==0` — both interpret it identically). `ml.geometry_oracle.
  apply_primitive` already "builds a single-segment `DubinsArc` and integrates it with
  the same `pose_at`/`sample` machinery the renderers and towplanner consume."

## 3. Architecture

```
solve --backend learned                       (CLI, unchanged dispatch)
        │
        ▼
src/hangarfit/learned.py  solve_learned(...)   (WHEEL-SHIPPED, thin seam)
        │  lazy import ml.infer; clean error if ml / onnxruntime / weights absent
        ▼
ml/infer.py  solve_learned_impl(...)           (torch-free runtime: onnxruntime + numpy)
        ├── env_from_scenario(scenario)  ───►  HangarFitEnv          (ml/env.py)
        ├── OrtPolicy(onnx_path)         ───►  onnxruntime session    (forward only)
        ├── rollout loop: encode → forward → argmax → decode → step  (records primitives)
        ├── build MovesPlan: Primitive[] → DubinsArc per object       (towplanner types)
        └── verify: collisions.check + Caddy egress                  (FINAL GATE)
        ▼
SolveResult(status, layouts, diagnostics, plans)

ml/export.py  export_onnx(policy, path)        (torch; contributor-only)
ml/train.py   --save-onnx PATH                 (export after training)
```

### 3.1 Seam — `src/hangarfit/learned.py` (wheel-shipped, thin)

`solve_learned` gains a `weights_path: str | Path | None = None` keyword (the rest of the
signature is unchanged). It lazy-imports `ml.infer` inside the function body (so importing
`hangarfit.learned` never pulls `ml`/onnxruntime) and delegates. The CLI `solve` subparser
gains a `--weights PATH` flag, passed through on the `--backend learned` branch; there is
**no default weights path** in this rung (a default lookup over signed Release-asset
weights is #6), so `--backend learned` without `--weights` is a clean
`LearnedBackendUnavailableError`. `solve_learned` raises `LearnedBackendUnavailableError`
with a specific, actionable message when:

- `weights_path` is `None` → *"pass `--weights PATH` (no default weights ship yet; see #6)."*

- `ml` is not importable (a bare pip-installed wheel — `ml/` isn't shipped) →
  *"the learned backend requires a source checkout (the `ml/` package); wheel
  distribution lands in #6."*
- `onnxruntime` is not importable → *"install the `[learned-infer]` extra."*
- the weights file is absent / unreadable → name the path looked up.

This keeps torch/onnxruntime/`ml` out of the wheel's hard dependencies. Making the
inference path work from a bare wheel is **#6's** packaging job (it decides whether to
relocate the torch-free inference core into `src/hangarfit/`).

### 3.2 Inference impl — `ml/infer.py` (NEW)

Runtime deps: `onnxruntime` + `numpy` only (no torch). Public entry:

```python
def solve_learned_impl(
    scenario: Scenario, *, weights_path: str | Path,
    budget_s: float, alternatives: int, seed: int | None, plan_paths: bool,
) -> SolveResult: ...
```

- **`env_from_scenario(scenario, difficulty)`** — adapt a production
  `hangarfit.models.Scenario` into the env's constructor args (`hangar`, `fleet`,
  `requested_ids`, `ground_objects`, `fixed_placements`). Uses a generous default
  `DifficultyConfig` for production: `max_objects=None` (drive the whole requested set),
  with `per_object_step_budget` / `total_step_budget` large enough that the budget is a
  safety stop, not a curriculum cap.
- **`OrtPolicy`** — a thin wrapper over an onnxruntime `InferenceSession` (pinned
  `CPUExecutionProvider`, single-threaded for determinism per ADR-0027 tier-1). Method
  `forward(obs_tensors) -> (kind_gear_logits, magnitude_bin_logits)` returns numpy
  arrays; `act(obs, turn_radius)` does numpy `argmax` + `action_space.decode` (mirrors
  `policy.act(deterministic=True)` without torch).
- **Rollout** — mirrors `ml/eval.py::policy_reach`: `obs = env.reset()`; while not done and
  `obs.active is not None`: `encode(obs)` → `OrtPolicy.act` → `env.step(action)`. Accumulate,
  **per active object**, its spawn `start` pose and the ordered list of emitted
  `Primitive`s (the env grades-then-discards swept paths, so the driver must record them).
- **`MovesPlan` construction** — for each driven object: `DubinsArc(start=spawn_pose,
  end=parked_pose, turn_radius_m=body.effective_turn_radius_m(), segments=tuple(
  Segment(kind=p.kind, length_m=p.magnitude, gear=p.gear) for p in primitives))`, wrapped
  in `Move(plane_id, target_slot=parked_pose, path=arc)`. **Edge case:** an object parked
  with **zero** primitives → `DubinsArc.segments` would be empty (which `__post_init__`
  rejects) → emit `Move(..., path=None)` (the established best-effort `None`-path idiom).
- **Verification & status** — build the terminal `Layout` (from the env's frozen set),
  run the deterministic `collisions.check` + Caddy egress gate.
  - valid → `status="found"`, `layouts=(layout,)`,
    `plans=(MovesPlan,)` when `plan_paths` else `(None,)`.
  - invalid / budget-exhausted before a full park set → a no-layout status
    (`"exhausted_budget"`), `layouts=()`, `plans=()`. **Never** raise on a bad proposal —
    the verifier rejecting a layout is a normal outcome, identical tier to RR-MC failing
    to find one.
  - `alternatives > 1`: this rung emits a **single** layout (argmax rollout is
    deterministic); diverse sampling is #696. Note in diagnostics if `alternatives > 1`
    was requested; do not error.

### 3.3 ONNX export — `ml/export.py` (NEW) + `ml/train.py --save-onnx`

- `export_onnx(policy, path, *, opset=17)`: wrap `forward` in a thin positional-input
  `nn.Module` that takes the five tensors (`raster, tokens, token_mask, active_index,
  legal_action_mask`) and returns the two logit tensors (**drops the value head**).
  `torch.onnx.export` with dynamic axes for batch `B` and token count `N`.
- I/O contract (frozen here so the parity test and the numpy adapter agree):
  - inputs: `raster (B,C,H,W) f32`, `tokens (B,N,24) f32`, `token_mask (B,N) bool`,
    `active_index (B,) i64`, `legal_action_mask (B,9) bool`.
  - outputs: `kind_gear_logits (B,9) f32`, `magnitude_bin_logits (B,5) f32`.
  - The `-inf` legal-mask `masked_fill` stays **inside** the exported graph, so the
    inference `argmax` matches the torch `act()` argmax exactly (clean parity test).
- `train.py --save-onnx PATH` exports alongside the existing `torch.save(state_dict)`.
- **Known implementation risk:** `nn.TransformerEncoder` with `src_key_padding_mask`
  can be opset-finicky to export. The export-parity test (3.5a) is the tripwire; the
  remedy is bumping the opset or, if needed, swapping the encoder for an explicit
  attention block with the same weights. The numpy adapter never has to know — it only
  consumes the exported graph.

### 3.4 Packaging — `pyproject.toml`

Add `learned-infer = ["onnxruntime"]` under `[project.optional-dependencies]`. Version
pin + lockfile regeneration tracked here; CI wiring (a lane that installs the extra) is
**#6**.

### 3.5 Tests (TDD)

All ml/-side tests live under `tests/ml/`. torch / onnxruntime are absent from CI's
`[dev]` install, so the relevant tests `importorskip` there (the established pattern) —
real coverage arrives with #6's CI lane.

a. **Export parity** [needs torch]: build a fixed-seed `HangarFitPolicy`, export to a
   temp ONNX, and assert `argmax` of each head matches between the torch `forward` and
   the onnxruntime session on a fixed observation batch (logits may differ sub-ULP;
   **argmax must match** — that is the inference-relevant invariant).
b. **DubinsArc endpoint parity** [torch-free]: given a recorded `Primitive` sequence and
   the env's accumulated parked pose, assert `DubinsArc(...).pose_at(length) ≈
   parked_pose` within tolerance (guards the 1:1 segment mapping).
c. **`SolveResult` shape & validity** [needs onnxruntime + a checkpoint]: a learned solve
   returns a well-formed `SolveResult` (status/layouts/plans index-aligned per the
   `__post_init__` invariants), and any returned layout passes `collisions.check`.
d. **ADR-0027 tier-1 canary** [needs onnxruntime]: two within-build runs (same weights +
   seed + pinned CPU EP) produce bit-identical `SolveResult`s.
e. **Clean fallbacks**: missing onnxruntime / missing weights / `ml` unimportable each
   raise `LearnedBackendUnavailableError` with an actionable message; the CLI surfaces
   exit-2 (extends the existing seam clean-error test).
f. **Verifier-rejects path**: a policy proposing an invalid terminal layout yields a
   no-layout status, not an exception.

## 4. Determinism (ADR-0027)

The verifier (`collisions.check` + towplanner) stays strict and `determinism-guard`-bound,
unchanged. The learned proposer is **outside** ADR-0003 / `determinism-guard`. This rung
lands the **tier-1** canary (within-build bit-identity: fixed weights + seed + pinned CPU
EP). Tier-2 (cross-machine validity-only) is recorded by ADR-0027 and exercised once a
shared trained checkpoint exists (#7). `ml.infer` is **not** added to `determinism-guard`'s
remit.

## 5. Risks & mitigations

| Risk | Mitigation |
|---|---|
| TransformerEncoder mask export breaks under opset 17 | Export-parity test (3.5a) is the tripwire; bump opset or use an explicit attention block. |
| Untrained checkpoint proposes only invalid layouts | Expected — verifier yields a no-layout `SolveResult`; tests assert the *shape*/*fallback*, not reachability. |
| Zero-primitive object (immediate Park) → empty `DubinsArc.segments` | Emit `Move(path=None)` (best-effort idiom). |
| `alternatives > 1` requested | Emit one layout, note in diagnostics, do not error; diverse sampling is #696. |
| Inference can't run from a bare wheel (`ml/` not shipped) | Out of scope by design — clean error; wheel distribution is #6. |

## 6. Acceptance criteria

Mirrors issue #706:

- [ ] `ml/export.py` exports a `HangarFitPolicy` to ONNX; `train.py --save-onnx PATH` writes it.
- [ ] Export-parity test passes (argmax match, both heads).
- [ ] `solve --backend learned --weights PATH` returns a `SolveResult`; a valid terminal
      layout passes `collisions.check` + egress; `--render-paths`/`view` consume the emitted
      `DubinsArc` tow plan.
- [ ] DubinsArc endpoint ≈ env parked pose (integration-parity canary).
- [ ] ADR-0027 tier-1 within-build double-run is bit-identical.
- [ ] Clean fallbacks (no onnxruntime / weights / `ml`) → `LearnedBackendUnavailableError`,
      CLI exit-2.
- [ ] Invalid policy proposal → no-layout status, not an exception.
- [ ] `learned-infer` extra added; CHANGELOG `[Unreleased]` entry added.
