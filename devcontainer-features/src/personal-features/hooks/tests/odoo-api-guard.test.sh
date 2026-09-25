#!/usr/bin/env bash
# odoo-api-guard.test.sh — table-driven check for the odoo-api-guard PreToolUse
# hook (#811, #813, #907, #832).
#
# Feeds the guard exactly the JSON envelope a `PreToolUse` Bash hook receives on
# stdin and asserts, per case, that it either stays silent (allow) or emits a
# `permissionDecision: "deny"` whose reason NAMES THE RULE that fired. The rule
# name is the assertion, not the prose: #813 was filed because a GitHub Contents
# API read was denied with a message about Odoo transports, and a denial that
# cannot say which rule it came from is the same defect.
#
# SHIPPED BESIDE THE GUARD, not kept in the repo's test folder, for the reason
# #811 is about: the guard is policy, and policy whose check lives somewhere the
# provisioned machine cannot reach is policy nobody can re-verify after a change.
# install.sh puts it next to the hook, so it runs standalone on any provisioned
# host with no devcontainer and no test harness:
#
#     /usr/local/share/personal-features/hooks/tests/odoo-api-guard.test.sh
#     ./odoo-api-guard.test.sh [path-to-odoo-api-guard.sh]
#
# The Feature test drives the same script against the installed guard. Exits 0
# when every case holds, 1 otherwise, printing one line per case.
set -u

GUARD="${1:-$(dirname "$0")/../odoo-api-guard.sh}"

if [ ! -x "$GUARD" ]; then
    echo "odoo-api-guard.test: no executable guard at $GUARD" >&2
    exit 1
fi
if ! command -v jq >/dev/null 2>&1; then
    echo "odoo-api-guard.test: jq is required to build the hook envelopes" >&2
    exit 1
fi

pass=0
fail=0

# run_case <expect> <rule-or-dash> <description> <command> [tool-name]
#   expect: "allow" (guard prints nothing) or "deny" (guard names <rule>)
run_case() {
    _expect="$1"; _rule="$2"; _desc="$3"; _cmd="$4"; _tool="${5:-Bash}"

    _payload="$(jq -nc --arg t "$_tool" --arg c "$_cmd" \
        '{hook_event_name:"PreToolUse", tool_name:$t, tool_input:{command:$c}}')"
    _out="$(printf '%s' "$_payload" | "$GUARD" 2>/dev/null)"
    _rc=$?

    if [ "$_rc" -ne 0 ]; then
        # A PreToolUse hook that exits non-zero for its own reasons blocks the
        # call for a reason the caller cannot act on; the guard must always exit 0.
        printf 'FAIL [%s] %s: guard exited %s\n' "$_expect" "$_desc" "$_rc"
        fail=$((fail + 1))
        return
    fi

    if [ "$_expect" = "allow" ]; then
        if [ -z "$_out" ]; then
            printf 'ok   allow  %s\n' "$_desc"
            pass=$((pass + 1))
        else
            printf 'FAIL allow  %s: guard denied it (%s)\n' "$_desc" "$_out"
            fail=$((fail + 1))
        fi
        return
    fi

    _decision="$(printf '%s' "$_out" | jq -r '.hookSpecificOutput.permissionDecision // ""' 2>/dev/null)"
    _reason="$(printf '%s' "$_out" | jq -r '.hookSpecificOutput.permissionDecisionReason // ""' 2>/dev/null)"
    if [ "$_decision" != "deny" ]; then
        printf 'FAIL deny   %s: expected a deny, got %s\n' "$_desc" "${_out:-<silence>}"
        fail=$((fail + 1))
        return
    fi
    case "$_reason" in
        *"'$_rule'"*)
            printf 'ok   deny   %s (rule %s)\n' "$_desc" "$_rule"
            pass=$((pass + 1))
            ;;
        *)
            printf 'FAIL deny   %s: denial does not name rule %s (%s)\n' \
                "$_desc" "$_rule" "$_reason"
            fail=$((fail + 1))
            ;;
    esac
}

# --- the narrowed decode rule (#813, #907) -----------------------------------
# A bare decode is a READ. The GitHub Contents API returns the file body base64
# encoded in `.content` and has no unencoded variant, so decoding it is the only
# way to read the response - 32 of the 53 decodes in the corpus were this one
# idiom, and all 32 were denied with a message about Odoo transports.
run_case allow - "GitHub Contents API read idiom" \
    "gh api repos/OCA/server-tools/contents/README.md --jq '.content' | base64 -d"
run_case allow - "decode redirected to a file" \
    "echo aGVsbG8K | base64 -d > /tmp/decoded.txt"
run_case allow - "AWS SSM user-data read (#813: 11 of 53)" \
    "aws ec2 describe-instance-attribute --attribute userData --query 'UserData.Value' --output text | base64 -d | md5sum"
run_case allow - "a decode inside a Python program" \
    "python3 -c \"import base64; print(base64.b64decode(payload).decode())\""
run_case allow - "a grep whose PATTERN merely contains the marker (#907)" \
    "grep -rn 'b64decode' /workspaces/devcontainer-features"

# The threat the rule was always described as covering: decoded bytes handed
# straight to something that executes them.
run_case deny decode-to-interpreter "decode piped into sh" \
    "echo cGF5bG9hZAo= | base64 -d | sh"
run_case deny decode-to-interpreter "decode piped into bash with a sudo prefix" \
    "cat payload.b64 | base64 --decode | sudo bash"
run_case deny decode-to-interpreter "python decode piped into exec" \
    "python3 -c \"print(b64decode(p))\" | exec bash"
run_case deny decode-to-interpreter "python decode piped into eval" \
    "python3 -c \"import base64;print(base64.b64decode(p))\" | eval"
run_case deny decode-to-interpreter "hex decode piped into a shell" \
    "cat payload.hex | xxd -r -p | bash"
run_case deny decode-to-interpreter "openssl decode piped into python" \
    "openssl enc -d -base64 -in payload.b64 | python3 -"

# --- the rules that were always doing real work (#907 keeps these) ------------
run_case deny odoo-rpc "XML-RPC client import" \
    "python3 -c \"import xmlrpc.client; xmlrpc.client.ServerProxy(url)\""
run_case deny odoo-rpc "JSON-RPC endpoint over curl" \
    "curl -s https://example.odoo.com/jsonrpc -d '{}'"
run_case deny odoo-rpc "a web-session authenticate call" \
    "curl -s https://example.odoo.com/web/session/authenticate"
run_case deny odoo-rpc "an RPC client library" \
    "pip install odoorpc"
run_case deny odoo-rpc "execute_kw inside a heredoc body" \
    "$(printf 'python3 <<EOF\nmodels.execute_kw(db, uid, pw, "res.partner", "search", [[]])\nEOF\n')"
run_case deny odoo-credentials "reading the service-account credentials" \
    "cat /usr/local/share/odoo-sdk-config/config.json"
run_case deny odoo-prod-host "ssh to an odoo.sh production build host" \
    "ssh 31385103@qoc-fulton-main-31385103.dev.odoo.com psql -c 'select 1'"

# --- everything else is none of the guard's business --------------------------
run_case allow - "an ordinary command" "ls -la /workspaces"
run_case allow - "a staging build host is not production" \
    "ssh 31385104@qoc-fulton-staging-31385104.dev.odoo.com odoo shell"
run_case allow - "a non-Bash tool is ignored" \
    "python3 -c \"import xmlrpc.client\"" Edit

printf '\nodoo-api-guard.test: %s passed, %s failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
