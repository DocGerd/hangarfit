# Completion Paired-Witness Diagnostic Probe — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decide one bit — is the learned-backend cold-start completion wall the door→interior *drive* (learning capacity) or the *slot sparsity* (geometry)? — via one paired door-spawn training run that holds the drive fixed and toggles only the manifold width.

**Architecture:** Add two opt-in single-rung "completion" curriculum stages (pre-park k=N−1=2 of a valid 3-object witness, drive the last object in from the door at φ=1, no backplay knob): one on the tight Herrenteich notch (`witness_notch.yaml`, conditional last-slot ~0.064), one on a new roomy witness (`witness_roomy.yaml` on the 25×30 m `test_hangar_large.yaml`, ~0.278). A `--completion-probe {notch,roomy}` flag trains exactly one stage from a fresh policy. The decision metric is **marginal completion** read from the existing `--metrics-out` JSONL via the floor-aware transform `max(0, 3·valid_placed − 2)`. No `ml/env.py` / `ml/reward.py` / `ml/action_space.py` / `ml/encoding.py` changes.

**Tech Stack:** Python 3.12, PyTorch (the `[train]` extra), the `ml/` dev/CI-only RL workspace, `hangarfit` CLI (`solve`/`check`), pytest.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-06-25-learned-backend-completion-paired-witness-probe-design.md` — every requirement there is in force.
- **Determinism / default-neutrality (ml-rl-guard):** the new flag MUST default off; absent `--completion-probe`, every existing ladder is **byte-identical** to before. No change to the action table, `SCHEMA_VERSION`, env reset distribution, or reward emission.
- **Feasibility-first (the #832/#835 lesson):** the roomy arm is invalid as evidence unless `witness_roomy.yaml` is `hangarfit check`-VERIFIED valid and a k-prefix of a valid layout (any prefix of a valid layout is valid by construction).
- **Metric is MARGINAL, never aggregate:** read `valid_placed` only through the floor-aware transform against the pre-registered (N−1)/N = 2/3 null. A place-nothing completion reads 2/3, not 0.
- **No env/reward/encoding code:** witness fixture + curriculum stages + CLI flag + an analysis helper only.
- **`ml/` is a top-level package outside `src/`:** run from repo root (`cwd=root` or `PYTHONPATH=$PWD`). In a worktree, use `PYTHONPATH=$PWD/src python -m …` (the editable `.pth` points at the main checkout).
- **GitFlow:** issue-driven; PR-1 (code) lands and merges before the run; PR-2 records the verdict. Never commit to `develop` directly; never `--no-verify`/force.
- **Roomy arm fixtures use** `tests/fixtures/test_hangar_large.yaml` (25 w × 30 l, clearance 0.3) + `data/fleet.yaml`. Tight arm reuses `examples/herrenteich/hangar.yaml` + `examples/herrenteich/fleet.yaml` at clearance 0.05 (the existing notch manifold — the measured 0.000 wall). The clearance differs by arm *on purpose*: the controlled variable is the resulting conditional last-slot probability (the manifold), confirmed in Task 1.

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `tests/fixtures/ml/witness_roomy.yaml` | **Create.** Bare `placements:` (3 objects) — a valid k=3 layout on `test_hangar_large.yaml`. | T1 |
| `tests/fixtures/ml/scenario_roomy.yaml` | **Create.** Solve scenario used to author the roomy witness (reproducible/auditable). | T1 |
| `tests/ml/test_stage_builder.py` | **Modify.** Add `test_witness_roomy_*` validity-pin tests mirroring the notch ones. | T1 |
| `ml/curriculum.py` | **Modify.** Add roomy path constants + `_completion_stage(witness)` builder. | T2 |
| `tests/ml/test_curriculum.py` (or the curriculum test module) | **Modify.** Stage-config + default-neutrality tests. | T2, T3 |
| `ml/train.py` | **Modify.** Add `--completion-probe {notch,roomy}` → single-rung schedule. | T3 |
| `ml/probe_metric.py` | **Create.** `marginal_completion()` floor-aware transform. | T4 |
| `tests/ml/test_probe_metric.py` | **Create.** Unit test for the transform. | T4 |
| `ml/README.md` | **Modify.** Lever-ledger placeholder row for the pending probe (PR-1); verdict (PR-2). | T5, T7 |
| `docs/adr/0028-...md` | **Modify (PR-2).** Re-open-trigger ledger: record the bit. | T7 |

---

## Task 1: Author + validity-pin the roomy witness

**Files:**
- Create: `tests/fixtures/ml/scenario_roomy.yaml`, `tests/fixtures/ml/witness_roomy.yaml`
- Modify: `tests/ml/test_stage_builder.py`
- Test: `tests/ml/test_stage_builder.py::test_witness_roomy_*`

**Interfaces:**
- Produces: `tests/fixtures/ml/witness_roomy.yaml` — a bare `placements:` list of 3 objects, valid on `test_hangar_large.yaml` + `data/fleet.yaml` at clearance 0.3, every prefix valid. Consumed by T2's `_completion_stage("roomy")` via `anchor_layout_path`.

- [ ] **Step 1: Write the failing validity-pin tests.** In `tests/ml/test_stage_builder.py`, mirror the existing `test_witness_notch_*` block (read it first, lines ~156–202). Add:

```python
def _load_witness_roomy(clearance_m: float = 0.3):
    """Load the committed roomy witness against the roomy completion rung's hangar
    (test_hangar_large, clearance override) + the synthetic box fleet — the same hangar/fleet
    the completion-roomy probe trains on (ADR-0028 trigger-#3 paired probe)."""
    hangar = dataclasses.replace(
        load_hangar("tests/fixtures/test_hangar_large.yaml"),
        clearance_m=clearance_m,
        apron_depth_m=8.0,
    )
    fleet = load_fleet("data/fleet.yaml")
    return load_layout("tests/fixtures/ml/witness_roomy.yaml", fleet=fleet, hangar=hangar)


