#!/usr/bin/env bash
# release-manifest.sh — what would actually ship if <from> were merged into <to>.
#
# Usage: release-manifest.sh <owner/repo> <repo_path> <from> <to> [--max-commits N]
#
# Read-only. Touches no branch, opens nothing, merges nothing.
#
# Chain: git log <to>..<from> gives the commits that <to> does not have; the
# GitHub commits/{sha}/pulls API is then asked which PRs touch each commit.
#
# That API does NOT return only "the PR that introduced this commit". It returns
# every PR whose head branch contains the sha, including OPEN ones that merely
# branched off the same history. Unfiltered it reports work that is not shipping:
# on a repo with 20 open branches cut from the release branch, a 14-commit delta
# came back as 27 PRs. So only PRs with a non-null merged_at are kept. If the PR
# count ever exceeds the commit count, that filter has regressed.
#
# The PR title carries "[task NNN]" written by odoo-pr. Modules come from the
# git trees, not from PR files, because what installs or updates is a property of
# the delta, not of who wrote it.
#
# Everything is derived, nothing is assumed. A PR whose title has no task id
# lands in "unresolved" rather than being guessed at — the note that tells a task
# it shipped must not go to the wrong task, and a release that silently omits
# work is worse than one that asks.
#
# Task ids come from three sources, in descending trust, and the manifest records
# WHICH one matched:
#   tag           "[task 30412]" — what odoo-pr writes. Canonical.
#   title-prefix  "30412#slug"   — the human convention in these repos.
#   branch        head "30412-slug" or "30412#slug".
# Only "tag" is safe to fan chatter out from unreviewed. The other two are
# inferences from a naming habit, so they are surfaced for confirmation rather
# than trusted: a release note posted to a wrong task id is a client-visible
# mistake that cannot be unsent.
#
# Modules are classified by scripts/module-classify.sh at the plugin root, which
# compares __manifest__.py across the two trees:
#   new      __manifest__.py exists in <from>, not in <to>   -> needs -i
#   updated  exists in both, files changed                   -> needs -u
#   removed  exists in <to>, gone in <from>                  -> OUT OF SCOPE
# Removed modules are reported and never turned into a command. Uninstalling is a
# database operation with data loss attached; it is a separate, human-run job.
#
# That classifier is shared with odoo-dev:odoo-pr rather than reimplemented here,
# so what a task PR says to install and what the release that ships it says to
# install cannot drift apart.
#
# "updated" modules also record whether the manifest version changed. On odoo.sh
# only a version bump triggers an automatic module update, so an unbumped module
# is code that deploys and does nothing until someone runs -u by hand.
#
# Last stdout line: {"from","to","commits","prs":[{"number","url","title",
#   "task_id","task_id_source","modules":[],"author","head"}],"unresolved":[],
#   "tasks":[],"inferred_task_ids":[],"modules":[],"modules_new":[],
#   "modules_updated":[{"name","version_from","version_to","bumped"}],
#   "modules_removed":[],"skipped_commits","table_md"}
#
# Exit codes: 0 ok | 2 usage | 5 empty delta (nothing to release)
set -euo pipefail

[ $# -ge 4 ] || { echo "usage: release-manifest.sh <owner/repo> <repo_path> <from> <to> [--max-commits N]" >&2; exit 2; }
slug="$1"; repo_path="$2"; from="$3"; to="$4"; shift 4
max_commits="${RELEASE_MAX_COMMITS:-500}"
while [ $# -gt 0 ]; do
  case "$1" in
    --max-commits) max_commits="${2:?}"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

git -C "$repo_path" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || { echo "not a git work tree: $repo_path" >&2; exit 2; }
command -v gh >/dev/null 2>&1 || { echo "gh is not installed" >&2; exit 2; }

git -C "$repo_path" fetch --quiet origin 2>/dev/null \
  || echo "WARN could not fetch origin — computing the delta from local refs only" >&2

# Remote-tracking refs first: the question is what origin/<from> has that
# origin/<to> lacks, and a stale local branch would answer a different question.
resolve_ref() {
  local name="$1" ref
  for ref in "refs/remotes/origin/$name" "refs/heads/$name"; do
    if git -C "$repo_path" show-ref --verify --quiet "$ref"; then
      printf '%s\n' "$ref"; return 0
    fi
  done
  echo "branch '$name' exists neither on origin nor locally in $repo_path" >&2
  return 2
}
from_ref="$(resolve_ref "$from")" || exit 2
to_ref="$(resolve_ref "$to")" || exit 2

mapfile -t shas < <(git -C "$repo_path" rev-list "$to_ref..$from_ref")
total="${#shas[@]}"
[ "$total" -gt 0 ] || {
  echo "nothing to release: $from is not ahead of $to" >&2
  exit 5
}

skipped=0
if [ "$total" -gt "$max_commits" ]; then
  skipped=$(( total - max_commits ))
  # Named, never silent: a truncated manifest that does not say so reads as
  # "this is everything".
  echo "WARN delta is $total commits; mapping the newest $max_commits, skipping $skipped (raise with --max-commits)" >&2
  shas=("${shas[@]:0:$max_commits}")
fi

# One API call per commit, so ask only about commits whose PR is still unknown.
seen_pr=""
pr_payloads="$(mktemp "${TMPDIR:-/tmp}/release-manifest.XXXXXX.jsonl")"
trap 'rm -f "$pr_payloads"' EXIT
for sha in "${shas[@]}"; do
  resp="$(gh api "repos/$slug/commits/$sha/pulls" --jq '.[] | {number, url: .html_url, title, author: .user.login, head: .head.ref, merged_at: .merged_at}' 2>/dev/null || true)"
  [ -n "$resp" ] || continue
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    # The merged-only filter lives here rather than in the --jq above so that it
    # is exercised by the tests: the test stub answers `gh` directly and never
    # evaluates a jq expression, so a filter written there is untested code.
    n="$(node -e 'const p = JSON.parse(process.argv[1]); console.log(p.merged_at ? p.number : "")' "$line")"
    [ -n "$n" ] || continue
    case ",$seen_pr," in *",$n,"*) continue ;; esac
    seen_pr="$seen_pr,$n"
    printf '%s\n' "$line" >> "$pr_payloads"
  done <<< "$resp"
