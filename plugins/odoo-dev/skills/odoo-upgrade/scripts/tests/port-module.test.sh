#!/usr/bin/env bash
# port-module.test.sh — offline tests for port-module.sh.
#
# odoo-bin and odoo-module-migrate are PATH stubs that log argv and rewrite
# files. A real git repo in a temp dir holds the module and a second module.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$(cd "$SCRIPT_DIR/.." && pwd)/port-module.sh"
work="$(mktemp -d "${TMPDIR:-/tmp}/port-module-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
expect() { if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: wanted '$3', got '$2'" >&2; fi; }
contains() { case "$2" in *"$3"*) expect "$1" yes yes ;; *) expect "$1" "$2" "...$3...";; esac; }

stubs="$work/stubs"; mkdir -p "$stubs"
cat > "$stubs/odoo-bin" <<'STUB'
#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG/odoo-bin.log"
echo changed > "$STUB_WT/acme/a.py"
echo changed > "$STUB_WT/other/b.py"
exit 1
STUB
cat > "$stubs/odoo-module-migrate" <<'STUB'
#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG/migrate.log"
echo migrated > "$STUB_WT/acme/c.py"
exit 0
STUB
chmod +x "$stubs/odoo-bin" "$stubs/odoo-module-migrate"

wt="$work/wt"; mkdir -p "$wt/acme" "$wt/other"
printf "{'name': 'Acme', 'version': '18.0.1.0.0'}\n" > "$wt/acme/__manifest__.py"
echo orig > "$wt/acme/a.py"; echo orig > "$wt/acme/c.py"; echo orig > "$wt/other/b.py"
git -C "$wt" init -q
git -C "$wt" add -A
git -C "$wt" -c core.hooksPath=/dev/null -c user.name=t -c user.email=t@t commit -qm init

export STUB_LOG="$work" STUB_WT="$wt"
# A PATH of the stubs plus the basic tools, with no real odoo or migrator.
base_path="$stubs:/usr/bin:/bin"

out="$(PATH="$base_path" bash "$SUT" acme --to 19.0 --worktree "$wt" 2>"$work/err")"; rc=$?
expect "rc ok" "$rc" 0
contains "argv odoo-bin" "$(cat "$work/odoo-bin.log")" "upgrade_code --from 18.0 --to 19.0 --addons-path $wt"
contains "argv migrator" "$(cat "$work/migrate.log")" "--directory $wt --modules acme --init-version-name 18.0 --target-version-name 19.0 --no-commit"
expect "outside reverted" "$(cat "$wt/other/b.py")" orig
expect "inside kept" "$(cat "$wt/acme/a.py")" changed
expect "json" "$(printf '%s' "$out" | python3 -I -c 'import json,sys; d=json.load(sys.stdin); print(d["module"],d["from"],d["to"],d["upgrade_code"]["rc"],d["upgrade_code"]["files_changed"],d["migrator"]["ran"],d["migrator"]["files_changed"],d["ok"])')" \
  "acme 18.0 19.0 1 ['acme/a.py'] True ['acme/c.py'] True"

# Missing migrator: exit 2, hint, upgrade_code rewrite stays.
git -C "$wt" checkout -q -- .
nomig="$work/nomig"; mkdir -p "$nomig"; cp "$stubs/odoo-bin" "$nomig/"
out="$(PATH="$nomig:/usr/bin:/bin" bash "$SUT" acme --to 19.0 --worktree "$wt" 2>"$work/err")"; rc=$?
expect "missing migrator rc" "$rc" 2
contains "install hint" "$(cat "$work/err")" "pip install 'odoo-module-migrator"
contains "ran false" "$out" '"ran": false'
expect "step 1 kept" "$(cat "$wt/acme/a.py")" changed

# --to missing: exit 2.
PATH="$base_path" bash "$SUT" acme --worktree "$wt" >/dev/null 2>&1; expect "no --to rc" "$?" 2

echo "port-module.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
