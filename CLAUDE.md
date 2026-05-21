# Project context — hangarfit

This file is the durable context for the project. Read it first in any new session.

---

## What this project is

`hangarfit` is an **on-demand exception tool** for a flying club: when the standard hangar parking layout breaks (delayed return, surprise maintenance, etc.), it helps find *a* valid alternative arrangement.

The tool checks whether a hand-authored candidate layout is physically valid (no fuselage / wing / strut collisions, fits in the hangar, maintenance plane in the right spot) and renders a top-down PNG so a human can eyeball it.

**Phase 1 scope** (the current focus): build the substrate — aircraft data model, hangar model, collision checker, visualizer, CLI. **No planner / search / optimization** in Phase 1.

---

## The fleet (9 aircraft)

| ID | Name | Wing | Gear | Movement mode | Wing strut? | Notes |
|---|---|---|---|---|---|---|
| `scheibe_falke` | Scheibe SF-25E Falke | High | Monowheel + outriggers | `always_cart` | No (cantilever) | Outriggers folded into wing footprint |
| `aviat_husky` | Aviat Husky A-1 | High | Tailwheel | `always_own_gear` | **Yes** | |
| `fuji` | Fuji FA-200 | **Low** | Nosewheel | `always_own_gear` | No (cantilever) | The only low-wing |
| `wild_thing` | Wild Thing | High | Nosewheel | `always_cart` | **Yes** | |
| `zlin_savage` | Zlin Savage | High | Tailwheel | `always_cart` | **Yes** | |
| `cessna_140` | Cessna 140 | High | Tailwheel | `cart_eligible` | **Yes** | V-strut treated as one strut per side |
| `cessna_150` | Cessna 150 | High | Nosewheel | `cart_eligible` | **Yes** | |
| `ctsl` | Flight Design CTSL | High | Nosewheel | `cart_eligible` | No (cantilever) | |
| `fk9_mkii` | FK9 Mk II | High | Nosewheel | `cart_eligible` | **Yes** | |

**Cart rule**: of the 4 `cart_eligible` planes, **at most one** uses the spare carts at a time. Cart mounting is operationally free — the algorithm can pick any cart assignment.

**Motion (relevant for the future planner, not Phase 1 collision)**:
- On carts: holonomic (any direction, including sideways).
- On own gear: non-holonomic (Dubins-path-style curves bounded by turn radius).

---

## The hangar

Stack-like layout: deep, one door at the front. The back-most spot doubles as the **maintenance bay** (curtained off when in use); a plane scheduled for maintenance must already be parked at the back. All dimensions in `data/hangar.yaml` are **placeholders** until real measurements are taken.

---

## The parts model (the most important rule)

> Each aircraft is a list of **parts**. Every part is an oriented rectangle in plan view with a height range `[z_bottom_m, z_top_m]`. Fuselage, wing, and each strut are all parts.
>
> **Collision rule**: two parts from different planes conflict iff their 2D polygons overlap (with horizontal clearance) AND their z-ranges overlap (with vertical clearance).

This is the single most important geometric rule in the project. Every future feature sits on top of it. If the parts model or the collision rule is wrong, every downstream layout will be wrong.

### Why parts (and not a single bounding box)?

- A single bbox can't represent the legality of a **high-wing's wingtip overlapping a low-wing's fuselage area in plan view**: the heights differ, so it's fine, but a flat bbox would mark it as a collision.
- **Wing struts** (on Husky, Wild Thing, Zlin Savage, both Cessnas, FK9) occupy a thin column from lower fuselage out to the underside of the wing. The "wing volume" of a strut-braced plane is NOT free — another plane's wing can't nest through where the strut lives. The parts model expresses this directly; a bbox model cannot.

### YAML convenience: the `struts:` block

For strut-braced planes, `fleet.yaml` accepts a high-level `struts:` block that the loader expands into two mirrored strut `Part`s (one per side). This keeps the YAML readable while still funneling into the uniform parts model internally.

---

## Coordinate convention

**Hangar (world) coordinates**: origin at the front-left corner, looking down.

```
       +x ->
  +---[door]-------+
  |                |
  | y (deeper)     |
  v                |
  |                |
  +----------------+
```

- `+x` runs right along the door wall.
- `+y` runs deeper into the hangar.
- **Heading 0°** = nose pointing toward `+y` (deeper into hangar).
- **Heading 90°** = nose toward `+x` (right).

**Plane-local coordinates** (used in `fleet.yaml` part offsets):

- Origin = plane reference point (main-gear / cart centroid).
- `+x` = forward (toward nose).
- `+y` = right (toward right wingtip).

