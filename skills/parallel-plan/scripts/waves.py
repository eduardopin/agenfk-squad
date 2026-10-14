#!/usr/bin/env python3
"""Turn a plan's cards into waves of work that can run at the same time.

A card can start once every card it depends on is done. Two cards that touch
the same file or the same named resource (`state:...`, `contract:...`,
`serial:...`) never share a wave, even when neither depends on the other,
because two worktrees writing one file meet again as a merge conflict.

Input is JSON, from a file or `-` for stdin, in one of two shapes:

  * the output of `agenfk list --json`: each card's description carries the
    block the `scheduler` agent writes,
        Depends on: <id>, <id> (reason)
        Touches: path/file.py, dir/, docs/*.md, state:<name>
        Size: 3
    Ids may be shortened to any unique prefix. Cards with status DONE count
    as done.
  * {"cards": [{"id", "title", "depends_on", "touches", "size", "status"}]}

    waves.py plan.json
    agenfk list --project <id> --json | waves.py --parent <epic-id> -
    waves.py --json --max-width 3 --done a1b2c3d4 plan.json

A card with children (an epic, a story with tasks) is a container: only leaf
cards are scheduled, and depending on a container means depending on its
leaves. A card that waits on an open card outside `--parent` is reported as
blocked, never put in a wave.

Exit codes: 0 plan printed, 2 the input cannot be planned (a cycle, an unknown
or ambiguous id, a dependency line that cannot be read, no cards in scope, bad
JSON). Standard library only.
"""
import argparse
import fnmatch
import json
import re
import sys

DONE_STATUSES = {"DONE", "CLOSED", "ARCHIVED"}
NAMED = re.compile(r"^[a-z][a-z0-9_-]*:")
WILDCARD = re.compile(r"[*?\[]")


class PlanError(Exception):
    """The input describes a plan that cannot be scheduled."""


# --- reading -----------------------------------------------------------------

PLACEHOLDERS = {"none", "nothing", "n/a", "na", "-", "nenhum", "nenhuma"}
BLOCK_LINE = re.compile(r"\s*(?:(?:[-*+>]|\d+[.)])\s+)?(depends on|touches|size)\s*:\s*(.*)$", re.I)
MIN_PREFIX = 6   # a shorter word on a Depends on line is more likely prose than an id
REASON = re.compile(r"\([^)]*\)")


def _split(value):
    if isinstance(value, str):
        value = value.split(",")
    out = []
    for v in value or []:
        v = REASON.sub(" ", str(v)).strip().strip("`").strip()
        if v and v.lower() not in PLACEHOLDERS:
            out.append(v)
    return out


def _depends(value, cid):
    """Every id on a `Depends on:` line. A word that is not an id stops the
    plan: a dependency read wrong would put a card in a wave too early."""
    text = REASON.sub(" ", value)
    if text.strip().lower() in PLACEHOLDERS:
        return []
    ids = []
    for token in re.split(r"[,;\s]+", text):
        token = token.strip("`")
        if not token or token.lower() in ("and", "e", "&") or token.lower() in PLACEHOLDERS:
            continue
        if not re.fullmatch(r"[\w-]+", token):
            raise PlanError(f"card {cid}: cannot read {token!r} in 'Depends on: {value.strip()}'")
        ids.append(token)
    return ids


def _block(description, cid):
    deps, touches, size = [], [], 1
    for line in (description or "").splitlines():
        m = BLOCK_LINE.match(line.replace("**", "").replace("__", ""))
        if not m:
            continue
        key, value = m.group(1).lower(), m.group(2)
        if key == "depends on":
            deps += _depends(value, cid)
        elif key == "touches":
            touches += _split(value)
        else:
            size = value.strip()
    return deps, touches, size


def _size(value, cid):
    try:
        size = int(value)
    except (TypeError, ValueError):
        raise PlanError(f"card {cid}: size {value!r} is not a whole number")
    if size < 1:
        raise PlanError(f"card {cid}: size must be at least 1")
    return size


