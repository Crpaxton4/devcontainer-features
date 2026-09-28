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
# Part (b) is the commit hook, which has exactly one shape since the PR and release
# gates were removed in #904. It is judged on a repository rather than on an
# artifacts dir, so it builds its own fixture repo with two modules and moves the
# index or the work tree before each case. Its allowed cases matter as much as its
# denied ones: this shape fires on a command every repository on the machine runs,
# so each thing the hook declines to decide is asserted by name.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
ALLOWLIST="$ROOT/hooks/bash-allowlist.sh"
COMMITHOOK="$ROOT/hooks/commit-hook.sh"

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

# --- part (b): the commit hook -----------------------------------------------------
# One shape, `git commit`, judged on the repository rather than on any artifact.
# The PR and release gates that used to live here went out with gate.sh in #904, so
# there is nothing left to build an artifacts dir for.

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
# A git repository with no Odoo module anywhere in it, a plain directory with no
# repository above it, and a path that does not exist at all.
NOMOD="$(mkwt 30999-no-modules nomod)"
PLAIN="$WT/not-a-repo"; mkdir -p "$PLAIN"
GONE="$WT/no-such-worktree"

# The commit hook is not the tester hook, and it is no longer the PR hook either:
# it engages on `git commit` and says nothing about anything else. The three calls
# it used to hold against gate.sh are asserted here by name, so that removing the
# gate is a property of the suite rather than an absence in it.
allowed "$COMMITHOOK" "commit: an unrelated command" odoo-dev-pr "echo hello" "$NOMOD"
allowed "$COMMITHOOK" "commit: gh pr create is no longer gated" \
  odoo-dev-pr "gh pr create --draft --title T --body-file /tmp/body.md" "$NOMOD"
allowed "$COMMITHOOK" "commit: pr-open.sh is no longer gated" \
  odoo-dev-pr "bash /plugin/skills/odoo-pr/scripts/pr-open.sh $NOMOD acme/erp staging --title T"
allowed "$COMMITHOOK" "commit: release-pr.sh is no longer gated" \
  odoo-dev-pr "bash /plugin/skills/odoo-release/scripts/release-pr.sh acme/erp UAT main"
allowed "$COMMITHOOK" "commit: main session, unrelated command" "" "ls -la" "$NOMOD"

# No cwd in the payload at all: there is no repository to read, so the call passes.
OUT="$(node -e 'process.stdout.write(JSON.stringify({hook_event_name:"PreToolUse",tool_name:"Bash",tool_input:{command:"git commit -m x"}}))' \
       | bash "$COMMITHOOK" 2>/dev/null)"; RC=$?
if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then ok "commit: git commit with no cwd in the payload -> allowed"
else bad "commit: git commit with no cwd in the payload: rc=$RC out=$OUT"; fi


# --- the manifest version bump ---------------------------------------------------
# The hook engages on the change set a commit would carry, so every case below moves
# the index or the work tree first and then asserts what the hook says about the
# commit that would follow. The evidence for this shape is the repository, and
# nothing else: no artifact, no state dir, no task id.

MOD="$WT/modrepo"
mkdir -p "$MOD/stockflow_sync/models" "$MOD/addons/nested_mod"
git init -q -b main "$MOD" 2>/dev/null \
  || { git init -q "$MOD"; git -C "$MOD" symbolic-ref HEAD refs/heads/main; }
# gitq — the fixture's own git, never the hook's. Local hooks are disabled so a
# machine with a global commit-msg policy still runs this suite.
gitq() { git -C "$MOD" -c core.hooksPath=/dev/null -c user.email=t@example.com -c user.name=t "$@" >/dev/null 2>&1; }
# manifest <path> <version> — the smallest thing module-classify.sh's version
# expression, and therefore the hook's, will read.
manifest() { printf "{\n    'name': 'fixture',\n    'version': '%s',\n}\n" "$2" > "$1"; }

manifest "$MOD/stockflow_sync/__manifest__.py" 17.0.1.0.0
manifest "$MOD/addons/nested_mod/__manifest__.py" 17.0.2.0.0
printf '# base\n' > "$MOD/stockflow_sync/models/sale.py"
printf '# base\n' > "$MOD/addons/nested_mod/api.py"
printf '# readme\n' > "$MOD/README.md"
gitq add -A
gitq commit -q --no-verify -m "chore: fixture modules"

