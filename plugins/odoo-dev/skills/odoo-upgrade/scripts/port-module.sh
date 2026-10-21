#!/usr/bin/env bash
# port-module.sh — the mechanical part of a module port as one script (#1154).
#
# Usage: port-module.sh <module> --to T --worktree W [--from S]
#
# S defaults to the series prefix of W/<module>/__manifest__.py, else the major
# of $ODOO_VERSION.
#
# Step 1  `odoo-bin upgrade_code --from S --to T --addons-path W`.
#         upgrade_code has no module filter, so it runs against all of W.
#         Afterwards every tracked path OUTSIDE W/<module> that it changed is
#         restored with `git checkout --`. (A symlinked tmp addons dir would pin
#         the tree, so this script does not use one.) Only paths that were clean
#         before the run are restored; your earlier edits stay. Untracked files
#         that upgrade_code creates outside the module are not touched.
#         There is no --dry-run: the diff is the review.
#         Binary: odooNN-bin under $ODOO_SRC_ROOT (default /var/lib/odoo/src)
#         when the container major ($ODOO_VERSION) differs from T and that file
#         exists (as run-tests.sh does for a side core), else odoo-bin, else odoo.
# Step 2  `odoo-module-migrate --directory W --modules <module>
#         --init-version-name S --target-version-name T --no-commit`.
#         Missing on PATH: exit 2 with the install hint. The step 1 rewrites stay
#         in the tree and the JSON says the migrator did not run.
#         Install: pip install 'odoo-module-migrator==0.5.0'
#         pin: 0.5.0, uploaded 2025-08-21 and the newest release on PyPI on
#         2026-09-20, the date of the upstream merge named in #1154.
#
# Nothing is committed. The commit is a later change (#1168): add it after the
# JSON line is built, in the "commit" marker below.
#
# Output: one JSON line on stdout:
#   {module, from, to, upgrade_code:{rc, files_changed[]},
#    migrator:{ran, rc, files_changed[]}, ok}
# File paths are relative to W.
# Exit: 0 ok · 1 a tool failed · 2 usage or the migrator is missing
set -uo pipefail

usage() { echo "usage: port-module.sh <module> --to T --worktree W [--from S]" >&2; exit 2; }

module=""; to=""; wt=""; from=""
while [ $# -gt 0 ]; do
  case "$1" in
    --to) to="${2:-}"; shift 2 || usage ;;
    --worktree) wt="${2:-}"; shift 2 || usage ;;
    --from) from="${2:-}"; shift 2 || usage ;;
    -*) usage ;;
    *) [ -z "$module" ] || usage; module="$1"; shift ;;
  esac
done
[ -n "$module" ] && [ -n "$to" ] && [ -n "$wt" ] || usage
[ -d "$wt/$module" ] || { echo "port-module.sh: $wt/$module is not a directory" >&2; exit 2; }

if [ -z "$from" ]; then
  manifest="$wt/$module/__manifest__.py"
  if [ -f "$manifest" ]; then
    from="$(grep -oE "['\"]version['\"][[:space:]]*:[[:space:]]*['\"][0-9]+\.[0-9]+" "$manifest" | grep -oE '[0-9]+\.[0-9]+$' | head -1)"
  fi
  if [ -z "$from" ] && [ -n "${ODOO_VERSION:-}" ]; then
    from="$(printf '%s' "$ODOO_VERSION" | grep -oE '^[0-9]+\.[0-9]+' | head -1)"
  fi
  [ -n "$from" ] || { echo "port-module.sh: no --from, no manifest version prefix and no ODOO_VERSION" >&2; exit 2; }
fi

# Pick the odoo binary.
odoo_bin=""
t_major="${to%%.*}"; c_ver="${ODOO_VERSION:-}"; c_major="${c_ver%%.*}"
side="${ODOO_SRC_ROOT:-/var/lib/odoo/src}/odoo${t_major}-bin"
if [ -n "${ODOO_VERSION:-}" ] && [ "$c_major" != "$t_major" ] && [ -x "$side" ]; then
  odoo_bin="$side"
elif command -v odoo-bin >/dev/null 2>&1; then
  odoo_bin="odoo-bin"
elif command -v odoo >/dev/null 2>&1; then
  odoo_bin="odoo"
else
  echo "port-module.sh: no odoo-bin or odoo on PATH" >&2; exit 2
fi

# Tracked paths with unstaged changes, relative to W.
dirty() { git -C "$wt" diff --name-only --relative 2>/dev/null | sort; }
# Lines in $2 and not in $1.
newer() { comm -13 <(printf '%s\n' "$1") <(printf '%s\n' "$2"); }

before="$(dirty)"
"$odoo_bin" upgrade_code --from "$from" --to "$to" --addons-path "$wt"
uc_rc=$?
after="$(dirty)"
changed="$(newer "$before" "$after")"

uc_files=""; outside=""
while IFS= read -r p; do
  [ -n "$p" ] || continue
  case "$p" in
    "$module"/*) uc_files="${uc_files}${p}"$'\n' ;;
    *) outside="${outside}${p}"$'\n' ;;
  esac
done <<<"$changed"
while IFS= read -r p; do
  [ -n "$p" ] || continue
  git -C "$wt" checkout -- "$p"
done <<<"$outside"

mg_ran=false; mg_rc=0; mg_files=""; missing=false
if [ "$uc_rc" -le 1 ]; then
  if command -v odoo-module-migrate >/dev/null 2>&1; then
    mg_ran=true
    b2="$(dirty)"
    odoo-module-migrate --directory "$wt" --modules "$module" \
      --init-version-name "$from" --target-version-name "$to" --no-commit
    mg_rc=$?
    mg_files="$(newer "$b2" "$(dirty)")"
  else
    missing=true
    echo "port-module.sh: odoo-module-migrate is not on PATH; the upgrade_code rewrites stay in the tree. Install it: pip install 'odoo-module-migrator==0.5.0'" >&2
  fi
fi

ok=false
if [ "$uc_rc" -le 1 ] && [ "$missing" = false ] && [ "$mg_rc" -eq 0 ]; then ok=true; fi

python3 -I -c '
import json, sys
m, f, t, rc, ucf, ran, mrc, mf, ok = sys.argv[1:10]
lines = lambda s: [x for x in s.split("\n") if x]
print(json.dumps({"module": m, "from": f, "to": t,
  "upgrade_code": {"rc": int(rc), "files_changed": lines(ucf)},
  "migrator": {"ran": ran == "true", "rc": int(mrc), "files_changed": lines(mf)},
  "ok": ok == "true"}))' "$module" "$from" "$to" "$uc_rc" "$uc_files" "$mg_ran" "$mg_rc" "$mg_files" "$ok"

# commit: #1168 adds `chore(<module>): upgrade_code <series>` here, before exit.
[ "$missing" = true ] && exit 2
[ "$ok" = true ] && exit 0
exit 1
