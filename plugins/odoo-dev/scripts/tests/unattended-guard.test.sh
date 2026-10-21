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
# The run trigger must come from each case, never from the caller's environment, and
# HOME must resolve outside any git tree so the ~ and $HOME cases are about the guard.
unset ODOO_UNATTENDED CLAUDE_CONFIG_DIR
export HOME="$OUTSIDE"
gitq() { git -C "$REPO" -c core.hooksPath=/dev/null -c user.email=t@example.com -c user.name=t "$@" >/dev/null 2>&1; }
gitq init -q -b main
printf 'tracked\n' > "$REPO/tracked.txt"
printf 'ignored.log\n' > "$REPO/.gitignore"
gitq add -A
gitq commit -q -m init

# payload <agent_type> <command> <cwd> [<tool_name>] [<extra tool_input JSON>]
payload() {
  node -e '
const [type, cmd, cwd, tool, extra] = process.argv.slice(1);
const p = {
  session_id: "test-session", cwd, permission_mode: "default",
  hook_event_name: "PreToolUse", tool_name: tool || "Bash",
  tool_input: Object.assign({ command: cmd }, extra ? JSON.parse(extra) : {}),
  tool_use_id: "toolu_test",
};
if (type) { p.agent_id = "agent-under-test"; p.agent_type = type; }
process.stdout.write(JSON.stringify(p));
' "$1" "$2" "${3:-$REPO}" "${4:-}" "${5:-}"
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
denied "delete main" "deletes a remote branch" "git push origin :main"
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

# --- redirect spellings (the guard reads operators, not whole tokens) -------------
denied "redirect with no space either side" "redirect into" "echo hi>f.txt"
denied "append with no space either side" "redirect into" "echo hi>>f.txt"
denied "cat>file" "redirect into" "cat>f.txt"
denied "redirect to a double-quoted target" "redirect into" 'echo hi >"f.txt"'
denied "redirect to a single-quoted target" "redirect into" "echo hi >'f.txt'"
denied "clobber redirect >|" "redirect into" "echo hi >| f.txt"
denied "ampersand redirect &>" "redirect into" "echo hi &> f.txt"
denied "xargs tee" "tee writes" "echo hi | xargs tee f.txt"
denied "descriptor redirect 2>file" "redirect into" "ls 2>err.txt"
allowed "2>&1 with no spaces" "ls tracked.txt 2>&1 | cat"
allowed "redirect to /dev/null with no space" "ls tracked.txt >/dev/null"
allowed "no-space redirect outside any tree" "echo hi>$OUTSIDE/scratch.txt"
allowed "a quoted target outside any tree" "echo hi > \"$OUTSIDE/scratch.txt\""

# --- unexpandable targets are not judged (no false denials) ------------------------
allowed "redirect to ~/x" "echo hi > ~/x"
allowed "redirect to \$HOME/x" 'echo hi > $HOME/x'
allowed "redirect to a quoted unset variable" 'echo hi > "$LOG_FILE_NOT_SET"'
allowed "tee to ~/x" "echo hi | tee ~/x"

# --- command position: keywords, wrappers, shells ----------------------------------
denied "force push in braces" "force push" "{ git push --force origin feat/x; }"
denied "force push after then" "force push" "if true; then git push --force origin feat/x; fi"
denied "force push in a for loop" "force push" "for x in a; do git push -f origin feat/x; done"
denied "force push after !" "force push" "! git push --force origin feat/x"
denied "env -i git push --force" "force push" "env -i git push --force origin feat/x"
denied "env -i docker" "docker" "env -i docker ps"
denied "env -u X docker" "docker" "env -u HOME docker ps"
denied "timeout git push --force" "force push" "timeout 5 git push --force origin feat/x"
denied "timeout -s KILL" "force push" "timeout -s KILL 5 git push -f origin feat/x"
denied "nice git push --force" "force push" "nice -n 5 git push --force origin feat/x"
denied "stdbuf -o0" "force push" "stdbuf -o0 git push --force origin feat/x"
denied "ionice git push --force" "force push" "ionice -c3 git push --force origin feat/x"
denied "xargs git push --force" "force push" "echo x | xargs git push --force origin feat/x"
denied "find -exec git push --force" "force push" "find . -name x -exec git push --force origin feat/x \\;"
denied "sudo -u x docker" "docker" "sudo -u x docker ps"
denied "bash -lc" "force push" 'bash -lc "git push --force origin feat/x"'
denied "bash -ce" "force push" 'bash -ce "git push --force origin feat/x"'
denied "echo piped to bash" "force push" "echo 'git push --force origin feat/x' | bash"
denied "printf piped to sh" "force push" "printf 'git push --force origin feat/x' | sh"
denied "here-string to bash" "force push" "bash <<< 'git push --force origin feat/x'"
denied "heredoc to bash" "force push" "$(printf '%s\n' "bash <<EOT" "git push --force origin feat/x" "EOT")"
denied "heredoc piped to bash" "force push" "$(printf '%s\n' "cat <<EOT | bash" "git push --force origin feat/x" "EOT")"
denied "alias then run" "force push" "alias gp='git push --force origin feat/x'; gp"
denied "nerdctl" "nerdctl" "nerdctl ps"
denied "ctr" "ctr" "ctr images ls"
denied "buildah" "buildah" "buildah images"
allowed "env with a variable and a harmless command" "env FOO=1 git status"
allowed "timeout on a harmless command" "timeout 5 git status"
allowed "xargs on a harmless command" "echo x | xargs echo"
allowed "echo piped to a non-shell" "echo 'git push --force' | wc -c"

# --- heredoc extraction must not eat real commands ---------------------------------
denied "here-string does not hide the next line" "docker" "$(printf '%s\n' "cat <<< x" "docker ps")"
denied "quoted << does not hide the next line" "docker" "$(printf '%s\n' "echo '<<X'" "docker ps")"
denied "double-quoted << does not hide the next line" "docker" "$(printf '%s\n' 'echo "a << b"' "docker ps")"
denied "a real heredoc, then a command after it" "docker" "$(printf '%s\n' "cat <<EOT" "body" "EOT" "docker ps")"

# --- protected branches are case-insensitive ---------------------------------------
denied "push UAT" "protected branch UAT" "git push origin UAT"
denied "push HEAD:UAT" "protected branch UAT" "git push origin HEAD:UAT"
denied "push Staging" "protected branch Staging" "git push origin Staging"
denied "push Main" "protected branch Main" "git push origin Main"
allowed "push a branch that merely contains main" "git push origin feat/main-menu"

# --- push option parsing, deletes, --repo and cd -----------------------------------
denied "push --repo=origin main" "protected branch main" "git push --repo=origin main"
denied "push --repo origin main" "protected branch main" "git push --repo origin main"
denied "push --delete" "deletes a remote branch" "git push origin --delete feat/x"
denied "push -d" "deletes a remote branch" "git push -d origin feat/x"
denied "push :branch" "deletes a remote branch" "git push origin :feat/x"
allowed "push -o value is not a refspec" "git push -o ci.skip origin feat/x"
MAINCO="$TMP/mainco"
mkdir -p "$MAINCO"
git -C "$MAINCO" init -q -b main
git -C "$MAINCO" -c core.hooksPath=/dev/null -c user.email=t@example.com -c user.name=t commit -q --allow-empty -m init
denied "cd into a main checkout, then push" "protected branch main" "cd $MAINCO && git push origin"
denied "cd then push HEAD" "protected branch main" "cd $MAINCO; git push origin HEAD"
allowed "cd into the feature checkout, then push" "cd $REPO && git push origin"

# --- pull request routes -----------------------------------------------------------
denied "gh pr create direct" "gh pr create" "gh pr create --draft --title t"
denied "gh-as-owner gh pr create" "gh pr create" "gh-as-owner.sh gh pr create --title t"
denied "gh api merge route" "merges or reviews" "gh api repos/o/r/pulls/5/merge -X PUT"
denied "gh api merge route, bare" "merges or reviews" "gh api repos/o/r/pulls/5/merge"
denied "gh api review POST" "merges or reviews" "gh api repos/o/r/pulls/5/reviews -f event=APPROVE"
denied "gh api graphql merge" "merges or reviews" "gh api graphql -f query='mutation { mergePullRequest(input: {pullRequestId: \"x\"}) { clientMutationId } }'"
allowed "gh api reading reviews" "gh api repos/o/r/pulls/5/reviews"
allowed "gh api reading comments" "gh api repos/o/r/pulls/5/comments"

# --- the run trigger: ODOO_UNATTENDED, whatever the agent is ------------------------
# envrun <env value> <agent_type> <command>
envrun() { OUT="$(payload "$2" "$3" "$REPO" | env ODOO_UNATTENDED="$1" bash "$GUARD" 2>/dev/null)"; RC=$?; }
env_denied() {
  envrun "$2" "$3" "$4"
  if [ "$RC" -eq 0 ] && printf '%s' "$OUT" | grep -q '"permissionDecision":"deny"'; then ok "$1 -> denied"
  else bad "$1: expected a deny, rc=$RC out=${OUT:-<silence>}"; fi
}
env_allowed() {
  envrun "$2" "$3" "$4"
  if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then ok "$1 -> allowed"
  else bad "$1: expected silence, rc=$RC out=${OUT:-<silence>}"; fi
}
env_denied "pr fork, push --force, ODOO_UNATTENDED=1" 1 "odoo-dev-pr" "git push --force origin feat/x"
env_denied "pr fork, push main" 1 "odoo-dev:odoo-dev-pr" "git push origin main"
env_denied "pr fork, gh pr ready" 1 "odoo-dev-pr" "gh pr ready 12"
env_denied "builder fork, docker" 1 "odoo-dev-builder" "docker ps"
env_denied "tester fork, redirect into the tree" 1 "odoo-dev-tester" "echo hi > tracked.txt"
env_denied "main session of the run" 1 "" "git push -f origin feat/x"
env_denied "any non-empty value engages, including 0" 0 "odoo-dev-pr" "git push -f origin feat/x"
env_allowed "pr fork, an ordinary push" 1 "odoo-dev-pr" "git push -u origin feat/x"
env_allowed "pr fork, gh pr view" 1 "odoo-dev-pr" "gh pr view 12"
env_denied "variable empty: pr fork, the agent prefix still engages" "" "odoo-dev-pr" "git push --force origin feat/x"
env_allowed "variable empty: non-plugin agent, force push passes" "" "general-purpose" "git push --force origin feat/x"
env_allowed "variable empty: interactive docker passes" "" "" "docker ps"
OUT="$(printf 'not json' | env ODOO_UNATTENDED=1 bash "$GUARD" 2>/dev/null)"; RC=$?
if [ "$RC" -eq 0 ] && printf '%s' "$OUT" | grep -q '"permissionDecision":"deny"'; then
  ok "unparseable payload while ODOO_UNATTENDED is set -> denied"
else bad "unparseable payload with ODOO_UNATTENDED: rc=$RC out=${OUT:-<silence>}"; fi

# --- the Claude configuration directory, in a git tree or not ----------------------
# HOME is $OUTSIDE, so ~/.claude is $OUTSIDE/.claude, which is not in any git tree.
denied "config: redirect to ~/.claude/settings.json" "Claude configuration directory" "echo x > ~/.claude/settings.json"
denied "config: append with no spaces" "Claude configuration directory" "echo x>>~/.claude/settings.json"
denied "config: redirect by \$HOME" "Claude configuration directory" 'echo x > $HOME/.claude/settings.json'
denied "config: redirect by absolute path" "Claude configuration directory" "echo x > $OUTSIDE/.claude/settings.json"
denied "config: tee into it" "Claude configuration directory" "echo x | tee ~/.claude/settings.json"
denied "config: tee -a into it" "Claude configuration directory" "echo x | tee -a $OUTSIDE/.claude/hooks/h.sh"
denied "config: sed -i in it" "Claude configuration directory" "sed -i s/a/b/ ~/.claude/settings.json"
denied "config: perl -pi in it" "Claude configuration directory" "perl -pi -e s/a/b/ ~/.claude/settings.json"
denied "config: a nested new directory" "Claude configuration directory" "echo x > ~/.claude/new/dir/f.json"
denied "config: through a relative path from inside it" "Claude configuration directory" "cd ~/.claude && echo x > settings.json"
denied "config: bash -c" "Claude configuration directory" "bash -c 'echo x > ~/.claude/settings.json'"
allowed "config: reading it" "cat ~/.claude/settings.json"
allowed "config: a sibling that only shares the prefix" "echo x > $OUTSIDE/.claude-other/f.json"
allowed "config: a different file under HOME" "echo x > ~/notes.txt"

# CLAUDE_CONFIG_DIR relocates the directory; the guard follows it and keeps ~/.claude.
RELOC="$TMP/relocated-config"
mkdir -p "$RELOC"
# cfgrun <command>: run with CLAUDE_CONFIG_DIR set to the relocated directory
cfgrun() { OUT="$(payload "$AGENT" "$1" "$REPO" | env CLAUDE_CONFIG_DIR="$RELOC" bash "$GUARD" 2>/dev/null)"; RC=$?; }
cfg_denied() {
  cfgrun "$2"
  if [ "$RC" -eq 0 ] && printf '%s' "$OUT" | grep -q '"permissionDecision":"deny"' \
     && printf '%s' "$OUT" | grep -qF "Claude configuration directory"; then ok "$1 -> denied"
  else bad "$1: expected a config deny, rc=$RC out=${OUT:-<silence>}"; fi
}
cfg_allowed() {
  cfgrun "$2"
  if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then ok "$1 -> allowed"
  else bad "$1: expected silence, rc=$RC out=${OUT:-<silence>}"; fi
}
cfg_denied "relocated: redirect into CLAUDE_CONFIG_DIR" "echo x > $RELOC/settings.json"
cfg_denied "relocated: tee into CLAUDE_CONFIG_DIR" "echo x | tee $RELOC/settings.json"
cfg_denied "relocated: sed -i in CLAUDE_CONFIG_DIR" "sed -i s/a/b/ $RELOC/settings.json"
cfg_denied "relocated: by variable" 'echo x > $CLAUDE_CONFIG_DIR/settings.json'
cfg_denied "relocated: ~/.claude is still guarded" "echo x > ~/.claude/settings.json"
cfg_allowed "relocated: a path beside CLAUDE_CONFIG_DIR" "echo x > $RELOC-beside/f.json"
cfg_allowed "relocated: reading CLAUDE_CONFIG_DIR" "cat $RELOC/settings.json"
# The run trigger works on the same rule: a pr fork cannot write the settings either.
envrun 1 "odoo-dev-pr" "echo x > ~/.claude/settings.json"
if printf '%s' "$OUT" | grep -qF "Claude configuration directory"; then ok "pr fork under ODOO_UNATTENDED, config write -> denied"
else bad "pr fork config write: out=${OUT:-<silence>}"; fi

# --- backslash line continuations join lines before anything is read ---------------
denied "continuation: flag on the next line" "force push" "$(printf '%s\n' 'git push origin x \' '--force')"
denied "continuation: subcommand on the next line" "force push" "$(printf '%s\n' 'git \' 'push --force origin x')"
denied "continuation: gh pr merge split" "gh pr merge" "$(printf '%s\n' 'gh pr \' '  merge 5')"
denied "continuation: protected branch on the next line" "protected branch main" "$(printf '%s\n' 'git push origin \' 'main')"
denied "continuation: docker split" "docker" "$(printf '%s\n' 'doc\' 'ker ps')"
allowed "continuation: a harmless command" "$(printf '%s\n' 'git status \' '--short')"

# --- quoted text is text: commit messages and PR bodies may name the forbidden things
QT="'"
allowed "quoted: escaped backticks in a double-quoted commit message" 'git commit -m "never use \`git push --force\`"'
allowed "quoted: backticks in a single-quoted commit message" "git commit -m ${QT}never use \`docker ps\` here${QT}"
allowed "quoted: escaped backticks in a PR body" 'gh-as-owner.sh pr-create /w --body "Do not call \`gh pr merge\`..."'
allowed "quoted: single-quoted \$( ) is text" "git commit -m ${QT}never run \$(docker ps)${QT}"
allowed "quoted: heredoc commit message inside \$( ) in double quotes" "$(printf '%s\n' "git commit -m \"\$(cat <<${QT}EOF${QT}" 'never git push --force or `docker ps` or do not' "don${QT}t" 'EOF' ')"')"
allowed "quoted: heredoc PR body inside \$( )" "$(printf '%s\n' "gh-as-owner.sh pr-create /w --body \"\$(cat <<${QT}EOF${QT}" 'docker ps > tracked.txt; gh pr merge 5' 'EOF' ')"')"
denied "quoted: an unescaped backtick in double quotes really runs" "docker" 'git commit -m "x `docker ps` y"'
denied "quoted: a real \$( ) in double quotes runs" "docker" 'echo "$(docker ps)"'
denied "quoted: a command after a quoted message still counts" "force push" "git commit -m ${QT}a \`b\`${QT} && git push --force origin x"

