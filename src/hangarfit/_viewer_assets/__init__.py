"""Static assets for the 3D viewer (vendored Three.js + ``viewer.js``).

Shipped as package data and read at runtime by :mod:`hangarfit.viewer` to
assemble a single self-contained, offline HTML file. Not importable Python —
this package exists only so :func:`importlib.resources.files` can locate the
assets inside an installed wheel.
"""
