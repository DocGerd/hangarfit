# Spatial-token cross-attention policy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the spatially-blind global-average-pool in `ml/policy.py` with an opt-in spatial-token cross-attention path so object tokens attend to free-space cells instead of one broadcast global summary (#809, the #736 plateau lever).

**Architecture:** A new `spatial_tokens: bool = False` constructor flag on `HangarFitPolicy`. OFF builds and runs today's exact net (byte-identical, 0 new params). ON keeps the CNN feature map `(B, C, 24, 12)`, projects each cell to a spatial token (+ fixed sin/cos 2D PE), concatenates the 288 spatial tokens with the 16 object tokens into one transformer sequence, and feeds a spatial summary into a widened value head. Pure `ml/policy.py` + `tests/ml/` change; no `encoding.py` / `SCHEMA_VERSION` / `#752` impact.

**Tech Stack:** Python 3.12, PyTorch (the `[train]` extra; tests `importorskip("torch")`), pytest. Spec: `docs/superpowers/specs/2026-06-23-spatial-token-policy-design.md`.

## Global Constraints

- **Default-neutrality (4c-ii).** `spatial_tokens=False` (the default) MUST register **zero** new parameters, in the **same module-registration order** as today, and run today's exact `forward()` → byte-identical. Existing determinism/byte-identity canaries must not need re-baseline.
- **`ml-rl-guard` invariants.** No new RNG in `forward()`; the fixed sin/cos PE is computed deterministically (not learned). Validity = the product checker (untouched — observation-consumption only). No new silent-failure surface.
- **Scope:** `ml/policy.py`, `tests/ml/`, and a thin `ml/train.py` CLI wire only. No `ml/encoding.py`, no `SCHEMA_VERSION` bump, no scene/v2 contact.
- **Resolution:** v1 uses the 2 m (`24×12`) feature-map tap. A 1 m (`48×24`) tap is pre-registered as a follow-up lever, NOT built here.
- **`d_model` divisibility:** the 2D sin/cos PE requires `d_model % 4 == 0` (default 128, test dims 64/32 all satisfy it).
- **Commits:** TDD, one logical change per commit. The repo runs a PostToolUse ruff+pytest hook after `ml/*.py` edits; let it pass before committing.
- **Branch:** `feature/809-spatial-token-policy` (already created off develop; the design spec is already committed on it).

---

### Task 1: Fixed sin/cos 2D positional-encoding helper

**Files:**
- Modify: `ml/policy.py` (add module-level helper + `import math`)
- Test: `tests/ml/test_policy.py`

**Interfaces:**
- Produces: `_sincos_pos_2d(h: int, w: int, d_model: int) -> torch.Tensor` returning `(h*w, d_model)` float32, **row-major** over the `h×w` grid (cell index `= row*w + col`, matching `Tensor.flatten(2)`), deterministic, no RNG. Requires `d_model % 4 == 0`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/ml/test_policy.py` (after the imports; `_sincos_pos_2d` is imported from `ml.policy`):

```python
import math  # noqa: E402  (top of file, with the other stdlib imports)
from ml.policy import _sincos_pos_2d  # noqa: E402  (add to the existing ml.policy import line or its own)


def test_sincos_pos_2d_shape_finite_deterministic():
    pe = _sincos_pos_2d(24, 12, 64)
    assert pe.shape == (24 * 12, 64)
    assert pe.dtype == torch.float32
    assert torch.isfinite(pe).all()
    assert torch.equal(pe, _sincos_pos_2d(24, 12, 64))  # no RNG -> identical


def test_sincos_pos_2d_row_major_distinct_cells():
    pe = _sincos_pos_2d(24, 12, 64)
    # row-major: index = row*w + col. (0,0)=0, (0,1)=1, (1,0)=12 must all differ.
    assert not torch.equal(pe[0], pe[1])
    assert not torch.equal(pe[0], pe[12])


