#!/usr/bin/env bash
# run-tests.sh — run an Odoo module's tests on a throwaway DB and report machine
# evidence a PR gate can trust.
#
# Usage: run-tests.sh <repo> <task_id> <module> [--test-tags T] [--db-suffix S]
#                     [--with-tours] [--repo-path DIR]
#
# Mechanics:
#   - addons path override per command, worktree path FIRST so worktree modules
#     shadow the originals; original path read from the odoo config
#   - throwaway DB <project>_test_<id><suffix>, dropped by a trap on every exit
#     path, so a crashed run does not leave a database behind
#   - Odoo major detected and recorded; 16-19 share the
#     --test-enable/--test-tags/--stop-after-init surface
#
# Two execution modes, detected rather than configured:
#   host        docker is present and <project>-odoo-1 is running -> docker exec
#   container   no docker, but odoo and postgres answer here -> run directly
# The lifted original only knew the first. Inside the devcontainer — where the
# checkout is bind-mounted and odoo is on PATH — it failed on `docker: command
# not found`, which reads as a broken script rather than a wrong assumption.
#
# Last stdout line: {"passed","db","module","odoo_version","mode","tests_run",
#   "tours_declared","tours_run","tours_passed","failures":[{"test","error"}],
#   "log_file","log_excerpt","db_dropped"}
#
# failures[].error is the EXTRACTED exception message ("AssertionError: 2 != 1"),
# parsed out of the failure's traceback — not the adjacent log line, and not the
# traceback itself. That distinction is load-bearing downstream: odoo-pr may
# publish this field in a client-visible PR, and publishes log_excerpt nowhere.
#
# FAIL-CLOSED, three ways, because "no failures" is not evidence:
#   - tests_run is the count Odoo actually executed. An unrecognized log format
#     reports 0, which blocks the PR rather than passing it on an assumption.
#   - tours_declared counts start_tour() calls in the module's own tests. If the
#     module declares tours and tours_run is 0, the run FAILS. Odoo skips tours
#     without a browser and logs the skip at INFO, so this is the difference
#     between "the tours pass" and "the tours never ran".
#   - --with-tours exits 6 rather than running when no browser is available.
#
# Concurrency: several invocations may hit ONE odoo container at the same time
# (the stack is a shared singleton, not a lock), so every run is isolated
# explicitly — own database, own log file, own HTTP port, no cron threads, a
# bounded per-process connection budget.
#
# Exit codes: 0 ran (check "passed") | 2 usage/environment | 6 tours requested
# but no usable browser
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[ $# -ge 3 ] || { echo "usage: run-tests.sh <repo> <task_id> <module> [--test-tags T] [--db-suffix S] [--with-tours] [--repo-path DIR]" >&2; exit 2; }
repo="$1"; task_id="$2"; module="$3"; shift 3

tags=""; suffix=""; with_tours=false; repo_path=""
while [ $# -gt 0 ]; do
  case "$1" in
    --test-tags) tags="${2:?}"; shift 2 ;;
    --db-suffix) suffix="${2:?}"; shift 2 ;;
    --repo-path) repo_path="${2:?}"; shift 2 ;;
    --with-tours) with_tours=true; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

CREATEDB_ATTEMPTS="${CREATEDB_ATTEMPTS:-5}"
CREATEDB_RETRY_S="${CREATEDB_RETRY_S:-3}"
HTTP_PORT_BASE="${HTTP_PORT_BASE:-18000}"
HTTP_PORT_RANGE="${HTTP_PORT_RANGE:-1000}"
DB_MAXCONN="${DB_MAXCONN:-8}"
WORKTREE_SUBDIR="${WORKTREE_SUBDIR:-.worktrees}"
WORKTREE_ADDONS_BASE="${WORKTREE_ADDONS_BASE:-/mnt/extra-addons}"
ODOO_CONF="${ODOO_RC:-/etc/odoo/odoo.conf}"
SHARED_DB_CONTAINER="${ODOO_SHARED_DB_CONTAINER:-odoo-shared-db-1}"

# Container names come from the compose project name, which compose derives by
# lowercasing and stripping the workspace folder: repo "QOC" runs as "qoc-odoo-1".
# Building these from "$repo" verbatim made every exec against an uppercase repo
# fail with "No such container".
proj="$(printf '%s' "$repo" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9_-]//g')"
[ -n "$proj" ] || { echo "repo name normalizes to nothing: $repo" >&2; exit 2; }

