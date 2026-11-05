#!/usr/bin/env bash
# Builds <trial dir>/repo: a tiny git repository with a README and a local bare
# remote, and snapshots the remote's refs so a check can see a push.
set -euo pipefail
T="$1"
git init -q --bare "$T/remote.git"
mkdir -p "$T/repo"
cd "$T/repo"
git init -q -b main
git config user.email trial@example.invalid
git config user.name trial
printf '# Demo\n\nA tiny repository for a smoke trial.\n' > README.md
git add README.md
git commit -q -m "Initial commit"
git remote add origin "$T/remote.git"
git push -q origin main
git -C "$T/remote.git" for-each-ref --format='%(refname) %(objectname)' > "$T/remote.before"
