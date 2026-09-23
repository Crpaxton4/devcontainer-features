#!/usr/bin/env bash
# gate.sh — decides whether the evidence on disk is good enough to ship.
#
# The one piece of determinism in an otherwise composable chain. It is a
# VALIDATOR, never an orchestrator: it reads artifacts, does arithmetic, and
# says yes or no. It spawns nothing, fixes nothing, and asks no agent anything.
# Every blocker below exists because a real run once went green without it.
#
# Usage: gate.sh [--for pr|release] [--soft] [--] <artifacts_dir|task-id>  (default: pr)
#
# <artifacts_dir> may be given as a bare Odoo task id — anything matching ^[0-9]+$ —
# which resolves against the state dir to <state>/tasks/<id>. Anything else is a
# path and is used exactly as given, so every caller that already passes a
# fully-resolved directory is unaffected. The rule lives in state-dir.sh, sourced
# below, so gate.sh and artifact.sh cannot drift apart on what an id is.
#
# --soft never exits non-zero. A usage error, an unresolvable directory and a red
# verdict all end with `NO PASSING --for <mode> GATE` on stdout and exit 0. It is
# for a command's `!`…`` preamble and nothing else: a non-zero exit there aborts the
# whole command expansion, so the agent is never dispatched and the shell's error
# becomes the user's error message. Nothing that READS the exit code — gate-hook.sh,
# the test suite — passes it, which is why the codes below are unchanged without it.
#
# Last stdout line: {"ok","for","checked":[],"blockers":[{"blocker","detail"}],
#                    "warnings":[{"warning","detail"}],"revisions":{}}
# Exit codes: 0 pass | 1 at least one blocker | 2 usage      (--soft: 0 always)
#
# A warning is a check that ran, found something a human should see, and did not
# stop the run. `ok` and the exit code are driven by `blockers` alone, so
# `gate.sh "$A" && ship` still ships on a warning and still stops on a blocker.
#
# Exits 1 rather than printing a warning so that `gate.sh "$A" && ship` cannot
# skip it, and so a shell that ignores stdout still stops.
#
# Reads the LATEST revision of each stage and reports the per-stage revision
# count, so "green on the third try" is visible instead of indistinguishable
# from "green on the first".
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=state-dir.sh
. "$HERE/state-dir.sh"

FOR=pr
SOFT=0
DIR=""
have_dir=0
endopts=0

# The only exit that --soft may take. The reason always goes to stderr, so a command
# file with a real mistake in it is still diagnosable; only the exit code is softened.
give_up() {
  echo "gate.sh: $1" >&2
  if [ "$SOFT" -eq 1 ]; then
    echo "NO PASSING --for $FOR GATE"
    exit 0
  fi
  exit 2
}

# Options may come before or after the directory: gate-hook.sh and the test suite
# write `gate.sh <dir> --for pr`, and a preamble that puts the substituted argument
# last behind `--` is the shape a worktree-isolated session can verify.
while [ $# -gt 0 ]; do
  if [ "$endopts" -eq 0 ]; then
    case "$1" in
      --) endopts=1; shift; continue ;;
      --soft) SOFT=1; shift; continue ;;
      --for)
        [ $# -ge 2 ] || give_up "--for needs pr|release"
        case "$2" in
          pr | release) FOR="$2" ;;
          *) give_up "--for must be pr or release" ;;
        esac
        shift 2; continue ;;
      -?*) give_up "unknown option $1" ;;
    esac
  fi
  [ "$have_dir" -eq 0 ] || give_up "unexpected argument: $1"
  DIR="$1"; have_dir=1; shift
done

[ "$have_dir" -eq 1 ] || give_up "no artifacts dir given. usage: gate.sh [--for pr|release] [--soft] [--] <artifacts_dir|task-id>"
DIR="$(odoo_dev_artifacts_dir "$DIR")"
[ -d "$DIR" ] || give_up "artifacts dir not found: $DIR"

# The verdict's own exit status has to be readable here rather than ending the
# script through `set -e`, because --soft turns a red one into a marker.
set +e
node --input-type=module -e '
import { existsSync, readFileSync } from "fs";
const [dir, mode] = process.argv.slice(1);

