// Build the committed viewer bundle from viewer/src/*.ts.
//
// ADR-0020: this toolchain is DEV/CI-ONLY. `pip install` and the wheel build never
// invoke it — they consume the committed src/hangarfit/_viewer_assets/viewer.js. Once
// the `viewer-build-drift` CI guard lands (scope of #438, with the #439 port) it will
// rebuild and assert the committed bundle equals this output, keeping the shipped
// artifact in sync with the TS source. (During the #437 scaffold the committed
// viewer.js is the hand-written renderer, not this output — see ADR-0020.)
//
// three stays EXTERNAL: the bare `three` / OrbitControls imports are left in the output
// and resolved at HTML-assembly time by viewer.py's `data:` import-map over the vendored
// r160 sources, so viewer.py is unchanged. Bundling three would bloat the byte-diffed
// drift artifact ~58x (1.27 MB vs ~22 KB) and erode the guard — see ADR-0020.
//
// Unminified + a fixed es2022 target => a legible, byte-reproducible committed diff.
//
// VIEWER_OUTFILE overrides the destination — used by verification / headless builds so
// the committed bundle is never clobbered. CI's drift guard leaves it unset and writes
// the canonical path.
import { build } from "esbuild";
import { fileURLToPath } from "node:url";

const DEFAULT_OUTFILE = fileURLToPath(
  new URL("../src/hangarfit/_viewer_assets/viewer.js", import.meta.url),
);

await build({
  entryPoints: [fileURLToPath(new URL("./src/main.ts", import.meta.url))],
  outfile: process.env.VIEWER_OUTFILE ?? DEFAULT_OUTFILE,
  bundle: true,
  format: "esm",
  target: "es2022",
  minify: false,
  charset: "utf8",
  legalComments: "none",
  external: ["three", "three/addons/controls/OrbitControls.js"],
  logLevel: "info",
});