def test_sincos_pos_2d_requires_d_model_div4():
    with pytest.raises((AssertionError, ValueError)):
        _sincos_pos_2d(24, 12, 66)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/ml/test_policy.py::test_sincos_pos_2d_shape_finite_deterministic -v`
Expected: FAIL — `ImportError: cannot import name '_sincos_pos_2d'`.

- [ ] **Step 3: Implement the helper**

Add `import math` to the top of `ml/policy.py` (with the stdlib imports), and add this module-level function (e.g. just below the imports, above `to_batch`):

```python
def _sincos_pos_2d(h: int, w: int, d_model: int) -> Tensor:
    """Fixed (non-learned) 2D sin/cos positional encoding for an ``h × w`` grid,
    flattened ROW-MAJOR to ``(h*w, d_model)`` so cell index ``row*w + col`` matches
    ``Tensor.flatten(2)``. The first ``d_model/2`` channels encode the row, the second
    half the column. Deterministic (no RNG). Requires ``d_model % 4 == 0``."""
    if d_model % 4 != 0:
        raise ValueError(f"_sincos_pos_2d needs d_model % 4 == 0, got {d_model}")
    d_half = d_model // 2

    def _1d(length: int, dim: int) -> Tensor:
        pos = torch.arange(length, dtype=torch.float32).unsqueeze(1)  # (length, 1)
        idx = torch.arange(0, dim, 2, dtype=torch.float32)  # (dim/2,)
        div = torch.exp(-math.log(10000.0) * idx / dim)  # (dim/2,)
        out = torch.zeros(length, dim, dtype=torch.float32)
        out[:, 0::2] = torch.sin(pos * div)
        out[:, 1::2] = torch.cos(pos * div)
        return out

    row_pe = _1d(h, d_half)  # (h, d_half)
    col_pe = _1d(w, d_half)  # (w, d_half)
    grid = torch.zeros(h, w, d_model, dtype=torch.float32)
    grid[:, :, :d_half] = row_pe.unsqueeze(1)  # row varies along dim 0
    grid[:, :, d_half:] = col_pe.unsqueeze(0)  # col varies along dim 1
    return grid.reshape(h * w, d_model)  # row-major flatten
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/ml/test_policy.py -k sincos -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/policy.py tests/ml/test_policy.py
git commit -m "feat(ml): fixed sin/cos 2D positional-encoding helper (#809)"
```

---

### Task 2: `spatial_tokens` flag — ON-branch `__init__` + `forward()`

**Files:**
- Modify: `ml/policy.py:92-142` (`HangarFitPolicy.__init__` and `forward`)
- Test: `tests/ml/test_policy.py`

**Interfaces:**
- Consumes: `_sincos_pos_2d` (Task 1); `RASTER_CHANNELS`, `TOKEN_DIM`, `ACTION_DIM`, `MAGNITUDE_DIM`, `PolicyOutput`, `to_batch`, `_obs` (existing).
- Produces: `HangarFitPolicy(..., spatial_tokens: bool = False)`; with `spatial_tokens=True`, `forward(batch)` returns a `PolicyOutput` over the `16 + H*W` sequence, with `value_head` consuming `cat(pooled_obj, g, pooled_spatial)`.

- [ ] **Step 1: Write the failing ON-path tests**

Add to `tests/ml/test_policy.py`:

```python
def _model_spatial(seed=0, d_model=64):
    torch.manual_seed(seed)
    return HangarFitPolicy(d_model=d_model, n_layers=2, n_heads=4, spatial_tokens=True).eval()


def test_spatial_on_forward_output_shapes():
    out = _model_spatial()(to_batch([_obs(), _obs()]))
    assert isinstance(out, PolicyOutput)
    assert out.kind_gear_logits.shape == (2, 9)
    assert out.magnitude_bin_logits.shape == (2, 5)
    assert out.value.shape == (2,)


def test_spatial_on_forward_deterministic_and_finite():
    m = _model_spatial(seed=5)
    batch = to_batch([_obs()])
    a, b = m(batch), m(batch)
    assert torch.isfinite(a.value).all()
    assert torch.isfinite(a.kind_gear_logits.nan_to_num(neginf=0.0)).all()
    assert torch.equal(a.value, b.value)
    assert torch.equal(a.kind_gear_logits, b.kind_gear_logits)


