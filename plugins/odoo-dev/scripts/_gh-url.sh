# _gh-url.sh — sourced, never executed. Safe construction of GitHub API paths
# that carry a git ref.
#
# WHY. `gh api repos/<owner>/<repo>/branches/<branch>` must not be built by
# plain interpolation. A '#' in the branch name starts a URL FRAGMENT, so the
# request goes out against a TRUNCATED name and GitHub answers 404 for a branch
# that plainly exists. Quoting in the shell does not help: the truncation
# happens when the path is assembled into a URL, not when the shell splits
# words. A '/' is worse — it silently becomes a path separator and changes which
# endpoint is being called.
#
# Not hypothetical here. '#' in a branch name is a designed-in convention in
# these repos, not an edge case: skills/odoo-task-env/scripts/existing-work.sh
# matches '<task id>#<slug>' on purpose, with the comment that "'#' is what
# humans push", and its fixtures carry names like `29616#pr-branch`. A branch
# like that is a live element of a project's branch_flow, so the first
# `branches/<branch>` call written without an encode step would report a real
# branch as MISSING and a promotion as having nothing to ship. See issue #762,
# where exactly that happened during a branch-verification audit.
#
# NO CALLER IN THIS TREE TODAY, ON PURPOSE. Every GitHub API path the plugin
# currently builds interpolates a SHA (release-manifest.sh, from `git rev-list`)
# or a PR number (release-manifest.sh); branch names reach
# `gh` only as FLAG values — `gh pr list --head`, `gh pr create --base/--head`,
# `gh pr edit --base` — which gh encodes itself rather than pasting into a path.
# This file exists so the first call that does need a ref inside a path has a
# correct one to reach for instead of inventing it. scripts/tests/gh-url.test.sh
# both exercises it and lints the tree for the unencoded shape, so neither the
# helper nor the rule can rot unnoticed.
#
# PREFER gh_branch_exists WHEN ONLY EXISTENCE IS IN QUESTION. Listing branches
# and matching locally never puts the name in a URL at all, so there is no
# encoding left to get wrong.
#
# Sourced as:
#   . "<plugin root>/scripts/_gh-url.sh"

# gh_path_segment <value> -> percent-encoded value, safe as ONE path segment.
#
# Keeps the RFC 3986 unreserved set and escapes everything else, byte by byte,
# rather than blacklisting the characters known to hurt: a git ref legally
# admits '#', '%', '+', '&', '=', ',', ';', '(', ')', '|', '<', '>', '!', '$'
# and more, and a blacklist would have to be right about every one of them
# forever. A whitelist is wrong only in the harmless direction — it can
# over-encode, which the server decodes back to the same name.
#
# NEVER pass owner/repo through this. The '/' in a slug IS a path separator;
# encoding it to %2F addresses a repository that does not exist. Encode the
# segments, not the path:
#
#   gh api "repos/$slug/branches/$(gh_path_segment "$branch")"
gh_path_segment() {
  local value="${1-}" out="" i char hex
  # Byte-wise, not character-wise: a UTF-8 ref has to come out as one %XX per
  # byte, which is what the server decodes back. Without this the loop would
  # walk characters in a UTF-8 locale and emit one bogus escape per character.
  local LC_ALL=C
  for (( i = 0; i < ${#value}; i++ )); do
    char="${value:i:1}"
    case "$char" in
      [A-Za-z0-9._~-]) out+="$char" ;;
      *) printf -v hex '%02X' "'$char"; out+="%$hex" ;;
    esac
  done
  printf '%s' "$out"
}

# gh_branch_exists <owner/repo> <branch>
#   -> 0 the branch is on the remote | 1 it is not | 2 the listing failed
#
# Lists and matches locally rather than asking for the branch by name. That is
# one extra round trip on a large repo and it is worth it: the name never enters
# a URL, so no encoding mistake can turn "exists" into 404. Exit 2 is kept
# distinct from exit 1 because "GitHub did not answer" and "the branch is gone"
# lead to opposite decisions, and collapsing them is how an audit concludes a
# branch_flow is broken when the network merely hiccuped.
#
# The match is exact, whole-line and literal (-F -x): a substring or regex match
# would report '30412#fix' as present when only '30412#fix-2' exists, and '#'
# and '.' in a name are regex metacharacters waiting to do that quietly.
gh_branch_exists() {
  local slug="$1" branch="$2" names
  names="$(gh api "repos/$slug/branches" --paginate --jq '.[].name' 2>/dev/null)" || return 2
  printf '%s\n' "$names" | grep -Fxq -- "$branch"
}
