#!/usr/bin/env bash
# upgrade_service.sh — drive the upgrade.odoo.com service for an on-premise
# Enterprise database, in either of the two shapes this actually takes.
#
# Usage:
#   upgrade_service.sh <test|production> --target <ver>
#     ( --ssh <user@host> --db <name> [--remote-dir DIR]
#     | --local --dump FILE --contract CODE [--filestore DIR] )
#     [--restore-as NAME] [--no-restore] [--update-modules "a,b,c"]
#     [--cores N] [--yes-production] [--dry-run]
#
# Two modes because on-premise upgrades happen two ways: the client runs on the
# customer's own server (where the database lives and the contract code is in
# ir_config_parameter), or against a dump fetched into this devcontainer (where
# there is no database, so the contract must be supplied).
#
# The client itself is fetched fresh each run, exactly as the documentation
# prescribes:
#     python3 <(curl -s https://upgrade.odoo.com/upgrade) test -d <db> -t <target>
#
# Flags passed through, verified against the client's own argparse:
#   -d/--dbname XOR -i/--dump   -t/--target   -c/--contract   -r/--restore-name
#   -x/--no-restore   -j/--core-count
# There is NO --db-host/--db-user/--db-password and no --filestore: the client
# takes its database connection from libpq environment only, and merges the
# filestore itself from the hardcoded ~/.local/share/Odoo/filestore. Anything
# else this script does with a filestore, it does itself.
#
# The service identifier is a TOKEN, not a request id. It is what `status`,
# `log`, `restore` and `wipe` take, and it is what Odoo support needs. It is
# extracted from the client output and carried in this script's JSON, including
# on failure — a failed upgrade whose token was lost cannot be asked about.
#
# SAFETY
#   - `production` refuses without --yes-production (exit 7). A production run
#     uploads the live database, and modifications made after the upload are
#     lost; the docs say to stop using the database first. sop.md keeps this
#     step [MANUAL] and this guard is the mechanical half of that.
#   - A local TEST restore is NEUTRALIZED before this script returns. A restored
#     copy of production still holds its real mail servers, payment credentials
#     and scheduled actions, and mail leaving a test restore reaches customers.
#
# Last stdout line: {"mode","aim","db","target","token","status",
#   "upgraded_artifact","restored_db","neutralized","modules_updated",
#   "duration_s","log_file"}
#
# Exit codes: 0 ok | 2 usage | 4 the upgrade service failed (token in the JSON)
#             | 7 production without --yes-production
set -uo pipefail

UPGRADE_URL="${ODOO_UPGRADE_URL:-https://upgrade.odoo.com/upgrade}"

usage() { sed -n '3,12p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

[ $# -ge 1 ] || usage
aim="$1"; shift
case "$aim" in test|production) : ;; *) echo "first argument must be test or production" >&2; usage ;; esac

mode=""; ssh_target=""; db=""; dump=""; contract=""; target=""
restore_as=""; filestore=""; remote_dir=""; update_modules=""
cores="${UPGRADE_CORES:-4}"; no_restore=false; yes_production=false; dry_run=false
while [ $# -gt 0 ]; do
  case "$1" in
    --ssh)             mode="ssh"; ssh_target="${2:?}"; shift 2 ;;
    --local)           mode="local"; shift ;;
    --db)              db="${2:?}"; shift 2 ;;
    --dump)            dump="${2:?}"; shift 2 ;;
    --contract)        contract="${2:?}"; shift 2 ;;
    --target)          target="${2:?}"; shift 2 ;;
    --restore-as)      restore_as="${2:?}"; shift 2 ;;
    --filestore)       filestore="${2:?}"; shift 2 ;;
    --remote-dir)      remote_dir="${2:?}"; shift 2 ;;
    --update-modules)  update_modules="${2:?}"; shift 2 ;;
    --cores)           cores="${2:?}"; shift 2 ;;
    --no-restore)      no_restore=true; shift ;;
    --yes-production)  yes_production=true; shift ;;
    --dry-run)         dry_run=true; shift ;;
    *) echo "unknown option: $1" >&2; usage ;;
  esac
done

[ -n "$mode" ] || { echo "pick a mode: --ssh <user@host> or --local" >&2; usage; }
[ -n "$target" ] || { echo "--target is required (e.g. --target 19.0)" >&2; usage; }

if [ "$aim" = production ] && [ "$yes_production" != true ]; then
  echo "refusing: a production upgrade uploads the LIVE database and any change made after the upload is lost. Stop using the database, confirm with the customer, then pass --yes-production." >&2
  exit 7
fi

if [ "$mode" = ssh ]; then
  [ -n "$db" ] || { echo "--db is required in --ssh mode" >&2; usage; }
  [ -n "$dump" ] && { echo "--dump belongs to --local mode" >&2; usage; }
else
  [ -n "$dump" ] || { echo "--dump is required in --local mode" >&2; usage; }
  [ -f "$dump" ] || { echo "dump not found: $dump" >&2; exit 2; }
  # The client hard-errors without it: there is no database to read
  # ir_config_parameter 'database.enterprise_code' from.
  [ -n "$contract" ] || { echo "--contract is required with --dump (no database to read the subscription code from)" >&2; usage; }
  case "$dump" in
    *.sql|*.dump|*.zip|*.sql.gz) : ;;
    *) [ -f "$dump/toc.dat" ] || { echo "dump must be .sql, .dump, .zip, .sql.gz, or a directory containing toc.dat: $dump" >&2; exit 2; } ;;
  esac
fi

log="$(mktemp "${TMPDIR:-/tmp}/upgrade-service.XXXXXX.log")"
started=$(date +%s)

