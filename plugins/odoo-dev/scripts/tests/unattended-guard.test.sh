#!/usr/bin/env bash
# unattended-guard.test.sh - the implementer's PreToolUse guard, driven by synthetic
# payloads on stdin.
#
# Offline: no network, no docker, no Odoo. The only thing built is a throwaway git
# repo under mktemp, because the redirect and push rules judge a path or a branch
# against a real work tree.
#
# Every denied case asserts a substring of the reason, so a guard that starts
# refusing for the wrong reason goes red. The allowed cases matter as much: the
# guard watches a shell that runs git every few seconds, and each thing it declines
# to decide is named here.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
GUARD="$ROOT/hooks/unattended-guard.sh"
AGENT=odoo-dev-implementer

pass=0; fail=0
ok()  { echo "PASS $*"; pass=$((pass + 1)); }
bad() { echo "FAIL $*"; fail=$((fail + 1)); }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
REPO="$TMP/repo"
OUTSIDE="$TMP/outside"
mkdir -p "$REPO" "$OUTSIDE"
gitq() { git -C "$REPO" -c core.hooksPath=/dev/null -c user.email=t@example.com -c user.name=t "$@" >/dev/null 2>&1; }
gitq init -q -b main
printf 'tracked\n' > "$REPO/tracked.txt"
printf 'ignored.log\n' > "$REPO/.gitignore"
gitq add -A
gitq commit -q -m init

# payload <agent_type> <command> <cwd> [<tool_name>]
payload() {
  node -e '
const [type, cmd, cwd, tool] = process.argv.slice(1);
const p = {
  session_id: "test-session", cwd, permission_mode: "default",
  hook_event_name: "PreToolUse", tool_name: tool || "Bash",
  tool_input: { command: cmd }, tool_use_id: "toolu_test",
};
if (type) { p.agent_id = "agent-under-test"; p.agent_type = type; }
process.stdout.write(JSON.stringify(p));
' "$1" "$2" "${3:-$REPO}" "${4:-}"
}

OUT=""; RC=0
run() { OUT="$(payload "$@" | bash "$GUARD" 2>/dev/null)"; RC=$?; }

# allowed <label> <command> [<agent_type>] [<cwd>] [<tool>]
allowed() {
  local label="$1" cmd="$2" type="${3-$AGENT}" cwd="${4:-$REPO}" tool="${5:-}"
  run "$type" "$cmd" "$cwd" "$tool"
  if [ "$RC" -ne 0 ]; then bad "$label: expected exit 0, got $RC"; return; fi
  if [ -n "$OUT" ]; then bad "$label: expected silence, got: $OUT"; return; fi
  ok "$label -> allowed"
}

# denied <label> <want-substring> <command> [<cwd>]
denied() {
  local label="$1" want="$2" cmd="$3" cwd="${4:-$REPO}"
  run "$AGENT" "$cmd" "$cwd"
  if [ "$RC" -ne 0 ]; then bad "$label: expected exit 0, got $RC"; return; fi
  if ! printf '%s' "$OUT" | grep -q '"permissionDecision":"deny"'; then
    bad "$label: expected a deny, got: ${OUT:-<silence>}"; return
  fi
  if ! printf '%s' "$OUT" | grep -qF "$want"; then
    bad "$label: reason did not mention \"$want\": $OUT"; return
  fi
  ok "$label -> denied ($want)"
}

# --- force push, every variant -----------------------------------------------------
denied "push --force" "force push" "git push --force origin feat/x"
denied "push --force-with-lease" "force push" "git push --force-with-lease origin feat/x"
denied "push --force-with-lease=ref" "force push" "git push --force-with-lease=feat/x origin feat/x"
denied "push --force-if-includes" "force push" "git push --force-if-includes origin feat/x"
denied "push -f" "force push" "git push -f origin feat/x"
denied "push -fu cluster" "force push" "git push -fu origin feat/x"
denied "push +refspec" "+refspec" "git push origin +feat/x"
denied "push -C dir --force" "force push" "git -C $REPO push --force origin feat/x"
denied "force push after &&" "force push" "git status && git push -f origin feat/x"
denied "force push inside bash -c" "force push" "bash -c 'git push --force origin feat/x'"
denied "force push inside a substitution" "force push" 'echo $(git push --force origin feat/x)'
denied "force push through gh-as-owner" "force push" "gh-as-owner.sh push $REPO feat/x --force"
denied "push --mirror" "protected" "git push --mirror origin"
denied "push --all" "protected" "git push --all origin"

