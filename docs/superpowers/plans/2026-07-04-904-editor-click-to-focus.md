# #904 Editor Click-to-Focus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple *focus* from *fleet membership* in the `view --solve --edit` editor — a canvas click focuses a plane for editing (never dropping its constraints); fleet add/remove stays on the existing #910 palette checkboxes.

**Architecture:** Pure `viewer/src/interaction/*` change. A new three.js-free `highlight.ts` module owns the emissive-hue decision as a pure function (`focusAwareHex`) so the new tri-state (focused / member / excluded) is unit-tested without the untested raycaster. `editor.ts` wires it in and changes the `pointerup` handler to focus-only, plus removes the palette handler's `focusedId` side effect. The committed `viewer.js` bundle is rebuilt. No Python / `_HUD_EDIT` / scene / loader change.

**Tech Stack:** TypeScript (strict) + three r160 (vendored, external); esbuild bundle → `src/hangarfit/_viewer_assets/viewer.js`; `node --test` for pure units; pytest for the Python byte-identity guard.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-07-04-904-editor-click-to-focus-design.md` (approved).
- **Branch:** `feature/904-editor-click-to-focus` (already created off `develop`).
- **ADR-0002 / ADR-0029:** the browser never re-derives geometry. `editor.ts` and `highlight.ts` must **not** import `affine.ts` or `anchors.ts`. Focus / membership are pure state over Python-emitted scalars.
- **No Python change.** `tests/test_viewer.py` byte-identity asserts on the `--edit` HTML must stay green (positive proof no HUD DOM was added).
- **Rebuild the bundle in the same change.** After editing any `viewer/src/*.ts`, run `npm --prefix viewer/ run build` and commit `src/hangarfit/_viewer_assets/viewer.js` — the `viewer-build-drift` CI guard fails otherwise.
- **Directory-aware commands:** use `npm --prefix viewer/ run <script>` (never `cd viewer && …`).
- **Delivery:** draft PR → review arc (`pr-review-toolkit:code-reviewer` mandatory + `scene-schema-guard` because the committed `viewer.js` is in its scope) → resolve findings → `gh pr ready`. Never merge from Claude.

---

## File Structure

- **Create** `viewer/src/interaction/highlight.ts` — pure emissive-hue decision. Exports `EXCLUDED_EMISSIVE`, `FOCUS_EMISSIVE`, and `focusAwareHex(selected, focused, orig)`. No three/DOM import → unit-testable in isolation.
- **Create** `viewer/test/highlight.test.ts` — `node --test` truth table for `focusAwareHex`.
- **Modify** `viewer/src/interaction/editor.ts` — import `focusAwareHex`; make `applyHighlight` focus-aware; `pointerup` → focus-only; palette handler → drop the `focusedId` side effect.
- **Modify** `src/hangarfit/_viewer_assets/viewer.js` — rebuilt bundle (generated; commit alongside).
- **Modify** `CHANGELOG.md` — one `[Unreleased]` entry for the editor UX change.

---

### Task 1: Pure `focusAwareHex` highlight module (TDD)

**Files:**
- Create: `viewer/src/interaction/highlight.ts`
- Test: `viewer/test/highlight.test.ts`

**Interfaces:**
- Produces:
  - `export const EXCLUDED_EMISSIVE = 0x552200` — amber "excluded" hue (unchanged from the current inline literal in `editor.ts`).
  - `export const FOCUS_EMISSIVE = 0x2266ff` — the new "focused" highlight hue (a blue accent, deliberately distinct from amber).
  - `export function focusAwareHex(selected: boolean, focused: boolean, orig: number): number` — precedence: `focused` → `FOCUS_EMISSIVE`; else `selected` → `orig`; else `EXCLUDED_EMISSIVE`.

- [ ] **Step 1: Write the failing test**

Create `viewer/test/highlight.test.ts`:

```ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { focusAwareHex, EXCLUDED_EMISSIVE, FOCUS_EMISSIVE } from '../src/interaction/highlight.ts';

const ORIG = 0x0a0a0a; // a representative base emissive (often near-black on plane materials)

test('focus takes visual precedence over membership', () => {
  assert.equal(focusAwareHex(true, true, ORIG), FOCUS_EMISSIVE); // focused member
  assert.equal(focusAwareHex(false, true, ORIG), FOCUS_EMISSIVE); // focused non-member
});

test('unfocused member shows its original emissive', () => {
  assert.equal(focusAwareHex(true, false, ORIG), ORIG);
});

test('unfocused non-member shows the excluded amber', () => {
  assert.equal(focusAwareHex(false, false, ORIG), EXCLUDED_EMISSIVE);
});

test('excluded amber constant is the historical hue (byte-compatible with the old inline literal)', () => {
  assert.equal(EXCLUDED_EMISSIVE, 0x552200);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix viewer/ run test`
Expected: FAIL — cannot resolve `../src/interaction/highlight.ts` (module not found).

- [ ] **Step 3: Write minimal implementation**

Create `viewer/src/interaction/highlight.ts`:

```ts
// viewer/src/interaction/highlight.ts — the pure emissive-hue decision for the
// editor's plane highlight (#904). Three-way precedence over the SINGLE emissive
// channel: a focused plane shows FOCUS_EMISSIVE regardless of membership (so
// "which plane am I editing" reads independently of "is it in the fleet"); an
// unfocused plane shows its membership hue (original emissive if selected, amber
// EXCLUDED_EMISSIVE if not). Pure numbers-in/number-out: NO three/DOM import, so
// it is unit-tested in isolation without the untested raycaster path. The focused
// hue doubles as #911's TransformControls gizmo-target indicator.

/** Amber "excluded from the fleet" emissive (unchanged from editor.ts's old inline literal). */
export const EXCLUDED_EMISSIVE = 0x552200;

