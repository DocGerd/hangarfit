# Architecture Decision Records

This directory captures the *why* behind the architecture of `hangarfit`.
The architecture itself — what the system is and how its parts fit
together — lives in [`docs/architecture/`](../architecture/). The ADRs
here record the consequential decisions that shaped that architecture,
the alternatives that were considered and rejected, and the consequences
the team is signing up for.

## Why we record decisions

Most code surfaces *what* was done. The lasting question is *why*: why
this approach instead of an obvious alternative, what tradeoff was the
deciding factor, what would have to change for the choice to be wrong.
A reader six months from now should be able to answer that question
without asking the maintainer.

The deeper rationale is captured in [ADR-0000](0000-record-architecture-decisions.md).

## Format

We use a [MADR](https://adr.github.io/madr/)-flavored Markdown template
(see [`template.md`](template.md)). Every ADR has the same structure:

- **Status** — Proposed / Accepted / Deprecated / Superseded by ADR-XXXX.
- **Context & Problem Statement** — what forced the decision.
- **Decision Drivers** — what the choice had to optimize for.
- **Considered Options** — at least two; ADRs without rejected options
  do not earn their keep.
- **Decision Outcome** — the chosen option, with the reasoning.
- **Consequences** — positive, negative, and neutral consequences the
  team accepts.
- **Compliance** — how we verify the decision is followed (tests,
  lints, conventions).
- **More Information** — links to specs, code, related ADRs.

## Numbering convention

- ADRs are numbered with **four-digit, zero-padded, monotonically
  increasing** integers: `0000`, `0001`, `0002`, …
- Numbers are **never reused**, even when an ADR is deprecated or
  superseded. A deprecated ADR stays in the directory with its status
  updated; its number is still its name.
- The next available number is `current_highest + 1`. If two ADRs are
  proposed in parallel, the second one to land takes the next number.
  Resolve at PR-rebase time, not at proposal time: the later-landing PR
  renames its file (`git mv NNNN-old-slug.md MMMM-old-slug.md`), updates
  the `# ADR-MMMM:` header inside the file, updates the
  [Index](#index) entry, and updates any cross-references in other
  ADRs that pointed at the old number. The earlier-landing PR is
  not touched.
- ADR-0000 is reserved for the meta-decision "we use ADRs."

## How to author a new ADR

1. Pick the next number. The exact incantation is
   `ls docs/adr/[0-9][0-9][0-9][0-9]-*.md | tail -1` to see the current
   highest; add one and zero-pad to four digits.
2. Copy `template.md` to `NNNN-short-slug.md`, using kebab-case for the
   slug.
3. Fill in the sections. Honor the "≥ 2 considered options" rule.
4. Open a PR per the project's standard
   [GitFlow workflow](../../CLAUDE.md#development-workflow). Link the
   ADR from the **Index** section below in the same PR.
5. ADR status starts at **Proposed**. It flips to **Accepted** when the
   PR is merged.

When a later ADR replaces this one, update this one's status to
**Superseded by ADR-XXXX** in a follow-up PR. Do not delete the
superseded ADR — it remains the record of what we believed at the time.

There is an optional helper, [`adr-tools`](https://github.com/npryce/adr-tools),
that automates numbering (`adr new "Title"`). It is not required; the
manual workflow above is canonical.

## Index

| ADR  | Title                                                                                                  | Status   |
|------|--------------------------------------------------------------------------------------------------------|----------|
| 0000 | [Record architecture decisions](0000-record-architecture-decisions.md)                                 | Accepted |
| 0001 | [Aircraft geometry as a list of parts](0001-aircraft-parts-model.md)                                   | Accepted |
| 0002 | [Plane-local → world transform has determinant −1](0002-determinant-minus-one-transform.md)            | Accepted |
| 0003 | [Random-restart min-conflicts (RR-MC) for the static layout solver](0003-rr-mc-solver-algorithm.md)    | Accepted |
| 0004 | [Diversity metric for K alternatives (edit count with per-plane thresholds)](0004-diversity-metric.md) | Accepted |
| 0005 | [Maintenance bay rule — fuselage centroid in back strip](0005-maintenance-bay-rule.md)                 | Superseded by [ADR-0006](0006-bay-intrusion-maintenance-rule.md) |
| 0006 | [Maintenance bay rule — `bay_intrusion` on any non-occupant vertex strictly inside the bay rectangle](0006-bay-intrusion-maintenance-rule.md) | Accepted |
| 0007 | [Tow-path planner v1 — empty-hangar fill, Dubins-only, cart-as-own-gear](0007-tow-path-planner-v1-scope.md) | Accepted (fork-2 "Dubins-only" superseded by [ADR-0010](0010-reeds-shepp-motion-model.md)) |
| 0008 | [Inter-plane spread soft preference (repulsion-energy surrogate for maximin)](0008-inter-plane-spread-soft-preference.md) | Accepted |
| 0009 | [Single supported Python — 3.12, anchored to the distro LTS](0009-single-supported-python-version.md) | Accepted |
| 0010 | [Reeds–Shepp motion model — towplanner v2](0010-reeds-shepp-motion-model.md) | Accepted |
| 0011 | [Linear history strategy under GitFlow — squash feature merges, accept release merge commits, target a clean first-parent mainline](0011-linear-history-strategy-under-gitflow.md) | Superseded by [ADR-0014](0014-merge-commit-only-history-strategy.md) |
| 0012 | [Split the fuselage into front/aft so a wing may overhang a tail but not a cockpit](0012-fuselage-front-aft-split.md) | Accepted |
| 0013 | [Wheel positions are canonical per-aircraft data; turn_radius_m stays empirical, cross-checked at load](0013-wheels-canonical-data.md) | Accepted |
| 0014 | [Merge-commit-only history under GitFlow — squash/rebase disabled as a release-safety guardrail](0014-merge-commit-only-history-strategy.md) | Proposed |
| 0015 | [Wheels do not participate in the static collision model (gear stays render/motion data)](0015-wheels-not-in-collision-model.md) | Proposed |
