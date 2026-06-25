# ADR-0028: Learned-backend dense train-to-mastery — resolved-negative; scope to the shipped inference seam

- **Status:** Accepted

- **Date:** 2026-06-24
- **Deciders:** [@DocGerd](https://github.com/DocGerd)

## Context & Problem Statement

Epic #607 added an opt-in learned backend (a neural *proposer* of poses + place-and-tow
order, behind the unchanged deterministic verifier). Its **inference seam** shipped
(#706, `learned.py` → `ml.infer`, verifier-gated, same `SolveResult` shape — see
[ADR-0027](0027-learned-backend-determinism-scope.md)). The remaining open question was
**train-to-mastery**: can a PPO policy learn to *construct* a valid **dense** packing —
specifically the frontier dense rung `trio-notch` (three aircraft packed around the
structural office-notch keep-out at the real 0.10 m clearance on the real Herrenteich
hangar)?

Five gate-run levers each KILLed at the **same** `valid_placed ≈ 0.333`
"place-one-validly-then-abstain" fixed point, one per distinct lever class:

| # | Lever | Class | Result |
|---|---|---|---|
| #794 | `--anchor-trio-notch` (pre-park a valid witness prefix) | start-state scaffold | KILL — vp 0.333, no transfer |
| #810 | `--spatial-tokens` (cross-attention over free-space cells) | representation | KILL — vp 0.333 = control exactly |
| #813 | `--r-valid-progress` (banked marginal valid-coverage carrot) | reward economics | KILL — moved the argmax into invalid **piling** |
| #817 | `--entropy-floor` (hold exploration temperature high) | exploration | KILL — inert, vp 0.333 = control |
| #823 | `--backplay-trio-notch` (reverse-curriculum, φ:0→1) | start-state distribution (ρ₀) | KILL on transfer (0.000); scaffold-only mixture 0.63–0.69 |

Rather than pull a sixth lever blind, a **pre-registered measure-first probe** was run to
decide whether to keep levering or stop. This ADR records the decision that followed.

## Decision Drivers

- **The charter.** The learned backend is chartered to **reach** dense layouts the
  deterministic RR-MC solver **misses** ("reach, not beat"). [§3 Context & Scope](../architecture/03-context-and-scope.md)
  **explicitly excludes** the reshuffle / "Tower of Hanoi" problem; empty-hangar fill already
  ships via the deterministic solver.
- **Evidence over fatigue.** A stop decision must rest on a *measured root cause*, not five
  inconclusive tries.
- **Honest scope.** Ship what works (the seam) and document what does not, without claiming a
  train-to-mastery the data refutes.

## The verified probe (the measured root cause)

Two torch-light experiments through the **product checker** (`ml.geometry_oracle.layout_valid`
= `collisions.check` + Caddy egress — the same oracle the env, the bonus, and the benchmark
use), independently reproduced by a multi-agent verification pass. Scripts and raw results are
**gitignored gate-run scratch** (`basin_mc.py`, `phi_eval.py`, `phi_eval_control.py`,
`probe-verdict.md`); they run against the checkpoints the lever recipes in
[`ml/README.md`](../../ml/README.md) produce. The decisive numbers are reproduced below.

- **φ=1 cold-start completion `vp = 0.000`.** With two of the three notch aircraft pre-parked
  at a valid witness prefix and the **third spawned at the door**, the policy cannot
  drive-and-pack it — measured `0.000` on the backplay checkpoint **and**, as a
  confound-removing control, on **both** non-backplay standard-curriculum checkpoints (the
  φ=0 *spawn-at-its-own-valid-pose* positive control reads `1.000` on backplay seed0, so the
  zero is a real policy failure, not broken wiring). The earlier "0.63–0.69 placement is
  learnable" was a φ-mixture average dominated by near-witness episodes; honest cold-start
  completion is zero across two independent training regimes.
- **Valid-triple manifold ≈ 2e-3 and FLAT across clearance.** Over 10 000 uniform 3-pose
  samples — a uniform-over-bounding-box estimate, so a *lower bound* on the true valid measure —
  `P(valid triple) ≈ 2e-3` at 0.10 m and **unchanged** at 0.30 m (+200% clearance), while
  `P(valid pair) ≈ 0.107` and per-object placement is mastered. The load-bearing, sampler-
  independent claim is the *flat-across-clearance* comparison (the absolute fraction is the
  lower bound, the flatness is robust): valid 3-packings exist but are **sparse isolated
  points** that relaxing clearance does **not** widen — so a clearance-relaxation modeling fix
  cannot help; the binding constraint is the joint packing geometry of the third object.
- **RR-MC already solves `trio-notch`** (all three aircraft, 0.10 m, ~30 s, 4/4 seeds). So
  `trio-notch` is a curriculum *stepping-stone*, **not** a charter target — clearing it would
  not even be a chartered win. The chartered dense target (all-8,
  `examples/herrenteich/layout.yaml`, solver-unreachable) is strictly harder and inherits the
  same cold-start wall.

