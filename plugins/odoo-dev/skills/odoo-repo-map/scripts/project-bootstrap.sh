#!/usr/bin/env bash
# project-bootstrap.sh — record the mechanically derivable half of a repo-map
# entry for a checkout nobody has mapped yet, so an unmapped project is a PHASE
# of a run rather than the end of it (#995).
#
# Usage: project-bootstrap.sh "<checkout path>" [--project "<project name>"]
#
# project-resolve.sh exiting 3 (unmapped) used to end the release route with
# nothing done, and the entry then got written by hand — which the skill forbids
# and which, observed in the field, produced a second entry whose repo_path
# already belonged to another project. Everything that stop was waiting on,
# except the branch chain, is readable off the checkout itself:
#
#   repo           basename of the checkout path (the map's lookup key)
#   repo_path      the checkout, absolutised
#   remote         owner/repo parsed out of `git remote get-url origin`
#   odoo_version   the majority NN.0 series across the checkout's
#                  __manifest__.py files; failing that, the checked-out branch
#                  name when it is itself spelled NN.0; failing that, omitted
#   default_branch origin/HEAD when the clone records one, else the checked-out
#                  branch; omitted when neither resolves (a detached HEAD)
#   project        --project, defaulting to repo
#
# What it deliberately does NOT derive is branch_flow. The chain is the one field
# whose last element points a release at a customer, so it stays unset, which
# leaves flow_confirmed false and leaves project-resolve.sh's exit-4 guard to
# force a human to vouch for it. Bootstrapping is not confirming.
#
# The write goes through `repo-map.sh add`, never to the file: the atomic
# validate -> .bak -> rename path and the one-checkout-one-entry rule are both
# that script's, and this one has no business reimplementing either.
#
# A checkout whose repo_path is ALREADY on an entry is not re-added. That is the
# bind-mount case the devcontainer actually has — repo_path /mnt/extra-addons on
# an entry whose `repo` can never equal `extra-addons`, so resolving by folder
# name misses it and it looks unmapped. Exit 3 prints the entry that owns the
# path, which is the project name the caller should resolve by.
#
# Last stdout line: the resulting map entry, `repo-map.sh get` verbatim —
# {"project","repo","repo_path",...}
#
# Exit codes: 0 entry written | 2 usage, or the path is not a git checkout
#           | 3 this checkout is already mapped (its entry is printed)
#           | anything else is repo-map.sh's own code, passed through unchanged
#             (2 duplicate project name or colliding repo_path, 4 validation)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() { echo "project-bootstrap.sh: $*" >&2; exit 2; }

[ $# -ge 1 ] || die "usage: project-bootstrap.sh \"<checkout path>\" [--project \"<project name>\"]"
checkout="$1"; shift
project=""
while [ $# -gt 0 ]; do
  case "$1" in
    --project) project="${2:?}"; shift 2 ;;
    *) die "unknown option: $1" ;;
  esac
done

[ -d "$checkout" ] || die "checkout not found: $checkout"
# Absolutised here rather than trusted as typed, because repo_path is only ever
# useful absolute and a relative one would resolve against whatever directory the
# caller happened to be standing in — the guesswork the map exists to stop.
repo_path="$(cd "$checkout" && pwd)"
repo="$(basename "$repo_path")"
[ -n "$repo" ] && [ "$repo" != "/" ] || die "cannot derive a repo folder name from: $checkout"
git -C "$repo_path" rev-parse --git-dir >/dev/null 2>&1 \
  || die "not a git checkout: $repo_path (repo, remote and branch are all read from git)"

# --- already mapped? ----------------------------------------------------------
# Asked before anything is derived: adding a second entry for a checkout that
# already has one is the exact defect this script exists to stop happening by
# hand, and the answer the caller needs is the project name, not a new entry.
existing="$(bash "$SCRIPT_DIR/repo-map.sh" list)"
owner="$(node --input-type=module -e '
  const [listRaw, repoPath] = process.argv.slice(1);
  const projects = JSON.parse(listRaw);
  const hit = Object.entries(projects).find(([, e]) => e.repo_path === repoPath);
  if (hit) console.log(hit[0]);
' -- "$existing" "$repo_path")"
if [ -n "$owner" ]; then
  echo "project-bootstrap.sh: $repo_path is already the repo_path of project \"$owner\" — nothing to bootstrap; resolve by that project name: project-resolve.sh \"$owner\"" >&2
  bash "$SCRIPT_DIR/repo-map.sh" get "$owner"
  exit 3
fi

[ -n "$project" ] || project="$repo"

