# Changelog

All notable changes to this project are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Learned backend (epic #607, #736): dense train-to-mastery is RESOLVED-NEGATIVE — the lever
  program is stopped and the backend is scoped to the shipped inference seam (#706).** New
  [ADR-0028](docs/adr/0028-learned-backend-train-to-mastery-resolved-negative.md) records the
  decision; `ml/README.md` and arc42 §5 carry the honest scope. Five gate-run levers (one per
  lever class: #794 start-state scaffold, #809 representation, #812 reward economics, #815
  exploration, #821 reverse-curriculum) each KILLed at the same `valid_placed ≈ 0.333`
  place-one-then-abstain fixed point; a pre-registered measure-first probe then converted those
  into a single **measured** root cause — φ=1 cold-start completion `vp = 0.000` (confirmed on
  both backplay *and* non-backplay control checkpoints), a valid-triple manifold ≈ 2e-3 that is
  FLAT across clearance, and the fact that the deterministic RR-MC solver already reaches
  `trio-notch` (so it was a curriculum stepping-stone, never a charter target). The wall is
  cold-start drive-and-pack of the marginal object into a sparse, clearance-invariant slot; only
  a ρ₀ lever that *trains* that distribution could move it, and the measured capability is zero.
  ADR-0028 carries the falsifiable **re-open gate** (reach-rate beats RR-MC witness-absent, or a
  relative-coordinate encoder lands, or a re-charter to completion) and the **do-not-reattempt**
  list. No behavior change: the inference seam, the determinism contract ([ADR-0027](docs/adr/0027-learned-backend-determinism-scope.md)),
  and `ml/`'s dev/CI-only status are untouched.
- **Learned backend (#835, epic #607): the #832 trigger-#1 reach-rate verdict is RETRACTED — it
  tested an infeasible population.** PR #832 merged a "trigger #1 **NOT MET**" reading (RR-MC `0/9`,
  policies `0/108`) whose "witness-absent" stratum was selected by RR-MC reach ≈ 0 on
  **over-capacity** fleet subsets of the tight 18 m hangar that **cannot fit the full fleet** —
  where RR-MC reaches 0 because the layout is **infeasible**, not missed, so a policy reaching 0 is
  **vacuous** (testing the impossible, not a dominance failure). A valid witness-absent kind needs a
  **feasibility witness**: a valid layout *proven to exist* (hand-authored like
  `examples/herrenteich/layout.yaml`, or a big-budget `solve` result) that the fair-budget deployed
  RR-MC misses. `ml/README.md` + [ADR-0028](docs/adr/0028-learned-backend-train-to-mastery-resolved-negative.md)
  now restate trigger #1 as **runnable but not yet validly executed**; `ml.reach_rate`'s
  `witness_absent_kinds` / `dominance_verdict` docstrings + CLI output carry the
  necessary-but-not-sufficient feasibility caveat (gate boolean logic unchanged). A rung-by-rung
  audit confirms every **training** rung is feasible (`trio-notch` / `trio-box` RR-MC reach **1.000**
  at `k=3`; the committed `witness_box` / `witness_notch` fixtures), so the lever KILLs (all measured
  on the feasible `trio-notch`) and ADR-0028's decision **stand**. `CLAUDE.md` gains a
  feasibility-first ML-eval guard. Docs + a dev/CI-only caveat; no behavior change (`ml/` never
  ships in the wheel).

### Added

- **Learned backend (#827, epic #607): opt-in `--relative-encoder` ego-centric observation encoder
  (ADR-0028 re-open trigger #2).** Augments object pose tokens with four SE(2) ego-relative
  coordinates (`fwd, right, sinΔθ, cosΔθ` in the active object's body frame); `TOKEN_DIM` 24→28 and
  `SCHEMA_VERSION` 1→2 when on, default off = byte-identical. The encoder's frame is derived from the
  policy architecture so the token width and `token_proj` can never disagree. Dev/CI-only (`ml/`),
  not shipped in the wheel. The `trio-notch` ladder gate result is recorded separately once run.
- **Learned backend (#821, epic #607): backplay reverse-curriculum lever (`--backplay-trio-notch`)
  for the dense `trio-notch` plateau (#736), plus a held-out `witness_notch_B` generalization probe.**
  The fifth #736 lever after four refutations (#736 anchor, #809 representation, #812 economics,
  #815 entropy floor) — and the only one that shifts the **start-state distribution** (ρ₀) rather
  than reward/representation/exploration (Ng–Harada–Russell proves potential-based shaping cannot
  move the argmax, which is why those failed). `--backplay-trio-notch` (curriculum-only) inserts an
  opt-in ladder of `trio-notch-backplay-{50,75,100}` sub-rungs before `trio-notch`: each pre-parks
  the **k=N−1 prefix** of a committed 3-object notch witness (`tests/fixtures/ml/witness_notch.yaml`)
  and spawns the single driven object a fraction φ∼U(0, φ_cap) along the corridor from its witness
  park-pose (φ=0, near-solved) **out to the door** (φ=1); the φ_cap ceiling anneals 0.5→1.0 across
  the contiguous sub-rungs, promoted on the **same** windowed `valid_placed` gate (the reverse
  curriculum *is* the rung sequence — no per-iteration anneal code). This collapses the diagnosed
  cold-start coverage minimum (validly park one, abandon two) by starting near the dense solution
  and receding. The env **never snaps or auto-parks** — it only moves *where* the episode begins;
  the corridor spawn is taken only when collision-free (deterministic admissibility check) else it
  falls back to the door. The held-out `witness_notch_B.yaml` (a second geometrically-distinct valid
  notch packing, shipped via #822) is the witness-absent transfer-evaluation variant that hardens
  the pre-registered gate against near-witness overfit. Default-neutral: `--backplay-trio-notch`
  absent ⇒ `DEFAULT_LADDER` byte-identical (φ=None ⇒ byte-identical door spawn); the flag fails
  loud under `--schedule trivial`, `backplay_phi_cap` and `anchor_prob` are mutually exclusive, and
  every witness k-prefix is product-checker valid (`collisions.check` + Caddy egress). The
  per-iteration `--metrics-out` JSONL surfaces the rung's `phi_cap` (the gate's confound watch).
  `ml/` is dev/CI-only (never shipped in the wheel).
- **Learned backend (#812, epic #607): per-commitment economics reward lever (`--r-valid-progress`).**
  A banked marginal valid-coverage credit that targets the dense `trio-notch` plateau (#736), where the
  policy validly parks one freebie aircraft then *abstains* on the rest because the marginal 2nd/3rd
  commitment's expected value is negative — the **economics** argmax the spatial-token representation
  lever (#809) was refuted as unable to move. `step_reward` gains one term
  `r_valid_progress * max(0, valid_park_count − 1)`, paid **only on a Park where the whole layout is
  valid** (the product checker) and scaled by the marginal valid-object count beyond the freebie — so
  the 2nd validly-placed object banks the weight and the 3rd twice it. Banked per-step (it survives GAE
  while the #714 terminal-validity flag collapses) and gated on `park_valid`, so an invalid pile pays
  exactly 0. Default `0.0` ⇒ **byte-identical**. `ml/` is dev/CI-only (never shipped in the wheel).
- **Learned backend (#809, epic #607): opt-in spatial-token cross-attention policy (`--spatial-tokens`).**
  Replaces the spatially-blind global-average-pool — which collapsed the CNN occupancy feature map
  to a single vector broadcast to every object token, so the policy knew *how full* the hangar was
  but never *where* the free gaps were (the dense trio-notch plateau lever, #736). With
  `--spatial-tokens` the CNN feature map becomes per-cell **spatial tokens** (fixed sin/cos 2D
  positional encoding) that the object tokens **cross-attend** to, plus a spatial summary fed to the
  critic. Default off ⇒ **byte-identical** to the prior net (zero new parameters; a deliberate
  new-architecture re-baseline when on, persisted in the checkpoint's `policy_kwargs`). `ml/` is
  dev/CI-only (never shipped in the wheel).
- **Learned backend (#711, epic #607): statistical reach-rate harness (`python -m ml.reach_rate`).**
  Lifts the #695 reach benchmark from a 4-row binary existence table to a reproducible
  reach-**rate** over a **sampled population**: reach-rate ± **Wilson** CI per scenario-kind,
  for both **multi-alternative RR-MC** (`rrmc_reach_multi` solves for N alternatives and counts
  reached if *any* is valid + fully routable — strictly stronger than the #695 `alternatives=1`
  best-spread-only check) and a trained policy (`--policy`, multi-sample stochastic rollouts for
  variance). Both arms judge reach by the same product-checker predicate (`geometry_oracle.layout_valid`
  + routable-by-construction, #694), never the env oracle. `sample_population` draws fleet-subset
  fill scenarios deterministic in `seed`. Per the issue's cost caveat, the RR-MC arm defaults to a
  small population at a modest restart budget (a large baseline is meant to be recorded once and
  frozen, mirroring the `bench_baseline.json` freeze). The RR-MC + stats arms are torch-free;
  `--policy` needs `[train]`. `ml/` is dev/CI-only (never shipped in the wheel).
- **Compare multiple solver alternatives in the 3D viewer (`view --solve --alternatives N`, #666).**
  `hangarfit view --solve --alternatives N -o out.html` solves for up to N diverse layouts and
  builds **one** self-contained offline HTML carrying all of them, with a **switcher** (a
  dropdown plus ←/→ keys) that flips between solutions in a shared, fixed camera — so the aircraft
  that moved between alternatives visibly pop — and a per-solution metrics readout (min inter-plane
  gap, planes moved vs solution #1 and average shift, tow-routability), mirroring the numbers
  `solve` already narrates. When fewer than N diverse solutions exist it carries what there is and
  labels "Found n of N"; with a single solution it falls through to the ordinary single-scene
  render (no compare chrome). `--alternatives` requires `--solve` (a hand-authored layout is a
  single arrangement) and
  exits 2 otherwise. The multi-solution container is a viewer-HTML-level `<script id="solutions">`
  blob (`hangarfit.viewer-compare/v1`) layered **over** N independent `scene/v2` docs — not a
  scene/v2 schema change — so `scene.build_scene` (and its byte-determinism + the scene-contract
  key-parity guard) is untouched and each carried scene is byte-identical to a standalone render
  (ADR-0003). New pure switcher logic (`viewer/src/compare.ts`) is node-unit-tested; the switch
  path re-runs the transform self-check per solution (headless-verified).
- **Learned backend (#736, epic #607): witness-anchored notch-trio curriculum rung
  (`--anchor-trio-notch`) — the 3-object joint-discovery scaffold on the real notch hangar.**
  A diagnostic of the stalled notch trio (`valid_placed`~0.25 on both seeds) found not a
  place-nothing collapse but a **coverage minimum**: the policy validly parks **one** aircraft
  and abandons the other two, because a 2nd/3rd commitment risks the hard collision penalty.
  Since `examples/herrenteich/layout.yaml` is a *valid 8-object witness on that exact hangar*,
  the trio physically fits — the wall is cold-start joint discovery, not capacity.
  `--anchor-trio-notch` (curriculum-only) inserts an opt-in `trio-notch-anchored` rung before
  `trio-notch` that **pre-parks a k=1 prefix of a committed 3-object notch witness**
  (`tests/fixtures/ml/witness_notch.yaml`) and drives the other two in — the trio analogue of the
  #712 `--seed-anchor` box scaffold. The pool is pinned to the witness's objects (so
  `max_objects` equals the witness count, validated pre-flight). Default-neutral:
  `--anchor-trio-notch` absent ⇒ `DEFAULT_LADDER` byte-identical; the flag fails loud under
  `--schedule trivial`. The witness's every k-prefix is product-checker valid (`collisions.check`
  + Caddy egress) at the rung's 0.05 m and the file's 0.10 m clearance. `ml/` is dev/CI-only
  (never shipped in the wheel).

### Changed

### Fixed

## [0.16.0] — 2026-06-22

### Added

- **Solver (#614, epic #600 / milestone 34): SOFT door-priority tie-breaker
  (`Scenario.door_order`).** The deferred soft half of #603's HARD Caddy egress gate: a scenario
  may declare a desired door-proximity order (a top-level `door_order:` list of placeable body ids;
  the first should park nearest the door). Among the already-collision-valid candidate basins the
  solver collects, a new **lexicographically-subordinate** selection term ranks layouts by a
  Kendall-tau inversion count over the placed `door_order` bodies (door distance = the `y_m`
  reference coordinate, the same idiom as the `_back_bias` soft term; absent bodies, e.g.
  a maintenance plane treated as away, are skipped). It sits **above** the ADR-0008 spread terms (a
  declared order wins over maximal spread) but strictly **below every hard rule** — consumed only
  after the collision gate passes, and it stays subordinate to the ADR-0026 egress gate (which
  independently rejects un-routable layouts downstream), so it can never make an invalid layout
  selectable nor reorder a hard rejection. **Determinism (ADR-0003):** unset (`door_order` absent,
  the default) ⇒ a constant `0.0` deviation prefix on the selection key ⇒ byte-identical solver,
  verified by the 6-plane canaries and an unset-is-zero unit. Closes #614, the last open child of
  epic #600 / milestone 34 (Ground objects + Herrenteich calibration).

- **Solver (#754, epic #607 Wave 3 / #760): Lever B — opt-in `solve --sat-collisions` numpy
  SAT box oracle for the collision narrow-phase.** The pairwise + ground-obstacle narrow-phase
  (~61% of each box-rung iteration is shapely `Polygon` work, #381) now has an opt-in second path:
  for **rectangle × rectangle** part pairs, `collisions.check(..., sat_collisions=True)` routes the
  plan-view verdict and the `total_penetration_m2` area through pure-numpy SAT / GJK-distance /
  Sutherland–Hodgman kernels (`hangarfit._sat`, productionized from the #735-validated spike)
  instead of GEOS. A part-kind guard (`WorldPart.is_oriented_rect`, set only on the scalar
  oriented-rectangle build path) falls back to shapely the instant any tapered/strut **polygon**
  part appears — so the SAT path only ever runs where the #735 corpus validated it. Plumbed
  `cli → SearchConfig.sat_collisions → solver._score → collisions.check`, accelerating the
  descent's hot scorer. **Determinism (ADR-0003):** the flag defaults **off**, and off is
  byte-identical to the pre-#754 checker. **On is self-byte-identical** (numpy SAT is referentially
  transparent) but **NOT equal to the off run** — SAT reproduces the GEOS verdict surface to
  ~5e-15 with **0 conflict-count flips** on a 200k clearance-weighted corpus, so layout *validity*
  is unchanged, but the float-noise `total_penetration_m2` can shift the spread/tiebreak trajectory.
  CPU shapely therefore stays the **validity + determinism authority** (#694): the `(0, 0.0)`
  validity gate is SAT-invariant (count never flips), so the returned layout is always
  shapely-valid; SAT only makes the inner search cheaper. Most useful on box-rung-style
  all-rectangle fleets. Validated by a check-level bit-diff harness vs GEOS across all-rect /
  mixed / tapered-fallback fixtures, a monkeypatch test proving the GEOS seam is genuinely bypassed
  (not a silent fallback), and a `solve --sat-collisions` self-determinism + shapely-validity gate.

- **Learned backend (#755, epic #607 Wave 4 / #761): opt-in `--pipeline-update` — overlap the
  CPU rollout with the GPU PPO update (one-iteration-stale pipeline).** In the vectorized
  curriculum path, while `ppo_update` runs on the live policy the workers collect the NEXT rollout
  under a frozen `deepcopy` snapshot of the **pre-update** policy (exactly one iteration stale),
  recovering the GPU/worker idle time during the otherwise strictly-sequential rollout↔update
  phases. The rollout runs on a single background thread; the live policy and the snapshot share no
  mutable state (separate module objects; the env is touched only by the rollout thread, serialized
  across iterations by `future.result()`). The next rollout is launched only when another iteration
  will consume it (never on the stop/final iteration), so no speculative rollout is wasted and there
  is nothing to drain. **Default off = byte-identical** sequential training (the existing loop is
  left untouched in the `else` branch). **On is NON-deterministic by design** — the one-iteration
  staleness AND the background rollout sharing the global torch RNG with the update's minibatch
  shuffle (a safe, mutex-guarded race) perturb the learning curve — so it is re-gated on a two-seed
  `ml.gate` valid_placed delta, NOT a byte-diff (that re-gate is a follow-up long-run). No effect
  with `--schedule trivial` or `--n-envs 1`. Dev/CI-only (`ml/`); no shipped-wheel surface.

- **Learned backend (#754, epic #607 Wave 3 / #760): Lever A — whole-leg swept-envelope AABB
  early-out for the rollout's swept-clearance oracle (byte-identical).** `swept_intrusion_m2`
  (`ml/geometry_oracle.py`) sampled a tow leg at 0.05 m / 1° and ran the per-pose `_motion_clear`
  + overlap measurement on every sample — and #733's pose cache mostly misses for the swept mover
  (its pose changes each sample). Lever A proves a whole clear leg in ONE test: the result is 0.0
  iff no sampled pose's mover parts overlap a parked obstacle part (walls/notch/bay only gate
  `_motion_clear`, never contribute leak), and every pose's footprint lies within the body's
  footprint radius `R` of that pose's `(x, y)` — `R` is heading-independent because the
  determinant-−1 transform preserves Euclidean norm — so the bbox of the swept `(x, y)` inflated
  by `R` is a conservative superset of every pose's footprint. If that envelope AABB is strictly
  separated from every precomputed obstacle-part AABB, the leg short-circuits to 0.0 with **zero
  per-pose Polygon builds**. **Byte-identical** (ADR-0003): a conservative lower-bound filter (the
  same logic as the per-pose AABB prefilter + `collisions._aabbs_separated_beyond_clearance`) that
  fires only when the full loop's result is exactly 0.0 — it can never mask a real intrusion,
  verified by an 80-leg adversarial fuzz asserting equality to the exact unfiltered reference plus
  a zero-per-pose-build proof. (Lever B — the opt-in `--sat-collisions` numpy-SAT box oracle — is a
  follow-up.) Dev/CI-only (`ml/`); no shipped-wheel surface.

- **Learned backend (#753, epic #607 Wave 2 / #759): episode-scoped pose cache — widen
  #733 from per-step to per-episode (byte-identical).** #733's `cached_parts_world` memo was
  opened in a fresh `pose_cache_scope()` per `_EnvWorker.step`/`reset`, so it was thrown away
  every timestep and every frozen parked / identity-pose body was re-transformed from scratch
  each step. The worker now holds **one pose dict across the whole episode** (`pose_cache_scope`
  gained an optional `cache` arg so a caller-owned dict persists across scopes), cleared at each
  episode boundary (reset + the in-step auto-reset) to bound memory. Each worker owns its own
  dict, so `SyncVectorEnv` (N workers, one process, one ContextVar) never cross-leaks and the
  ContextVar set/reset stays per-call LIFO-safe. The genuine win is the encoder's repeated
  identity-pose `_body_dims` / `_parked_occupancy` rebuilds (the heaviest parked consumers are
  already env-cached, so the active mover + swept arc still miss — that is #735/#754). **Byte-
  identical** (ADR-0003): `cached_parts_world` is referentially transparent (exact-float pose
  key, frozen-slots `WorldPart`, read-only consumers), so widening changes only *when* a pose is
  rebuilt, never its bytes — pinned by the #733 cache-on/off reward+obs stream test and the
  solver/towplanner determinism canaries (the `cache=None` default keeps `solve`/`plan_fill`
  byte-identical). A new test proves the identity body-dims pose is rebuilt **once per episode**,
  not once per step. Dev/CI-only (`ml/`); no shipped-wheel surface.

- **Learned backend (#752, epic #607 Wave 2 / #759): shrink the rollout IPC payload —
  uint8 raster + drop the re-shipped static channels (byte-identical).** Every vectorized
  training step pickles each worker's 7×192×96 float32 raster over a Pipe, but two-thirds of
  that traffic is waste: the raster is binary occupancy shipped at 4 bytes/cell, and 4 of the 7
  channels (`oob`/`bay`/`apron`/`door`) depend only on `(hangar, config)` yet were recomputed
  *and* re-shipped byte-identically by every worker every step. The encoder now splits into a
  **static block** (`encoding.static_block(hangar, config)` — the 4 hangar-fixed channels) and
  `encode_dynamic` (the 3 observation-dependent channels as **uint8** 0/1); the worker ships only
  the dynamic block, and the parent re-prepends the rung's cached static block in `to_batch`
  (`encoding.reassemble_raster`), rehydrating the full 7-channel float32 raster. This cuts the
  per-step worker→parent payload ~57% **and** deletes the 3 static `shapely.contains_xy` calls
  from every worker step (computed once per rung instead). **Byte-identical** (ADR-0003): the
  dynamic block is binary, so float32(0/1)→uint8→float32 is lossless and `reassemble_raster`
  reproduces `encode()`'s raster bit-for-bit (`test_reassemble_raster_equals_full_encode_bitwise`);
  `to_batch` keys on the uint8 dtype, so every non-vectorized caller (full float32 obs) passes
  straight through, and Sync still equals Subproc byte-for-byte. Dev/CI-only (`ml/`); no
  shipped-wheel surface.

- **Learned backend (#751, epic #607 Wave 1 / #758): opt-in `--vec-start-method`
  {spawn,forkserver,fork} to cut per-worker training RAM.** RAM — not cores or GPU — is the
  ceiling for both `--n-envs auto` (#747) and the concurrent sweep runner (#749): `spawn`
  re-imports torch + shapely *privately* in every worker (~327 MiB PSS/worker measured). Since
  the workers are torch-free in their *ops*, `--vec-start-method forkserver` forks them from a
  shared server that preloads those modules once, so all workers share the pages copy-on-write —
  **measured ~327 → ~71 MiB PSS/worker (~4.6×; CPU-only, Linux/Py3.12, N=4)**, which raises the achievable `--n-envs`/sweep
  concurrency. **Default stays `spawn`** (the byte-identity reference); `forkserver` is verified
  **byte-identical** to it (the worker's `stage_rng` is `worker_index`-keyed, so the start method
  can't perturb the trajectory — pinned by `test_subproc_forkserver_byte_identical_to_sync` +
  `test_sync_equals_subproc_byte_identical`). `fork` is an explicit escape hatch that warns loudly
  (copying a torch-loaded / CUDA-holding parent can deadlock — `forkserver` is the safe path).
  Dev/CI-only (`ml/`); no shipped-wheel surface.

- **Learned backend (#750, epic #607, throughput Wave 1): a transitions/sec training-loop canary
  + a vectorized width-N GAE scan.** Two dev/CI-only changes so the rest of the throughput work is
  measured, not eyeballed. (1) `python -m bench.train_throughput` is the `ml/` twin of
  `bench.profile_pipeline`: it runs a small, fixed, deterministic CPU training loop (`SyncVectorEnv`)
  and reports **transitions/sec** + **iters/sec** with the per-phase rollout-vs-update split as a
  table or `--json`, **bound on a fixed step COUNT** (`iterations × rollout_len × n_envs`, mirroring
  the #381 `max_restarts` binding) so only machine speed varies run-to-run. It confirms the Wave 1
  premise — ~85% of per-iteration wall-clock is the shapely-bound rollout. No `--gate`: a throughput
  ceiling is jitter-prone on shared runners, so it reports, never enforces. (2) `compute_gae_vec`
  (`ml/ppo.py`) is rewritten from a `for env in range(N)` wrapper around the scalar reverse-scan into
  a single width-N reverse scan, removing the last O(T·N) pure-Python loop so GAE stops scaling with
  `n_envs`. **Byte-identical** (ADR-0003): the scalar loop boxes every term via `float()` → a float64
  accumulator, so the vector scan runs its `delta`/`last_gae` accumulator in float64 (`.double()`) and
  casts back to float32 only at each `adv[t]` write — a naive float32 scan diverges by ~2.4e-6 (a
  determinism break that fails the `n_envs=1` / Sync≡Subproc byte-identity oracle). Verified by
  `torch.equal` against the per-env `compute_gae` over mid-rollout / all-done / no-done patterns
  (`test_compute_gae_vec_byte_identical_to_per_env_loop`). Dev/CI-only (`ml/` + `bench/`); no
  shipped-wheel surface. The torch-CI hook stays scoped out of v1 (CI installs only `[dev]`, no torch).

- **Learned backend (#749, epic #607, throughput Wave 1): concurrent multi-seed sweep runner
  `python -m ml.sweep`.** The mastery deliverable is the two/three-seed gate (the `ml/README`
  trio-box recipe), run **serially** today — one launch per seed, babysat by hand. One on-policy
  run is throughput-capped by its synchronous step-dependency, so the box sits idle (~26 cores,
  ~10% GPU). `ml/sweep.py` is a **torch-free** orchestrator that spawns K **unmodified**
  `python -m ml.train` subprocesses (one per `--seed`), each with a distinct `--seed` +
  per-cell `--metrics-out`/`--checkpoint-out`/`--save` path, runs them **concurrently** up to
  `--max-concurrency` (default **2**, documented **RAM-bound** — ~10 GB/run → K=3 risks OOM on a
  31 GB box, *not* core-bound), and aggregates child exit codes into a single pass/fail verdict
  in deterministic seed order. **Loud by contract** (the #749 risk): any non-zero child — or a
  child that *crashes* — makes the runner exit non-zero, surfaced as a failed cell with its
  error text rather than silently corrupting a 2-seed verdict. Pure orchestration, **no
  training-loop edits** — each child is byte-identical to running it alone, so co-locating cells
  on one GPU adds nothing beyond `--device cuda`. The job-spawning seam is injectable for fast
  deterministic unit tests (no real multi-minute training spawned). Per-cell metrics roll up via
  the existing torch-free `python -m ml.gate`. Expected **~2× sweep wall-clock** (not Kx —
  aligned rollout bursts oversubscribe). Dev/CI-only (`ml/`); no shipped-wheel surface.

- **Learned backend (#748, epic #607 Wave 1 / #758): out-of-order (as-completed) vec-env
  fan-in.** `SubprocVectorEnv` collected each step's N worker replies with a strict in-order
  blocking recv (`worker 0`, then `1`, …), so the single slowest shapely worker stalled the
  whole batch every step (head-of-line blocking) — worsening as `--n-envs` and per-rung
  shapely-cost variance grow. It now drains workers **as they complete**
  (`multiprocessing.connection.wait`) and **re-indexes by worker** before batching, so the
  per-index payloads and the policy input are unchanged — only the pipe read order differs.
  **Byte-identical, no flag** (pinned by `test_sync_equals_subproc_byte_identical` + a new
  reversed-completion-order test proving index alignment is preserved). Recovers the
  worst-case-worker stall; the win grows with `n_envs`. Dev/CI-only (`ml/`); no shipped-wheel surface.

- **Learned backend (#747, epic #607 Wave 1 / #758): cap worker BLAS/OMP threads +
  `--n-envs auto` to fill idle cores.** A measured `--n-envs 16` run pinned only ~5.5 of
  32 cores, and the spawn workers ran *unconstrained* BLAS/OMP/MKL threading, so naively
  raising `--n-envs` toward the core count would **oversubscribe** the box. Two paired,
  byte-identical changes: (1) each `ml/vector_env.py` spawn worker now caps itself to a
  single thread (`OMP/OPENBLAS/MKL/NUMEXPR_NUM_THREADS=1` + `torch.set_num_threads(1)`) at
  worker-loop entry — one core per worker. The cap is set in the **parent** before
  `spawn` so each child inherits it at interpreter startup (OpenBLAS/MKL fix their pool
  size at numpy-import time and ignore a late env write — measured cpu/wall ≈ 31 vs 1).
  (2) `--n-envs auto` sizes the worker pool to
  `max(1, min(schedulable_cores − reserved, (MemAvailable − headroom) // per_worker))`,
  using `os.sched_getaffinity` (cgroup/`taskset`-aware, unlike `cpu_count` which
  overcounts on WSL2/containers) and `/proc/meminfo` `MemAvailable`. The default stays `1`, so
  `--n-envs 1` remains the byte-identity floor; `auto`/raised values are opt-in per run.
  The thread cap is byte-identical (anchored on the **torch-free-worker** invariant —
  workers run no policy ops, so a 1-thread cap can't perturb numpy/shapely reduction
  order), proven by `test_sync_equals_subproc_byte_identical` + a fixed-action
  reward-stream diff. `--n-envs > 1` (or `auto`) on `--schedule trivial` now fails loud
  (the trivial path is single-env). Dev/CI-only (`ml/`); no shipped-wheel surface.

- **Learned backend (#734, epic #607): slope-aware `--auto-budget` per curriculum rung.**
  A fixed `--max-iters-per-stage` cap is wrong in both directions — it truncated the trio-box
  (N=3) run while `valid_placed` was *still climbing* (peak 0.65, under-trained not collapsed),
  and it lets a stuck sub-threshold rung grind to the cap with no upward signal. `--auto-budget`
  replaces the fixed cap with a closed loop: a pure `BudgetController` (in `ml/curriculum.py`,
  mirroring `should_promote`'s purity) fits a robust **Theil–Sen slope** over the per-iteration
  windowed-mean promotion-metric series (default `valid_placed`) and **extends** the rung while
  competency hasn't fired and the slope is positive, **stopping early** once the slope is
  non-positive for `plateau_patience` consecutive windows (or the hard ceiling is reached).
  Mis-fire guards: a `min_iters` floor, the `plateau_patience` consecutive-window debounce, and
  the `--auto-budget-max-iters` ceiling (default 1000). Wired
  into both the single-env and vectorized per-rung loops; **default off** → the fixed-`max_iters`
  path reproduces today's runs byte-for-byte (4c-ii default-neutrality). Distinct from the
  manual `--stop-after-rung` (#723). Dev/CI-only (`ml/`); no shipped-wheel surface.

- **Learned backend (#733, epic #607): activate `pose_cache_scope` + `cached_parts_world`
  in the `ml/` RL rollout — top throughput lever, closes the #453/#704 gap.** Per-iteration
  training is ~94% CPU/shapely-bound and ~61% of the rollout is `aircraft_parts_world`
  Polygon construction; the env was rebuilding the **same** `(body, pose)` shapely parts
  repeatedly across the collision check, the reward oracle and the encoder rasterizer. The
  `ml/` geometry consumers (`geometry_oracle` intrusion / swept-intrusion / active-misfit and
  the `encoding` rasterizer) now call the pose-memoized `cached_parts_world`, and the
  vectorized `_EnvWorker.step`/`reset` + the single-env `collect_rollout` open a per-step
  `pose_cache_scope` spanning the env step **and** the encode, so each pose is built once
  across all consumers. The scope is opened **per env method call** (one fixed-fleet env), so
  the `(plane_id, x, y, heading)` key is never stale even when `SyncVectorEnv` steps workers
  in one process. **Byte-identical** (ADR-0003): `cached_parts_world` is an inert passthrough
  outside a scope and returns the same `WorldPart`s the pure function builds inside one, so
  the new `pose_cache=False` toggle (on `_EnvWorker` / `collect_rollout`, default-on)
  reproduces the un-cached run bit-for-bit — verified via the established fixed-action
  reward-stream + encoded-observation diff, not checkpoint hashes. Folds in an additive,
  byte-identical AABB-disjointness pre-filter on the swept-intrusion leak loop (reusing the
  obstacles' precomputed `world_part_aabbs`). Dev/CI-only (`ml/`); no shipped-wheel surface.

- **Learned backend (#730, epic #607): trio-box training-gate harness + launch recipe.**
  No-GPU prep for the #698 train-to-mastery frontier — does the #720/#728 four-lever ladder
  generalize past the 2-object `pair-box` to the 3-object `trio-box` rung (the historical
  ≥2-object wall)? Adds `ml/gate.py` (`python -m ml.gate METRICS.jsonl --rung trio-box`), a
  **torch-free** reader for the `--metrics-out` JSONL that emits a per-rung verdict headlining
  `valid_placed` (never `valid_rate` — vacuously 1.0 under place-nothing) with a **piling
  watchdog** (`fraction_placed` high while `valid_placed` low = committing objects invalidly).
  Verdicts: `mastered` / `piling` / `place-nothing` / `in-progress` / `no-data`, with exit
  codes (0/1/2) for unattended sweeps. `ml/README.md` gains the two-seed `--stop-after-rung
  trio-box` checkpoint-resume launch recipe + resume gotchas; a curriculum smoke test guards the
  exact truncated sweep-shape ladder. Does not run the sweep itself (needs the GPU).

- **Learned backend (#724, epic #607): #720 empty-start `pair-box` gate — two-seed PASS; L4
  clipping confirmed load-bearing; recipe `reward_clip 10 → 50`.** The #720 L5+L4 gate was run as a
  #722 checkpoint-resume sweep on GPU. The empty-start `pair-box` rung — `valid_placed=0.000` in
  every prior gate — now **promotes by competency on both seeds** (seed 0 iter 27 `vp` 0.80, seed 1
  iter 19 `vp` 0.85), placing both objects validly with no piling. A controlled A/B (seed 0, same
  upstream checkpoint, same seed, byte-identical iter 0, only the three L4 flags differing) showed
  **L4 trust-region clipping is load-bearing, not optional**: clip-off collapses to place-nothing
  (the deep ≈−1400 collision-penalty gradient outlier drives PPO into the place-nothing absorbing
  state), clip-on masters. The recipe's `--reward-clip` is corrected `10.0 → 50.0` — sized so the
  per-step **graded** valid-park bonus (`r_valid_park 30 + r_first_valid 15 = 45`) below the clip
  (so the L5 near-miss gradient survives) while the deep spikes are clamped — the episode-completing
  step's `r_terminal` credit does saturate the ±50 clamp, by design. `10` would clip even the graded
  bonus and flatten the L5 gradient; `50` is the validated value. `ml/README.md` recipe + WIN section updated with the confirmed result.
  Docs-only — the L4 flags already shipped in #720.

- **Learned backend (#722, epic #607): `--stop-after-rung NAME` truncates the curriculum
  ladder for sweep cells.** The #720 economics re-gate runs as a checkpoint-resume sweep — train
  the ladder once through `pair-mixed`, then `--load` and sweep only the `pair-box` rung across a
  grid of `--w-col`/`--valid-park-grade-scale` cells. Previously each resumed cell, after
  mastering/capping `pair-box`, ground on into `trio-box`/`trio-notch`/`trio-notch-strict`
  (≈3 extra `trio-*` rungs, each to the per-stage cap, of wasted PPO iters per cell), with
  manual `Ctrl-C` the only workaround. `--stop-after-rung`
  (curriculum-only) drops every rung after the named one — `--stop-after-rung pair-mixed` for the
  upstream train, `--stop-after-rung pair-box` for each cell — so the sweep runs unattended. A
  pure `truncate_after_rung` schedule transform (`ml/curriculum.py`) mirroring the `with_*_rung`
  grafts; the `train_curriculum` loop is untouched. A name (not a count) because resume *skips*
  completed rungs, making a count ambiguous; a typo'd rung fails loud. Absent ⇒ byte-identical.

- **Learned backend (#720, epic #607): graded-economics + PPO trust-region levers to break
  the empty-start place-nothing cliff.** A multi-agent diagnosis of the `--mixed-anchor` gate
  failure (seed-0: `pair-mixed` capped oscillating ~0.2, `pair-box` collapsed to
  `valid_placed 0.000`) root-caused it as *economics × discoverability*: from empty, do-nothing
  is a small bounded loss (≈−8 observed on the failed seed-0 gate run) while any exploratory
  mis-Park books the **unclipped** `−w_col·overlap`
  (−5000…−12000), so place-nothing is the genuine reward argmax. **L5** (reward, default-neutral):
  `--valid-park-grade-scale` grades the `r_valid_park` bonus by near-miss misfit
  (`r_valid_park·exp(−misfit/scale)`) into an uphill gradient toward the witness slot;
  `--r-first-valid` is a one-time breakthrough bonus on an episode's first valid placement;
  `--w-col` exposes the collision weight. **L4** (PPO): `--reward-clip`,
  `--value-clip-eps` (PPO2 clipped value loss), and `--target-kl` (epoch early-stop) tame the
  unclipped collision spike that drove the gate's −5000…−12000 sawtooth; `ppo_update` now also
  reports `approx_kl`/`epochs_run`. As shipped in #720 all knobs were 0/None ⇒ byte-identical
  training; the L4 trio later graduated to default-on (#728 — see Changed). The refuted
  continuous-k-anneal lever is **not** added (policy-invariant on the empty-start sub-MDP).

- **Mixed-start anchor curriculum rung (`--mixed-anchor`, #712 follow-up).** An opt-in
  `pair-mixed` rung where each episode randomly starts anchored (k=1) or empty (k=0), keeping
  empty-start episodes in the training mix to bridge the k=1→k=0 start-state cliff that
  collapsed the empty-start `pair-box` to place-nothing. Default-off ⇒ byte-identical training.

- **Learned backend (#712, epic #607): seed-anchor start-state curriculum graft
  (`--seed-anchor`) — the 2-object joint-discovery scaffold.** The #714 re-gate confirmed a
  genuine joint-discovery wall (`trivial` + `solo-box` master, but `pair-box` stalls at
  `valid_placed ≈ 0.05`, and the `--normalize-returns`-off control is strictly worse, so the
  residual is discovery, not the normalizer). `--seed-anchor` (curriculum-only) inserts an
  opt-in `pair-anchored` rung before `pair-box` that **pre-parks a k-prefix of a committed
  witness layout** (`tests/fixtures/ml/witness_box.yaml`) and drives only the remaining N−k
  objects in, so 2-object discovery is scaffolded by a guaranteed-valid 1-object start.
  Correctness rests on one property — a k-prefix of a valid layout is itself valid (removing
  objects cannot create conflicts) — so the partial start needs **no runtime solver / search**
  (the env stays solver-free). `DifficultyConfig.seed_anchor` (an unwired stub) is replaced by
  `seed_anchor_k: int = 0`; `HangarFitEnv` gains an `anchor_placements` arg and pre-parks the
  request **prefix** at reset, which composes with the curriculum's existing seeded
  per-episode permutation into a **seeded-random k-subset** anchor (so the choice is
  deterministic + Sync≡Subproc byte-identical). `Stage.anchor_layout_path` + `stage_builder`
  load the witness (single source of truth for the rung's object set and poses). All
  default-neutral: `seed_anchor_k=0` / `--seed-anchor` absent → CPU byte-identical to prior
  runs; `--seed-anchor` fails loud under `--schedule trivial`.

- **Learned backend (#710, epic #607): opt-in CUDA training (`--device cuda`).**
  `ml.train` / `train_curriculum` / `train` gain a `--device {cpu,cuda}` knob. `cpu`
  (the default) is unchanged and **byte-identical** to prior runs — every device move is
  gated behind `device.type != 'cpu'`, so the ADR-0027 / determinism contract holds for the
  CPU path. `cuda` moves the policy + the PPO-update minibatch tensors to the GPU (GAE stays
  on CPU, a per-step scalar loop); it is an **explicitly non-deterministic** fast path
  (GPU RNG / kernels). The `ml.train` **CLI** rejects `--device cuda` loudly when
  `torch.cuda.is_available()` is `False` (library callers own device selection). Measured
  ~5.8x on the PPO update on an RTX 4090; the shapely-geometry rollout stays CPU-bound, so
  the net per-iter gain is bounded by the update's share.

- **Learned backend (#710, epic #607): per-rung training-metric dump + promotion-gate
  CLI levers for the mastery study.** `ml.train --schedule curriculum` gains
  `--metrics-out PATH` (writes one JSONL record per PPO iteration —
  `stage`/`iter`/`n_eps`/`mean_ep_reward`/`fraction_placed`/`valid_rate`/`valid_placed`),
  exposing the compound `valid_placed` learning curve the CLI previously discarded (it
  logged only `mean_ep_reward`). Two new `PromotionPolicy` overrides — `--promotion-metric
  {fraction_placed,valid_rate,valid_placed}` and `--promotion-threshold` — let a run
  advance the easy rungs on `valid_rate` (or a lowered threshold) while `valid_placed` is
  still pinned at 0. All three are default-neutral (omitting them is byte-identical to
  prior runs); `--metrics-out`/`--promotion-*` are curriculum-only and fail loud under
  `--schedule trivial`. The per-iter metric helpers (`episode_metrics`,
  `history_metric_records`, `with_promotion_overrides`) are pure/torch-free in
  `ml/curriculum.py`.

- **Learned backend (#710, epic #607): resume checkpoints (`--load` / `--checkpoint-out`).**
  `ml.train --schedule curriculum` gains `--checkpoint-out PATH` (writes a rich resume
  checkpoint after **each rung** — policy + Adam optimizer + return-normalizer state +
  architecture + completed-rung position, via the new `ml/checkpoint.py`) and `--load PATH`
  (restores all of it and **skips already-completed rungs**), so a long box-rung mastery run
  survives a crash. Distinct from `--save` (still a bare `state_dict` for the ONNX/`ml.eval`
  consumer); the resume checkpoint loads with `weights_only=True` (no arbitrary-code
  deserialization). The checkpoint's architecture is authoritative — a conflicting
  `policy_kwargs` raises. Both flags default off → the legacy path is byte-identical;
  curriculum-only (fail loud under `--schedule trivial`).

- **Learned backend (#710, epic #607): policy-architecture + PPO CLI knobs.** `ml.train`
  gains `--d-model` / `--n-layers` / `--n-heads` (policy size; omitting them keeps
  `HangarFitPolicy`'s own defaults) and `--epochs` / `--minibatch-size` (`PPOConfig`),
  so the mastery run can scale net size / update epochs without code edits. Default-neutral:
  omitting the arch flags yields `policy_kwargs=None` (own defaults) and the PPO flags
  default to the `PPOConfig` dataclass values — byte-identical to prior runs.

- **Learned backend (#710, epic #607): Park/drive-out economics rebalance
  (`--r-unplaced-penalty`).** A new default-0 `RewardWeights.r_unplaced_penalty` adds a
  terminal penalty per **unplaced** fraction (`terminal = r_terminal·frac −
  r_unplaced_penalty·(1−frac)`), so running an object to budget exhaustion is no longer free
  relative to committing a Park. This targets the diagnosed cause of `valid_placed=0` (the
  agent learned to avoid committing a Park to dodge the one-shot `−w_col` collision cliff —
  the `fraction_placed` 0.991→0.476 collapse measured in the #697 baseline), which the originally-planned "dense
  collision-progress reward" could **not** fix (it duplicates the policy-invariant
  `dense_slot_potential` shaping and so cannot move the optimum). Default 0 → byte-identical;
  pairs with the existing `--r-valid-park` for the positive pull toward valid commits.

- **Learned backend (#714, #710, epic #607): validity-conditional terminal +
  `solo-box` sub-curriculum rung — the multi-object collapse fix.** After the economics
  rebalance let the policy **master the trivial (1-object) rung**, every ≥2-object rung still
  collapsed, oscillating between place-nothing and *commit-everything-invalidly* (parking a
  heap of overlapping objects). Root cause: the terminal credited `r_terminal·fraction_placed`
  **regardless of validity** — invisible at N=1 (fraction is 0/1) but a free `+r_terminal`
  for invalid piles at N≥2. Two default-neutral levers: (1) `--validity-conditional-terminal`
  (new `RewardWeights.validity_conditional_terminal`) credits the **valid** placed fraction
  instead — an invalid terminal layout scores effective-fraction 0, so an overlapping pile no
  longer pays; it also closes the budget-exhaustion branch, which previously carried no
  validity signal at all. Validity is the same whole-layout product checker
  (`collisions.check` + Caddy egress) that drives the `valid_placed` promotion gate. (2)
  `--solo-box-rung` (curriculum-only) inserts an opt-in `solo-box` rung (1 object, **whole
  fleet**) after `trivial`, decoupling the count jump (1→2) from the sampling-pool jump
  (single-fuji→whole-fleet) so single-object competency transfers. Both default off →
  byte-identical (the default ladder is unchanged); `--solo-box-rung` fails loud under
  `--schedule trivial`.

- **Vectorized training envs (#708, epic #607).** `train_curriculum`/`ml.train` gain an
  `n_envs` knob (`--n-envs`, `--vec-backend {sync,subproc}`) that runs N cold-joint envs in
  parallel for throughput — the shapely geometry + encoder rasterization run across N
  torch-free worker processes (`ml/vector_env.py`: `SyncVectorEnv`/`SubprocVectorEnv`), while
  the main process keeps the single batched policy forward + PPO (`VecRolloutBuffer` +
  per-env GAE). `n_envs=1` keeps the legacy single-stream path byte-identical. Because the
  workers are torch-free, `Sync(seed,N)` and `Subproc(seed,N)` are byte-identical.
  Foundation for the #698 train-to-mastery run.

- **Learned backend inference (#706, epic #607).** `solve --backend learned --weights PATH`
  now runs: a trained policy is exported to ONNX (`ml/export.py`, `train --save-onnx`) and
  run torch-free via onnxruntime (`ml/infer.py`), returning a `SolveResult` (valid layout +
  the policy's own drive-in tow plan) behind the deterministic verifier. New optional
  `[learned-infer]` extra (onnxruntime); the `[train]` extra also gains `onnx>=1.16`
  (required by `ml/export.py` to serialize the ONNX proto). The verifier
  (`collisions.check` + Caddy egress) remains the sole arbiter of validity (ADR-0027); an
  invalid proposal returns a no-layout result. Wheel distribution, CI, and signed weights
  are tracked in #6.

- **Learned backend (#607, 4c-ii): cold-joint RL env fixed-obstacle support and
  four default-neutral basin-escape knobs.** Fixed-obstacle pre-placements
  (immovable keep-outs from `Scenario.fixed_obstacle_placements`) are now honoured
  by `HangarFitEnv` — they are rendered into the occupancy raster and block the
  agent from parking on top of them, unblocking the eval benchmark's policy column
  on the Herrenteich anchors. Four optional training knobs are added, all
  default-neutral (byte-identical to prior runs when not set): `--r-valid-park`
  (bonus per Park action when the full layout passes `layout_valid`),
  `--dense-slot-potential` (in-hangar nearest-free-pocket shaping),
  `--entropy-start/--entropy-end/--entropy-anneal-iters` (per-rung entropy
  coefficient anneal), and `--normalize-returns` (std-only Welford return
  normalization before GAE). Training defaults are unchanged.

- **Cold-joint RL curriculum schedule (#607 sub-project #4b).** `python -m ml.train
  --schedule curriculum` now climbs a competency-gated difficulty ladder (object
  count → hangar shape → clearance) instead of training a single fixed stage; the
  fixed trivial stage remains reachable via `--schedule trivial`. New pure
  `ml/curriculum.py` (Stage ladder, promotion gate, seeded object sampling) and
  torch-free `ml/stage_builder.py`; `HangarFitEnv.reset()` gains an optional
  `requested_ids` override (default unchanged).

- **Cold-joint RL reach-not-beat eval benchmark (`ml/benchmark.py` + `ml/eval.py`, #607
  sub-project #4c-i).** A frozen curated set of real Herrenteich scenarios, each anchor
  paired with a committed witness layout the deterministic checker accepts (the
  reachability proof). `python -m ml.eval --checkpoint P` rolls a trained policy out
  deterministically and prints a side-by-side both-rates table against the RR-MC→tow
  baseline (recorded offline at a pre-registered budget into
  `tests/fixtures/ml/bench_baseline.json`). Success gate = valid + routable-by-construction
  (the product `collisions.check` + Caddy egress). `python -m ml.train --save P` exports a
  checkpoint. Dev/CI-only (the `[train]` extra); the Herrenteich anchors' policy column and
  env fixed-obstacle support land in 4c-ii (#693), and the env-oracle inert-bay
  over-strictness is tracked as #694.

- **Cold-joint RL environment + reward (`ml/`, #607/#672).** Added the dev/CI-only
  top-level `ml/` package with `HangarFitEnv` — a gym-style environment where an agent
  drives objects in from the apron and parks them one at a time, scored by a
  graded-lexicographic reward (collision/out-of-bounds/egress hard terms, movement
  cost, soft spread/sequence/region, terminal fraction-placed) plus policy-invariant
  potential-based shaping. Reuses the deterministic geometry oracle (`collisions.check`,
  the parts-model transform, the ADR-0010 motion primitives incl. the #647 strafe,
  the Caddy egress oracle) — **not** the RR-MC/Hybrid-A* search. No neural net, no
  training, no new runtime dependency; `ml/` is excluded from the wheel like `bench/`
  and `viewer/`. Sub-project #1 of the learned backend (ADR-0027).

- **`solve --backend {rrmc,learned}` seam + learned-backend determinism scope (ADR-0027, #607/#670).**
  Added the opt-in backend switch on `hangarfit solve`. The default `rrmc` is the
  unchanged deterministic random-restart solver (byte-identical — `determinism-guard`
  intact). `--backend learned` selects the planned neural backend (epic #607); it is
  **not yet implemented**, so it exits cleanly (code 2) with a pointer to #607 rather
  than a traceback, and the new `hangarfit.learned.solve_learned()` library entry
  returns the same `SolveResult` shape (stubbed to raise `LearnedBackendUnavailableError`).
  New **ADR-0027** amends ADR-0003's *scope*: the verifier (`collisions.check` +
  `towplanner`) stays strictly byte-identical and `determinism-guard`-gated, while the
  learned *proposer* gets a weaker, documented contract (within-build bit-identical;
  cross-machine validity-only) and is outside `determinism-guard`. No ML dependencies
  are added.

- **Real Airfield Herrenteich 'today' layout + clearance recalibration (#664).**
  Added `examples/herrenteich/layout_today.yaml`: the club's actual in-hangar set
  as described on 2026-06-15 — all **nine** aircraft (incl. the Scheibe Falke) +
  the **one** Duo Discus glider trailer (the spare is stored elsewhere) + the fixed
  fuel trailer + the rescue Caddy with a clear drive-out egress. Validating this
  real set against the model was the existence-proof test: an offline checker-driven
  search finds **no valid arrangement of it at the previous `clearance_m 0.20`** (its
  best still leaves conflicts) but seats it cleanly **at 0.10 m**, and the club
  confirms real wingtip-to-part gaps vary a lot
  and on dense days are very tight. So the Herrenteich `hangar.yaml` horizontal
  parked clearance is **recalibrated 0.20 → 0.10 m** (vertical wing-layer clearance
  unchanged at 0.15 m — it was not the binding constraint). Lowering the clearance
  only relaxes the constraint, so `layout.yaml`, `layout_full.yaml`, and
  `scenario_demo.yaml` stay valid; the synthetic `data/hangar.yaml` is untouched.
  `layout_full.yaml` is reframed as the alternative "both glider trailers inside"
  scenario (which forces one aircraft out). The collision **model** is unchanged —
  the gap was data calibration, and reliably packing this dense a 12-body set
  remains beyond the deterministic search (#607).

- **Caddy hard-door egress lane in 2D + 3D (#652).** The egress oracle
  (`towplanner.egress_first_conflict`) now optionally *surfaces* the winning
  drive-out corridor it used to compute and discard (new `egress_path_out`
  out-param + an `egress_corridors` helper that collects one per hard-door mover).
  `solve --render-paths` / `check --render` draw it as a dashed amber "keep-clear"
  decal on the 2D PNG, and `view` draws it as a dashed amber floor line in the 3D
  viewer (new `BRAND.egressLane` token + a `scene/v2` `egress_lanes` key, always
  emitted, empty when there is no hard-door egress lane). A blocked or absent
  egress is inert (`{}`) and byte-identical (ADR-0003) — the out-param defaults to
  `None`, so the solver's egress gate stays the authoritative exit-3 verdict. This
  completes the ground-object visualization arc opened by #606.

- **Placed-routed movers animate along their drive path (#651).** Building on the
  static ground-object render (#606), a placed/routed mover (the VW Caddy + glider
  trailers) now animates in the 3D viewer's whole-fill timeline — driving in along
  its routed path *after* every aircraft is parked — and its 2D `--render-paths`
  route is drawn in the neutral mover body colour (matching the `_draw_movers` body)
  so it reads as a ground vehicle, not an aircraft. A deferred (un-routable,
  `path=None`) mover stays at its static resting pose, like a fixed obstacle. The
  viewer reuses the same hidden→sample→parked state machine as aircraft (new pure,
  node-tested `framePoses`). Inert / byte-identical for aircraft-only layouts
  (ADR-0003). The Caddy hard-door egress lane (#652) is the remaining half of #606.

- **Ground objects render in the 2D PNG (#606).** `hangarfit check --render` /
  `solve --render-paths` now draw the floor's ground objects: the fixed obstacle
  (the Maul fuel trailer) as a hatched keep-out (distinct from the structural-notch
  hatch — an object, not absent floor), and the placed/routed movers (VW Caddy + the
  two glider trailers) as solid bodies in a neutral mover fill, deliberately outside
  the per-plane aircraft palette so a glance separates aircraft from trailer/vehicle.
  Each is labelled. Inert for aircraft-only layouts (byte-identical). The conflict
  validator now also knows about ground-object ids, so a real mover/obstacle conflict
  is not mistaken for a cross-layout mismatch. (scene/v2 + 3D-viewer rendering and the
  Caddy egress-lane decal are the follow-up half of #606.)

- **Lateral cart-strafe + free-swivel pivot tow motion (#599, ADR-0010).** The cart
  motion model gains a lateral *strafe* primitive (`Segment(kind="T")`) — a slide
  perpendicular to heading — so a broadside-parked cart-borne plane (e.g. the 18 m
  Scheibe, which can't pivot in a 15 m hangar) routes in through the door as a clean
  side-on slide, and `entry_poses` emits a broadside entry cone for broadside targets.
  Strafe is **cart-only** and gated on `mover_on_carts`; free-swivel-gear aircraft
  (`tow_pivotable`) pivot in place but don't strafe. The Herrenteich free-swivel
  aircraft (Aviat Husky, Cessna 140, Flight Design CTSL, FK9 Mk II) are modelled
  `tow_pivotable` so they pivot into their slots rather than using the catalog taxi
  turn radius. **Determinism:** RNG-free (ADR-0003 holds); cross-version byte-identity
  is intentionally re-baselined only for cart plans the more-capable motion now routes
  more cheaply (no existing fixture changed; the strafes are appended last so an
  existing pivot/straight path still wins a cost tie).

- **Separate tow-MOTION clearance, distinct from the parked clearance (#643).**
  A hangar may declare optional `motion_clearance_m` / `motion_wing_layer_clearance_m`
  — the margin the tow planner clears a *moving* mover against parked bodies, which
  reality threads far tighter than the parked spacing (a spotter watches the wingtips).
  `collisions.check` keeps the parked `clearance_m` for static validity; only the tow
  planner's per-pose checks (`path_first_conflict` and the in-search `_motion_clear`)
  use the tighter motion margin. Absent (the default) ⇒ the motion clearance IS the
  parked clearance, so plans are **byte-identical** (ADR-0003). This corrects an
  over-strict abstraction — applying the parked margin during motion — that made
  otherwise-routable dense layouts falsely un-routable.

- **Ground objects in the scene/v2 seam + 3D viewer (#606).** The interactive 3D
  viewer (`hangarfit view`) now renders the Stage-A ground objects — the fixed
  obstacle (Maul fuel trailer) as a warm-graphite keep-out volume and the placed
  movers (VW Caddy + glider trailers) as slate bodies, each visually distinct from
  the colour-coded aircraft, with a legend that names every class (and flags the
  hard-door egress Caddy). The `hangarfit.scene/v2` dict gains two always-emitted,
  inert-when-empty keys — `ground_objects` (placed bodies, each with its static
  `final_pose` affine) and `go_anchors` (their world corners for the viewer's
  load-time determinant-−1 self-check). This is the 3D companion of the 2D-PNG
  ground-object render (#649, the 2D first half of #606); same input ⇒ byte-identical scene
  (ADR-0003), and an aircraft-only layout differs only by the two empty
  collections. Mover *animation* and the Caddy egress lane are deferred follow-ups
  (the egress oracle exports no corridor geometry to draw).

- **Observation tensorizer (`ml/encoding.py`, #607).** Added a numpy-only,
  deterministic `encode(Observation, hangar, bodies, config) → ObservationTensors`
  that turns the cold-joint env's semantic `Observation` into fixed-shape tensors:
  a 7-channel world-frame raster (4 static keep-out channels: oob/bay/apron/door;
  3 dynamic occupancy channels: parked low/wing z-split + active footprint), a
  `(16, 24)` padded set-token table with status/type/dims/wing/movement/pose
  columns, and a `(9,)` legal-action mask. Schema versioned as `SCHEMA_VERSION=1`
  and documented in `docs/architecture/ml-observation-schema.md`. `meta` is a
  read-only `MappingProxyType` of floats for debugging / un-normalization. Dev-only
  (`ml/` is not in the wheel); no new runtime dependency. Sub-project #2 of the
  learned backend (epic #607).

- **Cold-joint policy network (`ml/policy.py` + `ml/action_space.py`, #607/#680).**
  Added the policy + value `torch` `nn.Module`: a CNN over the observation raster +
  masked self-attention over the object tokens → the active-object embedding → a
  legal-mask-gated `(kind,gear)` head + a `K=5` magnitude-bin head + a value head
  (movement-mode illegality hard-masked to −inf; collision stays a soft reward term).
  `ml/action_space.py` is the pure (no-torch) action contract: the factored-discrete
  bins + `decode(…, *, turn_radius_m) → Primitive | Park` in the units the env expects
  (radians for cart pivots, metres otherwise), reusing `encoding`'s canonical action
  order. Contributor-only — the new `[train]` (`torch`) extra; the network tests
  `importorskip` torch. No PPO loop / curriculum / rollouts yet. Sub-project #3 of the
  learned backend (epic #607).

- **Cold-joint PPO training core (`ml/ppo.py` + `ml/train.py`, #607/#684).** Added a
  roll-your-own (cleanrl-style) PPO that drives `HangarFitEnv` + `HangarFitPolicy`
  directly: `ml/ppo.py` (`RolloutBuffer`, GAE-λ `compute_gae`, clipped-surrogate
  `ppo_update`, the PARK-gated factored log-prob/entropy) and `ml/train.py` —
  `python -m ml.train` — which trains the policy on the fixed trivial curriculum stage
  (one object driven in from the apron and parked in a loose hangar) and logs a reward
  curve. Seedable / within-build deterministic (ADR-0027). Contributor-only (the
  `[train]` torch extra; the PPO tests `importorskip` torch). The curriculum schedule
  (#4b) and the reach-not-beat eval (#4c) are separate. Sub-project #4a of the learned
  backend (epic #607).

### Changed

- **Learned backend (#728, epic #607): the #720 L4 trust-region clipping bundle is now the
  `ml.train` default.** `--reward-clip 50` / `--value-clip-eps 0.2` / `--target-kl 0.03` — the
  values confirmed load-bearing by the two-seed #720/#722 `pair-box` gate (a controlled A/B showed
  clip-off collapses to place-nothing, clip-on masters) — graduated from opt-in flags to the
  argparse **and** `PPOConfig` dataclass defaults (kept in sync). New paired `--no-reward-clip` /
  `--no-value-clip-eps` / `--no-target-kl` off-switches restore the disabled (`None`) behavior —
  the only way to reach it, since there is no in-band "off" value (`--reward-clip 0` zeroes all
  rewards; `--target-kl 0` stops after the first epoch) — for A/B controls such as the seed-1 clip-OFF
  run. This is a deliberate training-default **re-baseline**: an unflagged run is no longer
  byte-identical to a pre-#720 run, but reproducibility (same seed → same stream) is unchanged. The
  four 4c-ii basin-escape knobs (`--r-valid-park`, `--dense-slot-potential`, entropy, `--normalize-returns`)
  remain default-neutral. `ml/README.md` recipe prose updated.

- **Herrenteich dataset realism pass (#657/#658/#659).** Tightened the real
  Airfield Herrenteich dataset to how the club actually parks (user on-site facts):
  - The **VW Caddy** is now modelled **multi-part** (#658) — a van body box
    (0→1.84 m) plus a small ~1.0×0.8 m roof-gear rack (1.84→2.04 m) — instead of one
    full-height prism. The club's Caddy carries roof-stowed gear (+0.20 m over stock);
    as a single 2.04 m box that blocked the wing layer across its whole footprint, but
    split, a wing whose underside sits at ~2.0 m may overhang the low van body
    (+0.16 m gap, clears) and only has to clear the localized rack — a realistic van
    model (low body + small roof load), not a full-height wall. (It governs any dense
    packing that nests a wing over the Caddy; inert in the shipped fishbone layout.)
  - **Two distinct glider trailers** (#657): `glider_trailer_1` → a 10.5 m Duo Discus
    (two-seat) closed trailer; `glider_trailer_2` → a 9.0×1.75×1.45 m single-seat
    15 m-class trailer (owner-measured Cobra) — previously both a generic 9.0×2.1×2.3.
  - The **Fuji FA-200-180** joins the Herrenteich `fleet.yaml` as a permanent ninth
    occupant (the only low-winger; a placeholder for a future C150; `always_own_gear`).
    Its envelope is published spec, cross-checked across sources (span 9.42 m, length
    7.98 m, wing area 14.0 m², fin to 2.59 m — correcting a transposed 2.02 m height);
    the undercarriage + tailplane spans stay estimates (`measured: false`).
  - `examples/herrenteich/layout_full.yaml` is re-authored as the **realistic
    in-hangar set** (#659), packed **fishbone** (continuous, mixed aircraft headings
    instead of an orthogonal nest — far more space-efficient and how a club really
    parks). With the rescue Caddy required to keep a clear drive-out egress
    (#603/#652), the fuel trailer hard against the left wall by the door, and both
    glider trailers inside, the hangar is one aircraft over capacity (confirmed by an
    exhaustive orthogonal-and-fishbone search), so the layout parks **seven of the
    eight aircraft + all four ground objects** (the motor-glider Scheibe Falke parks
    outside; the Caddy near the door with a clear egress, the Duo trailer on the right
    wall). Valid at the calibrated clearances and the Caddy's egress is now clear (was
    the documented egress-blocked finding). `layout.yaml` (all eight aircraft, no
    ground clutter) is unchanged — the "all eight fit" promise still lives there.

- **Herrenteich tow-motion clearance calibrated (#605/#643).** `examples/herrenteich/hangar.yaml`
  now sets `motion_clearance_m: 0.05` / `motion_wing_layer_clearance_m: 0.05` — the
  hand-cleared margin while a mover is in motion, distinct from the 0.20/0.15 *parked*
  spacing (the #646 mechanism). A `measured: false` modelling assumption like the parked
  values. With this plus the strafe (#599) and the dolly/free-swivel pivot data (#644),
  the broadside Scheibe and small dense subsets tow-route where the parked margin
  rejected them; the *full* dense all-8 remains gated on the greedy planner's routing
  search at scale (not the motion model — see #642).

- **Herrenteich Stemme modelled as dolly-pivotable for tow planning (#644).** The
  `examples/herrenteich/` fleet manifest now overrides the Stemme S10 to
  `movement_mode: always_cart` — it is hand-positioned on a dolly in the hangar,
  so it pivots in place rather than using its 10 m *taxi* turn radius (a per-fleet
  operational override, #595; flight specs stay in the catalog). Part of correcting
  the tow-motion abstraction (#643).

### Fixed

- **Learned backend (#742/#743, epic #607): the curriculum competency gate and
  `--auto-budget` now read the honest per-iteration metric, not a noisy 20-episode tail.**
  The promotion gate (`should_promote`) and the #734 auto-budget slope-fit both watched a
  per-episode `deque(maxlen=window)`; after `window.extend(ep_stats)` that retained only the
  **last ~20 episodes of the latest rollout** (out of ~250), a far noisier estimator than the
  per-iteration `valid_placed` the `--metrics-out` JSONL and `ml.gate` report. Two symptoms:
  **(#742)** a rung false-promoted `by competency` on a lucky autocorrelated 20-episode streak
  while its honest per-iteration mean was well below threshold (observed on `trio-box`: trainer
  said mastered at iter 136, `ml.gate` reported peak `valid_placed` 0.709 / `never` competent,
  still climbing); **(#743)** `--auto-budget` plateau-stopped a hard rung **during its flat
  pre-climb warmup** (`trio-box` stopped at iter 29, `valid_placed` ~0.04 — the climb only
  started ~iter 50). The fix unifies both decisions onto a single per-iteration honest series
  (`window_score` over the whole rollout, skipping no-episode iterations), so `PromotionPolicy.window`
  now counts **iterations** (default 3, was 20 episodes) and `should_promote` thresholds their
  mean — the trainer's verdict is now as trustworthy as `ml.gate`'s. A new `BudgetController`
  **floor-guard** (`min_level`, default 0.05) refuses to read a flat-at-floor warmup as a
  converged plateau (slope alone cannot tell floor-flat from ceiling-flat). New default-neutral
  CLI levers: `--promotion-window`, `--auto-budget-min-iters`, `--auto-budget-min-level`.
  **Deliberate re-baseline:** the gate now advances rungs at different iterations than the
  buggy per-episode tail did, so trained policies differ from pre-#742 runs. Run-twice
  determinism is preserved, and the `--auto-budget` flag stays default-neutral (toggling it off
  adds no controller call) — the re-baseline lives in the gate itself, not the auto-budget
  machinery. Dev/CI-only (`ml/`); no shipped-wheel surface.

- **Learned backend (#732, epic #607): PBRS now forces Φ(terminal) = 0 — removes a
  spurious −Φ(terminal) return bias.** The potential-based reward shaping added
  `γ·Φ(s′) − Φ(s)` every step but computed the terminal step's `Φ(s′)` from the live
  potential instead of forcing 0, so the undiscounted episode return picked up a
  constant `−Φ(terminal)` term — provably policy-invariant (Ng–Harada–Russell) only when
  `Φ(terminal) = 0`. `Φ` is ~0 on a *clean valid* completion but **nonzero** on exactly
  the non-clean terminals the curriculum must distinguish (budget-exhaustion stops with an
  object still unplaced; invalid/piled completions with residual overlap and/or an object
  still unplaced). `HangarFitEnv`
  now sets `Φ(s′) = 0` on both terminal paths (the terminal Park and the budget-exhaustion
  movement), so the terminal shaping reduces to `−Φ(prev)`. **Deliberate re-baseline:** this
  changes reward values on non-clean terminal episodes (clean completions are unaffected).

- **Learned backend env validity now matches `collisions.check` (#694).** The
  `HangarFitEnv` oracle no longer over-enforces the inert placeholder maintenance
  bay (the bay is only active when an aircraft is explicitly placed there; the
  `layout_valid` helper that the benchmark already uses is now the single shared
  validity gate for both the env reward and `ml/eval`).

- **`view` surfaces un-routable ground-object movers (#634).** Layout-mode
  `hangarfit view` already named an un-tow-routable *aircraft* (the static-degrade
  note), but a None-path *mover* — which `plan_fill` keeps as a best-effort static
  body rather than raising — rendered silently, unlike `solve --render-paths`
  (#612). `view` now threads `plan_fill`'s `unroutable_movers` out-param and warns
  one line per mover on stderr (the shared `_warn_unroutable_mover_ids` helper),
  closing the last `view`/`solve` surfacing parity gap. Plan-inert (byte-identical).

## [0.15.0] — 2026-06-12

### Added

- **`solve` suggests `--workers` on idle-core multi-restart runs (#628).** When a
  parallel-eligible solve (`--max-restarts` + spread) is left at the default
  `--workers 1` on a multi-core box, `hangarfit solve` now prints a one-line
  stderr hint naming the flag (with a capped example, e.g. `--workers 8`). Stderr
  only — stdout / `--json` / `--write-yaml` stay untouched — and it never fires in
  a regime where `--workers` would silently run serial (no `--max-restarts`,
  `--no-spread`, `--spread-stall-restarts` set, or a single core), so the default
  stays byte-identical. The `--workers` help text now states exactly when the flag
  is effective.

- **Glider-trailer placement + soft region preference (#604).** The solver now places and routes the glider trailers, with a soft right/left-region preference biasing them toward a chosen hangar wall; surfaced as per-layout `region_alignment` in `solve` output.
- **Ground-object data model (#601).** Catalog `fixed_obstacle`/`car`/`trailer`
  types and a layout `ground_objects:` block; fixed obstacles are keep-outs
  (a `ground_obstacle` conflict names the overlapping aircraft/mover) and
  movers join collision/tow enumeration. Empty-set output is byte-identical.
  (ADR-0025)
- **Herrenteich full real set + ground-object catalog (#605).** The real hangar's
  four non-aircraft occupants — a VW Caddy, two glider trailers, and a fixed
  "Maul" fuel trailer — now have `data/catalog/` entries, and a new
  `examples/herrenteich/layout_full.yaml` parks the full real set (8 aircraft +
  those four) in one arrangement that passes `hangarfit check`. `collisions.check`
  now bounds/notch-checks ground objects (previously aircraft-only). The
  Herrenteich clearances were calibrated (`clearance_m` 0.3→0.20,
  `wing_layer_clearance_m` 0.2→0.15) so the full set is feasible — the placeholder
  values were too loose to model real club packing density. Tow-routing of the
  full set, the hard Caddy nearest-door egress rule, and rendering of ground
  objects are deferred (#602/#603/#606).
- Optional polygon part footprints: a `Part` may carry a load-time-canonicalized
  `local_vertices` polygon (authored via a parametrized `planform: {root_chord_m,
  tip_chord_m}` wing block), used by the collision build-path while `length_m`/
  `width_m` stay the bounding box. Scalar fleets are byte-identical; the 3D viewer
  still renders boxes until the scene/v2 work. (#548, ADR-0024)
- 3D viewer renders polygon part footprints as extruded prisms (`scene/v2`): each
  plane box now carries an explicit `z_band` and an optional plane-local `vertices`
  ring, and the viewer extrudes polygon parts (e.g. a tapered glider wing) instead
  of drawing their bounding box. Scalar (rectangle) parts render byte-identically
  to v1. The det-−1 anchor self-check generalizes from 4 corners to N via the
  shared `geometry.part_local_ring` helper. (#549, ADR-0017)
- First shipped aircraft taper: the real Herrenteich **Scheibe SF-25E wing** is
  now authored as a symmetric double-taper `planform` (root = the existing
  1.01 m mean chord, tip = 0.45 × root). Its tapered wingtip nests where the
  bounding rectangle would falsely conflict — a value-proof regression reproduces
  the spike's flip-window order (~0.2 m wide) of rect-rejects / taper-accepts on the
  shipped parametrization. Every other shipped part (including the folded Stemme wing —
  folding is not a taper) stays a rectangle; the herrenteich layout stays valid
  with no golden re-pin (the polygon is a strict subset of its bbox). (#593, ADR-0024)

### Changed

- **Pose cache extended to ground-object movers (#626).** The #453 per-solve
  geometry memo now serves any placeable body — a `GroundObject` car/trailer as
  well as an aircraft — so a static mover obstacle's world parts are no longer
  rebuilt on every collision/clearance check (the #453 churn movers bypassed,
  which drove the #604 mover-routing congestion). `plan_fill` now also runs
  inside a pose-cache scope, so a *standalone* fill memoizes its obstacle field
  across the whole search (previously only an in-`solve` fill did). Output is
  **byte-identical** (ADR-0003: the cache returns the same immutable `WorldPart`
  list, exact-float keyed); the speed-up is routing-only — on the measured #604
  right-region two-trailer demo the standalone fill dropped ~1.8× and an
  aircraft-only fill ~2×. (#626)

- **Local test ergonomics: two-pass `make test` + host-relative perf canary
  (#624, #625).** A root `Makefile` mirrors CI's #492 two-pass test split for
  local dev (`make test` = a parallel bulk pass + a separate serial pass for the
  wall-clock determinism canaries; ~588 s → ~169 s, 3.5× on a 32-core box), with
  `make test-fast` / `lint` / `typecheck` / `format` / `check` rounding out the
  CI-parity targets. The `@slow` `plan_fill` perf canary
  (`tests/test_towplanner_perf.py`) is now **host-relative**: it calibrates its
  wall-clock ceiling off a per-run warm-up probe (floored at the original 400 s)
  rather than an absolute bound, so a slower box (e.g. WSL2) no longer
  false-fails on byte-identical, expansion-bound work. Dev/CI tooling only — no
  runtime, solver, or determinism impact. (#624, #625)

- **Per-object catalog data model (#595).** Fleet data is now a per-object
  **catalog** (`data/catalog/`, one file per aircraft carrying a `type:`
  discriminator) referenced **by path** from thin fleet manifests; inline
  aircraft definitions in fleet files are no longer supported (an inline mapping
  raises a migration hint). A manifest entry may override a per-fleet operational
  flag (`movement_mode`, `tow_pivotable`) on top of the shared static definition;
  geometry stays static and is never override-able. The `type:` discriminator
  reserves a clean home for non-aircraft physical objects (a future builder);
  an unregistered type is rejected with a clear error today. (#595)

### Fixed

- **Unroutable ground-object movers are surfaced, not silently dropped (#627,
  #612).** A best-effort mover the tow planner can't route keeps a `Move(path=None)`
  (ADR-0007 #197) — but, unlike an un-tow-routable *aircraft* (which is named on
  stderr / in `diagnostics.unroutable_planes`), it used to be silent and just
  rendered as a static body. `plan_fill` now threads the unroutable-mover ids out
  via an observational out-param (the `apron_dropped_out` idiom); `solve` collects
  them into `diagnostics.unroutable_movers` (additive `--json` field, no schema
  bump); and `hangarfit solve --render-paths` names each on stderr. **Byte-identical**
  (ADR-0003: the plan is unchanged) — this closes the deferred half of #602's "no
  silent skip" acceptance. (The related #604 mover-routing *congestion* under #627
  was separately cut ~1.8× by the #626 pose-cache extension; the residual is a
  genuinely un-routable layout being correctly disproven.)

- **Synthetic-vs-real Scheibe SF-25E divergence (#594).** The demo
  (`data/fleet.yaml`) and `examples/herrenteich/` now reference a single central
  catalog (`data/catalog/`), so each **shared** aircraft is defined exactly once
  with the real published-spec numbers — no per-world duplication. (`fuji` and
  `cessna_150`, not based at Herrenteich, stay synthetic placeholders.) (#594, via #595)

## [0.14.0] — 2026-06-10

### Added

- **`solve --spread-stall-restarts N` opt-in flag (#546).** Exposes the F7
  (#404) spread-stall early-exit — the spread post-pass stops after `N`
  restarts with no further inter-plane-gap improvement — through a new
  `hangarfit solve --spread-stall-restarts N` flag. **Opt-in, default off**, so
  every existing solve stays byte-identical; reproducibility remains
  `max_restarts`-scoped (the default-on flip is deferred while it is reconciled
  with the #544 parallel-restart path). Narrows the perceived-latency tail on
  easy interactive solves.

- **Parallel restarts (`solve --workers N`, #544, ADR-0003 amendment).** The
  RR-MC restart loop can now fan across worker processes — a measured **4.5× at
  8 workers** on the binding roomy-three spread-on regime (spike #540).
  `hangarfit solve` gains `--workers N` (default `1` = serial, today's behaviour)
  and `--max-restarts N` (cap the search at a fixed, cross-machine-reproducible
  restart count instead of the wall-clock `--budget`). Parallel restarts are
  **byte-identical to serial** in the `--max-restarts` + spread regime; for any
  other config `--workers` transparently runs serial and prints a note (never a
  silent fallback). Determinism is **preserved, not dropped**: as of #544 each
  restart is seeded by its index, so output is a pure function of
  `(scenario, seed)` *independent of worker count* — a one-time re-base of the
  goldens (the determinism contract's deliberate-algorithm-change clause), not a
  reproducibility loss. The speedup is sub-linear and placement-only (routing is
  RNG-free and post-merge), so it helps most on roomy spread-on fills with many
  restarts. `Scenario` (#545) and `Layout` (this change) are now picklable —
  via a shared proxy-aware helper — to cross the worker boundary.

### Changed

### Fixed

- **Maintenance-bay edge-crossing intrusion (#551, ADR-0018).** The bay-intrusion
  check now **also** consults a polygon-vs-bay intersection test — additively,
  only when no vertex lies inside the bay — on top of the existing per-vertex
  containment gate, so a thin part whose *edge* crosses the closed maintenance
  bay with no vertex inside is correctly flagged (the thin-edge blind spot
  ADR-0018 already closed for the hangar floor via `floor.covers`). Because the
  per-vertex test stays the primary gate, every existing verdict is
  **byte-identical** for today's rectangular parts; it hardens the checker ahead
  of slender/concave polygon parts (#548).

- **Strict top-level unknown-key allowlist for `hangar.yaml` / scenario /
  layout files (#516).** The loader now rejects an unrecognised **top-level**
  key in these files with an attributed `LoaderError` instead of silently
  dropping it to its default — extending the #513 fleet-entry allowlist (the same
  silent-failure class) to the top-level blocks. The motivating trap: a typo'd
  `apron_depth_m` (e.g. `apron_dpeth_m:`) previously fell back to depth 0
  silently; it is now a loud error. A well-formed file is unaffected.

- **`solve --max-restarts 0` / `--spread-stall-restarts 0` clean exit (#546).**
  A bad restart-budget knob (both must be `>= 1` when set) now reports a clean
  exit-2 input error instead of an uncaught `ValueError` traceback — the same
  contract as a `LoaderError` on malformed input.

## [0.13.0] — 2026-06-09

### Added

- **L-shaped hangar / structural-notch support (#528/#529/#530, epic #527,
  ADR-0018).** A hangar may now declare an optional `structural_notches:` list of
  always-on rectangular floor keep-outs in `hangar.yaml`, modelling a
  non-rectangular footprint (the real Airfield Herrenteich back-right office notch
  — `x ∈ [12.72, 15.08]`, `y ∈ [22.66, 31.76]` — is now recorded as data instead
  of avoided by hand). End to end: (1) **static containment (#528)** —
  `collisions.check` derives a Shapely floor polygon (bounding rectangle − notches)
  and rejects any part that parks in *or overhangs* a notch, reported as a distinct
  `structural_notch` conflict (escaping the outer wall stays `hangar_bounds`);
  (2) **tow keep-out (#529)** — the tow planner honours the notch for the plane
  *in transit* (polygon-overlap pose rejection + grid-heuristic cells blocked so a
  route bends around the dead pocket; a tow ending in the notch surfaces as a
  `structural_notch` conflict on the mover), treating the notch as a separate
  keep-out so the #411/#412 `y < 0` door/apron protrusion exemption is preserved;
  (3) **3D viewer (#530)** — `hangarfit view` renders the footprint as a true floor
  cutout (`ShapeGeometry`) plus interior walls, and `scene/v1` gains an
  always-emitted `structural_notches` array on the hangar block (empty for a
  rectangular hangar, documented in `scene-v1-schema.md`). The 2D PNG draws each
  notch as a cross-hatched keep-out overlay, and the same `covers` containment
  closes a latent vertex-only edge-crossing bug. **Inert and byte-identical when no
  notch is configured** (ADR-0003): the fast per-vertex bounds path and the
  original rectangular floor/render are retained for every synthetic `data/`
  hangar, test fixture, determinism canary, and the bench — only a notched hangar
  pays the `covers` cost.
- **Theme-aware README hero (#514).** The README banner now serves two
  brand-tuned SVG variants via an HTML `<picture>` element with
  `prefers-color-scheme` media queries — `docs/assets/banner-light.svg` (light
  theme / safe fallback) and `docs/assets/banner-dark.svg` (dark theme) — so
  GitHub picks the on-brand variant for each viewer's color scheme. Same
  composition, theme-appropriate BRAND.md tokens (recolour, not redesign); the
  original `docs/assets/banner.svg` is retained. Pure docs, no code impact.
- **Nose-out parked heading preference (#263, ADR-0022).** The solver now prefers
  to park each plane pointing **out** (nose toward the door) for an easy
  straight-out exit: an RNG-free `_nose_out` post-pass flips a plane's parked
  heading 180° toward the door when that stays collision-valid (soft — never
  overrides fit, never moves a plane, never un-parks one). **Default ON**;
  `--no-nose-out` to disable, or a per-plane `constraints.<id>.nose_out: false`
  for the nose-in exemption (e.g. a low-wing under a high-wing tail). Byte-identical
  determinism is preserved **even with the feature on** (the post-pass draws no
  RNG). Builds on #480, which makes a nose-out slot cheap to back into. Adds the
  per-layout `diagnostics.nose_out_flips` count (surfaced in `--json`).
- **`tow_pivotable` aircraft flag (#263, ADR-0022).** A per-plane flag marking a
  free-castering / nose-lift plane that pivots in place when **towed**
  (`effective_turn_radius_m() → 0`, routed via the existing zero-radius cart-pivot
  fan — no new motion primitive). Set for `aviat_husky`, `ctsl`, `fk9_mkii`. A
  realism flag (these types genuinely pivot when towed), orthogonal to
  `movement_mode`.
- **Tow paths on the 3D viewer floor (#505).** `hangarfit view` now draws each
  placed plane's full tow route as a coloured line on the hangar floor (`z ≈ 0`),
  one colour per plane — the 3D analogue of the 2D `solve --render-paths` overlay
  (#192/#193). Each line uses the plane's own viewer hue (`PLANES_DARK`, the same
  swatch as its boxes, nose cone, and legend entry; conflicted planes use the
  conflict ink), so the apron slide-in, in-hangar maneuvering, and tow order are
  legible at a glance — and path quality (e.g. a forward-then-reverse cusp) that a
  bare animation hides becomes visible. The apron lead-in is drawn verbatim: with
  `--apron-depth > 0` the line extends to `ty < 0` outside the door, and at depth 0
  it starts at the door (`y = 0`); a static / un-routed scene draws no line. A new
  `paths` HUD checkbox (next to `walls` / `labels`), **default ON**, shows or hides
  the routes. The route is derived from the existing `timeline.segments[].samples`
  affines with **no `scene/v1` schema change** (the ADR-0017 seam stays stable).
- **Too-shallow-apron observability warning (#503, ADR-0021).** With a staging
  apron (`apron_depth_m > 0`), a plane only slides in if the apron is deep enough
  for *its* footprint at an apron start pose; a plane too deep to fit was silently
  routed via the `y = 0` door line (no slide-in) with zero signal. The tow path now
  emits a deterministic, deduped **stderr** warning naming each such plane and a
  suggested minimum depth (its fore-aft footprint extent — a conservative
  sufficient bound, not the `auto` over-margin), on `solve --render-paths` and
  `view --animate`; `solve --json` additionally carries an additive
  `apron_shallow_drops` list (no schema bump). Emission lives at the CLI boundary
  keyed on the *returned* result and deduped per plane, so a discarded
  spread-fallback pass never warns and `--alternatives N` warns each plane once.
  **Output-only — the `MovesPlan` is byte-identical** (ADR-0003); raise
  `--apron-depth` past the warned value (or prefer `auto`) to engage the apron.
  Auto-deepening the apron is deferred (#503 Option 2).

### Changed

- **BREAKING (collision model): the empennage is now modelled as explicit tail
  surfaces (#518/#519/#520, ADR-0023).** Every aircraft gains a `tail` (horizontal
  stabilizer — wide, ~2.5–3.5 m span) and a new `vertical_stabilizer` `PartKind`
  (the fin + rudder — thin, on the centreline, rising to the published overall
  height *into* the wing-nesting layer). The checker now rejects two cases it
  silently passed before: a wing nested over a neighbour's tail that passes over
  that plane's **fin** (#520 — the fin reaches into the wing layer), and a
  wing/strut/fuselage clipping a realistic-width **tailplane** (#519). The
  collision *predicate is unchanged* — honest z-extents alone produce the correct
  verdict; a wing-over-tail nest stays legal exactly when it clears the centreline
  fin laterally. Per-part z expresses conventional / cruciform / T-tail
  configurations (the Stemme S10 is the fleet's one T-tail) with no per-type code.
  Some previously-"valid" layouts flip to invalid: the canonical
  `valid_wing_over_tail` fixtures were re-tuned to nest over the low tailplane
  while clearing the fin (with a new paired `invalid_wing_over_fin`), the real
  `examples/herrenteich/layout.yaml` all-eight arrangement was re-arranged to
  clear every fin, and the packed 9-plane fill is now statically valid but no
  longer tow-routable (wide tailplanes block the corridors). The placeholder
  `data/hangar.yaml` was widened 18 → 22 m so the canonical demo keeps its full
  plane set with the bulkier tail surfaces.
- **Fewest-moves tow routing — nose-out slots are backed in (#480, ADR-0010
  amendment).** The tow planner now minimises **moves** (direction changes), not
  reverse distance: word/path cost is `length + CUSP_PENALTY × cusps` (a *cusp* is
  a forward↔reverse change), replacing the old `_REVERSE_COST_FACTOR = 1.5`
  reverse-length penalty; forward motion is now preferred only as the
  deterministic tie-break. The door entry cone emits its rear-entry (nose-out)
  headings whenever the *target* parked heading is nose-out — independent of the
  staging apron — and a cost-aware start-seed analytic expansion returns the
  cheapest collision-clean approach, so a nose-out slot is **backed in** (in-hangar
  reorientation drops from ~162° to a near-straight slide-in) instead of
  pirouetting in the back corner. Determinism (ADR-0003) is preserved; this
  re-baselines the depth-0 tow grid for **nose-out** targets only, superseding the
  #412 depth-0 cross-version byte-identity for that case (the same-input contract
  is unchanged). Obstructed nose-out approaches that need mid-search maneuvering
  remain best-effort.
- **Herrenteich fleet refreshed to TCDS / 3-view-sourced dimensions + a working
  demo scenario (#536, refs ADR-0023 / ADR-0018).** The eight real-data occupants
  in `examples/herrenteich/fleet.yaml` move from estimated part dimensions to
  figures **sourced** from EASA/FAA TCDS + manufacturer manuals where published
  (wing chord, fuselage/cabin width, horizontal-stabilizer span, gear track +
  wheelbase; per-field provenance recorded inline). Two configurations are
  corrected against primary sources: the **Stemme S10 → taildragger** (twin
  retractable mains + tailwheel; EASA TCDS A.054), and the **CTSL tail →
  conventional-low** all-moving stabilator (was the secondary-source "cruciform"
  label; geometry unchanged). The Scheibe SF-25E's real **low** wing stays modelled
  **high** as the deliberate monowheel-tilt abstraction (a flat 18 m low wing is
  unclearable for any all-eight arrangement — search-verified across 40+ seeds;
  dimensions are real, only the z-layer is the modelling choice). The hand-built
  all-eight `examples/herrenteich/layout.yaml` stays **valid** (0 conflicts) under
  the refreshed dimensions, and a new **`examples/herrenteich/scenario_demo.yaml`**
  — a 3-aircraft subset — **solves and fully tow-routes** end-to-end
  (`solve --render-paths`, spread-off fallback ADR-0016) around the office notch,
  with the commands shown in the dataset README. Part fore-aft stations, most tail
  chords, all fin chords, and strut attach points remain honestly derived /
  estimated (unpublished for these light types); `measured: false` is retained
  (sourced, not on-site surveyed). Real-data only — `data/fleet.yaml` stays the
  synthetic placeholder and no `src/` behaviour changes.

### Fixed

- **Strict unknown-key allowlist on fleet aircraft entries (#513).** A misspelled
  field key in an `aircraft:` entry — e.g. `tow_pivot:` / `towpivotable:` /
  `tow-pivotable:` for the new `tow_pivotable` flag, or `turn_radius:` for
  `turn_radius_m:` — used to be **silently dropped to its default**, denying the
  capability the author tried to grant. `_build_aircraft` now validates each entry
  against a strict key allowlist *before any field is read* and raises an
  attributed `LoaderError` (`aircraft '<id>': unknown aircraft key(s) …`), so a
  typo of a *required* key surfaces as the offending key rather than a downstream
  "missing field". The nested `struts:` block gets the same guard, catching a
  misspelled near-duplicate alongside a correct key. Mirrors the existing strict
  `wheels:` and constraint-key allowlists.

## [0.12.0] — 2026-06-07

### Added

- **Tow-planner staging apron (#412, ADR-0021).** New optional `Hangar`
  scalar `apron_depth_m` (in `hangar.yaml`; default `0`) models a bounded
  staging apron in the `y ∈ [−apron_depth_m, 0)` region in front of the door.
  When set, the tow planner routes each plane **apron → door → slot** so the
  path begins *outside* the hangar and slides in through the door — including in
  the 3D viewer animation, with no `scene/v1` change (the first timeline sample
  simply sits at `ty < 0`). The depth may be authored as a number or the keyword
  `auto` (fleet-derived ≈ `max(plane length) + max(turn radius)`), and overridden
  per run with `--apron-depth N|auto` on both `solve` and `view`. The apron-pose
  grid adds rear-entry (nose-out) seed headings so a plane can back in tail-first,
  making nose-out parking *routable* (unblocks #263 without deciding it). The
  static `collisions.check` oracle is **untouched** (it still forbids `y < 0`),
  and the #411 jamb rejection is retained verbatim for footprints crossing the
  front wall. **`apron_depth_m = 0` / absent reproduces the pre-apron tow plan
  byte-for-byte** (ADR-0003); the apron logic lives entirely behind an
  `apron_depth_m > 0` gate.

### Changed

- **Incremental single-plane gap cache in the spread post-pass (#455).** The
  ADR-0008 spread hill-climb perturbs one plane per iteration and scores several
  candidate positions for it; the repulsion energy (`_inter_plane_energy`) now
  memoizes the expensive shapely edge-to-edge distance for the plane pairs that
  do *not* involve the moved plane (their gap is invariant across those
  candidates) and recomputes only the moved plane's pairs — an O(n²)→O(n)
  reduction in pairwise distances per candidate. The energy is still summed over
  all pairs in canonical order, so the result is **byte-for-byte identical** to
  before (ADR-0003): verified by diffing solve output against the prior `develop`
  across the two spread-active fixtures (3- and 6-plane) over 5 seeds each, the
  determinism canaries, and the bench run-twice check. It is a distance memo,
  never the bit-divergent delta-update. Measured `roomy_three_spread_on`
  placement 15.04 s → 14.08 s median (~6 %) at n = 3 (baseline itself down from
  the spike's 40.6 s after #453/#454); the saving grows with fleet size.

- **Consolidated example artifacts under a top-level `examples/` umbrella
  (#448).** The root `layouts/` (hand-authored demo layouts) and `herrenteich/`
  (the real DWG-measured Airfield Herrenteich dataset) directories moved to
  `examples/layouts/` and `examples/herrenteich/`, with a new `examples/README.md`
  index that restates the real-vs-synthetic distinction. The demo layouts' embedded
  `fleet:`/`hangar:` refs were re-pointed (`../data/…` → `../../data/…`); the
  synthetic `data/` placeholders are unchanged and stay at the root. No shipped
  artifact changes — neither directory was ever included in the wheel or sdist.

### Fixed

## [0.11.0] — 2026-06-06

### Added

- **Soft per-plane `priority` weight in `constraints:` (#441).** A new
  non-negative `priority` (float, `None` ≡ neutral) on `PlaneConstraint` lets a
  scenario nudge the ADR-0008 spread post-pass to give a more important plane
  more clearance: each plane-pair's repulsion energy is scaled by
  `(1 + priority_i)·(1 + priority_j)`, while the maximin basin selection still
  ranks on the raw geometric gap. It is the first *user-supplied soft*
  preference (pins and `force_on_carts` stay the only HARD constraints); the
  loader rejects negative, non-finite, or `bool` values. Determinism-safe and
  inert by default — with every `priority` unset every weight is exactly `1.0`,
  so the energy and the whole search stay byte-identical to before (ADR-0003).
- **Opt-in spread-stagnation early-exit for `solve()` (#404 / F7).** Two new
  `SearchConfig` fields — `spread_stall_restarts: int | None` (default `None`)
  and `spread_stall_epsilon_m: float` (default `0.05` m) — let a spread-ON solve
  stop the restart loop once N consecutive restarts fail to improve the selected
  set's maximin plan-view gap by epsilon, instead of always running the full
  budget. The counter arms only after a complete (`≥ alternatives`) selection
  exists, so hard scenarios still get the full budget to find their first answer.
  Default (`None`) preserves today's run-to-budget behaviour byte-for-byte (the
  determinism canaries are untouched); when enabled, the stop depends only on the
  seed-fixed restart sequence + an integer counter (never wall-clock), so the
  result is identical per-seed across machines — *narrowing* the #267 timing
  scope rather than widening it. Calibrated from the F6 benchmark
  (`bench.profile_pipeline`): `spread_stall_restarts=5` cuts the canonical
  `roomy_three_spread_on` regime from 30 restarts to 7 (~4×) while keeping 96 %
  of the achievable separation. New advisory
  `SolverDiagnostics.spread_stall_applied` reports when the early-exit fired. See
  ADR-0008 / ADR-0003 (2026-06-06 amendments).
- **Real Airfield Herrenteich dataset (`herrenteich/`, refs #79).** A
  self-contained real-world dataset kept separate from the synthetic `data/`
  placeholders: the DWG-measured hangar (15.08 m × 31.76 m, 13.46 m door), the
  eight aircraft usually hangared there (published-spec dimensions,
  second-source verified; adds a folded **Stemme S10** and a confirmed 18 m
  Scheibe SF-25E; drops Fuji/Cessna 150), and a valid all-eight `layout.yaml`
  (`hangarfit check` → exit 0) with a regression test. Surfaced two follow-ups:
  the L-shaped hangar's office **notch** is not yet modelled (spike #424, the
  files keep clear of it by hand), and the solver's bounding-box
  trivial-infeasibility gate then false-rejected this glider fleet (#425, fixed
  below) — the layout was found by driving the real part-collision checker
  directly. The default `data/` demo data is unchanged.
- **Brand source of truth in-repo (#414).** `docs/assets/BRAND.md` captures the
  hangarfit brand (DocGerdSoft lineage + the 2D tokens + the 3D dark-surface
  section + the full token table), so the viewer's colours, banners, and
  typography trace to one document.
- **Profile-first benchmarking — harness + always-on CI gate (#381, #403 / F6).**
  A committed dev/CI-only `bench/` harness (`python -m bench.profile_pipeline`)
  splits each regime's wall-clock into placement vs routing across trivial /
  roomy-multi / tight-placeholder × spread on/off regimes, binding on
  `max_restarts` (not wall-clock) so the numbers reproduce run-to-run; it lives
  at the repo root outside `where=["src"]`, so `pip install`, the wheel build,
  and pytest never touch it. Its headline finding
  (`docs/spikes/solve-tow-profiling.md`) overturns the prior premise that
  routing dominates: on the default spread-ON path placement is ~53× routing,
  almost all of it the spread post-pass rebuilding part geometry on every
  `collisions.check` — directly seeding the #453/#454 speedups below. F6 (#403)
  then promotes the harness's correctness, path-validity, determinism, and speed
  invariants into a dedicated `bench-gates.yml` that fails every `develop`/`main`
  PR on a regression (the speed ceiling a generous catastrophic-regression
  tripwire pinned to `ubuntu-24.04`, not a microbenchmark).

### Changed

- **Spread-off tow fallback promoted into the library `solve()` (#402 / F5).**
  The ADR-0016/#280 spread-vs-towability rescue used to live in `cli.py`, so any
  non-CLI caller of `solve(plan_paths=True)` bypassed it and could get a
  spread-maximized layout that was routable from the CLI but un-routable from the
  library. `solve()` now resolves the seed and `SearchConfig` once above both
  passes and, when spread stayed on and every returned layout came back
  un-routable, re-solves once with `spread=False` (inheriting the caller's
  `max_restarts`, so still deterministic, not wall-clock-bound). The swap is
  recorded on a new always-present `SolverDiagnostics.spread_fallback_applied`
  (default `False`, no schema bump); the CLI drops its own fallback and just
  surfaces the flag on stderr and in `--json` / `--write-yaml`. The re-selection
  is RNG-free and the `(0, 0.0)` validity gate is untouched, so the
  byte-identical determinism contract holds (ADR-0003).
- **Faster placement search — geometry memoization + a collision broad-phase
  (#453, #454).** The #381 spike found placement dominates the pipeline (~53×
  routing), bottlenecked on `aircraft_parts_world` rebuilding Shapely polygons on
  every collision/clearance check. #453 adds a `ContextVar`-scoped per-`solve()`
  cache keyed on `(plane_id, x, y, heading)` consulted at the hot call sites,
  taking the canonical `roomy_three_spread_on` placement from 42.3 s to ~18.7 s
  (~2.3×). #454 then adds a per-axis AABB broad-phase in
  `collisions._pairwise_conflicts` that skips the exact Shapely predicate for
  part-pairs whose bounding boxes are more than `clearance_m` apart — a provable
  lower bound on true edge-to-edge distance, so no conflicting pair is ever
  skipped — taking it a further 18.7 s → 15.7 s (−15.8 %). Both are pure-speed
  levers verified byte-identical against `develop` at fixed `max_restarts` across
  seeds; the conflict set, penetration accumulation order, and the determinism
  contract are unchanged (ADR-0003).
- **3D viewer renders on the DocGerdSoft dark-surface brand (#415).** `hangarfit
  view` now uses the dark-lifted fleet palette (`PLANES_DARK`, keyed by the same
  sorted id so 2D/3D plane identity is preserved), a unified scene shell
  (floor/grid/walls on the STATUS `wall` ink), a `maint`-violet maintenance bay
  (retiring the viewer's off-system red), an accent fill light, branded HUD chrome (dark
  neutrals, accent focus ring, amber honesty banner, Geist/mono typography), and a
  non-colour `⚠ conflict` label cue (the 3D analogue of the 2D hatch — "never hue
  alone"). Render-only: the `scene/v1` contract, the Python-owned determinant-−1
  transform, `build_scene` byte-determinism, and the collision model are unchanged.
- **Brand tokens centralized into one source (`src/hangarfit/brand.py`, #419).**
  Every brand colour, opacity, darken factor and font stack is now *defined once*
  in `brand.py` and *referenced* by all four render surfaces: `visualize.py` (2D)
  re-exports the names it always exposed, `scene.py` reads `PLANES_DARK` from
  `brand`, `viewer.py` builds its `_CSS` from brand tokens, and `viewer.js` reads
  its colours from a new canonical `BRAND` JSON blob injected into the HTML
  (separate from the scene blob — the `scene/v1` schema is unchanged) instead of
  hard-coded `0x` literals. Render-only and determinism-neutral: the emitted HTML
  is byte-identical across re-renders of a given scene, the CVD-safe palette
  (#326) values are unchanged, and the
  collision model / determinant-−1 transform are untouched (ADR-0019).
- **2D maintenance-bay and placeholder banner aligned to the brand tokens
  (#418).** Building on #419's centralization, the matplotlib 2D PNG drops two
  off-system reds the 3D surface had already resolved: the closed maintenance-bay
  fill now reads the `maint` violet the 3D bay uses (with an ink-dark edge/label
  — the lighter violet needs dark ink for contrast), and the "PLACEHOLDER DATA"
  honesty banner now uses the single-source `WARNING` amber, matching the 3D
  banner for cross-surface parity. Render-only with no collision, determinism, or
  `scene/v1` impact; the 3D banner value is unchanged, so the viewer HTML stays
  byte-identical.
- **Viewer ported to a typed, modular, dev/CI-only TypeScript toolchain (#436).**
  The single hand-written `_viewer_assets/viewer.js` is now built by an esbuild +
  `tsc` + eslint toolchain (top-level `viewer/`, ADR-0020) from typed modules
  under `viewer/src/*.ts`; Node is a dev/CI concern only — `pip install`, the
  wheel build, and pytest never invoke npm, and the wheel still ships the one
  committed `viewer.js` bundle. The migration scaffolded the toolchain (#437),
  atomically ported the renderer (#439), and added typed `scene-contract.ts` /
  `brand-contract.ts` mirrors with Python key-set parity tests plus node-native
  unit tests for the pure `affine` / `anchors` / `timeline` units (#440).
  Equivalence is semantic, not byte-for-byte: the headless render is
  pixel-identical (same screenshot hash) on a static and an animated fixture.
  Render-only and determinism-neutral — the `scene/v1` schema is unchanged,
  `scene.py` / `collisions.py` are untouched, Python still owns the
  determinant-−1 transform, and a `viewer-build-drift` CI guard byte-pins the
  committed bundle.

### Fixed

- **Tow entry respects the door-jamb clearance instead of clipping the wall
  (#411).** The #222 front-gap exemption dropped the entire front wall for a
  mover in transit, so a plane straddling `y < 0` *outside* the door opening (an
  off-centre or too-wide entry) clipped the solid wall/jamb with no rejection —
  visible in the 3D viewer as a wing through the wall at tow `t=0`. The exemption
  is now door-aware in the shared motion oracle: a vertex at `y < 0` is legal
  only when `door_left ≤ x ≤ door_right`, otherwise it is a `hangar_bounds`
  conflict. The door becomes a true motion gate for the whole tow, so off-centre
  entries that would clip are filtered (the planner self-selects a centred/angled
  entry) and a plane wider than the door at every orientation is reported
  un-towable (best-effort `plans[i]=None`) rather than drawn clipping. RNG-free
  and closed-form, so the ADR-0003 planner determinism contract holds.
- **Solver no longer false-rejects glider fleets (#425).** The pre-search
  trivial-infeasibility gate (`solve` check #2) summed each plane's *bounding
  box* (`fuselage_length × wingspan`), which for a thin-winged glider is mostly
  empty air — so an 18 m-span Scheibe Falke could push Σ bbox over the hangar
  floor and the solver would return `trivially_infeasible` without ever
  searching, even when a valid nested layout existed. The gate now sums each
  plane's actual **part-footprint rectangles** (a much tighter estimate), so
  glider-containing fleets reach the search; only genuinely-too-big fleets still
  short-circuit. RNG-free and pre-search, so the byte-identical determinism
  contract (ADR-0003) is unchanged.

### Security

- **Bumped pip 26.1.1 → 26.1.2 for PYSEC-2026-196 / CVE-2026-8643 (#460).** The
  `requirements-pip-tools.txt` bootstrap lockfile pinned `pip==26.1.1` (an
  `--allow-unsafe` transitive of pip-tools), which Scorecard code-scanning
  flagged as vulnerable (all pip < 26.1.2). The lockfile was regenerated with the
  canonical command plus `--upgrade-package pip`, bumping pip only to 26.1.2 with
  fresh hashes — a byte-stable diff under the drift guard, so the lockfile-drift
  CI jobs pass.
- **CI supply-chain coverage extended to the viewer TypeScript toolchain (#461,
  #462, #463).** The dev/CI-only `viewer/` codebase gained the monitoring the
  Python tree already had: CodeQL became a per-language matrix adding a
  `javascript-typescript` analysis scoped to `viewer/src` (vendored Three.js
  excluded), preserving the existing required `Analyze (Python)` check (#461);
  Dependabot got a weekly `npm` ecosystem entry for `/viewer`, with
  `three` / `@types/three` ignored because they are pinned in lockstep with
  vendored r160 (#462); and a PR-time `dependency-review` gate (fail-on high,
  covering pip + npm) plus `ruff` over the dev-only `bench/` harness landed
  (#463). All CI / supply-chain only — no runtime, collision, or determinism
  impact; the actions stay SHA-pinned.

## [0.10.0] — 2026-06-04

### Added

- **3D viewer renders landing gear + tow carts (#399).** `scene/v1` now emits
  per-plane `wheels[]` (canonical plane-local positions, ADR-0013) and an
  `on_carts` flag, plus a `gear_anchors` oracle. The viewer draws a wheel at each
  wheel point (+ a short leg up to the belly where it clears) — and a pallet deck
  under each wheel for carted planes — all parented to the existing per-plane
  affine Group, so the gear
  inherits the determinant-−1 transform and animates along the tow path for free.
  The load-time anchor self-check now also oracles gear world positions (the only
  cross-language backstop, since `viewer.js` is not pytest-covered). Wheels/carts
  are render-only and never enter the collision model (ADR-0015); `build_scene`
  stays byte-deterministic and `collisions.py` is untouched.
- **3D viewer polish — shadows, materials, labels, nose arrows (#400).** The
  viewer now casts soft contact shadows (a `PCFSoftShadowMap` key sun + ortho
  frustum sized to the hangar, a soft fill, softened ambient) so vertical
  clearance is legible — a high wing's shadow across a neighbour's tail is the
  viewer's reason to exist (ADR-0017). Materials are kind-based (translucent wings,
  thin metallic struts, a darker cockpit tint echoing the 2D render's cockpit
  shading). Each plane gets
  a billboarded id label (a `CanvasTexture` sprite drawn with safe `fillText`,
  never `innerHTML`) and a nose-cone arrow at its `+x` tip, both behind a new
  `labels` HUD toggle. All client-side with the already-vendored Three.js r160 —
  still a single self-contained offline HTML, no new assets, no determinism or
  collision risk.
- **Honesty banner + actionable readouts (#401).** A persistent "PLACEHOLDER
  DATA — illustrative only, not for real parking" banner now appears on both the
  2D PNG and the 3D viewer whenever any placed aircraft is on unmeasured
  (`measured: false`) data — so a club member never mistakes an illustrative
  render for a real parking plan (#79). It disappears once the data is measured.
  Valid layouts also surface two actionable numbers — the tightest plan-view
  inter-plane gap and the smallest wing-over-tail vertical clearance — computed by
  a new read-only `hangarfit.metrics` module (never entering the collision model).

### Changed

- **Plain-language conflict messages (#401).** `check` (exit 1) and the solver's
  trivially-infeasible / exhausted-budget summaries now lead each conflict with a
  readable sentence ("`fuji` overlaps `scheibe_falke`", "`x` intrudes into the
  maintenance bay", "`x` extends outside the hangar") instead of the raw `kind`
  enum, while keeping the precise `detail` (parts + z-gaps) verbatim. The exit-3
  "no feasible tow path (plane …)" message already named the blocking plane.

### Fixed

- **`hangarfit view` degrades to a static scene in seconds, not minutes
  (#398).** Layout-mode `view` now passes a small deterministic *global*
  tow-expansion cap (`_VIEW_TOW_MAX_TOTAL_EXPANSIONS`, 300) to `plan_fill`, so an
  un-routable layout (e.g. the default `layouts/example.yaml`) falls back to a
  static 3D render in ~5 s instead of grinding through the full ~16000-expansion
  disprove budget (~2 min). The bound is a deterministic expansion count, not a
  wall-clock deadline (ADR-0003); a fast-routable layout still animates, and an
  explicit `--tow-max-expansions` overrides the cap.

## [0.9.0] — 2026-06-02

### Added

- **`hangarfit --version`.** A top-level `--version` flag prints the installed
  package version and exits (#360).
- **DocGerdSoft "Horizon" brand identity.** Brand mark assets — avatar, banner,
  favicon, mark, and monogram SVGs under `docs/assets/` — and a brand identity
  note in the README (#380).

### Changed

- **Solver — back-of-hangar fill bias (#320).** The CLI now biases the spread
  post-pass to pack planes toward the back wall (default on; `--no-back-fill`
  disables, no effect under `--no-spread`), keeping the door-side approach
  corridors clear so `solve --render-paths` can thread a tow path to each slot.
  The bias is RNG-free re-ranking — same-seed output stays byte-identical.
  Documented as the 2026-06-01 amendment to ADR-0008.
- **Tow planner — `grid` heuristic is now the default, with a global
  fill-budget cap (#336).** The obstacle-aware `grid` A\* heuristic (added
  opt-in in v0.8.0) is now the default for `solve` / `plan_fill` / the CLI; the
  per-plane `_MAX_EXPANSIONS` is raised to 8000, and a *separate* deterministic
  global fill cap (`_MAX_FILL_EXPANSIONS`, 16000) bounds the total expansions
  across one fill so it never hangs. `--tow-heuristic euclidean` opts back into
  the older straight-line heuristic. Documented as the 2026-06-01 amendment to
  ADR-0007.
- **CLI `solve --render-paths` — spread-vs-towability backstop (#280).** When a
  default (spread-on) layout is fully un-routable, the CLI now re-solves once
  with spread disabled (reusing the same seed) and renders that tighter
  arrangement *if it routes* — reporting the swap on stderr, in `--json`
  (`diagnostics.spread_fallback_applied`), and as a `--write-yaml` provenance
  comment, never silently. With the #320 placement bias in play, multi-plane
  fills that were previously a bare exit 3 now route under default settings
  without the backstop firing at all. New ADR-0016.
- **Tow-path render palette retuned to the brand "Horizon" set (#380).** The
  renderer's per-plane colours move to the DocGerdSoft `PLANES` palette (Horizon
  `#0079B5` first), still derived from the Okabe–Ito CVD-safe set so every fill
  keeps maximal pairwise colour-blind separation.

### Security

- **Nightly fuzzing extended to the geometry and collision layers (#362, #369).**
  An Atheris + Hypothesis harness now fuzzes the oriented-rect transform and the
  pairwise collision checker on the nightly schedule, alongside the existing
  loader fuzzing.

## [0.8.0] — 2026-05-29

### Added

- **Wheel positions are now canonical per-aircraft data.** A new `Wheels` dataclass carries each aircraft's measured wheel positions in `fleet.yaml`, replacing the renderer's heuristic fuselage-fraction guesses; at load time `turn_radius_m` is cross-checked against the wheelbase (a 0.5×–5× sanity band). Documented in ADR-0013 (#322).
- Opt-in, default-off obstacle-aware A\* heuristic seam (`heuristic=` / `stats=`) on the tow-path planner, plus a reproducible routability benchmark and the towplanner-v2 spike write-up under `docs/superpowers/specs/`. The spike characterised why tight multi-plane fills are un-routable (budget-exhausted on tight finite-width maneuvering, not obstacle clutter) and found the obstacle-aware grid heuristic buys no extra routability (#332).

### Changed

- **BREAKING (pre-1.0):** `Aircraft.wheels` is now a required field — the loader raises a `LoaderError` on a missing or malformed `wheels:` block. All nine fleet aircraft carry a backfilled `wheels:` block (#322).
- Tow-path overlay now uses the CVD-safe Okabe–Ito 8-colour palette; the mid-wing colour moves to vermillion (`#d55e00`) for better protanopic separation from the low-wing yellow; and conflict overdraw is signalled with a hatch fill and dashed outline in addition to colour, so it survives greyscale and colour-blind viewing (#326).
- Cart-borne aircraft (`on_carts=True`) render as a small pallet under each wheel, oriented with the aircraft, instead of one body-sized deck rectangle — matching the physical cart geometry (#321).
- Hybrid-A\* per-plane node-expansion budget (`_MAX_EXPANSIONS`) raised 700 → 2000 — the empirical knee from a budget sweep — so more tight fills route; the slow-test per-plane perf ceiling was raised to match (#335).
- README badge row gains a CodeQL badge (slot 2) and the CI badge is now a clickable link, consistent with the other badges (#339).
- Release documentation prep is split into a dedicated `/release-prep` skill (CHANGELOG promotion + doc-freshness audit on its own focused-review PR into `develop`); `/release-cut` gains a Check E that refuses to cut until the CHANGELOG has been promoted (#325).

### Fixed

- `hangarfit.__version__` was a stale hard-coded `"0.0.1"` that never tracked `pyproject.toml`; it is now sourced from the installed package metadata via `importlib.metadata.version("hangarfit")`, with a `PackageNotFoundError` fallback for an uninstalled source tree, so it stays in sync with the release version (#341).

## [0.7.2] — 2026-05-28

Housekeeping cut. Two doc/test items left over from the v0.7.0/v0.7.1 release campaign — no behavioural change to `check`, `solve`, or `solve --render-paths` output for any existing scenario.

### Changed

- `tests/test_solver_search.py` now anchors every fixture / layout / data load on `Path(__file__).resolve().parent.parent` rather than process cwd, so pytest can be invoked from any directory and the tests still resolve the right files. Matches the existing convention in `tests/test_loader.py` (#317).
- README status section updated to reflect Phase 3a (tow-path planner v1) and Phase 3b (Reeds–Shepp v2) having shipped in v0.7.0/v0.7.1; removed the stale "No movement-sequence planning" out-of-scope claim and the stale "the example layout fails validation" parenthetical.

### Fixed

- LICENSE Apache-2.0 copyright line was the unfilled `[yyyy] [name of copyright owner]` template placeholder; now reads `Copyright 2026 DocGerdSoft (Patrick Kuhn)` (#310).
- `solver._plane_footprint_area` no longer leaves a `tail` part in both the reconstructed-fuselage span *and* the per-part lengths list — a structural double-count for an aircraft declaring both fuselage segments and a separate tail. Dormant in real use today (no fleet aircraft has a `tail` part) and behaviorally inert under the current `max()` reduction, but a regression guard against future helper refactors. Includes a unit test pinning the post-fix value (#317).

## [0.7.1] — 2026-05-27

First published release of the 0.7.x line. v0.7.0 was tagged on `main` but its GitHub Release could not be published — the tag was consumed by an immutable release during the release cut and is permanently reserved — so v0.7.1 supersedes it with identical features plus the release-workflow fix below.

### Fixed

- Release workflow is now compatible with GitHub immutable releases: it creates the release as a draft, uploads the Sigstore-signed artifacts while the draft is still mutable, then publishes — replacing the create-published-then-upload sequence that failed to attach assets to a sealed release (#285).

## [0.7.0] — 2026-05-27

The first release with tow-path planning: `hangarfit` can now plan how each aircraft is towed in and out, not just whether a static layout is collision-free. Also lands the full Arc42 architecture documentation set, the maintenance-bay walling rule, a spread-aware solver, and an OpenSSF supply-chain hardening pass.

### Added

- Tow-path planner (`towplanner` module): `hangarfit solve --render-paths` renders a per-plane tow path overlay plus a tow order. Best-effort — a layout the planner can't fully route still renders (blocking plane named on stderr); exit code `3` only when no candidate layout is tow-routable ([ADR-0007](docs/adr/0007-tow-path-planner-v1-scope.md), #188, #189, #190, #191, #196, #222, #197, #192, #193).
- Reeds–Shepp motion model — reverse arcs eliminate the reorientation loops of the Dubins-only first cut — and door **entry-cone** search over heading × offset (planner v2, [ADR-0010](docs/adr/0010-reeds-shepp-motion-model.md), #261, #262, #271).
- `bay_intrusion` maintenance-bay perimeter collision rule with partial-width, back-anchored geometry, replacing the legacy maintenance check ([ADR-0006](docs/adr/0006-bay-intrusion-maintenance-rule.md), #103, #104, #106, #107).
- Spread-aware solver: a best-of-all-basins post-pass maximizes the minimum inter-plane gap, surfacing `min_pairwise_gap_m` and `valid_basins_found` ([ADR-0008](docs/adr/0008-inter-plane-spread-soft-preference.md), #145, #267).
- Full Arc42 architecture documentation under `docs/architecture/` and an Architecture Decision Records system (ADR-0001 … ADR-0010) under `docs/adr/` (#132, #133, #134, #135, #136).
- Loader validates plane ids and `maintenance.plane` at the load boundary with did-you-mean suggestions (#221, #171, #175, #177).
- Nightly polyglot YAML-loader fuzzing (Hypothesis + Atheris); OpenSSF Scorecard Fuzzing 0→10 (#143, #253).
- OpenSSF Baseline L1 self-attestation and Best Practices **Silver** badge, with GOVERNANCE.md and Code-of-Conduct links (#232, #256, #259).
- Sigstore keyless cosign signing workflow for releases (#167).

### Changed

- Raised the supported Python floor to **3.12** (was 3.11) and collapsed the CI test matrix to a single 3.12 job; both hash-pinned lockfiles are now resolved on 3.12. **Breaking change** for 3.11 users ([ADR-0009](docs/adr/0009-single-supported-python-version.md), #213).
- Hash-pinned every lockfile end-to-end — dev deps, build toolchain, fuzz toolchain, and the pip-tools bootstrap — each guarded by a CI drift check (#140, #198, #199, #224).
- Solver determinism is now scoped to `max_restarts` ([ADR-0003](docs/adr/0003-rr-mc-solver-algorithm.md) amended, #267).
- Slimmed CLAUDE.md to operational guidance; migrated domain content to Arc42 (#137).
- LICENSE now ships in the sdist and wheel (#230).

### Removed

- Python 3.11 support and the multi-version CI matrix (#213).
- Legacy maintenance-bay collision check, superseded by `bay_intrusion` (#104).

### Security

- Added a security-posture document explaining the structural-zero OpenSSF Scorecard checks; made SECURITY.md phase-agnostic; documented the branch-protection residual cap (#142, #260, #225).

## [0.6.1] — 2026-05-23

Solver-polish follow-ups.

### Changed

- Broadened the `diversity_impossible` precondition wording in the solver spec (#119).

### Fixed

- Bounded `wall_time_s` in the fixture-matrix tests to stop time-sensitive flakes (#122).
- Fixed the OpenSSF Scorecard workflow push trigger to fire on the default branch and added `workflow_dispatch` (#126).
- Wired `CODECOV_TOKEN` so non-`main` coverage uploads succeed (#127).

## [0.6.0] — 2026-05-23

A large cut bundling the "going public" repository-hardening pass and the Phase 2a static layout solver. (There were no 0.2.0–0.5.0 release tags; that work shipped here.)

### Added

- `hangarfit solve` — a Random-Restart Monte-Carlo static layout solver that finds a valid arrangement when no hand-authored candidate exists, with pinning, minimal-edit repair, and forced-cart modes ([ADR-0003](docs/adr/0003-rr-mc-solver-algorithm.md)).
- Diversity metric for alternative layouts (`--alternatives`, edit-count thresholds) ([ADR-0004](docs/adr/0004-diversity-metric.md)).
- `SearchConfig.max_restarts` to bound the outer search loop (#111).
- Scenario types and penetration-depth reporting in `CheckResult`.

### Changed

- Default `layouts/example.yaml` is now a valid 6-plane layout.
- Corrected the placeholder dimensions in `fleet.yaml`.

### Security

- Added SECURITY.md, CONTRIBUTING.md, GitHub issue/PR templates, and Dependabot config (going-public milestone).
- Added CodeQL scanning and the OpenSSF Scorecard workflow + README badge; pinned all GitHub Actions to commit SHAs.
- Adopted ruff (lint + format), mypy, pre-commit, and pytest-cov → Codecov coverage in CI.

### Fixed

- Fixed a solver-determinism flake and added fail-loud regression canaries across the solver fixtures (#98).

## [0.1.0] — 2026-05-21

First Phase 1 cut — substrate for arranging the flying club fleet in a stack-style hangar.

### Added

- Aircraft, hangar, layout data models with cross-reference invariants (cart rule, movement-mode ↔ on-carts, maintenance-plane membership) (#1, #2).
- YAML loader with high-level `struts:` block expansion into mirrored Part instances (#3).
- Geometry primitives: plane-local → world transform (heading 0° = +y, CW positive), `aircraft_parts_world()` (#4).
- Collision checker: hangar bounds + maintenance-bay rule + pairwise parts overlap with 2D-plus-height clearances (#5).
- Visualizer: top-down PNG renderer, headless matplotlib, conflict highlighting (#6).
- CLI: `hangarfit check <layout> [--render <png>]` (#7).
- Apache-2.0 license, public-audience README, CI matrix (Python 3.11 + 3.12), branch protection on develop + main (#13, #14, #15, #16).
- Strut-aware golden tests + all-9-planes fixture using larger test-only hangar to accommodate strut-bracing geometry on placeholder dimensions (#5).

[Unreleased]: https://github.com/DocGerd/hangarfit/compare/v0.16.0...HEAD
[0.16.0]: https://github.com/DocGerd/hangarfit/compare/v0.15.0...v0.16.0
[0.15.0]: https://github.com/DocGerd/hangarfit/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/DocGerd/hangarfit/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/DocGerd/hangarfit/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/DocGerd/hangarfit/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/DocGerd/hangarfit/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/DocGerd/hangarfit/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/DocGerd/hangarfit/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/DocGerd/hangarfit/compare/v0.7.2...v0.8.0
[0.7.2]: https://github.com/DocGerd/hangarfit/compare/v0.7.1...v0.7.2
[0.7.1]: https://github.com/DocGerd/hangarfit/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/DocGerd/hangarfit/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/DocGerd/hangarfit/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/DocGerd/hangarfit/compare/v0.1.0...v0.6.0
[0.1.0]: https://github.com/DocGerd/hangarfit/releases/tag/v0.1.0
