#!/usr/bin/env python3
"""Render a page that carries an inline-SVG diagram and measure its geometry.

    python3 render.py PAGE.html [--out DIR] [--themes dark,light]
                      [--widths 1100,400] [--langs en,pt]
                      [--browser PATH] [--no-shots] [--json]

Opens the page in headless Chromium once per theme, width and language, injects
probe.js, and reads back what it measured. Prints one
`FAIL <rule>: [<theme> <width>px <lang>] <detail>` line per problem and exits 1,
or prints `ok [<theme> <width>px <lang>]` per render and exits 0. WARN lines
never fail the page. The rules, the exit codes and the probe's vocabulary are in
references/probe-rules.md.

The browser is, in order: --browser, $CHROME, google-chrome,
google-chrome-stable, chromium or chromium-browser on PATH, then the newest
Chromium in the Playwright cache ($PLAYWRIGHT_BROWSERS_PATH or
~/.cache/ms-playwright). Without one the page is unverified, not failing: the
script says so and exits 3. Pages under 500 px run inside an iframe of the exact
width, because headless Chrome will not open a window that narrow. Labels are
measured in every language the page's #i18n block carries, since a translated
label is usually longer than the English one.
"""
import argparse
import glob
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROBE = HERE / "probe.js"
NAMES = (
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
THEMES = {"dark", "light"}
# Exit codes; references/probe-rules.md carries the same table.
EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_NO_BROWSER, EXIT_CRASH = 0, 1, 2, 3, 4
EPILOG = """exit codes:
  0  every render passed
  1  the probe found a problem (a FAIL line says which)
  2  usage error or no such page
  3  no browser found, or its sandbox refused and no --no-sandbox was given: unverified, not failing
  4  the check itself crashed: the page is unverified, not failing"""
MIN_WINDOW = 500
HEIGHT = 4200
TIMEOUT = 90
ATTEMPTS = 2
# Rendering is subprocess-bound; four browsers at once is a modest load for a laptop.
WORKERS = 4
# The probe's own element is hidden; the probe's source text in the page never matches this.
OUT_RE = re.compile(r'<pre id="probe-out" hidden(?:="")?>(.*?)</pre>', re.S)


def find_browser(explicit, env=None, home=None):
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    if explicit:
        return explicit if os.access(explicit, os.X_OK) else None
    if env.get("CHROME"):
        return env["CHROME"] if os.access(env["CHROME"], os.X_OK) else None
    for name in NAMES:
        found = shutil.which(name, path=env.get("PATH", ""))
        if found:
            return found
    root = env.get("PLAYWRIGHT_BROWSERS_PATH") or str(home / ".cache" / "ms-playwright")
    builds = glob.glob(os.path.join(root, "chromium-*", "chrome-linux*", "chrome"))
    builds = [b for b in builds if os.access(b, os.X_OK)]

    def revision(path):
        m = re.search(r"chromium-(\d+)", path)
        return int(m.group(1)) if m else 0
    return max(builds, key=revision) if builds else None


def prepare(source, theme, base, probe):
    """Return the page with the theme on <html>, a <base> for relative URLs and the probe script."""
    if not re.search(r"<html[\s>]", source, re.I):
        source = ('<!doctype html><html><head><meta charset="utf-8">'
                  '<meta name="viewport" content="width=device-width,initial-scale=1"></head><body>'
                  + source + "</body></html>")

    def theme_on(m):
        attrs = re.sub(r'\sdata-theme\s*=\s*("[^"]*"|\'[^\']*\'|\S+)', "", m.group(1))
        return f'<html data-theme="{theme}"{attrs}>'
    source = re.sub(r"<html((?:\s[^>]*)?)>", theme_on, source, count=1, flags=re.I)
    tag = f'<base href="{Path(base).as_uri()}/">' if base else ""
    if re.search(r"<head[\s>]", source, re.I):
        source = re.sub(r"(<head(?:\s[^>]*)?>)", lambda m: m.group(1) + tag, source, count=1, flags=re.I)
    else:
        source = re.sub(r"(<html[^>]*>)", lambda m: m.group(1) + "<head>" + tag + "</head>", source, count=1,
                        flags=re.I)
    script = "<script>\n" + probe + "\n</script>"
    if re.search(r"</body>", source, re.I):
        return re.sub(r"</body>", lambda m: script + m.group(0), source, count=1, flags=re.I)
    return source + script


def languages(source):
    """The languages the page's #i18n block carries, English first; just en without one."""
    m = re.search(r'<script[^>]*id="i18n"[^>]*>(.*?)</script>', source, re.S)
    try:
        block = json.loads(m.group(1)) if m else {}
    except ValueError:
        block = {}
    langs = list(block) if isinstance(block, dict) else []
    langs = [lang for lang in langs if re.fullmatch(r"[a-z]{2}", lang)]
    return sorted(langs, key=lambda lang: (lang != "en", lang)) or ["en"]


def judge(result, lang=None):
    """Turn one probe result into (fails, warns), each a list of (rule, detail).

    lang is the language the render asked for; the page must have switched to it."""
    if "error" in result:
        rule = result.get("rule") or "probe-did-not-run"
        return [(rule, result["error"].splitlines()[0][:200])], []
    fails, warns = [], []
    # A page with no lang at all cannot show it switched; English is the unmarked default.
    shown = (result.get("lang") or "").lower() or ("en" if lang == "en" else "")
    if lang and not (shown == lang or shown.startswith(lang + "-")):
        fails.append(("language-not-applied",
                      f"asked for {lang}, the page shows lang={shown or 'nothing'}; its toggle ignores #{lang}"))
    if result.get("page_scrolls_sideways"):
        fails.append(("page-scrolls-sideways",
                      "body wider than the window" + (": " + ", ".join(result.get("past_viewport", []))
                                                      if result.get("past_viewport") else "")))
    if not result.get("diagrams"):
        fails.append(("no-diagram", "no svg[data-diagram] or .dia svg on the page"))
    else:
        # The stamp's Status cell: CURRENT with a date, or PROPOSED. Without it the reader takes a
        # proposal for the system that is running.
        status = result.get("status") or {"ok": False, "reason": "probe output has no status field"}
        if not status.get("ok"):
            fails.append(("status", status.get("reason") or "the stamp has no usable Status"))
    for d in (result.get("diagrams") or []):
        for rule, a, b in d["problems"]:
            fails.append((rule, f"{d['id']}: {a} | {b}"))
    if not result.get("fonts"):
        warns.append(("fonts", "no web font loaded; labels measured in a fallback face"))
    if result.get("unsandboxed"):
        warns.append(("sandbox", "Chromium refused to start sandboxed; ran with --no-sandbox: "
                                 + ", ".join(result["unsandboxed"])))
    return fails, warns


def run_browser(browser, args, sandbox=True, timeout=TIMEOUT):
    with tempfile.TemporaryDirectory(prefix="diagram-render-profile-") as profile:
        cmd = [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
               "--force-device-scale-factor=1", f"--user-data-dir={profile}", "--virtual-time-budget=6000",
               *([] if sandbox else ["--no-sandbox"]), *args]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


class SandboxRefused(Exception):
    """Chromium would not start sandboxed and --no-sandbox was not asked for."""


def run_sandboxed(browser, args, allow_unsandboxed=False):
    """Run with the sandbox; run again with --no-sandbox only when the caller opted in.

    Returns (completed process, whether the unsandboxed run was used)."""
    done = run_browser(browser, args)
    if done.returncode != 0 and "sandbox" in done.stderr.lower():
        if not allow_unsandboxed:
            raise SandboxRefused()
        return run_browser(browser, args, sandbox=False), True
    return done, False


def last_line(text):
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1][:200] if lines else "no output"


