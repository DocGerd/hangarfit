# Spike: Graphify (LLM + Tree-sitter knowledge-graph context layer) vs the hand-maintained Quick-Ref + auto-memory

- **Status:** Findings + recommendation. Evaluation-only; **no production change ships from this spike** — no dependency is added, no `.claude/` config is touched, no graph is built into the repo. The one conditional follow-up is named as a DEFER gate, not filed.
- **Date:** 2026-06-09
- **Spike issue:** [#569](https://github.com/DocGerd/hangarfit/issues/569)
- **Recommendation:** **NO-GO** (high confidence) for the question asked — replacing/augmenting the doc-navigation layer. One narrow code-graph niche is left as a DEFER gate that could flip it to **PARTIAL** (see the end).
- **Context:** *"~71× fewer tokens per session."* [Graphify](https://github.com/safishamsi/graphify) (MIT, © 2026 Safi Shamsi; PyPI `graphifyy` 0.8.36, Python ≥3.10, actively maintained) is a `/graphify` skill for Claude Code (and Cursor / Codex / Gemini CLI / Aider) that builds an LLM + Tree-sitter knowledge graph over code + docs + diagrams and answers queries against it. hangarfit is docs-heavy (24 ADRs, ~10 arc42 sections, Mermaid diagrams, 6 spike docs) — a plausible fit — but it is a *probabilistic* retrieval layer, and the project already runs a hand-maintained, deterministic, source-of-truth navigation discipline that cuts the other way if a probabilistic layer is over-trusted.
- **Prior art (don't re-tread):** this spike follows the project's measure-then-decline tradition — [#336](https://github.com/DocGerd/hangarfit/issues/336) (RRT-Connect NO-GO), [#331](https://github.com/DocGerd/hangarfit/issues/331)/[#332](https://github.com/DocGerd/hangarfit/issues/332) (CNN NO-GO), [#540](https://github.com/DocGerd/hangarfit/issues/540) (placement-side STRtree NO-GO). Same shape: a headline metric that does not transfer to this corpus's regime, weighed honestly against an existing free substitute, declined with the reason recorded.

---

## TL;DR

The spike question (from #569): *does querying a Graphify graph over `docs/` + `src/` beat the hand-maintained Quick-Ref + auto-memory for real session questions, at acceptable build cost, without introducing stale or hallucinated answers?* **No, on every clause.** Four conclusions, each measured against this repo, not Graphify's benchmark corpus:

1. **The token win does not exist at this scale.** Graphify's **71.5×** is a *1020-file* large-corpus number measured against a *"read/grep ALL raw files"* baseline (~465 KB of ADR+arch bodies). hangarfit never navigates that way: the **full Quick-Ref router is already resident in-context for free** (`CLAUDE.md` = 32,503 B + `MEMORY.md` = 24,513 B load every session). Routing cost is already **0 extra tokens**; resolution is **one targeted ADR read** (~1.8–7 KB). Graphify's own README says small corpora (hangarfit is **15 src files / 10.5K LOC + 124 `.md`**) get *"structural clarity, **not** compression."*

2. **The hand-maintained layer is 11/11 correct on the sample — and its correctness depends on exactly the prose Graphify would mis-extract.** A head-to-head on 11 representative session questions resolved all 11 to the right source. But **3 of them are correct only because the reader follows the pointer into an ADR's *amendment trail* or the live code** — the ADR *headline* is stale (see Finding 2). A prose-extraction graph keying on ADR body text would surface the stale headline = **wrong-vs-source on the high-stakes invariants**, failing the "zero wrong answers" gate.

3. **The "always reconfirm against source" mandate erases any acted-upon win.** #569 permits adoption only as a *"navigation hint, always reconfirmed against source."* Since you read the source ADR/code anyway, Graphify would only replace the *routing* step — which the Quick-Ref already does deterministically for 0 tokens. **No net saving on any answer you act on.**

4. **The one honest steelman is code-graph, not doc-nav — and `ripgrep` already serves it.** Graphify's deterministic value (AST call-graph, `EXTRACTED` confidence-1.0 edges) genuinely answers questions the flat Quick-Ref cannot ("what calls `_spread`", "every reference to the det-(−1) trap", "blast radius of `aircraft_parts_world`"). But on a 15-file single-language Python repo, **`rg` answers all three in ≤11 ms, deterministically, at zero cost / zero staleness / zero install** (measured below). Graphify would have to beat a tool already at the cost/freshness floor.

| Axis the #569 gate measures | Verdict | Why (measured) |
|---|---|---|
| **Measurable token win for doc-nav** | **NO-GO** | Real baseline = 0 routing tokens + 1 ADR read; 71.5× is a large-corpus-vs-grep-all number that doesn't transfer (small-corpus regime → "clarity not compression") |
| **Zero wrong-vs-source answers** | **NO-GO** | Doc value-add is the *paid, `INFERRED`/`AMBIGUOUS`* pass; would key on 3 verified stale ADR headlines (reverse-cost `1.5`, "notch not implemented", "bit-identical under seed") |
| **Acceptable build/run cost** | **NO-GO** | Docs value-add needs the **paid** Claude pass-2; new un-pinned PyPI dep vs the repo's hash-pin/OpenSSF posture; graph artifacts untracked; docs churn → recurring paid re-extract |
| **Code-graph niche (out of #569's framing)** | **DEFER** | Real, but must beat `rg` (11 ms, deterministic) not the Quick-Ref — gate named below |

**Recommendation: NO-GO** for adopting Graphify as a navigation layer. The hand-maintained Quick-Ref + auto-memory is already a free, deterministic, in-context version of what Graphify sells, and the project's source-of-truth discipline is the very thing a probabilistic layer would erode. **Complement-not-replace** is the only framing under which a future trial makes sense, and only for the code-graph niche, gated on beating `ripgrep`.

---

## What Graphify actually is (verified from primary sources)

Not from the marketing site (which 403s and overstates) — from the repo `README`, the shipped `skills/graphify/skill.md`, the PyPI metadata, and an independent reproduction write-up:

- **Two-pass build.** *Pass 1* (local, **free**, 0 tokens): Tree-sitter ASTs + call-graphs over code (25 languages). *Pass 2* (**paid**): the Claude API extracts concepts/relationships from markdown/RST prose, Claude vision reads diagrams, citation-mines PDFs. **For a docs-heavy repo, the value-add over a code-only graph is entirely in the paid pass.** Build spend is tracked in `graphify-out/cost.json`.
- **Storage / query.** `graph.json` + `graphify-out/` (HTML viz, Obsidian vault, wiki, `GRAPH_REPORT.md`, a SHA256 change cache). Queried via `graphify query` / `path A B` / `explain X` over a NetworkX graph (Leiden clustering, BFS). **Every edge is self-tagged** `EXTRACTED` (1.0, from AST), `INFERRED` (0.7–0.9), or `AMBIGUOUS` (<0.7) — Graphify is honest that prose-derived edges are guesses.
- **Hooks (accuracy correction).** The **current shipped v1 skill installs a *git post-commit* hook only** — *not* a `PreToolUse` hook. Graph queries are optional, user-initiated; the assistant is **not** auto-routed through the graph before file searches. The "Claude consults the graph before every file-search tool call" line is a marketing-site claim that does **not** match the shipped skill. (Worth getting right — overstating the invasiveness would be a cheap shot.)
- **Staleness.** Code saves → free AST-only rebuild (~10 s, 8 workers). **Doc/diagram changes → the graph goes stale until a *paid* `--update` re-extract.** Refresh is git-hook-driven (deliberately not per-turn, "too slow").
- **The 71.5× claim.** Measured on a *large mixed corpus* (Karpathy repos + papers + images; reproductions on 126–1020 files) against a "reading raw files" baseline. The README itself states the metric **scales with corpus size** and that small corpora get *"structural clarity, not compression."* No transparent per-query token methodology is published; third-party reviewers flag this.

---

## Method

This spike binds on **what hangarfit's navigation actually costs today**, not on Graphify's benchmark. Five strands, measured against the repo at `develop`:

1. **Inventory the real baseline** — what navigation content is resident in-context for free at session start, and what a "which ADR/file owns X" question actually costs beyond it.
2. **Head-to-head on 11 representative session questions** — answer each using only the hand-maintained layer, verify against source, record marginal cost and any place a single-source read would mislead.
3. **Steelman the GO case** — build the strongest honest argument for adopting Graphify here, then test it.
4. **Measure the steelman's strongest queries against `ripgrep`** — the free deterministic tool Graphify must actually beat on a single-language repo.
5. **Independent adversarial verdict** against the #569 gate (token win **and** zero wrong-vs-source).

What was **not** done, and why: no live `pip install graphifyy`, no `.claude/`/`CLAUDE.md` mutation, and no graph build. The docs value-add requires the **paid** pass-2 (real API spend), installing the integration mutates the shared, carefully-maintained `CLAUDE.md` + adds a hook, and `graphifyy` would be a new un-pinned dependency in a repo that hash-pins everything against an OpenSSF Scorecard posture. The #569 *gate question* (doc-nav vs the Quick-Ref) is decisively answerable without any of that, and the only thing a live build would additionally probe — the code-graph niche — is settled for free against `ripgrep` below. A live **free pass-1-only** trial is named as the DEFER gate for the user to greenlight if desired (see Recommendation).

---

## Findings

### 1. There is no doc-nav token win against the *real* baseline

Graphify's 71.5× is `query-cost ÷ read-all-files-cost`. hangarfit's denominator is not "read all files":

- **Resident free at session start:** `CLAUDE.md` (32,503 B, incl. the entire **19-row Quick-Ref router** mapping every topic → owning arc42 anchor + ADR number) + `MEMORY.md` (24,513 B, 77-entry auto-memory index). ≈ **14–15K tokens of navigation + live state, before any question is asked.**
- **Routing cost for a "which ADR owns X" question: 0 extra tokens.** The Quick-Ref row resolves the target deterministically — no grep, no search.
- **Resolution cost: exactly one targeted ADR read** (ADR-0009 ~1.8K tokens … ADR-0003 ~7K tokens). Several questions ("what is hangarfit", "is the hangar L-shaped", "what supersedes ADR-0005") need **zero** extra reads — the already-resident prose answers them.

So the marginal cost Graphify must beat is **~1 file read (~4–5K tokens)**, not ~110K. The genuine headroom is roughly **an order of magnitude smaller** than the headline, and the corpus (15 src files / 10.5K LOC; 124 `.md`) sits in the **small-corpus regime** where Graphify's own README promises *clarity, not compression*. The token win the GO gate requires simply isn't there.

### 2. The hand-maintained layer is 11/11 correct — and that exposes Graphify's accuracy problem, not its opportunity

All 11 representative questions resolved to the correct source via the Quick-Ref / auto-memory (9 via a direct Quick-Ref row, 2 via Open-questions/parts-model prose + the matching `MEMORY.md` line). Total marginal cost ≈ 11 ADR reads + 3 confirming code greps ≈ 50K tokens — i.e. cheap and exact.

The decisive observation is *why* it's correct. **Three of the eleven are right only because the reader follows the pointer into the ADR's amendment trail or the live source — the ADR headline is stale:**

| Question | Stale headline a single read would surface | Source of truth |
|---|---|---|
| Reverse-cost factor (ADR-0010) | Body prominently states `_REVERSE_COST_FACTOR = 1.5` | **Removed** by the #480 amendment → `CUSP_PENALTY = 10.0` ("fewest-moves"), `towplanner.py:545`; `_REVERSE_COST_FACTOR` is gone |
| L-shaped notch (ADR-0018) | Closes with *"Implementation sketch (NOT implemented in this spike)"* | **Shipped** via #528 — `collisions.py:106-130` `floor.covers` / `structural_notch` |
| Determinism contract (ADR-0003) | *"Status: Accepted / bit-identical under seed"* | **Narrowed** by the #267/#404/#544 amendments to the `max_restarts`-bound regime |

A prose-extraction graph keys on exactly this body text. Its `INFERRED`/`AMBIGUOUS` edges would encode the **stale** value (`1.5`, "not implemented", bare "bit-identical") — **wrong-vs-source on the project's highest-stakes invariants** (a tow-cost model, a collision rule guarded by a subagent, the determinism contract). This fails #569's "zero wrong answers" bar at precisely the questions where being wrong is most expensive. *(Note: the auto-memory can carry the same stale `1.5` — which is exactly why the discipline is "reconfirm against source," and exactly why a second probabilistic layer adds risk without adding truth.)*

### 3. The reconfirm mandate erases the acted-upon win

#569 only ever proposes Graphify as a *"navigation hint, always reconfirmed against source."* That is the right guardrail for a probabilistic layer — and it is also self-defeating economically. For any answer you act on, you read the source ADR/code regardless. Graphify would replace only the routing hop, which the in-context Quick-Ref already performs for **0 tokens, deterministically**. The net token saving on acted-upon answers is **zero or negative** (you've added a query + a graph to maintain in front of a read you'd do anyway).

### 4. The honest steelman is code-graph — and `ripgrep` already wins it

The strongest *fair* GO case is **not** doc-nav. It is sub-file, code-structure queries the flat Quick-Ref structurally cannot answer (it has **0 function-level references by design**): reverse call-graphs, cross-cutting invariant tracing, blast-radius. These ride Graphify's **`EXTRACTED` (1.0, AST, free pass-1)** edges — no hallucination exposure. That's a real gap in the Quick-Ref.

But the Quick-Ref isn't the competitor for code questions — **`ripgrep` is**, and on a 15-file single-language Python repo it is already at the floor:

| Steelman query | `ripgrep` result | Cost |
|---|---|---|
| *what calls `_spread`?* | All call sites: `solver.py:272/293/1260/1269/1380`, `metrics.py:70` mirror, … | **11 ms**, deterministic |
| *every reference to the det-(−1) trap / ADR-0002* | Enumerates every file in one call | <50 ms |
| *blast radius of `aircraft_parts_world`* | All 18 files (6 src + 12 test) listed | one `rg -l` |

`rg` is **zero-install, zero-API, zero-staleness, deterministic, and already used constantly.** Graphify's code graph is *semantically* richer (it distinguishes a call from a comment mention, and resolves structure `rg` can't), but for a 10.5K-LOC single-language repo under heavy naming-convention discipline that precision gap is small — and it is dwarfed by `rg`'s advantages on cost, freshness, determinism, and *not adding a dependency*. To justify adoption Graphify must beat **`rg`**, not the Quick-Ref. It doesn't clear that bar here.

### 5. Operational frictions confirmed against the repo

- **Supply chain.** `graphifyy` would be a **new un-pinned PyPI dependency** in a repo with four hash-pinned `requirements-*.txt` lockfiles, an enforced `lockfile-drift` guard, and a documented OpenSSF Scorecard posture. Its `graph.json` / `graphify-out/` / `cache/` artifacts are not git-ignored and would tax the clean-tree discipline.
- **Hook stack.** The project already trimmed a per-edit `PostToolUse` pytest hook once for velocity, and runs `PreToolUse` + `PostToolUse` + `Stop` hooks. The marketing always-on graph hook (the dev.to write-up says it's deliberately excluded as too slow) would land into that stack; the shipped git-post-commit hook is benign but adds a background rebuild per commit.
- **Churn → recurring paid staleness.** `MEMORY.md` shows a live milestone/PR/issue cadence; ADRs and `CLAUDE.md` change frequently. Each doc/diagram edit stales the graph until a **paid** `--update`, exactly where the hand-layer is already current and free.

---

## Recommendation

**NO-GO** — do not adopt Graphify as a navigation layer for hangarfit. It would duplicate a curated, human-trusted, zero-hallucination, **already-in-context** artifact; the headline token win does not transfer to this corpus's regime; the only doc value it adds is paid and probabilistic and would surface stale ADR headlines as fact; and the "reconfirm against source" guardrail that makes it safe also makes it economically pointless. This is the same disposition as CNN / RRT-Connect / STRtree: a metric that doesn't survive contact with this repo's regime, declined with the reasons recorded so it isn't re-litigated on marketing numbers.

**The Quick-Ref + auto-memory already *is* the local, free, deterministic knowledge graph** — hand-curated to point at source rather than to paraphrase it. The project's source-of-truth / determinism discipline is the asset; a probabilistic layer over the top is a liability against it, not an upgrade.

### The one thing that would flip the verdict — **DEFER**, gated

The code-graph niche (Finding 4) is the only honest opening, and it is **out of #569's framing** (#569 asks about beating the *Quick-Ref + auto-memory* for *session questions*, i.e. doc-nav). Disposition: **DEFER**, gated on a single cheap probe —

> **Gate:** a scoped, **free pass-1-only** (`graphify . ` AST graph, no paid extraction, run in a throwaway `/tmp` checkout, no `.claude` mutation) head-to-head showing the `EXTRACTED` code-graph answers *what-calls-`_spread`* / *every-ref-to-ADR-0002* / *`aircraft_parts_world` blast-radius* **measurably faster or more reliably than `rg`/`ripgrep`** on this repo. **Do not file an implementation issue until that probe beats `ripgrep`** — not merely the Quick-Ref.

If (and only if) it clears that gate, the verdict becomes **PARTIAL**: adopt Graphify's **code** graph as a **complement** to — never a replacement for — the Quick-Ref, with the prose/diagram pass-2 left off (paid + probabilistic + the stale-headline hazard). Replacing the Quick-Ref is never on the table: it would regress the ~35 curated lookups it pre-answers for 0 tokens.

This spike does **not** file that issue; the gate is unmet pending the probe, and the user can greenlight the free trial if the code-graph niche is worth chasing.

---

## Source-of-truth & determinism note

hangarfit's defining discipline is that **`CLAUDE.md` holds no domain assertions** — it *routes* to arc42/ADR, which hold the truth, and the solver/towplanner contract is *byte-identical determinism* (ADR-0003). A retrieval layer whose edges are `INFERRED`/`AMBIGUOUS`, that paraphrases ADR prose (including stale headlines), and that goes silently stale between paid rebuilds is structurally at odds with both. The Quick-Ref's value is precisely that it is *not* probabilistic: it is a hand-maintained, reviewed, version-controlled router. Trading that for a graph you must "always reconfirm against source" trades the project's strongest property for a token win that, here, isn't real.

---

## Out of scope

- **No production change, no dependency, no `.claude`/`CLAUDE.md` edit, no committed graph.** Output is this document; #569 closes with it.
- **No live paid build.** Deliberately declined (cost + shared-config mutation + supply-chain) — see Method. The free pass-1 code-graph trial is the named DEFER gate, not run here.
- **Prior NO-GOs re-listed so they aren't re-proposed without new evidence:** CNN ([#331](https://github.com/DocGerd/hangarfit/issues/331)/[#332](https://github.com/DocGerd/hangarfit/issues/332)), RRT-Connect ([#336](https://github.com/DocGerd/hangarfit/issues/336)), placement-side STRtree ([#540](https://github.com/DocGerd/hangarfit/issues/540)). Graphify-as-doc-nav now joins them.
- **Corpus-and-tooling-specific conclusion.** This NO-GO is measured against hangarfit's curated, auto-loaded Quick-Ref baseline. For a project *without* one — or a non-Claude assistant (Cursor/Aider/Codex) / new human contributor with no Quick-Ref internalized — Graphify's relative value is higher. The judgment is about *this* repo's substitute, not about Graphify in general.
