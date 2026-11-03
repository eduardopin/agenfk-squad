#!/usr/bin/env python3
"""Sweep of the design system of the project at hand.

Reads the repository (written spec, tokens, theme, component libraries) and
says which system the rendered piece should wear. With no system, it returns
the squad's neutral palette. It never runs project code: everything is read
and matched with regular expressions.

    python3 -I sweep.py [root=.]            summary in English
    python3 -I sweep.py [root] --json       full finding
    python3 -I sweep.py [root] --css        :root block with --ds-*

Precedence on conflict: written spec > tokens > component theme.
"""
import bisect
import json
import os
import re
import stat
import sys
import tempfile
import time

IGNORED = {"node_modules", "dist", "build", ".git", ".next", ".nuxt", ".svelte-kit", ".turbo",
           ".cache", "coverage", "out", "vendor", "__pycache__", ".venv", "venv", "target",
           ".output", "storybook-static", "worktrees", ".claude", ".agenfk"}
MAX_FILES = 6000
MAX_BYTES = 1_000_000
MAX_SECONDS = 8.0
MAX_DEPTH = 7
MAX_CSS_NESTING = 32      # real @media/@layer/nesting never goes past a few levels
MAX_RESOLVED = 1000       # a var() that expands past this is a bomb, not a colour
MAX_KEYS = 10000          # keys read from one JS object literal
MAX_SPEC_CHARS = 256_000  # text of a written spec that goes through the regexes
MAX_VALUE = 200           # a value that goes into the <style>; longer than this is neither colour nor font

ROLES = ["bg", "surface", "fg", "fg-2", "accent", "border",
         "font-display", "font-body", "font-mono"]
DS = {role: "--ds-" + role for role in ROLES}
COLOR_ROLES = ROLES[:6]
FONT_ROLES = ROLES[6:]

# Normalised names (no color-/palette-/... prefix and no -default/-main suffix) per role,
# in order of preference.
COLOR_ALIASES = {
    "bg": ["background", "bg", "canvas", "page", "page-bg", "body-bg", "app-bg", "bg-page",
           "surface-page", "base", "background-body"],
    "surface": ["surface", "card", "panel", "paper", "background-paper", "bg-surface", "surface-1",
                "surface-raised", "raised", "elevated", "bg-subtle", "background-subtle",
                "bg-elevated", "popover", "muted", "background-alt", "bg-2"],
    "fg": ["foreground", "fg", "text", "text-primary", "ink", "on-background", "on-surface",
           "body-text", "text-body", "content", "text-default", "fg-default"],
    "fg-2": ["muted-foreground", "text-secondary", "text-muted", "fg-muted", "fg-2", "fg-subtle",
             "text-subtle", "secondary-text", "ink-2", "ink-muted", "subtle", "text-2",
             "on-surface-variant", "content-secondary"],
    "accent": ["primary", "brand", "accent", "action", "interactive", "link", "cta", "highlight"],
    "border": ["border", "line", "divider", "stroke", "outline", "rule", "border-subtle", "hairline"],
}
FONT_ALIASES = {
    "font-display": ["display", "heading", "headings", "title", "head", "headline", "serif"],
    "font-body": ["body", "sans", "text", "base", "default", "copy", "ui", "primary"],
    "font-mono": ["mono", "code", "monospace"],
}
PREFIXES = ("colors-", "color-", "palette-", "theme-", "ds-", "sys-", "semantic-", "tokens-",
            "token-", "global-", "brand-", "base-", "md-sys-color-", "clr-", "c-")
SUFFIXES = ("-default", "-main", "-base", "-500", "-600")
FONT_PREFIXES = ("font-family-", "fontfamily-", "fontfamilies-", "font-", "fonts-", "typography-",
                 "family-", "typeface-", "ff-")

# The accent a library ships by default: finding only this is not the project's system.
DEFAULT_ACCENTS = {
    "222.2 47.4% 11.2%", "240 5.9% 10%", "0 0% 9%", "24 9.8% 10%", "220.9 39.3% 11%",
    "oklch(0.205 0 0)", "oklch(0.21 0.006 285.885)", "oklch(0.208 0.042 265.755)",
    "oklch(0.216 0.006 56.043)", "oklch(0.21 0.034 264.665)", "#1976d2", "#3182ce",
    "#228be6", "#1677ff", "#1890ff",
}

# The squad's neutral palette: cool and sober, without the cream and without black with an acid accent.
NEUTRAL = {
    "bg": "#f7f8fa", "surface": "#ffffff", "fg": "#15171c", "fg-2": "#4b5262",
    "accent": "#2f5bd3", "border": "#d5dae2",
    "font-display": 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif',
    "font-body": 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif',
    "font-mono": 'ui-monospace, "SFMono-Regular", Menlo, Consolas, "Liberation Mono", monospace',
}
NEUTRAL_DARK = {
    "bg": "#111318", "surface": "#1a1d24", "fg": "#e8eaef", "fg-2": "#a4abb9",
    "accent": "#86a6ff", "border": "#2e333d",
}

LIBS = [("@radix-ui/themes", "Radix Themes"), ("@radix-ui/", "Radix"), ("@mui/material", "MUI"),
        ("@material-ui/core", "MUI"), ("@mantine/core", "Mantine"), ("@chakra-ui/react", "Chakra"),
        ("antd", "Ant Design"), ("@headlessui/react", "Headless UI"), ("daisyui", "daisyUI"),
        ("@nextui-org/react", "NextUI"), ("@heroui/react", "HeroUI"), ("vuetify", "Vuetify"),
        ("@angular/material", "Angular Material"), ("primereact", "PrimeReact"),
        ("bootstrap", "Bootstrap"), ("@shadcn/ui", "shadcn")]

