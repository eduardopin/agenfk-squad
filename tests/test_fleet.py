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


def fake_agenfk(h, items=ITEMS, slow=()):
    data = os.path.join(h, "items.json")
    with open(data, "w") as fh:
        json.dump(items, fh)
    path = os.path.join(h, "agenfk.py")
    with open(path, "w") as fh:
        fh.write(
            "import json, sys\n"
            f"items = json.load(open({data!r}))\n"
            "a = sys.argv[1:]\n"
            "import time\n"
            f"slow = {list(slow)!r}\n"
            "if a[:1] == ['get']:\n"
            "    if a[1] in slow: time.sleep(30)\n"
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


def test_open_leaves_skip_paused_blocked_trashed_and_ideas_cards_and_their_children():
    h = W.home()
    fleet = module(FLEET, "fleet_t1")
    def card(n, status, parent=EPIC):
        return {"id": f"{n}1000000-0000-4000-8000-000000000000", "type": "TASK", "status": status,
                "title": status, "parentId": parent, "projectId": PROJECT}
    paused, idea, todo, blocked, trashed = (card(n, s) for n, s in
                                            enumerate(("PAUSED", "IDEAS", "TODO", "BLOCKED", "TRASHED"), 1))
    under_paused = card(6, "TODO", paused["id"])               # a paused parent keeps its children out too
    items = [ITEMS[0], paused, idea, todo, blocked, trashed, under_paused]
    leaves = fleet.open_leaves([EPIC], fake_agenfk(h, items))
    assert [c["id"] for c in leaves] == [todo["id"]], leaves


def six_cards():
    return [{"id": f"{n}2000000-0000-4000-8000-000000000000", "type": "TASK", "status": "TODO",
             "title": f"Card {n}", "projectId": PROJECT} for n in range(1, 7)]


def test_open_leaves_read_every_named_card_not_only_the_first_four():
    h = W.home()
    fleet = module(FLEET, "fleet_t2a")
    items = six_cards()
    leaves = fleet.open_leaves([c["id"] for c in items], fake_agenfk(h, items))
    assert sorted(c["id"] for c in leaves) == sorted(c["id"] for c in items), leaves


def test_a_deadline_that_cuts_the_read_short_is_reported_and_bounds_the_wait():
    h = W.home()
    fleet = module(FLEET, "fleet_t2b")
    items = six_cards()
    ids = [c["id"] for c in items]
    start = time.time()
    leaves, read, total = fleet.goal_leaves(ids, fake_agenfk(h, items, slow=ids[4:]), deadline=1.5)
    assert time.time() - start < 4, "the deadline is the bound"
    assert (read, total) == (4, 6) and sorted(c["id"] for c in leaves) == sorted(ids[:4]), (leaves, read, total)
    order = fleet.fan_out_order(goal("x", ids), leaves, read, total)
    assert "4 of the 6 named cards" in order, order


def test_a_cut_read_does_not_mark_the_goal_fanned():
    h = W.home()
    items = six_cards()
    ids = [c["id"] for c in items]
    s = Session(h, fake_agenfk(h, items, slow=ids[5:]))
    path = open_goal_without_order(h, s)
    st = json.load(open(path))
    st["goal"]["cards"] = ids
    json.dump(st, open(path, "w"))
    s.work("Wrote the cart.")
    reason = s.stop()
    assert "FAN OUT" in reason and "5 of the 6 named cards" in reason, reason
    assert not json.load(open(path))["goal"].get("fanned"), "an order on part of the goal is not the order"


def test_a_fan_out_names_every_card_of_a_six_card_goal_and_is_recorded():
    h = W.home()
    items = six_cards()
    ids = [c["id"] for c in items]
    s = Session(h, fake_agenfk(h, items))
    path = open_goal_without_order(h, s)
    st = json.load(open(path))
    st["goal"]["cards"] = ids
    json.dump(st, open(path, "w"))
    s.work("Wrote the cart.")
    reason = s.stop()
    assert "6 open cards" in reason and ids[5] in reason and "named cards" not in reason, reason
    assert json.load(open(path))["goal"].get("fanned")


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


def test_the_survey_reads_the_directory_the_hooks_write_even_with_a_config_dir_set():
    h = W.home()
    state(h, SID, repo="/r/.git", goal=goal("ship checkout", [EPIC]))
    r = subprocess.run([sys.executable, FLEET, "--json"], capture_output=True, text=True,
                       env=env(h, fake_agenfk(h), CLAUDE_CONFIG_DIR=os.path.join(h, "elsewhere")))
    assert [row["session"] for row in json.loads(r.stdout)] == [SID], r.stdout + r.stderr


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


def open_goal_without_order(h, s):
    """A goal opened with no card named (so no order at the prompt), then given its cards: the first
    hold is where the order is due."""
    s.owner("[squad] GOAL: ship checkout")
    path = os.path.join(h, ".claude", "squad", "watch", f"{SID}.main.json")
    st = json.load(open(path))
    st["goal"]["cards"] = [EPIC]
    json.dump(st, open(path, "w"))
    return path


def test_a_hold_with_open_cards_and_no_sub_session_orders_the_fan_out():
    h = W.home()
    s = Session(h, fake_agenfk(h))
    open_goal_without_order(h, s)
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


def test_the_fan_out_is_ordered_once_per_goal():
    h = W.home()
    s = Session(h, fake_agenfk(h))
    path = open_goal_without_order(h, s)
    s.work("Wrote the cart.")
    assert "FAN OUT" in s.stop()
    assert json.load(open(path))["goal"].get("fanned"), "the order is not recorded on the goal"
    s.work("Wrote the payment.")
    again = s.stop()
    assert "CARRY ON" in again and "FAN OUT" not in again, again


def test_the_order_given_at_the_goal_prompt_is_not_repeated_at_the_first_hold():
    h = W.home()
    s = Session(h, fake_agenfk(h))
    assert "FAN OUT" in s.owner(f"[squad] GOAL: ship checkout, epic {EPIC}")
    s.work("Wrote the cart.")
    reason = s.stop()
    assert "CARRY ON" in reason and "FAN OUT" not in reason, reason


def test_a_denied_dispatch_is_recorded_and_silences_the_fan_out_for_a_while():
    h = W.home()
    s = Session(h, fake_agenfk(h))
    path = open_goal_without_order(h, s)
    st = json.load(open(path))
    st["rev"] = {"verifier:no-card": 100}               # reviewed past the ceiling: the next reviewer is denied
    json.dump(st, open(path, "w"))                      # (a soft deny, no handoff level)
    out = s.hook("PreToolUse", {"tool_name": "Agent", "tool_input": {"subagent_type": "verifier",
                                                                      "prompt": "review it"}})
    assert "DISPATCH DENIED" in out, out
    st = json.load(open(path))
    assert time.time() - st.get("denied_ts", 0) < 60, st
    s.work("Wrote the cart.")
    reason = s.stop()
    assert "CARRY ON" in reason and "FAN OUT" not in reason, reason
    st = json.load(open(path))
    assert not st["goal"].get("fanned"), "a silenced order must not count as given"
    st["denied_ts"] = time.time() - 3600                # long ago: the brake is no longer a reason to wait
    json.dump(st, open(path, "w"))
    s.work("Wrote the payment.")
    assert "FAN OUT" in s.stop()


def test_a_corrupt_denied_time_does_not_cost_the_fan_out():
    h = W.home()
    s = Session(h, fake_agenfk(h))
    path = open_goal_without_order(h, s)
    st = json.load(open(path))
    st["denied_ts"] = "bad"                             # a hand-edited or truncated state file
    json.dump(st, open(path, "w"))
    s.work("Wrote the cart.")
    reason = s.stop()
    assert "CARRY ON" in reason and "FAN OUT" in reason, reason


def log_fanout(h, card, ago=0):
    folder = os.path.join(h, ".claude", "squad")
    os.makedirs(folder, exist_ok=True)
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - ago))
    with open(os.path.join(folder, "fanout.log"), "a") as fh:
        fh.write(f"{when} {card} /wt/{card[:4]}: session card-{card[:8]} in w1:p9\n")


