#!/usr/bin/env bash
# run-tests.sh — run an Odoo module's tests on a throwaway DB and report machine
# evidence a PR gate can trust.
#
# Usage: run-tests.sh <repo> <task_id> <module> [--test-tags T|all] [--db-suffix S]
#                     [--with-tours] [--repo-path DIR] [--addons-path P]
#                     [--data-dir DIR] [--target-series NN.0] [--odoo-bin PATH]
#                     [--odoo-conf PATH] [--put ARTIFACTS_DIR]
#
#   --test-tags:     Odoo's test-tag grammar, passed to odoo verbatim. The default
#                    is /<module>: only the module under test runs. Without a
#                    filter Odoo runs the tests of EVERY installed module (the
#                    whole dependency tree), which does not finish in a usable
#                    time. The value `all` is the escape: it means no filter, so
#                    odoo gets --test-enable and no --test-tags.
#   --target-series: the Odoo series the MODULE targets, as in 18.0. Compared
#                    against the series this container's core actually runs. No
#                    flag means no comparison — every caller that is not
#                    series-aware passes none — so the check is absent rather
#                    than passed. When it disagrees with the container's core
#                    and no --odoo-bin was given, the devcontainer's SIDE CORE
#                    for that series is used if it exists (see below) and the
#                    run goes on; only without one is the result a refusal.
#   --odoo-bin:      the Odoo executable to run in place of `odoo` (the version
#                    probe, the core-addons probe and the test run all use it).
#                    It forces container mode: a side core exists only inside
#                    the devcontainer, so exit 2 when `odoo` and pg_isready do
#                    not answer here. The core-addons probe imports the `odoo`
#                    package of THAT core, found beside the binary (see below).
#   --odoo-conf:     a config file passed as `-c PATH` to every odoo invocation
#                    (version probe and test run). Without it the ambient
#                    config is auto-loaded, as before. addons_path is still
#                    never read from it.
#   --put:           after the result JSON is built, write it into
#                    <ARTIFACTS_DIR>/task.json under test.<module> through the
#                    plugin's artifact.sh. stdout still prints the JSON. A failed
#                    write prints the reason on stderr and exits 7. This is how
#                    the test section is written by the script and never re-typed.
#
# Mechanics:
#   - addons path STATED, never inherited: worktree FIRST so worktree modules
#     shadow the originals, then the core addons directories resolved from the
#     RUNNING Odoo (odoo.__file__). addons_path is deliberately NOT read from
#     odoo.conf — one unrelated broken tree in the ambient config aborts registry
#     load, and the run then reports zero tests instead of a failure.
#   - --data-dir is stated too, under this run's artifacts dir. Odoo appends
#     <data_dir>/addons/<series> to the addons path whatever --addons-path says,
#     so setting only one of the two flags looks isolated and is not.
#   - throwaway DB <project>_test_<id><suffix>, dropped by a trap on every exit
#     path, so a crashed run does not leave a database behind
#   - Odoo major detected and recorded; 16-19 share the
#     --test-enable/--test-tags/--stop-after-init surface
#
# Two execution modes, detected rather than configured, container FIRST:
#   container   odoo and postgres answer here -> run directly
#   host        otherwise, docker is present and <project>-odoo-1 is running
#               -> docker exec
# The lifted original only knew host mode. Inside the devcontainer — where the
# checkout is bind-mounted and odoo is on PATH — it failed on `docker: command
# not found`; once docker-from-docker put `docker` on PATH in there too, the
# docker-first order picked host mode from inside the stack (#1039).
#
# Side core (#1055): the devcontainer ships a second Odoo per extra series as
#   $ODOO_SRC_ROOT/odooNN-bin with odooNN.conf beside it (ODOO_SRC_ROOT defaults
#   to /var/lib/odoo/src, as in odoo-prior-art's addons_paths.sh). A project on
#   18.0 in a 19.0 container has no other way to get test evidence. So, in
#   container mode with no --odoo-bin, a --target-series that disagrees with the
#   container's major makes this script look for odooNN-bin AND odooNN.conf. If
#   both exist it uses them and goes on (odoo_version is then the side core's
#   version and odoo_bin names the binary); if not, the series_mismatch refusal
#   stands and its error names the two paths that were looked for.
#   The core-addons probe must import the side core's own `odoo` package, so it
#   runs with PYTHONPATH set to the source tree found beside the binary: for
#   <dir>/odooNN-bin that is <dir>/odooNN, then <dir> itself, each only when it
#   holds odoo/__init__.py. When none does (a packaged core) the probe runs
#   plain; pass --addons-path if that resolves the wrong core.
#
# Last stdout line: {"passed","status","error","db","module","odoo_version",
#   "odoo_bin","module_target_series","mode","produced_by",
#   "tests_run","suites":[{"module","collected","executed","failed"}],
#   "collected_is_executed","addons_path","data_dir","tours_declared","tours_run",
#   "tours_passed","failures":[{"test","error"}],"log_file","log_excerpt",
#   "db_dropped"}
#
# status is the vocabulary a gate branches on: "passed" | "failed" |
# "registry_aborted" | "series_mismatch". The third exists because "the registry
# never loaded" and "this module ships no tests" both rendered as tests_run: 0
# with an empty failure list, and only one of those is safe to wave through. The
# fourth is the same shape with a different cause: a module of another series is
# never installed at all (#978). "error" carries the decisive log line for
# registry_aborted, the two disagreeing series for series_mismatch, and is null
# otherwise.
#
# failures[].error is the EXTRACTED exception message ("AssertionError: 2 != 1"),
# parsed out of the failure's traceback — not the adjacent log line, and not the
# traceback itself. That distinction is load-bearing downstream: odoo-pr may
# publish this field in a client-visible PR, and publishes log_excerpt nowhere.
#
# FAIL-CLOSED, three ways, because "no failures" is not evidence:
#   - tests_run is the count Odoo actually executed. An unrecognized log format
#     reports 0, which blocks the PR rather than passing it on an assumption.
#   - a run whose registry died before the first test was collected reports
#     status "registry_aborted" and quotes the decisive error, rather than a
#     silent tests_run: 0 that reads as "this module has no tests".
#   - tours_declared counts start_tour() calls in the module's own tests. If the
#     module declares tours and tours_run is 0, the run FAILS. Odoo skips tours
#     without a browser and logs the skip at INFO, so this is the difference
#     between "the tours pass" and "the tours never ran".
#   - --with-tours exits 6 rather than running when no browser is available.
#   - a run whose container does not run the module's own series REFUSES to
#     start: Odoo sets installable=False on a manifest from another series, logs
#     it at WARNING, and then prints "0 failed, 0 error(s) of 0 tests" and exits
#     0 — a green zero whose cause appears nowhere in the result (#978).
#
# Concurrency: several invocations may hit ONE odoo container at the same time
# (the stack is a shared singleton, not a lock), so every run is isolated
# explicitly — own database, own log file, own HTTP port, no cron threads, a
# bounded per-process connection budget.
#
# Exit codes: 0 ran (check "passed") | 2 usage/environment | 6 tours requested
# but no usable browser | 7 --put could not write the result
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

