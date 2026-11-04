#!/usr/bin/env python3
"""squad-router: route hints, and the owner's standing rules flagged as decisions to record.

Self-checking: the exit code is the verdict. No network, no files written.

    python3 tests/test_router.py

The directive detector is judged on both sides: rules the owner states (English
and Portuguese, with and without accents) must fire, and questions or ordinary
work requests that merely contain always / never / sempre / nunca must not.
The hook itself must stay inside its line budget and exit 0 on any input.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "hooks", "squad-router.py")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


router = load("squad_router", HOOK)


def run(stdin: bytes):
    done = subprocess.run([sys.executable, HOOK], input=stdin, capture_output=True, timeout=20,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    return done.returncode, done.stdout.decode("utf-8"), done.stderr.decode("utf-8")


def prompt(text):
    return run(json.dumps({"prompt": text}).encode())


def is_directive(text):
    return router.directive(router.norm(text))


POSITIVE_EN = [
    "Always run the full suite before pushing.",
    "Never commit directly to main.",
    "From now on, PRs target beta.",
    "Going forward, use pnpm instead of npm.",
    "We don't use ORMs in this repo.",
    "Don't ever force-push a shared branch.",
    "Stop using moment.js, it is deprecated.",
    "Prefer httpx over requests for new code.",
    "Please always pass the card id to the agents.",
    "Pull requests on this repo target beta from now on.",
    "Fix the login bug. Also, never deploy on Fridays.",
    "Rule: never merge with a red gate",
    "We don’t ship on Fridays.",
]

POSITIVE_PT = [
    "Sempre rode a suíte antes do push.",
    "Nunca faça commit direto na main.",
    "Daqui em diante, os PRs vão para a beta.",
    "A partir de agora use pnpm.",
    "Não use ORM neste projeto.",
    "Nao use ORM neste projeto.",
    "Pare de usar moment.js.",
    "Prefira httpx a requests.",
    "Nunca mais abra PR sem o verificador.",
    "A gente não usa ORM aqui.",
    "Sempre que abrir um PR, rode a suíte inteira.",
    "Por favor, nunca use --no-verify.",
    "Quero que você sempre comente o card.",
    "Os PRs vão para a beta de agora em diante.",
]

NEGATIVE = [
    # questions
    "Should we always squash merges?",
    "Why does the build never finish?",
    "Does the router always print two hints",
    "Is it true we never ran the migration?",
    "The test always fails on CI, can you fix it?",
    "Por que o build nunca termina?",
    "Devemos sempre usar pnpm?",
    "Devemos sempre usar pnpm",
    "Como a gente nunca viu isso?",
    # ordinary work requests that contain the word
    "Fix the flaky test that never finishes.",
    "Add a retry so the upload never times out.",
    "Implement an always-on toggle in the settings.",
    "Write tests for the case where the parser never returns.",
    "Never mind, just fix the login.",
    "Never seen this error before, investigate the logs.",
    "We don't have tests for the parser; write them.",
    "Stop the server and restart it.",
    "O teste sempre falha no CI, corrija.",
    "Sempre que rodo o teste ele trava, investigue.",
    "Nunca funcionou o login no Safari, conserta.",
    "Nunca mais funcionou depois do deploy, veja os logs.",
    "O servidor parou de responder depois do deploy.",
    "Faça o deploy, nunca vi esse erro.",
    "Refactor the cache so it is always warm.",
    "Me explique por que a fila sempre trava.",
    "Always the same error, check the logs.",
    "Sempre o mesmo erro, veja os logs.",
    # an instruction for one run, not a standing rule
    "Não use o cache nesse teste, ele está corrompido.",
    "Stop using the staging database for now.",
    # text the owner did not type as a rule
    "[squad-watch] WAKE: never stop until it is delivered",
    "<task-notification>always run the suite</task-notification>",
    "```\nalways run make\n```\nWhat does this script do",
    "> PRs target beta from now on\nWho wrote this line in the README",
    "should PRs target beta from now on",
    "Devemos mandar os PRs para a beta daqui em diante",
]


def test_positive_directives_in_english_fire():
    missed = [p for p in POSITIVE_EN if not is_directive(p)]
    assert len(POSITIVE_EN) >= 5 and not missed, missed


def test_positive_directives_in_portuguese_fire_with_or_without_accents():
    missed = [p for p in POSITIVE_PT if not is_directive(p)]
    assert len(POSITIVE_PT) >= 5 and not missed, missed


def test_questions_and_work_requests_do_not_fire():
    fired = [p for p in NEGATIVE if is_directive(p)]
    assert len(NEGATIVE) >= 10 and not fired, fired


def test_the_hook_prints_one_decision_line_for_a_directive():
    rc, out, err = prompt("Never commit directly to main. And always run the suite.")
    lines = out.splitlines()
    assert rc == 0 and not err, (rc, err)
    assert lines == [router.DECISION], lines
    assert lines[0].startswith("[squad decision] ")
    assert "squad-decisions add" in lines[0] and "--source owner" in lines[0], lines[0]
    assert "open a card" in lines[0], lines[0]


def test_the_decision_line_uses_a_source_the_decisions_cli_accepts():
    decisions = load("squad_decisions", os.path.join(ROOT, "hooks", "squad-decisions.py"))
    source = re.search(r"--source (\S+?)`", router.DECISION)
    assert source and decisions.SOURCE.fullmatch(source.group(1)), router.DECISION


def test_the_hook_stays_silent_on_a_question_or_a_plain_task():
    for text in ("Should we always squash merges?", "Fix the flaky test that never finishes."):
        rc, out, err = prompt(text)
        assert (rc, out, err) == (0, "", ""), (text, rc, out, err)


def test_existing_route_hints_still_fire_and_carry_no_decision_line():
    rc, out, _ = prompt("write the terraform for the new vpc")
    lines = out.splitlines()
    assert rc == 0 and lines[0].startswith("[squad route] infra -> `infra` agent"), lines
    assert lines[-1].startswith("A project-specific agent"), lines
    assert not any(l.startswith("[squad decision]") for l in lines), lines
    rc, out, _ = prompt("quanto custa o cluster por mês?")
    assert rc == 0 and out.startswith("[squad route] cost -> `finops` agent"), out


def test_the_output_stays_within_the_hint_budget_plus_one_decision_line():
    # matches infra, finops and product: three roles, more than MAX_HINTS
    text = ("From now on, always run terraform plan first; tell me how much does the cluster cost "
            "and prioritize the roadmap. Never deploy on Fridays.")
    rc, out, _ = prompt(text)
    lines = out.splitlines()
    routes = [l for l in lines if l.startswith("[squad route] ")]
    decisions = [l for l in lines if l.startswith("[squad decision] ")]
    assert rc == 0, rc
    assert len(routes) == router.MAX_HINTS and len(decisions) == 1, lines
    assert len(lines) <= router.MAX_HINTS + 2, lines
    assert lines[-1] == router.DECISION, lines


def test_malformed_input_exits_0_without_output():
    for raw in (b"", b"not json", b"[]", b"null", b"\"always run\"", b"{\"prompt\": 5}",
                b"{\"prompt\": null}", b"{\"prompt\": [\"always run\"]}", b"\xff\xfe\x00", b"{\"prompt\": "):
        rc, out, err = run(raw)
        assert (rc, out, err) == (0, "", ""), (raw, rc, out, err)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok ", name)
