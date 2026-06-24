# Ego-centric (relative) coordinate encoder — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in ego-centric (relative) coordinate encoder for the learned backend — each object's pose expressed in the active object's SE(2) body frame — behind a default-neutral `--relative-encoder` flag, then gate it on the `trio-notch` ladder.

**Architecture:** *Augment* the 24-dim object-pose token with four new ego-relative columns (`fwd, right, sinΔθ, cosΔθ`) while keeping the existing absolute columns. `TOKEN_DIM` 24 (OFF) / 28 (ON); `SCHEMA_VERSION` 1 (OFF) / 2 (ON). The new columns are written by `ml/encoding.py`; the policy's `token_proj` is resized to consume them; the single CLI flag threads through `policy_kwargs`, and the encoder's `ego_centric` is *derived from the resolved policy_kwargs inside the trainer* so the token width and `token_proj` can never disagree.

**Tech Stack:** Python 3.12, numpy (encoder, no torch), PyTorch (policy/train), pytest. The `ml/` package is dev/CI-only (top-level, never shipped in the wheel).

## Global Constraints

- **OFF byte-identical (4c-ii default-neutrality).** With the flag absent, every output — tokens, schema, policy `state_dict` — is bit-identical to today. Keep the module constants `TOKEN_DIM=24` and `SCHEMA_VERSION=1` as the OFF values; add *new* `EGO_TOKEN_DIM=28` / `SCHEMA_VERSION_EGO=2`. Existing canary tests run on the default (OFF) config and MUST stay green **unchanged** — they are the byte-identity proof.
- **Single source of truth for the flag.** One CLI flag (`--relative-encoder`) → one `policy_kwargs` key (`relative_encoder`). The encoder's `EncoderConfig.ego_centric` is *derived* from that key inside `train()`/`train_curriculum()`, never set independently.
- **Heading convention (ADR-0002 compass).** `heading_deg` from world +y, CW-positive. The token stores `(sin(radians(heading_deg)), cos(radians(heading_deg)))`. The ego basis is `forward=(sinθ_a, cosθ_a)`, `right=(cosθ_a, −sinθ_a)` — determinant −1 by the compass convention; this is *not* the ADR-0002 part-polygon trap (the kinematic frame is self-consistent). The encoding is invariant under proper rigid scene motions (SE(2)).
- **Normalization.** Ego deltas are normalized by `config.pos_ref_m` (=20.0), **not** the 24×48 m raster window (absolute cols keep the window).
- **Guards (run before PR-ready):** `ml-rl-guard` (knob default-neutrality, training reproducibility) + `determinism-guard`. CI's `mypy` covers `src/hangarfit/` only; run `mypy ml/` over the **whole package** locally.
- **`ml/` import:** run from repo root (`cwd=/home/pkuhn/hangarfit` or `PYTHONPATH=$PWD`); torch is the user's `~/.local` GPU build — do **not** `pip install .[train]` (clobbers it). Tests: `pytest tests/ml/`.
- **Spec:** `docs/superpowers/specs/2026-06-24-relative-encoder-ego-centric-design.md`. **Issue:** #827 (epic #607). **Branch:** `feature/827-relative-encoder` (already cut, spec committed).

---

### Task 1: Encoder constants, `EncoderConfig.ego_centric`, dim/schema helpers

**Files:**
- Modify: `ml/encoding.py:22` (SCHEMA_VERSION area), `:38` (TOKEN_DIM), `:50-57` (EncoderConfig)
- Test: `tests/ml/test_encoding.py` (new test function)

**Interfaces:**
- Produces: `EGO_EXTRA_COLS=4`, `EGO_TOKEN_DIM=28`, `SCHEMA_VERSION_EGO=2`; `EncoderConfig.ego_centric: bool` (default `False`); `token_dim(config: EncoderConfig) -> int`; `schema_version_for(config: EncoderConfig) -> int`.

- [ ] **Step 1: Write the failing test**

In `tests/ml/test_encoding.py`, add (near `test_schema_version_and_dims_constants`, ~line 47):

```python
def test_ego_constants_and_helpers():
    # OFF constants are unchanged (byte-identity anchor)
    assert encoding.TOKEN_DIM == 24 and encoding.SCHEMA_VERSION == 1
    # New ego constants
    assert encoding.EGO_EXTRA_COLS == 4
    assert encoding.EGO_TOKEN_DIM == 28
    assert encoding.SCHEMA_VERSION_EGO == 2
    off = EncoderConfig()
    on = EncoderConfig(ego_centric=True)
    assert off.ego_centric is False and on.ego_centric is True
    assert encoding.token_dim(off) == 24 and encoding.token_dim(on) == 28
    assert encoding.schema_version_for(off) == 1
    assert encoding.schema_version_for(on) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_encoding.py::test_ego_constants_and_helpers -v`
