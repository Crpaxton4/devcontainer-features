#!/usr/bin/env bash
# writeback.sh — deterministic Odoo chatter/activity writeback over `odoo-sdk cmd`.
#
# Why: writebacks used to ride on "probe the mcp__odoo-mcp__* tools and pick one",
# which made every PR/release note a model-driven judgement call — and before the
# activity tools existed, activity intent was smuggled through an `[ACTIVITY]`
# marker inside a task_note. Reads stay MCP-tool, model-driven; writebacks are
# script-driven and deterministic. This script is that writer: three verbs, each
# a fixed mapping onto registry commands the CLI serves.
#
# Usage:
#   writeback.sh note <task_id> <text> [--dedupe-key KEY]
#       -> odoo-sdk cmd get_tasks        --args '{"domain":[["id","=",N]],"limit":1}'
#          then
#          odoo-sdk cmd start_task       --args '{"task_id":N,"task_name":...,
#            "project_id":...,"project_name":...}'
#          (the ensure-session step — see "Sessions" below; start_task needs the
#           resolved identity, which is why the task is read first)
#       -> odoo-sdk cmd task_note        --args '{"task_id":N,"note":TEXT[,"dedupe_key":KEY]}'
#   writeback.sh activity <task_id> --summary TEXT
#                [--type NAME] [--note TEXT] [--deadline YYYY-MM-DD] [--user-id N]
#       -> odoo-sdk cmd search_activity_types --args '{"query":NAME,"res_model":"project.task"}'
#          (only with --type; exact case-insensitive name match wins, else a single
#           candidate is accepted, else this is an error naming every candidate)
#       -> odoo-sdk cmd schedule_activity --args '{"res_id":N,"summary":...,
#            "activity_type":<resolved id>,"note":...,"date_deadline":...,"user_id":...}'
#          (res_model is left to the command's project.task default)
#   writeback.sh done <task_id> --match PATTERN [--feedback TEXT]
#       -> odoo-sdk cmd get_activities   --args '{"res_id":N}'
#          (PATTERN is a case-insensitive substring matched against each activity's
#           summary and activity-type name; exactly one activity must match)
#       -> odoo-sdk cmd mark_activity_done --args '{"activity_id":ID,"feedback":TEXT}'
#
# The 500-character chatter cap is pre-checked here for `note` so a too-long note
# fails fast with the count in the message — but the SDK's
# enforce_chatter_body_limit (commands/builtin/task_note.py) stays authoritative:
# whatever passes the pre-check is still judged server-side, and rejected, never
# truncated, when it is over.
#
# Preflight (#902 #889): `odoo-sdk cmd` is what every verb below rides on, and it
# has existed since #727 (cli/__main__.py registers the `cmd` subparser and routes
# it to cmd_cmd). Both issues were filed from machines whose *installed* odoo-sdk
# predated that release, so the binary was on PATH but argparse rejected `cmd` and
# the caller got a usage dump on stderr and nothing on stdout. Checking that the
# binary exists is therefore not enough: `odoo-sdk cmd --help` is probed too, and a
# CLI that cannot serve `cmd` fails here as MissingCLI (exit 4) naming the upgrade.
#
# Sessions (#891): `task_note` requires a RUNNING tracking session
# (require_active_run) and raises TaskNotRunningError without one — which bites
# exactly when the branch was cut by hand rather than driven through start_task.
# The `note` verb therefore ensures the session itself: it resolves the task's
# identity with `get_tasks` and calls `start_task`, which is idempotent in every
# session state (already RUNNING -> already_running: true and zero side effects;
# AWAITING_ANSWERS / STOPPED -> reopened in place; none -> a fresh run). The
# session is deliberately NOT stopped afterwards: the note is part of running work,
# and stopping a session this script did not own would truncate someone's tracking.
# `activity` and `done` do not ensure a session, because none of the commands they
# dispatch (search_activity_types / schedule_activity / get_activities /
# mark_activity_done) calls require_active_run — starting a run for them would
# leave a session nobody opened and nobody stops.
#
# Output: on success, the CLI's own JSON result, verbatim, as the last stdout
# line. On a CLI failure, the CLI's JSON error envelope {"error":{"type",
# "message"}} is passed through verbatim on stdout and this script exits with the
# CLI's exit code (1 boundary, 2 usage). Errors raised here (ambiguous type, no
# matching activity, over-long note, missing CLI) print the same envelope shape.
# That guarantee is absolute (#902 #889): output the CLI produced that does not
# parse as JSON — a usage dump, a traceback, an empty stdout — is wrapped into
# {"error":{"type":"NonJsonOutput","message":...}} carrying the raw text (stdout
# and stderr, trimmed and capped), so a caller parsing stdout as JSON always has
# something to parse.
#
# Exit codes: 0 success
#           | 1/2 passed through from the CLI (boundary / usage)
#           | 2 argument or pre-check error
#           | 3 resolution failure (no or ambiguous type / activity match,
#             unresolvable task identity)
#           | 4 odoo-sdk is not on PATH, or is too old to serve `cmd` (#727),
#             or answered a dispatch with non-JSON while exiting 0
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: writeback.sh note <task_id> <text> [--dedupe-key KEY]
       writeback.sh activity <task_id> --summary TEXT [--type NAME] [--note TEXT]
                    [--deadline YYYY-MM-DD] [--user-id N]
       writeback.sh done <task_id> --match PATTERN [--feedback TEXT]
