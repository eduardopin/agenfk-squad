#!/usr/bin/env bash
# Runs N blind trials of a scenario: each one a fresh headless Claude Code
# session in its own copy of the scenario's repository, with a stub gh, no AWS
# credentials, no user hooks and no MCP. Nothing a trial does leaves its folder.
#
#   trial.sh --scenario <dir> [--n 3] [--plugin-dir <dir>]... [--model <id>]
#            [--budget <usd per trial>] [--out <dir>] [--dry]
#
# --dry builds the trial folders and runs check.sh without starting Claude, to
# prove a scenario's setup and check before spending money on it.
# --out defaults to $SKILL_TRIAL_OUT (else ~/.cache/skill-trial) plus a
# scenario-and-time folder name. A scenario must live under HOME: the jail's
# /tmp is empty.
set -uo pipefail
usage() { sed -n '2,/^set -uo/p' "${BASH_SOURCE[0]}" | grep '^#' | sed 's/^# \{0,1\}//'; }
case "${1:-}" in -h|--help) usage; exit 0 ;; esac
command -v bwrap >/dev/null || { echo "skill-trial needs bwrap (apt install bubblewrap)" >&2; exit 69; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
N=3 MODEL=claude-opus-5-5 BUDGET=3 OUT="" SCEN="" DRY=0 PLUGINS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --scenario) SCEN="$(cd "$2" && pwd -P)"; shift 2 ;;
    --n) N="$2"; shift 2 ;;
    --plugin-dir) PLUGINS+=(--plugin-dir "$(cd "$2" && pwd -P)"); shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --budget) BUDGET="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --dry) DRY=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done
[ -n "$SCEN" ] && [ -x "$SCEN/setup.sh" ] && [ -f "$SCEN/prompt.txt" ] && [ -x "$SCEN/check.sh" ] || {
  echo "a scenario needs setup.sh, prompt.txt and check.sh (executable)" >&2; exit 64; }
