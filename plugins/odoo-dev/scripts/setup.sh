#!/usr/bin/env bash
# setup.sh — install and authenticate the dependencies. The counterpart to
# preflight.sh: that one answers "is this ready?", this one answers "make it
# ready".
#
# Usage: setup.sh [--check] [--yes]
#
# Why it exists: a fresh machine or a rebuilt container used to fail late and
# cryptically. coderabbit-local.sh exits 3 with "coderabbit CLI is not
# installed", tours silently skip because no browser is present, and repo-map.sh
# reads a state dir nobody ever created. Every one of those is knowable up front.
#
# It NEVER performs an interactive login itself. Device-flow and browser auth
# cannot be driven from a script without hanging, so this script detects what is
# missing, does what it can safely do unattended, and prints the exact command
# for the user to run for anything interactive. Every external command runs under
# `timeout` for the same reason: nothing here may wait on input or on a stalled
# network.
#
#   --check  report only, and exit non-zero if anything required is missing. This
#            is the form CI and the post-rebuild checklist use. It creates no
#            state dir, installs nothing, and writes nothing of its own.
#
#            It is NOT a no-op on the filesystem, and saying otherwise would be a
#            lie you would find out about in CI. It runs status reads — `gh auth
#            status`, `coderabbit auth status` — because that is the only way to
#            answer the question, and those CLIs write their own machine-id,
#            device-id and log files under HOME when asked. Measured on a fresh
#            HOME: 8 files, none of them ours. Nothing under ODOO_DEV_STATE_DIR
#            and nothing in the plugin tree is touched.
#   --yes    also take the one heavy unattended step: fetch the headless browser
#            (~100 MB, network). Without it the browser is left as a manual step,
#            because a setup run should not quietly download 100 MB.
#
# Host versus devcontainer is not decided here. preflight.sh already owns that
# decision, so this script runs it and reads `context` out of its JSON: one
# definition, not two. Host-only concerns (docker, the devcontainer CLI) are
# checked only when that answer is "host" — four confident failures inside a
# perfectly good container help nobody. Its JSON is printed on the way past, so
# setup and readiness are one pass.
#
# odoo-mcp is deliberately NOT configured here. It is global, this plugin ships
# no credentials and declares no MCP server, and MCP is not callable from bash
# anyway. The check reads the global config to see whether it is declared, and
# says plainly that reachability is a question for the session.
#
# The JSON below is built with printf rather than node, because a script whose
# job includes reporting that node is missing must not need node to say so.
#
# Last stdout line: {"ok","context","installed":[],"missing":[],"manual_steps":[]}
# Exit codes: 0 ready | 1 something required is missing or needs a manual step
#             | 2 usage
set -uo pipefail

CHECK=0; YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1; shift ;;
    --yes|-y) YES=1; shift ;;
    *) echo "usage: setup.sh [--check] [--yes]" >&2; exit 2 ;;
  esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$HERE/.." && pwd)"
PREFLIGHT="$PLUGIN_ROOT/skills/odoo-task-env/scripts/preflight.sh"
BROWSER_ENSURE="$PLUGIN_ROOT/skills/odoo-test-run/scripts/browser-ensure.sh"
BOOTSTRAP="$HERE/bootstrap-state.sh"
STATE_DIR="${ODOO_DEV_STATE_DIR:-$HOME/.local/share/odoo-dev}"
BOUND_S="${SETUP_TIMEOUT_S:-20}"
PREFLIGHT_S="${SETUP_PREFLIGHT_TIMEOUT_S:-90}"
BROWSER_INSTALL_S="${SETUP_BROWSER_TIMEOUT_S:-600}"

installed=(); missing=(); manual=()

say()  { echo "$*" >&2; }
pass() { echo "PASS $1${2:+ — $2}" >&2; }
note() { echo "NOTE $*" >&2; }