RE_COLOR = re.compile(r"^\s*(#[0-9a-fA-F]{3,8}|-?[\d.]+(?:deg)?\s+[\d.]+%\s+[\d.]+%"
                      r"(?:\s*/\s*[\d.]+%?)?|[a-z]+)\s*$")
COLOR_FUNCTIONS = {"rgb", "rgba", "hsl", "hsla", "oklch", "oklab", "lab", "lch", "hwb", "color", "color-mix"}
# Inside a colour function only these may nest; image-set(), cross-fade(), url() and the like fetch.
NESTED_FUNCTIONS = COLOR_FUNCTIONS | {"var", "calc", "min", "max", "clamp"}
RE_COLOR_ARGS = re.compile(r"(?:[^\S\n]|[\w.,%/+*#()-])+")   # no quotes, backslash, newline, ; { } < >
# A function name (empty for a bare "(") is tried only where a run of letters and hyphens starts: without
# the lookbehind every letter of a run with no "(" re-read the rest of it (quadratic; 100k took 50 s).
# Same matches as ([A-Za-z-]*)\(, which already began at the start of the run.
RE_FUNCTION = re.compile(r"(?<![A-Za-z-])([A-Za-z-]*)\(")
RE_TRIPLE = re.compile(r"^-?[\d.]+(?:deg)?\s+[\d.]+%\s+[\d.]+%(\s*/\s*[\d.]+%?)?$")
RE_FIGMA = re.compile(r"https://(?:www\.)?figma\.com/(?:file|design|proto|board)/[\w-]+[^\s)\"'>]*")
# Starts only where a run of [\w-] starts and checks its ":" first: no retry from every "-" of a
# long run (that was quadratic on a line of dashes), and the same matches as a plain (--[\w-]+)\s*:.
RE_DECL = re.compile(r"(?<![\w-])(?=[\w-]+\s*:)[\w-]*?(--[\w-]+)\s*:\s*([^;{}]+)")
NAMED_COLORS = {"white", "black", "transparent", "currentcolor", "inherit"}
LEVEL_NAMES = {3: "written spec", 2: "tokens", 1: "components"}


# ---------------------------------------------------------------- helpers

RE_UNSAFE = re.compile(r"[<>{};\\@]|/\*|\*/|(?:url|src|image|image-set|cross-fade|element|expression)\s*\("
                       r"|javascript:|[\x00-\x1f\x7f]", re.I)


class OutOfTime(Exception):
    """The sweep's time budget ran out inside a parser."""


_budget = {"deadline": float("inf"), "hit": False}


def out_of_time():
    if time.monotonic() > _budget["deadline"]:
        _budget["hit"] = True
        return True
    return False


def safe(value):
    """A value from the repository ends up inside <style>: nothing that closes a block or a tag,
    and balanced parentheses and quotes (an unpaired "(" makes CSS swallow the rest of the block)."""
    if not value or len(value) > MAX_VALUE or RE_UNSAFE.search(value):
        return False
    if value.count('"') % 2 or value.count("'") % 2:
        return False
    level = 0
    for c in re.sub(r"\"[^\"]*\"|'[^']*'", "", value):
        level += (c == "(") - (c == ")")
        if level < 0:
            return False
    return level == 0


def inside(path, limit):
    """The real target of path (symlinks and ../ resolved) lies under limit, itself a real path."""
    try:
        real = os.path.normcase(os.path.realpath(path))
        limit = os.path.normcase(limit)
        return os.path.commonpath([real, limit]) == limit
    except ValueError:   # another drive on Windows
        return False


def read(path, limit):
    """Regular files only (no symlink to /dev/zero, no FIFO), never more than MAX_BYTES, and never a
    file whose real target is outside limit (a real path, required): a symlink in the repository that
    points at ~/.config or another project would carry that file's text into --json."""
    try:
        if not inside(path, limit):
            return ""
        st = os.stat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_BYTES:
            return ""
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read(MAX_BYTES + 1)
        return "" if len(text) > MAX_BYTES else text
    except (OSError, ValueError):
        return ""


def rel(root, path):
    """Path relative to root, always with "/": the evidence and the conflicts read the same on Windows."""
    return os.path.relpath(path, root).replace(os.sep, "/")


IMPORT_LEVELS = 3   # how far above the swept root the repository of a relative @import may start
SYSTEM_TEMPS = ("/tmp", "/var/tmp")   # system temporary folders, barred even when $TMPDIR points elsewhere


def import_limit(root):
    """Boundary of a relative @import: the nearest ancestor of root (root itself up to IMPORT_LEVELS
    above it) that holds a .git, so a monorepo app reaches its sibling token package. If that ancestor
    is a filesystem root, $HOME, the system temporary folder (the one $TMPDIR names and those in
    SYSTEM_TEMPS, barred even when $TMPDIR is elsewhere) or an ancestor of one of them, or there is
    no .git within reach, or the temporary folder cannot be found, the boundary is root itself."""
    root = os.path.realpath(root)
    try:
        temp = tempfile.gettempdir()
    except OSError:   # no writable temporary folder: it cannot be ruled out, so stay at root
        return root
    guarded = []
    for p in (os.path.expanduser("~"), temp, *SYSTEM_TEMPS):
        if p and p != "~":
            guarded.append(os.path.realpath(p))
    d = root
    for _ in range(IMPORT_LEVELS + 1):
        if os.path.lexists(os.path.join(d, ".git")):
            if os.path.dirname(d) == d or any(inside(g, d) for g in guarded):
                return root
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return root


def looks_like_font(name, value):
    n = name.lower()
    if any(k in n for k in ("font", "family", "typeface")) and not any(
            k in n for k in ("size", "weight", "height", "spacing", "leading", "tracking", "color")):
        return True
    v = value.lower()
    return bool(re.search(r"\b(sans-serif|serif|monospace|system-ui|ui-monospace)\b", v))