odoo_c="${proj}-odoo-1"
dbname="${proj}_test_${task_id}${suffix}"

# ---- mode detection ----------------------------------------------------------
mode=""
if command -v docker >/dev/null 2>&1 \
   && docker ps --filter "name=^${odoo_c}$" --format '{{.Names}}' | grep -q .; then
  mode="host"
elif command -v odoo >/dev/null 2>&1 && pg_isready >/dev/null 2>&1; then
  mode="container"
else
  echo "no way to run Odoo: docker is absent or ${odoo_c} is not running (run odoo-task-env's stack-ensure.sh first), and this is not a working Odoo container" >&2
  exit 2
fi

if [ "$mode" = host ]; then
  odoo_run() { docker exec "$odoo_c" "$@"; }
  odoo_run_env() { local e="$1"; shift; docker exec -e "$e" "$odoo_c" "$@"; }
  # The shared db container is addressed directly rather than through the odoo
  # container: the trap must still drop the DB if the odoo container went away
  # mid-run. -U "$proj": the project's role owns its databases; trust auth.
  db_run() { docker exec "$SHARED_DB_CONTAINER" "$@" -U "${TEST_DB_ROLE:-$proj}"; }
else
  odoo_run() { "$@"; }
  odoo_run_env() { local e="$1"; shift; env "$e" "$@"; }
  # libpq env (PGHOST/PGUSER) is already set in the devcontainer; adding -U here
  # would override a correct role with a guess derived from a folder name.
  db_run() { "$@" ${TEST_DB_ROLE:+-U "$TEST_DB_ROLE"}; }
fi

version_raw="$(odoo_run odoo --version 2>/dev/null | tail -1)"
major="$(printf '%s' "$version_raw" | { grep -oE '[0-9]+' || true; } | head -1)"
major="${major:-0}"
case "$major" in
  16|17|18|19) : ;;  # same test-flag surface; adjust here if a version diverges
  *) echo "WARN unrecognized Odoo version '$version_raw' — using standard flags" >&2 ;;
esac

orig_addons="$( { odoo_run grep -E '^\s*addons_path' "$ODOO_CONF" || true; } | head -1 | sed 's/^[^=]*=\s*//')"
[ -n "$orig_addons" ] || { echo "could not read addons_path from $ODOO_CONF" >&2; exit 2; }
worktree_addons="$WORKTREE_ADDONS_BASE/$WORKTREE_SUBDIR/task-${task_id}"
addons="${worktree_addons},${orig_addons}"

# ---- tours: declared, and can they actually run? -----------------------------
# Counted from the SOURCE, before anything runs, so "the module has tours" is a
# fact about the code rather than a fact about the log.
tours_declared=0
module_src=""
for base in "$repo_path" "$worktree_addons" "$WORKTREE_ADDONS_BASE"; do
  [ -n "$base" ] || continue
  if [ -d "$base/$module" ]; then module_src="$base/$module"; break; fi
  if [ -d "$base/$WORKTREE_SUBDIR/task-${task_id}/$module" ]; then
    module_src="$base/$WORKTREE_SUBDIR/task-${task_id}/$module"; break
  fi
