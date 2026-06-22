# Geometry-cut: eliminate redundant per-step geometry in the `ml/` training rollout

- **Date:** 2026-06-17
- **Issue:** #704 (part of epic #607, throughput half of #698)
- **Status:** Approved (design); implementation pending
- **Scope:** `ml/env.py`, `ml/geometry_oracle.py` (+ `tests/ml/`). **No change** to `src/hangarfit/` or to public `StepInfo`/reward semantics.

## 1. Problem & motivation

The cold-joint RL training loop (`ml/`) is throughput-bound, and that throughput tax is paid on *every* downstream experiment: the curriculum-to-mastery run (#698), knob tuning, and statistical reach-rate sampling.

Profiling a 512-step trivial rollout (24.2 s wall, after warmup; `tottime` sort) showed the cost split is **geometry, not neural-net compute**:

| Cost | Time | Share | GPU-able? |
|---|---|---|---|
| Shapely / geometry (`aircraft_parts_world` 13.8 s cumulative, polygon construction, `contains_xy`, coord iteration) | ~17 s | **~70%** | No — CPU/GEOS |
| Policy NN (`conv2d` 4.6 s + `linear` 1.2 s) | ~6 s | ~25% | yes, but batch=1 tiny CNN |
| other | ~1 s | ~5% | — |

Because ~70% is CPU polygon geometry, the GPU / batched-policy ("vectorized envs") path is Amdahl-capped at ~1.3× and is **not** the lever (this supersedes #698's "vectorized envs first" framing). The lever is **removing redundant geometry**. `aircraft_parts_world` is rebuilt **~90× per step** by three distinct redundancies:

1. **Triple `check()` per Park step.** `collisions.check(layout)` runs up to **3×** on the *same* layout — via `overlap_area_m2` (penetration), `layout_valid` (the `r_valid_park` gate), and `_info`'s `_layout_valid`. Caddy egress is computed just as many times. Only **one** check + one egress is needed.
2. **Eager validity on every step.** `_info` sets `valid = _layout_valid()` (full check + egress) on *every* step, but `collect_rollout`/`eval` read `info.valid` only on `done` steps.
3. **Recomputing the frozen parked set during a drive-in.** `_potential()`'s `remaining_overlap = overlap_area_m2(self._layout())` and `swept_intrusion_m2`'s `_build_obstacles(parked_layout)` are recomputed on *every movement step*, but `self._layout()` (parked + fixed) is **unchanged** during a drive-in — the parked set changes only on a Park.

## 2. Goal & non-goals

**Goal.** Cut the redundant per-step geometry in the `ml/` reward path so each training iteration is faster, with **zero change** to training output (byte-identical) and no determinism risk. Then re-profile to decide whether a later rung (multiprocess vector envs, or the encoder-raster cut) is still warranted.

**Non-goals.** GPU/CUDA. Batched-policy ("vectorized") rollouts. Caching the encoder raster (`ml/encoding.py`). Any change to `src/hangarfit/` (the deterministic verifier is ground truth and stays untouched). Any change to `StepInfo` field semantics.

## 3. The byte-identical contract

Every cut here is **byte-identical to training output**: a cached value equals the recomputed one bit-for-bit (a pure, deterministic function of the same inputs), so the reward stream, GAE advantages/returns, gradients, and the saved `state_dict` are unchanged. `StepInfo.valid` keeps its exact current semantics (validity of the frozen `_parked + _fixed` set, active object excluded) — the episode cache serves it for free, so the "validity only on `done`" idea (originally "A1") is **unnecessary** and dropped. The contract is verified by a regression test that diffs a checkpoint before/after (§6).

## 4. Design

### Component 1 — one check+egress per layout (`ml/geometry_oracle.py`)

Add a frozen value object and a single-pass scorer:

```python
@dataclass(frozen=True, slots=True)
class LayoutScore:
    penetration_m2: float    # check(layout).total_penetration_m2  (overlap gradient)
    collisions_valid: bool   # check(layout).valid
    egress_blocked: bool     # egress_blocked(layout)

def score_layout(layout: Layout) -> LayoutScore:
    cr = check(layout)
    return LayoutScore(cr.total_penetration_m2, cr.valid, egress_blocked(layout))
```

`layout_valid(layout)` becomes `s.collisions_valid and not s.egress_blocked` for an existing `LayoutScore`. `overlap_area_m2` / `layout_valid` stay as thin public wrappers (callers outside the env — benchmark, eval — are unaffected). The env switches to `score_layout`, so one step does **one** `check` + **one** egress instead of three each.

### Component 2 — `swept_intrusion_m2` accepts pre-built obstacles (`ml/geometry_oracle.py`)

Add an optional parameter so the caller can supply the frozen obstacle set:

```python
def swept_intrusion_m2(body, swept, *, parked_layout, active_id, obstacles=None) -> float:
    obstacles = obstacles if obstacles is not None else _build_obstacles(parked_layout, mover_id=active_id)
    ...
```

Default `None` preserves today's behavior byte-for-byte for any other caller. The env passes its cached obstacles.

### Component 3 — env-scoped episode cache (`ml/env.py`)

State added to `HangarFitEnv`:
- `self._parked_version: int` — bumped by 1 on every Park (append to `_parked`); reset to 0 in `_reset_state()`.
- `self._score_cache: tuple[int, LayoutScore] | None` — `(version, score)` for the frozen `self._layout()`.
- `self._obstacles_cache: tuple[int, str, object] | None` — `(version, active_id, obstacles)` for `swept_intrusion` (keyed on `active_id` because `_build_obstacles` excludes the mover, which changes per driven object).

Accessors:
- `_parked_score() -> LayoutScore`: return cached if `version` matches, else compute `score_layout(self._layout())`, cache, return. Preserve the existing empty-set short-circuit: when `not (self._parked or self._fixed)`, return the trivial `LayoutScore(0.0, True, False)` without calling `check`.
- `_parked_obstacles(active_id) -> obstacles`: cached on `(version, active_id)`.

Wiring (no behavioral change, only fewer recomputations):
- `_potential()` reads `remaining_overlap = self._parked_score().penetration_m2`.
- Movement branch: `swept_intrusion_m2(..., obstacles=self._parked_obstacles(active_id))`.
- `_info`'s `valid`: derived from `self._parked_score()` (movement-terminal & non-terminal) — cache hit during a drive-in.
- Park branch: after appending to `_parked` and bumping the version, call `self._parked_score()` **once** for the new placed set, and read everything from that single score — `overlap = score.penetration_m2`, `egress = score.egress_blocked` (the `ctx.egress_blocked` field), `park_valid = score.collisions_valid and not score.egress_blocked` (and the same value backs `_info.valid`). The per-part `intrusion_area_m2` (active body only) and `aircraft_parts_world(body, pl)` stay pose-dependent and recompute as today.

## 5. Cache-correctness argument (the invariant)

`self._layout()` is a pure function of `(_parked, _fixed, hangar, fleet, ground_objects)`. After construction, `hangar`/`fleet`/`ground_objects`/`_fixed` are immutable; within an episode the only mutation to `_parked` is an **append on Park**. Therefore a counter that bumps on Park is a **complete and exact** invalidation key for any quantity derived from `self._layout()` (`LayoutScore`, obstacles). `reset()` reconstructs `_parked = []` and resets the counter + caches. The active object is never in `self._layout()`, so spawning the next object does not affect the cached parked quantities. Consequently the cached value is identical to the recomputed value on every read.

## 6. Determinism & testing

The byte-identity guarantee is established **by construction**: the committed equivalence units prove every cached/refactored path returns the exact value of the pre-refactor path, from which identical training output follows. A stored golden `state_dict` hash is deliberately *not* committed (it is torch-version- and platform-dependent); instead the end-to-end checkpoint diff is run as a manual verification step and reported in the PR.

- **Equivalence units (committed, fast, `tests/ml/test_geometry_oracle.py` + `tests/ml/test_env.py`):**
  - `score_layout(layout)` equals `(overlap_area_m2(layout), layout_valid(layout), egress_blocked(layout))` computed separately — on a valid layout, an overlapping layout, and an egress-blocked layout.
  - `swept_intrusion_m2(..., obstacles=pre_built)` equals `swept_intrusion_m2(...)` (default `None` path).
  - Across a multi-object episode, `_parked_score()` and `_parked_obstacles(active_id)` equal freshly-computed `score_layout`/`_build_obstacles` at **every** step, including the step immediately after a Park (cache returns the correct, not stale, value).
  - A full seeded rollout (`collect_rollout`, fixed seed) produces an **identical reward sequence and per-step term breakdown** before/after — the in-suite byte-identity assertion (drive the env with a fixed action script, diff rewards + `StepInfo.terms`).
- **Cache invariants (committed):** `_parked_version` is 0 after `reset()`, increments by exactly 1 per Park, and resets on `reset()`; caches are per-instance (no module/global state).
- **Manual verification (reported in PR, not committed):** capture the SHA-256 of a saved `state_dict` from a tiny seeded run (`--schedule trivial --iterations 5 --seed 0` + the four knobs) on `develop`; confirm `feature/704-geometry-cut` reproduces it bit-for-bit. Re-run the 512-step rollout profile and report the new wall-clock + cost-split shift.

## 7. Risks & mitigations

- **Stale cache.** Mitigated by the §5 invariant + the version/reset unit tests. The cache is per-env-instance (no global/module state), so it is also safe under a future multiprocess vector env.
- **Hidden reader of `info.valid` mid-episode.** Not relied upon (validity semantics are unchanged here), but the byte-identity regression would catch any divergence regardless.
- **Oracle signature change (`swept_intrusion_m2`).** Additive optional param with a `None` default → existing callers byte-identical; covered by the equality unit test.

## 8. Success criteria

1. Byte-identity regression green (checkpoint + metrics bit-identical to `develop`).
2. Cache-correctness units green.
3. Measured wall-clock reduction on the 512-step profile (target: a meaningful fraction of the redundant ~70%, reported with before/after numbers — exact figure measured, not promised).
4. `ruff`/`mypy ml/` clean; formal `/pr-review` + `ml-rl-guard` pass.