[ $# -ge 3 ] || { echo "usage: run-tests.sh <repo> <task_id> <module> [--test-tags T|all (default /<module>)] [--db-suffix S] [--with-tours] [--repo-path DIR] [--addons-path P] [--data-dir DIR] [--target-series NN.0] [--odoo-bin PATH] [--odoo-conf PATH] [--put ARTIFACTS_DIR]" >&2; exit 2; }
repo="$1"; task_id="$2"; module="$3"; shift 3

tags=""; suffix=""; with_tours=false; repo_path=""; addons_override=""; data_dir_override=""
target_series=""; put_dir=""; odoo_bin=""; odoo_conf=""
while [ $# -gt 0 ]; do
  case "$1" in
    --test-tags) tags="${2:?}"; shift 2 ;;
    --target-series) target_series="${2:?}"; shift 2 ;;
    --odoo-bin) odoo_bin="${2:?}"; shift 2 ;;
    --odoo-conf) odoo_conf="${2:?}"; shift 2 ;;
    --db-suffix) suffix="${2:?}"; shift 2 ;;
    --repo-path) repo_path="${2:?}"; shift 2 ;;
    --addons-path) addons_override="${2:?}"; shift 2 ;;
    --data-dir) data_dir_override="${2:?}"; shift 2 ;;
    --put) put_dir="${2:?}"; shift 2 ;;
    --with-tours) with_tours=true; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

# Default the filter to the module under test; `all` is the only way to widen it.
# The odoo invocations below pass --test-tags only when $tags is non-empty.
[ -n "$tags" ] || tags="/$module"
[ "$tags" != "all" ] || tags=""