# --- gh api: every spelling of the method, and the ref and merge routes -------------
denied "gh api --method=POST on reviews" "merges or reviews" "gh api repos/a/b/pulls/5/reviews --method=POST"
denied "gh api -XPOST on reviews" "merges or reviews" "gh api repos/a/b/pulls/5/reviews -XPOST"
denied "gh api force-update of a ref" "git ref" "gh api repos/a/b/git/refs/heads/main -X PATCH -f sha=x -F force=true"
denied "gh api delete of a ref" "git ref" "gh api repos/a/b/git/refs/heads/x -X DELETE"
denied "gh api DELETE by --method=" "git ref" "gh api repos/a/b/git/refs/heads/x --method=DELETE"
denied "gh api ref DELETE by -XDELETE" "git ref" "gh api repos/a/b/git/refs/heads/x -XDELETE"
denied "gh api merges route" "git ref" "gh api repos/a/b/merges -f base=main -f head=x"
denied "gh pr close" "gh pr close" "gh pr close 5"
allowed "gh api reading a ref" "gh api repos/a/b/git/refs/heads/main"
allowed "gh api reading a ref with --method=GET" "gh api repos/a/b/git/refs/heads/main --method=GET"
allowed "gh api reading reviews with -XGET" "gh api repos/a/b/pulls/5/reviews -XGET"

