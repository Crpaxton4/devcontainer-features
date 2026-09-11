#!/usr/bin/env bash
# browser-ensure.sh — find (or install) the headless browser Odoo needs for
# HttpCase tours, and prove it will actually be used.
#
# Usage: browser-ensure.sh [--install | --check]
#
# Why this exists: Odoo does not fail when the browser is missing. Every path in
# odoo/tests/common.py raises unittest.SkipTest — missing executable, Chrome that
# never opens its devtools port, absent websocket-client — and a skipped test is
# logged at INFO and counts as a pass. A suite with tours and no browser is
# silently, permanently green. So this script checks the preconditions up front
# and the caller refuses to run tours without them, rather than discovering
# afterwards that nothing ran.
#
# Search order mirrors Odoo's own _find_executable(), then adds the Playwright
# cache, which is where a devcontainer usually already has a Chrome build.
#
# --install fetches Playwright's chromium-headless-shell (~100 MB, network).
# Off by default: a test run should not quietly download 100 MB.
#
# --check answers the same question in one line of prose and installs nothing,
# ever. It exists for the skill body, whose `` !`cmd` `` injections run every time
# the skill is rendered — including at every agent spawn that preloads it. A load
# path must never pull 100 MB over the network, and it must never fail, so --check
# neither installs nor gates: it reports, and it always exits 0.
#
# Last stdout line (default and --install): {"browser_bin","source","version",
#                    "websocket_client","screenshots_dir","screencasts_dir"}
# Last stdout line (--check): one sentence, no JSON.
# Exit codes: 0 ok | 2 usage | 6 no usable browser (or no websocket-client).
#             --check is always 0, whatever it finds.
set -euo pipefail

install=false
check=false
case "${1:-}" in
  --install) install=true; shift ;;
  --check)   check=true;   shift ;;
esac
[ $# -eq 0 ] || { echo "usage: browser-ensure.sh [--install | --check]" >&2; exit 2; }

PLAYWRIGHT_CACHE="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"
ARTIFACTS_DIR="${ODOO_TEST_ARTIFACTS_DIR:-${TMPDIR:-/tmp}/odoo-test-run}"

bin=""; source=""

usable() { [ -n "${1:-}" ] && [ -x "$1" ] && "$1" --version >/dev/null 2>&1; }

# 1. An explicit override wins, exactly as Odoo reads it.
if usable "${ODOO_BROWSER_BIN:-}"; then
  bin="$ODOO_BROWSER_BIN"; source="env"
fi

# 2. Odoo's own PATH search, in Odoo's order.
if [ -z "$bin" ]; then
  for candidate in google-chrome chromium chromium-browser google-chrome-stable; do
    found="$(command -v "$candidate" 2>/dev/null || true)"
    if usable "$found"; then bin="$found"; source="path"; break; fi
  done
fi

# 3. The Playwright cache. Prefer chrome-headless-shell: it is the build meant
# for exactly this, and it is smaller than full Chromium.
if [ -z "$bin" ] && [ -d "$PLAYWRIGHT_CACHE" ]; then
  while IFS= read -r candidate; do
    if usable "$candidate"; then bin="$candidate"; source="playwright"; break; fi
  done < <(
    ls -d "$PLAYWRIGHT_CACHE"/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell 2>/dev/null | sort -r
    ls -d "$PLAYWRIGHT_CACHE"/chromium-*/chrome-linux64/chrome 2>/dev/null | sort -r
  )
fi

# --check stops here. It reports what the search found and returns, so that no
# caller on a load path can install anything or be broken by an absent browser.
if [ "$check" = true ]; then
  if [ -z "$bin" ]; then
    echo "no headless browser — tours would be SKIPPED and the suite would still report green. Run browser-ensure.sh --install (~100 MB, network) or set ODOO_BROWSER_BIN before --with-tours."
  elif ! python3 -c "import websocket" >/dev/null 2>&1; then
    echo "browser present ($("$bin" --version 2>/dev/null | head -1), $source) but websocket-client is not importable by python3, so Odoo would SKIP every tour anyway: pip install websocket-client."
  else
    echo "browser ready: $("$bin" --version 2>/dev/null | head -1) from $source, websocket-client importable — tours will run."
  fi
  exit 0
fi

# 4. Fetch one, only when asked.
if [ -z "$bin" ] && [ "$install" = true ]; then
  echo "installing playwright chromium-headless-shell into $PLAYWRIGHT_CACHE" >&2
  if command -v npx >/dev/null 2>&1; then
    PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_CACHE" npx --yes playwright install chromium-headless-shell >&2 || true
  elif command -v python3 >/dev/null 2>&1 && python3 -c "import playwright" >/dev/null 2>&1; then
    PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_CACHE" python3 -m playwright install chromium-headless-shell >&2 || true
  else
    echo "neither npx nor the python playwright package is available to install a browser" >&2
  fi
  while IFS= read -r candidate; do
    if usable "$candidate"; then bin="$candidate"; source="playwright-installed"; break; fi
  done < <(ls -d "$PLAYWRIGHT_CACHE"/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell 2>/dev/null | sort -r)
fi

if [ -z "$bin" ]; then
  echo "no usable headless browser found. Looked at: \$ODOO_BROWSER_BIN; google-chrome/chromium/chromium-browser/google-chrome-stable on PATH; $PLAYWRIGHT_CACHE. Re-run with --install to fetch one, or set ODOO_BROWSER_BIN. Without a browser Odoo SKIPS every tour and the suite is silently green." >&2
  exit 6
fi

# The other silent-skip path: no websocket-client means HttpCase.browser_js
# raises SkipTest before the browser is ever launched.
websocket_client=false
python3 -c "import websocket" >/dev/null 2>&1 && websocket_client=true
if [ "$websocket_client" != true ]; then
  echo "websocket-client is not importable by python3 — Odoo skips every browser test without it (pip install websocket-client)" >&2
  exit 6
fi

# Odoo writes failure screenshots to config['screenshots']/<db>/screenshots. The
# devcontainer config points that at a host bind mount that need not exist here;
# an unwritable dir turns a real test failure into a confusing IOError.
mkdir -p "$ARTIFACTS_DIR/screenshots" "$ARTIFACTS_DIR/screencasts"

version="$("$bin" --version 2>/dev/null | head -1)"

node -e '
  const [bin, source, version, ws, artifacts] = process.argv.slice(1);
  console.log(JSON.stringify({
    browser_bin: bin, source, version,
    websocket_client: ws === "true",
    screenshots_dir: artifacts + "/screenshots",
    screencasts_dir: artifacts + "/screencasts",
  }));
' "$bin" "$source" "$version" "$websocket_client" "$ARTIFACTS_DIR"
