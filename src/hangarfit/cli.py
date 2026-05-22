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
import sys

from hangarfit import collisions, visualize
from hangarfit.loader import LoaderError, load_fleet, load_hangar, load_layout
from hangarfit.models import CheckResult, Conflict, Layout, SolveResult

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
        help="RNG seed (default: None -> resolved from system entropy).",
    )
    solve.add_argument(
        "--render",
        default=None,
        metavar="PATTERN",
        help="Write top-down PNG(s). Must contain '{i}' if --alternatives > 1.",
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
    """Write the human-readable summary to stdout. See spec §5.3."""
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
        line = f"  #{i}: {len(layout.placements)} planes placed; 0 conflicts; score=(0, 0.0)"
        if i > 1:
            parts = []
            for j in range(i - 1):
                moved, avg_shift = _placement_delta(result.layouts[j], layout)
                total = len(layout.placements)
                parts.append(
                    f"{moved} of {total} planes shifted vs #{j + 1} (avg shift {avg_shift:.1f} m)"
                )
            line = f"  #{i}: {'; '.join(parts)}"
        print(line)


# Position threshold (m) used purely for the human-output "shifted vs"
# count. Mirrors DiversityConfig.position_threshold_m's default so the
# narration agrees with the filter that gated acceptance — not imported
# from DiversityConfig because the CLI doesn't (yet) expose the threshold
# as a flag; if it ever does, swap the constant for the configured value.
_HUMAN_SHIFT_THRESHOLD_M = 0.5


def _placement_delta(a: Layout, b: Layout) -> tuple[int, float]:
    """Return (planes_moved, mean_xy_shift_m) between two layouts.

    Planes-moved counts placements whose Euclidean (x, y) shift exceeds
    ``_HUMAN_SHIFT_THRESHOLD_M`` — same metric the solver's diversity
    filter uses. Heading-only shifts are intentionally ignored for the
    narration: the audience is reading "how different does this layout
    LOOK"; a pure rotation reads as the same layout to the eye even
    though the solver considers it diverse.
    """
    import math

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
        if shift > _HUMAN_SHIFT_THRESHOLD_M:
            moved += 1
    mean = total_shift / len(shared) if shared else 0.0
    return moved, mean


def cmd_solve(args: argparse.Namespace) -> int:
    """Run the ``solve`` subcommand. See spec §5 for the data flow."""
    # Imports are local so that `hangarfit check` users don't pay the
    # solver / matplotlib import cost — matplotlib is the slow one and
    # only `--render` needs it.
    from hangarfit.loader import load_scenario
    from hangarfit.solver import solve

    try:
        fleet_override = load_fleet(args.fleet) if args.fleet is not None else None
        hangar_override = load_hangar(args.hangar) if args.hangar is not None else None
        scenario = load_scenario(args.scenario, fleet=fleet_override, hangar=hangar_override)
    except LoaderError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    result = solve(
        scenario,
        budget_s=args.budget,
        alternatives=args.alternatives,
        seed=args.seed,
    )

    if args.json:
        _emit_solve_json(args.scenario, result)
    else:
        _emit_solve_human(result, alternatives=args.alternatives)

    # Exit code (spec §5.2). --strict-k flips 0 -> 1 only for found_partial.
    if not result.layouts:
        return 1
    if result.status == "found_partial" and args.strict_k:
        return 1
    return 0


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


def _emit_solve_json(scenario_path: str, result: SolveResult) -> None:
    """Write the hangarfit.solve/v1 payload to stdout. See spec §5.4."""
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
