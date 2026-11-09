# Probe rules and exit codes

`scripts/probe.js` runs inside the rendered page and measures what the eye
misses. `scripts/render.py` injects it, opens the page in headless Chromium once
per theme, width and language, and prints one line per problem:

```
FAIL <rule>: [<theme> <width>px <lang>] <detail>
ok [<theme> <width>px <lang>] <diagram>: N labels, N icons, N edges
```

```bash
python3 -I ~/.claude/skills/cloud-diagram/scripts/render.py page.html --out shots
```

Flags, all optional:

| Flag | Meaning |
|---|---|
| `--out` | directory for previews, screenshots and probe JSON (default: a fresh temp dir) |
| `--themes` | `dark`, `light` or both (default `dark,light`) |
| `--widths` | window widths in px (default `1100,400`) |
| `--langs` | languages to measure, such as `en,pt` (default: every one in the page's `#i18n` block) |
| `--browser` | path to Chrome or Chromium, ahead of `$CHROME` and the PATH lookup |
| `--no-sandbox` | when Chromium refuses to start sandboxed, run it unsandboxed and print a `WARN sandbox` naming the runs (screenshot, dump-dom); without it that refusal exits 3 |
| `--no-shots` | skip the PNG screenshots |
| `--json` | print every probe result as JSON |

Stdlib only.

`scripts/render.sh` stays for a shell-only path (both themes, a list of widths, the
same probe). It carries the same rules; `render.py` adds languages, JSON output
and parallel renders, and is the one to reach for first. The two use different
exit codes (below), so read the one you ran.

## What the probe reads

A diagram is any `svg[data-diagram]`, `.dia svg` or `svg#dia`. Inside it:

| Element | Meaning |
|---|---|
| `<text>` | a label (text inside a badge or `<defs>` is skipped) |
| `<use x y width height>` | an icon |
| `.ar` or `.edge` | an edge: `path` (M, L, H, V, Z, absolute or relative), `line`, `polyline` or `polygon` |
| `.jn` or `.junction` | the dot that marks a fork, where one edge leaves another |
| `g.st[data-s]` or `g.step[data-step]` | a numbered badge |
| `rect.grp`, `.g-sub`, `.g-priv`, `.g-svc`, `.g-aux` | a group boundary |

Every shape is mapped through its `transform` into the svg's own coordinates, so
a node under `<g transform="translate(...)">` is measured where it is drawn. A
badge is matched against the drawing's `ol.steps`: the list whose `data-diagram`
names the svg id, else the first list after the drawing and before the next one.

## Rules, per diagram

| Rule | Fails when |
|---|---|
| `label-overlap` | two labels overlap |
| `label-on-icon` | a label overlaps an icon |
| `label-on-border` | a label straddles a group's line. Inside a band that spans zones (`g-svc`, `g-aux`) a label may cross the lines of the groups that band itself crosses, and only those |
| `step-on-label` | a numbered badge covers a label |
| `step-on-icon` | a numbered badge covers an icon |
| `edge-through-label` | an edge runs through a label |
| `edge-through-icon` | an edge runs more than 3 px past an icon's edge; touching the edge to arrive is fine |
| `edge-crossing` | two edges cross away from a junction dot |
| `edge-fork-unmarked` | an edge starts or ends on the middle of another with no junction dot there |
| `edge-corridor` | two edges run along the same line (closer than 3 px, overlapping by more than 2 px) |
| `edge-not-orthogonal` | an edge has a diagonal segment or a curve, or the edge class sits on an element that is not a path, line or polyline |
| `steps-mismatch` | the badges are not 1..N for the N items of the drawing's `ol.steps` |

## Rules, per page

| Rule | Fails when |
|---|---|
| `page-scrolls-sideways` | the body is wider than the window; the diagram must scroll inside its own `.scroll` container |
| `status` | the stamp has no Status cell, says CURRENT with no `YYYY-MM-DD` date, or says neither that nor PROPOSED |
| `language-not-applied` | the render asked for a language (`#pt`) and the page kept another one, so its labels were not measured |
| `no-diagram` | no diagram on the page |
| `probe-did-not-run` | the probe left no result (script error, timeout) |

Warnings never fail the page: `fonts` (no web font loaded, so labels were
measured in a fallback face) and `sandbox` (Chromium refused to start sandboxed
and `--no-sandbox` was given, so the render ran unsandboxed).

Languages come from the page's `#i18n` block (`<script type="application/json"
id="i18n">{"en": {...}, "pt": {...}}</script>`), English first; a page without
one is rendered in `en`. The render opens `page.html#<lang>` and the page's own
script must switch on the hash.

## Exit codes of `render.py`

| Code | Meaning |
|---|---|
| `0` | ok: every render passed |
| `1` | fail: the probe found a problem; a `FAIL` line says which |
| `2` | usage error or no such page; checked before a browser is looked for |
| `3` | no browser found, or Chromium refused its sandbox and `--no-sandbox` was not given. The page is unverified, not failing. Install one (`npx playwright install chromium`), point `--browser` or `$CHROME` at it, or add `--no-sandbox` where the sandbox cannot run |
| `4` | crash: the check itself broke, and the page is unverified, not failing |

`render.sh` exits 1 on a problem or a page with no diagram, 2 when there is no
Chrome, 3 on a usage error, 4 when Chromium refused to start sandboxed and
`--no-sandbox` was not given. Its sandbox rule is `render.py`'s: Chromium starts
sandboxed; `render.sh --no-sandbox <page.html>` repeats a refused run
unsandboxed and prints a `WARN sandbox` line, and without the flag the refusal
exits 4 with a message naming it. Its code 3 is a usage error, not a missing
browser, which is why the sandbox refusal takes 4 and not `render.py`'s 3.
