#!/usr/bin/env python3
"""skill-export: a skill leaves as a zip claude.ai and ChatGPT accept, with the frontmatter they parse, and a
skill carrying a secret never leaves at all.

Self-checking: the exit code is the verdict. Every case builds its skills under one temporary directory that
is removed afterwards. Standard library only: the script reads the flat `key: value` frontmatter itself.

    python3 tests/test_skill_export.py
"""
import contextlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
EXPORT = ROOT / "skills" / "skill-export" / "scripts" / "export_skills.py"
# Fake identifiers: twelve digits, a GUID, an SSO instance id. None of them belongs to anyone.
ACCOUNT_A, ACCOUNT_B = "123456789012", "210987654321"
DATADOG_LAYER_ACCOUNT = "464622532012"
FAKE_GUID = "11111111-2222-3333-4444-555555555555"
FAKE_GUID_DIGITS = "11111111-2222-3333-4444-123456789012"


@contextlib.contextmanager
def workdir():
    tmp = tempfile.mkdtemp(prefix="skill-export-test-")
    try:
        yield Path(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def load_script():
    spec = importlib.util.spec_from_file_location("export_skills_under_test", EXPORT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def skill(root, name, head, body="Body.\n", extra=None):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\n{head}\n---\n{body}", encoding="utf-8")
    for rel, text in (extra or {}).items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(text, encoding="utf-8")
    return d


def run(tmp, src, *args):
    out = tmp / "out"
    org = str(tmp / "none.json")
    r = subprocess.run([sys.executable, str(EXPORT), "--personal", str(src), "--out", str(out),
                        "--org-manifest", org, *args],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    return r, out


def manifest(out):
    return json.loads((out / "manifest.json").read_text(encoding="utf-8"))


def frontmatter_of(z, name):
    head = z.read(f"{name}/SKILL.md").decode("utf-8").split("---")[1]
    return load_script().parse_frontmatter(head)


def test_help_exits_zero_and_needs_no_third_party_module():
    r = subprocess.run([sys.executable, str(EXPORT), "--help"], capture_output=True, text=True,
                       encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    assert "--out" in r.stdout and "--redact-email-domain" in r.stdout, r.stdout
    load_script()
    assert "yaml" not in sys.modules, "the script must not import PyYAML"


def test_exports_a_zip_per_skill_and_one_plugin():
    with workdir() as tmp:
        src = tmp / "src"
        skill(src, "notes", "name: notes\ndescription: Take notes: meetings, calls\nversion: 1.2.0",
              extra={"scripts/run.py": "print(1)\n", "__pycache__/x.pyc": "junk"})
        r, out = run(tmp, src)
        assert r.returncode == 0, r.stderr
        with zipfile.ZipFile(out / "skills" / "notes.zip") as z:
            assert sorted(z.namelist()) == ["notes/SKILL.md", "notes/scripts/run.py"]
            fm = frontmatter_of(z, "notes")
        assert fm == {"name": "notes", "description": "Take notes: meetings, calls",
                      "metadata": {"version": "1.2.0"}}, fm
        with zipfile.ZipFile(out / "plugin" / "exported-skills.zip") as z:
            assert json.loads(z.read(".claude-plugin/plugin.json"))["name"] == "exported-skills"
            assert "skills/notes/SKILL.md" in z.namelist()


def test_the_exported_frontmatter_is_strict_yaml_with_quoted_strings():
    # Claude Code takes `description: a: b` bare; the uploaders do not, so the export quotes every string.
    with workdir() as tmp:
        src = tmp / "src"
        skill(src, "notes", "name: notes\ndescription: Take notes: meetings, calls\nargument-hint: [file]")
        r, out = run(tmp, src)
        assert r.returncode == 0, r.stderr
        with zipfile.ZipFile(out / "skills" / "notes.zip") as z:
            head = z.read("notes/SKILL.md").decode("utf-8").split("---")[1]
        assert 'description: "Take notes: meetings, calls"' in head, head
        assert 'argument-hint: "[file]"' in head, head


def test_same_input_gives_the_same_sha():
    with workdir() as tmp:
        src = tmp / "src"
        skill(src, "notes", "name: notes\ndescription: Take notes")
        _, out = run(tmp, src)
        first = manifest(out)[0]["sha256"]
        _, out = run(tmp, src)
        assert manifest(out)[0]["sha256"] == first


def test_secret_or_invalid_name_is_skipped_and_fails_the_run():
    with workdir() as tmp:
        src = tmp / "src"
        skill(src, "leaky", "name: leaky\ndescription: Leaks", extra={"ref.md": "key AKIAABCDEFGHIJKLMNOP\n"})
        skill(src, "claude-helper", "name: claude-helper\ndescription: Reserved word")
        skill(src, "agenfk-code", "name: agenfk-code\ndescription: Excluded by default")
        r, out = run(tmp, src)
        assert r.returncode == 1
        rows = {m["name"]: m for m in manifest(out)}
        assert set(rows) == {"leaky", "claude-helper"}, set(rows)
        assert any("aws-access-key" in e for e in rows["leaky"]["errors"])
        assert not (out / "skills" / "leaky.zip").exists()
        assert not (out / "skills" / "claude-helper.zip").exists()


def test_account_ids_guids_and_sso_instance_become_placeholders():
    with workdir() as tmp:
        src = tmp / "src"
        skill(src, "deploy", f"name: deploy\ndescription: Deploy to {ACCOUNT_A}",
              body=f"Role arn:aws:iam::{ACCOUNT_B}:role/x, layer arn:aws:lambda:r:{DATADOG_LAYER_ACCOUNT}:layer:DD.\n"
                   f"Tenant {FAKE_GUID}.\n",
              extra={"ref.md": "sso ssoins-0123456789abcdef\n", "sum.sha256": "ab905174938448cd\n"})
        r, out = run(tmp, src)
        assert r.returncode == 0, r.stderr
        with zipfile.ZipFile(out / "skills" / "deploy.zip") as z:
            text = z.read("deploy/SKILL.md").decode("utf-8") + z.read("deploy/ref.md").decode("utf-8")
            assert z.read("deploy/sum.sha256").decode("utf-8") == "ab905174938448cd\n"
        for leaked in (ACCOUNT_A, ACCOUNT_B, FAKE_GUID, "ssoins-0123"):
            assert leaked not in text, leaked
        assert DATADOG_LAYER_ACCOUNT in text and "<AWS_ACCOUNT_ID>" in text
        assert manifest(out)[0]["redacted"] == {"aws-account-id": 2, "guid": 1, "sso-instance": 1}
        assert (out / "INSTALL.md").is_file()


def test_email_domains_are_redacted_only_when_asked_for():
    with workdir() as tmp:
        src = tmp / "src"
        skill(src, "mail", "name: mail\ndescription: Mail",
              body="Ask Alice.Smith@EXAMPLE.ORG or ops@mail.example.org, not bob@elsewhere.net.\n")
        r, out = run(tmp, src)
        assert r.returncode == 0, r.stderr
        with zipfile.ZipFile(out / "skills" / "mail.zip") as z:
            assert "Alice.Smith@EXAMPLE.ORG" in z.read("mail/SKILL.md").decode("utf-8")
        r, out = run(tmp, src, "--redact-email-domain", "example.org")
        assert r.returncode == 0, r.stderr
        with zipfile.ZipFile(out / "skills" / "mail.zip") as z:
            text = z.read("mail/SKILL.md").decode("utf-8")
        assert "Alice.Smith" not in text and "ops@" not in text, text
        assert "bob@elsewhere.net" in text and text.count("<EMAIL>") == 2, text
        assert manifest(out)[0]["redacted"] == {"email": 2}


def test_literal_strings_on_the_redaction_list_are_replaced():
    with workdir() as tmp:
        src = tmp / "src"
        skill(src, "ref", "name: ref\ndescription: Work at Acme Widgets",
              body="Acme Widgets runs on project-falcon.\n", extra={"a.md": "see PROJECT-FALCON\n"})
        r, out = run(tmp, src, "--redact-literal", "Acme Widgets", "--redact-literal", "project-falcon")
        assert r.returncode == 0, r.stderr
        with zipfile.ZipFile(out / "skills" / "ref.zip") as z:
            text = z.read("ref/SKILL.md").decode("utf-8") + z.read("ref/a.md").decode("utf-8")
        assert "Acme" not in text and "falcon" not in text.lower(), text
        assert manifest(out)[0]["redacted"] == {"literal": 4}


def test_skill_the_org_already_delivers_is_not_exported():
    with workdir() as tmp:
        src = tmp / "src"
        skill(src, "runbook", "name: runbook\ndescription: Org copy exists")
        skill(src, "notes", "name: notes\ndescription: Take notes")
        org = tmp / "synced" / "manifest.json"
        org.parent.mkdir()
        org.write_text(json.dumps({"skills": [{"name": "runbook"}]}), encoding="utf-8")
        r, out = run(tmp, src, "--org-manifest", str(org))
        assert r.returncode == 0, r.stderr
        assert not (out / "skills" / "runbook.zip").exists()
        assert (out / "skills" / "notes.zip").exists()
        assert "already delivered by claude.ai" in (out / "index.md").read_text(encoding="utf-8")
        with zipfile.ZipFile(out / "plugin" / "exported-skills.zip") as z:
            assert not any(n.startswith("skills/runbook/") for n in z.namelist())


def test_redaction_survives_case_subdomains_underscores_and_numeric_guids():
    with workdir() as tmp:
        src = tmp / "src"
        skill(src, "edge", "name: edge\ndescription: Edge cases",
              body=f"Mail Pat.Doe@EXAMPLE.COM or ops@mail.example.com.\n"
                   f"Role team_{ACCOUNT_A} and GUID {FAKE_GUID_DIGITS}.\n"
                   "Commit 0a1b2c3d4e5f60718293a4b5c6d7e8f9 stays.\n")
        r, out = run(tmp, src, "--redact-email-domain", "example.com")
        assert r.returncode == 0, r.stderr
        with zipfile.ZipFile(out / "skills" / "edge.zip") as z:
            text = z.read("edge/SKILL.md").decode("utf-8")
        for leaked in ("Pat.Doe", "ops@", ACCOUNT_A, "11111111", "123456789012"):
            assert leaked not in text, leaked
        assert "0a1b2c3d4e5f60718293a4b5c6d7e8f9" in text


def test_identifier_in_a_file_that_is_not_utf8_fails_closed():
    with workdir() as tmp:
        src = tmp / "src"
        d = skill(src, "legacy", "name: legacy\ndescription: Latin-1 file")
        (d / "notes.txt").write_bytes(f"Caf\u00e9 account {ACCOUNT_B}\n".encode("latin-1"))
        r, out = run(tmp, src, "--allow-binary")  # without it the file alone refuses the skill
        assert r.returncode == 1
        assert any("aws-account-id left in legacy/notes.txt" in e for e in manifest(out)[0]["errors"])
        assert not (out / "skills" / "legacy.zip").exists()


def test_a_secret_in_a_file_that_is_not_utf8_skips_the_skill():
    key = "AKIAABCDEFGHIJKLMNOP"
    with workdir() as tmp:
        src = tmp / "src"
        d = skill(src, "latin", "name: latin\ndescription: Latin-1 secret")
        (d / "notes.txt").write_bytes(f"Caf\u00e9 {key}\n".encode("latin-1"))
        d = skill(src, "wide", "name: wide\ndescription: UTF-16 secret")
        (d / "notes.txt").write_bytes(f"key {key}\n".encode("utf-16"))
        d = skill(src, "wide-nobom", "name: wide-nobom\ndescription: UTF-16 without a BOM")
        (d / "notes.txt").write_bytes(f"key {key}\n".encode("utf-16-le"))
        r, out = run(tmp, src)
        assert r.returncode == 1
        for row in manifest(out):
            assert any("secret: aws-access-key in notes.txt" in e for e in row["errors"]), row
            assert not (out / "skills" / f"{row['name']}.zip").exists()


def test_a_symlink_out_of_the_skill_is_refused_by_name():
    if os.name == "nt":
        print("skipped: symlinks need privileges on Windows")
        return
    with workdir() as tmp:
        outside = tmp / "outside.txt"
        outside.write_text("private\n", encoding="utf-8")
        src = tmp / "src"
        d = skill(src, "linky", "name: linky\ndescription: Links out")
        os.symlink(outside, d / "ref.txt")
        r, out = run(tmp, src)
        assert r.returncode == 1
        assert any("symlink ref.txt" in e for e in manifest(out)[0]["errors"]), manifest(out)
        assert not (out / "skills" / "linky.zip").exists()


def test_a_file_that_is_not_utf8_refuses_the_skill_unless_allowed():
    with workdir() as tmp:
        src = tmp / "src"
        d = skill(src, "pics", "name: pics\ndescription: Has an image")
        (d / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\xff\xfe")
        r, out = run(tmp, src)
        assert r.returncode == 1
        assert any("logo.png is not UTF-8 text" in e for e in manifest(out)[0]["errors"]), manifest(out)
        r, out = run(tmp, src, "--allow-binary")
        assert r.returncode == 0, r.stderr
        row = manifest(out)[0]
        assert any("logo.png is not UTF-8 text" in w for w in row["warnings"]), row
        with zipfile.ZipFile(out / "skills" / "pics.zip") as z:
            assert z.read("pics/logo.png").startswith(b"\x89PNG")


def test_utf16_without_a_bom_is_searched_for_identifiers():
    with workdir() as tmp:
        src = tmp / "src"
        d = skill(src, "wide", "name: wide\ndescription: UTF-16 identifier")
        (d / "n.txt").write_bytes(f"account {ACCOUNT_A}\n".encode("utf-16-le"))
        r, out = run(tmp, src, "--allow-binary")
        assert r.returncode == 1
        assert any("aws-account-id left in wide/n.txt" in e for e in manifest(out)[0]["errors"]), manifest(out)


def test_angle_brackets_leave_the_description():
    # claude.ai refuses '<' and '>' in a description ("XML tags"); Claude Code accepts them.
    with workdir() as tmp:
        src = tmp / "src"
        skill(src, "handoff", "name: handoff\ndescription: Writes to <repo>/handoffs/ before a /clear -> pickup")
        r, out = run(tmp, src)
        assert r.returncode == 0, r.stderr
        with zipfile.ZipFile(out / "skills" / "handoff.zip") as z:
            fm = frontmatter_of(z, "handoff")
        assert fm["description"] == "Writes to [repo]/handoffs/ before a /clear - pickup", fm
        assert any("angle brackets" in w for w in manifest(out)[0]["warnings"])


def test_a_skill_that_calls_developer_tools_is_warned_not_skipped():
    with workdir() as tmp:
        src = tmp / "src"
        skill(src, "ship", "name: ship\ndescription: Ship it", body="Run `git push` then `terraform plan`.\n")
        r, out = run(tmp, src)
        assert r.returncode == 0, r.stderr
        row = manifest(out)[0]
        assert row["errors"] == [] and any("developer tools" in w for w in row["warnings"]), row
        assert (out / "skills" / "ship.zip").exists()


def test_only_exports_the_named_skills():
    with workdir() as tmp:
        src = tmp / "src"
        skill(src, "one", "name: one\ndescription: One")
        skill(src, "two", "name: two\ndescription: Two")
        r, out = run(tmp, src, "--only", "two")
        assert r.returncode == 0, r.stderr
        assert [m["name"] for m in manifest(out)] == ["two"]


def test_the_frontmatter_reader_takes_flat_pairs_quotes_and_continuations():
    mod = load_script()
    fm = mod.parse_frontmatter(
        "name: demo\n"
        "description: Use on \"x\": a, b\n"
        "  and a continued line\n"
        "quoted: 'it''s'\n"
        "json: \"tab\\tand \\\"q\\\"\"\n"
        "version: 1.2\n"
        "metadata:\n"
        "  owner: team-a\n"
        "  tier: 2\n"
        "\n"
        "# a comment\n"
        "empty:\n")
    assert fm == {"name": "demo", "description": "Use on \"x\": a, b and a continued line",
                  "quoted": "it's", "json": "tab\tand \"q\"", "version": "1.2",
                  "metadata": {"owner": "team-a", "tier": "2"}, "empty": ""}, fm


def test_a_skill_without_frontmatter_is_skipped():
    with workdir() as tmp:
        src = tmp / "src"
        d = src / "bare"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("# no frontmatter\n", encoding="utf-8")
        r, out = run(tmp, src)
        assert r.returncode == 1
        assert any("no frontmatter" in e for e in manifest(out)[0]["errors"])


def test_a_git_ref_source_ships_the_commit_not_the_local_edit():
    if shutil.which("git") is None:
        print("skipped: git is not installed")
        return
    with workdir() as tmp:
        repo = tmp / "repo"
        skill(repo / "skills", "released", "name: released\ndescription: Released text")
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
        for cmd in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "x"], ["tag", "v1"]):
            subprocess.run(["git", "-C", str(repo), *cmd], check=True, capture_output=True, env=env)
        (repo / "skills" / "released" / "SKILL.md").write_text(
            "---\nname: released\ndescription: Local edit\n---\n", encoding="utf-8")
        empty = tmp / "personal"
        empty.mkdir()
        r, out = run(tmp, empty, "--git-repo", str(repo), "--git-ref", "v1", "--git-subdir", "skills")
        assert r.returncode == 0, r.stderr
        with zipfile.ZipFile(out / "skills" / "released.zip") as z:
            assert "Released text" in z.read("released/SKILL.md").decode("utf-8")
        assert manifest(out)[0]["source"].startswith("git:"), manifest(out)[0]


def test_a_git_ref_or_subdir_that_looks_like_an_option_is_refused():
    if shutil.which("git") is None:
        print("skipped: git is not installed")
        return
    with workdir() as tmp:
        repo = tmp / "repo"
        skill(repo / "skills", "released", "name: released\ndescription: Released text")
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
        for cmd in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "x"], ["tag", "v1"]):
            subprocess.run(["git", "-C", str(repo), *cmd], check=True, capture_output=True, env=env)
        empty = tmp / "personal"
        empty.mkdir()
        stray = tmp / "stray.tar"
        r, _ = run(tmp, empty, "--git-repo", str(repo), "--git-ref", "v1", f"--git-subdir=--output={stray}")
        assert r.returncode != 0 and not stray.exists(), (r.returncode, stray.exists())
        assert "Traceback" not in r.stderr, r.stderr
        r, _ = run(tmp, empty, "--git-repo", str(repo), "--git-ref=--git-dir", "--git-subdir", "skills")
        assert r.returncode != 0 and "Traceback" not in r.stderr, r.stderr


def test_a_skill_skipped_on_a_rerun_leaves_no_old_zip_behind():
    with workdir() as tmp:
        src = tmp / "src"
        skill(src, "later-bad", "name: later-bad\ndescription: Fine at first")
        r, out = run(tmp, src)
        assert r.returncode == 0 and (out / "skills" / "later-bad.zip").exists(), r.stderr
        key = "AKIA" + "ABCDEFGHIJKLMNOP"
        (src / "later-bad" / "notes.md").write_text(f"key {key}\n", encoding="utf-8")
        r, out = run(tmp, src)
        assert r.returncode == 1, r.stderr
        assert not (out / "skills" / "later-bad.zip").exists()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