def test_witness_roomy_is_a_valid_three_object_layout():
    lay = _load_witness_roomy()
    assert len(lay.placements) == 3
    assert go.layout_valid(lay)


def test_witness_roomy_every_prefix_is_valid():
    lay = _load_witness_roomy()
    for k in range(len(lay.placements) + 1):
        prefix = dataclasses.replace(lay, placements=lay.placements[:k])
        assert go.layout_valid(prefix), f"roomy witness prefix k={k} is invalid"


def test_witness_roomy_every_single_anchor_is_valid():
    lay = _load_witness_roomy()
    for i in range(len(lay.placements)):
        single = dataclasses.replace(lay, placements=(lay.placements[i],))
        assert go.layout_valid(single), f"single anchor #{i} ({lay.placements[i].plane_id}) invalid"


def test_witness_roomy_has_margin_at_stricter_clearance():
    # Roomy slack: still valid at a stricter 0.5 m clearance, so the trio has real geometric
    # margin at the rung's 0.3 m (the fat-slot manifold is not a borderline graze).
    assert go.layout_valid(_load_witness_roomy(clearance_m=0.5))
```

- [ ] **Step 2: Run the tests; verify they fail (fixture absent).**

Run: `pytest tests/ml/test_stage_builder.py -k witness_roomy -v`
Expected: FAIL — `LoaderError`/file-not-found on `witness_roomy.yaml`.

- [ ] **Step 3: Author the solve scenario.** Read `tests/fixtures/scenario_minimal.yaml` to confirm the scenario schema, then create `tests/fixtures/ml/scenario_roomy.yaml` selecting **three small `data/fleet.yaml` aircraft** (e.g. `fuji`, `cessna_150`, `aviat_husky` — pick three that the fleet defines and that fit comfortably) on the large test hangar:

```yaml
# Solve scenario used to AUTHOR tests/fixtures/ml/witness_roomy.yaml (ADR-0028 trigger-#3).
# A roomy 3-object fill on the 25x30 test hangar: produces the fat-conditional-last-slot
# witness (the paired probe's roomy arm). Reproducible/auditable per the spec's default.
fleet: ../../../data/fleet.yaml
hangar: ../test_hangar_large.yaml
planes: [fuji, cessna_150, aviat_husky]
```

(Adjust the `planes:` ids to three the fleet actually defines; confirm with `python -c "from hangarfit.loader import load_fleet; print(list(load_fleet('data/fleet.yaml').planes))"`. Match the scenario key names to `scenario_minimal.yaml` exactly.)

- [ ] **Step 4: Solve → write layout → check VALID.**

```bash
hangarfit solve tests/fixtures/ml/scenario_roomy.yaml --write-yaml /tmp/claude-1000/-home-pkuhn-hangarfit/bc5a17c0-b276-47e0-8338-44c16929904d/scratchpad/roomy.yaml
hangarfit check /tmp/claude-1000/-home-pkuhn-hangarfit/bc5a17c0-b276-47e0-8338-44c16929904d/scratchpad/roomy.yaml --render /tmp/claude-1000/-home-pkuhn-hangarfit/bc5a17c0-b276-47e0-8338-44c16929904d/scratchpad/roomy.png; echo "check exit=$?"
```
Expected: `hangarfit check` prints `VALID` and `check exit=0`. If solve reports trivial-infeasible or check exits 1, widen the plane choice / re-run; the 25×30 hangar fits 3 small aircraft comfortably.

- [ ] **Step 5: Strip fleet/hangar keys → bare witness.** `solve --write-yaml` emits `fleet:`/`hangar:`/`placements:`; the witness loader **rejects** a file that sets `fleet:`/`hangar:` AND takes overrides. Create `tests/fixtures/ml/witness_roomy.yaml` with ONLY the `placements:` block (drop `on_carts` if all `false`, matching `witness_notch.yaml`), plus a header comment mirroring `witness_notch.yaml`'s provenance note:

```yaml
# Committed seed-anchor witness for the ADR-0028 trigger-#3 completion-roomy probe rung.
#
# A VALID 3-object layout on the roomy 25x30 test hangar (tests/fixtures/test_hangar_large.yaml),
# produced by `hangarfit solve tests/fixtures/ml/scenario_roomy.yaml` and verified by
# `hangarfit check` (exit 0). The paired completion probe pre-parks a k=2 prefix and drives the
# last object in from the door (phi=1); the fat conditional last-slot (~0.278, vs the notch's
# ~0.064) is the manifold-width arm. A k-prefix of a valid layout is itself valid, so the
# pre-parked 2-prefix is feasibility-clean with NO runtime solver call. Validity pinned by
# tests/ml/test_stage_builder.py::test_witness_roomy_*. Do not hand-edit poses without re-running.
#
# No `fleet:`/`hangar:` fields on purpose: the env + stage_builder load this with fleet=/hangar=
# OVERRIDES (test_hangar_large at clearance 0.3 + data/fleet.yaml), and the loader rejects a file
# that sets those AND takes overrides.
placements:
  - {plane: <id1>, x_m: <x>, y_m: <y>, heading_deg: <h>}
  - {plane: <id2>, x_m: <x>, y_m: <y>, heading_deg: <h>}
  - {plane: <id3>, x_m: <x>, y_m: <y>, heading_deg: <h>}