# --- GraphQL ref mutations, protection and PATCH-close -----------------------------
denied "graphql updateRef (a force update)" "merges or reviews" "gh api graphql -f query='mutation { updateRef(input: {refId: \"x\", oid: \"y\", force: true}) { clientMutationId } }'"
denied "graphql deleteRef" "merges or reviews" "gh api graphql -f query='mutation { deleteRef(input: {refId: \"x\"}) { clientMutationId } }'"
denied "graphql createRef" "merges or reviews" "gh api graphql -f query='mutation { createRef(input: {}) { clientMutationId } }'"
denied "graphql closePullRequest" "merges or reviews" "gh api graphql -f query='mutation { closePullRequest(input: {pullRequestId: \"x\"}) { clientMutationId } }'"
denied "gh api PATCH pulls/N state=closed" "closes a pull request" "gh api repos/a/b/pulls/5 -X PATCH -f state=closed"
denied "gh api branch protection DELETE" "branch protection" "gh api repos/a/b/branches/main/protection -X DELETE"
allowed "graphql read-only query" "gh api graphql -f query='query { viewer { login } }'"
allowed "gh api reading branch protection" "gh api repos/a/b/branches/main/protection"

# --- an even run of backslashes does not continue the line -------------------------
denied "even backslashes: docker on the next line" "docker" "$(printf '%s\n' 'echo foo \\' 'docker ps')"
denied "even backslashes: force push on the next line" "force push" "$(printf '%s\n' 'echo foo \\' 'git push --force origin x')"
denied "odd run (three backslashes) still continues" "force push" "$(printf '%s\n' 'git push origin x \\\' ' --force')"
denied "odd backslash (one) with CRLF: still continues" "force push" "$(printf 'git push origin x \\\r\n--force')"
allowed "even backslashes: two harmless lines" "$(printf '%s\n' 'echo foo \\' 'git status')"

