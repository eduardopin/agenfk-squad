---
name: skill-export
description: Export personal Claude Code skills, and skills committed in a git repository, as zips that claude.ai (Team/Enterprise) and ChatGPT (Business/Enterprise) admins upload once for the whole workspace, so people without GitHub or Claude Code (admin, HR, finance) get them in Claude Desktop, claude.ai and ChatGPT. Redacts account ids, GUIDs and the e-mail domains and strings you list. Use on "export my skills", "install the skills for HR", "skills in ChatGPT", "give the rfc skill to people without GitHub".
---

# skill-export

On claude.ai and ChatGPT a skill belongs to the account, not to the computer.
Claude Desktop reads the same list as claude.ai, so nothing is installed on a
laptop: an owner uploads the zips once and every member gets them.

## Export

```bash
python3 ~/.claude/skills/skill-export/scripts/export_skills.py --out ~/skills-export \
  --redact-email-domain example.com --redact-literal "Acme Widgets"
```

Python 3.10 or later, standard library only. `--help` lists every option.

Sources: `~/.claude/skills` (symlinks resolved; `--personal DIR` replaces it and
repeats), and with `--git-repo REPO` the skills committed in that repository at
`--git-ref` (default `origin/main`) under `--git-subdir` (default `skills`),
read with `git archive`, so a local edit never ships. Fetch first if the ref
is a remote branch. A name found in two sources keeps the first and `index.md`
says so. Skipped by default: `agenfk.*` (they need the AgEnFK server), `synced`
and every skill claude.ai already syncs to this account, read from
`~/.claude/skills/synced/*/manifest.json`, so nothing reaches a member twice.
`--only a b c` exports a short list.

Redaction runs over every text file in the skill:

- Always: 12-digit AWS account ids by `<AWS_ACCOUNT_ID>` (a public vendor
  account such as Datadog's Lambda layer stays, the ARN needs it, and so does a
  12-digit run inside a hash), GUIDs by `<ID>`, SSO instance ids.
- Only when named: `--redact-email-domain DOMAIN` (repeatable) turns addresses
  at that domain and its subdomains into `<EMAIL>`; `--redact-literal TEXT`
  (repeatable, any case) turns a company, project or person name into
  `<REDACTED>`. The script cannot guess which strings identify you: read
  `index.md` for what was redacted and add what it missed.
- A file that is not UTF-8 skips the redactor, so it is searched as latin-1 and
  UTF-16 afterwards. An identifier still found there skips the skill.

The output:

| Path | Upload where |
|---|---|
| `plugin/<plugin-name>.zip` | claude.ai: Organization settings > Plugins > Add plugins > Upload a file. All skills in one upload; on Enterprise, assign it to a group. Same name overwrites, so a new export is the update. `--plugin-name` sets the name (default `exported-skills`) |
| `skills/<name>.zip` | claude.ai Team: Organization settings > Skills > Add, one per skill. ChatGPT Enterprise/Edu: admin Skills page > Add skill, then publish to the workspace or a group |
| `index.md`, `manifest.json` | Source, sha256, redactions and warnings per skill. Read before uploading |
| `INSTALL.md` | How to import, per kind of account: organisation, standalone, ChatGPT |

## What the script changes and refuses

- Frontmatter keys other than `name`, `description`, `license`,
  `allowed-tools`, `compatibility` and `metadata` move under `metadata`, and the
  frontmatter is rewritten as strict YAML with every string quoted. Claude Code
  accepts an unquoted `description: a: b`; the uploaders do not.
- The frontmatter is read by a small stdlib reader, not PyYAML. It handles flat
  `key: value` pairs, quoted values, indented continuation lines and one level
  of `metadata:` pairs. A list, a block scalar (`>`, `|`) or an anchor is kept
  as the text written, not parsed. Skills written to the Agent Skills format
  use flat pairs, so this is enough; a skill that needs more should be
  simplified first.
- `<` and `>` leave the description (`<repo>` becomes `[repo]`): claude.ai
  refuses them as XML tags.
- A skill is skipped, and the run exits 1, on: no frontmatter; a name outside
  `[a-z0-9-]`, over 64 characters or containing `claude`/`anthropic`; a
  description empty or over 1024 characters; an AWS key, private key, GitHub,
  Slack or API token in any file. Those five patterns are the whole secret
  scan: a JWT, an Azure or GCP key, an AWS secret key or a `password=` line
  passes it, so read a skill before you export it. A skipped skill's zip from
  an earlier run in the same `--out` is deleted.
- `--git-ref` and `--git-subdir` may not start with `-`, so neither reaches
  git as an option.
- Zips carry fixed timestamps: an unchanged skill keeps its sha256 between
  exports, so `manifest.json` diffs show what changed.

Warnings do not skip. A skill that calls `gh`, `aws`, `terraform`, `git` or
`~/.claude` is flagged as unlikely to work in chat, where there is no terminal.
Decide per skill with `--only`.

## Before the first upload

- **claude.ai**: an Owner uploads. Organization settings > Plugins & skills >
  Policy must have "Code execution and file creation" and "Skills" on.
  Org-wide skills also sync into Claude Code for every engineer signed in with
  a Claude account (`syncClaudeAiSkills`). Prefer a group-scoped plugin on
  Enterprise; on Team, set `syncClaudeAiSkills: false` in managed settings if
  that sync is unwanted.
- **ChatGPT**: the help article names neither the zip layout nor a size limit.
  Upload one skill and check it passes the scan before uploading the rest.
  Everything uploaded there is stored by OpenAI.
- Neither product has a bulk upload API. The Claude plugin is the only
  one-upload path.
