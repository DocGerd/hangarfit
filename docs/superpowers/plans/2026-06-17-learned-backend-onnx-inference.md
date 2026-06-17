# Learned-backend ONNX export + `solve --backend learned` inference — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `solve --backend learned` produce a real `SolveResult` (valid layout + the policy's own drive-in tow plan) from a trained checkpoint, with no torch in the inference path, behind the unchanged deterministic verifier.

**Architecture:** A thin wheel-shipped seam (`src/hangarfit/learned.py`) lazy-imports a torch-free `ml/infer.py` that builds the cold-joint env from a production `Scenario`, runs an argmax rollout via an onnxruntime session, records each object's drive-in `Primitive`s, and assembles a `SolveResult` whose tow `MovesPlan` maps the recorded `Primitive`s 1:1 onto a `DubinsArc`. The deterministic `collisions.check` + Caddy egress gate is the sole arbiter of validity.

**Tech Stack:** Python 3.12, PyTorch 2.12 (`[train]` extra, contributor-only export), onnxruntime (new `[learned-infer]` extra, inference), numpy, shapely.

**Spec:** `docs/superpowers/specs/2026-06-17-learned-backend-onnx-inference-design.md`. **Issue:** #706 (epic #607 sub-project #5).

## Global Constraints

- **Python 3.12 only** (ADR-0009).
- **No torch / onnxruntime in the default install or the wheel.** torch stays the `[train]` extra; onnxruntime is the new `[learned-infer]` extra. `ml/` is never in the wheel (`[tool.setuptools.packages.find] where=["src"]`).
- **The dependency direction is `ml → src` only.** `src/hangarfit/learned.py` must NOT import `ml.*` at module load; it lazy-imports inside the function body.
- **Verifier is the final gate** (ADR-0027 / the prime directive): an invalid policy proposal yields a no-layout `SolveResult` (status `"exhausted_budget"`, empty `layouts`/`plans`), never an exception. The verifier (`collisions.check` + egress) is `go.layout_valid(layout)`.
- **Determinism (ADR-0027):** the verifier stays strict & `determinism-guard`-bound (unchanged). The learned proposer is OUTSIDE `determinism-guard`; this rung lands the **tier-1** within-build bit-identity canary (fixed weights + seed + pinned `CPUExecutionProvider`). Do NOT add `ml.infer` to `determinism-guard`.
- **Tests:** ml-side tests live in `tests/ml/`. torch tests `pytest.importorskip("torch")`; onnxruntime tests `pytest.importorskip("onnxruntime")`. CI installs only `[dev]`, so these skip in CI until #6 adds the lane (matches the existing torch-test pattern).
- **`DubinsArc.segments` must be non-empty** (its `__post_init__` raises otherwise) → a zero-primitive object emits `Move(path=None)`.
- **ONNX I/O contract (frozen):** inputs `raster (B,7,192,96) f32`, `tokens (B,N,24) f32`, `token_mask (B,N) bool`, `active_index (B,) i64`, `legal_action_mask (B,9) bool`; outputs `kind_gear_logits (B,9) f32`, `magnitude_bin_logits (B,5) f32`. The `-inf` legal-mask `masked_fill` stays inside the graph.
- **Local dev:** `pip install onnxruntime` (not yet in any lockfile) before running the inference tests; `torch` is already present.
- After ml/ edits the PostToolUse hook runs `ruff` + `pytest tests/ml/`; the Stop hook runs `mypy` (over `src/hangarfit/`, plus `ml/` when torch is importable). Keep both green.

---

### Task 1: ONNX export — `ml/export.py`

**Files:**
- Create: `ml/export.py`
- Test: `tests/ml/test_export.py`

**Interfaces:**
- Consumes: `ml.policy.HangarFitPolicy`, `ml.policy.to_batch`, `ml.encoding.{ObservationTensors, EncoderConfig, encode, RASTER_CHANNELS, TOKEN_DIM, ACTION_DIM}`, `ml.policy.PolicyOutput`.
- Produces: `export_onnx(policy: HangarFitPolicy, path: str | Path, *, example: ObservationTensors | None = None, opset: int = 17) -> None` — writes an ONNX graph of the policy forward (value head dropped). `ONNX_INPUT_NAMES`, `ONNX_OUTPUT_NAMES` (tuples of str) for downstream `ml.infer`.

- [ ] **Step 1: Write the failing test**

```python
# tests/ml/test_export.py
"""ONNX export of HangarFitPolicy (sub-project #5, #607). Needs the [train] extra
(torch) to export and the [learned-infer] extra (onnxruntime) to run the round-trip."""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")
ort = pytest.importorskip("onnxruntime")

import numpy as np

from ml.encoding import EncoderConfig, encode
from ml.export import ONNX_INPUT_NAMES, ONNX_OUTPUT_NAMES, export_onnx
from ml.policy import HangarFitPolicy, to_batch
from ml.train import build_trivial_env


def _example_obs():
    env = build_trivial_env()
    obs = env.reset()
    return encode(obs, env.hangar, {**env.fleet, **env.ground_objects}, EncoderConfig())


def test_export_argmax_parity(tmp_path):
    torch.manual_seed(0)
    policy = HangarFitPolicy()
    policy.eval()
    obs_t = _example_obs()
    out_path = tmp_path / "policy.onnx"
    export_onnx(policy, out_path)

    batch = to_batch([obs_t])
    with torch.no_grad():
        torch_out = policy(batch)
    feed = {
        "raster": batch["raster"].numpy(),
        "tokens": batch["tokens"].numpy(),
        "token_mask": batch["token_mask"].numpy(),
        "active_index": batch["active_index"].numpy(),
        "legal_action_mask": batch["legal_action_mask"].numpy(),
    }
    sess = ort.InferenceSession(str(out_path), providers=["CPUExecutionProvider"])
    kind_logits, mag_logits = sess.run(list(ONNX_OUTPUT_NAMES), feed)

    assert tuple(ONNX_INPUT_NAMES) == ("raster", "tokens", "token_mask", "active_index", "legal_action_mask")
    assert int(np.argmax(kind_logits, axis=-1)[0]) == int(torch_out.kind_gear_logits.argmax(-1)[0])
    assert int(np.argmax(mag_logits, axis=-1)[0]) == int(torch_out.magnitude_bin_logits.argmax(-1)[0])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pip install onnxruntime` (once), then `pytest tests/ml/test_export.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ml.export'`.

