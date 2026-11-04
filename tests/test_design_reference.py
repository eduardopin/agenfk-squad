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
import ntpath
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


def finding(root, **kw):
    code, out, err = run(root, "--json", **kw)
    assert code == 0, err
    return json.loads(out)


def skipped(case, why):
    print(f"     skipped: {case} ({why})")


def link(target, name):
    """A symlink, or False where the platform does not allow one (Windows without the privilege)."""
    try:
        os.symlink(target, name)
        return True
    except (OSError, NotImplementedError, AttributeError) as e:
        skipped(f"symlink {os.path.basename(name)}", type(e).__name__)
        return False


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


# 5b. Monorepo: the app imports the token package by relative path, outside the swept root but inside
# the repository (the nearest ancestor with a .git)
def test_relative_css_import_reaches_the_sibling_token_package():
    mono = project({
        ".git/HEAD": "ref: refs/heads/main\n",
        "packages/brand/tokens.css": ":root{--brand:#04cc98;--bg:#f8fafc;--fg:#000f3b}.dark{--bg:#020617}",
        "packages/app/src/index.css": '@import "tailwindcss";\n@import "../../brand/tokens.css";\n'
                                      "body{font-family:var(--font-sans)}",
    })
    a = finding(os.path.join(mono, "packages", "app"))
    assert a["roles"]["accent"] == "#04cc98", a["roles"]
    assert "../brand/tokens.css" in a["evidence"], a["evidence"]


def test_relative_paths_use_forward_slashes_on_windows_too():
    real_os = sw.os

    class WinOs:
        path, sep = ntpath, "\\"
    sw.os = WinOs
    try:
        r = sw.rel("C:\\repo\\packages\\app", "C:\\repo\\packages\\brand\\tokens.css")
    finally:
        sw.os = real_os
    assert r == "../brand/tokens.css", r


OUTSIDE = ":root{--bg:#aa0000;--fg:#00aa00}"


FIGMA_OUT = "https://www.figma.com/file/outsidemark/brand"
# Each read site of the sweep with a file whose text, when read, puts "outsidemark" in --json.
SYMLINK_SITES = [
    ("DESIGN.md", "# Outsidemark\n" + FIGMA_OUT + "\n"),
    ("docs/brand.md", "# Brand\n" + FIGMA_OUT + "\n"),
    ("tailwind.config.ts", "export default { theme: { extend: { colors: { outsidemark: '#aa0000', "
                           "background: '#fdfcfb' } } } }"),
    ("tokens.json", json.dumps({"color": {"outsidemark": {"$value": "#aa0000"}}})),
    ("theme.json", json.dumps({"color": {"outsidemark": {"$value": "#aa0000"}}})),
    ("theme.ts", "export const theme = { outsidemark: '#aa0000', background: '#fdfcfb' }"),
    ("layout.tsx", "import { Inter } from 'next/font/google'\n"
                   "const inter = Inter({ subsets: ['latin'], variable: '--font-outsidemark' })"),
    ("package.json", json.dumps({"name": "@outsidemark/ui"})),
    ("README.md", "# p\n" + FIGMA_OUT + "\n"),
]


# 5k. A symlink inside the swept root whose target is outside it is not read; one that stays inside is
def test_symlink_out_of_the_root_is_not_read():
    base = project({"outside/x.css": OUTSIDE, "proj/README.md": "# p\n", "proj/vendor/real.css": OUTSIDE})
    root = os.path.join(base, "proj")
    os.makedirs(os.path.join(root, "src"))
    if not link(os.path.join(base, "outside", "x.css"), os.path.join(root, "src", "linked.css")):
        return
    code, out, err = run(root, "--json")
    assert code == 0, err
    a = json.loads(out)
    assert a["verdict"] == "neutral" and "#aa0000" not in out, out
    # vendor/ is not walked: the only way in to real.css is a symlink that stays inside the root.
    os.remove(os.path.join(root, "src", "linked.css"))
    link(os.path.join(root, "vendor", "real.css"), os.path.join(root, "src", "linked.css"))
    a = finding(root)
    assert a["verdict"] == "project" and a["tokens"].get("--bg") == "#aa0000", a

    # Every other place the sweep reads a file: the same symlink out is not read, and one that stays
    # inside the root is (the control proves the marker would show if the file were read).
    for name, text in SYMLINK_SITES:
        base = project({"outside/" + os.path.basename(name): text,
                        "proj/vendor/" + os.path.basename(name): text, "proj/.keep": ""})
        root = os.path.join(base, "proj")
        here = os.path.join(root, name)
        os.makedirs(os.path.dirname(here), exist_ok=True)
        if not link(os.path.join(base, "outside", os.path.basename(name)), here):
            return
        code, out, err = run(root, "--json")
        assert code == 0, (name, err)
        assert "outsidemark" not in out.lower(), (name, out)
        os.remove(here)
        link(os.path.join(root, "vendor", os.path.basename(name)), here)
        assert "outsidemark" in json.dumps(finding(root)).lower(), (name, "control: inside the root it is read")


