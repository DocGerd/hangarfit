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
from hangarfit.models import CheckResult, Conflict

_JSON_SCHEMA = "hangarfit.check/v1"


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser with the ``check`` subcommand."""
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
        help="Override the fleet data file. Cannot be combined with a layout that already embeds a fleet path.",
    )
    check.add_argument(
        "--hangar",
        metavar="PATH",
        default=None,
        help="Override the hangar data file. Cannot be combined with a layout that already embeds a hangar path.",
    )
    check.add_argument(
        "--json",
        action="store_true",
        help=f"Emit JSON on stdout (schema: {_JSON_SCHEMA}).",
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


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns an exit code; does not call ``sys.exit``."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "check":
        return cmd_check(args)
    # argparse with required=True should make this unreachable.
    parser.error(f"unknown command: {args.cmd!r}")
