#!/usr/bin/env bash
# hooks.test.sh — the two PreToolUse hooks, driven by synthetic payloads on stdin.
#
# Offline: no network, no docker, no Odoo, no repos tree. The only thing built here
# is a handful of throwaway git repos and artifact dirs under mktemp.
#
# Every case asserts the DECISION and, when it denies, a substring of the reason —
# because "exits 0 with a deny" would stay green if the hook started denying for
# the wrong reason, and the wrong reason is exactly the failure mode a blocklist
# has. The `existing-work.sh ... 2>/dev/null` case is the sharp one: it must be
# denied for not being on the allowlist, and specifically NOT for containing `>`.
#
# Part (b) runs each resolution failure through both worktree-keyed PR shapes on
# purpose, because the two answer it differently: pr-open.sh is this plugin's own
# script and is gated whatever happens, while a bare `gh pr create` is gated only
# when the hook can attribute it to a task. release-pr.sh is keyed by a branch pair
# rather than by a worktree and is gated like pr-open.sh. The same worktree therefore denies under one
# shape and passes in silence under the other, and both halves are asserted.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
ALLOWLIST="$ROOT/hooks/bash-allowlist.sh"
GATEHOOK="$ROOT/hooks/gate-hook.sh"
FIX="$HERE/fixtures/gate"

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

# --- part (a): the read-only tester ---------------------------------------------

TESTER=odoo-dev-tester

allowed "$ALLOWLIST" "tester: artifact.sh put" \
  "$TESTER" "artifact.sh put /tmp/artifacts 30-test /tmp/run.json"
allowed "$ALLOWLIST" "tester: artifact.sh by plugin-root path" \
  "$TESTER" 'bash "${CLAUDE_PLUGIN_ROOT}/scripts/artifact.sh" put /tmp/a 30-test -'
allowed "$ALLOWLIST" "tester: browser-ensure.sh" "$TESTER" "browser-ensure.sh"
allowed "$ALLOWLIST" "tester: module-classify.sh" "$TESTER" "module-classify.sh /tmp/wt"

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
denied "$ALLOWLIST" "tester: python -c open(f,w)" "not on the odoo-dev-tester allowlist" \
  "$TESTER" 'python -c "open(\"/tmp/f\",\"w\").write(\"x\")"'
denied "$ALLOWLIST" "tester: chaining behind an allowlisted first token" "contains ;" \
  "$TESTER" "artifact.sh put x; rm -rf /tmp/y"
denied "$ALLOWLIST" "tester: && chaining" "contains &" \
  "$TESTER" "artifact.sh put x && rm -rf /tmp/y"
denied "$ALLOWLIST" "tester: command substitution" "contains \$(" \
  "$TESTER" 'artifact.sh put $(rm -rf /tmp/y)'
denied "$ALLOWLIST" "tester: pipe into a writer" "contains |" \
  "$TESTER" "artifact.sh list /tmp/a | tee /tmp/f"
denied "$ALLOWLIST" "tester: a second line" "spans more than one line" \
  "$TESTER" "$(printf 'artifact.sh list /tmp/a\nrm -rf /tmp/y')"
denied "$ALLOWLIST" "tester: tee" "not on the odoo-dev-tester allowlist" \
  "$TESTER" "tee /tmp/f"
denied "$ALLOWLIST" "tester: a bare \$VAR the hook cannot resolve" "not on the odoo-dev-tester allowlist" \
  "$TESTER" '$ARTIFACT put /tmp/a 30-test -'

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

# --- part (b): the gate at the tool boundary -------------------------------------

