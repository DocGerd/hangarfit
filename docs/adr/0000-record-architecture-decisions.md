# ADR-0000: Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-05-23
- **Deciders:** [@DocGerd](https://github.com/DocGerd)

## Context & Problem Statement

`hangarfit` is a small, single-maintainer project that nonetheless makes
real architectural decisions: a parts-based collision model instead of
bounding boxes, a determinant-−1 coordinate transform, a random-restart
hill-climbing solver instead of a constraint solver, and so on. Until
now, the *why* of those decisions has lived in `CLAUDE.md`, in commit
messages, and in the maintainer's head. As the project goes public and
attracts (or expects to attract) outside contributors, that informal
record is no longer sufficient: a new reader has no place to find out
why a counter-intuitive design choice was made, what alternatives were
considered, or what would have to change for the decision to be wrong.

## Decision Drivers

- **Decisions survive the people who made them.** A future contributor
  must be able to evaluate, override, or honor a past decision without
  needing a synchronous handover with the maintainer.
- **The rejected branches matter as much as the chosen one.** A
  decision that does not name what it rejected is indistinguishable
  from "we never considered alternatives" — which is the failure mode
  this practice exists to prevent.
- **The format should be cheap to write.** Anything heavier than a
  Markdown file in the repo will not get written. The goal is "low
  friction → high adoption," not "rigorous documentation discipline."
- **The architecture document (Arc42) and the decision record are
  different artifacts.** Arc42 describes *what is*; ADRs describe *why
  it is so*. Mixing them produces a document that is good at neither.

## Considered Options

1. **Per-decision Markdown ADRs in `docs/adr/`** (MADR-flavored).
2. **Decision log appended to the Arc42 §9 page.**
3. **Free-form prose in CLAUDE.md** (the status quo until this ADR).
4. **No formal record** — rely on git history and PR descriptions.

## Decision Outcome

**Chosen option: per-decision Markdown ADRs in `docs/adr/`**, using a
[MADR](https://adr.github.io/madr/)-flavored template
(see [`template.md`](template.md)). Each ADR is a separate file with a
zero-padded monotonic number, lives at the conventional location, and
is linked from `docs/architecture/09-architecture-decisions.md` for
Arc42 readers.

### Why not the Arc42 §9 in-page log?

It scales badly. A single ever-growing section becomes hard to link to
specifically (one giant file with anchor links), hard to update without
merge conflicts when multiple decisions are in flight, and hard to flip
the status of without disturbing surrounding entries. Per-file ADRs
solve all three trivially.

### Why not free-form prose in CLAUDE.md?

CLAUDE.md is the durable project spec, mixing operational guidance
(GitFlow, how to invoke subagents) with domain knowledge (the parts
model, the coordinate convention) with — historically — decision
rationale. Issue [#137](https://github.com/DocGerd/hangarfit/issues/137)
explicitly sets out to slim CLAUDE.md to operational-only content; the
domain knowledge moves to Arc42, and decision rationale moves here.
Keeping decision rationale in CLAUDE.md would re-mix the three roles
this milestone is trying to separate.

### Why not "no formal record, rely on git history"?

Git history is excellent at *what* changed and *when*. It is terrible
at *why* the alternative was rejected — a commit message can mention
"we chose X over Y" but cannot be searched for "show me all decisions
that rejected approach Y," cannot be flipped to "Superseded" later, and
cannot be linked from architecture docs as a stable address. ADRs do
all three.

## Consequences

### Positive

- Decisions become first-class, addressable, queryable artifacts.
- The rejected-branches discipline (≥ 2 considered options) forces the
  author to actually consider alternatives, not just record a fait
  accompli.
- New contributors can read `docs/adr/` end-to-end and understand the
  shape of the project's thinking, not just its code.
- When a decision is later overturned, the superseding ADR can link
  back to the original, preserving the history of the project's
  thinking.

### Negative

- One more file per decision. For trivial decisions, that overhead
  isn't worth it — and the heuristic for what counts as "consequential
  enough for an ADR" is fuzzy. We will sometimes write ADRs that don't
  earn their keep, and sometimes skip ones we should have written.
- Templates accumulate cargo-cult sections if not pruned periodically.
  The template (`template.md`) is allowed to evolve; ADRs already
  written do not retroactively need to match.

### Neutral

- Numbering is global and monotonic. Two ADRs proposed in parallel
  resolve their numbers at PR-rebase time, not at proposal time.

## Compliance

- The template file [`template.md`](template.md) encodes the required
  sections; ADRs missing them are caught at PR review.
- The "≥ 2 considered options" rule is the load-bearing discipline.
  PR review is responsible for enforcing it; there is no lint.
- The ADR README's [Index](README.md#index) must be updated in the
  same PR that introduces a new ADR. Forgetting that is also caught
  at PR review.

## More Information

- Format reference: [MADR — Markdown Any Decision Records](https://adr.github.io/madr/)
- Optional tooling: [`adr-tools`](https://github.com/npryce/adr-tools)
- Related: [Arc42 §9 redirect](../architecture/09-architecture-decisions.md),
  the slim-down of CLAUDE.md tracked in
  [#137](https://github.com/DocGerd/hangarfit/issues/137).