def color_function(v):
    """One colour function and nothing after its closing ")": rgb(...), color-mix(in oklab, #fff 20%, #000).
    Arguments carry no quotes, and the only functions nested in them are colour ones (or var/calc)."""
    m = RE_FUNCTION.match(v)
    if not m or m.group(1) not in COLOR_FUNCTIONS or not RE_COLOR_ARGS.fullmatch(v):
        return False
    if any(f.group(1).lower() not in NESTED_FUNCTIONS for f in RE_FUNCTION.finditer(v)):
        return False   # also a bare "(" with no function name
    level = 0
    for i, c in enumerate(v):
        level += (c == "(") - (c == ")")
        if level < 0 or (level == 0 and c == ")" and i != len(v) - 1):
            return False
    return level == 0 and v.endswith(")")


def looks_like_color(value):
    v = value.strip()
    if color_function(v):
        return True
    if not RE_COLOR.match(v):
        return False
    if re.fullmatch(r"[a-z]+", v):
        return v.lower() in NAMED_COLORS - {"inherit", "currentcolor", "transparent"}
    return True


def normalise_color(name):
    n = name.lower().lstrip("-").replace("_", "-").replace(".", "-")
    changed = True
    while changed:
        changed = False
        for p in PREFIXES:
            if n.startswith(p) and len(n) > len(p):
                n, changed = n[len(p):], True
        for s in SUFFIXES:
            if n.endswith(s) and len(n) > len(s):
                n, changed = n[: -len(s)], True
    return n


def normalise_font(name):
    n = name.lower().lstrip("-").replace("_", "-").replace(".", "-")
    changed = True
    while changed:
        changed = False
        for p in FONT_PREFIXES + ("theme-", "ds-", "sys-", "tokens-"):
            if n.startswith(p) and len(n) > len(p):
                n, changed = n[len(p):], True
        for s in ("-family", "-font", "-default"):
            if n.endswith(s) and len(n) > len(s):
                n, changed = n[: -len(s)], True
    if n in ("fontfamily", "font-family", "family", "font", "fonts", "typography"):
        n = "default"
    return n


RE_VAR = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^()]*(?:\([^()]*\))?[^()]*))?\)")


class Bomb(Exception):
    """A var() expansion past MAX_RESOLVED characters, or past the time budget."""


def resolve(value, tokens):
    """Replace var(--x) with the token's value, recursively. Returns None if a var with no value is left.
    An expansion past MAX_RESOLVED characters or past the time budget returns "\x00", which fails
    safe(): the caller drops the token, as it dropped the huge string this used to build."""
    try:
        return _resolve(value, tokens, 0, {})
    except Bomb:
        return "\x00"


def _resolve(value, tokens, depth, memo):
    """Memoised per (value, depth): `--a: var(--a) var(--a) ...` no longer costs k**9 calls."""
    if depth > 8:
        return None
    if "var(" not in value:
        if len(value) > MAX_RESOLVED:
            raise Bomb
        return value.strip()
    key = (value, depth)
    if key in memo:
        return memo[key]
    if len(value) > MAX_RESOLVED or out_of_time():
        raise Bomb

    def swap(m):
        name, fallback = m.group(1), (m.group(2) or "").strip()
        if name in tokens:
            r = _resolve(tokens[name], tokens, depth + 1, memo)
            if r is not None:
                return r
        if fallback:
            r = _resolve(fallback, tokens, depth + 1, memo)
            if r is not None:
                return r
        return "\x00"

    out = value
    for _ in range(6):
        new = RE_VAR.sub(swap, out)
        if len(new) > MAX_RESOLVED:
            raise Bomb
        if new == out:
            break
        out = new
    memo[key] = None if "\x00" in out or "var(" in out else out.strip()
    return memo[key]


def final_color(value):
    """A bare HSL triple (shadcn) becomes hsl(...)."""
    v = value.strip()
    return f"hsl({v})" if RE_TRIPLE.match(v) else v


def font_stack(value):
    v = value.strip().rstrip(",")
    if not v:
        return v
    if not re.search(r"\b(sans-serif|serif|monospace|system-ui|cursive|ui-\w+)\b", v):
        v += ", sans-serif" if "mono" not in v.lower() else ", monospace"
    return v


def family(value):
    """First family of a stack, without quotes."""
    first = value.split(",")[0].strip().strip("'\"")
    if not first or first.startswith("var(") or first in (
            "system-ui", "sans-serif", "serif", "monospace", "-apple-system", "ui-sans-serif",
            "ui-monospace", "ui-serif", "inherit"):
        return None
    return first


# ---------------------------------------------------------------- CSS

def strip_comments(text):
    """Drop /* ... */. By hand: the regex retried every "/*" of an unclosed run up to the end of the text.
    An unclosed comment stays, as it stayed with the regex."""
    out, i = [], 0
    while True:
        a = text.find("/*", i)
        b = text.find("*/", a + 2) if a >= 0 else -1
        if b < 0:
            break
        out.append(text[i:a])
        i = b + 2
    out.append(text[i:])
    return "".join(out)


def css_blocks(text, context="", level=0):
    """Yield (context, selector, body-without-child-blocks) for each block, recursing up to
    MAX_CSS_NESTING levels (hostile CSS with thousands of braces does not bring the sweep down)."""
    if level > MAX_CSS_NESTING:
        return
    i, n = 0, len(text)
    start = 0
    while i < n:
        c = text[i]
        if c == "{":
            if out_of_time():
                raise OutOfTime
            selector = text[start:i].strip()
            depth, j = 1, i + 1
            while j < n and depth:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                j += 1
            body = text[i + 1: j - 1]
            selector = selector.split(";")[-1].strip()
            if selector.startswith("@"):
                yield from css_blocks(body, (context + " " + selector).strip(), level + 1)
                if selector.startswith("@theme"):
                    yield context, ":root", re.sub(r"\{[^{}]*\}", "", body)
            else:
                yield context, selector, re.sub(r"\{[^{}]*\}", "", body)
                if "{" in body:
                    yield from css_blocks(body, (context + " " + selector).strip(), level + 1)
            i = j
            start = j
            continue
        if c == "}":
            start = i + 1
        i += 1


