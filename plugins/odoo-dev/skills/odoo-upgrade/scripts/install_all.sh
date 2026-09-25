#!/usr/bin/env bash
# install_all.sh — install the custom modules of an addons tree onto throwaway
# databases and report, per independent dependency tree, exactly what broke.
#
# Usage: install_all.sh --db <name> [options] [ADDONS_PATH]
#        install_all.sh --help
#
# The old shape took its target from `odoo.conf`'s `db_name`, which in a project
# devcontainer is the developer's live working database — so the documented way
# to verify a port pointed a destructive `-i` install at real data (#880). Three
# things changed, and each is a refusal rather than a warning:
#
#   --db is required        no default, no fallback, never read from a config
#                           file. A verification run installs into a database it
#                           is willing to destroy, and only the caller knows
#                           which one that is.
#   pre-install inspection  a target that already has modules installed beyond
#                           `base` and its auto-installed closure is somebody's
#                           working database. Refused unless --force. Databases
#                           this script created are stamped and exempt, so the
#                           fix-and-retry loop can keep reusing its own name.
#   --data-dir always set   `--addons-path` alone does NOT exclude the
#                           enterprise tree: Odoo appends
#                           `<data_dir>/addons/<series>` to the addons path, and
#                           data_dir defaults to /var/lib/odoo. Isolating an
#                           install needs the data dir moved too — filestore and
#                           sessions follow it.
#
# Two modes sit on top of the plain full install, both of them about the cost of
# the verify loop rather than its correctness:
#
#   --template   The core dependency closure of the custom modules is stock
#                Odoo and assumed correct, yet every iteration reinstalls it
#                (~125 modules before the first line of custom code, #879). It
#                is installed ONCE into a template database keyed by a hash of
#                (Odoo build id, sorted core dependency set); each working
#                database is then `CREATE DATABASE ... TEMPLATE ...` (what
#                `createdb -T` issues) and installs only the custom modules.
#                A key mismatch — new container build, changed dependencies —
#                rebuilds the template, so a stale one cannot mask a real
#                incompatibility.
#
#   --per-tree   The loader aborts the whole registry on the first ParseError,
#                so one serial full-tree install returns one defect per run no
#                matter how many are present (#878). The custom modules are
#                partitioned into connected components over their own `depends`
#                (core dependencies are ignored, so two trees that merely share
#                `mail` stay separate), each component installs into its own
#                throwaway database, and each reports its own pass/fail with the
#                failing module and the decisive log line. The full install of
#                everything runs last, and only once every tree is green — a
#                full run over known-broken trees re-finds what the trees
#                already reported.
#
# With no mode flags this is the plain full install the SOP has always called
# for: one database, every module, one log.
#
# Output: the last stdout line is a JSON summary — {db, addons_path, data_dir,
# odoo_build, mode, template:{db,key,reused,core_modules}, custom_modules,
# trees:[{modules,db,status,error,module,log}], full:{...}, passed}. Same
# document to --summary <file>. Odoo's own output goes to a log file per
# database, never to stdout: the log volume is the cost these modes exist to
# cut, so the paths are reported and the decisive lines are lifted out.
#
# Exit: 0 everything installed | 1 an install failed (a RESULT, reported in the
# summary) | 2 usage, safety refusal, or a broken environment.
set -euo pipefail

usage() {
  cat <<'USAGE'
install_all.sh — verify an upgrade pass by installing custom modules on throwaway DBs

Usage: install_all.sh --db <name> [options] [ADDONS_PATH]

Required:
  --db <name>            target database. No default: this script drops and
                         recreates its target, so it never guesses one.

Options:
  --addons-path <dir>    addons tree under test (also accepted positionally;
                         default /mnt/extra-addons)
  --data-dir <dir>       Odoo data_dir for every run — filestore, sessions, and
                         the auto-appended <data_dir>/addons/<series> tree.
                         Default <artifacts>/data; never Odoo's own default.
  --artifacts <dir>      scratch root for logs, data dir and summary (default
                         $INSTALL_ALL_ARTIFACTS_DIR, else
                         ${TMPDIR:-/tmp}/odoo-install-all)
  --conf <file>          odoo.conf to pass to Odoo verbatim. Refused when it
                         sets db_name — hand over a copy without it, or drop
                         the flag and let the auto-detected config be sanitized
                         into the artifacts dir.
  --no-conf              pass no config file at all
  --template             build (or reuse) a template database holding the core
                         dependency closure, and create each working database
                         from it
  --template-db <name>   name the template explicitly (implies --template;
                         default odoo_tpl_<key12>)
  --per-tree             install each independent custom dependency tree into
                         its own throwaway database first, then the full install
  --summary <file>       write the JSON summary here as well as to stdout
  --force                proceed even though the target already has modules
                         installed. The target is still dropped first.
  --keep                 keep the per-tree throwaway databases
  -h, --help             this text

Examples:
  install_all.sh --db upgrade_check /mnt/extra-addons
  install_all.sh --db upgrade_check --template --per-tree \
                 --summary /tmp/install.json /mnt/extra-addons
USAGE
}

