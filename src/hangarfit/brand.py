"""The single machine-readable source of every hangarfit brand token.

This module is the **one** place a brand colour, opacity, darken factor or font
stack is *defined*; every render surface — the 2D matplotlib renderer
(:mod:`hangarfit.visualize`), the scene builder (:mod:`hangarfit.scene`), the
HTML/CSS assembler (:mod:`hangarfit.viewer`) and the offline Three.js viewer
(``_viewer_assets/viewer.js``) — *references* it so a value can never drift
between surfaces (#419). Before this module the tokens were hand-copied across
all four files; the human source of truth, ``docs/assets/BRAND.md``, is now
mirrored by exactly one piece of *code*.

The categorical plane palette is **Okabe–Ito-derived and CVD-safe** (#326,
https://jfly.uni-koeln.de/color/): distinguishable under deuteranopia,
protanopia and tritanopia and legible in grey-scale. Identity must never rest on
hue alone — every surface pairs colour with a non-colour cue (ink outline, mono
id, hatch + dashed edge in 2D; a ``⚠ conflict`` label suffix in 3D). The dark
``PLANES_DARK`` row is the dark-surface expression of the same hues, same index →
same plane, so 2D and 3D keep one plane→colour identity.

**Render-only / determinism-neutral.** Nothing here touches the collision model,
the determinant-−1 transform, or the ``scene/v1`` contract; changing a value
re-tints a render but never alters geometry or solver/planner output. Values
match ``docs/assets/BRAND.md`` (esp. §2 and §4) byte-for-byte.

The 3D viewer tokens that historically lived only as ``0xRRGGBB`` literals inside
``viewer.js`` now live here too and are injected into the HTML as a canonical
JSON ``BRAND`` blob (see :func:`hangarfit.viewer.render_viewer`); ``viewer.js``
reads them at runtime rather than hard-coding them. 3D colours are kept as
``#RRGGBB`` strings here and passed to ``new THREE.Color(str)`` in the viewer —
no parsing library, fully offline.
"""

from __future__ import annotations

# ── 2D matplotlib expression (BRAND.md §2) ──────────────────────────────────
# PLANES — one colour per aircraft (light figures, on white), ordered for max
# pairwise CVD separation. Each fill also clears ≥3:1 on white (WCAG non-text).
PLANES: list[str] = [
    "#0079B5",  # 01 Horizon (the hangarfit accent)
    "#D55E00",  # 02 Vermillion
    "#009E73",  # 03 Sea green
    "#B45CA6",  # 04 Orchid
    "#B37903",  # 05 Amber
    "#108FAA",  # 06 Cyan
    "#4C4C9E",  # 07 Indigo
    "#8A542D",  # 08 Sienna
    "#5E646B",  # 09 Graphite
]

# PLANES_DARK — lifted fills for the dark-figure variant (drawn at ~92% opacity
# on #0D0E10). Same ordering/identity as PLANES; this is the 3D scene's fleet.
PLANES_DARK: list[str] = [
    "#3FA3D6",
    "#E8794A",
    "#33B894",
    "#CE7EC0",
    "#D29A2E",
    "#3FB6CE",
    "#8585C9",
    "#BC8154",
    "#9AA0A8",
]

# STATUS — status & structure inks (fixed across surfaces). Key is "wall" per the
# authoritative README drop-in: wall/door/datum all map to the one
# graphite-strong ink #3B4046.
STATUS: dict[str, str] = {
    "valid": "#0F7C72",  # 5.06:1 on white
    "conflict": "#C8442C",  # 4.86:1 — always pair with a hatch + ink edge
    "maint": "#7B63A3",  # 5.05:1 — maintenance-bay fill
    "wall": "#3B4046",  # 10.5:1 — wall · door · datum ink
}

# Plane / part outline ink — the "never hue alone" guarantee: every plane part is
# stroked in this ink so the silhouette reads without colour. Distinct from the
# wall ink (STATUS["wall"]).
INK_EDGE: str = "#14161A"

# Unreachable-by-default fallback when a placement can't be mapped to a PLANES
# index; gray so a stray placement reads as "uncoloured", not a real plane hue.
FALLBACK_COLOR: str = "#95a5a6"  # gray

# Conflict ink, sourced from STATUS (single source for the conflict red).
CONFLICT_COLOR: str = STATUS["conflict"]  # "#C8442C"

