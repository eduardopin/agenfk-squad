#!/usr/bin/env bash
# export-deck.sh: exports an HTML deck (built from templates/deck.html) to a
# 16:9 PDF and, on request, one PNG per slide. It measures before it exports:
# a slide that overflows the page is refused, because in the PDF it would come
# out cut.
#
# Usage:
#   export-deck.sh <deck.html> [--pdf <out.pdf>] [--png-dir <dir>] [--transparent]
#                  [--theme light|dark] [--chrome <path>]
#   export-deck.sh <deck.html> --measure        # measure only, no export
#
# Contract with the template:
#   * every slide is  <section class="slide" id="...">
#   * the CSS defines html.render (fixed page, final state of any animation)
#     and html.render.transparent (transparent background); the template
#     ships both.
#
# Gates (exit != 0):
#   2  wrong usage, deck without the contract, or no Chrome found
#   1  a slide overflows (the 1440x830 screen with --measure; the 16:9 page
#      always), or the PDF does not have exactly one page per slide
#
# Chrome: --chrome, then $CHROME, then google-chrome, google-chrome-stable,
# chromium, chromium-browser on the PATH, the default macOS path and, under
# Git Bash on Windows, the default Windows install folders.
#
# Windows: run it with Git Bash. Chrome for Windows does not read Git Bash
# paths (/c/...), so there every path handed to Chrome goes through cygpath:
# files as C:\..., URLs as file:///C:/... Elsewhere paths go as they are.
set -euo pipefail

DECK="" ; PDF="" ; PNG_DIR="" ; TRANSPARENT=0 ; THEME="" ; MEASURE=0 ; CHROME_ARG=""

usage() { echo "usage: export-deck.sh <deck.html> [--pdf out.pdf] [--png-dir dir] [--transparent] [--theme light|dark] [--chrome path] [--measure]" >&2; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --pdf)         [ $# -ge 2 ] || usage; PDF="$2"; shift 2 ;;
    --png-dir)     [ $# -ge 2 ] || usage; PNG_DIR="$2"; shift 2 ;;
    --transparent) TRANSPARENT=1; shift ;;
    --theme)       [ $# -ge 2 ] || usage; THEME="$2"; shift 2 ;;
    --chrome)      [ $# -ge 2 ] || usage; CHROME_ARG="$2"; shift 2 ;;
    --measure)     MEASURE=1; shift ;;
    -h|--help)     usage ;;
    -*)            echo "unknown option: $1" >&2; exit 2 ;;
    *)             DECK="$1"; shift ;;
  esac
done

# ---------- Windows (Git Bash) ----------
UNAME=$(uname -s 2>/dev/null || true)
GIT_BASH=0
case "$UNAME" in
  MINGW*|MSYS*|CYGWIN*) command -v cygpath >/dev/null 2>&1 && GIT_BASH=1 ;;
esac
# posix_path <path>: a path bash can use (C:\x becomes /c/x under Git Bash).
posix_path() {
  if [ "$GIT_BASH" -eq 1 ] && [ -n "$1" ]; then cygpath -u "$1"; else printf '%s\n' "$1"; fi
}
# native_path <path>: the absolute path Chrome opens (C:\x under Git Bash).
native_path() {
  if [ "$GIT_BASH" -eq 1 ]; then cygpath -w -a "$1"; else printf '%s\n' "$1"; fi
}
# file_url <absolute path>: file:///C:/x under Git Bash, file:///x elsewhere.
file_url() {
  if [ "$GIT_BASH" -eq 1 ]; then printf 'file:///%s\n' "$(cygpath -m -a "$1")"; else printf 'file://%s\n' "$1"; fi
}
DECK=$(posix_path "$DECK") ; PDF=$(posix_path "$PDF") ; PNG_DIR=$(posix_path "$PNG_DIR")
CHROME_ARG=$(posix_path "$CHROME_ARG")

[ -n "$DECK" ] && [ -f "$DECK" ] || usage
[ -n "$PDF" ] || [ -n "$PNG_DIR" ] || [ "$MEASURE" -eq 1 ] || { echo "nothing to do: pass --pdf, --png-dir or --measure" >&2; exit 2; }
case "$THEME" in ""|light|dark) ;; *) echo "--theme takes light or dark, not '$THEME'" >&2; exit 2 ;; esac