# emit <json> — print the result, and with --put write it into task.json under
# test.<module>. Both result paths (series mismatch and completed run) go through
# here, so a reader never has to ask which one wrote the entry.
emit() {
  printf '%s\n' "$1"
  [ -n "$put_dir" ] || return 0
  if ! printf '%s' "$1" | "$PLUGIN_ROOT/scripts/artifact.sh" set "$put_dir" test --key "$module" - >/dev/null; then
    echo "run-tests.sh: --put $put_dir: could not write the test entry for $module into task.json (see above)" >&2
    exit 7
  fi
}

CREATEDB_ATTEMPTS="${CREATEDB_ATTEMPTS:-5}"
CREATEDB_RETRY_S="${CREATEDB_RETRY_S:-3}"
HTTP_PORT_BASE="${HTTP_PORT_BASE:-18000}"
HTTP_PORT_RANGE="${HTTP_PORT_RANGE:-1000}"
DB_MAXCONN="${DB_MAXCONN:-8}"
WORKTREE_SUBDIR="${WORKTREE_SUBDIR:-.worktrees}"
WORKTREE_ADDONS_BASE="${WORKTREE_ADDONS_BASE:-/mnt/extra-addons}"
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
# Container mode is tried FIRST. Inside the devcontainer `docker` is on PATH too
# (docker-from-docker, the host's daemon), and the project stack can be running
# on that host, so the old docker-first order picked host mode from inside the
# container and ran `docker exec` against a stack this process is not in; Odoo
# died 0.4 s into init with no traceback and every run read `tests_run: 0`
# (#1039). A shell where `odoo` and `pg_isready` both answer IS the stack, and
# needs no PATH shim to say so.
#
# An explicit --odoo-bin is a side core, which exists only in here: it is never
# run through docker exec, so host mode is not an option for it.
odoo_bin_explicit=false
if [ -n "$odoo_bin" ]; then odoo_bin_explicit=true; else odoo_bin="odoo"; fi
mode=""
if command -v "$odoo_bin" >/dev/null 2>&1 && pg_isready >/dev/null 2>&1; then
  mode="container"
elif [ "$odoo_bin_explicit" = true ]; then
  echo "--odoo-bin $odoo_bin needs container mode: '$odoo_bin' must be executable here and pg_isready must answer. A side core is not reachable through docker exec." >&2
  exit 2
elif command -v docker >/dev/null 2>&1 \
   && docker ps --filter "name=^${odoo_c}$" --format '{{.Names}}' | grep -q .; then
  mode="host"
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

# read_version — sets version_raw and major from $odoo_bin (with -c $odoo_conf).
read_version() {
  version_raw="$(odoo_run "$odoo_bin" ${odoo_conf:+-c "$odoo_conf"} --version 2>/dev/null | tail -1)"
  major="$(printf '%s' "$version_raw" | { grep -oE '[0-9]+' || true; } | head -1)"
  major="${major:-0}"
}
read_version

# ---- series mismatch, which is NOT "no tests" either -------------------------
# An 18.0 module on a 19.0 core is not a failing test and not a crash. Odoo reads
# the manifest, logs "The module <m> has an incompatible version, setting
# installable=False" at WARNING, installs nothing, and then prints "0 failed,
# 0 error(s) of 0 tests" and exits 0. Nothing in that result says the module was
# never installed, so it reads as a module that ships no tests (#978).
#
# Compared on the MAJOR alone: a core answers "19.0-20260810" and a target is
# written "19.0", and the build dates never agree. A core whose series could not
# be read at all is a mismatch too — a series that cannot be proved equal is not
# equal, which is the same fail-closed rule the rest of this script follows.
target_major=""
if [ -n "$target_series" ]; then
  target_major="$(printf '%s' "$target_series" | { grep -oE '[0-9]+' || true; } | head -1)"
  [ -n "$target_major" ] || {
    echo "--target-series must name an Odoo series, as in 18.0: got '$target_series'" >&2
    exit 2
  }
fi

# Side core (#1055): the container's major differs from the module's, and nobody
# named a binary. The devcontainer ships odooNN-bin + odooNN.conf for the other
# series; use them when BOTH exist, and re-read the version from the side core so
# the comparison below, and the result, describe the core that will run.
side_note=""
if [ -n "$target_major" ] && [ "$target_major" != "$major" ] \
   && [ "$odoo_bin_explicit" = false ] && [ "$mode" = container ]; then
  side_root="${ODOO_SRC_ROOT:-/var/lib/odoo/src}"
  side_bin="$side_root/odoo${target_major}-bin"
  side_conf="$side_root/odoo${target_major}.conf"
  if [ -x "$side_bin" ] && [ -f "$side_conf" ]; then
    odoo_bin="$side_bin"; odoo_conf="$side_conf"
    read_version
  else
    side_note=" (no side core for ${target_major}.0: looked for $side_bin and $side_conf)"
  fi
