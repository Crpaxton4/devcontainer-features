#!/usr/bin/env bash
# preflight.sh — one-shot environment gate for the delivery suite.
#
# Usage: preflight.sh [--soft] [--target-series V] [--custom-tree DIR]
#   --soft:          report failures but exit 0.
#   --target-series: the Odoo series the work targets, compared against
#                    ODOO_VERSION. Also read from $TARGET_SERIES. Unset means
#                    there is nothing to compare, and the check passes.
#   --custom-tree:   in a devcontainer, import every enterprise module in the
#                    dependency closure of the modules under DIR (plus enterprise
#                    auto_install modules whose depends the closure meets). A
#                    failed import is "import <name>: <error>"; a name found in no
#                    tree is "closure: unknown module <name>".
#
# Runs ALL checks rather than stopping at the first, because the useful answer is
# "these three things are wrong", not "the first thing is wrong" three times in a
# row. PASS/FAIL per check on stderr, one JSON object as the last stdout line.
#
# Context-aware, because the requirements genuinely differ. Detected CONTAINER
# FIRST: odoo on PATH and pg_isready answering is a devcontainer, even when docker
# is on PATH too (docker-from-docker mounts the host socket, #1053).
#   devcontainer  odoo and a reachable postgres; docker and the repos tree are
#                 neither present nor needed. Also the answer when NEITHER odoo
#                 plus postgres NOR docker is available: the odoo and postgres
#                 checks then FAIL by name.
#   host          otherwise, when docker is available: docker, a populated repos
#                 tree, gh, and the devcontainer CLI — this is where worktrees
#                 and stacks are managed from
# The lifted original only knew the host case and reported four confident
# failures inside a perfectly good container.
#
# The odoo-mcp reachability check is deliberately absent: MCP is not callable
# from bash. Check it from the session.
#
# Last stdout line: {"ok","context","failures":[],"warnings":[],
#                    "ram_available_gb","repos_dir"}
# Exit codes: 0 ok (or --soft) | 1 at least one check failed
set -uo pipefail

SOFT=0
TARGET_SERIES="${TARGET_SERIES:-}"
CUSTOM_TREE=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --soft) SOFT=1 ;;
    --target-series) TARGET_SERIES="${2:-}"; shift ;;
    --target-series=*) TARGET_SERIES="${1#*=}" ;;
    --custom-tree) CUSTOM_TREE="${2:-}"; shift ;;
    --custom-tree=*) CUSTOM_TREE="${1#*=}" ;;
    *) ;;  # unknown arguments are ignored, as they always were
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/_common.sh"

MIN_REPOS="${PREFLIGHT_MIN_REPOS:-5}"
MIN_RAM_GB="${PREFLIGHT_MIN_RAM_GB:-8}"

failures=(); warnings=()

check() {
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "PASS $name" >&2
  else
    echo "FAIL $name" >&2
    failures+=("$name")
  fi
}

# Container first, as stack-ensure.sh and run-tests.sh do (#1053): inside the
# devcontainer docker is on PATH too (host socket), so it cannot decide. When
# neither is true the answer is still "devcontainer": its odoo/postgres checks
# then FAIL by name, which says what is missing; "host" would say nothing true.
if command -v odoo >/dev/null 2>&1 && pg_isready >/dev/null 2>&1; then
  context="devcontainer"
elif command -v docker >/dev/null 2>&1; then
  context="host"
else
  context="devcontainer"
fi

repos_dir=""
if [ "$context" = host ]; then
  # An unresolvable tree becomes a FAIL on the repos check, never a crash here.
  repos_dir="${REPOS_DIR:-$("$REPO_MAP_SCRIPTS/repos-dir.sh" --raw 2>/dev/null || true)}"
  repos_populated() {
    [ -n "$repos_dir" ] && [ -d "$repos_dir" ] \
      && [ "$(ls "$repos_dir" 2>/dev/null | wc -l)" -ge "$MIN_REPOS" ]
  }
  check docker docker info --format '{{.ServerVersion}}'
  check repos repos_populated
  check devcontainer-cli devcontainer --version
else
  check odoo odoo --version
  check postgres pg_isready

  # An upgrade run in a container of the wrong series ports code this container
  # cannot install, let alone test, and the result is an unverifiable commit
  # (#876). Compared here rather than trusted from prose. No target named means
  # nothing to compare: the check is absent rather than failed, because every
  # non-upgrade caller passes none. The failure carries BOTH values, since
  # "series mismatch" alone does not say which way to rebuild.
  if [ -n "$TARGET_SERIES" ]; then
    case "${ODOO_VERSION:-}" in
      "$TARGET_SERIES"*)
        echo "PASS series (ODOO_VERSION=${ODOO_VERSION:-unset})" >&2 ;;
      *)
        echo "FAIL series: ODOO_VERSION=${ODOO_VERSION:-unset}, target $TARGET_SERIES" >&2
        failures+=("series: ODOO_VERSION=${ODOO_VERSION:-unset}, target $TARGET_SERIES") ;;
    esac
  fi

  # The enterprise closure of a custom tree must import. A broken enterprise
  # tree otherwise shows up as a traceback in the middle of a port, and the
  # agent patches around it (#1130). Unknown modules fail by name.
  if [ -n "$CUSTOM_TREE" ]; then
    ent_root="${ODOO_ENTERPRISE_ROOT:-/var/lib/odoo/addons}/${TARGET_SERIES:-${ODOO_VERSION:-}}"
    odoo_file="$(python3 -c 'import odoo; print(odoo.__file__)' 2>/dev/null | tail -1)"
    if [ -n "$odoo_file" ]; then
      comm_root="$(dirname "$odoo_file")/addons"
    else
      comm_root=/usr/lib/python3/dist-packages/odoo/addons
    fi
    closure="$(python3 - "$CUSTOM_TREE" "$ent_root" "$comm_root" <<'PY'
