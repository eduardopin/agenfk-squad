#!/usr/bin/env python3
"""Summarises a trial run: one row per trial, then the totals. Exit 0 when no
trial FAILED or ERRORed, 1 otherwise, and 3 when any setup.sh failed or no trial
ran at all (the run is incomplete, whatever the other verdicts); N/A trials (the
scenario never reached the thing under test) count as neither and are reported,
because too many of them means the scenario is unrealistic, not that the skill
passed. A cost the stream does not state is shown as ?, never as $0.00."""
import argparse
import json
import sys
from pathlib import Path

VERDICT = {0: "PASS", 1: "FAIL", 2: "N/A", 3: "SETUP", 4: "ERROR"}


def stream(path):
    """cost is None when a session ran but its stream states no cost (killed, timed
    out, or a result event without one); 0 when no session ran (no stream file)."""
    cost, turns = (None, 0) if path.exists() else (0, 0)
    denied, answer, failed = 0, "", False
    if not path.exists():
        return cost, turns, denied, answer, failed
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
            c = e.get("total_cost_usd")
            cost = c if isinstance(c, (int, float)) and not isinstance(c, bool) else None
            turns = e.get("num_turns") or 0
            answer = (e.get("result") or "").strip()
            # claude reports auth and API failures as a result with is_error ("Not logged in")
            failed = e.get("is_error") is True
    return cost, turns, denied, answer, failed


def main(out):
    rows, total, unknown, fails, na, errors, setups = [], 0.0, 0, 0, 0, 0, 0
    trials = [p for p in Path(out).glob("trial-*") if p.name.split("-")[1].isdigit()]
    for t in sorted(trials, key=lambda p: int(p.name.split("-")[1])):
        rc_file = t / "check.rc"
        raw = rc_file.read_text().strip() if rc_file.exists() else "3"
        rc = int(raw) if raw.isdigit() else raw
        cost, turns, denied, answer, failed = stream(t / "stream.jsonl")
        secs = (t / "seconds").read_text().strip() if (t / "seconds").exists() else "?"
        why = (t / "check.out").read_text().strip().splitlines()[-1:] if (t / "check.out").exists() else []
        first = answer.splitlines()[0][:100] if answer else ""
        # check.sh crashed (127, a signal, garbage in check.rc): no verdict was given.
        if rc not in VERDICT:
            rc, why = 4, [f"check.sh exited {rc or 'with nothing'}" + (f": {why[0]}" if why else "")]
        # The session never ran (API unreachable, auth, jail): not a verdict on the skill.
        elif rc != 3 and (failed or answer.startswith("API Error")
                          or (not answer and (t / "stream.jsonl").exists())):
            rc, why = 4, [first or "no answer: read stderr.txt"]
        if cost is None:
            unknown += 1
        else:
            total += cost
        fails += rc == 1
        na += rc == 2
        errors += rc == 4
        setups += rc == 3
        price = "?" if cost is None else f"${cost:.2f}"
        rows.append(f"| {t.name} | {VERDICT[rc]} | {why[0] if why else ''} | {price} | {turns} | {denied} | {secs}s | {first} |")
    if not rows:
        print(f"No trial-N folder in {out}: no trial ran, so there is nothing to report.")
        return 3
    print("| Trial | Verdict | Check | Cost | Turns | Hook denials | Wall | First line of the answer |")
    print("|---|---|---|---|---|---|---|---|")
    print("\n".join(rows))
    spent = f"${total:.2f}" + (" + ?" if unknown else "")
    print(f"\n{len(rows)} trials, {fails} FAIL, {na} N/A, {errors} ERROR, {setups} SETUP, {spent}. Folder: {out}")
    if unknown:
        print(f"Cost unknown for {unknown} trial(s) (no cost in stream.jsonl: killed or timed out): "
              "the total is incomplete.")
    if errors:
        print("ERROR means the session or its check never gave a verdict (API, auth, jail, a crashed check.sh): "
              "read stderr.txt and check.out in that trial before reading anything else.")
    if rows and na * 2 > len(rows):
        print("More than half the trials never reached the behaviour under test: make the scenario more realistic.")
    if setups:
        print("SETUP means setup.sh failed: read setup.log in that trial. The run is incomplete.")
        return 3
    return 1 if fails or errors else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Summarise a skill-trial output folder as one markdown table. "
                                 "Exit 1 when any trial FAILED or ERRORed, 3 when any setup.sh failed "
                                 "or no trial ran (wins over 1).")
    ap.add_argument("out", help="folder holding trial-N directories")
    sys.exit(main(ap.parse_args().out))