EOF
  exit 2
}

# fail_json <exit_code> <type> <message> — the CLI's envelope shape, for errors
# raised on this side of the dispatch, so a caller parses one shape everywhere.
fail_json() {
  node -e 'console.log(JSON.stringify({error: {type: process.argv[1], message: process.argv[2]}}))' \
    "$2" "$3"
  exit "$1"
}

# is_json <text> — true when <text> parses as JSON. Any JSON type counts, not just
# an object: the lookups below (search_activity_types, get_activities, get_tasks)
# legitimately answer with an array, and get_task with null.
is_json() {
  printf '%s' "$1" | node -e '
    let d = "";
    process.stdin.on("data", c => { d += c; });
    process.stdin.on("end", () => { try { JSON.parse(d); } catch (e) { process.exit(1); } });
  '
}

# The child's stderr, kept out of its stdout so a successful result is never
# polluted by a warning — and still available verbatim when the output is garbage.
# Created on the first dispatch rather than at load time so the preflight errors
# below still print their envelope on a PATH too sparse to have mktemp on it.
CLI_ERR=""
cli_err_file() {
  [ -n "$CLI_ERR" ] && return 0
  CLI_ERR="$(mktemp "${TMPDIR:-/tmp}/writeback-stderr.XXXXXX")"
  trap 'rm -f "$CLI_ERR"' EXIT
}

# cli <name> <args_json> — one dispatch. Sets CLI_OUT/CLI_RC rather than exiting,
# because a caller may need the stdout (a lookup) before deciding what comes next.
# CLI_OUT is guaranteed to be JSON afterwards (#902 #889): output that does not
# parse — an argparse usage dump, a traceback, an empty stdout — becomes a
# NonJsonOutput envelope carrying the raw stdout+stderr, so every exit path of this
# script prints one parseable shape.
CLI_OUT=""
CLI_RC=0
cli() {
  CLI_RC=0
  cli_err_file
  : > "$CLI_ERR"
  CLI_OUT="$(odoo-sdk cmd "$1" --args "$2" 2>"$CLI_ERR")" || CLI_RC=$?
  if is_json "$CLI_OUT"; then
    return 0
  fi
  CLI_OUT="$(printf '%s\n%s\n' "$CLI_OUT" "$(cat "$CLI_ERR")" | node -e '
    const name = process.argv[1];
    const code = process.argv[2];
    let raw = require("fs").readFileSync(0, "utf8").trim();
    if (raw.length > 2000) raw = raw.slice(0, 2000) + " [...truncated]";
    console.log(JSON.stringify({error: {type: "NonJsonOutput", message:
      "odoo-sdk cmd " + name + " exited " + code + " without printing JSON — raw " +
      "output: " + (raw || "(none)")}}));
  ' "$1" "$CLI_RC")"
  # A child that failed keeps its own code; one that exited 0 with garbage is a
  # broken CLI, which is what 4 means here.
  [ "$CLI_RC" -ne 0 ] || CLI_RC=4
}

# die_cli — pass the CLI's error envelope through verbatim and exit with its code.
die_cli() {
  printf '%s\n' "$CLI_OUT"
  exit "$CLI_RC"
}

[ $# -ge 1 ] || usage
verb="$1"; shift
case "$verb" in note|activity|done) : ;; *) echo "unknown subcommand: $verb" >&2; usage ;; esac

[ $# -ge 1 ] || usage
task_id="$1"; shift
case "$task_id" in
  ''|*[!0-9]*) echo "task_id must be numeric: $task_id" >&2; exit 2 ;;
esac

command -v odoo-sdk >/dev/null 2>&1 \
  || fail_json 4 "MissingCLI" "odoo-sdk is not on PATH — install it with: python3 -m pip install ./libraries/odoo_sdk"