STATE="$(mktemp -d)"
mkdir -p "$STATE/tasks/30412" "$STATE/tasks/30999"
cp "$FIX/all-green"/*.json  "$STATE/tasks/30412/"
cp "$FIX/zero-tests"/*.json "$STATE/tasks/30999/"
export ODOO_DEV_STATE_DIR="$STATE"

WT="$(mktemp -d)"
# mkwt <branch> <name> — a worktree is only ever read here, never checked out over.
# Local hooks are disabled so a machine with a global commit-msg policy still runs
# this suite.
mkwt() {
  local branch="$1" d="$WT/$2"
  mkdir -p "$d"
  git init -q -b "$branch" "$d" 2>/dev/null \
    || { git init -q "$d"; git -C "$d" symbolic-ref HEAD "refs/heads/$branch"; }
  git -C "$d" -c core.hooksPath=/dev/null -c user.email=t@example.com -c user.name=t \
      commit -q --allow-empty --no-verify -m "chore: fixture"
  echo "$d"
}
GREEN="$(mkwt 30412-stockflow-sync green)"
RED="$(mkwt   30999-red-run       red)"
NOID="$(mkwt  no-task-id-here     noid)"
ORPHAN="$(mkwt 88888-nothing-here orphan)"
# A plain directory with no repository anywhere above it, and a path that does not
# exist at all. Neither can be read for a branch.
PLAIN="$WT/not-a-repo"; mkdir -p "$PLAIN"
GONE="$WT/no-such-worktree"

PR="bash /plugin/skills/odoo-pr/scripts/pr-open.sh"

# --- shape one: pr-open.sh, gated whatever happens -------------------------------
# Every one of these worktrees reappears below under `gh pr create`, where most of
# them are allowed. The difference is the point: an invocation of our own script is
# inside the workflow by construction, so failing to find the evidence is a broken
# workflow rather than somebody else's repository.

allowed "$GATEHOOK" "gate: pr-open.sh against a green artifacts dir" \
  odoo-dev-pr "$PR $GREEN acme/erp staging --title T --body-file /tmp/body.md"
denied "$GATEHOOK" "gate: pr-open.sh against a red artifacts dir" "no_tests" \
  odoo-dev-pr "$PR $RED acme/erp staging --title T --body-file /tmp/body.md"
denied "$GATEHOOK" "gate: pr-open.sh on a branch with no task id" "carries no leading task id" \
  odoo-dev-pr "$PR $NOID acme/erp staging --title T --body-file /tmp/body.md"
denied "$GATEHOOK" "gate: pr-open.sh with a task id that has no artifacts dir" "no directory at" \
  odoo-dev-pr "$PR $ORPHAN acme/erp staging --title T --body-file /tmp/body.md"
denied "$GATEHOOK" "gate: pr-open.sh against a directory that is not a git worktree" "not a git worktree" \
  odoo-dev-pr "$PR $PLAIN acme/erp staging --title T --body-file /tmp/body.md"
denied "$GATEHOOK" "gate: pr-open.sh against a path that does not exist" "is not a directory" \
  odoo-dev-pr "$PR $GONE acme/erp staging --title T --body-file /tmp/body.md"
denied "$GATEHOOK" "gate: pr-open.sh with no worktree argument" "without its first positional argument" \
  odoo-dev-pr "$PR --title T --body-file /tmp/body.md"
denied "$GATEHOOK" "gate: pr-open.sh named through a variable" "not as a resolvable token" \
  odoo-dev-pr '"$PR_OPEN"pr-open.sh /tmp/wt acme/erp staging'
denied "$GATEHOOK" "gate: two pr-open.sh in one command" "names pr-open.sh 2 times" \
  odoo-dev-pr "$PR $GREEN acme/erp staging --title T --body-file /tmp/b && $PR $RED acme/erp staging"

# --- shape two: release-pr.sh, gated on the release directory --------------------
# It carries no worktree, so the release dir comes from its <from> and <to>
# positionals as <state>/releases/<from>-to-<to>. It calls `gh pr create` inside
# itself, so the shape below never sees that call and this is the only stop.

mkdir -p "$STATE/releases/UAT-to-main" "$STATE/releases/staging-to-main" \
         "$STATE/releases/dev-to-UAT"
cp "$FIX/release-green"/*.json          "$STATE/releases/UAT-to-main/"
cp "$FIX/untagged-and-inferred"/*.json  "$STATE/releases/staging-to-main/"
cp "$FIX/unconfirmed-flow"/*.json       "$STATE/releases/dev-to-UAT/"

RELPR="bash /plugin/skills/odoo-release/scripts/release-pr.sh"
RELFLAGS="--manifest-file /tmp/m.json --assignee a --reviewer b"

allowed "$GATEHOOK" "gate: release-pr.sh against a clean release dir" \
  odoo-dev-pr "$RELPR acme/erp UAT main $RELFLAGS"

# The whole point of demoting untagged_pr: a release carrying two unattributable
# PRs and one inferred id is a warning, and a warning does not stop the tool.
allowed "$GATEHOOK" "gate: release-pr.sh against a release dir with untagged PRs" \
  odoo-dev-pr "$RELPR acme/erp staging main $RELFLAGS"

denied "$GATEHOOK" "gate: release-pr.sh against an unconfirmed flow" "unconfirmed_flow" \
  odoo-dev-pr "$RELPR acme/erp dev UAT $RELFLAGS"
denied "$GATEHOOK" "gate: release-pr.sh with no release dir for the branch pair" "no directory at" \
  odoo-dev-pr "$RELPR acme/erp feature main $RELFLAGS"
denied "$GATEHOOK" "gate: release-pr.sh without its positional arguments" "positional arguments" \
  odoo-dev-pr "$RELPR $RELFLAGS"
denied "$GATEHOOK" "gate: release-pr.sh with a branch pair that is not two branch names" "plain branch names" \
  odoo-dev-pr "$RELPR acme/erp ../../etc main $RELFLAGS"
denied "$GATEHOOK" "gate: release-pr.sh named through a variable" "not as a resolvable token" \
  odoo-dev-pr '"$REL"release-pr.sh acme/erp UAT main'
denied "$GATEHOOK" "gate: two release-pr.sh in one command" "names release-pr.sh 2 times" \
  odoo-dev-pr "$RELPR acme/erp UAT main $RELFLAGS && $RELPR acme/erp dev UAT $RELFLAGS"

# A mention is not an invocation here either.
allowed "$GATEHOOK" "gate: grep for release-pr.sh" \
  odoo-dev-pr "grep -rn release-pr.sh skills/" "$RED"

# --- shape three: a bare gh pr create, gated only when it can be attributed ------
# gh pr create carries no worktree of its own, so the payload cwd is the worktree.
# When the branch names a task and its artifacts dir exists, the gate decides.
allowed "$GATEHOOK" "gate: gh pr create from a green worktree" \
  odoo-dev-pr "gh pr create --draft --title T --body-file /tmp/body.md" "$GREEN"
denied "$GATEHOOK" "gate: gh pr create from a red worktree" "no_tests" \
  odoo-dev-pr "gh pr create --draft --title T --body-file /tmp/body.md" "$RED"

# The narrowing. `gh pr create` is an ordinary command that any repository on the
# machine may run, so when the hook cannot tie it to a task it says nothing at all.
# Denying here would police work that has nothing to do with this plugin, and the
# deliberate cost is that a task branch named without its id can slip past.
allowed "$GATEHOOK" "gate: gh pr create on a branch with no task id" \
  odoo-dev-pr "gh pr create --fill" "$NOID"
allowed "$GATEHOOK" "gate: gh pr create with a task id that has no artifacts dir" \
  odoo-dev-pr "gh pr create --fill" "$ORPHAN"
allowed "$GATEHOOK" "gate: gh pr create outside any git repository" \
  odoo-dev-pr "gh pr create --fill" "$PLAIN"
allowed "$GATEHOOK" "gate: gh pr create from a cwd that does not exist" \
  odoo-dev-pr "gh pr create --fill" "$GONE"
allowed "$GATEHOOK" "gate: gh pr create in the main session, unrelated repo" \
  "" "gh pr create --draft --title T" "$PLAIN"

# No cwd in the payload at all: the worktree cannot be determined, so there is
# nothing to attribute the PR to and the call passes.
OUT="$(node -e 'process.stdout.write(JSON.stringify({hook_event_name:"PreToolUse",tool_name:"Bash",tool_input:{command:"gh pr create --fill"}}))' \
       | bash "$GATEHOOK" 2>/dev/null)"; RC=$?
if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then ok "gate: gh pr create with no cwd in the payload -> allowed"
else bad "gate: gh pr create with no cwd in the payload: rc=$RC out=$OUT"; fi

# The gate hook is not the tester hook: it engages on the PR shapes and nothing else.
allowed "$GATEHOOK" "gate: an unrelated command" odoo-dev-pr "echo hello" "$RED"
allowed "$GATEHOOK" "gate: gh pr list is a read" odoo-dev-pr "gh pr list --state open" "$RED"
allowed "$GATEHOOK" "gate: main session, unrelated command" "" "ls -la" "$RED"

# --- a mention is not an invocation ----------------------------------------------
# The names this hook gates are ordinary words. They turn up in commit messages, in
# greps, in documentation and in this very test file, and none of those is a PR
# being opened. Every case below runs with the RED worktree as its cwd, so a hook
# that matched the name anywhere in the text would deny on no_tests and the test
# would catch it. This is the regression that made the plugin deny work on its own
# repository: a `git commit` whose message named the script was refused because the
# word after the name in the prose was read as a worktree path.

HEREDOC_COMMIT="$(printf '%s\n' \
  "git commit -q --no-verify -F - <<'EOF'" \
  "docs: write down what the gate hook matches" \
  "" \
  "pr-open.sh is this plugin's own script, so every invocation of it is gated," \
  "and a bare gh pr create is gated only when it can be attributed to a task." \
  "EOF")"
allowed "$GATEHOOK" "gate: a commit message that names both shapes in a heredoc" \
  odoo-dev-pr "$HEREDOC_COMMIT" "$RED"
allowed "$GATEHOOK" "gate: grep for the script name" \
  odoo-dev-pr "grep -rn pr-open.sh hooks/" "$RED"
allowed "$GATEHOOK" "gate: cat the hook itself" \
  odoo-dev-pr "cat hooks/gate-hook.sh" "$RED"
allowed "$GATEHOOK" "gate: the name inside a quoted argument" \
  odoo-dev-pr 'echo "run pr-open.sh next"' "$RED"
allowed "$GATEHOOK" "gate: the name in a -m commit message" \
  odoo-dev-pr 'git commit -m "docs: explain pr-open.sh"' "$RED"
allowed "$GATEHOOK" "gate: the name in a sed expression" \
  odoo-dev-pr "sed -i s/pr-open.sh/x/ file.md" "$RED"

# The other half of the same rule: command position still counts, wrapped or bare,
# first segment or last. Each of these asserts the decision the resolve logic
# reaches, so none of them can pass by the hook failing to match at all.
denied "$GATEHOOK" "gate: pr-open.sh invoked bare, no wrapper" "no_tests" \
  odoo-dev-pr "pr-open.sh $RED acme/repo staging --title t --body-file b --draft"
denied "$GATEHOOK" "gate: pr-open.sh invoked through bash" "is not a directory" \
  odoo-dev-pr "bash /path/to/pr-open.sh $GONE acme/repo staging --title t"
denied "$GATEHOOK" "gate: gh pr create --fill" "no_tests" \
  odoo-dev-pr "gh pr create --fill" "$RED"
denied "$GATEHOOK" "gate: gh pr create in a later segment" "no_tests" \
  odoo-dev-pr "cd $RED && gh pr create --fill" "$RED"

# More than one candidate dir for one task id is a resolution failure, not a coin
# toss — and it is the one failure that denies under both shapes. The task id
# resolved, so the PR is inside this workflow whichever command opened it, and one
# task with its evidence filed in two places is an inconsistency worth naming.
mkdir -p "$STATE/tasks/30412-stockflow" "$STATE/tasks/30412-other"
rm -rf "$STATE/tasks/30412"
denied "$GATEHOOK" "gate: two candidate artifact dirs for one task id" "matches 2 directories" \
  odoo-dev-pr "$PR $GREEN acme/erp staging --title T --body-file /tmp/body.md"
denied "$GATEHOOK" "gate: two candidate artifact dirs, reached by gh pr create" "matches 2 directories" \
  odoo-dev-pr "gh pr create --fill" "$GREEN"

rm -rf "$STATE" "$WT"

echo
echo "hooks.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