```
(Fill `<...>` from the solved `/tmp/.../roomy.yaml`.)

- [ ] **Step 6: Run the validity-pin tests; verify they pass.**

Run: `pytest tests/ml/test_stage_builder.py -k witness_roomy -v`
Expected: 4 PASS.

- [ ] **Step 7: Manifold-contrast sanity check (record, don't commit a flaky test).** Write a throwaway script in the scratchpad that MC-samples the 3rd object's pose (uniform over in-bounds x,y and 8 headings) given the committed 2-prefix, for BOTH witnesses, and prints the valid fraction (conditional last-slot P). Confirm roomy ≫ notch (target ≥ 3×). Record both numbers in the PR-1 body. This proves the controlled variable (manifold width) is real on the *actual* committed prefixes, not just the session estimate.

```bash
PYTHONPATH=$PWD python /tmp/claude-1000/-home-pkuhn-hangarfit/bc5a17c0-b276-47e0-8338-44c16929904d/scratchpad/manifold_contrast.py
# Expected stdout, e.g.:  notch cond-last-slot ~0.06 (N=20000) | roomy ~0.28 | ratio ~4.4x
```

- [ ] **Step 8: Commit.**

```bash
git add tests/fixtures/ml/witness_roomy.yaml tests/fixtures/ml/scenario_roomy.yaml tests/ml/test_stage_builder.py
git commit -m "test(607): roomy completion witness + validity pins (ADR-0028 trigger-#3 probe)"
```

---

## Task 2: Two completion stages + curriculum config tests

**Files:**
- Modify: `ml/curriculum.py`
- Test: the curriculum test module (find it: `tests/ml/test_curriculum.py` or wherever `Stage`/ladder builders are tested)

**Interfaces:**
- Consumes: `_NOTCH_HANGAR`, `_NOTCH_FLEET`, `_WITNESS_NOTCH`, `_LENIENT_CLEARANCE` (existing in `ml/curriculum.py`).
- Produces: `_completion_stage(witness: str) -> Stage` — `witness ∈ {"notch","roomy"}`; both `max_objects=3, seed_anchor_k=2, per_object_step_budget=80, total_step_budget=80`, **no** `backplay_phi_cap` (door spawn, φ=1). Stage names `"completion-notch"` / `"completion-roomy"`. Consumed by T3's CLI wiring.

- [ ] **Step 1: Write the failing stage-config test.** In the curriculum test module, add:

```python
def test_completion_stage_notch_is_door_spawn_drive_one():
    from ml.curriculum import _completion_stage
    s = _completion_stage("notch")
    assert s.name == "completion-notch"
    assert s.difficulty.max_objects == 3
    assert s.difficulty.seed_anchor_k == 2            # pre-park 2, drive 1
    assert s.difficulty.backplay_phi_cap is None      # door spawn (phi=1), no backplay mixture
    assert s.difficulty.anchor_prob is None           # fixed-k, not mixed-start
    assert s.anchor_layout_path == "tests/fixtures/ml/witness_notch.yaml"
    assert s.hangar_path == "examples/herrenteich/hangar.yaml"
    assert s.clearance_m == 0.05


