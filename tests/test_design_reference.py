#!/usr/bin/env python3
"""The design-reference sweep finds the project's design system and falls back to neutral when there is none.

Self-checking: the exit code is the verdict. Builds fake projects in temporary
directories and runs scripts/sweep.py the way the other skills run it.

    python3 tests/test_design_reference.py

To also check the skill for names that must not be published (a project, a
customer, a person), list them in SQUAD_PRIVATE_TERMS, comma-separated. The
list lives in your environment, never in the repository.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "design-reference")
SCRIPT = os.path.join(SKILL, "scripts", "sweep.py")
CLOUD = os.path.join(ROOT, "skills", "cloud-diagram")
sys.dont_write_bytecode = True   # importing the script must not leave a __pycache__ in the skill
sys.path.insert(0, os.path.join(SKILL, "scripts"))
import sweep as sw  # noqa: E402

# Private context that must never ship: home paths and a session uuid. Names of
# a project, a customer or a person come from SQUAD_PRIVATE_TERMS (comma-separated),
# which lives in your environment, never in the repository.
_terms = [re.escape(t.strip()) for t in os.environ.get("SQUAD_PRIVATE_TERMS", "").split(",") if t.strip()]
PRIVATE = re.compile("|".join([r"/home/[a-z][a-z0-9_-]*/", r"/Users/[A-Za-z]",
                               r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"]
                              + _terms), re.I)
KEYS = {"root", "verdict", "system", "evidence", "spec", "tokens", "roles", "dark",
        "fonts", "components", "storybook", "figma", "conflicts"}
DS = ["--ds-bg", "--ds-surface", "--ds-fg", "--ds-fg-2", "--ds-accent", "--ds-border",
      "--ds-font-display", "--ds-font-body", "--ds-font-mono"]
TOKENS = ":root{--background:#fff;--foreground:#111;--primary:#0f766e}"
_TMP = []


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def project(files):
    root = tempfile.mkdtemp(prefix="sweep-")
    _TMP.append(root)
    for path, text in files.items():
        dest = os.path.join(root, path)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(text)
    return root


def run(root, *flags, **kw):
    p = subprocess.run([sys.executable, "-I", SCRIPT, root, *flags], capture_output=True, text=True,
                       timeout=kw.pop("timeout", 60), **kw)
    return p.returncode, p.stdout, p.stderr


def finding(root):
    code, out, err = run(root, "--json")
    assert code == 0, err
    return json.loads(out)


def css_vars(text, block=":root {"):
    i = text.find(block)
    body = text[i: text.find("}", i)] if i >= 0 else ""
    return dict(re.findall(r"(--ds-[\w-]+):\s*([^;]+);", body))


TAILWIND = {
    "package.json": json.dumps({"name": "shop-tw", "dependencies": {"tailwindcss": "3.4.0"}}),
    "tailwind.config.ts": """