done
if [ -n "$module_src" ]; then
  # `|| true` on every extraction pipeline below, not decoration: grep exits 1
  # when it matches nothing, and under `set -o pipefail` that aborts the script
  # at exactly the moment the honest answer is "zero".
  tours_declared="$( { grep -rl "start_tour(" "$module_src" --include='*.py' 2>/dev/null || true; } | wc -l | tr -d ' ')"
fi

# Odoo writes failure screenshots to config['screenshots']/<db>/screenshots on ANY
# failing HttpCase, not only on a tour. The devcontainer config points that at a
# host bind mount that need not exist here, and an unwritable path turns a real
# assertion failure into "PermissionError: [Errno 13] Permission denied" — the
# defect gets hidden behind the reporting of the defect.
ARTIFACTS_DIR="${ODOO_TEST_ARTIFACTS_DIR:-${TMPDIR:-/tmp}/odoo-test-run}"
mkdir -p "$ARTIFACTS_DIR/screenshots" "$ARTIFACTS_DIR/screencasts"
screenshot_flags=(--screenshots="$ARTIFACTS_DIR/screenshots" --screencasts="$ARTIFACTS_DIR/screencasts")

browser_env=""
if [ "$with_tours" = true ]; then
  browser_json="$("$SCRIPT_DIR/browser-ensure.sh")" || {
    echo "--with-tours was requested but no usable browser is available (see above). Refusing to run: Odoo would SKIP every tour and report the suite as green." >&2
    exit 6
  }
  browser_bin="$(node -e 'console.log(JSON.parse(process.argv[1]).browser_bin)' "$browser_json")"
  browser_env="ODOO_BROWSER_BIN=$browser_bin"
fi

# Per-run HTTP port, stable for this task+suffix and distinct across concurrent
# runs. Tours need a real bound port, so this is not merely defensive.
port_key="$(printf '%s' "${task_id}${suffix}" | cksum | awk '{print $1}')"
port=$(( HTTP_PORT_BASE + port_key % HTTP_PORT_RANGE ))

# Unique per invocation, and created only once the run is going to happen.
# Deriving the log from $dbname alone made a second fix-cycle attempt truncate
# the first one's log — destroying the artifact reported as log_file. Kept (not
# trapped away) on purpose: the reported path must outlive this script.
log="$(mktemp "${TMPDIR:-/tmp}/${dbname}.XXXXXX.log")"

db_dropped=false
cleanup() {
  if db_run dropdb --if-exists "$dbname" >/dev/null 2>&1; then db_dropped=true; fi
}
trap cleanup EXIT

db_run dropdb --if-exists "$dbname" >/dev/null

# Bounded retry: concurrent creates off a shared template can transiently fail
# with "source database ... is being accessed by other users". Any OTHER failure
# is real and reported immediately rather than slept on.
created=false
createdb_err=""
for _ in $(seq 1 "$CREATEDB_ATTEMPTS"); do
  if createdb_err="$(db_run createdb "$dbname" 2>&1)"; then created=true; break; fi
  case "$createdb_err" in
    *"is being accessed by other users"*) sleep "$CREATEDB_RETRY_S" ;;
    *) break ;;
  esac
done
[ "$created" = true ] || { echo "createdb failed for $dbname: $createdb_err" >&2; exit 2; }

# Isolation flags, all three for concurrency rather than for this run's own sake:
#   --http-port           unique per run
#   --max-cron-threads=0  the config file is auto-loaded into every process, so
#                         without this each concurrent run also starts a cron
#                         thread against its own database
#   --db_maxconn          the documented default is 64 PER WORKER; N concurrent
#                         processes at 64 can exhaust postgres's max_connections,
#                         which is host-global (one shared server)
set +e
if [ -n "$browser_env" ]; then
  odoo_run_env "$browser_env" odoo \
    --addons-path="$addons" \
    -d "$dbname" -i "$module" \
    --test-enable ${tags:+--test-tags "$tags"} \
    --http-port="$port" --max-cron-threads=0 --db_maxconn="$DB_MAXCONN" \
    "${screenshot_flags[@]}" \
    --stop-after-init \
    >"$log" 2>&1
else
  odoo_run odoo \
    --addons-path="$addons" \
    -d "$dbname" -i "$module" \
    --test-enable ${tags:+--test-tags "$tags"} \
    --http-port="$port" --max-cron-threads=0 --db_maxconn="$DB_MAXCONN" \
    "${screenshot_flags[@]}" \
    --stop-after-init \
    >"$log" 2>&1
fi
rc=$?
set -e

# Odoo logs test failures as "FAIL:"/"ERROR:" lines under the test loggers, and
# exits non-zero on failed tests in 16-19.
fail_count="$(grep -cE '(FAIL|ERROR): ' "$log" || true)"

# Executed-test extraction, in two tiers.
#   Primary: Odoo's own end-of-run summaries, emitted per test phase:
#     "0 failed, 0 error(s) of 42 tests when loading database 'x'"
#   at_install and post_install each emit one, so they are SUMMED — taking only
#   the last line reported the post_install phase alone and undercounted every
#   run that had both.
#   Secondary: count the per-test "Starting <Class>.<method> ..." lines, for a
#   run whose summary never appeared.
# Neither matching means 0 — see the fail-closed note in the header.
tests_run="$( { grep -oE 'of [0-9]+ tests when loading database' "$log" || true; } \
  | { grep -oE '[0-9]+' || true; } | awk '{s+=$1} END {print s+0}')"
if [ "${tests_run:-0}" -eq 0 ]; then
  tests_run="$(grep -cE 'Starting [A-Za-z_][A-Za-z0-9_]*\.[A-Za-z0-9_]+ \.\.\.' "$log" || true)"
  tests_run="${tests_run:-0}"