def is_dark(context, selector):
    s = (context + " " + selector).lower()
    return bool(re.search(r"\.dark\b|data-theme\s*=\s*['\"]?dark|data-mode\s*=\s*['\"]?dark|"
                          r"\.theme-dark|prefers-color-scheme\s*:\s*dark|data-color-scheme\s*=\s*"
                          r"['\"]?dark|\[dark\]", s))


def is_root(selector):
    parts = [p.strip().lower() for p in selector.split(",")]
    return any(p in (":root", "html", ":host", "body", "*") or p.startswith(":root")
               or p.startswith("html") or re.match(r"^\[data-theme", p) or p in (".light", ".dark")
               or p.startswith(".theme-") for p in parts)


USAGE = [  # (selector, property, role): where the CSS itself says which token plays the role
    (r"^(html|body|:root|main)$", r"background(-color)?", "bg"),
    (r"^(html|body|:root)$", r"color", "fg"),
    (r"^(html|body|:root)$", r"font-family", "font-body"),
    (r"^(h1|h2|h1\s*,\s*h2.*|\.font-display|\.display)$", r"font-family", "font-display"),
    (r"^(code|pre|kbd|code\s*,.*)$", r"font-family", "font-mono"),
    (r"^(a|a:link)$", r"color", "accent"),
    (r"^(:focus-visible|\*:focus-visible)$", r"outline(-color)?", "accent"),
]
APPLY = {"bg": "bg", "text": "fg", "font": "font-body"}
RE_USAGE_VAR = re.compile(r"var\(\s*(--[\w-]+)[^)]*\)")


def css_usage(selector, body, usage):
    sel = selector.strip().lower()
    for pattern, prop, role in USAGE:
        if not re.match(pattern, sel):
            continue
        for m in re.finditer(r"(?:^|;)\s*%s\s*:\s*([^;]+)" % prop, body):
            value = m.group(m.lastindex).strip()
            # A closed var() can only end at the last ")": searching past it, every unclosed "var(--a"
            # re-read the rest of the value to its end (quadratic; 120k of them took 50 s). Same match.
            found = RE_USAGE_VAR.search(value, 0, value.rfind(")") + 1)
            # A colour longer than safe()'s cap never reaches the <style>; it is rejected, not truncated
            # (a prefix of a longer value could pass), and does not go through looks_like_color.
            if role.startswith("font") or found or (len(value) <= MAX_VALUE and looks_like_color(value)):
                usage.setdefault(role, value if role.startswith("font") or not found else found.group(0))
    if sel in ("body", "html"):
        for m in re.finditer(r"@apply\s+([^;]+)", body):
            for cls in m.group(1).split():
                pre, _, name = cls.partition("-")
                if pre in APPLY and name and not re.match(r"^(\d|\[|xs$|sm$|base$|lg$|xl$|\dxl$)", name):
                    usage.setdefault(APPLY[pre],
                                     f"var(--{'font' if pre == 'font' else 'color'}-{name}, var(--{name}))")


def css_tokens(text, usage=None):
    light, dark = {}, {}
    usage = {} if usage is None else usage
    for context, selector, body in css_blocks(strip_comments(text)):
        if not is_dark(context, selector):
            css_usage(selector, body, usage)
        if not is_root(selector) and not is_dark(context, selector):
            continue
        target = dark if is_dark(context, selector) else light
        for name, value in RE_DECL.findall(body):
            target.setdefault(name, value.strip().replace("!important", "").strip())
    return light, dark


def imports(path, text, seen, limit, depth=0):
    """CSS @import by relative path (a monorepo's token package, for instance), only within limit
    (import_limit): an @import "../../x.css" never reads outside the repository."""
    seen.add(os.path.realpath(path))
    out = []
    if depth > 3:
        return out
    for m in re.finditer(r"@import\s+(?:url\()?['\"](\.{1,2}/[^'\"]+\.css)['\"]", text):
        target = os.path.normpath(os.path.join(os.path.dirname(path), m.group(1)))
        if os.path.realpath(target) in seen or not inside(target, limit) or not os.path.isfile(target):
            continue
        content = read(target, limit)
        out.append((target, content))
        out += imports(target, content, seen, limit, depth + 1)
    return out


# ---------------------------------------------------------------- JS/TS objects by regex

RE_JS_KEY = re.compile(r"\s*(?:['\"]([^'\"]+)['\"]|([\w$-]+))\s*:\s*")
# A backslash is only ever an escape: with "." also free to take it, an unclosed '\\\\...' line
# backtracked through every way of pairing the backslashes (exponential).
RE_JS_STRING = re.compile(r"(['\"`])((?:\\.|(?!\1)[^\\\n])*)\1")
RE_JS_STOP = re.compile(r"[,}\n]")
RE_JS_RUN = re.compile(r"\s+|[\w$-]+")