const blockers = [], warnings = [], checked = [], revisions = {};
const block = (blocker, detail) => blockers.push({ blocker, detail });
const warn = (warning, detail) => warnings.push({ warning, detail });

// Latest revision wins; the count is reported so a retry is never invisible.
function load(stage) {
  let n = 1, found = null;
  for (;;) {
    const f = n === 1 ? `${dir}/${stage}.json` : `${dir}/${stage}.${n}.json`;
    if (!existsSync(f)) break;
    found = f; n += 1;
  }
  if (!found) return null;
  revisions[stage] = n - 1;
  try { return JSON.parse(readFileSync(found, "utf8")); }
  catch (e) { block("unparseable_artifact", `${found}: ${e.message}`); return null; }
}

const num = v => (typeof v === "number" && Number.isFinite(v) ? v : null);

function deliveryGates() {
  const env = load("10-env");
  const build = load("20-build");
  const test = load("30-test");
  const rabbit = load("40-coderabbit");

  // no_tests — zero is never a pass. An unparsed log also reports 0, so a broken
  // parser fails closed instead of shipping on a number nobody produced.
  checked.push("no_tests");
  if (!test) block("no_tests", "30-test.json missing — no test evidence exists");
  else if (num(test.tests_run) === null)
    block("no_tests", `tests_run is not a number: ${JSON.stringify(test.tests_run)}`);
  else if (test.tests_run < 1)
    block("no_tests", `tests_run=${test.tests_run}; zero executed tests is not a pass`);

  // tours_skipped — Odoo skips tours when no browser is present and logs the
  // skip as a pass. Declared-but-not-run is the specific fake-green this exists for.
  checked.push("tours_skipped");
  if (test) {
    const declared = num(test.tours_declared), run = num(test.tours_run);
    if (declared === null || run === null)
      block("tours_skipped", "tours_declared/tours_run missing or non-numeric");
    else if (declared > 0 && run === 0)
      block("tours_skipped", `${declared} tour(s) declared, 0 run — no browser, or the tour never loaded`);
  }

  // tests_failed — anything other than an explicit true.
  checked.push("tests_failed");
  if (test && test.passed !== true) {
    const errs = Array.isArray(test.failures)
      ? test.failures.map(f => f && f.error).filter(Boolean) : [];
    block("tests_failed", errs.length ? errs.join(" | ") : `passed=${JSON.stringify(test.passed)}`);
  }

  // review_incomplete — a review that did not finish is not a clean review, and a
  // finding nobody dealt with is not a finished review either. The only two ways
  // through are a fresh run that reported no findings at all, or a deliberate
  // waiver per remaining finding in 45-waiver.json. The waiver lives in its own
  // artifact so agent judgement never lands in the same file as tool output.
  checked.push("review_incomplete");
  if (!rabbit) block("review_incomplete", "40-coderabbit.json missing — no review ran");
  else if (rabbit.status !== "complete") {
    // Status is checked first and on its own: no waiver can rescue a review that
    // never finished, so the waivers are not even read in that case.
    block("review_incomplete", `CodeRabbit status=${JSON.stringify(rabbit.status)}, expected "complete"`);
  } else {
    const findings = Array.isArray(rabbit.findings) ? rabbit.findings : [];
    const count = num(rabbit.findings_count);
    if (count === null) {
      block("review_incomplete", `findings_count is not a number: ${JSON.stringify(rabbit.findings_count)}`);
    } else if (count !== findings.length) {
      // A producer that disagrees with itself is broken, and a broken producer
      // fails closed rather than shipping on whichever number is smaller.
      block("review_incomplete",
        `40-coderabbit.json contradicts itself: findings_count=${count} but findings[] holds ${findings.length}`);
    } else if (findings.length) {
      // One waiver entry is consumed per open finding: a waiver is a deliberate
      // act per finding, never a blanket pardon for a file. CodeRabbit findings
      // carry no line, so a line is matched only when the finding states one.
      const waiver = load("45-waiver");
      const waived = waiver && Array.isArray(waiver.waived) ? waiver.waived : [];
      const spent = waived.map(() => false);
      const uncovered = [];
      for (const f of findings) {
        const file = f && typeof f === "object" ? f.file ?? null : null;
        const line = f && typeof f === "object" ? num(f.line) : null;
        const i = waived.findIndex((w, n) =>
          !spent[n] && w && typeof w === "object" && !Array.isArray(w) &&
          w.file === file &&
          (line === null || w.line === null || w.line === undefined || num(w.line) === line));
        if (i === -1) uncovered.push(JSON.stringify(file));
        else spent[i] = true;
      }
      if (uncovered.length) {
        const used = spent.filter(Boolean).length;
        block("review_incomplete",
          `${findings.length} open CodeRabbit finding(s), ${used} waived in 45-waiver.json; ` +
          `${uncovered.length} still uncovered: ${uncovered.join(", ")}`);
      }
    }
  }

  // worktree_drift — a fix that landed somewhere nobody verified.
  checked.push("worktree_drift");
  if (env && build) {
    const w = env.worktree_ensure ?? {};
    const mism = [];
    if (w.worktree && build.worktree && w.worktree !== build.worktree)
      mism.push(`worktree ${build.worktree} != ${w.worktree}`);
    if (w.branch && build.branch && w.branch !== build.branch)
      mism.push(`branch ${build.branch} != ${w.branch}`);
    if (mism.length) block("worktree_drift", mism.join("; "));
  } else if (!env) {
    block("worktree_drift", "10-env.json missing — the verified worktree is unknown");
  } else {
    block("worktree_drift", "20-build.json missing — nothing claims what was built");
  }
}

