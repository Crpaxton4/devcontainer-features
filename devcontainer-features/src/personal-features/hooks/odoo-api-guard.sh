#!/usr/bin/env bash
# odoo-api-guard.sh — PreToolUse guard for Bash.
#
# SHIPPED BY THIS FEATURE (#811). It used to be hand-maintained in the shared
# claude-home and owned by nobody, which is exactly the failure #811 records: the
# Odoo external-API prohibition is written down in this repo
# (plugins/odoo-dev/skills/odoo-devcontainer/references/external-api.md) while its
# only enforcement lived on one machine. install.sh now stages this file into
# /usr/local/share/personal-features/hooks and sync-claude-hooks publishes it into
# $CLAUDE_CONFIG_DIR/hooks, so both sides of the ~/.claude bind mount can run it
# (#803) and the #805 audit resolves it like any other hook command.
#
# WHAT IT DOES: denies a Bash command whose FULL text — heredoc bodies included —
# carries one of the markers in the RULES table below. It complements
# permissions.deny, which matches command prefixes and tokens and (as of Claude
# Code 2.1.268) does not look inside heredoc bodies or composed pipelines.
#
# THE RULES, and why each one exists:
#
#   odoo-prod-host        odoo.sh PRODUCTION build hosts (<name>-main-<id>.dev
#                         .odoo.com and the -prod/-production variants). Those are
#                         read-only-by-human: on 2026-08-18/19 three ssh+psql
#                         commands, one of them a COPY dumping production rows to a
#                         local CSV, reached qoc-fulton-main-31385103 and all
#                         succeeded — one of them a minute after the user said
#                         "You do not have access to prod".
#   odoo-rpc              direct XML-RPC / JSON-RPC / web-session transports and the
#                         third-party Odoo RPC client libraries. Odoo access goes
#                         through the odoo-mcp MCP server only.
#   odoo-credentials      the service-account credential directory odoo-mcp reads.
#   decode-to-interpreter an encoded payload decoded STRAIGHT INTO an interpreter.
#
# THE DECODE RULE IS NARROW ON PURPOSE (#813, #907). It used to match any decode
# anywhere in the command text. That blocked the GitHub Contents API read idiom —
# `gh api repos/O/R/contents/PATH --jq '.content' | base64 -d`, which has no
# unencoded variant and accounted for 32 of the 53 decodes in the corpus — plus
# AWS SSM file transfers, binary attachment dumps, and a read-only `grep` whose
# *search pattern* merely contained the marker string (#907: investigating the
# guard tripped the guard). It now fires only when the decoded bytes are piped, in
# the same pipeline, into a shell or interpreter — which is the behaviour the rule
# was always described as covering. A bare decode (to stdout, to a file, inside a
# Python program) passes.
#
# KNOWN LIMIT: THE FILE-THEN-RUN BYPASS (#832). This guard reasons about the text
# of one Bash command. Writing that command into a file with a file-writing tool
# (which this hook never sees) and then invoking the file presents different text,
# and every rule above is stepped around. Observed, not theorised: /tmp/push-777.sh
# and /tmp/w3-try/cred.sh were created that way during an /implement-issues run and
# both ran. Closing it would mean either reading the target of every `bash <path>`
# invocation (which closes only the direct form) or forbidding write-then-invoke
# entirely (which breaks ordinary scripting), so it is accepted and documented
# rather than patched. Treat this guard as a tripwire against the accidental and
# the routine, not as a containment boundary against a determined caller.
#
# DENIAL MESSAGES NAME THE RULE, NOT THE MATCHED LITERAL (#907). Echoing the
# literal made the guard's own denials un-quotable: the first attempt to file #832
# was itself denied because the issue body described the decoder flags. The rule id
# is the diagnostic; the table above is where it is looked up.
#
# Always exits 0. A denial is expressed through hookSpecificOutput, never through a
# non-zero exit status — a PreToolUse hook that exits non-zero for an unrelated
# reason would read to the harness as a block on every call.
set -u