def test_spatial_on_still_masks_illegal_kinds():
    batch = to_batch([_obs()])
    out = _model_spatial()(batch)
    legal = batch["legal_action_mask"][0]
    probs = out.kind_gear_logits.softmax(-1)[0]
    assert torch.all(probs[~legal] == 0.0)


def test_feat_mean_equals_adaptive_avgpool():
    # The ON branch computes g = cnn_proj(feat.mean((2,3))); this must equal the old
    # AdaptiveAvgPool2d(1)+Flatten so the global pathway is preserved exactly.
    from torch import nn
    feat = torch.randn(2, 64, 24, 12)
    via_mean = feat.mean(dim=(2, 3))
    via_pool = nn.Flatten()(nn.AdaptiveAvgPool2d(1)(feat))
    assert torch.allclose(via_mean, via_pool, atol=1e-6)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/ml/test_policy.py -k spatial_on -v`
Expected: FAIL — `HangarFitPolicy.__init__() got an unexpected keyword argument 'spatial_tokens'`.

- [ ] **Step 3: Update `__init__`**

In `ml/policy.py`, change the `HangarFitPolicy.__init__` signature and body. Replace the current signature line and the `self.cnn = ...` / `self.value_head = ...` blocks:

```python
    def __init__(
        self,
        *,
        d_model: int = 128,
        n_layers: int = 2,
        n_heads: int = 4,
        cnn_channels: tuple[int, ...] = (16, 32, 64),
        spatial_tokens: bool = False,
    ) -> None:
        super().__init__()
        self.spatial_tokens = spatial_tokens
        convs: list[nn.Module] = []
        in_ch = RASTER_CHANNELS
        for ch in cnn_channels:
            convs += [nn.Conv2d(in_ch, ch, kernel_size=3, stride=2, padding=1), nn.ReLU()]
            in_ch = ch
        if spatial_tokens:
            # keep the feature MAP (B, C, H, W); the spatial path consumes it directly
            self.cnn = nn.Sequential(*convs)
        else:
            self.cnn = nn.Sequential(*convs, nn.AdaptiveAvgPool2d(1), nn.Flatten())
        self.cnn_proj = nn.Linear(cnn_channels[-1], d_model)  # g: -> D
        self.token_proj = nn.Linear(TOKEN_DIM, d_model)  # tokens 24 -> D
        self.fuse = nn.Linear(2 * d_model, d_model)  # concat(token, g) 2D -> D
        layer = nn.TransformerEncoderLayer(
            d_model, n_heads, dim_feedforward=4 * d_model, batch_first=True
        )
        self.encoder = nn.TransformerEncoder(layer, n_layers)
        self.kind_head = nn.Sequential(
            nn.Linear(d_model, d_model), nn.ReLU(), nn.Linear(d_model, ACTION_DIM)
        )
        self.mag_head = nn.Sequential(
            nn.Linear(d_model, d_model), nn.ReLU(), nn.Linear(d_model, MAGNITUDE_DIM)
        )
        value_in = 3 * d_model if spatial_tokens else 2 * d_model
        self.value_head = nn.Sequential(
            nn.Linear(value_in, d_model), nn.ReLU(), nn.Linear(d_model, 1)
        )
        # ON-only modules are registered LAST so the OFF branch's module-registration order
        # (and therefore its param-init RNG stream + state_dict keys) is byte-identical to today.
        if spatial_tokens:
            self.spatial_proj = nn.Linear(cnn_channels[-1], d_model)  # per-cell -> D