# Tow-path overlay palette (#192): Okabe–Ito 8-colour CVD-safe set. One colour
# per plane, cycled by sorted plane_id; deliberately excludes the conflict colour
# and the gray fallback so a path never blurs into a conflict highlight.
TOW_PATH_COLORS: tuple[str, ...] = (
    "#000000",  # black
    "#e69f00",  # orange
    "#56b4e9",  # sky blue
    "#009e73",  # bluish green
    "#f0e442",  # yellow
    "#0072b2",  # blue
    "#d55e00",  # vermillion
    "#cc79a7",  # reddish purple
)
TOW_PATH_LINEWIDTH: float = 1.6

# 2D alphas / darkens (the matplotlib render's translucency budget).
FUSELAGE_ALPHA: float = 0.9  # near-opaque: two overlapping fuselages = conflict.
FUSELAGE_FRONT_DARKEN: float = 0.62  # cockpit tint = ×0.62 per RGB channel (sRGB).
WING_ALPHA: float = 0.4  # translucent so stacked wings show their plan overlap.
CART_DECK_ALPHA: float = 0.85  # cart/dolly pallet squares.

# Closed maintenance-bay "wall" style (2D) — saturated red + slashed hatch, kept
# visually distinct from the conflict red so the two reds don't blur in one image.
BAY_WALL_FACE: str = "#922b21"
BAY_WALL_EDGE: str = "#641e16"
BAY_WALL_ALPHA: float = 0.55
BAY_WALL_HATCH: str = "///"
BAY_LABEL_COLOR: str = "#ffffff"

# Hangar / door inks (2D). The hangar edge folds wall/door/datum onto STATUS
# "wall"; the door is lightened (DOOR_EDGE) so the opening reads as "open".
HANGAR_EDGE: str = STATUS["wall"]  # "#3B4046"
DOOR_EDGE: str = "#bdc3c7"  # light gray — visually "open"

# Gear / cart glyph slates (2D), shared by name with the 3D viewer (WHEEL / CART
# DECK). Neutral grays that read on the off-white floor and stay clear of the
# wing-position palette, the conflict red, and the tow-path colours.
WHEEL_COLOR: str = "#566573"  # dark slate-gray — individual wheel discs
CART_DECK_COLOR: str = "#aab7b8"  # lighter gray — cart/dolly pallet squares

# 2D honesty banner + readout chrome (BRAND.md §6 parity follow-ups are tracked
# separately; these are the values the PNG currently draws).
PLACEHOLDER_BANNER_BG_2D: str = "#b00020"  # placeholder banner fill (2D)
PLACEHOLDER_BANNER_TEXT_2D: str = "white"  # placeholder banner ink (2D)
READOUT_BG_2D: str = "#ecf0f1"  # readout chip fill (2D)
READOUT_EDGE_2D: str = "#bdc3c7"  # readout chip edge (2D)
READOUT_TEXT_2D: str = "#2c3e50"  # readout chip ink (2D)

# ── Font stacks (BRAND.md §5) ───────────────────────────────────────────────
# Geist for UI/text; Geist Mono for machine output (ids, clock, readouts).
# Offline the viewer has no vendored Geist, so each stack falls back gracefully.
FONT_SANS: str = '"Geist",system-ui,sans-serif'
FONT_MONO: str = '"Geist Mono",ui-monospace,"SF Mono",Menlo,monospace'

# ── 3D / dark-surface expression (BRAND.md §3-§4) ───────────────────────────
# Neutral core (dark mode).
PAPER: str = "#0d0e10"  # --paper — scene background / page bg
SURFACE: str = "#15171a"  # --surface — floor / HUD glass base
MIST: str = "#1B1E22"  # --mist — HUD button bg
HAIRLINE: str = "#2A2E33"  # --hairline — HUD borders / top edge
HAIRLINE_2: str = "#202428"  # --hairline-2 — faintest grid line / hemisphere ground
INK: str = "#ECEEF1"  # --ink — body / label text
GRAPHITE_STRONG: str = "#C2C7CD"  # --graphite-strong — readouts text

# Accent — "Horizon" blue, dark-lifted (focus ring, scrubber, fill-light tint).
ACCENT_DARK: str = "#3FA3D6"

# Scene shell (viewer.js): floor / grid / walls / bay.
SCENE_BG: str = PAPER  # 0x0d0e10
FLOOR: str = SURFACE  # 0x15171a
GRID_MAJOR: str = STATUS["wall"]  # 0x3b4046 — structural majors = wall ink
GRID_MINOR: str = HAIRLINE_2  # 0x202428 — faintest structural line
WALLS: str = STATUS["wall"]  # 0x3b4046 — wall = #3B4046 in 2D and 3D
WALLS_OPACITY: float = 0.20
BAY: str = STATUS["maint"]  # 0x7b63a3 — maintenance bay = maint violet
BAY_OPACITY: float = 0.32

