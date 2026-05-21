"""Command-line interface for hangarfit.

Implements:
    hangarfit check LAYOUT [--render OUT.png] [--fleet PATH] [--hangar PATH] [--json]

See ``docs/superpowers/specs/2026-05-21-cli-design.md`` for the design.

JSON output schema: ``hangarfit.check/v1`` — a faithful dump of the
:class:`hangarfit.models.Conflict` dataclass (``kind`` / ``planes`` /
``detail``). Bump the schema version if and only if ``Conflict``
itself grows new fields.
"""

from __future__ import annotations

import argparse


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
        help="Override the layout's embedded fleet: ref. Cannot be combined with a layout that has an embedded fleet: field.",
    )
    check.add_argument(
        "--hangar",
        metavar="PATH",
        default=None,
        help="Override the layout's embedded hangar: ref. Cannot be combined with a layout that has an embedded hangar: field.",
    )
    check.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON on stdout (schema: hangarfit.check/v1).",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns an exit code; does not call ``sys.exit``."""
    parser = build_parser()
    args = parser.parse_args(argv)
    # Dispatch will be added in Task 3.
    raise NotImplementedError("Task 3 will wire up cmd_check dispatch.")