/** Blue "currently focused for editing" emissive — deliberately distinct from the amber excluded hue. */
export const FOCUS_EMISSIVE = 0x2266ff;

/**
 * The emissive hex a plane's highlight should show.
 * @param selected whether the plane is in the fleet (`isSelected(intent, id)`)
 * @param focused  whether the plane is the one being edited (`id === focusedId`)
 * @param orig     the plane's original (pre-highlight) emissive hex
 */
export function focusAwareHex(selected: boolean, focused: boolean, orig: number): number {
  if (focused) return FOCUS_EMISSIVE;
  return selected ? orig : EXCLUDED_EMISSIVE;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix viewer/ run test`
Expected: PASS (the 4 new `highlight.test.ts` tests plus the existing suite).

- [ ] **Step 5: Typecheck + lint the new module**

Run: `npm --prefix viewer/ run typecheck && npm --prefix viewer/ run lint`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add viewer/src/interaction/highlight.ts viewer/test/highlight.test.ts
git commit -m "feat(viewer): pure focusAwareHex highlight decision (#904)

Three-way emissive precedence (focused > member > excluded) as a pure,
three.js-free function so the new focus visual is unit-tested without the
untested raycaster path.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QrHcFVwhf2L3wNVQZffMMd"
```

---

### Task 2: Wire focus-only click + focus-aware highlight + palette decouple into `editor.ts`, rebuild bundle

**Files:**
- Modify: `viewer/src/interaction/editor.ts` (the import block ~8–26; `pointerup` handler ~101–116; `applyHighlight` ~118–123; palette `change` handler ~367–379)
- Modify (generated): `src/hangarfit/_viewer_assets/viewer.js`

**Interfaces:**
- Consumes: `focusAwareHex`, `EXCLUDED_EMISSIVE` from `./highlight.ts` (Task 1). *(`FOCUS_EMISSIVE` is used indirectly via `focusAwareHex`; import only what is referenced so eslint's no-unused stays green.)*

- [ ] **Step 1: Import the pure helper; delete the inline amber literal**

In `viewer/src/interaction/editor.ts`, add to the imports (near the `./selection.ts` import, ~line 23):

```ts
import { focusAwareHex } from './highlight.ts';
```

- [ ] **Step 2: Make `applyHighlight` focus-aware**

Replace the body of `applyHighlight` (currently ~lines 118–123):

```ts
  function applyHighlight(): void {
    // Selected planes keep their original emissive; deselected planes glow amber ("excluded").
    for (const t of targets) {
      t.mat.emissive.setHex(isSelected(intent, t.id) ? t.orig : 0x552200);
    }
  }
```

with:

```ts
  function applyHighlight(): void {
    // Three states over the emissive channel (#904): the FOCUSED plane glows blue
    // (which plane the panel edits — independent of membership); an unfocused plane
    // shows its membership hue (original emissive if in the fleet, amber if excluded).
    for (const t of targets) {
      t.mat.emissive.setHex(focusAwareHex(isSelected(intent, t.id), t.id === focusedId, t.orig));
    }
  }
```

- [ ] **Step 3: Make the `pointerup` handler focus-only**

Replace the `pointerup` listener body (currently ~lines 101–116):

```ts
  el.addEventListener(
    'pointerup',
    (ev: PointerEvent) => {
      if (Math.hypot(ev.clientX - downX, ev.clientY - downY) > 6) return; // a drag, not a click
      const id = pick(ev);
      if (!id) return;
      intent = toggleSelection(intent, id);
      focusedId = isSelected(intent, id) ? id : null;
      applyHighlight();
      renderReadout();
      syncControls();
      renderDoorOrder(); // deselect un-ranks; focus change updates the add-button state
      renderPalette(); // reflect the selection change in the palette checkboxes
    },
    sig,
  );
```

with (drop the membership toggle; drop `renderReadout`/`renderPalette` — `selectedPlaneIds` is unchanged):

```ts
  el.addEventListener(
    'pointerup',
    (ev: PointerEvent) => {
      if (Math.hypot(ev.clientX - downX, ev.clientY - downY) > 6) return; // a drag, not a click
      const id = pick(ev);
      if (!id) return;
      // #904: click = FOCUS only. No membership toggle, so a plane's priority/pin/
      // cart-mode/door-rank are never dropped by focusing it. Fleet add/remove lives
      // on the #910 palette checkboxes. focusedId is the stable, membership-independent
      // handle #911's TransformControls gizmo will attach to.
      focusedId = id;
      applyHighlight(); // repaint the focus highlight onto the newly focused plane
      syncControls(); // control enablement reflects the focused plane (gated on isSelected)
      renderDoorOrder(); // the ＋rank button enablement reads focus + membership
    },
    sig,
  );
```

- [ ] **Step 4: Remove the palette handler's `focusedId` side effect**

In `renderPalette`, replace the aircraft branch of the checkbox `change` handler (currently ~lines 367–379):

```ts
      box.addEventListener('change', () => {
        if (isAircraft) {
          intent = toggleSelection(intent, id);
          focusedId = isSelected(intent, id) ? id : focusedId === id ? null : focusedId;
          applyHighlight();
          renderReadout();
          syncControls();
          renderDoorOrder();
        } else if (isMover) {
          intent = toggleGroundObject(intent, id);
        }
        renderPalette();
      });
```

with (drop the `focusedId` mutation — palette = membership only; canvas = focus):

```ts
      box.addEventListener('change', () => {
        if (isAircraft) {
          // #904: the palette toggles fleet membership ONLY; it must not move focus
          // (focus is the canvas click's job — moving it here would yank a future
          // gizmo target). The focused plane's controls still refresh below in case
          // its own membership was the thing toggled.
          intent = toggleSelection(intent, id);
          applyHighlight();
          renderReadout();
          syncControls();
          renderDoorOrder();
        } else if (isMover) {
          intent = toggleGroundObject(intent, id);
        }
        renderPalette();
      });
```

- [ ] **Step 5: Confirm `toggleSelection` is still imported/used**

`toggleSelection` is still used by the palette handler (Step 4), so its import stays. Confirm no import became unused:

Run: `npm --prefix viewer/ run typecheck && npm --prefix viewer/ run lint`
Expected: no errors (in particular, no "unused import" for `toggleSelection`, `isSelected`, or `focusAwareHex`).

- [ ] **Step 6: Run the viewer unit suite (must stay green)**

Run: `npm --prefix viewer/ run test`
Expected: PASS — `selection.test.ts`, `highlight.test.ts`, and the rest. (editor.ts wiring has no direct unit; these confirm the pure surfaces are intact.)

- [ ] **Step 7: Rebuild the committed bundle**

Run: `npm --prefix viewer/ run build`
Expected: writes `src/hangarfit/_viewer_assets/viewer.js`. Confirm it changed:

Run: `git status --short src/hangarfit/_viewer_assets/viewer.js`
Expected: ` M src/hangarfit/_viewer_assets/viewer.js`

- [ ] **Step 8: Confirm no Python byte-identity regression**

Run: `pytest tests/test_viewer.py -q`
Expected: PASS — the `--edit` HTML bytes are unchanged (we added no `_HUD_EDIT` DOM), proving the palette-only scope held.

- [ ] **Step 9: Commit**

```bash
git add viewer/src/interaction/editor.ts src/hangarfit/_viewer_assets/viewer.js
git commit -m "feat(viewer): click = focus, not fleet toggle (#904)

Canvas click sets focusedId unconditionally (no membership toggle, so a plane's
priority/pin/cart-mode/door-rank are never dropped by focusing it). applyHighlight
now paints a distinct focus hue via focusAwareHex. The #910 palette checkbox stays
the single membership writer and no longer moves focus as a side effect. Rebuilt
viewer.js. No _HUD_EDIT change (test_viewer.py byte-identity stays green).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QrHcFVwhf2L3wNVQZffMMd"
```

---

### Task 3: Headless smoke, CHANGELOG, draft PR + review arc

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Headless render smoke (swiftshader)**

Generate an `--edit` viewer and screenshot it; the transform self-check must not banner.

```bash
hangarfit view tests/fixtures/scenario_minimal.yaml --solve --edit \
  -o /tmp/claude-1000/-home-pkuhn-hangarfit/46869df9-a28d-4a9d-9a1d-8960d3de0014/scratchpad/edit904.html
google-chrome --headless=new --use-gl=angle --use-angle=swiftshader \
  --enable-unsafe-swiftshader --virtual-time-budget=8000 \
  --screenshot=/tmp/claude-1000/-home-pkuhn-hangarfit/46869df9-a28d-4a9d-9a1d-8960d3de0014/scratchpad/edit904.png \
  "file:///tmp/claude-1000/-home-pkuhn-hangarfit/46869df9-a28d-4a9d-9a1d-8960d3de0014/scratchpad/edit904.html"
```

Expected: a screenshot renders (dbus/UPower + WebGL "ReadPixels stall" lines are noise); no on-page transform-mismatch banner. *(A manual pass in a real browser to eyeball the blue focus highlight + confirm clicking never drops constraints is recommended but not gating.)*

- [ ] **Step 2: Add the CHANGELOG entry**

In `CHANGELOG.md`, under `## [Unreleased]` → `### Changed` (create the `### Changed` subsection if absent, in the conventional Added/Changed/Fixed order), add:

```markdown
- Editor (`view --solve --edit`): a canvas click now **focuses** a plane for editing instead of toggling its fleet membership, so revising an already-selected plane no longer drops its priority/pin/cart-mode/door-rank. Fleet add/remove stays on the catalog palette checkboxes; the focused plane shows a distinct highlight. (#904)
```

- [ ] **Step 3: Commit the CHANGELOG**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): editor click-to-focus (#904)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QrHcFVwhf2L3wNVQZffMMd"
```

- [ ] **Step 4: Push and open the draft PR**

```bash
git push -u origin feature/904-editor-click-to-focus
```

Then create the PR (base `develop`, draft) with a body file (never inline a `gh` body — the git-guard hook). Write the body to a scratch file and:

```bash
gh pr create --draft --base develop \
  --title "feat(viewer): editor click-to-focus — decouple focus from fleet membership (#904)" \
  --body-file /tmp/claude-1000/-home-pkuhn-hangarfit/46869df9-a28d-4a9d-9a1d-8960d3de0014/scratchpad/pr904-body.md
```

Body must contain `Closes #904`, a summary of the decouple, the palette-only affordance decision (link the spec), the deferred "in fleet" mirror fast-follow, and the "no Python/`_HUD_EDIT` change → byte-identity green" note. Then set metadata via REST (`gh pr edit` is broken here):

```bash
gh api -X PATCH repos/DocGerd/hangarfit/pulls/<n> -f milestone= # (leave unmilestoned — epic #436, no v0.x milestone)
gh api -X POST repos/DocGerd/hangarfit/issues/<n>/labels -f 'labels[]=enhancement' -f 'labels[]=area:viewer'
```
*(Set `assignee=DocGerd` via the issues REST endpoint too.)*

- [ ] **Step 5: Run the review arc**

Dispatch the mandatory main pass plus the bundle guard against the PR diff (read-only, point at `origin/feature/904-editor-click-to-focus`):
- `pr-review-toolkit:code-reviewer` — main pass (mandatory).
- `scene-schema-guard` — the committed `viewer.js` is in its scope; confirm it flags **no** scene/contract/transform change (this PR touches neither `scene.py` nor `viewer.py` nor a `*-contract.ts`).
- *(Optional)* `pr-review-toolkit:pr-test-analyzer` — assess the `highlight.test.ts` truth-table coverage.

- [ ] **Step 6: Convert findings to inline review threads, fix, resolve**

One inline thread per finding on the diff line (batch via one `POST /pulls/<n>/reviews` with `comments[]` if ≥5). Anchor threads on the reviewed commit **before** pushing fixes (they auto-mark "outdated"). Fix each (commit + push), then reply + GraphQL `resolveReviewThread` on every thread. Re-run the review if changes were non-trivial.

- [ ] **Step 7: Flip the PR ready**

When the review arc is clean:

```bash
gh pr ready <n>
```

Tell the user the PR is **clean and ready for final review**. Do **not** merge (user is the sole merger).

---

## Self-Review

**1. Spec coverage:**
- §2 palette-only affordance → Task 2 Step 3 (focus-only click) + Task 2 Step 4 (palette stays the writer). ✓
- §3.1 pointerup focus-only, keep `hypot>6` + `if(!id)` guards, drop `renderReadout`/`renderPalette` → Task 2 Step 3. ✓
- §3.2 third focus visual + pure `focusAwareHex` helper → Task 1 + Task 2 Step 2. ✓
- §3.3 palette handler `focusedId` decouple → Task 2 Step 4. ✓
- §3.4 no Python/`_HUD_EDIT` change; `syncControls` gating untouched → asserted by Task 2 Step 8 (byte-identity) and by *not* editing `syncControls`. ✓
- §5 testing plan (pure unit, Python byte-identity, headless smoke, viewer.js rebuild) → Task 1 Steps 1–4, Task 2 Steps 6–8, Task 3 Step 1. ✓
- §6 deferred mirror → out of scope; noted in the PR body (Task 3 Step 4). ✓
- §7 review guards (scene-schema-guard on viewer.js, drift guard) → Task 2 Step 7 + Task 3 Step 5. ✓

**2. Placeholder scan:** every code step shows full code; commands have expected output. `<n>` is the PR number (only knowable after Step 4) — intentional, not a placeholder.

**3. Type consistency:** `focusAwareHex(selected, focused, orig)` is defined in Task 1 and called with `(isSelected(intent, t.id), t.id === focusedId, t.orig)` in Task 2 Step 2 — matches (booleans, number). `EXCLUDED_EMISSIVE`/`FOCUS_EMISSIVE` names consistent across Task 1 module + test.