def js_object(text, start):
    """Read a JS object literal from the '{' at start; return a flattened dict."""
    out = {}
    stack = []
    i, n = start + 1, len(text)
    keys = steps = 0
    no_stop = False     # no , } or newline left after i: stop searching for one
    while i < n:
        steps += 1
        if not steps & 1023 and out_of_time():
            raise OutOfTime
        m = RE_JS_KEY.match(text, i)
        if not m:
            c = text[i]
            if c == "}":
                if not stack:
                    return out, i
                stack.pop()
                i += 1
                continue
            # No key at i means no key anywhere in the same run of spaces or of [\w$-]: the regex reaches
            # the same end of the run. Skip the whole run; stepping one character at a time re-read the
            # rest of it at every step (900 KB of spaces after `colors:{` took ~50 s).
            run = RE_JS_RUN.match(text, i)
            i = run.end() if run else i + 1
            continue
        key = m.group(1) or m.group(2)
        i = m.end()
        keys += 1
        if keys > MAX_KEYS:
            break
        if i < n and text[i] == "{":
            stack.append(key)
            i += 1
            continue
        if i < n and text[i] == "[":
            end = text.find("]", i)
            if end < 0:
                break
            items = re.findall(r"['\"`]([^'\"`]+)['\"`]", text[i:end])
            out["-".join(stack + [key])] = ", ".join(
                f'"{x}"' if " " in x and not x.startswith("var(") else x for x in items)
            i = end + 1
            continue
        m2 = RE_JS_STRING.match(text, i)
        if m2:
            out["-".join(stack + [key])] = m2.group(2)
            i = m2.end()
            continue
        end = None if no_stop else RE_JS_STOP.search(text, i)
        no_stop = end is None
        i = end.start() if end else i + 1
    return out, n


def key_block(text, key):
    """Find `key: {` and return the flattened object."""
    found = {}
    end = -1
    for m in re.finditer(r"\b%s\s*:\s*\{" % re.escape(key), text):
        if m.start() < end:
            continue   # inside the object just read: re-reading it from every `key: {` was quadratic
        obj, end = js_object(text, m.end() - 1)
        for k, v in obj.items():
            found.setdefault(k, v)
    return found


def tailwind_tokens(text):
    text = re.sub(r"//[^\n]*", "", strip_comments(text))
    toks = {}
    for k, v in key_block(text, "colors").items():
        toks.setdefault("--color-" + re.sub(r"-DEFAULT$", "", k), v)
    for k, v in key_block(text, "fontFamily").items():
        toks.setdefault("--font-" + re.sub(r"-DEFAULT$", "", k), v)
    return toks


# The \s* after the optional group met the \s* after "=" whenever the group did not match: "theme ="
# and 150k spaces with no "{" split that run between the two in every way (quadratic, 35 s). The last
# \s* now lives inside the group; the language accepted is the same.
RE_THEME = re.compile(r"(?:createTheme|extendTheme|createTokens|defineConfig|createSystem|"
                      r"MantineThemeOverride|theme)\s*(?:=|\()\s*(?:\w+\s*\(\s*)?\{")


def theme_tokens(text):
    """theme.ts/js from MUI, Chakra, Mantine and the like: everything becomes a flattened token."""
    text = re.sub(r"//[^\n]*", "", strip_comments(text))
    toks = {}
    end = -1
    for m in RE_THEME.finditer(text):
        if m.start() < end:
            continue
        obj, end = js_object(text, m.end() - 1)
        for k, v in obj.items():
            toks.setdefault("--" + k, v)
    return toks


RE_FONT_CALL = re.compile(r"\b(\w+)\s*\(\s*\{")
RE_FONT_VAR = re.compile(r"variable\s*:\s*['\"](--[\w-]+)['\"]")


def next_font_tokens(text):
    """`Inter({ ..., variable: '--font-inter' })`: the first variable after the call's "{" and before
    its first "}". Found by bisection over precomputed positions: the regex with [^}]*? read from
    every unclosed "f({" to the end of the file (quadratic)."""
    toks = {}
    if "next/font" not in text:
        return toks
    found = list(RE_FONT_VAR.finditer(text))
    starts = [v.start() for v in found]
    braces = [i for i, c in enumerate(text) if c == "}"]
    pos = 0
    while True:
        call = RE_FONT_CALL.search(text, pos)
        if not call:
            return toks
        k = bisect.bisect_left(starts, call.end())
        b = bisect.bisect_left(braces, call.end())
        if k < len(found) and (b == len(braces) or starts[k] < braces[b]):
            toks[found[k].group(1)] = f'"{call.group(1).replace("_", " ")}"'
            pos = found[k].end()
        else:
            pos = call.start() + 1


# ---------------------------------------------------------------- tokens.json (DTCG / Style Dictionary)

def flatten_json(obj, path=(), out=None):
    out = {} if out is None else out
    if isinstance(obj, dict):
        if "$value" in obj or ("value" in obj and not isinstance(obj.get("value"), dict)):
            out[path] = obj.get("$value", obj.get("value"))
            return out
        for k, v in obj.items():
            if not str(k).startswith("$"):
                flatten_json(v, path + (str(k),), out)
    return out


def json_tokens(text, file_name):
    try:
        flat = flatten_json(json.loads(text))
    except (ValueError, RecursionError):
        return {}, {}
    light, dark = {}, {}
    dark_file = "dark" in file_name.lower()
    for path, value in flat.items():
        parts = [p for p in path if p.lower() not in ("light", "dark", "modes", "themes")]
        dark_here = dark_file or any(p.lower() == "dark" for p in path)
        if isinstance(value, list):
            value = ", ".join(f'"{x}"' if " " in str(x) else str(x) for x in value)
        if isinstance(value, dict):
            continue
        value = str(value)
        value = re.sub(r"\{([\w.-]+)\}", lambda m: "var(--" + m.group(1).replace(".", "-") + ")", value)
        name = "--" + "-".join(parts).replace(".", "-")
        (dark if dark_here else light).setdefault(name, value)
    return light, dark


# ---------------------------------------------------------------- written spec

# (?=(\s*))\1 is an atomic \s* (Python 3.10 has no \s*+): \s* and .* overlapped, so the regex re-read
# the whitespace run after every "#" once per length of \s* ("#" + 60k spaces took 28 s). Only m.end()
# is read where it is used, so the extra group changes nothing.
RE_DARK_HEADING = re.compile(r"^#+(?!#)(?=(\s*))\1.*(?:dark|escuro).*$", re.M | re.I)