```

- [ ] **Step 4: Branch `forward()` and add the spatial forward**

In `ml/policy.py`, at the top of `forward`, dispatch to the spatial path; leave the existing OFF body unchanged below it:

```python
    def forward(self, batch: dict[str, Tensor]) -> PolicyOutput:
        if self.spatial_tokens:
            return self._forward_spatial(batch)
        raster, tokens = batch["raster"], batch["tokens"]
        token_mask, active_index = batch["token_mask"], batch["active_index"]
        legal = batch["legal_action_mask"]
        g = self.cnn_proj(self.cnn(raster))  # (B, D)
        tok = self.token_proj(tokens)  # (B, N, D)
        g_b = g.unsqueeze(1).expand(-1, tok.shape[1], -1)  # (B, N, D)
        fused = self.fuse(torch.cat([tok, g_b], dim=-1))  # (B, N, D)
        emb = self.encoder(fused, src_key_padding_mask=~token_mask)  # (B, N, D)
        idx = active_index.clamp(min=0).view(-1, 1, 1).expand(-1, 1, emb.shape[-1])
        active_emb = emb.gather(1, idx).squeeze(1)  # (B, D)
        m = token_mask.unsqueeze(-1).to(emb.dtype)  # (B, N, 1)
        pooled = (emb * m).sum(1) / m.sum(1).clamp(min=1.0)  # (B, D)
        kind_logits = self.kind_head(active_emb).masked_fill(~legal, float("-inf"))
        mag_logits = self.mag_head(active_emb)
        value = self.value_head(torch.cat([pooled, g], dim=-1)).squeeze(-1)
        return PolicyOutput(kind_logits, mag_logits, value)

    def _forward_spatial(self, batch: dict[str, Tensor]) -> PolicyOutput:
        """ON-branch forward: object tokens cross-attend to per-cell spatial tokens.
        ``g = feat.mean((2,3))`` reproduces the old AdaptiveAvgPool2d(1)+Flatten exactly;
        the spatial tokens + a critic spatial summary are purely additive."""
        raster, tokens = batch["raster"], batch["tokens"]
        token_mask, active_index = batch["token_mask"], batch["active_index"]
        legal = batch["legal_action_mask"]
        feat = self.cnn(raster)  # (B, C, H, W) — no pool/flatten on this branch
        h, w = feat.shape[2], feat.shape[3]
        g = self.cnn_proj(feat.mean(dim=(2, 3)))  # (B, D) == AdaptiveAvgPool2d(1)+Flatten
        pos = _sincos_pos_2d(h, w, g.shape[-1]).to(feat.device, feat.dtype)  # (H*W, D)
        sp = self.spatial_proj(feat.flatten(2).mT) + pos  # (B, H*W, D)
        tok = self.token_proj(tokens)  # (B, N, D)
        n_obj = tok.shape[1]
        g_b = g.unsqueeze(1).expand(-1, n_obj, -1)  # (B, N, D)
        fused_obj = self.fuse(torch.cat([tok, g_b], dim=-1))  # (B, N, D)
        seq = torch.cat([fused_obj, sp], dim=1)  # (B, N + H*W, D)
        sp_pad = torch.zeros(sp.shape[0], sp.shape[1], dtype=torch.bool, device=sp.device)
        pad = torch.cat([~token_mask, sp_pad], dim=1)  # (B, N + H*W); spatial rows valid
        emb = self.encoder(seq, src_key_padding_mask=pad)  # (B, N + H*W, D)
        emb_obj = emb[:, :n_obj, :]  # (B, N, D)
        idx = active_index.clamp(min=0).view(-1, 1, 1).expand(-1, 1, emb_obj.shape[-1])
        active_emb = emb_obj.gather(1, idx).squeeze(1)  # (B, D)
        m = token_mask.unsqueeze(-1).to(emb.dtype)  # (B, N, 1)
        pooled_obj = (emb_obj * m).sum(1) / m.sum(1).clamp(min=1.0)  # (B, D)
        pooled_spatial = emb[:, n_obj:, :].mean(dim=1)  # (B, D) — all spatial rows valid
        kind_logits = self.kind_head(active_emb).masked_fill(~legal, float("-inf"))
        mag_logits = self.mag_head(active_emb)
        value = self.value_head(
            torch.cat([pooled_obj, g, pooled_spatial], dim=-1)
        ).squeeze(-1)
        return PolicyOutput(kind_logits, mag_logits, value)
