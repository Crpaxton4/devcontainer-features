#!/usr/bin/env bash
# addons_paths.sh — where to grep when asking "does standard Odoo already do this?"
#
# Usage: addons_paths.sh [--series 19.0]
#
# A prior-art verdict is only as good as the tree it was grepped against, and
# the trees differ per machine: community may be the installed package or a
# source checkout, enterprise is one directory per series, and the series you
# care about is the TARGET, which is often not the series this container runs.
# Resolving that by hand is where "I grepped and found nothing" quietly becomes
# "I grepped the wrong version".
#
# --series picks the enterprise/community trees for that series when they are
# checked out; without it, the running series ($ODOO_VERSION / odoo --version).
#
# Last stdout line: {"series","odoo_version","addons_paths":[],"community_path",
#   "enterprise_path","custom_paths":[],"missing":[]}
# "missing" names what was asked for and is not on this machine — an empty
# community_path or enterprise_path is a reason to stop, not to grep harder.
set -euo pipefail

series=""
while [ $# -gt 0 ]; do
  case "$1" in
    --series) series="${2:?}"; shift 2 ;;
    *) echo "usage: addons_paths.sh [--series 19.0]" >&2; exit 2 ;;
  esac
done

ODOO_CONF="${ODOO_RC:-/etc/odoo/odoo.conf}"
ENTERPRISE_ROOT="${ODOO_ENTERPRISE_ROOT:-/var/lib/odoo/addons}"
SRC_ROOT="${ODOO_SRC_ROOT:-/var/lib/odoo/src}"

running="${ODOO_VERSION:-}"
if [ -z "$running" ] && command -v odoo >/dev/null 2>&1; then
  running="$(odoo --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1)"
fi
series="${series:-$running}"

conf_addons=""
[ -f "$ODOO_CONF" ] && conf_addons="$(grep -E '^\s*addons_path' "$ODOO_CONF" | head -1 | sed 's/^[^=]*=\s*//' || true)"

# Community: the installed package is authoritative for the RUNNING series only.
# For any other series it must come from a source checkout, and if there is none
# the honest answer is "not on this machine".
community=""
if [ -n "$series" ] && [ "$series" = "$running" ]; then
  community="$(python3 -c 'import odoo, os; print(os.path.join(os.path.dirname(odoo.__file__), "addons"))' 2>/dev/null || true)"
fi
if [ -z "$community" ] || [ ! -d "$community" ]; then
  community=""
  major="${series%%.*}"
  for cand in "$SRC_ROOT/odoo$major/odoo/addons" "$SRC_ROOT/odoo${series}/odoo/addons"; do
    [ -d "$cand" ] && { community="$cand"; break; }
  done
  if [ -z "$community" ] && [ -n "$major" ]; then
    cand="$(ls -d "$SRC_ROOT"/odoo-"$series".* 2>/dev/null | sort -r | head -1 || true)"
    [ -n "$cand" ] && [ -d "$cand/odoo/addons" ] && community="$cand/odoo/addons"
  fi
fi

enterprise=""
[ -n "$series" ] && [ -d "$ENTERPRISE_ROOT/$series" ] && enterprise="$ENTERPRISE_ROOT/$series"

node -e '
  const { existsSync } = require("fs");
  const [series, running, confAddons, community, enterprise, entRoot] = process.argv.slice(1);
  const custom = confAddons.split(",").map((p) => p.trim()).filter((p) => p && existsSync(p));
  const missing = [];
  if (!community) missing.push(`community tree for ${series || "?"}`);
  if (!enterprise) missing.push(`enterprise tree for ${series || "?"} (looked in ${entRoot})`);
  if (!series) missing.push("series (pass --series, or run where $ODOO_VERSION is set)");
  const paths = [community, enterprise, ...custom].filter(Boolean);
  console.log(JSON.stringify({
    series: series || null,
    odoo_version: running || null,
    addons_paths: paths,
    community_path: community || null,
    enterprise_path: enterprise || null,
    custom_paths: custom,
    missing,
  }));
' "$series" "$running" "$conf_addons" "$community" "$enterprise" "$ENTERPRISE_ROOT"
