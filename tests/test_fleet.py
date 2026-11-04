#!/usr/bin/env python3
"""Parallel by default: the architect reads every live session and fans a goal out.

  - skills/architect/scripts/fleet.py lists each main session with its goal, the open
    cards under it, its live sub-sessions, and a FAN OUT verdict when a goal has two or
    more open cards and nothing runs in parallel;
  - squad-watch orders FAN OUT when a goal opens and on each carry-on hold in that case;
  - squad-wake wakes an idle session whose goal is open, past the old-conversation age,
    and names the goal;
  - skills/architect/scripts/fanout.py opens a card that does not fit a sub-agent in its
    own herdr tab and Claude session, in its worktree, with the goal as the first prompt.

Self-checking: the exit code is the verdict. Throwaway HOME, fake agenfk and herdr.

    python3 tests/test_fleet.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLEET = os.path.join(ROOT, "skills", "architect", "scripts", "fleet.py")
FANOUT = os.path.join(ROOT, "skills", "architect", "scripts", "fanout.py")
WATCH = os.path.join(ROOT, "hooks", "squad-watch.py")


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


W = module(os.path.join(ROOT, "tests", "test_wake.py"), "test_wake_helpers")

EPIC = "e0000000-0000-4000-8000-000000000000"
S1, S2, S3 = ("51000000-0000-4000-8000-000000000001", "52000000-0000-4000-8000-000000000002",
              "53000000-0000-4000-8000-000000000003")
T1, T2 = "71000000-0000-4000-8000-000000000001", "72000000-0000-4000-8000-000000000002"
LONE = "10000000-0000-4000-8000-00000000000a"
PROJECT = "p0000000-0000-4000-8000-000000000000"
SID = "abcdef01-2345-6789-abcd-ef0123456789"
OTHER = "fedcba98-7654-3210-fedc-ba9876543210"

ITEMS = [
    {"id": EPIC, "type": "EPIC", "status": "IN_PROGRESS", "title": "Checkout", "projectId": PROJECT},
    {"id": S1, "type": "STORY", "status": "DONE", "title": "Cart", "parentId": EPIC, "projectId": PROJECT},
    {"id": S2, "type": "STORY", "status": "TODO", "title": "Payment", "parentId": EPIC, "projectId": PROJECT},
    {"id": T1, "type": "TASK", "status": "IN_PROGRESS", "title": "Webhook", "parentId": S2,
     "projectId": PROJECT},
    {"id": T2, "type": "TASK", "status": "TODO", "title": "Refunds", "parentId": S2, "projectId": PROJECT},
    {"id": S3, "type": "STORY", "status": "TODO", "title": "Receipt email", "parentId": EPIC,
     "projectId": PROJECT},
    {"id": LONE, "type": "TASK", "status": "TODO", "title": "One thing", "projectId": PROJECT},
]


def fake_agenfk(h, items=ITEMS):
    data = os.path.join(h, "items.json")
    with open(data, "w") as fh:
        json.dump(items, fh)
    path = os.path.join(h, "agenfk.py")
    with open(path, "w") as fh:
        fh.write(
            "import json, sys\n"
            f"items = json.load(open({data!r}))\n"
            "a = sys.argv[1:]\n"
            "if a[:1] == ['get']:\n"
            "    hit = [i for i in items if i['id'] == a[1]]\n"
            "    if not hit: sys.exit(1)\n"
            "    print(json.dumps(hit[0])); sys.exit(0)\n"
            "if a[:1] == ['list'] and '--project' in a:\n"
            "    pid = a[a.index('--project') + 1]\n"
            "    print(json.dumps([i for i in items if i['projectId'] == pid])); sys.exit(0)\n"
            "sys.exit(1)\n")
    return f"{sys.executable} {path}"


def state(h, sid, agent="main", ago=0, **fields):
    folder = os.path.join(h, ".claude", "squad", "watch")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{sid}.{agent}.json")
    with open(path, "w") as fh:
        json.dump(fields, fh)
    t = time.time() - ago
    os.utime(path, (t, t))
    return path


def goal(text, cards, source="owner", **extra):
    now = time.time()
    g = {"text": text, "source": source, "cards": cards, "holds": 0, "idle": 0, "ts": now,
         "progress_ts": now}
    g.update(extra)
    return g


def env(h, agenfk, **more):
    e = dict(os.environ, HOME=h, USERPROFILE=h)
    for key in list(e):
        if key.startswith(("SQUAD_", "HERDR_")):
            del e[key]
    e["SQUAD_AGENFK"] = agenfk
    e.update(more)
    return e


# --- fleet.py: the survey ---------------------------------------------------------------------

def test_open_leaves_are_the_unfinished_cards_at_the_bottom_of_the_goal():
    h = W.home()
    fleet = module(FLEET, "fleet_a")
    leaves = fleet.open_leaves([EPIC], fake_agenfk(h))
    assert sorted(c["id"] for c in leaves) == sorted([T1, T2, S3]), leaves
    assert fleet.open_leaves([LONE], fake_agenfk(h))[0]["id"] == LONE
    assert fleet.open_leaves([S1], fake_agenfk(h)) == []


def test_open_leaves_fail_open_when_agenfk_does_not_answer():
    fleet = module(FLEET, "fleet_b")
    assert fleet.open_leaves([EPIC], f"{sys.executable} -c 'import sys; sys.exit(1)'") == []


def test_the_survey_flags_a_goal_with_open_cards_and_nothing_running():
    h = W.home()
    state(h, SID, repo="/r/.git", goal=goal("ship checkout", [EPIC]))
    state(h, OTHER, repo="/r/.git", goal=goal("ship checkout too", [EPIC]))
    state(h, OTHER, agent="a1", ago=30)                    # a live sub-session of OTHER
    state(h, "11111111-2222-3333-4444-555555555555", repo="/r/.git")   # no goal
    r = subprocess.run([sys.executable, FLEET, "--json"], capture_output=True, text=True,
                       env=env(h, fake_agenfk(h)))
    rows = {row["session"]: row for row in json.loads(r.stdout)}
    assert rows[SID]["verdict"] == "FAN OUT" and rows[SID]["open_cards"] == 3, rows[SID]
    assert rows[OTHER]["verdict"] == "CARRY" and rows[OTHER]["live_subs"] == 1, rows[OTHER]
    assert rows["11111111-2222-3333-4444-555555555555"]["verdict"] == "-"


def test_the_survey_prints_a_table_for_a_person():
    h = W.home()
    state(h, SID, repo="/r/.git", goal=goal("ship checkout", [EPIC]))
    r = subprocess.run([sys.executable, FLEET], capture_output=True, text=True, env=env(h, fake_agenfk(h)))
    assert r.returncode == 0, r.stderr
    assert "FAN OUT" in r.stdout and SID[:8] in r.stdout and "ship checkout" in r.stdout, r.stdout


def test_blocked_released_and_single_card_goals_are_not_fanned_out():
    h = W.home()
    fleet = module(FLEET, "fleet_c")
    ag = fake_agenfk(h)
    assert fleet.verdict(goal("x", [EPIC], blocked=True), fleet.open_leaves([EPIC], ag), 0) == "BLOCKED"
    assert fleet.verdict(goal("x", [EPIC], released=True), fleet.open_leaves([EPIC], ag), 0) == "STOPPED"
    assert fleet.verdict(goal("x", [LONE]), fleet.open_leaves([LONE], ag), 0) == "CARRY"


# --- squad-watch: FAN OUT is the order --------------------------------------------------------

class Session:
    def __init__(self, h, agenfk):
        self.h, self.agenfk = h, agenfk
        self.transcript = os.path.join(h, f"{SID}.jsonl")
        open(self.transcript, "w").close()

    def hook(self, event, extra=None):
        p = {"hook_event_name": event, "session_id": SID, "transcript_path": self.transcript, "cwd": self.h}
        p.update(extra or {})
        r = subprocess.run([sys.executable, WATCH], input=json.dumps(p), capture_output=True, text=True,
                           env=env(self.h, self.agenfk))
        return r.stdout.strip()

    def add(self, rec):
        with open(self.transcript, "a") as fh:
            fh.write(json.dumps(rec) + "\n")

    def owner(self, text):
        self.add({"type": "user", "message": {"role": "user", "content": text}})
        return self.hook("UserPromptSubmit", {"prompt": text})

    def work(self, text):
        self.add({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t", "name": "Edit", "input": {"file_path": "a", "old_string": "a",
                                                                       "new_string": "b"}}]}})
        self.add({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": text}]}})

    def stop(self):
        out = self.hook("Stop")
        return json.loads(out).get("reason", "") if out else ""


def test_opening_a_goal_with_parallel_cards_orders_the_fan_out():
    h = W.home()
    s = Session(h, fake_agenfk(h))
    out = s.owner(f"[squad] GOAL: ship checkout, epic {EPIC}")
    ctx = out                             # UserPromptSubmit context is plain stdout (emit())
    assert "FAN OUT" in ctx and T1 in ctx and S3 in ctx, out
    assert "parallel-plan" in ctx and "fanout.py" in ctx, ctx


def test_a_hold_with_open_cards_and_no_sub_session_orders_the_fan_out():
    h = W.home()
    s = Session(h, fake_agenfk(h))
    s.owner(f"[squad] GOAL: ship checkout, epic {EPIC}")
    s.work("Wrote the cart.")
    reason = s.stop()
    assert "CARRY ON" in reason and "FAN OUT" in reason, reason
    assert "3 open cards" in reason, reason


def test_a_hold_with_a_live_sub_session_is_a_plain_carry_on():
    h = W.home()
    s = Session(h, fake_agenfk(h))
    s.owner(f"[squad] GOAL: ship checkout, epic {EPIC}")
    state(h, SID, agent="a1", ago=10)
    s.work("Dispatched wave 1.")
    reason = s.stop()
    assert "CARRY ON" in reason and "FAN OUT" not in reason, reason


def test_a_single_open_card_is_a_plain_carry_on():
    h = W.home()
    s = Session(h, fake_agenfk(h))
    s.owner(f"[squad] GOAL: close {LONE}")
    s.work("Half done.")
    reason = s.stop()
    assert "CARRY ON" in reason and "FAN OUT" not in reason, reason


# --- squad-wake: an open goal is not an old conversation --------------------------------------

def test_an_idle_session_with_an_open_goal_is_woken_past_the_age_and_told_the_goal():
    h = W.home()
    W.fake_herdr(h, {"w1:p1": [SID, "idle"]})
    W.conversation(h, SID, reply="Wave 1 merged.", ago=50_000)
    W.watch_state(h, SID, goal=goal("ship checkout", [EPIC]))
    W.scan(h, {"SQUAD_AGENFK": fake_agenfk(h)})
    p = W.lines(h, "prompts")
    assert len(p) == 1 and "[squad-watch] WAKE" in p[0], p
    assert "ship checkout" in p[0] and "FAN OUT" in p[0], p[0]


def test_an_old_session_without_a_goal_is_still_left_alone():
    h = W.home()
    W.fake_herdr(h, {"w1:p1": [SID, "idle"]})
    W.conversation(h, SID, reply="Wave 1 merged.", ago=50_000)
    W.scan(h, {"SQUAD_AGENFK": fake_agenfk(h)})
    assert W.lines(h, "prompts") == []


# --- fanout.py: a card in its own session -----------------------------------------------------

def fake_herdr_tabs(h):
    path = os.path.join(h, "herdr")
    log = os.path.join(h, "herdr.log")
    with open(path, "w") as fh:
        fh.write(f"""#!/usr/bin/env python3