# --- protected and default branches ------------------------------------------------
denied "push main by name" "protected branch main" "git push origin main"
denied "push HEAD:main" "protected branch main" "git push origin HEAD:main"
denied "push refs/heads/master" "protected branch master" "git push origin feat/x:refs/heads/master"
denied "push release branch" "protected branch release/1.0" "git push origin release/1.0"
denied "delete main" "protected branch main" "git push origin :main"
denied "push with no refspec while on main" "protected branch main" "git push"
denied "push through gh-as-owner to main" "protected branch main" "gh-as-owner.sh push $REPO main"
gitq checkout -q -b feat/x
allowed "push a feature branch" "git push -u origin feat/x"
allowed "push HEAD on a feature branch" "git push origin HEAD"
allowed "push with no refspec on a feature branch" "git push"
allowed "push through gh-as-owner" "gh-as-owner.sh push $REPO feat/x"
allowed "the word main as a commit message" 'git commit -m "merge main into feat"'
gitq checkout -q main
denied "push HEAD while on main" "protected branch main" "git push origin HEAD"
gitq checkout -q feat/x

# --- pull request verbs ------------------------------------------------------------
denied "gh pr merge" "gh pr merge" "gh pr merge 12 --squash"
denied "gh pr ready" "gh pr ready" "gh pr ready 12"
denied "gh pr review" "gh pr review" "gh pr review 12 --approve"
denied "gh -R pr merge" "gh pr merge" "gh -R owner/repo pr merge 12"
denied "gh-as-owner gh pr merge" "gh pr merge" "gh-as-owner.sh gh pr merge 12 -R owner/repo"
denied "gh-as-owner pr-merge" "pr-merge" "gh-as-owner.sh pr-merge 12"
allowed "gh pr view" "gh pr view 12"
allowed "gh pr list" "gh pr list --state open"
allowed "gh-as-owner pr-create" "gh-as-owner.sh pr-create $REPO --base main --title t --body-file b.md"
allowed "the phrase gh pr merge inside a commit message" 'git commit -m "never run gh pr merge"'

# --- edits through a shell inside a working tree ----------------------------------
denied "sed -i on a tracked file" "sed -i" "sed -i s/a/b/ tracked.txt"
denied "sed -i.bak on a tracked file" "sed -i" "sed -i.bak s/a/b/ tracked.txt"
denied "sed --in-place" "sed -i" "sed --in-place s/a/b/ tracked.txt"
denied "sed -ni cluster" "sed -i" "sed -ni p tracked.txt"
denied "sed -i with -e" "sed -i" "sed -i -e s/a/b/ tracked.txt"
denied "perl -pi" "perl -i" "perl -pi -e s/a/b/ tracked.txt"
denied "echo redirect" "redirect into" "echo hi > tracked.txt"
denied "echo append" "redirect into" "echo hi >> tracked.txt"
denied "redirect to a new file in the tree" "redirect into" "echo hi > new.txt"
denied "redirect with no space" "redirect into" "echo hi >new.txt"
denied "redirect by absolute path" "redirect into" "echo hi > $REPO/new.txt"
denied "heredoc into a tracked path" "redirect into" "$(printf '%s\n' "cat > tracked.txt <<'EOF'" "content" "EOF")"
denied "tee into the tree" "tee writes" "echo hi | tee tracked.txt"
denied "tee -a into the tree" "tee writes" "echo hi | tee -a tracked.txt"
denied "redirect inside bash -c" "redirect into" "bash -c 'echo hi > tracked.txt'"
allowed "sed -i on a file outside any tree" "sed -i s/a/b/ $OUTSIDE/scratch.txt"
allowed "sed without -i" "sed -n p tracked.txt"
allowed "sed -n with a pipe" "cat tracked.txt | sed -n 1p"
allowed "redirect to /dev/null" "echo hi > /dev/null"
allowed "redirect stderr to /dev/null" "ls tracked.txt 2>/dev/null"
allowed "stderr to stdout" "ls tracked.txt 2>&1"
allowed "redirect outside any tree" "echo hi > $OUTSIDE/scratch.txt"
allowed "redirect to a gitignored path" "echo hi > ignored.log"
allowed "tee outside any tree" "echo hi | tee $OUTSIDE/scratch.txt"
allowed "heredoc body that names redirects and docker" "$(printf '%s\n' "cat <<'EOF' | wc -l" "docker ps > tracked.txt" "git push --force" "EOF")"
allowed "a redirect character inside quotes" 'grep ">" tracked.txt'
allowed "cat with a heredoc and no redirect" "$(printf '%s\n' "cat <<'EOF'" "hello" "EOF")"

