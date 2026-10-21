#!/usr/bin/env bash
# upgrade-preflight.test.sh — offline tests for upgrade-preflight.sh.
#
# The three sibling scripts are stand-ins in a stub directory named by
# ODOO_DEV_SIBLING_SCRIPTS; `ssh` and `odoo` are stubs on PATH; the fingerprint
# runs against a real git repo in a temp directory. No network, no Odoo.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$(cd "$SCRIPT_DIR/.." && pwd)/upgrade-preflight.sh"
work="$(mktemp -d "${TMPDIR:-/tmp}/upgrade-preflight-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
expect() { if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: wanted '$3', got '$2'" >&2; fi; }
contains() { case "$2" in *"$3"*) expect "$1" yes yes ;; *) expect "$1" "$2" "...$3...";; esac; }

bin="$work/bin"; stubs="$work/stubs"; ent="$work/enterprise"
mkdir -p "$bin" "$stubs" "$ent"
git -C "$ent" init -q . && git -C "$ent" -c user.name=t -c user.email=t@t commit -q --allow-empty -m "chore: init"

cat > "$bin/odoo" <<'STUB'
#!/usr/bin/env bash
echo "Odoo Server 19.0-test"
STUB
cat > "$bin/ssh" <<'STUB'
#!/usr/bin/env bash
[ "${SSH_STUB_FAIL:-}" = connect ] && exit 255
case "$*" in
  *psql*) printf '%s\n' "${SSH_STUB_DB-prod_db}" ;;
esac
exit 0
STUB
cat > "$stubs/project-resolve.sh" <<'STUB'
#!/usr/bin/env bash
echo '{"project":"Acme","repo":"acme/odoo","repo_path":"/repos/acme","default_branch":"main","odoo_version":"18.0"}'
STUB
cat > "$stubs/preflight.sh" <<'STUB'
#!/usr/bin/env bash
echo "$*" > "${STUB_ARGS_FILE:-/dev/null}"
echo '{"ok":true,"failures":[]}'
STUB
cat > "$stubs/addons_paths.sh" <<'STUB'
#!/usr/bin/env bash
echo "{\"series\":\"19.0\",\"enterprise_path\":\"$ENT_PATH\",\"missing\":${ADDONS_MISSING:-[]}}"
STUB
export ENT_PATH="$ent"
chmod +x "$bin"/* "$stubs"/*

run() { PATH="$bin:$PATH" ODOO_DEV_SIBLING_SCRIPTS="$stubs" bash "$SUT" "$@" 2>"$work/err" >"$work/out"; echo $?; }
field() { python3 -I -c 'import json,sys; print(json.loads(open(sys.argv[1]).read().splitlines()[-1])[sys.argv[2]])' "$work/out" "$1"; }

# all green, with ssh
expect "green exit" "$(STUB_ARGS_FILE="$work/args" run --target 19.0 --project Acme --ssh host1)" 0
expect "green ok" "$(field ok)" True
expect "green source_db" "$(field source_db)" prod_db
expect "green repo" "$(field repo)" acme/odoo
expect "green repo_path" "$(field repo_path)" /repos/acme
expect "green default_branch" "$(field default_branch)" main
expect "green enterprise_path" "$(field enterprise_path)" "$ent"
expect "green fingerprint length" "$(field env_fingerprint | wc -c)" 65
contains "green stderr" "$(cat "$work/err")" "PASS ssh"
contains "preflight args" "$(cat "$work/args")" "--soft --target-series 19.0 --custom-tree /repos/acme"

# --repo-path overrides
run --target 19.0 --project Acme --repo-path /other >/dev/null
expect "repo-path override" "$(field repo_path)" /other

# fingerprint moves with the enterprise HEAD
before="$(field env_fingerprint)"
git -C "$ent" -c user.name=t -c user.email=t@t commit -q --allow-empty -m "chore: two"
run --target 19.0 --project Acme >/dev/null
[ "$before" != "$(field env_fingerprint)" ] && expect "fingerprint moves" ok ok || expect "fingerprint moves" same changed

# missing addons entry
expect "missing exit" "$(ADDONS_MISSING='["/mnt/enterprise"]' run --target 19.0 --project Acme)" 10
contains "missing failure" "$(field failures)" "addons: missing /mnt/enterprise"
expect "missing ok" "$(ADDONS_MISSING='["/mnt/enterprise"]' run --target 19.0 --project Acme >/dev/null; field ok)" False

# ssh failure
expect "ssh exit" "$(SSH_STUB_FAIL=connect run --target 19.0 --project Acme --ssh host1)" 10
contains "ssh failure" "$(field failures)" "ssh:"
expect "ssh empty db exit" "$(SSH_STUB_DB= run --target 19.0 --project Acme --ssh host1)" 10
contains "ssh empty db" "$(field failures)" "ssh:"

# usage
expect "no target exit" "$(run --project Acme)" 2
expect "no project exit" "$(run --target 19.0)" 2

echo "upgrade-preflight.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