# fail <name> <why> [manual step ...]
fail() {
  local name="$1" why="$2"; shift 2
  echo "FAIL $name — $why" >&2
  missing+=("$name")
  while [ $# -gt 0 ]; do manual+=("$1"); shift; done
}

have() { command -v "$1" >/dev/null 2>&1; }

# bounded <seconds> <cmd...> — nothing this script runs may wait on input or on a
# stalled network. Where coreutils timeout is absent there is nothing to bound
# with, so the command runs bare rather than not at all.
bounded() {
  local s="$1"; shift
  if have timeout; then timeout "$s" "$@"; else "$@"; fi
}

# --- node and git: hard prerequisites -------------------------------------------
# Every script in this plugin shells out to node to build its JSON, and every
# delivery skill runs git. Nothing downstream degrades gracefully without them.
if have node; then pass node "$(command -v node)"; else
  fail node "not on PATH" \
    "install Node.js 18 or newer — https://nodejs.org/en/download (every script in this plugin builds its JSON with node)"
fi
if have git; then pass git "$(command -v git)"; else
  fail git "not on PATH" "install git — https://git-scm.com/downloads"
fi

# --- gh, and the two ways it can be present but useless -------------------------
if ! have gh; then
  fail gh "not on PATH" "install the GitHub CLI — https://cli.github.com then run: gh auth login"
else
  pass gh "$(command -v gh)"
  gh_status="$(bounded "$BOUND_S" gh auth status 2>&1)"; gh_rc=$?
  if [ "$gh_rc" -ne 0 ]; then
    fail gh-auth "not logged in (gh auth status exit $gh_rc)" "gh auth login"
  else
    pass gh-auth "logged in"
    # A token without `repo` cannot read a private client repo and a token
    # without `workflow` cannot push a branch that touches .github/workflows.
    # Both surface much later as a confusing 403 on a push. The scope names are
    # matched across the whole of `gh auth status`, not per account: the failure
    # this catches is a token minted without them at all.
    want=""
    case "$gh_status" in *"'repo'"*) ;; *) want="repo" ;; esac
    case "$gh_status" in *"'workflow'"*) ;; *) want="${want:+$want }workflow" ;; esac
    if [ -n "$want" ]; then
      fail gh-scopes "token scopes do not cover: $want" \
        "gh auth refresh -h github.com -s repo -s workflow"
    else
      pass gh-scopes "repo, workflow"
    fi
  fi
fi

# --- coderabbit ------------------------------------------------------------------
# A review that cannot authenticate is coderabbit-local.sh exit 3, not a clean
# review, and the gate blocks on review_incomplete. Both halves are checked.
CR_INSTALL="install the CodeRabbit CLI — https://docs.coderabbit.ai/cli (curl -fsSL https://cli.coderabbit.ai/install.sh | sh)"
if ! have coderabbit; then
  fail coderabbit "not on PATH" "$CR_INSTALL" "coderabbit auth login"
else
  pass coderabbit "$(command -v coderabbit)"
  if bounded "$BOUND_S" coderabbit auth status >/dev/null 2>&1; then
    pass coderabbit-auth "authenticated"
  else
    fail coderabbit-auth "not authenticated — a review that cannot authenticate is exit 3, not a clean review" \
      "coderabbit auth login"
  fi
fi

# --- python3 ---------------------------------------------------------------------
if have python3; then
  pass python3 "$(command -v python3)"
else
  fail python3 "not on PATH" \
    "install python3 — odoo-prior-art/scripts/oca_check.py and odoo-upgrade/scripts/module_inventory.py need it"
fi

# --- headless browser -------------------------------------------------------------
# Odoo does not fail when the browser is missing: it raises SkipTest, logs at
# INFO, and reports the suite green. browser-ensure.sh --check answers the whole
# question in one sentence and installs nothing.
BROWSER_STEP="$BROWSER_ENSURE --install   # ~100 MB, network"
browser_state="unknown"
if [ ! -x "$BROWSER_ENSURE" ] && [ ! -f "$BROWSER_ENSURE" ]; then
  fail browser "browser-ensure.sh not found at $BROWSER_ENSURE" "$BROWSER_STEP"
