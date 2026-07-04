# #904 — Editor: click-to-focus interaction (decouple focus from fleet membership)

**Date:** 2026-07-04
**Issue:** #904 (fast-follow to the v0.18.0 interactive placement editor, #442)
**Epic:** #436 (interactive plane-placement editor)
**Status:** design approved; implementation pending
**Scope:** pure `viewer/src/interaction/*` + a `viewer.js` rebuild. **No** Python / `_HUD_EDIT` / scene / loader change.

---

## 1. Problem

In the shipped `hangarfit view --solve --edit` editor a canvas click **toggles** a plane's fleet
membership, and `focusedId` (the plane the priority / pin / on-carts / cart-mode / ＋rank controls act
on) is bound to selection:

```ts
// viewer/src/interaction/editor.ts — pointerup handler (current)
intent = toggleSelection(intent, id);
focusedId = isSelected(intent, id) ? id : null;
```

`toggleSelection` (`selection.ts`) **deletes** the plane's `priorities` / `mustPositions` /
`cartModeOverrides` and drops its `doorOrder` rank on deselect. So revising an **already-selected**
plane requires: click it (→ deselects, dropping its constraints) → click again (→ re-adds + focuses).
The gesture is awkward and transiently destructive because *focus* and *fleet membership* are the same
click.

## 2. Decision — affordance: **palette-only** (click = focus; membership stays on the #910 palette)

The fix decouples the two concerns:

- **Canvas click = focus** a plane for editing, unconditionally, with **no state mutation** (focus any
  plane whether or not it is in the fleet; never drop constraints).
- **Fleet add/remove** stays on the existing **#910 catalog palette** checkboxes — the single
  membership writer.