# (1) A module changed, the manifest standing still. The reason must name the
# module and the version it is stuck on, because "denied" alone would stay green
# if the hook started denying every commit.
printf '# changed\n' >> "$MOD/stockflow_sync/models/sale.py"
gitq add stockflow_sync/models/sale.py
denied "$COMMITHOOK" "commit: git commit touching a module with no manifest bump" \
  "without moving the manifest version" \
  odoo-dev-builder 'git commit -m "feat: sync stock"' "$MOD"
denied "$COMMITHOOK" "commit: the deny names the module and its stuck version" \
  "stockflow_sync (still 17.0.1.0.0)" \
  odoo-dev-builder 'git commit -m "feat: sync stock"' "$MOD"

# The repository is the payload cwd unless `-C` moves it, and the same commit
# denies from an unrelated cwd when it does.
denied "$COMMITHOOK" "commit: git -C into the module repo from elsewhere" \
  "without moving the manifest version" \
  odoo-dev-builder "git -C $MOD commit -m \"feat: sync stock\"" "$PLAIN"
# --git-dir moves the repository somewhere the hook does not follow.
allowed "$COMMITHOOK" "commit: git --git-dir is not followed" \
  odoo-dev-builder "git --git-dir=$MOD/.git commit -m x" "$PLAIN"
# An explicit pathspec narrows the commit to a subset the hook does not
# reconstruct. This is the documented bypass, asserted so nobody removes it by
# accident and nobody adds it back by accident either.
allowed "$COMMITHOOK" "commit: git commit with an explicit pathspec" \
  odoo-dev-builder 'git commit -m "feat: sync stock" stockflow_sync' "$MOD"
allowed "$COMMITHOOK" "commit: git commit with a pathspec after --" \
  odoo-dev-builder 'git commit -m "feat: sync stock" -- stockflow_sync' "$MOD"

# (2) The same change with the version moved. The hook asks whether the version
# differs from the base, not whether bump_manifest_version.py is what moved it —
# a hand edit is a bump.
manifest "$MOD/stockflow_sync/__manifest__.py" 17.0.1.1.0
gitq add stockflow_sync/__manifest__.py
allowed "$COMMITHOOK" "commit: git commit with the manifest version moved" \
  odoo-dev-builder 'git commit -m "feat: sync stock"' "$MOD"
gitq commit -q --no-verify -m "feat: sync stock"

# (3) A file outside every module is not a module change.
printf '# more\n' >> "$MOD/README.md"
gitq add README.md
allowed "$COMMITHOOK" "commit: git commit touching no module" \
  odoo-dev-builder 'git commit -m "docs: readme"' "$MOD"
gitq commit -q --no-verify -m "docs: readme"

# (4) A nested module is found by walking up to the nearest __manifest__.py, so a
# repo that keeps its addons under addons/ is gated like a flat one.
printf '# changed\n' >> "$MOD/addons/nested_mod/api.py"
gitq add addons/nested_mod/api.py
denied "$COMMITHOOK" "commit: a nested module is found by walking up" \
  "addons/nested_mod (still 17.0.2.0.0)" \
  odoo-dev-builder 'git commit -m "fix: api"' "$MOD"
gitq reset -q --hard HEAD

# (5) The first commit of a new module has no earlier version to move away from.
mkdir -p "$MOD/brand_new"
manifest "$MOD/brand_new/__manifest__.py" 17.0.1.0.0
printf '# new\n' > "$MOD/brand_new/models.py"
gitq add brand_new
allowed "$COMMITHOOK" "commit: the first commit of a new module" \
  odoo-dev-builder 'git commit -m "feat: brand_new"' "$MOD"
gitq commit -q --no-verify -m "feat: brand_new"

# (6) Deleting a module removes code and bumps nothing.
gitq rm -r -q brand_new
allowed "$COMMITHOOK" "commit: removing a module" \
  odoo-dev-builder 'git commit -m "chore: drop brand_new"' "$MOD"
gitq commit -q --no-verify -m "chore: drop brand_new"