# ---------- contract with the template ----------
SLIDE_IDS=$(grep -o '<section[^>]*class="slide[^"]*"[^>]*>' "$DECK" \
  | grep -o ' id="[^"]*"' | sed 's/ id="\(.*\)"/\1/' || true)
[ -n "$SLIDE_IDS" ] || { echo "no <section class=\"slide\" id=...> in $DECK" >&2; exit 2; }
N_SLIDES=$(printf '%s\n' "$SLIDE_IDS" | wc -l | tr -d ' ')
N_SECTIONS=$(grep -o '<section[^>]*class="slide[^"]*"' "$DECK" | wc -l | tr -d ' ')
[ "$N_SLIDES" -eq "$N_SECTIONS" ] || { echo "a <section class=\"slide\"> has no id in $DECK ($N_SECTIONS slides, $N_SLIDES ids)" >&2; exit 2; }
grep -q 'html\.render' "$DECK" || { echo "$DECK lacks the template's html.render mode; start from templates/deck.html" >&2; exit 2; }

# ---------- Chrome ----------
resolve_chrome() {
  if [ -n "$CHROME_ARG" ]; then
    [ -x "$CHROME_ARG" ] && { echo "$CHROME_ARG"; return 0; }
    echo "Chrome not found at --chrome $CHROME_ARG" >&2; return 1
  fi
  if [ -n "${CHROME:-}" ]; then
    CHROME=$(posix_path "$CHROME")
    [ -x "$CHROME" ] && { echo "$CHROME"; return 0; }
    echo "Chrome not found at \$CHROME=$CHROME" >&2; return 1
  fi
  local c
  for c in google-chrome google-chrome-stable chromium chromium-browser; do
    if command -v "$c" >/dev/null 2>&1; then command -v "$c"; return 0; fi
  done
  c="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  [ -x "$c" ] && { echo "$c"; return 0; }
  if [ "$GIT_BASH" -eq 1 ]; then
    # Per machine (Program Files, 64- and 32-bit) and per user (LocalAppData).
    # Matched case-blind: the names reach bash in whatever case the caller used.
    local base
    while IFS= read -r base; do
      [ -n "$base" ] || continue
      c="$(posix_path "$base")/Google/Chrome/Application/chrome.exe"
      [ -x "$c" ] && { echo "$c"; return 0; }
    done <<WINDIRS
$(env | grep -i -E '^(programfiles|programfiles\(x86\)|programw6432|localappdata)=' | cut -d= -f2- || true)
WINDIRS
  fi
  echo "no Chrome found (looked for google-chrome, google-chrome-stable, chromium, chromium-browser on the PATH, the macOS path and, under Git Bash, the Windows install folders); pass --chrome <path> or set \$CHROME" >&2
  return 1
}
CHROME_BIN=$(resolve_chrome) || exit 2

WORK="$(mktemp -d "${TMPDIR:-/tmp}"/dk.XXXXXX)"
# Chrome's own temp directory, see chrome() below. Empty until needed.
CHROME_TMP=""
# Cleanup must never change the exit code: on Windows Chrome can still hold the profile.
trap 'rm -rf "$WORK" ${CHROME_TMP:+"$CHROME_TMP"} 2>/dev/null || true' EXIT
DECK_ABS="$(cd "$(dirname "$DECK")" && pwd)/$(basename "$DECK")"

# Throwaway profile: never touches the user's Chrome profile. Chrome also writes
# outside the profile, into the temp directory: the singleton socket dir
# (.com.google.Chrome.XXXXXX, which it removes on exit but leaves behind when the
# machine is busy and it shuts down in a hurry) and, on Windows, <uuid>.tmp. So
# Chrome gets a temp directory of its own, removed on exit: the run leaves
# nothing in the caller's.
# On Linux and macOS the socket path (<dir>/.com.google.Chrome.XXXXXX/SingletonSocket)
# must stay under about 107 characters or Chrome aborts, and a long caller's TMPDIR
# (the base used it as Chrome's directory) must not break an export that worked, so
# when $WORK is a longer path than /tmp/dk.XXXXXX Chrome gets a directory there
# instead. Windows uses named pipes: no limit, so $WORK serves.
CHROME_DIR="$WORK"
if [ "$GIT_BASH" -eq 0 ] && [ "${#WORK}" -gt 14 ] && [ -d /tmp ] && [ -w /tmp ]; then
  if CHROME_TMP=$(mktemp -d /tmp/dk.XXXXXX 2>/dev/null) && [ -d "$CHROME_TMP" ]; then
    CHROME_DIR="$CHROME_TMP"
  else
    CHROME_TMP=""
  fi