def load(data, parent=None):
    """Return {id: card}. Every dependency is resolved against the whole list,
    so a card may wait on one outside the planned scope. The scope is every
    leaf card (a card with children is a container, not work), or only the leaf
    descendants of `parent`."""
    if isinstance(data, dict):
        items = data.get("cards", data.get("items"))
        native = "cards" in data
    else:
        items, native = data, False
    if not isinstance(items, list):
        raise PlanError("expected a list of cards, or {\"cards\": [...]}")
    cards = {}
    for item in items:
        if not isinstance(item, dict) or item.get("id") in (None, ""):
            raise PlanError(f"every card needs an id: {item!r}")
        cid = str(item["id"])
        if cid in cards:
            raise PlanError(f"card {cid} appears twice")
        if native:
            deps, touches, size = _split(item.get("depends_on")), _split(item.get("touches")), item.get("size", 1)
        else:
            deps, touches, size = _block(item.get("description"), cid)
        cards[cid] = {"id": cid, "title": str(item.get("title") or ""), "depends_on": deps,
                      "touches": touches, "size": _size(size, cid),
                      "parent": str(item.get("parentId") or "") or None,
                      "done": str(item.get("status") or "").upper() in DONE_STATUSES}
    if not cards:
        raise PlanError("the plan has no cards")
    ids = list(cards)
    children = {cid: [] for cid in ids}
    for c in cards.values():
        if c["parent"] in children:
            children[c["parent"]].append(c["id"])

    def leaves(cid):
        out, stack, seen = [], [cid], set()
        while stack:
            n = stack.pop()
            if n in seen:          # parent links that loop, only possible in hand-written JSON
                continue
            seen.add(n)
            if children.get(n):
                stack += reversed(children[n])
            elif n != cid:
                out.append(n)
        return out

    for c in cards.values():
        c["refs"] = [resolve(ids, d, c["id"]) for d in c["depends_on"]]

    # A block written on a story or an epic binds every task under it: the leaf
    # inherits its ancestors' dependencies and touched files. Waiting on a
    # container means waiting on every leaf under it.
    for c in cards.values():
        if children[c["id"]]:
            continue
        chain, node = [c], c
        while node["parent"] in cards and len(chain) <= len(cards):
            node = cards[node["parent"]]
            chain.append(node)
        deps, touches = [], []
        for holder in chain:
            for ref in holder["refs"]:
                expanded = leaves(ref) if children[ref] else [ref]
                if c["id"] in expanded:
                    where = "" if holder is c else f" (written on {holder['id']})"
                    raise PlanError(f"card {c['id']} waits on {ref}, which contains it{where}")
                deps += [d for d in expanded if d not in deps]
            if not touches:        # a task's own files beat the story's, which list every task's
                touches = list(holder["touches"])
        c["depends_on"], c["touches"] = deps, touches
    if parent:
        known = ids + sorted({c["parent"] for c in cards.values() if c["parent"] and c["parent"] not in cards})
        hits = [i for i in known if i == parent] or [i for i in known if i.startswith(parent)]
        if not hits:
            raise PlanError(f"no card matches --parent {parent}")
        if len(hits) > 1:
            raise PlanError(f"--parent {parent} is ambiguous ({', '.join(hits)})")
        root = hits[0]
        scope = set(leaves(root)) if root in cards else {
            leaf for c in cards.values() if c["parent"] == root
            for leaf in (leaves(c["id"]) if children[c["id"]] else [c["id"]])}
    else:
        scope = {cid for cid in ids if not children[cid]}
    for cid, c in cards.items():
        c["scope"] = cid in scope
    return cards


def resolve(ids, ref, owner):
    if ref in ids:
        return ref
    if len(ref) < MIN_PREFIX:
        raise PlanError(f"card {owner} depends on {ref}, which is not in the plan "
                        f"(a shortened id needs at least {MIN_PREFIX} characters)")
    hits = [i for i in ids if i.startswith(ref)]
    if not hits:
        raise PlanError(f"card {owner} depends on {ref}, which is not in the plan")
    if len(hits) > 1:
        raise PlanError(f"card {owner}: {ref} is ambiguous ({', '.join(hits)})")
    return hits[0]


# --- arithmetic --------------------------------------------------------------

def _stem(entry):
    return WILDCARD.split(entry, 1)[0]


