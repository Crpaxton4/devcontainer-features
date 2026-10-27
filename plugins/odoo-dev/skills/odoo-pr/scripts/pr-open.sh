#!/usr/bin/env bash
# pr-open.sh — push a task branch and open (or update) its pull request.
#
# Usage: pr-open.sh <worktree> <owner/repo> <base> --title T --body-file F
#                   [--assign-me] [--no-push] [--no-bump]
#
# Before the push it bumps the manifest version ONCE PER PR (there is no per-commit
# hook). Every module that <base>...HEAD touches and whose version has not moved since
# the merge-base is bumped with bump_manifest_version.py, and one commit
# `chore(<modules>): bump manifest versions` carries the bumps. --no-bump skips this.
# A module whose files are all deleted is never bumped. When the task's upgrade state
# (task.json) does not mark it `dropped`, a `> Warning:` line is appended to the body
# file instead; it never refuses.
#
# Rerun-safe by construction: it asks GitHub for an existing PR on this head
# FIRST and edits that one rather than opening a second. A task that gets a
# second PR loses its review history and its CodeRabbit thread.
#
# <base> is passed explicitly and is never inferred. The GitHub default branch is
# routinely NOT where a task PR belongs — one live client repo defaults to Odoov18
# while its PRs target staging — so the base comes from odoo-repo-map's branch_flow.
#
# --body-file rather than an inline body: PR bodies are multi-line markdown with
# backticks and pipes, and passing that through a shell argument is how a body
# ends up truncated at the first special character. An empty file is exit 5,
# because a PR with an empty body is worse than no PR.
#
# Last stdout line: {"pr_url","pr_number","action":"created"|"updated","draft",
#                    "base","head","assigned"}
# Exit codes: 0 ok | 2 usage | 3 manifest bump failed | 5 body file missing or empty
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUMP_PY="$HERE/../../odoo-devcontainer/scripts/bump_manifest_version.py"
JSON_GET="$HERE/../../../scripts/json-get.sh"

[ $# -ge 3 ] || { echo "usage: pr-open.sh <worktree> <owner/repo> <base> --title T --body-file F [--assign-me] [--no-push] [--no-bump]" >&2; exit 2; }
worktree="$1"; slug="$2"; base="$3"; shift 3

title=""; body_file=""; assign_me=false; do_push=true; do_bump=true
while [ $# -gt 0 ]; do
  case "$1" in
    --title)     title="${2:?}"; shift 2 ;;
    --body-file) body_file="${2:?}"; shift 2 ;;
    # --draft is accepted and ignored: PRs are always drafts. There is no
    # --ready, because lifting the draft is a human gate.
    --draft)     shift ;;
    --assign-me) assign_me=true; shift ;;
    --no-push)   do_push=false; shift ;;
    --no-bump)   do_bump=false; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[ -n "$title" ] || { echo "--title is required" >&2; exit 2; }
[ -n "$body_file" ] || { echo "--body-file is required" >&2; exit 2; }
[ -s "$body_file" ] || { echo "body file is missing or empty: $body_file" >&2; exit 5; }
[ -d "$worktree" ] || { echo "not a directory: $worktree" >&2; exit 2; }
git -C "$worktree" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || { echo "not a git work tree: $worktree" >&2; exit 2; }
command -v gh >/dev/null 2>&1 || { echo "gh is not installed" >&2; exit 2; }

head="$(git -C "$worktree" rev-parse --abbrev-ref HEAD)"
[ "$head" != "HEAD" ] || { echo "worktree is in detached HEAD state: $worktree" >&2; exit 2; }
[ "$head" != "$base" ] || { echo "refusing: head and base are both '$base' — a PR needs two different branches" >&2; exit 2; }

# manifest_version — the version string of an Odoo manifest read on stdin, or nothing.
# Both quote styles appear in the wild; the first `version` key wins.
manifest_version() {
  sed -n -E "s/.*['\"]version['\"][[:space:]]*:[[:space:]]*['\"]([^'\"]+)['\"].*/\1/p" | head -1 || true
}

