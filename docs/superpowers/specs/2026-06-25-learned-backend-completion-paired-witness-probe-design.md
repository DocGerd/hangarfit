# Design — Completion paired-witness diagnostic probe (ADR-0028 trigger-#3 geometry confound)

**Date:** 2026-06-25
**Status:** Draft for review
**Relates to:** epic #607 (learned backend), [ADR-0028](../../adr/0028-learned-backend-train-to-mastery-resolved-negative.md) (train-to-mastery resolved-negative), the #821 backplay lever, the #832/#835 feasibility-first lesson.

---

## 1. Context

The learned RL backend's **dense empty-start train-to-mastery** charter is *resolved-negative* (ADR-0028):
six orthogonal levers each KILLed at the same `valid_placed ≈ 0.333` "place-one-validly-then-abstain"
fixed point. The measure-first probe named the binding wall as **cold-start drive-and-pack of the
marginal object into a sparse, clearance-invariant valid slot**, evidenced by a `valid_placed = 0.000`
at φ=1 (the last object spawned at the door and towed all the way in) — confirmed on *non-backplay
control* checkpoints, i.e. a policy-family property, not training damage.

The natural next direction is the **real deployment shape**: complete a mostly-parked hangar (place the
last 1–2 planes after a late return / surprise maintenance), ADR-0028 re-open trigger #3. A design-panel
evaluation (4 lenses + 3 adversarial verifiers) surfaced one decisive observation that reopens the
question as *testable* rather than settled:

> ADR-0028's load-bearing "2e-3 needle" is the **unconditional** joint-triple probability. Completion
> never samples three random planes — it **conditions on a valid (N−1)-prefix already parked**. The
> decision-relevant quantity is the **conditional last-slot probability**, measured this session at
> **0.064 (tight notch) vs 0.278 (roomy)** — a 4.4× gap. So completion structurally escapes the 2e-3
> needle, and manifold width becomes a controllable experimental variable.

ADR-0028's diagnosis has **two ingredients**: the door→interior **drive** and the sparse **slot**. No
single naive framing separates them. This probe does.

## 2. Problem statement

Decide one bit, cheaply and feasibly-grounded: **is the binding wall the door→interior drive (learning
capacity) or the slot sparsity (geometry)?**

- If it is **slot geometry**, a fatter conditional last-slot should lift door-start completion → a
  *scoped* completion charter on roomy/medium hangars becomes defensible.
- If it is the **drive**, more valid termini will not help → the resolved-negative *generalizes* beyond
  the notch and the bank is fortified.

The honest prior (stated by the synthesis lead) is **NO-GO**: a fatter slot multiplies valid *termini*
but does not teach the multi-segment reverse-capable tow from the door into any of them; the φ=1=0.000
floor is a strong policy-family prior. The probe's value is that a NO-GO **at 4.4× more slack** closes
the last geometry confound and makes the bank bulletproof; a GO reopens a *scoped* direction. **Either
outcome is decision-grade.**

## 3. The probe — paired door-spawn, manifold-width as the only controlled variable

One paired experiment. Hold the **drive fixed** (door spawn, φ=1) and toggle **only** slot-width:

| Arm | Hangar / witness | N | seed_anchor_k | Driven | Conditional last-slot |
|---|---|---|---|---|---|
| **Tight** | `tests/fixtures/ml/witness_notch.yaml` (existing, validity-pinned) on the Herrenteich notch | 3 | 2 | 1 | ~0.064 (the measured 0.000 wall) |
| **Roomy** | **`tests/fixtures/ml/witness_roomy.yaml` (new)** on `tests/fixtures/test_hangar_large.yaml` (25×30 m, clearance 0.3) | 3 | 2 | 1 | ~0.278 |

- **Start state = door, φ=1.** This is the env default `_spawn` at the door pose: a completion rung with
  `seed_anchor_k = N−1` and **no** backplay-φ knob. No env / reward / action-space code changes
  (the substrate map confirms F_door is zero-new-code).
- **Controlled variable = the witness/hangar fixture only.** Identical training config, hyperparameters,
  and seeds across both arms.
