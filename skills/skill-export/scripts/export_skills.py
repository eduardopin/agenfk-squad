#!/usr/bin/env python3
"""Packages Claude Code skills for upload to claude.ai (Team/Enterprise) and
ChatGPT (Business/Enterprise) workspaces, where a skill belongs to the account,
not to the machine: one admin upload reaches every member's Desktop app.

Writes into --out:
  skills/<name>.zip            one skill per zip, <name>/SKILL.md at the root
  plugin/<plugin-name>.zip     every skill in one Claude plugin (one upload,
                               assignable to a group on Enterprise)
  manifest.json, index.md      source, sha256, redactions and warnings per skill
  INSTALL.md                   how to import, per kind of account

Account ids, GUIDs and SSO instance ids are replaced by placeholders in every
text file. E-mail domains and literal strings that identify your organisation
are not known to the script: name them with --redact-email-domain and
--redact-literal. A skill the claude.ai organisation already delivers is not
exported again.

Frontmatter limit: SKILL.md is read with a small stdlib reader, not PyYAML. It
understands flat `key: value` pairs, quoted values, continuation lines indented
under a key, and one level of nested `key: value` pairs (for `metadata:`). Block
scalars (`>`, `|`), lists and anchors are not YAML here: such a value is kept as
the text it was written as.

Exit 1 when a skill was skipped (invalid name or description, or a secret found).
"""
import argparse
import glob
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from datetime import date
from pathlib import Path

sys.dont_write_bytecode = True

HOME = Path.home()
# Keys the Agent Skills spec and the claude.ai uploader accept; anything else
# (version, argument-hint, ...) moves into metadata so the upload is not refused.
ALLOWED_KEYS = {"name", "description", "license", "allowed-tools", "compatibility", "metadata"}
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
JUNK_DIRS = {".git", "__pycache__", "node_modules", ".pytest_cache", ".venv", ".mypy_cache"}
JUNK_FILES = {".DS_Store"}
PLUGIN_LIMIT = 50 * 1024 * 1024
SKILL_WARN = 8 * 1024 * 1024
SECRETS = {
    "aws-access-key": re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "github-token": re.compile(r"\b(ghp|gho|ghs|ghu)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{40,}"),
    "slack-token": re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}"),
    "api-key": re.compile(r"\bsk-(ant-)?[A-Za-z0-9_-]{32,}"),
}
# Placeholders for data that identifies accounts. The exported skill still
# reads as an instruction; the reader fills in their own.
# Order matters: a GUID's last group can be twelve digits, so GUIDs go first.
REDACT = {
    "guid": (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<ID>"),
    "sso-instance": (re.compile(r"\bssoins-[0-9a-f]{16}\b", re.I), "<SSO_INSTANCE_ID>"),
    # Lookarounds, not \b: "role_123456789012" has no word boundary before the digits.
    "aws-account-id": (re.compile(r"(?<!\d)\d{12}(?!\d)"), "<AWS_ACCOUNT_ID>"),
}
HEX_RUN = re.compile(r"[0-9a-fA-F]{16,}")
# Vendor accounts that publish public artifacts; the ARN does not work without them.
PUBLIC_ACCOUNTS = {"464622532012"}  # Datadog Lambda layers
# Words that tell an office user the skill will not run outside a developer laptop.
LOCAL_TOOLS = re.compile(r"\b(gh|aws|terraform|tofu|kubectl|agenfk|git)\s+[a-z-]+|~/\.claude|~/dev/")
INSTALL = Path(__file__).resolve().parent.parent / "INSTALL.md"
FM_KEY = re.compile(r"^([A-Za-z0-9_-]+):(?:\s+(.*)|\s*)$")


def unquote(value):
    """A quoted scalar without the quotes; a bare one as written."""
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        try:
            return json.loads(v)
        except ValueError:
            return v[1:-1]
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1].replace("''", "'")
    return v