die() { echo "install_all.sh: $*" >&2; exit 2; }
need_value() { [ "$2" -ge 2 ] || die "$1 needs a value"; }

db=""; addons=""; data_dir=""; artifacts=""; conf=""; conf_explicit=false
no_conf=false; force=false; use_template=false; template_db=""; per_tree=false
summary_file=""; keep=false

while [ $# -gt 0 ]; do
  case "$1" in
    --db)          need_value --db $#;          db="$2"; shift 2 ;;
    --addons-path) need_value --addons-path $#; addons="$2"; shift 2 ;;
    --data-dir)    need_value --data-dir $#;    data_dir="$2"; shift 2 ;;
    --artifacts)   need_value --artifacts $#;   artifacts="$2"; shift 2 ;;
    --conf)        need_value --conf $#;        conf="$2"; conf_explicit=true; shift 2 ;;
    --summary)     need_value --summary $#;     summary_file="$2"; shift 2 ;;
    --template-db) need_value --template-db $#; template_db="$2"; use_template=true; shift 2 ;;
    --no-conf)   no_conf=true; shift ;;
    --template)  use_template=true; shift ;;
    --per-tree)  per_tree=true; shift ;;
    --force)     force=true; shift ;;
    --keep)      keep=true; shift ;;
    -h|--help)   usage; exit 0 ;;
    --)          shift; break ;;
    -*)          die "unknown option: $1 (--help for the list)" ;;
    *)           [ -z "$addons" ] || die "unexpected extra argument: $1"
                 addons="$1"; shift ;;
  esac
done
if [ $# -gt 0 ]; then
  [ -z "$addons" ] || die "unexpected extra argument: $1"
  addons="$1"; shift
  [ $# -eq 0 ] || die "unexpected extra argument: $1"
fi

# ---- the #880 refusals ---------------------------------------------------------
[ -n "$db" ] || die "--db <name> is required.
  This script DROPS and recreates its target, and it will not guess which
  database you are willing to lose. It used to take the name from odoo.conf's
  db_name, which in a devcontainer is the live project database.
  Pass a throwaway name:  install_all.sh --db upgrade_check ${addons:-/mnt/extra-addons}"

valid_dbname() {
  case "$1" in
    [A-Za-z_]*) : ;;
    *) return 1 ;;
  esac
  case "$1" in
    *[!A-Za-z0-9_-]*) return 1 ;;
  esac
  return 0
}
valid_dbname "$db" || die "not a usable database name: '$db' (letters, digits, _ and - only, not starting with a digit)"

addons="${addons:-/mnt/extra-addons}"
[ -d "$addons" ] || die "addons path is not a directory: $addons"
addons="$(cd "$addons" && pwd)"

artifacts="${artifacts:-${INSTALL_ALL_ARTIFACTS_DIR:-${TMPDIR:-/tmp}/odoo-install-all}}"
mkdir -p "$artifacts" || die "cannot create the artifacts dir: $artifacts"
artifacts="$(cd "$artifacts" && pwd)"

# Never Odoo's default (/var/lib/odoo): that is where the project's filestore
# lives, and it is also where Odoo finds the enterprise addons that
# --addons-path on its own does not exclude.
data_dir="${data_dir:-$artifacts/data}"
mkdir -p "$data_dir" || die "cannot create the data dir: $data_dir"

# ---- config file ---------------------------------------------------------------
if [ "$no_conf" = true ]; then
  conf=""