def overlap(a, b):
    """True when two Touches entries can name the same thing. Paths overlap by
    equality, a directory (`dir/`) over what is inside it, or a glob over what
    it can match; `kind:name` resources only by equality."""
    a, b = a.removeprefix("./"), b.removeprefix("./")
    if a == b:
        return True
    if NAMED.match(a) or NAMED.match(b):
        return False
    for x, y in ((a, b), (b, a)):
        if x.endswith("/") and y.startswith(x):
            return True
        if WILDCARD.search(x):
            if fnmatch.fnmatchcase(y, x):
                return True
            if WILDCARD.search(y) or y.endswith("/"):
                # two patterns, or a pattern and a directory: overlap unless their fixed parts diverge
                sx, sy = _stem(x), _stem(y)
                if sx.startswith(sy) or sy.startswith(sx):
                    return True
    return False


def shared(c1, c2):
    for a in c1["touches"]:
        for b in c2["touches"]:
            if overlap(a, b):
                return a if a == b else f"{a} ~ {b}"
    return None


def _cycle(cards, pending):
    """Refuse a dependency cycle and name it. `pending` is walked in input
    order, so the same plan always names the same cycle."""
    members, state = set(pending), {}
    for start in pending:
        if state.get(start):
            continue
        stack, path = [(start, iter(cards[start]["depends_on"]))], [start]
        state[start] = 1
        while stack:
            node, deps = stack[-1]
            nxt = next((d for d in deps if d in members), None)
            if nxt is None:
                state[node] = 2
                stack.pop()
                path.pop()
            elif state.get(nxt) == 1:
                loop = path[path.index(nxt):] + [nxt]
                raise PlanError("dependency cycle: " + " -> ".join(loop))
            elif not state.get(nxt):
                state[nxt] = 1
                stack.append((nxt, iter(cards[nxt]["depends_on"])))
                path.append(nxt)


def _topo(cards, pending):
    """Dependencies before dependents, in input order where free. Iterative, so a
    long chain cannot hit the recursion limit."""
    waiting = {c: sum(d in pending for d in cards[c]["depends_on"]) for c in cards if c in pending}
    dependents = {c: [] for c in waiting}
    for c in waiting:
        for d in cards[c]["depends_on"]:
            if d in pending:
                dependents[d].append(c)
    order = [c for c in waiting if not waiting[c]]
    for c in order:
        for n in dependents[c]:
            waiting[n] -= 1
            if not waiting[n]:
                order.append(n)
    return order


