# hangarfit — Brand

The single source of truth for the **hangarfit** product brand. hangarfit is a
product brand derived from the umbrella **DocGerdSoft** design system; it inherits
that system's neutral core, delta mark, Geist type, 8-pt grid, status inks and
CVD-safety guarantees unchanged, and expresses itself through **exactly one
accent**: Horizon blue `#0079B5` (dark-lifted `#3FA3D6`).

This file folds in the existing 2D expression (matplotlib top-down render, marks,
banner) and adds the **3D / dark-surface expression** for the offline Three.js
viewer (`hangarfit view`). Issue #414 (this document) defines the brand; issue
#415 applies it to `viewer.js` / `viewer.py`. Everything here is **render-only** —
no change to the `scene/v1` contract, the Python-owned determinant-−1 transform,
determinism, or the collision model.

- **Parent system:** `DocGerd/brand` → `color/tokens.css`, `type/typography.css`,
  `docs/brand-guidelines.md`.
- **2D tokens & code:** `src/hangarfit/visualize.py` (top brand-palette block).
- **3D surface:** `src/hangarfit/_viewer_assets/viewer.js`,
  `src/hangarfit/viewer.py` (`_CSS` / `_HUD`); schema `docs/architecture/scene-v1-schema.md`; rationale ADR-0017.
- **Marks:** `docs/assets/{banner,mark,monogram,avatar,favicon}.svg`.

---

## 1 · Lineage — what hangarfit inherits from DocGerdSoft

The governing rule of the parent system: **one neutral core (fixed) + one wordmark
+ one grid; each product expresses itself through exactly one accent.** hangarfit
follows it — only the accent rotates. Inherited verbatim:

- **Personality.** "An instrument, not a poster." Calm, exact, Swiss-restraint.
  One accent of *weight*, not colour. Machine output is always mono. Text measure
  ≤ 70ch. No ®/™, no mascot, never imply a company.
- **Type.** Geist (UI & text) + Geist Mono (machine output: code, ids, paths,
  metrics, timestamps, kickers). Geist has **no italics** — emphasise with weight
  (500/600), never slant or colour. Mono data: `tnum` + slashed `zero`, no
  ligatures.
- **Neutral core (dark mode).** `--paper #0D0E10` · `--surface #15171A` ·
  `--mist #1B1E22` · `--mist-2 #141619` · `--hairline #2A2E33` ·
  `--hairline-2 #202428` · `--graphite #969CA4` · `--graphite-strong #C2C7CD` ·
  `--ink #ECEEF1`.
- **Spacing (8-pt):** 4 · 8 · 12 · 16 · 24 · 32 · 48 · 64 · 96 · 128.
  **Radius:** 3 / 6 / 10 / 16 / pill. **Focus ring:** the product accent.
- **The "never hue alone" guarantee.** Identity and status must never rest on
  colour alone — always a second, non-colour signal (ink outline, label, hatch,
  dashed stroke). This is load-bearing for CVD-safety and B&W printouts.
- **Status-ink meanings (fixed across surfaces).** `valid #0F7C72` ·
  `conflict #C8442C` · `maint #7B63A3` · `wall·door·datum #3B4046`.

### Accent — "Horizon" blue (the only hangarfit-specific colour)

The signature DocGerdSoft accent is steel azure `#2C6CB0`. hangarfit rotates the
hue a few degrees cooler — toward a clean flight horizon — at the same family
lightness/chroma.

| Token | Light | Dark | Contrast | Role |
|---|---|---|---|---|
| `--accent` | `#0079B5` | `#3FA3D6` | 4.77:1 on white · 6.7:1 on `#0D0E10` | Links, focus, primary action, mark, `fit` |
| `--accent-strong` | `#005E94` | `#6BBCE2` | 6.93:1 on white | Hover / pressed |
| `--accent-tint` | `#E4F1FA` | `#10242F` | — | Wash, selected, badges |

> Evaluated & rejected: Sky `#128BBC` (3.86:1 — fails AA text), Deep `#295CA5`
> (pulls back toward the signature azure). **Pick = `#0079B5`** (dark `#3FA3D6`).

---

## 2 · 2D expression (matplotlib top-down) — folded in

