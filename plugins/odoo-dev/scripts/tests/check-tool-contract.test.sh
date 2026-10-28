#!/usr/bin/env bash
# check-tool-contract.test.sh — offline tests for check-tool-contract.sh.
#
# No network, no docker, no Odoo required: fixture cases inject their surface
# with --surface-file. The real-tree case runs the gate against the live
# `odoo-dev cmd --list --json` when a working odoo-dev is on PATH, and skips
# otherwise.
#
# Failures asserted by the name they must report, not by exit code alone —
# "exits 1" would stay green if the gate started failing for the wrong reason.
#
# Two cases put a stub `odoo-dev` early on PATH (one that does not run, one
# that runs but has no `cmd` subcommand) and assert the gate refuses with
# exit 2 instead of dying on the broken one (#775).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$HERE/../check-tool-contract.sh"
PLUGIN_ROOT="$(cd "$HERE/../.." && pwd)"
FIX="$HERE/fixtures/tool-contract"

pass=0; fail=0
ok()  { echo "PASS $*"; pass=$((pass + 1)); }
bad() { echo "FAIL $*"; fail=$((fail + 1)); }

# --- fixture: an unknown tool name must fail and be named ------------------------

out="$(bash "$SUT" --plugin-root "$FIX/unknown-tool" \
        --surface-file "$FIX/unknown-tool/surface.txt" 2>&1)"; rc=$?
if [ "$rc" -ne 1 ]; then
  bad "unknown-tool: expected exit 1, got $rc: $out"
else
  ok "unknown-tool: exits 1"
fi
for name in frobnicate_widgets bogus_bare; do
  if printf '%s' "$out" | grep -q "$name"; then
    ok "unknown-tool: reports $name"
  else
    bad "unknown-tool: did not report $name: $out"
  fi
done
# A known reference in the same tree must NOT be flagged, and the ambient
# backticked snake_case (`default_branch`) must not be treated as a tool.
for name in task_note default_branch view_mode; do
  if printf '%s' "$out" | grep -qE "^  $name  "; then
    bad "unknown-tool: falsely flagged $name: $out"
  else
    ok "unknown-tool: does not flag $name"
  fi
done
# The report must point at the file carrying the reference.
if printf '%s' "$out" | grep -q "skills/demo-skill/SKILL.md"; then
  ok "unknown-tool: names the referencing file"
else
  bad "unknown-tool: no file location in: $out"
fi

# --- fixture: every reference on the surface must pass --------------------------

out="$(bash "$SUT" --plugin-root "$FIX/all-known" \
        --surface-file "$FIX/all-known/surface.txt" 2>&1)"; rc=$?
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q "all on the"; then
  ok "all-known: passes ($out)"
else
  bad "all-known: expected clean pass, got rc=$rc: $out"
fi

# --- fixture: a missing surface source must refuse to run (exit 2) --------------

out="$(bash "$SUT" --plugin-root "$FIX/all-known" \
        --surface-file "$FIX/all-known/no-such-surface.txt" 2>&1)"; rc=$?
if [ "$rc" -eq 2 ]; then
  ok "missing surface file: exits 2, never a silent pass"
else
  bad "missing surface file: expected exit 2, got $rc: $out"
fi

# --- a broken odoo-dev on PATH must be refused, not crash the gate (#775) -------

# `command -v` finds a stale console script; `odoo-dev cmd --list --json |
# python3` then blows up, and `set -o pipefail` once propagated that straight
# out of the gate. Two stubs, one per way a console script can be broken. The
# gate must refuse with its documented exit 2 - never the crash, never a pass.
work="$(mktemp -d "${TMPDIR:-/tmp}/tool-contract-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT
stub_bin="$work/broken-bin"
mkdir -p "$stub_bin"

assert_refuses() {
  local what="$1" out rc
  out="$(PATH="$stub_bin:$PATH" bash "$SUT" --plugin-root "$FIX/all-known" 2>&1)"; rc=$?
  if [ "$rc" -eq 2 ] && printf '%s' "$out" | grep -q "cannot resolve the command surface"; then
    ok "$what: refuses with the documented exit 2, never a silent pass"
  else
    bad "$what: expected exit 2, got rc=$rc: $out"
  fi
}

# (a) does not run at all - `--version` itself fails.
cat > "$stub_bin/odoo-dev" <<'STUB'
#!/usr/bin/env bash
echo "ModuleNotFoundError: No module named 'odoo_cli'" >&2
exit 1
STUB
chmod +x "$stub_bin/odoo-dev"
assert_refuses "broken odoo-dev (no import)"

# (b) answers --version, but rejects the dispatcher - an older build.
cat > "$stub_bin/odoo-dev" <<'STUB'
#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then
  echo "odoo-dev 0.0.1"
  exit 0
fi
echo "odoo-dev: error: argument command: invalid choice: '${1:-}'" >&2
exit 2
STUB
chmod +x "$stub_bin/odoo-dev"
assert_refuses "stale odoo-dev (no cmd subcommand)"

# An explicit --surface-file must still win over a broken CLI outright.
out="$(PATH="$stub_bin:$PATH" bash "$SUT" --plugin-root "$FIX/all-known" \
        --surface-file "$FIX/all-known/surface.txt" 2>&1)"; rc=$?
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q "all on the"; then
  ok "broken odoo-dev: --surface-file still takes precedence"
else
  bad "broken odoo-dev: --surface-file should have won, got rc=$rc: $out"
fi

# A working stub serves the surface at run time: the gate reads it from
# `odoo-dev cmd --list --json`.
cat > "$stub_bin/odoo-dev" <<'STUB'
#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then echo "odoo-dev 9.0.0"; exit 0; fi
if [ "${1:-}" = "cmd" ] && [ "${2:-}" = "--list" ]; then
  echo '[{"name":"task_note","description":"x"},{"name":"get_task","description":"z"},{"name":"timesheet_summary","description":"y"}]'
  exit 0
fi
exit 2
STUB
chmod +x "$stub_bin/odoo-dev"
out="$(PATH="$stub_bin:$PATH" bash "$SUT" --plugin-root "$FIX/all-known" 2>&1)"; rc=$?
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q "all on the 3-command surface"; then
  ok "stub odoo-dev: surface read at run time ($out)"
else
  bad "stub odoo-dev: expected a pass on a 3-command surface, got rc=$rc: $out"
fi

# --- the real plugin tree against the live odoo-dev -----------------------------

if command -v odoo-dev >/dev/null 2>&1 && odoo-dev --version >/dev/null 2>&1; then
  out="$(bash "$SUT" --plugin-root "$PLUGIN_ROOT" 2>&1)"; rc=$?
  if [ "$rc" -eq 0 ]; then
    ok "real tree via installed odoo-dev: $out"
  else
    bad "real tree via installed odoo-dev: rc=$rc: $out"
  fi
else
  echo "SKIP real tree via installed odoo-dev (no working odoo-dev here)"
fi

echo
echo "check-tool-contract.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