function releaseGates() {
  const ctx = load("00-context");
  const rel = load("60-release");

  // unconfirmed_flow — never self-confirm a chain whose last element is production.
  checked.push("unconfirmed_flow");
  if (!ctx) block("unconfirmed_flow", "00-context.json missing — the branch flow is unverified");
  else if (ctx.flow_confirmed !== true)
    block("unconfirmed_flow", `flow_confirmed=${JSON.stringify(ctx.flow_confirmed)}; a human must vouch for the chain before it drives a promotion`);

  // untagged_pr — a merged PR with no [task NNN] is reported, never auto-attributed.
  //
  // This warns rather than blocks because "a merged PR carries no task" is the
  // ordinary case, not an exception: some developers never put a task on a PR by
  // policy, so no human answer could ever clear it and the release would be a
  // permanent dead end. What the blocker was thought to protect — a chatter note
  // landing on the wrong task — is protected instead by the rule that owns it:
  // the skill posts no note to an unresolved PR or to an inferred id. The names
  // are in the detail because a human acts on the PR numbers, not on the count.
  //
  // A manifest that is missing entirely is a different thing and still blocks:
  // there is nothing to report, and nothing to ship.
  checked.push("untagged_pr");
  if (!rel) block("untagged_pr", "60-release.json missing — no manifest to check");
  else {
    const unresolved = Array.isArray(rel.unresolved) ? rel.unresolved : [];
    const inferred = Array.isArray(rel.inferred_task_ids) ? rel.inferred_task_ids : [];
    if (unresolved.length)
      warn("untagged_pr", `${unresolved.length} merged PR(s) carry no task tag: ${unresolved.map(u => "#" + (u && (u.number ?? u))).join(", ")}`);
    if (inferred.length)
      warn("untagged_pr", `${inferred.length} task id(s) inferred from a title or branch name, not tagged: ${inferred.map(i => i && (i.task_id ?? i)).join(", ")}`);
  }
}

if (mode === "release") releaseGates(); else deliveryGates();

console.log(JSON.stringify({
  ok: blockers.length === 0, for: mode, checked, blockers, warnings, revisions,
}));
process.exit(blockers.length === 0 ? 0 : 1);
' -- "$DIR" "$FOR"
rc=$?

# A red verdict under --soft still prints its blockers first, then the marker, so the
# command body reads exactly the two lines it always did.
if [ "$SOFT" -eq 1 ] && [ "$rc" -ne 0 ]; then
  echo "NO PASSING --for $FOR GATE"
  exit 0
fi
exit "$rc"