The authoritative 2D token block, lifted verbatim into
`src/hangarfit/visualize.py`. The categorical set is **Okabe–Ito-derived** and
tuned so every fill also clears **≥ 3:1 on white** (WCAG non-text). Every plane
also carries the `_INK_EDGE` (`#14161A`) outline and a mono id; conflicts add a
hatch + dashed edge — never colour alone.

### `PLANES` — one colour per aircraft (light figures, on white)

| # | Name | Hex | on white |
|---|---|---|---|
| 01 | Horizon | `#0079B5` | 4.77 |
| 02 | Vermillion | `#D55E00` | 3.87 |
| 03 | Sea green | `#009E73` | 3.42 |
| 04 | Orchid | `#B45CA6` | 4.18 |
| 05 | Amber | `#B37903` | 3.71 |
| 06 | Cyan | `#108FAA` | 3.80 |
| 07 | Indigo | `#4C4C9E` | 7.41 |
| 08 | Sienna | `#8A542D` | 6.20 |
| 09 | Graphite | `#5E646B` | 5.98 |

### `STATUS` & structure

| Role | Hex | Notes |
|---|---|---|
| valid | `#0F7C72` | 5.06:1 |
| conflict | `#C8442C` | 4.86:1 — always pair with hatch + ink edge |
| maint | `#7B63A3` | 5.05:1 — maintenance-bay identity fill |
| wall · door · datum | `#3B4046` | 10.5:1 — structure ink (`_HANGAR_EDGE`) |
| ink edge | `#14161A` | `_INK_EDGE` — the "never hue alone" outline |

### `PLANES_DARK` — lifted fills (dark figures, on `#0D0E10`)

Same ordering/identity as `PLANES`; each row is the dark-surface expression of the
same hue. Contrast measured on `#0D0E10`:

| # | Hex | on `#0D0E10` |
|---|---|---|
| 01 | `#3FA3D6` | 6.7 |
| 02 | `#E8794A` | 6.6 |
| 03 | `#33B894` | 7.7 |
| 04 | `#CE7EC0` | 6.7 |
| 05 | `#D29A2E` | 7.7 |
| 06 | `#3FB6CE` | 8.0 |
| 07 | `#8585C9` | 5.6 |
| 08 | `#BC8154` | 5.8 |
| 09 | `#9AA0A8` | 7.2 |

All clear WCAG AA (≥ 4.5:1) on the dark paper.

---

## 3 · 3D / dark-surface expression (the `hangarfit view` viewer)

The 3D viewer is a **dark interactive scene** that exists to show the *vertical*
clearances the 2D plan view can't — a high wing's shadow falling across a
neighbour's tail (ADR-0017). The brand job here is to put that scene fully on the
DocGerdSoft system: a `#0D0E10` room, neutral honest light so the CVD palette
renders true, the plane fleet in `PLANES_DARK`, status inks unchanged, and HUD
chrome built from the dark neutral core.

**Three principles for the dark surface:**

1. **Honest light.** Sky, hemisphere and key lights stay neutral white so a
   plane's brand hue is the hue you see — no warm/cool cast that would erode the
   CVD separation the palette was tuned for. The *only* coloured light is a faint
   fill tinted from the horizon accent.
2. **One token per role, across surfaces.** A wall is `#3B4046` in the PNG *and*
   in the scene. The maintenance bay is `maint` violet in both. Conflict is
   `#C8442C` in both. The viewer stops inventing ad-hoc slates and reds.
3. **Never hue alone — in 3D too.** A 3D box can't hatch, so the redundant
   non-colour signal moves to the billboard **label**: a conflicted plane's id
   chip turns to the conflict ink *and* gains a `⚠ conflict` suffix, so the state
   reads without colour.

### Conflict redundancy in 3D

2D pairs the conflict fill with a hatch + dashed edge. 3D has no hatch, so the
non-colour cue is text: append `⚠ conflict` to the plane's label (already a
`CanvasTexture` drawn with safe `fillText`) and flip the chip background to
`#C8442C`. The plane fill also goes to the conflict ink. This keeps the
"never hue alone" guarantee on the dark surface.

---

## 4 · Token table — drop straight into `viewer.js` / `viewer.py`

Every value below is render-only. "Constant" names the exact `viewer.js` /
`viewer.py` symbol to change. Hex shown as both `#RRGGBB` and the `0xRRGGBB` the
JS uses.