Expected: FAIL — `AttributeError: module 'ml.encoding' has no attribute 'EGO_EXTRA_COLS'` (and `ego_centric` not a field).

- [ ] **Step 3: Write minimal implementation**

In `ml/encoding.py`, after `SCHEMA_VERSION = 1` (line 22) add:

```python
SCHEMA_VERSION_EGO = 2  # stamped when EncoderConfig.ego_centric (the augment ego frame, #827)
```

After `TOKEN_DIM = 24` (line 38) add:

```python
EGO_EXTRA_COLS = 4  # ego-relative pose cols (fwd, right, sinΔθ, cosΔθ) appended when ego_centric
EGO_TOKEN_DIM = TOKEN_DIM + EGO_EXTRA_COLS  # 28
```

In `EncoderConfig` (after `pos_ref_m: float = 20.0`, line 57) add the field:

```python
    ego_centric: bool = False  # #827: augment tokens with SE(2) ego-relative pose cols (24..27)
```

After the `EncoderConfig` dataclass (before `ObservationTensors`, ~line 59) add the helpers:

```python
def token_dim(config: EncoderConfig) -> int:
    """Per-token feature width for ``config``: EGO_TOKEN_DIM (28) when ego-centric, else 24."""
    return EGO_TOKEN_DIM if config.ego_centric else TOKEN_DIM


def schema_version_for(config: EncoderConfig) -> int:
    """Observation schema version for ``config``: SCHEMA_VERSION_EGO (2) when ego-centric, else 1."""
    return SCHEMA_VERSION_EGO if config.ego_centric else SCHEMA_VERSION
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ml/test_encoding.py::test_ego_constants_and_helpers tests/ml/test_encoding.py::test_config_defaults -v`
Expected: PASS (both — `test_config_defaults` still green; the new field has a default so its tuple assertions are untouched).

- [ ] **Step 5: Commit**

```bash
git add ml/encoding.py tests/ml/test_encoding.py
git commit -m "feat(827): ego-centric encoder constants + EncoderConfig.ego_centric + dim/schema helpers

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0158YRoWo9BXVv6kAuJ9kC2U"
```

---

### Task 2: `_token_row` + `_tokens` write the ego-relative columns

**Files:**
- Modify: `ml/encoding.py:229-264` (`_token_row`), `:267-310` (`_tokens`)
- Test: `tests/ml/test_encoding.py`

**Interfaces:**
- Consumes: `token_dim()`, `EncoderConfig.ego_centric` (Task 1).
- Produces: `_token_row(..., active_pose: tuple[float,float,float] | None = None)` writes cols `24..27` when `config.ego_centric`; `_tokens` sizes the table by `token_dim(config)` and passes the active object's pose as `active_pose`.

- [ ] **Step 1: Write the failing tests**

In `tests/ml/test_encoding.py`, add (after `test_tokens_status_type_pose_and_padding`, ~line 203). The fixture mirrors the existing one (active `aviat_husky` at `(11, -4, 90°)`, parked `fuji` at `(11, 12, 0°)`):

