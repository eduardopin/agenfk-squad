# The design system of the project at hand

A piece that belongs to a project looks like that project. The squad's neutral
palette is the resource for when there is no system, not the default. When the
project has a system, its tokens and typography win, and its components are
reused where the medium allows. The copy does not change with the chrome: the
writing, quality-floor and accessibility rules hold the same on a piece that
wears the project's system.

It applies to every rendered piece: screen, component, HTML document,
Artifact, PDF, deck, diagram (`cloud-diagram`), banner.

## The sweep

Run it once per project per session, on the first piece you render, and keep
the result. It is not once per piece. In a monorepo, run it on the workspace
the piece belongs to: the sweep follows relative CSS `@import` to the token
package.

What the sweep reads stays inside the project:

- A symlink whose real target is outside the swept root is not read; one that
  points inside it is.
- A relative `@import` is followed only within the repository: the nearest
  ancestor of the swept root that holds a `.git`, looking at most three levels
  up. If that ancestor is a filesystem root, `$HOME`, the system temporary
  folder or an ancestor of one of them, or there is no `.git` within reach,
  the boundary is the swept root itself. A token package outside that
  boundary is not read: run the sweep on a root that contains it.

```bash
S=~/.claude/skills/design-reference/scripts/sweep.py   # or ../design-reference/scripts/ from another skill
python3 -I "$S" <root>          # 3–8 line summary: system, where it was found, conflicts
python3 -I "$S" <root> --json   # full finding (roles, tokens, dark, components, Figma)
python3 -I "$S" <root> --css    # :root block with --ds-*, ready to paste
```

The `--css` output is the contract the squad's templates read:

| Variable | Role |
|---|---|
| `--ds-bg` | page background |
| `--ds-surface` | surface (card, panel) |
| `--ds-fg` | text |
| `--ds-fg-2` | secondary text |
| `--ds-accent` | accent (action, link, highlight) |
| `--ds-border` | border and rule |
| `--ds-font-display` / `--ds-font-body` / `--ds-font-mono` | title, body, data/code |

With a dark theme in the project, it also emits
`@media (prefers-color-scheme: dark) {:root:not([data-theme="light"])}` and
`:root[data-theme="dark"]`. A role the project does not define is derived from
the ones it does (`color-mix` between text and background), not filled in with
the neutral palette.

The `--json` keys: `root`, `verdict` (`project` or `neutral`), `system`,
`evidence`, `spec`, `tokens`, `roles`, `dark`, `fonts`, `components`,
`storybook`, `figma`, `conflicts`.

With no access to the repository, or a one-off piece outside any project: skip
the sweep and say so in one line, instead of dressing the piece silently.

## What counts as a system

| Finding | Verdict |
|---|---|
| A token or theme file with named colour and type: `tailwind.config.*`, `tokens.css`, `globals.css` with `:root`, `@theme` (Tailwind 4), `*.tokens.json` (DTCG or Style Dictionary), `theme.ts` | System. Use it |
| A component library in use: an internal `@company/ui`, shadcn (`components.json`), MUI, Mantine, Radix Themes, Chakra, Ant | System. Reuse the components where the medium allows and read the props before inventing a shape |
| A written spec: `DESIGN.md`, `docs/design*`, `BRAND.md`, a Figma link in the README | Read and follow it. It outranks the code |
| Storybook | The rendered components are the truth. Read them |
| Only the library's default theme, nothing customised | Not a system. Neutral palette |
| A client's logo and palette inside the client's repository | A third-party brand. Ask before using it |

The sweep settles the mechanical cases on its own (it finds the token by name,
or by its use in the CSS when the project has a vocabulary of its own: the
`background` of `body` is the background, the colour of `a` and the focus
`outline` are the accent, the font of `h1` is the title face). The last two
rows of the table are your judgement.

**Precedence on conflict:** written spec > tokens > component theme. The sweep
records the conflict in one line; repeat that line in your answer.

## Wearing it

- Paste the token block as it is. Do not re-derive colour from a screenshot
  or from a logo file.
- Load the project's fonts and put them where the template puts its own. A
  font self-hosted in the project (`@fontsource`, `next/font`) becomes
  `@font-face` or Google Fonts of the same family; never the "closest" system
  font.
- Reuse a real component where the medium allows (a screen in the app itself,
  an Artifact in React). A static HTML page mirrors the visual language with
  the project's tokens.
- **One system per page.** Squad tokens and project tokens on the same page is
  exactly the failure this rule exists to prevent.
- Say one line in the answer: which system the piece wears and where it was
  found. A sweep that finds nothing gets the same line ("neutral palette: the
  project has no design system").

## When the neutral palette stays

No system in the repository, no access to the repository, or a one-off piece
(an ad-hoc report, a deck for an external meeting with no defined brand): use
the neutral `--css`. It is cool and sober on purpose, with AA contrast
measured in the test, so it does not compete with the content.

A rendered document that already exists keeps its chrome. An edit never
re-dresses a page that already wears a system, ours or the project's.

## Origin

Adapted from the "host project's design system" rule of an internal writing
skills pack, rewritten to be generic.