fi

case "$major" in
  16|17|18|19) : ;;  # same test-flag surface; adjust here if a version diverges
  *) echo "WARN unrecognized Odoo version '$version_raw' — using standard flags" >&2 ;;
esac

if [ -n "$target_major" ] && [ "$target_major" != "$major" ]; then
  if [ "$major" = 0 ]; then
    mismatch_error="could not read this container's Odoo series (odoo --version said '$version_raw') and the module targets $target_series — refusing to run: a series that cannot be proved equal is not equal${side_note}"
  else
    mismatch_error="this container runs Odoo ${major}.0 but the module targets $target_series — refusing to run: Odoo sets installable=False on a manifest from another series, installs nothing, and still reports 0 failed, 0 error(s) of 0 tests with exit 0${side_note}"
  fi
  # The SAME key set as the completed-run emitter at the bottom of this script, so
  # a reader never has to branch on which path wrote the artifact; the offline
  # suite asserts the two key sets are identical. Nothing ran, so every count is 0
  # and the paths a run would have stated are null rather than invented.
  mismatch_json="$(node -e '
    const [db, module_, version, target, mode, err, bin] = process.argv.slice(1);
    console.log(JSON.stringify({
      passed: false, status: "series_mismatch", error: err,
      db, module: module_,
      odoo_version: version, odoo_bin: bin, module_target_series: target, mode,
      produced_by: "run-tests.sh",
      tests_run: 0,
      suites: [], collected_is_executed: true,
      addons_path: null, data_dir: null,
      tours_declared: 0, tours_run: 0, tours_passed: 0,
      failures: [{ test: module_ + ": Odoo series mismatch", error: err }],
      log_file: null, log_excerpt: "",
      db_dropped: false,
    }));
  ' "$dbname" "$module" "$version_raw" "$target_series" "$mode" "$mismatch_error" "$odoo_bin")"
  emit "$mismatch_json"
  exit 0  # a series mismatch is a RESULT, like a test failure
fi

# ---- addons path: stated, never inherited ------------------------------------
# This used to be "${worktree},$(addons_path read out of odoo.conf)", which made
# the result of this run depend on every unrelated directory some other tool had
# left in the ambient config. One module in one of those trees that cannot be
# imported aborts registry load; collection never starts; the run reports
# tests_run: 0 with an empty failure list — indistinguishable from a module that
# genuinely ships no tests. See issue #887.
#
# So the core addons come from the interpreter that is about to run the tests:
# odoo.__file__ is that process's own answer to "which Odoo is this", and it
# cannot name a sibling tree the module under test has no interest in. Both
# layouts are probed, in this order: the packaged one (<pkg>/addons, holding
# base) and the source one (<root>/addons, holding the community modules).
worktree_addons="$WORKTREE_ADDONS_BASE/$WORKTREE_SUBDIR/task-${task_id}"
if [ -n "$addons_override" ]; then
  addons="$addons_override"
else
  # A side core's `odoo` package is not what plain python3 imports, so the probe
  # gets the source tree beside the binary on PYTHONPATH (see the header).
  probe_path=""
  if [ "$odoo_bin" != odoo ]; then
    bin_abs="$(command -v "$odoo_bin" || true)"
    bin_dir="$(dirname "$bin_abs")"; bin_stem="$(basename "$bin_abs")"; bin_stem="${bin_stem%-bin}"
    for cand in "$bin_dir/$bin_stem" "$bin_dir"; do
      if [ -f "$cand/odoo/__init__.py" ]; then probe_path="${probe_path:+$probe_path:}$cand"; fi
    done
  fi
  probe_py='import odoo, os
pkg = os.path.dirname(os.path.abspath(odoo.__file__))
out = []
for d in (os.path.join(pkg, "addons"), os.path.join(os.path.dirname(pkg), "addons")):
    if d not in out and os.path.isdir(d):
        out.append(d)