elif [ -z "$conf" ]; then
  for candidate in "${ODOO_RC:-}" /etc/odoo/odoo.conf "${HOME:-}/.odoorc"; do
    [ -n "$candidate" ] || continue
    [ -r "$candidate" ] || continue
    conf="$candidate"; break
  done
fi

conf_value() { # conf_value <file> <key>
  { grep -iE "^[[:space:]]*$2[[:space:]]*=" "$1" || true; } \
    | tail -1 | sed 's/^[^=]*=[[:space:]]*//; s/[[:space:]]*$//'
}

conf_db_name=""; conf_addons=""
if [ -n "$conf" ]; then
  [ -r "$conf" ] || die "cannot read the config file: $conf"
  conf_db_name="$(conf_value "$conf" db_name)"
  case "$conf_db_name" in False|false|None|none|"") conf_db_name="" ;; esac
  conf_addons="$(conf_value "$conf" addons_path)"
fi

# The target must never be the database the config calls the project database.
# That is the exact mistake #880 is about, and no flag makes it right.
if [ -n "$conf_db_name" ] && [ "$conf_db_name" = "$db" ]; then
  die "--db '$db' is the db_name declared in $conf.
  That is the project's own working database, not a verification target.
  Refusing — pick a throwaway name."
fi

if [ -n "$conf_db_name" ]; then
  if [ "$conf_explicit" = true ]; then
    die "$conf declares db_name = $conf_db_name.
  This script never takes its target from a config file, and a config that
  names one is also read by every Odoo sub-invocation.
  Re-run with --conf pointing at a copy without db_name, or drop --conf and the
  auto-detected config will be sanitized into $artifacts/odoo.conf for you."
  fi
  # Auto-detected config: the developer did not ask for it, so strip what must
  # not leak in (target database, filestore, database visibility) and use a copy.
  sanitized="$artifacts/odoo.conf"
  grep -ivE '^[[:space:]]*(db_name|data_dir|db_filter)[[:space:]]*=' "$conf" > "$sanitized" || true
  echo "install_all.sh: $conf declares db_name = $conf_db_name — using a sanitized copy at $sanitized" >&2
  conf="$sanitized"
fi

# ---- environment ---------------------------------------------------------------
command -v python3 >/dev/null 2>&1 || die "python3 is required (manifest parsing and the JSON summary)"
ODOO=""
for candidate in odoo odoo-bin; do
  if command -v "$candidate" >/dev/null 2>&1; then ODOO="$candidate"; break; fi
done
[ -n "$ODOO" ] || die "neither odoo nor odoo-bin is on PATH — run this inside the target-series devcontainer"
for tool in psql createdb dropdb; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool is not on PATH"
done

# Build id for the template key. odoo.release.version_info is the precise
# answer; --version is the one that survives not being able to import odoo.
build_id="$(python3 -c 'import odoo.release as r; print(".".join(str(p) for p in r.version_info))' 2>/dev/null || true)"
if [ -z "$build_id" ]; then
  build_id="$("$ODOO" --version 2>/dev/null | tail -1 | tr -d '\r' || true)"
fi
build_id="${build_id:-unknown}"

# ---- the addons tree: modules, core dependencies, independent trees -------------
graph=""
if ! graph="$(python3 - "$addons" <<'PY'
import ast, os, sys

root = sys.argv[1]
mods = {}
for entry in sorted(os.listdir(root)):
    path = os.path.join(root, entry)
    manifest = os.path.join(path, "__manifest__.py")
    if entry.startswith(".") or not os.path.isdir(path) or not os.path.isfile(manifest):
        continue
    try:
        data = ast.literal_eval(open(manifest, encoding="utf-8").read())
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    # An uninstallable module is not a verification target; Odoo refuses -i on it.
    if data.get("installable") is False:
        continue
    mods[entry] = sorted({str(d) for d in (data.get("depends") or [])})

if not mods:
    sys.exit(3)

names = sorted(mods)
# Core = depended on but not present in the tree under test, plus `base`, which
# every database has whether or not a manifest names it. Assumed correct: this
# is the set the template database preinstalls.
core = sorted({"base"} | {d for deps in mods.values() for d in deps if d not in mods})