# The binary existing is not the same as the binary serving `cmd` (#902 #889): an
# odoo-sdk installed before #727 has no `cmd` subparser and answers every dispatch
# below with an argparse usage dump on stderr and nothing on stdout. Probe it once,
# here, so that case is a named error with an upgrade in it rather than a usage
# dump attributed to whichever verb happened to run first.
probe_rc=0
probe_out="$(odoo-sdk cmd --help 2>&1)" || probe_rc=$?
probe_out="${probe_out:0:400}"  # argparse names every choice it does accept; that
                                # list is the useful half and it is short.
[ "$probe_rc" -eq 0 ] || fail_json 4 "MissingCLI" \
  "the installed odoo-sdk does not serve 'odoo-sdk cmd' — it predates the cmd subcommand (#727), which every writeback here dispatches through. Upgrade it with: python3 -m pip install --upgrade ./libraries/odoo_sdk (or: uv tool install --force ./libraries/odoo_sdk). Probe 'odoo-sdk cmd --help' exited ${probe_rc}: ${probe_out}"

# ensure_session <task_id> — #891. task_note requires a RUNNING tracking session
# and raises TaskNotRunningError without one, so the writeback creates it rather
# than making the caller discover the requirement at the end of a task. start_task
# is idempotent in every session state, but it takes *resolved* identity (task
# name, project id and name — the MCP tool does that resolution), so the task is
# read first. The session is left running on purpose: the note is part of the work.
ensure_session() {
  cli get_tasks "{\"domain\":[[\"id\",\"=\",$1]],\"limit\":1}"
  [ "$CLI_RC" -eq 0 ] || die_cli
  identity_rc=0
  start_args="$(node -e '
    const rows = JSON.parse(process.argv[1]);
    const row = Array.isArray(rows) ? rows[0] : null;
    if (!row) process.exit(20);
    const project = row.project_id;
    if (!Array.isArray(project) || project.length !== 2) process.exit(21);
    console.log(JSON.stringify({
      task_id: Number(process.argv[2]),
      task_name: String(row.name),
      project_id: Number(project[0]),
      project_name: String(project[1]),
    }));
  ' "$CLI_OUT" "$1")" || identity_rc=$?
  if [ "$identity_rc" -eq 20 ]; then
    fail_json 3 "ValueError" "task $1 does not exist (or is not visible to this login), so no tracking session can be started for it"
  elif [ "$identity_rc" -ne 0 ]; then
    fail_json 3 "ValueError" "task $1 has no project, and start_task needs a resolved project — start the session yourself (mcp__odoo-mcp__start_task) and rerun"
  fi
  cli start_task "$start_args"
  [ "$CLI_RC" -eq 0 ] || die_cli
}

