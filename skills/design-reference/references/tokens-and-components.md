# Tokens and components

How to build or extend the system when the project asks for it. If the
project already has a system, it rules: extend it in its own format, do not
switch formats.

## Three layers

```
component    --button-bg, --card-padding         changes per component
   ↓
semantic     --color-primary, --spacing-section   changes with the theme
   ↓
primitive    --color-blue-600, --space-4          rarely changes
```

- A component reads semantic; semantic reads primitive. A component never
  reads a primitive or a raw hex.
- A theme (light, dark, brand B) swaps **only the semantic layer**.
- A semantic name says the role, not the look: `destructive`, not `red`;
  `muted`, not `gray`. Every background colour has its `-foreground` pair.
- Spacing scale on a base of 4 (`--space-1` = 4px … `--space-16` = 64px); type
  on a modular scale (1.2–1.25) with `line-height` 1.1–1.3 on titles and
  1.5–1.6 on body; radius and shadow in 3–4 steps, not one per component.
- Format: W3C DTCG (`*.tokens.json`, `$value`, `$type`, alias `{color.blue.600}`)
  when there is a pipeline; otherwise, CSS custom properties on `:root` with
  dark in `@media (prefers-color-scheme: dark)` and `[data-theme="dark"]`.

## Component spec

Every new component ships with these four tables, kept short:

| Variant | Background | Text | Border | Use |
|---|---|---|---|---|
| default | primary | primary-foreground | — | main action, one per view |
| secondary | secondary | secondary-foreground | — | supporting action |
| outline | transparent | foreground | border | tertiary |
| ghost | transparent | foreground | — | quiet action, in a bar |
| destructive | destructive | destructive-foreground | — | delete, revoke |

| Size | Height | Padding X | Font | Icon |
|---|---|---|---|---|
| sm | 32px | 12px | 14px | 16px |
| md | 40px | 16px | 14px | 18px |
| lg | 48px | 24px | 16px | 20px |

Plus the **states** table (below) and the **anatomy** in ASCII (leading icon,
label, trailing icon; label, field, help/error).

## States

Priority when two hold at once: disabled > loading > active > focus > hover >
default.

| State | Trigger | What changes |
|---|---|---|
| hover | pointer over it | a slight colour change (only under `@media (hover: hover)`) |
| focus | keyboard | a 2px ring with a 2px offset in the `--ring` colour, over the background |
| active | pressed | darker colour + `scale(0.97)` |
| disabled | `disabled` / `aria-disabled` | opacity 0.5, `cursor: not-allowed`, contrast ≥ 3:1 kept |
| loading | async action | spinner in place of the icon, width does not change, `aria-busy` |
| error | validation | border and message in the error colour, `aria-invalid`, the text says how to get out |

Transition: colour and background 150ms `ease`; transform 200ms ease-out;
never `transition: all`.

## shadcn and Tailwind

- shadcn keeps the theme in `globals.css`: variables on `:root` and `.dark`.
  In the old version the value is a bare HSL triple without `hsl()`
  (`--primary: 222 47% 11%`, used as `hsl(var(--primary) / .5)`); in the new
  one, `oklch(...)`. Customise the theme by changing those variables, not the
  components' classes.
- shadcn's `accent` is a hover grey, not the brand colour: the brand is
  `primary`.
- Tailwind 3 exposes the tokens in `theme.extend.colors` / `fontFamily`;
  Tailwind 4 in `@theme { --color-*: …; --font-*: … }` in the CSS. Extend, do
  not replace the whole theme.
- Dark mode by class (`darkMode: 'class'` or `@variant dark`) with
  `next-themes` (`attribute="class"`, `defaultTheme="system"`,
  `disableTransitionOnChange`) and `suppressHydrationWarning` on `<html>`.
- A recurring new variant becomes a component variant (`cva`), not a repeated
  `className`; `className` is for the one-off case.
- Live documentation: `https://ui.shadcn.com/llms.txt` and the Tailwind docs
  through `context7`, not from memory.

## Origin

The idea of the token layers, the specs and the states comes from the claudekit
`design-system` and `ui-styling` skills. It is restated in this skill's own
words and no file or passage is copied, so no licence is claimed.