```python
def test_tokens_ego_relative_cols_worked_example():
    c = EncoderConfig(ego_centric=True)
    fleet = _fuji()
    pl = Placement(plane_id="fuji", x_m=11.0, y_m=12.0, heading_deg=0.0, on_carts=False)
    active = ActiveObject(
        object_id="aviat_husky",
        body=fleet["aviat_husky"],
        pose=Pose(x_m=11.0, y_m=-4.0, heading_deg=90.0),
        on_carts=False,
    )
    obs = _obs(
        parked=(ParkedObject(object_id="fuji", placement=pl),),
        active=active,
        unplaced=("cessna_150",),
    )
    tokens, _mask, active_index = _tokens(obs, fleet, c)
    # width grew to 28
    assert tokens.shape == (16, encoding.EGO_TOKEN_DIM)
    # absolute cols 18..21 are STILL written (augment, not replace): active heading 90 -> sin1 cos0
    assert abs(tokens[1, 20] - 1.0) < 1e-6 and abs(tokens[1, 21]) < 1e-6
    # active object's own ego cols are the origin (0,0,0,1)
    assert active_index == 1
    assert list(tokens[1, 24:28]) == [0.0, 0.0, 0.0, 1.0]
    # parked fuji is 16 m due-north of an east-facing active -> fwd 0, right -16 (to its left),
    # normalized by pos_ref_m=20 -> (0, -0.8); relative heading 0-90=-90 -> sin -1, cos 0
    assert abs(tokens[0, 24] - 0.0) < 1e-6
    assert abs(tokens[0, 25] - (-0.8)) < 1e-6
    assert abs(tokens[0, 26] - (-1.0)) < 1e-6
    assert abs(tokens[0, 27] - 0.0) < 1e-6
    # unplaced row has zero ego cols (no pose)
    assert list(tokens[2, 24:28]) == [0.0, 0.0, 0.0, 0.0]


def test_tokens_off_path_is_24_wide_and_unchanged():
    # OFF config: width stays 24, no ego cols exist (byte-identity anchor)
    c = EncoderConfig()
    fleet = _fuji()
    pl = Placement(plane_id="fuji", x_m=11.0, y_m=12.0, heading_deg=0.0, on_carts=False)
    obs = _obs(parked=(ParkedObject(object_id="fuji", placement=pl),), active=None, unplaced=())
    tokens, _mask, _ai = _tokens(obs, fleet, c)
    assert tokens.shape == (16, encoding.TOKEN_DIM) == (16, 24)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/ml/test_encoding.py::test_tokens_ego_relative_cols_worked_example -v`
Expected: FAIL — `IndexError` (row is 24-wide, col 27 out of range) or shape `(16, 24)` ≠ `(16, 28)`.

- [ ] **Step 3: Write minimal implementation**

In `ml/encoding.py`, change `_token_row`'s signature (line 229-236) to add `active_pose`:

```python
def _token_row(
    body: Aircraft | GroundObject,
    *,
    status: str,
    on_carts: bool,
    pose: tuple[float, float, float] | None,
    config: EncoderConfig,
    active_pose: tuple[float, float, float] | None = None,
) -> np.ndarray:
    row = np.zeros(token_dim(config), dtype=np.float32)
```

Then at the END of `_token_row`, replace the `# reserved 22..23 stay 0` comment/return (line 263-264) with:

```python
    # reserved 22..23 stay 0 (region_side, seq_order)
    if config.ego_centric and pose is not None and active_pose is not None:
        ax, ay, ah = active_pose
        th_a = np.radians(ah)
        s_a, c_a = float(np.sin(th_a)), float(np.cos(th_a))
        dx, dy = pose[0] - ax, pose[1] - ay
        fwd = dx * s_a + dy * c_a  # forward axis (sinθ_a, cosθ_a)
        right = dx * c_a - dy * s_a  # right axis (cosθ_a, -sinθ_a); det-1 compass frame
        dth = np.radians(pose[2] - ah)
        row[TOKEN_DIM + 0] = fwd / config.pos_ref_m
        row[TOKEN_DIM + 1] = right / config.pos_ref_m
        row[TOKEN_DIM + 2] = float(np.sin(dth))
        row[TOKEN_DIM + 3] = float(np.cos(dth))
    return row
```

In `_tokens` (line 276), size the table by config and compute the anchor. Change line 276:

```python
    tokens = np.zeros((config.max_objects, token_dim(config)), dtype=np.float32)
```

After the active block (after line 293, where `active_index`/`a` are set) compute the anchor pose, and pass it to `_token_row` in the build loop (line 308). Insert before the `for oid in obs.unplaced_ids` loop (line 294):

```python
    active_pose = (
        (obs.active.pose.x_m, obs.active.pose.y_m, obs.active.pose.heading_deg)
        if obs.active is not None
        else None
    )
```

And change the build-loop call (line 308) to thread `active_pose`:

```python
        tokens[i] = _token_row(
            body, status=status, on_carts=on_carts, pose=pose, config=config, active_pose=active_pose
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/ml/test_encoding.py -k "ego_relative or off_path_is_24 or status_type_pose or wing_and_movement" -v`
Expected: PASS — the two new tests pass AND the existing `test_tokens_status_type_pose_and_padding` / `test_tokens_wing_and_movement_one_hots` stay green (OFF byte-identity).

- [ ] **Step 5: Commit**

```bash
git add ml/encoding.py tests/ml/test_encoding.py
git commit -m "feat(827): _token_row/_tokens write SE(2) ego-relative pose cols 24..27

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0158YRoWo9BXVv6kAuJ9kC2U"
```

---

