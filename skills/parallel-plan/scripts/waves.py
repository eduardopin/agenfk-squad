#!/usr/bin/env python3
"""Read cards from JSON and propose deterministic waves; never dispatch or mutate.

A list, {"items": [...]} or {"cards": [...]} accepts id, parentId, status,
depends_on, touches, size, and a description with Depends on:/Touches:/Size:.
Parents pass their constraints to every leaf. DONE/CLOSED satisfy dependencies;
blocked, paused, cancelled, archived or trashed cards do not. Unknown Touches
serialize conservatively. --parent plans only the leaves under one container:
an open card outside it blocks what waits on it. --done names merged cards the
board has not closed yet. Exit 0 is a forecast, 2 means invalid input or cycle.
"""
import argparse
from collections import deque
import fnmatch
import heapq
import json
from pathlib import Path
import posixpath
import re
import sys

BLOCKED = {'BLOCKED', 'PAUSED', 'CANCELLED', 'CANCELED', 'ARCHIVED', 'TRASHED', 'IDEAS'}
DONE = {'DONE', 'CLOSED'}
NAMED = re.compile(r'^[a-z][a-z0-9_-]*:')
WILDCARD = re.compile(r'[*?\[]')
NOTHING = {'none', 'nothing', 'nenhum', 'nenhuma', 'n/a', 'na', '-'}
JOINERS = {'and', 'e', '&'}
# A shorter word on a Depends on line is more likely prose than an id prefix.
MIN_PREFIX = 6


def tokens(value, field):
    if isinstance(value, str):
        parts, start, depth = [], 0, 0
        for i, char in enumerate(value):
            if char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
                if depth < 0:
                    raise ValueError(f'{field}: unbalanced annotation')
            elif char == ',' and depth == 0:
                parts.append(value[start:i]); start = i + 1
        if depth:
            raise ValueError(f'{field}: unbalanced annotation')
        value = parts + [value[start:]]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f'{field} must be strings or a comma-separated string')
    return [v.strip().strip('`') for v in value if v.strip().strip('`')]


def block(text):
    if not isinstance(text, str):
        raise ValueError('description must be text')
    deps, touches, size, fence = [], [], None, None
    for line in text.splitlines():
        f = re.match(r'^\s*(`{3,}|~{3,})(.*)$', line)
        if f:
            marker = f.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence) and not f.group(2).strip():
                fence = None
            continue
        if fence:
            continue
        line = re.sub(r'^\s*(?:[-*+>]|\d+[.)])\s+', '', line).strip()
        # Bold may wrap the key or the whole line; a ** inside a path is a glob.
        m = re.match(r'^(\*\*)?(Depends on|Touches|Size)(\*\*)?:(\*\*)?\s*(.*)$', line, re.I)
        if not m:
            continue
        key, value = m.group(2).lower(), m.group(5)
        if m.group(1) and not (m.group(3) or m.group(4)) and value.endswith('**'):
            value = value[:-2]
        if key == 'depends on':
            deps.extend(depends(value))
        elif key == 'touches':
            touches.extend(t for t in tokens(value, key) if t.casefold() not in NOTHING)
        else:
            size = value
    return deps, touches, size


def depends(value):
    """Every id on a Depends on line. A word that is not an id fails: reading
    only the first word of `a and b` would put b's dependent in a wave early."""
    ids = []
    for token in tokens(value, 'depends on'):
        while re.search(r'\([^()]*\)', token):
            token = re.sub(r'\([^()]*\)', ' ', token)
        if token.strip().casefold() in NOTHING:
            continue
        for word in re.split(r'[;\s]+', token):
            word = word.strip('`')
            if not word or word.casefold() in JOINERS | NOTHING:
                continue
            if not re.fullmatch(r'[\w-]+', word):
                raise ValueError(f'Depends on: cannot read {word!r} in {value.strip()!r}')
            ids.append(word)
    return ids


def normalize_touch(path):
    # Human notes are annotations, not part of a path that could hide a collision.
    path = re.sub(r'\s+\([^()]*\)\s*$', '', path).strip()
    if re.search(r'\s+\(', path):
        raise ValueError('Touches: malformed or nested annotation')
    if not path:
        raise ValueError('Touches: empty path')
    if re.match(r'^[a-zA-Z]:[/\\\\]', path) or '\\' in path:
        raise ValueError('Touches cannot contain absolute Windows paths or backslashes')
    if NAMED.match(path):
        return path
    if path.startswith('/') or '..' in path.split('/'):
        raise ValueError('Touches must be relative normalized paths or kind:name resources')
    trailing = path.endswith('/')
    path = posixpath.normpath(path)
    if path == '.':
        return './'
    return path + ('/' if trailing else '')