else
  browser_line="$(bounded "$BOUND_S" bash "$BROWSER_ENSURE" --check 2>/dev/null)"
  case "$browser_line" in
    "browser ready"*) browser_state="ready" ;;
    *websocket-client*) browser_state="no-websocket" ;;
    *) browser_state="absent" ;;
  esac

  # The one heavy step worth taking unattended, and only when asked twice: not
  # --check, and --yes.
  if [ "$browser_state" != "ready" ] && [ "$CHECK" -eq 0 ] && [ "$YES" -eq 1 ]; then
    say "installing the headless browser (~100 MB, network)"
    bounded "$BROWSER_INSTALL_S" bash "$BROWSER_ENSURE" --install >/dev/null 2>&1
    browser_line="$(bounded "$BOUND_S" bash "$BROWSER_ENSURE" --check 2>/dev/null)"
    case "$browser_line" in
      "browser ready"*) browser_state="ready"; installed+=("browser") ;;
      *websocket-client*) browser_state="no-websocket" ;;
      *) browser_state="absent" ;;
    esac
  fi

  case "$browser_state" in
    ready) pass browser "${browser_line#browser ready: }" ;;
    no-websocket)
      fail browser "$browser_line" "python3 -m pip install websocket-client" ;;
    *)
      fail browser "no headless browser — tours SKIP, the suite still reports green, and the gate blocks on tours_skipped" \
        "$BROWSER_STEP" ;;
  esac
fi

# --- odoo-mcp ---------------------------------------------------------------------
# Global, and not this plugin's to configure. Read-only: this script never writes
# an MCP config. Whether the server actually answers is a session question, since
# MCP is not callable from bash — which is also why preflight.sh omits it.
MCP_STEP="configure odoo-mcp in your GLOBAL MCP config (this plugin ships no credentials and declares no server), then confirm from a session: claude mcp list"
mcp_config=""
for f in "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.claude.json" "$HOME/.claude.json" \
         "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json" "$HOME/.claude/settings.json"; do
  if [ -f "$f" ] && grep -q '"odoo-mcp"' "$f" 2>/dev/null; then mcp_config="$f"; break; fi
done
if [ -n "$mcp_config" ]; then
  pass odoo-mcp "declared in $mcp_config"
  note "odoo-mcp reachability is not testable from bash — confirm with 'claude mcp list' in a session"
else
  fail odoo-mcp "not declared in any global MCP config this script can read" "$MCP_STEP"
fi

# --- state dir ---------------------------------------------------------------------
# Lives outside the plugin tree, so a fresh machine or a skipped bootstrap leaves
# it missing and repo-map.sh reports a bogus "project unmapped" from a file that
# was never there. bootstrap-state.sh is idempotent; it is called, never reimplemented.
if [ -f "$STATE_DIR/repo-map.json" ]; then
  pass state-dir "$STATE_DIR"
elif [ "$CHECK" -eq 1 ]; then
  fail state-dir "$STATE_DIR/repo-map.json is absent" "$BOOTSTRAP"
elif ! have node; then
  fail state-dir "$STATE_DIR/repo-map.json is absent, and bootstrap-state.sh needs node" "$BOOTSTRAP"
else
  boot_out="$(bounded "$BOUND_S" bash "$BOOTSTRAP" 2>/dev/null | tail -1)"
  if [ -f "$STATE_DIR/repo-map.json" ]; then
    pass state-dir "$STATE_DIR"
    case "$boot_out" in *'"created":[]'*) ;; *) installed+=("state-dir") ;; esac
  else
    fail state-dir "bootstrap-state.sh did not produce $STATE_DIR/repo-map.json" "$BOOTSTRAP"
  fi
fi

# --- preflight: the context answer, and the readiness pass ---------------------------
# Run last so it sees the state dir this script may just have created, and read
# for `context` so host versus devcontainer has exactly one definition. It needs
# node for its JSON, so a machine without node gets an honest "unknown" rather
# than a guess.
context="unknown"
preflight_json=""
if ! have node; then
  note "skipping preflight.sh: it builds its JSON with node"
  manual+=("$PREFLIGHT   # once node is installed")