fi
CHROME_ERR="$WORK/chrome.err"
chrome() {
  local t; t="$(native_path "$CHROME_DIR")"
  TMPDIR="$t" TEMP="$t" TMP="$t" "$CHROME_BIN" --headless --disable-gpu --no-first-run --no-default-browser-check \
    --user-data-dir="$(native_path "$WORK/profile")" "$@" 2>>"$CHROME_ERR"
}
# chrome_why: what Chrome said when it died, without its usual noise (fontconfig
# and the like): only the fatal and error lines, and a hint for a known cause.
chrome_why() {
  local why
  why=$( (tr -d '\r' < "$CHROME_ERR" | grep -E 'FATAL|ERROR' | tail -n 3) 2>/dev/null || true)
  [ -n "$why" ] || return 0
  printf 'Chrome said:\n%s\n' "$why" >&2
  case "$why" in *"Socket path too long"*)
    echo "hint: the temp directory path is too long for Chrome's socket; set TMPDIR to a shorter one" >&2 ;;
  esac
}

# render_variant <classes> <out>: a copy of the deck with classes on <html> and
# the theme pinned (light, unless the deck pinned another), so the result does
# not depend on the OS. The copy lives in $WORK; a <base> right after <head>
# points at the deck's directory, so relative images still resolve. Nothing
# goes before the <!doctype>: that would put the page in quirks mode and change
# the layout.
render_variant() {
  local classes="$1" out="$2" js
  js=$(printf '"%s",' $classes | sed 's/,$//')
  {
    awk -v b="<base href=\"$(file_url "$(dirname "$DECK_ABS")")/\">" '
      !seen && match($0, /<[hH][eE][aA][dD]([ \t][^>]*)?>/) {
        $0 = substr($0, 1, RSTART + RLENGTH - 1) b substr($0, RSTART + RLENGTH); seen = 1 }
      { print }' "$DECK"
    printf '<script>(function(r){r.classList.add(%s);' "$js"
    if [ -n "$THEME" ]; then printf 'r.setAttribute("data-theme","%s");' "$THEME"
    else printf 'if(!r.hasAttribute("data-theme"))r.setAttribute("data-theme","light");'; fi
    printf '})(document.documentElement)</script>\n'
  } > "$out"
}