def test_completion_stage_roomy_uses_large_hangar_and_roomy_witness():
    from ml.curriculum import _completion_stage
    s = _completion_stage("roomy")
    assert s.name == "completion-roomy"
    assert s.difficulty.max_objects == 3
    assert s.difficulty.seed_anchor_k == 2
    assert s.difficulty.backplay_phi_cap is None
    assert s.anchor_layout_path == "tests/fixtures/ml/witness_roomy.yaml"
    assert s.hangar_path == "tests/fixtures/test_hangar_large.yaml"
    assert s.fleet_path == "data/fleet.yaml"
    assert s.clearance_m == 0.3


def test_completion_stage_rejects_unknown_witness():
    import pytest
    from ml.curriculum import _completion_stage
    with pytest.raises(ValueError):
        _completion_stage("bogus")
```

- [ ] **Step 2: Run; verify fail.**

Run: `pytest <curriculum_test_module> -k completion_stage -v`
Expected: FAIL — `cannot import name '_completion_stage'`.

- [ ] **Step 3: Implement the stage builder + constants.** In `ml/curriculum.py`, near the existing notch constants (after `_LENIENT_CLEARANCE`), add:

```python
# ADR-0028 trigger-#3 completion paired-witness probe (opt-in via --completion-probe; NOT in any
# default ladder, so absent the flag the env is byte-identical). The ROOMY arm's hangar/fleet:
_ROOMY_HANGAR = "tests/fixtures/test_hangar_large.yaml"
_ROOMY_FLEET = "data/fleet.yaml"
# Committed roomy completion witness (a valid 3-object fill on the 25x30 test hangar). Validity
# pinned by tests/ml/test_stage_builder.py::test_witness_roomy_*.
_WITNESS_ROOMY = "tests/fixtures/ml/witness_roomy.yaml"
_ROOMY_CLEARANCE = 0.3  # the test_hangar_large file value; the roomy slot is the controlled var


def _completion_stage(witness: str) -> Stage:
    """A single door-spawn COMPLETION rung for the ADR-0028 trigger-#3 paired probe: pre-park
    k=N-1=2 of a valid 3-object witness and drive the LAST object in from the door (phi=1, NO
    backplay knob). ``witness`` selects the manifold arm — 'notch' (tight Herrenteich notch,
    conditional last-slot ~0.064, the measured 0.000 wall) or 'roomy' (the fat 25x30 hangar,
    ~0.278). The arms differ ONLY in hangar/fleet/witness/clearance; manifold width is the
    controlled variable. Opt-in (built only by --completion-probe); no default ladder includes
    it, so the env stays byte-identical when the flag is absent."""
    if witness == "notch":
        return Stage(
            name="completion-notch",
            difficulty=DifficultyConfig(
                max_objects=3, seed_anchor_k=2, per_object_step_budget=80, total_step_budget=80
            ),
            hangar_path=_NOTCH_HANGAR,
            fleet_path=_NOTCH_FLEET,
            anchor_layout_path=_WITNESS_NOTCH,
            clearance_m=_LENIENT_CLEARANCE,
        )
    if witness == "roomy":
        return Stage(
            name="completion-roomy",
            difficulty=DifficultyConfig(
                max_objects=3, seed_anchor_k=2, per_object_step_budget=80, total_step_budget=80
            ),
            hangar_path=_ROOMY_HANGAR,
            fleet_path=_ROOMY_FLEET,
            anchor_layout_path=_WITNESS_ROOMY,
            clearance_m=_ROOMY_CLEARANCE,
        )
    raise ValueError(f"_completion_stage: unknown witness {witness!r} (expected 'notch'|'roomy')")