def parse_frontmatter(head):
    """Flat `key: value` pairs, with indented lines continuing the value above
    them and, when a key has no value, indented `key: value` pairs nested under
    it. Claude Code reads an unquoted `description: a: b` as one string, so does
    this. No lists, anchors or block scalars (see the module docstring)."""
    fm, key = {}, None
    for line in head.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] not in " \t":
            m = FM_KEY.match(line)
            if not m:
                continue
            key = m.group(1)
            fm[key] = unquote(m.group(2) or "")
            continue
        if key is None:
            continue
        text = line.strip()
        sub = FM_KEY.match(text)
        if isinstance(fm[key], dict):
            if sub:
                fm[key][sub.group(1)] = unquote(sub.group(2) or "")
            else:
                last = list(fm[key])[-1]
                fm[key][last] = f"{fm[key][last]} {text}".strip()
        elif fm[key] == "" and sub:
            fm[key] = {sub.group(1): unquote(sub.group(2) or "")}
        else:
            fm[key] = f"{fm[key]} {text}".strip()
    return fm


def split_frontmatter(text):
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", text, re.S)
    if not m:
        return None, text
    return parse_frontmatter(m.group(1)), m.group(2)


def chat_safe(desc):
    """The claude.ai uploader refuses '<' and '>' in a description, reading them
    as XML tags; Claude Code accepts them. A placeholder <repo> leaves as [repo]."""
    desc = re.sub(r"<([^<>\n]*)>", r"[\1]", desc)
    return desc.replace("<", "").replace(">", "")


def normalize(fm):
    out = {k: fm[k] for k in fm if k in ALLOWED_KEYS}
    meta_in = fm.get("metadata")
    meta = {str(k): str(v) for k, v in meta_in.items()} if isinstance(meta_in, dict) else {}
    for k, v in fm.items():
        if k not in ALLOWED_KEYS:
            meta[str(k)] = v if isinstance(v, str) else json.dumps(v)
    out.pop("metadata", None)
    if meta:
        out["metadata"] = meta
    if isinstance(out.get("description"), str):
        out["description"] = chat_safe(out["description"])
    return out


def dump_frontmatter(fm):
    """Strict YAML: every string is a JSON double-quoted scalar, which YAML reads
    back unchanged, so a colon or a bracket in a value cannot break the upload."""
    lines = []
    for k, v in fm.items():
        if isinstance(v, dict):
            lines.append(f"{k}:")
            lines += [f"  {sk}: {json.dumps(sv, ensure_ascii=False)}" for sk, sv in v.items()]
        else:
            lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
    return "\n".join(lines) + "\n"


def validate(name, fm):
    errors = []
    if fm is None:
        return ["SKILL.md has no frontmatter"]
    if fm.get("name") != name:
        errors.append(f"frontmatter name {fm.get('name')!r} differs from directory {name!r}")
    if not NAME_RE.match(name) or len(name) > 64:
        errors.append("name must be lowercase letters, digits and hyphens, 64 characters at most")
    if "anthropic" in name or "claude" in name:
        errors.append("name may not contain 'anthropic' or 'claude'")
    desc = fm.get("description")
    if not isinstance(desc, str) or not desc.strip():
        errors.append("description is empty")
    elif len(desc) > 1024:
        errors.append(f"description is {len(desc)} characters, the limit is 1024")
    return errors


def files_of(skill_dir):
    for p in sorted(skill_dir.rglob("*")):
        rel = p.relative_to(skill_dir)
        if any(part in JUNK_DIRS for part in rel.parts) or p.name in JUNK_FILES or p.suffix == ".pyc":
            continue
        if p.is_file() and not p.is_symlink():
            yield rel, p


def unsafe_entries(skill_dir):
    """Symlinks and anything resolving outside the skill: a link to ~/.ssh would
    be zipped as the file it points at. Refused by name, like safe_extract."""
    root, bad = skill_dir.resolve(), []
    for p in sorted(skill_dir.rglob("*")):
        rel = p.relative_to(skill_dir)
        if any(part in JUNK_DIRS for part in rel.parts):
            continue
        if p.is_symlink():
            bad.append(f"symlink {rel.as_posix()} is not exported")
        elif p.resolve() != root and root not in p.resolve().parents:
            bad.append(f"{rel.as_posix()} resolves outside the skill")
    return bad


