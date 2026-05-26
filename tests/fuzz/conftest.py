"""Hypothesis settings profiles for the loader fuzz suite.

Selected via the HYPOTHESIS_PROFILE env var (default "ci"):
  ci      — fast, runs on every PR (default `pytest`)
  nightly — deep run for the nightly fuzz workflow
  dev     — local opt-in middle ground

deadline=None: the loader does small-file I/O per example; a per-example
deadline flakes on CI cold starts for no signal.
"""

import os

from hypothesis import HealthCheck, settings

settings.register_profile("dev", max_examples=100, deadline=None)
settings.register_profile("ci", max_examples=50, deadline=None)
settings.register_profile(
    "nightly",
    max_examples=2000,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "ci"))
