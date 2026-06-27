# #844b Continuous-Traj-Opt Determinism-First Pre-Check (Gate 0) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a falsifiable Gate-0 verdict — GO / NO-GO(unshippable) / NO-GO(dominated) — on whether continuous trajectory optimization for the `fk9_mkii↔cessna_140` tow nook could *ever* ship under the ADR-0003 determinism contract, **before building any optimizer**.

**Architecture:** An investigation spike, not a code build. Three analysis steps (any one can NO-GO), each appending one section to a single spike doc; a small throwaway experiment confirms snap byte-stability. No optimizer, no `scipy`/CasADi, no new dependency, no `src/` change. The spec is `docs/superpowers/specs/2026-06-27-fk9-cessna-trajopt-precheck-design.md`.

**Tech Stack:** Python 3.12; reads `src/hangarfit/towplanner.py` (closed-form Reeds–Shepp solvers, `Move`/`MovesPlan`/`Segment`, `path_first_conflict`); `numpy` (transitively available); Markdown deliverable.

## Global Constraints

- **Branch:** `feature/844b-trajopt-determinism-precheck` (already created off `develop`; spec committed at `eca4fce`). All commits here. PR base `develop`, body `Refs #844` (umbrella issue; does not `Closes`).
- **ADR-0003 byte-identity is the bar.** The `MovesPlan` is the artifact that must be identical.
- **No optimizer. No `scipy`/CasADi. No new dependency. No `src/` change.** Gate 0 introduces no production code path.
- **`scipy` is NOT installed**; `numpy` (2.4.6) is available transitively. Do not add either.
- **Deliverable = a spike doc.** Any experiment script is throwaway/local by default; commit it only if it demonstrably earns being a reusable harness (it likely does not). Stay **out of `bench/`** (it runs in the required `bench correctness` CI check).
- **Do not re-tread the refuted heuristic class** (`#840`: heading-aware SE(2) field 108,991 vs 96,949 = 0.89×, intrinsic near-C\* A\* plateau). Gate 0 is about a *non-search* method's shippability, not search guidance.
- **Verdict must be one of the three bands**, stated with measured/quoted evidence — no hedge.
- Spike doc path: `docs/spikes/herrenteich-fk9-cessna-trajopt-determinism-precheck.md`.

---

### Task 1: Step 0.1 — Pin the exact determinism requirement (same- vs cross-machine)

The linchpin. The verdict's severity depends on whether ADR-0003 byte-identity is tested/required **same-machine** (achievable by a fixed-seed/fixed-BLAS optimizer) or **cross-machine** (raw iterative output almost certainly dead; only a discrete-word reduction survives).