### Scene shell — `viewer.js`

| Element | Recommended | Constant (where) | Currently | Rationale |
|---|---|---|---|---|
| Background | `#0D0E10` *(keep)* | `scene.background = 0x0d0e10` | `0x0d0e10` | Brand `--paper` dark. Already correct. |
| Floor | `#15171A` | `floor` material `color` `0x16181c` → `0x15171a` | `0x16181c` | Brand `--surface` dark — the raised plane the fleet sits on. Keep `roughness:1` (matte, catches shadow). |
| Grid major | `#3B4046` *(keep)* | `GridHelper(…, 0x3b4046, …)` | `0x3b4046` | STATUS `wall` ink — structural majors match the 2D wall colour. |
| Grid minor | `#202428` | `GridHelper(…, …, 0x23262b)` → `0x202428` | `0x23262b` | Brand `--hairline-2` dark — the faintest structural line. |
| Walls (+opacity) | `#3B4046` @ **0.20** | `addWall` material `color 0x4b5560` → `0x3b4046`, `opacity 0.16` → `0.20` | `0x4b5560` @ 0.16 | Unify on STATUS `wall` so "wall = `#3B4046`" in 2D **and** 3D; opacity nudged up so the darker pane still catches the key light. |
| Maintenance bay (+opacity) | `#7B63A3` @ **0.32** | bay material `color 0x922b21` → `0x7b63a3`, `opacity 0.34` → `0.32` | `0x922b21` @ 0.34 | Brand STATUS `maint` violet. Removes the off-system red that collided with the conflict red; CVD-safe; ties the bay to one token across surfaces. |
| Datum / door edge | `#3B4046`; threshold marker `#969CA4` if drawn | (no mesh today) | — | STATUS `wall·door·datum` ink. If a door threshold line is added, lift to `--graphite` dark so the opening reads as "open," mirroring the 2D `_DOOR_EDGE`. |

### Lights — `viewer.js`

| Element | Recommended | Constant | Currently | Rationale |
|---|---|---|---|---|
| Hemisphere | sky `#FFFFFF` / ground `#202428` @ **0.85** *(keep)* | `HemisphereLight(0xffffff, 0x202428, 0.85)` | same | Neutral white sky keeps hues honest; ground bounce = `--hairline-2` dark (already on-brand). |
| Sun (key) | `#FFFFFF` @ **1.0** *(keep)* | `DirectionalLight(0xffffff, 1.0)` | same | Neutral white key so the CVD palette renders true; casts the contact shadows the viewer exists for. |
| Fill | `#CFE3F2` @ **0.3** | `DirectionalLight(0xcfe0ff, 0.3)` → `0xcfe3f2` | `0xcfe0ff` @ 0.3 | The scene's *only* coloured light = a pale tint of the horizon accent (`#3FA3D6`), so even the soft fill belongs to the brand hue. |

### Plane materials — `viewer.js`

