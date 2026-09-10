#!/usr/bin/env bash
# coderabbit-poll.sh — bounded wait for CodeRabbit activity on a PR.
#
# Usage: coderabbit-poll.sh <owner/repo> <pr_number> <since_iso8601>
#
# Polls every POLL_INTERVAL (default 60 s), at most MAX_POLLS (default 15)
# iterations, for reviews or review comments from coderabbit newer than
# <since_iso8601>. Judgment about the findings stays with the calling agent;
# this script owns only the polling mechanics.
#
# Last stdout line: {"found", "timed_out", "comments": [{"id","path","line","body"}]}
set -euo pipefail

POLL_INTERVAL="${POLL_INTERVAL:-60}"
MAX_POLLS="${MAX_POLLS:-15}"

[ $# -eq 3 ] || { echo "usage: coderabbit-poll.sh <owner/repo> <pr_number> <since_iso8601>" >&2; exit 2; }
slug="$1"; pr="$2"; since="$3"

collect() {
  {
    gh api "repos/$slug/pulls/$pr/comments" --paginate 2>/dev/null || echo '[]'
    echo '===REVIEWS==='
    gh pr view "$pr" --repo "$slug" --json reviews 2>/dev/null || echo '{"reviews":[]}'
  } | node -e '
    const raw = require("fs").readFileSync(0, "utf8");
    const [commentsRaw, reviewsRaw] = raw.split("===REVIEWS===");
    const since = process.argv[1];
    const isCR = u => u && /coderabbit/i.test(u.login || u);
    let comments = [];
    try {
      for (const c of JSON.parse(commentsRaw))
        if (isCR(c.user) && c.created_at > since)
          comments.push({ id: c.id, path: c.path || null, line: c.line ?? null,
                          body: (c.body || "").slice(0, 4000) });
    } catch {}
    let reviewFound = false;
    try {
      for (const r of JSON.parse(reviewsRaw).reviews || [])
        if (isCR(r.author?.login || r.author) && (r.submittedAt || "") > since) reviewFound = true;
    } catch {}
    console.log(JSON.stringify({ found: reviewFound || comments.length > 0, comments }));
  ' "$since"
}

for i in $(seq 1 "$MAX_POLLS"); do
  result="$(collect)"
  if [ "$(echo "$result" | node -e 'console.log(JSON.parse(require("fs").readFileSync(0,"utf8")).found)')" = "true" ]; then
    echo "$result" | node -e '
      const r = JSON.parse(require("fs").readFileSync(0, "utf8"));
      console.log(JSON.stringify({ ...r, timed_out: false }));'
    exit 0
  fi
  echo "poll $i/$MAX_POLLS: no coderabbit activity yet" >&2
  [ "$i" -lt "$MAX_POLLS" ] && sleep "$POLL_INTERVAL"
done

echo '{"found": false, "timed_out": true, "comments": []}'
