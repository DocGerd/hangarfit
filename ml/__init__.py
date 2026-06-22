"""hangarfit learned-backend RL workspace (epic #607).

Dev/CI-only — like ``bench/`` and ``viewer/`` this top-level package is NOT in the
wheel (``[tool.setuptools.packages.find] where = ["src"]``). It holds the cold-joint
RL environment + reward (sub-project #1) that reuse the deterministic ``hangarfit``
geometry as a reward oracle. No neural net or training lives here yet.
"""
