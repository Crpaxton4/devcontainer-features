#!/usr/bin/env bash
# hooks.test.sh — the tester allowlist hook (bash-allowlist.sh over lib/bash-allowlist.js),
# driven by synthetic payloads on stdin.
#
# Offline: no network, no docker, no Odoo, no repos tree.
#
# Every case asserts the DECISION and, when it denies, a substring of the reason —
# because "exits 0 with a deny" would stay green if the hook started denying for
# the wrong reason, and the wrong reason is exactly the failure mode a blocklist
# has. The `existing-work.sh ... 2>/dev/null` case is the sharp one: it must be
# denied for not being on the allowlist, and specifically NOT for containing `>`.
#
# The commit hook is gone: the manifest bump runs once per PR in pr-open.sh, and
# pr-open.test.sh covers it. The unattended guard has its own suite.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
ALLOWLIST="$ROOT/hooks/bash-allowlist.sh"

pass=0; fail=0
ok()  { echo "PASS $*"; pass=$((pass + 1)); }
bad() { echo "FAIL $*"; fail=$((fail + 1)); }

# payload <agent_type> <command> <cwd>
# An empty <agent_type> means the main session: agent_id and agent_type are both
# absent, which is what the harness actually sends outside a subagent.
payload() {
  node -e '
const [type, cmd, cwd] = process.argv.slice(1);
const p = {
  session_id: "test-session", transcript_path: "/tmp/transcript.jsonl", cwd,
  permission_mode: "default", hook_event_name: "PreToolUse",
  tool_name: "Bash", tool_input: { command: cmd }, tool_use_id: "toolu_test",
};
if (type) { p.agent_id = "agent-under-test"; p.agent_type = type; }
process.stdout.write(JSON.stringify(p));
' "$1" "$2" "${3:-/tmp}"
}

OUT=""; RC=0
# run <hook> <agent_type> <command> <cwd>
run() {
  local hook="$1"
  OUT="$(payload "$2" "$3" "${4:-/tmp}" | bash "$hook" 2>/dev/null)"; RC=$?
}

# allowed <hook> <label> <agent_type> <command> <cwd>
# Allowed means exactly that: exit 0 and not one byte on stdout, so the call goes
# on to the normal permission flow untouched.
allowed() {
  local hook="$1" label="$2"; shift 2
  run "$hook" "$@"
  if [ "$RC" -ne 0 ]; then bad "$label: expected exit 0, got $RC"; return; fi
  if [ -n "$OUT" ]; then bad "$label: expected silence, got: $OUT"; return; fi
  ok "$label -> allowed"
}

# denied <hook> <label> <want-substring> <agent_type> <command> <cwd>
denied() {
  local hook="$1" label="$2" want="$3"; shift 3
  run "$hook" "$@"
  if [ "$RC" -ne 0 ]; then bad "$label: expected exit 0, got $RC"; return; fi
  if ! printf '%s' "$OUT" | grep -q '"permissionDecision":"deny"'; then
    bad "$label: expected a deny, got: ${OUT:-<silence>}"; return
  fi
  if ! printf '%s' "$OUT" | grep -qF "$want"; then
    bad "$label: reason did not mention \"$want\": $OUT"; return
  fi
  ok "$label -> denied ($want)"
}

# --- the read-only tester ---------------------------------------------------------

TESTER=odoo-dev-tester

allowed "$ALLOWLIST" "tester: artifact.sh get build" \
  "$TESTER" "artifact.sh get /tmp/artifacts build"
allowed "$ALLOWLIST" "tester: artifact.sh get plan --raw by plugin-root path" \
  "$TESTER" 'bash "${CLAUDE_PLUGIN_ROOT}/scripts/artifact.sh" get /tmp/a plan --raw'

# The review section is the tester's only write. It has no Edit/Write and no
# redirection, so the inline --json form is the one that has to pass: there is no
# file it could have written first.
allowed "$ALLOWLIST" "tester: artifact.sh set review --json inline" \
  "$TESTER" 'artifact.sh set /tmp/artifacts review --json {"findings":[],"criteria_results":[{"criterion":"1","verdict":"pass","evidence":"test_a"}]}'
