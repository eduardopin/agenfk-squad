# Installing the exported skills

A skill lives in the Claude or ChatGPT account, not on the computer. Nothing is
copied onto the laptop: once the account has the skill, every computer that
person signs in to has it.

Use one path per account. `plugin/<plugin-name>.zip` and the zips in `skills/`
carry the same skills; installing both shows each skill twice.

| The person signs in with | Path |
|---|---|
| A work account in a Claude Team or Enterprise organisation | A. An owner uploads the plugin once; nothing to do on the computer |
| A Claude account outside the organisation (Free, Pro, Max) | B. Upload each zip in Claude Desktop, on that account |
| ChatGPT | C |

Skills that claude.ai already delivers to the organisation (the ones in
`~/.claude/skills/synced`, and Anthropic's built-ins such as `docx` and `pdf`)
are not in this export, so path A adds nothing twice.

## A. Claude Team or Enterprise organisation, done once by an Owner

1. Organization settings > Plugins & skills > Policy: turn on "Code execution
   and file creation" and "Skills".
2. Organization settings > Plugins > Add plugins > Upload a file, and choose
   `plugin/<plugin-name>.zip`.
3. In the plugin's settings, assign it to the group that should have it and set
   it to "Installed by default" (group assignment is an Enterprise feature).
4. On each computer the person opens Claude Desktop and signs in with the work
   account. The skills are listed under Customize > Skills.

To update, export again and upload the plugin with the same name: it replaces
the old version for everyone.

## B. A person's own Claude account

Repeat on each account, not on each computer.

1. Copy the `skills` folder to the computer. Do not unzip the files.
2. Claude Desktop > Settings > Capabilities: turn on "Code execution and file
   creation" (called "Create and edit files" in newer versions). Skills do not
   run without it.
3. Customize > Skills > "+" > "Create skill" > "Upload a skill", and choose one
   zip. Repeat for each zip the person needs.
4. Turn each uploaded skill on in the list.

To update a skill, delete it from the list and upload the new zip.

If Customize > Plugins on that account offers to upload a file, uploading
`plugin/<plugin-name>.zip` there installs every skill in one step, in place of
step 3. Anthropic does not document that button for individual accounts, so
check before relying on it.

## C. ChatGPT

Skills in ChatGPT need a Business or Enterprise workspace with Skills turned
on by its admin.

- Workspace admin: the admin Skills page > "+" (Add skill), one zip at a time,
  then publish each skill to the workspace or to a group.
- Individual member, when the admin allowed uploads: Skills > Create > Upload
  from your computer.

OpenAI does not document the zip layout. Upload one skill first and check that
its scan does not mark it "Needs review" or "Blocked" before uploading the rest.

## What was removed

AWS account ids, GUIDs and SSO instance ids are replaced by placeholders such
as `<AWS_ACCOUNT_ID>`. E-mail addresses and literal strings are replaced only
when the export was run with `--redact-email-domain` or `--redact-literal`.
`index.md` lists what each skill lost and which skills call developer tools
(`gh`, `aws`, `terraform`, `git`); those need a terminal and do little in chat.