### Task 3: `encode`/`encode_dynamic` schema stamp + the SE(2) invariance property test

**Files:**
- Modify: `ml/encoding.py:436` (`encode` stamp), `:463` (`encode_dynamic` stamp)
- Test: `tests/ml/test_encoding.py`

**Interfaces:**
- Consumes: `schema_version_for()` (Task 1), the ego token columns (Task 2).
- Produces: `encode()`/`encode_dynamic()` stamp `schema_version=schema_version_for(config)`.

- [ ] **Step 1: Write the failing tests**

In `tests/ml/test_encoding.py`, add. The invariance test applies a rigid SE(2) transform (rotate by α about the origin, then translate) to **every** pose including the active object, and asserts the ego cols are invariant while the absolute cols move:

```python
def test_encode_ego_schema_is_2():
    fleet = _fuji()
    pl = Placement(plane_id="fuji", x_m=11.0, y_m=12.0, heading_deg=0.0, on_carts=False)
    active = ActiveObject(
        object_id="aviat_husky", body=fleet["aviat_husky"],
        pose=Pose(x_m=11.0, y_m=-4.0, heading_deg=90.0), on_carts=False,
    )
    obs = _obs(parked=(ParkedObject(object_id="fuji", placement=pl),), active=active, unplaced=())
    out = encode(obs, _hangar(), fleet, EncoderConfig(ego_centric=True))
    assert out.schema_version == 2
    assert out.tokens.shape == (16, 28)


def test_ego_cols_are_se2_invariant():
    import math

    fleet = _fuji()

    def build(rot_deg: float, tx: float, ty: float):
        # rotate (x,y) by rot_deg in the compass sense and shift headings by the same amount,
        # so the WHOLE scene undergoes one rigid SE(2) motion.
        a = math.radians(rot_deg)
        ca, sa = math.cos(a), math.sin(a)

        def xf(x, y):
            return (ca * x - sa * y + tx, sa * x + ca * y + ty)

        px, py = xf(11.0, 12.0)
        axp, ayp = xf(11.0, -4.0)
        pl = Placement(plane_id="fuji", x_m=px, y_m=py, heading_deg=0.0 + rot_deg, on_carts=False)
        active = ActiveObject(
            object_id="aviat_husky", body=fleet["aviat_husky"],
            pose=Pose(x_m=axp, y_m=ayp, heading_deg=90.0 + rot_deg), on_carts=False,
        )
        obs = _obs(parked=(ParkedObject(object_id="fuji", placement=pl),), active=active, unplaced=())
        return _tokens(obs, fleet, EncoderConfig(ego_centric=True))[0]

    base = build(0.0, 0.0, 0.0)
    moved = build(37.0, 5.0, -8.0)
    # ego cols (24..27) of the parked object are unchanged under the rigid motion
    assert np.allclose(base[0, 24:28], moved[0, 24:28], atol=1e-5)
    # absolute cols (18..21) DO change (different world pose)
    assert not np.allclose(base[0, 18:22], moved[0, 18:22], atol=1e-3)
```

> Note: reuse the module's existing `_hangar()` / `_fuji()` / `_obs()` test helpers. If `_hangar()` is not already a helper in this file, use the same hangar fixture `test_encode_full_shapes_and_meta` (line 273) uses.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/ml/test_encoding.py::test_encode_ego_schema_is_2 -v`
Expected: FAIL — `assert out.schema_version == 2` fails (encode still stamps `SCHEMA_VERSION`=1).

- [ ] **Step 3: Write minimal implementation**

In `ml/encoding.py`, change the `encode()` return (line 436) from `schema_version=SCHEMA_VERSION,` to:

```python
        schema_version=schema_version_for(config),
```