# An ampersand inside the payload is a deny, and the reason is the ampersand rather
# than anything about the section.
denied "$ALLOWLIST" "tester: an ampersand inside the inline payload" "contains &" \
  "$TESTER" 'artifact.sh set /tmp/artifacts review --json {"findings":[{"file":"a.py","line":1,"severity":"high","what":"2 failures & 1 error"}]}'
allowed "$ALLOWLIST" "tester: browser-ensure.sh" "$TESTER" "browser-ensure.sh"
allowed "$ALLOWLIST" "tester: module-classify.sh" "$TESTER" "module-classify.sh /tmp/wt"
# The tester runs the suite through session-db.sh (#1078), so two sessions on one
# task cannot drop each other's database. Only that exact name passes.
allowed "$ALLOWLIST" "tester: session-db.sh run" "$TESTER" \
  "$ROOT/skills/odoo-session-db/scripts/session-db.sh run repo 1 mod --session-id abc --put /x"
denied "$ALLOWLIST" "tester: a script named like session-db.sh" "not on the odoo-dev-tester allowlist" \
  "$TESTER" "$ROOT/skills/odoo-session-db/scripts/session-db.sh.bak run repo 1 mod"
# The allowlist admits the script, so the tester must carry the skill that tells it
# where the script is. Without the skill the tester runs run-tests.sh by name.
if grep -qE '^  - odoo-session-db$' "$ROOT/agents/odoo-dev-tester.md"; then
  ok "tester: agents/odoo-dev-tester.md lists the odoo-session-db skill"
else
  bad "tester: agents/odoo-dev-tester.md does not list the odoo-session-db skill"
fi

# 2>/dev/null must not be what decides anything. run-tests.sh keeps it and passes;
# existing-work.sh keeps it and is refused for the one true reason — it is not a
# script this agent may run. A blocklist on `>` gets both of these wrong.
allowed "$ALLOWLIST" "tester: run-tests.sh with 2>/dev/null" \
  "$TESTER" "run-tests.sh --db throwaway --modules sale 2>/dev/null"
allowed "$ALLOWLIST" "tester: run-tests.sh with >/dev/null 2>&1" \
  "$TESTER" "run-tests.sh --db throwaway >/dev/null 2>&1"

run "$ALLOWLIST" "$TESTER" "existing-work.sh 30412 --json 2>/dev/null"
if [ "$RC" -eq 0 ] \
   && printf '%s' "$OUT" | grep -q '"permissionDecision":"deny"' \
   && printf '%s' "$OUT" | grep -qF "not on the odoo-dev-tester allowlist" \
   && ! printf '%s' "$OUT" | grep -qF "redirects"; then
  ok "tester: existing-work.sh ... 2>/dev/null -> denied for the allowlist, not for >"
else
  bad "tester: existing-work.sh ... 2>/dev/null: rc=$RC out=$OUT"
fi

denied "$ALLOWLIST" "tester: bash -c with a real redirect" "redirects somewhere other than /dev/null" \
  "$TESTER" "bash -c 'echo x > /tmp/f'"
# The case a write-pattern blocklist misses entirely: nothing here looks like a write.
denied "$ALLOWLIST" "tester: an interpreter one-liner that writes a file" "not on the odoo-dev-tester allowlist" \
  "$TESTER" 'perl -e "unlink q(/tmp/f)"'
denied "$ALLOWLIST" "tester: chaining behind an allowlisted first token" "contains ;" \
  "$TESTER" "artifact.sh set x; rm -rf /tmp/y"
denied "$ALLOWLIST" "tester: && chaining" "contains &" \
  "$TESTER" "artifact.sh set x && rm -rf /tmp/y"
denied "$ALLOWLIST" "tester: command substitution" "contains \$(" \
  "$TESTER" 'artifact.sh set $(rm -rf /tmp/y)'
denied "$ALLOWLIST" "tester: pipe into a writer" "contains |" \
  "$TESTER" "artifact.sh list /tmp/a | tee /tmp/f"
denied "$ALLOWLIST" "tester: a second line" "spans more than one line" \
  "$TESTER" "$(printf 'artifact.sh list /tmp/a\nrm -rf /tmp/y')"
denied "$ALLOWLIST" "tester: tee" "not on the odoo-dev-tester allowlist" \
  "$TESTER" "tee /tmp/f"