def render_one(browser, out, theme, width, lang, shots, no_sandbox=False):
    tag = f"{theme}-{width}-{lang}"
    page = out / f"preview-{theme}-{lang}.html"
    target = f"{page.as_uri()}#{lang}"
    if width < MIN_WINDOW:
        frame = out / f"frame-{tag}.html"
        frame.write_text(
            '<!doctype html><html><head><meta charset="utf-8"><style>body{margin:0;background:#888}'
            f'iframe{{display:block;border:0;width:{width}px;height:{HEIGHT}px}}</style></head><body>'
            f'<iframe src="{html.escape(target)}"></iframe><script>addEventListener(\'message\',function(e){{'
            'if(e.data&&e.data.probe){var p=document.createElement("pre");p.id="probe-out";p.hidden=true;'
            'p.textContent=e.data.probe;document.body.appendChild(p)}})</script></body></html>', encoding="utf-8")
        target = frame.as_uri()
    window = f"--window-size={max(width, MIN_WINDOW)},{HEIGHT}"
    result = {}
    unsandboxed = []
    # A web font fetched through a TLS-inspecting proxy sometimes misses the
    # virtual-time budget; a second attempt measures in the real face.
    for _ in range(ATTEMPTS):
        try:
            if shots:
                shot, bare = run_sandboxed(browser, [window, f"--screenshot={out / f'shot-{tag}.png'}", target],
                                           no_sandbox)
                if shot.returncode != 0:
                    return tag, {"rule": "screenshot",
                                 "error": f"browser exit {shot.returncode}: {last_line(shot.stderr)}"}
                if bare and "screenshot" not in unsandboxed:
                    unsandboxed.append("screenshot")
            done, bare = run_sandboxed(browser, [window, "--dump-dom", target], no_sandbox)
            if bare and "dump-dom" not in unsandboxed:
                unsandboxed.append("dump-dom")
        except subprocess.TimeoutExpired:
            return tag, {"error": f"browser timed out after {TIMEOUT}s"}
        found = OUT_RE.findall(done.stdout)
        if not found:
            return tag, {"error": f"probe did not run (no #probe-out in the rendered page); browser exit "
                                  f"{done.returncode}: {last_line(done.stderr)}"}
        try:
            result = json.loads(html.unescape(found[-1]))
        except ValueError as exc:
            return tag, {"error": f"unreadable probe output: {exc}"}
        if unsandboxed:
            result["unsandboxed"] = unsandboxed
        if result.get("fonts") or "error" in result:
            break
    return tag, result


