#!/usr/bin/env bash
# port-lint.test.sh — offline tests for port-lint.sh.
#
# pylint and oca-checks-odoo-module are PATH stubs. PORT_LINT_HAVE_PYLINT_ODOO=1
# (test-only) replaces the `import pylint_odoo` probe.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$(cd "$SCRIPT_DIR/.." && pwd)/port-lint.sh"
work="$(mktemp -d "${TMPDIR:-/tmp}/port-lint-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
expect() { if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: wanted '$3', got '$2'" >&2; fi; }
contains() { case "$2" in *"$3"*) expect "$1" yes yes ;; *) expect "$1" "$2" "...$3...";; esac; }
jq_py() { python3 -I -c "import json,sys; d=json.loads(sys.argv[1]); print($2)" "$1"; }

wt="$work/wt"; mkdir -p "$wt/acme"
echo "{'name': 'Acme'}" > "$wt/acme/__manifest__.py"

dirty="$work/dirty"; clean="$work/clean"; none="$work/none"
mkdir -p "$dirty" "$clean" "$none"
cat > "$dirty/pylint" <<'STUB'
#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG/pylint.log"
printf '%s\n' '[{"type":"error","message-id":"E8146","symbol":"deprecated-name-get","path":"acme/models/a.py","line":3,"message":"name_get"},{"type":"warning","message-id":"W8165","symbol":"deprecated-self-cr","path":"acme/models/b.py","line":9,"message":"self.cr"}]'
exit 22
STUB
cat > "$dirty/oca-checks-odoo-module" <<'STUB'
#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG/oca.log"
echo "acme/views/v.xml:4 xml-deprecated-tree-attribute The tree tag is deprecated"
echo "acme/views/w.xml:7 xml-syntax-error Syntax error"
exit 1
STUB
printf '#!/usr/bin/env bash\necho "[]"\n' > "$clean/pylint"
printf '#!/usr/bin/env bash\nexit 0\n' > "$clean/oca-checks-odoo-module"
chmod +x "$dirty"/* "$clean"/*
export STUB_LOG="$work" PORT_LINT_HAVE_PYLINT_ODOO=1
tools="/usr/bin:/bin"

out="$(PATH="$dirty:$tools" bash "$SUT" "$wt" --series 19.0 --modules acme 2>/dev/null)"; rc=$?
expect "dirty rc" "$rc" 1
expect "errors" "$(jq_py "$out" "len(d['errors'])")" 2
expect "warnings" "$(jq_py "$out" "len(d['warnings'])")" 2
expect "ok false" "$(jq_py "$out" "d['ok']")" False
expect "module" "$(jq_py "$out" "d['errors'][0]['module']")" acme
contains "pylint argv" "$(cat "$work/pylint.log")" "--valid-odoo-versions=19.0"
contains "oca argv" "$(cat "$work/oca.log")" "--no-verbose $wt/acme"

out="$(PATH="$clean:$tools" bash "$SUT" "$wt" --series 19.0 2>/dev/null)"; rc=$?
expect "clean rc" "$rc" 0
expect "clean ok" "$(jq_py "$out" "d['ok']")" True

err="$(PATH="$none:$tools" PORT_LINT_HAVE_PYLINT_ODOO= bash "$SUT" "$wt" --series 19.0 2>&1 >/dev/null)"; rc=$?
expect "missing rc" "$rc" 2
contains "pylint hint" "$err" "pip install pylint-odoo"
contains "oca hint" "$err" "pip install oca-odoo-pre-commit-hooks"

PATH="$clean:$tools" bash "$SUT" "$wt" >/dev/null 2>&1; expect "no --series rc" "$?" 2

echo "port-lint.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