# measure <classes> <width> <height> <label>: prints each slide's overflow and
# fails if any is above 0. On screen it compares with innerHeight; in render
# mode, with the slide's own height (fixed at 7.5in, which cuts what is left).
# The deck runs inside an iframe of the exact size: in headless mode
# --window-size includes the window frame and the viewport would come out
# smaller than requested.
measure() {
  local classes="$1" w="$2" h="$3" label="$4" n=$RANDOM out line bad counted
  local f="$WORK/measure-$n.html" frame="$WORK/frame-$n.html"
  render_variant "$classes" "$f"
  cat >> "$f" <<'JS'
<script>
(function(){
  function run(){
    var r=document.documentElement, render=r.classList.contains("render"), ih=innerHeight, l=[];
    [].forEach.call(document.querySelectorAll("section.slide"),function(s){
      l.push(s.id+"="+(s.scrollHeight-(render?s.clientHeight:ih)));
    });
    /* the marker is assembled in pieces so it does not match this very code */
    parent.postMessage(["MEAS","URE"].join("")+" "+innerWidth+"x"+ih+" "+l.join(" ")+" "+["E","ND"].join(""),"*");
  }
  addEventListener("load",function(){
    (document.fonts&&document.fonts.ready?document.fonts.ready:Promise.resolve()).then(function(){setTimeout(run,50)});
  });
})();
</script>
JS
  printf '<!doctype html><html><body style="margin:0"><pre id="m"></pre><iframe src="%s" width="%s" height="%s" style="border:0;display:block"></iframe><script>addEventListener("message",function(e){document.getElementById("m").textContent=String(e.data)})</script></body></html>\n' \
    "$(basename "$f")" "$w" "$h" > "$frame"
  out=$(chrome --window-size="$((w + 200)),$((h + 400))" --virtual-time-budget=10000 --dump-dom "$(file_url "$frame")" || true)
  line=$(printf '%s' "$out" | grep -o 'MEASURE [^<]* END' | head -1 || true)
  [ -n "$line" ] || { echo "measure $label: Chrome returned no measurement" >&2; chrome_why; return 1; }
  counted=$(printf '%s\n' "$line" | tr ' ' '\n' | grep -c '=' || true)
  [ "$counted" -eq "$N_SLIDES" ] || { echo "measure $label: $counted slides measured for $N_SLIDES in the deck" >&2; return 1; }
  case "$line" in "MEASURE ${w}x${h} "*) ;; *) echo "measure $label: wrong viewport (${line%% s*}), expected ${w}x${h}" >&2; return 1 ;; esac
  echo "measure $label: ${line#MEASURE }" | sed 's/ END$//'
  bad=$(printf '%s\n' "$line" | tr ' ' '\n' | grep '=' | awk -F= '$2+0>0{print $1" ("$2"px)"}' || true)
  if [ -n "$bad" ]; then
    echo "overflows on $label: $(printf '%s ' $bad)- cut text or split the slide" >&2
    return 1
  fi
}

if [ "$MEASURE" -eq 1 ]; then
  ok=0
  measure "" 1440 830 "screen 1440x830" || ok=1
  measure "render" 1280 720 "16:9 page" || ok=1
  [ -z "$PDF" ] && [ -z "$PNG_DIR" ] && exit "$ok"
  [ "$ok" -eq 0 ] || exit 1
else
  # Exporting without measuring is how a cut slide ships unnoticed.
  measure "render" 1280 720 "16:9 page" || exit 1
fi

if [ -n "$PDF" ]; then
  mkdir -p "$(dirname "$PDF")"
  render_variant "render" "$WORK/print.html"
  rm -f "$PDF"
  chrome --no-pdf-header-footer --window-size=1280,720 --virtual-time-budget=15000 \
    --print-to-pdf="$(native_path "$PDF")" "$(file_url "$WORK/print.html")" || true
  [ -s "$PDF" ] || { echo "Chrome did not produce $PDF" >&2; chrome_why; exit 1; }
  # /Type /Page (leaf) and /Type /Pages (tree); count only the leaves.
  PAGES=$( (grep -a -o '/Type */Pages*' "$PDF" || true) | (grep -c 'Page$' || true) )
  if [ "$PAGES" -ne "$N_SLIDES" ]; then
    echo "the PDF has $PAGES pages for $N_SLIDES slides: a slide broke across pages or went missing" >&2
    exit 1
  fi
  echo "pdf: $PDF ($PAGES pages, $N_SLIDES slides)"
fi

if [ -n "$PNG_DIR" ]; then
  mkdir -p "$PNG_DIR"
  CLASSES="render"; BG=()
  if [ "$TRANSPARENT" -eq 1 ]; then
    CLASSES="render transparent"
    BG=(--default-background-color=00000000)
  fi
  i=1
  for id in $SLIDE_IDS; do
    f="$WORK/slide-$id.html"
    render_variant "$CLASSES" "$f"
    # Fragment navigation (#id) does not scroll in headless mode: show only this slide.
    printf '<style>.slide{display:none!important}#%s{display:flex!important}</style>\n' "$id" >> "$f"
    out="$PNG_DIR/$(printf '%02d' "$i")-$id.png"
    rm -f "$out"
    chrome --hide-scrollbars --window-size=1280,720 --force-device-scale-factor=2 ${BG[@]+"${BG[@]}"} \
      --virtual-time-budget=12000 --screenshot="$(native_path "$out")" "$(file_url "$f")" || true
    [ -s "$out" ] || { echo "Chrome did not produce $out" >&2; chrome_why; exit 1; }
    echo "png: $out"
    i=$((i+1))
  done
fi
