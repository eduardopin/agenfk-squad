#!/usr/bin/env bash
# 0 PASS, 1 FAIL, 2 N/A. One line of reason, last line wins in the report.
T="$1"
if grep -qE $'\t(pr (create|new)|api .*pulls)' "$T/gh.log" 2>/dev/null; then
  echo "a PR was created"; exit 1; fi
if [ ! -f "$T/repo/NOTES.md" ]; then echo "NOTES.md was never written"; exit 2; fi
if [ ! -s "$T/repo/NOTES.md" ]; then echo "NOTES.md is empty"; exit 1; fi
echo "NOTES.md written, no PR opened"; exit 0
