# Architecture documentation

This directory documents the architecture of `hangarfit` following a slim
subset of the [Arc42](https://arc42.org/) template. Architectural decisions
(the *why* behind the architecture) live separately in
[`docs/adr/`](../adr/) as Architecture Decision Records.

## Reading order

If you are new to the project, read in this order:

1. **§1 Introduction & Goals** — what `hangarfit` is, what it must do well,
   who it is for.
2. **§3 Context & Scope** — how `hangarfit` fits into the flying-club
   operations around it, and what is explicitly *not* in scope.
3. **§2 Architecture Constraints** — the non-negotiables that shaped every
   later decision.
4. **§4 Solution Strategy** — the headline choices that explain "why does
   the code look like this?"
5. **§5 Building Block View** and **§6 Runtime View** — the static module
   map and the dynamic flow of a `hangarfit check` / `hangarfit solve`
   invocation.
6. **§8 Crosscutting Concepts** — the domain rules (parts model, coordinate
   convention, clearances) that show up everywhere.
7. **§9 Architecture Decisions** — the ADR index, when you want to know
   *why* a specific choice was made and what alternatives were rejected.

## Section index

| §  | Section                  | File                                                                   | Status |
|----|--------------------------|------------------------------------------------------------------------|--------|
| 1  | Introduction & Goals     | [`01-introduction-and-goals.md`](01-introduction-and-goals.md)         | ✅ shipped |
| 2  | Architecture Constraints | [`02-architecture-constraints.md`](02-architecture-constraints.md)     | ✅ shipped |
| 3  | Context & Scope          | [`03-context-and-scope.md`](03-context-and-scope.md)                   | ✅ shipped |
| 4  | Solution Strategy        | `04-solution-strategy.md`                                              | TBD ([#133](https://github.com/DocGerd/hangarfit/issues/133)) |
| 5  | Building Block View      | `05-building-block-view.md`                                            | TBD ([#133](https://github.com/DocGerd/hangarfit/issues/133)) |
| 6  | Runtime View             | `06-runtime-view.md`                                                   | TBD ([#133](https://github.com/DocGerd/hangarfit/issues/133)) |
| 8  | Crosscutting Concepts    | `08-crosscutting-concepts.md`                                          | TBD ([#134](https://github.com/DocGerd/hangarfit/issues/134)) |
| 9  | Architecture Decisions   | [`09-architecture-decisions.md`](09-architecture-decisions.md) → [`docs/adr/`](../adr/) | ✅ shipped |

## Sections deliberately omitted

The full Arc42 template has twelve sections. Four are omitted on purpose:

- **§7 Deployment View** — `hangarfit` is a single-binary CLI with no
  deployment topology; the entire "deployment" is `pip install .`.
- **§10 Quality Requirements** — captured inline in §1 quality goals
  instead of as a separate quality-scenario catalogue.
- **§11 Risks & Technical Debt** — tracked as a live ledger in
  [GitHub Milestones](https://github.com/DocGerd/hangarfit/milestones)
  and [Issues](https://github.com/DocGerd/hangarfit/issues), where they
  stay current; a static Markdown copy would drift.
- **§12 Glossary** — the project vocabulary (fleet, hangar, parts model,
  collision rule) is defined where it is introduced in §1 and §8.

Stub sections would suggest cargo-culted Arc42; the slim form keeps every
section load-bearing.

## ADRs

Decisions belong in [`docs/adr/`](../adr/). The Arc42 §9 page
([`09-architecture-decisions.md`](09-architecture-decisions.md)) is a thin
redirect to that directory.
