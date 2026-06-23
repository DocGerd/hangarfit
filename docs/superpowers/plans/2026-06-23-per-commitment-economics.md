# Per-commitment economics lever (`r_valid_progress`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a default-neutral, banked marginal valid-coverage reward credit (`r_valid_progress`) that pays the policy for each *new* validly-placed object, to break the `trio-notch` marginal-commitment-economics plateau.

**Architecture:** One additive term in `ml/reward.py::step_reward`, gated on the existing `park_valid` product-checker bool and scaled by a new `RewardContext.valid_park_count` field the env populates from `len(self._parked)`. One new `RewardWeights` knob (`r_valid_progress`, default `0.0` → byte-identical) and one `--r-valid-progress` CLI flag. No SCHEMA / observation / policy change.

**Tech Stack:** Python 3.12, the `ml/` RL workspace (torch-free for `reward.py`/`env.py`/`types.py`), pytest. Spec: `docs/superpowers/specs/2026-06-23-per-commitment-economics-design.md`.

## Global Constraints

- **Default-neutral (4c-ii):** `r_valid_progress` defaults `0.0`; with it 0.0 the reward stream is **byte-identical** to today. No existing determinism / byte-identity canary may need re-baselining.
- **Determinism (ADR-0027):** the reward stays a pure function of `(ctx, weights)` — `float·int·bool` arithmetic, no RNG/clock.
- **Must not reopen the invalid-pile basin:** the new term is gated on `park_valid is True` (the `collisions.check` + Caddy-egress product checker). An invalid/overlapping Park pays exactly `0.0`. Do not weaken `validity_conditional_terminal` or the L4 reward clip.
- **`ml-rl-guard` applies** (touches `ml/reward.py`, `ml/types.py`, `ml/env.py`, `tests/ml/`). Run `ruff`/`mypy ml/` (whole package) + `pytest tests/ml/`.
- **Run from the repo root** (the top-level `ml/` package is not on the editable install's path): `pytest tests/ml/...`.
- Commit subjects end with `Part of #812`.

---

### Task 1: Reward term + knob + context field (`ml/reward.py`, `ml/types.py`)

**Files:**
- Modify: `ml/types.py` (add `r_valid_progress` to `RewardWeights`, after `validity_conditional_terminal` ~line 138)
- Modify: `ml/reward.py` (add `valid_park_count` to `RewardContext` after `first_valid_now` ~line 34; add the `valid_progress` term in `step_reward` ~line 84)
- Test: `tests/ml/test_reward.py` (append a new section)

**Interfaces:**
- Produces: `RewardWeights.r_valid_progress: float = 0.0`; `RewardContext.valid_park_count: int = 0`; `step_reward` returns `… + valid_progress` where `valid_progress = w.r_valid_progress * max(0, ctx.valid_park_count - 1)` when `ctx.park_valid` is `True`, else `0.0`.
- Consumes: existing `RewardContext.park_valid: bool | None`.

- [ ] **Step 1: Write the failing tests** — append to `tests/ml/test_reward.py` (the file already has the `_ctx(**kw)` helper and imports `step_reward`, `RewardContext`, `RewardWeights`):

```python
# ---------------------------------------------------------------------------
# #812 — banked marginal valid-coverage credit (r_valid_progress)
# ---------------------------------------------------------------------------


def test_r_valid_progress_default_zero_is_byte_identical():
    # park_valid True with a count set, but the knob default 0.0 -> no change.
    ctx = _ctx(park_valid=True, valid_park_count=3)
    assert step_reward(ctx, RewardWeights()) == step_reward(
        ctx, RewardWeights(r_valid_progress=0.0)
    )


def test_r_valid_progress_pays_marginal_count_beyond_the_freebie():
    w = RewardWeights(r_valid_progress=8.0)
    base = RewardWeights(r_valid_progress=0.0)
    # 1st valid object (count 1) pays 0; 2nd pays 8; 3rd pays 16 (max(0, n-1) * 8).
    for count, expected in ((1, 0.0), (2, 8.0), (3, 16.0)):
        ctx = _ctx(park_valid=True, valid_park_count=count)
        assert step_reward(ctx, w) - step_reward(ctx, base) == pytest.approx(expected)


def test_r_valid_progress_not_paid_on_invalid_park():
    # park_valid False (an overlapping pile) earns ZERO coverage credit -> firewall.
    w = RewardWeights(r_valid_progress=8.0)
    base = RewardWeights(r_valid_progress=0.0)
    ctx = _ctx(park_valid=False, valid_park_count=0, overlap_m2=0.05)
    assert step_reward(ctx, w) == step_reward(ctx, base)


def test_r_valid_progress_not_paid_on_nonpark_step():
    # park_valid None (a movement step) -> term structurally absent.
    w = RewardWeights(r_valid_progress=8.0)
    base = RewardWeights(r_valid_progress=0.0)
    ctx = _ctx(park_valid=None)
    assert step_reward(ctx, w) == step_reward(ctx, base)


def test_r_valid_progress_clip_headroom_at_recipe_scale():
    # The banked valid-Park bonus (valid_park + valid_progress + first_valid) must stay within
    # reward_clip=50 at the recipe scale so GAE never sees it truncated:
    # r_valid_park 30 + r_valid_progress 8*(3-1) + r_first_valid 0 = 46 <= 50.
    REWARD_CLIP = 50.0
    w = RewardWeights(r_valid_park=30.0, r_valid_progress=8.0, r_first_valid=0.0)
    off = RewardWeights(r_valid_park=0.0, r_valid_progress=0.0, r_first_valid=0.0)
    ctx = _ctx(park_valid=True, valid_park_count=3, overlap_m2=0.0, intrusion_m2=0.0)
    bonus = step_reward(ctx, w) - step_reward(ctx, off)
    assert bonus == pytest.approx(46.0)
    assert bonus <= REWARD_CLIP
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/ml/test_reward.py -k r_valid_progress -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'r_valid_progress'` (and `valid_park_count`), because the knob and field don't exist yet.

- [ ] **Step 3: Add the `valid_park_count` field to `RewardContext`** — in `ml/reward.py`, immediately after the `first_valid_now: bool = False` field (~line 34):

```python
    first_valid_now: bool = False
    # The number of validly-co-placed objects at THIS Park step (len(_parked) on a Park where
    # the whole layout is valid, else 0). Consumed only when RewardWeights.r_valid_progress > 0.
    # Internal reward input — not an observation. (#812)
    valid_park_count: int = 0
```

- [ ] **Step 4: Add the knob to `RewardWeights`** — in `ml/types.py`, immediately after the `validity_conditional_terminal: bool = False` field (~line 138):

```python
    validity_conditional_terminal: bool = False
    # Banked per-valid-Park credit, scaled by the marginal valid-object count beyond the freebie:
    # pays r_valid_progress * max(0, valid_park_count - 1) on a Park where the WHOLE layout is
    # valid (valid_park_count = # driven-in objects). 0.0 -> term identically 0 -> byte-identical.
    # The 1st valid object pays 0 (r_first_valid owns the breakthrough), the 2nd pays
    # r_valid_progress, the 3rd 2x. Banked per-step so it survives GAE while the #714 terminal
    # flag collapses; gated on park_valid so an invalid pile never pays. (#812)
    r_valid_progress: float = 0.0
```

- [ ] **Step 5: Add the `valid_progress` term to `step_reward`** — in `ml/reward.py`, replace the final two lines of `step_reward` (the `first_valid = …` line and the `return …` line, ~lines 83-84):

```python
    first_valid = w.r_first_valid if ctx.first_valid_now else 0.0
    # Banked marginal valid-coverage credit (#812): pays only on a Park where the whole layout
    # is valid (park_valid True), scaled by the marginal valid-object count beyond the freebie.
    # park_valid None (non-Park) and False (invalid pile) both pay 0 -> the pile firewall.
    valid_progress = (
        w.r_valid_progress * float(max(0, ctx.valid_park_count - 1)) if ctx.park_valid else 0.0
    )
    return hard + movement + soft + terminal + shaping + valid_park + first_valid + valid_progress
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/ml/test_reward.py -k r_valid_progress -v`
Expected: PASS (5 tests).

- [ ] **Step 7: Run the full reward suite + lint/type to confirm no regression (byte-identity canaries)**

Run: `pytest tests/ml/test_reward.py -v && ruff check ml/reward.py ml/types.py && mypy ml/`
Expected: PASS; `mypy ml/` clean (run over the whole `ml/` package, not a single file).

- [ ] **Step 8: Commit**

```bash
git add ml/reward.py ml/types.py tests/ml/test_reward.py
git commit -m "feat(ml): banked marginal valid-coverage reward credit (r_valid_progress)

Add a default-neutral RewardWeights.r_valid_progress knob and a
RewardContext.valid_park_count field; step_reward gains one term
r_valid_progress*max(0, valid_park_count-1), paid only on a valid Park
(park_valid True). 1st valid object pays 0, 2nd pays the weight, 3rd 2x.
Knob default 0.0 -> byte-identical. Gated on the product checker so an
invalid pile pays 0.

Part of #812"
```

---

### Task 2: Env wiring + `info.terms` diagnostic (`ml/env.py`)

**Files:**
- Modify: `ml/env.py` (populate `valid_park_count` in the Park-branch `RewardContext` ~line 293-310; add a `valid_park_count` key to the `_info` terms dict ~line 396)
- Test: `tests/ml/test_env.py` (append one test)

**Interfaces:**
- Consumes: `RewardContext.valid_park_count` (from Task 1), existing `park_valid` and `len(self._parked)`.
- Produces: on a Park step the env passes `valid_park_count = len(self._parked) if park_valid else 0`; `StepInfo.terms["valid_park_count"]` exposes `float(ctx.valid_park_count)` for diagnosis.

- [ ] **Step 1: Write the failing test** — append to `tests/ml/test_env.py` (imports already present: `HangarFitEnv`, `Park`, `Primitive`, `_env`):

```python
def test_env_threads_valid_park_count_into_info_gated_on_validity():
    # On a Park step, valid_park_count == placed when the WHOLE layout is valid, else 0
    # (the #812 firewall: an invalid park carries zero marginal coverage count).
    env = _env()
    env.reset()
    for _ in range(20):
        if env._active_pose is not None and env._active_pose.y_m >= 1.0:
            break
        env.step(Primitive(kind="S", magnitude=1.0, gear=1))
    _, _, _, info = env.step(Park())
    expected = info.placed if info.valid else 0
    assert info.terms["valid_park_count"] == float(expected)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/ml/test_env.py::test_env_threads_valid_park_count_into_info_gated_on_validity -v`
Expected: FAIL — `KeyError: 'valid_park_count'` (the `_info` terms dict has no such key yet).

- [ ] **Step 3: Populate `valid_park_count` on the Park-branch context** — in `ml/env.py`, in the `RewardContext(...)` built inside the `if isinstance(action, Park):` branch, add the field right after `first_valid_now=first_valid_now,` (~line 309):

```python
                first_valid_now=first_valid_now,
                # len(_parked) includes the just-appended pose; 0 on an invalid park so the
                # banked coverage credit never pays a pile (#812).
                valid_park_count=len(self._parked) if park_valid else 0,
```

- [ ] **Step 4: Surface it in `info.terms`** — in `ml/env.py::_info`, add one entry to the `terms` dict right after `"terminal_fraction": ctx.terminal_fraction or 0.0,` (~line 396):

```python
                "terminal_fraction": ctx.terminal_fraction or 0.0,
                "valid_park_count": float(ctx.valid_park_count),
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/ml/test_env.py::test_env_threads_valid_park_count_into_info_gated_on_validity -v`
Expected: PASS.

- [ ] **Step 6: Run the full env suite + lint/type**

Run: `pytest tests/ml/test_env.py -v && ruff check ml/env.py && mypy ml/`
Expected: PASS; `mypy ml/` clean.

- [ ] **Step 7: Commit**

```bash
git add ml/env.py tests/ml/test_env.py
git commit -m "feat(ml): env feeds valid_park_count to the reward + info.terms (#812)

The Park branch sets RewardContext.valid_park_count = len(_parked) on a
valid park (0 on an invalid one), and _info surfaces it as the
valid_park_count term for vp-plateau diagnosis. Additive: movement steps
and invalid parks report 0.

Part of #812"
```

---

### Task 3: `--r-valid-progress` CLI flag (`ml/train.py`)

**Files:**
- Modify: `ml/train.py` (add the `--r-valid-progress` argument in `build_argparser` after `--r-first-valid` ~line 957; pass `r_valid_progress=args.r_valid_progress` into the `RewardWeights(...)` construction ~line 1091)
- Test: `tests/ml/test_train_curriculum.py` (append one test; the file already imports `build_argparser`)

**Interfaces:**
- Consumes: `RewardWeights.r_valid_progress` (Task 1).
- Produces: `build_argparser().parse_args([...]).r_valid_progress: float` (default `0.0`), wired into the training `RewardWeights`.

- [ ] **Step 1: Write the failing test** — append to `tests/ml/test_train_curriculum.py`:

```python
def test_r_valid_progress_flag_parses_and_defaults_neutral():
    parser = build_argparser()
    assert parser.parse_args([]).r_valid_progress == 0.0  # default-neutral
    assert parser.parse_args(["--r-valid-progress", "8.0"]).r_valid_progress == 8.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/ml/test_train_curriculum.py::test_r_valid_progress_flag_parses_and_defaults_neutral -v`
Expected: FAIL — `AttributeError: 'Namespace' object has no attribute 'r_valid_progress'`.

- [ ] **Step 3: Add the argparse argument** — in `ml/train.py::build_argparser`, immediately after the `--r-first-valid` argument block (the one ending ~line 957), add:

```python
    p.add_argument(
        "--r-valid-progress",
        type=float,
        default=0.0,
        help="banked per-valid-Park credit scaled by the marginal valid-object count beyond the "
        "freebie (r_valid_progress*max(0, n-1) on a Park where the whole layout is valid); 0 = off "
        "(byte-identical); the #812 per-commitment economics lever",
    )
```

- [ ] **Step 4: Wire it into `RewardWeights`** — in `ml/train.py`, in the `weights = RewardWeights(...)` construction (~line 1087), add the field right after `r_first_valid=args.r_first_valid,` (~line 1091):

```python
        r_first_valid=args.r_first_valid,
        r_valid_progress=args.r_valid_progress,
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/ml/test_train_curriculum.py::test_r_valid_progress_flag_parses_and_defaults_neutral -v`
Expected: PASS.

- [ ] **Step 6: Run the ml suite + lint/type as a final gate**

Run: `pytest tests/ml/ -q && ruff check ml/ && mypy ml/`
Expected: PASS across `tests/ml/`; ruff + `mypy ml/` clean.

- [ ] **Step 7: Commit**

```bash
git add ml/train.py tests/ml/test_train_curriculum.py
git commit -m "feat(ml): --r-valid-progress CLI flag wires the #812 lever into training

Default 0.0 (byte-identical). Recipe deploys it on the trio-notch run on
top of valid_park_grade_scale>0; the magnitude is tuned by a two-seed
ml.gate A/B sweep (6/8/12).

Part of #812"
```

---

## Out of scope (deferred)

- The convex-stick adjunct (`unplaced_penalty_exponent`) — spec §10. Not implemented in #812.
- The trio-notch A/B sweep + an `ml/README` recipe note — a follow-up after the knob lands (the recipe lives there alongside the other gate recipes). Not part of this code change.

## Self-Review

**Spec coverage:**
- §2 reward term → Task 1 Step 5. ✓
- §3 knob (`r_valid_progress=0.0`) → Task 1 Step 4. ✓
- §4 env hook (`valid_park_count`) + optional `info.terms` diagnostic → Task 2. ✓
- §5 argmax (per-count payout 0/1×/2×) → Task 1 `test_r_valid_progress_pays_marginal_count_beyond_the_freebie`. ✓
- §6 pile-safety → Task 1 `test_r_valid_progress_not_paid_on_invalid_park` (reward level) + Task 2 invariant (env level). ✓
- §7 determinism → covered by the default-neutral byte-identity test + `ml-rl-guard` gate (Task 1 Step 7 / Task 3 Step 6). ✓
- §8 tests: default-neutral byte-identity ✓, clip-headroom guard ✓ (`test_r_valid_progress_clip_headroom_at_recipe_scale`), pile-firewall ✓, positive-credit ✓, determinism ✓ (mypy/ruff + byte-identity). ✓
- §9 recipe / CLI flag → Task 3. ✓ (the A/B run itself is out of scope, noted above.)
- §10/§11 caveats/open-questions → documentation only; no task needed.

**Placeholder scan:** none — every step has exact code, exact paths, exact commands, expected output.

**Type consistency:** `r_valid_progress: float` and `valid_park_count: int` are used identically in `types.py`, `reward.py`, `env.py`, `train.py`, and all tests. The reward term `w.r_valid_progress * float(max(0, ctx.valid_park_count - 1))` matches the field types and the spec formula. `info.terms` values are `float`, so `float(ctx.valid_park_count)` matches the dict's value type.
