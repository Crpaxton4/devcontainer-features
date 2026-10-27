#!/usr/bin/env bash
# json-get.sh — the one JSON reader in the plugin's shell scripts.
#
# Usage: json-get.sh [-l] <expression> [name=value ...] < json
#
# The JSON on stdin is bound to `d`. Each name=value operand is bound as a
# string in `a` (a.name). The expression is JavaScript and its value is printed:
# a string raw, null or undefined as nothing, anything else as compact JSON.
# With -l an array prints one element per line instead.
#
# Example: odoo-dev repo-map get X --json | json-get.sh 'd.odoo_version'
#
# A thrown error prints its message on stderr and exits 1.
set -euo pipefail

lines=0
if [ "${1:-}" = "-l" ]; then lines=1; shift; fi
[ $# -ge 1 ] || { echo "usage: json-get.sh [-l] <expression> [name=value ...]" >&2; exit 2; }

node -e '
  const [lines, expr, ...pairs] = process.argv.slice(1);
  const a = Object.fromEntries(pairs.map((p) => [p.slice(0, p.indexOf("=")), p.slice(p.indexOf("=") + 1)]));
  const show = (v) => (typeof v === "string" ? v : JSON.stringify(v));
  try {
    const d = JSON.parse(require("fs").readFileSync(0, "utf8"));
    const v = new Function("d", "a", "return (" + expr + ");")(d, a);
    if (v === null || v === undefined) process.exit(0);
    if (lines === "1" && Array.isArray(v)) v.forEach((x) => console.log(show(x)));
    else console.log(show(v));
  } catch (e) {
    console.error(e.message);
    process.exit(1);
  }
' "$lines" "$@"