fi
tests_run="${tests_run:-0}"

# Tours: Odoo logs "[n/N] Tour <name> -> Step ..." per step and "tour succeeded"
# once per completed tour, both as messages on the <test>.browser logger.
# Both patterns are anchored to that logger on purpose. A bare `grep "tour
# succeeded"` also matched the traceback of a FAILING test, because the printed
# source line is `success_signal="tour succeeded"` — so a run in which no tour
# started reported one as passed.
# sed rather than a second grep, and not only to avoid another unguarded exit
# code: the match carries the "[3/9]" step counter, so sorting the raw matches
# would count one nine-step tour as nine tours.
tours_run="$( { grep -oE '\.browser: \[[0-9]+/[0-9]+\] Tour [A-Za-z0-9_.]+ ' "$log" || true; } \
  | sed -E 's/.*Tour ([A-Za-z0-9_.]+) *$/\1/' | sort -u | wc -l | tr -d ' ')"
tours_passed="$(grep -cE '\.browser: tour succeeded[[:space:]]*$' "$log" || true)"
tours_passed="${tours_passed:-0}"

passed=false
[ "$rc" -eq 0 ] && [ "$fail_count" -eq 0 ] && [ "$tests_run" -gt 0 ] && passed=true

# The silent-skip guard. A module that declares tours and ran none did not pass;
# it did not test. This is the whole reason --with-tours exists.
tour_gap=false
if [ "$tours_declared" -gt 0 ] && [ "$tours_run" -eq 0 ]; then
  passed=false
  tour_gap=true
fi

# Drop now (trap remains as backstop) so db_dropped is accurate in the JSON.
cleanup
trap - EXIT

node -e '
  const fs = require("fs");
  const [logFile, passed, dbname, module_, version, dropped, testsRun,
         toursDeclared, toursRun, toursPassed, tourGap, mode] = process.argv.slice(1);
  const text = fs.readFileSync(logFile, "utf8");
  const lines = text.split("\n");

  // Odoo prefixes every log RECORD with a timestamp; the continuation lines of a
  // traceback carry none. So the block belonging to one failure is everything
  // between its FAIL:/ERROR: line and the next timestamped line.
  const STAMPED = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}/;
  // The exception line that ENDS a Python traceback: "AssertionError: 2 != 1".
  const EXCEPTION = /^[A-Za-z_][A-Za-z0-9_.]*(Error|Exception|Failure|Exit):/;
  // Traceback scaffolding, never the message itself.
  const NOISE = /^([-=]{3,}$|Traceback \(most recent call last\):$|File ")/;

  const failures = [];
  for (let i = 0; i < lines.length && failures.length < 20; i++) {
    const m = lines[i].match(/(?:FAIL|ERROR): ([^\n]*)/);
    if (!m) continue;
    const block = [];
    for (let j = i + 1; j < lines.length && !STAMPED.test(lines[j]); j++) {
      const line = lines[j].trim();
      if (line && !NOISE.test(line)) block.push(line);
    }
    const named = block.filter((line) => EXCEPTION.test(line));
    const message = named.length ? named[named.length - 1] : (block[block.length - 1] || "");
    failures.push({ test: m[1].trim().slice(0, 200), error: message.slice(0, 300) });
  }
  if (tourGap === "true") {
    failures.unshift({
      test: module_ + ": declared tours did not run",
      error: `${toursDeclared} test file(s) call start_tour() but no tour executed — Odoo skips tours when the browser or websocket-client is missing, and logs the skip as a pass. Re-run with --with-tours.`,
    });
  }

  const excerpt = lines.slice(-50).join("\n");
  console.log(JSON.stringify({
    passed: passed === "true", db: dbname, module: module_,
    odoo_version: version, mode,
    tests_run: Number(testsRun) || 0,
    tours_declared: Number(toursDeclared) || 0,
    tours_run: Number(toursRun) || 0,
    tours_passed: Number(toursPassed) || 0,
    failures, log_file: logFile, log_excerpt: excerpt,
    db_dropped: dropped === "true",
  }));
' "$log" "$passed" "$dbname" "$module" "$version_raw" "$db_dropped" "$tests_run" \
  "$tours_declared" "$tours_run" "$tours_passed" "$tour_gap" "$mode"

[ "$passed" = true ] || exit 0  # a test failure is a RESULT, not a script error