done

# Modules from the PR's changed files: the top-level directory of a path in an
# addons repo IS the module. Commit scopes are a weaker second source — they are
# hand-written and go stale — so they are only consulted when a PR has no files.
files_map="$(mktemp "${TMPDIR:-/tmp}/release-files.XXXXXX.jsonl")"
trap 'rm -f "$pr_payloads" "$files_map"' EXIT
while IFS= read -r line; do
  [ -n "$line" ] || continue
  n="$(node -e 'console.log(JSON.parse(process.argv[1]).number)' "$line")"
  paths="$(gh api "repos/$slug/pulls/$n/files" --paginate --jq '.[].filename' 2>/dev/null | head -300 || true)"
  node -e '
    const [n, raw] = process.argv.slice(1);
    const mods = [...new Set(raw.split("\n").filter(Boolean)
      .map((p) => p.split("/")[0])
      .filter((d) => d && !d.startsWith(".") && !d.includes(".")))];
    console.log(JSON.stringify({ number: Number(n), modules: mods }));
  ' "$n" "$paths" >> "$files_map"
done < "$pr_payloads"

# Module classification from the two trees. Deliberately NOT from the PR file
# lists: a module can be added by one PR and removed by another in the same
# delta, and only the trees know the net result. In release terms <to> is the
# base and <from> is the head, which is why the arguments look reversed here.
plugin_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
mod_json="$("$plugin_root/scripts/module-classify.sh" "$repo_path" "$to" "$from" | tail -1)"

node -e '
  const fs = require("fs");
  const [prsPath, filesPath, from, to, commits, skipped, modsJson] = process.argv.slice(1);
  const readJsonl = (p) => fs.readFileSync(p, "utf8").split("\n").filter((l) => l.trim()).map((l) => JSON.parse(l));

  const modulesBy = new Map(readJsonl(filesPath).map((r) => [r.number, r.modules]));
  // Descending trust. "tag" is what odoo-pr writes and is the only source safe
  // to act on unreviewed; the others are inferences from a naming habit.
  const SOURCES = [
    ["tag",          (pr) => /\[task (\d+)\]/i.exec(pr.title || "")],
    ["title-prefix", (pr) => /^(\d{3,})[#-]/.exec(pr.title || "")],
    ["branch",       (pr) => /^(\d{3,})[#-]/.exec(pr.head || "")],
  ];

  const prs = [];
  const unresolved = [];
  for (const pr of readJsonl(prsPath)) {
    let taskId = null, source = null;
    for (const [name, match] of SOURCES) {
      const m = match(pr);
      if (m) { taskId = m[1]; source = name; break; }
    }
    const row = {
      number: pr.number, url: pr.url, title: pr.title, author: pr.author ?? null,
      head: pr.head ?? null, task_id: taskId, task_id_source: source,
      modules: modulesBy.get(pr.number) ?? [],
    };
    (taskId ? prs : unresolved).push(row);
  }
  const byNumber = (a, b) => a.number - b.number;
  prs.sort(byNumber); unresolved.sort(byNumber);

  const tasks = [...new Set(prs.map((p) => p.task_id))];
  const inferred = [...new Set(prs.filter((p) => p.task_id_source !== "tag").map((p) => p.task_id))];
  const modules = [...new Set([...prs, ...unresolved].flatMap((p) => p.modules))].sort();

  // A markdown pipe inside a title would split the cell and shift every column
  // after it.
  const cell = (s) => String(s ?? "").replace(/\|/g, "\\|");
  const summary = (t) => cell(String(t).replace(/\s*\[task \d+\]\s*$/i, "").trim());
  const rows = [
    "| Task | PR | Module(s) | Summary |",
    "| --- | --- | --- | --- |",
    ...prs.map((p) => {
      // The marker flags an id that was inferred rather than written.
      const id = p.task_id_source === "tag" ? p.task_id : `${p.task_id} ?`;
      return `| ${id} | [#${p.number}](${p.url}) | ${p.modules.join(", ") || "n/a"} | ${summary(p.title)} |`;
    }),
    ...unresolved.map((p) => `| _unresolved_ | [#${p.number}](${p.url}) | ${p.modules.join(", ") || "n/a"} | ${summary(p.title)} |`),
  ];

  // module-classify.sh already sorted every list and worked out the bumps; this
  // only renames its fields onto the published manifest shape.
  const mods = JSON.parse(modsJson);
  const modulesNew = mods.install;
  const modulesRemoved = mods.removed;
  const modulesUpdated = mods.details.filter((x) => x.status === "update").map((x) => ({
    name: x.name,
    version_from: x.version_from,
    version_to: x.version_to,
    bumped: x.bumped,
  }));

  console.log(JSON.stringify({
    from, to,
    commits: Number(commits),
    prs, unresolved, tasks, inferred_task_ids: inferred, modules,
    modules_new: modulesNew,
    modules_updated: modulesUpdated,
    modules_removed: modulesRemoved,
    skipped_commits: Number(skipped),
    table_md: rows.join("\n"),
  }));
' "$pr_payloads" "$files_map" "$from" "$to" "$total" "$skipped" "$mod_json"