# (7) -a widens the commit to tracked work-tree changes, and the two forms must
# disagree on the same state: nothing is staged, so a plain commit carries no
# module and passes, while -a carries the module and is denied.
printf '# unstaged\n' >> "$MOD/stockflow_sync/models/sale.py"
allowed "$COMMITHOOK" "commit: an unstaged module change, committed without -a" \
  odoo-dev-builder 'git commit -m "feat: more sync"' "$MOD"
denied "$COMMITHOOK" "commit: an unstaged module change, committed with -am" \
  "stockflow_sync (still 17.0.1.1.0)" \
  odoo-dev-builder 'git commit -am "feat: more sync"' "$MOD"
# `-ma` is `-m a`, not `-m` plus `-a`: the letters after a value-taking one are
# its value. Read as `-a` it would pick up the work-tree change above and deny,
# so this is the same state as the two cases above and the parse is what decides.
allowed "$COMMITHOOK" "commit: -ma is -m a and carries no -a" \
  odoo-dev-builder 'git commit -ma' "$MOD"
gitq checkout -- stockflow_sync/models/sale.py

# (8) --amend rewrites the tip, so the comparison base is the tip's parent. The
# bump in the commit being amended still counts, and reverting it is caught.
printf '# amended\n' >> "$MOD/stockflow_sync/models/sale.py"
manifest "$MOD/stockflow_sync/__manifest__.py" 17.0.1.2.0
gitq add -A
gitq commit -q --no-verify -m "feat: amendable"
printf '# amended twice\n' >> "$MOD/stockflow_sync/models/sale.py"
gitq add stockflow_sync/models/sale.py
allowed "$COMMITHOOK" "commit: --amend over a commit that already bumped" \
  odoo-dev-builder 'git commit --amend --no-edit' "$MOD"
manifest "$MOD/stockflow_sync/__manifest__.py" 17.0.1.1.0
gitq add stockflow_sync/__manifest__.py
denied "$COMMITHOOK" "commit: --amend that puts the version back" \
  "without moving the manifest version" \
  odoo-dev-builder 'git commit --amend --no-edit' "$MOD"
gitq reset -q --hard HEAD

# (9) A merge in progress is not somebody editing a module.
printf '# merged\n' >> "$MOD/stockflow_sync/models/sale.py"
gitq add stockflow_sync/models/sale.py
: > "$MOD/.git/MERGE_HEAD"
allowed "$COMMITHOOK" "commit: a commit with a merge in progress" \
  odoo-dev-builder 'git commit --no-edit' "$MOD"
rm -f "$MOD/.git/MERGE_HEAD"
denied "$COMMITHOOK" "commit: the same commit once the merge marker is gone" \
  "without moving the manifest version" \
  odoo-dev-builder 'git commit --no-edit' "$MOD"
gitq reset -q --hard HEAD

# (10) Everything the hook cannot decide passes in silence, because `git commit`
# is an ordinary command in every repository on the machine.
allowed "$COMMITHOOK" "commit: git commit in a repo with no modules" \
  odoo-dev-builder 'git commit -m "chore: whatever"' "$NOMOD"
allowed "$COMMITHOOK" "commit: git commit outside any git repository" \
  odoo-dev-builder 'git commit -m "chore: whatever"' "$PLAIN"
allowed "$COMMITHOOK" "commit: git commit with no cwd to resolve" \
  odoo-dev-builder 'git commit -m "chore: whatever"' "$GONE"
allowed "$COMMITHOOK" "commit: a read-only git subcommand" \
  odoo-dev-builder 'git status --short' "$MOD"
allowed "$COMMITHOOK" "commit: the word commit as an argument, not a subcommand" \
  odoo-dev-builder 'git log --format=%s -1 commit' "$MOD"
# A mention is not an invocation here either: the name of the rule inside a
# message is prose, and the heredoc body is data.
printf '# mentioned\n' >> "$MOD/stockflow_sync/models/sale.py"
gitq add stockflow_sync/models/sale.py
allowed "$COMMITHOOK" "commit: git commit named inside a heredoc body" \
  odoo-dev-builder "$(printf '%s\n' "cat <<'EOF'" "git commit -m x" "EOF")" "$MOD"
allowed "$COMMITHOOK" "commit: grep for the word" \
  odoo-dev-builder 'grep -rn "git commit" docs/' "$MOD"
gitq reset -q --hard HEAD

rm -rf "$WT"

echo
echo "hooks.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