def spec_tokens(text):
    # Linear on hostile text: the spec is cut at MAX_SPEC_CHARS, the line prefix no longer crosses
    # newlines ([\s>*-]* did, from every line start to the end of a run of blank lines) and the
    # arguments of a colour are capped (an unclosed "rgb(" per line read to the end of the text).
    text = text[:MAX_SPEC_CHARS]
    toks = {}
    for name, value in RE_DECL.findall(text):
        toks.setdefault(name, value.strip())
    color = r"(#[0-9a-fA-F]{3,8}\b|(?:oklch|rgb|rgba|hsl|hsla|oklab)\([^)]{0,200}\))"
    lead = r"^(?:[^\S\n]|[>*-])*`?"
    for m in re.finditer(lead + r"([A-Za-z][\w .-]{0,40}?)`?\s*[:=]\s*[\"'`]?" + color, text, re.M):
        toks.setdefault("--" + re.sub(r"[\s.]+", "-", m.group(1).strip().lower()), m.group(2))
    for m in re.finditer(r"^\|\s*`?([A-Za-z][\w .-]{0,40}?)`?\s*\|[^|\n]*?" + color, text, re.M):
        toks.setdefault("--" + re.sub(r"[\s.]+", "-", m.group(1).strip().lower()), m.group(2))
    # Detection data: Portuguese spec labels (fonte, tipo, titulo, corpo, escuro) are read too.
    for m in re.finditer(lead + r"((?:font|fonte|tipo|type|typeface)[\w -]{0,30}?)`?\s*[:=]\s*"
                         r"[\"'`]?([A-Z][\w ]+(?:,[^\n`\"']*)?)", text, re.M):
        name = re.sub(r"^(fonte|tipo|type|typeface)", "font", m.group(1).strip().lower())
        name = re.sub(r"t[i\u00ed]tulo", "display", name).replace("corpo", "body")
        toks.setdefault("--" + re.sub(r"[\s.]+", "-", name), m.group(2).strip())
    dark = {}
    m = RE_DARK_HEADING.search(text)
    if m:
        section = text[m.end():]
        end = re.search(r"^#+\s", section, re.M)
        section = section[: end.start()] if end else section
        dark = simple_spec_tokens(section)
    return toks, dark


def simple_spec_tokens(text):
    toks = {}
    for name, value in RE_DECL.findall(text):
        toks.setdefault(name, value.strip())
    return toks


def spec_title(text):
    m = re.search(r"^name:\s*['\"]?([^'\"\n]+)", text, re.M) or re.search(r"^#\s+(.+)$", text, re.M)
    return m.group(1).strip()[:60] if m else None


# ---------------------------------------------------------------- roles

def roles_of(tokens, global_tokens):
    """Map tokens -> roles. Return {role: (value, token_name)}."""
    everything = dict(global_tokens)
    everything.update(tokens)
    by_color, by_font = {}, {}
    for name, value in tokens.items():
        if looks_like_font(name, value):
            by_font.setdefault(normalise_font(name), (name, value))
        else:
            by_color.setdefault(normalise_color(name), (name, value))
    out = {}
    for role, aliases in COLOR_ALIASES.items():
        for a in aliases:
            if a in by_color:
                name, value = by_color[a]
                r = resolve(value, everything)
                if r and safe(r) and looks_like_color(r) and "var(" not in r:
                    out[role] = (final_color(r), name)
                    break
    for role, aliases in FONT_ALIASES.items():
        for a in aliases:
            if a in by_font:
                name, value = by_font[a]
                r = resolve(value, everything)
                if r is None:
                    r = re.sub(r"var\([^)]*\)\s*,?\s*", "", value).strip(" ,")
                if r and safe(r) and (family(r) or role == "font-mono"):
                    out[role] = (font_stack(r), name)
                    break
    return out


# ---------------------------------------------------------------- sweep

def sweep(root):
    """The finding for root. Parsers and resolve() check the time budget (MAX_SECONDS for the files,
    a fresh MAX_SECONDS for the roles): a hostile file stops the sweep as truncated, never hangs it."""
    before = dict(_budget)
    try:
        return _sweep(root)
    finally:
        _budget.update(before)


