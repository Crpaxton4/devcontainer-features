#!/usr/bin/env bash
# setup.test.sh — one case per way a machine can be unready, each asserting the
# name setup.sh reports and the command it hands back.
#
# Offline: no network, no docker, no Odoo, no repos tree, and nothing is ever
# installed. Missing tools are simulated by running setup.sh with a PATH that
# contains only a curated bin dir of symlinks, so "coderabbit is absent" is
# literally true for that run rather than mocked. gh and coderabbit are small
# stub scripts, which is also how "logged out" is tested without touching a real
# credential store.
#
# Every case asserts the exact missing[] name rather than only the exit code,
# because "exits 1" would stay green if setup.sh started failing for the wrong
# reason. Every case also re-asserts the JSON shape: the last stdout line is the
# machine-readable half of this script's contract.
#
# Nothing here writes outside its own mktemp dir: HOME, ODOO_DEV_STATE_DIR and
# CLAUDE_CONFIG_DIR are all redirected into it.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETUP="$HERE/../setup.sh"

pass=0; fail=0
ok()  { echo "PASS $*"; pass=$((pass + 1)); }
bad() { echo "FAIL $*"; fail=$((fail + 1)); }

TMPDIRS=()
newtmp() { local d; d="$(mktemp -d)"; TMPDIRS+=("$d"); echo "$d"; }

# The coreutils every script in the chain reaches for. Named explicitly so that
# what a case leaves OUT (node, git, gh, coderabbit, python3) is the only
# variable in it.
BASE_TOOLS="bash sh sed tr grep cat ls sort head tail wc awk cut mkdir cp rm ln
            dirname basename mktemp timeout env date uname free"

