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


# --- review round 1 and the owner's example of 2026-11-04 ---------------------------------------

def test_an_offer_to_open_the_next_front_does_not_stop_the_session():
    s = Session()
    s.owner("quais frentes ainda estão em andamento?")
    s.reply("Nenhuma. Esperando você: #69 falta o merge.\n\n"
            "Ainda não começou: o follow-up do #74.\n\nPosso abrir essa frente agora, se você quiser.")
    out = s.stop()
    assert carried(out), out
    assert "merge" in out and "only the owner" in out, "the order separates owner gates from the session's work"


def test_english_offers_are_caught():
    for text in ("Want me to start the webhook card now?", "Shall I open the PR?",
                 "I can open the follow-up front if you want."):
        s = Session()
        s.owner("status?")
        s.reply(text)
        assert carried(s.stop()), text


def test_an_implicit_hold_ends_when_the_reply_no_longer_names_a_next_step():
    s = Session()
    s.owner("Explain the deploy flow.")
    s.reply("The next step in the docs is the release tag.")
    assert carried(s.stop())
    s.reply("To be clear: there is nothing for me to do here, that was an explanation.")
    assert s.stop() == "nothing"


def test_an_implicit_hold_without_work_lets_go_sooner_than_an_explicit_one():
    s = Session()
    s.owner("Explain the deploy flow.")
    holds = 0
    for _ in range(4):
        s.reply("Next step in the docs: the release tag.")
        if carried(s.stop()):
            holds += 1
    assert holds == 2, holds              # the first hold is not counted as progress


def test_restating_the_goal_does_not_reset_the_guard():
    s = Session()
    s.owner("Build the plan and run it.")
    s.reply("[squad] GOAL: deliver the plan\n\nStarting.")
    s.edit()
    s.reply("Wrote wave 1.")
    assert carried(s.stop())
    outs = []
    for _ in range(3):
        s.look()
        s.reply("[squad] GOAL: deliver the plan\n\nChecking again.")
        outs.append(s.stop())
    assert outs[-1] == "nothing", outs     # 3 stops without progress, restated goal or not


def test_a_session_goal_does_not_lift_the_owners_blocked():
    s = Session()
    s.owner("[squad] GOAL: ship the checkout feature")
    s.edit()
    s.reply("[squad] BLOCKED: the owner approves the card on the board")
    assert s.stop() == "nothing"
    s.reply("[squad] GOAL: ship the checkout feature\n\nStill waiting.")
    assert s.stop() == "nothing"


def test_an_explicit_goal_without_progress_for_a_day_expires():
    s = Session()
    s.owner("[squad] GOAL: ship the checkout feature")
    s.edit()
    s.reply("Working.")
    assert carried(s.stop())
    st = s.state()
    st["goal"]["progress_ts"] = st["goal"]["ts"] = st["goal"]["ts"] - 13 * 3600
    s.set_state(goal=st["goal"])
    s.owner("unrelated: what time is it?")
    s.reply("15:00.")
    assert s.stop() == "nothing"
    assert not s.state().get("goal"), s.state()


def test_two_releases_in_a_row_close_an_explicit_goal():
    s = Session()
    s.owner("[squad] GOAL: ship the checkout feature")
    s.edit()
    s.reply("Working.")
    s.stop()
    for _ in range(2):                     # the owner keeps chatting, the session never works
        for _ in range(3):
            s.look()
            s.reply("ok.")
            s.stop()
        s.owner("another unrelated question")
    assert not s.state().get("goal"), s.state()


def test_the_third_refused_done_is_accepted_while_the_card_is_open():
    s = Session(statuses={CARD: "IN_PROGRESS"})
    s.owner(f"[squad] GOAL: close card {CARD}")
    for _ in range(3):
        s.edit()
        s.reply("[squad] DONE: all good")
        assert "DONE refused" in s.stop()
    s.edit()
    s.reply("[squad] DONE: all good")
    assert s.stop() == "nothing"


