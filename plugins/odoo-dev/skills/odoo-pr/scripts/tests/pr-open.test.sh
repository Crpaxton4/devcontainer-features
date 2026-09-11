#!/usr/bin/env bash
# pr-open.test.sh — offline tests for pr-open.sh. A stub `gh` on PATH records the
# subcommands it was asked to run and answers from a tiny JSON state file, so the
# rerun-safe branch (edit an existing PR rather than open a second) is exercised
# without touching GitHub.
#
# That branch is the one worth testing: a task that gets a second PR loses its
# review history and its CodeRabbit thread, and the bug is invisible until it
# happens on a real task.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$(cd "$SCRIPT_DIR/.." && pwd)/pr-open.sh"
work="$(mktemp -d "${TMPDIR:-/tmp}/pr-open-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
expect() { if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: wanted '$3', got '$2'" >&2; fi; }
field() { node -e 'console.log(String(JSON.parse(process.argv[1])[process.argv[2]] ?? "null"))' "$1" "$2"; }
called() { grep -qx "$1" "$work/gh-calls"; }

bin="$work/bin"; mkdir -p "$bin"
cat > "$bin/gh" <<'STUB'
#!/usr/bin/env bash
# Records "<subcommand> <verb>" per invocation and answers from $GH_EXISTING.
echo "$1 ${2:-}" >> "$GH_CALLS"
case "$1 ${2:-}" in
  "pr list")
    # A marker file, not an env var: `gh pr create` runs in its own process, so
    # exporting from there would be invisible to the NEXT `gh pr list` the
    # script makes to learn the new PR's number.
    if [ "${GH_EXISTING:-}" = "yes" ] || [ -f "$GH_CALLS.created" ]; then
      echo '[{"number":77,"url":"https://github.com/o/r/pull/77","isDraft":true}]'
    else
      echo '[]'
    fi ;;
  "pr create") : > "$GH_CALLS.created"; echo "https://github.com/o/r/pull/77" ;;
  "pr view")
    echo '{"url":"https://github.com/o/r/pull/77","number":77,"isDraft":true,"baseRefName":"UAT","headRefName":"4242-my-slug"}' ;;
  *) : ;;
esac
exit 0
STUB
chmod +x "$bin/gh"

repo="$work/repo"; mkdir -p "$work/no-hooks"
git init --quiet "$repo"
git -C "$repo" config user.email t@e.invalid
git -C "$repo" config user.name t
git -C "$repo" config core.hooksPath "$work/no-hooks"
echo x > "$repo/f"; git -C "$repo" add f; git -C "$repo" commit --quiet -m seed
git -C "$repo" checkout --quiet -b 4242-my-slug
printf '## body\n' > "$work/body.md"

run() { # run <existing?> [extra args...]
  local existing="$1"; shift
  : > "$work/gh-calls"; rm -f "$work/gh-calls.created"
  PATH="$bin:$PATH" GH_CALLS="$work/gh-calls" GH_EXISTING="$existing" \
    bash "$SUT" "$repo" o/r UAT --title "feat(m): x [task 4242]" \
      --body-file "$work/body.md" --no-push "$@" 2>/dev/null | tail -1
}

# ---------- create path --------------------------------------------------------
out="$(run no --draft --assign-me)"
expect "creates when none exists" "$(field "$out" action)" "created"
expect "reports the url"          "$(field "$out" pr_url)" "https://github.com/o/r/pull/77"
expect "reports the number"       "$(field "$out" pr_number)" "77"
expect "base is the one given"    "$(field "$out" base)" "UAT"
expect "assigned"                 "$(field "$out" assigned)" "true"
called "pr create"; expect "called pr create" "$?" "0"
# Existence is checked BEFORE creating; skipping that check is how a task gets
# a second PR.
expect "checked for an existing PR first" "$(head -1 "$work/gh-calls")" "pr list"

# ---------- rerun path ---------------------------------------------------------
out="$(run yes --draft --assign-me)"
expect "updates when one exists" "$(field "$out" action)" "updated"
called "pr edit"; expect "called pr edit" "$?" "0"
if called "pr create"; then fail=$((fail+1)); echo "FAIL rerun must not open a second PR" >&2; else pass=$((pass+1)); fi
# A rerun that only edited the body must still leave the PR assigned.
expect "rerun still assigns" "$(field "$out" assigned)" "true"

# Lifting the draft is a human gate, so the script must never call `gh pr ready`,
# with any flag. There is also no path back to draft, because
# that would undo a human's decision to request review.
out="$(run yes)"
if called "pr ready"; then fail=$((fail+1)); echo "FAIL must never mark a PR ready" >&2; else pass=$((pass+1)); fi
out="$(run yes --draft)"
if called "pr ready"; then fail=$((fail+1)); echo "FAIL --draft must not drag a ready PR back" >&2; else pass=$((pass+1)); fi

# ---------- guards -------------------------------------------------------------
guard() { PATH="$bin:$PATH" GH_CALLS="$work/gh-calls" GH_EXISTING=no bash "$SUT" "$@" >/dev/null 2>&1; echo $?; }
expect "missing body file is 5" "$(guard "$repo" o/r UAT --title T --body-file "$work/nope.md" --no-push)" "5"
: > "$work/empty.md"
expect "empty body file is 5"   "$(guard "$repo" o/r UAT --title T --body-file "$work/empty.md" --no-push)" "5"
expect "no title is 2"          "$(guard "$repo" o/r UAT --body-file "$work/body.md" --no-push)" "2"
expect "head == base is 2"      "$(guard "$repo" o/r 4242-my-slug --title T --body-file "$work/body.md" --no-push)" "2"
expect "non-worktree is 2"      "$(guard "$work" o/r UAT --title T --body-file "$work/body.md" --no-push)" "2"

echo "{\"passed\": $pass, \"failed\": $fail}"
[ "$fail" -eq 0 ]
