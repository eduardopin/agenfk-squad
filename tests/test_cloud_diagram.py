#!/usr/bin/env python3
"""The cloud-diagram skill fails a drawing with no status or with a floating edge, and its pages pass.

Self-checking: the exit code is the verdict. Writes only to temporary
directories, all under one directory of the run (TMPDIR of every child points
there too), removed at the end; the last case proves nothing is left. The render.sh cases need Chrome or Chromium; without one they are
reported as SKIP and do not fail. They run offline: the Google Fonts link is
stripped from the page copy and Chrome resolves no host name.

    python3 tests/test_cloud_diagram.py
"""
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "cloud-diagram")
CHECK = os.path.join(SKILL, "scripts", "diagram_check.py")
RENDER = os.path.join(SKILL, "scripts", "render.sh")
TEMPLATE = os.path.join(SKILL, "references", "template.html")
EXAMPLE = os.path.join(SKILL, "references", "example.html")
CLEAN = os.path.join(SKILL, "fixtures", "clean.drawio")
BASH = shutil.which("bash") or "/bin/bash"
CHROME_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
# Private context that must never ship: home paths, an AWS account id, a session uuid.
PRIVATE = re.compile(r"/home/[a-z][a-z0-9_-]*/|/Users/[A-Za-z]|\b\d{12}\b"
                     r"|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
DS = ("--ds-bg", "--ds-surface", "--ds-fg", "--ds-fg-2", "--ds-accent", "--ds-border",
      "--ds-font-display", "--ds-font-body", "--ds-font-mono")


class Skip(Exception):
    pass


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def variant(text, name):
    d = tempfile.mkdtemp(prefix="cloud-diagram-")
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def swap(text, *pairs):
    for a, b in pairs:
        assert a in text, f"{a!r} is not in the fixture"
        text = text.replace(a, b)
    return text


def check(path, *args, script=CHECK):
    r = subprocess.run([sys.executable, "-I", "-B", script, path, *args], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def chrome():
    if os.environ.get("CHROME") and os.access(os.environ["CHROME"], os.X_OK):
        return os.environ["CHROME"]
    for c in CHROME_NAMES:
        if shutil.which(c):
            return shutil.which(c)
    return None


# ---------- diagram_check ----------

def test_clean_drawio_passes():
    code, out = check(CLEAN)
    assert code == 0 and "FAIL" not in out and "WARN" not in out, out


def test_drawio_with_a_floating_edge_and_no_status_fails():
    bad = variant(swap(read(CLEAN), (' source="alb" target="ecs"', ' source="alb"'),
                       ("current, as of 2026-10-01", "overview")), "bad.drawio")
    code, out = check(bad)
    assert code == 1, out
    assert "FAIL floating-edge" in out and "'e2' has no target" in out, out
    assert "FAIL status" in out and "PROPOSED" in out, out


def test_current_with_no_date_fails_and_proposed_passes():
    undated = variant(swap(read(CLEAN), (", as of 2026-10-01", "")), "undated.drawio")
    code, out = check(undated)
    assert code == 1 and "FAIL status" in out and "no date" in out, out
    proposed = variant(swap(read(CLEAN), ("current, as of 2026-10-01", "proposed")), "proposed.drawio")
    code, out = check(proposed)
    assert code == 0, out


def test_ai_sounding_label_fails_and_allow_downgrades():
    slop = variant(swap(read(CLEAN), ('value="ECS checkout service"', 'value="Seamless checkout service"')),
                   "slop.drawio")
    code, out = check(slop)
    assert code == 1 and "FAIL lexicon" in out, out
    code, out = check(slop, "--allow", "seamless")
    assert code == 0 and "WARN lexicon" in out, out
    marked = variant(read(CLEAN).replace("<mxfile", "<!-- writing: allow seamless -->\n<mxfile", 1)
                     .replace('value="ECS checkout service"', 'value="Seamless checkout service"'), "marked.drawio")
    code, out = check(marked)
    assert code == 0 and "WARN lexicon" in out, out


def test_the_writing_skill_lexicon_is_used_when_it_is_installed():
    # A sibling skills/writing/scripts/slop_lint.py replaces the built-in lexicon. "zorbleflux"
    # is only in the stub: if the import path breaks, the gate falls back and this case goes red.
    tree = tempfile.mkdtemp(prefix="cloud-diagram-")
    try:
        scripts = os.path.join(tree, "skills", "cloud-diagram", "scripts")
        writing = os.path.join(tree, "skills", "writing", "scripts")
        os.makedirs(scripts)
        os.makedirs(writing)
        shutil.copy(CHECK, scripts)
        with open(os.path.join(writing, "slop_lint.py"), "w", encoding="utf-8") as f:
            f.write("import re, unicodedata\n"
                    "ALLOW_RE = re.compile(r'<!--\\s*writing:\\s*allow\\s+(.+?)\\s*-->', re.I)\n"
                    "BANNED_LEXICON = {'zorbleflux': 'stub word'}\n"
                    "def strip_accents(t):\n"
                    "    return ''.join(c for c in unicodedata.normalize('NFD', t) if unicodedata.category(c) != 'Mn')\n")
        copy = os.path.join(scripts, "diagram_check.py")
        stub = variant(swap(read(CLEAN), ('value="ECS checkout service"', 'value="ECS zorbleflux service"')),
                       "stub.drawio")
        code, out = check(stub, script=copy)
        assert code == 1 and "FAIL lexicon" in out and "zorbleflux" in out and "stub word" in out, out
        assert "built-in list" not in out, out
        # the stub's lexicon replaces the built-in one, it does not add to it
        builtin = variant(swap(read(CLEAN), ('value="ECS checkout service"', 'value="Seamless checkout service"')),
                          "builtin.drawio")
        code, out = check(builtin, script=copy)
        assert code == 0, out
        assert not [b for b, _, _ in os.walk(writing) if os.path.basename(b) == "__pycache__"]
    finally:
        shutil.rmtree(tree)


def _lone_check(writing_stub=None):
    """diagram_check.py copied into a skills/ tree; writing_stub, when given, is its slop_lint.py."""
    tree = tempfile.mkdtemp(prefix="cloud-diagram-")
    scripts = os.path.join(tree, "skills", "cloud-diagram", "scripts")
    os.makedirs(scripts)
    shutil.copy(CHECK, scripts)
    if writing_stub is not None:
        writing = os.path.join(tree, "skills", "writing", "scripts")
        os.makedirs(writing)
        with open(os.path.join(writing, "slop_lint.py"), "w", encoding="utf-8") as f:
            f.write(writing_stub)
    return tree, os.path.join(scripts, "diagram_check.py")


def test_built_in_lexicon_applies_with_no_writing_skill_and_says_so():
    tree, copy = _lone_check()
    try:
        slop = variant(swap(read(CLEAN), ('value="ECS checkout service"', 'value="Seamless checkout service"')),
                       "slop.drawio")
        code, out = check(slop, script=copy)
        assert code == 1 and "FAIL lexicon" in out and "'seamless'" in out, out
        note = [l for l in out.splitlines() if l.startswith("note:")]
        assert len(note) == 1 and "built-in list of 13 words" in note[0] and "not installed" in note[0], out
        assert "WARN" not in note[0] and "FAIL" not in note[0], note
        code, out = check(CLEAN, script=copy)
        assert code == 0 and "built-in list of 13 words" in out and "WARN" not in out, out
    finally:
        shutil.rmtree(tree)


def test_writing_skill_with_the_wrong_names_falls_back_with_a_note():
    # slop_lint is there but exports LEXICON, not BANNED_LEXICON: the gate must not pass blank.
    tree, copy = _lone_check("import re\nALLOW_RE = re.compile('x')\nLEXICON = {'zorbleflux': 'stub'}\n"
                             "def strip_accents(t):\n    return t\n")
    try:
        slop = variant(swap(read(CLEAN), ('value="ECS checkout service"', 'value="Seamless checkout service"')),
                       "slop.drawio")
        code, out = check(slop, script=copy)
        assert code == 1 and "FAIL lexicon" in out and "'seamless'" in out, out
        note = [l for l in out.splitlines() if l.startswith("note:")]
        assert len(note) == 1 and "built-in list of 13 words" in note[0] and "does not export" in note[0], out
        assert "WARN" not in note[0] and "FAIL" not in note[0], note
    finally:
        shutil.rmtree(tree)


def test_check_help_exits_0_and_a_dash_path_goes_after_double_dash():
    for flag in ("-h", "--help"):
        r = subprocess.run([sys.executable, "-I", "-B", CHECK, flag], capture_output=True, text=True)
        assert r.returncode == 0 and "Check a .drawio diagram" in r.stdout and "[--allow TERM] [--]" in r.stdout, (
            flag, r.returncode, r.stdout + r.stderr)
    tmp = tempfile.mkdtemp(prefix="cloud-diagram-")
    try:
        shutil.copy(CLEAN, os.path.join(tmp, "-clean.drawio"))
        r = subprocess.run([sys.executable, "-I", "-B", CHECK, "--", "-clean.drawio"], capture_output=True,
                           text=True, cwd=tmp)
        assert r.returncode == 0 and "ok -clean.drawio" in r.stdout, r.stdout + r.stderr
        r = subprocess.run([sys.executable, "-I", "-B", CHECK, "-clean.drawio"], capture_output=True,
                           text=True, cwd=tmp)
        assert r.returncode == 2 and "unknown option -clean.drawio" in r.stderr, r.stdout + r.stderr
    finally:
        shutil.rmtree(tmp)


def _boxes(n):
    return "".join(
        f'<mxCell id="x{i}" value="Queue {i}" style="shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.sqs;'
        f'fillColor=#E7157B;" vertex="1" parent="1"><mxGeometry x="{20 * i}" y="600" width="40" height="40" '
        f'as="geometry" /></mxCell>' for i in range(n))


def test_label_and_box_limits_at_the_boundary():
    base = read(CLEAN)
    six = check(variant(swap(base, ('value="Users"', 'value="one two three four five six"')), "6.drawio"))[1]
    seven = check(variant(swap(base, ('value="Users"', 'value="one two three four five six seven"')), "7.drawio"))[1]
    assert "label-length" not in six and "WARN label-length" in seven and "'users'" in seven, (six, seven)
    four = check(variant(swap(base, ('id="e1" value="HTTPS"', 'id="e1" value="one two three four"')), "4.drawio"))[1]
    five = check(variant(swap(base, ('id="e1" value="HTTPS"', 'id="e1" value="one two three four five"')), "5.drawio"))[1]
    assert "label-length" not in four and "WARN label-length" in five and "'e1'" in five, (four, five)
    # the first page already has 4 boxes (Users, ALB, ECS, RDS): +21 = 25 passes, +22 = 26 warns
    first = base.index("</root>")
    twenty_five = check(variant(base[:first] + _boxes(21) + base[first:], "25.drawio"))[1]
    twenty_six = check(variant(base[:first] + _boxes(22) + base[first:], "26.drawio"))[1]
    assert "crowded" not in twenty_five and "WARN crowded" in twenty_six and "26 boxes" in twenty_six, (
        twenty_five, twenty_six)


def test_compressed_page_and_duplicate_id_fail():
    text = read(CLEAN)
    compressed = re.sub(r"(<diagram[^>]*>)\s*<mxGraphModel.*?</mxGraphModel>\s*(</diagram>)",
                        r"\1jZJNb4MwDIZ/DdcJSLu11/VjO+zUw86ImIRJIFEISvn3MxBga6tJk5DsP7Zfx45M+GRYYnlh\2",
                        text, count=1, flags=re.S)
    code, out = check(variant(compressed, "compressed.drawio"))
    assert code == 1 and "FAIL compressed" in out, out
    dup = variant(swap(text, ('id="ecs"', 'id="alb"')), "dup.drawio")
    code, out = check(dup)
    assert code == 1 and "FAIL duplicate-id" in out, out


def test_doctype_is_refused_before_parsing():
    bomb = variant('<?xml version="1.0"?>\n<!DOCTYPE m [<!ENTITY a "aaaa">]>\n' + read(CLEAN), "dtd.drawio")
    code, out = check(bomb)
    assert code == 1 and "FAIL xml" in out and "DOCTYPE" in out, out


def test_unreadable_drawio_exits_3_and_bad_usage_exits_2():
    code, _ = check(os.path.join(tempfile.mkdtemp(prefix="cloud-diagram-"), "missing.drawio"))
    assert code == 3, code
    r = subprocess.run([sys.executable, "-I", "-B", CHECK], capture_output=True, text=True)
    assert r.returncode == 2, r.returncode


# ---------- template, example and skill, no Chrome ----------

def test_template_has_a_status_and_reads_the_design_system():
    html = read(TEMPLATE)
    css = "".join(re.findall(r"<style>(.*?)</style>", html, re.S))
    assert re.search(r"<dt>Status</dt><dd>(PROPOSED|CURRENT as of \d{4}-\d{2}-\d{2})", html), "stamp with no Status"
    assert "<dt>State</dt>" in html, "the State cell stays"
    block = re.search(r":root \{(.*?)\}", css, re.S).group(1)
    for v in DS:
        assert v + ":" in block, f"{v} has no built-in default"
    for chrome_var in ("--page:var(--ds-bg)", "--surface:var(--ds-surface)", "--ink:var(--ds-fg)",
                       "--accent:var(--ds-accent)", "--display:var(--ds-font-display)", "--body:var(--ds-font-body)"):
        assert chrome_var in css, f"the chrome does not read --ds-*: {chrome_var}"
    assert ':root[data-theme="dark"] {' in css
    assert "data-diagram" in html, "the probe finds the diagram by data-diagram"
    externals = set(re.findall(r'(?:src|href)="(https?://[^"/]+)', html))
    assert externals <= {"https://fonts.googleapis.com"}, externals


def test_example_is_a_dated_current_drawing_with_an_inventory():
    html = read(EXAMPLE)
    assert re.search(r"<dt>Status</dt><dd>CURRENT as of \d{4}-\d{2}-\d{2} · source: ", html), "example status"
    assert "fictional" in html.lower(), "the example says it is not a real system"
    assert re.search(r"<th>Source</th>", html), "the example carries an inventory with a source per node"
    externals = set(re.findall(r'(?:src|href)="(https?://[^"/]+)', html))
    assert externals <= {"https://fonts.googleapis.com"}, externals


def test_skill_is_generic_english_and_documents_the_rules():
    md = read(os.path.join(SKILL, "SKILL.md"))
    front = md.split("---")[1]
    assert re.findall(r"(?m)^(\w+):", front) == ["name", "description"]
    assert re.search(r"(?m)^name: cloud-diagram$", front)
    for term in ("Inventory before geometry", "Status", "Audience", "Isomorphism", "Education",
                 "design-reference/scripts/sweep.py", "diagram_check.py", "references/template.html",
                 "references/example.html", "render.sh", "## Origin"):
        assert term in md, term
    assert re.search(r"`design-reference/scripts/sweep\.py`\s+is\s+not\s+installed", md), \
        "SKILL.md says what to do when the sweep is not installed"
    for p in ("references/template.html", "references/example.html", "references/inventory-and-drawio.md",
              "fixtures/clean.drawio", "scripts/probe.js", "scripts/render.sh", "THIRD_PARTY_NOTICES.md"):
        assert os.path.exists(os.path.join(SKILL, p)), p
    assert "Apache License, Version 2.0" in read(os.path.join(SKILL, "THIRD_PARTY_NOTICES.md"))
    # every relative skill path the docs name exists
    for doc in ("SKILL.md", "references/inventory-and-drawio.md", "THIRD_PARTY_NOTICES.md"):
        for ref in re.findall(r"`((?:references|scripts|fixtures)/[\w.-]+)`", read(os.path.join(SKILL, doc))):
            assert os.path.exists(os.path.join(SKILL, ref)), f"{doc} names {ref}, which does not exist"


def test_no_private_context_and_no_portuguese_prose_in_the_skill():
    found = []
    for base, _, files in os.walk(SKILL):
        for a in files:
            p = os.path.join(base, a)
            for n, line in enumerate(read(p).splitlines(), 1):
                if PRIVATE.search(line):
                    found.append(f"{os.path.relpath(p, ROOT)}:{n}: {line.strip()[:80]}")
    assert not found, "\n".join(found)
    # The docs and pages are English; Portuguese words stay only as detection data in the scripts.
    pt = re.compile(r"[ãõç]|\b(?:não|você|diagrama|prancha|seta|rótulo|sonda|situação)\b", re.I)
    for rel in ("SKILL.md", "references/inventory-and-drawio.md", "references/template.html",
                "references/example.html", "fixtures/clean.drawio", "scripts/render.sh"):
        bad = [f"{rel}:{n}: {l.strip()[:80]}" for n, l in enumerate(read(os.path.join(SKILL, rel)).splitlines(), 1)
               if pt.search(l)]
        assert not bad, "\n".join(bad)


def test_render_without_chrome_exits_2_and_names_the_way_forward():
    # A PATH holding only what render.sh needs before it looks for Chrome: no browser on it.
    tmp = tempfile.mkdtemp(prefix="cloud-diagram-")
    try:
        env = _no_chrome_env(tmp)
        r = subprocess.run([BASH, RENDER, TEMPLATE, os.path.join(tmp, "out")], capture_output=True, text=True,
                           env=env, timeout=60)
        assert r.returncode == 2 and "no Chrome/Chromium on PATH" in r.stdout and "$CHROME" in r.stdout, (
            r.returncode, r.stdout + r.stderr)
        env["CHROME"] = os.path.join(tmp, "not-a-browser")
        r = subprocess.run([BASH, RENDER, TEMPLATE, os.path.join(tmp, "out")], capture_output=True, text=True,
                           env=env, timeout=60)
        assert r.returncode == 2 and "no Chrome at $CHROME=" in r.stdout, (r.returncode, r.stdout + r.stderr)
        assert not os.path.exists(os.path.join(tmp, "out")), "nothing is written before Chrome is found"
    finally:
        shutil.rmtree(tmp)


def _no_chrome_env(tmp):
    bin_dir = os.path.join(tmp, "bin")
    os.makedirs(bin_dir)
    os.symlink(shutil.which("dirname"), os.path.join(bin_dir, "dirname"))
    env = {k: v for k, v in os.environ.items() if k != "CHROME"}
    env["PATH"] = bin_dir
    return env


def test_render_help_exits_0_and_a_dash_page_goes_after_double_dash():
    tmp = tempfile.mkdtemp(prefix="cloud-diagram-")
    try:
        env = _no_chrome_env(tmp)         # help needs no browser and no tool on PATH
        for flag in ("-h", "--help"):
            r = subprocess.run([BASH, RENDER, flag], capture_output=True, text=True, env=env, timeout=60)
            assert r.returncode == 0 and "render.sh [--no-sandbox] [--] <page.html>" in r.stdout and "status=NO-DIAGRAM" in r.stdout, (
                flag, r.returncode, r.stdout + r.stderr)
            assert not r.stdout.lstrip().startswith("#!"), r.stdout
        shutil.copy(TEMPLATE, os.path.join(tmp, "-page.html"))
        # after --, -page.html is the page: render.sh goes on and stops only for the missing Chrome
        r = subprocess.run([BASH, RENDER, "--", "-page.html", "out"], capture_output=True, text=True, env=env,
                           cwd=tmp, timeout=60)
        assert r.returncode == 2 and "no Chrome/Chromium on PATH" in r.stdout, (r.returncode, r.stdout + r.stderr)
        r = subprocess.run([BASH, RENDER, "-page.html", "out"], capture_output=True, text=True, env=env,
                           cwd=tmp, timeout=60)
        assert r.returncode == 3 and "unknown option -page.html" in r.stderr, (r.returncode, r.stdout + r.stderr)
        r = subprocess.run([BASH, RENDER], capture_output=True, text=True, env=env, timeout=60)
        assert r.returncode == 3 and "usage: render.sh" in r.stderr, (r.returncode, r.stdout + r.stderr)
        assert not os.path.exists(os.path.join(tmp, "out")), "nothing is written before Chrome is found"
    finally:
        shutil.rmtree(tmp)


# ---------- render.sh (SKIP without Chrome) ----------

def _offline(tmp):
    """A Chrome wrapper that resolves no host name, so the suite never reaches the network."""
    real = chrome()
    if not real:
        raise Skip("no Chrome/Chromium on PATH and no $CHROME: render.sh not run")
    wrapper = os.path.join(tmp, "chrome-offline")
    with open(wrapper, "w", encoding="utf-8") as f:
        f.write(f'#!/bin/sh\nexec {shlex.quote(real)} --host-resolver-rules="MAP * ~NOTFOUND" "$@"\n')
    os.chmod(wrapper, 0o755)
    return {**os.environ, "CHROME": wrapper}


def _page(tmp, text, name):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(re.sub(r'<link[^>]*fonts\.googleapis\.com[^>]*>\n?', "", text))
    return p


def _render(env, page, out):
    return subprocess.run([BASH, RENDER, page, out, "1100"], capture_output=True, text=True, timeout=300, env=env)


def test_render_passes_the_template_and_fails_without_status():
    tmp = tempfile.mkdtemp(prefix="cloud-diagram-")
    try:
        env = _offline(tmp)
        r = _render(env, _page(tmp, read(TEMPLATE), "ok.html"), os.path.join(tmp, "ok"))
        assert r.returncode == 0 and "status=ok" in r.stdout and "problems=0" in r.stdout, r.stdout + r.stderr
        assert "diagrams=1 " in r.stdout, r.stdout
        none = re.sub(r"<div><dt>Status</dt><dd>[^<]*</dd></div>", "", read(TEMPLATE))
        r = _render(env, _page(tmp, none, "none.html"), os.path.join(tmp, "none"))
        assert r.returncode == 1 and "status=MISSING" in r.stdout and "no Status cell" in r.stdout, r.stdout
        current = swap(read(TEMPLATE), ("PROPOSED · sample drawing", "CURRENT"))
        r = _render(env, _page(tmp, current, "current.html"), os.path.join(tmp, "current"))
        assert r.returncode == 1 and "no YYYY-MM-DD date" in r.stdout, r.stdout
    finally:
        shutil.rmtree(tmp)


def test_render_passes_the_example_and_catches_a_crossing_arrow():
    tmp = tempfile.mkdtemp(prefix="cloud-diagram-")
    try:
        env = _offline(tmp)
        r = _render(env, _page(tmp, read(EXAMPLE), "example.html"), os.path.join(tmp, "example"))
        assert r.returncode == 0 and "status=ok" in r.stdout and "problems=0" in r.stdout, r.stdout + r.stderr
        assert "diagrams=1 " in r.stdout, r.stdout
        # a vertical arrow dropped through the request row crosses it away from any junction
        crossed = swap(read(EXAMPLE), ('<path class="ar" d="M779 474H609"',
                                       '<path class="ar" d="M474 150V420"/>\n<path class="ar" d="M779 474H609"'))
        r = _render(env, _page(tmp, crossed, "crossed.html"), os.path.join(tmp, "crossed"))
        assert r.returncode == 1 and "edge-crossing" in r.stdout, r.stdout
    finally:
        shutil.rmtree(tmp)


def test_render_fails_a_page_with_no_diagram_and_reads_a_dash_page_after_double_dash():
    tmp = tempfile.mkdtemp(prefix="cloud-diagram-")
    try:
        env = _offline(tmp)
        empty = _page(tmp, "<main><h1>Checkout</h1><p>No drawing on this page.</p></main>\n", "empty.html")
        r = _render(env, empty, os.path.join(tmp, "empty"))
        assert r.returncode == 1 and "diagrams=0 " in r.stdout and "status=NO-DIAGRAM" in r.stdout, r.stdout
        assert "status=ok" not in r.stdout and "no diagram found" in r.stdout, r.stdout
        # a relative page whose name starts with - and a relative output folder
        _page(tmp, read(TEMPLATE), "-ok.html")
        r = subprocess.run([BASH, RENDER, "--", "-ok.html", "dash", "1100"], capture_output=True, text=True,
                           timeout=300, env=env, cwd=tmp)
        assert r.returncode == 0 and "diagrams=1 " in r.stdout and "status=ok" in r.stdout, r.stdout + r.stderr
        assert os.path.exists(os.path.join(tmp, "dash", "shot-light-1100.png")), os.listdir(tmp)
    finally:
        shutil.rmtree(tmp)


# ---------- probe.js rules and render.py ----------

RENDER_PY = os.path.join(SKILL, "scripts", "render.py")
PROBE_JS = os.path.join(SKILL, "scripts", "probe.js")
RULES_DOC = os.path.join(SKILL, "references", "probe-rules.md")
FIXTURES = os.path.join(ROOT, "tests", "cloud_diagram_fixtures")
SHELL = os.path.join(FIXTURES, "shell.html")
BASE_SVG = os.path.join(FIXTURES, "base.svg")
PY = [sys.executable, "-I", "-B"]
ALL_RULES = ("label-overlap", "label-on-icon", "label-on-border", "step-on-label", "step-on-icon",
             "edge-through-label", "edge-through-icon", "edge-crossing", "edge-fork-unmarked",
             "edge-corridor", "edge-not-orthogonal", "steps-mismatch")


def render_module():
    import importlib.util
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("cloud_diagram_render", RENDER_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_render(*args, env=None, cwd=None):
    return subprocess.run([*PY, RENDER_PY, *args], capture_output=True, text=True, timeout=600, env=env, cwd=cwd)


def bare_env(tmp):
    """No browser anywhere: an empty PATH, an empty HOME and an empty Playwright cache."""
    for d in ("bin", "home", "cache"):
        os.makedirs(os.path.join(tmp, d), exist_ok=True)
    # Start from the real environment: Windows Python will not start without SystemRoot and friends.
    env = {**os.environ, "PATH": os.path.join(tmp, "bin"), "HOME": os.path.join(tmp, "home"),
           "USERPROFILE": os.path.join(tmp, "home"), "PLAYWRIGHT_BROWSERS_PATH": os.path.join(tmp, "cache")}
    env.pop("CHROME", None)
    return env


def fixture_page(tmp, name="page.html", diagrams=""):
    return _page(tmp, read(SHELL).replace("{{DIAGRAMS}}", diagrams), name)


def drawing(ident, extra="", steps=1, named=True):
    svg = read(BASE_SVG).replace("{{ID}}", ident).replace("{{EXTRA}}", extra)
    attr = f' data-diagram="{ident}"' if named else ""
    items = "".join(f"<li>step {n}</li>" for n in range(1, steps + 1))
    return f'{svg}<ol class="steps"{attr}>{items}</ol>\n'


def badge(n, cx, cy, cls="st", attr="data-s"):
    return (f'<g class="{cls}" {attr}="{n}"><circle cx="{cx}" cy="{cy}" r="11"/>'
            f'<text x="{cx}" y="{cy + 4}" text-anchor="middle">{n}</text></g>')


AZ = ('<rect class="grp g-az" x="40" y="60" width="240" height="200"/>'
      '<rect class="grp g-az" x="290" y="60" width="250" height="200"/>')
# name: (markup added to the clean drawing, drawing options, the rules the probe must report, as a set)
CASES = {
    "clean": ("", {}, set()),
    "label-overlap": ('<text x="70" y="170">Alpha2</text>', {}, {"label-overlap"}),
    "label-on-icon": ('<text x="62" y="130">Over</text>', {}, {"label-on-icon"}),
    "label-partly-on-icon": ('<text x="70" y="145">Partially over</text>', {}, {"label-on-icon"}),
    "label-on-border": ('<text x="8" y="200">Straddle</text>', {}, {"label-on-border"}),
    "band-label-may-cross": (AZ + '<rect class="grp g-svc" x="60" y="200" width="480" height="50"/>'
                             '<text x="285" y="230" text-anchor="middle">Service</text>', {}, set()),
    "plain-box-label-may-not": (AZ + '<rect x="60" y="200" width="480" height="50"/>'
                                '<text x="285" y="230" text-anchor="middle">Service</text>', {}, {"label-on-border"}),
    "step-on-label": (badge(2, 84, 165), {"steps": 2}, {"step-on-label"}),
    "step-on-icon": (badge(2, 84, 124), {"steps": 2}, {"step-on-icon"}),
    "steps-mismatch-more-items": ("", {"steps": 2}, {"steps-mismatch"}),
    "steps-mismatch-more-badges": (badge(2, 450, 60), {}, {"steps-mismatch"}),
    "steps-match": (badge(2, 450, 60), {"steps": 2}, set()),
    "edge-through-label": ('<path class="ar" d="M84 200V150"/>', {}, {"edge-through-label"}),
    "edge-through-icon": ('<path class="ar" d="M84 60V124"/>', {}, {"edge-through-icon"}),
    "edge-crossing": ('<path class="ar" d="M204 60V200"/>', {}, {"edge-crossing"}),
    "edge-crossing-at-a-junction": ('<path class="ar" d="M204 60V200"/><circle class="jn" cx="204" cy="124" r="3.5"/>',
                                    {}, set()),
    "edge-fork-unmarked": ('<path class="ar" d="M204 124V220"/>', {}, {"edge-fork-unmarked"}),
    "edge-fork-marked": ('<path class="ar" d="M204 124V220"/><circle class="jn" cx="204" cy="124" r="3.5"/>', {}, set()),
    "edge-corridor": ('<path class="ar" d="M150 125H250"/>', {}, {"edge-corridor"}),
    "edge-parallel-apart": ('<path class="ar" d="M150 134H250"/>', {}, set()),
    "edge-diagonal": ('<path class="ar" d="M120 200L220 240"/>', {}, {"edge-not-orthogonal"}),
    "edge-curve": ('<path class="ar" d="M120 200C150 200 180 220 220 240"/>', {}, {"edge-not-orthogonal"}),
    "edge-relative-diagonal": ('<path class="ar" d="m120 200l50 40"/>', {}, {"edge-not-orthogonal"}),
    "edge-relative-orthogonal": ('<path class="ar" d="m120 200h100v40"/>', {}, set()),
    "edge-on-a-rect": ('<rect class="edge" x="400" y="200" width="20" height="20"/>', {}, {"edge-not-orthogonal"}),
    "edge-line-and-polyline": ('<line class="ar" x1="400" y1="200" x2="500" y2="200"/>'
                               '<polyline class="ar" points="400 220 500 220 500 260"/>', {}, set()),
    "transform-moves-a-label-onto-an-icon": ('<g transform="translate(0,60)"><text x="60" y="60">Moved</text></g>',
                                             {}, {"label-on-icon"}),
    "transform-moves-a-label-clear": ('<g transform="translate(240,0)"><text x="0" y="230">Far</text></g>', {}, set()),
    "transform-moves-an-edge-through-an-icon": ('<g transform="translate(-24,0)"><path class="ar" d="M108 60V124"/></g>',
                                                {}, {"edge-through-icon"}),
    "vocabulary-edge-junction-step": ('<path class="edge" d="M204 60V200"/><circle class="junction" cx="204" cy="124" r="3.5"/>'
                                      + badge(2, 450, 60, "step", "data-step"), {"steps": 2}, set()),
    "vocabulary-step-on-label": (badge(2, 84, 165, "step", "data-step"), {"steps": 2}, {"step-on-label"}),
    "vocabulary-edge-crossing": ('<path class="edge" d="M204 60V200"/>', {}, {"edge-crossing"}),
    "list-follows-its-own-drawing-a": ("", {"named": False}, set()),
    "list-follows-its-own-drawing-b": (badge(2, 450, 60), {"steps": 2, "named": False}, set()),
}
_PROBED = {}


def probed():
    """Every case on one page, rendered once: {case: (set of rules, problems)} from the probe's own JSON."""
    if _PROBED:
        return _PROBED
    tmp = tempfile.mkdtemp(prefix="cloud-diagram-")
    env = _offline(tmp)
    ids = {name: f"c{n}" for n, name in enumerate(CASES)}
    page = fixture_page(tmp, diagrams="".join(drawing(ids[n], e, **kw) for n, (e, kw, _) in CASES.items()))
    r = run_render(page, "--out", os.path.join(tmp, "out"), "--themes", "light", "--widths", "1100",
                   "--no-shots", env=env)
    with open(os.path.join(tmp, "out", "probe-light-1100-en.json"), encoding="utf-8") as f:
        result = json.load(f)
    assert "error" not in result, (result, r.stdout + r.stderr)
    by_id = {d["id"]: d for d in result["diagrams"]}
    assert set(by_id) == set(ids.values()), (sorted(by_id), r.stdout)
    for name, ident in ids.items():
        _PROBED[name] = ({p[0] for p in by_id[ident]["problems"]}, by_id[ident]["problems"])
    return _PROBED


def expect(*names):
    got = probed()
    wrong = {n: (CASES[n][2], got[n][0], got[n][1]) for n in names if got[n][0] != CASES[n][2]}
    assert not wrong, "\n".join(f"{n}: wanted {w}, got {g}: {p}" for n, (w, g, p) in wrong.items())


def test_probe_a_clean_drawing_reports_nothing():
    expect("clean", "edge-parallel-apart", "edge-relative-orthogonal", "edge-line-and-polyline")


def test_probe_label_rules():
    expect("label-overlap", "label-on-icon", "label-partly-on-icon", "label-on-border")


def test_probe_label_across_a_group_line_is_allowed_only_inside_a_band():
    expect("band-label-may-cross", "plain-box-label-may-not")


def test_probe_badge_rules():
    expect("step-on-label", "step-on-icon")


def test_probe_steps_match_the_list_of_their_own_drawing():
    expect("steps-mismatch-more-items", "steps-mismatch-more-badges", "steps-match",
           "list-follows-its-own-drawing-a", "list-follows-its-own-drawing-b")


def test_probe_edge_through_a_label_or_an_icon():
    expect("edge-through-label", "edge-through-icon")


def test_probe_edge_crossing_fork_and_corridor():
    expect("edge-crossing", "edge-crossing-at-a-junction", "edge-fork-unmarked", "edge-fork-marked",
           "edge-corridor", "edge-parallel-apart")


def test_probe_edges_are_orthogonal_and_may_be_relative_lines_or_polylines():
    expect("edge-diagonal", "edge-curve", "edge-relative-diagonal", "edge-relative-orthogonal",
           "edge-on-a-rect", "edge-line-and-polyline")


def test_probe_measures_shapes_where_a_transform_draws_them():
    expect("transform-moves-a-label-onto-an-icon", "transform-moves-a-label-clear",
           "transform-moves-an-edge-through-an-icon")


def test_probe_reads_both_class_vocabularies():
    expect("vocabulary-edge-junction-step", "vocabulary-step-on-label", "vocabulary-edge-crossing")


def test_probe_rules_in_the_cases_cover_every_documented_rule():
    # a rule added to the probe with no case here fails this, so the table above cannot fall behind
    covered = set().union(*(c[2] for c in CASES.values()))
    assert covered == set(ALL_RULES), set(ALL_RULES) ^ covered
    assert set(re.findall(r'report\("([a-z-]+)"', read(PROBE_JS))) == set(ALL_RULES)


# ---------- render.py without a browser ----------

def test_render_py_help_exits_0_with_no_browser_and_lists_the_exit_codes():
    tmp = tempfile.mkdtemp(prefix="cloud-diagram-")
    try:
        for flag in ("-h", "--help"):
            r = run_render(flag, env=bare_env(tmp))
            assert r.returncode == 0, (flag, r.stdout + r.stderr)
            for word in ("--themes", "--widths", "--langs", "--browser", "--json", "--no-shots", "--out"):
                assert word in r.stdout, (word, r.stdout)
            assert re.search(r"(?m)^\s*3\b.*no browser", r.stdout), r.stdout
    finally:
        shutil.rmtree(tmp)


def test_render_py_with_no_browser_exits_3_says_so_and_writes_nothing():
    tmp = tempfile.mkdtemp(prefix="cloud-diagram-")
    try:
        env = bare_env(tmp)
        page = fixture_page(tmp, diagrams=drawing("d"))
        out = os.path.join(tmp, "out")
        r = run_render(page, "--out", out, env=env)
        assert r.returncode == 3 and "no browser found" in r.stderr and "unverified" in r.stderr, (
            r.returncode, r.stdout + r.stderr)
        r = run_render(page, "--out", out, "--browser", os.path.join(tmp, "missing"), env=env)
        assert r.returncode == 3 and "no browser found" in r.stderr, (r.returncode, r.stdout + r.stderr)
        assert not os.path.exists(out), "nothing is written before a browser is found"
    finally:
        shutil.rmtree(tmp)


def test_render_py_usage_errors_exit_2_before_it_looks_for_a_browser():
    tmp = tempfile.mkdtemp(prefix="cloud-diagram-")
    try:
        env = bare_env(tmp)
        page = fixture_page(tmp, diagrams=drawing("d"))
        for args, word in (([os.path.join(tmp, "missing.html")], "no such page"),
                           ([page, "--widths", "wide"], "--widths"),
                           ([page, "--widths", ""], "--widths"),
                           ([page, "--themes", "sepia"], "--themes"),
                           ([page, "--langs", "EN"], "--langs"),
                           ([page, "--nope"], "--nope"),
                           ([], "page")):
            r = run_render(*args, env=env)
            assert r.returncode == 2 and word in r.stderr, (args, r.returncode, r.stdout + r.stderr)
    finally:
        shutil.rmtree(tmp)


def _sandbox_stub(tmp, refuse_screenshot=False):
    """A browser that refuses to start sandboxed, and logs every call; with --no-sandbox it answers.

    refuse_screenshot makes --screenshot fail for a reason that is not the sandbox."""
    log = os.path.join(tmp, "calls.log")
    probe = json.dumps({"lang": "en", "fonts": ["x"], "status": {"ok": True}, "viewport": "1100x4200",
                        "page_scrolls_sideways": False, "past_viewport": [],
                        "diagrams": [{"id": "d", "problems": [], "texts": 1, "icons": 1, "edges": 1}]})
    path = os.path.join(tmp, "stub-browser")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"#!{sys.executable}\nimport sys\n"
                f"open({log!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
                + ("if any(a.startswith('--screenshot') for a in sys.argv):\n"
                   "    sys.stderr.write('Failed to write the screenshot\\n')\n"
                   "    sys.exit(1)\n" if refuse_screenshot else "") +
                "if '--no-sandbox' not in sys.argv:\n"
                "    sys.stderr.write('No usable sandbox! Update your kernel\\n')\n"
                "    sys.exit(1)\n"
                "if '--dump-dom' in sys.argv:\n"
                f"    print('<pre id=\"probe-out\" hidden>' + {probe!r} + '</pre>')\n")
    os.chmod(path, 0o755)
    return path, log


def test_render_py_sandbox_failure_exits_3_naming_the_flag_and_never_retries_unsandboxed():
    tmp = tempfile.mkdtemp(prefix="cloud-diagram-")
    try:
        stub, log = _sandbox_stub(tmp)
        page = fixture_page(tmp, diagrams=drawing("d"))
        r = run_render(page, "--out", os.path.join(tmp, "out"), "--browser", stub, "--themes", "light",
                       "--widths", "1100", "--langs", "en", env=bare_env(tmp))
        assert r.returncode == 3 and "--no-sandbox" in r.stderr and "sandbox" in r.stderr, (
            r.returncode, r.stdout + r.stderr)
        assert len(r.stderr.strip().splitlines()) == 1, r.stderr
        assert "--no-sandbox" not in read(log), "the browser was started unsandboxed without being asked"
    finally:
        shutil.rmtree(tmp)


def test_render_py_fails_when_the_screenshot_run_fails_for_a_reason_other_than_the_sandbox():
    tmp = tempfile.mkdtemp(prefix="cloud-diagram-")
    try:
        stub, _ = _sandbox_stub(tmp, refuse_screenshot=True)
        page = fixture_page(tmp, diagrams=drawing("d"))
        r = run_render(page, "--out", os.path.join(tmp, "out"), "--browser", stub, "--themes", "light",
                       "--widths", "1100", "--langs", "en", "--no-sandbox", env=bare_env(tmp))
        out = r.stdout + r.stderr
        assert r.returncode == 1 and "FAIL" in out and "Failed to write the screenshot" in out, (
            r.returncode, out)
    finally:
        shutil.rmtree(tmp)


def test_render_sh_is_sandboxed_by_default_refuses_without_the_flag_and_runs_unsandboxed_with_it():
    if os.name == "nt":
        raise Skip("bash and a stub Chrome cannot run on Windows")
    tmp = tempfile.mkdtemp(prefix="cloud-diagram-")
    try:
        stub, log = _sandbox_stub(tmp)
        page = fixture_page(tmp, diagrams=drawing("d"))
        env = {**os.environ, "CHROME": stub}
        r = subprocess.run([BASH, RENDER, page, os.path.join(tmp, "out"), "1100"], capture_output=True,
                           text=True, timeout=120, env=env)
        assert r.returncode == 4 and "--no-sandbox" in r.stderr, (r.returncode, r.stdout + r.stderr)
        assert "--no-sandbox" not in read(log), "render.sh started Chromium unsandboxed without being asked"
        os.remove(log)
        r = subprocess.run([BASH, RENDER, "--no-sandbox", page, os.path.join(tmp, "out2"), "1100"],
                           capture_output=True, text=True, timeout=120, env=env)
        assert r.returncode == 0 and "WARN sandbox" in r.stderr, (r.returncode, r.stdout + r.stderr)
        calls = read(log).splitlines()
        # sandboxed first, then the same run unsandboxed: screenshot and dump-dom, light and dark
        assert sum("--no-sandbox" in c for c in calls) == 4 and len(calls) == 8, calls
        assert all("--no-sandbox" not in c for c in calls[0::2]), calls
    finally:
        shutil.rmtree(tmp)


def test_render_py_no_sandbox_flag_runs_unsandboxed_and_warns_for_each_run():
    tmp = tempfile.mkdtemp(prefix="cloud-diagram-")
    try:
        stub, log = _sandbox_stub(tmp)
        page = fixture_page(tmp, diagrams=drawing("d"))
        r = run_render(page, "--out", os.path.join(tmp, "out"), "--browser", stub, "--themes", "light",
                       "--widths", "1100", "--langs", "en", "--no-sandbox", env=bare_env(tmp))
        out = r.stdout + r.stderr
        assert r.returncode == 0, (r.returncode, out)
        warns = [line for line in r.stdout.splitlines() if line.startswith("WARN sandbox")]
        assert len(warns) == 1 and "screenshot" in warns[0] and "dump-dom" in warns[0], out
        calls = read(log).splitlines()
        # each run is tried sandboxed first, then repeated unsandboxed: screenshot and dump-dom
        assert sum("--no-sandbox" in c for c in calls) == 2, calls
    finally:
        shutil.rmtree(tmp)


def test_render_py_finds_the_browser_in_order_and_prefers_the_newest_playwright_build():
    mod = render_module()
    tmp = tempfile.mkdtemp(prefix="cloud-diagram-")
    try:
        def exe(*parts):
            p = os.path.join(tmp, *parts)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write("#!/bin/sh\n")
            os.chmod(p, 0o755)
            return p
        bare = {"PATH": os.path.join(tmp, "bin"), "PLAYWRIGHT_BROWSERS_PATH": os.path.join(tmp, "pw")}
        assert mod.find_browser(None, env=bare, home=tmp) is None
        old = exe("pw", "chromium-1129", "chrome-linux", "chrome")
        new = exe("pw", "chromium-1134", "chrome-linux64", "chrome")
        exe("pw", "chromium-999", "chrome-linux", "chrome")
        assert mod.find_browser(None, env=bare, home=tmp) == new, "revision 1134 beats 1129 and 999 (numeric, not text)"
        assert old != new
        on_path = exe("bin", "chromium")
        assert mod.find_browser(None, env=bare, home=tmp) == on_path, "PATH comes before the Playwright cache"
        env_chrome = exe("env", "chrome")
        assert mod.find_browser(None, env={**bare, "CHROME": env_chrome}, home=tmp) == env_chrome
        explicit = exe("explicit", "chrome")
        assert mod.find_browser(explicit, env={**bare, "CHROME": env_chrome}, home=tmp) == explicit
        # a path that was asked for and is not executable is an answer, not a reason to look elsewhere
        assert mod.find_browser(os.path.join(tmp, "nope"), env=bare, home=tmp) is None
        assert mod.find_browser(None, env={**bare, "CHROME": os.path.join(tmp, "nope")}, home=tmp) is None
    finally:
        shutil.rmtree(tmp)


def test_render_py_judge_turns_probe_output_into_fail_and_warn_lines():
    mod = render_module()
    good = {"lang": "en", "page_scrolls_sideways": False, "past_viewport": [], "fonts": ["Inter"],
            "status": {"ok": True, "text": "PROPOSED"},
            "diagrams": [{"id": "d", "texts": 1, "icons": 1, "edges": 1, "problems": []}]}
    assert mod.judge(good, "en") == ([], [])
    bad = dict(good, diagrams=[{"id": "d", "problems": [["edge-crossing", "M0 0H9", "M4 -2V2"]]}])
    assert mod.judge(bad, "en")[0] == [("edge-crossing", "d: M0 0H9 | M4 -2V2")]
    assert mod.judge({"error": "boom\nstack"})[0] == [("probe-did-not-run", "boom")]
    assert [r for r, _ in mod.judge(dict(good, diagrams=[]), "en")[0]] == ["no-diagram"]
    side = mod.judge(dict(good, page_scrolls_sideways=True, past_viewport=["div.wide"]), "en")[0]
    assert side == [("page-scrolls-sideways", "body wider than the window: div.wide")], side
    # the status of the stamp is a gate, and it names the reason the probe gave
    nostat = mod.judge(dict(good, status={"ok": False, "text": "", "reason": "stamp has no Status cell"}), "en")[0]
    assert nostat == [("status", "stamp has no Status cell")], nostat
    # a page that kept another language than the one asked for was not measured in it
    assert [r for r, _ in mod.judge(dict(good, lang="en"), "pt")[0]] == ["language-not-applied"]
    assert mod.judge(dict(good, lang="pt-BR"), "pt")[0] == []
    assert mod.judge({k: v for k, v in good.items() if k != "lang"}, "en")[0] == [], "no lang attribute reads as en"
    warns = mod.judge(dict(good, fonts=[], unsandboxed=["screenshot", "dump-dom"]), "en")
    assert warns[0] == [] and [r for r, _ in warns[1]] == ["fonts", "sandbox"], warns


def test_render_py_prepare_sets_the_theme_wraps_a_fragment_and_injects_the_probe_last():
    mod = render_module()
    base = tempfile.mkdtemp(prefix="cloud-diagram-")   # absolute on every OS, which as_uri() needs
    try:
        page = mod.prepare('<html lang="en" data-theme="dark" class="x"><head></head><body><p>hi</p></body></html>',
                           "light", base, "/*probe*/")
        uri = pathlib.Path(base).as_uri()
    finally:
        shutil.rmtree(base)
    assert page.count("data-theme=") == 1 and 'data-theme="light"' in page and 'lang="en"' in page, page
    assert f'<base href="{uri}/">' in page, page
    assert page.index("/*probe*/") > page.index("<p>hi</p>") and page.rstrip().endswith("</body></html>"), page
    frag = mod.prepare("<svg></svg>", "dark", None, "/*probe*/")
    assert frag.startswith("<!doctype html>") and 'data-theme="dark"' in frag and "<svg></svg>" in frag, frag
    assert "<base" not in frag and frag.rstrip().endswith("</html>") and "/*probe*/" in frag, frag


def test_render_py_languages_come_from_the_i18n_block_english_first():
    mod = render_module()
    block = '<script type="application/json" id="i18n">{"pt": {}, "es": {}, "en": {}}</script>'
    assert mod.languages(block) == ["en", "es", "pt"]
    assert mod.languages("<p>none</p>") == ["en"]
    assert mod.languages('<script id="i18n">{not json</script>') == ["en"]
    # valid JSON that is not an object has no language keys: fall back, never crash
    assert mod.languages('<script id="i18n">["en", "pt"]</script>') == ["en"]
    assert mod.languages('<script id="i18n">"en"</script>') == ["en"]


def test_render_py_is_stdlib_only_states_its_encoding_and_leaves_no_bytecode():
    import ast
    import test_test_encoding
    src = read(RENDER_PY)
    tree = ast.parse(src)
    mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert mods and mods <= set(sys.stdlib_module_names), sorted(mods - set(sys.stdlib_module_names))
    assert not test_test_encoding.missing_encoding(tree), "render.py has a text-mode file call with no encoding="
    assert src.startswith("#!/usr/bin/env python3")


def test_probe_rules_reference_documents_every_rule_and_exit_code():
    doc = read(RULES_DOC)
    rules = set(re.findall(r'report\("([a-z-]+)"', read(PROBE_JS)))
    rules |= set(re.findall(r'\("([a-z-]+)",', read(RENDER_PY)))   # the page rules render.py adds
    assert set(ALL_RULES) < rules, rules
    for rule in sorted(rules):
        assert f"`{rule}`" in doc, f"references/probe-rules.md does not document {rule}"
    for code, meaning in (("0", "ok"), ("1", "fail"), ("2", "usage"), ("3", "no browser"), ("4", "crash")):
        assert re.search(rf"(?mi)^\|\s*`?{code}`?\s*\|.*{meaning}", doc), f"exit code {code} ({meaning}) is not in the table"
    md = read(os.path.join(SKILL, "SKILL.md"))
    assert "scripts/render.py" in md and "references/probe-rules.md" in md, "SKILL.md points at neither"
    pt = re.compile(r"[ãõç]|\b(?:não|você|diagrama|prancha|seta|rótulo|sonda|situação)\b", re.I)
    for p in (RENDER_PY, RULES_DOC, SHELL, BASE_SVG):
        bad = [f"{os.path.basename(p)}:{n}: {l.strip()[:80]}" for n, l in enumerate(read(p).splitlines(), 1)
               if pt.search(l) or PRIVATE.search(l)]
        assert not bad, "\n".join(bad)


# ---------- render.py with a browser (SKIP without one) ----------

def test_render_py_passes_the_template_and_the_example_in_every_theme_and_width():
    tmp = tempfile.mkdtemp(prefix="cloud-diagram-")
    try:
        env = _offline(tmp)
        for name, src in (("template", TEMPLATE), ("example", EXAMPLE)):
            out = os.path.join(tmp, name)
            r = run_render(_page(tmp, read(src), name + ".html"), "--out", out, env=env)
            assert r.returncode == 0 and "FAIL" not in r.stdout, r.stdout + r.stderr
            for theme in ("dark", "light"):
                for width in (1100, 400):
                    assert f"ok [{theme} {width}px en]" in r.stdout, r.stdout
                    assert os.path.getsize(os.path.join(out, f"shot-{theme}-{width}-en.png")) > 1000
                    assert os.path.exists(os.path.join(out, f"probe-{theme}-{width}-en.json"))
    finally:
        shutil.rmtree(tmp)


def test_render_py_fails_one_line_per_problem_and_prints_json_on_request():
    tmp = tempfile.mkdtemp(prefix="cloud-diagram-")
    try:
        env = _offline(tmp)
        page = fixture_page(tmp, diagrams=drawing("d", '<path class="ar" d="M204 60V200"/>'))
        r = run_render(page, "--themes", "light", "--widths", "1100", "--no-shots", "--json", "--out",
                       os.path.join(tmp, "out"), env=env)
        assert r.returncode == 1, r.stdout + r.stderr
        assert "FAIL edge-crossing: [light 1100px en] d: M108 124H300 | M204 60V200" in r.stdout \
            or "FAIL edge-crossing: [light 1100px en] d: M204 60V200 | M108 124H300" in r.stdout, r.stdout
        assert "ok [" not in r.stdout and not glob_shots(os.path.join(tmp, "out")), r.stdout
        payload = json.loads(r.stdout[r.stdout.index("{"):r.stdout.rindex("}") + 1])
        assert payload["light-1100-en"]["diagrams"][0]["problems"][0][0] == "edge-crossing", payload
    finally:
        shutil.rmtree(tmp)


def glob_shots(out):
    return [f for f in os.listdir(out) if f.endswith(".png")]


def test_render_py_page_rules_no_diagram_no_status_and_sideways_scroll():
    tmp = tempfile.mkdtemp(prefix="cloud-diagram-")
    try:
        env = _offline(tmp)
        flags = ("--themes", "light", "--widths", "1100", "--no-shots")
        r = run_render(_page(tmp, "<main><h1>No drawing</h1></main>", "none.html"), *flags, env=env)
        assert r.returncode == 1 and "FAIL no-diagram:" in r.stdout, r.stdout + r.stderr
        nostat = read(SHELL).replace("<dt>Status</dt><dd>PROPOSED</dd>", "<dt>Region</dt><dd>eu</dd>")
        page = _page(tmp, nostat.replace("{{DIAGRAMS}}", drawing("d")), "nostat.html")
        r = run_render(page, *flags, env=env)
        assert r.returncode == 1 and "FAIL status: [light 1100px en] stamp has no Status cell" in r.stdout, r.stdout
        wide = fixture_page(tmp, "wide.html", '<div style="width:1500px;height:10px">wide</div>' + drawing("d"))
        r = run_render(wide, *flags, env=env)
        assert r.returncode == 1 and "FAIL page-scrolls-sideways:" in r.stdout and "div" in r.stdout, r.stdout
    finally:
        shutil.rmtree(tmp)


def test_render_py_measures_every_language_the_page_carries_and_fails_one_that_ignores_the_hash():
    tmp = tempfile.mkdtemp(prefix="cloud-diagram-")
    try:
        env = _offline(tmp)
        flags = ("--themes", "light", "--widths", "1100", "--no-shots")
        i18n = '<script type="application/json" id="i18n">{"en": {}, "pt": {}}</script>\n'
        switch = '<script>if(location.hash==="#pt")document.documentElement.lang="pt"</script>\n'
        body = drawing("d")
        ok = fixture_page(tmp, "ok.html", i18n + switch + body)
        r = run_render(ok, *flags, env=env)
        assert r.returncode == 0 and "ok [light 1100px en]" in r.stdout and "ok [light 1100px pt]" in r.stdout, r.stdout
        ignores = fixture_page(tmp, "ignores.html", i18n + body)
        r = run_render(ignores, *flags, env=env)
        assert r.returncode == 1 and "FAIL language-not-applied: [light 1100px pt]" in r.stdout, r.stdout
        assert "ok [light 1100px en]" in r.stdout, "English still passes"
        only = run_render(ignores, *flags, "--langs", "en", env=env)
        assert only.returncode == 0 and "pt]" not in only.stdout, only.stdout
    finally:
        shutil.rmtree(tmp)


def test_render_py_default_output_is_a_fresh_temporary_directory_not_the_pages_folder():
    # with no --out the run names a fresh temporary directory and leaves the page's folder as it was
    tmp = tempfile.mkdtemp(prefix="cloud-diagram-")
    try:
        env = _offline(tmp)
        page = fixture_page(tmp, diagrams=drawing("d"))
        before = set(os.listdir(tmp))
        r = run_render(page, "--themes", "light", "--widths", "1100", "--no-shots", env=env)
        assert r.returncode == 0, r.stdout + r.stderr
        assert set(os.listdir(tmp)) == before, "a run with no --out must not write beside the page"
        named = re.search(r"in (\S+)\s*$", r.stdout.strip())
        assert named and os.path.isdir(named.group(1)), r.stdout
        shutil.rmtree(named.group(1))
    finally:
        shutil.rmtree(tmp)


def test_zz_no_pycache_in_the_skill():
    dirty = [b for b, _, _ in os.walk(SKILL) if os.path.basename(b) == "__pycache__"]
    assert not dirty, dirty


if __name__ == "__main__":
    failures = skipped = passed = 0
    # One temporary directory per run: every mkdtemp above, and the TMPDIR of every child (Chrome's
    # profile included), lands in it. Without it each run left a directory per case in /tmp.
    RUN_TMP = tempfile.mkdtemp(prefix="cloud-diagram-run-")
    tempfile.tempdir = os.environ["TMPDIR"] = RUN_TMP
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                try:
                    fn()
                    passed += 1
                    print("ok  ", name)
                except Skip as e:
                    skipped += 1
                    print("SKIP", name, "-", e)
                except Exception as e:  # an AssertionError and an unexpected error both fail
                    failures += 1
                    print("FAIL", name, "\n     ", f"{type(e).__name__}: {e}"[:1500].replace("\n", "\n      "))
    finally:
        shutil.rmtree(RUN_TMP, ignore_errors=True)
    if os.path.exists(RUN_TMP):
        failures += 1
        print("FAIL nothing is left in the temporary directory after the run:", RUN_TMP)
    else:
        passed += 1
        print("ok   nothing is left in the temporary directory after the run")
    print(f"{passed} passed, {skipped} skipped, {failures} failed")
    sys.exit(1 if failures else 0)