- [ ] **Step 3: Write minimal implementation**

```python
# ml/export.py
"""Export a trained HangarFitPolicy to ONNX (sub-project #5, epic #607). Needs the
[train] extra (torch). The exported graph is the per-step policy FORWARD — the value
head (critic) is dropped, since inference only needs the two action heads. The
``-inf`` legal-mask is applied inside the graph, so a downstream numpy ``argmax``
yields a legal action with no extra masking. Inference (onnxruntime) lives in
``ml/infer.py`` and needs no torch."""

from __future__ import annotations

from pathlib import Path

import torch
from torch import Tensor, nn

from ml.encoding import ACTION_DIM, EncoderConfig, RASTER_CHANNELS, TOKEN_DIM
from ml.policy import HangarFitPolicy, to_batch

ONNX_INPUT_NAMES = ("raster", "tokens", "token_mask", "active_index", "legal_action_mask")
ONNX_OUTPUT_NAMES = ("kind_gear_logits", "magnitude_bin_logits")


class _ForwardWrapper(nn.Module):
    """Positional-input wrapper over the dict-input policy forward, returning only the
    two action-head logit tensors (drops the scalar value head)."""

    def __init__(self, policy: HangarFitPolicy) -> None:
        super().__init__()
        self.policy = policy

    def forward(
        self,
        raster: Tensor,
        tokens: Tensor,
        token_mask: Tensor,
        active_index: Tensor,
        legal_action_mask: Tensor,
    ) -> tuple[Tensor, Tensor]:
        out = self.policy(
            {
                "raster": raster,
                "tokens": tokens,
                "token_mask": token_mask,
                "active_index": active_index,
                "legal_action_mask": legal_action_mask,
            }
        )
        return out.kind_gear_logits, out.magnitude_bin_logits


def _dummy_inputs(config: EncoderConfig) -> tuple[Tensor, ...]:
    """A single-sample batch with the encoder's shapes; values are irrelevant to the
    traced graph (masks all-True so nothing is force-masked during tracing)."""
    n = config.max_objects
    raster = torch.zeros(1, RASTER_CHANNELS, config.grid_h, config.grid_w, dtype=torch.float32)
    tokens = torch.zeros(1, n, TOKEN_DIM, dtype=torch.float32)
    token_mask = torch.ones(1, n, dtype=torch.bool)
    active_index = torch.zeros(1, dtype=torch.long)
    legal = torch.ones(1, ACTION_DIM, dtype=torch.bool)
    return raster, tokens, token_mask, active_index, legal


def export_onnx(
    policy: HangarFitPolicy,
    path: str | Path,
    *,
    example=None,
    opset: int = 17,
) -> None:
    """Trace ``policy``'s forward to an ONNX file at ``path``. Pass ``example`` (an
    ``ObservationTensors``) to trace real shapes, else a default-config dummy is used.
    Batch ``B`` and token count ``N`` are dynamic axes."""
    policy.eval()
    wrapper = _ForwardWrapper(policy)
    if example is not None:
        b = to_batch([example])
        args = (b["raster"], b["tokens"], b["token_mask"], b["active_index"], b["legal_action_mask"])
    else:
        args = _dummy_inputs(EncoderConfig())
    dynamic_axes = {
        "raster": {0: "B"},
        "tokens": {0: "B", 1: "N"},
        "token_mask": {0: "B", 1: "N"},
        "active_index": {0: "B"},
        "legal_action_mask": {0: "B"},
        "kind_gear_logits": {0: "B"},
        "magnitude_bin_logits": {0: "B"},
    }
    with torch.no_grad():
        torch.onnx.export(
            wrapper,
            args,
            str(path),
            input_names=list(ONNX_INPUT_NAMES),
            output_names=list(ONNX_OUTPUT_NAMES),
            dynamic_axes=dynamic_axes,
            opset_version=opset,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ml/test_export.py -v`