IN=$(cat 2>/dev/null || true)
[ -n "$IN" ] || exit 0
command -v python3 >/dev/null 2>&1 || exit 0

# The payload travels in the environment rather than on stdin: stdin here carries
# the Python program itself (a quoted heredoc, so nothing in it is expanded by the
# shell and the regexes below can use whichever quotes they need).
ODOO_API_GUARD_PAYLOAD="$IN" python3 - <<'PY' || true
import json
import os
import re

DECODE = (
    r"(?:base64\s+(?:-d|-D|--decode)"
    r"|b64decode|a2b_base64|frombase64"
    r"|xxd\s+-r"
    r"|openssl\s+(?:enc|base64)\b[^|;&\n]*-d\b)"
)
# The dangerous continuation: the decode's output piped, within the SAME pipeline
# (no `;`, `&`, `&&` or newline in between), into something that executes it.
SINK = r"(?:sh|bash|zsh|dash|ksh|python3?|node|nodejs|perl|ruby|php|eval|exec|source)"
PREFIX = r"(?:sudo\s+|command\s+|env\s+(?:\S+=\S+\s+)*|xargs\s+(?:-\S+\s+)*)*"
DECODE_TO_INTERPRETER = DECODE + r"[^|;&\n]*\|\s*" + PREFIX + SINK + r"\b"

RULES = (
    (
        "odoo-prod-host",
        "an odoo.sh PRODUCTION build host",
        r"-main-\d+\.dev\.odoo\.com"
        r"|-prod-\d+\.dev\.odoo\.com"
        r"|-production-\d+\.dev\.odoo\.com",
        "Production build hosts are read-only-by-human: run nothing there, reads "
        "included, without an explicit ask in the transcript.",
    ),
    (
        "odoo-rpc",
        "a direct Odoo external-API transport or RPC client library",
        r"/xmlrpc/|xmlrpc\.client|xmlrpclib|ServerProxy"
        r"|/jsonrpc|/json/2/|/json/1/|execute_kw|X-Odoo-Database"
        r"|service[\"']?\s*:\s*[\"']object"
        r"|/web/session/authenticate|/web/session/get_session_info"
        r"|/web/dataset/call_kw|/web/dataset/call_button"
        r"|/web/webclient/version_info|/web/database/"
        r"|odoorpc|erppeek|odooly|openerplib|odoolib|odoo_rpc_client"
        r"|aio_odoorpc|odoo_json2|odoo-xmlrpc|odoo-await",
        "Odoo access goes through the odoo-mcp MCP server only; an MCP failure is "
        "reported, never worked around by reaching for another transport.",
    ),
    (
        "odoo-credentials",
        "the service-account credential directory odoo-mcp reads",
        r"odoo-sdk-config",
        "Those credentials belong to odoo-mcp and to nothing else; do not read, "
        "copy or print them.",
    ),
    (
        "decode-to-interpreter",
        "an encoded payload decoded straight into an interpreter",
        DECODE_TO_INTERPRETER,
        "A bare decode is fine — `gh api ... | base64 -d`, `... | base64 -d > file`, "
        "a decode inside a Python program. Piping decoded bytes into a shell or an "
        "interpreter is not: it hides every other rule in this guard. Write the "
        "payload to a file and read it before running it.",
    ),
)

COMPILED = [
    (rule_id, label, re.compile(pattern, re.IGNORECASE), advice)
    for rule_id, label, pattern, advice in RULES
]


def main():
    try:
        payload = json.loads(os.environ.get("ODOO_API_GUARD_PAYLOAD", ""))
    except Exception:
        return
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return
    command = tool_input.get("command") or ""
    if not isinstance(command, str) or not command:
        return

    for rule_id, label, pattern, advice in COMPILED:
        if not pattern.search(command):
            continue
        reason = (
            "odoo-api-guard: denied by rule '%s' (%s in the command text, heredoc "
            "bodies included). %s See the rule table in the guard's header." % (
                rule_id, label, advice)
        )
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}))
        return


try:
    main()
except Exception:
    pass
PY
exit 0
