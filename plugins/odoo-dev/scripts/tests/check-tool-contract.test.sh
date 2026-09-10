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

if command -v odoo-sdk >/dev/null 2>&1 || python3 -c 'import odoo_sdk' >/dev/null 2>&1; then
  out="$(bash "$SUT" --plugin-root "$PLUGIN_ROOT" 2>&1)"; rc=$?
  if [ "$rc" -eq 0 ]; then
    ok "real tree via installed SDK: $out"
  else
    bad "real tree via installed SDK: rc=$rc: $out"
  fi
else
  echo "SKIP real tree via installed SDK (odoo-sdk not installed here)"
fi

echo
echo "check-tool-contract.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