# Lights (viewer.js): neutral white key/hemisphere keeps hues honest; the only
# coloured light is a pale tint of the horizon accent.
LIGHT_WHITE: str = "#ffffff"
HEMISPHERE_SKY: str = LIGHT_WHITE
HEMISPHERE_GROUND: str = HAIRLINE_2  # 0x202428
HEMISPHERE_INTENSITY: float = 0.85
SUN_COLOR: str = LIGHT_WHITE
SUN_INTENSITY: float = 1.0
FILL_COLOR: str = "#cfe3f2"  # pale tint of the horizon accent
FILL_INTENSITY: float = 0.3

# Gear / cart (viewer.js) — same tokens as the 2D WHEEL_COLOR / CART_DECK_COLOR.
WHEEL_COLOR_3D: str = WHEEL_COLOR  # 0x566573
CART_DECK_COLOR_3D: str = CART_DECK_COLOR  # 0xaab7b8

# Conflict (viewer.js) — same STATUS ink as 2D; the label cue supplies the
# non-colour redundancy 3D can't hatch.
CONFLICT_3D: str = STATUS["conflict"]  # 0xc8442c

# Labels (viewer.js makeLabel) + legend.
LABEL_TEXT: str = INK  # #ECEEF1
LABEL_CHIP_BG: str = "rgba(21,23,26,0.86)"  # the one HUD glass, shared with the bar
LABEL_CONFLICT_CHIP: str = STATUS["conflict"]  # #C8442C

# HUD chrome (viewer.py _CSS).
HUD_GLASS: str = "rgba(21,23,26,.86)"  # surface glass for the HUD bar
BUTTON_BG: str = MIST  # #1B1E22
BUTTON_BORDER: str = HAIRLINE  # #2A2E33
FOCUS_RING: str = ACCENT_DARK  # #3FA3D6
SCRUBBER_ACCENT: str = ACCENT_DARK  # #3FA3D6
ERROR_BANNER_BG: str = "#BC4438"  # --danger: a real "do not trust this render"
ERROR_BANNER_TEXT: str = "#fff"
PLACEHOLDER_BANNER_BG: str = "#D6A23E"  # --warning amber: illustrative data
PLACEHOLDER_BANNER_TEXT: str = INK_EDGE  # #14161A — dark ink on the amber
READOUTS_TEXT: str = GRAPHITE_STRONG  # #C2C7CD


def viewer_brand_tokens() -> dict[str, object]:
    """The canonical token object injected into the HTML as the ``BRAND`` JSON
    blob and consumed by ``viewer.js`` (#419).

    Keys are the JS read-sites (``BRAND.floor``, ``BRAND.wallsOpacity``, …).
    Colours are ``#RRGGBB`` strings passed to ``new THREE.Color(str)`` in the
    viewer; opacities/intensities are plain numbers. Serialized canonically
    (sorted keys, compact separators) by the assembler so the HTML stays
    byte-deterministic. **This is the only token surface the viewer reads** — do
    not hard-code colour literals in ``viewer.js``.
    """
    return {
        # scene shell
        "sceneBg": SCENE_BG,
        "floor": FLOOR,
        "gridMajor": GRID_MAJOR,
        "gridMinor": GRID_MINOR,
        "walls": WALLS,
        "wallsOpacity": WALLS_OPACITY,
        "bay": BAY,
        "bayOpacity": BAY_OPACITY,
        # lights
        "hemisphereSky": HEMISPHERE_SKY,
        "hemisphereGround": HEMISPHERE_GROUND,
        "hemisphereIntensity": HEMISPHERE_INTENSITY,
        "sun": SUN_COLOR,
        "sunIntensity": SUN_INTENSITY,
        "fill": FILL_COLOR,
        "fillIntensity": FILL_INTENSITY,
        # gear / cart
        "wheel": WHEEL_COLOR_3D,
        "cartDeck": CART_DECK_COLOR_3D,
        # conflict + labels
        "conflict": CONFLICT_3D,
        "labelText": LABEL_TEXT,
        "labelChipBg": LABEL_CHIP_BG,
        "labelConflictChip": LABEL_CONFLICT_CHIP,
        # mono label/legend font stack (offline fallback)
        "fontMono": FONT_MONO,
    }