This affordance was chosen by a four-lens judge panel (UX / discoverability, code architecture,
forward-compat with #911, testability / accessibility) over two alternatives — a focused-panel
"in fleet" toggle and a canvas modifier-click.

| Lens | Focused-panel toggle | **Palette-only** | Modifier-click |
|---|---|---|---|
| UX & discoverability | 5 | 4 | 2 |
| Code architecture | 3 | **5** | 2 |
| Forward-compat #911 | 5 | 4 | 2 |
| Testability / accessibility | 4 | **5** | 2 |
| **Sum** | 17 | **18** | 8 |

**Why palette-only wins:**

1. **The bug is already fixed by the decouple alone.** `syncControls` already gates priority / pin /
   carts / cart-mode on `active = isSelected(intent, focusedId)`, *not* on focus. So once click merely
   sets `focusedId = id`, a focused-but-non-member plane already shows correctly-disabled controls with
   **zero new membership control**. No "in fleet" widget is required to make the decouple correct.
2. **Net deletion, one writer.** `renderPalette` already renders a checkbox per catalog aircraft that
   calls `toggleSelection`, fed by `build_editor_context.catalog` which already emits **every** fleet
   aircraft. Palette-only keeps exactly one membership writer and needs **no `_HUD_EDIT` change**, so no
   `tests/test_viewer.py` byte-identity churn. A focused-panel toggle would add a second `toggleSelection`
   call site plus an ongoing `syncControls`↔`renderPalette` cross-refresh drift surface — for a
   discoverability nicety.
3. **Testability.** The `pick()` raycaster path (`getBoundingClientRect` + camera projection + raycast)
   has **zero** test precedent (`grep viewer/test` for `pointerup|raycast|dispatchEvent` is empty) and is
   the flaky surface. Palette-only keeps membership on the already-unit-tested `toggleSelection` /
   checkbox-`change` path and is touch-native (relevant to the stated Android UI direction).
4. **`modifier_click` is rejected** (unanimous 2/5): a stationary Shift/Alt-click and a #911
   TransformControls gizmo pointerdown occupy the same canvas pixels over the focused plane — the 6 px
   stationary/drag guard cannot disambiguate them — and modifier keys do not exist on touch.

**ADR-0002 / ADR-0029 invariant held.** All of focus / membership / priority / pin are pure scalar
state over Python-emitted, id-keyed values (`currentPoses`, `catalog`, `selectedPlaneIds`). `editor.ts`
is barred from importing `affine.ts` / `anchors.ts`; `pick()` only reads already-built plane Groups.
Nothing recomputes or inverts the determinant-−1 plane-local→world transform.

## 3. Design

### 3.1 `pointerup` handler → focus-only (`editor.ts` ~101–116)

Replace the toggle body with a pure focus set:

```ts
if (Math.hypot(ev.clientX - downX, ev.clientY - downY) > 6) return; // a drag, not a click — KEEP
const id = pick(ev);
if (!id) return;                 // clicking empty space leaves focus as-is — KEEP
focusedId = id;                  // focus any picked plane, member or not; no state mutation
applyHighlight();                // now also paints the focus highlight (§3.2)
syncControls();                  // control enablement reflects the newly focused plane
renderDoorOrder();               // ＋rank button enablement reads focus + membership
```

- **Drop** `intent = toggleSelection(...)` and the `isSelected(...)`-bound focus assignment.
- **Drop** `renderReadout()` and `renderPalette()` from this path — `selectedPlaneIds` did not change,
  so the readout text and palette checkboxes are unchanged.
- **Keep** the `hypot > 6` stationary-click guard (the click-vs-drag discriminator #911 relies on) and
  the `if (!id) return` empty-space guard.

### 3.2 Focus visual — a third state distinct from membership (`applyHighlight`, `editor.ts` ~118–123)

Today the emissive **hue** encodes membership only:

```ts
t.mat.emissive.setHex(isSelected(intent, t.id) ? t.orig : 0x552200); // member=orig, excluded=amber
```

Once click-to-focus can land on a **non-member**, "the amber-vs-original plane is the one I'm editing"
no longer holds — focus needs its own visual. Extend `applyHighlight` to a **three-way precedence**:

```
focused          → a distinct focus highlight hue (a brand accent, clearly NOT the amber excluded hex)
else member      → t.orig
else (excluded)  → 0x552200 (amber, unchanged)
```

- Primary approach: reserve a focus emissive hue that takes visual precedence over the membership hue.
  Rationale for precedence over a strictly-orthogonal channel: many plane materials ship with a
  near-black base emissive, so a pure `emissiveIntensity` bump would be invisible on a focused *member*.
  A reserved hue is robustly visible and keeps the diff to one function.
- Losing the amber cue *while a plane is focused* is acceptable: only one plane is focused at a time, and
  its membership stays legible via the palette checkbox state + the panel control enablement.
- **Alternative** if the manual test shows the precedence hue reads ambiguously: an outline / edges ring
  on the focused plane (orthogonal to emissive). Deferred unless needed — it adds Object3D lifecycle
  (creation + disposal on re-focus / dispose) the reserved-hue approach avoids.
- **Pure decision helper for unit test:** extract the color choice into a pure function, e.g.
  `focusAwareHex(isSelected: boolean, isFocused: boolean, orig: number): number`, and drive
  `applyHighlight` through it. This gives a deterministic `viewer/test` unit for the tri-state logic
  **without** touching the untested raycaster path — matching the codebase's pure-state-transition test
  culture (`viewer/test/selection.test.ts`).

The focus highlight doubles as **#911's manipulation-target indicator**: the TransformControls gizmo
mounts on `focusedId`'s Group, so the focus highlight and the gizmo target are the same object.

### 3.3 Decouple the palette handler (`editor.ts` ~367–379)

The palette aircraft-checkbox `change` handler currently moves focus as a side effect of a membership
toggle:

```ts
intent = toggleSelection(intent, id);
focusedId = isSelected(intent, id) ? id : focusedId === id ? null : focusedId; // ← remove
```

Under the decoupled model, toggling membership from the list must **not** move focus (focus is the
canvas click's job; moving it would yank a future gizmo target). **Remove** the `focusedId` mutation
line; keep the `toggleSelection` write and the follow-up `applyHighlight()` / `syncControls()` /
`renderDoorOrder()` / `renderPalette()` refreshes (membership changed, so the highlight hue, the focused
plane's control enablement if it was the toggled one, the door-order ranking, and the palette all still
need to reflect it). Result: **canvas = focus, palette = membership, fully orthogonal.**

### 3.4 What explicitly does NOT change

- **No Python / `_HUD_EDIT` / scene / loader / schema change.** The `#palette` + `#palette-list` DOM and
  the `catalog` editor-context field already exist. `tests/test_viewer.py` byte-identity asserts on the
  edit HTML must still pass unchanged (a positive regression check).
- **`syncControls` gating is untouched** — `active = isSelected(intent, id)` already disables
  priority / pin / carts / cart-mode for a focused-but-non-member plane. The decouple is already wired.
- **The `initialIntent` "whole fleet selected" seed is untouched.** An untouched editor still exports the
  same bytes (ADR-0002 byte-path invariant).

## 4. Forward-compatibility with #911 (drag-to-fix)

This design is the enabling half of #911:

1. The canvas now has exactly **one** click meaning (focus) and one drag meaning (orbit / gizmo). The
   `hypot > 6` guard already distinguishes a click from a drag; TransformControls handle
   pointerdown/up are either > 6 px moves or consumed by the gizmo, and its `dragging-changed` disables
   OrbitControls — no contention.
2. The gizmo attaches to `focusedId`'s Group — a **stable, membership-independent** target (you can pose
   a plane before it is even in the fleet, matching #911's "drag → pin intent"). The §3.2 focus highlight
   is exactly that target's affordance.
3. Because membership lives **off-canvas** in the palette (not a modifier gesture on the same pixels),
   #911's on-canvas drag can never be confused with a membership toggle — the specific interaction
   collision that rejected `modifier_click`.
4. #911's drag-end → `serve` pin intent reuses the existing `mustPositions` / `pinAtCurrent` seam keyed by
   `focusedId`. #904 need not build it, but leaves `focusedId` as the clean handle.

## 5. Testing plan

- **Pure unit (`viewer/test`):** `focusAwareHex` tri-state truth table (focused / member / excluded ×
  representative `orig`). Deterministic, no raycaster, no WebGL.
- **Python (`tests/test_viewer.py`):** unchanged — assert the `--edit` HTML bytes are byte-identical (no
  `_HUD_EDIT` change). This is the regression guard that palette-only added no HUD DOM.
- **Headless smoke (swiftshader):** the existing `checkAnchors` banner-hidden render still passes (no
  scene change). Optionally dispatch a `pointerup` to focus a plane and assert no throw; kept light
  because `pick()` is the untested/flaky surface.
- **Manual test:** `hangarfit view <scenario> --solve --edit -o edit.html`; open; verify (a) clicking a
  plane focuses it (focus highlight appears) and never drops its priority/pin, (b) clicking an
  already-selected plane no longer deselects it, (c) the palette checkbox includes/excludes a plane
  without moving focus, (d) focusing an excluded (amber) plane shows disabled priority/pin.
- **`viewer.js` rebuild** committed in the same change (the `viewer-build-drift` CI guard enforces it).

## 6. Scope / non-goals

- **Deferred fast-follow (not in #904):** the panel's "in fleet" **mirror** checkbox on the focused-plane
  panel (a second control that routes through the *same* `toggleSelection` writer to co-locate membership
  with the other focused-plane controls). Fully additive and reversible — pull it forward only if real
  editor usage shows off-canvas membership for a visible plane actually hurts. Deferring costs nothing
  because both controls read the same `intent.selectedPlaneIds`.
- **Not #904:** #911 drag-to-fix, #912 mover pin, any `serve` round-trip, any Python/schema change.

## 7. Review guards this touches

- `scene-schema-guard` — only if `viewer.py` changes; it does **not** here (verify at PR time).
- The `viewer/src/*.ts` → `viewer.js` rebuild reminder (PostToolUse hook) + the `viewer-build-drift`
  CI guard.