def test_done_is_refused_for_a_card_named_by_its_eight_character_prefix():
    s = Session(statuses={"51d1c739": "IN_PROGRESS"})
    s.owner("[squad] GOAL: ship checkout, card 51d1c739")
    s.edit()
    s.reply("[squad] DONE: all done")
    assert "DONE refused: card 51d1c739" in s.stop()


def test_a_date_or_a_word_is_not_taken_for_a_card_prefix():
    s = Session(statuses={"20261104": "IN_PROGRESS", "deadbeef": "IN_PROGRESS"})
    s.owner("[squad] GOAL: ship checkout by 20261104, no deadbeef")
    s.edit()
    s.reply("[squad] DONE: all done")
    assert s.stop() == "nothing"


def test_a_prefix_and_its_full_id_are_one_card():
    s = Session(statuses={CARD: "IN_PROGRESS"})
    s.owner(f"[squad] GOAL: ship checkout, card {CARD[:8]} ({CARD})")
    assert s.state()["goal"]["cards"] == [CARD]


def test_an_external_id_is_not_looked_up_so_the_goal_carries_the_agenfk_id():
    s = Session(statuses={"INFRA-419": "IN_PROGRESS"})
    s.owner("[squad] GOAL: ship checkout, INFRA-419")
    s.edit()
    s.reply("[squad] DONE: all done")
    assert s.stop() == "nothing"


def test_done_check_stays_inside_the_hook_timeout():
    import time as _t
    cards = [f"{i:08x}-2222-3333-4444-555555555555" for i in range(8)]
    s = Session(statuses={c: "DONE" for c in cards})
    slow = os.path.join(s.h, "slow.py")
    with open(slow, "w") as fh:
        fh.write("import time, json, sys\ntime.sleep(2)\nprint(json.dumps({'status': 'IN_PROGRESS'}))\n")
    s.env["SQUAD_AGENFK"] = f"{sys.executable} {slow}"
    s.owner("[squad] GOAL: close " + " ".join(cards))
    s.edit()
    s.reply("[squad] DONE: all closed")
    t0 = _t.time()
    out = s.stop()
    assert _t.time() - t0 < 9, _t.time() - t0
    assert "DONE refused" in out, out      # lookups run in parallel and still answer


# --- review round 2 ------------------------------------------------------------------------------

def test_a_conceptual_answer_ending_in_an_offer_is_not_held():
    for text in ("X works by polling the queue every 5 s. Want me to go deeper?",
                 "That is the whole mechanism. Shall I show an example?",
                 "Ficou claro? Quer que eu explique melhor a parte do cache?"):
        s = Session()
        s.owner("how does X work?")
        s.reply(text)
        assert s.stop() == "nothing", text


def test_an_offer_after_work_in_the_turn_is_held_even_without_a_delivery_word():
    s = Session()
    s.owner("refactor the parser")
    s.edit()
    s.reply("Refactored the tokenizer. Want me to go on with the rest?")
    assert carried(s.stop())


def test_an_offer_inside_a_code_block_or_a_quote_is_not_read():
    s = Session()
    s.owner("show me the prompt you use")
    s.reply("Here it is:\n```\nPosso abrir essa frente agora, se você quiser.\n```\n> want me to open the PR?\n")
    assert s.stop() == "nothing"


def test_a_blocked_goal_survives_a_night_and_re_arms_when_the_owner_answers():
    s = Session()
    s.owner("[squad] GOAL: ship the checkout feature")
    s.edit()
    s.reply("[squad] BLOCKED: merge approval on PR #12 is the owner's")
    assert s.stop() == "nothing"
    st = s.state()
    st["goal"]["ts"] -= 13 * 3600
    st["goal"]["progress_ts"] = st["goal"].get("progress_ts", st["goal"]["ts"]) - 13 * 3600
    s.set_state(goal=st["goal"])
    s.owner("merged, carry on")
    s.edit()
    s.reply("Rebased the next branch.")
    assert carried(s.stop()), s.state()