| Element | Recommended | Constant | Currently | Rationale |
|---|---|---|---|---|
| Plane fill | **`PLANES_DARK`** (by sorted-id index) | `p.color` in the scene JSON — emit `PLANES_DARK[i]` from `scene.py` (`_color_map`) instead of `PLANES[i]` | `PLANES` (light) | Dark-lifted CVD-safe fills (5.6–8.0:1 on `#0D0E10`). Same index → same plane: **identity parity with 2D is preserved, no hue reassignment.** |
| Emissive policy | body **none**; nose cone `colour × 0.25` (cap 0.30) | `boxMaterial` (no emissive) / `addLabelAndNose` nose `emissive` | nose ×0.25 | Lit fills stay honest so CVD separation holds under the key light; reserve a faint self-emit for the nose wayfinding cone only. |
| Wing | `PLANES_DARK` colour, opacity **0.5** *(keep)* | `boxMaterial` `kind==='wing'` | 0.5 | Translucent so vertical stacking reads — the 3D analogue of 2D `_WING_ALPHA` (0.4). Matte body `roughness 0.7`. |
| Strut | `PLANES_DARK` colour, `roughness 0.35` / `metalness 0.85` *(keep)* | `boxMaterial` `kind==='strut'` | same | Metallic so struts read as gear, distinct from the matte body. |
| Cockpit (`fuselage_front`) | `colour × 0.55` (linear) *(keep)* | `boxMaterial` `multiplyScalar(0.55)` | ×0.55 | Darker cockpit tint = the 3D analogue of 2D `_FUSELAGE_FRONT_DARKEN` (0.62 sRGB) — same *intent* (a darker cockpit), applied in **linear** space, **not** a perceptual sRGB match (see the `boxMaterial` note in `viewer.js`). |
| Wheels | `#566573` *(keep)* | `WHEEL_COLOR = 0x566573` (= `visualize._WHEEL_COLOR`) | same | Gear slate, deliberately offset from the graphite fleet fill (`PLANES[8]` `#5E646B` in 2D / `PLANES_DARK[8]` `#9AA0A8` in the 3D scene) so gear never reads as plane-09. Tokenise as **Gear slate**. |
| Cart decks | `#AAB7B8` *(keep)* | `CART_DECK_COLOR = 0xaab7b8` (= `visualize._CART_DECK_COLOR`) | same | Lighter neutral so the dolly pallet separates from the darker wheel disc. Tokenise as **Cart deck**. |
| Conflict | `#C8442C` **+ label cue** | `CONFLICT = 0xc8442c`; add `⚠ conflict` in `makeLabel` / chip bg `#C8442C` | `0xc8442c` (colour only) | STATUS `conflict` ink. The label suffix supplies the non-colour redundancy 3D can't hatch — honours "never hue alone." |
| Nose arrow | plane `PLANES_DARK` colour, `emissive ×0.25` *(keep)* | `addLabelAndNose` nose material | plane hue | The wayfinding tip carries the plane's own identity hue + a faint self-emit. |

### Labels & HUD — `viewer.js` (`makeLabel`) and `viewer.py` (`_CSS`)

| Element | Recommended | Constant | Currently | Rationale |
|---|---|---|---|---|
| Label text | `#ECEEF1` | `makeLabel` `fillStyle '#e8eaed'` → `'#ECEEF1'` | `#e8eaed` | Brand `--ink` dark. |
| Label chip bg | `rgba(21,23,26,0.86)` | `makeLabel` `fillStyle 'rgba(18,20,24,0.82)'` | `rgba(18,20,24,.82)` | Brand `--surface` dark @ 0.86 — the one **HUD glass**, shared with the HUD bar. |
| Label / id font | Geist Mono → `ui-monospace` offline | `makeLabel` `ctx.font … 'system-ui'` → mono stack | system-ui | Plane ids are machine output → mono per brand; a mono fallback stack keeps it offline. |
| HUD bar | `rgba(21,23,26,0.86)` + 1px top `#2A2E33` | `_CSS` `#hud{background:rgba(18,20,24,.86)}` | `rgba(18,20,24,.86)` | Brand surface glass; hairline top edge = `--hairline` dark. |
| HUD buttons | bg `#1B1E22` / border `#2A2E33` / text `#ECEEF1`; focus ring `#3FA3D6` | `_CSS` `#hud button` (`#2a2d33` / `#3b4046`) | `#2a2d33` / `#3b4046` | Brand `--mist` / `--hairline` / `--ink` dark; accent focus ring = brand focus. |
| Honesty / placeholder banner | bg `#D6A23E`, text `#14161A`, **700** | `_CSS` `#placeholder{background:#b00020}` | `#b00020` | Brand `--warning` (amber): semantically *"illustrative data,"* not a failure — and visibly distinct from the red error & red conflict. Keep the 2D PNG banner in parity (see §6). |
| Note / error banner | bg `#BC4438`, text `#FFFFFF`, **600** | `_CSS` `#banner{background:#7a1f1f}` | `#7a1f1f` | Brand `--danger`: a real *"do not trust this render"* failure (WebGL/transform-check). One on-system red, kept separate from the conflict ink. |
| Readouts text | `#C2C7CD`, mono, tabular | `_CSS` `#readouts{color:#aeb6c2}` | `#aeb6c2` | Brand `--graphite-strong` dark — readable but clearly secondary to white ink; mono-data figures. |
| Legend chip text / dot | text `#ECEEF1`; dot = plane `PLANES_DARK` colour | `legend` `.sw` / `.sw i` | inherits | Body ink; dot carries the plane's dark fill (matches the scene). |
| Body / HUD text colour | `#ECEEF1` | `_CSS` `body{color:#e8eaed}` → `#ECEEF1` | `#e8eaed` | Brand `--ink` dark. |

