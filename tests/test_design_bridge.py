#!/usr/bin/env python3
"""The design bridge translates motion without losing a value, and the image generator never bills an API.

Self-checking: the exit code is the verdict. Runs the motion-figma.mjs tests
(with a count floor: `node --test` exits 0 with zero tests), checks that the
skill and the agent cite files that exist, and runs gpt-image.sh against a fake
`codex` on a temporary PATH: with no sign-in, or signed in with an API key, it
must stop BEFORE calling codex.

    python3 tests/test_design_bridge.py

Needs node, bash, jq, file and sha256sum on PATH.
"""
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "design-bridge")
AGENT = os.path.join(ROOT, "agents", "design-bridge.md")
GPT = os.path.join(SKILL, "scripts", "gpt-image.sh")
TEST_FLOOR = 26

FAILURES = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"  -> {detail}"))
    if not cond:
        FAILURES.append(name)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def png_1x1():
    row = b"\x00\xff\x00\x00\xff"

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row)) + chunk(b"IEND", b""))


def sandbox(auth_mode):
    """A PATH with a fake codex and a CODEX_HOME with (or without) auth.json."""
    base = tempfile.mkdtemp(prefix="design-bridge-")
    bin_ = os.path.join(base, "bin")
    home = os.path.join(base, "codex-home")
    os.makedirs(bin_)
    os.makedirs(home)
    png = os.path.join(base, "source.png")
    with open(png, "wb") as f:
        f.write(png_1x1())
    marker = os.path.join(base, "codex-called")
    with open(os.path.join(bin_, "codex"), "w") as f:
        f.write(f"""#!/usr/bin/env bash
[ "$1" = "--version" ] && {{ echo "codex-cli 0.0.0-test"; exit 0; }}
touch '{marker}'
while [ $# -gt 0 ]; do [ "$1" = "-C" ] && dir="$2"; shift; done
cp '{png}' "$dir/stub-1.png"
echo "$dir/stub-1.png | 1 x 1"
""")
    os.chmod(os.path.join(bin_, "codex"), 0o755)
    if auth_mode is not None:
        with open(os.path.join(home, "auth.json"), "w") as f:
            json.dump({"auth_mode": auth_mode, "OPENAI_API_KEY": None}, f)
    with open(os.path.join(home, "config.toml"), "w") as f:
        f.write('model = "test-model"\n')
    env = dict(os.environ, PATH=bin_ + os.pathsep + os.environ["PATH"], CODEX_HOME=home,
               DESIGN_BRIDGE_INBOX=os.path.join(base, "inbox"))
    return env, marker


def gpt(env, *args):
    p = subprocess.run(["bash", GPT, *args], capture_output=True, text=True, env=env, cwd=ROOT)
    return p.returncode, p.stdout.strip(), p.stderr


# 1. motion-figma.mjs: node tests, with a floor.
p = subprocess.run(["node", "--test", "motion-figma.test.mjs"], capture_output=True, text=True,
                   cwd=os.path.join(SKILL, "scripts"))
m = re.search(r"^# pass (\d+)", p.stdout, re.M)
passed = int(m.group(1)) if m else 0
check("node --test of motion-figma exits 0", p.returncode == 0, p.stdout[-800:])
check(f"at least {TEST_FLOOR} tests passed", passed >= TEST_FLOOR, f"passed {passed}")

# 1b. Called through a link (as in ~/.claude/skills), the script still runs.
link_dir = tempfile.mkdtemp(prefix="design-bridge-link-")
os.symlink(SKILL, os.path.join(link_dir, "design-bridge"))
anims = os.path.join(link_dir, "a.json")
with open(anims, "w") as f:
    json.dump({"animations": [{"name": "x", "durationMs": 100, "frames": [
        {"offset": 0, "opacity": 0}, {"offset": 1, "opacity": 1}]}]}, f)
p = subprocess.run(["node", os.path.join(link_dir, "design-bridge", "scripts", "motion-figma.mjs"),
                    "export", anims], capture_output=True, text=True)
check("through the link, export prints the layers",
      p.returncode == 0 and '"layer": "x"' in p.stdout,
      f"rc={p.returncode} out={p.stdout[:200]!r} err={p.stderr[:200]!r}")