import type { Config } from 'tailwindcss'
export default {
  content: ['./src/**/*.tsx'],
  theme: {
    extend: {
      colors: {
        background: '#fdfcfb',   // app background
        foreground: '#1c1917',
        primary: { DEFAULT: '#0f766e', foreground: '#ffffff' },
        border: '#e7e5e4',
      },
      fontFamily: {
        display: ['"Fraunces"', 'serif'],
        sans: ['Inter', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
    },
  },
} satisfies Config
""",
}

SHADCN_GLOBALS = """
@tailwind base;
@layer base {
  :root {
    --background: 40 33% 98%;
    --foreground: 160 30% 10%;
    --primary: 158 64% 32%;
    --primary-foreground: 0 0% 100%;
    --muted: 40 20% 94%;
    --muted-foreground: 160 8% 38%;
    --border: 40 15% 85%;
  }
  .dark {
    --background: 160 25% 7%;
    --foreground: 40 20% 95%;
    --primary: 158 55% 55%;
  }
}
"""


# 1. Tailwind with colours and fonts in the theme
def test_tailwind_config_gives_roles_fonts_and_css():
    tw = project(TAILWIND)
    a = finding(tw)
    assert KEYS <= set(a), sorted(KEYS - set(a))
    assert a["verdict"] == "project", a["verdict"]
    assert a["system"] == "shop-tw", a["system"]
    r = a["roles"]
    assert r["bg"] == "#fdfcfb", r
    assert r["accent"] == "#0f766e", "the accent comes from DEFAULT: %s" % r
    assert r["border"] == "#e7e5e4", r
    assert "Fraunces" in (r["font-display"] or ""), r
    assert (r["font-body"] or "").startswith("Inter"), r
    assert a["fonts"][:2] == ["Fraunces", "Inter"], a["fonts"]
    assert "tailwind.config.ts" in a["evidence"], a["evidence"]
    code, out, _ = run(tw, "--css")
    v = css_vars(out)
    assert code == 0 and all(k in v for k in DS), sorted(set(DS) - set(v))
    assert v.get("--ds-accent") == "#0f766e", v
    assert v.get("--ds-surface") == "#fdfcfb", "no surface role: derived from the background %s" % v
    assert "prefers-color-scheme" not in out, "no dark theme, no dark block: " + out


# 2. tokens.css on :root with a dark theme by media query and by data-theme
def test_tokens_css_resolves_var_and_merges_both_dark_forms():
    tk = project({"src/styles/tokens.css": """
/* brand tokens */
:root {
  --color-blue-700: #1d4ed8;
  --color-bg: #ffffff;
  --color-surface: #f4f6f8;
  --color-text: #0b1220;
  --color-text-muted: #475569;
  --color-primary: var(--color-blue-700);
  --color-border: #cbd5e1;
  --font-heading: "Space Grotesk", system-ui, sans-serif;
  --font-body: "IBM Plex Sans", system-ui, sans-serif;
  --font-mono: "IBM Plex Mono", monospace;
}
@media (prefers-color-scheme: dark) {
  :root { --color-bg: #0b1220; --color-text: #e2e8f0; }
}
[data-theme="dark"] { --color-surface: #111a2e; --color-primary: #93c5fd; }
.card { --color-bg: red; padding: 4px; }
"""})
    a = finding(tk)
    assert a["verdict"] == "project", a
    assert a["roles"]["accent"] == "#1d4ed8", "var() resolved: %s" % a["roles"]
    assert a["roles"]["fg-2"] == "#475569", a["roles"]
    assert a["roles"]["bg"] == "#ffffff", "a component selector is not a root token: %s" % a["roles"]
    assert a["dark"].get("bg") == "#0b1220" and a["dark"].get("accent") == "#93c5fd", a["dark"]
    code, out, _ = run(tk, "--css")
    dark = css_vars(out, ':root[data-theme="dark"] {')
    media = css_vars(out, ':root:not([data-theme="light"]) {')
    assert dark.get("--ds-bg") == "#0b1220", dark
    assert media == dark, (media, dark)
    assert dark.get("--ds-border") == "#cbd5e1", "a missing dark role inherits the light one: %s" % dark
    assert '"Space Grotesk"' in css_vars(out).get("--ds-font-display", ""), out


# 3. Customised shadcn (HSL triple, .dark) and stock shadcn
def test_customised_shadcn_is_the_project_and_stock_shadcn_is_neutral():
    sh = project({
        "components.json": json.dumps({"style": "new-york", "tailwind": {"css": "app/globals.css"}}),
        "app/globals.css": SHADCN_GLOBALS,
        "package.json": json.dumps({"name": "panel", "dependencies": {"@radix-ui/react-dialog": "1"}}),
    })
    a = finding(sh)
    assert a["verdict"] == "project", a
    assert a["components"][:2] == ["shadcn", "Radix"], a["components"]
    assert a["roles"]["accent"] == "hsl(158 64% 32%)", "the HSL triple becomes hsl(): %s" % a["roles"]
    assert a["roles"]["surface"] == "hsl(40 20% 94%)", "muted becomes the surface: %s" % a["roles"]
    assert a["dark"].get("bg") == "hsl(160 25% 7%)", a["dark"]
    stock = project({
        "components.json": "{}",
        "app/globals.css": SHADCN_GLOBALS.replace("158 64% 32%", "222.2 47.4% 11.2%")
        .replace("40 33% 98%", "0 0% 100%").replace("160 30% 10%", "222.2 84% 4.9%"),
    })
    a = finding(stock)
    assert a["verdict"] == "neutral", a["verdict"]
    assert any("library's default theme" in c for c in a["conflicts"]), a["conflicts"]
    assert "shadcn" in a["components"], a["components"]


# 4. DESIGN.md in conflict with the tokens: the written spec wins
def test_written_spec_beats_tokens_and_the_conflict_is_one_line():
    cf = project({
        "DESIGN.md": "---\nname: Atlas\n---\n# Atlas\n\n## Colours\n- primary: \"#c2410c\"\n"
                     "- background: #fffbf5\n\n## Type\n- font-display: Fraunces\n",
        "src/tokens.css": ":root { --color-primary: #2563eb; --color-background: #ffffff; --color-border: #e5e7eb; }",
    })
    a = finding(cf)
    assert a["verdict"] == "project", a
    assert a["system"] == "Atlas", a["system"]
    assert a["roles"]["accent"] == "#c2410c", a["roles"]
    assert a["roles"]["bg"] == "#fffbf5", a["roles"]
    assert a["roles"]["border"] == "#e5e7eb", "a role only in the tokens still holds: %s" % a["roles"]
    assert any(c.startswith("accent:") and "DESIGN.md" in c and "src/tokens.css" in c
               and "written spec > tokens" in c for c in a["conflicts"]), a["conflicts"]
    assert a["spec"] == ["DESIGN.md"], a["spec"]


def test_written_spec_in_portuguese_labels_is_read_too():
    pt = project({"DESIGN.md": "# Atlas\n- background: #fffbf5\n- fonte-título: Fraunces\n- fonte-corpo: Inter\n"})
    r = finding(pt)["roles"]
    assert (r["font-display"] or "").startswith("Fraunces"), r
    assert (r["font-body"] or "").startswith("Inter"), r


# 5. W3C DTCG tokens.json with an alias and a dark mode
def test_dtcg_tokens_resolve_aliases_lists_and_dark_mode():
    dt = project({"tokens/core.tokens.json": json.dumps({
        "color": {
            "$type": "color",
            "slate": {"900": {"$value": "#0f172a"}, "50": {"$value": "#f8fafc"}},
            "background": {"$value": "{color.slate.50}"},
            "text": {"$value": "{color.slate.900}"},
            "accent": {"$value": "#7c3aed"},
            "dark": {"background": {"$value": "#020617"}},
        },
        "font": {"body": {"$type": "fontFamily", "$value": ["Source Sans 3", "sans-serif"]}},
    })})
    a = finding(dt)
    assert a["roles"]["bg"] == "#f8fafc" and a["roles"]["fg"] == "#0f172a", a["roles"]
    assert a["roles"]["accent"] == "#7c3aed", a["roles"]
    assert '"Source Sans 3"' in (a["roles"]["font-body"] or ""), a["roles"]
    assert a["dark"].get("bg") == "#020617", a["dark"]


# 5b. Monorepo: the app imports the token package by relative path, outside the swept root
def test_relative_css_import_reaches_the_sibling_token_package():
    mono = project({
        "packages/brand/tokens.css": ":root{--brand:#04cc98;--bg:#f8fafc;--fg:#000f3b}.dark{--bg:#020617}",
        "packages/app/src/index.css": '@import "tailwindcss";\n@import "../../brand/tokens.css";\n'
                                      "body{font-family:var(--font-sans)}",
    })
    a = finding(os.path.join(mono, "packages", "app"))
    assert a["roles"]["accent"] == "#04cc98", a["roles"]
    assert "../brand/tokens.css" in a["evidence"], a["evidence"]


# 5c. A vocabulary of its own: the use in the CSS (body, a, focus, h1) says which token plays the role
def test_css_usage_names_the_roles_of_an_unknown_vocabulary():
    voc = project({"src/globals.css": """
@layer base {
  :root { --abyss: #06141d; --panel: #0d2430; --ink: #dce8ec; --sonar: #34e7c8; --line: #1c4a5a; }
  body { background-color: var(--abyss); color: var(--ink); }
  h1, h2, h3 { font-family: "Cinzel", serif; }
  :focus-visible { outline: 2px solid var(--sonar); }
}
"""})
    r = finding(voc)["roles"]
    assert r["bg"] == "#06141d", r
    assert r["fg"] == "#dce8ec", r
    assert r["accent"] == "#34e7c8", "the accent from the focus outline: %s" % r
    assert (r["font-display"] or "").startswith('"Cinzel"'), r
    ap = project({"app.css": ":root{--background:#fff;--foreground:#111;--color-ocean:#0369a1}"
                             "body{@apply bg-background text-foreground}a{color:var(--color-ocean)}"})
    r = finding(ap)["roles"]
    assert r["accent"] == "#0369a1", "the link gives the accent when there is no primary: %s" % r


# 5d. A hostile value in the repository does not escape the piece's <style> block
def test_hostile_values_never_reach_the_css():
    hostile = project({
        "tailwind.config.js": "module.exports={theme:{extend:{colors:{background:'#fff',foreground:'#111',"
                              "primary:'red</style><script>alert(1)</script>',border:'#ddd'},"
                              "fontFamily:{sans:['Inter}body{display:none', 'sans-serif']}}}}",
        "DESIGN.md": "# Evil</style><script>x()</script>\n- background: #ffffff\n",
    })
    code, out, _ = run(hostile, "--css")
    assert code == 0 and "<" not in out and ">" not in out and out.count("{") == out.count("}") == 1, out
    assert "alert" not in out and "display:none" not in out, out
    assert "script" not in json.dumps(finding(hostile)["roles"])
    unclosed = project({"tokens.css": ":root{--background:#fff;--foreground:#111;--primary:oklch(0.5 0.1 200;--border:#ddd}"})
    code, out, _ = run(unclosed, "--css")
    assert "oklch(0.5 0.1 200;" not in out and out.count("(") == out.count(")"), out


def test_safe_guards_on_its_own():
    # The <style> and @ guard holds by itself, without looks_like_color nor { } ;
    for v in ("oklch(0.5 0.1 200", "rgb(1,2,3))", 'Inter"', "'Inter", "#fff\n}", "x" * 201,
              "Inter</style><script>alert(1)</script>", "x@import", "Inter<b", "a>b", "url(x)"):
        assert not sw.safe(v), v
    for v in ("oklch(0.5 0.1 200)", "color-mix(in oklab, #fff 20%, #000)", '"Space Grotesk", sans-serif',
              "hsl(1 2% 3%)"):
        assert sw.safe(v), v
    font = project({
        "tailwind.config.js": "module.exports={theme:{extend:{colors:{background:'#fff',foreground:'#111',"
                              "primary:'#0f766e'},fontFamily:{sans:['Inter</style><script>alert(1)</script>',"
                              "'sans-serif'],display:['Fraunces@import','serif']}}}}",
    })
    code, out, _ = run(font, "--css")
    assert code == 0 and "<" not in out and "@import" not in out and "Fraunces" not in out \
        and "Inter" not in out, out


# 5f. A hostile repository: a symlink to a device, a FIFO and endless nested CSS neither hang nor crash
def test_device_symlink_fifo_and_deep_nesting_are_survived():
    if os.path.exists("/dev/zero"):
        zero = project({"tokens.css": TOKENS})
        for name in ("theme.json", "z.css", "DESIGN.md"):
            os.symlink("/dev/zero", os.path.join(zero, name))
        code, out, err = run(zero, "--css", preexec_fn=lambda: __import__("resource").setrlimit(
            __import__("resource").RLIMIT_AS, (1_500_000_000, 1_500_000_000)))
        assert code == 0 and "#0f766e" in out, (code, err[-300:])
    fifo = project({"tokens.css": TOKENS})
    os.mkfifo(os.path.join(fifo, "theme.css"))
    try:
        code, out, err = run(fifo, "--css", timeout=30)
    except subprocess.TimeoutExpired:
        raise AssertionError("hung opening the FIFO")
    assert code == 0 and "#0f766e" in out, err[-200:]
    deep = project({"a.css": TOKENS + "a{" * 20000})
    code, out, err = run(deep, "--css")
    assert code == 0 and "#0f766e" in out and "Traceback" not in err, (code, err[-300:])
    deep_json = project({"tokens.css": TOKENS, "theme.json": '{"a":' * 5000 + "1" + "}" * 5000})
    code, out, err = run(deep_json, "--css")
    assert code == 0 and "#0f766e" in out and "Traceback" not in err, (code, err[-300:])
    try:
        alone = sw.json_tokens('{"a":' * 5000 + "1" + "}" * 5000, "theme.json")
    except RecursionError:
        alone = "RecursionError"
    assert alone == ({}, {}), "json_tokens swallows deep JSON by itself: %s" % (alone,)


# 5h. A colour that fetches: image-set(), cross-fade(), image(), src() after a valid colour never reach --ds-*
def test_colour_that_fetches_a_url_never_reaches_the_css():
    hostile = project({"tokens.css": ':root{--background: rgb(0,0,0) image-set("https://x/b.png" 1x);'
                                     "--foreground:#111;--primary:#0f766e}"})
    code, out, _ = run(hostile, "--css")
    assert code == 0 and "image-set" not in out and "https" not in out, out
    assert css_vars(out)["--ds-bg"] != "rgb(0,0,0)", "the valid prefix must not pass either"
    for v in ('rgb(0,0,0) image-set("https://x/b.png" 1x)', '#111 -webkit-image-set("https://x/c.png" 1x)',
              'hsl(10 20% 30%) cross-fade(a 50%, "https://x/d.png")', 'rgb(1,2,3) src("https://x/e.png")',
              'color-mix(in oklab, red 50%, blue) image("https://x/f.png")', "rgb(image-set(x))",
              "rgb(1 2 3) rgb(4 5 6)", 'rgb("1" 2 3)', "rgb((1))", "rgb(1 2 3)x"):
        assert not sw.looks_like_color(v), v
    for v in ('rgb(0,0,0) image-set("https://x/b.png" 1x)', "a, image-set(x 1x)", "a cross-fade(b, c)",
              "a image(b)", "a src(b)"):
        assert not sw.safe(v), v
    for v in ("rgb(0 0 0 / 0.1)", "hsl(210deg 40% 98% / 0.5)", "oklch(70% 0.1 200 / 50%)", "#0f766e", "white",
              "color-mix(in srgb, rgb(1 2 3) 50%, white)", "rgb(calc(255 * 0.5) 0 0)", "222.2 47.4% 11.2%"):
        assert sw.looks_like_color(v), v


def timed(files, limit, flag="--css"):
    root = project(files)
    t = time.monotonic()
    try:
        code, out, err = run(root, flag, timeout=limit * 5)
    except subprocess.TimeoutExpired:
        raise AssertionError(f"still running after {limit * 5}s")
    took = time.monotonic() - t
    assert code == 0 and "Traceback" not in err, err[-300:]
    assert took < limit, f"{took:.1f}s >= {limit}s"
    return out


# 5i. A var() that points at itself k times was k**9 calls; a fan-out chain was an exponential string
def test_self_referencing_var_is_bounded_and_rejected():
    out = timed({"t.css": ":root{--background: " + " ".join(["var(--background)"] * 10)
                          + ";--foreground:#111;--primary:#0f766e}"}, 2)
    assert css_vars(out)["--ds-bg"] == sw.NEUTRAL["bg"], out
    chain = {f"--t{i}": " ".join([f"var(--t{i + 1})"] * 10) for i in range(6)}
    chain["--t6"] = "#fff"
    t = time.monotonic()
    r = sw.resolve("var(--t0)", chain)
    assert time.monotonic() - t < 2 and not (r and sw.safe(r)), repr(r)[:80]
    assert sw.resolve("var(--a, var(--b))", {"--b": "#fff"}) == "#fff", "a plain fallback still resolves"


# 5j. Inputs that made the parsers quadratic (or exponential) now finish within the budget
def test_hostile_text_parses_in_linear_time():
    timed({"tailwind.config.js": "colors:{" * 8000}, 3)
    timed({"tailwind.config.js": "module.exports={theme:{colors:{" + ('a:"",' * 190000)[:990000] + "}}}"}, 3)
    timed({"DESIGN.md": "-" * 80000}, 3)
    timed({"DESIGN.md": "#" * 80000}, 3)
    timed({"DESIGN.md": "a: rgb(\n" * 60000}, 3)
    timed({"tokens.css": ":root{" + "x--" * 300000 + "}"}, 3)
    timed({"tokens.css": ":root{--a:#fff}" + "/* " * 300000}, 3)
    timed({"tailwind.config.js": "module.exports={theme:{colors:{a:'" + "\\" * 60 + "x\n}}}"}, 3)
    timed({"layout.tsx": "import x from 'next/font/google'\n" + " b({" * 200000}, 3)
    # Same tokens as before on the ordinary shapes the regexes were rewritten for.
    assert sw.spec_tokens("- `Background`: #ffffff\n\n> font-body: Inter, sans-serif\n| Accent | `#0f766e` |\n"
                          "--border: #ddd;\n## Dark\n--bg: #000;\n") == (
        {"--border": "#ddd", "--bg": "#000", "--background": "#ffffff", "--accent": "#0f766e",
         "--font-body": "Inter, sans-serif"}, {"--bg": "#000"})
    assert sw.next_font_tokens("import { Inter } from 'next/font/google'\nconst inter = Inter({ subsets: "
                               "['latin'], variable: '--font-inter' })") == {"--font-inter": '"Inter"'}


# 5g. Limits against a large repository: files and depth
def test_file_and_depth_limits():
    many = project({f"a{i:03d}.txt": "x" for i in range(40)})
    with open(os.path.join(many, "zz.css"), "w") as f:
        f.write(TOKENS)
    before = sw.MAX_FILES
    sw.MAX_FILES = 10
    try:
        a = sw.sweep(many)
    finally:
        sw.MAX_FILES = before
    assert a["roles"]["accent"] is None and any("limit" in c for c in a["conflicts"]), a["conflicts"]
    assert sw.sweep(many)["roles"]["accent"] == "#0f766e", "below the limit the same repo finds the tokens"

    def at(depth):
        return project({"/".join(f"n{i}" for i in range(depth)) + "/tokens.css": TOKENS})
    assert sw.sweep(at(sw.MAX_DEPTH + 2))["roles"]["accent"] is None
    assert sw.sweep(at(sw.MAX_DEPTH + 1))["roles"]["accent"] is None, "exact boundary: MAX_DEPTH+1 is out"
    assert sw.sweep(at(sw.MAX_DEPTH))["roles"]["accent"] == "#0f766e", "within MAX_DEPTH is in"


# 6. Empty project: neutral, with the neutral palette and measured AA contrast
def test_empty_project_is_neutral_with_both_themes():
    empty = project({"README.md": "# nothing here\n", "node_modules/x/tokens.css": ":root{--primary:#f00;--background:#000}"})
    a = finding(empty)
    assert a["verdict"] == "neutral" and a["system"] is None, a
    assert not a["tokens"], "node_modules is ignored: %s" % a["tokens"]
    code, out, _ = run(empty, "--css")
    assert "neutral" in out.splitlines()[0], out.splitlines()[0]
    assert css_vars(out).get("--ds-accent") == sw.NEUTRAL["accent"], out
    assert ':root[data-theme="dark"]' in out and "prefers-color-scheme: dark" in out, out
    code, out, _ = run(empty)
    assert code == 0 and 1 <= len(out.strip().splitlines()) <= 8, out


def _lum(hexa):
    h = hexa.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_neutral_palettes_meet_aa():
    bad = []
    for palette, name in ((sw.NEUTRAL, "light"), (sw.NEUTRAL_DARK, "dark")):
        for role in ("fg", "fg-2", "accent"):
            for ground in ("bg", "surface"):
                r = _contrast(palette[role], palette[ground])
                if r < 4.5:
                    bad.append(f"{name}: {role} on {ground} {r:.2f}")
        r = _contrast(palette["border"], palette["bg"])
        if r < 1.25:
            bad.append(f"{name}: border on bg {r:.2f}")
    assert not bad, bad


# 7. Wrong use of the command line
def test_usage_errors_exit_2():
    empty = project({"README.md": "# x\n"})
    code, _, err = run(empty, "--json", "--css")
    assert code == 2, (code, err)
    code, _, _ = run(empty, "--html")
    assert code == 2, code
    code, _, _ = run(os.path.join(empty, "missing"))
    assert code == 2, code


# 8. The contract cloud-diagram reads: its sweep path exists and its --ds-* block is what the sweep emits
def test_cloud_diagram_contract_holds():
    md = read(os.path.join(CLOUD, "SKILL.md"))
    named = re.findall(r"~/\.claude/skills/([\w-]+/scripts/[\w.]+)", md)
    assert "design-reference/scripts/sweep.py" in named, named
    for p in named:
        assert os.path.isfile(os.path.join(ROOT, "skills", p)), p
    template = read(os.path.join(CLOUD, "references", "template.html"))
    assert set(re.findall(r"(--ds-[\w-]+)\s*:", template)) == set(DS), "the template's --ds-* block is the sweep's"
    for root in (project(TAILWIND), project({"README.md": "# x\n"})):
        code, out, err = run(root, "--css")
        assert code == 0, err
        assert set(re.findall(r"(--ds-[\w-]+)\s*:", out)) == set(DS), out
        assert set(css_vars(out)) == set(DS), "the :root block carries all nine variables: " + out


# 9. The skill: references exist, are named, English, nothing private
def test_skill_names_the_sweep_and_every_reference_it_points_to():
    md = read(os.path.join(SKILL, "SKILL.md"))
    front = md.split("---")[1]
    assert re.search(r"(?m)^name: design-reference$", front)
    for term in ("scripts/sweep.py", "--css", "--ds-bg", "references/design-system-sweep.md",
                 "references/tokens-and-components.md", "references/identity-and-pieces.md",
                 "references/quality-floor.md", "`cloud-diagram`"):
        assert term in md, term
    for doc in ("SKILL.md", "references/design-system-sweep.md", "references/tokens-and-components.md",
                "references/identity-and-pieces.md", "references/quality-floor.md"):
        for ref in re.findall(r"`((?:references|scripts)/[\w.-]+)", read(os.path.join(SKILL, doc))):
            assert os.path.exists(os.path.join(SKILL, ref)), f"{doc} names {ref}, which does not exist"


def test_no_private_context_and_no_portuguese_prose_in_the_skill():
    found = []
    pt = re.compile(r"[ãõç]|\b(?:não|você|varredura|peça|referencias|"
                    r"design-referencia|escrita|apresentacao|diagrama-cloud)\b", re.I)
    for base, _, files in os.walk(SKILL):
        for a in files:
            p = os.path.join(base, a)
            for n, line in enumerate(read(p).splitlines(), 1):
                where = f"{os.path.relpath(p, ROOT)}:{n}: {line.strip()[:80]}"
                if PRIVATE.search(line):
                    found.append("private " + where)
                # Portuguese words stay only as detection data in the script.
                if a.endswith(".md") and pt.search(line):
                    found.append("portuguese " + where)
    assert not found, "\n".join(found)


def test_zz_no_pycache_in_the_skill():
    dirty = [b for b, _, _ in os.walk(SKILL) if os.path.basename(b) == "__pycache__"]
    assert not dirty, dirty


if __name__ == "__main__":
    failures = passed = 0
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                try:
                    fn()
                    passed += 1
                    print("ok  ", name)
                except Exception as e:  # an AssertionError and an unexpected error both fail
                    failures += 1
                    print("FAIL", name, "\n     ", f"{type(e).__name__}: {e}"[:1500].replace("\n", "\n      "))
    finally:
        for d in _TMP:
            shutil.rmtree(d, ignore_errors=True)
    print(f"{passed} passed, {failures} failed")
    sys.exit(1 if failures else 0)