```

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/ml/test_policy.py -k "spatial_on or feat_mean" -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Run the whole policy suite (catch OFF regressions)**

Run: `pytest tests/ml/test_policy.py -v`
Expected: PASS (all existing + new). The existing OFF tests must be green.

- [ ] **Step 7: Commit**

```bash
git add ml/policy.py tests/ml/test_policy.py
git commit -m "feat(ml): opt-in spatial-token cross-attention forward path (#809)"
```

---

### Task 3: Default-neutrality guards (OFF byte-identical, ON adds exactly the expected params)

**Files:**
- Test: `tests/ml/test_policy.py`

**Interfaces:**
- Consumes: `HangarFitPolicy` with the `spatial_tokens` flag (Task 2).

- [ ] **Step 1: Write the failing tests**

```python
def test_spatial_off_is_byte_identical_to_default():
    # spatial_tokens=False (the default) must reproduce today's net exactly: same params,
    # same module order (same seed -> identical weights), same forward.
    torch.manual_seed(7)
    a = HangarFitPolicy(d_model=64, n_layers=2, n_heads=4, spatial_tokens=False).eval()
    torch.manual_seed(7)
    b = HangarFitPolicy(d_model=64, n_layers=2, n_heads=4).eval()  # default
    sa, sb = a.state_dict(), b.state_dict()
    assert set(sa) == set(sb)
    for k in sa:
        assert torch.equal(sa[k], sb[k]), k
    oa, ob = a(to_batch([_obs()])), b(to_batch([_obs()]))
    assert torch.equal(oa.kind_gear_logits, ob.kind_gear_logits)
    assert torch.equal(oa.magnitude_bin_logits, ob.magnitude_bin_logits)
    assert torch.equal(oa.value, ob.value)


def test_spatial_off_registers_no_spatial_params():
    m = HangarFitPolicy(d_model=64, spatial_tokens=False)
    assert not any("spatial_proj" in k for k in m.state_dict())
    assert m.value_head[0].weight.shape == (64, 128)  # 2*d_model input


def test_spatial_on_adds_spatial_proj_and_widens_value_head():
    m = HangarFitPolicy(d_model=64, spatial_tokens=True)
    assert any("spatial_proj" in k for k in m.state_dict())
    assert m.value_head[0].weight.shape == (64, 192)  # 3*d_model input
```

- [ ] **Step 2: Run to verify (these should PASS immediately — they pin Task 2's contract)**

Run: `pytest tests/ml/test_policy.py -k "spatial_off or spatial_on_adds" -v`
Expected: PASS. If `test_spatial_off_is_byte_identical_to_default` FAILS, the OFF branch perturbed the module-registration order — fix `__init__` so ON-only modules are registered strictly last and OFF builds the identical set.

- [ ] **Step 3: Commit**

```bash
git add tests/ml/test_policy.py
git commit -m "test(ml): pin spatial_tokens default-neutrality (OFF byte-identical) (#809)"
```

---

### Task 4: Checkpoint-flag guard + terminal-observation contract

**Files:**
- Test: `tests/ml/test_policy.py`

**Interfaces:**
- Consumes: `HangarFitPolicy` (both flag values), `_terminal_obs` (existing in the test file).

- [ ] **Step 1: Write the failing tests**

```python
def test_state_dict_does_not_cross_load_across_flag():
    # The two branches have different state_dict key-sets; a strict load must raise rather
    # than silently partial-load (the checkpoint persists policy_kwargs incl. spatial_tokens).
    off = HangarFitPolicy(d_model=64, spatial_tokens=False)
    on = HangarFitPolicy(d_model=64, spatial_tokens=True)
    with pytest.raises(RuntimeError):
        on.load_state_dict(off.state_dict())
    with pytest.raises(RuntimeError):
        off.load_state_dict(on.state_dict())


@pytest.mark.parametrize("spatial", [False, True])
def test_act_on_terminal_observation_raises_both_branches(spatial):
    # PPO never value-forwards a terminal obs (compute_gae zeroes last_value via 1-dones[-1]);
    # act() is the public guard and must reject a terminal obs on BOTH branches, so the
    # 288 always-valid spatial tokens never turn a terminal forward into finite garbage.
    torch.manual_seed(2)
    m = HangarFitPolicy(d_model=64, n_layers=2, n_heads=4, spatial_tokens=spatial).eval()
    obs_t = _terminal_obs()
    assert obs_t.active_index < 0
    with pytest.raises(ValueError, match="terminal"):
        m.act(obs_t, turn_radius_m=8.0)