```

- [ ] **Step 4: Run; verify pass.**

Run: `pytest <curriculum_test_module> -k completion_stage -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit.**

```bash
git add ml/curriculum.py <curriculum_test_module>
git commit -m "feat(607): completion-probe curriculum stages (door-spawn, drive-one)"
```

---

## Task 3: `--completion-probe` CLI flag (single-rung schedule)

**Files:**
- Modify: `ml/train.py`
- Test: the train/curriculum test module

**Interfaces:**
- Consumes: `_completion_stage` (T2).
- Produces: `--completion-probe {notch,roomy}` (default `None`). When set, the trained schedule is exactly `[_completion_stage(value)]` (a fresh policy, one rung). When `None`, schedule building is byte-identical to before.

- [ ] **Step 1: Write the failing test.** First read `ml/train.py` to find the function that maps parsed args → the `list[Stage]`/ladder (where `--anchor-trio-notch` and `--schedule` are consumed; likely `build_schedule(args)` or inline in `main`). Then add a test asserting the flag yields the single completion rung and that absence is unchanged:

```python
def test_completion_probe_flag_builds_single_completion_rung():
    from ml.train import build_argparser, build_schedule  # adjust to the real builder name
    args = build_argparser().parse_args(["--completion-probe", "roomy"])
    stages = build_schedule(args)
    assert [s.name for s in stages] == ["completion-roomy"]


def test_no_completion_probe_is_default_neutral():
    from ml.train import build_argparser, build_schedule
    base = build_argparser().parse_args(["--schedule", "curriculum"])
    with_flag_default = build_argparser().parse_args(["--schedule", "curriculum"])
    # default None => identical schedule to the pre-change curriculum
    assert [s.name for s in build_schedule(base)] == [s.name for s in build_schedule(with_flag_default)]
    assert all(s.name not in ("completion-notch", "completion-roomy") for s in build_schedule(base))
```

(If schedule construction is inlined in `main`, refactor the minimal mapping into a `build_schedule(args)` helper as part of this step so it is unit-testable; keep behavior byte-identical for existing flags.)

- [ ] **Step 2: Run; verify fail.**

Run: `pytest <train_test_module> -k completion_probe -v`
Expected: FAIL — unrecognized argument `--completion-probe` (or import error on `build_schedule`).

- [ ] **Step 3: Add the argparse flag.** In `ml/train.py`'s `build_argparser()`, near `--anchor-trio-notch`:

```python
p.add_argument(
    "--completion-probe",
    choices=["notch", "roomy"],
    default=None,
    help="ADR-0028 trigger-#3 diagnostic: train ONLY a single door-spawn completion rung "
    "(pre-park 2 of a 3-object witness, drive the last in from the door at phi=1) on the chosen "
    "manifold arm — 'notch' (tight, the measured 0.000 wall) or 'roomy' (fat slot). Overrides "
    "the ladder with one rung from a fresh policy; default None = unchanged (byte-identical).",
)
```

- [ ] **Step 4: Wire the single-rung schedule.** In the schedule builder, BEFORE the normal ladder construction, short-circuit on the flag:

```python
if args.completion_probe is not None:
    return [_completion_stage(args.completion_probe)]
```

(Place this so it takes precedence over `--schedule`/`--anchor-trio-notch`. Import `_completion_stage` from `ml.curriculum` if not already in scope.)

- [ ] **Step 5: Run; verify pass + full curriculum suite green (no neutrality regression).**

Run: `pytest <train_test_module> -k completion_probe -v && pytest tests/ml/test_curriculum.py -q`
Expected: new tests PASS; existing curriculum tests unchanged/green.

- [ ] **Step 6: Commit.**

```bash
git add ml/train.py <train_test_module>
git commit -m "feat(607): --completion-probe single-rung schedule (default-neutral)"
```

---