client_args=("$aim" "-t" "$target" "-j" "$cores")
if [ "$mode" = ssh ]; then client_args+=("-d" "$db"); else client_args+=("-i" "$dump"); fi
[ -n "$contract" ] && client_args+=("-c" "$contract")
[ -n "$restore_as" ] && client_args+=("-r" "$restore_as")
[ "$no_restore" = true ] && client_args+=("-x")

emit() { # emit <status> <token> <artifact> <restored_db> <neutralized> <modules>
  node -e '
    const [mode, aim, db, target, status, token, artifact, restored, neutralized, modules, dur, log] = process.argv.slice(1);
    console.log(JSON.stringify({
      mode, aim, db: db || null, target, token: token || null, status,
      upgraded_artifact: artifact || null, restored_db: restored || null,
      neutralized: neutralized === "true",
      modules_updated: modules ? modules.split(",").filter(Boolean) : [],
      duration_s: Number(dur), log_file: log,
    }));
  ' "$mode" "$aim" "$db" "$target" "$1" "$2" "$3" "$4" "$5" "$6" \
    "$(( $(date +%s) - started ))" "$log"
}

if [ "$dry_run" = true ]; then
  if [ "$mode" = ssh ]; then
    echo "would run on $ssh_target: python3 <(curl -s $UPGRADE_URL) ${client_args[*]}" >&2
  else
    echo "would run here: python3 <(curl -s $UPGRADE_URL) ${client_args[*]}" >&2
  fi
  emit "dry-run" "" "" "" false ""
  exit 0
fi

if [ "$mode" = ssh ]; then
  # bash -lc, and process substitution, because the documented invocation IS a
  # process substitution — piping the client into python breaks its stdin, which
  # it uses to prompt about resuming an interrupted request.
  remote_cd=""
  [ -n "$remote_dir" ] && remote_cd="cd $(printf '%q' "$remote_dir") && "
  ssh -o StrictHostKeyChecking=accept-new "$ssh_target" \
    "bash -lc '${remote_cd}python3 <(curl -s $UPGRADE_URL) $(printf '%q ' "${client_args[@]}")'" \
    2>&1 | tee "$log"
  rc="${PIPESTATUS[0]}"
else
  python3 <(curl -s "$UPGRADE_URL") "${client_args[@]}" 2>&1 | tee "$log"
  rc="${PIPESTATUS[0]}"
fi

# "The secret token is '<token>'" — the only identifier the service gives back,
# and the one support asks for. Captured whether the run succeeded or not.
token="$( { grep -oE "secret token is '[^']+'" "$log" || true; } | tail -1 | sed -E "s/.*'([^']+)'.*/\1/")"
artifact="$( { grep -oE '[A-Za-z0-9_.-]+\.dump' "$log" || true; } | tail -1)"

if [ "${rc:-1}" -ne 0 ]; then
  emit "failed" "$token" "$artifact" "" false ""
  echo "upgrade service failed (exit $rc). Token: ${token:-unknown}. Full output: $log" >&2
  echo "Open a ticket with that token — see references/support-tickets.md." >&2
  exit 4
fi

restored=""
neutralized=false
if [ "$no_restore" != true ]; then
  restored="$( { grep -oE "restored .*database '[^']+'|--dbname [A-Za-z0-9_]+" "$log" || true; } | tail -1 | sed -E "s/.*'([^']+)'.*/\1/; s/--dbname //")"
  [ -n "$restored" ] || restored="$restore_as"
fi

# Neutralize BEFORE anything else touches the restore. Order matters: a module
# update on a non-neutralized copy can fire scheduled actions and send mail.
if [ -n "$restored" ] && [ "$aim" = test ]; then
  if command -v odoo >/dev/null 2>&1; then
    odoo neutralize -d "$restored" >>"$log" 2>&1 && neutralized=true || true
  elif command -v odoo-bin >/dev/null 2>&1; then
    odoo-bin neutralize -d "$restored" >>"$log" 2>&1 && neutralized=true || true
  fi
  if [ "$neutralized" != true ]; then
    echo "WARN could not neutralize '$restored'. Do NOT open it until you have run: odoo-bin neutralize -d $restored" >&2
  fi
fi

# The client merges the filestore only on the test/production paths, and only
# from the hardcoded ~/.local/share/Odoo/filestore. Any other source is ours.
if [ -n "$filestore" ] && [ -n "$restored" ]; then
  dest="${ODOO_FILESTORE_ROOT:-$HOME/.local/share/Odoo/filestore}/$restored"
  mkdir -p "$dest"
  rsync -a "$filestore/" "$dest/" >>"$log" 2>&1 \
    || echo "WARN filestore merge from $filestore into $dest failed — see $log" >&2
fi

if [ -n "$update_modules" ] && [ -n "$restored" ]; then
  if [ "$neutralized" != true ] && [ "$aim" = test ]; then
    echo "refusing to update modules on a test restore that is not neutralized: $restored" >&2
    emit "restored-not-neutralized" "$token" "$artifact" "$restored" false ""
    exit 4
  fi
  odoo_bin="$(command -v odoo || command -v odoo-bin || true)"
  if [ -n "$odoo_bin" ]; then
    "$odoo_bin" -d "$restored" -u "$update_modules" --stop-after-init >>"$log" 2>&1 \
      || { emit "module-update-failed" "$token" "$artifact" "$restored" "$neutralized" ""
           echo "module update failed on $restored — see $log" >&2; exit 4; }
  else
    echo "WARN no odoo binary here; skipping the module update" >&2
    update_modules=""
  fi
fi

emit "ok" "$token" "$artifact" "$restored" "$neutralized" "$update_modules"
