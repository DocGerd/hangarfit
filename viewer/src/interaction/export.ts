// viewer/src/interaction/export.ts
import type { Intent, EditorContext } from './intent-contract.ts';

/** Deterministic float rendering: round to 4 dp, always show ≥1 decimal. */
function num(n: number): string {
  const r = Math.round(n * 1e4) / 1e4;
  return Number.isInteger(r) ? `${r}.0` : `${r}`;
}

export function intentToScenarioYaml(intent: Intent, ctx: EditorContext): string {
  const selected = [...intent.selectedPlaneIds].sort();
  // The maintenance occupant is never rendered/selected/constrained, but the Scenario
  // invariant requires maintenance ∈ fleet_in (models.py Scenario.__post_init__) — union it in.
  const fleetIn = [...new Set(ctx.maintenance ? [...selected, ctx.maintenance.plane] : selected)].sort();
  const lines: string[] = [];
  lines.push(`fleet: ${ctx.fleet}`);
  lines.push(`hangar: ${ctx.hangar}`);
  lines.push(`fleet_in: [${fleetIn.join(', ')}]`);
  if (ctx.maintenance) { lines.push('maintenance:'); lines.push(`  plane: ${ctx.maintenance.plane}`); }

  // Only selected planes may carry constraints (never the maintenance plane).
  const constrained = selected.filter((id) => intent.mustPositions[id] || intent.priorities[id] !== undefined);
  if (constrained.length) {
    lines.push('constraints:');
    for (const id of constrained) {
      lines.push(`  ${id}:`);
      const mp = intent.mustPositions[id];
      if (mp) {
        lines.push(
          `    pin: { x_m: ${num(mp.x)}, y_m: ${num(mp.y)}, ` +
          `heading_deg: ${num(mp.heading)}, on_carts: ${mp.onCarts} }`,
        );
      }
      const p = intent.priorities[id];
      if (p !== undefined) lines.push(`    priority: ${num(p)}`);
    }
  }
  return lines.join('\n') + '\n';
}