def overlap(a, b):
    if a == b:
        return True
    if NAMED.match(a) or NAMED.match(b):
        return False
    if a == './' or b == './':
        return True
    for x, y in ((a, b), (b, a)):
        if not WILDCARD.search(x) and y.startswith(x.rstrip('/') + '/'):
            return True
        if WILDCARD.search(x):
            if fnmatch.fnmatchcase(y, x):
                return True
            # Glob intersection is conservative: matching prefixes may overlap,
            # even when neither pattern string matches the other one literally.
            prefix = WILDCARD.split(x, 1)[0]
            other = WILDCARD.split(y, 1)[0] if WILDCARD.search(y) else y
            if prefix.startswith(other) or other.startswith(prefix):
                return True
    return False


def load(data, root=None, done=()):
    items = data.get('cards', data.get('items')) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError('expected a list, {"cards": [...]} or {"items": [...]}')
    cards = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id'].strip():
            raise ValueError('each card needs a nonempty string id')
        cid = item['id']
        if cid in cards:
            raise ValueError(f'duplicate id: {cid}')
        try:
            deps, touches, weight = block(item.get('description') or '')
        except ValueError as exc:
            raise ValueError(f'{cid}: {exc}') from None
        deps += tokens(item.get('depends_on', []), 'depends_on')
        touches += tokens(item.get('touches', []), 'touches')
        weight = item.get('size', weight if weight is not None else 1)
        if isinstance(weight, str) and re.fullmatch(r'[1-9][0-9]*', weight):
            weight = int(weight)
        if type(weight) is not int or weight < 1:
            raise ValueError(f'{cid}: size must be a positive integer')
        status = item.get('status', 'TODO')
        if not isinstance(status, str):
            raise ValueError(f'{cid}: status must be text')
        cards[cid] = dict(id=cid, parent=item.get('parentId'), deps=deps,
                          touches=sorted({normalize_touch(p) for p in touches}), size=weight, status=status.upper())
    ids = sorted(cards)
    def resolve(ref, what='dependency'):
        if ref in cards:
            return ref
        if len(ref) < MIN_PREFIX:
            raise ValueError(f'unknown {what}: {ref} (a shortened id needs at least {MIN_PREFIX} characters)')
        hits = [cid for cid in ids if cid.startswith(ref)]
        if len(hits) != 1:
            raise ValueError(f'unknown or ambiguous {what}: {ref}')
        return hits[0]
    children = {cid: [] for cid in ids}
    for cid, c in cards.items():
        parent = c['parent']
        if parent is not None:
            if not isinstance(parent, str) or parent not in cards:
                raise ValueError(f'{cid}: unknown parent')
            children[parent].append(cid)
        c['deps'] = sorted({resolve(d) for d in c['deps']})
    # Iterative forest traversal also rejects parent cycles, independent of the
    # dependency graph. No recursive traversal, even for deeply nested exports.
    roots = sorted(cid for cid in ids if cards[cid]['parent'] is None)
    order, queue = [], deque(roots)
    while queue:
        cid = queue.popleft(); order.append(cid)
        queue.extend(sorted(children[cid]))
    if len(order) != len(cards):
        raise ValueError('parent cycle')
    leaves = {}
    for cid in reversed(order):
        leaves[cid] = set().union(*(leaves[ch] for ch in children[cid])) if children[cid] else {cid}
    inherited = {}
    for cid in order:
        c = cards[cid]; parent = inherited.get(c['parent'])
        if parent and parent['done'] and c['status'] not in DONE:
            raise ValueError(f'{cid}: completed parent has an unfinished child')
        inherited[cid] = dict(deps=set(c['deps']) | (parent['deps'] if parent else set()),
                              touches=set(c['touches']) | (parent['touches'] if parent else set()),
                              blocked=c['status'] in BLOCKED or bool(parent and parent['blocked']),
                              done=c['status'] in DONE)
    if root is not None:
        container = resolve(root, '--parent')
        if not children[container]:
            raise ValueError(f'--parent {root}: no cards to plan under it')
        scope = leaves[container]
    else:
        scope = set(ids)
    # Merged but not yet closed on the board: counts as done, like DONE.
    merged = set().union(*(leaves[resolve(ref, '--done')] for ref in done))
    result = {}
    for cid in ids:
        if children[cid]:
            continue
        context = inherited[cid]
        deps = set().union(*(leaves[d] for d in context['deps']))
        if cid in deps:
            raise ValueError(f'dependency cycle at {cid}')
        result[cid] = dict(cards[cid], deps=deps, touches=sorted(context['touches']),
                           blocked=context['blocked'], done=context['done'] or cid in merged,
                           scope=cid in scope)
    return result


def cycle(deps, stuck):
    """Name one dependency cycle among the cards a topological sort left over.
    Each of them waits on another one of them, so following the smallest such
    dependency from the smallest card must come back to a card already seen."""
    path, seen, cid = [], {}, min(stuck)
    while cid not in seen:
        seen[cid] = len(path); path.append(cid)
        cid = min(deps[cid] & stuck)
    return ' -> '.join(path[seen[cid]:] + [cid])


def shared(a, b):
    for x in a:
        for y in b:
            if overlap(x, y):
                return x if x == y else f'{x} ~ {y}'
    return None


