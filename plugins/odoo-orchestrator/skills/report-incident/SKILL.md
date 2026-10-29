---
name: report-incident
description: "Draft a terse GitHub issue for an incident in the Odoo dev suite (odoo-dev, odoo-orchestrator, dispatcher), with a current environment block and no private data. Files it only after you approve. Use when a tool or workflow failed."
user-invocable: true
---

# Report an incident

Turn one incident into a GitHub issue in `Crpaxton4/devcontainer-features`.
The argument is an optional description. When it is present, use it as the first draft of the Summary.

## 1. Read the environment

Run each command on its own. If a command fails, write `unknown` for that field.

```
odoo-dev --version
claude --version
claude plugin list
```

`claude plugin list` shows the installed plugin names and versions. Use only odoo-dev and odoo-orchestrator.
The version of this plugin is also in `${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json`.

If the incident is in the orchestrator or the dispatcher, also run these. `ODOO_ORCHESTRATOR_CHECKOUT` is the checkout that holds `stack/compose.yaml`.

```
git -C "$ODOO_ORCHESTRATOR_CHECKOUT" rev-parse --short HEAD
grep -m1 '^version' "$ODOO_ORCHESTRATOR_CHECKOUT/libraries/dispatcher/pyproject.toml"
```

Build this block. Use `n/a` for `transport` when no Odoo call was involved.

```
<environment>
<suite_version>odoo-dev <version> (host), main at <checkout commit or n/a></suite_version>
<python_version><version> (host)</python_version>
<claude_code_version><version></claude_code_version>
<plugins>odoo-dev <version>, odoo-orchestrator <version></plugins>
<transport><xmlrpc, jsonrpc or n/a></transport>
</environment>
```

For an orchestrator incident, add the dispatcher version to `suite_version`.
Get the Python version from `python3 --version`.

## 2. Draft the issue

Write the body with these sections, in this order. Keep it terse, for a reader who is an AI.

1. **Summary**: 1-2 sentences.
2. **Symptoms**: bullets of the errors or behavior you saw.
3. **Environment**: the block from step 1, verbatim.
4. **Steps to reproduce**: if they are known.
5. **Context**: the relevant tool calls, responses or state from this session.

Write the title as `<component>: <problem stated as a fact>`.

## 3. CRITICAL: privacy rules

NEVER put any of these in the title or the body:

- The Odoo server URL.
- The database name.
- A username, password or API key.
- A customer name.
- Any other identifying detail.

Before you show the draft, read it again and remove or generalize each match.

## 4. Approve, then file

1. Show the full draft (title and body) to the user.
2. Wait. Do not file until the user approves. Apply the edits the user asks for and show it again.
3. Write the body to a file with the Write tool, for example `/tmp/report-incident-body.md`.
4. File the issue with the body file. Never pass the body inline: the shell truncates it at the first special character.

```
gh issue create -R Crpaxton4/devcontainer-features --title "<component>: <problem stated as a fact>" --body-file <path>
```

5. Give the user the issue URL that `gh` prints.