# Connected components over intra-tree depends only. Two modules that merely
# share a core dependency are independent and must not be merged.
parent = {n: n for n in names}


def find(x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


for name in names:
    for dep in mods[name]:
        if dep in mods:
            a, b = find(name), find(dep)
            if a != b:
                parent[b] = a

groups = {}
for name in names:
    groups.setdefault(find(name), []).append(name)
trees = sorted((sorted(g) for g in groups.values()), key=lambda g: g[0])

print("MODULES\t" + ",".join(names))
print("CORE\t" + ",".join(core))
for tree in trees:
    print("TREE\t" + ",".join(tree))
PY
)"; then
  die "no installable modules with a __manifest__.py under $addons"
fi

modules_csv=""; core_csv=""; trees=()
while IFS=$'\t' read -r kind value; do
  case "$kind" in
    MODULES) modules_csv="$value" ;;
    CORE)    core_csv="$value" ;;
    TREE)    trees+=("$value") ;;
  esac
done <<< "$graph"
[ -n "$modules_csv" ] || die "no installable modules with a __manifest__.py under $addons"

# ---- database helpers ----------------------------------------------------------
db_exists() {
  [ -n "$(psql -Atqc "SELECT 1 FROM pg_database WHERE datname = '$1'" -d postgres 2>/dev/null || true)" ]
}

# A target holding modules beyond `base` and its auto-installed closure is
# somebody's working database, not a fresh one. auto_install is how "and its
# closure" is expressed without hard-coding a list that drifts per series.
#
# A database THIS script created is exempt, and that exemption is what keeps the
# fix-and-retry loop usable: iteration two targets the same name as iteration
# one, which by then holds every custom module. Ownership is recorded in the
# database itself (install_all_owned) rather than inferred from the name.
guard_target() {
  local target="$1" registry extra
  if [ -n "$(psql -Atqc "SELECT to_regclass('public.install_all_owned')" -d "$target" 2>/dev/null || true)" ]; then
    return 0
  fi
  registry="$(psql -Atqc "SELECT to_regclass('public.ir_module_module')" -d "$target" 2>/dev/null || true)"
  [ -n "$registry" ] || return 0
  extra="$(psql -Atqc "SELECT name FROM ir_module_module WHERE state IN ('installed', 'to upgrade', 'to remove') AND name <> 'base' AND COALESCE(auto_install, false) = false ORDER BY name" -d "$target" 2>/dev/null || true)"
  [ -n "$extra" ] || return 0
  if [ "$force" = true ]; then
    echo "install_all.sh: --force: $target already has $(printf '%s\n' "$extra" | wc -l | tr -d ' ') module(s) installed, dropping it anyway" >&2
    return 0
  fi
  {
    echo "install_all.sh: refusing to install into '$target' — it already has modules installed:"
    printf '%s\n' "$extra" | head -10 | sed 's/^/    /'
    echo "  This script drops and recreates its target, so that would destroy a working"
    echo "  database. Use a fresh name, or pass --force if you really mean this one."
  } >&2
  exit 2
}