elif [ ! -f "$PREFLIGHT" ]; then
  note "skipping preflight.sh: not found at $PREFLIGHT"
else
  preflight_json="$(bounded "$PREFLIGHT_S" bash "$PREFLIGHT" --soft 2>/dev/null | tail -1)"
  case "$preflight_json" in
    *'"context":"host"'*)         context="host" ;;
    *'"context":"devcontainer"'*) context="devcontainer" ;;
    *) note "preflight.sh produced no JSON within ${PREFLIGHT_S}s" ;;
  esac
fi

# --- host-only concerns ---------------------------------------------------------------
# Gated on preflight's answer. Inside a container docker and the devcontainer CLI
# are neither present nor needed, and reporting them missing would be noise.
if [ "$context" = host ]; then
  if bounded "$BOUND_S" docker info >/dev/null 2>&1; then
    pass docker "daemon reachable"
  else
    fail docker "the docker daemon is not reachable" \
      "start docker, or install it — https://docs.docker.com/engine/install/"
  fi
  if bounded "$BOUND_S" devcontainer --version >/dev/null 2>&1; then
    pass devcontainer-cli "$(bounded "$BOUND_S" devcontainer --version 2>/dev/null)"
  else
    fail devcontainer-cli "not on PATH" "npm install -g @devcontainers/cli"
  fi
else
  note "docker, the devcontainer CLI and the repos tree are host concerns — not checked in context '$context'"
fi

# --- preflight's verdict, minus what is already named above -----------------------
# One pass, one answer: a preflight failure this script cannot see (the repos
# tree, postgres, stray feature-managed skills) still has to make setup say "not
# ready". Names it already reported are subtracted first, so nothing is reported
# twice. Done after the host block so its findings are subtracted too.
if [ -n "$preflight_json" ]; then
  case "$preflight_json" in
    *'"failures":['*)
      rest="${preflight_json#*\"failures\":[}"
      rest="${rest%%]*}"
      ;;
    *) rest="" ;;
  esac
  for name in "${missing[@]+"${missing[@]}"}"; do rest="${rest//\"$name\"/}"; done
  case "$rest" in
    *'"'*)
      rest="$(printf '%s' "$rest" | tr -d '"' | tr ',' ' ' | sed 's/  */ /g; s/^ //; s/ $//')"
      fail preflight "preflight.sh also reports: $rest" \
        "$PREFLIGHT   # fix what its failures[] names" ;;
    *) pass preflight "nothing beyond what is named above" ;;
  esac
fi

# --- report ------------------------------------------------------------------------
[ -n "$preflight_json" ] && printf '%s\n' "$preflight_json"

if [ "${#installed[@]}" -gt 0 ]; then
  say ""
  say "done for you: ${installed[*]}"
fi

if [ "${#manual[@]}" -gt 0 ]; then
  say ""
  say "left for you to run — none of these can be driven from a script:"
  n=1
  for step in "${manual[@]}"; do
    say "  $n. $step"
    n=$((n + 1))
  done
fi

json_escape() {
  local s="${1//\\/\\\\}"
  s="${s//\"/\\\"}"
  printf '%s' "$s"
}

json_array() {
  local out="" item
  for item in "$@"; do out="$out,\"$(json_escape "$item")\""; done
  printf '[%s]' "${out#,}"
}

ok=true
[ "${#missing[@]}" -gt 0 ] && ok=false

printf '{"ok":%s,"context":"%s","installed":%s,"missing":%s,"manual_steps":%s}\n' \
  "$ok" "$(json_escape "$context")" \
  "$(json_array "${installed[@]+"${installed[@]}"}")" \
  "$(json_array "${missing[@]+"${missing[@]}"}")" \
  "$(json_array "${manual[@]+"${manual[@]}"}")"

[ "$ok" = true ] || exit 1
exit 0
