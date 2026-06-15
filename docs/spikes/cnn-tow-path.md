# Spike #332 — Can a learned model produce a valid *tow path*?

**Status:** CONCLUDED — verdict **GO, as the routability half of a single *joint* learned layout+path backend** (#607), where the model co-designs poses **and** the drive-in sequence so that the layout is **routable by construction**. What is *set aside* is only the narrow alternative of bolting a learned A\* *heuristic* onto the existing monotonic deterministic planner — that idea cannot dissolve the real dense-fleet blocker and does not deliver joint routability. Pure-feasibility spike; no shippable code.

**TL;DR.** Tow-path generation is **not** deferred. The target is a learned model that produces a valid layout *and* its tow paths *at the same time* (the cold-joint, routability-by-construction design). The reason this is the right call — and not a drop-in A\* heuristic — is what we learned since the spike was filed: the multi-body un-routability was (a) a tow-*motion abstraction* over-strictness (now fixed: #645/#646/#647/#648) and (b) **monotonic placement through a single door** — each body routes once to its final pose and never moves again (#667), so a body whose corridor passes through an already-parked body has no path *regardless of heuristic quality*. A joint agent that drives bodies in from the apron in a co-designed order **never produces** such a box-out in the first place — routability is established by construction, with the geometry oracle as the reward/verifier. That is the GO.

---

## Evidence base

- Two-probe gate **#642** (closed 2026-06-15) — Probe B (routability) results below; its explicit conclusion was that greedy place-then-route failing **argues *for* joint placement+routing**, not against a learned tow path.
- Today's root-cause: **#667** (shuffle-aware tow routing) and its merged increment **#668** (deterministic order-search in `plan_fill._place_rest`).
- Motion-abstraction fixes that overturned the interim "fundamentally un-routable" verdict: **#645** (Stemme dolly / cart turn radius), **#646** (motion clearance ≠ parked clearance), **#647** (lateral strafe + free-swivel pivot), **#648** (0.05 m calibration).
- Cold-joint env + reward design: [`docs/superpowers/specs/2026-06-12-learned-backend-cold-joint-rl-env-design.md`](../superpowers/specs/2026-06-12-learned-backend-cold-joint-rl-env-design.md) — drive-in-from-apron, routability by construction, geometry as the reward oracle only.
- Incumbent planner: `src/hangarfit/towplanner.py` — Hybrid-A\* + Reeds–Shepp (ADR-0007 v1, ADR-0010 v2 reverse-capable); determinism ADR-0003 (guarded by `determinism-guard`).
- Sibling spike: **#331** ([`cnn-layout.md`](cnn-layout.md)) — the layout half (also GO; the two resolve **together** into the joint backend).

---

## Q1 — Is it possible?

Yes. There are two families, and the spike's job is to pick the right one:

| Formulation | What it does | Verdict |
|---|---|---|
| **(B) Joint learned proposer — poses + drive-in order together** | The model emits a *sequence* of (object, coarse pose) decisions; bodies drive in from the apron one at a time; the layout is routable **by construction**. The geometry oracle (`collisions.check` / `path_first_conflict` / `plan_path` per leg) is the reward + verifier only. | **CHOSEN** — this is the user's "layout + path at the same time" and the cold-joint design |
| (A) Learned A\* cost-to-go *heuristic* for the existing planner | CNN: occupancy grid + goal → cost field that reorders A\* node expansion; kinematic search + feasibility unchanged | **Set aside** — reorders search; cannot break a single-door cyclic lock; does not produce joint layouts |
| Neural A\* / VIN; MPNet sampler; direct path regression | Various end-to-end / sampling planners | Out of scope for this gate (higher risk; would post-validate against the same oracle) |

**The hard part — non-holonomic kinematics — is shared by both and is feasible.** Tows are reverse-capable Reeds–Shepp curves (ADR-0010) with an entry-cone constraint (#262), a per-cusp `CUSP_PENALTY` cost so forward wins on an exact tie (#480), plus the #647 cart **strafe** (`Segment "T"`) and **free-swivel pivot** primitives. In the joint design these are the *action space* of the drive-in agent, validated leg-by-leg by the unchanged oracle — so kinematic validity is guaranteed by construction + verification, not asserted by the net.

## Q2 — Is it beneficial?

**Yes — and the benefit is the whole point of the epic.** Two findings since the spike was filed *clarified* (not killed) the benefit:

**(a) The 2-body wall was an abstraction artifact, now fixed.** Probe B's interim "FUNDAMENTAL — dense all-8 proven tow-unroutable" reading was an over-strict tow-motion model (motion clearance pinned to the full *parked* margin; in-hangar body using its *taxi* turn radius instead of dolly-pivoted). Both fixed (#645/#646/#647/#648). So routability is **not** fundamentally blocked.

**(b) The remaining dense-fleet wall is *placement structure*, which the joint agent is designed to avoid.** With the abstraction corrected, `plan_fill` is still a monotonic greedy fill through a single 13.46 m door (#667). #668 added a deterministic order search, but on the full Herrenteich *today* fleet it proved a **true cyclic lock** (no static order seats all 9 bodies; 120 k expansions / ~525 s, #667). The static-dense all-8 `layout.yaml` is likewise **tow-unreachable by *any* method** — the #642 probe showed Scheibe + Stemme cannot both route into an *empty* hangar at their dense poses, so it is a static-feasibility artifact, not a tow-achievable configuration. **This is precisely why a joint model is needed:** it does not target the static-dense layout — it co-designs a *routable* (less-dense, differently-posed) arrangement, picking the density that *is* routable. A place-then-route decomposition (greedy *or* a learned heuristic on top of it) cannot make that trade-off; a joint agent can.

**Baseline (quantified — from the #667 routing root-cause investigation, 2026-06-15).** Routability of solver-found valid layouts at the default global / per-plane expansion budgets (`_MAX_FILL_EXPANSIONS` / `_MAX_EXPANSIONS` in `towplanner.py`, currently 16000 / 8000):

| subset size | routable | wall-clock |
|---|---|---|
| 4 aircraft | **1/1** | 32 s |
| 5 aircraft | **0/1** (aviat_husky / fk9 blocked, budget exhausted) | 345 s |
| 6 aircraft | **0/1** | 316 s |
| lone broadside Scheibe (18 m) | ✅ routes (0.5 s with strafe) | — |
| Scheibe + Stemme (N=2, deep static-dense poses) | ❌ no order routes both into an *empty* hangar | — |

Read correctly: the deterministic place-then-route ceiling is ~4 at the default budget, and it is expensive — **because it is solving the wrong decomposition** (fixed dense poses, then route). The joint agent reframes the objective to *routable density*, which is strictly below static-optimal density but is the real "who fits — and can be towed in — today" question.

## Q3 — How would it integrate, modularly?

**Proposer + verifier, non-negotiable** — identical seam to #331 (one backend produces both poses and the tow plan):

- `--backend learned` returns the **same `SolveResult` shape** (poses + the `MovesPlan`), so render / `view` / `--write-yaml` are unchanged. RR-MC + the deterministic planner stay the default and the fallback.
- The learned drive-in sequence is validated **leg by leg** by the unchanged oracle (`collisions.check` + `path_first_conflict` / `plan_path`); a leg that fails validation is rejected — the model never certifies a path.
- **Determinism (ADR-0003):** the verifier (collisions + towplanner) stays strictly byte-identical-bound and `determinism-guard`-gated; the learned proposer gets the same explicitly-amended weaker contract as #331 (within-build bit-identical with fixed weights/seed/EP; cross-machine = verifier-validity-only).
- **Packaging:** ONNX-only inference (`[learned-infer]`); torch contributor-only (`[train]`); `ml/` never in the wheel.

## Q4 — How would it be trained?

Per the cold-joint env design (which **supersedes** the earlier behavior-cloning / teacher-nester framing of the primer and #607): a **single agent learns to place *and* tow every object end-to-end from reward alone — no teacher, no behavior cloning, no deterministic search in the loop.** It autoregressively drives each object in from the apron and parks it; the reward is potential-based (Ng–Harada–Russell) shaping read from the geometry oracle (graded penetration, routability margin, sequence-deviation) with the binary valid+routable verdict kept as the truth signal (policy-invariant shaping → anti reward-hacking). The deterministic code (`collisions.check` + the ADR-0010 movement primitives incl. strafe) is retained **only as the reward oracle and the final safety gate**. A curriculum ramps difficulty (object count, hangar shape, clearance), optionally anchored near a known-valid seed layout — *curriculum only, not BC; no teacher labels are consumed.*

**Honest scope caveat (from #642 Probe A):** the static-dense feasible region is a *needle*, and routable density is below static-optimal density — so the realistic target is the routable daily subsets, ramped by a curriculum, possibly plateauing before the full all-8 + all ground objects. Worth confirming the **max routable count/density** for the Herrenteich set (sweep less-dense arrangements) — that number bounds the learned backend's ambition.

---

## Verdict & recommendation

**GO — the tow path is learned *jointly* with the layout** (cold-joint, routability-by-construction), as the routability dimension of epic **#607**. Specifically:

1. **Joint, not deferred.** One learned model co-designs poses **and** the drive-in order; routability is established by construction and confirmed leg-by-leg by the unchanged oracle. This is the user's "layout + path at the same time."
2. **Set aside idea (A)** — a learned A\* heuristic on the *existing monotonic planner*. It reorders search but cannot break single-door cyclic locks, and it keeps the place-then-route decomposition that #642 proved is the wrong tool. (It remains a *minor, optional* later optimization for routing-search *expense* on already-routable fills — not a substitute for the joint backend.)
3. **Target routable density, not the static-dense peak.** The static-dense `layout.yaml` is tow-unreachable by any method; the joint agent's job is to find the densest *routable* arrangement.
4. **Verifier authoritative; ADR-0003 scope amended, not weakened.**

This and #331 resolve **together**: a single joint learned layout+path backend (#607). The deterministic shuffle-aware planner (#667) remains a complementary, independent line that would also enrich the verifier's routability oracle.

## Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| Routable density is low (needle region, #642 Probe A) → joint model's reachable target is modest | High | Confirm max routable count first; curriculum easy→dense; judge value vs "park fewer planes" honestly |
| Mistaking idea (A) for the goal (a learned heuristic on the monotonic planner) | Resolved here | Joint, routability-by-construction is the GO; (A) is explicitly a minor later optimization |
| Non-holonomic action space (heading / reverse / entry-cone / cart primitives) | Medium | Reuse the existing primitives as the agent action space; validate leg-by-leg via the oracle |
| Determinism contract (towplanner is `determinism-guard`-gated) | Medium | Same ADR-0003 scope amendment as #331; verifier stays byte-identical |
| Dependency weight | Low | ONNX-only inference; never in wheel |

> Companion verdict for the layout half is in [`cnn-layout.md`](cnn-layout.md) (#331): **GO** for the same joint backend's placement dimension, scoped to routable arrangements (the static-dense all-8 is a needle and tow-unreachable).