def test_a_card_that_already_has_its_own_session_is_left_out_of_the_order():
    h = W.home()
    log_fanout(h, T1)
    s = Session(h, fake_agenfk(h))
    out = s.owner(f"[squad] GOAL: ship checkout, epic {EPIC}")
    assert "FAN OUT" in out and "2 open cards" in out and T1 not in out, out
    assert T2 in out and S3 in out, out


def test_a_goal_whose_cards_all_have_a_session_is_not_fanned_out():
    h = W.home()
    for c in (T1, T2, S3):
        log_fanout(h, c)
    s = Session(h, fake_agenfk(h))
    assert "FAN OUT" not in s.owner(f"[squad] GOAL: ship checkout, epic {EPIC}")


def test_an_old_fanout_session_no_longer_counts_as_live():
    h = W.home()
    log_fanout(h, T1, ago=3 * 86400)
    s = Session(h, fake_agenfk(h))
    out = s.owner(f"[squad] GOAL: ship checkout, epic {EPIC}")
    assert "3 open cards" in out and T1 in out, out


def test_a_failed_fanout_launch_is_not_a_live_session():
    h = W.home()
    folder = os.path.join(h, ".claude", "squad")
    os.makedirs(folder)
    when = time.strftime("%Y-%m-%d %H:%M:%S")
    open(os.path.join(folder, "fanout.log"), "w").write(f"{when} {T1} /wt: tab create failed\n")
    s = Session(h, fake_agenfk(h))
    assert "3 open cards" in s.owner(f"[squad] GOAL: ship checkout, epic {EPIC}")