import json, sys
a = sys.argv[1:]
open({log!r}, "a").write(json.dumps(a) + "\\n")
if a[:2] == ["tab", "create"]:
    print(json.dumps({{"result": {{"tab": {{"tab_id": "t9"}}, "root_pane": {{"pane_id": "w1:p9"}}}}}}))
elif a[:2] == ["agent", "list"]:
    print(json.dumps({{"result": {{"agents": []}}}}))
""")
    os.chmod(path, 0o755)
    return path, log


def test_fanout_opens_a_tab_in_the_worktree_starts_claude_and_hands_it_the_goal():
    h = W.home()
    wt = tempfile.mkdtemp(dir=h)
    subprocess.run(["git", "init", "-q", wt], check=True)
    herdr, log = fake_herdr_tabs(h)
    r = subprocess.run([sys.executable, FANOUT, "--card", T1, "--worktree", wt, "--title", "Webhook"],
                       capture_output=True, text=True,
                       env=env(h, fake_agenfk(h), HERDR_BIN_PATH=herdr, HERDR_PANE_ID="w1:p1"))
    assert r.returncode == 0, r.stdout + r.stderr
    calls = [json.loads(x) for x in open(log).read().splitlines()]
    create = next(c for c in calls if c[:2] == ["tab", "create"])
    assert "--cwd" in create and create[create.index("--cwd") + 1] == wt, create
    start = next(c for c in calls if c[:2] == ["agent", "start"])
    assert "--kind" in start and start[start.index("--kind") + 1] == "claude" and "w1:p9" in start, start
    prompt = next(c for c in calls if c[:2] == ["agent", "prompt"])
    assert any(f"[squad] GOAL: deliver card {T1}" in x for x in prompt), prompt
    assert any("[squad] DONE:" in x for x in prompt), prompt


def test_fanout_refuses_a_folder_that_is_not_a_git_checkout():
    h = W.home()
    herdr, log = fake_herdr_tabs(h)
    r = subprocess.run([sys.executable, FANOUT, "--card", T1, "--worktree", h], capture_output=True,
                       text=True, env=env(h, fake_agenfk(h), HERDR_BIN_PATH=herdr, HERDR_PANE_ID="w1:p1"))
    assert r.returncode != 0 and not os.path.exists(log), r.stdout


def test_fanout_outside_herdr_says_how_to_open_the_session_by_hand():
    h = W.home()
    wt = tempfile.mkdtemp(dir=h)
    subprocess.run(["git", "init", "-q", wt], check=True)
    r = subprocess.run([sys.executable, FANOUT, "--card", T1, "--worktree", wt], capture_output=True,
                       text=True, env=env(h, fake_agenfk(h)))
    assert r.returncode == 2, r.returncode
    assert "claude" in r.stdout and wt in r.stdout and T1 in r.stdout, r.stdout


if __name__ == "__main__":
    import traceback
    failed = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception:
                traceback.print_exc()
                failed.append(name)
                print("FAIL", name)
                continue
            print("ok ", name)
    if failed:
        print("failed:", " ".join(failed))
        sys.exit(1)