# 5l. A relative @import stops at the repository: never above it, never a repo that is $HOME, the
# system temporary folder or an ancestor of one, never a .git more than three levels up
def test_relative_import_never_leaves_the_repository():
    # From repo/app/src, ../../../outside.css is base/outside.css: one level above the repository.
    up = '@import "../../../outside.css";'
    base = project({"outside.css": OUTSIDE, "repo/.git/HEAD": "x\n", "repo/app/src/index.css": up})
    assert os.path.isfile(os.path.normpath(os.path.join(base, "repo", "app", "src", "../../../outside.css")))
    a = finding(os.path.join(base, "repo", "app"))
    assert a["verdict"] == "neutral" and "--bg" not in a["tokens"], a

    imp = '@import "../../outside.css";'

    base = project({"h/.git/HEAD": "x\n", "h/outside.css": OUTSIDE, "h/proj/src/index.css": imp})
    h, root = os.path.join(base, "h"), os.path.join(base, "h", "proj")
    assert finding(root)["tokens"].get("--bg") == "#aa0000", "control: an ordinary repository reads it"
    for case, env in (("repo is $HOME", {"HOME": h, "USERPROFILE": h}),
                      ("repo is an ancestor of $HOME", {"HOME": os.path.join(h, "users", "me"),
                                                         "USERPROFILE": os.path.join(h, "users", "me")}),
                      ("repo is the temporary folder", {"TMPDIR": h, "TEMP": h, "TMP": h})):
        a = finding(root, env=dict(os.environ, **env))
        assert a["verdict"] == "neutral" and "--bg" not in a["tokens"], (case, a)

    deep = {".git/HEAD": "x\n", "a/b/c/x.css": OUTSIDE, "a/b/c/d/src/index.css": '@import "../../x.css";'}
    base = project(deep)
    a = finding(os.path.join(base, "a", "b", "c", "d"))
    assert "--bg" not in a["tokens"], "a .git four levels up is out of reach: %s" % a["tokens"]
    base = project({k.replace("a/", "", 1) if not k.startswith(".") else k: v for k, v in deep.items()})
    a = finding(os.path.join(base, "b", "c", "d"))
    assert a["tokens"].get("--bg") == "#aa0000", "three levels up is in reach: %s" % a["tokens"]


# 5m. No writable temporary folder (gettempdir raises): the sweep still gives a verdict, and a relative
# @import falls back to the swept root, since the temporary folder can no longer be ruled out
def test_no_temporary_folder_still_gives_a_verdict():
    base = project({".git/HEAD": "x\n", "brand.css": OUTSIDE, "app/src/index.css": '@import "../../brand.css";',
                    "app/src/own.css": ":root{--fg:#111111;--accent:#0f766e}"})
    app = os.path.join(base, "app")
    assert finding(app)["tokens"].get("--bg") == "#aa0000", "control: with a temporary folder the repo is in reach"
    no_temp = ("import runpy, sys, tempfile\n"
               "def gone():\n    raise FileNotFoundError('No usable temporary directory found')\n"
               "tempfile.gettempdir = gone\n"
               "sys.argv = [sys.argv[1]] + sys.argv[2:]\n"
               "runpy.run_path(sys.argv[0], run_name='__main__')\n")
    p = subprocess.run([sys.executable, "-I", "-B", "-c", no_temp, SCRIPT, app, "--json"],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    a = json.loads(p.stdout)
    assert a["verdict"] == "project" and a["tokens"].get("--accent") == "#0f766e", a
    assert "--bg" not in a["tokens"], "without a temporary folder the limit is the swept root: %s" % a["tokens"]


def reads_outside_with(root, system_temps, tmpdir):
    """Sweep root with SYSTEM_TEMPS swapped (the real /tmp must not get a .git in a test) and $TMPDIR
    pointing at tmpdir; True when the OUTSIDE token above root was read through a relative @import."""
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import sweep; "
            "sweep.SYSTEM_TEMPS = tuple(sys.argv[3:]); sys.exit(sweep.main([sys.argv[2], '--json']))")
    p = subprocess.run([sys.executable, "-I", "-B", "-c", code, os.path.dirname(SCRIPT), root, *system_temps],
                       capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, TMPDIR=tmpdir, TEMP=tmpdir, TMP=tmpdir))
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)["tokens"].get("--bg") == "#aa0000"