and the identical line in `encode_dynamic()` (line 463) the same way.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/ml/test_encoding.py -v`
Expected: PASS — all of `test_encoding.py`, including the unchanged `test_encode_full_shapes_and_meta` (`out.schema_version == 1` on the default config) and `test_encode_dynamic_nonraster_fields_match_full_encode` (schema parity holds: both stamp via the same `config`).

- [ ] **Step 5: Commit**

```bash
git add ml/encoding.py tests/ml/test_encoding.py
git commit -m "feat(827): stamp SCHEMA_VERSION_EGO + prove SE(2) ego-col invariance

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0158YRoWo9BXVv6kAuJ9kC2U"
```

---

### Task 4: Policy `relative_encoder` kwarg resizes `token_proj`

**Files:**
- Modify: `ml/policy.py:18` (import), `:119-143` (`__init__` signature + `token_proj`)
- Test: `tests/ml/test_policy.py`

**Interfaces:**
- Consumes: `EGO_TOKEN_DIM` (Task 1).
- Produces: `HangarFitPolicy(..., relative_encoder: bool = False)`; `token_proj.in_features` = 28 when on, 24 when off; `self.relative_encoder` attribute.

- [ ] **Step 1: Write the failing tests**

In `tests/ml/test_policy.py`, add:

```python
def test_policy_off_is_byte_identical():
    import torch
    torch.manual_seed(0)
    a = HangarFitPolicy()
    torch.manual_seed(0)
    b = HangarFitPolicy(relative_encoder=False)
    sa, sb = a.state_dict(), b.state_dict()
    assert sa.keys() == sb.keys()
    assert all(torch.equal(sa[k], sb[k]) for k in sa)
    assert a.token_proj.in_features == 24


def test_policy_relative_encoder_sizes_token_proj():
    p = HangarFitPolicy(relative_encoder=True)
    assert p.relative_encoder is True
    assert p.token_proj.in_features == 28


def test_policy_relative_forward_consumes_28_wide_tokens():
    import numpy as np
    import torch
    from ml.encoding import EncoderConfig, encode
    from ml.policy import to_batch
    # build one ego observation (reuse a minimal env/obs fixture available in tests/ml)
    obs_t = _one_ego_observation()  # helper: encode(..., EncoderConfig(ego_centric=True))
    assert obs_t.tokens.shape[-1] == 28
    batch = to_batch([obs_t])
    out = HangarFitPolicy(relative_encoder=True)(batch)
    assert out.kind_logits.shape == (1, 9) and out.magnitude_bin_logits.shape == (1, 5)
```

> `_one_ego_observation()`: if `tests/ml/test_policy.py` already has an observation fixture/helper, wrap it to pass `EncoderConfig(ego_centric=True)`. Otherwise build the same `_fuji()`/`_obs()` fixture as `test_encoding.py` and call `encode(obs, hangar, fleet, EncoderConfig(ego_centric=True))`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/ml/test_policy.py::test_policy_relative_encoder_sizes_token_proj -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'relative_encoder'`.

- [ ] **Step 3: Write minimal implementation**

In `ml/policy.py`, extend the import (line 18, `from ml.encoding import (`) to include `EGO_TOKEN_DIM` alongside `TOKEN_DIM`, `RASTER_CHANNELS`.

In `HangarFitPolicy.__init__`, add the kwarg (after `spatial_tokens: bool = False,`, line 126):

```python
        relative_encoder: bool = False,
```

After `self.spatial_tokens = spatial_tokens` (line 129) add:

```python
        self.relative_encoder = relative_encoder
```

Change `token_proj` (line 143) from `nn.Linear(TOKEN_DIM, d_model)` to:

```python
        token_in = EGO_TOKEN_DIM if relative_encoder else TOKEN_DIM
        self.token_proj = nn.Linear(token_in, d_model)  # tokens 24 (or 28 ego) -> D
```

> Byte-identity: when `relative_encoder=False`, `token_in == TOKEN_DIM == 24`, so `token_proj` and every later module's init RNG draw are identical to today. `relative_encoder` is a plain bool attribute (no RNG). The `spatial_tokens` ON-only block stays last (unchanged).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/ml/test_policy.py -v`
Expected: PASS — the three new tests pass and all existing policy tests stay green (OFF byte-identity).

- [ ] **Step 5: Commit**

```bash
git add ml/policy.py tests/ml/test_policy.py
git commit -m "feat(827): HangarFitPolicy --relative-encoder sizes token_proj 24->28

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0158YRoWo9BXVv6kAuJ9kC2U"
```

---

### Task 5: CLI flag + `policy_kwargs` + encoder derivation (end-to-end)

**Files:**
- Modify: `ml/train.py:55` (import `replace`), `:288-308` (`train()`), `:451-504` (`train_curriculum()`), `:895-902` (argparse), `:1187-1196` (policy_kwargs)
- Test: `tests/ml/test_train.py` (or the existing train smoke test file)

**Interfaces:**
- Consumes: `EncoderConfig.ego_centric` (Task 1), `relative_encoder` policy kwarg (Task 4).
- Produces: `--relative-encoder` CLI flag; `policy_kwargs["relative_encoder"]=True` when set; `enc.ego_centric` derived from `policy_kwargs["relative_encoder"]` in both trainers.

- [ ] **Step 1: Write the failing tests**