def decodings(data):
    """Every reading a secret or an identifier could hide in: UTF-8 when valid,
    else latin-1, plus UTF-16 with a BOM or when NUL bytes make up a good part
    of the file (UTF-16 text without a BOM)."""
    try:
        texts = [data.decode("utf-8")]
    except UnicodeDecodeError:
        texts = [data.decode("latin-1")]
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        texts.append(data.decode("utf-16", errors="replace"))
    elif data and data.count(b"\0") * 8 >= len(data):
        texts += [data.decode(enc, errors="replace") for enc in ("utf-16-le", "utf-16-be")]
    return texts


def is_text(data):
    """UTF-8 without NUL bytes: the only kind the redactor can read and clean."""
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return b"\0" not in data


def scan(skill_dir):
    """Returns (errors, warnings, undecodable): errors skip the skill, undecodable
    names the files that are not UTF-8 text, which the redactor cannot clean."""
    errors, warnings, local, undecodable = unsafe_entries(skill_dir), set(), False, []
    for rel, p in files_of(skill_dir):
        try:
            data = p.read_bytes()
        except OSError as e:
            errors.append(f"cannot read {rel.as_posix()}: {e.strerror or e}")
            continue
        if not is_text(data):
            undecodable.append(rel.as_posix())
        for text in decodings(data):
            for kind, rx in SECRETS.items():
                if rx.search(text):
                    errors.append(f"secret: {kind} in {rel.as_posix()}")
            if LOCAL_TOOLS.search(text):
                local = True
    errors = list(dict.fromkeys(errors))
    if local:
        warnings.add("calls developer tools (gh, aws, terraform, git, ~/.claude): may not work in chat")
    return errors, sorted(warnings), undecodable


def build_rules(email_domains, literals):
    """REDACT plus what the caller named: e-mail addresses at their domains and
    literal strings (case-insensitive). The literals run first, so a name inside
    an address is gone before the address rule looks."""
    rules = {}
    if literals:
        rules["literal"] = (re.compile("|".join(re.escape(x) for x in literals), re.I), "<REDACTED>")
    if email_domains:
        domains = "|".join(re.escape(d.lower().lstrip("@")) for d in email_domains)
        rules["email"] = (re.compile(rf"\b[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)*(?:{domains})\b", re.I), "<EMAIL>")
    rules.update(REDACT)
    return rules


def in_hash(m):
    """Twelve digits inside a sha256 or a commit id are part of the hash."""
    line_start = m.string.rfind("\n", 0, m.start()) + 1
    line_end = m.string.find("\n", m.end())
    for run in HEX_RUN.finditer(m.string, line_start, len(m.string) if line_end < 0 else line_end):
        if run.start() <= m.start() and m.end() <= run.end():
            return True
    return False


def keep(kind, m):
    return kind == "aws-account-id" and (m.group(0) in PUBLIC_ACCOUNTS or in_hash(m))


def redact(text, counts, rules):
    for kind, (rx, placeholder) in rules.items():
        def sub(m):
            if keep(kind, m):
                return m.group(0)
            counts[kind] = counts.get(kind, 0) + 1
            return placeholder
        text = rx.sub(sub, text)
    return text


def clean_bytes(data, counts, rules):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    return redact(text, counts, rules).encode("utf-8")


def residue(data, rules):
    """Identifiers still in a file after redaction. A file that is not UTF-8
    skipped the redactor, so it is read as UTF-16 and latin-1 here: the export
    fails closed instead of shipping what it could not read."""
    texts = decodings(data)
    found = set()
    for text in texts:
        for kind, (rx, _) in rules.items():
            if any(not keep(kind, m) for m in rx.finditer(text)):
                found.add(kind)
    return sorted(found)


def org_delivered(pattern):
    """Names claude.ai already syncs to this account: the organisation's
    provisioned skills and Anthropic's built-ins. Exporting them would duplicate."""
    names = set()
    for m in glob.glob(os.path.join(str(HOME), pattern)):
        try:
            data = json.loads(Path(m).read_text(encoding="utf-8"))
            names |= {x["name"] for x in data.get("skills", [])}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return names