def test_the_merge_is_the_sessions_only_where_the_project_lets_it_merge():
    s = Session()
    s.owner("[squad] GOAL: ship the checkout feature")
    s.edit()
    s.reply("Next step: the webhook.")
    out = s.stop()
    assert "merge where the project's rules let the session merge" in out, out
    assert "merge approval the project reserves for the owner" in out, out


def test_reaching_the_cap_closes_the_goal():
    s = Session(env={"SQUAD_CARRY_CAP": "1"})
    s.owner("[squad] GOAL: ship the checkout feature")
    s.edit()
    s.reply("Working.")
    assert carried(s.stop())
    s.edit()
    s.reply("Working more.")
    assert s.stop() == "nothing"
    assert not s.state().get("goal"), s.state()


def test_the_cap_closes_an_implicit_goal_too():
    s = Session(env={"SQUAD_CARRY_CAP": "1"})
    s.owner("ship it")
    verdicts = []
    for _ in range(4):
        s.edit()
        s.reply("Next step: the next file.")
        verdicts.append(carried(s.stop()))
    assert verdicts == [True, False, False, False], verdicts
    s.owner("next request")                             # only the owner reopens the implicit hold
    s.edit()
    s.reply("Next step: the next file.")
    assert carried(s.stop())


def test_an_offer_about_an_issue_or_a_commit_is_not_a_delivery():
    for text in ("The failure comes from issue 12 upstream.\nShall I explain the issue in more detail?",
                 "That branch has 3 commits. Want me to look at the commit message?"):
        s = Session()
        s.owner("why is this branch red?")
        s.reply(text)
        assert s.stop() == "nothing", text


def test_a_closed_goal_is_not_reopened_by_the_session_alone():
    s = Session(env={"SQUAD_CARRY_CAP": "1"})
    s.owner("go")
    s.reply("[squad] GOAL: deliver the plan\n\nStarting.")
    s.edit()
    s.reply("Wave 1.")
    assert carried(s.stop())
    s.edit()
    s.reply("[squad] GOAL: deliver the plan\n\nWave 2.")
    assert s.stop() == "nothing"                  # the cap closed it
    s.edit()
    s.reply("[squad] GOAL: deliver the plan\n\nWave 3.")
    assert s.stop() == "nothing", "the same goal does not come back until the owner speaks"


# --- review round 4: the lines the session is told to write must be lines the watcher reads -------

def _load(name, file):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "hooks", file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_windows_agenfk_command_keeps_its_backslashes_and_drops_its_quotes():
    w = _load("squad_watch_argv", "squad-watch.py")
    assert w.agenfk_argv(r"C:\hostedtoolcache\python.exe C:\tools\agenfk.py", windows=True) == \
        [r"C:\hostedtoolcache\python.exe", r"C:\tools\agenfk.py"]
    assert w.agenfk_argv(r'"C:\Program Files\Python\python.exe" "C:\a b\agenfk.py"', windows=True) == \
        [r"C:\Program Files\Python\python.exe", r"C:\a b\agenfk.py"]
    assert w.agenfk_argv("python3 '/tmp/a b/agenfk.py'", windows=False)[1:] == ["/tmp/a b/agenfk.py"]
    assert w.agenfk_argv('"unclosed', windows=False) == []


def test_card_status_runs_the_windows_split_of_a_quoted_agenfk_path():
    """card_status reaches agenfk through agenfk_argv, so a Windows SQUAD_AGENFK with a space in its
    path must arrive as one word, and a bare one with its backslashes: the split is faked as Windows here."""
    w = _load("squad_watch_card_status", "squad-watch.py")
    seen = []

    class Done:
        returncode, stdout = 0, '{"status": "IN_PROGRESS"}'

    real_which, real_run = shutil.which, subprocess.run     # the module objects are shared: put them back
    w.AGENFK = r'"C:\Program Files\Python\python.exe" C:\tools\agenfk.py'
    w.agenfk_argv.__defaults__ = (None, True)         # the `windows` default, as on a Windows runner
    try:
        shutil.which = lambda word: None              # no launcher lookup: the quoted path stays as is
        subprocess.run = lambda argv, **kw: seen.append(argv) or Done()
        assert w.card_status(CARD) == "IN_PROGRESS"
    finally:
        shutil.which, subprocess.run = real_which, real_run
    assert seen == [[r"C:\Program Files\Python\python.exe", r"C:\tools\agenfk.py", "get", CARD, "--json"]]