## Task 4: Marginal-completion metric helper

**Files:**
- Create: `ml/probe_metric.py`
- Test: `tests/ml/test_probe_metric.py`

**Interfaces:**
- Produces: `marginal_completion(valid_placed: float, *, n: int = 3, k: int = 2) -> float` — for a drive-one (k = n−1) completion rung, returns the floor-aware marginal completion `max(0.0, n*valid_placed - k)`. Consumed by the grading step (T7).

- [ ] **Step 1: Write the failing unit test.** Create `tests/ml/test_probe_metric.py`:

```python
import math
import pytest
from ml.probe_metric import marginal_completion


def test_place_nothing_floor_reads_zero_marginal():
    # k=2 of n=3 pre-parked => a place-nothing/abstain policy reads valid_placed = 2/3 exactly,
    # which is the pre-registered null: MARGINAL completion = 0 (it never parked the last object).
    assert marginal_completion(2.0 / 3.0, n=3, k=2) == pytest.approx(0.0, abs=1e-9)


def test_full_completion_reads_one():
    assert marginal_completion(1.0, n=3, k=2) == pytest.approx(1.0)


def test_partial_completion_is_linear_above_floor():
    # valid_placed = 0.7667 => marginal = 3*0.7667 - 2 = 0.30 (the GO threshold).
    assert marginal_completion(0.76667, n=3, k=2) == pytest.approx(0.30, abs=1e-3)


def test_invalid_piling_below_floor_clamps_to_zero():
    # An invalid-pile policy reads valid_placed BELOW the 2/3 floor; marginal clamps to 0
    # (no spurious negative, no false GO).
    assert marginal_completion(0.5, n=3, k=2) == 0.0


def test_rejects_non_drive_one():
    with pytest.raises(ValueError):
        marginal_completion(0.8, n=3, k=1)  # drive=2: the affine transform does not apply
```

- [ ] **Step 2: Run; verify fail.**

Run: `pytest tests/ml/test_probe_metric.py -v`
Expected: FAIL — `No module named 'ml.probe_metric'`.

- [ ] **Step 3: Implement.** Create `ml/probe_metric.py`:

```python
"""Marginal last-object completion metric for the ADR-0028 trigger-#3 completion probe.

With ``seed_anchor_k = N-1`` pre-parked of a valid witness, a place-nothing / abstain policy
already reads ``valid_placed = (N-1)/N`` (the pre-parked prefix is valid and counts in both the
numerator ``len(_parked)`` and the denominator ``len(requested_ids)`` — env.py:112,355). So the
training-time aggregate ``valid_placed`` is FLOORED and must be read MARGINALLY, never as a raw
success rate (the #821 0.63 masquerade). For a DRIVE-ONE rung (k = N-1, no backplay mixture) the
relation is exact and conservative:

    valid_placed = (N-1)/N + p/N - (2/N)*q     where p = P(last object validly parked),
                                                     q = P(last object INVALIDLY piled)
    => p = N*valid_placed - (N-1)   when q ≈ 0 (abstain, not pile);
       q>0 only makes N*valid_placed-(N-1) an UNDER-estimate, so clamping at 0 never yields a
       false positive. Hence marginal_completion = max(0, N*valid_placed - (N-1)).

Analysis-only: no env/reward/encoding change. Read the door-spawn rung's windowed-final
``valid_placed`` (both seeds) through this transform; threshold the result, never the raw value.
"""

from __future__ import annotations


def marginal_completion(valid_placed: float, *, n: int = 3, k: int = 2) -> float:
    """Floor-aware marginal last-object completion from a drive-one completion rung's aggregate
    ``valid_placed``. Returns ``max(0, n*valid_placed - k)``. Requires k == n-1 (drive exactly
    one object); the affine transform is undefined for drive>1."""
    if k != n - 1:
        raise ValueError(
            f"marginal_completion is defined only for a drive-one rung (k == n-1); got n={n}, k={k}"
        )
    return max(0.0, n * valid_placed - k)
```

- [ ] **Step 4: Run; verify pass.**