import ast, os, sys

custom, ent, comm = sys.argv[1:4]


def manifest(root, name):
    path = os.path.join(root, name, "__manifest__.py")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = ast.literal_eval(fh.read())
    except (OSError, ValueError, SyntaxError):
        return {}
    return data if isinstance(data, dict) else {}


enterprise, unknown, seen = set(), set(), set()


def visit(name):
    if name in seen:
        return
    seen.add(name)
    for root, bucket in ((custom, None), (ent, enterprise), (comm, None)):
        data = manifest(root, name)
        if data is None:
            continue
        if bucket is not None:
            bucket.add(name)
        for dep in data.get("depends", []) or []:
            visit(dep)
        return
    unknown.add(name)


for entry in sorted(os.listdir(custom)) if os.path.isdir(custom) else []:
    if manifest(custom, entry) is not None:
        visit(entry)

# Enterprise glue modules that install themselves once their depends are present.
done = False
while not done:
    done = True
    have = seen
    for entry in sorted(os.listdir(ent)) if os.path.isdir(ent) else []:
        data = manifest(ent, entry)
        if data and data.get("auto_install") and entry not in seen:
            deps = data.get("depends", []) or []
            if deps and all(d in have for d in deps):
                visit(entry)
                done = False

for name in sorted(enterprise):
    print("E " + name)
for name in sorted(unknown):
    print("U " + name)
PY
)"
    while read -r kind name; do
      case "$kind" in
        E)
          if err="$(python3 -c "import odoo.addons.$name" 2>&1 >/dev/null)"; then
            echo "PASS import $name" >&2
          else
            err="$(printf '%s\n' "$err" | tail -1)"
            echo "FAIL import $name: $err" >&2
            failures+=("import $name: $err")
          fi ;;
        U)
          echo "FAIL closure: unknown module $name" >&2
          failures+=("closure: unknown module $name") ;;
      esac
    done <<< "$closure"
  fi
fi

# Needed in both contexts: every skill downstream talks to GitHub.
check gh-auth gh auth status

# The state dir lives outside the plugin tree and the suite creates it, never this
# script. A missing dir is named here, not discovered later. The repo map inside
# it is created on the first `odoo-dev repo-map set`, so its absence is no failure.
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$SCRIPT_DIR/../../.." && pwd)}"
STATE_DIR="$(bash "$PLUGIN_ROOT/scripts/state-dir.sh")"
state_dir_ready() { [ -d "$STATE_DIR" ]; }
check state-dir state_dir_ready

# The repo-map scripts and the suite call the odoo-dev CLI.
odoo_dev_cli_ready() {
  local version
  version="$(odoo-dev --version)" || return 1
  version="${version#odoo-dev }"
  # requires.json: odoo-dev >=9
  [ "${version%%.*}" -ge 9 ] 2>/dev/null
}
check odoo-dev-cli odoo_dev_cli_ready

# Leftover pre-migration loose copies of plugin skills shadow their plugin
# twins. Reports only; the strays are safe to delete — the devcontainer feature
# stopped shipping them (#701–#708), so nothing recreates them on rebuild.
stray_skills_clean() {
  [ -z "$(bash "$PLUGIN_ROOT/scripts/check-stray-skills.sh" 2>/dev/null)" ]
}
check stray-skills stray_skills_clean

ram_gb="$(free -g 2>/dev/null | awk '/^Mem:/{print $7}')"
ram_gb="${ram_gb:-0}"
if [ "$context" = host ] && [ "$ram_gb" -lt "$MIN_RAM_GB" ]; then
  # A warning, not a failure: stack-ensure.sh evicts under pressure rather than
  # refusing, so low RAM makes things slow rather than impossible.
  warnings+=("ram: ${ram_gb} GB available, advise >= ${MIN_RAM_GB}")
  echo "WARN ${warnings[0]}" >&2
fi

ok=true
[ "${#failures[@]}" -gt 0 ] && ok=false

as_json_array() {
  [ "$#" -eq 0 ] && { echo '[]'; return; }
  printf '%s\n' "$@" | node -e '
    const lines = require("fs").readFileSync(0, "utf8").trim().split("\n");
    console.log(JSON.stringify(lines));'
}

node -e '
  const [ok, context, failures, warnings, ram, repos] = process.argv.slice(1);
  console.log(JSON.stringify({
    ok: ok === "true", context,
    failures: JSON.parse(failures), warnings: JSON.parse(warnings),
    ram_available_gb: Number(ram), repos_dir: repos || null,
  }));
' "$ok" "$context" "$(as_json_array "${failures[@]+"${failures[@]}"}")" \
  "$(as_json_array "${warnings[@]+"${warnings[@]}"}")" "$ram_gb" "$repos_dir"

if [ "$ok" = false ] && [ "$SOFT" -eq 0 ]; then
  exit 1
fi
exit 0