print(",".join(out))'
  core_addons="$( { if [ -n "$probe_path" ]; then
      odoo_run_env "PYTHONPATH=$probe_path" python3 -c "$probe_py"
    else
      odoo_run python3 -c "$probe_py"
    fi 2>/dev/null || true; } | tail -1 | tr -d '\r')"
  [ -n "$core_addons" ] || {
    echo "could not resolve Odoo's core addons directory from the running Odoo (importing odoo failed). Pass --addons-path explicitly — addons_path is deliberately not read from odoo.conf." >&2
    exit 2
  }
  addons="${worktree_addons},${core_addons}"
  # Enterprise is OPT-IN, named by env var, and never discovered from odoo.conf:
  # a stale ambient enterprise checkout is precisely what aborted the registry in
  # #887, so it joins the path only when the caller says this module needs it.
  ent="${ODOO_ENTERPRISE_ADDONS:-}"
  if [ -n "$ent" ] && odoo_run test -d "$ent"; then
    addons="${addons},${ent}"
  fi
fi

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

# Odoo's default data_dir is shared, and Odoo APPENDS <data_dir>/addons/<series>
# to the addons path whatever --addons-path said. So an ambient tree unpacked
# there walks straight back into a run that believed it had stated its own path:
# isolating a run needs both flags, and setting only one looks like it worked.
# Per-run, under this run's artifacts dir, so concurrent runs cannot share one.
data_dir="${data_dir_override:-$ARTIFACTS_DIR/data/$dbname}"
mkdir -p "$data_dir" 2>/dev/null || true
odoo_run mkdir -p "$data_dir" >/dev/null 2>&1 || true

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
  odoo_run_env "$browser_env" "$odoo_bin" ${odoo_conf:+-c "$odoo_conf"} \
    --addons-path="$addons" --data-dir="$data_dir" \
    -d "$dbname" -i "$module" \
    --test-enable ${tags:+--test-tags "$tags"} \
    --http-port="$port" --max-cron-threads=0 --db_maxconn="$DB_MAXCONN" \
    "${screenshot_flags[@]}" \
    --stop-after-init \
    >"$log" 2>&1
else
  odoo_run "$odoo_bin" ${odoo_conf:+-c "$odoo_conf"} \
    --addons-path="$addons" --data-dir="$data_dir" \
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

# ---- registry aborted, which is NOT "no tests" -------------------------------
# Registry load happens before collection. When it dies — a module in the addons
# path that cannot be imported, a failed odoo.modules.loading phase — no test is
# ever collected, so tests_run is 0 and the failure list is empty, which reads
# exactly like a module that ships no tests. Both used to be reported the same
# way, and only one of them is safe to wave through.
#
# The decisive line is looked for BEFORE the first "Starting <test>" line: an
# ImportError raised inside a test that did run is a test failure, already
# handled, and must not be re-labelled as an aborted registry.
first_start="$( { grep -nE 'Starting [A-Za-z_][A-Za-z0-9_.]*\.[A-Za-z0-9_]+ \.\.\.' "$log" || true; } | head -1 | cut -d: -f1)"
pre_log="$log"
if [ -n "$first_start" ] && [ "$first_start" -gt 1 ] 2>/dev/null; then
  pre_log="${log}.pre"
  head -n "$((first_start - 1))" "$log" >"$pre_log"
fi

registry_aborted=false
registry_error=""
if [ -z "$first_start" ]; then
  # Most specific first: the import that actually killed the registry is far more
  # useful to whoever has to fix it than "Failed to load registry".
  for pat in '(ModuleNotFoundError|ImportError):' 'odoo\.modules\.(loading|registry)' '[Ff]ailed to (load|initialize) (the )?(registry|database)'; do
    registry_error="$( { grep -m1 -E "$pat" "$pre_log" || true; } | head -1)"
    if [ -n "$registry_error" ]; then registry_aborted=true; break; fi
  done
  # The looser rule from the issue: zero tests plus a traceback is a run that
  # died, whatever logger happened to report it.
  if [ "$registry_aborted" = false ] && [ "$tests_run" -eq 0 ] \
     && grep -q 'Traceback (most recent call last):' "$log"; then
    registry_aborted=true
    registry_error="$( { grep -E '^[A-Za-z_][A-Za-z0-9_.]*(Error|Exception|Failure|Exit):' "$log" || true; } | tail -1)"
    [ -n "$registry_error" ] || registry_error="$(tail -1 "$log")"
  fi
fi
[ "$pre_log" = "$log" ] || rm -f "$pre_log"

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

# An aborted registry is never a pass, whatever the exit code and the (absent)
# failure list say.
status=failed
if [ "$registry_aborted" = true ]; then
  passed=false
  status=registry_aborted
