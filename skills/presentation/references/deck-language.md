# Deck language

The visual and editorial system of the `templates/deck.html` template. The
template already implements all of it; this document explains why, so you can
extend it without breaking it. If the project has a `DESIGN.md`, it wins over
everything visual here; the text rules always apply.

## Tokens and theme

Three layers, as `design-reference` prescribes:

1. **Design system** (`--ds-*`): the project's values, pasted from
   `sweep.py --css`, or the squad's neutral palette when the project has
   none. Components never read from here.
2. **Semantics** (`--color-*`, `--chip-*`, `--font-*`): the role of each
   colour. They read `--ds-*` and the status primitives (`--p-*`).
3. **Components**: the template's classes, which read only semantics.

The neutral palette, with WCAG contrast measured against the background:

| Role | Light (default) | Dark | Contrast light / dark |
|---|---|---|---|
| `--color-bg` | `#f7f8fa` | `#111318` | n/a |
| `--color-ink` (titles) | `#15171c` | `#e8eaef` | 16.9:1 / 15.4:1 |
| `--color-ink-2` (body) | `#4b5262` | `#a4abb9` | 7.4:1 / 8.1:1 |
| `--color-ink-3` (caption, source) | same as body | same as body | 7.4:1 / 8.1:1 |
| `--color-accent` (the reviewer's pencil) | `#2f5bd3` | `#86a6ff` | 5.6:1 / 7.9:1 |
| `--color-positive` / `--color-negative` | `#17683f` / `#a8322a` | `#6fd19b` / `#ff8f84` | ≥ 6.3:1 / ≥ 8.4:1 |
| chip now / next / partner | ink on its own background | same | 5.9 / about 4.7 / 6.3:1 (light), 8.3 / about 6.5 / 9.1:1 (dark) |

The `next` chip background is a 12% `color-mix` of the accent into the
background, so its ratio depends on the project's accent; recheck it after
pasting a design system. A project palette replaces these values: measure its
contrast again rather than trusting this table.

The dark theme is a luminance mapping, not an inversion. It applies through
`prefers-color-scheme: dark` (unless `data-theme="light"`) or through
`<html data-theme="dark">`. The export script pins light when the deck pins
nothing, so the PDF does not depend on the exporter's system.

No token is translucent. This is not taste: a transparent PNG background has
nothing to blend a translucent colour with, and the box comes out a different
colour.

**There is one accent, and it is spent sparingly**: the reviewer's underline
(`em`) on the word that carries the slide, the source reference number, the
new step, the rule above the callout and the active dot of the rail. Green and
red are for positive and negative deltas. A chip's colour is meaning, not
decoration.

## Typography

- The project's font, from `--ds-font-display` and `--ds-font-body`, for
  everything that is read. The neutral default is the system UI stack. Titles
  in 700 to 800, tight tracking, `text-wrap: balance`.
- `--ds-font-mono` only for data: the slide number, the step number and the
  source list. Mono is not a "technical" costume; chips and the footer are
  sans.
- Tabular figures only in a table column (`td.n`). In some grotesques `tnum`
  also widens the period and the comma, and in running text that opens gaps
  in the punctuation (seen in the first export of the template).

## Slide grammar

Each slide is claim title, then content, then sources. There is no eyebrow
above the title: the title carries its own weight, and the slide number sits
in the corner.

| Pattern | Class | When |
|---|---|---|
| Claim on the left, evidence on the right | `.split` + `.column` | the workhorse |
| Fact + qualifier + value | `.ledger .lr` (`.v up` / `.v dn`) | worked numbers |
| Source reference | `<sup class="ref">n</sup>` + `<ol class="sources">` | every number |
| Line to remember | `.callout` | one per slide, at most |
| Caveat | `.note` | what is illustrative, simulated or depends on someone |
| Not-real label | `.label` (dashed border) | inside the slide, next to what it labels |
| Status | `.chip now / next / partner` | always with the same meaning |
| Closing with owner and date | `.status .sr` | the final ask |
| Journey in steps | `.steps .step` (`.new` marks the proposal) | up to 5 steps |
| Table | `table`, `td.n` | a comparison with more than two dimensions |
| Product screen | `.phone` with `--app-*` | capability, preview |

Equal-size cards as the slide's structure, a card inside a card and the "hero
number with a small label" are anti-patterns in `design-reference`; the
template does not ship them. If the deck needs a big number, it goes in the
`ledger`, with a qualifier and a source.

A footer on every slide: the project name (or the brand mark, if the project
provides the SVG) on the left and the short deck title on the right.

## Tone (not negotiable)

- Whole sentences, readable and short. No mnemonics, no "punchy" fragments,
  no abbreviation to save a word, no `=` in prose, no middle-dot lists in the
  body.
- One number per concept, owned by one slide. Two slides never give different
  numbers for the same thing.
- Every number says where it came from, on the slide itself. Invented or
  illustrative content is labelled where it appears: a "Preview · depends on
  X" label inside the mockup is better than a footnote.
- Never invent a named third party (bank, insurer, partner, customer). Use the
  kind of institution, with illustrative numbers labelled as such.
- Cost, price and margin come from `finops` / `finance`, with the date of the
  quote.
- **No sentence needs a second reading, and none is defined by what it is
  not.** "No warning sign, although that is not evidence that it helps" fails
  both; "It is not hurting conversion. Whether it improves it is too early to
  say" is the same claim, understood on the first reading. Statistical
  vocabulary (significance, null result, variance, confidence) is a sign that
  the plain consequence has not been written yet.

The text goes through the `writing` skill before it goes on the slide.

## Register (technical or non-technical)

The skill asks for the register before writing (SKILL.md, section 3). It
changes the vocabulary, never the sentence rules above. Non-technical is the
default for anyone outside the team that built the thing. The swaps that keep
coming back:

| Internal | Non-technical |
|---|---|
| model or service codename | what it does: "the model that reviews the request" |
| SDK, component, library | the product's name, or "the app" |
| staging / production | "our test environment" / "for customers" |
| endpoint, pipeline, integration | the behaviour it produces |
| merged, in progress on the card | "delivered", "in progress" |
| internal ritual name (daily, review) | "the progress review of <date>" |
| pipeline tooling (CI, build distribution) | "a test version", "the release" |

Two checks before a non-technical deck goes out. First, read every line as
someone who has never been to a project meeting: a noun that means something
only to the team is a defect, including one the audience heard once and did
not keep. Second, grep the finished deck for the internal terms in the table;
a clean sweep is part of declaring it done.

Keep the terms the audience itself uses, even technical-sounding ones (A/B
test, conversion, funnel), and the names of your product and its features,
which are how the audience recognises the work.

## Product mockup (the phone pattern)

- `.phone`: a frame with an island, a status bar, the app bar, a thread and a
  message field. Bubbles `.b-user` / `.b-ai` and a `.widget` card.
- **Use the product's real design system**: replace the `--app-*` tokens
  (background, ink, line, accent, bubble, font) with the tokens from the
  product's repository. Never invent a product's look.
- Staggered entrance: `.msg` with a `--d` delay, replayed when the phone's
  slide becomes current (the navigation script toggles `.seen`). It is the
  template's only motion: ease-out `cubic-bezier(.23,1,.32,1)`, 420ms, only
  with `prefers-reduced-motion: no-preference`. Without the `.seen` class,
  without JavaScript or with reduced motion, the message shows still, never
  invisible. Render mode freezes everything at its final state.

## Export lessons (why render mode is the way it is)

1. A blurred shadow and a large gradient rasterise as hard bands in headless
   Chrome (PDF and screenshot). Render mode removes the shadow.
2. Fragment navigation (`#id`) does not scroll in headless mode; a variant
   with one visible slide per PNG is the only reliable capture.
3. A translucent fill on a transparent background exports wrong; hence the
   opaque tokens.
4. The print media query collapses the responsive grid; render mode pins the
   desktop with `!important`.
5. Headless `--window-size` includes the frame; the measurement uses an
   iframe of the exact size.
6. `tabular-nums` in the body opens gaps in the punctuation of some
   grotesques.
7. A PDF viewer caches `file://`; reload before you distrust the export.
8. A transparent PNG carries the theme's ink: light for a light destination,
   `--theme dark` for a dark one.