# 5o. /tmp and /var/tmp are barred as the repository of a relative @import even when $TMPDIR is elsewhere
def test_literal_system_temporary_folders_are_barred_even_with_tmpdir_elsewhere():
    assert "/tmp" in sw.SYSTEM_TEMPS and "/var/tmp" in sw.SYSTEM_TEMPS, sw.SYSTEM_TEMPS
    base = project({"h/.git/HEAD": "x\n", "h/outside.css": OUTSIDE,
                    "h/proj/src/index.css": '@import "../../outside.css";'})
    h, root = os.path.join(base, "h"), os.path.join(base, "h", "proj")
    elsewhere = project({})
    assert reads_outside_with(root, (), elsewhere), "control: no system temporary folder in the way, it is read"
    assert not reads_outside_with(root, (h,), elsewhere), "the repository is a literal system temporary folder"
    assert not reads_outside_with(root, (os.path.join(h, "tmp"),), elsewhere), \
        "the repository is an ancestor of a literal system temporary folder"


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


# 5e. The dark value of a role found by its use resolves against the light and the dark tokens together:
# a dark token may point at one only the light theme defines, and a name both define takes the dark value.
# Resolving against the dark tokens alone, or the light ones alone, turns this red. The literal revert of
# #43 (`{**global_light, **global_dark}` in place of global_both) builds the same mapping and cannot.
def test_dark_value_of_a_css_usage_role_resolves_against_both_themes():
    voc = project({"src/globals.css": """
:root { --sea: #06141d; --abyss: #020202; --quill: #dce8ec; --glow: #aaaaaa; }
.dark { --sea: var(--abyss); --quill: var(--glow); --glow: #eeeeee; }
body { background-color: var(--sea); color: var(--quill); }
"""})
    a = finding(voc)
    assert a["roles"]["bg"] == "#06141d" and a["roles"]["fg"] == "#dce8ec", a["roles"]
    assert a["dark"].get("bg") == "#020202", "a dark token that points at a light-only one: %s" % a["dark"]
    assert a["dark"].get("fg") == "#eeeeee", "a name both themes define takes the dark value: %s" % a["dark"]


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
    if hasattr(os, "mkfifo"):
        fifo = project({"tokens.css": TOKENS})
        os.mkfifo(os.path.join(fifo, "theme.css"))
        try:
            code, out, err = run(fifo, "--css", timeout=30)
        except subprocess.TimeoutExpired:
            raise AssertionError("hung opening the FIFO")
        assert code == 0 and "#0f766e" in out, err[-200:]
    else:
        skipped("FIFO", "no os.mkfifo on this platform")
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


def children_cpu():
    """CPU seconds used by finished child processes, or None where the OS does not say (Windows)."""
    try:
        import resource
    except ImportError:
        return None
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


def sweep_took(files, limit, flag="--css"):
    """The sweep's output, its wall-clock seconds and its CPU seconds (wall clock where there is no rusage)."""
    root = project(files)
    t, cpu = time.monotonic(), children_cpu()
    try:
        code, out, err = run(root, flag, timeout=limit * 5)
    except subprocess.TimeoutExpired:
        raise AssertionError(f"still running after {limit * 5}s")
    took = time.monotonic() - t
    used = children_cpu()
    assert code == 0 and "Traceback" not in err, err[-300:]
    return out, took, (used - cpu if cpu is not None else took)


def timed(files, limit, flag="--css"):
    out, took, _ = sweep_took(files, limit, flag)
    assert took < limit, f"{took:.1f}s >= {limit}s"
    return out