Expected: PASS. (If the TransformerEncoder mask export fails, bump `opset` to 18 in `export_onnx`'s default; if it still fails, that is the known risk — escalate before changing the network.)

- [ ] **Step 5: Commit**

```bash
git add ml/export.py tests/ml/test_export.py
git commit -m "feat(706): ONNX export of HangarFitPolicy forward (argmax parity)

Refs #706"
```

---

### Task 2: `train.py --save-onnx` wiring

**Files:**
- Modify: `ml/train.py` (add `save_onnx` param to `train` + `train_curriculum`; `--save-onnx` arg; call `export_onnx` after each `torch.save`)
- Test: `tests/ml/test_train_curriculum.py` (add one fast test) OR `tests/ml/test_export.py`

**Interfaces:**
- Consumes: `ml.export.export_onnx`.
- Produces: `train(..., save_onnx: str | None = None)` and `train_curriculum(..., save_onnx: str | None = None)` write an ONNX file when `save_onnx` is set; CLI flag `--save-onnx PATH`.

- [ ] **Step 1: Write the failing test**

```python
# tests/ml/test_export.py — append
def test_train_save_onnx_writes_file(tmp_path):
    from ml.train import train

    onnx_path = tmp_path / "trivial.onnx"
    train(iterations=1, rollout_len=16, save_onnx=str(onnx_path))
    assert onnx_path.exists() and onnx_path.stat().st_size > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_export.py::test_train_save_onnx_writes_file -v`
Expected: FAIL — `TypeError: train() got an unexpected keyword argument 'save_onnx'`.

- [ ] **Step 3: Write minimal implementation**

In `ml/train.py`:
- Add the import near the top: `from ml.export import export_onnx`.
- In `train(...)`: add `save_onnx: str | None = None` to the signature; after `if save is not None: torch.save(...)`, add:

```python
    if save_onnx is not None:
        export_onnx(policy, save_onnx)
```

- In `train_curriculum(...)`: add the same `save_onnx: str | None = None` param and the same two-line export after its `torch.save`.
- In `build_argparser()`: add

```python
    p.add_argument(
        "--save-onnx",
        type=str,
        default=None,
        help="also export the trained policy forward to this ONNX path (inference)",
    )
```

- In `main()`: pass `save_onnx=args.save_onnx` to both the `train(...)` and `train_curriculum(...)` calls.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ml/test_export.py::test_train_save_onnx_writes_file -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ml/train.py tests/ml/test_export.py
git commit -m "feat(706): train --save-onnx exports the policy alongside state_dict

Refs #706"
```

---

### Task 3: `learned-infer` optional extra — `pyproject.toml`

**Files:**
- Modify: `pyproject.toml` (`[project.optional-dependencies]`)

**Interfaces:**
- Produces: `pip install -e ".[learned-infer]"` installs onnxruntime.

- [ ] **Step 1: Inspect the current extras block**

Run: `grep -n -A6 "\[project.optional-dependencies\]" pyproject.toml`
Expected: shows `train = ["torch"]` (and `dev`, etc.).

- [ ] **Step 2: Add the extra**

Add to `[project.optional-dependencies]` (mirror the `train` entry's style; pin floor consistent with the repo's convention):

```toml
learned-infer = ["onnxruntime>=1.18"]
```

- [ ] **Step 3: Verify it resolves**

Run: `pip install -e ".[learned-infer]" 2>&1 | tail -3`
Expected: installs/【already satisfied】onnxruntime; no error.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "build(706): add [learned-infer] optional extra (onnxruntime)

Lockfile regen + CI lane are #6. Refs #706"
```

> Note: the dev/build lockfiles are regenerated separately (`docs/dev/lockfiles.md`); wiring `learned-infer` into CI's install is **#6**. Do not hand-edit `requirements-*.txt` (the PreToolUse guard blocks it).

---

### Task 4: `ml/infer.py` — `OrtPolicy` (torch-free onnxruntime forward)

**Files:**
- Create: `ml/infer.py`
- Test: `tests/ml/test_infer.py`

**Interfaces:**
- Consumes: the ONNX file from Task 1; `ml.encoding.{ObservationTensors, ACTION_DIM}`; `ml.action_space.decode`; `ml.types.{Primitive, Park}`.
- Produces: `class OrtPolicy` with `__init__(self, onnx_path: str | Path)` and `act(self, obs: ObservationTensors, *, turn_radius_m: float) -> Primitive | Park` (numpy argmax + decode; mirrors `HangarFitPolicy.act(deterministic=True)` without torch).

- [ ] **Step 1: Write the failing test**

```python
# tests/ml/test_infer.py
"""Torch-free onnxruntime inference for the learned backend (sub-project #5, #607)."""

from __future__ import annotations

import pytest

ort = pytest.importorskip("onnxruntime")
torch = pytest.importorskip("torch")  # OrtPolicy is torch-free, but we export with torch here

from ml.encoding import EncoderConfig, encode
from ml.export import export_onnx
from ml.infer import OrtPolicy
from ml.policy import HangarFitPolicy
from ml.train import build_trivial_env


def test_ortpolicy_matches_torch_act(tmp_path):
    torch.manual_seed(0)
    policy = HangarFitPolicy()
    policy.eval()
    env = build_trivial_env()
    obs = env.reset()
    obs_t = encode(obs, env.hangar, {**env.fleet, **env.ground_objects}, EncoderConfig())
    tr = obs.active.body.effective_turn_radius_m()

    onnx_path = tmp_path / "p.onnx"
    export_onnx(policy, onnx_path, example=obs_t)
    ort_pol = OrtPolicy(onnx_path)

    (_k, _m), _lp, torch_action = policy.act(obs_t, turn_radius_m=tr, deterministic=True)
    ort_action = ort_pol.act(obs_t, turn_radius_m=tr)
    assert type(ort_action) is type(torch_action)
    assert ort_action == torch_action
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_infer.py::test_ortpolicy_matches_torch_act -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ml.infer'`.

- [ ] **Step 3: Write minimal implementation**

```python
# ml/infer.py
"""Torch-free inference for the learned backend (sub-project #5, epic #607).

Runs a trained policy exported to ONNX (``ml/export.py``) with onnxruntime + numpy —
NO torch in this module. ``solve_learned_impl`` (later task) drives the cold-joint env
to a terminal layout and returns a ``SolveResult`` behind the deterministic verifier.

Determinism (ADR-0027): the proposer's tier-1 contract is within-build bit-identity
(fixed weights + seed + pinned CPUExecutionProvider). The verifier stays strict and is
the sole arbiter of validity — an invalid proposal yields a no-layout SolveResult."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ml.action_space import decode
from ml.encoding import ObservationTensors
from ml.export import ONNX_OUTPUT_NAMES
from ml.types import Park, Primitive


class OrtPolicy:
    """A trained policy forward as an onnxruntime session. ``act`` mirrors
    ``HangarFitPolicy.act(deterministic=True)`` with numpy argmax — the ``-inf`` legal
    mask is already baked into the graph, so argmax always yields a legal action."""

    def __init__(self, onnx_path: str | Path) -> None:
        import onnxruntime as ort  # local import: onnxruntime is the [learned-infer] extra

        # Pin CPUExecutionProvider single-threaded for the ADR-0027 tier-1 bit-identity
        # contract (within-build double-run reproducibility).
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(onnx_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )

    def act(self, obs: ObservationTensors, *, turn_radius_m: float) -> Primitive | Park:
        if obs.active_index < 0:
            raise ValueError("OrtPolicy.act called on a terminal observation (active_index < 0)")
        feed = {
            "raster": obs.raster[None].astype(np.float32),
            "tokens": obs.tokens[None].astype(np.float32),
            "token_mask": obs.token_mask[None],
            "active_index": np.asarray([obs.active_index], dtype=np.int64),
            "legal_action_mask": obs.legal_action_mask[None],
        }
        kind_logits, mag_logits = self._session.run(list(ONNX_OUTPUT_NAMES), feed)
        kind_idx = int(np.argmax(kind_logits[0]))
        mag_idx = int(np.argmax(mag_logits[0]))
        return decode(kind_idx, mag_idx, turn_radius_m=turn_radius_m)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ml/test_infer.py::test_ortpolicy_matches_torch_act -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ml/infer.py tests/ml/test_infer.py
git commit -m "feat(706): OrtPolicy — torch-free onnxruntime forward + argmax decode

Refs #706"
```

---

### Task 5: `ml/infer.py` — `env_from_scenario`

**Files:**
- Modify: `ml/infer.py` (add `env_from_scenario`)
- Test: `tests/ml/test_infer.py` (add)

**Interfaces:**
- Consumes: `hangarfit.models.Scenario` (fields `fleet`, `hangar`, `fleet_in`, `ground_objects`, `ground_object_defs`, `fixed_obstacle_placements`; properties `placeable_ids`, `mover_ids`); `ml.env.HangarFitEnv`; `ml.types.DifficultyConfig`; `dataclasses.replace`.
- Produces: `env_from_scenario(scenario: Scenario, *, apron_depth_m: float = 8.0) -> HangarFitEnv` — the production-`Scenario` counterpart of `ml.benchmark.build_scenario_env`.

- [ ] **Step 1: Write the failing test**

```python
# tests/ml/test_infer.py — append
from hangarfit.loader import load_scenario  # noqa: E402


def test_env_from_scenario_queues_placeables(tmp_path):
    import pathlib

    from ml.infer import env_from_scenario

    root = pathlib.Path(__file__).resolve().parents[2]
    scenario = load_scenario(str(root / "tests/fixtures/scenario_minimal.yaml"))
    env = env_from_scenario(scenario)
    obs = env.reset()
    assert obs.active is not None
    assert set(env.requested_ids) == set(scenario.placeable_ids)
    assert env.hangar.apron_depth_m == 8.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_infer.py::test_env_from_scenario_queues_placeables -v`
Expected: FAIL — `ImportError: cannot import name 'env_from_scenario'`.

- [ ] **Step 3: Write minimal implementation**

Add to `ml/infer.py` (and the imports it needs at the top):

```python
from dataclasses import replace

from hangarfit.models import Scenario
from ml.env import HangarFitEnv
from ml.types import DifficultyConfig


def env_from_scenario(scenario: Scenario, *, apron_depth_m: float = 8.0) -> HangarFitEnv:
    """Build a cold-joint env from a production ``Scenario`` (the inference counterpart
    of ``ml.benchmark.build_scenario_env``, which builds from a ``BenchScenario`` path).

    Movable bodies (aircraft + placed-routed movers) are driven in from an apron; fixed
    obstacles (immovable keep-outs) are PRE-PLACED at their surveyed poses, never driven —
    the same scene the deterministic verifier sees. The difficulty budgets are generous
    (a safety stop, not a curriculum cap)."""
    fixed_ids = [
        gid
        for gid in scenario.ground_objects
        if scenario.ground_object_defs[gid].object_class == "fixed_obstacle"
    ]
    placed = {p.plane_id for p in scenario.fixed_obstacle_placements}
    missing = [g for g in fixed_ids if g not in placed]
    if missing:
        raise ValueError(
            f"env_from_scenario: fixed obstacle(s) {missing} have no entry in "
            f"scenario.fixed_obstacle_placements — they would silently appear un-placed."
        )
    placeable = scenario.placeable_ids
    movers = {gid: scenario.ground_object_defs[gid] for gid in scenario.mover_ids}
    fixed_defs = {gid: scenario.ground_object_defs[gid] for gid in fixed_ids}
    per_object = 120
    difficulty = DifficultyConfig(
        max_objects=len(placeable),
        per_object_step_budget=per_object,
        total_step_budget=per_object * max(1, len(placeable)),
    )
    hangar = replace(scenario.hangar, apron_depth_m=apron_depth_m)
    return HangarFitEnv(
        hangar=hangar,
        fleet=scenario.fleet,
        requested_ids=placeable,
        ground_objects={**movers, **fixed_defs},
        fixed_placements=scenario.fixed_obstacle_placements,
        difficulty=difficulty,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ml/test_infer.py::test_env_from_scenario_queues_placeables -v`
Expected: PASS.
(If `tests/fixtures/scenario_minimal.yaml` lacks an apron or movers, the test still holds — it only asserts the queue == `placeable_ids` and the apron override.)

- [ ] **Step 5: Commit**

```bash
git add ml/infer.py tests/ml/test_infer.py
git commit -m "feat(706): env_from_scenario — cold-joint env from a production Scenario

Refs #706"
```

---

### Task 6: `ml/infer.py` — rollout + `MovesPlan` (Primitive → DubinsArc)

**Files:**
- Modify: `ml/infer.py` (add `_DrivenObject`, `rollout`, `build_moves_plan`)
- Test: `tests/ml/test_infer.py` (add the endpoint-parity canary)

**Interfaces:**
- Consumes: `OrtPolicy`, `env_from_scenario`, `ml.encoding.{EncoderConfig, encode}`, `hangarfit.towplanner.{Pose, Segment, DubinsArc, Move, MovesPlan}`, `ml.types.{Primitive, Park}`, `hangarfit.models.Layout`.
- Produces:
  - `rollout(env, ort_policy, encoder) -> tuple[Layout, list[_DrivenObject], StepInfo]` — drives the env to termination, recording each PARKED object's `(object_id, start_pose, end_pose, primitives)`.
  - `build_moves_plan(layout, driven, env) -> MovesPlan` — one `Move` per driven object; `path` a `DubinsArc` built from its `Primitive`s, or `None` if it parked with zero primitives.
  - `_DrivenObject` dataclass: `object_id: str`, `start_pose: Pose`, `end_pose: Pose`, `primitives: list[Primitive]`.

- [ ] **Step 1: Write the failing test (DubinsArc endpoint parity)**

```python
# tests/ml/test_infer.py — append
import math  # noqa: E402

from hangarfit.towplanner import DubinsArc, Segment  # noqa: E402
from ml.geometry_oracle import apply_primitive  # noqa: E402
from ml.types import Primitive  # noqa: E402


def test_primitive_sequence_dubinsarc_endpoint_parity():
    """A multi-segment DubinsArc built from a primitive sequence integrates to the same
    pose as chaining apply_primitive — the guarantee build_moves_plan relies on."""
    from hangarfit.towplanner import Pose

    start = Pose(x_m=5.0, y_m=-4.0, heading_deg=0.0)
    prims = [
        Primitive(kind="S", magnitude=2.0, gear=1),
        Primitive(kind="L", magnitude=3.0, gear=1),
        Primitive(kind="S", magnitude=1.0, gear=1),
    ]
    tr = 4.0
    pose = start
    for p in prims:
        pose, _ = apply_primitive(pose, p, turn_radius_m=tr)
    arc = DubinsArc(
        start=start,
        end=pose,
        turn_radius_m=tr,
        segments=tuple(Segment(kind=p.kind, length_m=p.magnitude, gear=p.gear) for p in prims),
    )
    integrated = arc.pose_at(arc.length_m)
    assert math.isclose(integrated.x_m, pose.x_m, abs_tol=1e-6)
    assert math.isclose(integrated.y_m, pose.y_m, abs_tol=1e-6)
    assert math.isclose(integrated.heading_deg, pose.heading_deg, abs_tol=1e-6)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_infer.py::test_primitive_sequence_dubinsarc_endpoint_parity -v`
Expected: PASS immediately if the integrators already agree (they share `pose_at`), or FAIL on a tolerance bug. This test pins the invariant; if it passes, that confirms the design assumption. (It does not need `rollout` yet — it is the canary that justifies Step 3's builder.)

- [ ] **Step 3: Write the implementation**

Add to `ml/infer.py`:

```python
from dataclasses import dataclass, field

from hangarfit.models import Layout
from hangarfit.towplanner import DubinsArc, Move, MovesPlan, Pose, Segment
from ml.encoding import EncoderConfig, encode
from ml.types import StepInfo  # re-exported via ml.types


@dataclass(slots=True)
class _DrivenObject:
    object_id: str
    start_pose: Pose
    end_pose: Pose
    primitives: list[Primitive] = field(default_factory=list)


def rollout(
    env: HangarFitEnv, policy: OrtPolicy, encoder: EncoderConfig | None = None
) -> tuple[Layout, list[_DrivenObject], StepInfo]:
    """Drive ``env`` to termination under ``policy`` (argmax). Record each PARKED object's
    spawn pose, the ordered primitives it was driven with, and its frozen (parked) pose.
    Objects abandoned at budget exhaustion are NOT recorded (they are not in the terminal
    layout). Returns (terminal_layout, driven_objects_in_park_order, last_step_info)."""
    enc = encoder or EncoderConfig()
    bodies = {**env.fleet, **env.ground_objects}
    obs = env.reset()
    driven: list[_DrivenObject] = []
    # The active object is identified at spawn; its primitives accumulate until a Park.
    current = _DrivenObject(obs.active.object_id, obs.active.pose, obs.active.pose)
    done = False
    info: StepInfo | None = None
    while not done and obs.active is not None:
        obs_t = encode(obs, env.hangar, bodies, enc)
        tr = obs.active.body.effective_turn_radius_m()
        action = policy.act(obs_t, turn_radius_m=tr)
        if isinstance(action, Park):
            current.end_pose = obs.active.pose  # the pose Park freezes
            driven.append(current)
        else:
            current.primitives.append(action)
        obs, _reward, done, info = env.step(action)
        if isinstance(action, Park) and not done and obs.active is not None:
            current = _DrivenObject(obs.active.object_id, obs.active.pose, obs.active.pose)
    if info is None:
        raise ValueError("rollout: episode produced no steps")
    return env._layout(), driven, info


def build_moves_plan(layout: Layout, driven: list[_DrivenObject], env: HangarFitEnv) -> MovesPlan:
    """Map each driven object's recorded primitives onto a DubinsArc tow Move (1:1
    Primitive->Segment). A zero-primitive object (parked at spawn) gets Move(path=None)
    — the established best-effort idiom, since DubinsArc.segments must be non-empty."""
    moves: list[Move] = []
    for d in driven:
        target = Pose(x_m=d.end_pose.x_m, y_m=d.end_pose.y_m, heading_deg=d.end_pose.heading_deg)
        if not d.primitives:
            moves.append(Move(plane_id=d.object_id, target_slot=target, path=None))
            continue
        tr = env._body(d.object_id).effective_turn_radius_m()
        segments = tuple(
            Segment(kind=p.kind, length_m=p.magnitude, gear=p.gear) for p in d.primitives
        )
        arc = DubinsArc(start=d.start_pose, end=target, turn_radius_m=tr, segments=segments)
        moves.append(Move(plane_id=d.object_id, target_slot=target, path=arc))
    return MovesPlan(target_layout=layout, moves=tuple(moves))
```

- [ ] **Step 4: Add a rollout integration test**

```python
# tests/ml/test_infer.py — append
def test_rollout_builds_moves_plan(tmp_path):
    from ml.infer import OrtPolicy, build_moves_plan, env_from_scenario, rollout
    import pathlib

    torch.manual_seed(0)
    policy = HangarFitPolicy()
    policy.eval()
    onnx_path = tmp_path / "p.onnx"
    export_onnx(policy, onnx_path)
    ort_pol = OrtPolicy(onnx_path)

    root = pathlib.Path(__file__).resolve().parents[2]
    scenario = load_scenario(str(root / "tests/fixtures/scenario_minimal.yaml"))
    env = env_from_scenario(scenario)
    layout, driven, info = rollout(env, ort_pol)
    plan = build_moves_plan(layout, driven, env)
    # Every parked object has a Move; every Move targets a real slot pose.
    assert len(plan.moves) == len(driven)
    assert all(m.target_slot is not None for m in plan.moves)
```

Run: `pytest tests/ml/test_infer.py -v`
Expected: PASS (the untrained policy may park few/no objects — the test only asserts structural coherence, not reachability).

- [ ] **Step 5: Commit**

```bash
git add ml/infer.py tests/ml/test_infer.py
git commit -m "feat(706): rollout + Primitive->DubinsArc MovesPlan builder

Endpoint parity is by construction (env apply_primitive shares DubinsArc.pose_at).
Refs #706"
```

---

### Task 7: `ml/infer.py` — `solve_learned_impl` (assemble `SolveResult` behind the verifier)

**Files:**
- Modify: `ml/infer.py` (add `solve_learned_impl`)
- Test: `tests/ml/test_infer.py` (add shape/validity + invalid→no-layout tests)

**Interfaces:**
- Consumes: `rollout`, `build_moves_plan`, `env_from_scenario`, `OrtPolicy`, `ml.geometry_oracle.layout_valid`, `hangarfit.models.{SolveResult, SolverDiagnostics, Scenario}`.
- Produces: `solve_learned_impl(scenario: Scenario, *, weights_path: str | Path, budget_s: float, alternatives: int, seed: int | None, plan_paths: bool) -> SolveResult`.

- [ ] **Step 1: Write the failing test**

```python
# tests/ml/test_infer.py — append
def test_solve_learned_impl_returns_well_formed_result(tmp_path):
    import pathlib

    from ml.infer import solve_learned_impl

    torch.manual_seed(0)
    policy = HangarFitPolicy()
    policy.eval()
    onnx_path = tmp_path / "p.onnx"
    export_onnx(policy, onnx_path)

    root = pathlib.Path(__file__).resolve().parents[2]
    scenario = load_scenario(str(root / "tests/fixtures/scenario_minimal.yaml"))
    result = solve_learned_impl(
        scenario, weights_path=onnx_path, budget_s=30.0, alternatives=1, seed=0, plan_paths=True
    )
    # SolveResult.__post_init__ enforces status/layouts/plans coherence; construction
    # succeeding is the structural assertion. An untrained policy almost always fails to
    # park the whole set validly, so expect a no-layout status here.
    assert result.status in ("found", "exhausted_budget")
    assert len(result.plans) == len(result.layouts)
    from ml.geometry_oracle import layout_valid

    if result.status == "found":
        assert layout_valid(result.layouts[0])
        assert len(result.plans) == 1
    else:
        assert result.layouts == ()
        assert result.plans == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_infer.py::test_solve_learned_impl_returns_well_formed_result -v`
Expected: FAIL — `ImportError: cannot import name 'solve_learned_impl'`.

- [ ] **Step 3: Write minimal implementation**

Add to `ml/infer.py`:

```python
import time

from hangarfit.models import SolveResult, SolverDiagnostics
from ml import geometry_oracle as go


def solve_learned_impl(
    scenario: Scenario,
    *,
    weights_path: str | Path,
    budget_s: float,
    alternatives: int,
    seed: int | None,
    plan_paths: bool,
) -> SolveResult:
    """Run the learned backend: argmax rollout of the ONNX policy over the env built from
    ``scenario``, then return a verifier-gated SolveResult. The verifier (collisions.check
    + Caddy egress, via go.layout_valid) is the sole arbiter — an invalid or incomplete
    proposal yields a no-layout 'exhausted_budget' result, never an exception.

    ``alternatives`` > 1 is accepted but yields a single (deterministic argmax) layout;
    diverse sampling is #696. ``budget_s`` is advisory here (the env's step budget bounds
    the rollout); it is recorded but not enforced as a wall-clock deadline (ADR-0003-style
    reproducibility favours the step-count bound)."""
    start = time.monotonic()
    resolved_seed = seed if seed is not None else 0
    policy = OrtPolicy(weights_path)
    env = env_from_scenario(scenario)
    layout, driven, info = rollout(env, policy)

    complete = info.placed == info.total
    if complete and go.layout_valid(layout):
        plan = build_moves_plan(layout, driven, env) if plan_paths else None
        return SolveResult(
            status="found",
            layouts=(layout,),
            diagnostics=SolverDiagnostics(
                restarts_attempted=0, wall_time_s=time.monotonic() - start, seed=resolved_seed
            ),
            plans=(plan,),
        )
    return SolveResult(
        status="exhausted_budget",
        layouts=(),
        diagnostics=SolverDiagnostics(
            restarts_attempted=0, wall_time_s=time.monotonic() - start, seed=resolved_seed
        ),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ml/test_infer.py -v`
Expected: PASS. (Confirm `SolverDiagnostics(restarts_attempted=..., wall_time_s=..., seed=...)` constructs — those three are the only no-default fields per `solver.py`'s call sites. If mypy/construction complains about a missing required field, add it with the solver's default value.)

- [ ] **Step 5: Commit**

```bash
git add ml/infer.py tests/ml/test_infer.py
git commit -m "feat(706): solve_learned_impl — verifier-gated SolveResult assembly

Refs #706"
```

---

### Task 8: Seam wiring — `src/hangarfit/learned.py`

**Files:**
- Modify: `src/hangarfit/learned.py` (add `weights_path`; lazy-import `ml.infer`; clean fallbacks)
- Test: `tests/test_learned.py` (extend)

**Interfaces:**
- Consumes: `ml.infer.solve_learned_impl` (lazy).
- Produces: `solve_learned(scenario, *, weights_path=None, budget_s=30.0, alternatives=1, seed=None, plan_paths=True) -> SolveResult`. Raises `LearnedBackendUnavailableError` (specific message) when `weights_path` is None, `ml`/`onnxruntime` unimportable, or the weights file is absent.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_learned.py — extend (read the file first; keep existing tests)
import pytest

from hangarfit.learned import LearnedBackendUnavailableError, solve_learned


def _minimal_scenario():
    from hangarfit.loader import load_scenario
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    return load_scenario(str(root / "tests/fixtures/scenario_minimal.yaml"))


def test_solve_learned_no_weights_is_clean_error():
    with pytest.raises(LearnedBackendUnavailableError, match="--weights"):
        solve_learned(_minimal_scenario(), weights_path=None)


def test_solve_learned_missing_weights_file_is_clean_error(tmp_path):
    missing = tmp_path / "nope.onnx"
    with pytest.raises(LearnedBackendUnavailableError, match="weights"):
        solve_learned(_minimal_scenario(), weights_path=str(missing))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_learned.py -v`
Expected: FAIL — `solve_learned()` got an unexpected keyword `weights_path` (current signature has no `weights_path`).

- [ ] **Step 3: Write minimal implementation**

Replace the body of `solve_learned` in `src/hangarfit/learned.py` (keep the module docstring; update it to drop "always raises"). New signature + body:

```python
def solve_learned(
    scenario: Scenario,
    *,
    weights_path: str | Path | None = None,
    budget_s: float = 30.0,
    alternatives: int = 1,
    seed: int | None = None,
    plan_paths: bool = True,
) -> SolveResult:
    """Learned-backend counterpart to :func:`hangarfit.solver.solve` (epic #607).

    Lazy-imports the torch-free ``ml.infer`` inference path (onnxruntime). Raises
    :class:`LearnedBackendUnavailableError` with an actionable message when the backend
    cannot run: no ``--weights`` given, the ``ml`` package is absent (a bare wheel — see
    #6), ``onnxruntime`` (the ``[learned-infer]`` extra) is missing, or the weights file
    does not exist. The deterministic verifier remains the sole arbiter of validity
    (ADR-0027); an invalid proposal returns a no-layout ``SolveResult``, not an error.
    """
    if weights_path is None:
        raise LearnedBackendUnavailableError(
            "the learned backend needs trained weights: pass --weights PATH "
            "(no default weights ship yet; tracked in #6)"
        )
    if not Path(weights_path).is_file():
        raise LearnedBackendUnavailableError(
            f"learned-backend weights not found at {weights_path!r}"
        )
    try:
        from ml.infer import solve_learned_impl
    except ImportError as exc:
        raise LearnedBackendUnavailableError(
            "the learned backend requires the inference dependencies: install the "
            "'[learned-infer]' extra (onnxruntime) in a source checkout that includes "
            "the 'ml/' package (wheel distribution is tracked in #6). "
            f"(import failed: {exc})"
        ) from exc
    return solve_learned_impl(
        scenario,
        weights_path=weights_path,
        budget_s=budget_s,
        alternatives=alternatives,
        seed=seed,
        plan_paths=plan_paths,
    )
```

Add the imports at the top of `learned.py` (these are stdlib/typing-safe — no torch/onnxruntime/ml at module load):

```python
from pathlib import Path
```

and move `Scenario`, `SolveResult` out of `TYPE_CHECKING` into a runtime import IF the new signature annotations are evaluated — they are not (`from __future__ import annotations` is already at the top), so keep them under `TYPE_CHECKING`. Verify `from __future__ import annotations` is present (it is).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_learned.py -v`
Expected: PASS. (The no-weights and missing-file errors fire before any `ml` import, so they pass even without onnxruntime installed.)

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/learned.py tests/test_learned.py
git commit -m "feat(706): wire the learned seam to ml.infer with clean fallbacks

Refs #706"
```

---

### Task 9: CLI `--weights` flag — `src/hangarfit/cli.py`

**Files:**
- Modify: `src/hangarfit/cli.py` (add `--weights` to the `solve` subparser; pass it on the `--backend learned` branch)
- Test: `tests/test_cli_backend.py` (extend)

**Interfaces:**
- Consumes: `args.weights`.
- Produces: `solve --backend learned --weights PATH`; `--backend learned` without `--weights` exits 2 with a clean message.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_backend.py — extend (read first; keep existing tests)
def test_backend_learned_without_weights_exits_2(capsys, tmp_path):
    from hangarfit.cli import main

    # a minimal valid solve scenario fixture
    code = main(["solve", "tests/fixtures/scenario_minimal.yaml", "--backend", "learned"])
    assert code == 2
    err = capsys.readouterr().err
    assert "weights" in err.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_backend.py -v`
Expected: FAIL — currently `--backend learned` raises the stub "not yet available" message; after Task 8 the seam needs `weights`, but the CLI does not pass it yet, so the no-weights branch is not reached with the right wiring. (Confirm the failure mode, then implement.)

- [ ] **Step 3: Write minimal implementation**

In `cli.py`, add to the `solve` subparser (near the `--backend` add_argument, ~line 367):

```python
    solve.add_argument(
        "--weights",
        type=str,
        default=None,
        dest="weights",
        help=(
            "Path to the learned backend's ONNX weights (only with --backend learned). "
            "No default ships yet (#6); omitting it with --backend learned exits cleanly."
        ),
    )
```

In the dispatch (~line 725), pass `weights_path=args.weights`:

```python
        result = solve_learned(
            scenario,
            weights_path=args.weights,
            budget_s=args.budget,
            alternatives=args.alternatives,
            seed=args.seed,
            plan_paths=args.render_paths,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_backend.py -v`
Expected: PASS (exit 2, message mentions weights).

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/cli.py tests/test_cli_backend.py
git commit -m "feat(706): solve --weights flag for the learned backend

Refs #706"
```

---

### Task 10: ADR-0027 tier-1 within-build bit-identity canary

**Files:**
- Test: `tests/ml/test_infer.py` (add the canary)

**Interfaces:**
- Consumes: `solve_learned_impl` (twice, identical inputs).

- [ ] **Step 1: Write the failing/asserting test**

```python
# tests/ml/test_infer.py — append
def test_learned_within_build_bit_identity(tmp_path):
    """ADR-0027 tier-1: same weights + seed + pinned CPU EP -> bit-identical SolveResult
    (poses + plan), within a single build. Cross-machine validity-only equivalence is a
    separate (deferred) canary."""
    import pathlib

    from ml.infer import solve_learned_impl

    torch.manual_seed(0)
    policy = HangarFitPolicy()
    policy.eval()
    onnx_path = tmp_path / "p.onnx"
    export_onnx(policy, onnx_path)

    root = pathlib.Path(__file__).resolve().parents[2]
    scenario = load_scenario(str(root / "tests/fixtures/scenario_minimal.yaml"))
    kw = dict(weights_path=onnx_path, budget_s=30.0, alternatives=1, seed=0, plan_paths=True)
    r1 = solve_learned_impl(scenario, **kw)
    r2 = solve_learned_impl(scenario, **kw)

    assert r1.status == r2.status
    assert r1.layouts == r2.layouts  # Layout is a frozen dataclass: structural equality
    assert r1.plans == r2.plans
```

- [ ] **Step 2: Run test**

Run: `pytest tests/ml/test_infer.py::test_learned_within_build_bit_identity -v`
Expected: PASS. (argmax is deterministic; the pinned single-thread CPU EP is reproducible within a build. If it flakes, verify `intra_op_num_threads=1`/`inter_op_num_threads=1` in `OrtPolicy`.)

- [ ] **Step 3: Commit**

```bash
git add tests/ml/test_infer.py
git commit -m "test(706): ADR-0027 tier-1 within-build bit-identity canary

Refs #706"
```

> The `ml-rl-guard` subagent guards this file family — the review (Task 11 / PR) runs it. `determinism-guard` is NOT involved (it guards only `solver.py`/`towplanner.py`).

---

### Task 11: Docs + CHANGELOG + ADR-0027 status

**Files:**
- Modify: `CHANGELOG.md` (`[Unreleased]`)
- Modify: `ml/README.md` (export + inference entry points)
- Modify: `CLAUDE.md` (a `solve --backend learned` command example under "Useful commands")
- Modify: `docs/adr/0027-learned-backend-determinism-scope.md` (note the tier-1 canary now exists)

**Interfaces:** none (docs only).

- [ ] **Step 1: CHANGELOG entry**

Add under `## [Unreleased]` → `### Added`:

```markdown
- **Learned backend inference (#706, epic #607).** `solve --backend learned --weights PATH`
  now runs: a trained policy is exported to ONNX (`ml/export.py`, `train --save-onnx`) and
  run torch-free via onnxruntime (`ml/infer.py`), returning a `SolveResult` (valid layout +
  the policy's own drive-in tow plan) behind the deterministic verifier. New optional
  `[learned-infer]` extra (onnxruntime). The verifier (`collisions.check` + Caddy egress)
  remains the sole arbiter of validity (ADR-0027); an invalid proposal returns a no-layout
  result. Wheel distribution, CI, and signed weights are tracked in #6.
```

- [ ] **Step 2: ml/README.md entry points**

Add an "Inference (#5)" subsection documenting:
- `python -m ml.train --schedule trivial --save model.pt --save-onnx model.onnx`
- `hangarfit solve <scenario> --backend learned --weights model.onnx [--render-paths]`
- the `[learned-infer]` extra (`pip install -e ".[learned-infer]"`).

- [ ] **Step 3: CLAUDE.md command example**

Under "Useful commands", near the existing learned-workspace block, add:

```bash
# #706 learned-backend inference (epic #607 sub-project #5). Export a trained policy to
# ONNX, then run it torch-free behind the verifier. Needs the [learned-infer] extra.
python -m ml.train --schedule trivial --save /tmp/p.pt --save-onnx /tmp/p.onnx   # [train]
hangarfit solve tests/fixtures/scenario_minimal.yaml --backend learned --weights /tmp/p.onnx
```

- [ ] **Step 4: ADR-0027 status note**

In `docs/adr/0027-...md` "Compliance" section, change the "Future (when the learned backend lands)" bullet to note the tier-1 within-build bit-identity canary now exists (`tests/ml/test_infer.py::test_learned_within_build_bit_identity`); tier-2 cross-machine validity-equivalence remains deferred (needs a shared trained checkpoint, #7). Leave Status as Proposed unless the user wants to flip it to Accepted.

- [ ] **Step 5: Run the full ml + seam test set and lint/type**

Run:
```bash
pytest tests/ml/test_export.py tests/ml/test_infer.py tests/test_learned.py tests/test_cli_backend.py -v
ruff check ml/ src/hangarfit/ tests/ && ruff format --check ml/ src/hangarfit/ tests/
mypy src/hangarfit/ && mypy ml/
```
Expected: all green (onnxruntime/torch tests run locally where installed; they skip cleanly where not).

- [ ] **Step 6: Commit**

```bash
git add CHANGELOG.md ml/README.md CLAUDE.md docs/adr/0027-learned-backend-determinism-scope.md
git commit -m "docs(706): CHANGELOG + ml/README + CLAUDE.md + ADR-0027 tier-1 canary note

Refs #706"
```

---

## Self-Review

**Spec coverage:**
- §3.1 thin seam + lazy import + clean fallbacks → Task 8. CLI `--weights` → Task 9. ✓
- §3.2 `env_from_scenario` → Task 5; rollout + MovesPlan(DubinsArc) → Task 6; verifier-gated `solve_learned_impl` + status mapping → Task 7. ✓
- §3.3 ONNX export (drop value head, dynamic axes, opset≥17) + `train --save-onnx` → Tasks 1–2. ✓
- §3.4 `learned-infer` extra → Task 3. ✓
- §3.5 tests (a) export parity → T1; (b) DubinsArc endpoint → T6; (c) SolveResult shape+validity → T7; (d) tier-1 bit-identity → T10; (e) clean fallbacks → T8/T9; (f) verifier-rejects → T7. ✓
- §4 determinism (tier-1 canary, no determinism-guard) → T10 + the note. ✓
- §6 CHANGELOG + `learned-infer` extra → T3/T11. ✓

**Placeholder scan:** No TBD/TODO; every code step shows real code. The one runtime unknown (TransformerEncoder ONNX export under opset 17) is called out with a concrete remedy in T1 Step 4, not left vague.

**Type consistency:** `solve_learned`/`solve_learned_impl` share the keyword set `(weights_path, budget_s, alternatives, seed, plan_paths)`. `OrtPolicy.act` returns `Primitive | Park` (matches `action_space.decode`). `rollout` returns `(Layout, list[_DrivenObject], StepInfo)`; `build_moves_plan(layout, driven, env)` consumes exactly that. `ONNX_OUTPUT_NAMES`/`ONNX_INPUT_NAMES` defined in `ml/export.py`, consumed in `ml/infer.py` (T4) and the test (T1). `SolverDiagnostics(restarts_attempted, wall_time_s, seed)` matches `solver.py`'s minimal call shape.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-17-learned-backend-onnx-inference.md`.
