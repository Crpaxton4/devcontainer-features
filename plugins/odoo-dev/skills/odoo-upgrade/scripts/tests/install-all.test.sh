#!/usr/bin/env bash
# install-all.test.sh — offline tests for install_all.sh.
#
# install_all.sh is the script that once pointed a destructive `-i` install at
# whatever odoo.conf's db_name happened to be, so what is worth testing is the
# refusals, not the happy path: they are the only part that has to hold when a
# developer is tired and the flags are nearly right.
#
# Stub `odoo`, `psql`, `createdb` and `dropdb` on PATH record their argv and
# emulate the handful of outputs the script actually reads — which databases
# exist, what ir_module_module says, the template key. The whole of the script
# above those calls (argument parsing, config sanitising, manifest parsing,
# component partitioning, log extraction, summary assembly) is real.
#
# No network, no docker, no postgres, no Odoo.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$(cd "$SCRIPT_DIR/.." && pwd)/install_all.sh"
work="$(mktemp -d "${TMPDIR:-/tmp}/install-all-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
expect() { if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: wanted '$3', got '$2'" >&2; fi; }
contains() { case "$2" in *"$3"*) expect "$1" yes yes ;; *) expect "$1" "$2" "...$3...";; esac; }

# ---------- stubbed environment ------------------------------------------------
bin="$work/bin"; mkdir -p "$bin"

cat > "$bin/psql" <<'STUB'
#!/usr/bin/env bash
# Emulates only the queries install_all.sh issues. State lives in flat files:
#   dbs             existing database names
#   installed.<db>  rows of ir_module_module that are 'installed'
#   owned.<db>      the install_all_owned stamp
#   tplkey.<db>     the stored template key
sql=""; db=""; prev=""
for arg in "$@"; do
  case "$prev" in
    -Atqc|-c) sql="$arg" ;;
    -d) db="$arg" ;;
  esac
  prev="$arg"
done
printf '%s\n' "$*" >> "$STUB_STATE/psql.argv"
case "$sql" in
  *"FROM pg_database"*)
    name="${sql##*datname = \'}"; name="${name%%\'*}"
    grep -qxF "$name" "$STUB_STATE/dbs" 2>/dev/null && echo 1 ;;
  *"to_regclass('public.install_all_owned')"*)
    [ -f "$STUB_STATE/owned.$db" ] && echo install_all_owned ;;
  *"to_regclass('public.ir_module_module')"*)
    [ -f "$STUB_STATE/installed.$db" ] && echo ir_module_module ;;
  *"FROM ir_module_module"*)
    cat "$STUB_STATE/installed.$db" 2>/dev/null ;;
  *"to_regclass('public.install_all_template')"*)
    [ -f "$STUB_STATE/tplkey.$db" ] && echo install_all_template ;;
  # Ahead of the SELECT arm on purpose: the write is one -c carrying
  # CREATE/DELETE/INSERT, and its DELETE clause also says
  # "FROM install_all_template".
  *"INSERT INTO install_all_template"*)
    key="${sql##*VALUES (\'}"; key="${key%%\'*}"
    printf '%s\n' "$key" > "$STUB_STATE/tplkey.$db" ;;
  *"SELECT key FROM install_all_template"*)
    cat "$STUB_STATE/tplkey.$db" 2>/dev/null ;;
  *"CREATE TABLE IF NOT EXISTS install_all_owned"*)
    : > "$STUB_STATE/owned.$db" ;;
esac
exit 0
STUB

cat > "$bin/createdb" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_STATE/createdb.argv"
tpl=""; name=""
while [ $# -gt 0 ]; do
  case "$1" in
    -T) tpl="$2"; shift 2 ;;
    *) name="$1"; shift ;;
  esac
done
printf '%s\n' "$name" >> "$STUB_STATE/dbs"
# CREATE DATABASE ... TEMPLATE copies the whole database, stamps included.
if [ -n "$tpl" ]; then
  [ -f "$STUB_STATE/installed.$tpl" ] && cp "$STUB_STATE/installed.$tpl" "$STUB_STATE/installed.$name"
  [ -f "$STUB_STATE/owned.$tpl" ] && cp "$STUB_STATE/owned.$tpl" "$STUB_STATE/owned.$name"
fi
exit 0
STUB