Run: `pytest tests/ml/test_probe_metric.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit.**

```bash
git add ml/probe_metric.py tests/ml/test_probe_metric.py
git commit -m "feat(607): floor-aware marginal-completion metric for the probe"
```

---

## Task 5: README ledger placeholder + assemble PR-1

**Files:**
- Modify: `ml/README.md`

- [ ] **Step 1: Add a pending-probe row to the lever ledger.** In `ml/README.md`'s lever-ledger section (read it first), add one row marking the probe as RUNNABLE-NOT-YET-RUN, with the pre-registered metric, floor, and GO/NO-GO rule. No verdict yet (filled in T7). Example row text:

```markdown
| completion paired-witness probe (ADR-0028 trigger-#3) | door-spawn φ=1, k=2/3, paired notch (~0.064) vs roomy (~0.278) | **runnable — not yet run** | metric = marginal completion `max(0, 3·valid_placed − 2)` vs the pre-registered 2/3 floor; GO = roomy ≥ 0.30 & notch ≈ 0 (slot-geometry) → scoped roomy charter; NO-GO = both ≈ 0 (drive binding) → resolved-negative generalizes |
```

- [ ] **Step 2: No CHANGELOG entry.** `ml/` is dev/CI-only (never shipped in the wheel), so this is not a user-facing change — confirm no `CHANGELOG.md` `[Unreleased]` entry is needed (the PR-create hook warning is advisory here; note the dev/CI-only rationale in the PR body).

- [ ] **Step 3: Commit + run the ml/ gates locally.**

```bash
git add ml/README.md
git commit -m "docs(607): ledger row for the pending completion paired-witness probe"
ruff check ml/ && ruff format --check ml/ && mypy ml/ && pytest tests/ml/ -q
```
Expected: all green.

- [ ] **Step 4: Open PR-1 (draft) and run the review arc.**

```bash
git push -u origin feature/<issue>-completion-paired-witness-probe
gh pr create --draft --base develop --title "Completion paired-witness diagnostic probe (ADR-0028 trigger-#3)" \
  --body "Closes #<issue>. Code-only for the paired probe: roomy witness + validity pins, two door-spawn completion stages, --completion-probe single-rung schedule, floor-aware marginal-completion metric. ml/ is dev/CI-only (no CHANGELOG). Manifold-contrast: notch ~0.06 vs roomy ~0.28 (recorded in Task 1). The RUN + verdict land in PR-2."
```
Then run `/pr-review` incl. **ml-rl-guard** (mandatory — this touches `ml/`), convert findings to threads, fix + resolve, flip out of draft when clean. **User merges.**

---

## Task 6: Run the paired probe (post-merge)

**Files:** none (operational). Run from a **worktree pinned to the merged branch** (fixtures are read lazily from the working tree — a mid-run branch switch breaks the run, the #736 gotcha).

- [ ] **Step 1: Pin a worktree to the merged code.**

```bash
git fetch origin develop
git worktree add /home/pkuhn/completion-probe-runs/wt origin/develop
```

- [ ] **Step 2: Run the 4 cells (2 arms × 2 seeds), identical config except the witness.** From inside the worktree (so `ml` resolves there and lazy fixture reads point at it):

```bash
cd /home/pkuhn/completion-probe-runs/wt
for arm in notch roomy; do for seed in 0 1; do
  PYTHONPATH=$PWD/src python -m ml.train --completion-probe $arm --seed $seed \
    --max-iters-per-stage 200 \
    --metrics-out /home/pkuhn/completion-probe-runs/metrics-$arm-s$seed.jsonl \
    --save /home/pkuhn/completion-probe-runs/policy-$arm-s$seed.pt
done; done
```
(Run in the background; this is the only expensive step. `--max-iters-per-stage 200` gives both arms an identical fixed budget. Confirm `--anchor-trio-notch` is NOT passed — the `--completion-probe` short-circuit owns the schedule.)

- [ ] **Step 3: Confirm engagement.** For each run, confirm the trained rung was `completion-<arm>` (grep the metrics JSONL `stage` field) and that `seed_anchor_k=2` / door spawn engaged (the metrics carry no `phi_cap` key — backplay was off). Record `n_eps`>0 per iter.

---

## Task 7: Grade + record the verdict (PR-2)

**Files:**
- Modify: `ml/README.md`, `docs/adr/0028-learned-backend-train-to-mastery-resolved-negative.md`

- [ ] **Step 1: Compute marginal completion per cell.** For each of the 4 JSONL files, take the windowed-final `valid_placed` (mean of the last ~10 iterations of the `completion-<arm>` stage, per the gate convention) and apply the helper:

```bash
PYTHONPATH=$PWD python -c "
import json, statistics as st
from ml.probe_metric import marginal_completion
for arm in ('notch','roomy'):
  for seed in (0,1):
    rows=[json.loads(l) for l in open(f'/home/pkuhn/completion-probe-runs/metrics-{arm}-s{seed}.jsonl')]
    vp=[r['valid_placed'] for r in rows if r['stage']==f'completion-{arm}' and r['valid_placed'] is not None]
    final=st.mean(vp[-10:])
    print(f'{arm} s{seed}: valid_placed_final={final:.3f}  marginal={marginal_completion(final):.3f}')
"
```

- [ ] **Step 2: Apply the pre-registered decision rule.**
  - **GO (slot-geometry):** roomy marginal ≥ ~0.30 (both seeds) AND notch marginal ≈ 0.000 → the wall is slot sparsity. Open a *scoped* roomy/medium completion charter as a separate issue; tight-dense KILL stays intact.
  - **NO-GO (drive binding):** roomy marginal ≈ 0.000 too → resolved-negative generalizes; geometry confound closed; bank fortified.
  - **Ambiguous** (roomy lifts but < 0.30, or notch non-zero): report the numbers, do not over-claim; treat as NO-GO for charter purposes, note the residual.

- [ ] **Step 3: Record the verdict (both directions).** Update the `ml/README.md` ledger row from "runnable — not yet run" to the measured marginal numbers + the bit; add an ADR-0028 re-open-trigger ledger entry recording trigger-#3 as EXERCISED with the outcome (drive-binding-generalizes vs slot-geometry-reopen). Keep the gate code/logic untouched (docs-only).

- [ ] **Step 4: Commit + open PR-2 (draft), review arc, user merges.**

```bash
git switch -c feature/<issue2>-completion-probe-verdict develop  # off fresh develop
git add ml/README.md docs/adr/0028-learned-backend-train-to-mastery-resolved-negative.md
git commit -m "docs(607): record completion paired-witness probe verdict (ADR-0028 trigger-#3)"
git push -u origin feature/<issue2>-completion-probe-verdict
gh pr create --draft --base develop --title "Record completion paired-witness probe verdict" --body "Closes #<issue2>. ..."
```
Run `/pr-review` (comment-analyzer + ml-rl-guard for the ledger/ADR wording), resolve, flip ready. **User merges.**

---

## Self-Review

**Spec coverage:**
- §3 paired door-spawn, 2 arms, controlled variable → T1 (roomy witness) + T2 (both stages) + T6 (paired run). ✓
- §4 marginal metric + pre-registered floor → T4 (helper + null encoded in tests) + T7 (windowed-final read). ✓
- §5 decision rule (GO/NO-GO/ambiguous) → T7 Step 2. ✓
- §6 feasibility witness (roomy authored + check-verified + validity-pinned) → T1. ✓
- §7 kill conditions: (1) aggregate-vs-marginal → T4 transform + the no-backplay-mixture design; (2) check-verified → T1 Step 4; (3) diagnostic-only → recorded in T7 / PR bodies; (4) arms differ only in witness → T2 (identical DifficultyConfig) + T6 (identical flags). ✓
- §8 success criteria → T1 (committed pinned witness), T2–T5 (no env/reward/action change, ml-rl-guard), T6 (4 cells × marginal), T7 (bit recorded). ✓
- §11 delivery: PR-1 (T1–T5) → run (T6) → PR-2 (T7). ✓

**Placeholder scan:** `<id1>/<x>/<y>` in T1 Step 5 and `<issue>`/`<curriculum_test_module>`/`<train_test_module>` are deliberate fill-ins resolved by their own steps (solved poses; the issue number; the test-module path located in the step) — not hidden work. The `build_schedule` name in T3 is flagged "adjust to the real builder name" with a refactor fallback. All code steps show real code.

**Type consistency:** `_completion_stage(witness: str) -> Stage` (T2) is called as `_completion_stage(args.completion_probe)` (T3) and `_completion_stage("notch"/"roomy")` (T2 tests) — consistent. `marginal_completion(valid_placed, *, n=3, k=2)` (T4) called the same in T7. DifficultyConfig fields (`max_objects`, `seed_anchor_k`, `per_object_step_budget`, `total_step_budget`, `backplay_phi_cap`) match the verbatim dataclass. Stage fields (`name`, `difficulty`, `hangar_path`, `fleet_path`, `anchor_layout_path`, `clearance_m`) match. ✓
