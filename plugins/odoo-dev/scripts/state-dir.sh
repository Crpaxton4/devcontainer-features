#!/usr/bin/env bash
# state-dir.sh — the one place that knows where odoo-dev keeps its state.
#
# Every command used to open with an inline `!`…`` preamble that expanded
# ${ODOO_DEV_STATE_DIR:-$HOME/.local/share/odoo-dev} twice, validated the task id
# with grep, created the directory with mkdir, and fell back with `||` — four
# commands' worth of shell in one backtick. A worktree-isolated Claude Code session
# refuses that: it cannot statically prove what a runtime-computed value inside an
# `&&`/`||` chain will run, so it declines the whole expansion and the command never
# loads. `/odoo-dev:pr` was therefore unreachable from exactly the place
# odoo-dev-builder is documented to work — its own worktree.
#
# So the shell moved into a script and the preamble became one plain call with a
# literal path. This file is that script, and it is also the single definition of
# the rules the rest of the plugin has to agree on:
#
#   * the state dir is $ODOO_DEV_STATE_DIR, defaulting to $HOME/.local/share/odoo-dev
#   * a task id is ^[0-9]+$ and nothing else, so a word of prose can never become a
#     directory (see gate 18 in validate.sh, and the `tasks/Create` bug it exists for)
#   * a release directory is releases/<from>-to-<to>, with each branch name plain
#     ([A-Za-z0-9][A-Za-z0-9._-]*) and containing no `..`
#
# Two ways in:
#
#   sourced — defines odoo_dev_state_dir, odoo_dev_artifacts_dir and
#             odoo_dev_valid_branch, and does nothing else. artifact.sh sources it
#             so that a bare task id resolves the same way everywhere.
#
#   run     — the CLI the command preambles call:
#
#               state-dir.sh                                             # the state dir
#               state-dir.sh task    [--create] [--else TEXT] -- <task-arg>
#               state-dir.sh release [--create] [--else TEXT] -- <task-arg> <from> <to>
#
#             `task` and `release` ALWAYS exit 0: a non-zero exit inside `!`…``
#             aborts the whole command expansion, so an unusable argument prints the
#             --else marker on stdout instead and the command body says what the
#             dispatched agent must do when it reads a marker rather than a path.
#             Without --create the directory must already exist. `release` takes the
#             command's first argument too, because the route is only the release
#             route when that argument is the literal word `release`.
#
#             A malformed CLI invocation — an unknown mode, the wrong number of
#             operands — is a bug in the command file rather than something a user
#             typed, so it still exits 2 rather than hiding behind the marker.

# The state dir, with the documented default. No trailing slash, ever.
odoo_dev_state_dir() {
  printf '%s' "${ODOO_DEV_STATE_DIR:-${HOME:-}/.local/share/odoo-dev}"
}

# The disambiguation rule for every <artifacts_dir> argument in this plugin: a bare
# Odoo task id resolves against the state dir, and ANYTHING else is a path, passed
# through untouched. Digits-only is the whole test, so no path can be mistaken for
# an id — a path has a `/`, a `.` or a letter in it — and every existing caller that
# already passes a fully-resolved directory keeps working.
odoo_dev_artifacts_dir() {
  case "$1" in
    '' | *[!0-9]*) printf '%s' "$1" ;;
    *) printf '%s/tasks/%s' "$(odoo_dev_state_dir)" "$1" ;;
  esac
}

# A branch name plain enough to be half of a directory name. `..` is rejected
# separately because `.` is otherwise legal, and `releases/../..` is the traversal
# this whole check exists to stop.
odoo_dev_valid_branch() {
  case "$1" in
    [A-Za-z0-9]*) ;;
    *) return 1 ;;
  esac
  case "$1" in
    *[!A-Za-z0-9._-]*) return 1 ;;
  esac
  case "$1" in
    *..*) return 1 ;;
  esac
  return 0
}

# Sourced: the functions above are the whole payload. Everything below is the CLI.
[ "${BASH_SOURCE[0]}" = "${0}" ] || return 0

set -euo pipefail

usage() {
  echo "usage: state-dir.sh [task|release [--create] [--else TEXT] -- <args>]" >&2
  exit 2
}

[ $# -gt 0 ] || { printf '%s\n' "$(odoo_dev_state_dir)"; exit 0; }

mode="$1"
shift
case "$mode" in
  task | release) ;;
  *) usage ;;
esac

marker='NO ARTIFACTS DIRECTORY'
create=0
while [ $# -gt 0 ]; do
  case "$1" in
    --create) create=1; shift ;;
    --else)
      [ $# -ge 2 ] || usage
      marker="$2"; shift 2 ;;
    --) shift; break ;;
    *) break ;;
  esac
done

# From here on nothing but a malformed invocation may exit non-zero.
dir=""
if [ "$mode" = task ]; then
  [ $# -eq 1 ] || usage
  case "$1" in
    '' | *[!0-9]*) ;;
    *) dir="$(odoo_dev_state_dir)/tasks/$1" ;;
  esac
else
  [ $# -eq 3 ] || usage
  if [ "$1" = release ] && odoo_dev_valid_branch "$2" && odoo_dev_valid_branch "$3"; then
    dir="$(odoo_dev_state_dir)/releases/$2-to-$3"
  fi
fi

[ -n "$dir" ] || { printf '%s\n' "$marker"; exit 0; }

if [ "$create" -eq 1 ]; then
  mkdir -p "$dir" 2>/dev/null || { printf '%s\n' "$marker"; exit 0; }
elif [ ! -d "$dir" ]; then
  printf '%s\n' "$marker"
  exit 0
fi

printf '%s\n' "$dir"
