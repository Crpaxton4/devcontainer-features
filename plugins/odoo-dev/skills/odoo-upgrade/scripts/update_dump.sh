#!/usr/bin/env bash
# update_dump.sh — the UPDATE stage (#1163): run `-u` on a restored, neutralized,
# already upgraded database and report what broke. The install loop covers an
# empty database; this covers stored data from the old series.
#
# Usage: update_dump.sh --db <restored db> --worktree W --series T --artifacts A
#                       --session-id S --modules a,b [--enterprise DIR] [--summary F]
#
# The database is a restored dump, not a throwaway: this script never creates,
# drops or stamps it (the install_all_owned rule does not apply). It refuses to
# run unless the database is neutralized. Odoo 16 and later set the key
# `database.is_neutralized` to `true` in ir_config_parameter when
# `odoo-bin neutralize -d <db>` finishes. A restore you made is not neutralized
# until you run that command.
#
# Two runs, the second only when the first is green:
#   ported  odoo-bin -d <db> -u <modules> ...
#   all     odoo-bin -d <db> -u all ...
# Both use --addons-path W[,DIR], --data-dir A/update/data and --stop-after-init.
# Each exit-0 run then passes the warning gate of install_all.sh (#1139): an
# odoo.* WARNING line after module loading starts, that neither
# accepted_warnings.default.txt nor A/accepted_warnings.txt accepts, fails the
# run. The normalisation rule below is a copy of install_all.sh scan_warnings.
#
# Summary (stdout, and --summary F): {db, modules, runs: [{step: ported|all, rc,
# log, decisive, class}], warnings_new[], status: pass|failed|env_failure, passed}.
# class is env or port, or null for a green run. It follows the install_all.sh
# extract_failure rule, minimal form: a decisive frame outside the worktree is env.
#
# Exit codes: 0 green | 1 a port defect or a new warning | 2 usage | 10 environment
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
die() { echo "update_dump.sh: $*" >&2; exit 2; }

db=""; worktree=""; series=""; artifacts=""; session_id=""; modules=""; enterprise=""; summary=""
while [ $# -gt 0 ]; do
  case "$1" in
    --db) db="${2:?--db needs a value}"; shift 2 ;;
    --worktree) worktree="${2:?--worktree needs a value}"; shift 2 ;;
    --series) series="${2:?--series needs a value}"; shift 2 ;;
    --artifacts) artifacts="${2:?--artifacts needs a value}"; shift 2 ;;
    --session-id) session_id="${2:?--session-id needs a value}"; shift 2 ;;
    --modules) modules="${2:?--modules needs a value}"; shift 2 ;;
    --enterprise) enterprise="${2:?--enterprise needs a value}"; shift 2 ;;
    --summary) summary="${2:?--summary needs a value}"; shift 2 ;;
    -h|--help) sed -n '2,/^set -uo/p' "${BASH_SOURCE[0]}" | sed '$d;s/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown argument '$1'" ;;
  esac
done
for pair in db:"$db" worktree:"$worktree" series:"$series" artifacts:"$artifacts" session-id:"$session_id" modules:"$modules"; do
  [ -n "${pair#*:}" ] || die "--${pair%%:*} is required"
done
[ -d "$worktree" ] || die "--worktree is not a directory: $worktree"
command -v python3 >/dev/null 2>&1 || die "python3 is required"
ODOO=""
for candidate in odoo odoo-bin; do
  if command -v "$candidate" >/dev/null 2>&1; then ODOO="$candidate"; break; fi
done
[ -n "$ODOO" ] || die "neither odoo nor odoo-bin is on PATH — run this inside the target-series devcontainer"
command -v psql >/dev/null 2>&1 || die "psql is not on PATH"

out_dir="$artifacts/update"; mkdir -p "$out_dir/data" || die "cannot create $out_dir"
warn_default="$HERE/accepted_warnings.default.txt"
warn_project="$artifacts/accepted_warnings.txt"

runs_file="$out_dir/runs.jsonl"; : > "$runs_file"
warnings_file="$out_dir/warnings.new.txt"; : > "$warnings_file"

# write_summary <status> <exit>: prints the document, writes --summary, exits.
write_summary() {
  python3 - "$1" "$db" "$modules" "$runs_file" "$warnings_file" "$summary" <<'PY'
import json, sys

status, db, modules, runs_file, warnings_file, out = sys.argv[1:7]
runs = [json.loads(line) for line in open(runs_file) if line.strip()]
warnings = [line.rstrip("\n") for line in open(warnings_file) if line.strip()]
doc = {
    "db": db,
    "modules": modules.split(","),
    "runs": runs,
    "warnings_new": warnings,
    "status": status,
    "passed": status == "pass",
}
text = json.dumps(doc, indent=2)
print(text)
if out:
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(text + "\n")
PY
  exit "$2"
}