def _sweep(root):
    root = os.path.abspath(root)
    real_root = os.path.realpath(root)
    limit = import_limit(root)
    t0 = time.monotonic()
    _budget.update(deadline=t0 + MAX_SECONDS, hit=False)
    sources = []          # (level, label, file, light, dark)
    spec, evidence, components, figma, conflicts, families = [], [], [], [], [], []
    storybook = False
    shadcn = False
    package_name = None
    internal_lib = None
    counted = 0
    truncated = False

    usage = {}
    seen = set()
    candidates = []
    for base, dirs, files in os.walk(root):
        depth = base[len(root):].count(os.sep)
        dirs[:] = sorted(d for d in dirs if d not in IGNORED and not (d.startswith(".") and d != ".storybook")
                         and depth < MAX_DEPTH)
        if ".storybook" in dirs:
            storybook = True
        for a in sorted(files):
            counted += 1
            if counted > MAX_FILES or time.monotonic() - t0 > MAX_SECONDS:
                truncated = True
                break
            candidates.append((depth, os.path.join(base, a)))
        if truncated:
            break
    candidates.sort(key=lambda x: (x[0], x[1]))

    for _, path in candidates:
        try:
            if time.monotonic() - t0 > MAX_SECONDS:
                truncated = True
                break
            a = os.path.basename(path)
            al = a.lower()
            r = rel(root, path)
            rl = r.lower()
            if al in ("design.md", "product.md", "ui.md", "brand.md", "design-system.md") or re.match(
                    r"^docs/(design|brand|ui|style)[\w-]*\.(md|mdx)$", rl) or re.match(
                    r"^docs/design[\w-]*/[\w-]+\.(md|mdx)$", rl):
                text = read(path, real_root)
                light, dark = spec_tokens(text)
                spec.append(r)
                if light or dark:
                    sources.append((3, "spec", r, light, dark))
                figma += RE_FIGMA.findall(text)
                if al == "design.md" and not package_name:
                    package_name = spec_title(text)
            elif al.endswith((".css", ".scss", ".pcss", ".postcss", ".less")):
                text = read(path, real_root)
                if "--" not in text and "@import" not in text:
                    continue
                for f, f_text in [(path, text)] + imports(path, text, seen, limit):
                    light, dark = css_tokens(f_text, usage)
                    if len(light) + len(dark) >= 2:
                        sources.append((2, "css", rel(root, f), light, dark))
            elif al.endswith(".tokens.json") or al in ("tokens.json", "design-tokens.json", "theme.json"):
                light, dark = json_tokens(read(path, real_root), al)
                if light or dark:
                    sources.append((2, "tokens", r, light, dark))
            elif re.match(r"tailwind\.config\.(js|cjs|mjs|ts|cts|mts)$", al):
                toks = tailwind_tokens(read(path, real_root))
                if toks:
                    sources.append((2, "tailwind", r, toks, {}))
            elif re.match(r"(theme|tema|tokens)\.(ts|tsx|js|jsx|mjs|cjs)$", al):
                toks = theme_tokens(read(path, real_root))
                if toks:
                    sources.append((1, "theme", r, toks, {}))
            elif re.match(r"(layout|_app|_document|fonts?)\.(ts|tsx|js|jsx)$", al):
                toks = next_font_tokens(read(path, real_root))
                if toks:
                    sources.append((2, "next-font", r, toks, {}))
            elif al == "components.json":
                shadcn = True
                evidence.append(r)
            elif al == "package.json":
                try:
                    pkg = json.loads(read(path, real_root))
                except ValueError:
                    continue
                if r == "package.json":
                    package_name = package_name or pkg.get("name")
                deps = {}
                for key in ("dependencies", "devDependencies", "peerDependencies"):
                    deps.update(pkg.get(key) or {})
                for d in deps:
                    for prefix, lib in LIBS:
                        if d == prefix or (prefix.endswith("/") and d.startswith(prefix)):
                            if lib not in components:
                                components.append(lib)
                    if re.match(r"^@[\w-]+/(ui|design-system|ds|components|tokens|theme)$", d):
                        internal_lib = internal_lib or d
                        if d not in components:
                            components.append(d)
                name = pkg.get("name") or ""
                if re.match(r"^@[\w-]+/(ui|design-system|ds|components|tokens|theme)$", name):
                    internal_lib = internal_lib or name
                    if name not in components:
                        components.append(name)
            elif al.startswith("readme"):
                figma += RE_FIGMA.findall(read(path, real_root))
        except (RecursionError, ValueError, MemoryError) as error:   # a hostile file does not bring the sweep down
            conflicts.append(f"{rel(root, path)} skipped: {type(error).__name__}")
        except OutOfTime:
            truncated = True
            break

    if shadcn and "shadcn" not in components:
        components.insert(0, "shadcn")

    _budget["deadline"] = time.monotonic() + MAX_SECONDS
    # Global token by name: the highest level wins, then the shallowest.
    sources.sort(key=lambda s: (-s[0], s[2].count("/"), s[2]))
    global_light, global_dark = {}, {}
    for _, _, _, light, dark in sources:
        for k, v in light.items():
            global_light.setdefault(k, v)
        for k, v in dark.items():
            global_dark.setdefault(k, v)

    roles = {p: None for p in ROLES}
    origin = {}
    dark_roles = {}
    for level, label, f, light, dark in sources:
        for role, (value, name) in roles_of(light, global_light).items():
            if roles[role] is None:
                roles[role] = value
                origin[role] = (level, f, name)
                if f not in evidence:
                    evidence.append(f)
            elif (family(roles[role]) != family(value) if role.startswith("font")
                  else norm(roles[role]) != norm(value)):
                l0, f0, _ = origin[role]
                reason = (f"{LEVEL_NAMES[l0]} > {LEVEL_NAMES[level]}" if l0 > level
                          else "same level, the shallowest wins")
                line = f"{role}: {f0} says {roles[role]}, {f} says {value}; {f0} wins ({reason})"
                if line not in conflicts:
                    conflicts.append(line)
        if dark:
            for role, (value, name) in roles_of(dark, {**global_light, **global_dark}).items():
                dark_roles.setdefault(role, value)
    # A role with no token of a known name: its use in the CSS (body, h1, a, focus) says which token plays it.
    for role, value in usage.items():
        if roles.get(role):
            continue
        r = resolve(value, global_light)
        if r is None and role.startswith("font"):
            r = re.sub(r"var\([^)]*\)\s*,?\s*", "", value).strip(" ,") or None
        if not r or not safe(r):
            continue
        if role.startswith("font"):
            if family(r):
                roles[role] = font_stack(r)
                origin[role] = (2, "CSS usage", value)
        elif looks_like_color(r):
            roles[role] = final_color(r)
            origin[role] = (2, "CSS usage", value)
            name = re.search(r"--[\w-]+", value)
            if name and global_dark.get(name.group(0)):
                dark_value = resolve(global_dark[name.group(0)], {**global_light, **global_dark})
                if dark_value and safe(dark_value) and looks_like_color(dark_value):
                    dark_roles.setdefault(role, final_color(dark_value))

    # Dark tokens that redefine light names (e.g. .dark { --background }) carry no font of their own.
    dark_roles = {p: v for p, v in dark_roles.items() if not p.startswith("font")}

    for fam in (family(roles[p]) for p in FONT_ROLES if roles[p]):
        if fam and fam not in families:
            families.append(fam)

    colors_found = [p for p in COLOR_ROLES if roles[p]]
    has_spec = any(o[0] == 3 for o in origin.values())
    default_accent = roles["accent"] and norm(re.sub(r"^hsl\((.*)\)$", r"\1", roles["accent"])) in {
        norm(x) for x in DEFAULT_ACCENTS}
    if has_spec or (len(colors_found) >= 2 and not default_accent) or (
            internal_lib and len(colors_found) >= 1):
        verdict = "project"
    else:
        verdict = "neutral"
        if default_accent:
            conflicts.append("only the library's default theme, not customised: the neutral palette holds")

    if verdict == "project":
        system = package_name or internal_lib or ("customised shadcn" if shadcn else None)
        if internal_lib and internal_lib != system:
            system = f"{system} ({internal_lib})"
    else:
        system = None
    if truncated or _budget["hit"]:
        conflicts.append(f"the sweep stopped at the limit ({MAX_FILES} files or {MAX_SECONDS:.0f}s): "
                         "run it at the root of the piece's workspace")

    return {
        "root": root,
        "verdict": verdict,
        "system": system,
        "evidence": evidence if verdict == "project" else [p for p in evidence if p.endswith(
            "components.json")],
        "spec": spec,
        "tokens": global_light,
        "roles": roles if verdict == "project" else {p: None for p in ROLES},
        "dark": dark_roles if verdict == "project" else {},
        "fonts": families if verdict == "project" else [],
        "components": components,
        "storybook": storybook,
        "figma": sorted(set(figma)),
        "conflicts": conflicts,
        "_origin": {p: o[1] for p, o in origin.items()},
    }


