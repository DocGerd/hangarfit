# ADR-0016: Spread-vs-towability — CLI re-solves spread-off as a backstop when a default-spread layout is un-routable

- **Status:** Proposed

- **Date:** 2026-06-02
- **Deciders:** Patrick Kuhn (DocGerd)

## Context & Problem Statement

`hangarfit solve --render-paths` should produce "a valid layout **plus** a valid
tow path for every plane". Two individually-correct, default-on features compose
into a globally-worse default (issue [#280](https://github.com/DocGerd/hangarfit/issues/280)):
the ADR-0008 spread post-pass *maximizes* inter-plane gaps (pushing planes toward
walls/corners), while the bounded tow planner (ADR-0007 / ADR-0010, per-plane
Hybrid-A\* with `towplanner._MAX_EXPANSIONS`) needs clear approach corridors from
the door cone to each slot. Spread could push a plane into a slot whose approach
lane the bounded search can no longer thread, so `plan_fill` returns
`plans[i] = None`; with every candidate un-routable the CLI returns a bare exit 3
("nothing is towable") — even though the *same* fleet and hangar route cleanly
with `--no-spread`. The tool silently picked the *less* useful of two valid
arrangements. This ADR records how `--render-paths` recovers a tow plan without
making the user know to retry with `--no-spread`.

This is the *backstop* half of the v0.9.0 "tow-friendly placement" work
(Direction A). The *primary* lever is placement — the back-of-hangar fill bias
([#320](https://github.com/DocGerd/hangarfit/issues/320), recorded as the
2026-06-01 amendment to [ADR-0008](0008-inter-plane-spread-soft-preference.md))
keeps the door-side approach corridors clear so the default-spread layout is
tow-routable in the first place. This ADR covers what `--render-paths` does when
placement is *not* enough.

## Decision Drivers

- **"Valid layout + valid path" is the headline UX.** A bare exit 3 reads as
  "these planes can't be towed" when a towable arrangement of the same planes
  demonstrably exists.
- **Never silently swap.** If the rendered arrangement is not the one a plain
  `solve` would emit, the user (and any machine consumer) must be told.
- **Preserve the ADR-0003 determinism contract.** A given `--seed` must yield
  the same result, including across the two-pass fallback.
- **Do not couple the planner into the solver's hot loop.** The static solver
  stays collision-only; tow-routability must not become a term in the seeded
  candidate scoring (the ADR-0008 "isolate the soft logic" driver).
- **Cheap in the common case.** With placement (#320) doing the heavy lifting,
  the backstop should cost nothing when the first pass already routes.

## Considered Options

1. **Automatic spread-off fallback in `--render-paths` (chosen).** When a
   default-spread layout is fully un-routable, re-solve once with `spread=False`,
   tow-plan that, and render it instead — reporting the swap on every channel.
2. **Towability as a soft factor in spread basin-selection.** Fold "is this
   basin tow-routable?" into the ADR-0008 / #267 collect-then-select scoring so a
   routable-but-tighter basin outranks an un-routable maximally-spread one.
3. **Advisory hint only.** Keep exit 3, but emit a stderr hint telling the user
   to retry with `--no-spread`.
4. **Raise `_MAX_EXPANSIONS`.** Give the bounded planner more budget so it
   threads the spread-widened corridors.
5. **Tow-aware spread axis.** Spread only along axes that do not block the
   door-approach corridors, so spread and towability never fight.

## Decision Outcome

**Chosen option: automatic spread-off fallback (option 1)**, because it restores
the "+ valid path" guarantee fully automatically, preserves spread whenever spread
*is* routable (the fallback only triggers on a fully un-routable first pass), and
keeps the solver and planner decoupled — the fallback lives entirely in the CLI
(`cmd_solve`), not the solver. Concretely:

- **Trigger.** Only when `--render-paths` is set, spread was *not* explicitly
  disabled (`args.spread` is true), the first solve returned at least one valid
  layout, and **every** returned plan is `None`
  (`all(plan is None for plan in result.plans)`).
- **Action.** Re-solve once with `SearchConfig(spread=False)` and
  `plan_paths=True`, reusing the **same resolved seed** as the spread pass.
- **Guard.** Accept the swap **only if** the no-spread re-solve actually routes
  at least one candidate (`any(plan is not None …)`). If it routes nothing
  (genuinely too tight — e.g. the placeholder hangar), keep the original spread
  result so exit 3 stands unchanged and no misleading note is printed.
- **Report (never silent).** On a successful swap: a stderr note ("layout
  un-routable with inter-plane spread; re-solved with spread disabled to produce
  tow paths"), a structured `diagnostics.spread_fallback_applied: true` in
  `--json`, and a provenance comment in any `--write-yaml` output. The rendered
  layout is the tighter no-spread arrangement, and the user is told so.

The determinism contract holds because the seed is resolved **once**, up front
(`resolved_seed = args.seed if args.seed is not None else secrets.randbits(32)`),
and shared by both passes; each `solve()` call is individually deterministic for
that seed (ADR-0003), and the fallback is a deterministic function of the first
pass's outcome.

Empirically (issue #280 acceptance probe, develop @ this ADR, seed 1, default
grid heuristic + back-fill on): the 3-plane and 6-plane fills that were exit 3
*before* the #320 placement lever now route at **exit 0 with the fallback never
firing** (`spread_fallback_applied=false`). So placement (#320) is what fixes the
common case; this fallback is the backstop that keeps a stray un-routable
spread-basin from ever reaching the user as a bare exit 3.

### Why not towability as a soft factor in spread selection (option 2)?

It would run `plan_fill` (a full Hybrid-A\* search) for *every* candidate basin
inside the solver's selection loop — expensive, and it couples the tow planner
into the static solver, violating the ADR-0008 driver that keeps the hard
conflict loop collision-only and soft preferences isolated. The chosen fallback
runs `plan_fill` at most twice total (once per pass), only on a fully un-routable
first pass.

### Why not an advisory hint only (option 3)?

A pure hint keeps the bare exit 3 and puts the work on the user — they must know
to retry with `--no-spread`, defeating the "valid layout + valid path" headline.
The chosen option *adopts the hint* (the stderr note) but pairs it with the
automatic re-solve, so the path appears without a second manual invocation.

### Why not raise `_MAX_EXPANSIONS` (option 4)?

A bigger budget is a band-aid: it does not address the geometry tension, costs
deterministic worst-case search time on every routable plane, and the
false-negatives persist for tight-enough corridors. (Separately, #336 *did* make
`grid` the default heuristic and set a global fill-budget cap of 8000 — see the
2026-06-01 amendment to [ADR-0007](0007-tow-path-planner-v1-scope.md) — but as an
efficiency/never-hang measure, **not** as the spread-vs-towability fix.)

### Why not a tow-aware spread axis (option 5)?

It is the most principled long-term answer but the most complex: it requires the
spread post-pass to know the door-approach geometry and constrain its descent
directions accordingly, re-coupling the two modules. The back-fill bias (#320)
captures most of the same benefit far more cheaply (bias planes toward the back
wall, clearing the door side) without the spread pass needing planner knowledge.
Left as possible future work if placement + fallback ever prove insufficient.

## Consequences

### Positive

- `solve --render-paths` no longer emits a bare exit 3 for a fleet+hangar that is
  towable under `--no-spread`; it routes (placement, the common case) or
  transparently re-solves spread-off (the backstop).
- The swap is reported on every channel (human stderr, `--json`, `--write-yaml`),
  so neither a person nor a machine consumer is misled about which arrangement was
  rendered.
- Solver and planner stay decoupled; the static solver remains collision-only and
  the ADR-0008 spread post-pass is unchanged.

### Negative

- Worst-case up to ~2× solve time — but **only** when the first (spread) pass is
  fully un-routable, which the #320 placement lever makes rare.
- The rendered layout after a swap is the *tighter* no-spread arrangement, not the
  roomy spread one a plain `solve` (without `--render-paths`) would emit. This is
  reported, not silent, but it does mean `--render-paths` and a plain `solve` can
  return different layouts for the same scenario+seed.

### Neutral

- The fallback is CLI-only behavior; `solve()` as a library call is unchanged
  (callers choose `spread` and `plan_paths` themselves).
- When the no-spread re-solve also routes nothing (placeholder hangar, genuinely
  too tight), behavior is identical to before this ADR: exit 3, original spread
  layout kept, no note.

## Compliance

- **`tests/test_cli_solve.py`** — the spread-fallback test class pins: the
  re-solve fires only on a fully un-routable spread pass; the swap is reported on
  stderr; `--json` surfaces `spread_fallback_applied` true after a swap and false
  otherwise; `--write-yaml` carries the provenance comment; a fallback that *also*
  routes nothing keeps exit 3 with no misleading note; and the fallback reuses the
  resolved seed (determinism across the two passes).

## More Information

- Related ADRs:
  [ADR-0003 — RR-MC solver algorithm and determinism contract](0003-rr-mc-solver-algorithm.md);
  [ADR-0007 — tow-path planner v1 scope](0007-tow-path-planner-v1-scope.md)
  (2026-06-01 #336 amendment: grid default + fill-budget cap);
  [ADR-0008 — inter-plane spread](0008-inter-plane-spread-soft-preference.md)
  (2026-06-01 #320 amendment: back-of-hangar fill bias — the *primary* placement
  lever this fallback backstops);
  [ADR-0010 — Reeds–Shepp motion model](0010-reeds-shepp-motion-model.md).
- Related issues / PRs:
  [#280](https://github.com/DocGerd/hangarfit/issues/280) (umbrella),
  [#320](https://github.com/DocGerd/hangarfit/issues/320) (placement lever),
  [#336](https://github.com/DocGerd/hangarfit/issues/336) (planner efficiency).
