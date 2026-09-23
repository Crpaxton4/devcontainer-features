#!/usr/bin/env bash
# check-tool-contract.test.sh — offline tests for check-tool-contract.sh.
#
# No network, no docker, no Odoo, no SDK install required: fixture cases inject
# their surface with --surface-file, and the real-tree case derives the surface
# statically from the `_name = "..."` declarations in the SDK's builtin command
# modules — the same names `cmd --list --json` serves, without importing the
# package. When the real CLI (or an importable odoo_sdk) happens to be present,
# a bonus case runs the gate end to end against it.
#
# Failures asserted by the name they must report, not by exit code alone —
# "exits 1" would stay green if the gate started failing for the wrong reason.
#
# Two cases put a stub `odoo-sdk` early on PATH (one that does not import, one
# that imports but has no `cmd` subcommand) and assert the gate falls through
# to the next source instead of dying on the broken one (#775).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$HERE/../check-tool-contract.sh"
PLUGIN_ROOT="$(cd "$HERE/../.." && pwd)"
REPO_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
FIX="$HERE/fixtures/tool-contract"
BUILTIN_DIR="$REPO_ROOT/libraries/odoo_sdk/src/odoo_sdk/commands/builtin"

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

# --- a broken odoo-sdk on PATH must fall through, not kill the gate (#775) ------

# Git worktrees share the main checkout's .venv, so a concurrent worker can
# leave the one console script every worker resolves stale or half-rewritten.
# `command -v` still finds it; `odoo-sdk cmd --list --json | python3` then
# blows up, and `set -o pipefail` propagated that straight out of the gate.
# Two stubs, one per way a console script can be broken. Either a later source
# resolves (exit 0) or the gate refuses with its documented exit 2 — never the
# crash, and never a silent pass.
work="$(mktemp -d "${TMPDIR:-/tmp}/tool-contract-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT
stub_bin="$work/broken-bin"
mkdir -p "$stub_bin"

assert_falls_through() {
  local what="$1" out rc
  out="$(PATH="$stub_bin:$PATH" bash "$SUT" --plugin-root "$FIX/all-known" 2>&1)"; rc=$?
  if [ "$rc" -eq 0 ]; then
    ok "$what: fell through to the importable module"
  elif [ "$rc" -eq 2 ] && printf '%s' "$out" | grep -q "cannot resolve the command surface"; then
    ok "$what: refuses with the documented exit 2, never a silent pass"
  else
    bad "$what: expected a clean fall-through, got rc=$rc: $out"
  fi
}

# (a) does not import at all — `--help` itself fails.
cat > "$stub_bin/odoo-sdk" <<'STUB'
#!/usr/bin/env bash
echo "ModuleNotFoundError: No module named 'odoo_sdk'" >&2
exit 1
STUB
chmod +x "$stub_bin/odoo-sdk"
assert_falls_through "broken odoo-sdk (no import)"

# (b) imports and answers --help, but rejects `cmd` — an older SDK build left
# on PATH by a shared venv, which the --help probe alone cannot see.
cat > "$stub_bin/odoo-sdk" <<'STUB'
#!/usr/bin/env bash
if [ "${1:-}" = "--help" ]; then
  echo "usage: odoo-sdk [-h] {list,report} ..."
  exit 0
fi
echo "odoo-sdk: error: argument command: invalid choice: '${1:-}'" >&2
exit 2
STUB
chmod +x "$stub_bin/odoo-sdk"
assert_falls_through "stale odoo-sdk (no cmd subcommand)"

# An explicit --surface-file must still win over a broken CLI outright.
out="$(PATH="$stub_bin:$PATH" bash "$SUT" --plugin-root "$FIX/all-known" \
        --surface-file "$FIX/all-known/surface.txt" 2>&1)"; rc=$?
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q "all on the"; then
  ok "broken odoo-sdk: --surface-file still takes precedence"
else
  bad "broken odoo-sdk: --surface-file should have won, got rc=$rc: $out"
fi

# --- the real plugin tree against the real command names ------------------------

# The static surface: every `_name = "..."` declared by a builtin command
# module. That is exactly what populates BUILTIN_COMMANDS (see
# _registration.py), so the derivation tracks the SDK without importing it.
if [ -d "$BUILTIN_DIR" ]; then
  surface="$(mktemp "${TMPDIR:-/tmp}/tool-surface.XXXXXX")"
  sed -nE 's/^[[:space:]]*_name(: str)?[[:space:]]*=[[:space:]]*"([a-z_]+)".*/\2/p' \
    "$BUILTIN_DIR"/*.py | sort -u > "$surface"
  count="$(wc -l < "$surface" | tr -d ' ')"
  if [ "$count" -ge 40 ]; then
    ok "static surface: $count command names parsed from builtin/"
  else
    bad "static surface: only $count names parsed — the sed pattern drifted"
  fi
  out="$(bash "$SUT" --plugin-root "$PLUGIN_ROOT" --surface-file "$surface" 2>&1)"; rc=$?
  if [ "$rc" -eq 0 ]; then
    ok "real tree: every referenced tool is on the real surface ($out)"
  else
    bad "real tree: rc=$rc: $out"
  fi
  rm -f "$surface"
else
  bad "static surface: $BUILTIN_DIR missing — cannot verify the real tree"
fi

# --- bonus: the live resolution paths, when an SDK is actually present ----------

# The guard probes whether a source actually SERVES a surface, not merely
# whether `odoo-sdk` resolves on PATH — a stale console script from a shared
# venv (#775) resolves and then serves nothing, and this case must skip on it
# rather than report the gate's correct exit 2 as a failure.
if { command -v odoo-sdk >/dev/null 2>&1 && odoo-sdk cmd --list --json >/dev/null 2>&1; } \
   || python3 -c 'import odoo_sdk' >/dev/null 2>&1; then
  out="$(bash "$SUT" --plugin-root "$PLUGIN_ROOT" 2>&1)"; rc=$?
  if [ "$rc" -eq 0 ]; then
    ok "real tree via installed SDK: $out"
  else
    bad "real tree via installed SDK: rc=$rc: $out"
  fi
else
  echo "SKIP real tree via installed SDK (no working odoo-sdk or odoo_sdk here)"
fi

echo
echo "check-tool-contract.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