def norm(v):
    return re.sub(r"\s+", " ", str(v).strip().lower().replace("'", '"'))


# ---------------------------------------------------------------- outputs

def complete(roles, base):
    """Fill a missing role by deriving it from what was found; neutral only where nothing can be derived."""
    p = {k: v for k, v in roles.items() if v}
    bg = p.get("bg") or base["bg"]
    fg = p.get("fg") or base["fg"]
    out = {
        "bg": bg,
        "surface": p.get("surface") or bg,
        "fg": fg,
        "fg-2": p.get("fg-2") or f"color-mix(in oklab, {fg} 72%, {bg})",
        "accent": p.get("accent") or fg,
        "border": p.get("border") or f"color-mix(in oklab, {fg} 18%, {bg})",
    }
    if "font-body" in base:
        body = p.get("font-body") or p.get("font-display") or base["font-body"]
        out["font-display"] = p.get("font-display") or body
        out["font-body"] = body
        out["font-mono"] = p.get("font-mono") or base["font-mono"]
    return out


def css(finding):
    lines = []
    if finding["verdict"] == "project":
        name = re.sub(r"[^\w@/ .()-]", "", finding["system"] or "project tokens")[:80]
        lines.append(f"/* project design system: {name} — sweep.py */")
        light = complete(finding["roles"], NEUTRAL)
        dark = complete({**finding["roles"], **finding["dark"]}, NEUTRAL_DARK) if finding["dark"] else None
    else:
        lines.append("/* squad neutral palette: the project has no design system — sweep.py */")
        light, dark = dict(NEUTRAL), dict(NEUTRAL_DARK)

    def block(sel, values, indent=""):
        body = "".join(f"{indent}  {DS[k]}: {v};\n" for k, v in values.items() if k in DS and safe(v))
        return f"{indent}{sel} {{\n{body}{indent}}}\n"

    out = "\n".join(lines) + "\n" + block(":root", light)
    if dark:
        colors = {k: v for k, v in dark.items() if not k.startswith("font")}
        out += "@media (prefers-color-scheme: dark) {\n" + block(
            ':root:not([data-theme="light"])', colors, "  ") + "}\n"
        out += block(':root[data-theme="dark"]', colors)
    return out


def summary(finding):
    lines = []
    if finding["verdict"] == "project":
        lines.append(f"System: {finding['system'] or 'project tokens'} (verdict: project).")
        lines.append("Found in: " + (", ".join(finding["evidence"][:6]) or "—") + ".")
        found = [f"{k}={v}" for k, v in finding["roles"].items() if v and not k.startswith("font")]
        lines.append("Roles: " + ("; ".join(found) if found else "no colour role resolved") + ".")
        if finding["fonts"]:
            lines.append("Fonts: " + ", ".join(finding["fonts"]) + ".")
        if finding["dark"]:
            lines.append(f"Dark theme: {len(finding['dark'])} roles.")
    else:
        lines.append("No design system in the project (verdict: neutral): the piece wears the squad's neutral palette.")
    if finding["spec"]:
        lines.append("Written spec: " + ", ".join(finding["spec"][:4]) + ".")
    if finding["components"] or finding["storybook"]:
        lines.append("Components: " + (", ".join(finding["components"]) or "—")
                     + ("; Storybook present" if finding["storybook"] else "") + ".")
    if finding["figma"]:
        lines.append("Figma: " + ", ".join(finding["figma"][:2]) + ".")
    for c in finding["conflicts"][:3]:
        lines.append("Conflict: " + c + ".")
    return "\n".join(lines[:8])


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}
    if flags - {"--json", "--css"} or len(args) > 1 or {"--json", "--css"} <= flags:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    root = args[0] if args else "."
    if not os.path.isdir(root):
        print(f"sweep: {root} is not a directory", file=sys.stderr)
        return 2
    finding = sweep(root)
    if "--json" in flags:
        print(json.dumps({k: v for k, v in finding.items() if not k.startswith("_")},
                         ensure_ascii=False, indent=2))
    elif "--css" in flags:
        print(css(finding), end="")
    else:
        print(summary(finding))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