OUT="${OUT:-${SKILL_TRIAL_OUT:-$HOME/.cache/skill-trial}/$(basename "$SCEN")-$(date +%Y%m%d-%H%M%S)}"
# Resolved outside the jail and bound into it: a binary under /tmp would vanish
# behind the jail's private /tmp. Tests point SKILL_TRIAL_CLAUDE at a fake.
CLAUDE_BIN="$(readlink -f "${SKILL_TRIAL_CLAUDE:-$(command -v claude)}")"
[ -x "$CLAUDE_BIN" ] || { echo "claude not found" >&2; exit 69; }
# A bind of HOME itself, or of a parent of it, would undo the read-only HOME.
mkdir -p "$OUT"; OUT="$(cd "$OUT" && pwd -P)"
for p in "$OUT" "$(dirname "$CLAUDE_BIN")"; do
  case "$HOME/" in "$p"/*) echo "refusing: $p is HOME or contains it" >&2; exit 64 ;; esac
done
# OUT is bound writable inside the jail, so it must not be, hold or sit inside
# a path whose contents steer the user's tools.
for d in .claude .config .local .ssh .aws; do
  q="$(readlink -f "$HOME/$d")"
  case "$OUT/" in "$q"/*) echo "refusing: --out $OUT is inside $q" >&2; exit 64 ;; esac
  case "$q/" in "$OUT"/*) echo "refusing: --out $OUT contains $q" >&2; exit 64 ;; esac
done
mkdir -p "$OUT/ghcfg"

# The trial's Bash runs as the user. bwrap gives it its own PID namespace and a
# fresh /proc (the host's /proc/<pid>/root would lead straight back to the real
# HOME), a private /tmp, /dev/shm and /run/user, no /mnt (the Windows drives),
# the whole filesystem read-only with every credential path under HOME emptied,
# and writable only those private tmpfs mounts, the output folder and the trial
# folder. ~/.claude.json is a
# per-trial copy kept outside $OUT and deleted after the trial. The OAuth file
# stays the real one: the claude process needs it and a refresh rotates it, so
# the token is readable inside the jail. The credential list below is a list,
# not a rule: a secret kept anywhere else under HOME stays readable. Network
# stays open for the API; with the keys hidden, a push or an API call has nothing to sign with.
jail() {
  local T="$1" cfg="$2"
  local a=(--ro-bind / / --dev /dev --tmpfs /dev/shm --unshare-pid --proc /proc --new-session --die-with-parent
           --tmpfs /tmp --tmpfs "/run/user/$(id -u)" --ro-bind "$HOME" "$HOME"
           # Every bind that opens a path goes before the masks below, so none
           # of them can bring a masked credential path back.
           --ro-bind "$(dirname "$CLAUDE_BIN")" "$(dirname "$CLAUDE_BIN")"
           --bind "$OUT" "$OUT" --bind "$T" "$T")
  # /mnt holds the Windows drives. On WSL /etc/resolv.conf points into /mnt/wsl,
  # so that one file comes back or the trial has no DNS.
  if [ -d /mnt ]; then
    a+=(--tmpfs /mnt)
    local r; r="$(readlink -f /etc/resolv.conf)"
    [[ "$r" == /mnt/* && -f "$r" ]] && a+=(--ro-bind "$r" "$r")
  fi
  local d
  for d in .ssh .aws .config/gh .kube .docker .gnupg infra .netrc .git-credentials \
           .config/git/credentials .azure .config/m365 .local/share/keyrings \
           .npmrc .terraform.d .terraformrc .pypirc .config/glab-cli .config/op .config/atlassian; do
    if [ -d "$HOME/$d" ]; then a+=(--tmpfs "$HOME/$d")
    elif [ -e "$HOME/$d" ]; then a+=(--ro-bind /dev/null "$HOME/$d"); fi
  done
  for d in cache debug file-history paste-cache projects session-env shell-snapshots \
           telemetry todos statsig plans tasks; do
    [ -d "$HOME/.claude/$d" ] && a+=(--tmpfs "$HOME/.claude/$d")
  done
  # A scenario that brings its own ANTHROPIC_API_KEY never needs the OAuth file,
  # so it is masked; otherwise the claude process needs it and a refresh rotates it.
  if [ -f "$HOME/.claude/.credentials.json" ]; then
    if grep -qE '^ANTHROPIC_API_KEY=' "$SCEN/env" 2>/dev/null; then
      a+=(--ro-bind /dev/null "$HOME/.claude/.credentials.json")
    else
      a+=(--bind "$HOME/.claude/.credentials.json" "$HOME/.claude/.credentials.json")
    fi
  fi
  if [ -f "$HOME/.claude.json" ]; then cp "$HOME/.claude.json" "$cfg"; a+=(--bind "$cfg" "$HOME/.claude.json"); fi
  printf '%s\0' "${a[@]}"
}

one() {
  local i="$1" T="$OUT/trial-$1" start=$SECONDS
  mkdir -p "$T"
  "$SCEN/setup.sh" "$T" > "$T/setup.log" 2>&1 || { echo "setup failed" > "$T/check.out"; echo 3 > "$T/check.rc"; return; }
  : > "$T/gh.log"
  if [ "$DRY" = 0 ]; then
    local cfg; cfg="$(mktemp)"; local JAIL=(); mapfile -d "" JAIL < <(jail "$T" "$cfg")
    # env -i: the calling session's environment can carry the settings.json "env"
    # block, and a variable there that switches the hook under test off turns
    # every trial into a false FAIL. A trial gets only what a fresh terminal
    # would give it.
    ( cd "$T/repo" && env -i HOME="$HOME" USER="${USER:-}" LANG="${LANG:-C.UTF-8}" TERM=dumb \
        ${NODE_EXTRA_CA_CERTS:+NODE_EXTRA_CA_CERTS="$NODE_EXTRA_CA_CERTS"} \
        ${SSL_CERT_FILE:+SSL_CERT_FILE="$SSL_CERT_FILE"} \
        $( [ -f "$SCEN/env" ] && grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$SCEN/env" ) \
        AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null GH_CONFIG_DIR="$OUT/ghcfg" \
        GIT_CONFIG_GLOBAL=/dev/null GIT_SSH_COMMAND=false GIT_TERMINAL_PROMPT=0 \
        GH_STUB_LOG="$T/gh.log" GH_STUB_RESPONSES="$SCEN/gh.json" \
        PATH="$SCEN/bin:$HERE/bin:$PATH" \
        timeout 900 bwrap "${JAIL[@]}" "$CLAUDE_BIN" -p "$(cat "$SCEN/prompt.txt")" --model "$MODEL" \
          --setting-sources project "${PLUGINS[@]}" --strict-mcp-config \
          --permission-mode acceptEdits --add-dir /tmp \
          --allowedTools Bash Read Grep Glob Skill Write Edit \
          --disallowedTools WebFetch WebSearch Agent \
          --max-budget-usd "$BUDGET" --no-session-persistence \
          --output-format stream-json --verbose > "$T/stream.jsonl" 2> "$T/stderr.txt" )
    rm -f "$cfg"
  fi
  echo $((SECONDS - start)) > "$T/seconds"
  "$SCEN/check.sh" "$T" > "$T/check.out" 2>&1; echo $? > "$T/check.rc"
}

for i in $(seq 1 "$N"); do one "$i" & done
wait
python3 "$HERE/report.py" "$OUT"
