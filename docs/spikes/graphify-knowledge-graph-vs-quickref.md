# Spike: Graphify (LLM + Tree-sitter knowledge-graph context layer) vs the hand-maintained Quick-Ref + auto-memory

- **Status:** Findings + recommendation. **Graphify was actually installed and run** (free pass-1) in an isolated throwaway `/tmp` venv — the repo, its dependencies, and `.claude/`/`CLAUDE.md` were **never** touched. Pass-2 (the paid LLM doc-graph) was *not* run: it needs an Anthropic **API key**, which a Claude Max subscription does not provide, and the maintainer opted to skip it once Graphify's own `benchmark` had settled the token question. **No production change ships from this spike.**
- **Date:** 2026-06-09
- **Spike issue:** [#569](https://github.com/DocGerd/hangarfit/issues/569)
- **Recommendation:** **NO-GO** (high confidence) for the question #569 asks — beating the doc-navigation layer (Quick-Ref + auto-memory). The one honest opening — a *code-graph* niche, **out of #569's framing** — was tested live and is a **soft PARTIAL**: Graphify's free AST graph genuinely beats `ripgrep` on *relational precision*, but the practical value over `rg` on a 15-file repo doesn't clearly justify a new dependency + rebuild discipline (see the Empirical trial + Recommendation).
- **Context:** *"~71× fewer tokens per session."* [Graphify](https://github.com/safishamsi/graphify) (MIT, © 2026 Safi Shamsi; PyPI `graphifyy` 0.8.36, Python ≥3.10, actively maintained) is a `/graphify` skill for Claude Code (and Cursor / Codex / Gemini CLI / Aider) that builds an LLM + Tree-sitter knowledge graph over code + docs + diagrams and answers queries against it. hangarfit is docs-heavy (24 ADRs, ~10 arc42 sections, Mermaid diagrams, 6 prior spike docs) — a plausible fit — but it is a *probabilistic* retrieval layer, and the project already runs a hand-maintained, deterministic, source-of-truth navigation discipline that cuts the other way if a probabilistic layer is over-trusted.
- **Prior art (don't re-tread):** this spike follows the project's measure-then-decline tradition — [#336](https://github.com/DocGerd/hangarfit/issues/336) (RRT-Connect NO-GO), [#331](https://github.com/DocGerd/hangarfit/issues/331)/[#332](https://github.com/DocGerd/hangarfit/issues/332) (CNN NO-GO), [#540](https://github.com/DocGerd/hangarfit/issues/540) (placement-side STRtree NO-GO). Same shape: a headline metric that does not transfer to this corpus's regime, weighed honestly against an existing free substitute, declined with the reason recorded.

---

## TL;DR

The spike question (from #569): *does querying a Graphify graph over `docs/` + `src/` beat the hand-maintained Quick-Ref + auto-memory for real session questions, at acceptable build cost, without introducing stale or hallucinated answers?* **No, on every clause.** Four conclusions, each measured against this repo, not Graphify's benchmark corpus:

1. **The token win does not exist at this scale — confirmed by Graphify's own benchmark.** I ran `graphify benchmark` on the real hangarfit graph: it reports **3.5× reduction, not 71.5×** (and only **2.2–2.5×** for ordinary questions; the 13–21× cases are the cross-cutting relational ones). Worse, its **average query cost is ~33,841 tokens** — measured against a 120K-token *"read everything"* baseline hangarfit never uses. hangarfit's **real** baseline: the full Quick-Ref router is **resident in-context for free** (`CLAUDE.md` 32,503 B + `MEMORY.md` 24,513 B every session) → routing is **0 extra tokens** and resolution is **one ~5K-token ADR read**. So against the real baseline Graphify's per-query cost is **~7× *higher*, not lower**. The 71.5× is a large-corpus figure; Graphify's README itself says small corpora (hangarfit = **15 src files / 124 `.md`**) get *"structural clarity, **not** compression."*

2. **The hand-maintained layer is 11/11 correct on the sample — and its correctness depends on exactly the prose Graphify would mis-extract.** A head-to-head on 11 representative session questions resolved all 11 to the right source. But **3 of them are correct only because the reader follows the pointer into an ADR's *amendment trail* or the live code** — the ADR *headline* is stale (see Finding 2). A prose-extraction graph keying on ADR body text would surface the stale headline = **wrong-vs-source on the high-stakes invariants**, failing the "zero wrong answers" gate.

3. **The "always reconfirm against source" mandate erases any acted-upon win.** #569 permits adoption only as a *"navigation hint, always reconfirmed against source."* Since you read the source ADR/code anyway, Graphify would only replace the *routing* step — which the Quick-Ref already does deterministically for 0 tokens. **No net saving on any answer you act on.**

4. **The one honest steelman is code-graph, not doc-nav — and here Graphify *does* beat `ripgrep` (measured).** Graphify's free AST call-graph (`EXTRACTED` confidence-1.0 edges) answers relational questions the flat Quick-Ref cannot. I built it for real ($0, 3.5 s) and ran the queries: `explain "_spread()"` names the caller `_run_restart()` with direction and relation type, where `rg -n '_spread\b' src/` returns **1 call + 1 def + 10 docstring/comment false-positives** you must eyeball-filter. `affected "aircraft_parts_world()"` gives a *function-level, relation-typed* blast radius vs `rg -l`'s file-level list. **So on relational precision the code graph wins.** The catch is what it costs to get there: a build + rebuild-on-change discipline, a new dependency, and graph noise — for a 15-file repo where `rg`'s superset is trivially filtered by eye. Real win, narrow value, **and out of #569's doc-nav framing.**

| Axis the #569 gate measures | Verdict | Why (measured) |
|---|---|---|
| **Measurable token win for doc-nav** | **NO-GO** | Graphify's *own* `benchmark` on this corpus = **3.5×** (2.2–2.5× ordinary), **~33,841 tokens/query** vs a baseline hangarfit never uses. Against the real in-context Quick-Ref baseline (0 routing + ~5K read), Graphify is **~7× more expensive per query** |
| **Zero wrong-vs-source answers** | **NO-GO** (analytic) | Doc value-add is the *paid, `INFERRED`/`AMBIGUOUS`* pass (not run — needs an API key); it keys on prose that carries 3 verified stale ADR headlines (reverse-cost `1.5`, "notch not implemented", "bit-identical under seed") |
| **Acceptable build/run cost** | **NO-GO** | Docs value-add needs the **paid** pass-2; new un-pinned PyPI dep vs the repo's hash-pin/OpenSSF posture; graph artifacts untracked; docs churn → recurring paid re-extract |
| **Code-graph niche (out of #569's framing)** | **soft PARTIAL** | Tested live: Graphify's free AST graph **beats `rg` on relational precision** (caller/blast-radius with direction + relation, no comment noise). But narrow value on a 15-file repo vs the dependency + rebuild cost |

**Recommendation: NO-GO** for adopting Graphify as the navigation layer #569 asks about. The hand-maintained Quick-Ref + auto-memory is already a free, deterministic, in-context version of what Graphify sells, the project's source-of-truth discipline is the very thing a probabilistic doc-graph would erode, and Graphify's own benchmark confirms there's no token win here. The code-graph niche is a real-but-narrow **complement-not-replace** judgment call, not adopted now.

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

This spike binds on **what hangarfit's navigation actually costs today**, plus a **live trial** of Graphify itself. Strands, measured against the repo at `develop`:

1. **Inventory the real baseline** — what navigation content is resident in-context for free at session start, and what a "which ADR/file owns X" question actually costs beyond it.
2. **Head-to-head on 11 representative session questions** — answer each using only the hand-maintained layer, verify against source, record marginal cost and any place a single-source read would mislead.
3. **Steelman the GO case** — the strongest honest argument for adopting Graphify here.
4. **Live trial (Empirical trial, below):** `pip install graphifyy` in a throwaway `/tmp` venv; build the **free pass-1 AST graph** over `src/`; run `graphify affected / explain / query`; run Graphify's own `graphify benchmark`; head-to-head against `ripgrep` on the same questions.
5. **Independent adversarial verdict** against the #569 gate (token win **and** zero wrong-vs-source).

What was **not** done, and why: **pass-2** (the paid LLM doc/diagram extraction) was not run. Its `--backend claude` path calls the **Anthropic API** and requires `ANTHROPIC_API_KEY` — which a **Claude Max** subscription does not provide (Max powers claude.ai + Claude Code via login; the API is separate, usage-billed). Free backends (Gemini key, local ollama) and an API key were all available routes, but the maintainer opted to skip pass-2 once `graphify benchmark` (run on the free graph) had already settled the token question. The pass-2 result would only have added an *empirical* test of the stale-headline hallucination hypothesis (Finding 2), which remains analytic. Nothing touched the repo, its deps, or `.claude/` — the entire trial lived in `/tmp` and is deleted with it.

---

## Findings

### 1. There is no doc-nav token win against the *real* baseline

Graphify's 71.5× is `query-cost ÷ read-all-files-cost`. hangarfit's denominator is not "read all files":

- **Resident free at session start:** `CLAUDE.md` (32,503 B, incl. the entire **19-row Quick-Ref router** mapping every topic → owning arc42 anchor + ADR number) + `MEMORY.md` (24,513 B, 77-entry auto-memory index — measurement-time snapshot; the auto-memory grows a little each session). ≈ **14–15K tokens of navigation + live state, before any question is asked.**
- **Routing cost for a "which ADR owns X" question: 0 extra tokens.** The Quick-Ref row resolves the target deterministically — no grep, no search.
- **Resolution cost: exactly one targeted ADR read** (ADR-0009 ~1.8K tokens … ADR-0003 ~7K tokens). Several questions ("what is hangarfit", "is the hangar L-shaped", "what supersedes ADR-0005") need **zero** extra reads — the already-resident prose answers them.

So the marginal cost Graphify must beat is **~1 file read (~5K tokens)**, not the ~120K "read everything" baseline its `benchmark` assumes (measured: 120,066 naive tokens, ~33,841 avg per query). Graphify's per-query cost is **larger than hangarfit's whole per-question cost**; the corpus (15 src files / 10.5K LOC; 124 `.md`) sits in the **small-corpus regime** where Graphify's own README promises *clarity, not compression*. The token win the GO gate requires simply isn't there.

### 2. The hand-maintained layer is 11/11 correct — and that exposes Graphify's accuracy problem, not its opportunity

All 11 representative questions resolved to the correct source via the Quick-Ref / auto-memory (9 via a direct Quick-Ref row, 2 via Open-questions/parts-model prose + the matching `MEMORY.md` line). Total marginal cost ≈ 11 ADR reads + 3 confirming code greps ≈ 50K tokens — i.e. cheap and exact.

The decisive observation is *why* it's correct. **Three of the eleven are right only because the reader follows the pointer into the ADR's amendment trail or the live source — the ADR headline is stale:**

| Question | Stale headline a single read would surface | Source of truth |
|---|---|---|
| Reverse-cost factor (ADR-0010) | Body prominently states `_REVERSE_COST_FACTOR = 1.5` | **Removed** by the #480 amendment → `CUSP_PENALTY = 10.0` ("fewest-moves"), `towplanner.py:545`; `_REVERSE_COST_FACTOR` is gone |
| L-shaped notch (ADR-0018) | Body still carries *"Implementation sketch (NOT implemented in this spike)"* deep at the bottom (the weakest of the three — the ADR's **status line is now `Accepted`** with a top-of-file Implementation note, so a status-aware reader is safe; only a naive prose extractor that ignores the status line would surface the buried string) | **Shipped** via #528 — `collisions.py:127/130` `floor.covers` / `structural_notch` |
| Determinism contract (ADR-0003) | *"Status: Accepted / bit-identical under seed"* | **Narrowed** by the #267/#404/#544 amendments to the `max_restarts`-bound regime |

A prose-extraction graph keys on exactly this body text. Its `INFERRED`/`AMBIGUOUS` edges would encode the **stale** value (`1.5`, "not implemented", bare "bit-identical") — **wrong-vs-source on the project's highest-stakes invariants** (a tow-cost model, a collision rule guarded by a subagent, the determinism contract). This fails #569's "zero wrong answers" bar at precisely the questions where being wrong is most expensive. *(Note: the auto-memory can carry the same stale `1.5` — which is exactly why the discipline is "reconfirm against source," and exactly why a second probabilistic layer adds risk without adding truth.)*

### 3. The reconfirm mandate erases the acted-upon win

#569 only ever proposes Graphify as a *"navigation hint, always reconfirmed against source."* That is the right guardrail for a probabilistic layer — and it is also self-defeating economically. For any answer you act on, you read the source ADR/code regardless. Graphify would replace only the routing hop, which the in-context Quick-Ref already performs for **0 tokens, deterministically**. The net token saving on acted-upon answers is **zero or negative** (you've added a query + a graph to maintain in front of a read you'd do anyway).

### 4. The honest steelman is code-graph — tested live, Graphify beats `ripgrep` on relational precision

The strongest *fair* GO case is **not** doc-nav. It is sub-file, code-structure queries the flat Quick-Ref structurally cannot answer (it has **0 function-level references by design**): reverse call-graphs, cross-cutting invariant tracing, blast-radius. These ride Graphify's **`EXTRACTED` (1.0, AST, free pass-1)** edges — no hallucination exposure. That's a real gap in the Quick-Ref. So I built the graph and measured it (full setup + raw output in the **Empirical trial** below).

Same question — *who calls `_spread`?* — both tools, over `src/`:

| | `ripgrep` (`rg -n '_spread\b' src/`) | Graphify (`explain "_spread()"`) |
|---|---|---|
| Result | 12 matches: **1 call** (`solver.py:272`), 1 `def` (`:1380`), **10 docstring/comment mentions** (9 in `solver.py` + `models.py:1400`) | `<-- _run_restart() [calls]` — *the* caller, with direction + relation; docstring shows as a separate `rationale_for` node, not a caller |
| Precision | substring superset → eyeball-filter the 10 false-positives | function-level, relation-typed, **zero comment noise** |
| Speed | **14 ms**, zero build | **~0.1 s/query**, after a free **3.5 s** AST build |
| Freshness | always current | stale after any code edit → must rebuild |

For the *blast radius* of `aircraft_parts_world`, `affected` likewise returns a **function-level, relation-typed** reverse-dependency set (`cached_parts_world() [calls]`, `check() [calls]`, `_spread_quality() [calls]`, importers …) where `rg -l` returns a flat file list. **On relational precision the code graph genuinely wins.**

The honest qualifier: this is a **15-file single-language Python repo under heavy naming-convention discipline**, where `rg`'s superset is trivially filtered by eye, the graph must be *rebuilt on every code change* to stay correct, it pulls in a new dependency + a 28-package tree-sitter stack, and the build is noisy (the `viewer.js` bundle alone inflated the graph to **1801 nodes / 5583 edges**, and docstrings became nodes). So the precision win is **real but its practical value is narrow** — it buys you relation-typed call/blast-radius queries you'd otherwise eyeball, at the cost of a build/staleness/dependency discipline. That is a genuine **complement** to `rg` for someone who asks relational code questions often; it is not a doc-nav win and it is out of #569's framing.

### 5. Operational frictions confirmed against the repo

- **Supply chain.** `graphifyy` would be a **new un-pinned PyPI dependency** in a repo with four hash-pinned `requirements-*.txt` lockfiles, an enforced `lockfile-drift` guard, and a documented OpenSSF Scorecard posture. Its `graph.json` / `graphify-out/` / `cache/` artifacts are not git-ignored and would tax the clean-tree discipline.
- **Hook stack.** The project already trimmed a per-edit `PostToolUse` pytest hook once for velocity, and runs `PreToolUse` + `PostToolUse` + `Stop` hooks. The marketing always-on graph hook (the dev.to write-up says it's deliberately excluded as too slow) would land into that stack; the shipped git-post-commit hook is benign but adds a background rebuild per commit.
- **Churn → recurring paid staleness.** `MEMORY.md` shows a live milestone/PR/issue cadence; ADRs and `CLAUDE.md` change frequently. Each doc/diagram edit stales the graph until a **paid** `--update`, exactly where the hand-layer is already current and free.

---

## Empirical trial — pass-1 run for real (2026-06-09)

Fully isolated, `$0`: `python3 -m venv` in `/tmp` → `pip install graphifyy` (**v0.8.36**; deps are all local — networkx + 28 tree-sitter language packs, **no LLM SDK**). No API keys in the environment, so pass-2 physically could not fire. Built over a *copy* of `src/` so no `graphify-out/` ever landed in the repo.

**Build (free pass-1, AST only):** `graphify ./src` → **1801 nodes, 5583 edges, 71 communities in 3.5 s**, `$0`. *Gotcha:* the default build tries LLM extraction on any markdown it finds and **aborts the whole build** if no backend is configured — I had to strip 2 stray `.md`/`.txt` files to get a clean code-only graph. A docs-heavy adopter therefore cannot avoid configuring (and paying for) a backend.

**Graphify's own token benchmark** (`graphify benchmark`, no key needed) on this corpus:

```
Corpus:          90,050 words → ~120,066 tokens (naive)
Graph:           1,801 nodes, 5,583 edges
Avg query cost:  ~33,841 tokens
Reduction:       3.5x fewer tokens per query
  [2.2x] how does authentication work
  [2.3x] what is the main entry point
  [2.5x] how are errors handled
  [21.9x] what connects the data layer to the api
  [13.6x] what are the core abstractions
```

**3.5×, not 71.5×.** Ordinary questions get 2.2–2.5×; the 13–21× outliers are the relational queries. And the **avg query cost ~33,841 tokens** is *larger* than hangarfit's entire real per-question cost (~5K for one ADR read off the in-context Quick-Ref) — so against the real baseline, querying the graph is a net **loss**. The 3.5× exists only relative to a "read all 120K tokens" baseline hangarfit never pays.

**Code-graph queries** (`affected` / `explain` / `query`) all ran **locally, deterministically, ~0.1 s, `$0`** — no backend needed — and are more precise than `rg` on relational questions (Finding 4).

**Pass-2 (paid doc-graph): not run.** `--backend claude` needs `ANTHROPIC_API_KEY` (the Anthropic API, *not* a Max subscription); no key was provided and the maintainer skipped it once the benchmark had settled the token question. So Finding 2's stale-headline hazard stands as an *analytic* argument, not an empirical measurement — but the benchmark already shows a doc-graph could not deliver a token win here even with perfect edges.

---

## Recommendation

**NO-GO** — do not adopt Graphify as a navigation layer for hangarfit. It would duplicate a curated, human-trusted, zero-hallucination, **already-in-context** artifact; the headline token win does not transfer to this corpus's regime; the only doc value it adds is paid and probabilistic and would surface stale ADR headlines as fact; and the "reconfirm against source" guardrail that makes it safe also makes it economically pointless. This is the same disposition as CNN / RRT-Connect / STRtree: a metric that doesn't survive contact with this repo's regime, declined with the reasons recorded so it isn't re-litigated on marketing numbers.

**The Quick-Ref + auto-memory already *is* the local, free, deterministic knowledge graph** — hand-curated to point at source rather than to paraphrase it. The project's source-of-truth / determinism discipline is the asset; a probabilistic layer over the top is a liability against it, not an upgrade.

### The code-graph niche — gate tested, **soft PARTIAL**

The code-graph niche (Finding 4) is the only honest opening, and it is **out of #569's framing** (#569 asks about beating the *Quick-Ref + auto-memory* for *session questions*, i.e. doc-nav). The gate from this spike's first draft — *does the free pass-1 graph beat `ripgrep`, not just the Quick-Ref?* — was **tested live** (Empirical trial), and the answer is **yes, on relational precision**: relation-typed callers and blast-radius with the comment noise stripped.

That makes the niche a **soft PARTIAL**, not a GO:

- **Real:** for someone who frequently asks *what-calls-X* / *blast-radius-of-X* / *what-connects-X-to-Y*, the free, deterministic, local AST graph is a genuine improvement over `rg` + eyeball.
- **Narrow:** on a 15-file single-language repo, `rg`'s superset is trivially filtered, and the graph costs a new dependency, a rebuild-on-change discipline, and untracked artifacts. The benchmark's relational outliers (13–21×) are where it would pay; the ordinary lookups (2.2–2.5×) are not worth it.

**Disposition:** not adopted, and **not filed** as an implementation issue — the value does not clear the dependency/maintenance cost for a repo this size. If the team later finds itself repeatedly asking relational code questions, the path is a **complement** (free pass-1 graph built in `/tmp` or git-ignored, queried on demand) — **never a replacement** for the Quick-Ref (which pre-answers its lookups for 0 tokens), and **never the paid pass-2 doc-graph** (the benchmark shows no token win, and it carries the stale-headline hazard).

---

## Source-of-truth & determinism note

hangarfit's defining discipline is that **`CLAUDE.md` holds no domain assertions** — it *routes* to arc42/ADR, which hold the truth, and the solver/towplanner contract is *byte-identical determinism* (ADR-0003). A retrieval layer whose edges are `INFERRED`/`AMBIGUOUS`, that paraphrases ADR prose (including stale headlines), and that goes silently stale between paid rebuilds is structurally at odds with both. The Quick-Ref's value is precisely that it is *not* probabilistic: it is a hand-maintained, reviewed, version-controlled router. Trading that for a graph you must "always reconfirm against source" trades the project's strongest property for a token win that, here, isn't real.

---

## Out of scope

- **No production change, no dependency, no `.claude`/`CLAUDE.md` edit, no committed graph.** Output is this document; #569 closes with it. The live pass-1 trial ran entirely in a throwaway `/tmp` venv (deleted after), never touching the repo or its hash-pinned env.
- **No paid pass-2 build.** Skipped — it needs an Anthropic **API key** (separate from the maintainer's Claude **Max** subscription), and `graphify benchmark` had already settled the token question on the free graph. The stale-headline hazard (Finding 2) therefore stays analytic.
- **Prior NO-GOs re-listed so they aren't re-proposed without new evidence:** CNN ([#331](https://github.com/DocGerd/hangarfit/issues/331)/[#332](https://github.com/DocGerd/hangarfit/issues/332)), RRT-Connect ([#336](https://github.com/DocGerd/hangarfit/issues/336)), placement-side STRtree ([#540](https://github.com/DocGerd/hangarfit/issues/540)). Graphify-as-doc-nav now joins them.
- **Corpus-and-tooling-specific conclusion.** This NO-GO is measured against hangarfit's curated, auto-loaded Quick-Ref baseline. For a project *without* one — or a non-Claude assistant (Cursor/Aider/Codex) / new human contributor with no Quick-Ref internalized — Graphify's relative value is higher. The judgment is about *this* repo's substitute, not about Graphify in general.
