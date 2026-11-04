#!/usr/bin/env bash
# Links the squad into the user level of Claude Code. Idempotent.
# Never overwrites a real file: it only creates or updates symlinks.
set -euo pipefail

SQUAD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"

link() {
  local src="$1" dst="$2" root real
  # Only what lives in this repository gets linked. A source that is itself a
  # symlink resolving elsewhere (a skill directory pointing at a download, say)
  # would put code nobody reviewed into every session.
  root="$(cd "$SQUAD" && pwd -P)"
  if command -v realpath >/dev/null 2>&1; then
    real="$(realpath "$src" 2>/dev/null)" || real=""
  else
    real="$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$src" 2>/dev/null)" || real=""
  fi
  case "$real" in
    "$root"/*) ;;
    *)
      echo "REFUSED $src resolves to ${real:-(unresolvable)}, outside the squad repository ($root)." >&2
      echo "        A third-party skill goes in as a copy under skills/vendor/<name>/, with a line in" >&2
      echo "        skills/vendor/LOCK (origin, commit, sha256 from python3 tests/test_vendor.py --hash" >&2
      echo "        skills/vendor/<name>). See \"Proposing a skill\" in CONTRIBUTING.md. The rest is still linked." >&2
      return 1
      ;;
  esac
  if [ -e "$dst" ] && [ ! -L "$dst" ]; then
    echo "SKIPPED $dst exists and is not a symlink — resolve it by hand" >&2
    return 1
  fi
  ln -sfn "$src" "$dst"
  echo "ok      $dst -> $src"
}

# The squad is built on AgenFK: every card, gate and verdict goes through its CLI.
# A missing CLI is a warning, not a failure: the links below are still useful.
if ! command -v agenfk >/dev/null 2>&1; then
  echo "WARNING the 'agenfk' CLI is not on PATH. The squad is built on AgenFK:" >&2
  echo "        install it from https://github.com/cglab-public/agenfk before using the agents." >&2
  echo >&2
fi

mkdir -p "$CLAUDE/agents" "$CLAUDE/hooks" "$CLAUDE/skills"
rc=0
for f in "$SQUAD"/agents/*.md; do
  link "$f" "$CLAUDE/agents/$(basename "$f")" || rc=1
done
# the squad's own skills (one directory per skill) and the third-party ones in vendor/
for d in "$SQUAD"/skills/*/ "$SQUAD"/skills/vendor/*/; do
  name="$(basename "$d")"
  [ "$name" = "vendor" ] && continue
  [ -f "$d/SKILL.md" ] || continue
  link "${d%/}" "$CLAUDE/skills/$name" || rc=1
done
link "$SQUAD/CLAUDE.md" "$CLAUDE/CLAUDE.md" || rc=1
link "$SQUAD/hooks/squad-router.py" "$CLAUDE/hooks/squad-router.py" || rc=1
link "$SQUAD/hooks/squad-watch.py" "$CLAUDE/hooks/squad-watch.py" || rc=1
link "$SQUAD/hooks/squad-switch.py" "$CLAUDE/hooks/squad-switch.py" || rc=1
link "$SQUAD/hooks/squad-turn.py" "$CLAUDE/hooks/squad-turn.py" || rc=1
link "$SQUAD/hooks/squad-seal.py" "$CLAUDE/hooks/squad-seal.py" || rc=1
link "$SQUAD/hooks/squad-wake.py" "$CLAUDE/hooks/squad-wake.py" || rc=1
link "$SQUAD/hooks/squad-compact.py" "$CLAUDE/hooks/squad-compact.py" || rc=1
mkdir -p "$HOME/.local/bin"       # the merge queue is called by name: `squad-turn request`
link "$SQUAD/hooks/squad-turn.py" "$HOME/.local/bin/squad-turn" || rc=1
link "$SQUAD/hooks/squad-seal.py" "$HOME/.local/bin/squad-seal" || rc=1

# squad-wake: a scan every 2 min that calls back a session that stopped without
# delivering and unsticks a stuck switch or pickup. It needs a clock outside the
# sessions (if they all stop, no hook runs): a systemd --user timer on Linux.
# Only when the user manager runs with this HOME: units written under another
# HOME (a sandbox, a test) would never run, and enabling them would reach the
# real manager.
manager_home=""
if command -v systemctl >/dev/null 2>&1; then
  manager_env="$(systemctl --user show-environment 2>/dev/null)" || manager_env=""
  manager_home="$(printf '%s\n' "$manager_env" | sed -n 's/^HOME=//p')"
fi
wake_cmd="python3 $CLAUDE/hooks/squad-wake.py"
unit="$HOME/.config/systemd/user"
# a unit is (re)written only when it is missing or was written by this installer
# (marker on its first line): a unit of your own is left alone, like any real file
unit_mark="# Written by agenfk-squad install.sh; rewritten on every install. Settings go in wake.env."
ours() {
  if [ -L "$1" ]; then return 1; fi
  if [ ! -e "$1" ]; then return 0; fi
  [ -f "$1" ] && [ "$(head -n 1 "$1")" = "$unit_mark" ]
}
units_ok=1
if [ -n "$manager_home" ] && [ "$manager_home" = "$HOME" ]; then
  for u in squad-wake.service squad-wake.timer; do
    if ! ours "$unit/$u"; then
      echo "SKIPPED $unit/$u exists and was not written by this installer — resolve it by hand" >&2
      units_ok=0
    fi
  done
fi
if [ -z "$manager_home" ] || [ "$manager_home" != "$HOME" ]; then
  echo "SKIPPED squad-wake.timer: no systemd --user manager for this HOME. Run $wake_cmd every 2 min" >&2
  echo "        yourself (cron: '*/2 * * * * $wake_cmd'; launchd on macOS). See docs/watcher.md." >&2
