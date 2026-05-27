"""Command-line interface for hangarfit.

Implements:
    hangarfit check LAYOUT [--render OUT.png] [--fleet PATH] [--hangar PATH] [--json]

See ``docs/superpowers/specs/2026-05-21-cli-design.md`` for the design.

JSON output schema: ``hangarfit.check/v1`` — the ``conflicts`` array carries
one object per :class:`hangarfit.models.Conflict` (``kind`` / ``planes`` /
``detail``). Bump the schema version if and only if ``Conflict``
itself grows new fields.
"""

from __future__ import annotations

import argparse
import json
import math
import secrets
import sys
from typing import TYPE_CHECKING

from hangarfit import collisions, visualize
from hangarfit.loader import LoaderError, load_fleet, load_hangar, load_layout
from hangarfit.models import CheckResult, Conflict, DiversityConfig, Layout, SolveResult

if TYPE_CHECKING:
    # Annotation-only: avoids importing the solver/towplanner stack at module
    # load for `hangarfit check` callers (the solver import is already deferred
    # into cmd_solve for the same reason).
    from hangarfit.towplanner import MovesPlan

_JSON_SCHEMA = "hangarfit.check/v1"
_SOLVE_JSON_SCHEMA = "hangarfit.solve/v1"


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser with the ``check`` and ``solve`` subcommands."""
    parser = argparse.ArgumentParser(
        prog="hangarfit",
        description="Check a hand-authored hangar layout for validity.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    check = sub.add_parser("check", help="Check a layout YAML.")
    check.add_argument("layout", help="Path to the layout YAML.")
    check.add_argument(
        "--render",
        metavar="OUT.png",
        default=None,
        help="Write a top-down PNG (runs even when the layout is invalid).",
    )
    check.add_argument(
        "--fleet",
        metavar="PATH",
        default=None,
        help="Override the fleet data file. Cannot be combined with a layout that already embeds a fleet path.",  # noqa: E501
    )
    check.add_argument(
        "--hangar",
        metavar="PATH",
        default=None,
        help="Override the hangar data file. Cannot be combined with a layout that already embeds a hangar path.",  # noqa: E501
    )
    check.add_argument(
        "--json",
        action="store_true",
        help=f"Emit JSON on stdout (schema: {_JSON_SCHEMA}).",
    )

    solve = sub.add_parser("solve", help="Solve a scenario to a valid layout.")
    solve.add_argument("scenario", help="Path to the scenario YAML.")
    solve.add_argument(
        "--budget",
        type=float,
        default=30.0,
        metavar="SEC",
        help="Wall-clock budget in seconds (default: 30.0).",
    )
    solve.add_argument(
        "--alternatives",
        type=int,
        default=1,
        metavar="N",
        help="Number of diverse alternative layouts (default: 1).",
    )
    solve.add_argument(
        "--seed",
        type=int,
        default=None,
        metavar="S",
        help=(
            "RNG seed (default: None -> resolved from system entropy). "
            "Under a wall-clock --budget, results are reproducible on the same "
            "machine but not guaranteed identical across machines, because the "
            "best-of-all basin selection (#267) depends on how many restarts "
            "fit the budget."
        ),
    )
    solve.add_argument(
        "--render",
        default=None,
        metavar="PATTERN",
        help="Write top-down PNG(s). Must contain '{i}' if --alternatives > 1.",
    )
    solve.add_argument(
        "--render-paths",
        action="store_true",
        dest="render_paths",
        help=(
            "Overlay each plane's tow path on the --render PNG(s), one colour per "
            "plane (requires --render). Tow-plans every returned layout; a layout "
            "the tow planner cannot route is rendered without paths and a warning "
            "names the blocking plane. Exit 3 if NO candidate is tow-routable. "
            "May run a second solve with inter-plane spread disabled if the "
            "spread layout can't be routed, up to ~2x the --budget."
        ),
    )
    solve.add_argument(
        "--write-yaml",
        default=None,
        metavar="PATTERN",
        dest="write_yaml",
        help="Write layout YAML(s). Must contain '{i}' if --alternatives > 1.",
    )
    solve.add_argument(
        "--strict-k",
        action="store_true",
        dest="strict_k",
        help="Exit 1 if status=found_partial (default: exit 0 unless 0 valid).",
    )
    solve.add_argument(
        "--json",
        action="store_true",
        dest="json",
        help=f"Emit JSON on stdout (schema: {_SOLVE_JSON_SCHEMA}).",
    )
    solve.add_argument(
        "--fleet",
        default=None,
        metavar="PATH",
        help="Override the fleet data file (refused if scenario YAML also sets 'fleet').",
    )
    solve.add_argument(
        "--hangar",
        default=None,
        metavar="PATH",
        help="Override the hangar data file (refused if scenario YAML also sets 'hangar').",
    )
    solve.add_argument(
        "--no-spread",
        action="store_false",
        dest="spread",
        default=True,
        help="Disable the inter-plane spread post-pass (default: spread enabled).",
    )

    return parser


def _emit_human(result: CheckResult) -> None:
    """Write the human-readable summary to stdout."""
    if result.valid:
        print("valid")
        return
    n = len(result.conflicts)
    print(f"invalid: {n} conflict{'s' if n != 1 else ''}")
    for c in result.conflicts:
        print(_format_conflict(c))


def _format_conflict(c: Conflict) -> str:
    """One-line human render of a Conflict. No re-parsing of ``detail``."""
    return f"  - {c.kind} [{', '.join(c.planes)}]: {c.detail}"


def _conflict_to_dict(c: Conflict) -> dict:
    """One-to-one dump of Conflict for the v1 JSON schema."""
    return {"kind": c.kind, "planes": list(c.planes), "detail": c.detail}


def _emit_json(layout_path: str, result: CheckResult) -> None:
    """Write the v1 JSON payload to stdout."""
    payload = {
        "schema": _JSON_SCHEMA,
        "layout": layout_path,
        "valid": result.valid,
        "conflicts": [_conflict_to_dict(c) for c in result.conflicts],
    }
    print(json.dumps(payload, indent=2))


def cmd_check(args: argparse.Namespace) -> int:
    """Run the ``check`` subcommand. See spec §4 for the data flow."""
    try:
        fleet_override = load_fleet(args.fleet) if args.fleet is not None else None
        hangar_override = load_hangar(args.hangar) if args.hangar is not None else None
        layout = load_layout(args.layout, fleet=fleet_override, hangar=hangar_override)
    except LoaderError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    result = collisions.check(layout)
    if args.json:
        _emit_json(args.layout, result)
    else:
        _emit_human(result)

    if args.render is not None:
        try:
            visualize.render_layout(layout, args.render, check_result=result)
        except OSError as e:
            print(f"error: render failed: {e}", file=sys.stderr)
            return 2

    return 0 if result.valid else 1


def _emit_solve_human(result: SolveResult, *, alternatives: int) -> None:
    """Write the human-readable summary to stdout."""
    d = result.diagnostics
    if result.status == "trivially_infeasible":
        # `best_partial` is fused with the explanatory conflict by the
        # solver (spec §4.1) — the first Conflict's detail is the
        # canonical human reason.
        print("Trivially infeasible:")
        if d.best_partial is not None:
            for c in d.best_partial.conflicts:
                print(f"  - {c.kind} [{', '.join(c.planes)}]: {c.detail}")
        return

    if result.status == "exhausted_budget":
        print(
            f"No valid layout found in {d.wall_time_s:.1f}s "
            f"(seed={d.seed}, {d.restarts_attempted} restarts)."
        )
        if d.best_partial is not None and d.best_partial.conflicts:
            n = len(d.best_partial.conflicts)
            print(f"Best partial had {n} conflict{'s' if n != 1 else ''}:")
            for c in d.best_partial.conflicts:
                print(f"  - {c.kind} [{', '.join(c.planes)}]: {c.detail}")
        print("Hint: increase --budget, or relax pins.")
        return

    # found or found_partial: at least one layout.
    n = len(result.layouts)
    if result.status == "found_partial":
        print(
            f"Found {n} of {alternatives} requested layouts in "
            f"{d.wall_time_s:.1f}s (seed={d.seed}, {d.restarts_attempted} restarts)."
        )
    else:
        print(
            f"Found {n} layout{'s' if n != 1 else ''} in "
            f"{d.wall_time_s:.1f}s (seed={d.seed}, {d.restarts_attempted} restarts)."
        )
    for i, layout in enumerate(result.layouts, start=1):
        gap = d.min_pairwise_gap_m[i - 1] if i - 1 < len(d.min_pairwise_gap_m) else math.inf
        gap_str = f"{gap:.2f} m" if math.isfinite(gap) else "n/a (single plane)"
        if i > 1:
            parts = []
            for j in range(i - 1):
                moved, avg_shift = _placement_delta(result.layouts[j], layout)
                total = len(layout.placements)
                parts.append(
                    f"{moved} of {total} planes shifted vs #{j + 1} (avg shift {avg_shift:.1f} m)"
                )
            line = f"  #{i}: {'; '.join(parts)}; min gap {gap_str}"
        else:
            line = (
                f"  #{i}: {len(layout.placements)} planes placed; 0 conflicts; "
                f"score=(0, 0.0); min gap {gap_str}"
            )
        print(line)


def _placement_delta(a: Layout, b: Layout) -> tuple[int, float]:
    """Return (planes_moved, mean_xy_shift_m) between two layouts.

    Planes-moved counts placements whose Euclidean (x, y) shift exceeds
    ``DiversityConfig().position_threshold_m`` — same metric the
    solver's diversity filter uses. Pulling the threshold directly from
    ``DiversityConfig`` (rather than mirroring it as a local constant)
    keeps the human-output narration in lockstep with the filter that
    gated acceptance: if the default ever changes, both sides shift
    together. ``DiversityConfig`` is a frozen dataclass with no side
    effects, so instantiating it here is free.

    Heading-only shifts are intentionally ignored for the narration:
    the audience is reading "how different does this layout LOOK"; a
    pure rotation reads as the same layout to the eye even though the
    solver considers it diverse.
    """
    import math

    threshold = DiversityConfig().position_threshold_m
    by_id_a = {p.plane_id: p for p in a.placements}
    by_id_b = {p.plane_id: p for p in b.placements}
    shared = sorted(set(by_id_a) & set(by_id_b))
    moved = 0
    total_shift = 0.0
    for pid in shared:
        pa, pb = by_id_a[pid], by_id_b[pid]
        dx = pa.x_m - pb.x_m
        dy = pa.y_m - pb.y_m
        shift = math.hypot(dx, dy)
        total_shift += shift
        if shift > threshold:
            moved += 1
    mean = total_shift / len(shared) if shared else 0.0
    return moved, mean


def cmd_solve(args: argparse.Namespace) -> int:
    """Run the ``solve`` subcommand."""
    # Defer the solver import only: ``hangarfit check`` invocations should
    # not pay the solver's import cost. Matplotlib is NOT deferred here —
    # ``from hangarfit import visualize`` at module top (cli.py:20) already
    # eagerly imports matplotlib and pins the Agg backend, so both `check`
    # and `solve` callers pay that cost regardless.
    from hangarfit.loader import load_scenario
    from hangarfit.models import SearchConfig
    from hangarfit.solver import solve

    # Validate output PATTERNs BEFORE solving — a user who typoed
    # --render shouldn't burn the full --budget only to crash on write.
    if args.alternatives > 1:
        for flag, pattern in (("--render", args.render), ("--write-yaml", args.write_yaml)):
            if pattern is not None and "{i}" not in pattern:
                print(
                    f"error: {flag} PATTERN must contain '{{i}}' "
                    f"when --alternatives > 1 (got: {pattern!r})",
                    file=sys.stderr,
                )
                return 2

    # --render-paths overlays the tow paths onto the --render PNG(s); without a
    # --render target there is nothing to draw on. Fail fast (usage error)
    # rather than tow-plan and silently produce no visible output.
    if args.render_paths and args.render is None:
        print("error: --render-paths requires --render PATTERN", file=sys.stderr)
        return 2

    try:
        fleet_override = load_fleet(args.fleet) if args.fleet is not None else None
        hangar_override = load_hangar(args.hangar) if args.hangar is not None else None
        scenario = load_scenario(args.scenario, fleet=fleet_override, hangar=hangar_override)
    except LoaderError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # Tow-plan only when the user asked to render paths (#193). Otherwise
    # plan_paths=False: the library bundle (SolveResult.plans) is available to
    # callers, but the CLI would just pay the per-plane Hybrid-A* search cost
    # for output it never draws.
    #
    # Resolve the seed ONCE here (rather than letting each solve() call draw its
    # own from system entropy when --seed is omitted) so the spread run and any
    # no-spread fallback share an identical, reproducible seed. This keeps the
    # ADR-0003 determinism contract intact across the two-pass fallback: a given
    # --seed (or a given resolved entropy seed) yields the same fallback result.
    resolved_seed = args.seed if args.seed is not None else secrets.randbits(32)

    result = solve(
        scenario,
        budget_s=args.budget,
        alternatives=args.alternatives,
        seed=resolved_seed,
        search=SearchConfig(spread=args.spread),
        plan_paths=args.render_paths,
    )

    # #280 — spread-vs-towability fallback. The ADR-0008 spread post-pass
    # (default ON) maximizes inter-plane gaps, which can push planes into
    # positions the bounded tow planner (towplanner._MAX_EXPANSIONS) can no
    # longer thread from the door cone: every plan comes back None and
    # --render-paths would return a bare exit 3 ("untowable") — even though the
    # SAME fleet+hangar routes cleanly with spread off. When the user did NOT
    # explicitly pass --no-spread, automatically RE-SOLVE with spread disabled,
    # tow-plan that, and — only if it actually routes at least one candidate —
    # render the routable (tighter) arrangement instead. The swap is always
    # reported on stderr (never silent: the user gets a different, tighter
    # layout than a plain spread solve would yield). If the no-spread re-solve
    # also routes nothing (genuinely too tight, e.g. the placeholder hangar),
    # keep the original spread result so exit 3 stands unchanged.
    #
    # ``spread_fallback_applied`` records whether the swap actually executed.
    # Non-interactive consumers (--json, --write-yaml) need a durable signal
    # that a tighter no-spread arrangement was substituted (#280 "never silently
    # swapped" for machines, not just the human stderr note).
    spread_fallback_applied = False
    if (
        args.render_paths
        and args.spread
        and result.layouts
        and all(plan is None for plan in result.plans)
    ):
        fallback = solve(
            scenario,
            budget_s=args.budget,
            alternatives=args.alternatives,
            seed=resolved_seed,
            search=SearchConfig(spread=False),
            plan_paths=True,
        )
        if fallback.layouts and any(plan is not None for plan in fallback.plans):
            print(
                "note: layout un-routable with inter-plane spread; re-solved "
                "with spread disabled to produce tow paths",
                file=sys.stderr,
            )
            result = fallback
            spread_fallback_applied = True

    if args.json:
        _emit_solve_json(args.scenario, result, spread_fallback_applied=spread_fallback_applied)
    else:
        _emit_solve_human(result, alternatives=args.alternatives)

    # Structured tow-path warnings: name the plane that blocked each layout the
    # tow planner could not route (best-effort — the layout is still valid and
    # is rendered, just without a path overlay; ADR-0007 + ADR-0010 / #197).
    if args.render_paths:
        _warn_unroutable(result)

    # Renders / YAML writes. Only run if we have layouts to write —
    # exhausted_budget / trivially_infeasible carry empty `layouts`.
    if result.layouts:
        try:
            if args.render is not None:
                _write_renders(
                    result.layouts,
                    args.render,
                    plans=result.plans if args.render_paths else None,
                )
            if args.write_yaml is not None:
                fleet_ref, hangar_ref = _resolve_fleet_hangar_refs(args)
                _write_yamls(
                    result.layouts,
                    args.write_yaml,
                    fleet_ref,
                    hangar_ref,
                    spread_fallback_applied=spread_fallback_applied,
                )
        except OSError as e:
            print(f"error: write failed: {e}", file=sys.stderr)
            return 2

    # Exit code (spec §5.2). Precedence: no layouts > no-tow-order > strict-k.
    if not result.layouts:
        return 1
    # --render-paths only: exit 3 when the tow planner could route NO candidate
    # (spike Q8/Q2 "no feasible order for any candidate"). A valid layout still
    # rendered, so this is distinct from exit 1 (no layout at all). Checked
    # before --strict-k: "can't tow anything" is the more actionable failure.
    if args.render_paths and all(plan is None for plan in result.plans):
        return 3
    if result.status == "found_partial" and args.strict_k:
        return 1
    return 0


def _expand_pattern(pattern: str, i: int) -> str:
    """Substitute ``{i}`` in ``pattern`` with 1-indexed ``i``.

    Patterns without ``{i}`` (only valid when K=1, enforced earlier)
    are returned unchanged.
    """
    return pattern.replace("{i}", str(i))


def _write_renders(
    layouts: tuple[Layout, ...],
    pattern: str,
    *,
    plans: tuple[MovesPlan | None, ...] | None = None,
) -> None:
    """Render each layout to PATTERN with ``{i}`` substituted.

    When ``plans`` is supplied (``--render-paths``), it is index-aligned with
    ``layouts``: ``plans[i]`` is overlaid as a tow-path polyline, or ``None``
    (un-routable layout) renders the plain layout. ``plans=None`` renders no
    overlays at all.

    Single-pass loop — any OSError bubbles to the caller; we don't
    swallow partial-write state because a half-written render set is
    worse than a clean failure that the user can re-run.
    """
    for i, layout in enumerate(layouts, start=1):
        moves_plan = plans[i - 1] if plans is not None else None
        visualize.render_layout(layout, _expand_pattern(pattern, i), moves_plan=moves_plan)


def _warn_unroutable(result: SolveResult) -> None:
    """Emit one stderr warning per returned layout the tow planner could not
    tow-route, naming the blocking plane (best-effort; the layout is still
    valid and rendered, just without a path overlay — ADR-0007 + ADR-0010 / #197).

    ``diagnostics.unroutable_planes`` is a *compacted* list (one entry per
    ``None`` plan, in returned-layout order), so the j-th ``None`` plan pairs
    with the j-th blocking plane. The solver builds both in one pass (one
    plane appended per ``None``), so the lengths must match; we assert that
    rather than papering over a desync with a misleading placeholder name (a
    mismatch is a producer bug, not a user condition).
    """
    unrouted_layouts = [i for i, plan in enumerate(result.plans, start=1) if plan is None]
    planes = result.diagnostics.unroutable_planes
    assert len(planes) == len(unrouted_layouts), (
        f"unroutable_planes ({len(planes)}) out of sync with None plans "
        f"({len(unrouted_layouts)}) — solver bug"
    )
    for layout_index, plane in zip(unrouted_layouts, planes, strict=True):
        print(
            f"warning: layout {layout_index}: no feasible tow path "
            f"(plane {plane!r} could not be routed); rendered without paths",
            file=sys.stderr,
        )


def _resolve_fleet_hangar_refs(args: argparse.Namespace) -> tuple[str, str]:
    """Resolve fleet/hangar refs to absolute paths for layout-YAML output.

    Preference order per source:
    1. ``--fleet`` / ``--hangar`` CLI override (rare; load_scenario
       refuses to mix override and embedded ref).
    2. ``fleet:`` / ``hangar:`` field inside the scenario YAML, joined
       to the scenario's parent directory.

    Returning absolute paths means the written layout YAML is
    location-independent — a user can stash it anywhere and still
    ``hangarfit check`` it without rewiring relative paths.
    """
    from pathlib import Path

    import yaml

    scenario_path = Path(args.scenario)
    # load_scenario already opened and parsed this YAML successfully, so
    # the file exists and is well-formed; re-reading here just to pluck
    # the two ref fields. Cheap second pass; avoids holding the parsed
    # scenario in cmd_solve's frame solely for this.
    with open(scenario_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        # load_scenario already rejected this — defensive guard so type
        # checkers don't see Any. Should be unreachable in practice.
        raise OSError(f"scenario YAML is not a mapping: {scenario_path}")

    if args.fleet is not None:
        fleet_abs = Path(args.fleet).resolve()
    else:
        fleet_rel = raw.get("fleet")
        if not isinstance(fleet_rel, str):
            raise OSError(
                f"cannot write layout YAML: scenario {scenario_path} has no "
                f"'fleet' field and no --fleet override"
            )
        fleet_abs = (scenario_path.parent / fleet_rel).resolve()

    if args.hangar is not None:
        hangar_abs = Path(args.hangar).resolve()
    else:
        hangar_rel = raw.get("hangar")
        if not isinstance(hangar_rel, str):
            raise OSError(
                f"cannot write layout YAML: scenario {scenario_path} has no "
                f"'hangar' field and no --hangar override"
            )
        hangar_abs = (scenario_path.parent / hangar_rel).resolve()

    return str(fleet_abs), str(hangar_abs)


def _write_yamls(
    layouts: tuple[Layout, ...],
    pattern: str,
    fleet_ref: str,
    hangar_ref: str,
    *,
    spread_fallback_applied: bool = False,
) -> None:
    """Write each layout to PATTERN with ``{i}`` substituted.

    Output format matches ``layouts/example.yaml`` so the file
    round-trips through ``hangarfit check``. Fleet/hangar refs are
    embedded as absolute paths so the written file is location-
    independent.

    When ``spread_fallback_applied`` is True the written layout is the tighter
    no-spread arrangement substituted by the --render-paths fallback (#280); a
    leading ``# note:`` comment marks that provenance. The comment is a YAML
    comment line, so the file still round-trips through ``hangarfit check``
    (the structured ``spread_fallback_applied`` JSON field carries the same
    signal for parsers; this header is for a human reading the .yaml later).
    """
    import yaml

    for i, layout in enumerate(layouts, start=1):
        payload: dict = {
            "fleet": fleet_ref,
            "hangar": hangar_ref,
        }
        if layout.maintenance_plane is not None:
            payload["maintenance"] = {"plane": layout.maintenance_plane}
        payload["placements"] = [
            {
                "plane": p.plane_id,
                "x_m": p.x_m,
                "y_m": p.y_m,
                "heading_deg": p.heading_deg,
                "on_carts": p.on_carts,
            }
            for p in layout.placements
        ]
        path = _expand_pattern(pattern, i)
        with open(path, "w") as f:
            if spread_fallback_applied:
                f.write(
                    "# note: produced with inter-plane spread disabled (auto-fallback, see #280)\n"
                )
            yaml.safe_dump(payload, f, sort_keys=False)


def _layout_to_dict(layout: Layout) -> dict:
    """Dump a Layout to the hangarfit.solve/v1 schema shape."""
    return {
        "placements": [
            {
                "plane": p.plane_id,
                "x_m": p.x_m,
                "y_m": p.y_m,
                "heading_deg": p.heading_deg,
                "on_carts": p.on_carts,
            }
            for p in layout.placements
        ],
        "maintenance_plane": layout.maintenance_plane,
    }


def _check_result_to_dict(result: CheckResult) -> dict:
    """Dump a CheckResult to the hangarfit.check/v1 conflicts shape."""
    return {
        "valid": result.valid,
        "conflicts": [_conflict_to_dict(c) for c in result.conflicts],
    }


def _emit_solve_json(
    scenario_path: str,
    result: SolveResult,
    *,
    spread_fallback_applied: bool = False,
) -> None:
    """Write the hangarfit.solve/v1 payload to stdout.

    ``spread_fallback_applied`` is threaded in from ``cmd_solve`` rather than
    read off ``SolverDiagnostics`` — the swap is a CLI-level decision (#280),
    not a solver fact, so it does not belong on ``SolverDiagnostics``. It is
    emitted as an always-present (default False) additive diagnostics field so
    non-interactive consumers can rely on it without a schema bump.
    """
    d = result.diagnostics
    payload = {
        "schema": _SOLVE_JSON_SCHEMA,
        "scenario": scenario_path,
        "status": result.status,
        "layouts": [_layout_to_dict(layout) for layout in result.layouts],
        "diagnostics": {
            "restarts_attempted": d.restarts_attempted,
            "wall_time_s": d.wall_time_s,
            "seed": d.seed,
            "best_partial": (
                _check_result_to_dict(d.best_partial) if d.best_partial is not None else None
            ),
            "best_partial_layout": (
                _layout_to_dict(d.best_partial_layout)
                if d.best_partial_layout is not None
                else None
            ),
            # Additive field (#193): the planes the v1 tow planner could not
            # route, in returned-layout order. Empty unless --render-paths ran
            # the planner. Backward-compatible — no schema bump.
            "unroutable_planes": list(d.unroutable_planes),
            # Additive (#267): achieved min plan-view gap per returned layout
            # (null where <2 planes, i.e. math.inf) + basins the search had to
            # choose from. Backward-compatible — no schema bump.
            "min_pairwise_gap_m": [g if math.isfinite(g) else None for g in d.min_pairwise_gap_m],
            "valid_basins_found": d.valid_basins_found,
            # Additive (#280): True when --render-paths re-solved with spread
            # disabled and substituted that tighter, tow-routable arrangement.
            # Always present (False in the normal no-swap case) so consumers can
            # rely on it. Backward-compatible — no schema bump.
            "spread_fallback_applied": spread_fallback_applied,
        },
    }
    print(json.dumps(payload, indent=2))


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns an exit code; does not call ``sys.exit``."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "check":
        return cmd_check(args)
    if args.cmd == "solve":
        return cmd_solve(args)
    # argparse with required=True should make this unreachable.
    parser.error(f"unknown command: {args.cmd!r}")
