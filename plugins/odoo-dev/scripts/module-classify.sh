#!/usr/bin/env bash
# module-classify.sh — which Odoo modules a diff installs, updates, or removes.
#
# Usage: module-classify.sh <repo_path> <base> <head>
#
# Read-only. Touches no branch, checks nothing out, opens nothing.
#
# A module is a directory carrying __manifest__.py, and the manifest is the
# decisive test because the manifest is exactly what Odoo keys on: -i for a module
# the database has never seen, -u for one it already has installed. Mis-sorting a
# module is not a cosmetic error. It is either a module that never installs, or a
# needless full update run against a production database.
#
# Status therefore comes from whether the manifest EXISTS at each ref, never from
# the add/modify letters in the diff. A module whose __manifest__.py was modified
# rather than added is an update, and no diff letter can tell those two apart:
#
#   present at head, absent at base   -> install
#   present at both                   -> update
#   present at base, absent at head   -> removed
#   present at neither                -> not a module; dropped
#
# Removed modules get no command. Deleting a directory removes code and does not
# uninstall anything; see odoo-release/references/module-commands.md.
#
# Candidates come from `git diff --name-status <base>...<head>` — three dots, so
# the comparison starts at the merge base. That is what a pull request actually
# proposes, rather than every difference between two moving branches.
#
# Both odoo-dev:odoo-pr and odoo-dev:odoo-release call this one script, so what a
# PR says to install and what the release that ships it says to install cannot
# drift apart.
#
# Last stdout line:
#   {"install":[],"update":[],"removed":[],
#    "details":[{"name","status","version_from","version_to","bumped"}]}
#
# version_from is the manifest version on <base>, version_to the version on
# <head>, and bumped is true only when both exist and differ. On odoo.sh an
# unbumped module deploys and does nothing until someone runs -u by hand.
#
# Exit codes: 0 ok | 2 usage, or not a git work tree, or an unresolvable ref
set -euo pipefail

[ $# -eq 3 ] || { echo "usage: module-classify.sh <repo_path> <base> <head>" >&2; exit 2; }
repo_path="$1"; base="$2"; head="$3"

git -C "$repo_path" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || { echo "not a git work tree: $repo_path" >&2; exit 2; }

# Remote-tracking refs first: the question is what origin/<head> carries that
# origin/<base> lacks, and a stale local branch would answer a different one. A
# raw sha or tag is accepted as given, so a detached PR head still classifies.
resolve_ref() {
  local name="$1" ref
  for ref in "refs/remotes/origin/$name" "refs/heads/$name"; do
    if git -C "$repo_path" show-ref --verify --quiet "$ref"; then
      printf '%s\n' "$ref"; return 0
    fi
  done
  if git -C "$repo_path" rev-parse --verify --quiet "$name^{commit}" >/dev/null 2>&1; then
    printf '%s\n' "$name"; return 0
  fi
  echo "ref '$name' exists neither on origin nor locally in $repo_path" >&2
  return 2
}
base_ref="$(resolve_ref "$base")" || exit 2
head_ref="$(resolve_ref "$head")" || exit 2

mods="$(mktemp "${TMPDIR:-/tmp}/module-classify.XXXXXX.jsonl")"
trap 'rm -f "$mods"' EXIT

manifest_at() { git -C "$repo_path" show "$1:$2/__manifest__.py" 2>/dev/null || true; }

# --name-status puts the status letter first and a rename puts TWO paths after
# it, so every field but the first is a path worth looking at.
while IFS= read -r dir; do
  [ -n "$dir" ] || continue
  # Dotfiles and anything with a dot in the name are never module directories.
  case "$dir" in .*|*.*) continue ;; esac
  m_base="$(manifest_at "$base_ref" "$dir")"
  m_head="$(manifest_at "$head_ref" "$dir")"
  [ -n "$m_base$m_head" ] || continue
  node -e '
    const [name, mBase, mHead] = process.argv.slice(1);
    // Both quote styles appear in the wild; take the first "version" key.
    const ver = (s) => (/["\x27]version["\x27]\s*:\s*["\x27]([^"\x27]+)["\x27]/.exec(s || "") || [])[1] || null;
    const inBase = Boolean(mBase), inHead = Boolean(mHead);
    const status = inHead && !inBase ? "install" : inBase && !inHead ? "removed" : "update";
    const versionFrom = ver(mBase), versionTo = ver(mHead);
    console.log(JSON.stringify({
      name, status,
      version_from: versionFrom,
      version_to: versionTo,
      // No bump means odoo.sh deploys the code and runs no update: the change is
      // on disk and inert until someone passes -u.
      bumped: Boolean(versionFrom && versionTo && versionFrom !== versionTo),
    }));
  ' "$dir" "$m_base" "$m_head" >> "$mods"
done < <(git -C "$repo_path" diff --name-status "$base_ref...$head_ref" \
         | awk -F'\t' '{ for (i = 2; i <= NF; i++) { n = split($i, p, "/"); if (n > 1) print p[1] } }' \
         | sort -u)

node -e '
  const fs = require("fs");
  const path = process.argv[1];
  const rows = fs.readFileSync(path, "utf8").split("\n").filter((l) => l.trim()).map((l) => JSON.parse(l));
  rows.sort((a, b) => a.name.localeCompare(b.name));
  const named = (status) => rows.filter((r) => r.status === status).map((r) => r.name);
  console.log(JSON.stringify({
    install: named("install"),
    update: named("update"),
    removed: named("removed"),
    details: rows,
  }));
' "$mods"