case "$verb" in
  note)
    [ $# -ge 1 ] || { echo "note needs the note text" >&2; usage; }
    text="$1"; shift
    dedupe_key=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --dedupe-key) dedupe_key="${2:?--dedupe-key needs a value}"; shift 2 ;;
        *) echo "unknown option: $1" >&2; usage ;;
      esac
    done
    # UX pre-check only; the SDK's enforce_chatter_body_limit is authoritative.
    # Counted in code points (Array.from), matching Python's len() server-side.
    chars="$(node -e 'console.log(Array.from(process.argv[1]).length)' "$text")"
    [ "$chars" -le 500 ] || fail_json 2 "ValueError" \
      "note is ${chars} characters; the chatter cap is 500 — shorten it (the SDK rejects over-limit notes, it does not truncate)"
    # #891: task_note refuses without a RUNNING session, so ensure one first.
    ensure_session "$task_id"
    args="$(node -e '
      const o = {task_id: Number(process.argv[1]), note: process.argv[2]};
      if (process.argv[3] !== "") o.dedupe_key = process.argv[3];
      console.log(JSON.stringify(o));
    ' "$task_id" "$text" "$dedupe_key")"
    cli task_note "$args"
    [ "$CLI_RC" -eq 0 ] || die_cli
    printf '%s\n' "$CLI_OUT"
    ;;

  activity)
    summary=""; type_name=""; note_body=""; deadline=""; user_id=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --summary)  summary="${2:?--summary needs a value}"; shift 2 ;;
        --type)     type_name="${2:?--type needs a value}"; shift 2 ;;
        --note)     note_body="${2:?--note needs a value}"; shift 2 ;;
        --deadline) deadline="${2:?--deadline needs a value}"; shift 2 ;;
        --user-id)  user_id="${2:?--user-id needs a value}"; shift 2 ;;
        *) echo "unknown option: $1" >&2; usage ;;
      esac
    done
    [ -n "$summary" ] || { echo "activity needs --summary" >&2; usage; }
    if [ -n "$user_id" ]; then
      case "$user_id" in ''|*[!0-9]*) echo "--user-id must be numeric: $user_id" >&2; exit 2 ;; esac
    fi

    type_id=""
    if [ -n "$type_name" ]; then
      q="$(node -e '
        console.log(JSON.stringify({query: process.argv[1], res_model: "project.task"}));
      ' "$type_name")"
      cli search_activity_types "$q"
      [ "$CLI_RC" -eq 0 ] || die_cli
      # Exact case-insensitive name match first; else accept a single candidate;
      # else refuse, naming every candidate — never guess a client-visible type.
      resolve_rc=0
      type_id="$(node -e '
        const types = JSON.parse(process.argv[1]);
        const want = process.argv[2].toLowerCase();
        const exact = types.filter(t => String(t.name).toLowerCase() === want);
        const pick = exact.length > 0 ? exact : types;
        if (pick.length === 1) { console.log(pick[0].id); process.exit(0); }
        if (pick.length === 0) process.exit(20);
        console.error(pick.map(t => t.id + " " + t.name).join("; "));
        process.exit(21);
      ' "$CLI_OUT" "$type_name" 2>/dev/null)" || resolve_rc=$?
      if [ "$resolve_rc" -eq 20 ]; then
        fail_json 3 "ValueError" "no activity type matches '${type_name}' — see: odoo-sdk cmd search_activity_types"
      elif [ "$resolve_rc" -ne 0 ]; then
        candidates="$(node -e '
          const types = JSON.parse(process.argv[1]);
          console.log(types.map(t => t.id + ":" + t.name).join(", "));
        ' "$CLI_OUT")"
        fail_json 3 "ValueError" "activity type '${type_name}' is ambiguous — candidates: ${candidates}"
      fi
    fi

    args="$(node -e '
      const [taskId, summary, typeId, note, deadline, userId] = process.argv.slice(1);
      const o = {res_id: Number(taskId), summary: summary};
      if (typeId !== "") o.activity_type = Number(typeId);
      if (note !== "") o.note = note;
      if (deadline !== "") o.date_deadline = deadline;
      if (userId !== "") o.user_id = Number(userId);
      console.log(JSON.stringify(o));
    ' "$task_id" "$summary" "$type_id" "$note_body" "$deadline" "$user_id")"
    cli schedule_activity "$args"
    [ "$CLI_RC" -eq 0 ] || die_cli
    printf '%s\n' "$CLI_OUT"
    ;;

  done)
    pattern=""; feedback=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --match)    pattern="${2:?--match needs a value}"; shift 2 ;;
        --feedback) feedback="${2:?--feedback needs a value}"; shift 2 ;;
        *) echo "unknown option: $1" >&2; usage ;;
      esac
    done
    [ -n "$pattern" ] || { echo "done needs --match" >&2; usage; }

    cli get_activities "{\"res_id\":$task_id}"
    [ "$CLI_RC" -eq 0 ] || die_cli
    # Case-insensitive substring over summary and activity-type name. Exactly one
    # activity must match: zero means nothing to complete, two or more means the
    # pattern does not identify the activity — both are refusals, never a guess.
    match_rc=0
    activity_id="$(node -e '
      const acts = JSON.parse(process.argv[1]);
      const want = process.argv[2].toLowerCase();
      const hit = acts.filter(a =>
        (String(a.summary || "") + " " + String(a.activity_type || ""))
          .toLowerCase().includes(want));
      if (hit.length === 1) { console.log(hit[0].activity_id); process.exit(0); }
      process.exit(hit.length === 0 ? 20 : 21);
    ' "$CLI_OUT" "$pattern" 2>/dev/null)" || match_rc=$?
    if [ "$match_rc" -eq 20 ]; then
      fail_json 3 "ValueError" "no open activity on task ${task_id} matches '${pattern}'"
    elif [ "$match_rc" -ne 0 ]; then
      candidates="$(node -e '
        const acts = JSON.parse(process.argv[1]);
        const want = process.argv[2].toLowerCase();
        const hit = acts.filter(a =>
          (String(a.summary || "") + " " + String(a.activity_type || ""))
            .toLowerCase().includes(want));
        console.log(hit.map(a => a.activity_id + ":" + (a.summary || a.activity_type)).join(", "));
      ' "$CLI_OUT" "$pattern")"
      fail_json 3 "ValueError" "pattern '${pattern}' matches more than one open activity on task ${task_id} — candidates: ${candidates}"
    fi

    args="$(node -e '
      const o = {activity_id: Number(process.argv[1])};
      if (process.argv[2] !== "") o.feedback = process.argv[2];
      console.log(JSON.stringify(o));
    ' "$activity_id" "$feedback")"
    cli mark_activity_done "$args"
    [ "$CLI_RC" -eq 0 ] || die_cli
    printf '%s\n' "$CLI_OUT"
    ;;
esac
