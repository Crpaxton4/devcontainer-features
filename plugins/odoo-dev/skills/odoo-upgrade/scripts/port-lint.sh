#!/usr/bin/env bash
# port-lint.sh — a static lint gate after the port of a module and before the install loop (#1155).
#
# Usage: port-lint.sh <worktree> --series T [--modules a,b]
#
# --modules defaults to every directory of <worktree> that holds a __manifest__.py.
#
# Tool 1  `pylint --load-plugins=pylint_odoo --disable=all --enable=odoolint
#         --valid-odoo-versions=T --output-format=json <module dirs>`.
#         Missing plugin: `pip install pylint-odoo`.
#         type error and fatal go to errors[]; warning, convention and refactor
#         go to warnings[].
# Tool 2  `oca-checks-odoo-module --no-verbose <module dirs>`. Each output line is
#         `<path>:<line> <code> <message>`. Every line is a warning, except the
#         codes xml-syntax-error and xml-duplicate-record-id, which are errors.
#         Missing tool: `pip install oca-odoo-pre-commit-hooks`.
# Neither tool checks that an xpath target exists. That is a separate check.
#
# Test-only: PORT_LINT_HAVE_PYLINT_ODOO=1 skips the `import pylint_odoo` probe.
#
# Output: one JSON line on stdout:
#   {series, modules[], errors[], warnings[], ok}
# Each entry is {tool, module, path, line, code, message}. ok is true when
# errors[] is empty.
# Exit: 0 no errors · 1 errors · 2 usage or a missing tool (the hint goes to stderr)
set -uo pipefail

usage() { echo "usage: port-lint.sh <worktree> --series T [--modules a,b]" >&2; exit 2; }

[ $# -ge 1 ] || usage
wt="$1"; shift
series=""; modules_csv=""
while [ $# -gt 0 ]; do
  case "$1" in
    --series) [ $# -ge 2 ] || usage; series="$2"; shift 2 ;;
    --modules) [ $# -ge 2 ] || usage; modules_csv="$2"; shift 2 ;;
    *) usage ;;
  esac
done
[ -n "$series" ] || usage
[ -d "$wt" ] || { echo "port-lint.sh: no such directory: $wt" >&2; exit 2; }
wt="$(cd "$wt" && pwd)"

modules=()
if [ -n "$modules_csv" ]; then
  IFS=',' read -r -a modules <<< "$modules_csv"
else
  for d in "$wt"/*/; do
    [ -f "${d}__manifest__.py" ] && modules+=("$(basename "$d")")
  done
fi
[ "${#modules[@]}" -gt 0 ] || { echo "port-lint.sh: no modules in $wt" >&2; exit 2; }
dirs=()
for m in "${modules[@]}"; do
  [ -d "$wt/$m" ] || { echo "port-lint.sh: no module directory: $wt/$m" >&2; exit 2; }
  dirs+=("$wt/$m")
done

missing=0
have_pylint=0
if [ "${PORT_LINT_HAVE_PYLINT_ODOO:-}" = 1 ] || python3 -c "import pylint_odoo" >/dev/null 2>&1; then
  command -v pylint >/dev/null 2>&1 && have_pylint=1
fi
if [ "$have_pylint" = 0 ]; then
  echo "port-lint.sh: pylint-odoo is missing. Install: pip install pylint-odoo" >&2
  missing=1
fi
have_oca=0
if command -v oca-checks-odoo-module >/dev/null 2>&1; then
  have_oca=1
else
  echo "port-lint.sh: oca-checks-odoo-module is missing. Install: pip install oca-odoo-pre-commit-hooks" >&2
  missing=1
fi
[ "$missing" = 0 ] || exit 2

tmp="$(mktemp -d "${TMPDIR:-/tmp}/port-lint.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
pylint --load-plugins=pylint_odoo --disable=all --enable=odoolint \
  --valid-odoo-versions="$series" --output-format=json "${dirs[@]}" \
  > "$tmp/pylint.json" 2> "$tmp/pylint.err" || true
oca-checks-odoo-module --no-verbose "${dirs[@]}" > "$tmp/oca.txt" 2> "$tmp/oca.err" || true

PL_TMP="$tmp" PL_WT="$wt" PL_SERIES="$series" PL_MODULES="$(IFS=,; echo "${modules[*]}")" \
python3 -I - <<'PY'
import json, os, re, sys

tmp, wt = os.environ["PL_TMP"], os.environ["PL_WT"]
errors, warnings = [], []

def rel(path):
    p = os.path.abspath(path) if os.path.isabs(path) else path
    return os.path.relpath(p, wt) if os.path.isabs(p) else p

def module_of(path):
    return rel(path).split(os.sep)[0]

try:
    data = json.load(open(os.path.join(tmp, "pylint.json")))
except ValueError:
    data = []
for m in data:
    path = rel(m.get("path", ""))
    entry = {"tool": "pylint-odoo", "module": module_of(m.get("path", "")), "path": path,
             "line": m.get("line"), "code": m.get("message-id") or m.get("symbol", ""),
             "message": m.get("message", "")}
    (errors if m.get("type") in ("error", "fatal") else warnings).append(entry)

ERROR_CODES = {"xml-syntax-error", "xml-duplicate-record-id"}
pat = re.compile(r"^(.+?):(\d+)\s+(\S+)\s*(.*)$")
for raw in open(os.path.join(tmp, "oca.txt")):
    raw = raw.strip()
    if not raw:
        continue
    g = pat.match(raw)
    if g:
        path, line, code, msg = g.group(1), int(g.group(2)), g.group(3), g.group(4)
    else:
        path, line, code, msg = "", None, "", raw
    entry = {"tool": "oca-checks-odoo-module", "module": module_of(path) if path else "",
             "path": rel(path) if path else "", "line": line, "code": code, "message": msg}
    (errors if code in ERROR_CODES else warnings).append(entry)

ok = not errors
print(json.dumps({"series": os.environ["PL_SERIES"], "modules": os.environ["PL_MODULES"].split(","),
                  "errors": errors, "warnings": warnings, "ok": ok}))
sys.exit(0 if ok else 1)
PY