def _shown_lines(text):
    """The `[squad] KEY: text` lines an instruction shows in backticks, as a session would copy them."""
    import re
    return re.findall(r"`(\[squad\] (?:GOAL|DONE|BLOCKED|END): [^`]+)`", text)


def _shown_everywhere(watch, wake):
    sources = {"CARRY_EXITS": watch.CARRY_EXITS, "wake text": wake.text(5)}
    for rel in ("CLAUDE.md", os.path.join("skills", "architect", "SKILL.md"), os.path.join("docs", "watcher.md")):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
            sources[rel] = fh.read()
    s = Session(statuses={CARD: "IN_PROGRESS"})
    s.owner(f"[squad] GOAL: close card {CARD}")
    s.edit()
    s.reply("[squad] DONE: card closed")
    sources["DONE refusal"] = s.stop()
    return [(name, line) for name, text in sources.items() for line in _shown_lines(text)]


def test_every_goal_line_the_instructions_show_is_one_the_watcher_reads():
    watch, wake = _load("sw_goal", "squad-watch.py"), _load("sq_goal", "squad-wake.py")
    shown = [(n, l) for n, l in _shown_everywhere(watch, wake) if l.startswith("[squad] GOAL")]
    assert len(shown) >= 2, shown
    for name, line in shown:
        assert watch.GOAL.search(line), f"{name} tells the session to write a goal the watcher does not read: {line}"


def test_every_exit_line_the_instructions_show_is_one_the_watcher_reads():
    watch, wake = _load("sw_lines", "squad-watch.py"), _load("sq_lines", "squad-wake.py")
    shown = [(n, l) for n, l in _shown_everywhere(watch, wake) if not l.startswith("[squad] GOAL")]
    assert len(shown) >= 8, shown
    for name, line in shown:
        assert watch.MARK.search(line), f"{name} tells the session to write a line the watcher does not read: {line}"
        assert wake.MARK.search(line), f"{name}: squad-wake does not read {line}"


def test_the_watcher_and_the_wake_read_end_lines_the_same_way():
    watch, wake = _load("sw_parity", "squad-watch.py"), _load("sq_parity", "squad-wake.py")
    lines = ["[squad] DONE: x", "[squad] END: x", "[squad] BLOCKED: x", "  > [squad] done: x",
             "`[squad] DONE: <evidence>`", "[squad] DONE: <evidence: PRs merged, cards DONE>",
             "[squad] END: <why nothing is left>", "[squad] DONE: PR #64 merged, cards DONE",
             "see [squad] DONE: x", "[squad] DONE:", "[squad] MAYBE: x"]
    verdicts = [(bool(watch.MARK.search(x)), bool(wake.MARK.search(x))) for x in lines]
    assert all(a == b for a, b in verdicts), list(zip(lines, verdicts))
    assert [a for a, _ in verdicts][:3] == [True, True, True]
    assert watch.MARK.pattern == wake.MARK.pattern


def test_a_blocked_goal_expires_after_a_week():
    s = Session()
    s.owner("[squad] GOAL: ship the checkout feature")
    s.edit()
    s.reply("[squad] BLOCKED: approval on the board")
    s.stop()
    st = s.state()
    st["goal"]["progress_ts"] -= 8 * 86400
    s.set_state(goal=st["goal"])
    s.owner("unrelated: hi")
    assert not s.state().get("goal"), s.state()


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
