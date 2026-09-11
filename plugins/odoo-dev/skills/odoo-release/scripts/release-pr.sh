#!/usr/bin/env bash
# release-pr.sh — open (or update) the DRAFT aggregation PR that proposes
# promoting <from> into <to>.
#
# Usage: release-pr.sh <owner/repo> <from> <to> --manifest-file F
#                      --assignee LOGIN --reviewer LOGIN|none
#                      [--hosting odoo-sh|on-prem] [--database DB] [--title T]
#
# This script opens pull requests. It does not approve, does not mark ready for
# review, and does not merge. Those are human gates and there is no flag here
# that crosses them.
#
# The PR is ALWAYS a draft. Lifting the draft is the signal a human has read the
# table and accepted it, so the tool that writes the table must not also be the
# thing that clears it.
#
# --assignee and --reviewer are required, with no default. An aggregation PR
# that names nobody sits until someone stumbles on it. They are a property of the
# project, so the caller reads them from the repo map — `release_assignee` and
# `release_reviewer`, set once per project — rather than guessing them from
# commit authorship. Pass `none` to record a deliberate absence a human chose.
#
# Body is built from the manifest so the PR and the manifest cannot drift.
#
# Rerun-safe: an existing open PR for this branch pair is EDITED, never
# duplicated. A release that opens a second PR splits its discussion across two
# threads.
#
# Last stdout line: {"pr_url","pr_number","action":"created"|"updated","draft",
#                    "from","to","tasks","unresolved","assignee","reviewer"}
# Exit codes: 0 ok | 2 usage | 5 manifest missing/empty/unparseable
set -euo pipefail

usage() {
  echo "usage: release-pr.sh <owner/repo> <from> <to> --manifest-file F --assignee LOGIN --reviewer LOGIN|none [--hosting odoo-sh|on-prem] [--database DB] [--title T]" >&2
}

[ $# -ge 3 ] || { usage; exit 2; }
slug="$1"; from="$2"; to="$3"; shift 3
manifest=""; title=""; assignee=""; reviewer=""; hosting="odoo-sh"; database=""; dry_run=false
while [ $# -gt 0 ]; do
  case "$1" in
    --manifest-file) manifest="${2:?}"; shift 2 ;;
    --title)         title="${2:?}"; shift 2 ;;
    --assignee)      assignee="${2:?}"; shift 2 ;;
    --reviewer)      reviewer="${2:?}"; shift 2 ;;
    --hosting)       hosting="${2:?}"; shift 2 ;;
    --database)      database="${2:?}"; shift 2 ;;
    --dry-run)       dry_run=true; shift ;;
    *) echo "unknown option: $1" >&2; usage; exit 2 ;;
  esac
done
[ -n "$manifest" ] || { echo "--manifest-file is required" >&2; exit 2; }
[ -s "$manifest" ] || { echo "manifest file is missing or empty: $manifest" >&2; exit 5; }
# Required so that "who owns this" is answered by a human, not by a default.
[ -n "$assignee" ] || { echo "--assignee is required: read release_assignee from the repo map, or have a human run repo-map.sh set-release-owners" >&2; exit 2; }
[ -n "$reviewer" ] || { echo "--reviewer is required: read release_reviewer from the repo map, or have a human run repo-map.sh set-release-owners ('none' records a deliberate absence)" >&2; exit 2; }
case "$hosting" in
  odoo-sh) ;;
  on-prem) [ -n "$database" ] || { echo "--database is required with --hosting on-prem, so the commands in the PR are copy-pastable rather than templated" >&2; exit 2; } ;;
  *) echo "--hosting must be odoo-sh or on-prem" >&2; exit 2 ;;
esac
command -v gh >/dev/null 2>&1 || { echo "gh is not installed" >&2; exit 2; }

body_file="$(mktemp "${TMPDIR:-/tmp}/release-body.XXXXXX.md")"
trap 'rm -f "$body_file"' EXIT

# Passed in rather than computed in node so the date is the shell's, and one
# release cannot end up stamped with two different days.
today="$(date -u +%Y-%m-%d)"