record_run() { # record_run <step> <rc> <log> <decisive> <class>
  python3 - "$runs_file" "$@" <<'PY'
import json, sys

path, step, rc, log, decisive, klass = sys.argv[1:7]
row = {"step": step, "rc": int(rc), "log": log, "decisive": decisive or None, "class": klass or None}
with open(path, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(row) + "\n")
PY
}

# ---- the neutralize gate ---------------------------------------------------------
flag="$(psql -Atqc "select value from ir_config_parameter where key='database.is_neutralized'" -d "$db" 2>"$out_dir/gate.err")"
if [ "$flag" != "true" ]; then
  line="database $db is not neutralized (database.is_neutralized is '${flag:-unset}'); run odoo-bin neutralize -d $db first"
  record_run gate 1 "$out_dir/gate.err" "$line" env
  echo "update_dump.sh: $line" >&2
  write_summary env_failure 10
fi

# Reads one log. Prints one JSON line: {decisive, class, new[]}. The warning
# normalisation is the one of install_all.sh scan_warnings; the failure class is the
# minimal form of its extract_failure: the last File "..." frame before the
# exception, and a frame outside the worktree means the environment.
analyse() { # analyse <log> <rc>
  python3 - "$1" "$2" "$worktree" "$warn_default" "$warn_project" <<'PY'
import json, re, sys

log, rc, tree, default_list, project_list = sys.argv[1:6]
tree = tree.rstrip("/") + "/"
WARNING = re.compile(r"^\S+ \S+ \d+ WARNING \S+ (odoo\.[^\s:]+): (.*)$")
EXCEPTION = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*(Error|Exception|Failure|Exit):")


def entries(path):
    try:
        text = open(path, errors="replace").read()
    except OSError:
        return []
    return [e.strip() for e in text.splitlines() if e.strip() and not e.lstrip().startswith("#")]


def accepted(line, patterns):
    for entry in patterns:
        if entry == line:
            return True
        try:
            if re.search(entry, line):
                return True
        except re.error:
            pass
    return False


try:
    lines = open(log, errors="replace").read().splitlines()
except OSError:
    lines = []
result = {"decisive": None, "class": None, "new": []}
if int(rc) == 0:
    patterns = entries(default_list) + entries(project_list)
    started, seen = False, set()
    for line in lines:
        if "odoo.modules.loading: loading" in line:
            started = True
        if not started:
            continue
        found = WARNING.match(line)
        if not found:
            continue
        norm = re.sub(r"\s+", " ", "%s: %s" % found.groups()).strip()
        if norm in seen:
            continue
        seen.add(norm)
        if not accepted(norm, patterns):
            result["new"].append(norm)
else:
    decisive, at = "", -1
    for index, line in enumerate(lines):
        if EXCEPTION.match(line.strip()):
            decisive, at = line.strip(), index
    if not decisive:
        for line in lines:
            if " ERROR " in line or " CRITICAL " in line:
                decisive = line.strip()
    if not decisive:
        decisive = next((x.strip() for x in reversed(lines) if x.strip()), "no output")
    frame = ""
    if at >= 0:
        for line in reversed(lines[:at]):
            found = re.search(r'File "([^"]+)"', line)
            if found:
                frame = found.group(1)
                break
    names_tree = tree in decisive
    klass = "port"
    if frame and not frame.startswith(tree) and not names_tree:
        klass = "env"
    result["decisive"] = decisive.replace("\t", " ")[:400]
    result["class"] = klass
print(json.dumps(result))
PY
}

addons="$worktree"
if [ -n "$enterprise" ]; then addons="$worktree,$enterprise"; fi

run_step() { # run_step <step> <-u argument>
  local step="$1" arg="$2" log="$out_dir/$1.log" rc info
  "$ODOO" -d "$db" -u "$arg" --addons-path="$addons" --data-dir "$out_dir/data" \
    --max-cron-threads=0 --stop-after-init > "$log" 2>&1
  rc=$?
  info="$(analyse "$log" "$rc")"
  python3 - "$runs_file" "$warnings_file" "$step" "$rc" "$log" "$info" <<'PY'
import json, sys

runs_file, warnings_file, step, rc, log, info = sys.argv[1:7]
info = json.loads(info)
row = {"step": step, "rc": int(rc), "log": log, "decisive": info["decisive"], "class": info["class"]}
if int(rc) == 0 and info["new"]:
    row["decisive"] = info["new"][0]
    row["class"] = "port"
    with open(warnings_file, "a", encoding="utf-8") as handle:
        handle.writelines(line + "\n" for line in info["new"])
with open(runs_file, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(row) + "\n")
print(row["class"] or "")
PY
}

for step in ported all; do
  arg="$modules"; if [ "$step" = all ]; then arg="all"; fi
  klass="$(run_step "$step" "$arg")"
  case "$klass" in
    env) write_summary env_failure 10 ;;
    port) write_summary failed 1 ;;
  esac
done
write_summary pass 0