# upgrade_status <module> — the module's status in the upgrade section of the task
# state, or nothing when no task.json carries an upgrade section for this worktree.
# The state is $ODOO_DEV_TASK_ARTIFACTS/task.json when set, else the task.json under
# ${ODOO_DEV_HOME:-$HOME/.config/odoo-dev}/tasks whose upgrade.inputs.worktree matches.
upgrade_status() {
  local wt_real f out files=()
  wt_real="$(cd "$worktree" && pwd -P)"
  if [ -n "${ODOO_DEV_TASK_ARTIFACTS:-}" ]; then
    files=("$ODOO_DEV_TASK_ARTIFACTS/task.json"); wt_real=""
  else
    for f in "${ODOO_DEV_HOME:-$HOME/.config/odoo-dev}"/tasks/*/task.json; do files+=("$f"); done
  fi
  for f in "${files[@]}"; do
    [ -f "$f" ] || continue
    out="$(bash "$JSON_GET" '(d.upgrade && d.upgrade.inputs && (a.wt === "" || d.upgrade.inputs.worktree === a.wt)) ? String(((d.upgrade.modules || {})[a.m] || {}).status || "unknown") : null' \
      "m=$1" "wt=$wt_real" < "$f" 2>/dev/null || true)"
    if [ -n "$out" ]; then printf '%s' "$out"; return; fi
  done
}

# bump_manifests — once per PR: bump each touched module whose version has not moved
# since the merge-base, make one commit, and warn in the body about a module deleted
# without a dropped entry. Silent when base...HEAD touches no module, or base is unknown.
bump_manifests() {
  local ref="" r mb path dir mod modules="" bumped=() scopes="" v_base v_head status
  for r in "origin/$base" "$base"; do
    if git -C "$worktree" rev-parse --verify -q "$r^{commit}" >/dev/null 2>&1; then ref="$r"; break; fi
  done
  [ -n "$ref" ] || return 0
  mb="$(git -C "$worktree" merge-base "$ref" HEAD 2>/dev/null)" || return 0
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    dir="$(dirname "$path")"
    while [ "$dir" != "." ] && [ "$dir" != "/" ]; do
      if git -C "$worktree" cat-file -e "HEAD:$dir/__manifest__.py" 2>/dev/null \
         || git -C "$worktree" cat-file -e "$mb:$dir/__manifest__.py" 2>/dev/null; then
        modules="$modules$dir"$'\n'; break
      fi
      dir="$(dirname "$dir")"
    done
  done < <(git -C "$worktree" diff --name-only "$ref...HEAD")
  modules="$(printf '%s' "$modules" | sed '/^$/d' | sort -u)"
  [ -n "$modules" ] || return 0

  while IFS= read -r mod; do
    v_base="$(git -C "$worktree" show "$mb:$mod/__manifest__.py" 2>/dev/null | manifest_version)"
    [ -n "$v_base" ] || continue                       # a new module has nothing to move away from
    if [ -z "$(git -C "$worktree" ls-tree -r --name-only HEAD -- "$mod")" ]; then
      status="$(upgrade_status "$mod")"
      if [ -n "$status" ] && [ "$status" != dropped ]; then
        printf '\n> Warning: %s deleted without a dropped entry in the upgrade state\n' "$mod" >> "$body_file"
      fi
      continue
    fi
    v_head="$(git -C "$worktree" show "HEAD:$mod/__manifest__.py" 2>/dev/null | manifest_version)"
    [ "$v_head" = "$v_base" ] || continue              # the version already moved (or is unreadable)
    python3 "$BUMP_PY" "$worktree/$mod" >&2 || { echo "manifest bump failed for $mod" >&2; exit 3; }
    git -C "$worktree" add -- "$mod/__manifest__.py"
    bumped+=("$mod"); scopes="${scopes:+$scopes,}$(basename "$mod")"
  done <<EOF
$modules
EOF
  [ "${#bumped[@]}" -eq 0 ] || git -C "$worktree" commit -q -m "chore($scopes): bump manifest versions" >&2
}

[ "$do_bump" = false ] || bump_manifests

if [ "$do_push" = true ]; then
  # -u so later pushes from this worktree need no arguments, and so `gh pr list
  # --head` resolves against a branch that actually exists on the remote.
  git -C "$worktree" push -u origin "HEAD:$head" >&2
fi

existing="$(gh pr list --repo "$slug" --head "$head" --state open \
  --json number,url,isDraft --limit 1 2>/dev/null || echo '[]')"
pr_number="$(node -e '
  const a = JSON.parse(process.argv[1] || "[]");
  console.log(a.length ? a[0].number : "");
' "$existing")"

if [ -n "$pr_number" ]; then
  action="updated"
  gh pr edit "$pr_number" --repo "$slug" --title "$title" --body-file "$body_file" --base "$base" >&2
  # Never `gh pr ready`. Lifting the draft is how a human signals they have read
  # the PR and accepted it; the tool that wrote it must not also clear that gate.
  # A PR a human already marked ready is likewise never dragged back to draft.
else
  action="created"
  create_args=(--repo "$slug" --base "$base" --head "$head" --title "$title" --body-file "$body_file")
  create_args+=(--draft)   # always; not a flag
  [ "$assign_me" = true ] && create_args+=(--assignee @me)
  gh pr create "${create_args[@]}" >&2
  pr_number="$(gh pr list --repo "$slug" --head "$head" --state open --json number --limit 1 \
    | node -e 'const a=JSON.parse(require("fs").readFileSync(0,"utf8"));console.log(a.length?a[0].number:"")')"
  [ -n "$pr_number" ] || { echo "PR was created but could not be found again for head '$head'" >&2; exit 2; }
fi

# Assignment is applied on both paths and is idempotent: a rerun that only
# updated the body must still leave the PR assigned.
assigned=false
if [ "$assign_me" = true ]; then
  gh pr edit "$pr_number" --repo "$slug" --add-assignee @me >&2 && assigned=true || true
fi

final="$(gh pr view "$pr_number" --repo "$slug" --json url,number,isDraft,baseRefName,headRefName)"
node -e '
  const [raw, action, assigned] = process.argv.slice(1);
  const pr = JSON.parse(raw);
  console.log(JSON.stringify({
    pr_url: pr.url, pr_number: pr.number, action,
    draft: pr.isDraft, base: pr.baseRefName, head: pr.headRefName,
    assigned: assigned === "true",
  }));
' "$final" "$action" "$assigned"