elif [ "$units_ok" = 0 ]; then
  rc=1                                   # the timer is left as it is: not reloaded, not enabled
else
  mkdir -p "$unit"
  herdr_bin="$(command -v herdr || true)"
  cat > "$unit/squad-wake.service" <<UNIT
$unit_mark
[Unit]
Description=squad-wake: calls back the Claude session that stopped without delivering

[Service]
Type=oneshot
Environment=HERDR_BIN_PATH=${herdr_bin:-herdr}
# the timer does not inherit the shell's environment: SQUAD_WAKE=0 and the thresholds go in this file
EnvironmentFile=-%h/.claude/squad/wake.env
ExecStart="$(command -v python3)" "$CLAUDE/hooks/squad-wake.py"
UNIT
  cat > "$unit/squad-wake.timer" <<UNIT
$unit_mark
[Unit]
Description=squad-wake every 2 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
UNIT
  if systemctl --user daemon-reload && systemctl --user enable --now squad-wake.timer >/dev/null; then
    echo "ok      squad-wake.timer (every 2 min; to turn it off: SQUAD_WAKE=0 in ~/.claude/squad/wake.env)"
  else
    echo "FAILED  squad-wake.timer" >&2
    rc=1
  fi
  # the timer runs outside the pane: herdr has to answer without the pane's environment
  if [ -n "$herdr_bin" ] && ! env -i HOME="$HOME" PATH=/usr/bin:/bin "$herdr_bin" agent list >/dev/null 2>&1; then
    echo "WARNING herdr did not answer outside the pane: squad-wake will log 'herdr did not answer'" >&2
  fi
fi

# oh-my-pi (omp) reads a different agent dialect at ~/.omp/agent/agents, and skips
# .claude/agents on purpose. Render the agents into its contract; see
# skills/harness-port/SKILL.md for the tool and model mapping.
if command -v omp >/dev/null 2>&1; then
  mkdir -p "$HOME/.omp/agent/extensions"
  # The watcher extension delegates every decision to hooks/squad-watch-omp.py.
  link "$SQUAD/extensions/squad-watch-omp.ts" \
       "$HOME/.omp/agent/extensions/squad-watch-omp.ts" || rc=1
  if python3 "$SQUAD/skills/harness-port/scripts/omp_agents.py" render \
       --src "$SQUAD/agents" --out "$HOME/.omp/agent/agents"; then
    echo "omp agents rendered into $HOME/.omp/agent/agents."
    echo "Point omp's model roles at an allowed model (DGX or OpenRouter):"
    echo "  modelRoles: { default: dgx-spark/GLM-5.3-Flash-EXL3, smol: dgx-spark/GLM-5.3-Flash-EXL3 }"
  else
    rc=1
  fi
else
  echo "note    omp not on PATH; skipped the oh-my-pi agent adapter"
fi

echo
echo "New agents and skills only show up in sessions started after this step."
echo "Hooks in settings.json (see docs/installation.md): squad-router.py on UserPromptSubmit;"
echo "squad-watch.py on PreToolUse, PostToolUse, PostToolUseFailure, SubagentStop, Stop, PreCompact and UserPromptSubmit."
echo "Register PostToolUseFailure too: without it, a merge that fails holds the merge turn until"
echo "it expires (SQUAD_TURN_HOLD, 120 s)."
echo "Changed in the merge turn: SQUAD_TURN_IDLE, SQUAD_TURN_MAX and SQUAD_TURN_CLAIM were removed;"
echo "SQUAD_TURN_HOLD, SQUAD_SEAL, \`git config squad.seal\` and \`git config squad.trunk\` are new"
echo "(see docs/configuration.md)."
exit $rc
