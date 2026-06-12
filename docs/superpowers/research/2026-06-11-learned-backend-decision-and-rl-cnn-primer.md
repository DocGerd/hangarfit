# Learned (RL) layout + tow backend — decision analysis & RL/CNN primer

_Generated 2026-06-11 from an agent-team panel (3 analyst lenses + 2 educators + a synthesis chair), grounded in a prior 13-agent verify-backed web-research pass. Covers GitHub issues #331 (learned layout) and #332 (learned tow path), to be graduated from spikes into an implementation epic._

---

## Part 1 — The four decisions: pros, cons, recommendation

**Overall panel confidence: `medium`**

### Integrated recommendation (the package)

Adopt one coherent, risk-staged, reversible ladder rather than four independent picks. Start with D2: run a time-boxed GO/NO-GO spike that builds the extended slow nester (confirmed absent from src/ today — the single biggest unproven precondition) plus a small Set-Transformer + coarse-pose supervised-proposer + deterministic-refiner probe, evaluated for free against the existing deterministic verifier (collisions.check + plan_fill, ground truth). Scope the spike not only to N<=5 (which RR-MC often already solves) but include at least one genuinely hard N=7-8 instance that ALSO probes single-door routability — this is the panel's unanimous hardening and the calibration that prevents a false-confidence GO. The spike's read drives everything downstream: if the nester cannot emit dense oblique z-nested teacher data, NO-GO cleanly shelves the project before torch/onnxruntime/CI surface is reshaped; if it can, proceed to the HYBRID paradigm (D1a) — BC warm-start into the basin where the confirmed dense margin (solver._score = conflict_count, total_penetration_m2) becomes informative, then optional PPO+KL fine-tune, with the BC-only checkpoint as a fully shippable v1 so the timeline never depends on PPO converging. Sequence placement-only first (D3a), reusing the existing deterministic plan_fill as a separate verifier stage and keeping its ~53x-placement-cost call OUT of the training loop; but treat the spike's hard-instance routability result as a trapdoor — if density and routability prove entangled, carry a cheap routability-proxy feature into milestone-1 placement state or flip to joint, because route-afterward otherwise re-creates RR-MC's exact single-door deadlock trap. Set the acceptance bar at REACH-not-beat + fast amortized inference (D4a): a binary, verifier-checkable, screenshot-able win on the case RR-MC provably can't touch, guarded by routing reached layouts through the existing refiner + ADR-0008 spread post-pass so 'reach' isn't 'reach-but-worse', and with an explicit path to harden toward BOTH if the spike shows systematic low quality. The one invariant binding all four together — and the gate the determinism-guard subagent will enforce — is that the learned proposer is a frozen, opt-in, off-the-critical-path artifact ([learned-infer]/[train] extras, signed ONNX release weights) that NEVER enters the verifier path: collisions.check and plan_fill stay bit-identical ground truth, RR-MC stays the default backend, and the new ADR amends ADR-0003's SCOPE (a weaker documented contract for the proposer) rather than weakening the verifier's contract. Net: every step is cheap, reversible, kill-switchable off the existing verifier, gates the largest unproven bet behind the smallest spike, and serves the user's stated dual goal of shipping an additive backend and learning RL/CNNs.

### D1: RL strategy: (a) HYBRID teacher warm-start + RL fine-tune, (b) PURE from-scratch RL, (c) SUPERVISED proposer only.

> **Recommendation:** (a) HYBRID — but explicitly conditional on D2's GO, with BC-only as a fully shippable v1 if the RL leg never pays off.

> **Confidence:** `medium` · **Consensus:** Unanimous 3/3 for (a). All three independently raise the SAME strongest counter — the teacher nester doesn't exist (which I confirmed in src/) — and all three resolve it identically by gating HYBRID behind the D2 spike and treating BC-only as the shippable fallback. The honest dissent is internal, not between lenses: the Shipper notes a hardcore time-minimizer would pick (c) with a cheap teacher and delete RL; the ML Researcher notes the dense verifier is the one thing that could make (b) viable where GFPack++ couldn't. No lens actually switches its vote on these, so consensus is real but each lens keeps an explicit escape hatch.

**Why:** HYBRID is the only paradigm with a precedent in this exact problem family and the only one whose structure doubles as a kill-switch (BC-only ships even if PPO never converges). The dense verifier is confirmed real, so the RL fine-tune is far better-posed here than in the literature. But every lens agrees the recommendation is meaningless unless the teacher precondition is proven first — hence the conditional on D2. Pure-RL is rejected for its undiagnosable near-flat-gradient failure on the exact hard case; supervised-only is the rational fallback, not the lead, because it cannot exceed a teacher that may itself inherit RR-MC's blind spot.

#### (a) HYBRID = teacher BC warm-start + RL fine-tune

**Pros**

- Only option with a direct smoking-gun precedent in the exact problem family (GFPack++ distills a slow nester rather than doing from-scratch RL).

- BC moves the policy into the basin where the verified dense margin (total_penetration_m2, confirmed at solver._score) becomes informative, defeating the RLVR near-zero-gradient desert on the hard 8-plane case.

- Structurally a kill-switch: the BC checkpoint is itself shippable as v1, so the timeline never depends on PPO converging.

- Best educational arc for a hobbyist (BC first, then RL is the textbook order) and the BC artifact is a frozen, reproducible ONNX that stays off the deterministic verifier path.

- PPO+KL-to-BC-prior can exceed teacher quality without collapse, so it is not capped like pure supervised.

**Cons**

- Load-bearing precondition does not exist: confirmed no teacher nester in src/; if the extended dense-oblique nester can't be built, HYBRID degrades to pure-RL-with-ceremony.

- The RL leg is the part most likely to burn weeks for marginal gain (RLVR limit: binary RL only sharpens what the base can already sample).

- Two-stage pipeline is more moving parts (torch training harness + onnxruntime inference) for a solo maintainer to keep green.

#### (b) PURE from-scratch RL

**Pros**

- Sidesteps the teacher-bootstrap dependency entirely — no unbuilt precondition to gate on.

- hangarfit's cheap DENSE verifier is the one ingredient most RL-packing literature lacks, so from-scratch RL is better-posed here than in the papers that rejected it.

- Maximal RL learning value if the goal is purely to learn the paradigm.

- If a GFlowNet variant is used, naturally yields the diverse layouts the project already values (ADR-0004/ADR-0008).

**Cons**

- Worst risk profile on the case that motivates the project: random init essentially never SAMPLES a valid dense layout, so the graded reward is a near-flat gradient on the 8-plane case.

- On a single hobbyist GPU you cannot scale past that desert.

- Failure is undiagnosable — no signal whether the bug is env, reward, or net; terrible debugging/educational ROI.

- Even the dense reward only helps once the policy already samples near-feasible dense layouts (RLVR limit), which is exactly what from-scratch can't reach here.

#### (c) SUPERVISED proposer only (no RL)

**Pros**

- Safest, fastest, most debuggable path to a working backend; a frozen ONNX artifact is trivially reproducible and determinism-contract-friendly.

- Forces the teacher to exist before any ML is attempted — honest about the real critical path.

- If paired with a cheap teacher (long-budget SA/RR-MC), could ship in days.

- No PPO infrastructure, no second reward-hacking surface.

**Cons**

- Hard-capped at the teacher's quality — can never exceed the nester it distills, so it cannot reach layouts the teacher itself cannot crack densely.

- If the teacher is only a beefed-up RR-MC, the proposer inherits RR-MC's exact blind spot on dense oblique 8-plane layouts — the whole point of the project.

- No curve to learn RL on, which only matters because the user explicitly wants to learn RL.

### D2: Spike gate: (a) YES time-boxed spike (build teacher nester + N<=5 supervised-proposer probe) as GO/NO-GO before the heavy ML epic, vs (b) NO commit the full epic now.