In `tests/ml/test_train.py` add a flag-threading unit and an end-to-end smoke (the smoke is the key integration proof — encoder 28-wide tokens consumed by a `relative_encoder=True` policy without a shape crash):

```python
def test_relative_encoder_flag_threads_to_policy_kwargs():
    import ml.train as t
    parser = t.build_parser()
    args = parser.parse_args(
        ["solve_stub", "--schedule", "trivial", "--relative-encoder", "--iterations", "1"]
    )
    assert args.relative_encoder is True


def test_train_trivial_relative_encoder_smoke(tmp_path):
    # 1-iteration trivial run with the flag on: proves encoder(28) <-> policy(token_proj 28) agree
    import ml.train as t
    save = tmp_path / "p.pt"
    t.train(
        seed=0, iterations=1, rollout_len=4,
        policy_kwargs={"relative_encoder": True}, log=False, save=str(save),
    )
    assert save.exists()
```

> Adapt the argparse positional/flags to the real `build_parser()` signature (check how an existing `test_train.py` smoke constructs `parser.parse_args([...])`). If `build_parser` is named differently, use the actual entry (`grep -n "def build_parser\|add_argument(\"--schedule\"" ml/train.py`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/ml/test_train.py::test_relative_encoder_flag_threads_to_policy_kwargs -v`
Expected: FAIL — `AttributeError: 'Namespace' object has no attribute 'relative_encoder'`.

- [ ] **Step 3: Write minimal implementation**

In `ml/train.py`:

(a) Import `replace` — change the top-of-file import. Add near the stdlib imports:

```python
from dataclasses import replace
```

(b) Argparse — after the `--spatial-tokens` block (line 902) add:

```python
    p.add_argument(
        "--relative-encoder",
        action="store_true",
        help="opt-in ego-centric augment encoder (#827, ADR-0028 re-open trigger #2): each "
        "object's pose is ALSO written in the active object's SE(2) body frame (4 extra token "
        "cols). Default off = byte-identical (TOKEN_DIM 24, schema 1); a deliberate representation "
        "re-baseline when on (TOKEN_DIM 28, schema 2); the flag is persisted in the checkpoint's "
        "policy_kwargs",
    )
```

(c) policy_kwargs — in the dict comprehension (line 1187-1196) add the key after `spatial_tokens`:

```python
            ("relative_encoder", True if args.relative_encoder else None),
```

(d) `train()` derivation — after `policy = HangarFitPolicy(**(policy_kwargs or {})).to(...)` (line 308) add:

```python
    enc = replace(enc, ego_centric=bool((policy_kwargs or {}).get("relative_encoder", False)))
```

(e) `train_curriculum()` derivation — after the load/else block resolves `saved_policy_kwargs` and `policy` (after line 503, before `completed_set = ...` line 504) add:

```python
    # Encoder ego-frame is DERIVED from the (post-load authoritative) architecture, so the token
    # width and the policy's token_proj can never disagree, and a loaded ego checkpoint auto-uses
    # an ego encoder regardless of whether --relative-encoder was re-passed (#827).
    enc = replace(enc, ego_centric=bool(saved_policy_kwargs.get("relative_encoder", False)))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/ml/test_train.py -k "relative_encoder" -v`
Expected: PASS — both tests; the smoke completes a 1-iteration trivial run, proving encoder/policy agreement end-to-end.

- [ ] **Step 5: Commit**

```bash
git add ml/train.py tests/ml/test_train.py
git commit -m "feat(827): --relative-encoder CLI flag; derive encoder ego-frame from policy_kwargs

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0158YRoWo9BXVv6kAuJ9kC2U"
```

---

### Task 6: Audit eval / benchmark / gate consumers for encoder derivation

**Files:**
- Inspect/Modify (as the audit finds): `ml/eval.py`, `ml/benchmark.py`, `ml/gate.py` (if present), any standalone consumer that builds its own `EncoderConfig` to evaluate a **loaded** checkpoint.
- Test: `tests/ml/test_eval.py` (or wherever standalone eval is tested)

**Interfaces:**
- Produces: any standalone consumer that loads a checkpoint and builds an encoder derives `ego_centric` from the checkpoint's `policy_kwargs.get("relative_encoder", False)`, mirroring Task 5's trainer derivation.

- [ ] **Step 1: Audit**

Run:
```bash
grep -rnE "EncoderConfig\(|load_checkpoint|policy_kwargs|encode\(|encode_dynamic\(" ml/eval.py ml/benchmark.py ml/gate.py 2>/dev/null
```
For each site that (a) loads a checkpoint AND (b) constructs an `EncoderConfig()` to encode observations for that loaded policy: it must derive `ego_centric` from the loaded `policy_kwargs`. If a consumer receives its `EncoderConfig` from the caller (e.g. the curriculum passes `enc` already derived in Task 5), **no change** is needed there — record that in the commit message.

- [ ] **Step 2: Write the failing test (only if a site needs changing)**

If e.g. `ml/eval.py` builds its own default encoder, add to `tests/ml/test_eval.py`:

```python
def test_eval_of_ego_checkpoint_uses_ego_encoder(tmp_path):
    # train+save a 1-iteration ego checkpoint, then standalone-eval it without re-passing the flag
    import ml.train as t
    save = tmp_path / "ego.pt"
    t.train(seed=0, iterations=1, rollout_len=4,
            policy_kwargs={"relative_encoder": True}, log=False, save=str(save))
    from ml.eval import evaluate_checkpoint  # adapt to the real entry point
    result = evaluate_checkpoint(str(save), episodes=1)  # must NOT raise a 24-vs-28 shape error
    assert result is not None
```

- [ ] **Step 3: Implement the derivation at each site found**

At each loading site, after the checkpoint's `policy_kwargs` are known:

```python
enc = replace(enc, ego_centric=bool(ckpt.policy_kwargs.get("relative_encoder", False)))
```

- [ ] **Step 4: Run the relevant tests**

Run: `pytest tests/ml/test_eval.py tests/ml/test_benchmark.py -v`
Expected: PASS (or, if no site needed changing, the existing suites stay green and this task is a verified no-op).

- [ ] **Step 5: Commit**

```bash
git add -A ml/ tests/ml/
git commit -m "feat(827): derive ego encoder from checkpoint policy_kwargs in standalone eval/benchmark

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0158YRoWo9BXVv6kAuJ9kC2U"
```

> ONNX export (`--save-onnx`) of an ego policy needs a 28-wide dummy input; that path is **out of scope for the trio-notch gate** (the gate trains + evals in-process, no ONNX). Track it as a follow-up under #827 only if ego weights are later shipped through the #706 inference seam.

---

### Task 7: Docs, CHANGELOG, ml/README knob table

**Files:**
- Modify: `docs/architecture/ml-observation-schema.md`, `CHANGELOG.md`, `ml/README.md`

**Interfaces:** none (documentation).

- [ ] **Step 1: Observation-schema doc**

In `docs/architecture/ml-observation-schema.md`, add a "Schema 2 — ego-centric augment (#827, opt-in)" subsection documenting: cols `24..27` = `(fwd, right, sinΔθ, cosΔθ)` in the active object's SE(2) body frame, normalized by `pos_ref_m`; `SCHEMA_VERSION_EGO=2`; OFF (schema 1) byte-identical; the det-−1 compass-basis note.

- [ ] **Step 2: CHANGELOG**

In `CHANGELOG.md` under `[Unreleased] → ### Added`, add:

```markdown
- Learned backend: opt-in `--relative-encoder` ego-centric observation encoder (#827, ADR-0028
  re-open trigger #2) — augments object pose tokens with SE(2) ego-relative coordinates (default
  off = byte-identical; schema 2 when on). Dev/CI-only (`ml/`), not shipped in the wheel.
```

- [ ] **Step 3: ml/README knob table**

In `ml/README.md`, add a row for `--relative-encoder` to the 4c-ii training-knob table (default-neutral; OFF byte-identical) with a one-line description and a pointer to the spec.

- [ ] **Step 4: Verify docs render / no broken refs**

Run: `grep -n "relative-encoder\|SCHEMA_VERSION_EGO\|#827" docs/architecture/ml-observation-schema.md CHANGELOG.md ml/README.md`
Expected: each file shows the new content.

- [ ] **Step 5: Commit**

```bash
git add docs/architecture/ml-observation-schema.md CHANGELOG.md ml/README.md
git commit -m "docs(827): document ego-centric encoder schema 2 + --relative-encoder knob

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0158YRoWo9BXVv6kAuJ9kC2U"
```

---

### Task 8: Full local verification + guards + PR

**Files:** none (verification + workflow)

- [ ] **Step 1: Full ml test suite + lint + types**

```bash
pytest tests/ml/ -v
ruff check ml/ tests/ml/ && ruff format --check ml/ tests/ml/
mypy ml/        # whole package (follow_imports=skip needs the full run)
```
Expected: all green. (Existing OFF canaries green unchanged = byte-identity proof.)

- [ ] **Step 2: Determinism + default-neutrality spot check**

```bash
# OFF byte-identity: ego_centric off encodes exactly as TOKEN_DIM=24 / schema 1
pytest tests/ml/test_encoding.py -k "off_path or schema or status_type_pose or deterministic" -v
```
Expected: PASS.

- [ ] **Step 3: Dispatch the guards (read-only, on origin refs)**

Dispatch `ml-rl-guard` and `determinism-guard` on the branch diff (`git diff origin/develop...HEAD`). Address any finding as its own commit.

- [ ] **Step 4: Push, open draft PR**

```bash
git push -u origin feature/827-relative-encoder
gh pr create --draft --base develop \
  --title "feat(607): ego-centric relative coordinate encoder lever (#827)" \
  --body "Closes #827 ..."   # body: spec link, ADR-0028 trigger #2, OFF byte-identical, gate plan
```
Set assignee/labels/milestone via `gh api -X PATCH` (per project convention). Run the `/pr-review` arc (code-reviewer + ml-rl-guard + determinism-guard + comment-analyzer for the docs), one inline thread per finding, fix + resolve, then `gh pr ready`.

---

### Task 9 (post-merge, SUPERVISED): the trio-notch gate

**This is the experiment, not code — run after the PR merges, supervised, from a worktree pinned to the branch (the lazy-fixture branch-switch gotcha).**

- [ ] **Pre-register** (write to the gate result doc BEFORE running): rung = `trio-notch` ladder; arms = OFF (control) vs `--relative-encoder` (treatment); 2 seeds; metric = windowed-final valid-placed `vp` (+ first-park `fp`); **WIN** = `vp` materially breaks the `≈0.333` fixed point; **KILL** = `vp` stays `≈0.333`.

- [ ] **Run** (GPU, 2 seeds, both arms; the `ml.gate` harness as in prior levers):

```bash
# from a worktree pinned to feature/827-relative-encoder (cwd inside it so `ml` resolves there)
python -m ml.train --schedule curriculum --anchor-trio-notch --relative-encoder \
  --n-envs auto --seed <S> --checkpoint-out ck-ego-s<S>.pt --metrics-out metrics-ego-s<S>.jsonl ...
# control arm: identical command WITHOUT --relative-encoder
```

- [ ] **Confound watch:** `epochs_run` parity between arms (#816 lesson); OFF arm reproduces the established control baseline before trusting the contrast.

- [ ] **On KILL:** record as the **6th refuted lever** in `ml/README.md` + auto-memory; encoder stays opt-in infra (do not re-run on notch). **On WIN:** proceed to the ADR-0028 trigger-#1 follow-up (reach-rate vs RR-MC on witness-absent, extend #711) — necessary for the real re-open.

---

## Self-Review

**1. Spec coverage:**
- §2 scope (encoder + flag + gate) → Tasks 1–8 (code) + Task 9 (gate). ✓
- §3 decisions (ego/augment/full-SE(2)/TOKEN_DIM 24→28/schema 1→2/locus EncoderConfig+policy_kwargs) → Tasks 1,2,4,5. ✓
- §4 encoding (cols 24..27, basis, pos_ref_m norm, active=(0,0,0,1), unplaced=0) → Task 2 + its tests. ✓
- §5 flag/schema/checkpoint → Tasks 3,4,5 (+ load-mismatch reuse of existing `policy.py:466`). ✓
- §6 canary re-baseline → realized as "keep OFF constants, add EGO_* constants; existing OFF tests stay green + new ON tests" (Tasks 1–4). Documented refinement: NO re-baseline of existing assertions is needed (cleaner than the spec assumed). ✓
- §6 guards (ml-rl-guard, determinism-guard) → Task 8. ✓
- §7 files touched → Tasks 1–7 cover all listed except ONNX export (explicitly deferred in Task 6, out of gate scope). ✓
- §8 gate methodology → Task 9. ✓

**2. Placeholder scan:** No "TBD/TODO/handle edge cases". The two `_one_ego_observation()` / `evaluate_checkpoint` references include explicit adaptation instructions to the real fixtures/entry points (the test files' exact helper names are discovered at implementation time, not invented here). ✓

**3. Type consistency:** `token_dim`/`schema_version_for`/`EGO_TOKEN_DIM`/`ego_centric`/`relative_encoder` are used with identical names and signatures across Tasks 1→6. `replace` (dataclasses) used identically in Tasks 5–6. ✓