def maybe_shared(cards, ids):
    """Pairs that can overlap at all, without comparing every pair: two plain
    paths overlap only under one first segment and a resource only by name,
    while a glob or the root is compared with every card."""
    groups, wild = {}, set()
    for cid in ids:
        for t in cards[cid]['touches']:
            if WILDCARD.search(t) or t == './':
                wild.add(cid)
            else:
                groups.setdefault(t if NAMED.match(t) else t.split('/', 1)[0], set()).add(cid)
    pairs = {tuple(sorted((a, b))) for a in wild for b in ids if a != b and cards[b]['touches']}
    for group in groups.values():
        members = sorted(group)
        pairs.update((a, b) for i, a in enumerate(members) for b in members[i + 1:])
    return sorted(pairs)


def plan(cards, width):
    done = {cid for cid, c in cards.items() if c['done']}
    active = {cid for cid, c in cards.items() if c.get('scope', True) and not c['done']}
    deps = {cid: cards[cid]['deps'] & active for cid in active}
    # An open card outside --parent cannot be scheduled here, so neither can
    # whatever waits on it.
    outside = {cid: cards[cid]['deps'] - active - done for cid in active}
    nexts = {cid: [] for cid in active}
    counts = {cid: len(ds) for cid, ds in deps.items()}
    for cid, ds in deps.items():
        for d in ds:
            nexts[d].append(cid)
    ready = [cid for cid in active if counts[cid] == 0]
    heapq.heapify(ready); topo = []
    while ready:
        cid = heapq.heappop(ready); topo.append(cid)
        for nxt in sorted(nexts[cid]):
            counts[nxt] -= 1
            if counts[nxt] == 0:
                heapq.heappush(ready, nxt)
    if len(topo) != len(active):
        raise ValueError('dependency cycle: ' + cycle(deps, active - set(topo)))
    blocked = {cid for cid in active if cards[cid]['blocked'] or outside[cid]}
    for cid in topo:
        if deps[cid] & blocked:
            blocked.add(cid)
    # What holds each blocked card; empty means its own or a parent's status.
    waiting_on = {cid: sorted((deps[cid] & blocked) | outside[cid]) for cid in sorted(blocked)}
    runnable = active - blocked
    tail, head, previous = {}, {}, {}
    for cid in topo:
        best = min(deps[cid], key=lambda d: (-head[d], d), default=None)
        head[cid] = cards[cid]['size'] + (head[best] if best else 0)
        previous[cid] = best
    for cid in reversed(topo):
        tail[cid] = cards[cid]['size'] + max((tail[n] for n in nexts[cid]), default=0)
    end = min(runnable, key=lambda c: (-head[c], c), default=None)
    path, length = [], head[end] if end else 0
    while end is not None:
        path.append(end); end = previous[end]
    path.reverse()
    waves, placed = [], set()
    while placed != runnable:
        candidates = sorted((c for c in runnable - placed if deps[c] <= placed), key=lambda c: (-tail[c], c))
        wave = []
        for cid in candidates:
            touches = cards[cid]['touches']
            if wave and (not touches or any(not cards[w]['touches'] or
                    any(overlap(a, b) for a in touches for b in cards[w]['touches']) for w in wave)):
                continue
            wave.append(cid)
            if len(wave) == width:
                break
        if not wave:
            raise ValueError('unschedulable dependencies')
        waves.append(sorted(wave)); placed.update(wave)
    # Pairs a shared resource keeps apart; a pair already ordered by a
    # dependency would never meet in one wave, so it is not reported.
    pairs = [(a, b, res) for a, b in maybe_shared(cards, runnable)
             for res in [shared(cards[a]['touches'], cards[b]['touches'])] if res]
    reach = {}
    if pairs:
        for cid in topo:
            reach[cid] = set(deps[cid]).union(*(reach[d] for d in deps[cid]))
    conflicts = [dict(cards=[a, b], resource=res) for a, b, res in pairs
                 if a not in reach[b] and b not in reach[a]]
    return dict(waves=waves, ready=waves[0] if waves else [], blocked=sorted(blocked),
                waiting_on=waiting_on, conflicts=conflicts,
                unknown_touches=sorted(c for c in runnable if not cards[c]['touches']),
                done=sorted(c for c in done if cards[c].get('scope', True)), critical_path=path,
                critical_length=length)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('cards', help='JSON file or - for stdin')
    p.add_argument('--max-width', type=int, default=6, choices=range(1, 7))
    p.add_argument('--parent', help='plan only the leaves under this epic or story (id or unique prefix)')
    p.add_argument('--done', default='', help='comma-separated ids merged but not yet closed on the board')
    args = p.parse_args()
    try:
        raw = sys.stdin.read() if args.cards == '-' else Path(args.cards).read_text(encoding='utf-8')
        cards = load(json.loads(raw), root=args.parent, done=tokens(args.done, '--done'))
        print(json.dumps(plan(cards, args.max_width), sort_keys=True))
        return 0
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        print(f'PLAN_ERROR: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
