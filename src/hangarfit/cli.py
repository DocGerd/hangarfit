"""Command-line interface for hangarfit.

Implements:
    hangarfit [--version]
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
import sys
from collections.abc import Iterable
from typing import TYPE_CHECKING, Literal

from hangarfit import __version__, collisions, visualize
from hangarfit.loader import LoaderError, load_fleet, load_hangar, load_layout
from hangarfit.models import (
    ApronShallowDrop,
    CheckResult,
    Conflict,
    DiversityConfig,
    Layout,
    SolveResult,
)

if TYPE_CHECKING:
    # Annotation-only: avoids importing the solver/towplanner stack at module
    # load for `hangarfit check` callers (the solver import is already deferred
    # into cmd_solve for the same reason).
    from hangarfit.towplanner import MovesPlan

_JSON_SCHEMA = "hangarfit.check/v1"
_SOLVE_JSON_SCHEMA = "hangarfit.solve/v1"

# #320 back-of-hangar fill bias: the SearchConfig.back_bias_weight the CLI passes
# when back-fill is enabled (the default; --no-back-fill sets it to 0.0). Chosen
# from a sweep over the acceptance fixtures (single plane → back wall; the
# scenario_minimal 2-plane fill clears the door) as the smallest weight that
# breaks the mid-hangar symmetry while staying a secondary term below the spread
# objective — it does not collapse the inter-plane gap. See ADR-0008 (amended).
_BACK_FILL_DEFAULT_WEIGHT = 1.0

# #398 view-mode fast-degrade cap: the *global* tow-expansion budget the `view`
# subcommand passes to ``plan_fill`` in layout mode. The default whole-fill
# budget (towplanner._MAX_FILL_EXPANSIONS, 16000) is tuned to *disprove* a hard
# fill in bounded time for batch `solve`, but at that scale an un-routable
# interactive `view` (e.g. examples/layouts/example.yaml) grinds ~2 min before falling
# back to a static scene. A far smaller global cap degrades to static in a few
# seconds. This bounds the search by a deterministic expansion COUNT, not a
# wall-clock deadline — a time limit would bail a genuinely-but-slowly-routable
# preview differently on a slow machine, the exact ADR-0003 violation it pretends
# to avoid. A genuinely fast-routable layout finishes well under this cap and
# still animates; --tow-max-expansions overrides it (and the per-plane budget).
#
# Value chosen from measurement (~14 ms / expansion here, dominated by shapely
# collision checks): at 300 the un-routable example.yaml degrades in ~5 s while
# the routable demo (valid_left_side_nesting) still animates using ≪300 total
# expansions. The roadmap suggested ~2000, but that measured ~30 s — failing the
# "within a few seconds" goal — so the confirm-at-implementation cap is 300, at
# the cost of a smaller routable-animation envelope for slow/tight hand-authored
# layouts (an accepted tradeoff: such a layout shows static rather than an
# animation). `view --solve` is unaffected — it routes via solve(), not here.
_VIEW_TOW_MAX_TOTAL_EXPANSIONS = 300


def _apron_depth_arg(value: str) -> float | Literal["auto"]:
    """argparse type for ``--apron-depth``: a finite non-negative metre value, or
    the literal ``"auto"`` (fleet-derived depth, resolved by the loader, ADR-0021).
    Rejects garbage / negatives at parse time with an exit-2 ArgumentTypeError."""
    if value.strip().lower() == "auto":
        return "auto"
    try:
        depth = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--apron-depth must be a number or 'auto', got {value!r}"
        ) from None
    if not math.isfinite(depth) or depth < 0:
        raise argparse.ArgumentTypeError(
            f"--apron-depth must be a finite, non-negative number or 'auto', got {value!r}"
        )
    return depth


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser with the ``check`` and ``solve`` subcommands."""
    parser = argparse.ArgumentParser(
        prog="hangarfit",
        description="Check a hand-authored hangar layout for validity.",
    )
    # Top-level so `hangarfit --version` works before any subcommand. The
    # version action fires (and exits 0) during parse, ahead of the
    # required-subparser check below, so no `cmd` is needed. The string is
    # sourced from the installed package metadata (hangarfit.__version__) so
    # it can never drift from pyproject.toml's [project] version.
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
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
        "--max-carts",
        type=int,
        metavar="N",
        default=None,
        dest="max_carts",
        help="Override the hangar's spare-cart count for the cart_eligible pool (default: the hangar.yaml value, or 1 if unset).",  # noqa: E501
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
        "--max-carts",
        type=int,
        metavar="N",
        default=None,
        dest="max_carts",
        help="Override the hangar's spare-cart count for the cart_eligible pool (default: the hangar.yaml value, or 1 if unset).",  # noqa: E501
    )
    solve.add_argument(
        "--apron-depth",
        type=_apron_depth_arg,
        metavar="N|auto",
        default=None,
        dest="apron_depth",
        help=(
            "Staging-apron depth (m) in front of the door (ADR-0021): each tow "
            "path starts outside the hangar and slides in. 'auto' derives "
            "~max(plane length)+max(turn radius) from the fleet. Default: the "
            "hangar.yaml value, or 0 (no apron, today's behaviour)."
        ),
    )
    solve.add_argument(
        "--no-spread",
        action="store_false",
        dest="spread",
        default=True,
        help="Disable the inter-plane spread post-pass (default: spread enabled).",
    )
    solve.add_argument(
        "--no-back-fill",
        action="store_false",
        dest="back_fill",
        default=True,
        help=(
            "Disable the back-of-hangar fill bias (#320). By default the spread "
            "post-pass also biases planes toward the back wall, leaving free space "
            "at the door; pass this to keep the symmetric spread only. (No effect "
            "with --no-spread, since the bias rides on the spread post-pass.)"
        ),
    )
    solve.add_argument(
        "--no-nose-out",
        action="store_false",
        dest="nose_out",
        default=True,
        help=(
            "Disable the nose-out parked-heading preference (#263). By default the "
            "solver flips each plane's parked heading toward the door (for an easy "
            "straight-out exit) when that stays collision-valid; pass this to keep "
            "the packing-chosen heading. Per-plane override: constraints.<id>.nose_out."
        ),
    )
    # ── Restart-budget + parallel knobs (#544). --max-restarts bounds the
    # search by a fixed restart count (cross-machine reproducible, NOT
    # wall-clock); --workers fans those restarts across processes, which is
    # byte-identical to serial only in this max_restarts-bound, spread-on regime.
    solve.add_argument(
        "--max-restarts",
        type=int,
        metavar="N",
        default=None,
        dest="max_restarts",
        help=(
            "Cap the RR-MC search at N restarts instead of the wall-clock "
            "--budget (the two gates compose; first to trip wins). A fixed "
            "restart count makes the result reproducible across machines "
            "regardless of speed, and is required to enable --workers."
        ),
    )
    solve.add_argument(
        "--workers",
        type=int,
        metavar="N",
        default=1,
        dest="workers",
        help=(
            "Fan the restarts across N worker processes (#544; default 1 = "
            "serial). Byte-identical to serial in the --max-restarts + spread "
            "regime; for any other config it runs serial (a note is printed). "
            "Speedup is sub-linear and placement-only — most useful on roomy "
            "spread-on fills with many restarts."
        ),
    )
    solve.add_argument(
        "--spread-stall-restarts",
        type=int,
        metavar="N",
        default=None,
        dest="spread_stall_restarts",
        help=(
            "Opt-in early-exit (F7/#404): stop the spread-ON restart loop after N "
            "consecutive restarts fail to improve the selected layouts' maximin "
            "plan-view gap (default: off = run to --budget/--max-restarts). The stop "
            "is seed-deterministic (an integer counter, never wall-clock) and only "
            "arms once a complete selection exists, so it just trims the "
            "polish-the-incumbent tail; no-op under --no-spread. NOTE: setting it "
            "makes the run ineligible for byte-identical --workers parallelism (#544)."
        ),
    )
    # ── Tow-planner knobs (grid heuristic default + global fill cap since #336;
    # spike #332). --tow-heuristic defaults to the shipped grid planner and
    # --tow-max-expansions widens the per-plane budget; both RNG-free (ADR-0003).
    solve.add_argument(
        "--tow-heuristic",
        choices=("euclidean", "grid"),
        default="grid",
        dest="tow_heuristic",
        help=(
            "A* tow-path heuristic. 'grid' (default since #336) is the "
            "obstacle-aware free-space geodesic that threads tight maneuvers in "
            "far fewer expansions; 'euclidean' is the older straight-line "
            "heuristic (opt out). Only affects --render-paths runs; deterministic, "
            "and the path is exact-oracle-validated."
        ),
    )
    solve.add_argument(
        "--tow-max-expansions",
        type=int,
        metavar="N",
        default=None,
        dest="tow_max_expansions",
        help=(
            "Per-plane Hybrid-A* expansion budget for tow planning (default: the "
            "module _MAX_EXPANSIONS=8000). Raise to trade time for routability on "
            "hard fills; a global per-fill cap bounds the worst case (#336)."
        ),
    )

    view = sub.add_parser(
        "view",
        help="Render an interactive, offline 3D HTML viewer of a layout/solution.",
    )
    view.add_argument(
        "input",
        help="Layout YAML (or scenario YAML with --solve).",
    )
    view.add_argument(
        "-o",
        "--output",
        required=True,
        metavar="OUT.html",
        help="Output HTML path (self-contained, opens offline in any browser).",
    )
    view.add_argument(
        "--solve",
        action="store_true",
        help="Treat the input as a scenario: solve it first, then view the result.",
    )
    view.add_argument(
        "--fleet",
        metavar="PATH",
        default=None,
        help="Override the fleet data file (same rule as `check`).",
    )
    view.add_argument(
        "--hangar",
        metavar="PATH",
        default=None,
        help="Override the hangar data file (same rule as `check`).",
    )
    view.add_argument(
        "--max-carts",
        type=int,
        metavar="N",
        default=None,
        dest="max_carts",
        help="Override the hangar's spare-cart count for the cart_eligible pool.",
    )
    view.add_argument(
        "--apron-depth",
        type=_apron_depth_arg,
        metavar="N|auto",
        default=None,
        dest="apron_depth",
        help=(
            "Staging-apron depth (m): each plane slides in from outside the door "
            "in the animation (ADR-0021). 'auto' derives from the fleet. "
            "Default: the hangar.yaml value, or 0 (no apron)."
        ),
    )
    view.add_argument(
        "--check",
        action="store_true",
        help="Layout mode: overlay collision conflicts (tint conflicting planes red).",
    )
    view.add_argument(
        "--no-animate",
        action="store_false",
        dest="animate",
        default=True,
        help="Skip tow planning — render a static 3D scene only.",
    )
    view.add_argument(
        "--spread",
        action="store_true",
        help=(
            "Solve mode: keep the inter-plane spread post-pass ON. Default is OFF "
            "for `view --solve` because spread routinely defeats the bounded tow "
            "planner, leaving the animation static (#280)."
        ),
    )
    view.add_argument(
        "--no-nose-out",
        action="store_false",
        dest="nose_out",
        default=True,
        help=(
            "Solve mode: disable the nose-out parked-heading preference (#263). "
            "By default the solver prefers headings that face the door."
        ),
    )
    view.add_argument(
        "--budget",
        type=float,
        default=30.0,
        metavar="SEC",
        help="Solve mode: wall-clock budget in seconds (default: 30.0).",
    )
    view.add_argument(
        "--seed",
        type=int,
        default=None,
        metavar="S",
        help="Solve mode: RNG seed (default: None -> system entropy).",
    )
    view.add_argument(
        "--tow-max-expansions",
        type=int,
        metavar="N",
        default=None,
        dest="tow_max_expansions",
        help=(
            "Per-plane Hybrid-A* expansion budget for the tow animation (default: "
            "the module _MAX_EXPANSIONS). In layout mode it also overrides the "
            "small global fast-degrade cap the viewer applies by default, so an "
            "un-routable layout falls back to a static 3D scene within a few "
            "seconds instead of grinding through the full disprove budget."
        ),
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


# Plain-language lead-in per single-plane conflict kind (#401). Pairwise overlaps
# are phrased "A overlaps B" generically — the authoritative ``detail`` already
# names the exact parts and z-gaps, so we never re-parse it (which would be fragile).
_SINGLE_CONFLICT_PHRASES = {
    "bay_intrusion": "intrudes into the maintenance bay",
    "hangar_bounds": "extends outside the hangar",
}


def _format_conflict(c: Conflict) -> str:
    """One-line *plain-language* render of a Conflict, keeping the authoritative
    ``detail`` verbatim (#401). No re-parsing of ``detail`` — the lead-in is built
    from ``kind`` + ``planes`` only."""
    planes = list(c.planes)
    if c.kind.endswith("_overlap") and len(planes) == 2:
        lead = f"{planes[0]} overlaps {planes[1]}"
    elif c.kind in _SINGLE_CONFLICT_PHRASES and planes:
        lead = f"{planes[0]} {_SINGLE_CONFLICT_PHRASES[c.kind]}"
    else:
        # Unknown / future kind: fall back to the raw kind + ids (still names the
        # planes), never crash.
        lead = f"{c.kind} [{', '.join(planes)}]"
    return f"  - {lead}: {c.detail}"


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
        hangar_override = (
            load_hangar(args.hangar, fleet=fleet_override) if args.hangar is not None else None
        )
        layout = load_layout(
            args.layout,
            fleet=fleet_override,
            hangar=hangar_override,
            max_carts=args.max_carts,
        )
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
                print(_format_conflict(c))
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
                print(_format_conflict(c))
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
        if i - 1 < len(d.region_alignment) and d.region_alignment[i - 1]:
            ra = ", ".join(f"{a.body_id}={a.alignment:.2f}" for a in d.region_alignment[i - 1])
            line += f"; region alignment {ra}"
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
    from hangarfit.solver import _parallel_eligible, solve

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
        hangar_override = (
            load_hangar(args.hangar, fleet=fleet_override) if args.hangar is not None else None
        )
        scenario = load_scenario(
            args.scenario,
            fleet=fleet_override,
            hangar=hangar_override,
            max_carts=args.max_carts,
            apron_depth=args.apron_depth,
        )
    except LoaderError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # Tow-plan only when the user asked to render paths (#193). Otherwise
    # plan_paths=False: the library bundle (SolveResult.plans) is available to
    # callers, but the CLI would just pay the per-plane Hybrid-A* search cost
    # for output it never draws.
    try:
        search_cfg = SearchConfig(
            spread=args.spread,
            nose_out=args.nose_out,
            back_bias_weight=_BACK_FILL_DEFAULT_WEIGHT if args.back_fill else 0.0,
            max_restarts=args.max_restarts,
            spread_stall_restarts=args.spread_stall_restarts,
        )
    except ValueError as e:
        # A bad restart-budget knob (e.g. --max-restarts 0 or --spread-stall-restarts
        # 0; both must be >= 1 when set) surfaces as a clean exit-2 input error rather
        # than an uncaught traceback — same contract as a LoaderError on malformed input.
        print(f"error: {e}", file=sys.stderr)
        return 2
    # Never silently ignore --workers (#544): if parallel restarts aren't
    # byte-identical-eligible for this config (no --max-restarts, or --no-spread),
    # solve() transparently runs serial — say so on stderr so the user isn't
    # left believing they got the speedup.
    if args.workers > 1 and not _parallel_eligible(search_cfg, args.workers):
        print(
            f"note: --workers {args.workers} ignored (runs serial) — parallel "
            "restarts need --max-restarts and the spread post-pass (drop "
            "--no-spread); see --workers help",
            file=sys.stderr,
        )
    result = solve(
        scenario,
        budget_s=args.budget,
        alternatives=args.alternatives,
        seed=args.seed,
        search=search_cfg,
        plan_paths=args.render_paths,
        tow_heuristic=args.tow_heuristic,
        tow_max_expansions=args.tow_max_expansions,
        workers=args.workers,
    )

    # Spread-vs-towability fallback (#280 → #402 / F5; ADR-0016). The re-solve
    # that swaps in a tighter, tow-routable no-spread arrangement when the spread
    # layout is valid-but-unroutable now lives in the library ``solve()`` so
    # every caller benefits, not just the CLI. The CLI just SURFACES it: report
    # the swap on stderr (never silent — the user gets a different, tighter
    # layout than a plain spread solve would yield) and thread the durable flag
    # into --json / --write-yaml so non-interactive consumers can rely on it.
    spread_fallback_applied = result.diagnostics.spread_fallback_applied
    if spread_fallback_applied:
        print(
            "note: layout un-routable with inter-plane spread; re-solved "
            "with spread disabled to produce tow paths",
            file=sys.stderr,
        )

    if args.json:
        _emit_solve_json(args.scenario, result)
    else:
        _emit_solve_human(result, alternatives=args.alternatives)

    # Structured tow-path warnings: name the plane that blocked each layout the
    # tow planner could not route (best-effort — the layout is still valid and
    # is rendered, just without a path overlay; ADR-0007 + ADR-0010 / #197).
    # Then warn (once per plane, deduped) about any plane that towed via the
    # door line because the apron was too shallow for its footprint (#503) —
    # keyed on the RETURNED result's diagnostics only, so the discarded
    # spread-fallback pass's drops never surface.
    if args.render_paths:
        _warn_unroutable(result)
        _warn_apron_shallow_drops(result.diagnostics.apron_shallow_drops)

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


def _warn_apron_shallow_drops(drops: Iterable[ApronShallowDrop]) -> None:
    """Emit one stderr warning per plane that towed via the ``y = 0`` door line
    because the site's apron was too shallow for its footprint (#503 / ADR-0021).

    ``drops`` carries the too-shallow-apron drops across *every returned* layout
    (one per dropped plane per layout — :attr:`SolverDiagnostics.apron_shallow_drops`
    in the solve path, or the ``apron_dropped_out`` out-param of a single
    :func:`~hangarfit.towplanner.plan_fill` in the view path). A plane may repeat
    (once per layout it is dropped in), so we **dedup by plane id**, warning once
    per plane and keeping the largest suggested depth seen for it. Emit order is
    first-seen order — deterministic because the producer's order is (#503).

    Each plane shows no slide-in: its footprint is too deep for the apron, so all
    its apron start poses were filtered. The suggested minimum depth is the plane's
    fore-aft FOOTPRINT extent — a conservative sufficient depth to engage the apron
    (the true gate is ≈ ``2·min(fore, aft)``), NOT the fleet-wide ``auto``
    over-margin. Purely observational: stderr only, never the plan."""
    suggested: dict[str, float] = {}
    for drop in drops:
        prev = suggested.get(drop.plane_id)
        if prev is None or drop.min_depth_m > prev:
            suggested[drop.plane_id] = drop.min_depth_m
    for plane_id, depth in suggested.items():
        print(
            f"warning: apron too shallow for plane {plane_id!r}: it tows in via the "
            f"door line (no slide-in). Increase the apron depth to >= {depth:.1f} m "
            f"(its footprint extent) so it engages the apron.",
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

    Output format matches ``examples/layouts/example.yaml`` so the file
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
) -> None:
    """Write the hangarfit.solve/v1 payload to stdout.

    ``spread_fallback_applied`` is read straight off ``SolverDiagnostics``:
    since #402 / F5 the spread-off re-solve is a library ``solve()`` decision
    (not a CLI one), so it is a genuine solver fact recorded on the result. See
    the ``SolverDiagnostics`` docstring for its always-present / no-schema-bump
    contract.
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
            # Additive (#280 → #402 / F5): True when solve() re-solved with
            # spread disabled and substituted that tighter, tow-routable
            # arrangement. Always present (False in the normal no-swap case) so
            # consumers can rely on it. Backward-compatible — no schema bump.
            "spread_fallback_applied": d.spread_fallback_applied,
            # Additive (#503): planes that towed via the door line because the
            # apron was too shallow for their footprint, with the suggested min
            # depth (their footprint extent). One entry per dropped plane per
            # returned layout; empty unless --render-paths ran the planner with an
            # apron set. Backward-compatible — no schema bump.
            "apron_shallow_drops": [
                {"plane": drop.plane_id, "min_depth_m": drop.min_depth_m}
                for drop in d.apron_shallow_drops
            ],
            # Additive (#263): nose-out heading flips applied per returned layout.
            # Backward-compatible — no schema bump.
            "nose_out_flips": list(d.nose_out_flips),
            # Additive (#604): per-layout per-object region alignment (0-1, 1.0 =
            # at the preferred wall). Empty list when no region preferences set.
            # Backward-compatible — no schema bump.
            "region_alignment": [
                {ra.body_id: ra.alignment for ra in layout_align}
                for layout_align in d.region_alignment
            ],
        },
    }
    print(json.dumps(payload, indent=2))


def cmd_view(args: argparse.Namespace) -> int:
    """Run the ``view`` subcommand: write a self-contained 3D HTML viewer.

    Layout mode loads a layout and (unless ``--no-animate``) best-effort
    tow-plans it for the whole-fill animation; an un-routable layout degrades to
    a static 3D scene with a stderr note (``plan_fill`` raises, so we catch it
    here — unlike the solver, it does not return ``None``-plans). Solve mode
    (``--solve``) solves a scenario first and views the first layout + its
    bundled plan; spread defaults OFF so the result is tow-routable (#280).
    """
    # Defer the scene/viewer/solver stack so `check` callers don't pay for it.
    from hangarfit import scene as scene_mod
    from hangarfit import viewer
    from hangarfit.models import SearchConfig
    from hangarfit.towplanner import NoFeasiblePlanError, plan_fill

    moves_plan: MovesPlan | None = None
    check_result: CheckResult | None = None
    # --check only applies to layout mode; a solved layout is valid by
    # construction (zero conflicts), so it would be a no-op. Surface the dropped
    # intent rather than silently ignoring it.
    if args.solve and args.check:
        print(
            "note: --check is ignored with --solve (a solved layout is valid by construction).",
            file=sys.stderr,
        )
    try:
        fleet_override = load_fleet(args.fleet) if args.fleet is not None else None
        hangar_override = (
            load_hangar(args.hangar, fleet=fleet_override) if args.hangar is not None else None
        )
        if args.solve:
            from hangarfit.loader import load_scenario
            from hangarfit.solver import solve

            scenario = load_scenario(
                args.input,
                fleet=fleet_override,
                hangar=hangar_override,
                max_carts=args.max_carts,
                apron_depth=args.apron_depth,
            )
            result = solve(
                scenario,
                budget_s=args.budget,
                alternatives=1,
                seed=args.seed,
                search=SearchConfig(spread=args.spread, nose_out=args.nose_out),
                plan_paths=args.animate,
                tow_max_expansions=args.tow_max_expansions,
            )
            if not result.layouts:
                print(f"error: no valid layout found (status={result.status})", file=sys.stderr)
                return 1
            layout = result.layouts[0]
            moves_plan = result.plans[0] if (args.animate and result.plans) else None
            if args.animate and moves_plan is None:
                print(
                    "note: solution not tow-routable; rendering static 3D scene.",
                    file=sys.stderr,
                )
            # #503: warn (deduped) about any plane that towed via the door line
            # because the apron was too shallow — the solve path carries the
            # RETURNED result's drops on its diagnostics, so a discarded
            # spread-fallback pass never contributes.
            if args.animate:
                _warn_apron_shallow_drops(result.diagnostics.apron_shallow_drops)
        else:
            layout = load_layout(
                args.input,
                fleet=fleet_override,
                hangar=hangar_override,
                max_carts=args.max_carts,
                apron_depth=args.apron_depth,
            )
            if args.check:
                check_result = collisions.check(layout)
            if args.animate:
                try:
                    # Cap the *global* fill budget so an un-routable layout
                    # degrades to a static scene in a few seconds rather than
                    # grinding through the full ~16000 disprove budget (#398). An
                    # explicit --tow-max-expansions overrides the view default and
                    # bounds the per-plane budget too.
                    view_total_cap = (
                        args.tow_max_expansions
                        if args.tow_max_expansions is not None
                        else _VIEW_TOW_MAX_TOTAL_EXPANSIONS
                    )
                    # #503: layout-mode view calls plan_fill directly (no
                    # SolverDiagnostics), so collect the too-shallow-apron drops via
                    # the plan-inert out-param and warn once each, deduped. Only a
                    # plan that is actually built (no NoFeasiblePlanError) reaches
                    # the warn call, so a discarded/failed plan never warns.
                    apron_drops: list[ApronShallowDrop] = []
                    moves_plan = plan_fill(
                        layout,
                        max_expansions=args.tow_max_expansions,
                        max_total_expansions=view_total_cap,
                        apron_dropped_out=apron_drops,
                    )
                    _warn_apron_shallow_drops(apron_drops)
                except NoFeasiblePlanError as e:
                    print(
                        f"note: layout not tow-routable (plane {e.plane_id!r} could not be "
                        f"routed); rendering static 3D scene.",
                        file=sys.stderr,
                    )
    except LoaderError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    scene = scene_mod.build_scene(layout, moves_plan=moves_plan, check_result=check_result)
    try:
        viewer.render_viewer(scene, args.output)
    except OSError as e:
        print(f"error: could not write {args.output}: {e}", file=sys.stderr)
        return 2
    print(f"wrote 3D viewer to {args.output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns an exit code; does not call ``sys.exit``."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "check":
        return cmd_check(args)
    if args.cmd == "solve":
        return cmd_solve(args)
    if args.cmd == "view":
        return cmd_view(args)
    # argparse with required=True should make this unreachable.
    parser.error(f"unknown command: {args.cmd!r}")
