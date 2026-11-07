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

On Windows the script runs under Git Bash and every test runs there too; the
browser is also looked for in the default Windows install folders, as the
export script does.
"""
import os
import pathlib
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
WINDOWS = os.name == "nt"
# Where Chrome for Windows installs, per machine and per user; the export script
# reads the same variables under Git Bash.
WINDOWS_CHROME_DIRS = ("PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "LOCALAPPDATA")


def find_bash():
    """The bash on the PATH, by full path. Run as plain "bash", Windows looks in
    System32 before the PATH and starts the WSL launcher, not Git Bash; that one
    never runs the script and leaves stderr empty."""
    b = shutil.which("bash")
    windir = os.path.normcase(os.environ.get("SystemRoot", r"C:\Windows"))
    if WINDOWS and (not b or os.path.normcase(b).startswith(windir)):
        git = shutil.which("git")  # Git for Windows: <Git>\cmd\git.exe, <Git>\bin\bash.exe
        b = git and os.path.join(os.path.dirname(os.path.dirname(git)), "bin", "bash.exe")
        b = b if b and os.path.isfile(b) else None
    return b or "/bin/bash"


BASH = find_bash()

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
    if os.access(MAC_CHROME, os.X_OK):
        return MAC_CHROME
    if WINDOWS:
        for var in WINDOWS_CHROME_DIRS:
            base = os.environ.get(var)
            c = base and os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
            if c and os.path.isfile(c):
                return c
    return None


def export(*args, env=None):
    e = dict(os.environ)
    e.pop("CHROME", None)
    e.update(env or {})
    return subprocess.run([BASH, SCRIPT, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", env=e, timeout=300)


def said(r):
    """What a failed assertion shows: the exit code and both streams."""
    return f"exit {r.returncode}\nstdout: {r.stdout!r}\nstderr: {r.stderr!r}"


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


def test_script_expands_arrays_safely_under_set_u_on_old_bash():
    """macOS ships bash 3.2 (see get.sh): there, "${A[@]}" of an empty array under
    set -u aborts with "unbound variable". Every array expansion must be guarded."""
    src = open(os.path.join(SKILL, "scripts", "export-deck.sh"), encoding="utf-8").read()
    bare = [m.group(0) for m in re.finditer(r'(?<![+])"\$\{(\w+)\[@\]\}"', src)
            if "${%s[@]+" % m.group(1) not in src[max(0, m.start() - 20):m.start()]]
    assert not bare, bare


def test_skill_says_what_happens_without_the_sweep():
    text = " ".join(open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8").read().split())
    assert "sweep.py` is not installed" in text and "sweep was not run" in text

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

def test_on_windows_the_script_runs_under_git_bash_not_the_wsl_launcher():
    """A bash in the Windows folder is the WSL launcher; find_bash takes Git's
    instead. The layout is faked, so this runs on any system."""
    global WINDOWS
    tmp = tempfile.mkdtemp(prefix="presentation-")
    saved = WINDOWS, os.environ.get("PATH"), os.environ.get("SystemRoot")
    try:
        def tool(*parts):
            p = os.path.join(tmp, *parts)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write("")
            os.chmod(p, 0o755)
            return p
        exe = ".exe" if os.name == "nt" else ""  # shutil.which on Windows needs the extension
        wsl = tool("Windows", "System32", "bash" + exe)
        tool("Git", "cmd", "git" + exe)
        git_bash = tool("Git", "bin", "bash.exe")
        os.environ["SystemRoot"] = os.path.join(tmp, "Windows")
        os.environ["PATH"] = os.pathsep.join([os.path.dirname(wsl), os.path.join(tmp, "Git", "cmd")])
        WINDOWS = True
        same = lambda a, b: os.path.normcase(a) == os.path.normcase(b)
        assert same(find_bash(), git_bash), find_bash()
        WINDOWS = False
        assert same(find_bash(), wsl), ("off Windows the PATH's bash stands", find_bash())
    finally:
        WINDOWS = saved[0]
        for k, v in (("PATH", saved[1]), ("SystemRoot", saved[2])):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp)


def test_script_refuses_a_deck_without_the_template_contract():
    tmp = tempfile.mkdtemp(prefix="presentation-")
    try:
        out = os.path.join(tmp, "x.pdf")
        no_render = variant(tmp, "no-render.html", lambda h: h.replace("html.render", "html.other"))
        r = export(no_render, "--pdf", out)
        assert r.returncode == 2 and "html.render" in r.stderr, said(r)
        no_slide = variant(tmp, "no-slide.html", lambda h: h.replace('class="slide', 'class="sheet'))
        r = export(no_slide, "--pdf", out)
        assert r.returncode == 2 and "section" in r.stderr, said(r)
        no_id = variant(tmp, "no-id.html", lambda h: h.replace(' id="s3"', "", 1))
        r = export(no_id, "--pdf", out)
        assert r.returncode == 2 and "has no id" in r.stderr, said(r)
        r = export(TEMPLATE)
        assert r.returncode == 2 and "nothing to do" in r.stderr, said(r)
        r = export(TEMPLATE, "--measure", "--theme", "sepia")
        assert r.returncode == 2 and "--theme" in r.stderr, said(r)
        assert not os.path.exists(out)
    finally:
        shutil.rmtree(tmp)


def test_missing_chrome_fails_with_a_clear_message():
    r = export(TEMPLATE, "--pdf", "/does/not/exist/x.pdf", "--chrome", "/does/not/exist/chrome")
    assert r.returncode == 2 and "Chrome not found at --chrome" in r.stderr, said(r)
    r = export(TEMPLATE, "--measure", env={"CHROME": "/does/not/exist/chrome"})
    assert r.returncode == 2 and "$CHROME" in r.stderr, said(r)
    if os.access(MAC_CHROME, os.X_OK):
        return  # the macOS fallback would find it; the PATH case cannot be shown here
    # A PATH with only the tools the script needs before it looks for Chrome.
    tools = ("bash", "grep", "sed", "wc", "tr")
    tmp = tempfile.mkdtemp(prefix="presentation-")
    try:
        if WINDOWS:
            # Git's own tool folders: an msys tool loads its DLLs from beside it,
            # so a link elsewhere would not start (and links need a privilege).
            path = os.pathsep.join(sorted({os.path.dirname(BASH)} | {
                os.path.dirname(shutil.which(t)) for t in tools if shutil.which(t)}))
        else:
            for tool in tools:
                os.symlink(shutil.which(tool), os.path.join(tmp, tool))
            path = tmp
        names = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
        if any(shutil.which(c, path=path) for c in names):
            return  # a browser sits beside the tools; the PATH case cannot be shown here
        # the Windows install folders point nowhere, or the script would find a Chrome there
        nowhere = {var: os.path.join(tmp, "nowhere") for var in WINDOWS_CHROME_DIRS}
        r = export(TEMPLATE, "--measure", env={"PATH": path, **nowhere})
        assert r.returncode == 2 and "no Chrome found" in r.stderr and "--chrome" in r.stderr, said(r)
    finally:
        shutil.rmtree(tmp)


# Stands in for Git Bash's cygpath off Windows: the POSIX root plays drive C:.
FAKE_CYGPATH = r"""#!/usr/bin/env bash
mode=$1; shift
abs=0; [ "${1:-}" = -a ] && { abs=1; shift; }
p=$1
case "$mode" in
  -u) case "$p" in [A-Za-z]:*) p=${p#?:} ;; esac; printf '%s\n' "$p" | tr '\\' / ;;
  -m|-w)
    case "$p" in /*) ;; *) [ "$abs" -eq 1 ] && p="$PWD/$p" ;; esac
    case "$p" in /*) p="C:$p" ;; esac
    if [ "$mode" = -w ]; then printf '%s\n' "$p" | tr / '\\'; else printf '%s\n' "$p"; fi ;;
  *) echo "fake cygpath: $mode is not faked" >&2; exit 2 ;;
esac
"""

# Stands in for Chrome: logs its arguments and each page's <base>, answers the
# measurement and writes the PDF and PNG it is asked for. It opens what it is
# handed through cygpath -u, so a path Chrome for Windows could not open fails.
FAKE_CHROME = r"""#!/usr/bin/env bash
log="@LOG@"
printf '%s\n' "$@" >> "$log"
# What the real Chrome does: scratch in the temp directory it was started with,
# outside its profile (a socket dir; on Windows a <uuid>.tmp file).
t=$(cygpath -u "${TMPDIR:-/tmp}")
@SOCKCHECK@
mkdir "$t/.com.google.Chrome.$$" && : > "$t/$$.tmp" && echo "scratch" >> "$log"
for a in "$@"; do
  case "$a" in
    file://*) u=${a#file://}; case "$u" in /[A-Za-z]:*) u=${u#/} ;; esac
              f=$(cygpath -u "$u"); grep -o '<base href="[^"]*"' "$f" >> "$log" || true ;;
  esac
done
for a in "$@"; do
  case "$a" in
    --dump-dom) echo '<pre>MEASURE 1280x720 @MEASURED@ END</pre>' ;;
    --print-to-pdf=*) f=$(cygpath -u "${a#*=}"); for s in @IDS@; do echo '/Type /Page'; done > "$f" ;;
    --screenshot=*) f=$(cygpath -u "${a#*=}"); echo png > "$f" ;;
  esac
done
"""


def test_under_git_bash_chrome_gets_windows_paths_and_file_urls():
    """Chrome for Windows cannot open /c/... or /tmp/...: under Git Bash the script
    hands it C:\\... files and file:///C:/... URLs, and finds it in the Windows
    install folders. Off Windows, uname, cygpath and Chrome are faked, so this
    runs on any system; on Windows the real uname and cygpath answer. Off Windows
    it also shows that a cygpath alone, without a Git Bash uname, changes nothing."""
    ids = slides(read(TEMPLATE))
    tmp = tempfile.mkdtemp(prefix="presentation-")
    try:
        def write(path, text):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            os.chmod(path, 0o755)
            return path
        log = os.path.join(tmp, "chrome.log")
        fake_chrome = FAKE_CHROME.replace("@LOG@", log.replace("\\", "/")) \
            .replace("@MEASURED@", " ".join(f"{s}=0" for s in ids)).replace("@IDS@", " ".join(ids)) \
            .replace("@SOCKCHECK@", "")
        nowhere = {var: os.path.join(tmp, "nowhere") for var in WINDOWS_CHROME_DIRS}

        def run(env, *chrome_arg):
            if os.path.exists(log):
                os.remove(log)
            out = os.path.join(tmp, "out")
            shutil.rmtree(out, ignore_errors=True)
            pdf, pngs = os.path.join(out, "deck.pdf"), os.path.join(out, "png")
            # The run's own temp directory: it must come out empty, whatever Chrome wrote to it.
            scratch = os.path.join(tmp, "scratch")
            shutil.rmtree(scratch, ignore_errors=True)
            os.makedirs(scratch)
            r = export(TEMPLATE, "--pdf", pdf, "--png-dir", pngs, *chrome_arg,
                       env={**(env or {}), "TMPDIR": scratch, "TEMP": scratch, "TMP": scratch})
            assert r.returncode == 0, said(r)
            assert os.listdir(scratch) == [], f"the export left {os.listdir(scratch)} in its TMPDIR"
            assert read(log).splitlines().count("scratch") == len(ids) + 2, \
                "the fake Chrome did not write its scratch, so the check above proves nothing"
            assert pages(pdf) == len(ids) and len(os.listdir(pngs)) == len(ids), said(r)
            lines = read(log).splitlines()
            urls = [a for a in lines if a.startswith("file:")]
            assert len(urls) == len(ids) + 2, urls  # the measurement, the PDF, one per slide
            bases = [a for a in lines if a.startswith("<base href=")]
            assert len(bases) == len(ids) + 1, bases  # the PDF and the slides carry the deck's <base>
            files = {k: [a[len(k):] for a in lines if a.startswith(k)]
                     for k in ("--user-data-dir=", "--print-to-pdf=", "--screenshot=")}
            assert [len(v) for v in files.values()] == [len(ids) + 2, 1, len(ids)], files
            return pdf, urls, bases, files

        if WINDOWS:
            chrome_bin = write(os.path.join(tmp, "fake-chrome"), fake_chrome)
            git_bash_env, chrome_arg = None, ("--chrome", chrome_bin)
        else:
            # A PATH with the fakes and only the tools the script and the fakes use,
            # so no real browser on this machine is found before the fake one.
            tools = os.path.join(tmp, "tools")
            os.makedirs(tools)
            for t in ("bash", "env", "grep", "sed", "awk", "wc", "tr", "cut", "head", "cat",
                      "mkdir", "mktemp", "rm", "dirname", "basename"):
                assert shutil.which(t), f"{t} is not on the PATH"
                os.symlink(shutil.which(t), os.path.join(tools, t))
            fakes = os.path.join(tmp, "fakes")
            write(os.path.join(fakes, "cygpath"), FAKE_CYGPATH)
            uname = write(os.path.join(fakes, "uname"), "#!/usr/bin/env bash\necho MINGW64_NT-10.0-20348\n")
            # where Chrome for Windows installs per user; nothing on the PATH
            apps = os.path.join(tmp, "apps")
            write(os.path.join(apps, "Google", "Chrome", "Application", "chrome.exe"), fake_chrome)
            path = os.pathsep.join([fakes, tools])
            git_bash_env = {"PATH": path, **nowhere, "LOCALAPPDATA": "C:" + apps}
            chrome_arg = ()

        pdf, urls, bases, files = run(git_bash_env, *chrome_arg)
        bad = [u for u in urls if not re.match(r"file:///[A-Za-z]:/[^\\]+$", u)]
        assert not bad, f"Chrome got URLs that are not file:///C:/...: {bad}"
        bad = [b for b in bases if not re.match(r'<base href="file:///[A-Za-z]:/[^\\"]+/templates/"$', b)]
        assert not bad, f"the deck's <base> is not file:///C:/.../: {bad}"
        bad = [f for v in files.values() for f in v if not re.match(r"[A-Za-z]:\\", f)]
        assert not bad, f"Chrome got file paths that are not C:\\...: {bad}"

        if not WINDOWS:
            # The same cygpath with a Linux uname: paths go to Chrome as they are.
            write(uname, "#!/usr/bin/env bash\necho Linux\n")
            pdf, urls, bases, files = run({"PATH": path}, "--chrome",
                                          os.path.join(apps, "Google", "Chrome", "Application", "chrome.exe"))
            assert all(u.startswith("file:///") and not re.match(r"file:///[A-Za-z]:", u) for u in urls), urls
            assert files["--print-to-pdf="] == [pdf], files
            assert all(f.startswith(tmp + "/") for f in files["--screenshot="]), files
    finally:
        shutil.rmtree(tmp)


# Chrome's unix socket (<TMPDIR>/.com.google.Chrome.XXXXXX/SingletonSocket) must fit in sun_path:
# the real Chrome aborts over this length, measured as 66 characters of TMPDIR on Linux.
SOCKET_CHECK = r'''p="$t/.com.google.Chrome.XXXXXX/SingletonSocket"
[ ${#p} -le 108 ] || { echo "[1:1:FATAL:process_singleton_posix.cc:313] Socket path too long: $p." >&2; exit 1; }'''


def test_a_long_tmpdir_still_exports_and_a_chrome_failure_says_why():
    """A TMPDIR long enough that Chrome's socket fits under it but not under a directory
    nested inside it must not break the export (the first scratch fix did), and the
    run must leave nothing in it. When Chrome dies, its fatal line reaches the user.
    POSIX only: Chrome for Windows uses named pipes, with no length limit."""
    if WINDOWS:
        raise Skip("Chrome for Windows has no socket path limit")
    ids = slides(read(TEMPLATE))
    tmp = tempfile.mkdtemp(prefix="lt-")
    try:
        long_tmp = tmp
        if len(long_tmp) < 62:  # pad the path to 62 characters
            long_tmp = os.path.join(tmp, "p" * (62 - len(tmp) - 1))
            os.makedirs(long_tmp)
        assert 62 <= len(long_tmp) <= 66, f"cannot build a 62..66 character TMPDIR from {tmp}"

        def write(path, text):
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            os.chmod(path, 0o755)
            return path
        log = os.path.join(tmp, "chrome.log")
        fakes = os.path.join(tmp, "fakes")
        os.makedirs(fakes)
        write(os.path.join(fakes, "cygpath"), FAKE_CYGPATH)
        fake = write(os.path.join(fakes, "chrome"), FAKE_CHROME.replace("@LOG@", log)
                     .replace("@MEASURED@", " ".join(f"{s}=0" for s in ids)).replace("@IDS@", " ".join(ids))
                     .replace("@SOCKCHECK@", SOCKET_CHECK))
        env = {"PATH": fakes + os.pathsep + os.environ["PATH"],
               "TMPDIR": long_tmp, "TEMP": long_tmp, "TMP": long_tmp}
        before = os.listdir(long_tmp)
        out = os.path.join(tmp, "out")
        r = export(TEMPLATE, "--pdf", os.path.join(out, "deck.pdf"), "--png-dir", os.path.join(out, "png"),
                   "--chrome", fake, env=env)
        assert r.returncode == 0, said(r)
        assert read(log).splitlines().count("scratch") == len(ids) + 2, "the fake Chrome did not run its checks"
        assert sorted(os.listdir(long_tmp)) == sorted(before), \
            f"the export left {set(os.listdir(long_tmp)) - set(before)} in its TMPDIR"

        # Chrome dies: the reason is not swallowed.
        dying = write(os.path.join(fakes, "dying"),
                      "#!/usr/bin/env bash\necho '[1:1:FATAL:x.cc:1] Socket path too long: /x' >&2\nexit 1\n")
        r = export(TEMPLATE, "--measure", "--chrome", dying, env=env)
        assert r.returncode == 1 and "Chrome returned no measurement" in r.stderr, said(r)
        assert "FATAL:x.cc:1] Socket path too long" in r.stderr and "hint:" in r.stderr, said(r)
        assert sorted(os.listdir(long_tmp)) == sorted(before), os.listdir(long_tmp)
    finally:
        shutil.rmtree(tmp)


# ---------- real export (SKIP without Chrome) ----------

def needs_chrome(fn):
    def run():
        if not chrome():
            # The Windows CI legs are where the Windows export is proven; a runner
            # without Chrome there would turn that proof into a quiet SKIP.
            assert not (WINDOWS and os.environ.get("GITHUB_ACTIONS") == "true"), \
                "no Chrome on the Windows CI runner: the Windows export would go unproven"
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
    scratch = None
    try:
        deck = variant(tmp, "probe.html", lambda h: h.replace("</body>", KEY_PROBE + "</body>"))
        # Chrome writes scratch into its temp directory too, so it gets one of its own, removed below.
        # Short on purpose: its unix socket path must stay under about 100 characters.
        scratch = tempfile.mkdtemp(prefix="c-")
        r = subprocess.run([chrome(), "--headless", "--disable-gpu", "--no-first-run",
                            f"--user-data-dir={tmp}/profile", "--window-size=1440,830",
                            "--virtual-time-budget=5000", "--dump-dom", pathlib.Path(deck).as_uri() + "#2"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                           env={**os.environ, "TMPDIR": scratch, "TEMP": scratch, "TMP": scratch})
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
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)


@needs_chrome
def test_template_exports_one_page_and_one_png_per_slide():
    ids = slides(read(TEMPLATE))
    assert len(ids) >= 3
    tmp = tempfile.mkdtemp(prefix="presentation-")
    try:
        pdf, pngs = os.path.join(tmp, "deck.pdf"), os.path.join(tmp, "png")
        r = export_with_chrome(TEMPLATE, "--measure", "--pdf", pdf, "--png-dir", pngs)
        assert r.returncode == 0, said(r)
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
        assert r.returncode == 1 and "overflows" in r.stderr and "s2" in r.stderr, said(r)
        assert not os.path.exists(pdf), "exported the cut slide"
        r = export_with_chrome(bad, "--measure")
        assert r.returncode == 1 and "screen 1440x830" in r.stderr, said(r)
    finally:
        shutil.rmtree(tmp)


@needs_chrome
def test_a_pdf_with_more_pages_than_slides_is_refused():
    tmp = tempfile.mkdtemp(prefix="presentation-")
    try:
        loose = '<div style="height:2in">Loose content outside any slide.</div>'
        bad = variant(tmp, "loose.html", lambda h: h.replace("</main>", "</main>" + loose, 1))
        r = export_with_chrome(bad, "--pdf", os.path.join(tmp, "x.pdf"))
        assert r.returncode == 1 and "pages for" in r.stderr, said(r)
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
