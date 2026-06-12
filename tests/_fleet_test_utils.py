"""Shared test helper for the per-object catalog model (#595).

Turns an inline fleet YAML *string* into a catalog + manifest on disk, so
loader / error-path tests can exercise the real catalog load path. Lives in
``tests/`` (a package) so the loader test modules can import it instead of each
carrying their own copy."""

from __future__ import annotations

from pathlib import Path

import yaml


def explode_fleet(tmp_path: Path, fleet_yaml: str) -> Path:
    """Materialise an inline fleet YAML *string* as a fleet manifest under
    ``tmp_path/fleet.yaml``; return its path.

    A well-formed ``{aircraft: [<dicts>]}`` fleet is exploded into per-object
    ``catalog/obj_<i>.yaml`` files (``type: aircraft`` prepended) + a manifest of
    refs, so aircraft-build validation fires through the catalog path (the
    validation message is preserved; only the error prefix changes). Any other
    shape (no ``aircraft`` key, ``aircraft`` not a list, or malformed YAML) is
    written verbatim, so the loader's manifest-level / YAML-parse guards still
    fire unchanged. This lets every fleet-loading test route through one helper."""
    manifest = tmp_path / "fleet.yaml"
    try:
        raw = yaml.safe_load(fleet_yaml)
    except yaml.YAMLError:
        raw = None
    entries = raw.get("aircraft") if isinstance(raw, dict) else None
    if isinstance(entries, list):
        cat = tmp_path / "catalog"
        cat.mkdir(exist_ok=True)
        refs: list[object] = []
        for i, ac in enumerate(entries):
            if isinstance(ac, dict):
                (cat / f"obj_{i}.yaml").write_text(
                    yaml.safe_dump({"type": "aircraft", **ac}, allow_unicode=True, sort_keys=False),
                    encoding="utf-8",
                )
                refs.append(f"catalog/obj_{i}.yaml")
            else:
                refs.append(ac)  # non-dict entry exercises the ref-shape guard
        manifest.write_text(
            yaml.safe_dump({"aircraft": refs}, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    else:
        manifest.write_text(fleet_yaml, encoding="utf-8")
    return manifest
