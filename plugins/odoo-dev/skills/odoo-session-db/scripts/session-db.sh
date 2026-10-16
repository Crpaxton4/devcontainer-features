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
#           database named here, or was not dropped, because then two tools
#           disagree about which database to drop.
#
# The session id comes from --session-id, else $SESSION_DB_ID, else random.
#
# Exit codes: 0 ok | 2 usage | 3 name or drop disagreement (run only)
# Env: SESSION_DB_RUN_TESTS overrides the run-tests.sh path (tests only).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_TESTS="${SESSION_DB_RUN_TESTS:-$SCRIPT_DIR/../../odoo-test-run/scripts/run-tests.sh}"

# Postgres cuts identifiers at 63 bytes and does it silently, so two long names
# that differ only past the cut would be one database.
PG_NAME_MAX=63

die() { echo "session-db.sh: $*" >&2; exit "${2:-2}"; }

# Same normalisation as run-tests.sh: lowercase, then strip. The agreement is
# checked on every `run`, so a change there that is not made here is caught.
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
  local proj name
  proj="$(norm_repo "$1")"
  [ -n "$proj" ] || die "repo name normalizes to nothing: $1"
  name="${proj}_test_${2}${3}"
  [ "${#name}" -le "$PG_NAME_MAX" ] || die "database name is ${#name} bytes, over the Postgres limit of $PG_NAME_MAX: $name"
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
    --session-id) sid="${2:?--session-id needs a value}"; shift 2 ;;
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
      [ "$a" != "--db-suffix" ] || die "--db-suffix is set by this script; do not pass it"
    done
    suffix="$(make_suffix "$sid")"
    want="$(make_name "$repo" "$task_id" "$suffix")"
    out="$("$RUN_TESTS" "$repo" "$task_id" "$module" --db-suffix "$suffix" "$@")" || exit $?
    last="$(printf '%s\n' "$out" | tail -1)"
    got="$(printf '%s' "$last" | node -e 'const j=JSON.parse(require("fs").readFileSync(0,"utf8"));console.log(j.db+" "+j.db_dropped)' 2>/dev/null)" \
      || die "run-tests.sh did not end with a JSON result" 3
    printf '%s\n' "$out"
    [ "${got% *}" = "$want" ] || die "run-tests.sh used database '${got% *}', this script named '$want'" 3
    [ "${got#* }" = "true" ] || die "database $want was not dropped" 3 ;;
  *) die "unknown command: $cmd" ;;
esac