**The transform**: at `heading_deg = 0`, plane `+x` should map to world `+y` (nose deeper into hangar). So the plane-local-to-world rotation is `(heading_deg - 90°)`, not `heading_deg`. **This is the off-by-90° trap of the project** — tests must include a non-90°-aligned heading (e.g., 45°) to catch any regression.

### Door model in Phase 1

The door is a **visual marker only**. All aircraft parts must fit fully inside the hangar rectangle for the layout to be considered valid. The door is rendered as a gap in the front wall by the visualizer but doesn't affect collision logic.

### Default clearances

| Clearance | Default | Configurable in |
|---|---|---|
| Horizontal | 0.30 m | `data/hangar.yaml` → `clearance_m` |
| Vertical | 0.20 m | `data/hangar.yaml` → `wing_layer_clearance_m` |

---

## Phase 1 deliverables

1. `data/fleet.yaml` — 9 aircraft, parts model, **placeholder dimensions** flagged with `measured: false`.
2. `data/hangar.yaml` — hangar dimensions + door + maintenance bay (placeholders).
3. `src/hangarfit/collisions.py` — the collision checker (the heart of Phase 1).
4. `src/hangarfit/visualize.py` — matplotlib top-down PNG renderer.
5. `src/hangarfit/cli.py` — `hangarfit check layouts/example.yaml --render out.png`.
6. **12 golden tests** in `tests/test_collisions.py` covering all conflict types, including the strut-aware cases (the canary that the parts model is intact).

### Out of scope for Phase 1

- No planner / search / optimization.
- No movement-sequence planning (no "Tower of Hanoi" reshuffling).
- No tracking of current hangar state across runs.
- No GUI / web frontend.
- No handling of late arrivals.

---

## Development workflow

**Strict GitFlow + issue-driven + PR-review on every change. The user is the only approver and merger.**

### Branching

| Branch | Purpose | Direct push allowed? |
|---|---|---|
| `main` | Production / release-tagged. | **No** (single empty bootstrap commit excepted) |
| `develop` | Integration; default branch on GitHub. | No, only via PR from `feature/*` |
| `feature/<slug>` | One per issue; off `develop`. | Yes (Claude works here) |
| `release/<version>` | Cut from `develop`, PR'd into both `main` and `develop`. | No, only via PR |
| `hotfix/<slug>` | Only if needed; off `main`. | No, only via PR |

### Per-PR process

1. Branch `feature/<slug>` off `develop`. Work, commit.
2. Open PR with `gh pr create` — base `develop`, body includes `Closes #N`.
3. Invoke `/pr-review` (or the `pr-review-toolkit:review-pr` skill).
4. Convert each finding into a **review thread on the diff** (via `gh pr review` line comments / `gh api .../pulls/<n>/comments`). Findings never live only in chat.
5. Resolve every thread: fix the code (preferred) or reply with rationale, then mark resolved.
6. If the changes were non-trivial, re-run the review.
7. Tell the user the PR is **clean and ready for final review**. The user approves and merges. **Never `gh pr merge` from Claude.**

### Issues

- Every change is tracked by a GitHub issue. No code without an issue.
- Issues are organized into milestones (one milestone = one releasable cut).
- PR bodies link to issues with `Closes #N` / `Fixes #N` (the body, not the title — only body syntax auto-closes).

---

## Subagents

Use the best-fitted model for the task.

- **`pr-review-toolkit:code-reviewer`** (Sonnet 4.6) — main PR review pass on every PR.
- **`pr-review-toolkit:silent-failure-hunter`** (Sonnet) — for PRs touching loader or collision code.
- **`pr-review-toolkit:type-design-analyzer`** (Sonnet) — when `models.py` changes.
- **`feature-dev:code-architect`** (Opus 4.7) — only for genuinely novel design decisions, not routine implementation.

Most coding goes direct in-session. Subagent dispatch is for review work and isolated heavy lifts.

---

## Worktrees

Allowed but not the default. Use only when two feature branches need parallel work (e.g., long-running test suite while writing the visualizer). For sequential issue flow, plain branch checkout is simpler.

---

## Open questions / TBD before trusting output

- **Real measurements** for every aircraft (`measured: false` in `fleet.yaml`). All current dimensions are eyeballed placeholders.
- **Real hangar measurements** (`data/hangar.yaml`) — length, width, door position and width, maintenance bay depth.

The collision checker will run on placeholder data, but until the measurements are real, the output is illustrative only.

---

## Useful commands

```bash
# Install
pip install -e ".[dev]"

# Run tests
pytest

# Phase 1 acceptance smoke test (once CLI lands)
hangarfit check layouts/example.yaml --render out.png

# GitFlow loops
git switch develop && git pull
git switch -c feature/<slug>
# ... work ...
git push -u origin feature/<slug>
gh pr create --base develop --title "..." --body "Closes #N ..."
```