```

- [ ] **Step 2: Run to verify it passes**

Run: `pytest tests/ml/test_policy.py -k "cross_load or terminal_observation_raises_both" -v`
Expected: PASS (3 cases: cross-load + terminal×2). `act()`'s existing `active_index < 0` guard (`ml/policy.py`) already rejects terminal before any forward, so both branches pass.

- [ ] **Step 3: Commit**

```bash
git add tests/ml/test_policy.py
git commit -m "test(ml): checkpoint-flag guard + terminal contract for spatial_tokens (#809)"
```

---

### Task 5: Thread `--spatial-tokens` through the train CLI

**Files:**
- Modify: `ml/train.py:822-839` (add the argparse flag near `--d-model`) and `ml/train.py:1090-1098` (the `policy_kwargs` assembly)
- Test: manual `--help` check (the smoke in Task 6 exercises the `policy_kwargs → policy` path)

**Interfaces:**
- Consumes: the `policy_kwargs` dict already threaded into `HangarFitPolicy(**policy_kwargs)` and persisted in the checkpoint.
- Produces: a `--spatial-tokens` store_true flag that adds `spatial_tokens=True` to `policy_kwargs` only when set (absent ⇒ default-neutral).

- [ ] **Step 1: Add the argparse flag**

In `ml/train.py`, immediately after the `--n-heads` argument block (around line 839), add:

```python
    p.add_argument(
        "--spatial-tokens",
        action="store_true",
        help="opt-in spatial-token cross-attention policy (#809): object tokens attend to "
        "free-space cells instead of one global-pool summary. Default off = byte-identical net "
        "(a deliberate new-architecture re-baseline when on; the flag is persisted in the "
        "checkpoint's policy_kwargs)",
    )
```

- [ ] **Step 2: Thread it into `policy_kwargs`**

In `ml/train.py`, change the `policy_kwargs` assembly (around line 1090) to include the flag only when set, preserving the default-neutral `or None` fallback:

```python
    policy_kwargs = {
        k: v
        for k, v in (
            ("d_model", args.d_model),
            ("n_layers", args.n_layers),
            ("n_heads", args.n_heads),
            ("spatial_tokens", True if args.spatial_tokens else None),
        )
        if v is not None
    } or None
```

- [ ] **Step 3: Verify the flag is wired**

Run: `python -m ml.train --help 2>&1 | grep -- "--spatial-tokens"`
Expected: the flag's help line prints (non-empty).

- [ ] **Step 4: Lint + type-check the change**

Run: `ruff check ml/train.py && mypy ml/`
Expected: no errors. (Run `mypy` over the whole `ml/` package, not a single file — `follow_imports = "skip"` makes a single-file run resolve cross-module imports as `Any`.)

- [ ] **Step 5: Commit**

```bash
git add ml/train.py
git commit -m "feat(ml): --spatial-tokens train CLI flag, default-neutral (#809)"
```

---

### Task 6: PPO smoke — the ON path trains without NaN

**Files:**
- Test: `tests/ml/test_train_curriculum.py` (alongside the existing `train(...)` smoke at line ~43)

**Interfaces:**
- Consumes: `ml.train.train(seed, iterations, rollout_len, policy_kwargs=…)` → `list[float]` per-iteration mean reward.

- [ ] **Step 1: Write the failing test**

Add to `tests/ml/test_train_curriculum.py` (ensure `import math` is present at the top):

```python
def test_spatial_tokens_ppo_smoke_trains_without_nan():
    # A handful of PPO iterations on the trivial rung with the ON architecture must run and
    # return finite rewards — proves the spatial path trains end-to-end (rollout + GAE +
    # update) with no NaN and no terminal-forward crash.
    history = train(
        seed=0,
        iterations=2,
        rollout_len=32,
        policy_kwargs={"spatial_tokens": True, "d_model": 32, "n_layers": 1, "n_heads": 2},
    )
    assert len(history) == 2
    assert all(isinstance(r, float) and math.isfinite(r) for r in history)
