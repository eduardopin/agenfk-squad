#!/usr/bin/env python3
"""The presentation skill ships an unbranded template and an export that refuses a bad deck.

Self-checking: the exit code is the verdict. The export tests run headless
Chrome in a temporary directory; without Chrome each of them prints SKIP and
the run still exits 0. Nothing is written to the repository.

    python3 tests/test_presentation.py

The browser comes from $CHROME when it is set (an empty or missing path counts
as no Chrome), otherwise from the same names the export script looks for. To
also check the skill for names that must not be published (a project, a
customer, a person), list them in SQUAD_PRIVATE_TERMS, comma-separated. The
list lives in your environment, never in the repository.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "presentation")
TEMPLATE = os.path.join(SKILL, "templates", "deck.html")
SCRIPT = os.path.join(SKILL, "scripts", "export-deck.sh")
MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

_terms = [re.escape(t.strip()) for t in os.environ.get("SQUAD_PRIVATE_TERMS", "").split(",") if t.strip()]
PRIVATE = re.compile("|".join([r"/home/[a-z]+/", r"/Users/[A-Za-z]+/"] + _terms), re.I)
# Names from the private squad this skill was ported from; none of them exists here.
UNPORTED = re.compile(r"apresentacao|design-referencia|varredura|\bescrita\b|financas|verificador"
                      r"|linguagem-do-deck|graficos-e-roteiros|referencias/", re.I)


class Skip(Exception):
    """The test did not run; the runner prints SKIP instead of ok."""


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def strip_comments(html):
    return re.sub(r"<!--.*?-->", "", html, flags=re.S)


def css():
    return re.search(r"<style>(.*?)</style>", read(TEMPLATE), re.S).group(1)


def slides(html):
    return re.findall(r'<section[^>]*class="slide[^"]*"[^>]*\bid="([^"]+)"', html)


def chrome():
    if "CHROME" in os.environ:
        c = os.environ["CHROME"]
        return c if c and os.access(c, os.X_OK) else None
    for c in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        p = shutil.which(c)
        if p:
            return p
    return MAC_CHROME if os.access(MAC_CHROME, os.X_OK) else None


def export(*args, env=None):
    e = dict(os.environ)
    e.pop("CHROME", None)
    e.update(env or {})
    return subprocess.run(["bash", SCRIPT, *args], capture_output=True, text=True, env=e, timeout=300)


def pages(pdf):
    with open(pdf, "rb") as f:
        return len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", f.read()))


def variant(tmp, name, change):
    """A copy of the template with one change, for the bad cases. The change must apply."""
    html = read(TEMPLATE)
    changed = change(html)
    assert changed != html, f"{name}: the change did not apply to the template"
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(changed)
    return path


# ---------- the template and the skill, no Chrome ----------

def test_nothing_private_or_unported_in_the_skill():
    found = []
    for base, _, files in os.walk(SKILL):
        for name in files:
            p = os.path.join(base, name)
            for n, line in enumerate(read(p).splitlines(), 1):
                if PRIVATE.search(line) or UNPORTED.search(line):
                    found.append(f"{os.path.relpath(p, ROOT)}:{n}: {line.strip()[:80]}")
    assert not found, "private or unported name in the skill:\n" + "\n".join(found)


def test_template_has_no_embedded_logo():
    html = strip_comments(read(TEMPLATE))
    assert "<symbol" not in html, "the template carries no logo; the brand comes from the project"
    assert "<path" not in html, "vector drawing embedded in the template"
    assert not [a for a in os.listdir(os.path.join(SKILL, "templates")) if a.endswith(".svg")]


def test_template_is_english():
    html = read(TEMPLATE)
    assert '<html lang="en">' in html
    for word in ("Ir para o slide", "Capa", "Confidencial", "Ilustrativo", "Prévia", "Existe hoje"):
        assert word not in html, f"Portuguese left in the template: {word!r}"


def test_template_has_render_mode_tokens_and_dark_theme():
    style = css()
    assert "html.render .slide{" in style and "html.render.transparent" in style
    assert re.search(r"@page\{size:13\.333in 7\.5in", style), "16:9 page"
    root = "".join(re.findall(r"(?m)^:root\{(.*?)^\}", style, re.S))
    for token in ("--color-bg", "--color-ink", "--color-accent", "--chip-now-ink"):
        assert token + ":" in root, f"{token} outside :root"
    assert "@media (prefers-color-scheme:dark)" in style
    dark = re.search(r':root\[data-theme="dark"\]\{(.*?)\}', style, re.S)
    assert dark and "--color-positive:" in dark.group(1), "status in dark mode through data-theme"
    ds_dark = re.search(r':root\[data-theme="dark"\] \{(.*?)\}', style, re.S)
    assert ds_dark and "--ds-bg:" in ds_dark.group(1), "design system in dark mode through data-theme"
    for chip in ("now", "next", "partner"):
        assert f".chip.{chip}" in style, f"chip {chip} has no style"


def test_template_tokens_are_opaque():
    tokens = re.findall(r"(--(?:p|color|chip)-[\w-]+):([^;]+);", css())
    assert len(tokens) > 20, "token pattern no longer matches the template"
    for name, value in tokens:
        assert not re.search(r"rgba|hsla|transparent|#[0-9a-f]{8}\b", value, re.I), \
            f"{name} is translucent: it breaks on a transparent PNG"


def test_skill_documents_the_rules_and_its_place_in_the_squad():
    md = read(os.path.join(SKILL, "SKILL.md"))
    front = md.split("---")[1]
    assert re.findall(r"(?m)^(\w+):", front) == ["name", "description"]
    assert re.search(r"(?m)^name: presentation$", front)
    for term in ("AskUserQuestion", "1440x830", "html.render", "chip now", "chip next",
                 "chip partner", "`writing`", "`frontend`", "`finops`", "`verifier`",
                 "## 0. The project rules", "## Origin", "references/deck-language.md",
                 "references/charts-and-outlines.md", "design-reference/scripts/sweep.py",
                 "--measure"):
        assert term in md, f"SKILL.md lacks {term!r}"
    for ref in ("deck-language.md", "charts-and-outlines.md"):
        assert os.path.exists(os.path.join(SKILL, "references", ref)), ref


def test_template_wears_the_sweep_contract():
    style = css()
    ds = ("--ds-bg", "--ds-surface", "--ds-fg", "--ds-fg-2", "--ds-accent", "--ds-border",
          "--ds-font-display", "--ds-font-body", "--ds-font-mono")
    default = re.search(r":root \{(.*?)\}", style, re.S).group(1)
    for v in ds:
        assert v + ":" in default, f"{v} has no neutral default"
        assert f"var({v})" in style, f"{v} is read by no semantic token"
    assert ':root[data-theme="dark"] {' in style, "dark --ds-* block"
    # the semantics read --ds-*, not raw hex: replacing the block re-dresses the whole deck
    for sem in ("--color-bg", "--color-ink", "--color-accent", "--font-text", "--font-title"):
        assert re.search(sem + r":var\(--ds-", style), f"{sem} does not read --ds-*"
    external = re.findall(r'(?:src|href)="(https?://[^"]+)"', strip_comments(read(TEMPLATE)))
    assert not external, f"template loads an external resource outside a comment: {external}"


def test_template_navigates_sideways_with_two_rails():
    html, style = read(TEMPLATE), css()
    assert "translateX(" in style and "--current" in style, "slide change by horizontal shift"
    assert re.search(r"prefers-reduced-motion:reduce\)\{\.deck\{transition:none", style), "no reduced motion"
    assert 'class="dots rail"' in html and 'class="dots footer-dots"' in html, "both rails"
    assert re.search(r"\.dots button\{[^}]*width:24px; height:24px", style), "24px target"
    assert "html.render .rail,html.render .navbar{display:none" in style


# ---------- script gates that need no Chrome ----------

def test_script_refuses_a_deck_without_the_template_contract():
    tmp = tempfile.mkdtemp(prefix="presentation-")
    try:
        out = os.path.join(tmp, "x.pdf")
        no_render = variant(tmp, "no-render.html", lambda h: h.replace("html.render", "html.other"))
        r = export(no_render, "--pdf", out)
        assert r.returncode == 2 and "html.render" in r.stderr, r.stderr
        no_slide = variant(tmp, "no-slide.html", lambda h: h.replace('class="slide', 'class="sheet'))
        r = export(no_slide, "--pdf", out)
        assert r.returncode == 2 and "section" in r.stderr, r.stderr
        no_id = variant(tmp, "no-id.html", lambda h: h.replace(' id="s3"', "", 1))
        r = export(no_id, "--pdf", out)
        assert r.returncode == 2 and "has no id" in r.stderr, r.stderr
        r = export(TEMPLATE)
        assert r.returncode == 2 and "nothing to do" in r.stderr, r.stderr
        r = export(TEMPLATE, "--measure", "--theme", "sepia")
        assert r.returncode == 2 and "--theme" in r.stderr, r.stderr
        assert not os.path.exists(out)
    finally:
        shutil.rmtree(tmp)


def test_missing_chrome_fails_with_a_clear_message():
    r = export(TEMPLATE, "--pdf", "/does/not/exist/x.pdf", "--chrome", "/does/not/exist/chrome")
    assert r.returncode == 2 and "Chrome not found at --chrome" in r.stderr, r.stderr
    r = export(TEMPLATE, "--measure", env={"CHROME": "/does/not/exist/chrome"})
    assert r.returncode == 2 and "$CHROME" in r.stderr, r.stderr
    if os.access(MAC_CHROME, os.X_OK):
        return  # the macOS fallback would find it; the PATH case cannot be shown here
    # A PATH with only the tools the script needs before it looks for Chrome.
    tmp = tempfile.mkdtemp(prefix="presentation-")
    try:
        for tool in ("bash", "grep", "sed", "wc", "tr"):
            os.symlink(shutil.which(tool), os.path.join(tmp, tool))
        r = export(TEMPLATE, "--measure", env={"PATH": tmp})
        assert r.returncode == 2 and "no Chrome found" in r.stderr and "--chrome" in r.stderr, r.stderr
    finally:
        shutil.rmtree(tmp)


# ---------- real export (SKIP without Chrome) ----------

def needs_chrome(fn):
    def run():
        if not chrome():
            raise Skip("no Chrome (set $CHROME or put google-chrome/chromium on the PATH)")
        fn()
    run.__name__ = fn.__name__
    return run


def export_with_chrome(*args):
    return export(*args, "--chrome", chrome())


KEY_PROBE = """<script>
addEventListener("load", function(){
  var log = [];
  function key(k, mods, target){
    var e = new KeyboardEvent("keydown", Object.assign({key: k, bubbles: true, cancelable: true}, mods || {}));
    (target || document.body).dispatchEvent(e);
    record((k === " " ? "Space" : k) + (mods && mods.shiftKey ? "+shift" : "") + (mods && mods.ctrlKey ? "+ctrl" : "")
      + (target ? "@input" : ""));
  }
  function swipe(dx, dy){
    function touch(type, x, y){
      var t = new Touch({identifier: 1, target: document.body, clientX: x, clientY: y});
      document.body.dispatchEvent(new TouchEvent(type, {touches: type === "touchend" ? [] : [t],
        changedTouches: [t], bubbles: true, cancelable: true}));
    }
    touch("touchstart", 600, 300); touch("touchend", 600 + dx, 300 + dy);
    record("swipe" + dx + "," + dy);
  }
  function record(name){
    var rail = [].map.call(document.querySelectorAll(".rail button"), function(b){ return b.getAttribute("aria-current"); }).indexOf("step");
    var foot = [].map.call(document.querySelectorAll(".footer-dots button"), function(b){ return b.getAttribute("aria-current"); }).indexOf("step");
    var live = [].map.call(document.querySelectorAll(".slide"), function(s){ return s.hasAttribute("inert"); }).indexOf(false);
    log.push(name + "=" + window.deck.current() + "," + rail + "," + foot + "," + live + "," + location.hash
      + "," + document.querySelector(".counter").textContent.replace(/ /g, ""));
  }
  var field = document.createElement("input"); document.body.appendChild(field);
  key("ArrowRight"); key("ArrowDown"); key("PageDown"); key(" "); key("ArrowLeft"); key("ArrowUp");
  key("PageUp"); key("End"); key(" ", {shiftKey: true}); key("Home"); key("ArrowLeft");
  key("ArrowRight", {ctrlKey: true}); key("ArrowRight", null, field);
  document.querySelectorAll(".footer-dots button")[3].click(); key("x");
  swipe(-120, 0); swipe(120, 0); swipe(-30, 0); swipe(-60, 200);
  var p = document.createElement("pre"); p.id = "probe"; p.textContent = "PROBE " + log.join("|") + " END";
  document.body.appendChild(p);
});
</script>"""


@needs_chrome
def test_any_arrow_changes_slide_and_the_rails_agree():
    tmp = tempfile.mkdtemp(prefix="presentation-")
    try:
        deck = variant(tmp, "probe.html", lambda h: h.replace("</body>", KEY_PROBE + "</body>"))
        r = subprocess.run([chrome(), "--headless", "--disable-gpu", "--no-first-run",
                            f"--user-data-dir={tmp}/profile", "--window-size=1440,830",
                            "--virtual-time-budget=5000", "--dump-dom", "file://" + deck + "#2"],
                           capture_output=True, text=True, timeout=120)
        m = re.search(r"<pre id=\"probe\">PROBE (.*?) END</pre>", r.stdout)
        assert m, "the probe did not run: " + r.stdout[-300:]
        n = len(slides(read(TEMPLATE)))
        # current slide (0-based), right rail, footer row, non-inert slide, hash, counter
        expected = [(k, f"{i},{i},{i},{i},#{i + 1},{i + 1}/{n}") for k, i in [
            ("ArrowRight", 2), ("ArrowDown", 3), ("PageDown", 4), ("Space", 4), ("ArrowLeft", 3), ("ArrowUp", 2),
            ("PageUp", 1), ("End", 4), ("Space+shift", 3), ("Home", 0), ("ArrowLeft", 0),
            ("ArrowRight+ctrl", 0), ("ArrowRight@input", 0), ("x", 3),
            # swipe: a long one to the left goes forward, to the right goes back; short or vertical does nothing
            ("swipe-120,0", 4), ("swipe120,0", 3), ("swipe-30,0", 3), ("swipe-60,200", 3)]]
        got = [tuple(x.rsplit("=", 1)) for x in m.group(1).split("|")]
        assert got == expected, "\n".join(f"{a} != {b}" for a, b in zip(got, expected) if a != b) or got
    finally:
        shutil.rmtree(tmp)


@needs_chrome
def test_template_exports_one_page_and_one_png_per_slide():
    ids = slides(read(TEMPLATE))
    assert len(ids) >= 3
    tmp = tempfile.mkdtemp(prefix="presentation-")
    try:
        pdf, pngs = os.path.join(tmp, "deck.pdf"), os.path.join(tmp, "png")
        r = export_with_chrome(TEMPLATE, "--measure", "--pdf", pdf, "--png-dir", pngs)
        assert r.returncode == 0, r.stderr
        assert "measure screen 1440x830: 1440x830" in r.stdout, r.stdout
        assert pages(pdf) == len(ids), f"{pages(pdf)} pages for {len(ids)} slides"
        outputs = sorted(os.listdir(pngs))
        assert outputs == [f"{i:02d}-{s}.png" for i, s in enumerate(ids, 1)], outputs
        with open(os.path.join(pngs, outputs[0]), "rb") as f:
            head = f.read(24)
        assert head[:8] == b"\x89PNG\r\n\x1a\n"
        assert int.from_bytes(head[16:20], "big") == 2560 and int.from_bytes(head[20:24], "big") == 1440
    finally:
        shutil.rmtree(tmp)


@needs_chrome
def test_an_overflowing_slide_is_refused_before_export():
    tmp = tempfile.mkdtemp(prefix="presentation-")
    try:
        text = "<p>" + "Too much text for a single slide. " * 120 + "</p>"
        bad = variant(tmp, "overflow.html",
                      lambda h: h.replace('<div class="callout">', text + '<div class="callout">', 1))
        pdf = os.path.join(tmp, "x.pdf")
        r = export_with_chrome(bad, "--pdf", pdf)
        assert r.returncode == 1 and "overflows" in r.stderr and "s2" in r.stderr, r.stderr
        assert not os.path.exists(pdf), "exported the cut slide"
        r = export_with_chrome(bad, "--measure")
        assert r.returncode == 1 and "screen 1440x830" in r.stderr, r.stderr
    finally:
        shutil.rmtree(tmp)


@needs_chrome
def test_a_pdf_with_more_pages_than_slides_is_refused():
    tmp = tempfile.mkdtemp(prefix="presentation-")
    try:
        loose = '<div style="height:2in">Loose content outside any slide.</div>'
        bad = variant(tmp, "loose.html", lambda h: h.replace("</main>", "</main>" + loose, 1))
        r = export_with_chrome(bad, "--pdf", os.path.join(tmp, "x.pdf"))
        assert r.returncode == 1 and "pages for" in r.stderr, (r.returncode, r.stderr)
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Skip as e:
                print("SKIP", name, "-", e)
                continue
            except Exception as e:  # an AssertionError or any unexpected error fails
                failures += 1
                print("FAIL", name, "\n     ", f"{type(e).__name__}: {e}"[:1500].replace("\n", "\n      "))
                continue
            print("ok  ", name)
    sys.exit(1 if failures else 0)
