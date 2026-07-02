"""#885: the ``--jobs N|auto`` parallel-execution path of the bench harness.

``bench correctness`` (the required CI gate) runs the fast regimes and exits
non-zero only on a VALIDITY / PATH-VALIDITY / DETERMINISM failure — verdicts that
are computed *inside* each ``run_regime`` (the determinism double-run is
within-process), so they do not depend on how the regimes are scheduled. This
module pins that contract: ``--jobs`` fans the regimes across worker processes
with byte-identical verdicts and order, and the speed-enforcing ``--gate`` path
is always forced back to serial (concurrent per-regime timing would trip the
ceilings).
"""

from __future__ import annotations

import pytest

import bench.profile_pipeline as pp
from bench.regimes import FIXTURES, Regime

_FAST_WITNESS = FIXTURES / "valid_left_side_nesting.yaml"


def _witness(key: str) -> Regime:
    """A tiny (~0.5 s), RNG-free, fully-routable witness regime for the parallel
    path — real enough to exercise pickling + a live process pool, cheap enough
    to stay a non-slow test."""
    return Regime(
        key=key,
        description="parallel-path test witness",
        layout=_FAST_WITNESS,
        n_planes=2,
        heavy=True,
    )


# ── --jobs resolution (pure) ─────────────────────────────────────────────────


def test_resolve_jobs_explicit_and_clamped() -> None:
    assert pp._resolve_jobs("1", 5) == 1
    assert pp._resolve_jobs("3", 5) == 3
    # More workers than regimes buys nothing — clamp to the regime count.
    assert pp._resolve_jobs("99", 5) == 5


def test_resolve_jobs_auto_is_bounded() -> None:
    for spec in ("auto", "0", "-4"):  # auto and any <=0 mean os.cpu_count()
        jobs = pp._resolve_jobs(spec, 5)
        assert 1 <= jobs <= 5


def test_resolve_jobs_rejects_garbage() -> None:
    with pytest.raises(SystemExit):
        pp._resolve_jobs("nan", 5)


def test_resolve_jobs_handles_empty_regime_list() -> None:
    # No regimes ⇒ still a valid worker count (>=1), never a min() over empty.
    assert pp._resolve_jobs("auto", 0) == 1


# ── parallel ≡ serial (real process pool) ────────────────────────────────────


def test_parallel_matches_serial() -> None:
    """Running two regimes across a pool yields the same verdicts, in the same
    order, as running them serially — the whole point of the correctness gate
    being schedule-independent."""
    regimes = [_witness("_p_a"), _witness("_p_b")]
    serial = pp._run_regimes(regimes, jobs=1)
    parallel = pp._run_regimes(regimes, jobs=2)

    assert [r.key for r in parallel] == [r.key for r in serial] == ["_p_a", "_p_b"]
    for s, p in zip(serial, parallel, strict=True):
        assert (p.layouts_valid, p.paths_valid, p.deterministic, p.n_routed) == (
            s.layouts_valid,
            s.paths_valid,
            s.deterministic,
            s.n_routed,
        )
        assert p.layouts_valid and p.paths_valid and p.deterministic


def test_single_regime_short_circuits_to_serial() -> None:
    """A one-regime run never spawns a pool even with jobs>1 (nothing to
    parallelize); it still produces the correct verdict."""
    (result,) = pp._run_regimes([_witness("_solo")], jobs=8)
    assert result.layouts_valid and result.paths_valid and result.deterministic


def test_available_cpus_is_positive() -> None:
    assert pp._available_cpus() >= 1


def test_pool_break_falls_back_to_serial(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A ``BrokenProcessPool`` (worker died — an infra hiccup, NOT a verdict
    failure) on the required correctness gate falls back to the trusted serial
    path rather than blocking every merge: same regimes, correct verdicts, note
    on stderr. A genuine verdict failure is a different exception and still
    propagates (covered by the fail-loud contract, verified separately)."""
    from concurrent.futures.process import BrokenProcessPool

    class _BrokenPool:
        def __init__(self, *a: object, **k: object) -> None: ...
        def __enter__(self) -> _BrokenPool:
            return self

        def __exit__(self, *a: object) -> bool:
            return False

        def map(self, *a: object, **k: object) -> object:
            raise BrokenProcessPool("simulated worker death")

    monkeypatch.setattr(pp, "ProcessPoolExecutor", _BrokenPool)
    results = pp._run_regimes([_witness("_b_a"), _witness("_b_b")], jobs=2)

    assert [r.key for r in results] == ["_b_a", "_b_b"]
    assert all(r.layouts_valid and r.paths_valid and r.deterministic for r in results)
    assert "falling back to serial" in capsys.readouterr().err


# ── --gate always runs serial ────────────────────────────────────────────────


def test_gate_forces_serial(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """Under --gate the speed ceilings need isolated serial timing, so --jobs is
    ignored: ``_run_regimes`` must be invoked with ``jobs == 1`` and the note
    printed to stderr — even when --jobs 4 is passed."""
    captured: dict[str, int] = {}
    real = pp._run_regimes

    def spy(regimes: list[Regime], jobs: int) -> list:
        captured["jobs"] = jobs
        return real(regimes, jobs)

    monkeypatch.setattr(pp, "_run_regimes", spy)
    # Two regimes so --jobs would otherwise resolve to >1 (a single regime clamps
    # to serial on its own, making the gate suppression a no-op). trivial_single
    # is the cheapest registered regime; its 10 s ceiling passes comfortably.
    rc = pp.main(
        ["--gate", "--jobs", "4", "--regime", "trivial_single", "--regime", "trivial_single"]
    )

    assert rc == 0
    assert captured["jobs"] == 1, "--gate must force serial regardless of --jobs"
    assert "ignored under --gate" in capsys.readouterr().err