**Files:**
- Read: `docs/adr/0003-rr-mc-solver-algorithm.md` (the determinism contract + any #267/#544 amendments)
- Read: `.claude/agents/determinism-guard.md` (locate first: `ls .claude/agents/ | grep -i determ`) — how the guard actually *tests* byte-identity
- Read: `tests/test_solver_canaries.py` (the double-solve canary mechanism — one process/machine twice, or cross-machine?)
- Read: `CLAUDE.md` determinism passages (`grep -n "byte-identical\|cross-machine\|determinism" CLAUDE.md`)
- Create/append: `docs/spikes/herrenteich-fk9-cessna-trajopt-determinism-precheck.md` (new file; the spike-doc deliverable)

**Interfaces:**
- Produces: a written **"Step 0.1 — determinism requirement"** section stating the verdict `same-machine` | `cross-machine` | `ambiguous`, with the exact quoted contract language and the canary's tested scope. Task 4 consumes this to choose the NO-GO severity.

- [ ] **Step 1: Read the contract and its test.** Read ADR-0003, the determinism-guard agent definition, and `tests/test_solver_canaries.py`. Identify (a) the *stated* contract wording and (b) what the canary *actually* runs — same process/machine twice, or a cross-machine claim.

- [ ] **Step 2: Resolve the verdict.** Decide whether byte-identity is required/tested **same-machine** or **cross-machine**. If the ADR *claims* cross-machine but the canary only *tests* same-machine, record that gap explicitly (it materially changes the gate).

- [ ] **Step 3: Write the spike-doc header + Step 0.1 section.** Create `docs/spikes/herrenteich-fk9-cessna-trajopt-determinism-precheck.md` with a title, a one-paragraph context pointer to the spec and `#844`, and a **"Step 0.1"** section: the verdict, the exact quotes (with `file:line`), and one sentence on what it implies for the gate.

- [ ] **Step 4: Verify the section is evidence-backed.** Confirm the section names a concrete verdict and cites at least the ADR clause and the canary test mechanism by `file:line`. No "TBD".

- [ ] **Step 5: Commit.**

```bash
git add docs/spikes/herrenteich-fk9-cessna-trajopt-determinism-precheck.md
git commit -m "docs(844): Gate-0 Step 0.1 — pin ADR-0003 same-vs-cross-machine determinism requirement

Refs #844"
```

---

### Task 2: Step 0.2 — Characterize today's deterministic seam

Establish what is byte-identity-deterministic in the *current* pipeline and exactly where an optimizer's output would have to enter to become a `MovesPlan`. This is also where the executor records the **exact API signatures** Task 3's experiment will call.

**Files:**
- Read: `src/hangarfit/towplanner.py` — `Segment` (`:87`), `Move` (`:236`), `MovesPlan` (`:262`), `Pose` (`:77`); the closed-form Reeds–Shepp solvers `_lsl`/`_rsr`/`_lsr`/`_rsl`/`_rlr`/`_lrl` (`:326-389`) and the ULP-tie comment (`:389`); `plan_dubins` (`:431`), `plan_reeds_shepp`, the cart (`r==0`) planner (`:465`); `path_first_conflict` (`:1306`); `MovesPlan.sample` (`:197`)
- Append: `docs/spikes/herrenteich-fk9-cessna-trajopt-determinism-precheck.md`

**Interfaces:**
- Consumes: nothing (independent read).
- Produces: a **"Step 0.2 — deterministic seam"** section, plus a recorded **API block** giving the *exact* signatures of `plan_reeds_shepp(...)`, `path_first_conflict(...)`, the `Pose`/`Segment`/`MovesPlan` constructors, and `MovesPlan.sample(...)`. Task 3's script consumes these signatures verbatim.

- [ ] **Step 1: Trace the emit path.** Read how `plan_path`/`plan_reeds_shepp` build the final `MovesPlan` from closed-form arc parameters, and how `path_first_conflict` validates it. Note whether any float comes from an *iterative* computation today (it should not — closed-form only).

- [ ] **Step 2: Assess closed-form determinism honestly.** Record whether the closed-form RS path uses transcendentals (`math.sin/cos/atan2`) whose results *can* differ across libm builds — this is what makes the Step 0.1 same-vs-cross distinction bite even for today's baseline. State whether the current baseline is robustly cross-machine or only same-machine.

- [ ] **Step 3: Name the seam.** Write the exact insertion point where an optimizer's output would enter the pipeline to become a `MovesPlan`, and what would have to be deterministic there.

- [ ] **Step 4: Record the API block.** Copy the *actual* current signatures of `plan_reeds_shepp`, `path_first_conflict`, `Pose`, `Segment`, `MovesPlan`, and `MovesPlan.sample` into the doc (these feed Task 3).

- [ ] **Step 5: Verify.** Confirm the section states (a) the deterministic seam with `file:line`, (b) an honest closed-form cross-machine assessment, and (c) the API block has real signatures (no placeholders).

- [ ] **Step 6: Commit.**

```bash
git add docs/spikes/herrenteich-fk9-cessna-trajopt-determinism-precheck.md
git commit -m "docs(844): Gate-0 Step 0.2 — characterize the closed-form Reeds-Shepp deterministic seam

Refs #844"
```

---

### Task 3: Step 0.3 — The reduction / snap experiment (throwaway script)

Test the load-bearing property empirically: can a continuous own-gear (R=0) "parallel-park" trajectory be **snapped to a canonical `MovesPlan`** that is (a) byte-identical across repeated construction and (b) accepted by `path_first_conflict` — and is the snap's determinism contained well enough that small continuous variations would map to the **same** discrete plan (the discrete-word-reduction escape)?

**Key cost-saver:** the byte-stability/validity property is a property of the *representation and snap*, not of the 39-min witness. Use a **fabricated representative** own-gear parallel-park trajectory (a short hand-specified pose sequence with a lateral offset at fixed heading). Re-running the real 0.25 m/10° witness (~39 min) is a **fallback only** if the representative test is inconclusive.

**Files:**
- Create (throwaway, local — NOT committed by default): `/tmp/claude-1000/-home-pkuhn-hangarfit/2395e148-c5e7-481e-93fe-f1dd0e1ef72d/scratchpad/gate0_snap_probe.py`
- Read (for the witness fallback only): `bench/se2_heuristic_probe.py` (does it already capture/cache a witness pose sequence? `grep -n "0.25\|witness\|0\.10\|pose\|sample" bench/se2_heuristic_probe.py`)
- Append: `docs/spikes/herrenteich-fk9-cessna-trajopt-determinism-precheck.md`

**Interfaces:**
- Consumes: the API block recorded in Task 2 (exact `plan_reeds_shepp` / `path_first_conflict` / `Pose` / `MovesPlan` signatures — adjust the script's calls to match them verbatim).
- Produces: a **"Step 0.3 — reduction/snap experiment"** section with the measured byte-diff and validity results.

- [ ] **Step 1: Check for an existing captured witness (cheap first).** Run the grep above on `bench/se2_heuristic_probe.py` and `tests/`. Record whether a witness pose sequence already exists (avoids the 39-min re-run). This only affects the optional fallback; proceed with the fabricated trajectory regardless.

- [ ] **Step 2: Write the throwaway snap probe.** In the scratchpad script, using the Task-2 signatures: (1) define a representative own-gear parallel-park as a list of `Pose` waypoints (lateral offset, equal start/end heading, R=0); (2) snap it by chaining closed-form `plan_reeds_shepp` shots between consecutive waypoints into one `MovesPlan`; (3) serialize the plan deterministically (e.g. `repr` of each segment's fields) and build it **twice**; (4) byte-diff the two serializations; (5) run `path_first_conflict` against the parked fk9/cessna neighbors (or a representative obstacle set) and record acceptance.

```python
# /tmp/.../scratchpad/gate0_snap_probe.py  (throwaway; adjust calls to Task-2 signatures)
from hangarfit.towplanner import Pose, MovesPlan, plan_reeds_shepp, path_first_conflict

def fabricate_parallel_park() -> list[Pose]:
    # own-gear R=0 lateral shuffle: same heading, lateral offset, via a deep cusp
    return [Pose(0.0, 0.0, 90.0), Pose(0.5, 2.0, 60.0), Pose(2.0, 1.5, 90.0)]

def snap_to_plan(waypoints, turn_radius_m=0.0):
    arcs = [plan_reeds_shepp(a, b, turn_radius_m=turn_radius_m)
            for a, b in zip(waypoints, waypoints[1:])]
    return MovesPlan(arcs)  # adjust constructor to the Task-2 signature

def serialize(plan) -> str:
    return "\n".join(repr(s) for arc in plan.moves for s in arc.segments)  # adjust attr names

wp = fabricate_parallel_park()
s1, s2 = serialize(snap_to_plan(wp)), serialize(snap_to_plan(wp))
print("BYTE_IDENTICAL_REPEAT:", s1 == s2)
print("SERIALIZATION:\n", s1)
# print("path_first_conflict:", path_first_conflict(snap_to_plan(wp), obstacles=...))
```

- [ ] **Step 3: Run it.**

Run: `PYTHONPATH=$PWD/src python /tmp/claude-1000/-home-pkuhn-hangarfit/2395e148-c5e7-481e-93fe-f1dd0e1ef72d/scratchpad/gate0_snap_probe.py`
Expected: prints `BYTE_IDENTICAL_REPEAT: True/False` and the serialized plan; if a validity check is wired, its verdict. (If `MovesPlan`/`plan_reeds_shepp` kwargs differ from the skeleton, fix per the Task-2 API block and re-run.)

- [ ] **Step 4: Interpret against the escape hatch.** Record: is repeated construction byte-identical (snap determinism, same-machine)? Does the snapped plan validate? Critically, does the *deterministic* snap absorb the optimizer's role (so only cross-machine **word** agreement would be needed — the reduction escape), or would a shippable plan still have to carry raw optimizer floats (→ collapses to find-then-cache = the rejected witness-cache)?

- [ ] **Step 5: Write the Step 0.3 section.** Append the measured results + the interpretation. State plainly whether a discrete-word reduction is plausible.

- [ ] **Step 6: Verify + commit (doc only).** Confirm the section reports concrete `BYTE_IDENTICAL_REPEAT` and validity outcomes. Do **not** commit the scratchpad script.

```bash
git add docs/spikes/herrenteich-fk9-cessna-trajopt-determinism-precheck.md
git commit -m "docs(844): Gate-0 Step 0.3 — snap/reduction experiment (byte-stability + reduction escape)

Refs #844"
```

---

### Task 4: Synthesize the Gate-0 verdict, cross-reference, and open the PR

Combine Steps 0.1–0.3 into one falsifiable verdict per the spec's §5 bands, wire the result into the surrounding docs, and open the draft PR for the review arc.

**Files:**
- Append: `docs/spikes/herrenteich-fk9-cessna-trajopt-determinism-precheck.md` (the **"Verdict"** section)
- Modify: `docs/spikes/herrenteich-fk9-cessna-lateral-shuffle.md` (add a one-line pointer to the Gate-0 verdict under the follow-up-(b) disposition)
- Modify (only if the verdict changes the documented disposition): `CLAUDE.md` "Open questions / TBD" — the Factor-2 / #844 bullet
- Create: `CHANGELOG.md` entry — **only if** the verdict is user-facing (a pure internal spike verdict may not warrant one; if omitted, the `gh pr create` advisory hook will note it, which is acceptable for a docs/spike PR — label `chore` + `area:docs`)

**Interfaces:**
- Consumes: the Step 0.1 / 0.2 / 0.3 sections from Tasks 1–3.
- Produces: the committed Gate-0 verdict and a draft PR `Refs #844`.

- [ ] **Step 1: Assign the band.** From Steps 0.1–0.3, select exactly one: **NO-GO(unshippable)** (cross-machine required AND no discrete-word reduction), **NO-GO(dominated)** (only realization is A\*-reachable or collapses to witness-cache), or **PASS→Gate 1** (a concrete plausibly-bit-stable path). Write the **"Verdict"** section: the band, the decisive evidence, and — if PASS — exactly what Gate 1 must then prove; if NO-GO, an explicit "the fk9↔cessna corridor remains a documented manual-insertion case" line.

- [ ] **Step 2: Cross-reference.** Add a one-line pointer in `herrenteich-fk9-cessna-lateral-shuffle.md` to the Gate-0 verdict. If (and only if) the verdict changes the documented disposition, update the `CLAUDE.md` #844 bullet — otherwise leave CLAUDE.md untouched (don't pad it).

- [ ] **Step 3: Verify the verdict is falsifiable + self-consistent.** Confirm exactly one band is chosen, every claim cites evidence from Steps 0.1–0.3, and the cross-refs don't contradict the verdict.

- [ ] **Step 4: Commit.**

```bash
git add docs/spikes/herrenteich-fk9-cessna-trajopt-determinism-precheck.md docs/spikes/herrenteich-fk9-cessna-lateral-shuffle.md
# add CLAUDE.md only if Step 2 changed it
git commit -m "docs(844): Gate-0 verdict — continuous-traj-opt determinism-shippability pre-check

Refs #844"
```

- [ ] **Step 5: Push + open the draft PR.**

```bash
git push -u origin feature/844b-trajopt-determinism-precheck
gh pr create --draft --base develop \
  --title "docs(844): #844b continuous-traj-opt determinism-first pre-check (Gate 0)" \
  --body "Gate-0 determinism-shippability pre-check for the fk9↔cessna nook (follow-up b of #844). Spec + plan + spike-doc verdict; no optimizer, no dependency, no src change.

Refs #844"
```

- [ ] **Step 6: Set PR metadata** (per project convention — `gh pr edit` is broken here, use the REST API): assignee `DocGerd`, labels `chore` + `area:docs` + `area:backend`, milestone "Spikes & exploration".

```bash
gh api -X PATCH repos/DocGerd/hangarfit/pulls/<n> -f ... # assignee/labels/milestone via issues endpoint as needed
```

---

## Post-plan: review arc (per project workflow, after execution)

Run `/pr-review` (code-reviewer + comment-analyzer, since this is a docs/spec-heavy PR), convert findings to one inline thread each, fix + resolve, then flip the PR ready and tell the user it's clean. **The user is the sole merger** — never `gh pr merge` or arm auto-merge.

## Self-Review (completed during authoring)

- **Spec coverage:** §1 motivation → Tasks 1–2 recap context; §2 question → Task 4 verdict; §3 sequencing → fixed task order; §4 Gate 0 Steps 0.1/0.2/0.3 → Tasks 1/2/3; §5 GO/NO-GO bands → Task 4 Step 1; §6 determinism constraints → Global Constraints + Task 1; §7 footprint → Global Constraints + Task 3 (throwaway script); §8 husky-incompleteness guard → carried into the Verdict's manual-insertion line; §9 decisions → encoded in task order + footprint; §10 open questions → Task 1 (linchpin), Task 2 (representation), Task 3 Step 1 (witness acquisition). No uncovered section.
- **Placeholder scan:** the `<n>` in Task 4 Steps 5–6 is a runtime PR number (unavoidable), not a content placeholder. No "TBD"/"add error handling"/"write tests for the above". The Task-3 script is a real skeleton with the one documented caveat (adjust kwargs to the Task-2 API block) — inherent to an investigation spike that reads signatures in an earlier task.
- **Type consistency:** `plan_reeds_shepp`, `path_first_conflict`, `Pose`, `MovesPlan`, `Segment`, `MovesPlan.sample` named consistently across Tasks 2–3; Task 2 records their exact signatures before Task 3 calls them.
