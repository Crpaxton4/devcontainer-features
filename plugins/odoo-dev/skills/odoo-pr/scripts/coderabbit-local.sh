#!/usr/bin/env bash
# coderabbit-local.sh — run a CodeRabbit review on a worktree BEFORE pushing.
#
# Usage: coderabbit-local.sh <worktree> [--base <branch>] [--timeout <seconds>]
#
# Why a script and not the plugin agent: the coderabbit plugin's reviewer passes
# `--dir` but never `--base`, and a task worktree has to be compared against the
# branch its PR will target — which is the project's flow branch, not whatever
# the worktree's upstream happens to be. Getting the base wrong reviews the wrong
# diff, quietly.
#
# The CLI's `review` exit code is undocumented, so it is NOT used as a verdict.
# The `--agent` stream is: one JSON object per line, with `type` in
# review_context | status | heartbeat | finding | complete | error |
# action_required. A run that produces no `complete` event is a FAILURE, not a
# clean review — that distinction is the whole point of this wrapper.
#
# Reviews routinely take 7-30 minutes; the default timeout allows for that.
#
# Last stdout line: {"clean","findings_count","findings":[...],"status","base"}
# Exit codes: 0 review completed (read "clean") | 2 usage | 3 the CLI did not
# produce a completed review (timeout, auth, rate limit, crash)
set -uo pipefail

[ $# -ge 1 ] || { echo "usage: coderabbit-local.sh <worktree> [--base <branch>] [--timeout <seconds>]" >&2; exit 2; }
worktree="$1"; shift
base=""; timeout_s="${CODERABBIT_TIMEOUT_S:-2400}"
while [ $# -gt 0 ]; do
  case "$1" in
    --base) base="${2:?}"; shift 2 ;;
    --timeout) timeout_s="${2:?}"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[ -d "$worktree" ] || { echo "not a directory: $worktree" >&2; exit 2; }
git -C "$worktree" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || { echo "not a git work tree: $worktree" >&2; exit 2; }
command -v coderabbit >/dev/null 2>&1 || { echo "coderabbit CLI is not installed (https://docs.coderabbit.ai/cli)" >&2; exit 3; }

raw="$(mktemp "${TMPDIR:-/tmp}/coderabbit-local.XXXXXX.jsonl")"
args=(review --agent)
[ -n "$base" ] && args+=(--base "$base")

# cwd is the worktree: the CLI resolves the repo from where it runs, and --dir
# only narrows within that repo.
( cd "$worktree" && timeout "$timeout_s" coderabbit "${args[@]}" ) >"$raw" 2>"$raw.err"
rc=$?

node -e '
  const fs = require("fs");
  const [rawPath, errPath, rc, base] = process.argv.slice(1);
  const lines = fs.readFileSync(rawPath, "utf8").split("\n").filter((l) => l.trim());

  const events = [];
  for (const line of lines) {
    try { events.push(JSON.parse(line)); } catch { /* progress noise, not an event */ }
  }
  const findings = events
    .filter((e) => e.type === "finding")
    .map((e) => ({
      severity: e.severity ?? null,
      file: e.fileName ?? null,
      comment: String(e.comment ?? "").slice(0, 4000),
    }));
  const complete = events.find((e) => e.type === "complete");
  const errored = events.find((e) => e.type === "error");
  const actionRequired = events.find((e) => e.type === "action_required");

  let status;
  if (actionRequired) status = "action_required";
  else if (errored) status = "error";
  else if (complete) status = "complete";
  else if (rc === "124") status = "timeout";
  else status = "no_completion";

  if (status !== "complete") {
    const detail = errored?.message
      ?? actionRequired?.status
      ?? fs.readFileSync(errPath, "utf8").trim().split("\n").slice(-3).join(" ").slice(0, 400)
      ?? "";
    // Never report a failed review as clean: that would let unreviewed code
    // through the gate wearing a green badge.
    console.error(`coderabbit review did not complete (${status}): ${detail}`);
    console.log(JSON.stringify({ clean: false, findings_count: findings.length, findings, status, base: base || null }));
    process.exit(3);
  }
  console.log(JSON.stringify({
    clean: findings.length === 0,
    findings_count: findings.length,
    findings, status, base: base || null,
  }));
' "$raw" "$raw.err" "$rc" "$base"
