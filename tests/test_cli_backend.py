"""CLI surface for the ``solve --backend`` switch (epic #607 rung 1, ADR-0027).

The default ``rrmc`` path is the unchanged deterministic solver; ``learned`` is the
opt-in neural backend, not yet implemented, which must fail cleanly (exit 2).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hangarfit.cli import build_parser, main

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SMOKE_FIXTURE = str(FIXTURES_DIR / "solve_feasible_smoke.yaml")


class TestBackendFlag:
    def test_backend_defaults_to_rrmc(self):
        args = build_parser().parse_args(["solve", SMOKE_FIXTURE])
        assert args.backend == "rrmc"

    def test_backend_learned_parses(self):
        args = build_parser().parse_args(["solve", SMOKE_FIXTURE, "--backend", "learned"])
        assert args.backend == "learned"

    def test_backend_invalid_choice_rejected(self):
        # argparse rejects an unknown choice with SystemExit(2) before any solving.
        with pytest.raises(SystemExit):
            build_parser().parse_args(["solve", SMOKE_FIXTURE, "--backend", "bogus"])

    def test_backend_learned_exits_2_cleanly(self, capsys):
        # Without --weights the backend raises LearnedBackendUnavailableError with a
        # weights-related message (Task 8 wired weights_path=None → that branch).
        code = main(["solve", SMOKE_FIXTURE, "--backend", "learned"])
        assert code == 2
        err = capsys.readouterr().err
        assert "weights" in err.lower()


def test_backend_learned_without_weights_exits_2(capsys, tmp_path):
    from hangarfit.cli import main

    # a minimal valid solve scenario fixture
    code = main(["solve", "tests/fixtures/scenario_minimal.yaml", "--backend", "learned"])
    assert code == 2
    err = capsys.readouterr().err
    assert "weights" in err.lower()