- **Replication:** 2 seeds per arm (mirrors the #815/#821/#827 two-seed gate convention), 4 cells total.
- **Reward unchanged:** the product checker (`collisions.check` + Caddy egress); `validity_conditional_terminal`
  gate intact.

## 4. Metric and pre-registered null — the masquerade guard

With `seed_anchor_k = 2` of `N = 3`, a **place-nothing** policy already reads
`terminal_fraction = len(_parked)/len(requested_ids) = 2/3 ≈ 0.667` (`ml/env.py:112` pre-parks into
`_parked`; `ml/env.py:355` divides by the full `requested_ids`). Aggregate `valid_placed` is therefore
**floored at ~2/3 and uninformative** — this is exactly the artifact that made #821's "0.63" look like
competence.

**The probe metric is MARGINAL last-object completion, never aggregate `valid_placed`:**

> **marginal completion** = P( all N validly parked | valid (N−1)-prefix pre-parked )
> operationally: the fraction of deterministic eval episodes whose `terminal_fraction == 1.0`
> (the single driven object validly parked), read at fixed φ=1.

**Pre-register the null BEFORE the run:** marginal completion of a place-nothing / abstain policy = **0**
(it never lifts `terminal_fraction` above the 2/3 floor). Record the floor and the null in the metrics
notes at run start.

**The first implementation step must confirm the exact extraction** of marginal completion from the
`--metrics-out` JSONL (the precise field/derivation that isolates "driven object validly parked" from the
floor), and add a tiny unit asserting a place-nothing rollout reads marginal = 0 while a witness-prefix +
valid-last-park reads 1.0. This nails the metric to code, not prose.

## 5. Decision rule (go/no-go)

Read marginal completion at fixed φ=1 per arm (windowed over the rung's iterations, both seeds):

- **GO (slot-geometry binding):** roomy marginal ≥ **~0.30** (clearly learning the fat slot) **AND**
  notch marginal ≈ **0.000**. → Open a *scoped* completion charter on roomy/medium hangars; the
  tight-dense KILL stays explicitly intact (not a blanket re-open).
- **NO-GO (drive binding):** roomy marginal ≈ **0.000** too. → Resolved-negative generalizes; record
  that the geometry confound is closed and the bank stands harder.
- **Ambiguous** (roomy lifts but < ~0.30, or notch non-zero): inconclusive — report the curve, do not
  over-claim; treat as NO-GO for charter purposes and note the residual.

The verdict is recorded in `ml/README.md` (lever ledger) **and** ADR-0028's re-open-trigger ledger
**regardless of direction**.

## 6. Feasibility-witness requirement (the #832/#835 guard)

The probe is only valid if **both** arms rest on a layout *proven valid by construction*:

- **Tight arm:** `witness_notch.yaml` is already a proven k-prefix of the valid all-8
  `examples/herrenteich/layout.yaml`, validity-pinned by `tests/ml/test_stage_builder.py`.
- **Roomy arm:** author `tests/fixtures/ml/witness_roomy.yaml` — a valid k=3 layout on
  `tests/fixtures/test_hangar_large.yaml` produced by a **big-budget `hangarfit solve`**
  (reproducible/auditable; roomy fills are trivial for RR-MC), then **verified with
  `hangarfit check witness_roomy.yaml --render out.png` (must report VALID)**, then committed with a
  `test_stage_builder` validity pin mirroring the notch witness. *Any k-prefix of a valid layout is
  valid*, so the pre-parked 2-prefix is feasibility-clean by construction.

**Without the verified roomy witness, a roomy 0.000 would be infeasibility, not learning failure — the
#832/#835 trap re-applied to start-states.** The verified fixture is a hard precondition for the run.

## 7. Kill conditions (what would invalidate the probe)

1. Marginal completion read off **aggregate `valid_placed`** or a **φ-mixture windowed mean** instead of
   fixed-φ=1 marginal — re-creates the 2/3-floor masquerade.
2. The **roomy witness is not `hangarfit check`-verified** → a roomy 0.000 is infeasibility, not a result.
3. Treating a roomy **GO as a deploy/dominance claim** — RR-MC saturates roomy fills, so even a roomy
   learning success is **diagnostic-only** and beats RR-MC nowhere it is chartered (ADR-0028 rejected
   re-scope-to-easier for exactly this).
4. The arms differ in **anything but the witness/hangar** (config, seeds, k, budget) → slot-width is no
   longer the controlled variable.

## 8. Success criteria (probe is "done")

1. A committed, `hangarfit check`-verified `witness_roomy.yaml` with a `test_stage_builder` validity pin
   exists before any training run.
2. Two completion door-spawn Stages (notch, roomy; `seed_anchor_k=2`, `max_objects=3`, no backplay knob)
   plus the marginal-metric unit test — **no env / reward / action-space code changed**, ml-rl-guard
   invariants intact, determinism contract honored.
3. The paired run produces a fixed-φ=1 **marginal** completion number for both arms × 2 seeds, read
   against the pre-registered 2/3-floor null, from the existing `--metrics-out` JSONL.
4. The one bit (drive-binding vs slot-geometry-binding) is decided and recorded in `ml/README.md` +
   ADR-0028 — either direction.
5. If GO: a scoped roomy/medium completion charter is opened as a *separate* follow-up with the
   tight-dense KILL left intact.

## 9. Out of scope

- No changes to `ml/env.py`, `ml/reward.py`, `ml/action_space.py`, or `ml/encoding.py`.
- No beat-RR-MC / deploy claim from this probe (diagnostic only).
- F_reposition (warm-start in-hangar nudge) — off-charter, needs an arc42 §3 / ADR-0028 amendment + a
  new perturbed-start witness family + new code; explicitly deferred. May be escalated as a *separate*
  charter-amendment decision only if this probe is NO-GO and the user wants one more lever.
- The φ-anneal sweep (F_staged): demoted — it adds drive-length as an uncontrolled confound on top of
  slot-width; the paired door-spawn probe is strictly cleaner for the same cost.

## 10. Open decisions for spec review

1. **Roomy-witness authoring** — defaulted to big-budget `hangarfit solve` (reproducible/auditable). The
   alternative is hand-author + `hangarfit check` (faster, but the check gate is then the only proof).
   Confirm the default.
2. **Iteration budget & seeds per arm** — defaulted to the two-seed gate convention and a budget matching
   the prior trio-notch-anchored rungs. Confirm or set explicitly.
3. **Marginal-metric extraction** — the exact JSONL field/derivation is to be pinned in the first
   implementation step (with the unit test in §4); flag if you want a specific definition.

## 11. Delivery shape (for the implementation plan)

Issue-driven GitFlow, two stages so the verified code lands before the run:

- **PR-1 (code):** `witness_roomy.yaml` + validity-pin test + two completion Stages + the marginal-metric
  unit test + an ml/README placeholder row. Full `/pr-review` arc incl. **ml-rl-guard**. User merges.
- **Run:** execute the paired probe on merged code from a **worktree pinned to the branch** (fixtures are
  read lazily from the working tree — a mid-run branch switch breaks it; the #736 gotcha).
- **PR-2 (docs):** record the verdict in `ml/README.md` lever ledger + ADR-0028 re-open-trigger ledger.
