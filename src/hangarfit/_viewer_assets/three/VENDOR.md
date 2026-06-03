# Vendored Three.js

These files are vendored (checked into the repo) so the `hangarfit view` 3D
viewer is **fully self-contained and offline** — the generated HTML embeds them
via a `data:` URL import-map and needs zero network access (ADR-0017). They ship
as package data inside the wheel, reachable via
`importlib.resources.files("hangarfit._viewer_assets.three")`.

## Pinned version

- **three.js** `r160` (npm `three@0.160.0`)
- License: **MIT** (`THREE_LICENSE.txt`, © 2010–2023 three.js authors)

## Sources (jsDelivr, pinned by exact version)

| File | Source URL |
|---|---|
| `three.module.js` | https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js |
| `OrbitControls.js` | https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/controls/OrbitControls.js |
| `THREE_LICENSE.txt` | https://cdn.jsdelivr.net/npm/three@0.160.0/LICENSE |

`OrbitControls.js` imports the bare specifier `from 'three'`; the viewer's
import-map resolves both `three` and
`three/addons/controls/OrbitControls.js` to `data:` URLs, so the bare import
works from a `file://` page.

## SHA-256 (verify after any refresh)

```
76dea8151bc9352aef3528b4262e249b2604f62543828328db978d060d61a495  three.module.js
5a44a9e86a2a0fb11933eed69bc2cd33c76a496854c1aed6ed776efa87d7b064  OrbitControls.js
852e0e8699169bf9f6fdc6bda3e682d078dcbc738b5d33e74df594721bff271d  THREE_LICENSE.txt
```

## Refresh procedure

```bash
V=<new-version>           # e.g. 0.161.0
DST=src/hangarfit/_viewer_assets/three
curl -fsSL "https://cdn.jsdelivr.net/npm/three@${V}/build/three.module.js" -o "$DST/three.module.js"
curl -fsSL "https://cdn.jsdelivr.net/npm/three@${V}/examples/jsm/controls/OrbitControls.js" -o "$DST/OrbitControls.js"
curl -fsSL "https://cdn.jsdelivr.net/npm/three@${V}/LICENSE" -o "$DST/THREE_LICENSE.txt"
sha256sum "$DST"/three.module.js "$DST"/OrbitControls.js "$DST"/THREE_LICENSE.txt
# Update the pinned version + the hash block above, then run the viewer tests.
```

After a refresh, run `pytest tests/test_viewer.py` and open a generated HTML to
confirm the viewer still loads (the in-browser anchor self-check will fail loudly
if a Three.js API change broke matrix handling).