node -e '
  const fs = require("fs");
  const [manifestPath, from, to, today, hosting, database, out] = process.argv.slice(1);
  let m;
  try { m = JSON.parse(fs.readFileSync(manifestPath, "utf8")); }
  catch (e) { console.error("unparseable manifest: " + e.message); process.exit(5); }
  if (!m.table_md) { console.error("manifest has no table_md"); process.exit(5); }

  const prCount = (m.prs?.length ?? 0) + (m.unresolved?.length ?? 0);
  const lines = [
    `## Release: \`${from}\` -> \`${to}\``,
    "",
    `Prepared ${today} · ${prCount} pull request(s) · ${m.commits} commit(s)`,
    "",
    "Draft on purpose. Lifting the draft, approving and merging are human steps.",
    "",
    "### Included work",
    "",
    m.table_md,
    "",
  ];

  // Surfaced in the PR itself, not only in the terminal that built it: whoever
  // reads this needs to see what could not be attributed.
  const inferred = m.inferred_task_ids ?? [];
  if (inferred.length) {
    lines.push("### Task ids marked `?`", "",
      "Inferred from the PR title or branch name, not from a `[task NNN]` tag. They ship, and they receive no release note.",
      "", ...inferred.map((t) => `- ${t}`), "");
  }
  if (m.unresolved?.length) {
    lines.push("### No task id", "",
      "These carry no derivable task id. They ship, and they receive no release note.",
      "", ...m.unresolved.map((p) => `- [#${p.number}](${p.url}) ${String(p.title).replace(/\|/g, "\\|")}`), "");
  }
  if (m.skipped_commits) {
    lines.push("### Truncated", "",
      `${m.skipped_commits} older commit(s) were not mapped to pull requests (--max-commits). This table is not the complete delta.`, "");
  }

  // ---- module install / update -------------------------------------------
  // Exact, copy-pastable, no placeholders. A command a reader has to edit is a
  // command a reader gets wrong.
  const isSh = hosting === "odoo-sh";
  const bin = isSh ? "odoo-bin" : `sudo -u odoo odoo -c /etc/odoo/odoo.conf -d ${database}`;
  const nw = m.modules_new ?? [];
  const up = m.modules_updated ?? [];
  const rm = m.modules_removed ?? [];

  lines.push("### Modules", "");
  if (!nw.length && !up.length && !rm.length) {
    lines.push("No module is added, updated or removed by this delta. Nothing to install or update.", "");
  } else {
    if (nw.length) {
      lines.push("**New — install:**", "", "```bash", `${bin} -i ${nw.join(",")} --stop-after-init`, "```", "");
    }
    if (up.length) {
      lines.push("**Existing — update:**", "", "```bash", `${bin} -u ${up.map((x) => x.name).join(",")} --stop-after-init`, "```", "");
      const rows = ["| Module | On `" + to + "` | Arriving | Auto-updates on merge |", "| --- | --- | --- | --- |",
        ...up.map((x) => `| \`${x.name}\` | ${x.version_from ?? "n/a"} | ${x.version_to ?? "n/a"} | ${x.bumped ? "yes" : "**no — version not bumped**"} |`)];
      lines.push(...rows, "");
      if (isSh && up.some((x) => !x.bumped)) {
        lines.push("Modules marked **no** carry no manifest version bump, so odoo.sh deploys the code without running an update. They stay inert until the `-u` above is run.", "");
      }
    }
    if (rm.length) {
      lines.push("**Removed — out of scope for this PR:**", "",
        rm.map((x) => `\`${x}\``).join(", "), "",
        "Deleting the code does not uninstall the module. Uninstalling drops its tables and data, so it is a separate, deliberate, human-run job and no command for it is generated here.", "");
    }
  }

  fs.writeFileSync(out, lines.join("\n"));
' "$manifest" "$from" "$to" "$today" "$hosting" "$database" "$body_file"

[ -n "$title" ] || title="release($to): $from -> $to ($today)"

# Print the exact body and open nothing, so the whole PR can be shown to a human
# before any of it becomes client-visible.
if [ "$dry_run" = true ]; then
  echo "$title"
  echo
  cat "$body_file"
  exit 0
fi

existing="$(gh pr list --repo "$slug" --head "$from" --base "$to" --state open --json number --limit 1 2>/dev/null || echo '[]')"
pr_number="$(node -e 'const a=JSON.parse(process.argv[1]||"[]");console.log(a.length?a[0].number:"")' "$existing")"

if [ -n "$pr_number" ]; then
  action="updated"
  gh pr edit "$pr_number" --repo "$slug" --title "$title" --body-file "$body_file" >&2
else
  action="created"
  # --draft is not optional and is not exposed as a flag.
  args=(--repo "$slug" --base "$to" --head "$from" --title "$title" --body-file "$body_file" --draft)
  gh pr create "${args[@]}" >&2
  pr_number="$(gh pr list --repo "$slug" --head "$from" --base "$to" --state open --json number --limit 1 \
    | node -e 'const a=JSON.parse(require("fs").readFileSync(0,"utf8"));console.log(a.length?a[0].number:"")')"
  [ -n "$pr_number" ] || { echo "release PR was created but could not be found again ($from -> $to)" >&2; exit 2; }
fi

# Best-effort and loud on failure: some repos refuse a review request on a draft,
# and losing the PR over it would be worse than reporting it.
[ "$assignee" = none ] || gh pr edit "$pr_number" --repo "$slug" --add-assignee "$assignee" >&2 \
  || echo "WARN could not assign $assignee — set it by hand" >&2
[ "$reviewer" = none ] || gh pr edit "$pr_number" --repo "$slug" --add-reviewer "$reviewer" >&2 \
  || echo "WARN could not request review from $reviewer (draft PRs sometimes refuse) — request it by hand" >&2

final="$(gh pr view "$pr_number" --repo "$slug" --json url,number,isDraft)"
node -e '
  const fs = require("fs");
  const [raw, action, from, to, manifestPath, assignee, reviewer] = process.argv.slice(1);
  const pr = JSON.parse(raw);
  const m = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  console.log(JSON.stringify({
    pr_url: pr.url, pr_number: pr.number, action, draft: pr.isDraft,
    from, to, tasks: m.tasks ?? [], unresolved: (m.unresolved ?? []).length,
    assignee, reviewer,
  }));
' "$final" "$action" "$from" "$to" "$manifest" "$assignee" "$reviewer"
