"""Tests for the hangarfit CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hangarfit.cli import main

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parent.parent


class TestArgparseUsageErrors:
    """Bare / unknown commands fall through to argparse's own SystemExit(2)."""

    def test_subparser_no_command_shows_help(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 2
        # argparse writes its usage error to stderr
        captured = capsys.readouterr()
        assert "usage:" in captured.err.lower()

    def test_unknown_subcommand_returns_2(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["nope"])
        assert exc_info.value.code == 2


class TestCheckHappyPath:
    """Valid layouts exit 0; invalid layouts exit 1 with conflict lines on stdout."""

    def test_check_valid_layout_returns_0(self, capsys):
        exit_code = main(["check", str(FIXTURES_DIR / "valid_two_separated.yaml")])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == "valid"
        assert captured.err == ""

    def test_default_example_layout_is_valid(self, capsys):
        """The canonical smoke test from CLAUDE.md must produce ``valid``.

        ``hangarfit check layouts/example.yaml`` is the first thing a new
        contributor runs after cloning. If this regresses, the default
        user experience is "the algorithm looks broken" — even when the
        checker itself is fine. Pin it.
        """
        exit_code = main(["check", str(REPO_ROOT / "layouts" / "example.yaml")])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == "valid"
        assert captured.err == ""

    def test_default_example_invalid_layout_lists_conflicts(self, capsys):
        """The companion ``layouts/example_invalid.yaml`` must exit 1 and
        produce at least one conflict — it's the demo for the red-overlay
        rendering and the JSON conflicts list."""
        exit_code = main(["check", str(REPO_ROOT / "layouts" / "example_invalid.yaml")])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.out.startswith("invalid:")
        assert captured.err == ""

    def test_check_invalid_layout_returns_1(self, capsys):
        exit_code = main(["check", str(FIXTURES_DIR / "invalid_fuselage_wing_overlap.yaml")])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.out.startswith("invalid:")
        # Conflict line uses the spec's format: "  - <kind> [<plane>[, <plane>]]: <detail>"
        # The fuselage front/aft split (#50/ADR-0012) replaced the single
        # fuselage_wing_overlap kind with the segment-specific kinds; this
        # fixture's wing crosses both of scheibe's fuselage segments.
        assert "fuselage_front_wing_overlap" in captured.out
        # Every conflict line starts with the two-space-dash prefix
        for line in captured.out.strip().split("\n")[1:]:
            assert line.startswith("  - ")
        assert captured.err == ""


class TestCheckLoadErrors:
    """LoaderError (file not found, bad YAML, invariant violation) → exit 2 on stderr."""

    def test_check_missing_file_returns_2(self, capsys):
        exit_code = main(["check", "definitely/does/not/exist.yaml"])
        assert exit_code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "error:" in captured.err
        assert "not found" in captured.err

    def test_check_malformed_yaml_returns_2(self, tmp_path, capsys):
        bad = tmp_path / "bad.yaml"
        bad.write_text(":::not valid yaml:::\n", encoding="utf-8")
        exit_code = main(["check", str(bad)])
        assert exit_code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "error:" in captured.err

    def test_check_invariant_violation_returns_2(self, capsys):
        # invalid_cart_rule.yaml puts two cart_eligible planes on_carts.
        # Layout.__post_init__ raises ValueError; loader wraps it in LoaderError.
        exit_code = main(["check", str(FIXTURES_DIR / "invalid_cart_rule.yaml")])
        assert exit_code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "error:" in captured.err
        assert "cart" in captured.err.lower()

    def test_check_max_carts_override_loosens_cart_rule(self, capsys):
        # --max-carts 2 replaces the cap on the loaded hangar before the Layout
        # is built, so the two-cart_eligible layout that exits 2 above now loads
        # — the load-time cart-rule rejection is gone.
        exit_code = main(
            ["check", str(FIXTURES_DIR / "invalid_cart_rule.yaml"), "--max-carts", "2"]
        )
        captured = capsys.readouterr()
        assert "At most" not in captured.err  # the cart-rule load error is gone
        assert exit_code != 2  # load succeeded; a non-zero would be a geometry verdict

    def test_check_max_carts_negative_is_clean_exit_2(self, capsys):
        # A negative --max-carts must surface as a clean exit-2 LoaderError,
        # not a raw ValueError traceback from dataclasses.replace.
        exit_code = main(["check", str(FIXTURES_DIR / "invalid_cart_rule.yaml"), "--max-carts=-1"])
        assert exit_code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "error:" in captured.err
        assert "max_carts" in captured.err


class TestCheckJsonOutput:
    """--json emits the hangarfit.check/v1 schema on stdout."""

    def test_check_json_valid_emits_schema_v1(self, capsys):
        layout = str(FIXTURES_DIR / "valid_two_separated.yaml")
        exit_code = main(["check", "--json", layout])
        assert exit_code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["schema"] == "hangarfit.check/v1"
        assert payload["valid"] is True
        assert payload["conflicts"] == []
        assert payload["layout"] == layout

    def test_check_json_invalid_lists_conflicts(self, capsys):
        layout = str(FIXTURES_DIR / "invalid_fuselage_wing_overlap.yaml")
        exit_code = main(["check", "--json", layout])
        assert exit_code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["schema"] == "hangarfit.check/v1"
        assert payload["valid"] is False
        assert len(payload["conflicts"]) >= 1
        for c in payload["conflicts"]:
            # Faithful dump of Conflict — exactly these three keys, nothing else.
            assert set(c.keys()) == {"kind", "planes", "detail"}
            assert isinstance(c["kind"], str)
            assert isinstance(c["planes"], list)
            assert 1 <= len(c["planes"]) <= 2
            assert all(isinstance(p, str) for p in c["planes"])
            assert isinstance(c["detail"], str)


class TestCheckRender:
    """--render writes a PNG on valid and invalid layouts, exits 2 on render failure,
    and is skipped entirely when there is a structural load error."""

    def test_check_render_writes_png(self, tmp_path, capsys):
        out = tmp_path / "valid.png"
        layout = str(FIXTURES_DIR / "valid_two_separated.yaml")
        exit_code = main(["check", layout, "--render", str(out)])
        assert exit_code == 0
        assert out.exists()
        assert out.stat().st_size > 0

    def test_check_render_on_invalid_writes_png(self, tmp_path, capsys):
        out = tmp_path / "invalid.png"
        layout = str(FIXTURES_DIR / "invalid_fuselage_wing_overlap.yaml")
        exit_code = main(["check", layout, "--render", str(out)])
        assert exit_code == 1
        assert out.exists()
        assert out.stat().st_size > 0

    def test_check_render_skipped_on_structural_error(self, tmp_path, capsys):
        out = tmp_path / "should_not_exist.png"
        layout = str(FIXTURES_DIR / "invalid_cart_rule.yaml")
        exit_code = main(["check", layout, "--render", str(out)])
        assert exit_code == 2
        assert not out.exists()

    def test_check_render_failure_returns_2(self, tmp_path, capsys):
        # Unwritable path: the intermediate directory does not exist.
        out = tmp_path / "no_such_dir" / "out.png"
        layout = str(FIXTURES_DIR / "valid_two_separated.yaml")
        exit_code = main(["check", layout, "--render", str(out)])
        assert exit_code == 2
        captured = capsys.readouterr()
        assert "error:" in captured.err


class TestFleetHangarOverrides:
    """--fleet / --hangar work only when the layout has no embedded ref."""

    def test_no_override_uses_embedded(self, capsys):
        # Existing fixtures all embed fleet:/hangar: — no override given,
        # the loader resolves from the YAML. This is the regression guard.
        exit_code = main(["check", str(FIXTURES_DIR / "valid_two_separated.yaml")])
        assert exit_code == 0
        assert capsys.readouterr().out.strip() == "valid"

    def test_fleet_override_with_clean_layout(self, tmp_path, capsys):
        # A layout that does NOT embed fleet:/hangar: — both must come from --fleet/--hangar.
        # We copy an existing fixture but strip the embedded refs.
        src = FIXTURES_DIR / "valid_two_separated.yaml"
        clean = tmp_path / "clean_layout.yaml"
        original = src.read_text(encoding="utf-8")
        stripped = "\n".join(
            line
            for line in original.splitlines()
            if not (line.startswith("fleet:") or line.startswith("hangar:"))
        )
        clean.write_text(stripped + "\n", encoding="utf-8")

        exit_code = main(
            [
                "check",
                str(clean),
                "--fleet",
                str(REPO_ROOT / "data" / "fleet.yaml"),
                "--hangar",
                str(REPO_ROOT / "data" / "hangar.yaml"),
            ]
        )
        assert exit_code == 0
        assert capsys.readouterr().out.strip() == "valid"

    def test_fleet_override_with_embedded_fleet_errors(self, capsys):
        # Both kwarg and embedded are present — loader rejects this.
        exit_code = main(
            [
                "check",
                str(FIXTURES_DIR / "valid_two_separated.yaml"),
                "--fleet",
                str(REPO_ROOT / "data" / "fleet.yaml"),
            ]
        )
        assert exit_code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "error:" in captured.err


def test_solve_human_output_shows_min_gap(capsys):
    from hangarfit.cli import main

    rc = main(
        ["solve", "tests/fixtures/solve_fresh_six_planes.yaml", "--seed", "7", "--budget", "5"]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "min gap" in out  # per-layout spread quality line


def test_solve_json_output_has_spread_diagnostics(capsys):
    import json

    from hangarfit.cli import main

    rc = main(
        [
            "solve",
            "tests/fixtures/solve_fresh_six_planes.yaml",
            "--seed",
            "7",
            "--budget",
            "5",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    diag = payload["diagnostics"]
    assert "min_pairwise_gap_m" in diag
    assert "valid_basins_found" in diag
    assert len(diag["min_pairwise_gap_m"]) == len(payload["layouts"])
    # finite floats stay numbers; single-plane inf becomes null
    assert all(g is None or isinstance(g, (int, float)) for g in diag["min_pairwise_gap_m"])


def test_solve_json_single_plane_min_gap_is_null(capsys):
    """A single-plane layout has no plane pairs → min_pairwise_gap_m is
    math.inf internally, which MUST serialize as JSON null (not the invalid
    token `Infinity`). Guards the inf→null mapping in _emit_solve_json."""
    import json

    from hangarfit.cli import main

    rc = main(
        [
            "solve",
            "tests/fixtures/solve_trivial_single_plane.yaml",
            "--seed",
            "7",
            "--budget",
            "5",
            "--json",
        ]
    )
    raw = capsys.readouterr().out
    assert rc == 0
    assert "Infinity" not in raw  # would be invalid JSON
    payload = json.loads(raw)  # must parse
    gaps = payload["diagnostics"]["min_pairwise_gap_m"]
    assert gaps == [None]
