# §4 Solution Strategy

This section names the architecture-shaping choices that explain why the
codebase looks the way it does. Each headline below survived a
considered alternative (or several); the detailed rationale lives in
[the ADRs](../adr/). This section is the index of which choice belongs
to which ADR, not a re-derivation.

## Headline decisions

Ordered roughly Phase 1 → Phase 2a → cross-cutting scope and operations
choices: the geometric substrate first (parts model, transform), then
the solver layered on top of it (algorithm, constraint posture), then
the surrounding shape of the tool (delivery, documentation).

### Aircraft geometry as a list of parts, not a single bounding box

Every aircraft carries a tuple of `Part`s — each an oriented rectangle
in plan view plus a height range `[z_bottom_m, z_top_m]`. The collision
checker iterates over part-pairs across distinct aircraft and applies a
two-clause predicate: plan-view distance under `clearance_m` AND height
gap under `wing_layer_clearance_m`.

A single 2D bbox cannot express that a high-wing's wingtip is allowed
to project over a low-wing's fuselage area at different height. A single
3D bbox cannot express that the wing-layer column of a strut-braced
plane is occupied by its strut (and therefore blocks another plane's
wing from nesting through). The parts model expresses both directly.

See [ADR-0001](../adr/0001-aircraft-parts-model.md).

### The plane-local → world transform has determinant −1

The linear part of the heading transform is
`[[sin h, cos h], [cos h, −sin h]]` — determinant −1, a rotation composed
with a reflection. Two simultaneous sign flips (compass-CW vs. math-CCW
convention; left-handed plane-local-vs-world handedness mismatch) produce
the apparent oddity. A textbook CCW rotation matrix would silently break
every layout at non-axis-aligned headings, while passing tests at the
symmetric headings 0°, 90°, 180°, 270°.

This is the most "looks like a bug, is intentional" decision in the
project. The 45° canary test in `tests/test_geometry.py` and the
`geometry-invariant-guard` review-time subagent collectively keep
contributors from "fixing" it.

See [ADR-0002](../adr/0002-determinant-minus-one-transform.md).

### Random-restart min-conflicts (RR-MC) for the static layout solver

`hangarfit solve` is a random-restart hill climber with min-conflicts
perturbation, operating on continuous `(x_m, y_m, heading_deg)` state.
A constraint solver (Z3, MiniZinc) was rejected because quantising the
state to a grid loses fidelity at the placeholder fleet's clearance
budgets. Gradient-based methods stall on the parts-model collision
boundary's discontinuous penetration gradient. RR-MC handles the
continuous, non-smooth, deterministic-reproducibility-required problem
shape directly, and trivially extends to "K diverse alternatives" via
restart count.

See [ADR-0003](../adr/0003-rr-mc-solver-algorithm.md) for the algorithm
and [ADR-0004](../adr/0004-diversity-metric.md) for the diversity filter
that sits on top.

### Hard constraints first; soft preferences as isolated post-passes

The solver answers a satisficing question — "find *a* valid layout
under these hard constraints" — rather than an optimisation question
("find the *best* layout by these soft preferences"). Constraints are
exclusively HARD: maintenance plane, per-plane `pin` (full
Placement), per-plane `force_on_carts`. There is no "prefer this
region," no "minimise total movement vs baseline," no weighted
multi-objective in the conflict-resolution loop itself.

Soft preferences ship as **isolated post-passes** that run only after
a layout is already valid. This keeps the hard-constraint
determinism contract ([ADR-0003](../adr/0003-rr-mc-solver-algorithm.md))
unaffected. The first shipped soft preference is the **inter-plane
spread post-pass** (`solver._spread`, #145): once a layout reaches
`(0, 0.0)`, a repulsion-energy minimisation (`Σ exp(−gap/scale)`)
maximises inter-plane separation while preserving validity. See
[ADR-0008](../adr/0008-inter-plane-spread-soft-preference.md).

If further soft-constraint optimisation use cases materialise, each
gets its own ADR and, if possible, its own isolated post-pass rather
than a new key in the hard score tuple.

### CLI + JSON + optional PNG; nothing else

`hangarfit` is a single-binary CLI invoked from a terminal. It reads
local YAML, writes JSON to stdout (or a human-readable status to
stderr), and optionally a PNG render. No network calls, no daemon,
no database, no persistent state between invocations, no GUI.

This shape matches the operational pattern — an operator runs the tool
on a laptop in a hangar office, on demand, when the standard layout
breaks. Anything browser-shaped or service-shaped would introduce
auth, hosting, and uptime concerns that the actual usage does not
require.

See [§2 Architecture Constraints](02-architecture-constraints.md) for
the constraint statement and
[§3 Context & Scope](03-context-and-scope.md) for the operational
rationale.

### Plain Markdown docs + ADRs; no MkDocs or Sphinx

This documentation set is plain Markdown rendered by GitHub. There is
no MkDocs / Sphinx / Docusaurus build step. Mermaid diagrams render
natively in GitHub Markdown.

Choosing a documentation site generator is itself an architectural
decision that would deserve its own ADR. Until that decision is made,
GitHub's built-in rendering is enough — and the absence of a build step
removes one whole class of "did the docs publish?" failure modes.

## What the headline decisions do *not* cover

The headline decisions above are about the *shape* of the system. They
do not cover:

- **Specific data structures** — those are documented in §5 Building
  Block View where each module's responsibilities are named.
- **Specific runtime sequences** — those are in
  [§6 Runtime View](06-runtime-view.md).
- **Quality requirements** — captured inline in
  [§1 quality goals](01-introduction-and-goals.md#quality-goals).
- **Crosscutting concepts** like the coordinate convention, clearances,
  and the testing posture — those are in
  [§8 Crosscutting Concepts](08-crosscutting-concepts.md).

If a decision belongs in this section but is missing, the right move is
to add an ADR first (the *why*) and reference it here second (the
*what shape*).