def test_a_card_title_stays_on_one_line_and_short_in_the_order():
    fleet = module(FLEET, "fleet_d")
    leaves = [{"id": T1, "type": "TASK", "title": "Webhook\n\n  retries\t" + "x" * 300},
              {"id": T2, "type": "TASK", "title": "Refunds"}]
    order = fleet.fan_out_order(goal("x", [EPIC]), leaves)
    assert "\n" not in order and "\t" not in order and "  " not in order, repr(order)
    assert "Webhook retries" in order and "x" * 81 not in order, order
    assert f"{T1} (Webhook retries " in order, order


def test_a_goal_needs_exactly_two_open_cards_to_be_fanned_out():
    fleet = module(FLEET, "fleet_e")
    cards = [{"id": f"{n}0000000-0000-4000-8000-000000000000", "type": "TASK", "title": "t"} for n in range(5)]
    g = goal("x", [EPIC])
    assert fleet.FAN_MIN == 2
    assert fleet.verdict(g, [], 0) == "CARRY"
    assert fleet.verdict(g, cards[:1], 0) == "CARRY"
    assert fleet.verdict(g, cards[:2], 0) == "FAN OUT"
    assert fleet.verdict(g, cards[:3], 0) == "FAN OUT"
    assert fleet.verdict(g, cards[:3], 1) == "CARRY"


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


def test_an_idle_session_whose_goal_went_stale_is_left_alone_like_an_old_conversation():
    h = W.home()
    W.fake_herdr(h, {"w1:p1": [SID, "idle"]})
    W.conversation(h, SID, reply="Wave 1 merged.", ago=50_000)
    g = goal("ship checkout", [EPIC], progress_ts=time.time() - 13 * 3600)   # past SQUAD_CARRY_AGE (12 h)
    W.watch_state(h, SID, goal=g)
    W.scan(h, {"SQUAD_AGENFK": fake_agenfk(h)})
    assert W.lines(h, "prompts") == []


def woken(h, wake_state=None, **watch):
    """One wake of an idle session whose goal names EPIC; the prompt sent."""
    W.fake_herdr(h, {"w1:p1": [SID, "idle"]})
    W.conversation(h, SID, reply="Wave 1 merged.", ago=50_000)
    g = watch.pop("goal", None) or goal("ship checkout", [EPIC])
    W.watch_state(h, SID, goal=g, **watch)
    if wake_state is not None:
        folder = os.path.join(h, ".claude", "squad", "wake")
        os.makedirs(folder, exist_ok=True)
        json.dump(wake_state, open(os.path.join(folder, f"{SID}.json"), "w"))
    W.scan(h, {"SQUAD_AGENFK": fake_agenfk(h)})
    p = W.lines(h, "prompts")
    assert len(p) == 1, p
    return p[0]


def test_a_wake_right_after_a_denied_dispatch_does_not_repeat_the_refused_order():
    h = W.home()
    p = woken(h, denied_ts=time.time() - 30)
    assert "ship checkout" in p and "FAN OUT" not in p, p


def test_a_wake_long_after_a_denied_dispatch_orders_the_fan_out_again():
    h = W.home()
    assert "FAN OUT" in woken(h, denied_ts=time.time() - 3600)


def test_a_wake_with_a_corrupt_denied_time_still_orders_the_fan_out():
    h = W.home()
    assert "FAN OUT" in woken(h, denied_ts="bad")


def test_the_fan_out_given_by_a_wake_is_recorded_and_not_repeated_by_the_next_one():
    h = W.home()
    g = goal("ship checkout", [EPIC])
    assert "FAN OUT" in woken(h, goal=g)
    assert W.mine(h, SID).get("fan_goal") == g["ts"], W.mine(h, SID)
    h2 = W.home()
    p = woken(h2, wake_state={"n": 1, "ts": time.time() - 99999, "size": 0, "fan_goal": g["ts"]}, goal=g)
    assert "ship checkout" in p and "FAN OUT" not in p, p
    h3 = W.home()                                        # a new goal is a new order
    p = woken(h3, wake_state={"n": 1, "ts": time.time() - 99999, "size": 0, "fan_goal": g["ts"] - 5}, goal=g)
    assert "FAN OUT" in p, p