cat > "$bin/dropdb" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_STATE/dropdb.argv"
name=""
for arg in "$@"; do case "$arg" in --if-exists) ;; *) name="$arg" ;; esac; done
[ -n "$name" ] || exit 0
grep -vxF "$name" "$STUB_STATE/dbs" > "$STUB_STATE/dbs.tmp" 2>/dev/null
mv "$STUB_STATE/dbs.tmp" "$STUB_STATE/dbs" 2>/dev/null
rm -f "$STUB_STATE/installed.$name" "$STUB_STATE/owned.$name" "$STUB_STATE/tplkey.$name"
exit 0
STUB

cat > "$bin/odoo" <<'STUB'
#!/usr/bin/env bash
case "${1:-}" in --version) echo "Odoo Server 19.0-20260901"; exit 0 ;; esac
printf '%s\n' "$*" >> "$STUB_STATE/odoo.argv"
db=""; mods=""; prev=""
for arg in "$@"; do
  case "$prev" in -d) db="$arg" ;; -i) mods="$arg" ;; esac
  prev="$arg"
done
echo "2026-09-25 10:00:00,000 1 INFO $db odoo.modules.loading: loading 1 modules..."
IFS=, read -r -a wanted <<< "$mods"
for module in "${wanted[@]}"; do
  echo "2026-09-25 10:00:01,000 1 INFO $db odoo.modules.loading: Loading module $module (1/1)"
  case ",${ODOO_FAIL_ON:-}," in
    *",$module,"*)
      echo "2026-09-25 10:00:01,500 1 ERROR $db odoo.tools.convert: while parsing /mnt/extra-addons/$module/views/${module}_views.xml:12"
      echo "Traceback (most recent call last):"
      echo '  File "/opt/odoo/odoo/tools/convert.py", line 1, in _tag_record'
      echo "odoo.tools.convert.ParseError: Element '<xpath expr=\"//field[@name=\\'gone\\']\">' cannot be located in parent view"
      exit 1 ;;
  esac
  printf '%s\n' "$module" >> "$STUB_STATE/installed.$db"
