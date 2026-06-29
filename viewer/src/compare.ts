// ── #666 multi-solution compare: pure switcher logic ─────────────────────────
// No THREE, no DOM — node-tested in compare.test.ts (ADR-0020). The impure wiring
// (the <select>, ←/→ keydown, swapping the rendered world) lives in main.ts and is
// covered by the headless swiftshader smoke.
import type { CompareManifest, CompareSolution } from './scene-contract.ts';

/** Next solution index for a step of `dir` (±1), wrapping around `n` entries.
 * `n <= 0` clamps to 0 (degenerate; a compare HTML always carries >= 1). */
export function wrapIndex(current: number, n: number, dir: number): number {
  if (n <= 0) return 0;
  return (((current + dir) % n) + n) % n;
}

/** Clamp an arbitrary index (e.g. a parsed <select> value) into `[0, n)`. */
export function clampIndex(i: number, n: number): number {
  if (n <= 0) return 0;
  if (!Number.isFinite(i)) return 0;
  return Math.min(Math.max(Math.trunc(i), 0), n - 1);
}

/** Format a gap (m) for display: null (single plane) → "n/a". */
function fmtGap(min_gap_m: number | null): string {
  return min_gap_m == null ? 'n/a' : min_gap_m.toFixed(2) + ' m';
}

/** The `<select>` option labels, one per solution: `"#1 — gap 1.23 m"`. */
export function optionLabels(solutions: CompareSolution[]): string[] {
  return solutions.map((s) => `${s.label} — gap ${fmtGap(s.summary.min_gap_m)}`);
}

/** The per-solution metrics readout for the active solution. Solution #1 shows no
 * moved-vs-#1 term (it IS the baseline); later solutions add the diversity delta. */
export function formatSummary(sol: CompareSolution): string {
  const s = sol.summary;
  const parts = [`min gap ${fmtGap(s.min_gap_m)}`];
  if (s.planes_moved_vs_first > 0) {
    parts.push(`${s.planes_moved_vs_first} moved vs #1 (avg ${s.mean_shift_m.toFixed(1)} m)`);
  }
  parts.push(s.routable ? 'tow-routable' : 'not tow-routable');
  return parts.join(' · ');
}

/** "Found n of N requested" when fewer diverse solutions than asked for (mirrors
 * `solve`); otherwise a plain count. */
export function foundLabel(m: CompareManifest): string {
  if (m.count_found < m.count_requested) {
    return `Found ${m.count_found} of ${m.count_requested} requested`;
  }
  return `${m.count_found} solution${m.count_found === 1 ? '' : 's'}`;
}
