# ADR-NNNN: <Short, declarative title — the decision, not the topic>

- **Status:** Proposed | Accepted | Deprecated | Superseded by [ADR-XXXX](XXXX-slug.md)
  <!-- Proposed at PR-open; Accepted at PR-merge; Deprecated when the
       decision no longer applies but is not replaced; Superseded when
       a later ADR replaces it (link the replacement). -->

- **Date:** YYYY-MM-DD
- **Deciders:** <names / GitHub handles>

## Context & Problem Statement

<2–5 sentences. What situation forced this decision? What is the
question this ADR answers? Write so a reader can follow without
context from the PR or the milestone.>

## Decision Drivers

<The criteria the decision had to optimize for. List the ones that
actually drove the outcome, not every possible consideration.>

- <Driver 1>
- <Driver 2>
- …

## Considered Options

**At least two options must be listed.** An ADR without rejected
options is not an ADR — it is a description, and belongs in
`docs/architecture/` instead.

1. **<Option A — the chosen one, named in concrete terms>**
2. **<Option B — a genuine alternative, not a strawman>**
3. <Option C, D, … if any>

## Decision Outcome

**Chosen option: <Option A>**, because <one or two sentences naming the
deciding factor in the trade-off>.

### Why not <Option B>?

<One paragraph per rejected option. Be specific — "doesn't scale" is
not a reason; "would not let us express the height-disjoint pass-through
case from the parts model" is. Future readers should be able to see
the rejected branch on its own terms.>

### Why not <Option C>? *(if applicable)*

<Same shape.>

## Consequences

### Positive

- <What we gain.>

### Negative

- <What we accept as cost.>

### Neutral

- <Effects that are real but neither good nor bad.>

## Compliance

<How do we know the decision is being followed? Examples:
"`tests/test_geometry.py::test_heading_45_canary` is the compliance
check"; "Enforced by ruff rule X"; "Documented in
`docs/architecture/§8` — no automated check, breakage caught at
PR review.">

## More Information

- Related ADRs: <ADR-XXXX, ADR-YYYY>
- Related specs: <link to `docs/superpowers/specs/...`>
- Related issues / PRs: <#N, #M>
- External references: <papers, docs, blog posts>