# --- remote -------------------------------------------------------------------
# Same owner/repo shape project-resolve.sh sniffs, and left empty rather than
# guessed when the clone has no origin: an absent remote is a field a human can
# fill, a wrong one sends a release PR at somebody else's repository.
remote="$(git -C "$repo_path" remote get-url origin 2>/dev/null || true)"
if [ -n "$remote" ]; then
  remote="$(node -e '
    const url = process.argv[1].replace(/\.git$/, "");
    const m = url.match(/[:/]([^/:]+\/[^/]+)$/);
    if (m) console.log(m[1]);
  ' "$remote")"
fi

# --- branch -------------------------------------------------------------------
# origin/HEAD first: it is what the remote says its default is. The checked-out
# branch is the fallback because a checkout sitting on the series branch is the
# common case, and "HEAD" (detached) is not a branch name at all.
default_branch="$(git -C "$repo_path" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
default_branch="${default_branch#origin/}"
# symbolic-ref rather than `rev-parse --abbrev-ref`: it names the branch even on
# an unborn one (a fresh clone with no commit yet), and it fails outright on a
# detached HEAD instead of handing back the literal string "HEAD" as if that
# were a branch somebody could target.
current_branch="$(git -C "$repo_path" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
[ -n "$default_branch" ] || default_branch="$current_branch"

# --- odoo_version -------------------------------------------------------------
# The series is a property of the code, so it is counted off the manifests rather
# than read off one of them: a repo carrying a vendored addon on another series
# would otherwise decide the whole project's version. Majority wins; a tie is no
# answer and the field is omitted rather than guessed.
odoo_version="$(node --input-type=module -e '
  import { readFileSync, readdirSync, statSync } from "fs";
  import { join } from "path";
  const [root, branch] = process.argv.slice(1);
  const files = [];
  // Bounded walk: a manifest lives at the top of a module directory, so three
  // levels reaches a flat addons repo and a repo that groups them one deep,
  // without descending a node_modules or a vendored Odoo source tree.
  const walk = (dir, depth) => {
    if (depth > 3 || files.length > 500) return;
    let names;
    try { names = readdirSync(dir); } catch { return; }
    for (const n of names) {
      if (n === ".git" || n === "node_modules") continue;
      const p = join(dir, n);
      let st;
      try { st = statSync(p); } catch { continue; }
      if (st.isDirectory()) walk(p, depth + 1);
      else if (n === "__manifest__.py") files.push(p);
    }
  };
  walk(root, 0);
  const counts = new Map();
  for (const f of files) {
    let txt;
    try { txt = readFileSync(f, "utf8"); } catch { continue; }
    const m = txt.match(/["\x27]version["\x27]\s*:\s*["\x27]([^"\x27]+)["\x27]/);
    if (!m) continue;
    const v = m[1].match(/^(\d{1,2}\.0)(?:\.|$)/);
    if (v) counts.set(v[1], (counts.get(v[1]) || 0) + 1);
  }
  const ranked = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  if (ranked.length === 1 || (ranked.length > 1 && ranked[0][1] > ranked[1][1])) {
    console.log(ranked[0][0]);
  } else if (/^\d{1,2}\.0$/.test(branch)) {
    // No manifest answer: a branch literally named for the series is the one
    // other mechanical source, and it is the Odoo convention for a client repo.
    console.log(branch);
  }
' -- "$repo_path" "$current_branch")"

# --- write it -----------------------------------------------------------------
args=(add "$project" "$repo" --repo-path "$repo_path")
[ -n "$default_branch" ] && args+=(--default-branch "$default_branch")
[ -n "$odoo_version" ] && args+=(--odoo-version "$odoo_version")
[ -n "$remote" ] && args+=(--remote "$remote")
args+=(--notes "Bootstrapped by project-bootstrap.sh from the checkout: every field was derived mechanically and nobody has vouched for a branch chain yet. Record one with set-flow --flow-confirmed.")

# A repo folder name two projects answer to is legal and normal (a delivery
# project and its upgrade project), but it makes resolving BY FOLDER ambiguous
# from here on, so it is said out loud at the moment it is created.
node --input-type=module -e '
  const [listRaw, repo] = process.argv.slice(1);
  const projects = JSON.parse(listRaw);
  const same = Object.entries(projects).filter(([, e]) => (e.repo || "").toLowerCase() === repo.toLowerCase());
  if (same.length) {
    console.error("project-bootstrap.sh: repo folder \"" + repo + "\" is already on " + same.length +
      " entry/entries (" + same.map(([n]) => n).join(", ") + ") — resolving by folder name will be ambiguous" +
      " (project-resolve.sh exit 6); resolve by project name instead");
  }
' -- "$existing" "$repo"

bash "$SCRIPT_DIR/repo-map.sh" "${args[@]}" >&2
bash "$SCRIPT_DIR/repo-map.sh" get "$project"
