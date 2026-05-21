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

> Each aircraft is a list of **parts**. Every part is an oriented rectangle in plan view with a height range `[z_bottom_m, z_top_m]`. Fuselage, wing, each strut, and the tail (where modeled) are all parts — the closed set of `PartKind` values lives in `models.py`.
>
> **Collision rule**: two parts from different aircraft conflict iff **both** hold:
>
> 1. **In plan view**: `polygon_a.distance(polygon_b) < clearance_m` (the closest distance between the polygons is less than the horizontal clearance).
> 2. **In height**: the gap between `[z_bottom_a, z_top_a]` and `[z_bottom_b, z_top_b]` is less than `wing_layer_clearance_m` (treating overlap as a gap of 0).
>
> Parts of the *same* aircraft are never checked against each other (a Husky's wing and its own strut "overlap" by design).

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

**The transform** (plane-local → world). `heading_deg` is the **compass-style angle of the nose**, measured from world `+y` (the "deeper into hangar" direction), CW positive. Concretely:

- At `heading_deg = 0`, the nose vector in world coords is `(0, 1)`.
- At `heading_deg = 90°`, the nose vector is `(1, 0)`.
- At `heading_deg = 45°`, the nose vector is `(√2/2, √2/2)` — pointing into the (+x, +y) quadrant.

A part with plane-local offset `(u, v)` (u forward, v right) at placement `(px, py, heading)` lands at:

```
world_x = px + u·sin(heading) + v·cos(heading)
world_y = py + u·cos(heading) − v·sin(heading)
```

Equivalently, the linear part is `[[sin h, cos h], [cos h, −sin h]]` applied to `(u, v)`. **This matrix has determinant −1** — it is a rotation **composed with a reflection**, not a pure rotation. Two ways to land here: (a) compass headings rotate CW while standard math angles rotate CCW (one sign flip), and (b) the plane-local right-handed-feeling axes (forward, right) end up describing a left-handed mapping when laid against the (right-along-door, deeper-into-hangar) world frame (a second sign flip).

**Do NOT** drop in a textbook CCW rotation matrix `[[cos α, −sin α], [sin α, cos α]]` and call it done — the result will be silently wrong, and worse, will *look* correct in tests at headings 0°, 90°, 180° because those are the symmetric cases. Tests must include at least one **non-axis-aligned heading** (45° is canonical) to catch any regression: at heading 45° the nose vector should be `(√2/2, √2/2)`, and a plane-local part at `(u=0, v=1)` (one meter to the right of plane origin) should land at world `(√2/2, −√2/2)` — right and toward the door, never up and into the hangar.

### Door model in Phase 1

The door is a **visual marker only**. All aircraft parts must fit fully inside the hangar rectangle for the layout to be considered valid. The door is rendered as a gap in the front wall by the visualizer but doesn't affect collision logic.

### Default clearances

Both clearances are configurable in `data/hangar.yaml` (`Hangar.clearance_m`, `Hangar.wing_layer_clearance_m`).

| Clearance | Default | Key in `hangar.yaml` |
|---|---|---|
| Horizontal | 0.30 m | `clearance_m` |
| Vertical | 0.20 m | `wing_layer_clearance_m` |

---

## Phase 1 deliverables

Current status of the Phase 1 cut. Only the CLI remains before the first tagged release (`release/0.1.0`).

| # | Deliverable | Issue | Status |
|---|---|---|---|
| 1 | `data/fleet.yaml` — 9 aircraft, parts model, **placeholder dimensions** flagged with `measured: false` | #3 | ✅ shipped |
| 2 | `data/hangar.yaml` — hangar dimensions + door + maintenance bay (placeholders) | #3 | ✅ shipped |
| 3 | `src/hangarfit/collisions.py` — the collision checker (the heart of Phase 1) | #5 | ✅ shipped |
| 4 | `src/hangarfit/visualize.py` — matplotlib top-down PNG renderer | #6 | ✅ shipped |
| 5 | `src/hangarfit/cli.py` — `hangarfit check layouts/example.yaml --render out.png` | #7 | ⏳ **next** |
| 6 | Strut-aware golden-test suite in `tests/test_collisions.py` — same-height wing overlap, high-over-low height-disjoint pass, strut-blocks-nesting, inboard / outboard strut-free nesting, maintenance-bay rule, all-9-planes valid layout (the cart rule is exercised separately at `Layout` construction; see module map) | #5 | ✅ shipped |

The strut-aware golden tests are the canary that the parts model is intact. If they pass, the geometry is trustworthy on the current (placeholder) data.

## Where things live (module map)

| File | Responsibility |
|---|---|
| `src/hangarfit/models.py` | Frozen dataclasses + invariants (`Aircraft`, `Hangar`, `Layout`, `Conflict`, `CheckResult`). Cross-reference rules (cart rule, `movement_mode` ↔ `on_carts`, maintenance plane in fleet & placed) are enforced in `Layout.__post_init__`. |
| `src/hangarfit/loader.py` | YAML → models. Expands the high-level `struts:` block into two mirrored strut `Part`s before constructing `Aircraft`. |
| `src/hangarfit/geometry.py` | Plane-local → world transform (the determinant −1 trap lives here) and `aircraft_parts_world()`. |
| `src/hangarfit/collisions.py` | The `check(layout)` entry point. Enforces hangar bounds, maintenance-bay position (centroid of the designated plane's fuselage parts is in the back strip; if that plane has no fuselage parts, an explicit `maintenance_no_fuselage` conflict is emitted rather than silently passing), and pairwise parts overlap. **Not here:** the cart rule (already enforced upstream in `Layout`). |
| `src/hangarfit/visualize.py` | Top-down PNG renderer. Forces a headless matplotlib backend at import time so it runs in CI / pytest without a display server. When a `CheckResult` is passed, validates that its conflicts reference only planes from the layout, then overdraws the conflicting parts in red. |
| `src/hangarfit/cli.py` | **Not yet shipped — issue #7.** |
| `tests/fixtures/*.yaml` | One YAML per scenario, `valid_*` / `invalid_*` naming. Add new collision regressions by dropping in a fixture, not by writing geometry literals in Python. |
| `tests/fixtures/test_hangar_large.yaml` | Test-only larger hangar (30 × 25 m, length × width). Used by `valid_all_nine_planes.yaml` because the placeholder fleet's strut bracing forces ~2.6 m of x-clearance between strut-braced planes whose fuselage y-bands overlap — which doesn't fit in the placeholder 25 × 18 m hangar (see `data/hangar.yaml`). This is a placeholder-dimension artifact, not a checker bug. Will go away once real measurements arrive. See the fixture header for full reasoning. |

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
| `main` | Production / release-tagged. | **No** |
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

Use the best-fitted model for the task. The model class to pick is "as much reasoning as the work needs" — heavy for novel design and deep review, lighter for mechanical work.

- **`pr-review-toolkit:code-reviewer`** — main PR review pass on every PR.
- **`pr-review-toolkit:comment-analyzer`** — for PRs that meaningfully change docs (README, CLAUDE.md, docstrings).
- **`pr-review-toolkit:silent-failure-hunter`** — for PRs touching loader or collision code.
- **`pr-review-toolkit:type-design-analyzer`** — when `models.py` changes.
- **`feature-dev:code-architect`** — only for genuinely novel design decisions, not routine implementation.

Most coding goes direct in-session. Subagent dispatch is for review work and isolated heavy lifts.

---

## Project-local Claude Code config

The `.claude/` directory holds team-shared Claude Code settings (currently: a PostToolUse pytest hook that auto-runs tests after edits under `src/hangarfit/` or `tests/`). See [.claude/README.md](.claude/README.md) for what's there and how to disable per-contributor via a gitignored `.claude/settings.local.json`.

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

# CI: GitHub Actions runs `pytest` on Python 3.11 + 3.12 for PRs into
# develop/main (see .github/workflows/ci.yml). No coverage gate yet.

# Phase 1 acceptance smoke test (once CLI lands)
hangarfit check layouts/example.yaml --render out.png

# GitFlow loops
git switch develop && git pull
git switch -c feature/<slug>
# ... work ...
git push -u origin feature/<slug>
gh pr create --base develop --title "..." --body "Closes #N ..."
```
