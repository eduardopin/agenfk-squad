# Visual identity and graphic pieces

For a logo, brand palette, banner, cover and social media piece. Brand voice
and copy belong to the `writing` skill; only what is seen lives here.

## Identity: what a brand guide needs

If the project has a guide (`docs/brand*`, `BRAND.md`, `DESIGN.md`), it rules.
If it does not and the owner asks for one, the guide ships with:

- **Logo** in four versions (horizontal, stacked, symbol only, wordmark only)
  and four colourways (full, inverted, dark mono, light mono).
- **Clear space** = the symbol's height around the whole logo. **Minimum
  size**: 120px (full logo) and 24px (symbol) on screen; 35mm and 10mm in
  print.
- **Allowed background** per version (full on light, inverted on dark and on
  the primary colour; photo only with measured contrast). Never stretch,
  rotate, add a shadow, gradient, outline or a colour outside the palette.
- **Palette** in a hierarchy: 1–2 primary, 2–3 secondary, 3–5 neutral, 4
  semantic (success, warning, error, info). Each colour with hex, role and
  where to use it; contrast measured for text (4.5:1) and for UI (3:1).
- **Type**: roles (display, body, mono), weights (400 body, 500 navigation,
  600 subtitle, 700 title), scale and `line-height`.
- **Image and icon**: photo style (light, subject, editing), illustration
  style and icon style (outline or filled, stroke, radius).

The guide becomes tokens (`references/tokens-and-components.md`): the brand is
the primitive layer, never a loose hex on the screens.

## Pieces: sizes

| Where | Piece | px |
|---|---|---|
| Instagram | post / story | 1080×1080 / 1080×1920 |
| LinkedIn | personal / company cover | 1584×396 / 1128×191 |
| X | header | 1500×500 |
| YouTube | channel art (safe area) | 2560×1440 (1546×423) |
| Facebook | cover / event | 820×312 / 1920×1080 |
| Pinterest | pin | 1000×1500 |
| Display | medium rectangle / leaderboard / half page / mobile | 300×250 / 728×90 / 300×600 / 320×50 |
| Web | hero / blog header / email | 1920×600–1080 / 1200×628 / 600×200 |
| Print | roll-up | 850×2000 mm, 300 DPI, CMYK, 3–5 mm bleed |

## Pieces: rules

- **Safe area**: what matters stays in the central 70–80%; no text or call to
  action closer than 50–100px to the edge.
- **Three zones**: top for the brand or promise, middle for the message and
  visual, bottom for the call to action.
- **One call to action per piece**, high contrast, an action verb, ≥ 44px
  tall.
- **Type**: at most 2 families; title ≥ 32px, body ≥ 16px. An ad with text
  covering less than 20% of the area.
- **Art direction**: pick 1 of 2–3 candidates and say why (e.g. minimalist,
  type as image, full-bleed photo, duotone, editorial, geometric, data as the
  hero). The same rule as SKILL.md §2 holds: no default look on a free axis.
- **Production**: HTML/CSS at the exact size, captured with headless Chrome
  (`--window-size=W,H --screenshot`), one file per format:
  `<style>-<W>x<H>.png`. A file above 5MB gets compressed.
- The piece wears the project's design system (`sweep.py --css`) or the brand
  guide; with neither, ask for the brand before inventing one.

## Origin

The idea of this checklist comes from the claudekit `brand` (visual part) and
`banner-design` skills. It is restated in this skill's own words and no file or
passage is copied, so no licence is claimed.