elif [ "$passed" = true ]; then
  status=passed
fi

# Drop now (trap remains as backstop) so db_dropped is accurate in the JSON.
cleanup
trap - EXIT

result_json="$(node -e '
  const fs = require("fs");
  const [logFile, passed, dbname, module_, version, dropped, testsRun,
         toursDeclared, toursRun, toursPassed, tourGap, mode, status,
         registryError, addonsPath, dataDir, targetSeries, odooBin] = process.argv.slice(1);
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
  // A registry abort leaves NO FAIL:/ERROR: line to extract, so the list would be
  // empty — the exact shape that reads as "nothing went wrong here".
  if (status === "registry_aborted") {
    failures.unshift({
      test: module_ + ": registry aborted before any test was collected",
      error: (registryError || "the registry failed to load and no test was collected").trim().slice(0, 300),
    });
  }

  // ---- per-suite counts ------------------------------------------------------
  // Odoo names the module in the LOGGER of each test record
  // ("odoo.addons.<module>.tests.<file>"), and on 17+ also inside the message
  // ("Starting <module>.<Class>.<test> ..."), so a per-module tally is honest.
  //
  // What the log does NOT expose, on any of 16-19, is a per-module COLLECTED
  // count distinct from the executed one: the only collection-shaped line Odoo
  // emits is the per-phase "... of N tests when loading database", which names
  // no module and is itself printed after those N tests ran. So collected and
  // executed carry the same number here and collected_is_executed says so,
  // rather than inventing a count nobody logged. The run-level comparison the
  // gate wants is tests_run against a registry that aborted — which is what
  // status reports.
  const SUITE_MSG = /Starting\s+([A-Za-z0-9_]+)\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+\s+\.\.\./;
  const moduleOf = (line) => {
    const logger = line.match(/odoo\.addons\.([A-Za-z0-9_]+)/);
    if (logger) return logger[1];
    const msg = line.match(SUITE_MSG);
    if (msg) return msg[1];
    return module_;  // the run installs exactly one module; attribute to it
  };
  const suiteMap = new Map();
  const suite = (name) => {
    if (!suiteMap.has(name)) suiteMap.set(name, { module: name, collected: 0, executed: 0, failed: 0 });
    return suiteMap.get(name);
  };
  for (const line of lines) {
    if (/Starting\s+[A-Za-z_][A-Za-z0-9_.]*\.[A-Za-z0-9_]+\s+\.\.\./.test(line)) {
      const s = suite(moduleOf(line));
      s.collected += 1;
      s.executed += 1;
    } else if (/(?:FAIL|ERROR): /.test(line)) {
      suite(moduleOf(line)).failed += 1;
    }
  }
  const suites = [...suiteMap.values()].sort((a, b) => a.module.localeCompare(b.module));

  const excerpt = lines.slice(-50).join("\n");
  console.log(JSON.stringify({
    passed: passed === "true", status,
    error: status === "registry_aborted" ? (registryError || "").trim().slice(0, 300) || null : null,
    db: dbname, module: module_,
    // odoo_version is the core this run used; module_target_series is what the
    // caller said the module is FOR, null when nobody said. The pair is the only
    // way a reader can tell a real zero from a series mismatch (#978).
    // odoo_bin: the executable this run used, "odoo" unless a side core was
    // selected (--odoo-bin or the --target-series auto-select, #1055).
    odoo_version: version, odoo_bin: odooBin, module_target_series: targetSeries || null, mode,
    // Provenance, not a number: downstream readers cannot otherwise tell this
    // artifact from one an agent hand-assembled around its own claim (#982).
    produced_by: "run-tests.sh",
    tests_run: Number(testsRun) || 0,
    suites, collected_is_executed: true,
    addons_path: addonsPath, data_dir: dataDir,
    tours_declared: Number(toursDeclared) || 0,
    tours_run: Number(toursRun) || 0,
    tours_passed: Number(toursPassed) || 0,
    failures, log_file: logFile, log_excerpt: excerpt,
    db_dropped: dropped === "true",
  }));
' "$log" "$passed" "$dbname" "$module" "$version_raw" "$db_dropped" "$tests_run" \
  "$tours_declared" "$tours_run" "$tours_passed" "$tour_gap" "$mode" "$status" \
  "$registry_error" "$addons" "$data_dir" "$target_series" "$odoo_bin")"
emit "$result_json"

[ "$passed" = true ] || exit 0  # a test failure is a RESULT, not a script error
