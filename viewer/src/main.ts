// Placeholder entry for the #437 toolchain scaffold (ADR-0020 / epic #436).
//
// The real viewer — viewer.js's nine comment-delimited sections ported to typed
// modules (affine, anchors, renderer, hangar, gear, planes, labels, timeline, hud, …)
// — lands as ONE atomic, behavior-neutral PR in #439. This stub renders nothing; it
// exists only so the esbuild -> tsc -> eslint pipeline is real and reproducible before
// the port, and so #437 ships a buildable, guardable toolchain without touching the
// working committed viewer.js.
//
// The typed export below is inert (nothing imports it; it renders nothing) but is
// preserved as the entry's public API, so the build produces a non-empty, deterministic
// bundle that demonstrably exercises esbuild's TS type-stripping.
export const SCAFFOLD: { readonly epic: number; readonly issue: number } = {
  epic: 436,
  issue: 437,
};