created_dbs=()
cleanup() {
  local target
  if [ "$keep" = true ]; then return 0; fi
  for target in ${created_dbs+"${created_dbs[@]}"}; do
    dropdb --if-exists "$target" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT

ensure_fresh_db() { # ensure_fresh_db <db> [template]
  local target="$1" template="${2:-}"
  if db_exists "$target"; then
    guard_target "$target"
    dropdb --if-exists "$target" >/dev/null
  fi
  if [ -n "$template" ]; then
    createdb -T "$template" "$target" >/dev/null
  else
    createdb "$target" >/dev/null
  fi
  # Stamp it as ours, before anything is installed, so the next iteration of the
  # fix loop may drop it and a stranger's database still may not.
  psql -q -d "$target" -c "CREATE TABLE IF NOT EXISTS install_all_owned (created_at timestamptz DEFAULT now())" >/dev/null
}

odoo_args=()
if [ -n "$conf" ]; then odoo_args+=(-c "$conf"); fi
addons_arg="$addons"
if [ -n "$conf_addons" ]; then addons_arg="$addons,$conf_addons"; fi
odoo_args+=(--addons-path="$addons_arg" --data-dir "$data_dir" --max-cron-threads=0 --stop-after-init)

run_install() { # run_install <db> <modules-csv> <log>
  "$ODOO" -d "$1" -i "$2" "${odoo_args[@]}" > "$3" 2>&1
}

# The failing module and the one line that decides why, lifted out of a log the
# caller should not have to read. Everything else in that file is core loading.
extract_failure() { # extract_failure <log> -> "<module>\t<line>"
  python3 - "$1" <<'PY'
import re, sys

try:
    lines = open(sys.argv[1], errors="replace").read().splitlines()
except OSError:
    lines = []

module = ""
LOADING = (
    re.compile(r"[Ll]oading module ([a-z0-9_]+)"),
    re.compile(r"module ([a-z0-9_]+): creating or updating database tables"),
    re.compile(r"odoo\.modules\.loading: [Ll]oading ([a-z0-9_]+)"),
)
for line in lines:
    for pattern in LOADING:
        found = pattern.search(line)
        if found:
            module = found.group(1)

EXCEPTION = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*(Error|Exception|Failure|Exit):")
decisive = ""
for line in lines:
    stripped = line.strip()
    if EXCEPTION.match(stripped):
        decisive = stripped
if not decisive:
    for line in lines:
        if " ERROR " in line or " CRITICAL " in line:
            decisive = line.strip()
if not decisive:
    for line in reversed(lines):
        if line.strip():
            decisive = line.strip()
            break

# A path inside the failing module beats the last "Loading module": a ParseError
# names the file it choked on, while the loader logs the module it is about to
# start rather than the one that raised.
tail = "\n".join(lines[-120:])
path = re.search(
    r"/([a-z0-9_]+)/(?:views|report|reports|data|security|wizard|wizards|models|static|i18n)/",
    tail)
if path:
    module = path.group(1)

print("%s\t%s" % (module, decisive.replace("\t", " ")[:400]))
PY
}

records="$artifacts/records.tsv"
: > "$records"
record() { # record <kind> <db> <modules> <status> <module> <error> <log>
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$5" "$6" "$7" >> "$records"
}

# ---- template database ---------------------------------------------------------
template_key="$(python3 -c 'import hashlib,sys;print(hashlib.sha256(sys.argv[1].encode()).hexdigest())' "$build_id
$core_csv")"
template_reused=false
template_from=""
if [ "$use_template" = true ]; then
  template_db="${template_db:-odoo_tpl_${template_key:0:12}}"
  valid_dbname "$template_db" || die "not a usable template database name: '$template_db'"
  stored=""
  if db_exists "$template_db"; then
    if [ -n "$(psql -Atqc "SELECT to_regclass('public.install_all_template')" -d "$template_db" 2>/dev/null || true)" ]; then
      stored="$(psql -Atqc "SELECT key FROM install_all_template LIMIT 1" -d "$template_db" 2>/dev/null || true)"
    fi
    if [ "$stored" = "$template_key" ]; then
      template_reused=true
    else
      echo "install_all.sh: template $template_db is keyed ${stored:-(unkeyed)}, want $template_key — rebuilding" >&2
      dropdb --if-exists "$template_db" >/dev/null
    fi
  fi
  if [ "$template_reused" = false ]; then
    createdb "$template_db" >/dev/null
    template_log="$artifacts/$template_db.log"
    if ! run_install "$template_db" "${core_csv:-base}" "$template_log"; then
      failure="$(extract_failure "$template_log")"
      echo "install_all.sh: building the template database failed — ${failure#*$'\t'}" >&2
      echo "  log: $template_log" >&2
      dropdb --if-exists "$template_db" >/dev/null 2>&1 || true
      exit 1
    fi
    # Keyed inside the database, so a build or dependency change invalidates it
    # rather than silently masking an incompatibility.
    psql -q -d "$template_db" -c "CREATE TABLE IF NOT EXISTS install_all_template (key text PRIMARY KEY, created_at timestamptz DEFAULT now()); DELETE FROM install_all_template; INSERT INTO install_all_template (key) VALUES ('$template_key');" >/dev/null
  fi
  # CREATE DATABASE ... TEMPLATE refuses while another session holds the
  # template open. Every install above ran --stop-after-init, so nothing does.
  template_from="$template_db"
fi

# ---- per-tree installs ---------------------------------------------------------
failures=0
if [ "$per_tree" = true ]; then
  index=0
  for tree in ${trees+"${trees[@]}"}; do
    index=$((index + 1))
    tree_db="${db}_t${index}"
    tree_log="$artifacts/$tree_db.log"
    ensure_fresh_db "$tree_db" "$template_from"
    created_dbs+=("$tree_db")
    if run_install "$tree_db" "$tree" "$tree_log"; then
      record tree "$tree_db" "$tree" pass "" "" "$tree_log"
      echo "install_all.sh: tree $index/${#trees[@]} PASS ($tree)" >&2
    else
      failure="$(extract_failure "$tree_log")"
      failures=$((failures + 1))
      record tree "$tree_db" "$tree" fail "${failure%%$'\t'*}" "${failure#*$'\t'}" "$tree_log"
      echo "install_all.sh: tree $index/${#trees[@]} FAIL ($tree) at ${failure%%$'\t'*}: ${failure#*$'\t'}" >&2
      echo "  log: $tree_log" >&2
    fi
    if [ "$keep" != true ]; then dropdb --if-exists "$tree_db" >/dev/null 2>&1 || true; fi
  done
fi

# ---- the full install ----------------------------------------------------------
full_log="$artifacts/$db.log"
if [ "$per_tree" = true ] && [ "$failures" -gt 0 ]; then
  # A full run over known-broken trees aborts on the first of them and re-finds
  # what the per-tree pass already reported.
  record full "$db" "$modules_csv" skipped "" "$failures tree(s) failed — fix those first" ""
  echo "install_all.sh: skipping the full install — $failures tree(s) still failing" >&2
else
  ensure_fresh_db "$db" "$template_from"
  if run_install "$db" "$modules_csv" "$full_log"; then
    record full "$db" "$modules_csv" pass "" "" "$full_log"
  else
    failure="$(extract_failure "$full_log")"
    failures=$((failures + 1))
    record full "$db" "$modules_csv" fail "${failure%%$'\t'*}" "${failure#*$'\t'}" "$full_log"
    echo "install_all.sh: full install FAILED at ${failure%%$'\t'*}: ${failure#*$'\t'}" >&2
    echo "  log: $full_log" >&2
  fi
fi

# ---- summary -------------------------------------------------------------------
mode=full
if [ "$per_tree" = true ]; then mode=per-tree; fi
if [ "$use_template" = true ]; then mode="$mode+template"; fi

summary="$(python3 - "$records" "$db" "$addons" "$data_dir" "$build_id" "$mode" \
  "$modules_csv" "$core_csv" "${template_db:-}" "$template_key" "$template_reused" \
  "$use_template" <<'PY'
import json, sys

(records, db, addons, data_dir, build_id, mode, modules_csv, core_csv,
 template_db, template_key, template_reused, use_template) = sys.argv[1:13]


def split(value):
    return [part for part in value.split(",") if part]


trees, full = [], None
with open(records, encoding="utf-8") as handle:
    for line in handle:
        if not line.strip():
            continue
        kind, target, mods, status, module, error, log = (
            line.rstrip("\n").split("\t") + [""] * 7)[:7]
        entry = {
            "modules": split(mods), "db": target, "status": status,
            "error": error or None, "module": module or None, "log": log or None,
        }
        if kind == "tree":
            trees.append(entry)
        else:
            full = entry

statuses = [entry["status"] for entry in trees] + ([full["status"]] if full else [])
summary = {
    "db": db, "addons_path": addons, "data_dir": data_dir,
    "odoo_build": build_id, "mode": mode,
    "custom_modules": split(modules_csv),
    "template": {
        "db": template_db or None, "key": template_key,
        "reused": template_reused == "true", "core_modules": split(core_csv),
    } if use_template == "true" else None,
    "trees": trees,
    "full": full,
    "passed": bool(statuses) and all(status == "pass" for status in statuses),
}
print(json.dumps(summary))
PY
)"

if [ -n "$summary_file" ]; then
  mkdir -p "$(dirname "$summary_file")"
  printf '%s\n' "$summary" > "$summary_file"
fi
printf '%s\n' "$summary"

[ "$failures" -eq 0 ] || exit 1