# 2. The skill and the agent cite files that exist.
skill = read(os.path.join(SKILL, "SKILL.md"))
check("skill frontmatter has the right name", skill.startswith("---\nname: design-bridge\n"))
desc = re.search(r"^description: (.+)$", skill, re.M)
check("skill description is at most 700 characters", desc is not None and len(desc.group(1)) <= 700,
      len(desc.group(1)) if desc else "missing")
check("skill description with ': ' is quoted",
      desc is not None and (": " not in desc.group(1) or desc.group(1).startswith(('"', "'"))))
check("SKILL.md stays under 160 lines", len(skill.splitlines()) < 160, len(skill.splitlines()))
for ref in sorted(set(re.findall(r"(references/[\w-]+\.md|scripts/[\w.-]+)", skill))):
    check(f"the skill cites {ref}, which exists", os.path.exists(os.path.join(SKILL, ref)))
for name in sorted(os.listdir(os.path.join(SKILL, "references"))):
    check(f"references/{name} is cited by the skill", f"references/{name}" in skill)
agent = read(AGENT)
check("agent frontmatter has the right name", re.search(r"^name: design-bridge$", agent, re.M) is not None)
check("the agent loads the skill", "skill `design-bridge`" in agent)
check("the agent points to design-reference for the motion rule", "`design-reference`" in agent)

# 3. Nothing private ships: no home paths, account-like ids, keys or secrets.
forbidden = re.compile(r"/home/[a-z]+/|(?<!\d)\d{12}(?!\d)|-----BEGIN .*PRIVATE KEY-----"
                       r"|\bsk-[A-Za-z0-9_-]{16,}|OPENAI_API_KEY\s*=", re.I)
shipped = [AGENT] + [os.path.join(d, f) for d, _, fs in os.walk(SKILL) for f in fs]
for path in sorted(shipped):
    rel = os.path.relpath(path, ROOT)
    check(f"{rel} has no private path, account id or key", forbidden.search(read(path)) is None)
    check(f"{rel} is not a symlink", not os.path.islink(path))

# 4. gpt-image.sh: never calls codex without a ChatGPT-plan sign-in.
env, marker = sandbox(None)
code, _, err = gpt(env, "stub", "x")
check("no sign-in: exits 3", code == 3, err)
check("no sign-in: codex was not called", not os.path.exists(marker))
check("no sign-in: asks the owner to sign in", "codex login" in err, err)

env, marker = sandbox("apikey")
code, _, err = gpt(env, "stub", "x")
check("API-key sign-in: exits 3", code == 3, err)
check("API-key sign-in: codex was not called", not os.path.exists(marker))
check("API-key sign-in: says it would be new spend", "new spend" in err, err)

env, marker = sandbox("chatgpt")
code, _, err = gpt(env, "Bad Subject", "x")
check("subject not in kebab-case: exits 2", code == 2, err)
code, _, err = gpt(env, "stub", "x", "--variations", "5")
check("more than 4 variations: exits 2", code == 2, err)
code, folder, err = gpt(env, "stub", "x", "--dry-run")
check("dry run: exits 0 without calling codex", code == 0 and not os.path.exists(marker), err)

code, folder, err = gpt(env, "stub", "a request")
check("ChatGPT plan: exits 0", code == 0, err)
check("ChatGPT plan: codex was called", os.path.exists(marker))
origin = json.loads(read(os.path.join(folder, "origin.json"))) if code == 0 else {}
files = origin.get("files", [])
check("origin.json lists the image with sha256 and size",
      len(files) == 1 and len(files[0].get("sha256", "")) == 64 and files[0].get("size") == "1x1", files)
check("origin.json starts with triage pending and no destination",
      origin.get("triage", {}).get("status") == "pending" and origin.get("destination") is None, origin)
check("origin.json records the request, project and agent model",
      origin.get("request") == "a request" and origin.get("project") and origin.get("agent_model") == "test-model",
      origin)
check("the folder stays outside the repository", not os.path.realpath(folder).startswith(ROOT), folder)
check("the folder honours DESIGN_BRIDGE_INBOX",
      os.path.realpath(folder).startswith(os.path.realpath(env["DESIGN_BRIDGE_INBOX"])), folder)

print(f"\n{len(FAILURES)} failure(s)" if FAILURES else "\nall green")
sys.exit(1 if FAILURES else 0)