done
echo "2026-09-25 10:00:09,000 1 INFO $db odoo.modules.loading: Modules loaded."
exit 0
STUB
chmod +x "$bin"/*

# ---------- fixture addons tree ------------------------------------------------
# alpha <- alpha_ext is one tree; beta is a second one that merely shares the
# core dependency `mail`. A partition that merges them on that shared dependency
# is the bug this fixture exists to catch.
tree="$work/addons"; mkdir -p "$tree"
manifest() { mkdir -p "$tree/$1"; printf '{"name": "%s", "version": "19.0.1.0.0", "depends": [%s]%s}\n' "$1" "$2" "${3:-}" > "$tree/$1/__manifest__.py"; }
manifest alpha     "'base', 'mail'"
manifest alpha_ext "'alpha'"
manifest beta      "'base', 'mail'"
manifest legacy    "'base'" ', "installable": False'

state="$work/state"
reset() { rm -rf "$state"; mkdir -p "$state"; : > "$state/dbs"; }
art() { echo "$work/art"; }

run() { # run <artifacts-suffix> [args...]; stdout captured, stderr to $state/err
  local suffix="$1"; shift
  PATH="$bin:$PATH" STUB_STATE="$state" ODOO_FAIL_ON="${FAIL_ON:-}" \
    bash "$SUT" --artifacts "$work/art-$suffix" "$@" 2>"$state/err"
}

field() { python3 -c "
import json, sys
value = json.loads(sys.argv[1])
for key in sys.argv[2].split('.'):
    value = value[int(key)] if key.isdigit() else (value or {}).get(key)
print(json.dumps(value) if isinstance(value, (list, dict, bool, type(None))) else value)" "$1" "$2"; }

# ---------- 1. --db is required (#880) ------------------------------------------
reset
out="$(run req --no-conf "$tree")"; rc=$?
expect "no --db exits 2" "$rc" "2"
contains "no --db names the flag" "$(cat "$state/err")" "--db <name> is required"
expect "no --db runs no odoo" "$( [ -f "$state/odoo.argv" ] && echo yes || echo no )" "no"

# ---------- 2. a config that names a database is refused (#880) -----------------
reset
conf="$work/odoo.conf"
printf '[options]\naddons_path = /opt/odoo/addons\ndata_dir = /var/lib/odoo\ndb_name = projdb\n' > "$conf"
out="$(run confdb --db verify --conf "$conf" "$tree")"; rc=$?
expect "explicit conf with db_name exits 2" "$rc" "2"
contains "and says which key" "$(cat "$state/err")" "declares db_name = projdb"

# The target being the project's own database is refused ahead of everything,
# because no flag makes that the right answer.
reset
out="$(run confsame --db projdb --conf "$conf" "$tree")"; rc=$?
expect "--db equal to conf db_name exits 2" "$rc" "2"
contains "and says so" "$(cat "$state/err")" "is the db_name declared in"

# An auto-detected config was not asked for, so it is sanitised rather than
# refused — otherwise the script is unusable in the very devcontainer it is for.
reset
out="$(ODOO_RC="$conf" run sanitize --db verify "$tree")"; rc=$?
expect "auto-detected conf is sanitised, not refused" "$rc" "0"
expect "sanitised copy drops db_name" \
  "$(grep -c '^db_name' "$work/art-sanitize/odoo.conf" 2>/dev/null)" "0"
expect "sanitised copy drops data_dir" \
  "$(grep -c '^data_dir' "$work/art-sanitize/odoo.conf" 2>/dev/null)" "0"
contains "and is the config odoo gets" "$(cat "$state/odoo.argv")" "-c $work/art-sanitize/odoo.conf"

# ---------- 3. a populated target is refused unless --force (#880) --------------
reset
printf '%s\n' verify >> "$state/dbs"
printf 'sale\nstock\n' > "$state/installed.verify"
out="$(run guard --db verify --no-conf "$tree")"; rc=$?
expect "populated target exits 2" "$rc" "2"
contains "and lists what is there" "$(cat "$state/err")" "sale"
expect "and drops nothing" "$( [ -f "$state/dropdb.argv" ] && echo yes || echo no )" "no"

reset
printf '%s\n' verify >> "$state/dbs"
printf 'sale\nstock\n' > "$state/installed.verify"
out="$(run force --db verify --no-conf --force "$tree")"; rc=$?
expect "--force proceeds" "$rc" "0"
contains "--force drops the target" "$(cat "$state/dropdb.argv")" "verify"

# A database this script created is its own, so the fix-and-retry loop may
# target the same name twice without tripping the guard.
reset
printf '%s\n' verify >> "$state/dbs"
printf 'alpha\nbeta\n' > "$state/installed.verify"
: > "$state/owned.verify"
out="$(run owned --db verify --no-conf "$tree")"; rc=$?
expect "a stamped target is reused, not refused" "$rc" "0"

# ---------- 4. the plain full install still works -------------------------------
reset
out="$(run plain --db verify --no-conf "$tree")"; rc=$?
summary="$(printf '%s\n' "$out" | tail -1)"
expect "no mode flags exits 0" "$rc" "0"
expect "no mode flags is one install" "$(grep -c -- '-i ' "$state/odoo.argv")" "1"
expect "mode is full" "$(field "$summary" mode)" "full"
expect "no trees are reported" "$(field "$summary" trees)" "[]"
expect "full targets the named db" "$(field "$summary" full.db)" "verify"
expect "full passed" "$(field "$summary" full.status)" "pass"
expect "summary says passed" "$(field "$summary" passed)" "true"
# installable: False is not a verification target.
expect "uninstallable module is skipped" \
  "$(field "$summary" custom_modules)" '["alpha", "alpha_ext", "beta"]'
expect "no template without --template" "$(field "$summary" template)" "null"

# ---------- 5. isolation flags reach Odoo (#880) --------------------------------
contains "data dir is moved off Odoo's default" "$(cat "$state/odoo.argv")" "--data-dir $work/art-plain/data"
expect "and is never /var/lib/odoo" "$(grep -c '/var/lib/odoo' "$state/odoo.argv")" "0"
contains "the tree under test leads the addons path" "$(cat "$state/odoo.argv")" "--addons-path=$work/addons"

# ---------- 6. per-tree partition and summary shape (#878) ----------------------
reset
out="$(run trees --db verify --no-conf --per-tree --summary "$work/summary.json" "$tree")"; rc=$?
summary="$(printf '%s\n' "$out" | tail -1)"
expect "per-tree exits 0 when every tree is green" "$rc" "0"
expect "mode is per-tree" "$(field "$summary" mode)" "per-tree"
expect "two independent trees" "$(python3 -c "
import json, sys; print(len(json.loads(sys.argv[1])['trees']))" "$summary")" "2"
# alpha_ext depends on alpha, so they install together; beta shares only the
# core module mail and must stay on its own.
expect "tree 1 is the alpha chain" "$(field "$summary" trees.0.modules)" '["alpha", "alpha_ext"]'
expect "tree 2 is beta alone" "$(field "$summary" trees.1.modules)" '["beta"]'
expect "each tree gets its own db" "$(field "$summary" trees.1.db)" "verify_t2"
expect "tree status" "$(field "$summary" trees.0.status)" "pass"
expect "tree error is null when green" "$(field "$summary" trees.0.error)" "null"
expect "the full install runs last" "$(field "$summary" full.status)" "pass"
expect "three installs: two trees then the lot" "$(grep -c -- '-i ' "$state/odoo.argv")" "3"
expect "the last install is the full one" \
  "$(tail -1 "$state/odoo.argv" | grep -c -- '-i alpha,alpha_ext,beta')" "1"
expect "throwaway tree dbs are dropped" "$(grep -c 'verify_t1' "$state/dropdb.argv")" "2"
expect "--summary writes the same document" "$(cat "$work/summary.json")" "$summary"

# ---------- 7. a failing tree names its module and its decisive line (#878) -----
reset
out="$(FAIL_ON=beta run failtree --db verify --no-conf --per-tree "$tree")"; rc=$?
summary="$(printf '%s\n' "$out" | tail -1)"
expect "a failing tree exits 1" "$rc" "1"
expect "the green tree still passes" "$(field "$summary" trees.0.status)" "pass"
expect "the broken tree fails" "$(field "$summary" trees.1.status)" "fail"
expect "and names the module" "$(field "$summary" trees.1.module)" "beta"
contains "and quotes the decisive line" "$(field "$summary" trees.1.error)" "cannot be located in parent view"
# A full run over a known-broken tree aborts on it and re-finds what was
# already reported, so it is not run at all.
expect "the full install is skipped" "$(field "$summary" full.status)" "skipped"
expect "summary says not passed" "$(field "$summary" passed)" "false"
expect "the addons path is reported" "$(field "$summary" addons_path)" "$work/addons"

# ---------- 8. template build, reuse, and rebuild on a key change (#879) --------
reset
out="$(run tpl1 --db verify --no-conf --template-db fixedtpl --per-tree "$tree")"; rc=$?
summary="$(printf '%s\n' "$out" | tail -1)"
key1="$(field "$summary" template.key)"
expect "the first run builds the template" "$(field "$summary" template.reused)" "false"
expect "the template holds the core closure only" \
  "$(field "$summary" template.core_modules)" '["base", "mail"]'
contains "core modules are installed into it" "$(head -1 "$state/odoo.argv")" "-d fixedtpl -i base,mail"
contains "and working dbs come from it" "$(cat "$state/createdb.argv")" "-T fixedtpl verify_t1"

before="$(wc -l < "$state/odoo.argv")"
out="$(run tpl2 --db verify --no-conf --template-db fixedtpl --per-tree "$tree")"; rc=$?
summary="$(printf '%s\n' "$out" | tail -1)"
expect "a matching key is reused" "$(field "$summary" template.reused)" "true"
expect "the key is stable" "$(field "$summary" template.key)" "$key1"
expect "and the core closure is not reinstalled" \
  "$(( $(wc -l < "$state/odoo.argv") - before ))" "3"

# Change the core dependency set: the key changes, so the template is rebuilt
# rather than silently reused.
manifest beta "'base', 'mail', 'stock'"
out="$(run tpl3 --db verify --no-conf --template-db fixedtpl --per-tree "$tree")"; rc=$?
summary="$(printf '%s\n' "$out" | tail -1)"
expect "a changed dependency set changes the key" \
  "$( [ "$(field "$summary" template.key)" = "$key1" ] && echo same || echo different )" "different"
expect "and the template is rebuilt" "$(field "$summary" template.reused)" "false"
contains "the stale template is dropped first" "$(cat "$state/dropdb.argv")" "fixedtpl"
expect "the new closure includes the new dependency" \
  "$(field "$summary" template.core_modules)" '["base", "mail", "stock"]'
manifest beta "'base', 'mail'"

echo "{\"passed\": $pass, \"failed\": $fail}"
[ "$fail" -eq 0 ]
