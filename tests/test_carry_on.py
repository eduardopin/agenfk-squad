#!/usr/bin/env python3
"""A session with a defined delivery keeps working until the delivery is done.

squad-watch's Stop refuses the stop while a delivery is open: a `[squad] GOAL:`
line (from the owner or the session), or a reply that names a next step or card.
The session stops only with `[squad] DONE: / BLOCKED: / END:`; DONE is refused
while a card the goal names is not DONE in AgEnFK. The owner asking to stop and
the handoff level win, and a guard releases the stop when the session makes no
progress.

Self-checking: the exit code is the verdict. Runs in a throwaway HOME with a fake
agenfk, and never touches ~/.claude.

    python3 tests/test_carry_on.py
"""
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "hooks", "squad-watch.py")
CARD = "11111111-2222-3333-4444-555555555555"

_HOMES = []


@atexit.register
def _remove_homes():
    for h in _HOMES:
        shutil.rmtree(h, ignore_errors=True)


class Session:
    """A throwaway HOME, a main-session transcript and a fake agenfk."""

    def __init__(self, statuses=None, env=None):
        self.h = tempfile.mkdtemp(prefix="carry-")
        _HOMES.append(self.h)
        self.sid = "abcdef01-2345-6789-abcd-ef0123456789"
        self.transcript = os.path.join(self.h, f"{self.sid}.jsonl")
        open(self.transcript, "w").close()
        self.env = dict(env or {})
        cards = os.path.join(self.h, "cards.json")
        with open(cards, "w") as fh:
            json.dump(statuses or {}, fh)
        fake = os.path.join(self.h, "agenfk.py")
        with open(fake, "w") as fh:
            fh.write(
                "import json, sys\n"
                f"cards = json.load(open({cards!r}))\n"
                "args = sys.argv[1:]\n"
                "if len(args) >= 2 and args[0] == 'get' and args[1] in cards:\n"
                "    print(json.dumps({'id': args[1], 'status': cards[args[1]]}))\n"
                "    sys.exit(0)\n"
                "sys.exit(1)\n")
        self.env.setdefault("SQUAD_AGENFK", f"{sys.executable} {fake}")

    def hook(self, event, extra=None):
        p = {"hook_event_name": event, "session_id": self.sid, "transcript_path": self.transcript,
             "cwd": self.h}
        p.update(extra or {})
        e = dict(os.environ, HOME=self.h, USERPROFILE=self.h)
        for key in list(e):
            if key.startswith(("SQUAD_", "HERDR_")):
                del e[key]
        e.update(self.env)
        r = subprocess.run([sys.executable, HOOK], input=json.dumps(p), capture_output=True, text=True, env=e)
        return r.stdout.strip()

    def append(self, rec):
        with open(self.transcript, "a") as fh:
            fh.write(json.dumps(rec) + "\n")

    def owner(self, text):
        self.append({"type": "user", "isSidechain": False, "message": {"role": "user", "content": text}})
        return self.hook("UserPromptSubmit", {"prompt": text})

    def reply(self, text):
        self.append({"type": "assistant", "isSidechain": False,
                     "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}})

    def edit(self):
        """State-changing work: what the guard counts as progress."""
        self.append({"type": "assistant", "isSidechain": False, "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Edit",
             "input": {"file_path": "x.py", "old_string": "a", "new_string": "b"}}]}})

    def look(self):
        """A check that changes nothing: not progress."""
        self.append({"type": "assistant", "isSidechain": False, "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "git status"}}]}})

    def stop(self, extra=None):
        return said(self.hook("Stop", extra))

    def state(self):
        path = os.path.join(self.h, ".claude", "squad", "watch", f"{self.sid}.main.json")
        try:
            with open(path) as fh:
                return json.load(fh)
        except OSError:
            return {}

    def set_state(self, **kw):
        path = os.path.join(self.h, ".claude", "squad", "watch", f"{self.sid}.main.json")
        st = self.state()
        st.update(kw)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(st, fh)


def said(out):
    if not out:
        return "nothing"
    data = json.loads(out)
    if data.get("decision") == "block":
        return "block: " + data["reason"]
    return "other: " + out


def carried(text):
    return text.startswith("block:") and "CARRY ON" in text


# --- the symptom: a milestone reply that names the next card ------------------------------------

def test_a_reply_naming_the_next_card_does_not_stop_the_session():
    s = Session()
    s.owner("Entregue a feature de checkout inteira.")
    s.edit()
    s.reply("Card INFRA-101 entregue, PR #12 aberta. O próximo passo é o card INFRA-102.")
    out = s.stop()
    assert carried(out), out
    assert "[squad] DONE:" in out and "[squad] BLOCKED:" in out and "[squad] END:" in out, out


def test_english_next_step_is_caught_too():
    s = Session()
    s.owner("Ship the whole checkout feature.")
    s.edit()
    s.reply("Milestone 1 is merged.\n\nNext step: card 3, the payment webhook.")
    assert carried(s.stop())


def test_a_plain_answer_without_a_delivery_stops_as_before():
    s = Session()
    s.owner("Does the watcher run on WSL?")
    s.reply("Yes. It ran 170 tests green here.")
    assert s.stop() == "nothing"


# --- an explicit goal ---------------------------------------------------------------------------

def test_an_owner_goal_keeps_the_session_working_without_a_next_step_phrase():
    s = Session()
    s.owner("[squad] GOAL: ship the checkout feature, epic INFRA-100")
    s.edit()
    s.reply("Wrote the cart model and its tests.")
    out = s.stop()
    assert carried(out), out
    assert "ship the checkout feature" in out, out


def test_a_goal_the_session_declares_is_held_too():
    s = Session()
    s.owner("Build the plan and run it.")
    s.reply("[squad] GOAL: deliver docs/architect/plan.json, waves 1 to 3\n\nStarting wave 1.")
    s.edit()
    s.reply("Wave 1 dispatched.")
    out = s.stop()
    assert carried(out), out
    assert "waves 1 to 3" in out, out


def test_an_owner_question_mid_delivery_is_answered_and_the_work_resumes():
    s = Session()
    s.owner(f"[squad] GOAL: close card {CARD}")
    s.edit()
    s.reply("Working on it.")
    assert carried(s.stop())
    s.owner("Which branch are you on?")
    s.reply("feat/checkout.")
    assert carried(s.stop()), "an explicit goal outlives the owner's side question"


# --- the ways out -------------------------------------------------------------------------------

def test_done_is_accepted_when_the_named_card_is_done():
    s = Session(statuses={CARD: "DONE"})
    s.owner(f"[squad] GOAL: close card {CARD}")
    s.edit()
    s.reply("[squad] DONE: card closed, PR #12 merged with SHIP")
    assert s.stop() == "nothing"
    assert not s.state().get("goal"), s.state()
    s.owner("thanks, what's the time?")
    s.reply("13:00.")
    assert s.stop() == "nothing", "a delivered goal no longer holds the session"


def test_done_is_refused_while_the_named_card_is_open():
    s = Session(statuses={CARD: "IN_PROGRESS"})
    s.owner(f"[squad] GOAL: close card {CARD}")
    s.edit()
    s.reply("[squad] DONE: all good")
    out = s.stop()
    assert out.startswith("block:") and "DONE refused" in out and "IN_PROGRESS" in out, out


def test_done_is_accepted_when_agenfk_cannot_answer():
    s = Session(statuses={})               # the fake knows no card: the CLI fails, the hook fails open
    s.owner(f"[squad] GOAL: close card {CARD}")
    s.edit()
    s.reply("[squad] DONE: card closed")
    assert s.stop() == "nothing"


def test_blocked_releases_until_the_owner_speaks_again():
    s = Session()
    s.owner("[squad] GOAL: ship the checkout feature")
    s.edit()
    s.reply("[squad] BLOCKED: merge approval on PR #12 is the owner's")
    assert s.stop() == "nothing"
    s.look()
    s.reply("Still waiting for the approval.")
    assert s.stop() == "nothing", "BLOCKED holds until the owner answers"
    s.owner("Approved and merged, carry on.")
    s.edit()
    s.reply("Rebased the next branch.")
    assert carried(s.stop()), "the owner's answer re-arms the goal"


def test_end_releases_the_session():
    s = Session()
    s.owner("[squad] GOAL: ship the checkout feature")
    s.edit()
    s.reply("[squad] END: the epic was cancelled on the board")
    assert s.stop() == "nothing"
    assert not s.state().get("goal")


def test_quoting_the_marker_template_is_not_an_exit():
    s = Session()
    s.owner("[squad] GOAL: ship the checkout feature")
    s.edit()
    s.reply("I will finish with `[squad] DONE: <evidence>` later. Next step: the webhook.")
    assert carried(s.stop())


# --- the owner and the watcher win --------------------------------------------------------------

def test_the_owner_asking_to_stop_wins():
    s = Session()
    s.owner("[squad] GOAL: ship the checkout feature")
    s.edit()
    s.reply("Next step: the webhook.")
    assert carried(s.stop())
    s.owner("pode parar por hoje")
    s.reply("Ok. Next step tomorrow: the webhook.")
    assert s.stop() == "nothing"


def test_handoff_level_keeps_its_own_order():
    s = Session()
    s.owner("[squad] GOAL: ship the checkout feature")
    s.edit()
    s.reply("Next step: the webhook.")
    s.set_state(ctx_level=2, ctx=950_000)
    out = s.stop()
    assert "CARRY ON" not in out, out


def test_a_sub_agent_stop_is_not_held():
    s = Session()
    s.owner("[squad] GOAL: ship the checkout feature")
    s.reply("Next step: the webhook.")
    assert s.hook("Stop", {"agent_id": "a1"}) == ""


def test_an_implicit_next_step_does_not_outlive_the_owners_next_request():
    s = Session()
    s.owner("Run card INFRA-101.")
    s.edit()
    s.reply("Done with INFRA-101. Next card: INFRA-102.")
    assert carried(s.stop())
    s.owner("Forget that. What is 2+2?")
    s.reply("4.")
    assert s.stop() == "nothing"


# --- the runaway guard --------------------------------------------------------------------------

def test_three_stops_without_progress_release_the_session_and_leave_a_note():
    s = Session()
    s.owner("[squad] GOAL: ship the checkout feature")
    s.edit()
    s.reply("Next step: the webhook.")
    assert carried(s.stop())               # 1st hold: work was done before it
    out = ""
    for i in range(3):
        s.look()
        s.reply("Checked again, next step: the webhook.")
        out = s.stop()
        if i < 2:
            assert carried(out), (i, out)
    assert out == "nothing", out           # 3rd stop in a row without progress: released
    notes = " ".join(s.state().get("pending") or [])
    assert "without progress" in notes, s.state()
    s.look()
    s.reply("Next step: the webhook.")
    assert s.stop() == "nothing", "released stays released until progress or the owner"
    s.edit()
    s.reply("Wrote the webhook handler. Next step: its tests.")
    assert carried(s.stop()), "progress re-arms the goal"


def test_progress_between_stops_keeps_the_session_going():
    s = Session()
    s.owner("[squad] GOAL: ship the checkout feature")
    for _ in range(5):
        s.edit()
        s.reply("Next step: the next file.")
        assert carried(s.stop())


def test_the_cap_per_goal_releases_even_with_progress():
    s = Session(env={"SQUAD_CARRY_CAP": "2"})
    s.owner("[squad] GOAL: ship the checkout feature")
    for _ in range(2):
        s.edit()
        s.reply("Next step: the next file.")
        assert carried(s.stop())
    s.edit()
    s.reply("Next step: the next file.")
    assert s.stop() == "nothing"


def test_squad_carry_zero_turns_it_off():
    s = Session(env={"SQUAD_CARRY": "0"})
    s.owner("[squad] GOAL: ship the checkout feature")
    s.edit()
    s.reply("Next step: the webhook.")
    assert s.stop() == "nothing"


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
