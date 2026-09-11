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
# The 300-character chatter cap is pre-checked here for `note` so a too-long note
# fails fast with the count in the message — but the SDK's
# enforce_chatter_body_limit (commands/builtin/task_note.py) stays authoritative:
# whatever passes the pre-check is still judged server-side, and rejected, never
# truncated, when it is over.
#
# Output: on success, the CLI's own JSON result, verbatim, as the last stdout
# line. On a CLI failure, the CLI's JSON error envelope {"error":{"type",
# "message"}} is passed through verbatim on stdout and this script exits with the
# CLI's exit code (1 boundary, 2 usage). Errors raised here (ambiguous type, no
# matching activity, over-long note, missing CLI) print the same envelope shape.
#
# Exit codes: 0 success
#           | 1/2 passed through from the CLI (boundary / usage)
#           | 2 argument or pre-check error
#           | 3 resolution failure (no or ambiguous type / activity match)
#           | 4 odoo-sdk is not on PATH
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

# cli <name> <args_json> — one dispatch. Sets CLI_OUT/CLI_RC rather than exiting,
# because a caller may need the stdout (a lookup) before deciding what comes next.
CLI_OUT=""
CLI_RC=0
cli() {
  CLI_RC=0
  CLI_OUT="$(odoo-sdk cmd "$1" --args "$2")" || CLI_RC=$?
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
    [ "$chars" -le 300 ] || fail_json 2 "ValueError" \
      "note is ${chars} characters; the chatter cap is 300 — shorten it (the SDK rejects over-limit notes, it does not truncate)"
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
