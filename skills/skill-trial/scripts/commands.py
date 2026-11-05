#!/usr/bin/env python3
"""Prints every Bash command a trial ran, one per line, so a check.sh can grep
what the session did rather than what it wrote in its answer."""
import argparse
import json
import sys

ap = argparse.ArgumentParser(description="Print every Bash command a trial ran, one per line.")
ap.add_argument("stream", help="a trial's stream.jsonl")
for line in open(ap.parse_args().stream, errors="replace"):
    try:
        e = json.loads(line)
    except ValueError:
        continue
    if e.get("type") != "assistant":
        continue
    for b in (e.get("message") or {}).get("content") or []:
        if b.get("type") == "tool_use" and b.get("name") == "Bash":
            print(" ".join(str(b["input"].get("command", "")).split()))