# --- docker in any form ------------------------------------------------------------
denied "docker ps" "docker" "docker ps"
denied "docker by path" "docker" "/usr/bin/docker run --rm alpine true"
denied "docker compose" "docker" "docker compose up -d"
denied "docker-compose" "docker" "docker-compose up -d"
denied "sudo docker" "docker" "sudo docker ps"
denied "docker after a pipe" "docker" "echo x | docker run -i alpine cat"
denied "docker with an env prefix" "docker" "DOCKER_HOST=tcp://x docker ps"
denied "docker inside bash -c" "docker" "bash -c 'docker ps'"
denied "docker inside a substitution" "docker" 'echo $(docker ps)'
denied "docker inside backticks" "docker" 'echo `docker ps`'
allowed "the word docker as an argument" 'grep -rn docker README.md'
allowed "docker inside a commit message" 'git commit -m "no docker here"'

# --- ordinary work passes ----------------------------------------------------------
allowed "git status" "git status --short"
allowed "git commit" 'git commit -m "feat: x"'
allowed "git log" "git log --oneline -5"
allowed "a test run" "bash run-tests.sh --db scratch --modules sale"
allowed "an empty command" ""

# --- scope: only the implementer is policed ----------------------------------------
allowed "main session, force push" "git push --force origin feat/x" ""
allowed "tester agent, docker" "docker ps" "odoo-dev-tester"
allowed "builder agent, redirect into the tree" "echo hi > tracked.txt" "odoo-dev-builder"
allowed "implementer, a tool that is not Bash" "git push --force" "$AGENT" "$REPO" "Edit"
denied_ns() {
  run "odoo-dev:$AGENT" "$2" "$REPO"
  if printf '%s' "$OUT" | grep -q '"permissionDecision":"deny"'; then ok "$1 -> denied"; else bad "$1: expected a deny, got: ${OUT:-<silence>}"; fi
}
denied_ns "namespaced agent type" "git push --force origin feat/x"

# --- fail closed, but only inside the scope ----------------------------------------
OUT="$(printf 'not json at all %s' "$AGENT" | bash "$GUARD" 2>/dev/null)"; RC=$?
if [ "$RC" -eq 0 ] && printf '%s' "$OUT" | grep -q '"permissionDecision":"deny"'; then
  ok "unparseable payload naming the implementer -> denied"
else bad "unparseable payload naming the implementer: rc=$RC out=${OUT:-<silence>}"; fi

OUT="$(printf 'not json at all' | bash "$GUARD" 2>/dev/null)"; RC=$?
if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then ok "unparseable payload naming nobody -> allowed"
else bad "unparseable payload naming nobody: rc=$RC out=${OUT:-<silence>}"; fi

OUT="$(node -e 'process.stdout.write(JSON.stringify({agent_type:process.argv[1],agent_id:"a",tool_name:"Bash",tool_input:{}}))' "$AGENT" | bash "$GUARD" 2>/dev/null)"; RC=$?
if [ "$RC" -eq 0 ] && printf '%s' "$OUT" | grep -q '"permissionDecision":"deny"'; then
  ok "implementer Bash call with no command -> denied"
else bad "implementer Bash call with no command: rc=$RC out=${OUT:-<silence>}"; fi

echo
echo "unattended-guard.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