def plan(cards, max_width=4, done=None):
    if max_width < 1:
        raise PlanError("max width must be at least 1")
    done_ids = {c["id"] for c in cards.values() if c.get("done")}
    for ref in done or ():
        done_ids.add(resolve(list(cards), ref, "--done"))
    rank = {cid: i for i, cid in enumerate(cards)}
    scope = [cid for cid in cards if cards[cid].get("scope", True)]
    if not scope:
        raise PlanError("no cards to plan in this scope")
    open_ = [cid for cid in scope if cid not in done_ids]
    _cycle(cards, open_)

    # a card waiting on an open card outside the scope cannot start, nor can what waits on it
    in_open = set(open_)
    blocked = {cid: [d for d in cards[cid]["depends_on"] if d not in in_open and d not in done_ids]
               for cid in open_}
    blocked = {cid: ext for cid, ext in blocked.items() if ext}
    grew = True
    while grew:
        grew = False
        for cid in open_:
            if cid not in blocked and any(d in blocked for d in cards[cid]["depends_on"]):
                blocked[cid] = [d for d in cards[cid]["depends_on"] if d in blocked]
                grew = True
    pending = [cid for cid in open_ if cid not in blocked]
    pending_set = set(pending)
    order = _topo(cards, pending_set)
    deps = {cid: [d for d in cards[cid]["depends_on"] if d in pending_set] for cid in pending}

    # longest chain ending at each card (critical path) and starting at it (priority)
    head, prev = {}, {}
    for cid in order:
        best = max(deps[cid], key=lambda d: (head[d], -rank[d]), default=None)
        head[cid] = cards[cid]["size"] + (head[best] if best else 0)
        prev[cid] = best
    dependents = {cid: [] for cid in pending}
    for cid in pending:
        for d in deps[cid]:
            dependents[d].append(cid)
    tail = {}
    for cid in reversed(order):
        tail[cid] = cards[cid]["size"] + max((tail[n] for n in dependents[cid]), default=0)

    path = []
    end = max(pending, key=lambda c: (head[c], -rank[c]), default=None)
    while end:
        path.append(end)
        end = prev[end]
    path.reverse()

    waves, placed = [], set()
    while len(placed) < len(pending):
        ready = [c for c in pending if c not in placed and all(d in placed for d in deps[c])]
        ready.sort(key=lambda c: (-tail[c], rank[c]))
        wave = []
        for c in ready:
            if len(wave) == max_width:
                break
            if not any(shared(cards[c], cards[w]) for w in wave):
                wave.append(c)
        wave.sort(key=lambda c: rank[c])
        waves.append(wave)
        placed.update(wave)

    reach = {}
    for cid in order:
        reach[cid] = set(deps[cid]).union(*(reach[d] for d in deps[cid]))
    conflicts = []
    for i, a in enumerate(pending):
        for b in pending[i + 1:]:
            if a in reach[b] or b in reach[a]:
                continue
            res = shared(cards[a], cards[b])
            if res:
                conflicts.append({"cards": [a, b], "resource": res})

    return {
        "waves": waves,
        "ready": list(waves[0]) if waves else [],
        "critical_path": path,
        "critical_length": head[path[-1]] if path else 0,
        "conflicts": conflicts,
        "blocked": [{"card": cid, "waiting_on": blocked[cid],
                     "outside": [d not in in_open for d in blocked[cid]]} for cid in open_ if cid in blocked],
        "unplaced_touches": [f"{c[:8]} {cards[c]['title']}".rstrip() for c in pending if not cards[c]["touches"]],
        "done": [cid for cid in scope if cid in done_ids],
    }


# --- output ------------------------------------------------------------------

def render(cards, result):
    short = lambda c: c[:8]  # noqa: E731
    out = []
    for i, wave in enumerate(result["waves"], 1):
        out.append(f"Wave {i}" + (" (ready now)" if i == 1 else ""))
        out += [f"  {short(c)}  {cards[c]['title']}" for c in wave]
    if not result["waves"] and not result["blocked"]:
        out.append("Nothing left to schedule: every card is done.")
    if result["critical_path"]:
        out.append(f"Critical path (length {result['critical_length']}): "
                   + " -> ".join(short(c) for c in result["critical_path"]))
    if result["blocked"]:
        out.append("Blocked (waits on an open card outside this plan, directly or through another):")
        out += [f"  {short(b['card'])}  {cards[b['card']]['title']}  waits on "
                + ", ".join(f"{short(d)} ({'outside, open' if o else 'blocked'})"
                            for d, o in zip(b["waiting_on"], b["outside"])) for b in result["blocked"]]
    if result["conflicts"]:
        out.append("Kept apart by a shared resource:")
        out += [f"  {short(x['cards'][0])}, {short(x['cards'][1])}: {x['resource']}" for x in result["conflicts"]]
    if result["unplaced_touches"]:
        out.append("No Touches line, so collisions were not checked:")
        out += [f"  {u}" for u in result["unplaced_touches"]]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("path", help="JSON file, or - for stdin")
    ap.add_argument("--max-width", type=int, default=4, help="most cards in one wave (default 4)")
    ap.add_argument("--done", default="", help="comma-separated ids to treat as done")
    ap.add_argument("--parent", help="plan only the leaf cards under this epic or story (any unique id prefix)")
    ap.add_argument("--json", action="store_true", help="print the plan as JSON")
    args = ap.parse_args(argv)
    try:
        if args.path == "-":
            data = json.load(sys.stdin)
        else:
            with open(args.path) as fh:
                data = json.load(fh)
        cards = load(data, parent=args.parent)
        result = plan(cards, max_width=args.max_width, done=_split(args.done))
    except (PlanError, ValueError, OSError) as e:
        print(f"waves: {e}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2) if args.json else render(cards, result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
