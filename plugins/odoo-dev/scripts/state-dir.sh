#!/usr/bin/env bash
# state-dir.sh — the one place that knows where odoo-dev keeps its state.
#
# Every command used to open with an inline `!`…`` preamble that expanded
# the state dir path twice, validated the task id
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
#   * the state dir is ${ODOO_DEV_HOME:-$HOME/.config/odoo-dev} and nothing else.
#     The devcontainer exports ODOO_DEV_HOME=/usr/local/share/odoo-dev, where the
#     personal-features Feature bind-mounts the host ~/.config/odoo-dev. The host
#     default is that same bind source, so host and containers read one repo map.
#     Nothing in this plugin may restate the default; every caller resolves it
#     through here. This script never creates the directory: `odoo-dev` creates it
#     on first use and `odoo-dev doctor` reports it.
#   * a task id is ^[0-9]+$ and nothing else, so a word of prose can never become a
#     directory (see gate 18 in validate.sh, and the `tasks/Create` bug it exists for)
#   * a task's artifacts live in <state>/tasks/<id>/ — one task.json (artifact.sh)
#     plus whatever deliverables a stage puts beside it. The release route keeps no
#     directory at all: its scripts are re-run safe and it writes nothing to disk.
#
# Two ways in:
#
#   sourced — defines odoo_dev_state_dir and odoo_dev_artifacts_dir, and does
#             nothing else. artifact.sh sources it so that a bare task id resolves
#             the same way everywhere.
#
#   run     — the CLI the command preambles call:
#
#               state-dir.sh                                      # the state dir
#               state-dir.sh task [--create] [--else TEXT] [--missing TEXT] -- <task-arg>
#
#             `task` ALWAYS exits 0: a non-zero exit inside `!`…`` aborts the whole
#             command expansion, so an unusable argument prints the --else marker on
#             stdout instead and the command body says what the dispatched agent must
#             do when it reads a marker rather than a path. Without --create the
#             directory must already exist. --else is the marker for an argument
#             that is not a task id. --missing is the marker for a valid task id
#             whose directory does not exist yet; without --missing, --else covers both.
#
#             A malformed CLI invocation — an unknown mode, the wrong number of
#             operands — is a bug in the command file rather than something a user
#             typed, so it still exits 2 rather than hiding behind the marker.

# The state dir, with the documented default. No trailing slash, ever.
odoo_dev_state_dir() {
  printf '%s' "${ODOO_DEV_HOME:-$HOME/.config/odoo-dev}"
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

# Sourced: the functions above are the whole payload. Everything below is the CLI.
[ "${BASH_SOURCE[0]}" = "${0}" ] || return 0

set -euo pipefail

usage() {
  echo "usage: state-dir.sh [task [--create] [--else TEXT] [--missing TEXT] -- <task-arg>]" >&2
  exit 2
}

[ $# -gt 0 ] || { printf '%s\n' "$(odoo_dev_state_dir)"; exit 0; }

mode="$1"
shift
case "$mode" in
  task) ;;
  *) usage ;;
esac

marker='NO ARTIFACTS DIRECTORY'
missing=""
create=0
while [ $# -gt 0 ]; do
  case "$1" in
    --create) create=1; shift ;;
    --else)
      [ $# -ge 2 ] || usage
      marker="$2"; shift 2 ;;
    --missing)
      [ $# -ge 2 ] || usage
      missing="$2"; shift 2 ;;
    --) shift; break ;;
    *) break ;;
  esac
done

# From here on nothing but a malformed invocation may exit non-zero.
[ $# -eq 1 ] || usage
dir=""
case "$1" in
  '' | *[!0-9]*) ;;
  *) dir="$(odoo_dev_state_dir)/tasks/$1" ;;
esac

[ -n "$dir" ] || { printf '%s\n' "$marker"; exit 0; }

if [ "$create" -eq 1 ]; then
  mkdir -p "$dir" 2>/dev/null || { printf '%s\n' "$marker"; exit 0; }
elif [ ! -d "$dir" ]; then
  printf '%s\n' "${missing:-$marker}"
  exit 0
fi

printf '%s\n' "$dir"