def scales_linearly(make, n, limit, flag="--json"):
    """A sweep of n files fails when it hits the sweep's own time budget (MAX_SECONDS: the sweep stops and
    says so), or when it takes more than SCALE_RATIO times the fastest of three sweeps of n/10, or `limit`
    seconds, whichever is larger. Time is the child's CPU (wall clock on Windows, which has no rusage). A
    wall-clock bound alone measured the runner (a Windows leg took 5.1 s against 5). Measured here: the
    linear sweep runs 3 to 12 times its n/10, each quadratic loop #43 removed 11 to 46 times; on a heavily
    loaded machine the quadratic sweep reaches the budget first, which is why the budget line fails too."""
    small = min(sweep_took(_many(n // 10, make), limit, flag)[2] for _ in range(3))
    out, _, big = sweep_took(_many(n, make), limit * 4, flag)
    assert sw.LIMIT not in out, (
        f"{n} files hit the sweep's own budget ({big:.1f}s CPU where measured): slower than linear")
    bound = max(limit, SCALE_RATIO * small)
    assert big < bound, f"{n} files took {big:.1f}s, {n // 10} took {small:.1f}s (CPU where measured): more than {bound:.1f}s"
    return out


SCALE_RATIO = 15


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
    # A long run of spaces or of [\w$-] where no key starts: re-read from every one of its characters.
    timed({"tailwind.config.js": "colors:{" + " " * 900000}, 2)
    timed({"theme.js": "theme = {" + " " * 900000}, 2)
    timed({"tailwind.config.js": "colors:{" + "a" * 900000}, 2)
    # Same tokens as before on the ordinary shapes the regexes were rewritten for.
    assert sw.spec_tokens("- `Background`: #ffffff\n\n> font-body: Inter, sans-serif\n| Accent | `#0f766e` |\n"
                          "--border: #ddd;\n## Dark\n--bg: #000;\n") == (
        {"--border": "#ddd", "--bg": "#000", "--background": "#ffffff", "--accent": "#0f766e",
         "--font-body": "Inter, sans-serif"}, {"--bg": "#000"})
    assert sw.next_font_tokens("import { Inter } from 'next/font/google'\nconst inter = Inter({ subsets: "
                               "['latin'], variable: '--font-inter' })") == {"--font-inter": '"Inter"'}


# 5n. Single regexes where two quantifiers fought over one whitespace run (cubic or quadratic, 14 s to
# minutes). One re call cannot be interrupted by the time budget, so each has to be linear on its own.
def test_single_regex_attacks_finish_fast():
    timed({"DESIGN.md": "#" + " " * 60000}, 2, "--json")
    timed({"DESIGN.md": "#" + "\t" * 60000}, 2, "--json")
    timed({"DESIGN.md": "# " + " " * 20000 + "x" * 20000}, 2, "--json")
    for prop in ("background", "color", "font-family"):
        timed({"t.css": "body{%s: " % prop + "var(--a " * 120000 + ";}"}, 2, "--json")
    timed({"t.css": ":focus-visible{outline: " + "var(--a " * 120000 + ";}"}, 2, "--json")
    timed({"theme.ts": "theme =" + " " * 150000}, 2, "--json")
    timed({"theme.ts": "createTheme(" + "\n" * 150000}, 2, "--json")
    # RE_FUNCTION re-read a run of letters from each of them, and css_usage handed it the whole value.
    timed({"t.css": "body{--x:1; background: rgb(" + "a" * 100000 + ")}"}, 2, "--json")
    # Same results as before on the ordinary shapes.
    _, dark = sw.spec_tokens("# Palette\n--bg: #fff;\n## Dark mode\n--bg: #000;\n# End\n--bg: #f00;\n")
    assert dark == {"--bg": "#000"}, dark
    _, dark = sw.spec_tokens("#\n\n  Dark mode\n--bg: #111;\n")
    assert dark == {"--bg": "#111"}, "an empty heading followed by a dark line opens the same section: %s" % dark
    _, dark = sw.spec_tokens("#\tescuro\n--bg: #222;\n")
    assert dark == {"--bg": "#222"}, dark
    assert sw.spec_tokens("#  light palette\n--bg: #fff;\n")[1] == {}, "no dark word, no dark section"
    usage = {}
    sw.css_usage("body", "background: var(--bg, #fff) url(x.png); color: var(--fg", usage)
    assert usage == {"bg": "var(--bg, #fff)"}, "the closed var() is found, the unclosed one is not: %s" % usage
    usage = {}
    sw.css_usage("a", "color: var( --link )", usage)
    assert usage == {"accent": "var( --link )"}, usage
    theme = sw.theme_tokens("const theme = createTheme( deepmerge ( {palette:{primary:'#0f766e'}}))\n"
                            "export const t2 = extendTheme({ colors: { bg: '#fff' } })")
    assert theme == {"--palette-primary": "#0f766e", "--colors-bg": "#fff"}, theme
    for text, names in (("rgb(1 2 3)", ["rgb"]), ("color-mix(in oklab, var(--a) 20%, #000)", ["color-mix", "var"]),
                        ("a((b(", ["a", "", "b"]), ("x -webkit-y(", ["-webkit-y"]), ("((", ["", ""])):
        found = [m.group(1) for m in sw.RE_FUNCTION.finditer(text)]
        assert found == names, (text, found)
    for chunk in "a-":   # color_function on its own, past the cap css_usage now puts in front of it (old: 4 s)
        t = time.monotonic()
        sw.color_function("rgb(" + chunk * 30000 + ")")
        assert time.monotonic() - t < 1, f"color_function on 30k {chunk!r}: {time.monotonic() - t:.1f}s"
    # A colour longer than safe()'s cap is rejected, not truncated, and leaves the role to the next valid one.
    usage = {}
    sw.css_usage("body", "background: rgb(" + "1 " * 120 + ")", usage)
    assert usage == {}, "a colour over MAX_VALUE does not enter the usage: %s" % usage
    sw.css_usage("body", "background: rgb(1 2 3)", usage)
    assert usage == {"bg": "rgb(1 2 3)"}, "the over-long colour does not take the role first: %s" % usage
    usage = {}
    sw.css_usage("body", "background: rgb(" + "1 " * 97 + ")", usage)
    assert usage == {"bg": "rgb(" + "1 " * 97 + ")"}, "a colour at the cap still counts: %s" % usage


# 5p. ~200 KB of hostile text in every kind of file the sweep reads: each file is swept in under 5 s.
# The size and the limit keep both sides apart: the slowest legitimate case is under 1 s idle, every
# regression this table has caught took 10 s or more at this size.
# The prefix puts each parser in the state where its regexes look for more; the run is what they look at.
HOSTILE_FILES = {
    "DESIGN.md": ["", "# ", "- `bg`: ", "| bg | "],
    "docs/design-system.md": ["# "],
    # "--x:1; " because a CSS file with no "--" and no @import is not parsed: without it a run of letters
    # never reaches css_usage and the case cannot fail.
    "t.css": [":root{--a: ", "body{background: ", "@import ", "body{--x:1; background: rgb(",
              "a{--x:1; color: color-mix(in srgb, "],
    "tailwind.config.js": ["colors:{", "module.exports={theme:{colors:{a:"],
    "theme.ts": ["theme =", "createTheme("],
    "app/layout.tsx": ["import x from 'next/font/google'\nInter({"],
    "tokens.json": ['{"color":{"bg":{"$value":"'],
    "theme.json": ['{"a":'],
    "package.json": ['{"name":"', '{"dependencies":{"', '{"dependencies":{"@a/ui":'],
}
HOSTILE_RUNS = [" ", "\t", "#", "var(--a", "{", "(", "'", '"', "a", "-", " \t#var(--a {('\"\n"]
HOSTILE_SIZE = 200_000


def test_hostile_text_in_every_file_kind_is_swept_fast():
    for path, prefixes in sorted(HOSTILE_FILES.items()):
        for prefix in prefixes:
            for chunk in HOSTILE_RUNS:
                text = prefix + (chunk * (HOSTILE_SIZE // len(chunk) + 1))[:HOSTILE_SIZE]
                try:
                    timed({path: text}, 5, "--json")
                except AssertionError as e:
                    raise AssertionError(f"{path} {prefix!r} + {chunk!r} * {HOSTILE_SIZE // 1000} KB: {e}") from None


# 5q. A package.json that is valid JSON of the wrong shape is ignored, not a Traceback: before, a list,
# null or a number at the top raised AttributeError, a non-object dependency block or a non-string name
# a TypeError, and the whole sweep exited 1.
def test_package_json_of_the_wrong_shape_does_not_bring_the_sweep_down():
    for text in ("[1,2]", "null", "123", '"x"', "true", '{"dependencies":5}', '{"dependencies":[1,2]}',
                 '{"devDependencies":"x"}', '{"peerDependencies":null}', '{"name":123}', '{"name":["@a/ui"]}',
                 '{"name":{"x":1}}'):
        root = project({"package.json": text, "tokens.css": TOKENS})
        code, out, err = run(root, "--json")
        assert code == 0 and "Traceback" not in err, f"{text}: rc={code} {err[-300:]}"
        a = json.loads(out)
        assert a["verdict"] == "project" and a["roles"]["accent"] == "#0f766e", f"{text}: {a['verdict']}"
        assert a["components"] == [] and a["system"] is None, f"{text}: {a['components']} {a['system']}"
        assert not any("package.json" in c for c in a["conflicts"]), f"{text}: {a['conflicts']}"
    # The usable parts of a half-broken manifest still count.
    a = finding(project({"package.json": json.dumps({"name": 5, "dependencies": [1], "devDependencies":
                                                     {"@acme/ui": "1", "@mui/material": "5"}}), "tokens.css": TOKENS}))
    assert a["components"] == ["@acme/ui", "MUI"] and a["system"] == "@acme/ui", (a["components"], a["system"])
    a = finding(project({"package.json": json.dumps({"name": "@acme/ds", "dependencies": 7}), "tokens.css": TOKENS}))
    assert a["components"] == ["@acme/ds"] and a["system"] == "@acme/ds", (a["components"], a["system"])


# 5r. Each dependency was looked up in the components list (linear): 52k of them took 9 s.
def test_fifty_thousand_dependencies_are_read_fast():
    deps = {f"@s{i}/ui": "1" for i in range(50_000)}
    deps.update({"@radix-ui/react-dialog": "1", "@s0/ui": "2"})
    out = timed({"package.json": json.dumps({"name": "big", "dependencies": deps})}, 2, "--json")
    a = json.loads(out)
    assert len(a["components"]) == 50_001 and a["components"][:2] == ["@s0/ui", "@s1/ui"], a["components"][:3]
    assert a["components"].count("Radix") == 1 and len(set(a["components"])) == 50_001, "no duplicate"


# 5s. normalise_color/normalise_font stripped one prefix per pass and copied the rest: a 960 KB name of
# repeated prefixes took 2-3 s, three such files 7 s. A name past MAX_NAME is no longer stripped.
def test_huge_token_names_are_normalised_fast():
    color = ":root{--" + "colors-" * 137000 + "x:#fff;--background:#fff;--foreground:#111}"
    font = ":root{--" + "font-" * 190000 + "x: Inter, sans-serif;--background:#fff;--foreground:#111}"
    for text in (color, font):
        timed({f"t{i}.css": text for i in range(3)}, 2, "--json")
    for fn, name in ((sw.normalise_color, "-default" * 120000), (sw.normalise_font, "-family" * 137000)):
        t = time.monotonic()
        fn(name)
        assert time.monotonic() - t < 0.5, f"{fn.__name__} on {len(name) // 1000} KB: {time.monotonic() - t:.1f}s"
    # Same names as before on the ordinary shapes, up to the cap.
    for name, want in (("--color-primary", "primary"), ("--colors-primary-500", "primary"), ("--ds-bg-default", "bg"),
                       ("--md-sys-color-on-surface", "on-surface"), ("--c-c-c-fg", "fg"), ("--base-500", "500"),
                       ("--Palette_Text.Primary", "text-primary"), ("--" + "color-" * 32 + "accent", "accent")):
        assert sw.normalise_color(name) == want, (name, sw.normalise_color(name))
    for name, want in (("--font-family-display", "display"), ("--font-sans", "sans"), ("--font-family", "default"),
                       ("--typography-body-font", "body"), ("--ds-font-mono", "mono"),
                       ("--" + "font-" * 38 + "body", "body")):
        assert sw.normalise_font(name) == want, (name, sw.normalise_font(name))
    over = "--" + "color-" * 40 + "accent"
    assert sw.normalise_color(over) == over[2:], "past MAX_NAME the name is kept whole and names no role"


def _many(n, body):
    return {f"p{i // 100:02d}/t{i:04d}.css": body(i) for i in range(n)}


# 5t. roles_of copied every global token once per source, light and dark: 2000 token files of 100 tokens
# took 16 s or more.
def test_thousands_of_token_files_are_swept_fast():
    out = scales_linearly(lambda i: ":root{--background:#fff;--foreground:#111;--primary:#0f766e;"
                          + "".join(f"--x{i}-{k}:#abc;" for k in range(100)) + "} .dark{--background:#000}",
                          2000, 5)
    a = json.loads(out)
    assert a["roles"]["accent"] == "#0f766e" and len(a["tokens"]) == 3 + 2000 * 100, len(a["tokens"])
    assert a["dark"] == {"bg": "#000"}, a["dark"]
    assert a["conflicts"] == [], a["conflicts"][:3]


# 5u. Each conflict line was looked up in the conflicts list: 5500 files that disagree on the nine roles
# (about 50,000 lines) took about 8.5 s and stopped at the sweep's own 8 s budget, with 40,000 of the
# lines (about 16 s with no budget; the set takes about 1.4 s). A set answers the lookup; the list keeps
# the order.
def test_thousands_of_conflicting_files_are_swept_fast():
    roles = ("background", "card", "foreground", "muted-foreground", "primary", "border")
    out = scales_linearly(lambda i: ":root{" + "".join(f"--{r}:#{(i * 9 + s) % 0xffffff:06x};"
                                                     for s, r in enumerate(roles))
                          + f"--font-display:D{i},serif;--font-body:B{i},sans-serif;--font-mono:M{i},monospace}}",
                          5500, 5)
    conflicts = json.loads(out)["conflicts"]
    assert len(conflicts) == 9 * 5499 and len(set(conflicts)) == len(conflicts), len(conflicts)
    assert conflicts[0].startswith("bg: p00/t0000.css says #000000, p00/t0001.css says #000009;"), conflicts[0]


# 5v. The roles had the time budget only inside resolve(): sources of plain values never checked it, so
# thousands of them ran past MAX_SECONDS with no "limit" line. Now the loop over the sources checks it too.
def test_the_roles_stop_at_the_time_budget():
    root = project({f"t{i}.css": ":root{--background:#fff;--foreground:#111;--primary:#%06x}" % i
                    for i in range(5)})
    calls, real = [], sw.roles_of

    def slow(tokens, global_tokens):
        calls.append(1)
        sw._budget["deadline"] = float("-inf")   # the budget runs out during the first source
        return real(tokens, global_tokens)
    sw.roles_of = slow
    try:
        a = sw.sweep(root)
    finally:
        sw.roles_of = real
    assert len(calls) == 1, f"roles_of ran for {len(calls)} sources past the budget"
    assert any("limit" in c for c in a["conflicts"]), a["conflicts"]
    assert sw._budget["deadline"] == float("inf"), "sweep() restores the budget it found"


SUMMARY_BASE = {"verdict": "project", "system": "x", "evidence": ["a.css"], "fonts": ["Inter"],
                "dark": {"bg": "#000"}, "spec": ["DESIGN.md"], "components": ["Radix"], "storybook": True,
                "figma": ["https://www.figma.com/file/k/x"], "roles": {"bg": "#fff", "accent": "#0f766e"}}
ROLE_CONFLICTS = [f"accent: a.css says #{i}{i}{i}, b{i}.css says #000; a.css wins (same level, the shallowest wins)"
                  for i in range(3)]


def partial_lines():
    """The skipped and the limit lines as sweep() writes them, so a reworded message is tested as it ships."""
    root = project({"t.css": TOKENS, "u.css": TOKENS})
    real = sw.css_tokens

    def broken(text, usage):
        raise TypeError("a parser that breaks")
    sw.css_tokens = broken
    try:
        skipped_line = sw.sweep(root)["conflicts"][0]
    finally:
        sw.css_tokens = real
    before = sw.MAX_FILES
    sw.MAX_FILES = 1
    try:
        limit_line = sw.sweep(root)["conflicts"][-1]
    finally:
        sw.MAX_FILES = before
    assert skipped_line == "t.css" + sw.SKIPPED + "TypeError", skipped_line
    assert limit_line.startswith(sw.LIMIT), limit_line
    return skipped_line, limit_line


# 5w. The summary printed conflicts[:3] inside its 8 lines: a skipped file or the limit could vanish behind
# other conflicts or behind the other lines, with no word that the --json lists more. The two lines come
# from sweep() itself: a message reworded away from SKIPPED or LIMIT stops being found, and this goes red.
def test_summary_never_hides_a_partial_sweep():
    skipped_line, limit = partial_lines()
    other = skipped_line.replace("t.css", "y.css").replace("TypeError", "ValueError")
    for conflicts in ([skipped_line] + ROLE_CONFLICTS + [limit], ROLE_CONFLICTS + [other]):
        out = sw.summary(dict(SUMMARY_BASE, conflicts=conflicts)).splitlines()
        assert len(out) <= 8, out
        partial = [c for c in conflicts if c in (skipped_line, other, limit)]
        for c in partial:
            assert "Conflict: " + c + "." in out, f"{c!r} is not in the summary:\n" + "\n".join(out)
        assert f"Conflicts: {len(conflicts)} in all, 2 shown; --json lists every one." == out[-2], out[-2]
    # Up to three conflicts, the summary is the one it was: every line, no count.
    out = sw.summary(dict(SUMMARY_BASE, conflicts=ROLE_CONFLICTS[:2], spec=[], components=[], storybook=False,
                          figma=[]))
    assert out.splitlines()[-2:] == ["Conflict: " + c + "." for c in ROLE_CONFLICTS[:2]], out


# 5y. Past 8 lines the summary cut Spec, Components and Figma with no word: about half of the summaries
# with conflicts changed. Now a closing line says how many lines the --json has that the text does not.
def test_summary_says_how_many_lines_it_cut():
    cases = [dict(SUMMARY_BASE, conflicts=c) for c in ([], ROLE_CONFLICTS[:1], ROLE_CONFLICTS, ROLE_CONFLICTS * 2)]
    cases.append(dict(SUMMARY_BASE, conflicts=ROLE_CONFLICTS, verdict="neutral"))
    cut = 0
    for case in cases:
        out = sw.summary(case).splitlines()
        before = sw.SUMMARY_LINES
        sw.SUMMARY_LINES = 100
        try:
            whole = sw.summary(case).splitlines()
        finally:
            sw.SUMMARY_LINES = before
        assert len(out) <= 8, out
        if whole == out:
            assert not out[-1].endswith("lines in --json."), out
            continue
        cut += 1
        n = len(whole) - (len(out) - 1)
        assert out[-1] == f"+{n} lines in --json.", f"{n} lines cut, closing line {out[-1]!r}"
        assert all(line in whole for line in out[:-1]), out
        assert [c for c in whole if c.startswith("Conflict")] == [c for c in out if c.startswith("Conflict")], (
            "the conflicts keep their place", out)
    assert cut == 3, f"{cut} of the cases were cut: the fixture no longer reaches the limit"
    # Exactly 8 lines fit: nothing is cut, no closing line.
    assert len(sw.summary(dict(SUMMARY_BASE, conflicts=[])).splitlines()) == 8


# 5x. In the ordinary trees the tests build and in this repository, no file is skipped.
def test_no_file_is_skipped_in_ordinary_trees():
    trees = [project(TAILWIND), project({"app/globals.css": SHADCN_GLOBALS, "components.json": "{}"}),
             project({"tokens.css": TOKENS, "package.json": json.dumps({"name": "@acme/ui"}),
                      "DESIGN.md": "# Acme\n- background: #fff\n"}), ROOT]
    for root in trees:
        a = finding(root)
        assert not [c for c in a["conflicts"] if sw.SKIPPED in c], (root, a["conflicts"])
        assert "skipped" not in run(root)[1], root


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


def test_build_list_is_not_swallowed_by_the_companion_subsection():
    """The Build bullets (stack, native, cloud diagram, real content...) open section 4; the product UI
    companion is its own ### after them, with no bullet of the Build list inside it."""
    md = read(os.path.join(SKILL, "SKILL.md"))
    build = re.search(r"(?ms)^## 4\. Build\n(.*?)^## 5\.", md)
    assert build, "SKILL.md has a '## 4. Build' section followed by '## 5.'"
    head, *subs = re.split(r"(?m)^### ", build.group(1))
    for bullet in ("- Stack and components:", "- Native mobile:", "- A cloud or infrastructure architecture",
                   "- Real content from the start:", "- Copy is design material:", "- CSS:", "- Browser surfaces"):
        assert re.search(r"(?m)^" + re.escape(bullet), head), f"{bullet!r} is not in Build before any ###"
    companion = [s for s in subs if s.startswith("Product UI execution companion\n")]
    assert len(companion) == 1, [s.splitlines()[0] for s in subs]
    assert not re.search(r"(?m)^- ", companion[0]), "a bullet sits under the companion subsection"


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