denied "$ALLOWLIST" "tester: a bare \$VAR the hook cannot resolve" "not on the odoo-dev-tester allowlist" \
  "$TESTER" '$ARTIFACT set /tmp/a review --json {}'

allowed "$ALLOWLIST" "tester: git status"     "$TESTER" "git status"
allowed "$ALLOWLIST" "tester: git -C diff"    "$TESTER" "git -C /tmp/wt diff --stat"
allowed "$ALLOWLIST" "tester: git log"        "$TESTER" "git log --oneline -20"
allowed "$ALLOWLIST" "tester: git branch -a"  "$TESTER" "git branch -a"
allowed "$ALLOWLIST" "tester: git worktree list" "$TESTER" "git worktree list"
allowed "$ALLOWLIST" "tester: git remote -v"  "$TESTER" "git remote -v"
denied "$ALLOWLIST" "tester: git commit" "is not a read-only git subcommand" \
  "$TESTER" "git commit -m x"
denied "$ALLOWLIST" "tester: git push" "is not a read-only git subcommand" \
  "$TESTER" "git push"
denied "$ALLOWLIST" "tester: git branch -D" "not a read-only branch flag" \
  "$TESTER" "git branch -D old-branch"
denied "$ALLOWLIST" "tester: git branch <name> creates one" "creates or renames a branch" \
  "$TESTER" "git branch shiny-new-branch"
denied "$ALLOWLIST" "tester: git worktree add" "only \`git worktree list\`" \
  "$TESTER" "git worktree add /tmp/wt2 main"
denied "$ALLOWLIST" "tester: git diff --output" "git --output writes a file" \
  "$TESTER" "git diff --output=/tmp/patch"

# --- other agents are untouched --------------------------------------------------
# This is the regression that would make the plugin unusable. The hook ships to
# real sessions; if it policed anyone but the tester it would be a bug, not a
# feature.
allowed "$ALLOWLIST" "builder: bash -c with a redirect" \
  odoo-dev-builder "bash -c 'echo x > /tmp/f'"
allowed "$ALLOWLIST" "pr agent: gh pr create" odoo-dev-pr "gh pr create --draft"
allowed "$ALLOWLIST" "another plugin agent" some-other-agent "rm -rf /tmp/whatever"
allowed "$ALLOWLIST" "main session, no agent identity" "" "rm -rf /tmp/whatever"
allowed "$ALLOWLIST" "main session, no agent identity, writes a file" "" "echo x > /tmp/f"

# A non-Bash tool reaching this hook is not its business either.
OUT="$(node -e 'process.stdout.write(JSON.stringify({hook_event_name:"PreToolUse",agent_id:"a",agent_type:"odoo-dev-tester",tool_name:"Read",tool_input:{file_path:"/tmp/f"}}))' \
       | bash "$ALLOWLIST" 2>/dev/null)"; RC=$?
if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then ok "tester: a non-Bash tool -> allowed"
else bad "tester: non-Bash tool: rc=$RC out=$OUT"; fi

# --- fail closed, but only inside the scope --------------------------------------
OUT="$(printf 'this is not json at all' | bash "$ALLOWLIST" 2>/dev/null)"; RC=$?
if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then ok "unparseable payload naming nobody -> allowed"
else bad "unparseable payload naming nobody: rc=$RC out=$OUT"; fi

OUT="$(printf 'not json, but it mentions odoo-dev-tester' | bash "$ALLOWLIST" 2>/dev/null)"; RC=$?
if [ "$RC" -eq 0 ] && printf '%s' "$OUT" | grep -q '"permissionDecision":"deny"' \
   && printf '%s' "$OUT" | grep -qF "could not parse"; then
  ok "unparseable payload naming the tester -> denied"
else
  bad "unparseable payload naming the tester: rc=$RC out=$OUT"
fi

# --- the commit hook is gone -------------------------------------------------------
if [ ! -e "$ROOT/hooks/commit-hook.sh" ] && ! grep -q 'commit-hook' "$ROOT/hooks/hooks.json"; then
  ok "commit-hook.sh is deleted and not registered"
else
  bad "commit-hook.sh still exists or is registered in hooks.json"
fi

echo
echo "hooks.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