# mkbin <dir> [extra tool names...] — a PATH containing exactly these
mkbin() {
  local dir="$1"; shift
  mkdir -p "$dir"
  local t p
  for t in $BASE_TOOLS "$@"; do
    p="$(command -v "$t" 2>/dev/null)"
    case "$p" in /*) ln -sf "$p" "$dir/$t" ;; esac
  done
}

# gh that answers "logged in, with both scopes" without a network round trip
stub_gh_authed() {
  cat > "$1/gh" <<'EOS'
#!/bin/sh
if [ "$1" = auth ] && [ "$2" = status ]; then
  echo "  * Logged in to github.com account test"
  echo "  - Token scopes: 'gist', 'read:org', 'repo', 'workflow'"
  exit 0
fi
exit 0
EOS
  chmod +x "$1/gh"
}

# gh that answers "logged out" and RETURNS — the real failure mode being guarded
# against is a login prompt that waits forever, so this stub must never read stdin
stub_gh_loggedout() {
  cat > "$1/gh" <<'EOS'
#!/bin/sh
if [ "$1" = auth ] && [ "$2" = status ]; then
  echo "You are not logged into any GitHub hosts. To log in, run: gh auth login" >&2
  exit 1
fi
exit 1
EOS
  chmod +x "$1/gh"
}

stub_coderabbit_authed() {
  cat > "$1/coderabbit" <<'EOS'
#!/bin/sh
[ "$1" = auth ] && [ "$2" = status ] && exit 0
exit 0
EOS
  chmod +x "$1/coderabbit"
}

# A config declaring odoo-mcp, so the MCP check is not noise in cases about
# something else. Read-only to setup.sh; written here, never by it.
stub_mcp_config() {
  mkdir -p "$1"
  echo '{"mcpServers":{"odoo-mcp":{"command":"odoo-mcp"}}}' > "$1/.claude.json"
}

# jarr <json> <key> — array items, one per line
jarr() {
  printf '%s' "$1" | node -e '
    const o = JSON.parse(require("fs").readFileSync(0, "utf8"));
    const v = o[process.argv[1]];
    if (!Array.isArray(v)) process.exit(1);
    for (const i of v) console.log(i);' "$2"
}

# assert_shape <label> <json>
assert_shape() {
  if printf '%s' "$2" | node -e '
      const o = JSON.parse(require("fs").readFileSync(0, "utf8"));
      const want = ["ok", "context", "installed", "missing", "manual_steps"];
      const absent = want.filter((k) => !(k in o));
      if (absent.length) { console.error("absent keys: " + absent.join(",")); process.exit(1); }
      if (typeof o.ok !== "boolean") { console.error("ok is not a boolean"); process.exit(1); }
      if (typeof o.context !== "string") { console.error("context is not a string"); process.exit(1); }
      for (const k of ["installed", "missing", "manual_steps"])
        if (!Array.isArray(o[k])) { console.error(k + " is not an array"); process.exit(1); }
    ' 2>/dev/null; then
    ok "$1: last stdout line is JSON with all five keys"
  else
    bad "$1: last stdout line is not the five-key JSON object: $2"
  fi
}

# has <label> <list> <needle> — an exact array entry
has() {
  if printf '%s\n' "$2" | grep -qxF "$3"; then ok "$1"; else bad "$1 (got: $(printf '%s' "$2" | tr '\n' '|'))"; fi
}

# has_sub <label> <list> <substring>
has_sub() {
  if printf '%s\n' "$2" | grep -qF -- "$3"; then ok "$1"; else bad "$1 (got: $(printf '%s' "$2" | tr '\n' '|'))"; fi
}

# hasnt <label> <list> <needle>
hasnt() {
  if printf '%s\n' "$2" | grep -qxF "$3"; then bad "$1 — reported $3"; else ok "$1"; fi
}

# --- 1. a fresh machine: coderabbit and the browser absent ------------------------
T="$(newtmp)"; BIN="$T/bin"
mkbin "$BIN" node git python3
stub_gh_authed "$BIN"
stub_mcp_config "$T/cfg"
out="$(env PATH="$BIN" HOME="$T/home" ODOO_DEV_STATE_DIR="$T/state" \
       CLAUDE_CONFIG_DIR="$T/cfg" timeout 60 bash "$SETUP" --check 2>/dev/null)"; rc=$?
json="$(printf '%s' "$out" | tail -1)"
[ "$rc" -ne 0 ] && ok "fresh machine: --check exits non-zero ($rc)" \
                || bad "fresh machine: --check exited 0"
assert_shape "fresh machine" "$json"
miss="$(jarr "$json" missing)"; steps="$(jarr "$json" manual_steps)"
has     "fresh machine: coderabbit named in missing[]" "$miss" coderabbit
has     "fresh machine: browser named in missing[]"    "$miss" browser
has_sub "fresh machine: manual_steps points at the CodeRabbit CLI docs" "$steps" "https://docs.coderabbit.ai/cli"
has     "fresh machine: manual_steps carries the login command"         "$steps" "coderabbit auth login"
has_sub "fresh machine: manual_steps carries browser-ensure.sh --install" \
        "$steps" "skills/odoo-test-run/scripts/browser-ensure.sh --install"

# --- 2. --check writes nothing OF ITS OWN -------------------------------------------
# Pointed at a HOME and a state dir that do not exist. Both must still not exist.
#
# Note what this does and does not prove. gh and coderabbit are stubbed here, so
# this asserts that setup.sh itself writes nothing. With the real CLIs, asking
# `gh auth status` and `coderabbit auth status` makes THEM write a machine-id, a
# device-id and a log under HOME. That is unavoidable — it is the only way to
# answer the auth question — and the header comment says so rather than claiming
# a no-op the vendor tools do not honour.
T="$(newtmp)"; BIN="$T/bin"
mkbin "$BIN" node git python3
stub_gh_authed "$BIN"; stub_coderabbit_authed "$BIN"
env PATH="$BIN" HOME="$T/home" ODOO_DEV_STATE_DIR="$T/state" \
    CLAUDE_CONFIG_DIR="$T/cfg" timeout 60 bash "$SETUP" --check >/dev/null 2>&1
if [ ! -e "$T/state" ] && [ ! -e "$T/home" ]; then
  ok "--check creates neither the state dir nor HOME"
else
  bad "--check wrote: $(ls -d "$T/state" "$T/home" 2>/dev/null | tr '\n' ' ')"
fi

# --- 3. idempotence: run it twice --------------------------------------------------
T="$(newtmp)"; BIN="$T/bin"
mkbin "$BIN" node git python3
stub_gh_authed "$BIN"; stub_coderabbit_authed "$BIN"; stub_mcp_config "$T/cfg"
run_setup() {
  env PATH="$BIN" HOME="$T/home" ODOO_DEV_STATE_DIR="$T/state" \
      CLAUDE_CONFIG_DIR="$T/cfg" timeout 60 bash "$SETUP" 2>/dev/null | tail -1
}
one="$(run_setup)"; two="$(run_setup)"
assert_shape "first run" "$one"
assert_shape "second run" "$two"
has   "first run: state-dir reported in installed[]" "$(jarr "$one" installed)" state-dir
if [ -f "$T/state/repo-map.json" ]; then
  ok "first run: bootstrap-state.sh seeded $T/state"
else
  bad "first run: no repo-map.json under $T/state"
fi
if [ -z "$(jarr "$two" installed)" ]; then
  ok "second run: installed[] is empty — everything was already present"
else
  bad "second run: installed[] is $(jarr "$two" installed | tr '\n' '|')"
fi
hasnt "second run: state-dir is not reported missing" "$(jarr "$two" missing)" state-dir
if [ "$(jarr "$one" missing)" = "$(jarr "$two" missing)" ]; then
  ok "second run: missing[] is unchanged from the first"
else
  bad "second run: missing[] changed between runs"
fi

# --- 4. gh present but logged out ---------------------------------------------------
# The assertion that matters as much as the name is that it comes back at all:
# a device-flow login would sit there forever.
T="$(newtmp)"; BIN="$T/bin"
mkbin "$BIN" node git python3
stub_gh_loggedout "$BIN"; stub_coderabbit_authed "$BIN"; stub_mcp_config "$T/cfg"
out="$(env PATH="$BIN" HOME="$T/home" ODOO_DEV_STATE_DIR="$T/state" \
       CLAUDE_CONFIG_DIR="$T/cfg" timeout 25 bash "$SETUP" --check 2>/dev/null)"; rc=$?
json="$(printf '%s' "$out" | tail -1)"
if [ "$rc" -eq 124 ]; then
  bad "logged out: setup.sh hung and was killed by timeout"
else
  ok "logged out: setup.sh returned rather than hanging (rc=$rc)"
fi
[ "$rc" -ne 0 ] && ok "logged out: exits non-zero" || bad "logged out: exited 0"
assert_shape "logged out" "$json"
has "logged out: gh-auth named in missing[]"          "$(jarr "$json" missing)" gh-auth
has "logged out: manual_steps carries gh auth login"  "$(jarr "$json" manual_steps)" "gh auth login"

# --- 5. node or git missing: the hard failures ---------------------------------------
T="$(newtmp)"; BIN="$T/bin"
mkbin "$BIN" git python3          # no node
stub_gh_authed "$BIN"; stub_coderabbit_authed "$BIN"; stub_mcp_config "$T/cfg"
out="$(env PATH="$BIN" HOME="$T/home" ODOO_DEV_STATE_DIR="$T/state" \
       CLAUDE_CONFIG_DIR="$T/cfg" timeout 60 bash "$SETUP" --check 2>/dev/null)"; rc=$?
json="$(printf '%s' "$out" | tail -1)"
[ "$rc" -ne 0 ] && ok "no node: exits non-zero" || bad "no node: exited 0"
assert_shape "no node" "$json"
has     "no node: node named in missing[]"                "$(jarr "$json" missing)" node
has_sub "no node: manual_steps carries install guidance"  "$(jarr "$json" manual_steps)" "https://nodejs.org"

T="$(newtmp)"; BIN="$T/bin"
mkbin "$BIN" node python3         # no git
stub_gh_authed "$BIN"; stub_coderabbit_authed "$BIN"; stub_mcp_config "$T/cfg"
out="$(env PATH="$BIN" HOME="$T/home" ODOO_DEV_STATE_DIR="$T/state" \
       CLAUDE_CONFIG_DIR="$T/cfg" timeout 60 bash "$SETUP" --check 2>/dev/null)"; rc=$?
json="$(printf '%s' "$out" | tail -1)"
[ "$rc" -ne 0 ] && ok "no git: exits non-zero" || bad "no git: exited 0"
assert_shape "no git" "$json"
has     "no git: git named in missing[]"               "$(jarr "$json" missing)" git
has_sub "no git: manual_steps carries install guidance" "$(jarr "$json" manual_steps)" "https://git-scm.com/downloads"

# --- 6. inside a container, host concerns are not failures ----------------------------
# No docker on the stub PATH, which is exactly how preflight.sh decides it is in a
# container. The point is that docker's own absence is then not reported as a fault.
T="$(newtmp)"; BIN="$T/bin"
mkbin "$BIN" node git python3
stub_gh_authed "$BIN"; stub_coderabbit_authed "$BIN"; stub_mcp_config "$T/cfg"
out="$(env PATH="$BIN" HOME="$T/home" ODOO_DEV_STATE_DIR="$T/state" \
       CLAUDE_CONFIG_DIR="$T/cfg" timeout 60 bash "$SETUP" --check 2>/dev/null)"
json="$(printf '%s' "$out" | tail -1)"
assert_shape "in container" "$json"
if printf '%s' "$json" | grep -q '"context":"devcontainer"'; then
  ok "in container: context comes back devcontainer"
else
  bad "in container: context is not devcontainer: $json"
fi
miss="$(jarr "$json" missing)"
hasnt "in container: docker is not a failure"           "$miss" docker
hasnt "in container: devcontainer-cli is not a failure" "$miss" devcontainer-cli
hasnt "in container: the repos tree is not a failure"   "$miss" repos

# --- 7. usage --------------------------------------------------------------------------
bash "$SETUP" --nonsense >/dev/null 2>&1; rc=$?
[ "$rc" -eq 2 ] && ok "an unknown flag exits 2" || bad "an unknown flag exited $rc, want 2"

rm -rf "${TMPDIRS[@]+"${TMPDIRS[@]}"}"

echo
echo "setup.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
