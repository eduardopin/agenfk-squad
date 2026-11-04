#!/usr/bin/env python3
"""scripts/squad_settings.py honours a matcher set on one hook in the manifest.

A hook entry may carry "matchers": {event: matcher}, which wins over the
top-level "matchers" for that hook and event. Each test writes its own manifest
and config dir, so nothing here reads the real hooks/manifest.json or ~/.claude.

    python3 tests/test_squad_settings.py
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import squad_settings as ss  # noqa: E402

TMP = tempfile.mkdtemp(prefix="squad-settings-")


def config_with(*scripts):
    """A config dir whose hooks/ holds the given scripts, as install.sh leaves it."""
    config = tempfile.mkdtemp(dir=TMP)
    os.makedirs(os.path.join(config, "hooks"))
    for s in scripts:
        open(os.path.join(config, "hooks", s), "w").close()
    return config


def manifest(hooks, matchers=None):
    path = os.path.join(tempfile.mkdtemp(dir=TMP), "manifest.json")
    with open(path, "w") as fh:
        json.dump({"matchers": matchers if matchers is not None else {"PreToolUse": "*"},
                   "hooks": hooks}, fh)
    return path


def run(action, settings, config, manifest_path):
    plan, scripts = ss.load_manifest(manifest_path)
    report = []
    if action == "install":
        return ss.install(settings, config, plan, report), report
    return ss.uninstall(settings, config, set(scripts), report, set(plan)), report


def groups_of(settings, event, script, config):
    """The matcher of every group on this event that runs this squad script."""
    return [g.get("matcher") for g in settings.get("hooks", {}).get(event, [])
            for h in g.get("hooks", []) if ss.squad_script(h.get("command"), config) == script]


WATCH = {"script": "squad-watch.py", "timeout": 10, "events": ["PreToolUse", "Stop"]}
GRAPH = {"script": "squad-graph.py", "timeout": 10, "events": ["PreToolUse"],
         "matchers": {"PreToolUse": "Bash|Grep|Read|Glob"}}


# ---------------------------------------------------------------- tests

def test_a_hook_matcher_wins_over_the_top_level_one_for_its_event_only():
    config = config_with("squad-watch.py", "squad-graph.py")
    settings = {}
    changed, _ = run("install", settings, config, manifest([WATCH, GRAPH]))
    assert changed
    assert groups_of(settings, "PreToolUse", "squad-graph.py", config) == ["Bash|Grep|Read|Glob"], settings
    assert groups_of(settings, "PreToolUse", "squad-watch.py", config) == ["*"], settings
    assert groups_of(settings, "Stop", "squad-watch.py", config) == [None], settings


def test_a_hook_matcher_for_one_event_leaves_its_other_events_on_the_top_level_one():
    mixed = {"script": "squad-graph.py", "timeout": 10, "events": ["PreToolUse", "PostToolUse"],
             "matchers": {"PreToolUse": "Bash"}}
    config = config_with("squad-graph.py")
    settings = {}
    run("install", settings, config, manifest([mixed], matchers={"PreToolUse": "*", "PostToolUse": "Edit"}))
    assert groups_of(settings, "PreToolUse", "squad-graph.py", config) == ["Bash"], settings
    assert groups_of(settings, "PostToolUse", "squad-graph.py", config) == ["Edit"], settings


def test_a_hook_whose_link_is_missing_does_not_stay_on_its_old_broad_matcher():
    config = config_with()                                  # install.sh left no link
    old = {"type": "command", "command": ss.command_for(config, "squad-graph.py")}
    mine = {"type": "command", "command": "echo mine"}
    settings = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [old, mine]}]}}
    changed, report = run("install", settings, config, manifest([GRAPH]))
    assert changed, report
    assert settings == {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [mine]}]}}, settings
    assert any(line.startswith("SKIPPED") and "old registration was removed" in line for line in report), report


def test_two_matchers_on_one_event_land_in_two_groups():
    config = config_with("squad-watch.py", "squad-graph.py")
    settings = {}
    run("install", settings, config, manifest([WATCH, GRAPH]))
    pre = settings["hooks"]["PreToolUse"]
    assert sorted(g.get("matcher") for g in pre) == ["*", "Bash|Grep|Read|Glob"], pre
    assert all(len(g["hooks"]) == 1 for g in pre), pre


def test_hooks_that_share_a_matcher_share_one_group():
    other = dict(GRAPH, script="squad-other.py")
    config = config_with("squad-graph.py", "squad-other.py")
    settings = {}
    run("install", settings, config, manifest([GRAPH, other]))
    pre = settings["hooks"]["PreToolUse"]
    assert len(pre) == 1 and len(pre[0]["hooks"]) == 2, pre


def test_a_second_install_with_hook_matchers_changes_nothing():
    config = config_with("squad-watch.py", "squad-graph.py")
    m = manifest([WATCH, GRAPH])
    settings = {}
    run("install", settings, config, m)
    before = json.dumps(settings, sort_keys=True)
    changed, report = run("install", settings, config, m)
    assert not changed, report
    assert json.dumps(settings, sort_keys=True) == before


def test_a_hook_registered_under_an_old_matcher_moves_and_the_users_hooks_stay():
    config = config_with("squad-graph.py")
    old = ss.command_for(config, "squad-graph.py")
    mine = {"type": "command", "command": "echo mine"}
    settings = {"hooks": {"PreToolUse": [
        {"matcher": "*", "hooks": [{"type": "command", "command": old, "timeout": 10}, mine]},
        {"matcher": "Edit", "hooks": [{"type": "command", "command": old}]},
    ]}}
    changed, report = run("install", settings, config, manifest([GRAPH]))
    assert changed, report
    assert groups_of(settings, "PreToolUse", "squad-graph.py", config) == ["Bash|Grep|Read|Glob"], settings
    pre = settings["hooks"]["PreToolUse"]
    assert {"matcher": "*", "hooks": [mine]} in pre, pre           # the user's hook keeps its group
    assert not any(g.get("matcher") == "Edit" for g in pre), pre   # a group left empty is dropped
    assert any("moved" in line for line in report), report


def test_uninstall_removes_a_hook_registered_under_its_own_matcher():
    config = config_with("squad-watch.py", "squad-graph.py")
    m = manifest([WATCH, GRAPH])
    settings = {"model": "opus"}
    run("install", settings, config, m)
    changed, _ = run("uninstall", settings, config, m)
    assert changed
    assert settings == {"model": "opus"}, settings


def test_a_manifest_without_hook_matchers_installs_what_it_did_before():
    config = config_with("squad-router.py", "squad-watch.py")
    router = {"script": "squad-router.py", "timeout": 5, "events": ["UserPromptSubmit"]}
    settings = {}
    run("install", settings, config,
        manifest([router, WATCH], matchers={"UserPromptSubmit": "*", "PreToolUse": "*"}))
    expected = {"hooks": {
        "UserPromptSubmit": [{"matcher": "*", "hooks": [
            {"type": "command", "command": ss.command_for(config, "squad-router.py"), "timeout": 5}]}],
        "PreToolUse": [{"matcher": "*", "hooks": [
            {"type": "command", "command": ss.command_for(config, "squad-watch.py"), "timeout": 10}]}],
        "Stop": [{"hooks": [
            {"type": "command", "command": ss.command_for(config, "squad-watch.py"), "timeout": 10}]}],
    }}
    assert settings == expected, json.dumps(settings, indent=1)


def test_the_real_manifest_names_only_events_its_hooks_use_in_hook_matchers():
    with open(ss.MANIFEST) as fh:
        real = json.load(fh)
    for h in real["hooks"]:
        extra = set(h.get("matchers", {})) - set(h["events"])
        assert not extra, (h["script"], extra)


def test_a_copy_under_another_matcher_goes_even_when_the_right_one_is_there():
    config = config_with("squad-graph.py")
    cmd = {"type": "command", "command": ss.command_for(config, "squad-graph.py"), "timeout": 10}
    settings = {"hooks": {"PreToolUse": [
        {"matcher": "*", "hooks": [dict(cmd)]},
        {"matcher": "Bash|Grep|Read|Glob", "hooks": [dict(cmd)]},
    ]}}
    changed, report = run("install", settings, config, manifest([GRAPH]))
    assert changed, report
    assert groups_of(settings, "PreToolUse", "squad-graph.py", config) == ["Bash|Grep|Read|Glob"], settings
    changed, report = run("install", settings, config, manifest([GRAPH]))
    assert not changed, report


def test_only_the_hook_that_moves_leaves_a_group_it_shares_with_another_squad_hook():
    config = config_with("squad-watch.py", "squad-graph.py")
    shared = {"matcher": "*", "hooks": [
        {"type": "command", "command": ss.command_for(config, "squad-watch.py"), "timeout": 10},
        {"type": "command", "command": ss.command_for(config, "squad-graph.py"), "timeout": 10},
    ]}
    settings = {"hooks": {"PreToolUse": [shared]}}
    run("install", settings, config, manifest([WATCH, GRAPH]))
    assert groups_of(settings, "PreToolUse", "squad-watch.py", config) == ["*"], settings
    assert groups_of(settings, "PreToolUse", "squad-graph.py", config) == ["Bash|Grep|Read|Glob"], settings


def test_a_null_hook_matcher_means_no_matcher_even_when_the_top_level_has_one():
    config = config_with("squad-graph.py")
    settings = {}
    run("install", settings, config, manifest([dict(GRAPH, matchers={"PreToolUse": None})]))
    assert settings["hooks"]["PreToolUse"] == [{"hooks": [
        {"type": "command", "command": ss.command_for(config, "squad-graph.py"), "timeout": 10}]}], settings


def test_uninstall_keeps_the_users_hook_in_a_narrow_group():
    config = config_with("squad-graph.py")
    m = manifest([GRAPH])
    settings = {}
    run("install", settings, config, m)
    mine = {"type": "command", "command": "echo mine"}
    settings["hooks"]["PreToolUse"][0]["hooks"].append(mine)
    run("uninstall", settings, config, m)
    assert settings == {"hooks": {"PreToolUse": [{"matcher": "Bash|Grep|Read|Glob", "hooks": [mine]}]}}, settings


def test_uninstall_reaches_every_group_of_an_event():
    config = config_with("squad-watch.py", "squad-graph.py")
    m = manifest([WATCH, GRAPH])
    settings = {}
    run("install", settings, config, m)
    mine = {"type": "command", "command": "echo mine"}
    settings["hooks"]["PreToolUse"].append({"matcher": "Edit", "hooks": [mine]})
    run("uninstall", settings, config, m)
    assert settings == {"hooks": {"PreToolUse": [{"matcher": "Edit", "hooks": [mine]}]}}, settings


def test_a_malformed_hook_matcher_is_refused_before_anything_is_written():
    config = config_with("squad-graph.py")
    cases = [manifest([dict(GRAPH, matchers=bad)]) for bad in
             ("Bash", ["PreToolUse"], {"PreTooluse": "Bash"}, {"PreToolUse": 5}, {"PreToolUse": ["Bash"]})]
    cases.append(manifest([WATCH], matchers={"PreToolUse": 5}))          # the top-level value too
    for m in cases:
        with open(m) as fh:
            bad = fh.read()
        try:
            ss.load_manifest(m)
        except ss.BadManifest:
            pass
        else:
            raise AssertionError(f"{bad!r} was accepted")
        assert ss.main(["install", "--config", config, "--manifest", m]) == 2
        assert not os.path.exists(os.path.join(config, "settings.json"))


def test_a_moved_hook_keeps_the_timeout_the_user_gave_it():
    config = config_with("squad-graph.py")
    old = {"type": "command", "command": ss.command_for(config, "squad-graph.py"), "timeout": 45}
    settings = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [old]}]}}
    changed, report = run("install", settings, config, manifest([GRAPH]))      # the manifest says 10
    assert changed, report
    assert settings["hooks"]["PreToolUse"] == [{"matcher": "Bash|Grep|Read|Glob", "hooks": [
        {"type": "command", "command": ss.command_for(config, "squad-graph.py"), "timeout": 45}]}], settings


def test_collapsing_copies_keeps_the_timeout_the_user_gave_any_of_them():
    config = config_with("squad-watch.py")
    cmd = ss.command_for(config, "squad-watch.py")
    m = manifest([WATCH])                                   # the manifest says 10
    for first, second in (({"timeout": 10}, {"timeout": 99}), ({}, {"timeout": 99}), ({"timeout": 99}, {})):
        settings = {"hooks": {"PreToolUse": [
            {"matcher": "*", "hooks": [dict(first, type="command", command=cmd)]},
            {"matcher": "", "hooks": [dict(second, type="command", command=cmd)]},
        ]}}
        changed, report = run("install", settings, config, m)
        assert changed, report
        assert settings["hooks"]["PreToolUse"] == [{"matcher": "*", "hooks": [
            {"type": "command", "command": cmd, "timeout": 99}]}], (first, second, settings)
        changed, report = run("install", settings, config, m)
        assert not changed, report


def test_a_moved_hook_with_a_null_or_zero_timeout_gets_the_manifests():
    config = config_with("squad-graph.py")
    cmd = ss.command_for(config, "squad-graph.py")
    for given in (None, 0):
        settings = {"hooks": {"PreToolUse": [
            {"matcher": "*", "hooks": [{"type": "command", "command": cmd, "timeout": given}]}]}}
        changed, report = run("install", settings, config, manifest([GRAPH]))      # the manifest says 10
        assert changed, report
        assert settings["hooks"]["PreToolUse"] == [{"matcher": "Bash|Grep|Read|Glob", "hooks": [
            {"type": "command", "command": cmd, "timeout": 10}]}], (given, settings)


def test_a_hook_without_a_list_of_event_names_is_a_bad_manifest():
    config = config_with("squad-watch.py")
    missing = {k: v for k, v in WATCH.items() if k != "events"}
    for hook in (missing, dict(WATCH, events="Stop"), dict(WATCH, events=None), dict(WATCH, events=["Stop", 5])):
        m = manifest([hook])
        try:
            ss.load_manifest(m)
        except ss.BadManifest as e:
            assert "squad-watch.py" in str(e) and "events" in str(e), e
        else:
            raise AssertionError(f"{hook!r} was accepted")
        with contextlib.redirect_stderr(io.StringIO()):
            assert ss.main(["install", "--config", config, "--manifest", m]) == 2, hook
        assert not os.path.exists(os.path.join(config, "settings.json"))


def test_the_guarantees_name_the_empty_list_uninstall_removes():
    guarantees = ss.__doc__.split("Guarantees:")[1].split("\n\n")[0]
    with open(os.path.join(ROOT, "docs", "installation.md"), encoding="utf-8") as fh:
        windows_uninstall = fh.read().split("### Uninstall on Windows")[1].split("\n### ")[0]
    for text in (guarantees, windows_uninstall):
        flat = " ".join(text.split())
        assert "empty list" in flat and "manifest" in flat, text


def test_an_empty_or_missing_matcher_is_the_same_as_star():
    config = config_with("squad-watch.py")
    cmd = ss.command_for(config, "squad-watch.py")
    for pre in ({"matcher": ""}, {}):                       # the manifest says "*" on PreToolUse
        for stop in ({"matcher": "*"}, {"matcher": ""}):    # and nothing on Stop
            settings = {"hooks": {
                "PreToolUse": [dict(pre, hooks=[{"type": "command", "command": cmd, "timeout": 10}])],
                "Stop": [dict(stop, hooks=[{"type": "command", "command": cmd, "timeout": 10}])],
            }}
            before = json.dumps(settings, sort_keys=True)
            changed, report = run("install", settings, config, manifest([WATCH]))
            assert not changed, (pre, stop, report)
            assert json.dumps(settings, sort_keys=True) == before, settings
            assert all("already registered" in line for line in report), report


def test_install_leaves_no_empty_event_list_behind():
    config = config_with()                                  # install.sh left no link
    old = {"type": "command", "command": ss.command_for(config, "squad-graph.py")}
    settings = {"model": "opus", "hooks": {"PreToolUse": [{"matcher": "*", "hooks": [old]}]}}
    changed, report = run("install", settings, config, manifest([GRAPH]))
    assert changed, report
    assert settings == {"model": "opus"}, settings


def test_uninstall_clears_an_empty_event_list_an_older_install_left():
    config = config_with("squad-watch.py")
    m = manifest([dict(WATCH, events=["PreToolUse", "PostToolUse", "Stop"])],
                 matchers={"PreToolUse": "*", "PostToolUse": "*"})
    settings = {"model": "opus"}
    run("install", settings, config, m)
    settings["hooks"]["PostToolUse"] = []                   # an event the manifest names: the squad's leftover
    settings["hooks"]["Notification"] = []                  # one it does not name: the user's, left alone
    changed, report = run("uninstall", settings, config, m)
    assert changed, report
    assert settings == {"model": "opus", "hooks": {"Notification": []}}, settings


def test_uninstall_leaves_a_file_with_nothing_of_the_squads_alone():
    config = config_with("squad-watch.py")
    path = os.path.join(config, "settings.json")
    text = '{"hooks":   {"PreToolUse": [], "Notification": []}}\n'      # odd spacing: a rewrite would show
    with open(path, "w") as fh:
        fh.write(text)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert ss.main(["uninstall", "--config", config, "--manifest", manifest([WATCH])]) == 0
    with open(path) as fh:
        assert fh.read() == text
    assert not [f for f in os.listdir(config) if ".bak-" in f], os.listdir(config)
    assert "nothing to change" in out.getvalue(), out.getvalue()


def test_copies_under_empty_star_and_no_matcher_collapse_to_one():
    config = config_with("squad-watch.py")
    cmd = {"type": "command", "command": ss.command_for(config, "squad-watch.py"), "timeout": 10}
    mine = {"type": "command", "command": "echo mine"}
    settings = {"hooks": {"PreToolUse": [
        {"matcher": "", "hooks": [dict(cmd)]},
        {"matcher": "*", "hooks": [dict(cmd), mine]},
        {"hooks": [dict(cmd)]},
    ]}}
    changed, report = run("install", settings, config, manifest([WATCH]))
    assert changed, report
    assert settings["hooks"]["PreToolUse"] == [{"matcher": "", "hooks": [cmd]}, {"matcher": "*", "hooks": [mine]}], \
        settings
    changed, report = run("install", settings, config, manifest([WATCH]))
    assert not changed, report


def test_the_same_script_twice_on_one_event_is_refused():
    config = config_with("squad-graph.py")
    twice = manifest([GRAPH, dict(GRAPH, matchers={"PreToolUse": "Edit"})])
    try:
        ss.load_manifest(twice)
    except ss.BadManifest as e:
        assert "squad-graph.py" in str(e) and "PreToolUse" in str(e), e
    else:
        raise AssertionError("the same script twice on PreToolUse was accepted")
    assert ss.main(["install", "--config", config, "--manifest", twice]) == 2
    assert not os.path.exists(os.path.join(config, "settings.json"))
    # the same script on events that do not overlap is fine (the real manifest does it for PreCompact)
    plan, _ = ss.load_manifest(manifest([WATCH, dict(WATCH, events=["PreCompact"], timeout=20)]))
    assert plan["PreCompact"] == [("squad-watch.py", 20, None)], plan


def test_a_top_level_matchers_that_is_not_an_object_is_a_bad_manifest():
    for bad in ("*", ["PreToolUse"], 5):
        try:
            ss.load_manifest(manifest([WATCH], matchers=bad))
        except ss.BadManifest:
            pass
        else:
            raise AssertionError(f"top-level matchers {bad!r} was accepted")


def test_uninstall_needs_only_the_script_names_from_the_manifest():
    no_events = {k: v for k, v in WATCH.items() if k != "events"}
    broken = [
        manifest([WATCH, GRAPH], matchers="*"),                                     # top-level matchers
        manifest([WATCH, GRAPH, dict(GRAPH, matchers={"PreToolUse": "Edit"})]),     # a script twice
        manifest([WATCH, dict(GRAPH, matchers="Bash")]),                            # a hook's matchers
    ]
    bad_events = [manifest([hook, GRAPH]) for hook in                              # a hook's events:
                  (dict(WATCH, events="Stop"), no_events, dict(WATCH, events=None),    # a string, none, null
                   dict(WATCH, events={"Stop": True}))]                              # and an object
    for m in bad_events:
        # only the well-formed hook's events count; reading "events" as it comes would add S, t, o, p or Stop
        assert ss.manifest_names(m) == (["squad-watch.py", "squad-graph.py"], {"PreToolUse"}), m
    for m in broken + bad_events:
        config = config_with("squad-watch.py", "squad-graph.py")
        assert ss.main(["install", "--config", config, "--manifest", manifest([WATCH, GRAPH])]) == 0
        assert ss.main(["install", "--config", config, "--manifest", m]) == 2
        assert ss.main(["uninstall", "--config", config, "--manifest", m]) == 0, m
        with open(os.path.join(config, "settings.json")) as fh:
            assert json.load(fh) == {}, m


if __name__ == "__main__":
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                fn()
                print("ok ", name)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