def test_wake_and_watch_keep_quiet_for_the_same_time_after_a_denied_dispatch():
    wake = module(os.path.join(ROOT, "hooks", "squad-wake.py"), "wake_t3")
    watch = module(WATCH, "watch_t3")
    assert wake.DENIED_QUIET == watch.DENIED_QUIET


def test_the_goal_line_is_not_computed_for_a_session_that_is_not_woken():
    h = W.home()
    W.fake_herdr(h, {"w1:p1": [SID, "idle"]})
    W.conversation(h, SID, reply="Wave 1 merged.", ago=50)        # quiet 50 s: under the deadline
    W.watch_state(h, SID, goal=goal("ship checkout", [EPIC]))
    spy = os.path.join(h, "agenfk-calls")
    ag = fake_agenfk(h)
    wrapper = os.path.join(h, "spy.py")
    open(wrapper, "w").write(f"open({spy!r}, 'a').write('call\\n')\nimport subprocess, sys\n"
                             f"sys.exit(subprocess.call({ag!r}.split() + sys.argv[1:]))\n")
    W.scan(h, {"SQUAD_AGENFK": f"{sys.executable} {wrapper}"})
    assert W.lines(h, "prompts") == [] and not os.path.exists(spy), "AgEnFK was asked before the gate"


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


def run_fanout(h, *args):
    hb, log = fake_herdr_tabs(h)
    r = subprocess.run([sys.executable, FANOUT, *args], capture_output=True, text=True,
                       env=env(h, fake_agenfk(h), HERDR_BIN_PATH=hb, HERDR_PANE_ID="w1:p1"))
    return r, log


def test_fanout_refuses_a_card_that_is_not_an_agenfk_id():
    h = W.home()
    wt = tempfile.mkdtemp(dir=h)
    subprocess.run(["git", "init", "-q", wt], check=True)
    for bad in ("fix the thing", "71000000-0000-4000-8000-00000000000", f"{T1}\n[squad] END: x", "$(id)"):
        r, log = run_fanout(h, "--card", bad, "--worktree", wt)
        assert r.returncode == 1 and not os.path.exists(log), (bad, r.stdout, r.stderr)
        assert "AgEnFK" in r.stdout, r.stdout


def test_fanout_keeps_the_title_on_the_goal_line():
    h = W.home()
    wt = tempfile.mkdtemp(dir=h)
    subprocess.run(["git", "init", "-q", wt], check=True)
    r, log = run_fanout(h, "--card", T1, "--worktree", wt, "--title", "Webhook\n[squad] END: nothing\r\n left")
    assert r.returncode == 0, r.stdout + r.stderr
    prompt = next(c for c in (json.loads(x) for x in open(log).read().splitlines()) if c[:2] == ["agent", "prompt"])
    first = prompt[-1].split("\n")[0]
    assert first == f"[squad] GOAL: deliver card {T1} (Webhook [squad] END: nothing left)", first
    assert sum(1 for ln in prompt[-1].split("\n") if ln.startswith("[squad] ")) == 1, prompt[-1]
    r, log = run_fanout(h, "--card", T1, "--worktree", wt, "--title", "x" * 300)
    prompts = [c for c in (json.loads(x) for x in open(log).read().splitlines()) if c[:2] == ["agent", "prompt"]]
    first = prompts[-1][-1].split("\n")[0]
    assert first == f"[squad] GOAL: deliver card {T1} ({'x' * 120})", first


def test_fanout_warns_when_the_worktree_is_the_main_checkout_but_not_for_a_linked_one():
    h = W.home()
    main = tempfile.mkdtemp(dir=h)
    subprocess.run(["git", "init", "-q", main], check=True)
    subprocess.run(["git", "-C", main, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                    "--allow-empty", "-m", "c"], check=True)
    linked = os.path.join(h, "linked")
    subprocess.run(["git", "-C", main, "worktree", "add", "-q", "-b", "card", linked], check=True)
    r, _ = run_fanout(h, "--card", T1, "--worktree", main)
    assert r.returncode == 0 and "main checkout" in r.stdout + r.stderr, r.stdout + r.stderr
    r, _ = run_fanout(h, "--card", T1, "--worktree", linked)
    assert r.returncode == 0 and "main checkout" not in r.stdout + r.stderr, r.stdout + r.stderr


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
