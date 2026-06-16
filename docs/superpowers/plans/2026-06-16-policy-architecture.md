# Policy Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the cold-joint policy network — a torch `nn.Module` (policy + value) over the sub-project #2 `ObservationTensors`, plus the pure discrete action-space it decodes into the env's `Primitive | Park`.

**Architecture:** `ml/action_space.py` (pure, no torch) defines the factored-discrete action (a masked `(kind,gear)` head over `encoding`'s canonical 9-wide space + PARK, × a `K=5` magnitude bin) and `decode()` → `Primitive | Park` in the units the env expects. `ml/policy.py` (torch) runs a small CNN over the raster + masked self-attention over the tokens, gathers the active-object embedding, and emits a legal-mask-gated kind head + a `K`-way magnitude head + a value head. **No PPO loop / curriculum / rollouts** (→ #4).

**Tech Stack:** Python 3.12, numpy, torch (the new `[train]` extra), pytest.

**Spec:** `docs/superpowers/specs/2026-06-16-learned-backend-policy-architecture-design.md`

> **torch prerequisite:** `ml/policy.py` and `tests/ml/test_policy.py` need torch. The policy tests use `pytest.importorskip("torch")`, so they **skip** if torch is absent. To actually *verify* the network during implementation, install the CPU wheel first: `pip install torch` (or `pip install -e ".[train]"` after Task 2). `tests/ml/test_action_space.py` needs no torch and runs regardless.

---

## File Structure

- **Create** `ml/action_space.py` — pure (no torch): bins + `decode()`. The action contract, reused by the #4 trainer.
- **Create** `ml/policy.py` — torch: `to_batch()` adapter, `PolicyOutput`, `HangarFitPolicy(nn.Module)`.
- **Create** `tests/ml/test_action_space.py` (no torch — runs in CI) and `tests/ml/test_policy.py` (`importorskip` torch).
- **Modify** `pyproject.toml` — add the `[train]` optional-dependency extra.
- **Modify** `ml/types.py` — retire the stale `geometry_oracle.bin_magnitude` docstring line.
- **Modify** `CHANGELOG.md` — one `[Unreleased]` entry.

**Conventions:** cwd is repo root; on branch `feature/607-rung4-policy` (created off develop, which already has `ml/encoding.py` from #677). `ml/` is importable as `from ml.policy import ...`. The PostToolUse hook runs ruff + a pytest slice on `tests/` edits.

---

### Task 1: Action space — `ml/action_space.py` (pure, no torch)

**Files:**
- Create: `ml/action_space.py`
- Test: `tests/ml/test_action_space.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ml/test_action_space.py
"""Tests for the pure discrete action-space contract (ml/action_space.py)."""

from __future__ import annotations

import math

from ml import action_space
from ml.action_space import MAGNITUDE_DIM, PIVOT_BINS_DEG, TRANSLATION_BINS, decode
from ml.encoding import ACTION_DIM, PARK_INDEX, _CANONICAL_ACTIONS
from ml.types import Park, Primitive


def test_bins_and_dim():
    assert MAGNITUDE_DIM == len(TRANSLATION_BINS) == len(PIVOT_BINS_DEG) == 5
    assert TRANSLATION_BINS == (0.25, 0.5, 1.0, 2.0, 4.0)


def test_reuses_encoding_canonical_order():
    # single source of truth: action_space must reuse encoding's constants
    assert action_space._CANONICAL_ACTIONS is _CANONICAL_ACTIONS
    assert action_space.ACTION_DIM == ACTION_DIM == 9
    assert action_space.PARK_INDEX == PARK_INDEX == 8


def test_decode_park_ignores_bin():
    for b in range(MAGNITUDE_DIM):
        assert decode(PARK_INDEX, b, turn_radius_m=0.0) == Park()


def test_decode_cart_pivot_is_radians():
    # ('L', 1) is index 0; on a cart (turn_radius 0) the magnitude is radians of pivot
    act = decode(0, 2, turn_radius_m=0.0)
    assert isinstance(act, Primitive)
    assert act.kind == "L" and act.gear == 1
    assert math.isclose(act.magnitude, math.radians(PIVOT_BINS_DEG[2]))


def test_decode_owngear_arc_is_metres():
    # ('R', 1) is index 2; own-gear (turn_radius > 0) -> arc length in metres
    act = decode(2, 3, turn_radius_m=8.0)
    assert isinstance(act, Primitive)
    assert act.kind == "R" and act.gear == 1
    assert act.magnitude == TRANSLATION_BINS[3]


def test_decode_straight_and_strafe_are_metres():
    s = decode(1, 1, turn_radius_m=0.0)   # ('S', 1)
    assert isinstance(s, Primitive) and s.kind == "S" and s.magnitude == TRANSLATION_BINS[1]
    # 'T' (strafe) is index 6; always metres
    t = decode(6, 4, turn_radius_m=0.0)
    assert isinstance(t, Primitive) and t.kind == "T" and t.magnitude == TRANSLATION_BINS[4]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/ml/test_action_space.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ml.action_space'`.

- [ ] **Step 3: Write minimal implementation**

```python
# ml/action_space.py
"""Discrete action-space contract for the cold-joint policy (sub-project #3, #607).

Pure (no torch). The factored action = a kind index over ``encoding``'s canonical
9-wide action space (8 ``(kind, gear)`` movement actions + PARK at ``PARK_INDEX``)
times a ``K``-way magnitude bin. ``decode()`` turns a sampled action into the env's
``Primitive | Park`` in the units the env expects: radians for a cart pivot
(``L``/``R`` at ``turn_radius == 0``), metres for straights/strafes/own-gear arcs."""

from __future__ import annotations

import math
from typing import Literal, cast

from hangarfit.towplanner import SegmentKind
from ml.encoding import ACTION_DIM, PARK_INDEX, _CANONICAL_ACTIONS
from ml.types import Park, Primitive

__all__ = ["TRANSLATION_BINS", "PIVOT_BINS_DEG", "MAGNITUDE_DIM", "ACTION_DIM", "PARK_INDEX", "decode"]

TRANSLATION_BINS: tuple[float, ...] = (0.25, 0.5, 1.0, 2.0, 4.0)  # metres
PIVOT_BINS_DEG: tuple[float, ...] = (5.0, 15.0, 30.0, 45.0, 90.0)  # degrees -> radians at decode
MAGNITUDE_DIM = len(TRANSLATION_BINS)
assert MAGNITUDE_DIM == len(PIVOT_BINS_DEG), "magnitude bin tables must match in length"


def decode(kind_gear_idx: int, mag_bin_idx: int, *, turn_radius_m: float) -> Primitive | Park:
    """Resolve a sampled factored action into the env's ``Primitive | Park``.

    ``PARK_INDEX`` -> ``Park()`` (``mag_bin_idx`` ignored). A cart pivot (``kind`` in
    ``{'L', 'R'}`` and ``turn_radius_m == 0``) decodes to ``radians(PIVOT_BINS_DEG)``;
    everything else (``S``/``T`` and own-gear arcs) decodes to ``TRANSLATION_BINS`` metres.
    """
    if kind_gear_idx == PARK_INDEX:
        return Park()
    kind, gear = _CANONICAL_ACTIONS[kind_gear_idx]
    if kind in ("L", "R") and turn_radius_m == 0.0:
        magnitude = math.radians(PIVOT_BINS_DEG[mag_bin_idx])
    else:
        magnitude = TRANSLATION_BINS[mag_bin_idx]
    return Primitive(kind=cast(SegmentKind, kind), magnitude=magnitude, gear=cast("Literal[1, -1]", gear))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ml/test_action_space.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/action_space.py tests/ml/test_action_space.py
git commit -m "feat(607): policy action-space contract (factored discrete + decode)"
```

---

### Task 2: `[train]` extra + retire the stale `bin_magnitude` docstring

**Files:**
- Modify: `pyproject.toml` (`[project.optional-dependencies]`)
- Modify: `ml/types.py` (the `Primitive` docstring)

- [ ] **Step 1: Add the `[train]` extra**

In `pyproject.toml`, under `[project.optional-dependencies]` (which currently holds `dev`), add:

```toml
train = ["torch"]
```

(Contributor-only; not in the default install. `ml/` is already excluded from the wheel via `[tool.setuptools.packages.find] where = ["src"]`.)

- [ ] **Step 2: Retire the stale docstring reference**

In `ml/types.py`, the `Primitive` docstring references a helper that was never built (`"A binning helper lives in ``geometry_oracle.bin_magnitude``."`). Replace that sentence with one pointing at where binning actually lives now:

```
    ``magnitude`` is continuous: metres for ``S``/``T`` and own-gear arcs; radians of
    pivot for a cart turn (``L``/``R`` at turn_radius 0). The policy's discrete
    magnitude bins + the ``Primitive``-decode live in ``ml.action_space`` (sub-project #3).
```

- [ ] **Step 3: Verify nothing broke + the extra parses**

Run: `python -m pytest tests/ml/ -q` (existing suite still green) and `python -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))"` (valid TOML).
Expected: tests pass; no TOML error.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml ml/types.py
git commit -m "chore(607): add [train] torch extra; retire stale bin_magnitude docstring"
```

---

### Task 3: Observation→torch batch adapter — `ml/policy.py`

**Files:**
- Create: `ml/policy.py`
- Test: `tests/ml/test_policy.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ml/test_policy.py
"""Tests for the cold-joint policy network (ml/policy.py). Requires torch."""

from __future__ import annotations

import numpy as np
import pytest

torch = pytest.importorskip("torch")  # whole module skips without the [train] extra

from hangarfit.models import Placement
from ml.encoding import EncoderConfig, encode
from ml.policy import HangarFitPolicy, PolicyOutput, to_batch
from ml.types import ActiveObject, Observation, ParkedObject, Pose
from tests.ml.conftest import _fuji, empty_hangar


def _obs():
    fleet = _fuji()
    pl = Placement(plane_id="fuji", x_m=11.0, y_m=12.0, heading_deg=0.0, on_carts=False)
    active = ActiveObject(
        object_id="aviat_husky", body=fleet["aviat_husky"],
        pose=Pose(x_m=11.0, y_m=-4.0, heading_deg=0.0), on_carts=False,
    )
    obs = Observation(
        active=active, parked=(ParkedObject(object_id="fuji", placement=pl),),
        unplaced_ids=("cessna_150",), steps_this_object=0, steps_total=0,
    )
    return encode(obs, empty_hangar(), fleet, EncoderConfig())


def test_to_batch_shapes_and_dtypes():
    batch = to_batch([_obs(), _obs()])
    assert batch["raster"].shape == (2, 7, 192, 96) and batch["raster"].dtype == torch.float32
    assert batch["tokens"].shape == (2, 16, 24)
    assert batch["token_mask"].shape == (2, 16) and batch["token_mask"].dtype == torch.bool
    assert batch["active_index"].shape == (2,) and batch["active_index"].dtype == torch.long
    assert batch["legal_action_mask"].shape == (2, 9) and batch["legal_action_mask"].dtype == torch.bool
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/ml/test_policy.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ml.policy'` (or SKIP if torch absent — install torch first to proceed).

- [ ] **Step 3: Write minimal implementation**

```python
# ml/policy.py
"""Cold-joint policy network (sub-project #3, epic #607). torch nn.Module:
CNN(raster) + masked self-attention(tokens) -> active-object embedding ->
legal-mask-gated (kind,gear) head + K-way magnitude head + value head, plus the
ObservationTensors -> batched-tensor adapter. Requires the [train] extra (torch)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import torch
from torch import Tensor, nn

from ml.action_space import MAGNITUDE_DIM
from ml.encoding import ACTION_DIM, RASTER_CHANNELS, TOKEN_DIM, ObservationTensors


@dataclass
class PolicyOutput:
    kind_gear_logits: Tensor      # (B, ACTION_DIM) — illegal slots are -inf
    magnitude_bin_logits: Tensor  # (B, MAGNITUDE_DIM)
    value: Tensor                 # (B,)


def to_batch(obs: Sequence[ObservationTensors]) -> dict[str, Tensor]:
    """Stack a list of ObservationTensors into batched torch tensors. The only
    torch seam on the input side (ml/encoding.py stays numpy)."""
    return {
        "raster": torch.from_numpy(np.stack([o.raster for o in obs])),
        "tokens": torch.from_numpy(np.stack([o.tokens for o in obs])),
        "token_mask": torch.from_numpy(np.stack([o.token_mask for o in obs])),
        "active_index": torch.tensor([o.active_index for o in obs], dtype=torch.long),
        "legal_action_mask": torch.from_numpy(np.stack([o.legal_action_mask for o in obs])),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ml/test_policy.py -q`
Expected: PASS (1 test) — or SKIP if torch absent.

- [ ] **Step 5: Commit**

```bash
git add ml/policy.py tests/ml/test_policy.py
git commit -m "feat(607): policy ObservationTensors->torch batch adapter"
```

---

### Task 4: The network — `HangarFitPolicy.forward`

**Files:**
- Modify: `ml/policy.py`
- Test: `tests/ml/test_policy.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/ml/test_policy.py
def _model(seed=0):
    torch.manual_seed(seed)
    return HangarFitPolicy(d_model=64, n_layers=2, n_heads=4).eval()


def test_forward_output_shapes():
    out = _model()(to_batch([_obs(), _obs()]))
    assert isinstance(out, PolicyOutput)
    assert out.kind_gear_logits.shape == (2, 9)
    assert out.magnitude_bin_logits.shape == (2, 5)
    assert out.value.shape == (2,)


def test_illegal_kinds_are_masked_to_zero_probability():
    batch = to_batch([_obs()])  # fuji/husky are own-gear -> strafe (idx 6,7) illegal
    out = _model()(batch)
    legal = batch["legal_action_mask"][0]
    probs = out.kind_gear_logits.softmax(-1)[0]
    assert torch.all(probs[~legal] == 0.0)            # illegal -> exactly 0 after softmax
    assert torch.isclose(probs[legal].sum(), torch.tensor(1.0))  # legal mass sums to 1


def test_forward_is_deterministic_in_eval():
    batch = to_batch([_obs()])
    m = _model(seed=3)
    a, b = m(batch), m(batch)
    assert torch.equal(a.kind_gear_logits, b.kind_gear_logits)
    assert torch.equal(a.magnitude_bin_logits, b.magnitude_bin_logits)
    assert torch.equal(a.value, b.value)


def test_gradients_flow():
    m = HangarFitPolicy(d_model=64, n_layers=2, n_heads=4)  # train mode
    out = m(to_batch([_obs(), _obs()]))
    # avoid the -inf masked kind logits in the loss; use mag logits + value
    loss = out.magnitude_bin_logits.sum() + out.value.sum()
    loss.backward()
    assert any(p.grad is not None and torch.any(p.grad != 0) for p in m.parameters())


def test_single_and_batched_consistency():
    m = _model(seed=1)
    o = _obs()
    single = m(to_batch([o]))
    batched = m(to_batch([o, o]))
    assert torch.allclose(single.value, batched.value[:1], atol=1e-5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/ml/test_policy.py -q -k "forward or masked or deterministic or gradients or consistency"`
Expected: FAIL — `HangarFitPolicy` has no `forward` / cannot construct.

- [ ] **Step 3: Write minimal implementation**

```python
# add to ml/policy.py
class HangarFitPolicy(nn.Module):
    """Policy + value network over ObservationTensors. Acts on the active object:
    a legal-mask-gated (kind,gear) head + a K-way magnitude head + a scalar value."""

    def __init__(
        self,
        *,
        d_model: int = 128,
        n_layers: int = 2,
        n_heads: int = 4,
        cnn_channels: tuple[int, ...] = (16, 32, 64),
    ) -> None:
        super().__init__()
        convs: list[nn.Module] = []
        in_ch = RASTER_CHANNELS
        for ch in cnn_channels:
            convs += [nn.Conv2d(in_ch, ch, kernel_size=3, stride=2, padding=1), nn.ReLU()]
            in_ch = ch
        self.cnn = nn.Sequential(*convs, nn.AdaptiveAvgPool2d(1), nn.Flatten())  # (B, cnn_channels[-1])
        self.cnn_proj = nn.Linear(cnn_channels[-1], d_model)   # g: -> D
        self.token_proj = nn.Linear(TOKEN_DIM, d_model)         # tokens 24 -> D
        self.fuse = nn.Linear(2 * d_model, d_model)             # concat(token, g) 2D -> D
        layer = nn.TransformerEncoderLayer(
            d_model, n_heads, dim_feedforward=4 * d_model, batch_first=True
        )
        self.encoder = nn.TransformerEncoder(layer, n_layers)
        self.kind_head = nn.Sequential(nn.Linear(d_model, d_model), nn.ReLU(), nn.Linear(d_model, ACTION_DIM))
        self.mag_head = nn.Sequential(nn.Linear(d_model, d_model), nn.ReLU(), nn.Linear(d_model, MAGNITUDE_DIM))
        self.value_head = nn.Sequential(nn.Linear(2 * d_model, d_model), nn.ReLU(), nn.Linear(d_model, 1))

    def forward(self, batch: dict[str, Tensor]) -> PolicyOutput:
        raster, tokens = batch["raster"], batch["tokens"]
        token_mask, active_index = batch["token_mask"], batch["active_index"]
        legal = batch["legal_action_mask"]
        g = self.cnn_proj(self.cnn(raster))                       # (B, D)
        tok = self.token_proj(tokens)                             # (B, N, D)
        g_b = g.unsqueeze(1).expand(-1, tok.shape[1], -1)         # (B, N, D)
        fused = self.fuse(torch.cat([tok, g_b], dim=-1))          # (B, N, D)
        emb = self.encoder(fused, src_key_padding_mask=~token_mask)  # (B, N, D)
        idx = active_index.clamp(min=0).view(-1, 1, 1).expand(-1, 1, emb.shape[-1])
        active_emb = emb.gather(1, idx).squeeze(1)                # (B, D)
        m = token_mask.unsqueeze(-1).to(emb.dtype)               # (B, N, 1)
        pooled = (emb * m).sum(1) / m.sum(1).clamp(min=1.0)       # (B, D)
        kind_logits = self.kind_head(active_emb).masked_fill(~legal, float("-inf"))
        mag_logits = self.mag_head(active_emb)
        value = self.value_head(torch.cat([pooled, g], dim=-1)).squeeze(-1)
        return PolicyOutput(kind_logits, mag_logits, value)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ml/test_policy.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/policy.py tests/ml/test_policy.py
git commit -m "feat(607): HangarFitPolicy forward (CNN + masked attention + heads)"
```

---

### Task 5: `.act()` convenience

**Files:**
- Modify: `ml/policy.py`
- Test: `tests/ml/test_policy.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/ml/test_policy.py
from ml.types import Park, Primitive


def test_act_returns_only_legal_actions_and_decodes():
    m = _model(seed=2)
    obs_t = _obs()  # active = aviat_husky, own-gear (turn_radius > 0): strafe illegal
    tr = _fuji()["aviat_husky"].effective_turn_radius_m()
    legal = obs_t.legal_action_mask
    for _ in range(50):
        (kind_idx, mag_idx), log_prob, decoded = m.act(obs_t, turn_radius_m=tr)
        assert legal[kind_idx]                     # never samples an illegal (kind,gear)
        assert isinstance(decoded, (Primitive, Park))
        assert isinstance(log_prob, float)
        assert 0 <= mag_idx < 5


def test_act_deterministic_takes_argmax():
    m = _model(seed=2)
    obs_t = _obs()
    tr = _fuji()["aviat_husky"].effective_turn_radius_m()
    a = m.act(obs_t, turn_radius_m=tr, deterministic=True)
    b = m.act(obs_t, turn_radius_m=tr, deterministic=True)
    assert a[0] == b[0]   # same (kind_idx, mag_idx) every time
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/ml/test_policy.py -q -k act`
Expected: FAIL — `HangarFitPolicy` has no `act`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to ml/policy.py imports
from ml import action_space
from ml.types import Park, Primitive

# add as a method on HangarFitPolicy
    @torch.no_grad()
    def act(
        self, obs: ObservationTensors, *, turn_radius_m: float, deterministic: bool = False
    ) -> tuple[tuple[int, int], float, Primitive | Park]:
        """Sample a masked (kind_gear, mag_bin) for a single live observation; return
        the indices, the joint log-prob, and the decoded Primitive | Park. Only ever
        returns a legal (kind,gear) (illegal logits are -inf)."""
        out = self(to_batch([obs]))
        kind_dist = torch.distributions.Categorical(logits=out.kind_gear_logits)
        mag_dist = torch.distributions.Categorical(logits=out.magnitude_bin_logits)
        if deterministic:
            kind_idx = out.kind_gear_logits.argmax(-1)
            mag_idx = out.magnitude_bin_logits.argmax(-1)
        else:
            kind_idx = kind_dist.sample()
            mag_idx = mag_dist.sample()
        log_prob = float(kind_dist.log_prob(kind_idx) + mag_dist.log_prob(mag_idx))
        decoded = action_space.decode(int(kind_idx), int(mag_idx), turn_radius_m=turn_radius_m)
        return (int(kind_idx), int(mag_idx)), log_prob, decoded
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ml/test_policy.py -q -k act`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/policy.py tests/ml/test_policy.py
git commit -m "feat(607): HangarFitPolicy.act — masked sampling + decode"
```

---

### Task 6: CHANGELOG, full verification, PR

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the CHANGELOG entry**

Under `## [Unreleased] / ### Added` in `CHANGELOG.md`:

```markdown
- Learned backend (#607, sub-project #3): the cold-joint policy network
  (`ml/policy.py`, a torch `nn.Module`) — a CNN over the observation raster + masked
  self-attention over the object tokens, emitting a legal-mask-gated `(kind,gear)`
  head + a `K=5` magnitude-bin head + a value head — plus the pure discrete
  action-space contract (`ml/action_space.py`) that decodes a sampled action into the
  env's `Primitive | Park`. Contributor-only (the new `[train]` torch extra; `ml/`
  is not in the wheel); the policy tests `importorskip` torch.
```

- [ ] **Step 2: Lint + type-check the new modules**

Run:
```bash
ruff check ml/action_space.py ml/policy.py tests/ml/test_action_space.py tests/ml/test_policy.py
ruff format --check ml/action_space.py ml/policy.py tests/ml/test_action_space.py tests/ml/test_policy.py
mypy ml/action_space.py ml/policy.py
```
Expected: all clean. (`mypy ml/policy.py` requires torch installed to resolve stubs; if torch is absent, mypy reports a missing-import — install torch or note it skips.)

- [ ] **Step 3: Full ml suite**

Run: `python -m pytest tests/ml/ -q`
Expected: `test_action_space.py` (6) + `test_policy.py` (9, or SKIPPED if torch absent) + the existing 49 encoding/env/reward/oracle tests, all green.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(607): CHANGELOG for the policy network (sub-project #3)"
```

- [ ] **Step 5: Push + open the draft PR (base develop)**

```bash
git push -u origin feature/607-rung4-policy
gh pr create --draft --base develop \
  --title "feat(607): policy network architecture (sub-project #3 impl)" \
  --body "Closes #<RUNG4_ISSUE>. Implements the cold-joint policy network per the design spec (PR #679). action_space.py is pure (CI-tested); policy.py needs the [train] torch extra (tests importorskip; dedicated torch CI job in #6). Review arc: code-reviewer + type-design-analyzer (new PolicyOutput/action types). No geometry/solver/towplanner change -> geometry-invariant-guard / determinism-guard not required."
```

- [ ] **Step 6: Review arc, resolve, ready**

Invoke `/pr-review` (code-reviewer + type-design-analyzer for the new types). Convert findings to threads, fix or rebut, then `gh pr ready <n>` and hand off (the user is the sole merger).

---

## Self-Review (completed during plan authoring)

- **Spec coverage:** §3 modules → Tasks 1 (`action_space`) + 3–5 (`policy`); §4 action space (factored discrete, decode units, encoding-constant reuse) → Task 1; §5 network (CNN + masked self-attn + active gather + masked-mean pool + 3 heads, PARK slot, fusion width) → Task 4; §6 adapter → Task 3; §7 determinism → Task 4 (`test_forward_is_deterministic_in_eval`); §8 testing (action_space no-torch + policy importorskip: shapes, masking-to-zero-prob, determinism, gradients, `.act()` legal-only) → Tasks 1/3/4/5; §9 packaging (`[train]` extra) → Task 2; §10 workflow → Task 6. No gaps.
- **Type consistency:** `PolicyOutput(kind_gear_logits, magnitude_bin_logits, value)`, `HangarFitPolicy(d_model, n_layers, n_heads, cnn_channels)`, `to_batch(Sequence[ObservationTensors])`, `decode(kind_gear_idx, mag_bin_idx, *, turn_radius_m)`, `.act(obs, *, turn_radius_m, deterministic)` are used identically across tasks. Head widths: kind `ACTION_DIM=9`, mag `MAGNITUDE_DIM=5`, value `1` — consistent in Task 4 code and tests.
- **Placeholder scan:** every code step is complete; the only placeholder is `#<RUNG4_ISSUE>` in the PR body, filled when the impl issue is filed (Task 6).
- **Known mypy notes (documented):** `decode` casts `kind`/`gear` from `encoding._CANONICAL_ACTIONS` (`tuple[str, int]`) to `SegmentKind`/`Literal[1,-1]`; `mypy ml/policy.py` needs torch installed for stubs.