def write_zip(path, entries):
    """Fixed timestamps, so an unchanged skill keeps its sha256 between exports."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for arcname, data in entries:
            info = zipfile.ZipInfo(arcname, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)


def skill_entries(name, skill_dir, fm, body, counts, rules):
    head = dump_frontmatter(normalize(fm))
    yield f"{name}/SKILL.md", redact(f"---\n{head}---\n{body}", counts, rules).encode("utf-8")
    for rel, p in files_of(skill_dir):
        if rel.as_posix() != "SKILL.md":
            yield f"{name}/{rel.as_posix()}", clean_bytes(p.read_bytes(), counts, rules)


def safe_extract(tar, dest):
    """extractall(filter="data") exists only from Python 3.10.12; refuse links and
    paths that leave dest instead, on every 3.10."""
    root = Path(dest).resolve()
    for member in tar.getmembers():
        target = (root / member.name).resolve()
        if root != target and root not in target.parents:
            sys.exit(f"refusing archive member outside the target: {member.name}")
        if member.issym() or member.islnk() or member.isdev():
            sys.exit(f"refusing archive member that is a link or a device: {member.name}")
    tar.extractall(dest)


def git_source(repo, ref, subdir, tmp):
    """The committed content at a git ref, untouched by local edits."""
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", ref], capture_output=True, text=True, encoding="utf-8")
    sha = r.stdout.strip() if r.returncode == 0 else None
    if not sha:
        sys.exit(f"cannot resolve {ref} in {repo}; run git fetch there first")
    tar = subprocess.run(["git", "-C", str(repo), "archive", sha, subdir], capture_output=True, check=True).stdout
    dest = Path(tmp) / "git-source"
    dest.mkdir()
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        safe_extract(t, dest)
    return dest / subdir, f"git:{Path(repo).name}@{sha[:12]}"


def collect(sources, include, exclude):
    seen = {}
    for root, label in sources:
        if not Path(root).is_dir():
            continue
        for d in sorted(Path(root).iterdir()):
            name = d.name
            if not (d.resolve() / "SKILL.md").is_file() or name.startswith("."):
                continue
            if include and name not in include:
                continue
            if any(re.fullmatch(x, name) for x in exclude):
                continue
            if name in seen:
                seen[name]["shadowed"].append(label)
                continue
            seen[name] = {"name": name, "dir": d.resolve(), "source": label, "shadowed": []}
    return list(seen.values())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path(f"skills-export-{date.today().isoformat()}"))
    ap.add_argument("--personal", type=Path, action="append",
                    help="directory of skills, repeatable (default ~/.claude/skills; symlinks are resolved)")
    ap.add_argument("--git-repo", type=Path,
                    help="also export the skills committed in this repository, read with git archive")
    ap.add_argument("--git-ref", default="origin/main", help="ref to read from --git-repo (default origin/main)")
    ap.add_argument("--git-subdir", default="skills", help="skills directory inside --git-repo (default skills)")
    ap.add_argument("--only", nargs="*", default=[], help="export only these skill names")
    ap.add_argument("--exclude", nargs="*", default=["agenfk.*", "synced"],
                    help="regexes of names to skip (default: the AgEnFK tool's skills and claude.ai's own sync)")
    ap.add_argument("--redact-email-domain", action="append", default=[], metavar="DOMAIN",
                    help="replace e-mail addresses at this domain and its subdomains by <EMAIL>; repeatable")
    ap.add_argument("--redact-literal", action="append", default=[], metavar="TEXT",
                    help="replace this text, any case, by <REDACTED>; repeatable")
    ap.add_argument("--allow-binary", action="store_true",
                    help="ship files that are not UTF-8 text (images, fonts) unredacted, with a warning per file; "
                         "secrets and identifiers are still searched for in them")
    ap.add_argument("--plugin-name", default="exported-skills")
    ap.add_argument("--org-manifest", default=".claude/skills/synced/*/manifest.json",
                    help="claude.ai sync manifest(s), relative to $HOME; their skills are not exported")
    args = ap.parse_args(argv)
    rules = build_rules(args.redact_email_domain, args.redact_literal)

    with tempfile.TemporaryDirectory() as tmp:
        sources = [(p, f"personal:{p}") for p in (args.personal or [HOME / ".claude/skills"])]
        if args.git_repo:
            sources.append(git_source(args.git_repo, args.git_ref, args.git_subdir, tmp))
        skills = collect(sources, set(args.only), args.exclude)
        delivered = org_delivered(args.org_manifest)

        out = args.out
        manifest, plugin_entries, skipped = [], [], 0
        for s in skills:
            if s["name"] in delivered:
                manifest.append({"name": s["name"], "source": s["source"], "shadowed": s["shadowed"],
                                 "warnings": [], "errors": [], "status": "already delivered by claude.ai"})
                continue
            text = (s["dir"] / "SKILL.md").read_text(encoding="utf-8")
            fm, body = split_frontmatter(text)
            errors = validate(s["name"], fm)
            scan_errors, warnings, undecodable = scan(s["dir"])
            row = {"name": s["name"], "source": s["source"], "shadowed": s["shadowed"],
                   "warnings": warnings, "errors": errors + scan_errors}
            for rel in undecodable:
                if args.allow_binary:
                    row["warnings"].append(f"{rel} is not UTF-8 text: shipped unredacted (--allow-binary)")
                else:
                    row["errors"].append(f"{rel} is not UTF-8 text, so it cannot be redacted; "
                                         "remove it or pass --allow-binary")
            if fm and not errors and re.search(r"[<>]", fm["description"]):
                row["warnings"].append("angle brackets in the description, rewritten for the uploader")
            if row["errors"]:
                skipped += 1
                manifest.append(row)
                continue
            counts = {}
            entries = list(skill_entries(s["name"], s["dir"], fm, body, counts, rules))
            row["redacted"] = counts
            row["errors"] += [f"{k} left in {a} after redaction" for a, b in entries for k in residue(b, rules)]
            if row["errors"]:
                skipped += 1
                manifest.append(row)
                continue
            zpath = out / "skills" / f"{s['name']}.zip"
            write_zip(zpath, entries)
            data = zpath.read_bytes()
            if len(data) > SKILL_WARN:
                row["warnings"].append(f"zip is {len(data) // 1024} KiB; large uploads may be refused")
            row.update(zip=str(zpath.relative_to(out)), bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            plugin_entries += [(f"skills/{a}", b) for a, b in entries]
            manifest.append(row)

    exported = [r for r in manifest if "zip" in r]
    if exported:
        pj = {"name": args.plugin_name, "version": date.today().strftime("%Y.%m.%d"),
              "description": f"{len(exported)} skills for chat users, exported from Claude Code"}
        ppath = out / "plugin" / f"{args.plugin_name}.zip"
        write_zip(ppath, [(".claude-plugin/plugin.json", json.dumps(pj, indent=2).encode("utf-8"))] + plugin_entries)
        if ppath.stat().st_size > PLUGIN_LIMIT:
            print(f"ERROR {ppath} exceeds the 50 MB plugin limit; split it with --only", file=sys.stderr)
            skipped += 1

    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    lines = ["| Skill | Source | Status | Notes |", "|---|---|---|---|"]
    for r in manifest:
        status = r.get("status") or ("skipped" if r["errors"] else "exported")
        red = [f"redacted {n} {k}" for k, n in sorted((r.get("redacted") or {}).items())]
        notes = "; ".join(r["errors"] + red + r["warnings"] + [f"also in {x}" for x in r["shadowed"]])
        lines.append(f"| {r['name']} | {r['source']} | {status} | {notes} |")
    (out / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "INSTALL.md").write_text(INSTALL.read_text(encoding="utf-8"), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n{len(exported)} exported, {skipped} skipped -> {out.resolve()}")
    return 1 if skipped else 0


if __name__ == "__main__":
    sys.exit(main())
