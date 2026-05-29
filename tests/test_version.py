"""The package ``__version__`` is sourced from installed metadata, not hard-coded.

Guards the #341 fix: ``__version__`` used to be a literal ``"0.0.1"`` that drifted
from ``pyproject.toml`` across every release. It now derives from
``importlib.metadata`` so the two cannot disagree.
"""

from importlib.metadata import version

import hangarfit


def test_version_matches_installed_metadata() -> None:
    """``hangarfit.__version__`` reflects the installed distribution version."""
    assert hangarfit.__version__ == version("hangarfit")


def test_version_is_not_the_stale_placeholder() -> None:
    """Regression: the old hard-coded ``0.0.1`` placeholder is gone."""
    assert hangarfit.__version__ != "0.0.1"