# --- Odoo series branches are protected by name ------------------------------------
denied "push 19.0" "protected branch 19.0" "git push origin 19.0"
denied "push HEAD:18.0" "protected branch 18.0" "git push origin HEAD:18.0"
denied "push 17.0-dev" "protected branch 17.0-dev" "git push origin 17.0-dev"
denied "push odoo-19.0" "protected branch odoo-19.0" "git push origin odoo-19.0"
allowed "push a task branch <id>-<slug>" "git push -u origin 30412-add-field"

# --- scope: only the implementer is policed ----------------------------------------
allowed "main session, force push" "git push --force origin feat/x" ""
allowed "non-plugin agent, docker" "docker ps" "general-purpose"
allowed "implementer, a tool that is not Bash" "git push --force" "$AGENT" "$REPO" "Edit"
denied_as() {
  run "$2" "$3" "$REPO"
  if printf '%s' "$OUT" | grep -q '"permissionDecision":"deny"'; then ok "$1 -> denied"; else bad "$1: expected a deny, got: ${OUT:-<silence>}"; fi
}
denied_as "tester agent, docker" "odoo-dev-tester" "docker ps"
denied_as "builder agent, redirect into the tree" "odoo-dev-builder" "echo hi > tracked.txt"
denied_as "future agent odoo-dev-upgrade-implementer, force push" "odoo-dev-upgrade-implementer" "git push --force origin feat/x"
denied_as "namespaced agent type" "odoo-dev:$AGENT" "git push --force origin feat/x"
denied_as "namespaced future agent odoo-dev:odoo-dev-upgrader" "odoo-dev:odoo-dev-upgrader" "git push --force origin feat/x"

# --- background runs: a forked turn ends with the call ------------------------------
allowed "foreground run with a timeout" "timeout 600 install_all.sh --db x"
denied "trailing &" "background runs die" "install_all.sh --db x &"
denied "& between two commands" "background runs die" "install_all.sh --db x & echo started"
denied "nohup wrapper" "background runs die" "nohup install_all.sh --db x"
denied "setsid wrapper" "background runs die" "setsid install_all.sh --db x"
denied "timeout then nohup" "background runs die" "timeout 600 nohup install_all.sh --db x"
denied "disown head" "background runs die" "disown"
run "$AGENT" "echo hi" "$REPO" "Bash" '{"run_in_background":true}'
if printf '%s' "$OUT" | grep -qF 'background runs die'; then ok "run_in_background true -> denied"; else bad "run_in_background true: expected a deny, got: ${OUT:-<silence>}"; fi
allowed "run_in_background false" "echo hi" "$AGENT" "$REPO" "Bash"
allowed "&& is not background" "true && echo hi"
allowed "pipe is not background" "echo hi | cat"
allowed "|& is not background" "echo hi |& cat"
allowed "&> to a file outside any tree" "echo hi &>/tmp/unattended-guard-bg.log"

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
