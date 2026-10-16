#!/usr/bin/env bash
# session-db.sh — give one session its own throwaway test database name, and run
# the tests on it.
#
# Usage: session-db.sh suffix [--session-id ID]
#        session-db.sh name <repo> <task_id> [--session-id ID]
#        session-db.sh run  <repo> <task_id> <module> [--session-id ID] [run-tests.sh options]
#
# This WRAPS run-tests.sh. It adds no naming scheme of its own: the database is
# still <project>_test_<task_id><suffix>, and the only thing added is a suffix
# that is unique to the session, passed through run-tests.sh's own --db-suffix.
#
# Why: run-tests.sh runs `dropdb --if-exists` on its database name BEFORE it
# creates it. Two sessions on one task in one project, with no suffix, share one
# name, so the second drops the first one's database in the middle of its run.
# Postgres is one shared server for every container on the machine, so the name
# is the only separation there is.
#
#   suffix  prints _s<8 hex>. With --session-id the value is a hash of the id, so
#           every call in one session gives the same suffix. Without it the
#           value is random, so keep it (or pass --session-id).
#   name    prints the database name run-tests.sh will use for this session.
#   run     runs run-tests.sh with --db-suffix and prints its result JSON. Fails
#           closed (exit 3) if the database run-tests.sh reports is not the
#           database named here, or was not dropped (a series_mismatch result
#           creates no database, so it is exempt). The result is printed BEFORE
#           the checks, so a failing run or a failed check never hides it. When
#           run-tests.sh itself exits non-zero, that code is returned.
#           The check REPORTS a disagreement late, it does not prevent one:
#           run-tests.sh runs dropdb on the name it computed before this script
#           can look, and it has no way to be told a name. The normalisation
#           here is a second copy of its own. No input is known where they differ.
#
# The session id comes from --session-id, else $SESSION_DB_ID, else random. An
# empty or missing --session-id value is an error, never a fall back to random.
#
# Exit codes: 0 ok | 2 usage | 3 name or drop disagreement (run only) | else the
# exit code of run-tests.sh
# Env: SESSION_DB_RUN_TESTS overrides the run-tests.sh path (tests only).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_TESTS="${SESSION_DB_RUN_TESTS:-$SCRIPT_DIR/../../odoo-test-run/scripts/run-tests.sh}"

# Postgres cuts identifiers at 63 bytes and does it silently, so two long names
# that differ only past the cut would be one database.
PG_NAME_MAX=63

die() { echo "session-db.sh: $*" >&2; exit "${2:-2}"; }

# Same normalisation as run-tests.sh: lowercase, then strip. The agreement is
# checked on every `run`, but late (see the header).
norm_repo() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9_-]//g'; }

make_suffix() { # make_suffix <session-id or empty>
  local h
  if [ -n "$1" ]; then
    h="$(printf '%s' "$1" | sha1sum | cut -c1-8)"
  else
    h="$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')"
  fi
  printf '_s%s' "$h"
}

make_name() { # make_name <repo> <task_id> <suffix>
  local proj name nbytes
  case "$2" in ''|*[!0-9A-Za-z_-]*) die "task_id must match [0-9A-Za-z_-]+: $2" ;; esac
  proj="$(norm_repo "$1")"
  [ -n "$proj" ] || die "repo name normalizes to nothing: $1"
  name="${proj}_test_${2}${3}"
  # Bytes, not characters: ${#name} counts characters in a UTF-8 locale.
  nbytes="$(printf '%s' "$name" | LC_ALL=C wc -c | tr -d ' ')"
  [ "$nbytes" -le "$PG_NAME_MAX" ] || die "database name is $nbytes bytes, over the Postgres limit of $PG_NAME_MAX: $name"
  printf '%s' "$name"
}

[ $# -ge 1 ] || die "usage: session-db.sh suffix|name|run ... (see the header)"
cmd="$1"; shift

# Take --session-id out of the argument list wherever it sits. Every other
# argument is kept in order for run-tests.sh.
sid="${SESSION_DB_ID:-}"
rest=()
while [ $# -gt 0 ]; do
  case "$1" in
    --session-id)
      if [ $# -lt 2 ] || [ -z "$2" ]; then die "--session-id needs a non-empty value"; fi
      sid="$2"; shift 2 ;;
    *) rest+=("$1"); shift ;;
  esac
done
set -- "${rest[@]+"${rest[@]}"}"

case "$cmd" in
  suffix)
    [ $# -eq 0 ] || die "suffix takes no arguments besides --session-id"
    make_suffix "$sid"; echo ;;
  name)
    [ $# -eq 2 ] || die "usage: session-db.sh name <repo> <task_id> [--session-id ID]"
    make_name "$1" "$2" "$(make_suffix "$sid")"; echo ;;
  run)
    [ $# -ge 3 ] || die "usage: session-db.sh run <repo> <task_id> <module> [--session-id ID] [run-tests.sh options]"
    repo="$1"; task_id="$2"; module="$3"; shift 3
    for a in "$@"; do
      case "$a" in
        --db-suffix|--db-suffix=*) die "--db-suffix is set by this script; do not pass it" ;;
      esac
    done
    suffix="$(make_suffix "$sid")"
    want="$(make_name "$repo" "$task_id" "$suffix")"
    rc=0
    out="$("$RUN_TESTS" "$repo" "$task_id" "$module" --db-suffix "$suffix" "$@")" || rc=$?
    # Print the result BEFORE any check, so no check can hide it.
    printf '%s\n' "$out"
    # The last line that parses as a JSON object is the result, so a trailing
    # warning line cannot hide it.
    got="$(printf '%s\n' "$out" | node -e '
      const lines = require("fs").readFileSync(0, "utf8").split("\n").reverse();
      for (const l of lines) {
        try {
          const j = JSON.parse(l);
          if (j && typeof j === "object") { console.log(j.db + " " + j.db_dropped + " " + j.status); process.exit(0); }
        } catch (e) { /* not JSON */ }
      }
      process.exit(1);' 2>/dev/null)" || {
      [ "$rc" -eq 0 ] || exit "$rc"
      die "run-tests.sh printed no JSON result" 3
    }
    read -r got_db got_dropped got_status <<<"$got"
    [ "$got_db" = "$want" ] || die "run-tests.sh used database '$got_db', this script named '$want'" 3
    # A series_mismatch result creates no database, so there is nothing to drop.
    if [ "$got_dropped" != "true" ] && [ "$got_status" != "series_mismatch" ]; then
      die "database $want was not dropped" 3
    fi
    exit "$rc" ;;
  *) die "unknown command: $cmd" ;;
esac
