#!/usr/bin/env python3
"""Summarises a trial run: one row per trial, then the totals. Exit 0 when no
trial FAILED or ERRORed, 1 otherwise, and 3 when any setup.sh failed (the run is
incomplete, whatever the other verdicts); N/A trials (the scenario never reached the thing
under test) count as neither and are reported, because too many of them means
the scenario is unrealistic, not that the skill passed."""
import argparse
import json
import sys
from pathlib import Path

VERDICT = {0: "PASS", 1: "FAIL", 2: "N/A", 3: "SETUP", 4: "ERROR"}


def stream(path):
    cost = turns = 0
    denied, answer = 0, ""
    if not path.exists():
        return cost, turns, denied, answer
    for line in path.read_text(errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "user":
            for b in (e.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    c = b.get("content")
                    text = c if isinstance(c, str) else json.dumps(c)
                    if "hook error" in text or "permissionDecision" in text:
                        denied += 1
        elif e.get("type") == "result":
            cost, turns = e.get("total_cost_usd") or 0, e.get("num_turns") or 0
            answer = (e.get("result") or "").strip()
    return cost, turns, denied, answer


def main(out):
    rows, total, fails, na, errors, setups = [], 0.0, 0, 0, 0, 0
    for t in sorted(Path(out).glob("trial-*"), key=lambda p: int(p.name.split("-")[1])):
        rc_file = t / "check.rc"
        rc = int(rc_file.read_text().strip()) if rc_file.exists() else 3
        cost, turns, denied, answer = stream(t / "stream.jsonl")
        secs = (t / "seconds").read_text().strip() if (t / "seconds").exists() else "?"
        why = (t / "check.out").read_text().strip().splitlines()[-1:] if (t / "check.out").exists() else []
        first = answer.splitlines()[0][:100] if answer else ""
        # The session never ran (API unreachable, auth, jail): not a verdict on the skill.
        if answer.startswith("API Error") or (not answer and rc != 3 and (t / "stream.jsonl").exists()):
            rc, why = 4, [first or "no answer: read stderr.txt"]
        total += cost
        fails += rc == 1
        na += rc == 2
        errors += rc == 4
        setups += rc == 3
        rows.append(f"| {t.name} | {VERDICT.get(rc, rc)} | {why[0] if why else ''} | ${cost:.2f} | {turns} | {denied} | {secs}s | {first} |")
    print("| Trial | Verdict | Check | Cost | Turns | Hook denials | Wall | First line of the answer |")
    print("|---|---|---|---|---|---|---|---|")
    print("\n".join(rows))
    print(f"\n{len(rows)} trials, {fails} FAIL, {na} N/A, {errors} ERROR, {setups} SETUP, ${total:.2f}. Folder: {out}")
    if errors:
        print("ERROR means the session never ran: read stderr.txt in that trial before reading anything else.")
    if rows and na * 2 > len(rows):
        print("More than half the trials never reached the behaviour under test: make the scenario more realistic.")
    if setups:
        print("SETUP means setup.sh failed: read setup.log in that trial. The run is incomplete.")
        return 3
    return 1 if fails or errors else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Summarise a skill-trial output folder as one markdown table. "
                                 "Exit 1 when any trial FAILED or ERRORed, 3 when any setup.sh failed (wins over 1).")
    ap.add_argument("out", help="folder holding trial-N directories")
    sys.exit(main(ap.parse_args().out))
