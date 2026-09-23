#!/usr/bin/env bash
# gh-url.test.sh — offline tests for scripts/_gh-url.sh, plus the lint that
# keeps the rule it encodes from being bypassed.
#
# The bug this guards (issue #762): `gh api repos/<o>/<r>/branches/<branch>`
# with a '#' in the branch name returns 404 for a branch that exists, because
# '#' opens a URL fragment and the request goes out against a truncated name.
# '#' in a branch name is a designed-in convention in these repos, so the first
# unencoded `branches/<branch>` call would reintroduce it silently.
#
# The lint at the bottom is here rather than in validate.sh on purpose: the
# plugin CI job enumerates every *.test.sh under plugins/odoo-dev with `find`,
# so a suite is picked up without editing a list, and keeping the rule next to
# the helper it defends means one file to read instead of two.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS="$(cd "$SCRIPT_DIR/.." && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPTS/.." && pwd)"
work="$(mktemp -d "${TMPDIR:-/tmp}/gh-url-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
expect() { if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: wanted '$3', got '$2'" >&2; fi; }

# shellcheck source=../_gh-url.sh
. "$SCRIPTS/_gh-url.sh"

# ---------- gh_path_segment ----------------------------------------------------

# The reported case, verbatim from the issue.
expect "'#' becomes %23" \
  "$(gh_path_segment '12345#some-upgrade')" '12345%23some-upgrade'
# '/' is the quiet one: unencoded it is a path separator, so the request lands
# on a different endpoint rather than failing.
expect "'/' becomes %2F" \
  "$(gh_path_segment 'feature/12345#slug')" 'feature%2F12345%23slug'

# The RFC 3986 unreserved set survives untouched, so the common case of an
# ASCII branch name produces exactly the name.
expect "unreserved set passes through" \
  "$(gh_path_segment '30412-my_slug.v1~x')" '30412-my_slug.v1~x'

# Every other character a git ref legally admits. A blacklist would have to be
# right about all of these; the whitelist is right by construction.
expect "space"       "$(gh_path_segment 'a b')"  'a%20b'
expect "percent"     "$(gh_path_segment 'a%b')"  'a%25b'
expect "query chars" "$(gh_path_segment 'a?b=c&d')" 'a%3Fb%3Dc%26d'
expect "plus"        "$(gh_path_segment 'a+b')"  'a%2Bb'
expect "semicolon"   "$(gh_path_segment 'a;b')"  'a%3Bb'
expect "at and colon" "$(gh_path_segment 'a@b:c')" 'a%40b%3Ac'

# Empty in, empty out — never the literal string "null" or a stray escape.
expect "empty stays empty" "$(gh_path_segment '')" ''

# One %XX per BYTE, not per character: that is what the server decodes back.
expect "utf-8 encodes byte-wise" "$(gh_path_segment 'ü')" '%C3%BC'

# The whole point, end to end: the assembled path keeps the slug's '/' as a
# real separator and carries the '#' as %23.
branch='12345#some-upgrade'
expect "assembled path" \
  "repos/acme/addons/branches/$(gh_path_segment "$branch")" \
  'repos/acme/addons/branches/12345%23some-upgrade'

# ---------- gh_branch_exists ---------------------------------------------------

bin="$work/bin"; mkdir -p "$bin"
cat > "$bin/gh" <<'STUB'
#!/usr/bin/env bash
# Answers `gh api repos/<slug>/branches` from a fixed list; exits non-zero when
# GH_FAIL is set, so "could not ask" stays distinguishable from "not there".
[ -z "${GH_FAIL:-}" ] || { echo "api error" >&2; exit 1; }
printf '%s\n' '12345#some-upgrade' '12345#pre-some-upgrade' 'main'
STUB
chmod +x "$bin/gh"

exists() { PATH="$bin:$PATH" GH_FAIL="${2:-}" bash -c '. "$1"; gh_branch_exists o/r "$2"; echo $?' _ "$SCRIPTS/_gh-url.sh" "$1"; }

expect "finds a '#' branch by listing" "$(exists '12345#some-upgrade')" "0"
expect "finds a plain branch"          "$(exists 'main')" "0"
# Whole-line match: a prefix of a real branch is not a branch.
expect "prefix is not a match"         "$(exists '12345#some')" "1"
# '#' and '.' are regex metacharacters; -F keeps them literal.
expect "no regex interpretation"       "$(exists '12345.some-upgrade')" "1"
# A failed listing is exit 2, never exit 1 — "GitHub did not answer" and "the
# branch is gone" lead to opposite decisions.
expect "listing failure is 2"          "$(exists 'main' 1)" "2"

# ---------- lint: no ref interpolated raw into an API path ---------------------
#
# Endpoint families whose path segment IS a git ref. `commits/<ref>` takes a ref
# too, but every use in this tree interpolates a 40-hex sha from `git rev-list`,
# so listing it here would flag provably safe code; the helper's header says so.
GH_REF_PATH_RE='repos/[^" ]*/(branches|compare|git/refs[^" ]*)/\$\{?[A-Za-z_]'

# Scan <root> for the unencoded shape. Lines that call gh_path_segment are the
# fixed form and are exempt. tests/ is excluded: a suite has to be able to write
# the broken shape down in order to prove the lint catches it.
scan() {
  find "$1" -name '*.sh' -type f ! -path '*/tests/*' -print0 2>/dev/null \
    | xargs -0 -r grep -nE "$GH_REF_PATH_RE" 2>/dev/null \
    | grep -v 'gh_path_segment' || true
}

# The lint has to be proved to fire before its silence means anything.
mkdir -p "$work/probe"
printf '%s\n' 'gh api "repos/$slug/branches/$branch"' > "$work/probe/offender.sh"
printf '%s\n' 'gh api "repos/$slug/compare/${base}...${head}"' >> "$work/probe/offender.sh"
printf '%s\n' 'gh api "repos/$slug/branches/$(gh_path_segment "$branch")"' > "$work/probe/fixed.sh"
printf '%s\n' 'gh api "repos/$slug/pulls/$n/files"' > "$work/probe/unrelated.sh"

expect "lint catches branches/\$var" "$(scan "$work/probe" | grep -c 'offender.sh:1')" "1"
expect "lint catches compare/\${var}" "$(scan "$work/probe" | grep -c 'offender.sh:2')" "1"
expect "lint accepts the encoded form" "$(scan "$work/probe" | grep -c 'fixed.sh')" "0"
expect "lint ignores pulls/<number>"   "$(scan "$work/probe" | grep -c 'unrelated.sh')" "0"

hits="$(scan "$PLUGIN_ROOT")"
if [ -z "$hits" ]; then
  pass=$((pass+1))
else
  fail=$((fail+1))
  {
    echo "FAIL a git ref is interpolated raw into a GitHub API path:"
    printf '%s\n' "$hits" | sed "s|^$PLUGIN_ROOT/||; s/^/       /"
    echo "       A '#' in a branch name opens a URL fragment and the request is"
    echo "       made against a truncated name, so GitHub 404s a branch that"
    echo "       exists (issue #762). Source <plugin root>/scripts/_gh-url.sh and"
    echo "       wrap the segment: \"repos/\$slug/branches/\$(gh_path_segment \"\$branch\")\"."
    echo "       When only existence is in question, call gh_branch_exists instead:"
    echo "       it lists and matches locally, so the name never enters a URL."
  } >&2
fi

echo "{\"passed\": $pass, \"failed\": $fail}"
[ "$fail" -eq 0 ]
