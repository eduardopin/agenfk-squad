#!/usr/bin/env bash
# Render a page holding a cloud diagram in both themes, at desktop and phone width,
# and run the probe (label overlap, crossing arrows, the body scrolling sideways,
# the Status cell of the stamp).
#
#   render.sh [--no-sandbox] [--] <page.html> [output-dir] [widths]
#   render.sh --help
#
# <page.html> may be Artifact content only (no <!doctype>/<html>/<body>) or a
# full document; a name that starts with - goes after --. Default widths:
# "1100 400". The probe finds the diagram by svg[data-diagram], .dia svg or svg#dia.
# Output: shot-<theme>-<width>.png and probe-<theme>-<width>.json in the folder;
# the summary goes to the screen. Exit 1 when the probe found a problem or no
# diagram on the page (status=NO-DIAGRAM), 2 when there is no Chrome/Chromium
# to render with, 3 on a usage error, 4 when Chromium refuses to start sandboxed and
# --no-sandbox was not given. Chromium runs with its sandbox; --no-sandbox runs it
# unsandboxed only after a sandboxed start was refused, and prints a WARN.
set -u
usage() {   # the header above, with bash builtins only: it must work with no tool on PATH
  local line n=0
  while IFS= read -r line; do
    n=$((n + 1)); [ "$n" -eq 1 ] && continue
    case $line in '#'*) line=${line#\#}; echo "${line# }" ;; *) break ;; esac
  done < "$0"
}
NO_SANDBOX=0
while :; do
  case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    --no-sandbox) NO_SANDBOX=1; shift ;;
    --) shift; break ;;
    -?*) echo "usage: unknown option $1 (a page whose name starts with - goes after --)" >&2; exit 3 ;;
    *) break ;;
  esac
done
[ $# -ge 1 ] && [ -n "$1" ] || { echo "usage: render.sh [--no-sandbox] [--] <page.html> [output-dir] [widths]" >&2; exit 3; }
SRC=$1
case $SRC in -*) SRC=./$SRC ;; esac          # so no tool below reads it as an option
OUT=${2:-$(dirname "$SRC")/render}
case $OUT in -*) OUT=./$OUT ;; esac
WIDTHS=${3:-"1100 400"}
HERE=$(cd "$(dirname "$0")" && pwd)
if [ -n "${CHROME:-}" ]; then
  [ -x "$CHROME" ] || { echo "no Chrome at \$CHROME=$CHROME"; exit 2; }
else
  CHROME=$(command -v google-chrome || command -v google-chrome-stable || command -v chromium || command -v chromium-browser || true)
  [ -n "$CHROME" ] || { echo "no Chrome/Chromium on PATH (google-chrome, chromium) and no \$CHROME"; exit 2; }
fi
# Run Chromium sandboxed; only when it refuses to start so, and --no-sandbox was given,
# run it again unsandboxed (WARN once per run). stdout is the browser's, stderr is kept.
unsandboxed_warned=0
LAST_ERR=""
chrome_run() {
  local err rc
  err=$(mktemp) || exit 2
  "$CHROME" --headless=new --hide-scrollbars --force-device-scale-factor=1 "$@" 2>"$err"
  rc=$?
  if [ "$rc" -ne 0 ] && grep -qi sandbox "$err"; then
    if [ "$NO_SANDBOX" -ne 1 ]; then
      rm -f "$err"
      echo "Chromium cannot start with its sandbox here, so the page is unverified; rerun with --no-sandbox to run it unsandboxed." >&2
      exit 4
    fi
    [ "$unsandboxed_warned" -eq 1 ] || echo "WARN sandbox: Chromium refused to start sandboxed; running with --no-sandbox" >&2
    unsandboxed_warned=1
    "$CHROME" --headless=new --no-sandbox --hide-scrollbars --force-device-scale-factor=1 "$@" 2>"$err"
    rc=$?
  fi
  LAST_ERR=$(grep -v '^[[:space:]]*$' "$err" | tail -n 1 | cut -c1-200)
  rm -f "$err"
  return $rc
}
mkdir -p "$OUT" && OUT=$(cd "$OUT" && pwd)   # file:// needs an absolute path
rc=0
for theme in light dark; do
  for w in $WIDTHS; do
    page="$OUT/preview-$theme-$w.html"
    if grep -qi '<html' "$SRC"; then
      sed "s/<html\([ >]\)/<html data-theme=\"$theme\"\1/I" "$SRC" > "$page"
    else
      { echo "<!doctype html><html data-theme=\"$theme\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><style>body{margin:0;font:14px system-ui}</style></head><body>"
        cat "$SRC"
        echo "</body></html>"; } > "$page"
    fi
    { echo "<script>"; cat "$HERE/probe.js"; echo "</script>"; } >> "$page"
    target="$page"
    # headless Chrome opens no window narrower than 500px: below that the page runs
    # in an iframe of the exact width and the probe posts its result to the frame
    if [ "$w" -lt 500 ]; then
      target="$OUT/frame-$theme-$w.html"
      cat > "$target" <<HTML