---

## 5 · Typography treatment — banner / HUD / labels

Consistent with the hangarfit marks (`docs/assets/banner.svg`): Geist 600 wordmark,
tracking −0.03em, all-lowercase to match the CLI command, **`fit` in the accent**;
machine output in Geist Mono.

- **Viewer wordmark** (title bar / about): `hangar` in `--ink`, `fit` in
  `#3FA3D6`, Geist 600, −0.03em. The mark is the system delta in `#3FA3D6`.
- **HUD bar.** Geist (`--sans`), 13px / 500 for button labels; offline fallback is
  `system-ui` (Geist isn't vendored into the offline file — adopt the brand
  `--sans` *stack* so Geist is used wherever present, else falls back at no cost).
- **Machine output** — the clock (`0.0s`), readouts (`gap … · wing-over-tail …`),
  `towing: <id>`, plane ids in labels and the legend — is **Geist Mono**, tabular
  figures, slashed zero. Offline fallback `ui-monospace, "SF Mono", Menlo, monospace`.
- **Label sprites.** Plane id in mono, `#ECEEF1` on the `rgba(21,23,26,0.86)` glass
  chip; conflicted state appends `⚠ conflict` and flips the chip to `#C8442C`.
- **Kickers / section eyebrows** (in any branded chrome): mono, uppercase, 0.16em
  tracking, `--graphite` dark.

---

## 6 · Cross-surface parity follow-ups (2D PNG — outside the 3D scope)

These keep the *single source of truth* honest but touch `visualize.py`, not the
viewer. Render-only, no test/contract impact beyond a deliberate value change;
tracked as the sibling issue #418 (deliberately **not** folded into #415, which is 3D-only):

- **Maintenance bay → `maint` violet.** The 2D closed-bay currently draws
  `_BAY_WALL_FACE #922b21` / `_BAY_WALL_EDGE #641e16` (a second red that the code
  itself notes must be "kept distinct" from the conflict red). Align it to STATUS
  `maint #7B63A3` (fill) with an ink edge + retained hatch, matching the 3D bay and
  removing the two-reds problem.
- **Placeholder banner → `warning` amber.** The 2D `_draw_placeholder_banner` uses
  `#b00020`; move it to `--warning #D6A23E` with `#14161A` text so the
  "illustrative data" caution matches the viewer and is distinct from true errors.
- **Net effect:** the system's reds resolve to three *distinct, on-token* signals —
  **conflict** `#C8442C` (a collision), **danger/error** `#BC4438` (a render
  failure), **warning** `#D6A23E` (illustrative data) — plus **maint** violet
  `#7B63A3` for the bay. No more ad-hoc `#922b21` / `#b00020` / `#7a1f1f` reds.

---

## 7 · Assets — new / changed

| File | Status | Note |
|---|---|---|
| `docs/assets/BRAND.md` | **new** | This file — hangarfit's single brand source of truth (lineage + 2D + 3D). |
| `src/hangarfit/_viewer_assets/viewer.js` | change (#415) | Apply the §4 scene/light/material/label values. Render-only. |
| `src/hangarfit/viewer.py` | change (#415) | Apply the §4 `_CSS` HUD/banner/readout values. |
| `src/hangarfit/scene.py` | change (#415) | `_color_map` emits `PLANES_DARK[i]` for `p.color` (same index → same plane). Does **not** alter `scene/v1` shape. |
| `src/hangarfit/visualize.py` | follow-up (§6) | Optional 2D parity: bay → `maint` violet, placeholder banner → `warning` amber. |
| `docs/assets/hero-3d.png` | optional (new) | Rendered dark-scene hero for the README, alongside `banner.svg`. |
| `docs/assets/{banner,mark,monogram,avatar,favicon}.svg` | unchanged | Marks inherited as-is; the viewer wordmark uses the same delta + Geist treatment. |

---

*© 2026 Patrick Kuhn · Apache-2.0 · DocGerdSoft is a personal brand, not a company.
No ®/™. Built on the DocGerdSoft design system.*