def csv(text):
    return [part.strip() for part in text.split(",") if part.strip()]


def main(argv=None):
    try:
        return check(argv)
    except Exception as exc:  # a crash must not read as a failing page
        print(f"internal error, the page is unverified: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_CRASH


def check(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0], epilog=EPILOG,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("page")
    ap.add_argument("--out", help="directory for previews, screenshots and probe JSON (default: a temp dir)")
    ap.add_argument("--themes", default="dark,light")
    ap.add_argument("--widths", default="1100,400")
    ap.add_argument("--langs", help="default: every language in the page's #i18n block, else en")
    ap.add_argument("--browser")
    ap.add_argument("--no-sandbox", action="store_true",
                    help="when Chromium refuses to start sandboxed, run it unsandboxed (WARN) instead of exiting 3")
    ap.add_argument("--no-shots", action="store_true", help="skip the PNG screenshots")
    ap.add_argument("--json", action="store_true", help="print every probe result as JSON")
    a = ap.parse_args(argv)

    src = Path(a.page)
    if not src.is_file():
        print(f"no such page: {src}", file=sys.stderr)
        return EXIT_USAGE
    try:
        widths = [int(w) for w in csv(a.widths)]
    except ValueError:
        print(f"--widths takes integers: {a.widths}", file=sys.stderr)
        return EXIT_USAGE
    themes = csv(a.themes)
    if not widths or not themes or any(t not in THEMES for t in themes):
        print("--themes takes dark and/or light; --widths at least one width", file=sys.stderr)
        return EXIT_USAGE
    # Every usage error comes before the browser lookup, so a bad flag is a 2 on a machine with no browser too.
    langs = csv(a.langs) if a.langs else None
    if a.langs and (not langs or not all(re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", lang) for lang in langs)):
        print(f"--langs takes codes such as en,pt: {a.langs}", file=sys.stderr)
        return EXIT_USAGE
    browser = find_browser(a.browser)
    if not browser:
        where = a.browser or "$CHROME, PATH or the Playwright cache"
        print(f"no browser found ({where}); the page is unverified. Install one with "
              "`npx playwright install chromium` or `sudo apt install google-chrome-stable`.", file=sys.stderr)
        return EXIT_NO_BROWSER

    src_text = src.read_text(encoding="utf-8")
    langs = langs or languages(src_text)
    out = (Path(a.out) if a.out else Path(tempfile.mkdtemp(prefix=f"diagram-render-{src.stem}-"))).resolve()
    out.mkdir(parents=True, exist_ok=True)
    probe = PROBE.read_text(encoding="utf-8")
    for theme in themes:
        for lang in langs:
            (out / f"preview-{theme}-{lang}.html").write_text(
                prepare(src_text, theme, src.resolve().parent, probe), encoding="utf-8")

    jobs = [(t, w, lang) for t in themes for w in widths for lang in langs]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        try:
            results = list(pool.map(lambda j: render_one(browser, out, *j, not a.no_shots, a.no_sandbox),
                                    jobs))
        except SandboxRefused:
            print("Chromium cannot start with its sandbox here, so the page is unverified; "
                  "rerun with --no-sandbox to run it unsandboxed.", file=sys.stderr)
            return EXIT_NO_BROWSER

    failed = False
    for (theme, width, lang), (tag, result) in zip(jobs, results):
        (out / f"probe-{tag}.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
        label = f"[{theme} {width}px {lang}]"
        fails, warns = judge(result, lang)
        for rule, detail in fails:
            print(f"FAIL {rule}: {label} {detail}")
        for rule, detail in warns:
            print(f"WARN {rule}: {label} {detail}")
        if fails:
            failed = True
        else:
            print(f"ok {label} " + ", ".join(f"{d['id']}: {d['texts']} labels, {d['icons']} icons, {d['edges']} edges"
                                             for d in (result.get("diagrams") or [])))
    if a.json:
        print(json.dumps(dict((tag, r) for tag, r in results), indent=1))
    print(f"previews, probe results{'' if a.no_shots else ' and screenshots'} in {out}")
    return EXIT_FAIL if failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
