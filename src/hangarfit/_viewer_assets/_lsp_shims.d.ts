// Editor-only TypeScript-LSP shim — NOT shipped in the wheel.
//
// `viewer.js` imports the vendored Three.js through the runtime
// `<script type="importmap">` emitted by viewer.py: the `three` and
// `three/addons/controls/OrbitControls.js` specifiers resolve to `data:` URLs at
// load time, NOT from disk. Without these ambient declarations the
// typescript-lsp plugin reports spurious ts(2307) "cannot find module 'three'"
// diagnostics on viewer.js's import lines.
//
// Declaring them as untyped modules satisfies resolution without parsing the
// vendored three.module.js bundle (which jsconfig.json also excludes from
// analysis). See .claude/README.md ("LSP plugins") and issue #423.
//
// Packaging note: package-data ships only `*.js` under `_viewer_assets/`
// (pyproject.toml [tool.setuptools.package-data]), so this `.d.ts` never reaches
// the runtime asset bundle.
declare module 'three';
declare module 'three/addons/controls/OrbitControls.js';
