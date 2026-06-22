# ADR-0027: Learned-backend determinism scope — verifier strict, proposer weaker

- **Status:** Proposed

- **Date:** 2026-06-15
- **Deciders:** [@DocGerd](https://github.com/DocGerd)

## Context & Problem Statement

[ADR-0003](0003-rr-mc-solver-algorithm.md) binds the static solver and the tow
planner to a **byte-identical** reproducibility contract: the same `(scenario, seed)`
must produce a bit-identical `SolveResult` / `MovesPlan`, enforced by the
`determinism-guard` double-solve check on `solver.py` and `towplanner.py`. Epic #607
adds an **opt-in learned backend** (a neural *proposer* of poses + place-and-tow order)
behind the unchanged deterministic verifier. Neural inference is **not** bit-identical
across machines — floating-point op ordering, the ONNX-runtime execution provider, and
GPU/CPU hardware all introduce sub-ULP variation that no amount of seeding removes. So
we must decide *how the determinism contract applies to the learned path* without either
(a) weakening the guarantee that makes the deterministic tool trustworthy, or (b)
pretending a cross-hardware byte-identity that is physically not achievable.

## Decision Drivers

- The verifier's hard guarantee (validity + routability are ground truth) must stay intact and `determinism-guard`-bound.
- The learned path needs an **honest, useful** reproducibility contract — enough to debug and regression-test, without claiming what float/EP nondeterminism makes impossible.
- The project's safety invariant: **no invalid layout is ever returned to a human** — the deterministic checker is the final gate regardless of what the policy proposes.
- Keep `determinism-guard`'s scope precise so a green guard always means the same thing.

## Considered Options

1. **Scope split (chosen).** The verifier (`collisions.check` + `towplanner`) stays under the strict ADR-0003 byte-identical contract, `determinism-guard`-gated, exactly as today. The learned **proposer** is explicitly **outside** ADR-0003 / `determinism-guard` and gets a weaker, documented two-tier contract: **within-a-build, bit-identical** (fixed weights + fixed seed + pinned onnxruntime execution provider) and **cross-machine, verifier-validity-only** (the same valid+routable verdict, not byte-identical poses), with its own canaries.
2. **Force the learned path under full ADR-0003 byte-identity.** Pin every float op / EP so the learned output is bit-identical cross-machine too.
3. **Exempt the learned path from any determinism contract.** Treat its output as free-form; rely solely on the verifier to reject invalid results.

## Decision Outcome

**Chosen option: the scope split.** It preserves the verifier's hard guarantee unchanged while giving the learned path the strongest contract that is *actually true* — within-build bit-identity for debugging/regression, and cross-machine validity-equivalence below the verifier — and it keeps `determinism-guard`'s meaning crisp (it guards the verifier, never the proposer).

### Why not force the learned path under full ADR-0003 byte-identity?

Cross-hardware bit-identity for neural inference is brittle-to-impossible: it would require pinning the execution provider, disabling fast-math/fused ops, and freezing BLAS threading on every consumer's machine, and would *still* break on a different CPU/GPU. The contract would either be unmeetable (blocking the backend) or so fragile that a green check would give false confidence. The byte-identity guarantee earns its keep on the deterministic solver, where it is cheap and real; on the learned path it is neither.

### Why not exempt the learned path from any determinism contract?

A backend with *no* reproducibility contract cannot be debugged or regression-tested — a flaky proposal could not be distinguished from a regression, and "re-run and hope" is not a contract. Within-build bit-identity (achievable with fixed weights/seed/EP) is worth keeping; discarding it loses real value for no gain. The verifier-as-final-gate protects *safety* but says nothing about *reproducibility*, which is what this ADR is about.

## Consequences

### Positive

- The deterministic tool's trust contract is untouched: `determinism-guard` still means "the verifier/solver is bit-reproducible," with no carve-out that dilutes it.
- The learned backend ships with an honest, testable contract instead of an unmeetable promise or none at all.
- The safety invariant is orthogonal and preserved: the verifier rejects any invalid proposal regardless of determinism tier.

### Negative

- Two reproducibility tiers must be documented and understood (verifier strict; proposer weaker).
- The learned path needs its **own** canaries — a within-build double-run bit-identity check and a cross-machine validity-equivalence check — built when the backend lands (a later #607 rung).

### Neutral

- Until the learned backend is implemented, **no learned code exists**, so this ADR changes nothing about current behavior: `determinism-guard` and the verifier contract are exactly as before. The ADR records the governing decision ahead of the build so the contract cannot drift during implementation.
- `determinism-guard` continues to guard only `solver.py` / `towplanner.py`; the `--backend learned` path is explicitly out of its remit.

## Compliance

- Existing: `determinism-guard` (the double-solve diff on `solver.py` / `towplanner.py`) remains the compliance check for the **verifier** contract, unchanged.
- **Tier-1 (within-build bit-identity) canary now exists:** `tests/ml/test_infer.py::test_learned_within_build_bit_identity` verifies that two fresh single-threaded CPU-EP ONNX sessions on the same weights + seed produce byte-identical logits. Cross-machine (tier-2) validity-equivalence remains deferred to sub-project #7 (needs a shared trained checkpoint).
- No automated check is required while the backend is a stub (`solve_learned` raises `LearnedBackendUnavailableError`); the seam is exercised by the CLI clean-error test.

## More Information

- Related ADRs: [ADR-0003](0003-rr-mc-solver-algorithm.md) (the byte-identical contract whose scope this amends).
- Related specs: [`docs/superpowers/specs/2026-06-12-learned-backend-cold-joint-rl-env-design.md`](../superpowers/specs/2026-06-12-learned-backend-cold-joint-rl-env-design.md) — its §8 (Determinism & the verifier relationship) sketches the two-tier story and *defers* the binding decision to its sub-project #5; **this ADR is that decision**.
- Related spikes: #331 (CNN layout) and #332 (CNN tow path) — concluded in `docs/spikes/cnn-layout.md` / `docs/spikes/cnn-tow-path.md` (landing via #669).
- Related issues: epic #607; this rung #670.