> **Recommendation:** (a) YES — but harden the spike: include at least one genuinely hard N=7-8 routability-stretch instance so the GO/NO-GO is not over-fit to the easy decoupled regime.

> **Confidence:** `high` · **Consensus:** Unanimous 3/3 for (a) and it is the highest-confidence vote across the whole panel (high/high/high). Strikingly, all three converge on the SAME refinement of their own strongest counter: an N<=5-only spike risks false confidence, so the spike must include a hard N=7-8 stretch probe. There is no dissent on the pick; the only nuance is the Shipper and Maintainer both flag the teacher-build-twice risk, which the hardening (scope the spike's teacher to be reusable, not throwaway) mitigates.

**Why:** This is the linchpin of the entire strategy and the master kill-switch that makes the D1 HYBRID vote safe. The biggest risk is binary and currently unknown — can a nester produce dense oblique teacher data — and it is answerable in days off the existing verifier. The project has a documented precedent (CNN spikes #331/#332 that falsified their own hypothesis and saved an epic). The unanimous refinement is non-negotiable: scope the spike to a hard N=7-8 stretch instance, because the difficulty cliff between N=5 and the intractable N=8 oblique-nested case is exactly where representation and routability could break, and an easy-regime-only spike would mis-calibrate the gate.

#### (a) YES = time-boxed GO/NO-GO spike

**Pros**

- Retires the single biggest, currently-UNKNOWN feasibility risk (can a nester emit dense oblique z-nested teacher data at all?) for GPU-days instead of GPU-months.

- Fits the project's institutionalized spike convention (perpetual milestone #15, docs/spikes/, the CNN #331/#332 NO-GO precedent that already saved an epic).

- Cheapest possible kill-switch read off the existing deterministic verifier (ground truth, no human labeling).

- Highest educational ROI per unit risk: teaches BC, encoder design, and verifier-reward plumbing end-to-end before committing PPO/torch/onnxruntime to the wheel and CI surface.

- A GO produces reusable teacher-data infrastructure; a NO-GO cleanly shelves the project before reshaping the release roadmap.

**Cons**

- An N<=5 spike may prove nothing about the intractable N=8 regime — RR-MC often already solves N<=5, so a green probe can manufacture false confidence.

- The spike's hardest deliverable (the extended teacher) is most of the epic's hardest work, risking doing the expensive part twice or anchoring on a spike-grade-shortcut teacher.

- Spikes can become perpetual deferral ('one more spike').

#### (b) NO = commit the full ML epic now

**Pros**

- Avoids building the teacher twice; treats it as first-class milestone-1 work rather than throwaway scaffolding.

- No risk of a false-confidence green spike that doesn't transfer to N=8.

- Faster to the real deliverable IF the teacher turns out feasible.

**Cons**

- Bets weeks-to-months and reshapes wheel/CI/ADR surface on an unproven precondition — the exact anti-pattern the project's spike discipline exists to prevent.

- Drags torch/onnxruntime + a training harness into the repo before the core hypothesis is tested.

- A solo maintainer cannot afford to discover non-feasibility after the release roadmap is already committed.

### D3: Sequencing: (a) PLACEMENT-ONLY first (deterministic planner routes afterward), JOINT routability-aware reward in milestone 2, vs (b) JOINT from the start.

> **Recommendation:** (a) PLACEMENT-ONLY first — but the D2 spike MUST probe routability on a hard instance; if the spike shows density and routability are entangled, FLIP to joint (or at least a cheap routability-proxy feature in the placement state) in milestone 1.

> **Confidence:** `medium` · **Consensus:** Unanimous 3/3 for (a), but this is the panel's most genuinely contested decision — all three rate it medium, and all three articulate an unusually strong counter (the route-afterward deadlock is literally RR-MC's documented failure mode, including the noted un-routable example.yaml). The dissent is not a vote split but a shared, explicit conditional: the ML Researcher and Shipper both say if the spike reveals routability/density entanglement, D3 should flip to joint and D4 should harden. So the consensus is '(a) by default, with a spike-driven trapdoor to (b)'.

**Why:** Placement-only wins on cost realism and milestone independence: the ~53x plan_fill cost makes joint training intractable on one GPU as a starting point, and the dense reward genuinely lives in placement. But the panel will not paper over the load-bearing risk — route-afterward is exactly the irreversibility/single-door-deadlock trap RR-MC already fails at. The recommendation therefore inherits the D2 hardening: the spike's hard N=7-8 instance must test routability, not just packing density. If they prove entangled, milestone 1 must carry at least a cheap routability-proxy feature (not full plan_fill) in the placement state, or flip to joint outright.

#### (a) PLACEMENT-ONLY first; JOINT in milestone 2

**Pros**

- Smaller, independently shippable, independently testable milestone; reuses the existing deterministic plan_fill as a separate verifier stage instead of inventing joint reward.

- Decomposes by reward density: the dense, per-step-readable signal lives in placement (collisions._score); routability is sparse, delayed, and globally-coupled — master the dense sub-problem first (curriculum/staged-reward best practice).

- Keeps the expensive plan_fill call (~53x placement cost per the profiling spike) OUT of the training loop, so each gradient step stays fast on one GPU.

- Matches the project's own proven phase 2 (placement) / phase 3 (tow) sequencing.

- Milestone 2 can add routability as an annealed, optimality-preserving potential-based shaping term (NHR-1999) rather than full plan_fill every step.

**Cons**

- Structurally re-creates RR-MC's exact ordering/irreversibility + single-door deadlock trap: can learn gorgeous, valid, UN-routable (exit-3) layouts and declare success on the wrong metric.

- If routability and density are deeply entangled in this hangar (single door + apron), placement-only optimizes a metric partly orthogonal to the real goal, and milestone 2 becomes a near-total redo, not an extension.

- Risks a milestone that demos but never actually beats the baseline on the hard 8-plane case.

#### (b) JOINT from the start

**Pros**

- Routability informs placement directly — the only way the placement head ever learns the door-deadlock constraint, which the research flags as the natural seam.

- Avoids learning a whole layout class that is categorically un-routable and then discovering it at routing time with no gradient explaining why.

- If routability is the binding constraint, this is the only sequencing that targets the actual goal from day one.

**Cons**

- Every RL step must invoke towplanner.plan_fill (Reeds-Shepp / Hybrid-A*, ~53x placement cost), making gradient steps seconds long and the run intractably slow on a single GPU — near-disqualifying as a STARTING point.

- Mixes a dense placement reward with a sparse, near-binary routability cliff; the sparse term dominates failure modes and explodes reward-hacking risk.

- Forces a second reward-hacking surface and the hardest credit-assignment problem into the very first training run a beginner attempts.

### D4: Acceptance bar: (a) REACH layouts RR-MC misses + fast amortized inference (slow SA may still win per-instance), (b) BEAT per-instance solution quality vs RR-MC, (c) BOTH.

> **Recommendation:** (a) REACH-not-beat + fast amortized inference — with one quality guardrail: among reached-valid layouts, prefer the refiner+spread post-pass so reached layouts aren't visibly worse than RR-MC's on overlapping cases. Revisit toward (c) only if the D2 spike shows reached layouts are systematically low-quality.

> **Confidence:** `medium` · **Consensus:** Votes are 3/3 for (a), but confidence is the panel's most split: Shipper high, Maintainer high, ML Researcher LOW. That low is meaningful dissent — the ML Researcher argues 'reach' and 'quality' are not cleanly separable when the project itself encodes quality as a first-class metric (ADR-0008 spread), so (a) may be under-specifying success and (c) is the more honest bar. The Maintainer and Shipper counter that this is partly a learning project (the user wants to learn RL/CNNs), which dissolves some of the shipping-value critique. I record the (a) consensus but flag the quality-separability concern as the reason confidence drops to medium overall rather than high.

**Why:** (a) is the only bar that is binary, verifier-checkable for free, early, and aligned with the genuine value of a learned proposer (amortized inference expanding the feasible set on the case RR-MC provably can't touch). (b) is a category error where RR-MC returns nothing and a home-turf loss where it doesn't; (c) over-scopes and may be unreachable on one GPU. The ML Researcher's low-confidence dissent is legitimate and is why the recommendation carries a quality guardrail — route reached layouts through the existing refiner + ADR-0008 spread post-pass so 'reach' doesn't ship visibly worse arrangements — and an explicit trapdoor to harden toward (c) if the spike reveals systematic low quality.

#### (a) REACH-not-beat + fast amortized inference

**Pros**

- Binary, verifier-checkable, early win condition: did the learned backend produce a collisions.check-accepted layout on a case where RR-MC returns exit-nonzero? No fuzzy quality metric needed.

- Aligned with what a learned proposer is genuinely good at (amortized millisecond inference vs seconds-to-minutes search) — a real new capability, not a marginal improvement.

- Directly tied to the project's actual pain point (the intractable dense oblique 8-plane case) and demoable with a single screenshot.

- With a cheap verifier you can always run learned AND RR-MC and take the better of the two, so 'expand the feasible set fast' is the only marginal bar that matters.

- Doesn't pit a young proposer against a mature 2000-line solver on its home turf (per-instance optimization), avoiding a benchmark treadmill that would kill a hobbyist's morale.

**Cons**

- Risks shipping a backend nobody uses: if learned only matches what a patient SA could eventually find, a rational user just runs the slow solver longer.

- Capability without quality is the classic neural-CO disappointment — new-but-worse layouts (worse spread per ADR-0008, tighter margins) are a confusing opt-in product.

- 'Reach' and 'quality' aren't cleanly separable when the project encodes quality (diversity/spread) as a first-class metric, so (a) may quietly under-specify success.

#### (b) BEAT per-instance solution quality vs RR-MC

**Pros**

- Guarantees the learned backend is a real upgrade where both solvers work — no regression confusion for the user.

- Forces a concrete quality metric, which the project arguably needs anyway (it already values spread/diversity).

**Cons**

- Category error on the motivating case: where RR-MC returns nothing, there is no per-instance quality to beat.

- Where RR-MC does solve, a slow classical SA/nester with a huge budget will often legitimately win per-instance — a fight the learned model usually loses and shouldn't have to.

- A moving target requiring a metric definition; invites endless 'is it 3% tighter' bikeshedding that delays shipping.

- The verifier-repair refiner can top up quality classically anyway, making the bar partly redundant.

#### (c) BOTH

**Pros**

- The most intellectually demanding and genuinely useful bar; would guarantee the backend is never a regression AND expands the feasible set.

- Most defensible contract for a tool whose whole value is trustworthy verification.

**Cons**

- Inherits all of (b)'s cost (metric definition, treadmill, home-turf fight) on top of (a).

- May be literally unreachable on one GPU, turning a learning project into a guaranteed disappointment.

- Over-scopes milestone 1 and removes the early, crisp win condition the project needs to stay motivated.

---

## Reinforcement Learning, Grounded in the Hangar: A Primer for the hangarfit "learned" Backend

### The frame: agent, environment, and the proposer to verifier loop

**In plain terms.** RL is a loop between two halves. The AGENT is the thing that learns and decides; the ENVIRONMENT is everything the agent acts on and gets feedback from. Each turn the agent observes the current situation, picks an action, and the environment responds with a new situation plus a number called the REWARD that says how good that action turned out. The agent's only job is to learn to act so the total reward over the whole interaction is as high as possible. Crucially the agent never gets told the correct action; it only gets scored after the fact and has to infer good behavior from those scores. That scoring channel is the entire teaching signal.

**In hangarfit.** The AGENT is the learned proposer (the Set-Transformer + hangar-mask CNN + selection/pose/feasibility heads). The ENVIRONMENT is the hangar plus the deterministic checkers: it holds the keep-out geometry (L-shape, notch, bay, apron) and the planes already placed, and it answers every proposed move by running collisions.check() and (later) towplanner.plan_fill(). The verifier IS the environment's reward function: when the agent proposes a pose for the next plane, the environment calls check(), reads back CheckResult, and turns it into a reward. This is the 'proposer -> verifier(reward) loop' from the brief: the learned model never decides whether a layout is valid; collisions.check() does, and it is ground truth. The agent only learns to PROPOSE poses the verifier will accept.

### State vs observation

**In plain terms.** The STATE is the complete true situation of the world. The OBSERVATION is what the agent actually gets to see of it. They are often not identical: the agent may see a compressed or partial view. Good RL design makes the observation rich enough that the best next action is determinable from it (this is the Markov property: the observation should summarize everything relevant about the past, so the agent never needs to remember earlier turns to act well). If the observation hides something the optimal action depends on, no amount of training fully fixes it.

**In hangarfit.** The true STATE is the exact continuous pose (x, y, heading) of every plane placed so far, the full set of planes still waiting, and the exact keep-out geometry. The OBSERVATION fed to the network is a structured encoding of that: the variable plane-set goes through the permutation-invariant Set-Transformer (so the order you list planes in does not change the answer), and the hangar keep-out mask (L-shape + notch + bay + apron) goes through the small CNN as a fixed context image. Note the deliberate design choice in the brief: the CNN encodes the mask ONLY as context, NOT as the coordinate frame the pose is emitted in. The observation is Markov here because filling-order matters only through which planes remain and where placed ones sit, and both are in the observation.

### Action: what the agent actually chooses

**In plain terms.** The action is the decision the agent emits each turn. Actions can be discrete (pick one of N options) or continuous (emit real numbers). Continuous, high-dimensional action spaces are much harder to learn in than small discrete ones, because the agent must hit a good point in an infinite space rather than pick the best of a short list, and tiny coordinate errors can flip an outcome from good to catastrophic. A common engineering move is to make the action coarse and discrete, then clean it up deterministically afterward.

**In hangarfit.** The raw action is a pose in SE(2): a continuous (x, y, heading) for one plane. The recommended design deliberately does NOT have the network emit raw continuous SE(2). Instead the action is split: a SELECTION head picks WHICH waiting plane to place next (learning the placement order), and a COARSE pose head picks a discrete pocket plus a heading-bin. Then a DETERMINISTIC refiner snaps that coarse pose to exact continuous (x, y, heading) under collisions.check(). So the learned action is 'which plane, roughly where, roughly what heading' and the hard continuous precision is offloaded to a non-learned snap. This is the standard 'coarse-discrete action + deterministic refinement' trick applied to the SE(2) difficulty.

### Reward: the verifier's penetration-depth readout

**In plain terms.** Reward is the scalar score the environment returns after each action. It is the ONLY definition of 'good' the agent ever sees: whatever you reward, that is literally what the agent will learn to maximize, including unintended loopholes. A central distinction is SPARSE vs DENSE reward. A sparse reward is mostly zero and only spikes at success, so the agent gets almost no guidance about whether it is getting warmer. A dense reward changes smoothly with how close the agent is, so every step gives directional feedback. Dense reward is far easier to learn from, but it is also where you can accidentally reward the wrong proxy.

**In hangarfit.** hangarfit has BOTH signals built into one verifier call. CheckResult.valid is the SPARSE, binary truth: zero conflicts = success, anything else = failure. CheckResult.total_penetration_m2 is the DENSE readout: the summed shapely intersection-area across overlapping parts, which shrinks smoothly as planes stop overlapping. This is the project's escape hatch from the 'binary-reward desert': a freshly-initialized agent almost never samples a fully valid dense layout, so a purely binary reward would be near-zero everywhere with no gradient to climb. total_penetration_m2 gives a slope to descend even while every layout is still invalid. The discipline (from the research): keep valid as the truth that decides acceptance, use penetration depth only as an annealed auxiliary that helps the agent find its way to valid.

### Episode and step: one plane vs filling the hangar

**In plain terms.** A STEP is one action-and-feedback cycle. An EPISODE is one complete attempt from start to terminal end, made of many steps. The agent learns across thousands of episodes. The length of an episode (the 'horizon') matters: short horizons are easier because rewards and consequences are close together; long horizons make it hard to tell which early action caused a late failure (the credit-assignment problem).

**In hangarfit.** A STEP = placing one plane (select it, choose its coarse pose, refine, score). An EPISODE = filling the hangar with the whole plane-set, up to 10 planes, then ending. The horizon is therefore short (N <= 10), which is a genuine advantage for this problem and one reason a sequential MDP is attractive. But the credit-assignment trap is real and is exactly RR-MC's failure mode: a perfect placement of plane 3 can doom plane 8 (no valid pocket left, or the door route is now blocked). The episode ends either when all planes are placed (and the final layout goes to the verifier) or when a placement leaves no feasible continuation.

### Policy: the strategy the network embodies

**In plain terms.** The POLICY is the agent's strategy: a function from observation to action, usually written as a probability distribution over actions given what it sees. A STOCHASTIC policy outputs probabilities (it samples an action), which is what you want during learning because randomness is how the agent discovers new behavior. Training in RL literally means adjusting the policy's parameters (the neural-network weights) so it puts more probability on actions that led to higher reward. The policy IS what gets shipped; everything else is scaffolding to improve it.

**In hangarfit.** The policy is the trained weights of the proposer network. Given the current observation (planes remaining via the Set-Transformer, hangar mask via the CNN) it outputs a distribution over 'which plane next' and over coarse pose bins, with the feasibility head soft-masking poses it has learned are hopeless. At inference time under solve --backend learned, you sample (or take the most-likely) plane-and-pose, refine, verify, repeat. The shipped artifact is these weights (signed Release assets, run via onnxruntime under the [learned-infer] extra). The deterministic verifier and refiner are NOT part of the policy; they are the fixed environment the policy was trained against.

### Value function: judging a position before the game ends

**In plain terms.** A VALUE FUNCTION estimates expected future reward from a given situation (or situation-action pair), under the current policy. Where reward is the immediate score, value is the long-run outlook: 'from here, how well is this likely to end?' Value functions are what let an agent prefer an action whose immediate reward is mediocre but which sets up a great finish. Most strong RL algorithms learn a value estimate alongside the policy and use it to judge whether an action did better or worse than expected, rather than judging against raw reward (which is noisy). The gap 'actual outcome minus value estimate' is called the ADVANTAGE, and it is the cleaner training signal.

**In hangarfit.** A value head would estimate 'given these 4 planes are placed like this and these 6 remain, how likely is this to finish as a fully valid, tow-routable fill?' That is precisely the foresight RR-MC lacks. It lets the agent prefer a slightly-tighter placement of plane 4 because the value head has learned that configuration leaves room for the awkward 18 m Scheibe later. The advantage (did this placement beat what we expected from this state) is what PPO uses to push the policy, and it directly attacks the irreversibility trap the brief calls out for greedy sequential placement.

### Exploration vs exploitation

**In plain terms.** EXPLOITATION means doing what currently looks best; EXPLORATION means trying something uncertain to learn whether it is even better. An agent that only exploits gets stuck repeating the first decent strategy it found; an agent that only explores never converges. All of RL is managing this trade-off, usually by keeping the policy a bit random (and annealing that randomness down over training). Too little exploration is the silent killer: the agent locks onto a mediocre habit and the reward curve flatlines while better solutions sit undiscovered.

**In hangarfit.** This is the crux of why from-scratch RL is risky here. The valid dense layouts are oblique, z-disjoint nested arrangements that a random policy will essentially never stumble into, so naive exploration spends its whole budget in invalid space and the agent never sees a success to reinforce. That is the 'rare-valid-configs' hardness. The mitigations in the brief are all about making exploration land somewhere useful: warm-start from a teacher so the policy begins near valid regions, use the dense penetration reward so even invalid attempts give directional feedback, and use curriculum so early episodes are easy enough that successes actually occur and can be reinforced.

### On-policy vs off-policy

**In plain terms.** ON-POLICY algorithms learn only from data generated by the current version of the policy; once the policy updates, old experience is stale and discarded. They are simpler and more stable but sample-hungry. OFF-POLICY algorithms can learn from data generated by older policies, or by an entirely different actor (including a human or a classical solver), and so reuse experience and are more sample-efficient, but they are trickier to keep stable. The practical upshot: off-policy lets you learn from a replay buffer and from demonstrations; on-policy makes you keep generating fresh rollouts.

**In hangarfit.** PPO (the recommended fine-tuner) is ON-policy: each round it rolls out current-policy hangar-fills, scores them with the verifier, updates, and throws the rollouts away. That is fine because rollouts are cheap-ish (short N<=10 episodes, fast deterministic verifier). The warm-start phase is effectively OFF-policy in spirit: behavior cloning learns from the slow teacher nester's layouts, which the current policy did not generate. The split design (off-policy-style imitation first, then on-policy PPO) is exactly because pure on-policy from scratch cannot find the rare successes to learn from.

### PPO and why it is the usual default

**In plain terms.** PPO (Proximal Policy Optimization) is the workhorse default for continuous-control and structured-output RL. Its core idea is humble: when you update the policy toward higher-advantage actions, do not let any single update move the policy too far from the version that collected the data. It enforces this with a 'clipped' objective that ignores update pressure once the policy has shifted past a small trust region. The payoff is stability: RL training is notoriously prone to a catastrophic update that destroys a working policy, and PPO's small-step guarantee makes it forgiving and reproducible with modest tuning. It is the default not because it is the most sample-efficient but because it most reliably does not blow up, which matters enormously for a hobbyist on one GPU.

**In hangarfit.** PPO is the recommended fine-tune stage after the behavior-cloning warm-start, and the brief adds a KL-regularization caveat: keep the fine-tuned policy close to the cloned teacher so PPO does not wander off and forget the good packing instincts it inherited. For a single-GPU hobbyist this stability is the whole point; an exotic algorithm that needs careful tuning and many seeds is the wrong tool. PPO turns the verifier's per-step penetration reward and end-of-episode validity into small, safe policy nudges over thousands of hangar-fill rollouts.

### Potential-based reward shaping (adding dense hints without corrupting the goal)

**In plain terms.** Reward SHAPING means adding extra reward terms to guide learning faster than the sparse true reward would. The danger is that a careless shaping term changes WHAT the optimal policy is, so the agent optimizes your proxy instead of your real goal. The 1999 Ng-Harada-Russell result gives the safe recipe: if your extra reward is the DIFFERENCE of a 'potential' function between consecutive states (potential of new state minus potential of old state), then it provably does NOT change which policy is optimal. It only changes how fast the agent learns. So you get the guidance of a dense signal with a guarantee that you have not redefined success.

**In hangarfit.** Define a potential like 'minus the total_penetration_m2 of the current partial layout' (or a normalized version). The shaping reward each step is the IMPROVEMENT in that potential, i.e. how much overlap area you removed by this placement. Because it is a potential difference, the brief's optimal valid+routable layout stays optimal; penetration depth merely accelerates the agent toward it. This is the disciplined way to use total_penetration_m2: as a potential-based shaping term, with CheckResult.valid remaining the untouched terminal truth. It directly answers the brief's nuance that graded reward is helpful-but-not-magic and must be added safely.

### Reward hacking

**In plain terms.** Reward hacking is when the agent maximizes the reward you literally wrote while violating the goal you meant. It is not the agent cheating; it is the agent doing its job too well against an imperfect score. The usual cause is rewarding a proxy that is correlated with success but separable from it under enough optimization pressure. The defenses are: keep a hard, un-gameable truth term that actually defines success; treat all shaping/proxy terms as auxiliaries you can turn down (anneal) once learning is underway; and re-verify final outputs against the real criterion rather than the proxy.

**In hangarfit.** Concrete hazard here: if you reward 'low penetration area' too heavily, the agent can learn to park planes far apart in a sparse, easily-valid layout that scores great on the proxy but wastes the hangar and never achieves the dense packing you actually need; or it could exploit a soft term to push planes toward the door in ways the tow planner cannot route. The defenses map cleanly: collisions.check().valid is the un-gameable truth (a layout either has zero conflicts or it does not), penetration depth is annealed down as an auxiliary, and EVERY proposed layout is re-run through the deterministic verifier (and plan_fill for routability) before it is ever accepted or shipped. The verifier-in-the-loop is the structural guard against hacking.

### Behavior cloning / imitation as a warm-start

**In plain terms.** Behavior cloning (BC) is plain supervised learning on demonstrations: collect (situation, expert-action) pairs and train the policy to copy the expert, no reward needed. It is fast and stable but it can only imitate; it does not discover anything the demonstrations never showed, and it drifts when it reaches situations the expert never visited. The standard recipe is BC as a WARM-START: clone an expert to get the policy into a competent region, then switch to RL fine-tuning to push past the expert and handle states the demonstrations missed. Cloning many DIVERSE expert solutions (not one canonical answer per case) keeps the policy from collapsing onto a single mode.

**In hangarfit.** This is the single highest-leverage and highest-RISK piece. The brief is blunt: the BC teacher must be an EXTENDED slow classical nester that actually emits dense, oblique, z-nested packings, and that teacher DOES NOT EXIST YET. GFPack++ is cited as the proof the recipe works (it distilled ~100k teacher layouts). For hangarfit: build the slow nester first, have it produce many diverse valid layouts per scenario, behavior-clone the proposer on them to escape the cold-start desert, then PPO-fine-tune (KL-regularized) against the verifier reward to handle cases the teacher missed and to learn routability-awareness the teacher never had. The whole feasibility of the hybrid plan hinges on that teacher existing, which is why a time-boxed spike to build it is the proposed GO/NO-GO gate.

### Curriculum learning

**In plain terms.** Curriculum learning trains on easy instances first and hardens the task as the agent improves, the way you would teach a person. It works because early successes are what create the reward signal an agent needs to bootstrap; throw the hardest problem at a blank policy and it may never succeed once, so it never learns. A curriculum manufactures a smooth gradient of difficulty so there is always a next-rung challenge the current policy can occasionally beat.

**In hangarfit.** Start episodes with 2-3 planes in a roomy hangar where valid layouts are easy to hit, so the agent racks up real successes and the policy learns basic 'do not overlap, do not leave bounds.' Then ramp toward the brief's hard case: more planes, tighter clearances, the L-shape and back-right notch, the maintenance bay occupied, the single-door routability constraint, up to the intractable 8-plane Herrenteich fill. This directly counters the rare-valid-configs problem: the agent meets dense oblique nesting only after it can already pack loosely, instead of facing it cold.

### Why continuous SE(2) actions plus rare-valid configs make this hard, and what GFlowNets add

**In plain terms.** Two compounding difficulties. First, continuous SE(2) actions: the agent must choose real-valued position and rotation, an infinite action space where a tiny error flips a layout from valid to overlapping, so there is no margin and gradients are jagged. Second, rare valid configurations: the set of fully valid dense layouts is a vanishingly thin sliver of all possible layouts, so random exploration almost never lands on success and the binary reward is near-zero everywhere, giving nothing to climb. Together they make naive from-scratch RL a search for a needle in a high-dimensional haystack with a near-flat compass. Separately, if you want not just ONE solution but MANY genuinely different ones, standard reward-maximizing RL fights you: it collapses onto the single highest-reward mode. GFlowNets are an alternative training objective that learns to SAMPLE solutions with probability proportional to their reward, so it naturally produces a diverse set of distinct high-reward solutions rather than one.

**In hangarfit.** The SE(2) hardness is why the design refuses to emit raw continuous poses and instead uses coarse discrete pockets/heading-bins plus a deterministic refiner. The rare-valid hardness is why the design layers all three lifelines (dense penetration reward, teacher warm-start, curriculum) rather than betting on exploration. GFlowNets are relevant because hangarfit already VALUES diversity: ADR-0004's diversity metric and ADR-0008's spread post-pass both exist to surface multiple distinct arrangements for a human to eyeball. If the goal is 'show the club three genuinely different valid ways to park tonight' rather than one, a GFlowNet-style sampler over layouts is the principled fit; a plain PPO policy would tend to keep proposing minor variants of its single favorite.

### Common misconceptions

- Reward is NOT a loss function and you do not differentiate through it. The verifier (collisions.check) is non-differentiable and that is fine: RL never needs gradients of the reward. It uses the reward as a score and adjusts the policy's weights via the policy-gradient (PPO), so a discrete, geometric, deterministic verifier is a perfectly valid reward source.

- Dense reward (total_penetration_m2) is NOT the definition of success and must not replace the binary check. valid (zero conflicts) is the truth; penetration depth is an annealed auxiliary/potential-shaping term to find the way there. Treating the proxy as the goal is exactly how reward hacking happens.

- A short horizon (N<=10 planes) does NOT make this easy. The hardness is the rare-valid continuous geometry and the irreversibility/credit-assignment trap (placing plane 3 can doom plane 8), not episode length. Greedy one-plane-at-a-time placement is precisely what RR-MC already fails at.

- Behavior cloning is NOT optional polish in the recommended plan; it is the load-bearing escape from the cold-start reward desert, and it is gated on a teacher nester that emits dense oblique z-nested packings, which DOES NOT YET EXIST. No teacher, no warm-start, and pure from-scratch RL is the skeptical-leaning option.

- The learned model does NOT decide validity. It only PROPOSES; collisions.check() accepts or rejects and stays strictly deterministic (the ADR amends ADR-0003's scope so the verifier's determinism contract is untouched and only the proposer gets a weaker, documented contract).

- PPO being the default does NOT mean it is the most sample-efficient or most powerful. It is the default because it is stable and forgiving, which is what matters most for a single-GPU hobbyist; exotic algorithms that need heavy tuning are the wrong trade here.

- More exploration is NOT automatically better. Unguided exploration in this space burns the whole budget in invalid layouts and never sees a success to reinforce. The fixes are to make exploration land usefully (warm-start, dense reward, curriculum), not to crank up randomness.

- Reward shaping is NOT free to design by intuition. An arbitrary 'helpful' bonus can silently change the optimal policy. Only a potential-based shaping term (a difference of a state potential, per Ng-Harada-Russell 1999) is provably goal-preserving; everything else risks redefining success.

- GFlowNets are NOT just a fancier RL algorithm for getting a higher single score. Their distinct value is sampling MANY diverse high-reward solutions, which is only worth the complexity if the project actually wants diversity (it does: ADR-0004 / ADR-0008). For a single best layout, they are not the point.

- The CNN does NOT define the coordinate frame the pose is output in. In the recommended design it encodes the hangar keep-out mask as CONTEXT only; poses come from the coarse pose head and are snapped by the deterministic refiner. Conflating 'CNN sees the hangar' with 'CNN emits the pose grid' is a design error the brief explicitly warns against.

---

## CNNs, Set-Transformers, and the Neural Architectures Behind hangarfit's Learned Backend: A Grounded Primer for a Non-ML Engineer

### The shape of the problem, and why we reach for learning at all

**In plain terms.** Before any neural network jargon, fix what we are actually computing. We have up to 10 distinct objects (aircraft, each with its own wingspan, length, gear type, and how it can move) and we must choose for each a continuous pose: an (x, y) position on the floor and a heading angle. That is the OUTPUT. We also have a fixed scene: the hangar floor with regions you cannot use. A neural network is, at bottom, a very large parameterized function fitted to data: you feed it numbers, it emits numbers, and during 'training' you nudge millions of internal knobs (weights) so its outputs match what you want. We are reaching for it because the existing deterministic solver (RR-MC) provably CANNOT find the dense, oblique, vertically-stacked ('z-disjoint nested') arrangements a tight real hangar needs — the 8-plane Herrenteich case is intractable for it. A learned model that has SEEN many good layouts can propose one of those dense arrangements in a single fast forward pass, where blind search would never stumble onto it. Crucially the network only PROPOSES; the existing deterministic checker still decides validity. So the network never has to be correct, only good-enough-often to feed a cheap verifier.

**In hangarfit.** Output = a list of (x, y, heading) poses, one per plane, that becomes a Layout. Input = the plane set + the hangar geometry from data/hangar.yaml or examples/herrenteich/hangar.yaml. The ground-truth gate is collisions.check(layout) -> CheckResult (validity + graded penetration depth) and towplanner.plan_fill (routability + first-conflict/budget signal). These two functions are the spine of the whole design: the network is the proposer, these are the verifier. This mirrors the framing already baked into spikes #331/#332: 'CNN proposes, collisions.py stays the ground-truth oracle.'

### What a convolution and a feature map actually are

**In plain terms.** A convolution is a small sliding stencil. Picture the hangar floor drawn as a grid of cells, each cell holding a number — say 1 if that square metre is blocked, 0 if it is free. A convolution takes a tiny weight pattern (a 'kernel', e.g. 3x3) and slides it over every position in the grid. At each position it multiplies the 9 underlying cells by the 9 kernel weights and sums them, producing one output number. Sweep the whole grid and you get a new grid of numbers — that output grid is called a FEATURE MAP. One kernel might be tuned (by training) to fire on 'a wall edge running north-south'; another on 'an inside corner'; another on 'a narrow gap about one wingspan wide'. Stack many kernels and you get many feature maps, each a different learned filter. Stack convolutions in LAYERS — feeding one layer's feature maps into the next — and early layers detect edges and corners while later layers compose those into larger concepts like 'pocket deep enough for a tail-first plane' or 'the throat near the door'. The key economy: the SAME small kernel is reused at every grid position, so a CNN has very few weights relative to the grid size and learns local geometric patterns regardless of where they appear.

**In hangarfit.** The grid is the hangar's occupancy / keep-out mask: rasterize length_m x width_m into cells, marking the maintenance bay rectangle, the back-right office notch (the L-shape from ADR-0018), the area outside the door, and the staging apron (y<0) as blocked, and free floor as open. A convolution learns to recognize 'a notch corner here', 'a door throat this wide', 'a deep narrow bay-flanking aisle' — exactly the structural features that determine where a dense nest can go. Each feature map is a transformed view of that mask highlighting one kind of geometric affordance.

### Why CNNs suit grid/image data — and what 'translation-equivariance' buys us

**In plain terms.** Grid data has a special property: meaning is LOCAL and POSITION-RELATIVE. A corner is a corner whether it sits top-left or bottom-right of the image; a gap that fits a wing is just as useful at x=3 as at x=12. A CNN bakes this assumption in. Because the same kernel slides everywhere, if you shift the whole input one cell to the right, every feature map shifts one cell to the right too, unchanged in content. That property is TRANSLATION-EQUIVARIANCE: 'move the input, the detected features move with it, identically.' (Not to be confused with INVARIANCE, where the output stays put — that comes later, after pooling, when you only care THAT a feature exists, not where.) This is why CNNs dominate images: they don't need to relearn 'what an edge looks like' separately for every pixel location, so they need far less data and generalize across positions. A fully-connected network with no such structure would treat every cell as unrelated and would need astronomically more data to discover that geometry repeats.

**In hangarfit.** A 'one-wingspan-wide gap' or 'an inside corner of the L' means the same thing wherever it occurs on the hangar floor. Translation-equivariance lets the CNN learn that detector once and apply it across the whole mask — and lets it transfer between the synthetic data/hangar.yaml and the real Herrenteich L-shape, which differ in size and where the notch sits but share the same KINDS of local features.

### The trap: why a CNN is the WRONG tool to OUTPUT a continuous pose

**In plain terms.** Here is the failure that sank the earlier CNN spike, stated plainly. A CNN's native output is a grid — one number (or a few) per cell. If you try to make the network's OUTPUT be the pose directly, you are forced to discretize: 'plane goes in cell (i, j) at heading-bin k.' The pose you can emit is then capped by the grid resolution. A 25m x 22m hangar at 0.25m cells is 100x88 cells; headings binned at 15 degrees give 24 bins. But the packing we need is DENSE and OBLIQUE — clearances are 0.3m horizontal, 0.2m vertical, and valid nests depend on getting a heading right to a fraction of a degree so a wingtip clears a neighbour's fin by centimetres. Quantize the pose and you either (a) make the grid so fine that the output space explodes and training collapses, or (b) keep it coarse and your 'proposed' pose is never quite valid — it overlaps by 5cm everywhere. A CNN is excellent at PERCEIVING the grid; it is structurally bad at EMITTING a precise continuous pose, because its output frame IS the grid. That mismatch — using the perception frame as the answer frame — is the trap. The discrete spikes #331/#332 found exactly this: layout is a continuous CSP a pure CNN handles poorly.

**In hangarfit.** The fix the recommended design uses: the CNN encodes the keep-out mask ONLY as CONTEXT (a perception of where structure is), and NEVER as the pose output frame. Pose comes from separate heads that emit a COARSE pocket+heading-bin, which a DETERMINISTIC refiner then snaps to exact continuous (x, y, heading) under collisions.check. The grid is for seeing, not for answering. This is the single most important architectural correction versus the naive 'CNN draws the layout' idea.

### Encoders and embeddings: turning a plane into a vector the network can reason over

**In plain terms.** An ENCODER is any sub-network that takes raw, awkward input and produces a fixed-size list of numbers — a VECTOR — that captures its meaningful content. That output vector is an EMBEDDING. The point of an embedding is that 'similar things land near each other' in this number-space and that downstream layers can do arithmetic on them. For a single aircraft you feed in its raw attributes (wingspan, length, height, wing-mount height so you know its vertical layer, gear/movement type, strut geometry) and the encoder emits, say, a 64-number embedding. Two high-wing taildraggers of similar span get similar embeddings; a low-wing plane lands somewhere distinct because its wing occupies a different vertical band. The network never sees 'a Cessna'; it sees a point in embedding-space, and it learns which regions of that space pack which ways. The CNN above is ALSO an encoder — it encodes the whole hangar mask into a context embedding. So we have two encoders: one for each plane, one for the scene.

**In hangarfit.** Per-plane embedding is built from fleet.yaml fields (span, length, height, wing type for the z-layer, gear, movement mode incl. the #599 tow_pivotable / cart_eligible flags, struts). The low-wing Fuji FA-200's clear-low-lane requirement and the Scheibe's broadside-only constraint are exactly the kind of fact a good embedding must capture so the selection/pose heads can treat them differently. The hangar CNN produces a single scene-context embedding summarizing the L-shape, notch, bay, door throat, and apron.

### Attention and the Set-Transformer: reasoning over planes that interact

**In plain terms.** Embedding each plane in isolation is not enough, because planes INTERACT: a high-winger can overhang a low-winger's tailplane but not its cockpit; a wingtip nests over a neighbour only if their vertical layers are disjoint. The network needs each plane's representation to depend on the OTHER planes present. ATTENTION is the mechanism for this. For each plane, attention asks 'which other planes matter to me, and how much?', computes a weighted blend of their embeddings, and folds that into an updated embedding. Concretely each item emits a 'query' (what am I looking for), and every item emits a 'key' (what do I offer) and a 'value' (my content); the query-key match gives weights, and the weighted sum of values updates the item. Do this for all items at once, stack a few rounds, and every plane's embedding now reflects the whole set — 'I am a wide high-winger, and there are two narrow planes I could nest over, and a low-winger whose wing-lane I must avoid.' A SET-TRANSFORMER is just a Transformer (a stack of attention layers) arranged so it operates on an unordered SET rather than an ordered sequence — no position numbers are attached to the items.

**In hangarfit.** Attention is how the model learns the parts-model interactions (ADR-0001/0023): which planes can share XY because their z-layers don't overlap, which wingtip-over-tailplane nests are legal, which planes block each other's tow path through the single door. The Set-Transformer consumes the per-plane embeddings and returns interaction-aware embeddings the selection and pose heads then act on.

### Why permutation-invariance matters for a variable, heterogeneous plane-set

**In plain terms.** The plane-set is a SET, not a list: 'the Cessna and the Fuji and the Scheibe' is the same problem as 'the Fuji and the Scheibe and the Cessna' — order carries no meaning. And it is VARIABLE: sometimes 5 planes, sometimes 8, sometimes 10. A naive network with fixed input slots would (a) give different answers if you shuffled the input, which is absurd, and (b) break entirely when the count changes. PERMUTATION-INVARIANCE means: shuffle the inputs and the network's understanding is unchanged. Attention delivers this for free — because no position indices are attached, swapping two planes just swaps their outputs, it does not change the computation. (Compare a CNN, which is the OPPOSITE: position is everything, neighbours are fixed. That is right for the fixed-geometry floor mask and wrong for the order-free plane set — which is precisely why the two get DIFFERENT architectures.) Handling a variable count is equally natural: attention sums/pools over however many items are present.

**In hangarfit.** This is the exact gap the prior spike flagged: 'pure CNN handles a variable plane-set poorly -> GNN/transformer fits better.' A 5-plane example.yaml fill and an 8-plane Herrenteich fill must run through the SAME network. Permutation-invariance also means the network can't cheat by memorizing 'plane #3 always goes back-left' — it must reason from each plane's actual properties, which is what we want.

### Pointer networks and the selection head: learning WHICH plane to place next

**In plain terms.** The research is blunt that naive greedy 'place plane 1, then 2, then 3 in arbitrary order' is exactly what RR-MC already fails at — pick a bad order and you paint yourself into a corner (the irreversibility / door-deadlock trap). So the ORDER itself must be learned. A POINTER NETWORK is the tool: instead of outputting a fixed label, it outputs a POINTER into its own variable-length input — 'of the planes still unplaced, THIS one goes next.' Mechanically it reuses attention: it scores each remaining plane against the current partial-layout state and points at the highest-scoring one. We call this the SELECTION HEAD ('head' = a small output sub-network bolted onto the shared embeddings). It runs sequentially: select a plane, place it, update the state, select the next — but the SEQUENCE is chosen by the model, not fixed. This is what turns brittle greedy placement into learned, deadlock-aware ordering.

**In hangarfit.** The selection head learns orders like the real Herrenteich operator's: deep tail-first planes first, the broadside Scheibe slid in where its high wing can later overhang the Fuji's low wing, nose-out planes last so they don't trap others at the single door. towplanner.plan_fill is already sequential and its first-conflict/budget signal tells the model when an order created an un-routable deadlock — training signal for the selection head.

### The coarse-pose head plus deterministic refine: the pattern that dodges the resolution ceiling

**In plain terms.** This is the design's answer to the CNN-output trap, generalized. Split pose into two stages with two different tools. STAGE 1 (learned, coarse): a pose head emits a DISCRETE, low-resolution intent — 'put this plane in pocket region P, roughly heading-bin H' — plus a FEASIBILITY head that soft-masks obviously-bad cells (no point proposing a pose half inside the notch). Discrete + coarse is something a network learns reliably; it is a perception/intent decision, not a millimetre decision. STAGE 2 (deterministic, exact): a classical refiner takes that coarse seed and SNAPS it to a precise continuous (x, y, heading) by local adjustment under collisions.check — slide and rotate within the pocket until penetration depth hits zero, or report failure. The network supplies the hard part (which pocket, roughly what angle, in a dense oblique nest); deterministic geometry supplies the precision the network can't. You get continuous poses WITHOUT asking the network to emit continuous poses.

**In hangarfit.** Refiner consumes collisions.check's GRADED penetration depth as a gradient-free local objective: nudge the pose to drive overlap to zero, exactly what a continuous optimizer needs and what the verifier already returns. The coarse pocket vocabulary can be derived from the hangar mask (door throat, bay-flanking aisles, deep back corners minus the L-notch). End result feeds collisions.check + plan_fill for the final accept/reject — the verifier stays the deterministic ground truth (ADR-0003 scope preserved).

### Diffusion models for layout: the GFPack++-style alternative, briefly

**In plain terms.** A DIFFUSION model generates by DENOISING. Training: take a known-good layout, progressively add random noise until the poses are pure static, and teach a network to reverse ONE noise step. Inference: start from random poses (static) and apply the learned denoiser many times; coherent structure emerges from noise, like a photo developing. Its appeal for packing is that it naturally produces DIVERSE, globally-coordinated arrangements in ONE shot — it places all planes jointly rather than one-at-a-time, sidestepping the ordering/irreversibility trap entirely. The catch is it needs a large corpus of good layouts to learn from, and it doesn't guarantee validity — you still verify-and-repair its output. GFPack++ (the SOTA continuous-rotation irregular packer the research cites) pointedly AVOIDS reinforcement learning and instead distills a slow classical nester into a learned generator; diffusion is the same spirit — learn the distribution of good packings, then sample. For hangarfit it is the alternative to the sequential selection+pose pipeline, not a complement; you would pick one as the proposer.

**In hangarfit.** Diffusion would be the 'one-shot proposer + verifier-repair' branch of the design, contrasted with the recommended sequential selection-head pipeline. It fits hangarfit's existing taste for DIVERSITY (ADR-0004 diversity metric, ADR-0008 spread) — diffusion samples many distinct valid nests. But it depends on the same missing precondition as the recommended path: an extended slow nester that emits dense oblique z-nested teacher layouts, which does not exist yet.

### Putting the three together: CNN vs Set-Transformer vs Diffusion for THIS task

**In plain terms.** These are not competitors for one job; in the recommended design two of them do DIFFERENT jobs and the third is a road-not-taken. The CNN's job is PERCEIVING THE FIXED FLOOR: it ingests the static, position-sensitive hangar mask and outputs a scene-context embedding. It is right here precisely because the floor is a grid where position and locality matter and translation-equivariance helps transfer between hangars. The Set-Transformer's job is REASONING OVER THE PLANES: it ingests the order-free, variable, heterogeneous plane-set and outputs interaction-aware embeddings, feeding the selection head (which plane next) and the coarse-pose head (roughly where/what angle). It is right here precisely because the plane-set is a permutation-invariant set the CNN handles poorly. Diffusion is the WHOLE-LAYOUT GENERATOR ALTERNATIVE: it would replace the sequential selection+pose pipeline with one-shot joint generation. You would ship EITHER (Set-Transformer sequential proposer) OR (diffusion one-shot proposer) — and in BOTH cases the CNN-as-mask-encoder and the deterministic refine+verify stages stay. The one-line rule: CNN = perceive the floor; Set-Transformer = reason over the planes; deterministic refiner+verifier = make it exact and true. Pick CNN for grids, attention for sets, never swap them.

**In hangarfit.** Recommended design wiring: CNN(hangar keep-out mask) -> scene-context embedding; Set-Transformer(per-plane embeddings + scene context) -> interaction-aware embeddings -> selection head (order) + coarse-pose head (pocket+bin) + feasibility head (soft-mask); deterministic refiner -> exact (x,y,heading) under collisions.check; verifier collisions.check + towplanner.plan_fill -> accept/reject. Diffusion is the documented alternative proposer if the sequential one underperforms.

### Training the thing: BC warm-start, then PPO fine-tune with a shaped verifier reward

**In plain terms.** Two ways to teach the proposer, used in sequence. BEHAVIOR CLONING (BC) is plain imitation: collect many good layouts from a slow teacher and train the network to reproduce them — supervised learning, stable, the reliable warm-start. REINFORCEMENT LEARNING (RL, here PPO) is learning by trial-and-reward: the network proposes, the verifier scores, and the network shifts toward higher-scoring proposals. The research bottom line is HYBRID: BC first (so the network can already SAMPLE roughly-valid dense layouts), THEN RL to fine-tune — because pure from-scratch RL faces a near-zero-gradient desert (random init almost never samples a valid dense layout, so reward is almost always zero and there is nothing to climb). The reward design matters: keep the BINARY verifier (valid / invalid) as the TRUTH, but add the GRADED penetration depth as a DENSE auxiliary so 'almost valid' scores higher than 'wildly overlapping' — that gradient is what hangarfit has and pure-binary problems lack. Use potential-based shaping (Ng-Harada-Russell 1999) so this shaping provably doesn't change the optimal policy, and anneal it away, so the network can't 'reward-hack' the proxy by gaming overlap-depth instead of achieving real validity.

**In hangarfit.** Teacher = an EXTENDED slow nester emitting dense oblique z-nested packings — the single biggest missing precondition, and the reason a time-boxed spike (build the teacher + an N<=5 supervised probe) is the recommended GO/NO-GO gate before the heavy epic. Dense reward channel = collisions.check's graded penetration depth + plan_fill's first-conflict/budget signal. Binary truth = collisions.check validity. Shipping = opt-in solve --backend learned, [learned-infer]/[train] extras, an ADR amending ADR-0003's scope (verifier stays strictly deterministic; learned proposer gets a weaker documented contract).

### ONNX and onnxruntime: shipping inference without dragging the training stack along

**In plain terms.** You train with a heavy framework (PyTorch) that carries a large dependency tree and assumes a capable machine. But end users running 'hangarfit solve' should not need that. ONNX (Open Neural Network Exchange) is a portable FILE FORMAT for a trained network: you export the finished model from PyTorch to a single .onnx file that describes the computation graph and the learned weights, framework-agnostic. ONNXRUNTIME is a small, fast library that LOADS that file and runs it (inference only — forward pass, no training) on CPU or GPU, with a tiny footprint compared to the training stack. So the split is: TRAIN with the [train] extra (torch, GPU); SHIP and RUN with the [learned-infer] extra (onnxruntime only). Users get fast amortized inference — one forward pass proposes a layout — without installing or understanding the training machinery. And because ONNX is just a frozen graph + weights, the weights file can be signed and attached as a release asset, so users get a verifiable, reproducible model.

**In hangarfit.** Matches the recommended packaging exactly: [learned-infer] (onnxruntime) for users, [train] (torch) for development; signed Release-asset weights (the project already does Sigstore keyless cosign on release artifacts via release.yml). The opt-in solve --backend learned path loads the .onnx weights and runs onnxruntime to propose, then hands off to the deterministic refiner + collisions.check / towplanner.plan_fill verifier — keeping the default backend and the determinism contract (ADR-0003) untouched for everyone who doesn't opt in.

### Common misconceptions

- 'A CNN can just draw the layout.' No. A CNN's output frame is the grid, so any pose it emits is capped by grid resolution and heading-bin granularity — fatal for dense oblique nests needing centimetre/sub-degree precision under 0.3m clearances. This is the exact trap that sank the prior CNN spike (#331/#332). The CNN encodes the floor mask as CONTEXT only; pose comes from coarse heads + a deterministic refiner.

- 'CNN, Set-Transformer, and Diffusion are three options for the same job, pick the best.' No. In the recommended design the CNN and Set-Transformer do DIFFERENT jobs simultaneously (CNN perceives the fixed floor grid; Set-Transformer reasons over the order-free plane set). Diffusion is an ALTERNATIVE PROPOSER replacing the sequential selection+pose pipeline — you'd ship it instead of, not alongside, the Set-Transformer pipeline; the CNN encoder and deterministic verifier stay either way.

- 'The network has to be correct.' No. It only PROPOSES; collisions.check and towplanner.plan_fill remain the deterministic ground truth that accepts or rejects. The network can be wrong often and still be useful as long as a fast verifier filters its proposals. The determinism contract (ADR-0003) is preserved because the verifier never becomes learned.

- 'Sequential one-plane-at-a-time placement is obviously the fix.' Mixed. Sequential is the natural seam (plan_fill is already sequential, horizon N<=10 is short), but NAIVE greedy ordering is exactly what RR-MC already fails at — the ordering/irreversibility/door-deadlock trap. The order itself must be LEARNED (pointer/selection head) with lookahead, or replaced by one-shot diffusion + repair.

- 'Translation-equivariance means the output doesn't change when you move the input.' That's INVARIANCE. Equivariance means the features MOVE WITH the input (shift the mask, the feature maps shift identically). Invariance (output unchanged) is a later property from pooling. CNNs are equivariant by construction; that's why they share one kernel across all positions.

- 'Permutation-invariance is a nice-to-have.' It's load-bearing here. The plane set is genuinely orderless and variable in count (5, 8, 10 planes). Without it, shuffling the input would change the answer (absurd) and changing the count would break the network. Attention/Set-Transformers provide it for free; CNNs and fixed-slot networks do not.

- 'Just use a denser grid to fix the CNN resolution problem.' Refining the grid makes the discrete output space explode combinatorially, which collapses training, while a coarse grid leaves proposals chronically overlapping by a few centimetres. Neither works — the real fix is to stop using the perception grid as the answer frame (coarse-intent head + deterministic continuous refine).

- 'Graded reward is necessary and sufficient — just optimize penetration depth.' Refuted as absolute. Shaped proxies can be REWARD-HACKED (the policy games overlap-depth instead of achieving validity). Keep the BINARY verifier as truth; use the graded margin only as a potential-based, annealed AUXILIARY (Ng-Harada-Russell 1999) that provably preserves the optimal policy.

- 'Pure from-scratch RL with the verifier reward will learn this.' Skeptical. Random init almost never samples a valid dense layout, so the reward is a near-zero-gradient desert on the hard 8-plane case. The supported path is HYBRID: BC warm-start from a slow teacher first, THEN RL fine-tune (KL-regularized) — the GFPack++ result is the smoking gun.

- 'We can start training tomorrow.' The single biggest precondition is missing: an EXTENDED slow nester that actually emits dense oblique z-nested teacher layouts does not exist yet. No teacher -> no BC warm-start -> back to the RL desert. That's why the recommended D2 answer is a time-boxed spike (build the teacher + an N<=5 supervised probe) as a GO/NO-GO gate before committing the heavy ML epic.

- 'ONNX is a neural network / a framework.' It's neither — it's a portable file FORMAT (frozen computation graph + weights). onnxruntime is the small inference-only library that runs it. Training still happens in PyTorch; ONNX is purely how the finished model ships so users get fast inference via [learned-infer] without the [train]/torch stack.

---
