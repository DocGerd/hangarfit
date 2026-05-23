# §6 Runtime View

Two scenarios cover the operational use of `hangarfit`: validating a
candidate layout (`hangarfit check`) and searching for a valid layout
(`hangarfit solve`). Both are short-lived CLI invocations — there is
no daemon, no long-running process, no stateful session.

## Scenario 1: `hangarfit check layouts/example.yaml --render out.png`

The Phase 1 acceptance path. The operator has a candidate layout YAML
and wants a yes/no plus a visual.

```mermaid
sequenceDiagram
    participant Op as Operator
    participant CLI as cli.py
    participant Loader as loader.py
    participant Models as models.py
    participant Coll as collisions.py
    participant Geo as geometry.py
    participant Viz as visualize.py
    participant FS as Filesystem

    Op->>CLI: hangarfit check layout.yaml --render out.png
    CLI->>FS: read layout.yaml, fleet.yaml, hangar.yaml
    CLI->>Loader: parse YAML
    Loader->>Models: construct Aircraft, Hangar, Layout
    Note over Models: __post_init__ enforces<br/>cart rule, maintenance invariants
    Models-->>CLI: Layout (structurally valid)
    CLI->>Coll: check(layout)
    Coll->>Geo: aircraft_parts_world() for each placed plane
    Geo-->>Coll: world-coordinate Part polygons
    Note over Coll: hangar bounds → maintenance →<br/>pairwise parts overlap
    Coll-->>CLI: CheckResult (conflicts + total_penetration_m2)
    CLI->>Viz: render(layout, check_result)
    Viz->>FS: write out.png
    CLI->>Op: stdout JSON / stderr status + exit code (0 / 1 / 2)
```

The flow is strictly linear — there are no loops, no retries, no
parallelism. The same input produces the same output deterministically.

**Failure modes:**

- File-not-found, bad YAML, or invariant violation → exit code 2; the
  CLI prints a structured error and does not write a PNG.
- Layout structurally valid but geometrically invalid (`check()`
  returns conflicts) → exit code 1; the PNG (if requested) is still
  written, with conflicting parts overdrawn in red. This is on
  purpose: the operator wants the visual *especially* when the layout
  is broken.
- Everything OK → exit code 0; the PNG (if requested) shows the layout
  in neutral colors with no red overlay.

## Scenario 2: `hangarfit solve scenario.yaml --seed 42 --alternatives 3 --render out_{i}.png`

The Phase 2a path. The operator has a scenario (fleet, hangar,
constraints, optional pins) and wants the tool to find up to K
diverse valid layouts.

```mermaid
sequenceDiagram
    participant Op as Operator
    participant CLI as cli.py
    participant Loader as loader.py
    participant Solver as solver.py
    participant Coll as collisions.py
    participant Viz as visualize.py
    participant FS as Filesystem

    Op->>CLI: hangarfit solve scenario.yaml --seed 42 --alternatives 3
    CLI->>FS: read scenario.yaml, fleet.yaml, hangar.yaml
    CLI->>Loader: parse YAML → Scenario
    Loader-->>CLI: Scenario (structurally valid)

    CLI->>Solver: solve(scenario, budget_s=30, alternatives=3, seed=42)
    Note over Solver: Pre-search infeasibility checks<br/>(pins inside hangar?<br/>maint plane in back strip?)
    alt trivially infeasible
        Solver-->>CLI: SolveResult(status=trivially_infeasible)
    else feasible
        loop until K accepted or budget exhausted
            Note over Solver: Random initial placement
            loop descent (min-conflicts)
                Solver->>Coll: check(candidate)
                Coll-->>Solver: CheckResult
                alt zero conflicts
                    Note over Solver: candidate is valid
                else conflicts > 0
                    Note over Solver: perturb plane with max<br/>penetration contribution
                end
            end
            Note over Solver: Diversity filter:<br/>compare candidate to<br/>already-accepted layouts
            alt diverse enough
                Note over Solver: append to accepted
            else too similar
                Note over Solver: increment<br/>diversity_rejected_count
            end
        end
        alt K accepted before budget
            Solver-->>CLI: SolveResult(status=found, layouts, diagnostics, seed)
        else some-but-fewer-than-K accepted, budget exhausted
            Solver-->>CLI: SolveResult(status=found_partial, layouts, diagnostics, seed)
        else zero accepted, budget exhausted
            Solver-->>CLI: SolveResult(status=exhausted_budget, layouts=[], diagnostics, seed)
        end
    end

    loop per accepted layout
        CLI->>Viz: render(layout)
        Viz->>FS: write out_i.png
    end
    CLI->>Op: stdout JSON / stderr status + exit code
```

**Determinism.** Given the same scenario, the same `--seed`, and the
same project version (same `hangarfit.solve/v1` schema), the returned
`SolveResult` is bit-identical across runs. This is the
load-bearing contract behind quality goal #2; the determinism canaries
in `tests/test_solver_canaries.py` are the regression guard.

**Termination statuses.** The solver returns one of four
`SolveStatus` literals — three from the search loop and one from
the pre-search infeasibility check:

| Status | Meaning | Exit code (without `--strict-k`) |
|--------|---------|-----------------------------------|
| `found` | K solutions accepted | 0 |
| `found_partial` | 1 ≤ N < K accepted, budget exhausted | 0 |
| `exhausted_budget` | 0 accepted, budget exhausted | 1 |
| `trivially_infeasible` | Pre-search check failed | 1 |

With `--strict-k`, `found_partial` also returns exit code 1 — useful
for scripted invocation where "fewer than K alternatives" should be
treated as failure.

**No retries inside solve().** The solver does not retry on a single
candidate's failure — it just restarts. There is no exception path
from `check()` into the solver other than structural failure (which
would indicate a bug in the random-placement generator), and that
bubbles up as exit code 2.

## What is *not* a runtime concern

- **Long-running state.** Each invocation is stateless. There is no
  session, no checkpoint, no incremental rerun. The scenario YAML
  carries everything the tool needs.
- **Concurrent solves.** The CLI runs one solve at a time per process.
  Two simultaneous `hangarfit solve` invocations against different
  scenarios are independent processes; they do not share state.
- **Asynchronous notifications.** The tool does not push results
  anywhere — the operator reads stdout / stderr / the PNG on disk.
  Integration with anything event-driven would be a wrapper script's
  job, not the tool's.

For the static decomposition the runtime view sits on top of, see
[§5 Building Block View](05-building-block-view.md).
For the *why* behind any of the load-bearing runtime choices (RR-MC
vs alternatives, diversity filter, three-way termination), see
[the ADRs](../adr/).