```

- [ ] **Step 2: Run to verify it fails first (red), then implement is N/A**

Run: `pytest tests/ml/test_train_curriculum.py::test_spatial_tokens_ppo_smoke_trains_without_nan -v`
Expected: PASS directly (Task 2 already implemented the ON path; this test is the integration gate). If it FAILS with a shape/NaN error, debug the `_forward_spatial` path (most likely the `pad` mask or `pooled_spatial` shape) before proceeding — do not weaken the assertion.

- [ ] **Step 3: Commit**

```bash
git add tests/ml/test_train_curriculum.py
git commit -m "test(ml): PPO smoke for spatial_tokens ON path (#809)"
```

---

### Task 7: CHANGELOG (conditional) + full local gate

**Files:**
- Modify (conditional): `CHANGELOG.md`
- Verify: ruff, mypy, the `tests/ml/` suite

- [ ] **Step 1: Decide on a CHANGELOG entry**

`ml/` is dev/CI-only (never shipped in the wheel). Check whether recent `ml/`-only training-knob PRs added a `[Unreleased]` entry:

Run: `git log --oneline -20 -- CHANGELOG.md` and inspect whether prior `--r-valid-park` / `--normalize-returns`-class knobs were logged.
- If `ml/` training knobs are conventionally logged → add one line under `[Unreleased] → ### Added`:
  `- \`--spatial-tokens\` opt-in policy architecture (spatial-token cross-attention) for the learned backend (#809).`
- If they follow the dev-tooling no-entry policy → **no CHANGELOG edit**; note this in the PR body.

- [ ] **Step 2: Full lint + type + test gate**

Run:
```bash
ruff check ml/ tests/ml/ && ruff format --check ml/ tests/ml/
mypy ml/
pytest tests/ml/test_policy.py tests/ml/test_train_curriculum.py -v
```
Expected: all green.

- [ ] **Step 3: Run the broader ml/ suite to confirm no determinism/byte-identity regression**

Run: `pytest tests/ml/ -q`
Expected: PASS. Pay attention to `tests/ml/test_ppo.py`, `tests/ml/test_checkpoint.py`, and any byte-identity/determinism canary — the OFF default path must be unaffected.

- [ ] **Step 4: Commit (if CHANGELOG changed)**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): note --spatial-tokens learned-backend knob (#809)"
```

---

## Self-Review

**1. Spec coverage**

| Spec section | Task |
|---|---|
| §4.1 OFF bit-identical | Task 2 (`__init__` order) + Task 3 (byte-identity guard) |
| §4.2 ON modules (cnn_backbone, spatial_proj, fixed sin/cos PE, widened value_head) | Task 1 (PE) + Task 2 |
| §4.3 ON forward data flow (304-seq, masks, gathers) | Task 2 |
| §4.4 critic-summary fold-in (`cat(pooled_obj, g, pooled_spatial)`) | Task 2 |
| §5 determinism/guard (no RNG, default-neutral, checkpoint flag) | Task 2 + Task 3 + Task 4 |
| §6 terminal-forward obligation | Task 4 (terminal contract test; PPO live-only confirmed) |
| §7 2 m tap first | Task 2 (consumes the default `24×12` map; no escalation built) |
| §8 tests (shape, equivalence, g-equiv, determinism, checkpoint, terminal) | Tasks 2–4 |
| §9 `--spatial-tokens` CLI knob | Task 5 |
| §8 PPO smoke | Task 6 |
| §8 CHANGELOG (conditional) | Task 7 |

No gaps.

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code; every run step shows an exact command + expected result.

**3. Type consistency:** `spatial_tokens: bool` consistent across `__init__`/CLI/tests. `_sincos_pos_2d(h, w, d_model) -> Tensor` used identically in Task 1 and Task 2's `_forward_spatial`. `_forward_spatial(self, batch)` matches the `forward` dispatch. `value_head` input width `3*d_model` (ON) / `2*d_model` (OFF) consistent between Task 2 (`__init__`) and Task 3 (`(64,192)`/`(64,128)` assertions). `policy_kwargs` key `spatial_tokens` consistent between Task 5 and Task 6's smoke.