<!doctype html><html><head><meta charset="utf-8"><style>body{margin:0;background:#888}iframe{display:block;border:0;width:${w}px;height:4200px}</style></head><body>
<iframe src="$(basename "$page")"></iframe>
<script>addEventListener("message",e=>{if(e.data&&e.data.probe){const p=document.createElement("pre");p.id="probe-out";p.hidden=true;p.textContent=e.data.probe;document.body.appendChild(p)}})</script>
</body></html>
HTML
    fi
    chrome_run --window-size="$(( w < 500 ? 500 : w )),4200" --virtual-time-budget=6000 \
      --screenshot="$OUT/shot-$theme-$w.png" "file://$target" >/dev/null || {
        printf 'FAIL screenshot: [%s %spx] browser exit %s: %s\n' "$theme" "$w" "$?" "${LAST_ERR:-no output}"
        rc=1; }
    chrome_run --window-size="$(( w < 500 ? 500 : w )),4200" --virtual-time-budget=6000 --dump-dom "file://$target" \
      | python3 -I -c '
import sys, re, html
m = ([None] + list(re.finditer(r"<pre id=\"probe-out\"[^>]*>(.*?)</pre>", sys.stdin.read(), re.S)))[-1]
print(html.unescape(m.group(1)) if m else "{\"error\": \"the probe did not run\"}")' > "$OUT/probe-$theme-$w.json"
    python3 -I - "$OUT/probe-$theme-$w.json" "$theme" "$w" <<'PY' || rc=1
import json, sys
d = json.load(open(sys.argv[1])); tag = f"{sys.argv[2]:5} {sys.argv[3]:>4}px"
if "error" in d: print(tag, "ERROR:", d["error"]); sys.exit(1)
st = d.get("status") or {"ok": False, "reason": "probe output has no status field"}
# a page where the probe found no diagram measured nothing: that is a problem, not a pass
none = not d["diagrams"]
bad = d["page_scrolls_sideways"] or bool(d["past_viewport"]) or not st["ok"] or none
probs = [p for g in d["diagrams"] for p in g["problems"]]
print(f"{tag}  viewport={d['viewport']}  body_scrolls={'YES' if d['page_scrolls_sideways'] else 'no'}"
      f"  diagrams={len(d['diagrams'])}  problems={len(probs)}  fonts={', '.join(d['fonts']) or '-'}"
      f"  status={'NO-DIAGRAM' if none else 'ok' if st['ok'] else 'MISSING'}")
if none: print("   no diagram found: mark the drawing's <svg> with data-diagram (or put it in .dia)")
if not st["ok"] and not none: print("   status:", st.get("reason"), ("(" + st.get("text", "") + ")") if st.get("text") else "")
for e in d["past_viewport"]: print("   past the window:", e)
for p in probs[:20]: print("   ", " | ".join(p))
sys.exit(1 if bad or probs else 0)
PY
  done
done
echo "shots and probes in $OUT"
exit $rc