**Diagnosis (high confidence, narrow form).** The binding wall is **cold-start drive-and-pack
of the marginal object** into a sparse, clearance-invariant valid slot. Reward / representation
/ exploration-temperature levers reweight *already-reachable* outcomes (Ng–Harada–Russell:
potential-based shaping cannot move the argmax — which is *why* #810/#813/#817 failed); only a
lever that changes ρ₀ to actually **train the cold-start completion distribution** could move
it, and that one measured capability is `0.000`.

## Considered Options

1. **Bank the inference seam + stop the dense train-to-mastery chase (chosen).**
2. **Run one more forward GPU lever** (diverse-archive reverse-curriculum seeded by the
   basin-MC generator). Rejected as the primary path: it attacks the **non-binding** half of
   the wall — diversity multiplies *which* valid slot is targeted, not the measured-zero
   door→slot **drive** — so it is an expected KILL. Kept only as an optional, pre-registered
   final shot if the user wants certainty before stopping.
3. **Re-scope to an easier rung** (the 2-object `pair` rung). Rejected: a strictly-worse bank —
   RR-MC trivially saturates the 2-object rung, so there is nothing the learned backend reaches
   there that the solver misses.
4. **Re-charter to completion** (fit the last 1–2 planes into an occupied hangar). Rejected:
   [§3](../architecture/03-context-and-scope.md) **explicitly excludes** the reshuffle use case,
   and a completion-only skill beats the deterministic solver nowhere it is chartered to.

## Decision Outcome

**Chosen option: bank + stop, with a falsifiable re-open gate.** The negative result is
*strengthened* by the probe — five orthogonal lever axes are now a single **measured** root
cause, not five tries (a sixth axis, the #827 ego-centric coordinate encoder, was gated
*after* this decision and **confirms** it — see re-open trigger #2 below) — and the charter
never required clearing the dense trio. The actually-met
success criterion is the shipped, verifier-gated inference seam (#706).

**Re-open this decision if (any one):**

1. a future policy's dense-notch **reach-rate** (Wilson CI) **exceeds RR-MC's** on a
   **witness-absent** scenario-kind (the true charter target — masquerade-proof). *This gate is
   now **runnable** (`ml.reach_rate.dominance_verdict` / `--witness-absent-tau`, #831) and was
   executed once: on the witness-absent `k8` over-capacity stratum (a fair-budget RR-MC reaches
   **0/9** distinct subsets, Wilson `ci_hi` 0.30) all six trained gate-run checkpoints
   (control / ego / backplay × 2 seeds) reach **0/108** → verdict **NOT MET**. The trigger stays
   open for a future policy; no current one trips it, because the policies' competence regime
   (≤3-aircraft rungs) and RR-MC's miss regime (over-capacity dense) are disjoint.* **or**
2. ~~a **relative / object-centric coordinate encoder lands**~~ — **RESOLVED-NEGATIVE
   (#827 / #829, 2026-06-25).** *Rationale at the time: that encoder was the one
   structurally-untested confound.* The opt-in `--relative-encoder` ego-centric augment encoder
   (SE(2) body-frame pose columns, `TOKEN_DIM` 24→28) landed and was gated on the same two-seed
   trio-notch ladder: `trio-notch-anchored` windowed-final `valid_placed` **0.353 / 0.332**
   (both PILING) ≈ the OFF control **0.317 / 0.316**, both sub-0.45; transfer ≈ 0 on both arms.
   Two graders agree, all three confounds pass (engagement checkpoint-proven), and a 4-lens
   adversarial panel returned **0/4 refuters**. The confound is now **measured** —
   representation / coordinate-frame is *not* the bottleneck — so this trigger is **spent**:
   it *confirms* the decision rather than re-opening it, **or**
3. the use case is **re-chartered** toward last-1–2-plane completion.

### Do-not-reattempt (refuted axes)

Witness-imitation / BC / DAgger into the weights (single-witness-family + absolute-world-coord
encoder = oracle-masquerade); continuous-k-anneal / empty-start k-anneal-to-0 / multi-driven
backplay k→0 (the k=0 endpoint **is** pure empty-start — refuted, env-guarded at
`seed_anchor_k == n-1`, and the easiest sub-case is already 0.000); any new Φ/PBRS shaping incl.
`--dense-slot-potential` (policy-invariant) and pile-safe carrots (#813 family); entropy floor
(#817) and RND/count-based novelty (same exploration-temperature axis, inert on the
isolated-point geometry); spatial-token / richer-representation levers (#810) and the
ego-centric / relative coordinate-frame encoder (#827 / #829 — both confirm
representation / coordinate-frame is *not* the bottleneck; #827 *did* reproducibly lift the
upstream generic `trio-box` rung, but that gain does not transfer to the notch wall); removing
the L4 PPO trust-region clip (**load-bearing** — clip-off → place-nothing); dwell-longer (the
control already shows ~39-iter flat dwell at 0.333).

## Consequences

### Positive

- The shipped inference seam (#706) is the honest, real, verifier-gated deliverable, independent
  of train-to-mastery.
- The determinism and safety contracts are untouched: the verifier remains the sole arbiter of
  validity and routability ([ADR-0027](0027-learned-backend-determinism-scope.md)).
- GPU is freed; the re-open door is clean and evidence-anchored.

### Negative

- The learned backend does **not** (yet) reach the dense notch. Train-to-mastery is paused, not
  solved.

### Neutral

- The refuted-axes list above is the standing guard against re-treading. The probe artifacts
  (`basin_mc.py`, `phi_eval.py`, `phi_eval_control.py`, `probe-verdict.md`) are the cited
  evidence; the lever-by-lever record stays in [`ml/README.md`](../../ml/README.md).

## More Information

- Related ADRs: [ADR-0027](0027-learned-backend-determinism-scope.md) (the learned-path
  determinism/safety contract, which this decision leaves untouched);
  [ADR-0003](0003-rr-mc-solver-algorithm.md) (the deterministic solver that already reaches
  `trio-notch`).
- Related architecture: [§3 Context & Scope](../architecture/03-context-and-scope.md) (the
  charter exclusion of the reshuffle problem); [§5 Building Block View](../architecture/05-building-block-view.md)
  (the `learned.py` seam).
- Operational record + lever recipes: [`ml/README.md`](../../ml/README.md).
- Related issues: epic #607; the training-improvement backlog #736 (closed resolved-negative);
  the shipped inference seam #706.
