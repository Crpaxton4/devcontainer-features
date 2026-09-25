#!/usr/bin/env bash
set -euo pipefail

echo "Activating feature 'personal-features'"

# install.sh always runs as root. _REMOTE_USER/_REMOTE_USER_HOME come from the
# dev container CLI; default them so this also works under harnesses that run
# as root without setting them (e.g. this repo's own --remote-user root tests).
: "${_REMOTE_USER:=root}"
: "${_REMOTE_USER_HOME:=/root}"

# --- Download helpers -------------------------------------------------------
# The previous #!/bin/sh interpreter (dash on Debian) has no `set -o pipefail`,
# so a `curl … | sh`/`curl … | tar` pipe reported only the last stage's exit
# status - a failed download was silently masked and the `|| echo WARNING`
# fallbacks could never fire. These helpers download to a file first so a
# failure is caught directly instead of hidden behind a pipe (and give a single
# place to add checksumming later).

# fetch(url, dest[, sha256]): download url to dest, retrying transient failures.
# Fails loudly (non-zero exit) so callers can react.
#
# The optional third argument is the expected SHA-256 of the downloaded file; it
# is the "checksumming later" the comment above reserved space for, added here
# rather than at a call site so every future caller can opt in with one extra
# word. A mismatch DELETES the file before returning non-zero, so a caller that
# ignores the status can never go on to install a body that failed the check.
#
# Verification is opt-in per call, not mandatory, because the existing callers
# fetch installer scripts and release tarballs whose publishers re-cut assets
# under the same tag; pinning a digest for those would trade a working install
# for a broken one on every upstream re-tag. Where the digest IS pinned (odoo-ls
# below) that trade is the point: the binary runs as a long-lived server inside
# every session.
fetch() {
    local url="$1" dest="$2" expected="${3-}"
    curl -fsSL --retry 3 --retry-delay 2 -o "$dest" "$url" || return 1
    [ -n "$expected" ] || return 0
    local actual
    actual="$(sha256sum "$dest" | cut -d' ' -f1)"
    if [ "$actual" != "$expected" ]; then
        echo "ERROR: checksum mismatch for $url" >&2
        echo "  expected sha256 $expected" >&2
        echo "  actual   sha256 $actual" >&2
        rm -f "$dest"
        return 1
    fi
}

# run_installer(url, args...): download an installer script to a temp file, then
# execute it with the given args - the structural replacement for the masking
# `curl … | sh -s -- args` pipe. Error handling is explicit (not via set -e)
# because bash disables set -e inside a function called on the left of `||`,
# which is exactly how the best-effort callers below invoke this; relying on
# set -e there would let a failed download slip through and re-mask it.
run_installer() {
    local installer_url="$1"
    shift
    local installer_tmp
    installer_tmp="$(mktemp)"
    if ! fetch "$installer_url" "$installer_tmp"; then
        rm -f "$installer_tmp"
        return 1
    fi
    sh "$installer_tmp" "$@"
    local rc=$?
    rm -f "$installer_tmp"
    return "$rc"
}

# retry(cmd...): run cmd, retrying transient failures. Mirrors fetch()'s
# `curl --retry 3 --retry-delay 2` for commands curl can't drive - notably the
# `uv` wheel installs below, whose large downloads (e.g. onnxruntime, 17.8 MiB)
# can exceed uv's HTTP timeout on a slow link and fail the whole image build on
# the first hiccup. Runs up to 3 attempts with a 2s delay between them and
# returns the last attempt's exit status, so a caller under `set -e` still
# aborts if every attempt fails and a `|| ...` caller still sees the failure.
retry() {
    local attempt=1
    while true; do
        if "$@"; then
            return 0
        fi
        if [ "$attempt" -ge 3 ]; then
            return 1
        fi
        echo "WARNING: '$*' failed (attempt $attempt/3); retrying in 2s" >&2
        attempt=$((attempt + 1))
        sleep 2
    done
}

# Create the fixed container-side paths that the containerEnv vars point at and
# that the bind mounts overlay at runtime. Creating them here means the feature
# still works in test containers where no bind mounts are active (e.g. the
# devcontainer features test harness).
#
# The list of persisted paths lives in persisted-paths.tsv (shipped next to this
# script), the single source of truth shared with setup.sh/setup.ps1 and the
# Feature JSON; .github/scripts/check_persisted_paths.py fails CI if the JSON
# drifts from it. Loop it here instead of hardcoding the paths so adding one is a
# one-line manifest edit. A trailing slash marks a directory (mkdir -p); no
# trailing slash marks a file (touch its parent, then the file).
#
# The `provision` column decides whether the container creates the target.
# `container` rows are created/chowned/chmod'ed here (they double as the empty
# fallback dirs the containerEnv vars point at when no bind mount is active, e.g.
# in the features-test harness). `host` rows (the odoo-sdk task-tracker DB, #369)
# are the deliberate exception: the host provisions that directory + database and
# it is ONLY ever a bind mount, so the container must NOT pre-create it. This
# reverses the #115 conclusion, which was wrong: #115 correctly diagnosed that a
# build-time `chown` to $_REMOTE_USER bakes in the PRE-remap uid (updateRemoteUserUID
# moves the user at container-create precisely so bind mounts line up), but drew
# the wrong lesson - "therefore don't mount it". A mounted path never needs that
# build-time chown (the mount shadows the image dir), which is exactly why the six
# credential mounts already work. Pre-creating the tracker target here would be
# actively harmful: a missing/misconfigured mount would then be indistinguishable
# from a working one - the container would find an empty dir and silently build a
# fresh, container-local database that is discarded on rebuild. Not creating it is
# what makes a broken mount fail loudly (TrackerStateMissingError) instead.
_MANIFEST="$(dirname "$0")/persisted-paths.tsv"
_TAB="$(printf '\t')"
while IFS="$_TAB" read -r _name _host_source _container_target _env_var _env_value _mode _provision; do
    case "$_name" in ''|'#'*) continue ;; esac  # skip blank/comment lines
    case "$_provision" in host) continue ;; esac  # host-provisioned: never create in-container (#369)
    case "$_container_target" in
        */) mkdir -p "$_container_target" ;;
        *)  mkdir -p "$(dirname "$_container_target")"; touch "$_container_target" ;;
    esac
    chown "$_REMOTE_USER" "$_container_target"
    chmod "$_mode" "$_container_target"
done < "$_MANIFEST"

# create-pr: config-driven `gh pr create` wrapper. Reads global/per-project
# YAML from PR_AUTOMATION_CONFIG (bind-mounted at runtime, empty in test
# containers — the script tolerates missing config at every level).
install -m 0755 "$(dirname "$0")/create-pr" /usr/local/bin/create-pr

# gh-as-owner (#810): runs a push, a PR, or any gh call as the account that owns
# the checkout's origin remote, with the owner derived from that remote instead
# of decided on the command line. Two gh accounts share one config here, so the
# identity decision was being made by hand every time - and the spellings that
# make it by hand are the ones that put a token into argv, into a remote URL, or
# into a shared .git/config. This resolves the token inside one process and
# exports it to exactly one child; it is never an argument and never written
# anywhere. It was proven under .claude/commands/implement-issues/ first (see
# its header for the two mechanisms that failed before it); this install is what
# makes it machine-wide rather than scoped to one command's worker sessions, and
# that .claude/ path is now a delegator to this copy. Needs gh on PATH, which
# the hard dependsOn on the github-cli Feature guarantees.
install -m 0755 "$(dirname "$0")/gh-as-owner" /usr/local/bin/gh-as-owner

# --- Claude consulting skills: NOT shipped loose any more (#738) ------------
# This feature used to stage its consulting skills under /usr/local/share/
# personal-features/skills and publish them into $CLAUDE_CONFIG_DIR/skills with
# a sync-claude-skills script run from postCreateCommand. Both are gone: the
# skills now ship inside the odoo-dev plugin, which sync-claude-mcp installs
# from this repo's marketplace (#723). A loose copy would load as a personal
# skill ALONGSIDE its plugin twin and compete for the same triggers, so the
# feature seeds none - and sync-claude-mcp deletes the copies older containers
# left behind in the bind-mounted ~/.claude (see its odoo-dev block below).

# --- Claude Code lifecycle hooks (#327) -------------------------------------
# claude-event-hook: the hook shim invoked by every feature-owned hook entry; it
# forwards each Claude Code lifecycle event to `odoo-sdk log-event`. Installed to
# /usr/local/bin (outside the CLAUDE_CONFIG_DIR bind mount) so it is always on
# PATH at runtime. sync-claude-hooks: at runtime (postCreateCommand) merges the
# feature-owned hooks block into the live, mounted $CLAUDE_CONFIG_DIR/
# settings.json — the build-time directory is shadowed by the ~/.claude mount,
# same reason the retired skills sync ran from postCreateCommand (see above).
# THIS COPY IS NOT THE ONE settings.json NAMES (#803): that file is shared with
# the host, where /usr/local/bin/claude-event-hook does not exist, so every host
# hook exit-127'd. sync-claude-hooks now republishes this binary into
# $CLAUDE_CONFIG_DIR/hooks/ — reachable from both ends of the mount — and points
# the hook entries there. This install stays the build-time source of truth the
# runtime sync copies from.
install -m 0755 "$(dirname "$0")/claude-event-hook" /usr/local/bin/claude-event-hook
install -m 0755 "$(dirname "$0")/sync-claude-hooks" /usr/local/bin/sync-claude-hooks
# worktree-context-hook (#809): a second SessionStart hook, unrelated to event
# capture — it states the worktree Bash syntax constraint in the session context
# instead of letting every worktree session rediscover it by being refused.
# Published into $CLAUDE_CONFIG_DIR/hooks/ by the same runtime sync, for the same
# #803 reason; this install is its build-time source of truth.
install -m 0755 "$(dirname "$0")/worktree-context-hook" /usr/local/bin/worktree-context-hook
# publish-claude-wrapper (#807): the runtime half of the `claude` wrapper, which
# is generated further down. The wrapper is what DELIVERS
# system-prompt-append.md, and it used to exist only inside the image while the
# rules it delivers live in the bind mount - so the rules updated on every edit
# and their reader updated only on rebuild, silently. This script publishes the
# wrapper into $CLAUDE_CONFIG_DIR at container-create time (same #803 reason the
# hook sync runs from postCreateCommand: a build-time write there is shadowed by
# the mount) and reports an on-PATH wrapper that carries no
# --append-system-prompt-file at all.
install -m 0755 "$(dirname "$0")/publish-claude-wrapper" /usr/local/bin/publish-claude-wrapper

# Installed via npm (rather than the standalone native installer) so it rides
# on the Node.js runtime provided by the official node Feature (dependsOn).
# Feature install order/PATH propagation isn't reliably honored by every
# consumer (e.g. compose-based devcontainers, or base images - like Odoo's -
# that bake in their own ancient system Node ahead of nvm on PATH), so don't
# trust `node`/`npm` on PATH blindly: fall back to the node Feature's known
# nvm symlink, then hard-fail with an actionable error instead of letting npm
# crash deep inside install.cjs with a confusing syntax error on old Node.
if [ -d /usr/local/share/nvm/current/bin ]; then
    PATH="/usr/local/share/nvm/current/bin:$PATH"
fi

NODE_BIN="$(command -v node || true)"
NODE_VERSION="$( [ -n "$NODE_BIN" ] && "$NODE_BIN" --version 2>/dev/null || echo 'n/a')"
NODE_MAJOR="$( [ -n "$NODE_BIN" ] && "$NODE_BIN" -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
if [ "$NODE_MAJOR" -lt 18 ] 2>/dev/null; then
    echo "ERROR: personal-features requires Node.js >=18 on PATH to install @anthropic-ai/claude-code, but found: ${NODE_BIN:-no node on PATH} ($NODE_VERSION)." >&2
    echo "This Feature depends on ghcr.io/devcontainers/features/node, but Feature install order across base images/configs is not guaranteed - some base images (e.g. Odoo's) bundle their own old Node ahead of it on PATH. Add or pin a Node >=18 Feature explicitly in your devcontainer.json (or dev.containers.defaultFeatures), and ensure it installs before personal-features." >&2
    exit 1
fi

# Pinned so a CLI release cannot silently change what every rebuilt container
# gets - an unpinned `npm install -g` makes the image non-reproducible and lets
# an upstream regression land in every container at once (#741). The feature
# also sets DISABLE_AUTOUPDATER=1 (devcontainer-feature.json), so this version
# is the one the container KEEPS: nothing upgrades it behind our back. npm/
# Dependabot do not track shell-script pins - bump this by hand, same rule as
# the pinned GitHub-release tools further down. Keep it in step with
# CLAUDE_CODE_VERSION in .github/workflows/plugin-odoo-dev.yaml.
CLAUDE_CODE_VERSION=2.1.268  # npmjs.com/package/@anthropic-ai/claude-code

export PATH
npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}"

# npm links `claude` on PATH as a *relative* symlink into its global
# node_modules tree, so resolve the real absolute target before touching
# anything - moving the symlink itself instead would leave it dangling,
# since its relative target is only correct from its original directory.
WRAPPER_PATH="$(command -v claude)"
REAL_CLAUDE_BIN="$(readlink -f "$WRAPPER_PATH")"
rm "$WRAPPER_PATH"

# Wrap the real binary so a *bare interactive* session auto-connects to the IDE
# (`--ide`), while everything else passes through untouched. Injecting `--ide`
# only for the zero-arg TTY case - rather than maintaining an allowlist of
# subcommands to *exclude* from injection - means a new subcommand shipped by a
# future Claude Code release can never be silently mangled into
# `claude --ide <subcommand>` (the old allowlist would have needed a manual edit
# for every new subcommand, and any it missed broke). Subcommands (`claude mcp`,
# `claude auth login`), flags, prompts, and piped/non-interactive invocations
# all fall through to the passthrough arm.
#
# Accepted trade-off: `claude -c`, `claude -r`, and `claude "prompt"` no longer
# auto-get `--ide` (strict, predictable rule chosen over guessing intent).
#
# The same wrapper also injects `--append-system-prompt-file` (#740) so that
# session-wide style and policy rules load as SYSTEM prompt rather than drifting
# user-turn context. The file it points at is local-only and hand-maintained in
# the bind-mounted claude-home: this feature never ships it and never creates it,
# so the flag is injected only when the file is actually there - an absent file
# leaves the invocation byte-identical to the pre-#740 wrapper.
#
# Injection is gated on the invocation being a SESSION - no args at all, or a
# first argument that is a flag. Subcommands (`claude mcp`, `claude plugin`,
# anything whose first argument is not a flag) REJECT the option outright, so
# they must be left alone. `\${1#-}` != `\$1` is the POSIX test for "starts with
# a dash"; a `case` statement would read more naturally but would resurrect the
# subcommand-allowlist shape this wrapper deliberately does not have.
#
# THE WRAPPER IS ALSO ITS OWN SHARED COPY (#807). The prompt file lives in the
# bind mount and is therefore always current; the wrapper that reads it lived
# only in the image and was therefore current only until the next edit. On this
# machine that gap ran for the better part of two weeks across an unknown number
# of sessions, and nothing reported it: the flag was simply never passed, so
# every standing rule in the file was absent from all of them. So the wrapper now
# consults $CLAUDE_CONFIG_DIR/personal-features/claude-wrapper - the copy
# publish-claude-wrapper publishes at container-create time from the same source
# - and execs it when one is there. Same shape as #803's published hook shim,
# and the same reason: that directory is the one place both ends of the mount
# agree on, so a wrapper published by ANY container on this machine reaches every
# other one without a rebuild.
#
# The real binary cannot travel with the published copy (its path carries this
# image's Node version), so the stub passes its own through CLAUDE_REAL_BIN and
# the published copy prefers it over the path baked into whichever image wrote
# it. Re-entry is ruled out by comparing "$0" against the shared path rather than
# by an environment flag, so a nested `claude` still goes through the shared
# copy. A published copy that is absent, unreadable or non-executable leaves the
# invocation byte-identical to the pre-#807 wrapper.
#
# The identical bytes are written to a stable image path FIRST and installed onto
# PATH from there: publish-claude-wrapper needs a source outside the mount to
# copy from, exactly like /usr/local/bin/claude-event-hook is for the hook sync.
WRAPPER_SRC_DIR=/usr/local/share/personal-features
WRAPPER_SRC="$WRAPPER_SRC_DIR/claude-wrapper"
install -d -m 0755 "$WRAPPER_SRC_DIR"
cat > "$WRAPPER_SRC" << EOF
#!/bin/sh
set -e

REAL="$REAL_CLAUDE_BIN"
if [ -n "\${CLAUDE_REAL_BIN:-}" ] && [ -x "\${CLAUDE_REAL_BIN:-}" ]; then
    REAL="\$CLAUDE_REAL_BIN"
fi
PROMPT_FILE="\${CLAUDE_CONFIG_DIR:-/usr/local/share/claude-home}/system-prompt-append.md"
SHARED_WRAPPER="\${CLAUDE_CONFIG_DIR:-/usr/local/share/claude-home}/personal-features/claude-wrapper"

if [ "\$0" != "\$SHARED_WRAPPER" ] && [ -x "\$SHARED_WRAPPER" ]; then
    CLAUDE_REAL_BIN="\$REAL"
    export CLAUDE_REAL_BIN
    exec "\$SHARED_WRAPPER" "\$@"
fi

if [ -f "\$PROMPT_FILE" ] && { [ \$# -eq 0 ] || [ "\${1#-}" != "\$1" ]; }; then
    if [ \$# -eq 0 ] && [ -t 0 ]; then
        set -- --ide
    fi
    exec "\$REAL" --append-system-prompt-file "\$PROMPT_FILE" "\$@"
fi

if [ \$# -eq 0 ] && [ -t 0 ]; then
    exec "\$REAL" --ide
else
    exec "\$REAL" "\$@"
fi
EOF
chmod 0755 "$WRAPPER_SRC"
install -m 0755 "$WRAPPER_SRC" "$WRAPPER_PATH"

# --- Python libraries -------------------------------------------------------
# Wheels are bundled into this feature at release time. Installed into an
# isolated uv-managed venv to avoid touching system cryptography, which would
# break pyOpenSSL on odoo:17 (cryptography 41+ uses OpenSSL 3.x CFFI bindings
# that removed X509_V_FLAG_NOTIFY_POLICY).
#
# The venv's interpreter is PINNED and deliberately independent of the base
# image's python3 (#674). odoo_sdk is a pure RPC client - it never imports odoo
# core - so nothing requires its interpreter to match the container's Odoo
# Python. uv is a static binary that runs on every supported base (odoo:16's
# bullseye is glibc 2.31) and fetches its own python-build-standalone CPython
# (needs only glibc 2.17+) when the pinned version isn't on the image. This
# block used to be gated on the BASE IMAGE shipping python3 >= 3.10, which
# silently skipped the entire toolchain on odoo:16 (bullseye, Python 3.9): the
# only signal was a build-log warning, surfacing weeks later as a confusing
# ENOENT from a stale bind-mounted odoo-mcp MCP registration. The toolchain now
# installs unconditionally on every base image.
UV_PYTHON_PIN=3.11
FEATURE_DIR="$(dirname "$0")"
# Give uv's downloads plenty of headroom: the odoo_sdk dependency tree pulls
# large wheels (onnxruntime 17.8 MiB, numpy 15.9 MiB, ...) that can exceed
# uv's default 30s HTTP timeout on a slow link and fail the whole image
# build. Paired with the retry() wrapper on the install calls below.
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-300}"
# Put the uv-managed CPython somewhere EVERY container user can read. uv's
# default install dir is $HOME/.local/share/uv/python, and install.sh runs as
# root, so the interpreter would land under /root (mode 0700). Both the odoo-sdk
# venv and the mempalace tool venv symlink bin/python at that absolute path, so
# on any base image with a non-root remoteUser (mcr.../javascript-node ships
# `node`; the odoo images run as root, which is why only that one CI matrix leg
# caught it) every console script died with
# `bad interpreter: Permission denied` - exec(2) EACCES on the traversal into
# /root, not a PATH problem. /usr/local/share/uv already hosts the tool venvs,
# so this keeps the whole toolchain under one world-readable prefix.
export UV_PYTHON_INSTALL_DIR=/usr/local/share/uv/python
install -d -m 0755 "$UV_PYTHON_INSTALL_DIR"
# odoo base images don't ship uv; install it so we can create an isolated
# venv without touching system Python packages.
if ! command -v uv >/dev/null 2>&1; then
    UV_INSTALL_DIR=/usr/local/bin
    export UV_INSTALL_DIR
    # Deliberately not best-effort: uv is required downstream (`uv venv`),
    # so a masked download failure here would surface later as a confusing
    # error. run_installer fails loudly and aborts the build instead.
    run_installer https://astral.sh/uv/install.sh
    unset UV_INSTALL_DIR
fi
# Install the bundled odoo_sdk wheel(s) into one shared uv venv. The glob
# stays literal (a single non-matching entry) when no wheel is bundled, so
# gate on the first entry actually being a file before doing any work.
_SDK_ENV=/usr/local/share/uv/tools/odoo-sdk
_sdk_wheels=("$FEATURE_DIR"/odoo_sdk-*.whl)
if [ -f "${_sdk_wheels[0]}" ]; then
    # venv creation is hoisted out of the per-wheel loop: with a second
    # bundled wheel the old in-loop `uv venv` would re-run on the same path
    # and error. The `[ -d ]` guard also makes a re-provision over a
    # persisted venv a no-op. `uv pip install` still runs per wheel.
    # --python pins the uv-managed interpreter (#674); a failure here aborts
    # the build loudly rather than leaving a container with no odoo-mcp.
    [ -d "$_SDK_ENV" ] || retry uv venv --python "$UV_PYTHON_PIN" "$_SDK_ENV"
    for wheel in "${_sdk_wheels[@]}"; do
        retry uv pip install --python "$_SDK_ENV/bin/python" "$wheel"
    done
    # Link the entry points once, after all wheels are installed. ALL THREE
    # console scripts must be linked: `odoo-sdk` is the CLI that
    # claude-event-hook shells out to (it guards on `command -v odoo-sdk`
    # and silently no-ops when it is missing, so leaving it unlinked made
    # every feature-owned Claude Code hook a dead no-op - #496) and the only
    # entry point to `odoo-sdk prune`. Adding an entry point to the wheel
    # means adding it here.
    _SDK_ENTRYPOINTS=(odoo-sdk odoo-mcp odoo-tui)
    for _entry in "${_SDK_ENTRYPOINTS[@]}"; do
        ln -sf "$_SDK_ENV/bin/$_entry" "/usr/local/bin/$_entry"
    done
    # Verify at build time, where a broken install is cheap to catch and
    # loud. The downstream consumers (claude-event-hook, the MCP
    # registration below) all degrade to a *silent* no-op when an entry
    # point is missing, so without this the failure only ever shows up as
    # "the event table is empty" weeks later (#496).
    #
    # The hand-written loop that used to live here is gone (#805): the list of
    # programs the feature's hook path depends on, and the routine that resolves
    # them, now live together in sync-claude-hooks as HOOK_DEPS/--check-deps,
    # beside the hook commands themselves and under the same "add a hook -> add
    # its dependency" contract. This is the half of that check that CAN run at
    # build time; the commands in settings.json cannot be checked until the
    # ~/.claude mount is live, so sync-claude-hooks audits those at
    # container-create time instead.
    if ! /usr/local/bin/sync-claude-hooks --check-deps; then
        echo "ERROR: the odoo_sdk console scripts are not all executable on PATH after install (expected $_SDK_ENV/bin/<script> -> /usr/local/bin/<script>)." >&2
        echo "The bundled wheel installed but did not provide every entry point; hooks and CLI tooling that depend on them would silently no-op, so failing the build instead." >&2
        exit 1
    fi
else
    # The only remaining skip path, and it is a packaging condition, not a
    # base-image one: wheels are bundled at release/CI time (see
    # .github/workflows/test.yaml), so a plain dev checkout has none. Name the
    # consequence loudly - the silent version of this skip is exactly what let
    # #674 fester. sync-claude-mcp additionally deregisters a stale user-scope
    # odoo-mcp entry at container-create time so the mounted ~/.claude stays
    # self-consistent.
    echo "WARNING: no bundled odoo_sdk wheel in $FEATURE_DIR; skipping the odoo-sdk toolchain. This container will have NO odoo-sdk/odoo-mcp/odoo-tui on PATH (Claude Code hooks and the odoo-mcp MCP server will not work). Wheels are bundled at release/CI time." >&2
fi

# mempalace: global cross-project memory palace, auto-mined via Claude Code
# hooks (MEMPAL_DIR below). Install its venv under the same shared uv tools
# dir as odoo-sdk - pinned to the same uv-managed interpreter (#674) so the
# base image's Python is irrelevant here too - and link the entry point onto
# /usr/local/bin so it lands on every user's PATH (install.sh runs as root,
# so uv's default ~/.local would be root-only). Best-effort - a PyPI/network
# hiccup shouldn't fail the whole build, matching the other optional-tool
# installs - but the warning names the consequence instead of skipping
# silently.
#
# Pinned for the same reason as CLAUDE_CODE_VERSION above (#741): an unpinned
# `uv tool install` resolves to whatever PyPI publishes at build time, so two
# containers built a day apart get different hook/MCP behaviour and a bad
# upstream release reaches every rebuild at once. Dependabot does not track
# shell-script pins - bump this by hand.
MEMPALACE_VERSION=3.9.0  # pypi.org/project/mempalace
UV_TOOL_DIR=/usr/local/share/uv/tools UV_TOOL_BIN_DIR=/usr/local/bin \
    retry uv tool install --python "$UV_PYTHON_PIN" "mempalace==${MEMPALACE_VERSION}" \
    || echo "WARNING: failed to install mempalace; this container will have NO mempalace CLI/MCP server, so the mempalace Claude Code plugin hooks cannot mine and its MCP registration will fail with ENOENT" >&2

# --- the shared mempalace hub's own dependency set (#898) ---------------------
# The hub is no longer a process in THIS container; it is one long-lived sibling
# container on the host daemon, shared by every devcontainer (mempalace-hub-up
# below). That container has to install the same mempalace this Feature pinned,
# and it cannot read this container's uv tool venv - it is a different image on a
# different filesystem. So freeze the venv that was just built into a
# requirements file, copy it onto the shared palace mount at run time, and let a
# stock python:3.11-slim install exactly it.
#
# `uv pip freeze` over the tool venv, not a hand-written `mempalace==X`: the
# whole transitive closure matters here. chromadb - the single-writer backend the
# palace uses - and the onnxruntime/huggingface stack that embeds are pinned by
# the freeze along with everything else, so the hub runs the resolution this
# image was tested with rather than whatever PyPI publishes the day the hub is
# first created. That is also what makes the hub's `requirements-sha` label a
# meaningful reconcile key: a Feature release that changes any pin changes the
# sha, and mempalace-hub-up then recreates the hub instead of leaving a container
# built from last month's resolution running.
MEMPALACE_HUB_SHARE_DIR=/usr/local/share/personal-features
MEMPALACE_HUB_REQUIREMENTS="$MEMPALACE_HUB_SHARE_DIR/mempalace-hub-requirements.txt"
MEMPALACE_HUB_ENV_FILE="$MEMPALACE_HUB_SHARE_DIR/mempalace-hub.env"
MEMPALACE_HUB_IMAGE=python:3.11-slim
mkdir -p "$MEMPALACE_HUB_SHARE_DIR"
if [ -x /usr/local/share/uv/tools/mempalace/bin/python ] \
    && uv pip freeze --python /usr/local/share/uv/tools/mempalace/bin/python \
        > "$MEMPALACE_HUB_REQUIREMENTS" 2>/dev/null \
    && [ -s "$MEMPALACE_HUB_REQUIREMENTS" ]; then
    echo "mempalace hub: froze $(wc -l < "$MEMPALACE_HUB_REQUIREMENTS") pins to $MEMPALACE_HUB_REQUIREMENTS"
else
    # The freeze is best-effort for the same reason the install above is. A
    # single pin still gives the hub the right mempalace; it just lets pip
    # resolve the rest, which is weaker than this image was tested with, and is
    # said out loud rather than silently.
    printf 'mempalace==%s\n' "$MEMPALACE_VERSION" > "$MEMPALACE_HUB_REQUIREMENTS"
    echo "WARNING: could not freeze the mempalace venv; the shared hub will resolve mempalace==${MEMPALACE_VERSION}'s dependencies itself instead of reusing this image's tested set (#898)" >&2
fi
chmod 0644 "$MEMPALACE_HUB_REQUIREMENTS"

# The version and the image the hub runs, as data rather than as a string
# repeated in a second script. mempalace-hub-up sources this file, so bumping
# MEMPALACE_VERSION above moves the hub too.
printf 'MEMPALACE_HUB_VERSION=%s\nMEMPALACE_HUB_IMAGE=%s\n' \
    "$MEMPALACE_VERSION" "$MEMPALACE_HUB_IMAGE" > "$MEMPALACE_HUB_ENV_FILE"
chmod 0644 "$MEMPALACE_HUB_ENV_FILE"

# Belt-and-braces on the interpreter perms, after the last uv call that could
# have materialised a managed CPython. UV_PYTHON_INSTALL_DIR above fixes the
# *location*; this fixes the *modes* inside it, which come from whatever the
# python-build-standalone tarball carried and from root's umask. a+rX (capital
# X) adds the search/execute bit only where it already exists for the owner, so
# directories and real binaries become traversable/runnable for non-root users
# while plain .py files stay non-executable. Cheap enough to run
# unconditionally; guarded only on the dir existing, since a base image that
# already shipped the pinned Python means uv downloaded nothing.
if [ -d "$UV_PYTHON_INSTALL_DIR" ]; then
    chmod -R a+rX "$UV_PYTHON_INSTALL_DIR"
fi

# --- mempalace palace root reconciliation (#596, #643) -----------------------
# Upstream mempalace's hooks CLI hardcodes its palace root to ~/.mempalace and
# treats its absence as the user-removed kill-switch, while the MCP server
# writes to the bind mount at /usr/local/share/mempalace (MEMPALACE_PALACE_PATH,
# see persisted-paths.tsv). In a fresh container ~/.mempalace does not exist, so
# every plugin hook fire (Stop/SessionEnd/PreCompact) short-circuited silently -
# a no-op indistinguishable from a working install, the same failure class as
# #485. Symlink the home path onto the mount so both sides agree on ONE root and
# hook state/logs persist across rebuilds. The mount target dir already exists:
# the persisted-paths loop above created it (mempalace row).
#
# The link is load-bearing for far more than the hooks CLI: a whole class of
# mempalace state ignores MEMPALACE_PALACE_PATH and is hardcoded under
# $HOME/.mempalace - config.json and people_map.json (config.py), locks/
# (palace.py), wal/ (wal.py), hook_state/ (hooks_cli.py) and known_entities.json
# (miner.py). Without the link every one of those is container-local and lost on
# the next rebuild.
#
# Installed as a script and run from BOTH here and postCreateCommand, because
# the two passes see different filesystems. At image-build time the bind mount
# is not attached yet, so /usr/local/share/mempalace is still the empty dir the
# persisted-paths loop made and the mount-inspecting steps below have nothing to
# look at; the host's palace only appears at container-create time. Creating the
# symlink early is still worth doing (it resolves lazily, and it is baked into
# the image), and every step is idempotent, so running it twice is free. The
# script also makes the repair exercisable by the feature test - which is the
# only way to test it, since `devcontainer features test` runs no
# postCreateCommand.
#
# The script below no longer has a step 4c. The SessionStart recall hook used to
# be checked there (#805): it was one of exactly two hand-written assertions over
# the ten commands the shared settings.json references, and the other eight -
# odoo-api-guard.sh among them - were provisioned on trust. sync-claude-hooks now
# resolves EVERY command in that file at container-create time, so
# mempalace-recall.sh is covered by the general rule rather than by a rule of its
# own, and is reported exactly when settings.json actually references it. The
# #744 decision it encoded is kept verbatim there: warn, never create - a stub
# would look like a working recall while recalling nothing. This note lives out
# here rather than in the heredoc because everything inside the heredoc is
# shipped verbatim into /usr/local/bin/mempalace-repair, where commentary about
# install.sh's own history does not belong.
cat > /usr/local/bin/mempalace-repair << 'MEMPALACE_REPAIR'
#!/bin/sh
set -eu
# mempalace-repair [HOME_DIR] - reconcile mempalace's several disagreeing ideas
# of where the palace lives (#596, #643):
#
#   1. point <HOME_DIR>/.mempalace at the palace mount, migrating a
#      container-local directory left there by an earlier build;
#   2. remove the stray `~` directory an unexpanded --palace argument creates at
#      the mount root;
#   3. rewrite config.json's palace_path to agree with MEMPALACE_PALACE_PATH;
#   4. assert the two things mempalace-as-only-memory needs that are its own -
#      hooks.auto_save and identity.txt (#744) - warn-only. The third, the
#      SessionStart recall hook, moved to sync-claude-hooks, which resolves every
#      command settings.json references rather than this one alone (#805).
#
# HOME_DIR defaults to $HOME. MEMPALACE_MOUNT overrides the mount root and
# MEMPALACE_LINK_OWNER, when set, is chowned the resulting link; both exist so
# the feature test can drive this against a sandbox instead of the real palace.
# Every step is idempotent and none is fatal on its own.

HOME_DIR="${1:-${HOME:?HOME is unset and no directory argument was given}}"
MOUNT="${MEMPALACE_MOUNT:-/usr/local/share/mempalace}"
LINK="$HOME_DIR/.mempalace"

link_it() {
    ln -s "$MOUNT" "$LINK"
    if [ -n "${MEMPALACE_LINK_OWNER:-}" ]; then
        # -h chowns the link itself, not the (root-owned) target.
        chown -h "$MEMPALACE_LINK_OWNER" "$LINK"
    fi
}

# --- 1. home symlink ---------------------------------------------------------
# #643: the original guard was absent-only (`[ ! -e ] && [ ! -L ]`), so a run
# that found a REAL directory at ~/.mempalace took the else branch and merely
# warned - silently reintroducing exactly the data loss #596 existed to fix, and
# observed in the wild with live hook_state/, locks/ and wal/ stranded off the
# mount. Migrate such a directory onto the mount instead of giving up. Anything
# that is neither a directory nor a symlink is a user artifact and is left alone.
if [ -L "$LINK" ]; then
    if [ "$(readlink "$LINK")" = "$MOUNT" ]; then
        echo "mempalace-repair: $LINK already points at $MOUNT"
    else
        echo "WARNING: $LINK is a symlink to $(readlink "$LINK"), not $MOUNT; leaving it untouched (mempalace state may not persist across rebuilds)" >&2
    fi
elif [ ! -e "$LINK" ]; then
    link_it
    echo "mempalace-repair: linked $LINK to $MOUNT"
elif [ -d "$LINK" ]; then
    # Merge onto the mount rather than over it: `-n` keeps the mount's copy of
    # any file present on both sides. The mount is the surviving root and holds
    # what previous rebuilds accumulated; the container-local directory only ever
    # holds writes made since this image was built.
    echo "mempalace-repair: migrating $LINK onto $MOUNT (#643)"
    mkdir -p "$MOUNT"
    if cp -a -n "$LINK/." "$MOUNT/"; then
        rm -rf "${LINK:?}"
        link_it
        echo "mempalace-repair: migrated and linked $LINK to $MOUNT"
    else
        echo "WARNING: failed to migrate $LINK onto $MOUNT; leaving it untouched (mempalace state will not persist across rebuilds)" >&2
    fi
else
    echo "WARNING: $LINK exists and is neither a directory nor a symlink; leaving it untouched (mempalace plugin hooks may write to a container-local root)" >&2
fi

# --- 2. stray `~` artifact ---------------------------------------------------
# mempalace's MCP server resolves --palace with os.path.abspath() but WITHOUT
# os.path.expanduser() (mcp_server.py), unlike its CLI sibling. A literal `~/...`
# argument therefore resolves against the process cwd instead of $HOME, and the
# chroma backend then makedirs() it - producing a real directory named `~` under
# whatever root the server was started in. One such artifact exists on the mount
# in the wild (`<mount>/~/.mempalace/palace/`), stranding memories where nothing
# will ever read them.
#
# Nothing in this container passes --palace, so this removes existing damage
# rather than working around a live bug. Matched narrowly on that exact shape -
# an entry literally named `~` at the mount root that is a real directory, never
# a symlink - so a blanket delete can never reach real palace data.
if [ -d "$MOUNT/~" ] && [ ! -L "$MOUNT/~" ]; then
    echo "mempalace-repair: removing stray '~' directory under $MOUNT (#643)"
    rm -rf "$MOUNT/~"
fi

# --- 3. config.json palace_path ----------------------------------------------
# Three sources name the palace root and they disagree: MEMPALACE_PALACE_PATH
# (devcontainer-feature.json, persisted-paths.tsv) points at the mount, while a
# config.json carried in on the mount from another machine may name a host path
# that does not exist here. mempalace's Config.palace_path returns on the env
# branch BEFORE consulting config.json (config.py) and says nothing about the
# disagreement, so the stale value sits there indefinitely - misleading anyone
# who reads the file, and silently deciding the answer for any code path that
# reads config.json directly instead of going through Config.
#
# Rewrite ONLY the palace_path key so it agrees with the env var. topic_wings
# and hall_keywords in the same file are user content and are never touched; the
# rewrite preserves every other key and is a no-op when the two already agree.
# python3 is already a hard dependency of the mempalace install (jq is not).
if [ -n "${MEMPALACE_PALACE_PATH:-}" ] && [ -f "$LINK/config.json" ]; then
    python3 - "$LINK/config.json" "$MEMPALACE_PALACE_PATH" <<'MEMPALACE_RECONCILE_PY' || echo "WARNING: failed to reconcile mempalace palace_path, skipping" >&2
import json
import os
import sys

config_file, expected = sys.argv[1], os.path.abspath(os.path.expanduser(sys.argv[2]))
try:
    with open(config_file, encoding="utf-8") as handle:
        config = json.load(handle)
except (OSError, ValueError) as exc:
    print(f"WARNING: cannot read {config_file} ({exc}); leaving it untouched", file=sys.stderr)
    sys.exit(0)
if not isinstance(config, dict):
    print(f"WARNING: {config_file} is not a JSON object; leaving it untouched", file=sys.stderr)
    sys.exit(0)

# Expand before comparing so a config that already agrees, but spells the path
# with a `~`, is recognised as agreeing and left byte-identical.
current = config.get("palace_path")
if current is not None and os.path.abspath(os.path.expanduser(str(current))) == expected:
    sys.exit(0)

config["palace_path"] = expected
with open(config_file, "w", encoding="utf-8") as handle:
    json.dump(config, handle, indent=4)
    handle.write("\n")
print(f"mempalace-repair: palace_path {current!r} -> {expected!r} (#643)")
MEMPALACE_RECONCILE_PY
fi

# --- 4. final asserts: auto_save, identity, recall hook (#744) ----------------
# mempalace is now the ONLY memory in this container: Claude Code's native
# auto-memory is switched off in the shared settings.json (autoMemoryEnabled
# false, CLAUDE_CODE_DISABLE_AUTO_MEMORY=1) and the native memory files were
# mined into the palace. That removes the fallback, so three things have to hold
# or memory stops working with no error anywhere: hooks.auto_save true in
# config.json, an identity.txt for `mempalace wake-up`, and the SessionStart
# recall hook.
#
# All three are hand-maintained content, so this step REPORTS and does not
# repair - a generated config value or a stubbed hook would quietly mask the
# loss of the real one, which is the same failure mode in a better disguise. The
# single exception is a missing identity.txt: absent means there is nothing to
# preserve, so a minimal template is seeded. Every branch below is warn-only and
# the script's exit status is unaffected.

# 4a. config.json hooks.auto_save. mempalace's Stop / SessionEnd / PreCompact
# plugin hooks consult this key and no-op entirely when it is false - which it
# was, silently, until #744. Read-only: the file is the user's, and a false value
# may well be deliberate.
if [ -f "$LINK/config.json" ]; then
    python3 - "$LINK/config.json" <<'MEMPALACE_AUTOSAVE_PY' || echo "WARNING: mempalace-repair: could not check hooks.auto_save in $LINK/config.json (#744)" >&2
import json
import sys

config_file = sys.argv[1]
try:
    with open(config_file, encoding="utf-8") as handle:
        config = json.load(handle)
except (OSError, ValueError) as exc:
    print(f"WARNING: cannot read {config_file} ({exc}); cannot check hooks.auto_save (#744)", file=sys.stderr)
    sys.exit(0)
if not isinstance(config, dict):
    print(f"WARNING: {config_file} is not a JSON object; cannot check hooks.auto_save (#744)", file=sys.stderr)
    sys.exit(0)

hooks = config.get("hooks")
auto_save = hooks.get("auto_save") if isinstance(hooks, dict) else None
# `is True` on purpose: 1 and "true" are not what the plugin tests for.
if auto_save is True:
    sys.exit(0)
if auto_save is None:
    print(
        f"WARNING: {config_file} does not set hooks.auto_save; mempalace's Stop/SessionEnd/PreCompact "
        "hooks save nothing and this container has no other memory. Set it to true (#744).",
        file=sys.stderr,
    )
else:
    print(
        # json.dumps, not repr: the reader is looking at a JSON file, and
        # `auto_save=False` names a value that does not appear in it.
        f"WARNING: {config_file} has hooks.auto_save={json.dumps(auto_save)}, not true; mempalace's "
        "Stop/SessionEnd/PreCompact hooks are inert and nothing is being saved. Set it to true (#744).",
        file=sys.stderr,
    )
MEMPALACE_AUTOSAVE_PY
else
    echo "WARNING: mempalace-repair: no config.json under $LINK, so hooks.auto_save cannot be checked; mempalace's save hooks may be inert (#744)" >&2
fi

# 4b. identity.txt - the L0 context `mempalace wake-up` reads, without which the
# agent wakes up with no idea which machine it is on. Seeded only when the file
# is absent entirely; an existing one is never read, rewritten or touched,
# however stale it looks, because it is the user's own text.
MEMPALACE_IDENTITY="$MOUNT/identity.txt"
if [ ! -e "$MEMPALACE_IDENTITY" ]; then
    if mkdir -p "$MOUNT" 2>/dev/null && printf '%s\n' \
        'agent: devcontainer-claude' \
        'role: Claude Code running in a devcontainer built by the personal-features feature.' \
        'memory: mempalace is the only memory here; native Claude auto-memory is disabled.' \
        'note: seeded by mempalace-repair because identity.txt was missing (#744). Edit freely - once this file exists it is never rewritten.' \
        > "$MEMPALACE_IDENTITY" 2>/dev/null; then
        echo "mempalace-repair: seeded $MEMPALACE_IDENTITY with agent id devcontainer-claude (#744)"
    else
        echo "WARNING: mempalace-repair: $MEMPALACE_IDENTITY is missing and could not be seeded; 'mempalace wake-up' will start with no identity (#744)" >&2
    fi
fi
MEMPALACE_REPAIR
chmod 0755 /usr/local/bin/mempalace-repair

echo "Reconciling the mempalace palace root for $_REMOTE_USER_HOME"
MEMPALACE_LINK_OWNER="$_REMOTE_USER" /usr/local/bin/mempalace-repair "$_REMOTE_USER_HOME"

# --- Claude Code integrations: MCP server + plugins (#486, #484, #723) -------
# sync-claude-mcp registers the odoo-mcp MCP server and the mempalace and
# odoo-dev plugins at user scope. Installed to /usr/local/bin and run at
# container-create time
# (postCreateCommand), NOT here: $CLAUDE_CONFIG_DIR is shadowed at runtime by the
# feature's bind mount of the host's ~/.claude, so a registration written during
# the image build is discarded the moment the mount goes live. That is why the
# previous build-time `claude mcp add` never showed up in the persisted config
# (#486) - the write landed in the image layer the mount then covered.
cat > /usr/local/bin/sync-claude-mcp << 'EOF'
#!/bin/sh
# sync-claude-mcp - register the feature-owned Claude Code integrations (the
# odoo-mcp MCP server and the mempalace and odoo-dev plugins) at user scope,
# idempotently.
#
# Runs from the feature's postCreateCommand, where $CLAUDE_CONFIG_DIR is the
# LIVE bind mount of the host's ~/.claude, so what it writes actually persists
# across rebuilds. Best-effort: a registration failure warns but must not fail
# container create. Failures are reported rather than swallowed (#486).
set -u

: "${CLAUDE_CONFIG_DIR:=/usr/local/share/claude-home}"
export CLAUDE_CONFIG_DIR

if ! command -v claude >/dev/null 2>&1; then
    echo "sync-claude-mcp: claude is not on PATH; nothing to register" >&2
    exit 0
fi

# The server registers under the name of its console script: odoo-mcp. The
# guard has to check that SAME name - it used to check `odoo-sdk`, a name
# nothing ever registers, so it could never match and the add re-ran on every
# provision, the exact opposite of the intended "skip if already there" (#486).
if [ -x /usr/local/bin/odoo-mcp ]; then
    if claude mcp get odoo-mcp >/dev/null 2>&1; then
        echo "sync-claude-mcp: MCP server 'odoo-mcp' is already registered"
    elif claude mcp add --scope user odoo-mcp odoo-mcp; then
        echo "sync-claude-mcp: registered MCP server 'odoo-mcp'"
    else
        echo "WARNING: sync-claude-mcp: failed to register the 'odoo-mcp' MCP server" >&2
    fi
elif claude mcp get odoo-mcp >/dev/null 2>&1; then
    # odoo-mcp is NOT installed in this container, but the bind-mounted
    # ~/.claude carries a registration written by another container (or an
    # older image built before #674 installed the toolchain unconditionally).
    # Left in place, Claude Code reports a confusing ENOENT for a binary that
    # was never installed here - so deregister it and keep the persisted state
    # self-consistent. The next container that does ship the binary re-adds it
    # via the branch above. --scope user matches what this script registers; a
    # project/local-scope entry is the user's own and the remove then fails
    # into the warning below instead of touching it.
    if claude mcp remove --scope user odoo-mcp; then
        echo "sync-claude-mcp: removed stale 'odoo-mcp' MCP registration (odoo-mcp is not installed in this container, #674)"
    else
        echo "WARNING: sync-claude-mcp: 'odoo-mcp' is registered but not installed in this container, and deregistering it failed; 'claude mcp list' will report ENOENT for it" >&2
    fi
fi

# mempalace ships its Claude Code hooks (Stop/SessionEnd/PreCompact) as a
# plugin; without this registration the palace is installed and on PATH but
# nothing ever mines into it (#484).
#
# #484 flagged "does `claude plugin install` work non-interactively at build
# time?" as unverified. It now IS verified, and the answer is: only if a
# marketplace publishing mempalace is already configured. On a host whose
# ~/.claude has one (the mount makes it available here), this installs; on a
# bare CI runner it fails with "not found in any configured marketplace". That
# is a host-config precondition this Feature cannot satisfy for the user, so it
# stays best-effort and says so rather than failing container create.
mempalace_plugin_present=0
if command -v mempalace >/dev/null 2>&1; then
    if claude plugin list 2>/dev/null | grep -q mempalace; then
        echo "sync-claude-mcp: plugin 'mempalace' is already installed"
        mempalace_plugin_present=1
    elif claude plugin install --scope user mempalace; then
        echo "sync-claude-mcp: installed plugin 'mempalace'"
        mempalace_plugin_present=1
    else
        echo "WARNING: sync-claude-mcp: could not install the 'mempalace' plugin; its marketplace is not configured in \$CLAUDE_CONFIG_DIR ($CLAUDE_CONFIG_DIR). Add it with 'claude plugin marketplace add <repo>' on the host, or the mempalace hooks will not run." >&2
    fi
fi

# "already installed" above is satisfied by ANY cached copy, however old (#741).
# $CLAUDE_CONFIG_DIR is the host's ~/.claude bind mount, so a container seeded
# from a host that installed the plugin months ago keeps running that stale
# revision forever - the install branch never fires, and nothing else ever
# refreshes it. Converge it here on every container create.
#
# Strictly best-effort, and louder than the install above would justify being:
# the plugin already works at whatever revision is cached, so a failure here
# (offline runner, marketplace unreachable, a revision upstream withdrew) costs
# freshness, not function. Never let it fail container create - the guard keeps
# a nonzero `plugin update` off this script's exit status.
if [ "$mempalace_plugin_present" -eq 1 ]; then
    if claude plugin update mempalace@mempalace; then
        echo "sync-claude-mcp: plugin 'mempalace' is up to date"
    else
        echo "WARNING: sync-claude-mcp: could not update the 'mempalace' plugin; this container keeps whatever revision was already cached in \$CLAUDE_CONFIG_DIR ($CLAUDE_CONFIG_DIR), which may be stale. Check network/marketplace access, or run 'claude plugin update mempalace@mempalace' by hand." >&2
    fi
fi

# --- odoo-dev plugin: marketplace + install (#723) ---------------------------
# The odoo-dev consulting plugin ships from THIS repo's own marketplace
# (Crpaxton4/devcontainer-features). It replaces the five loose skills the
# feature used to copy into $CLAUDE_CONFIG_DIR/skills (#701-#708): the plugin
# carries them now, so the feature no longer ships or syncs loose skills at all
# (#738) - a loose copy would load alongside and shadow its plugin twin. Copies
# written by pre-migration containers persist in the bind-mounted ~/.claude
# though, so this block also deletes them once the plugin is in place (below).
# Same best-effort stance as the mempalace block above: the
# marketplace add clones from GitHub (auth rides in on the persisted
# ~/.config/gh mount), so missing auth/network warns and the next container
# create converges - it never fails container create.
#
# Migration first: the plugin was previously published from the standalone
# Crpaxton4/odoo-dev-claude-plugin repo. A persisted ~/.claude may still carry
# a marketplace sourced from that repo; installing from BOTH sources would put
# two odoo-dev plugins side by side. Match on the marketplace's SOURCE (its
# repo/URL), never on its name - a marketplace that merely reuses the name
# 'odoo-dev' but points elsewhere is the user's own and must be left alone.
# `claude plugin marketplace list --json` emits a list of objects with 'name'
# plus source fields ('repo' for GitHub sources, a URL/path otherwise); parse
# it defensively - any unexpected shape means "no match", never a failure.
# python3 is already a hard dependency of this feature (see mempalace-repair).
old_marketplace=""
if command -v python3 >/dev/null 2>&1; then
    old_marketplace="$(claude plugin marketplace list --json 2>/dev/null | python3 -c '
import json, sys
try:
    entries = json.load(sys.stdin)
except Exception:
    entries = []
for entry in entries if isinstance(entries, list) else []:
    if not isinstance(entry, dict):
        continue
    source = " ".join(str(entry.get(key, "")) for key in ("repo", "url", "source", "path"))
    if "odoo-dev-claude-plugin" in source and entry.get("name"):
        print(entry["name"])
        break
' 2>/dev/null)" || old_marketplace=""
fi
if [ -n "$old_marketplace" ]; then
    echo "sync-claude-mcp: marketplace '$old_marketplace' is sourced from the retired odoo-dev-claude-plugin repo; migrating the 'odoo-dev' plugin to the 'devcontainer-features' marketplace (#723)"
    if claude plugin uninstall odoo-dev >/dev/null 2>&1; then
        echo "sync-claude-mcp: uninstalled plugin 'odoo-dev' (old source; reinstalled from 'devcontainer-features' below)"
    fi
    if claude plugin marketplace remove "$old_marketplace"; then
        echo "sync-claude-mcp: removed retired marketplace '$old_marketplace'"
    else
        echo "WARNING: sync-claude-mcp: failed to remove the retired marketplace '$old_marketplace'; remove it by hand with 'claude plugin marketplace remove $old_marketplace'" >&2
    fi
fi

# Idempotent marketplace add. The name to check comes from this repo's
# .claude-plugin/marketplace.json ("devcontainer-features").
if claude plugin marketplace list --json 2>/dev/null | grep -q '"name"[[:space:]]*:[[:space:]]*"devcontainer-features"'; then
    echo "sync-claude-mcp: marketplace 'devcontainer-features' is already configured"
elif claude plugin marketplace add Crpaxton4/devcontainer-features; then
    echo "sync-claude-mcp: added marketplace 'devcontainer-features' (Crpaxton4/devcontainer-features)"
else
    echo "WARNING: sync-claude-mcp: could not add the 'devcontainer-features' marketplace (no GitHub auth/network in this container?). The odoo-dev plugin was not installed; add it later with 'claude plugin marketplace add Crpaxton4/devcontainer-features' - the next container create will also retry." >&2
fi

# Idempotent plugin install, pinned to this repo's marketplace via the
# plugin@marketplace form so a same-named plugin from another marketplace can
# neither satisfy nor break this install.
odoo_dev_installed=0
if claude plugin list 2>/dev/null | grep -q 'odoo-dev@devcontainer-features'; then
    echo "sync-claude-mcp: plugin 'odoo-dev@devcontainer-features' is already installed"
    odoo_dev_installed=1
elif claude plugin install --scope user odoo-dev@devcontainer-features; then
    echo "sync-claude-mcp: installed plugin 'odoo-dev@devcontainer-features'"
    odoo_dev_installed=1
else
    echo "WARNING: sync-claude-mcp: could not install the 'odoo-dev' plugin; install it later with 'claude plugin install --scope user odoo-dev@devcontainer-features' - the next container create will also retry." >&2
fi

# --- provision marker: make "these scripts are old" a visible state (#806) ---
# Everything above reports only what it DID. A feature script too old to carry a
# step reports nothing at all - and the container-create log it would have
# reported into is long gone by the time anyone wonders - so a container still
# running pre-#723 scripts is indistinguishable from one where that migration
# ran and succeeded. That, not the migration, was the defect in #806.
#
# Nothing baked into the image can close the gap by itself: a checker shipped in
# the image is exactly as absent from an old image as the step it would check.
# The only state that outlives the image is $CLAUDE_CONFIG_DIR - the host's
# bind-mounted ~/.claude, shared by every container this machine builds - so the
# provenance of THIS container's feature scripts is recorded there, and compared
# against the newest set that ever provisioned this config dir. An image older
# than one already seen here then says so, on every container create, instead of
# looking healthy while running retired behaviour.
#
# Deliberately NOT odoo-dev-specific: it fingerprints the feature-owned scripts
# themselves, so every step any of them ever gains is covered by the same
# marker - no per-migration assertion to remember to add. The `claude` wrapper
# is fingerprinted alongside them (#807) for exactly that reason: a wrapper too
# old to deliver the standing rules is the same defect wearing a different hat,
# and it announces itself here rather than needing its own breadcrumb. Adds no
# mount and no containerEnv var; the marker lives inside a directory the feature
# already mounts. Best-effort like the rest of this script: a marker that cannot be read
# or written warns, and the run still exits 0.
pf_marker="$CLAUDE_CONFIG_DIR/personal-features-provision.json"
# One fingerprinted path per line. A feature that adds a script appends its own
# line here and touches nothing else, so two such features do not land on the
# same line of the same file (#868).
pf_scripts=""
pf_add_script() {
    pf_scripts="${pf_scripts:+$pf_scripts }$1"
}
pf_add_script /usr/local/bin/sync-claude-mcp
pf_add_script /usr/local/bin/sync-claude-hooks
pf_add_script /usr/local/bin/claude-event-hook
pf_add_script /usr/local/bin/mempalace-repair
pf_add_script /usr/local/bin/resolve-mempal-dir
pf_add_script /usr/local/bin/create-pr
pf_add_script /usr/local/bin/gh-as-owner
pf_add_script /usr/local/bin/publish-claude-wrapper
pf_add_script /usr/local/share/personal-features/claude-wrapper
if command -v python3 >/dev/null 2>&1; then
    PF_MARKER="$pf_marker" PF_SCRIPTS="$pf_scripts" python3 -c '
import hashlib, json, os, sys, time

marker = os.environ["PF_MARKER"]
paths = os.environ["PF_SCRIPTS"].split()

# Fingerprint = content hash per script, plus the newest mtime across them. The
# hash answers "is this the same build"; the mtime answers "which build is
# older". Both are needed: a rebuild of unchanged scripts moves the mtime
# forward without changing behaviour, and must not be reported as drift.
scripts = {}
epoch = 0
for path in paths:
    try:
        with open(path, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        mtime = int(os.stat(path).st_mtime)
    except OSError:
        continue
    scripts[os.path.basename(path)] = digest
    epoch = max(epoch, mtime)

combined = hashlib.sha256(
    "".join("%s:%s\n" % (name, scripts[name]) for name in sorted(scripts)).encode()
).hexdigest()


def iso(value):
    if not value:
        return "unknown"
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value))


def as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# Any unexpected shape means "no previous marker", never a failure - same
# defensive stance as the marketplace parse above.
previous = {}
try:
    with open(marker) as handle:
        loaded = json.load(handle)
    if isinstance(loaded, dict):
        previous = loaded
except Exception:
    previous = {}

# High-water mark, carried forward separately from the current run, so a stale
# container writing its own (older) provenance cannot erase the evidence that
# something newer was here.
seen_epoch = as_int(previous.get("newest_script_epoch"))
seen_digest = previous.get("newest_script_digest")
seen_at = previous.get("newest_seen_at") or "unknown"
now = int(time.time())

# 60s of slack absorbs filesystem timestamp granularity; the digest test keeps
# a same-content rebuild quiet.
stale = bool(scripts) and (seen_epoch - epoch) > 60 and seen_digest != combined
if not stale:
    seen_epoch, seen_digest, seen_at = max(seen_epoch, epoch), combined, iso(now)

# This marker is SHARED state, not a file private to this script: the runtime
# hook heartbeat (#804) is stamped into it between provisions, and other writers
# keep their own keys here rather than each inventing a breadcrumb file. The
# document is rewritten on every provision, so the default has to be PRESERVE:
# start from what was already there and overwrite only the fields below, which
# this provision run owns. A key this script does not know about belongs to
# another writer; carrying it forward costs nothing, whereas dropping it makes
# the reader of that key say "never seen" when the truth is "erased" - the exact
# failure mode this marker exists to make visible (#868). Adding a key elsewhere
# therefore needs no edit here. Nothing is dropped: no key has ever needed
# resetting, and a reset belongs next to the writer that owns the key.
#
# The owned fields all describe the scripts in THIS container, so none of them
# may be inherited stale from the previous marker - that would be a lie about
# the current image and would defeat the staleness check (#806). The three
# newest_* high-water-mark fields are owned too: they are computed from the
# previous marker above, deliberately, so a stale run cannot lower them.
record = dict(previous)
record.update(
    {
        "schema": 1,
        "issue": "806",
        "provisioned_at": iso(now),
        "script_epoch": epoch,
        "script_digest": combined,
        "scripts": scripts,
        "newest_script_epoch": seen_epoch,
        "newest_script_digest": seen_digest,
        "newest_seen_at": seen_at,
        "stale_image": stale,
    }
)

# The hub liveness record (#898) is one such foreign key: mempalace-hub-up writes
# `mempalace_hub` into this same marker from postStartCommand, and
# `record = dict(previous)` above carries it through every provision without
# naming it here - which is exactly the property #868 made general.

if stale:
    sys.stderr.write(
        "WARNING: sync-claude-mcp: the personal-features scripts in this container are OLDER than the newest set "
        "that ever provisioned %s. This image was built %s; a container sharing this config dir ran scripts built %s. "
        "Steps this repo added in between are simply absent here - they emit nothing, so the container looks healthy "
        "while running retired behaviour (#806). Rebuild the container without cache to converge. Evidence: %s\n"
        % (os.path.dirname(marker) or marker, iso(epoch), iso(seen_epoch), marker)
    )

try:
    if os.path.dirname(marker):
        os.makedirs(os.path.dirname(marker), exist_ok=True)
    tmp = marker + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)
        handle.write("\n")
    try:
        # Same reasoning as the mempal-dir env file: install.sh cannot know which
        # uid runs postCreateCommand, and the marker holds no secret.
        os.chmod(tmp, 0o666)
    except OSError:
        pass
    os.replace(tmp, marker)
except OSError as exc:
    sys.stderr.write(
        "WARNING: sync-claude-mcp: could not write the provision marker %s (%s); a container running stale feature "
        "scripts cannot be told apart from a current one here (#806)\n" % (marker, exc)
    )
    raise SystemExit(0)

sys.stdout.write(
    "sync-claude-mcp: recorded feature-script provenance in %s (built %s, digest %s)\n"
    % (marker, iso(epoch), combined[:12])
)
' || echo "WARNING: sync-claude-mcp: the provision marker at $pf_marker could not be reconciled; a container running stale feature scripts cannot be told apart from a current one here (#806)" >&2
else
    echo "WARNING: sync-claude-mcp: python3 is not on PATH, so no provision marker was recorded at $pf_marker; a container running stale feature scripts cannot be told apart from a current one here (#806)" >&2
fi

# Stale loose skill copies, left behind by pre-migration containers (#738).
# These six names used to be copied into $CLAUDE_CONFIG_DIR/skills by the
# feature's retired sync-claude-skills: five moved into the odoo-dev plugin
# (#695-#699) and client-status-report was retired outright (#700). The
# directory is a host bind mount, so the copies outlive the image that wrote
# them - the five load as PERSONAL skills alongside their odoo-dev twins, two
# near-identical descriptions competing for the same triggers, and the sixth
# keeps offering a playbook nobody maintains. Nothing recreates any of them
# now, so deleting them is the fix (the same fix
# plugins/odoo-dev/scripts/check-stray-skills.sh reports but does not apply).
#
# Only ever with the plugin actually in place, so a failed install never leaves
# the machine with neither copy. "Stray" is defined exactly as
# check-stray-skills.sh defines it - a directory carrying a SKILL.md - and the
# name list is literal: anything not on it, including every user-authored
# skill, is untouched. Best-effort like the rest of this script: a removal that
# fails warns and the run still exits 0.
#
# The list is split into the same two groups check-stray-skills.sh reports
# (#778), because the two used to disagree: that script carried only the five
# plugin-shadowed names while this loop deleted six, so client-status-report was
# removed here by a script that never mentioned it and the report could go quiet
# while this still had work to do. The two lists are now gated against each
# other by .github/scripts/test_stray_skill_parity.py - edit one, edit both.
#
# stale_shadowed: moved into the odoo-dev plugin, which still ships them.
#   Mirrors odoo_sdk.skills.PACKAGED_SKILL_NAMES, the sources those copies are
#   generated from.
# stale_retired:  retired outright (#700) with no plugin twin and no
#   replacement. Still feature-seeded debris, so still deleted here - it simply
#   has nothing to fall back on, which is why it is named separately.
#
# Deliberately absent from both: ingest, lint, llm-wiki-workspace, process and
# query. They show up beside these in $CLAUDE_CONFIG_DIR/skills but this feature
# never seeded them and no plugin ships them - they are the user's own personal
# skills, and `rm -rf` on a name this feature does not own is data loss.
stale_shadowed="discovery-notes fibonacci-estimate odoo-code-review odoo-design-doc odoo-quote"
stale_retired="client-status-report"
if [ "$odoo_dev_installed" -eq 1 ]; then
    for stale_skill in $stale_shadowed $stale_retired; do
        stale_dir="$CLAUDE_CONFIG_DIR/skills/$stale_skill"
        [ -f "$stale_dir/SKILL.md" ] || continue
        if rm -rf "$stale_dir"; then
            echo "sync-claude-mcp: removed stale loose skill '$stale_skill' from $CLAUDE_CONFIG_DIR/skills (this feature no longer ships loose skills, #738)"
        else
            echo "WARNING: sync-claude-mcp: could not remove the stale loose skill copy at $stale_dir; it keeps loading as a personal skill until you delete it by hand" >&2
        fi
    done
fi

# Every step above is best-effort and must never fail container create, so the
# script's own exit status is fixed rather than inherited from the last one.
exit 0
EOF
chmod 0755 /usr/local/bin/sync-claude-mcp

# --- mempalace mine root (#485, #484) ---------------------------------------
# MEMPAL_DIR tells mempalace which project tree to mine. A Feature CANNOT know
# that path at build time: the spec hands install.sh only _REMOTE_USER /
# _CONTAINER_USER / _*_USER_HOME, and offers no ${containerWorkspaceFolder}
# substitution in containerEnv. The old hardcoded containerEnv MEMPAL_DIR=
# /workspaces was therefore a guess that is wrong for every container that
# mounts its project elsewhere - and mempalace treats an unresolvable MEMPAL_DIR
# as "mine nothing", with no diagnostic, so the failure looked exactly like
# success (transcript capture is independent of it and kept working).
#
# Lifecycle commands DO execute from the workspace folder, so resolve it there
# instead and persist the answer into an env file every shell sources (below).
MEMPAL_ENV_FILE=/usr/local/share/personal-features/mempal-dir.sh
mkdir -p "$(dirname "$MEMPAL_ENV_FILE")"
# Pre-create it here and make it writable by ANY uid in the container, for the
# same reason shell-history is mode 0777 (see persisted-paths.tsv): install.sh
# cannot know which user will run postCreateCommand. $_REMOTE_USER is the
# feature's best guess, but the dev container CLI runs lifecycle commands as the
# image's remoteUser, which is frequently a different account (root at build
# time vs `node` at create time on the javascript-node base image) - a chown to
# the wrong one makes the resolver's write fail and, because it fails loudly by
# design, takes container create down with it. The file holds one directory
# path, no secret. Only the file needs to be writable: `>` truncates in place
# and never needs write permission on the parent directory.
: > "$MEMPAL_ENV_FILE"
chmod 0666 "$MEMPAL_ENV_FILE"

cat > /usr/local/bin/resolve-mempal-dir << 'EOF'
#!/bin/sh
# resolve-mempal-dir - resolve the mempalace mine root and persist it as
# MEMPAL_DIR for every shell in this container.
#
# Run from the feature's postCreateCommand, whose cwd is the workspace folder.
# Resolution order: an explicit MEMPAL_DIR override, else the enclosing git
# worktree root, else the workspace folder itself.
#
# Deliberately FAILS LOUDLY (non-zero, with an actionable message) when the mine
# root cannot be resolved. The whole point of this script is that the previous
# behaviour - a wrong path that silently mined nothing - was indistinguishable
# from a working install (#485).
set -u

ENV_FILE=/usr/local/share/personal-features/mempal-dir.sh
WORKSPACE="${1:-$PWD}"

if [ -n "${MEMPAL_DIR:-}" ] && [ -d "$MEMPAL_DIR" ]; then
    # An explicit, resolvable override always wins - that is what it is for.
    RESOLVED="$MEMPAL_DIR"
else
    RESOLVED="$(git -C "$WORKSPACE" rev-parse --show-toplevel 2>/dev/null || true)"
    [ -n "$RESOLVED" ] || RESOLVED="$WORKSPACE"
fi

# A single quote in the path would break the quoting of the env file written
# below, so refuse it explicitly rather than emitting a file that fails to
# source (which would once again be a silent no-op).
case "$RESOLVED" in *\'*) RESOLVED="" ;; esac

if [ -z "$RESOLVED" ] || [ ! -d "$RESOLVED" ] || [ "$RESOLVED" = "/" ]; then
    echo "ERROR: resolve-mempal-dir could not resolve a mempalace mine root." >&2
    echo "  MEMPAL_DIR=${MEMPAL_DIR:-<unset>}" >&2
    echo "  workspace=$WORKSPACE" >&2
    echo "  resolved=${RESOLVED:-<empty>} (not a usable directory)" >&2
    echo "Set MEMPAL_DIR to the project directory mempalace should mine, or run this from inside the workspace folder. Failing instead of mining nothing silently." >&2
    exit 1
fi

# printf keeps this safe for paths containing spaces; single quotes are not
# expanded by the sourcing shell (a path containing one was rejected above).
# An unwritable env file is fatal for the same reason a wrong path is: shells
# would keep sourcing the stale/empty value and mine nothing, quietly.
if ! printf "export MEMPAL_DIR='%s'\n" "$RESOLVED" > "$ENV_FILE"; then
    echo "ERROR: resolve-mempal-dir resolved MEMPAL_DIR=$RESOLVED but could not write $ENV_FILE." >&2
    echo "Without it no shell picks the value up and mempalace mines nothing. install.sh pre-creates the file mode 0666 precisely so any lifecycle user can write it; check that it still exists and is writable." >&2
    exit 1
fi
echo "resolve-mempal-dir: MEMPAL_DIR=$RESOLVED"
EOF
chmod 0755 /usr/local/bin/resolve-mempal-dir

# --- mempalace workspace init (run-once) -------------------------------------
# `mempalace init` is what gives a project real room decomposition: the `rooms`
# list it writes into <project>/mempalace.yaml is what the miner routes files by.
# Without it every mined file lands in a single `general` room. #643 made init's
# project-local artifacts ignorable machine-wide (core.excludesfile above), which
# removed the reason NOT to run it - but nothing actually ran it, so every
# container still mined into the flat fallback.
#
# RUN-ONCE, NEVER CLOBBER. Re-running init is overwrite, not merge: save_config()
# in room_detector_local.py rebuilds mempalace.yaml from freshly detected rooms
# and writes it with mode "w", and cmd_init writes entities.json the same way.
# Any hand-tuned room name, description or keyword list would be destroyed. So
# this inits only when mempalace.yaml is ABSENT; once the file exists it is the
# user's to curate, and re-detecting is an explicit `rm mempalace.yaml` away.
# That is also why --auto-mine is NOT passed: the plugin's own hooks already
# drive mining, and adding it here would mine twice on every container create.
cat > /usr/local/bin/mempalace-init-workspace << 'MEMPALACE_INIT_WORKSPACE'
#!/bin/sh
set -u
# mempalace-init-workspace [DIR] - give the workspace repo a room structure,
# once, without ever overwriting one that already exists.
#
# DIR defaults to the MEMPAL_DIR that resolve-mempal-dir persisted, which is
# already "the enclosing git worktree root, else the workspace folder" - exactly
# the primary workspace repo - so the resolution logic is not duplicated here.
# Set MEMPALACE_SKIP_INIT=1 to opt out entirely. MEMPALACE_INIT_CMD overrides the
# binary and MEMPALACE_INIT_TIMEOUT the backstop, so the feature test can drive
# the guards against a stub.

ENV_FILE=/usr/local/share/personal-features/mempal-dir.sh
INIT_CMD="${MEMPALACE_INIT_CMD:-mempalace}"
INIT_TIMEOUT="${MEMPALACE_INIT_TIMEOUT:-300}"

if [ -n "${MEMPALACE_SKIP_INIT:-}" ]; then
    echo "mempalace-init-workspace: MEMPALACE_SKIP_INIT is set, skipping"
    exit 0
fi

TARGET="${1:-}"
if [ -z "$TARGET" ]; then
    # An explicit MEMPAL_DIR already in the environment wins over the persisted
    # file, matching resolve-mempal-dir's own precedence ("an explicit,
    # resolvable override always wins"). Sourcing unconditionally would let the
    # generated file silently beat the override, inverting that contract.
    if [ -z "${MEMPAL_DIR:-}" ] && [ -f "$ENV_FILE" ]; then
        # shellcheck source=/dev/null  # generated at container-create time by resolve-mempal-dir
        . "$ENV_FILE"
    fi
    TARGET="${MEMPAL_DIR:-}"
fi

# Unlike resolve-mempal-dir, every failure below is a WARNING, not a hard error.
# An unresolvable MEMPAL_DIR means mining nothing at all, which must fail loudly
# (#485); a missing room structure only means mining into the `general` fallback,
# which still works. Blocking container creation over an enhancement is the wrong
# trade, so this never exits non-zero.
if [ -z "$TARGET" ] || [ ! -d "$TARGET" ]; then
    echo "WARNING: mempalace-init-workspace: no usable mine root (MEMPAL_DIR=${TARGET:-<unset>}); skipping room detection, mining will use the flat 'general' fallback" >&2
    exit 0
fi

if ! command -v "$INIT_CMD" >/dev/null 2>&1; then
    echo "WARNING: mempalace-init-workspace: '$INIT_CMD' is not on PATH (its install is best-effort); skipping room detection" >&2
    exit 0
fi

if [ -e "$TARGET/mempalace.yaml" ]; then
    echo "mempalace-init-workspace: $TARGET/mempalace.yaml already exists, leaving it untouched"
    exit 0
fi

# `mempalace init` appends its own two-line block to <repo>/.gitignore (upstream
# issue #185, cli.py _ensure_mempalace_files_gitignored). That is exactly the
# per-repo dirtying the machine-wide core.excludesfile (#643) exists to avoid:
# the same two names are already ignored globally, so the append is redundant
# here AND leaves an uncommitted diff in the user's workspace on every fresh
# container. Snapshot .gitignore and put it back afterwards.
GITIGNORE="$TARGET/.gitignore"
GI_BACKUP=""
if [ -f "$GITIGNORE" ]; then
    GI_BACKUP="$(mktemp 2>/dev/null || echo '')"
    [ -n "$GI_BACKUP" ] && cp -p "$GITIGNORE" "$GI_BACKUP"
fi

# restore_gitignore(): undo init's append. When .gitignore existed, restore the
# exact bytes. When init CREATED it, delete it - but only after confirming every
# meaningful line is one mempalace put there, so a file some other process wrote
# concurrently is never destroyed.
restore_gitignore() {
    if [ -n "$GI_BACKUP" ] && [ -f "$GI_BACKUP" ]; then
        if ! cmp -s "$GI_BACKUP" "$GITIGNORE" 2>/dev/null; then
            cp -p "$GI_BACKUP" "$GITIGNORE" \
                && echo "mempalace-init-workspace: reverted init's .gitignore append (already covered by core.excludesfile, #643)"
        fi
        rm -f "$GI_BACKUP"
    elif [ -f "$GITIGNORE" ]; then
        if [ -z "$(grep -vE '^[[:space:]]*($|#|mempalace\.yaml$|entities\.json$)' "$GITIGNORE")" ]; then
            rm -f "$GITIGNORE" \
                && echo "mempalace-init-workspace: removed the .gitignore init created (already covered by core.excludesfile, #643)"
        else
            echo "WARNING: mempalace-init-workspace: $GITIGNORE gained unexpected content during init; leaving it untouched" >&2
        fi
    fi
}

# HEADLESS CONTRACT. Three independent guards, because upstream has narrowed
# --yes before (issue #179) and scopes it deliberately narrowly today:
#
#   --yes      bypasses the entity-confirmation AND room-approval prompts
#              (entity_detector.confirm_entities / room_detector_local
#              .get_user_approval). Neither guards EOFError, so --yes is what
#              keeps them from raising on a closed stdin.
#   --no-llm   skips provider acquisition and the external-LLM consent gate
#              entirely; init defaults to an Ollama provider that is not running
#              here.
#   < /dev/null  the post-init "Mine this directory now? [Y/n]" prompt is NOT
#              covered by --yes (upstream scopes --yes to entity auto-accept and
#              says so in _maybe_run_mine_after_init's docstring). It does catch
#              EOFError and treats it as decline, so an immediately-EOF stdin
#              answers it deterministically.
#
# Deliberately NOT `yes | mempalace init ...`. Verified against MemPalace 3.7.1:
# a `yes` pipe answers "y" to that mine prompt and runs a full synchronous mine
# inside postCreateCommand - minutes on a real corpus, and duplicated work since
# the plugin's own hooks already mine. Worse, `yes` never closes the pipe, so if
# any prompt ever escapes --yes again the "Name (or enter to stop):" loop in
# entity_detector consumes "y" forever and never terminates - reproduced here as
# a runaway that had to be killed. EOF is the only input that cannot say yes to
# something expensive and cannot fail to terminate a loop.
#
# `timeout` is the backstop for whatever this analysis missed: a wedged init
# degrades to a warning instead of hanging container creation. Absent on a
# minimal image, so it is used only when present.
echo "mempalace-init-workspace: detecting rooms for $TARGET"
if command -v timeout >/dev/null 2>&1; then
    set -- timeout "$INIT_TIMEOUT" "$INIT_CMD" init "$TARGET" --yes --no-llm
else
    set -- "$INIT_CMD" init "$TARGET" --yes --no-llm
fi

if "$@" < /dev/null; then
    restore_gitignore
    echo "mempalace-init-workspace: wrote $TARGET/mempalace.yaml"
else
    rc=$?
    restore_gitignore
    if [ "$rc" = 124 ]; then
        echo "WARNING: mempalace-init-workspace: '$INIT_CMD init' exceeded ${INIT_TIMEOUT}s and was killed; mining will use the flat 'general' fallback" >&2
    else
        echo "WARNING: mempalace-init-workspace: '$INIT_CMD init' failed (exit $rc); mining will use the flat 'general' fallback" >&2
    fi
fi
exit 0
MEMPALACE_INIT_WORKSPACE
chmod 0755 /usr/local/bin/mempalace-init-workspace

# --- the shared mempalace hub container (#898, #897, #921) -------------------
# mempalace hands the MCP writer lease to exactly one process per palace, and
# the palace is a HOST bind mount that every devcontainer on this machine sees.
# So the lock that arbitrates that lease is host-global, arbitrated by the host
# kernel across containers - while the per-container hub this replaced bound and
# health-checked container-local loopback. The second container to start was
# refused the flock, spent its whole restart budget in ~45s on a condition no
# retry can change, and gave up; every session in it then served its own palace
# copy and only the first to mutate could write (#764 reinstated). The lock is
# never reclaimed either: mempalace's own reaper skips `mine_palace_*.lock` by
# design (#897), and the refusal message names a PID from another namespace, so
# it misdirects even when it is read.
#
# The fix is one hub for the machine, not one per container: a long-lived
# sibling container on the HOST daemon, started over the mounted Docker socket
# from postStartCommand, that every devcontainer shares. The Feature therefore
# hard-depends on docker-outside-of-docker - it is the socket, the CLI and the
# group membership in one place, and this repo's own .devcontainer moved off
# docker-in-docker for it, because a nested daemon's socket would give each
# devcontainer its own private "shared" hub and reproduce the bug one level up.
#
# NO PLUGIN CHANGE AND NO MCP REGISTRATION. The existing stdio proxy in each
# container discovers the hub through the shared mount, unmodified. Verified
# against the installed mempalace 3.9.0 source rather than the docs:
#   * server_registry.write_serverinfo stores the `--host` VALUE VERBATIM, and
#     client_base_url only rewrites it to loopback for the wildcard binds
#     (_WILDCARD_HOSTS = 0.0.0.0, ::, [::]). Binding `--host mempalace-hub`
#     therefore publishes a name every sibling container can dial, which is
#     exactly what binding 0.0.0.0 could not do.
#   * read_live_serverinfo gates on _pid_alive, which is os.kill(pid, 0). The
#     server is PID 1 in its own container (sh execs python, cli.cmd_serve then
#     os.execve's the real server, so the pid never changes), and PID 1 exists in
#     every namespace - so the check that was a cross-namespace false negative is
#     now always true, for the right reason rather than by luck.
#   * server_state_dir is keyed by sha256 of the CANONICAL palace path and rooted
#     at $HOME/.mempalace, so the hub is given the palace at the same absolute
#     path the containers use and a $HOME whose .mempalace resolves onto the same
#     mount. Token and serverinfo.json then land in the one directory every
#     container already reads.
#   * cmd_serve auto-generates the bearer token 0600 into that directory for any
#     non-loopback bind, and load_server_tokens reads it from the same place. The
#     credential distributes itself over the mount; nothing has to carry it.
#
# The liveness record keeps the key and the merge semantics the per-container
# supervisor used - `mempalace_hub` in the #806 provision marker - so #921's
# overwrite is gone with the two racing timers that caused it: there is one
# writer of that key now, and it writes a terminal state per run.
cat > /usr/local/bin/mempalace-hub-up << 'MEMPALACE_HUB_UP'
#!/bin/sh
set -u
# mempalace-hub-up [up|status|down|logs] - reconcile the ONE mempalace hub
# container this host shares between every devcontainer (#898).
#
# `up` (the default, and what postStartCommand runs) is idempotent and NEVER
# FATAL: every path - including every failure - records a state into the
# provision marker and exits 0. A container with no hub is degraded, not broken,
# and is not worth failing container start over.
#
# States, in the order the reconcile can reach them:
#   disabled        MEMPALACE_SKIP_HUB is set
#   no_docker       no docker binary (docker-outside-of-docker not installed)
#   no_socket       no /var/run/docker.sock, or the daemon does not answer
#   self_not_found  cannot identify this container, or the palace mount's host path
#   network_failed  the shared network could not be created or joined
#   pull_failed     the hub image could not be pulled, or `docker run` failed
#   lock_held       something else holds the palace lock; a hub would be refused
#   starting        the hub container is up but has not answered /healthz yet
#   live            the hub answers /healthz
#   stopped         stopped by `mempalace-hub-up down`
#
# Overrides, for the feature test and for debugging:
#   MEMPALACE_SKIP_HUB           do not touch a hub at all
#   MEMPALACE_HUB_NAME           hub container name    (default: mempalace-hub)
#   MEMPALACE_HUB_NETWORK        shared network name   (default: mempalace)
#   MEMPALACE_HUB_PORT           hub port              (default: 8765)
#   MEMPALACE_HUB_MOUNT          palace mount, in-container (default: /usr/local/share/mempalace)
#   MEMPALACE_HUB_PALACE         palace path           (default: <mount>/palace)
#   MEMPALACE_HUB_IMAGE          hub image             (default: from the env file)
#   MEMPALACE_HUB_DOCKER         docker binary         (default: docker)
#   MEMPALACE_HUB_SOCKET         daemon socket         (default: /var/run/docker.sock)
#   MEMPALACE_HUB_REQUIREMENTS   frozen dependency set install.sh wrote
#   MEMPALACE_HUB_ENV_FILE       version/image record install.sh wrote
#   MEMPALACE_HUB_WAIT           seconds to wait for /healthz (default: 60)
#   MEMPALACE_HUB_MARKER         liveness record (default: the #806 provision marker)

HUB="${MEMPALACE_HUB_NAME:-mempalace-hub}"
NET="${MEMPALACE_HUB_NETWORK:-mempalace}"
PORT="${MEMPALACE_HUB_PORT:-8765}"
MOUNT="${MEMPALACE_HUB_MOUNT:-/usr/local/share/mempalace}"
PALACE="${MEMPALACE_HUB_PALACE:-$MOUNT/palace}"
SHARE="${MEMPALACE_HUB_SHARE_DIR:-/usr/local/share/personal-features}"
REQ="${MEMPALACE_HUB_REQUIREMENTS:-$SHARE/mempalace-hub-requirements.txt}"
ENV_FILE="${MEMPALACE_HUB_ENV_FILE:-$SHARE/mempalace-hub.env}"
DOCKER="${MEMPALACE_HUB_DOCKER:-docker}"
SOCKET="${MEMPALACE_HUB_SOCKET:-/var/run/docker.sock}"
WAIT="${MEMPALACE_HUB_WAIT:-60}"
URL="http://$HUB:$PORT"

# The liveness record shares the provision marker #806 already writes to the
# host-persisted $CLAUDE_CONFIG_DIR, rather than adding a second breadcrumb.
MARKER="${MEMPALACE_HUB_MARKER:-${CLAUDE_CONFIG_DIR:-/usr/local/share/claude-home}/personal-features-provision.json}"

# The image install.sh recorded alongside the frozen requirements, so the pin
# lives in one place. An exported MEMPALACE_HUB_IMAGE still wins over the file.
_image_override="${MEMPALACE_HUB_IMAGE:-}"
if [ -r "$ENV_FILE" ]; then
    # shellcheck source=/dev/null
    . "$ENV_FILE"
fi
IMAGE="${_image_override:-${MEMPALACE_HUB_IMAGE:-python:3.11-slim}}"

# Where the requirements file has to be for the HOST daemon to bind-mount it:
# on the shared palace mount, whose host path we resolve below. The daemon
# cannot see any path that exists only inside this container.
HUB_DIR="$MOUNT/hub"
HUB_HOME="$HUB_DIR/home"
HUB_REQ_ON_MOUNT="$HUB_DIR/mempalace-hub-requirements.txt"

say() { echo "mempalace-hub-up: $*"; }
warn() { echo "WARNING: mempalace-hub-up: $*" >&2; }

# hub_record STATE DETAIL - merge a `mempalace_hub` object into the shared
# marker, carrying any key a previous writer left forward. Every failure is
# swallowed: the record is a signal, never a dependency.
hub_record() {
    HUB_MARKER="$MARKER" HUB_STATE="$1" HUB_DETAIL="$2" HUB_CONTAINER="$HUB" \
    HUB_NETWORK="$NET" HUB_URL="$URL" HUB_IMAGE="$IMAGE" \
    python3 - <<'MEMPALACE_HUB_RECORD' 2>/dev/null || true
import json, os, time

marker = os.environ["HUB_MARKER"]
record = {}
try:
    with open(marker) as handle:
        loaded = json.load(handle)
    if isinstance(loaded, dict):
        record = loaded
except Exception:
    record = {}

previous = record.get("mempalace_hub")
if not isinstance(previous, dict):
    previous = {}

# Merge, exactly as the per-container supervisor's hub_record did: this writer
# owns the fields below and nothing else, so a key some other writer put under
# mempalace_hub survives rather than being erased.
hub = dict(previous)
hub.update(
    {
        "issue": "898",
        "state": os.environ["HUB_STATE"],
        "detail": os.environ["HUB_DETAIL"],
        "container": os.environ["HUB_CONTAINER"],
        "network": os.environ["HUB_NETWORK"],
        "url": os.environ["HUB_URL"],
        "image": os.environ["HUB_IMAGE"],
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
)
record["mempalace_hub"] = hub

directory = os.path.dirname(marker)
if directory:
    os.makedirs(directory, exist_ok=True)
tmp = marker + ".hub.tmp"
with open(tmp, "w") as handle:
    json.dump(record, handle, indent=2, sort_keys=True)
    handle.write("\n")
try:
    os.chmod(tmp, 0o666)
except OSError:
    pass
os.replace(tmp, marker)
MEMPALACE_HUB_RECORD
}

# hub_last - print the state the marker last recorded, so a human who finds no
# hub is told which of the ten states they are in rather than guessing.
hub_last() {
    HUB_MARKER="$MARKER" python3 - <<'MEMPALACE_HUB_LAST' 2>/dev/null || true
import json, os

try:
    with open(os.environ["HUB_MARKER"]) as handle:
        hub = json.load(handle)["mempalace_hub"]
    state = hub["state"]
except Exception:
    raise SystemExit(0)

print(
    "mempalace-hub-up: last recorded state '%s' at %s (%s)"
    % (state, hub.get("checked_at", "unknown"), hub.get("detail", ""))
)
if state == "lock_held":
    print(
        "mempalace-hub-up: a live process holds the palace lock, so a hub would be "
        "refused it. Do NOT delete the lock file: it is held by a running process, "
        "not stale. Stop that process (usually 'mempalace-hub-up down' on the old "
        "hub, or a session's own server) and run 'mempalace-hub-up' again."
    )
MEMPALACE_HUB_LAST
}

# /healthz is mempalace's own liveness route and the only credential-free one,
# so this works whether or not the hub has a bearer token. Probed by the hub's
# NETWORK NAME from inside this container - the whole point of the rearchitecture
# is that the address is the same everywhere. python3 rather than curl: python3
# is already a hard dependency of the mempalace install, curl is not guaranteed.
hub_alive() {
    HUB_PROBE_URL="$URL/healthz" python3 - <<'MEMPALACE_HUB_HEALTH' 2>/dev/null
import os, sys, urllib.request

try:
    with urllib.request.urlopen(os.environ["HUB_PROBE_URL"], timeout=2) as resp:
        sys.exit(0 if resp.status == 200 else 1)
except Exception:
    sys.exit(1)
MEMPALACE_HUB_HEALTH
}

# This container's id, as the host daemon knows it. mountinfo carries the full
# 64-hex id in the paths the daemon bind-mounts in (/var/lib/docker/containers/
# <id>/resolv.conf); cgroup carries it on cgroup v1; hostname is the short id on
# a container nobody renamed. All three are best-effort, hence the ladder.
hub_self_id() {
    _id=""
    if [ -r /proc/self/mountinfo ]; then
        _id="$(grep -o 'docker/containers/[0-9a-f]\{64\}' /proc/self/mountinfo 2>/dev/null | head -n 1 | sed 's|.*/||')"
    fi
    if [ -z "$_id" ] && [ -r /proc/self/cgroup ]; then
        _id="$(grep -o '[0-9a-f]\{64\}' /proc/self/cgroup 2>/dev/null | head -n 1)"
    fi
    if [ -z "$_id" ]; then
        _id="$(hostname 2>/dev/null || true)"
    fi
    printf '%s\n' "$_id"
}

# The HOST path behind the palace mount. The daemon only understands host paths,
# so `-v /usr/local/share/mempalace:...` would create a fresh empty volume rather
# than share the palace. Ask the daemon what it mounted where; fall back to
# mountinfo field 4, which is the source subtree of the bind.
hub_host_path() {
    _path="$("$DOCKER" inspect -f '{{range .Mounts}}{{if eq .Destination "'"$MOUNT"'"}}{{.Source}}{{end}}{{end}}' "$1" 2>/dev/null | head -n 1)"
    if [ -z "$_path" ] && [ -r /proc/self/mountinfo ]; then
        _path="$(awk -v t="$MOUNT" '$5 == t {print $4}' /proc/self/mountinfo 2>/dev/null | head -n 1)"
    fi
    printf '%s\n' "$_path"
}

hub_connected() {
    # shellcheck disable=SC2016  # a Go template, not a shell expansion
    _nets=" $("$DOCKER" inspect -f '{{range $name, $conf := .NetworkSettings.Networks}}{{$name}} {{end}}' "$1" 2>/dev/null) "
    case "$_nets" in
        *" $NET "*) return 0 ;;
        *) return 1 ;;
    esac
}

hub_sha() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" 2>/dev/null | cut -d' ' -f1
    else
        HUB_SHA_FILE="$1" python3 -c 'import hashlib, os, sys; sys.stdout.write(hashlib.sha256(open(os.environ["HUB_SHA_FILE"], "rb").read()).hexdigest())' 2>/dev/null
    fi
}

# Print the held palace lock and exit 0, or exit 1 when none is held. Probed
# with a non-blocking flock and released immediately, because the FILE existing
# proves nothing - #897 is the report of an operator being told to kill a PID
# from a dead container whose lock file merely survived on the mount. NOTHING
# here ever removes a lock: a held one is held by a living process.
hub_lock_held() {
    HUB_LOCK_DIR="$MOUNT/locks" python3 - <<'MEMPALACE_HUB_LOCK' 2>/dev/null
import fcntl, glob, os, sys

# mempalace 3.9.0 palace.mine_palace_lock:
#   ~/.mempalace/locks/mine_palace_<sha256(normcase(realpath(palace)))[:16]>.lock
held = ""
for path in sorted(glob.glob(os.path.join(os.environ["HUB_LOCK_DIR"], "mine_palace_*.lock"))):
    try:
        handle = open(path, "r")
    except OSError:
        continue
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        held = path
    else:
        fcntl.flock(handle, fcntl.LOCK_UN)
    handle.close()
    if held:
        break

if not held:
    sys.exit(1)
sys.stdout.write(held)
MEMPALACE_HUB_LOCK
}

# The hub's $HOME layout. server_state_dir and mine_palace_lock both root at
# $HOME/.mempalace, so the hub only shares the containers' token, serverinfo
# record and lock if its HOME/.mempalace resolves onto the same mount.
hub_prepare_home() {
    mkdir -p "$HUB_HOME" 2>/dev/null || true
    if [ ! -e "$HUB_HOME/.mempalace" ]; then
        ln -s "$MOUNT" "$HUB_HOME/.mempalace" 2>/dev/null || true
    fi
    [ -e "$HUB_HOME/.mempalace" ]
}

# The per-container hub left serverinfo records advertising 127.0.0.1 (and, had
# it ever bound wide, 0.0.0.0, which client_base_url rewrites to loopback). Both
# are undialable from a sibling container, and read_live_serverinfo would happily
# return one whose recorded pid collides with an unrelated local process. Clear
# them; the hub rewrites its own on the next start.
hub_clear_loopback_serverinfo() {
    HUB_SERVER_DIR="$MOUNT/server" python3 - <<'MEMPALACE_HUB_STALE' 2>/dev/null || true
import glob, json, os

LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]", "0.0.0.0", "::", "[::]"}
for path in glob.glob(os.path.join(os.environ["HUB_SERVER_DIR"], "*", "serverinfo.json")):
    try:
        with open(path, encoding="utf-8") as handle:
            info = json.load(handle)
        host = str(info.get("host", "")).strip().lower()
    except Exception:
        continue
    if host in LOOPBACK:
        try:
            os.remove(path)
            print("mempalace-hub-up: removed a stale loopback hub record at %s (#898)" % path)
        except OSError:
            pass
MEMPALACE_HUB_STALE
}

# Poll until the hub answers. Its first start installs the frozen dependency set
# into a --user site on the shared mount, which is minutes, not seconds - so a
# hub that has not answered yet is `starting`, an honest intermediate state, and
# not the `no_response` that #921 used to clobber the real one with.
hub_wait_live() {
    _waited=0
    while [ "$_waited" -lt "$WAIT" ]; do
        if hub_alive; then
            say "hub is live on $URL/mcp"
            hub_record live "answered $URL/healthz after ${_waited}s"
            return 0
        fi
        sleep 2
        _waited=$((_waited + 2))
    done
    say "hub container is up but has not answered $URL/healthz within ${WAIT}s"
    say "it is probably still installing its dependency set; 'mempalace-hub-up logs' shows the install, 'mempalace-hub-up status' re-probes"
    hub_record starting "no answer on $URL/healthz within ${WAIT}s"
    return 0
}

ACTION="${1:-up}"
case "$ACTION" in
    up|status|down|logs) ;;
    *)
        echo "usage: mempalace-hub-up [up|status|down|logs]" >&2
        exit 2
        ;;
esac

if [ -n "${MEMPALACE_SKIP_HUB:-}" ]; then
    say "MEMPALACE_SKIP_HUB is set, skipping"
    hub_record disabled "MEMPALACE_SKIP_HUB is set"
    exit 0
fi

if ! command -v "$DOCKER" >/dev/null 2>&1; then
    warn "'$DOCKER' is not on PATH. This Feature depends on docker-outside-of-docker for exactly this; without it there is no shared hub, so every Claude Code session on this host serves its own palace copy and only the first to mutate can write (#764, #898)."
    hub_record no_docker "'$DOCKER' is not on PATH"
    exit 0
fi

if [ ! -S "$SOCKET" ] || ! "$DOCKER" info >/dev/null 2>&1; then
    warn "the Docker daemon does not answer over $SOCKET, so the shared mempalace hub cannot be reconciled (#898). Check that docker-outside-of-docker mounted the host socket and that this user is in its group."
    hub_record no_socket "no usable daemon on $SOCKET"
    exit 0
fi

case "$ACTION" in
    up) ;;
    status)
        if hub_alive; then
            say "serving on $URL/mcp"
            hub_record live "answering $URL/healthz"
            exit 0
        fi
        say "nothing answering $URL/healthz"
        "$DOCKER" ps -a --filter "name=^/$HUB\$" --format 'mempalace-hub-up: container {{.Names}} is {{.Status}}' 2>/dev/null || true
        hub_last
        exit 0
        ;;
    down)
        "$DOCKER" stop "$HUB" >/dev/null 2>&1 || true
        hub_record stopped "stopped by 'mempalace-hub-up down'"
        say "stopped $HUB"
        exit 0
        ;;
    logs)
        shift
        exec "$DOCKER" logs "$@" "$HUB"
        ;;
esac

SELF_ID="$(hub_self_id)"
if [ -z "$SELF_ID" ] || ! "$DOCKER" inspect "$SELF_ID" >/dev/null 2>&1; then
    warn "could not identify this container to the host daemon (tried /proc/self/mountinfo, /proc/self/cgroup, hostname), so the palace mount's host path is unknown and no hub can be created (#898)."
    hub_record self_not_found "no container id the daemon recognises"
    exit 0
fi

HOST_PATH="$(hub_host_path "$SELF_ID")"
if [ -z "$HOST_PATH" ]; then
    warn "could not resolve the HOST path behind $MOUNT, so a hub would be given an empty volume instead of the palace (#898)."
    hub_record self_not_found "no host path for $MOUNT"
    exit 0
fi

if ! hub_prepare_home; then
    warn "could not prepare $HUB_HOME/.mempalace; the hub's \$HOME would not resolve onto the palace mount, so its token, serverinfo record and lock would land where no container reads them (#898)."
    hub_record self_not_found "cannot prepare $HUB_HOME"
    exit 0
fi
hub_clear_loopback_serverinfo

if ! "$DOCKER" network inspect "$NET" >/dev/null 2>&1; then
    # A sibling container may be doing this at the same instant, so a failed
    # create is only a failure if the network still is not there afterwards.
    "$DOCKER" network create "$NET" >/dev/null 2>&1 || true
fi
if ! "$DOCKER" network inspect "$NET" >/dev/null 2>&1; then
    warn "could not create the '$NET' Docker network, so this container and the hub have no name to meet on (#898)."
    hub_record network_failed "cannot create network '$NET'"
    exit 0
fi
if ! hub_connected "$SELF_ID"; then
    "$DOCKER" network connect "$NET" "$SELF_ID" >/dev/null 2>&1 || true
fi
if ! hub_connected "$SELF_ID"; then
    warn "could not join the '$NET' Docker network, so '$HUB' will not resolve from this container (#898)."
    hub_record network_failed "cannot join network '$NET'"
    exit 0
fi

# The requirements file is bind-mounted into the hub by the HOST daemon, so it
# has to live on a path the host can see: the shared mount, not this container's
# /usr/local/share/personal-features.
if [ -r "$REQ" ]; then
    mkdir -p "$HUB_DIR" 2>/dev/null || true
    if cp "$REQ" "$HUB_REQ_ON_MOUNT.tmp" 2>/dev/null; then
        mv "$HUB_REQ_ON_MOUNT.tmp" "$HUB_REQ_ON_MOUNT" 2>/dev/null || true
    fi
    rm -f "$HUB_REQ_ON_MOUNT.tmp" 2>/dev/null || true
fi
if [ ! -r "$HUB_REQ_ON_MOUNT" ]; then
    warn "could not stage $REQ onto the shared mount at $HUB_REQ_ON_MOUNT, so the hub has nothing to install (#898)."
    hub_record pull_failed "cannot stage the requirements file on the mount"
    exit 0
fi
REQ_SHA="$(hub_sha "$HUB_REQ_ON_MOUNT")"
REQ_SHA="${REQ_SHA:-unknown}"

# Reconcile whatever hub already exists against the dependency set THIS image
# was built with. The label is the whole comparison: a Feature release that moves
# any pin moves the sha, and a hub still running last month's resolution is
# replaced instead of quietly outliving it.
HUB_STATUS="$("$DOCKER" inspect -f '{{.State.Status}}' "$HUB" 2>/dev/null || true)"
if [ -n "$HUB_STATUS" ]; then
    HUB_SHA_LABEL="$("$DOCKER" inspect -f '{{index .Config.Labels "personal-features.requirements-sha"}}' "$HUB" 2>/dev/null || true)"
    if [ "$HUB_SHA_LABEL" != "$REQ_SHA" ]; then
        say "existing $HUB was built from a different dependency set ($HUB_SHA_LABEL != $REQ_SHA); recreating it"
        "$DOCKER" rm -f "$HUB" >/dev/null 2>&1 || true
        HUB_STATUS=""
    elif [ "$HUB_STATUS" = "running" ]; then
        if hub_alive; then
            say "hub is already live on $URL/mcp"
            hub_record live "answering $URL/healthz"
            exit 0
        fi
        hub_wait_live
        exit 0
    else
        say "starting the existing $HUB (was $HUB_STATUS)"
        "$DOCKER" start "$HUB" >/dev/null 2>&1 || true
        hub_wait_live
        exit 0
    fi
fi

# No hub container. If something still holds the palace lock, a new one would be
# refused it and crash-loop against a condition no retry can change - which is
# the whole of #897. Record it and stop; never delete the lock.
if LOCK_PATH="$(hub_lock_held)"; then
    warn "$LOCK_PATH is held by a live process, so a hub would be refused the palace writer lease (#897). NOT starting one, and NOT removing the lock - it is held, not stale. Stop the holder ('mempalace-hub-up down' if it is an old hub), then run 'mempalace-hub-up' again."
    hub_record lock_held "$LOCK_PATH is held"
    exit 0
fi

if ! "$DOCKER" image inspect "$IMAGE" >/dev/null 2>&1; then
    say "pulling $IMAGE"
    if ! "$DOCKER" pull "$IMAGE" >/dev/null 2>&1; then
        warn "could not pull $IMAGE, so the shared hub cannot be created (#898)."
        hub_record pull_failed "cannot pull $IMAGE"
        exit 0
    fi
fi

# The palace directory's owner, so everything the hub writes into the shared
# mount stays readable and writable by the containers - a root-owned drawer on a
# mount every non-root remoteUser shares is a permission failure later.
HUB_USER="$(stat -c '%u:%g' "$PALACE" 2>/dev/null || stat -c '%u:%g' "$MOUNT" 2>/dev/null || echo 0:0)"

# No --init: the server has to BE pid 1. read_live_serverinfo trusts a record
# only while its pid is alive, pids are namespace-local, and pid 1 is the one
# pid that exists in every namespace - so a pid-1 server is the record every
# sibling container's proxy can believe. `sh -c` execs into python, and
# cli.cmd_serve os.execve's the real server, so pid 1 is preserved end to end.
# SIGINT because that is what mempalace's server shuts down cleanly on; a SIGTERM
# kill is what strands the palace lock in the first place.
say "creating $HUB on network '$NET' from $IMAGE"
if ! "$DOCKER" run -d \
    --name "$HUB" \
    --hostname "$HUB" \
    --network "$NET" \
    --restart unless-stopped \
    --stop-signal SIGINT \
    --user "$HUB_USER" \
    --label "personal-features.requirements-sha=$REQ_SHA" \
    -v "$HOST_PATH:$MOUNT" \
    -v "$HOST_PATH/hub/mempalace-hub-requirements.txt:/hub-requirements.txt:ro" \
    -e "HOME=$HUB_HOME" \
    -e "MEMPALACE_PALACE_PATH=$PALACE" \
    -e MEMPALACE_MCP_IDLE_HOURS=0 \
    -e HF_HUB_DISABLE_SHARED_BLOBS=1 \
    --health-cmd "python -c \"import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('$URL/healthz', timeout=3).status == 200 else 1)\"" \
    --health-interval 30s \
    "$IMAGE" sh -c "pip install --user -q -r /hub-requirements.txt && exec python -m mempalace serve --host $HUB --port $PORT --palace $PALACE" \
    >/dev/null 2>&1; then
    warn "'docker run' failed for $HUB, so there is no shared hub (#898). 'mempalace-hub-up logs' may still have output if the container was created."
    hub_record pull_failed "'docker run' failed for $HUB"
    exit 0
fi

hub_wait_live
exit 0
MEMPALACE_HUB_UP
chmod 0755 /usr/local/bin/mempalace-hub-up

# --- Additional personal tooling --------------------------------------------
# Opinionated, always installed - this Feature is the owner's own personal
# config, not a general-purpose toolkit, so none of this is optional. If a
# tool stops being useful here, remove it instead of gating it behind an
# option. Independent of the Claude/Node logic above: installs via apt or
# static binaries, no dependency on Node being present.

DPKG_ARCH="$(dpkg --print-architecture)" # amd64 | arm64
# lazygit's linux assets are named x86_64 / arm64 - the x86_64 half matches
# ARCH_GNU but the arm64 half doesn't (that would be aarch64), so it needs its
# own column rather than reusing an existing one.
#
# qsv needs its own column too: it publishes a fully-static *musl* build only
# for x86_64 (no aarch64 musl asset), so amd64 uses that - it runs on any glibc
# and pulls in no shared libs - while arm64 falls back to the dynamically-linked
# gnu build (its only aarch64 option), which needs a recent glibc plus a runtime
# lib (see the qsv install below).
case "$DPKG_ARCH" in
    amd64) ARCH_DEB=amd64; ARCH_GNU=x86_64;  ARCH_SHORT=x64;   ARCH_LAZYGIT=x86_64; ARCH_QSV=x86_64-unknown-linux-musl  ;;
    arm64) ARCH_DEB=arm64; ARCH_GNU=aarch64; ARCH_SHORT=arm64; ARCH_LAZYGIT=arm64;  ARCH_QSV=aarch64-unknown-linux-gnu ;;
    *) echo "ERROR: unsupported architecture: $DPKG_ARCH" >&2; exit 1 ;;
esac

# --- Pinned tool versions ---------------------------------------------------
# Single source of truth for the GitHub-release tools installed below. These
# replace the old unauthenticated api.github.com "/releases/latest" lookups,
# which made builds non-reproducible and, worse, hit GitHub's anonymous rate
# limit (60 req/hr per IP, shared across CI runners behind one NAT) causing
# intermittent "failed to install, skipping" flakiness. Download URLs are now
# built deterministically from these constants, so the install path makes zero
# api.github.com calls. Dependabot does not track shell-script pins - bump them
# here by hand. Store the bare semver (no leading "v"); the "v" is added at each
# use site where the release tag / URL needs it (yq etc. tag as vX.Y.Z; gitleaks
# and zoxide also embed the bare version in the asset filename).
YQ_VERSION=4.53.3        # github.com/mikefarah/yq
EZA_VERSION=0.23.5       # github.com/eza-community/eza
TEALDEER_VERSION=1.8.1   # github.com/dbrgn/tealdeer
GITLEAKS_VERSION=8.30.1  # github.com/gitleaks/gitleaks
ZOXIDE_VERSION=0.10.0    # github.com/ajeetdsouza/zoxide
STARSHIP_VERSION=1.26.0  # github.com/starship/starship
# delta tags its releases WITHOUT a leading "v" (e.g. 0.19.2, not v0.19.2), so
# unlike the tools above its download URL uses the bare version verbatim.
DELTA_VERSION=0.19.2     # github.com/dandavison/delta
LAZYGIT_VERSION=0.63.0   # github.com/jesseduffield/lazygit
# qsv tags its releases WITHOUT a leading "v" (e.g. 21.1.0). Unlike every tool
# above it ships a .zip (not a raw binary or .tar.gz) bundling ~13 binaries, so
# it needs its own installer (install_qsv) rather than install_gh_release. The
# per-arch target (musl on amd64, gnu on arm64) is ARCH_QSV, set above.
QSV_VERSION=21.1.0       # github.com/dathere/qsv
# odoo-ls, the Odoo language server (#746). Unlike every tool above it is not a
# convenience CLI: Claude Code launches it as a long-lived LSP server that reads
# every Python/XML/CSV file in the workspace, so its download is the one here
# whose digest is pinned (see fetch's third argument). Tagged WITHOUT a leading
# "v" (1.6.0), and the per-arch asset name uses the same x86_64/aarch64 spelling
# as ARCH_GNU above. Bump all four values together: `curl -fsSL
# https://api.github.com/repos/odoo/odoo-ls/releases/latest` lists the assets,
# and sha256sum each downloaded file.
#
# 1.6.0, not the 1.4.0 named in #746: 1.4.0 was current when the issue was
# written, 1.6.0 is the current non-prerelease (1.5.x are all marked prerelease).
ODOO_LS_VERSION=1.6.0    # github.com/odoo/odoo-ls
ODOO_LS_SHA256_TYPESHED=f95e220274f29452ee02204b1b44f2645cb2050839b02ca40b3971aa89bbcf02
case "$ARCH_GNU" in
    x86_64)  ODOO_LS_SHA256=4424663a03a8433de60e822694e5532bd48bee41229344dfc0c7aaa95fe4b37b ;;
    aarch64) ODOO_LS_SHA256=e58d06913f1be1b1355f48756760d51d26bdf775c6d6b5a03a485732a9bb952f ;;
esac

# Installs a pinned GitHub release asset from a deterministic download URL (no
# api.github.com lookup). With a dest path ($3) it downloads a single binary
# there and marks it executable; otherwise it extracts a .tar.gz into
# /usr/local/bin - restricted to member $4 when given, so tarballs that also
# ship docs/man/completions (e.g. zoxide) don't litter /usr/local/bin. $5 sets
# tar's --strip-components, for tarballs that nest the binary under a top-level
# directory (e.g. delta's delta-<ver>-<arch>/delta) so it still lands directly
# on /usr/local/bin rather than in a subdir. Best-effort: these are optional,
# non-Claude tools, so a failure here warns and continues rather than failing
# the whole install.
install_gh_release() {
    local name="$1" url="$2" dest="${3-}" member="${4-}" strip="${5-}"
    if [ -n "$dest" ]; then
        if fetch "$url" "$dest"; then
            chmod +x "$dest"
        else
            echo "WARNING: failed to install $name, skipping" >&2
        fi
    else
        # Download to a temp file, then extract - piping curl into tar would
        # (without pipefail) hide a failed download behind tar's exit status.
        local tarball
        tarball="$(mktemp)"
        if fetch "$url" "$tarball"; then
            # ${member:+...}/${strip:+...} add their arguments only when set, so
            # a bare (whole-tarball) extract stays argument-clean under set -u.
            tar -xz -C /usr/local/bin ${strip:+--strip-components="$strip"} \
                -f "$tarball" ${member:+"$member"} \
                || echo "WARNING: failed to install $name, skipping" >&2
        else
            echo "WARNING: failed to install $name, skipping" >&2
        fi
        rm -f "$tarball"
    fi
}

# qsv (high-performance CSV data-wrangling toolkit) ships a .zip that bundles
# several binaries (qsv, qsvlite, qsvdp, portable variants, README) rather than
# a raw binary or a .tar.gz, so neither of install_gh_release's paths fit.
# Handle it with a scoped unzip that lands ONLY the `qsv` binary directly on
# /usr/local/bin (unzip -j junks the archive paths). Best-effort like the tools
# above: any failure warns and skips rather than failing the build. The arm64
# (gnu) build additionally needs a runtime lib, installed separately below.
install_qsv() {
    local url="$1" zip
    zip="$(mktemp)"
    if fetch "$url" "$zip" \
        && unzip -q -o -j "$zip" qsv -d /usr/local/bin \
        && [ -f /usr/local/bin/qsv ]; then
        chmod +x /usr/local/bin/qsv
    else
        echo "WARNING: failed to install qsv, skipping" >&2
    fi
    rm -f "$zip"
}

# odoo-ls (#746): the Odoo language server Claude Code launches over stdio.
# Neither install_gh_release path fits, for two reasons.
#
# 1. It does not belong in /usr/local/bin. The server resolves its typeshed
#    stubs RELATIVE TO ITS OWN BINARY - core/odoo.rs default_stdlib()/
#    default_stubs() look for `typeshed/stdlib` and `typeshed/stubs` next to
#    `current_exe()` before falling back to the cwd, and without stdlib stubs it
#    logs "Unable to find builtins.pyi" and resolves nothing. Unpacking 35 MiB of
#    typeshed into /usr/local/bin/typeshed to satisfy that is not acceptable, so
#    binary and stubs live together in /usr/local/share/odoo-ls and the wrapper
#    installed below is what goes on PATH. `--stdlib` could override the path
#    instead, but co-locating means the default is already right and one fewer
#    flag can drift.
# 2. Two assets, both digest-pinned (the per-arch tarball and the shared
#    typeshed.zip), and a partial install is worse than none: a server with no
#    stubs starts, answers, and silently resolves nothing. So this stages both
#    into a temp dir and only publishes once BOTH have passed their checksum.
#
# Best-effort like every tool above - a container with no language server is the
# pre-#746 state, which is degraded, not broken, and not worth failing an image
# build over. A checksum mismatch is loud (fetch prints both digests) and lands
# here as a skip; the feature test asserts the binary is present, so CI still
# goes red rather than shipping a silently server-less image.
install_odoo_ls() {
    local version="$1" tarball_sha="$2" typeshed_sha="$3"
    local dest=/usr/local/share/odoo-ls
    local base="https://github.com/odoo/odoo-ls/releases/download/${version}"
    local staging
    staging="$(mktemp -d)"

    if ! fetch "${base}/odoo-linux-${ARCH_GNU}-${version}.tar.gz" "$staging/server.tar.gz" "$tarball_sha"; then
        echo "WARNING: failed to install odoo-ls (server binary), skipping" >&2
        rm -rf "$staging"
        return 0
    fi
    # typeshed.zip is arch-independent and shared by every platform's asset.
    if ! fetch "${base}/typeshed.zip" "$staging/typeshed.zip" "$typeshed_sha"; then
        echo "WARNING: failed to install odoo-ls (typeshed stubs), skipping" >&2
        rm -rf "$staging"
        return 0
    fi

    # The tarball holds ./odoo_ls_server and nothing else; the zip's root holds
    # stdlib/ and stubs/ directly, so it unpacks into <dest>/typeshed.
    if tar -xz -C "$staging" -f "$staging/server.tar.gz" ./odoo_ls_server \
        && unzip -q -o "$staging/typeshed.zip" -d "$staging/typeshed" \
        && [ -d "$staging/typeshed/stdlib" ]; then
        mkdir -p "$dest"
        rm -rf "$dest/typeshed"
        mv "$staging/typeshed" "$dest/typeshed"
        install -m 0755 "$staging/odoo_ls_server" "$dest/odoo_ls_server"
        # The server's own log directory, and the ONE that cannot fail: with no
        # --logs-directory (or one that does not exist - the server checks
        # `path.exists()` and falls back rather than creating it) the rolling
        # file appender is built against <exe dir>/logs, and that build is an
        # .expect(), i.e. a panic at startup. 0777 for the same reason as
        # shell-history: install.sh cannot know which uid ends up running a
        # Claude Code session, and these are server logs, not a secret.
        mkdir -p "$dest/logs"
        chmod 0777 "$dest/logs"
    else
        echo "WARNING: failed to unpack odoo-ls, skipping" >&2
    fi
    rm -rf "$staging"
}

echo "Installing productivity/navigation CLI tools"
apt-get update -y
apt-get install -y --no-install-recommends ripgrep fd-find fzf bat jq unzip

# On arm64, qsv only ships the dynamically-linked gnu build, whose full binary
# links libwayland-client.so.0 (via its clipboard feature) - absent on the
# minimal base images, so `qsv --version` would fail to even load without it.
# (amd64 uses the fully-static musl build, which needs none of this.) Installed
# here, best-effort and synchronously before the parallel download jobs below:
# best-effort so a base image that lacks the package (or is too old to run qsv
# at all) just skips qsv instead of failing the whole build, and synchronous so
# it can't collide with the backgrounded installers over dpkg's lock.
if [ "$DPKG_ARCH" = arm64 ]; then
    apt-get install -y --no-install-recommends libwayland-client0 \
        || echo "WARNING: could not install libwayland-client0 (qsv runtime dep); qsv may not run" >&2
fi

# Debian/Ubuntu's apt packages ship these under different binary names to
# avoid clashing with existing system commands.
[ -x /usr/local/bin/fd ] || ln -s "$(command -v fdfind)" /usr/local/bin/fd
[ -x /usr/local/bin/bat ] || ln -s "$(command -v batcat)" /usr/local/bin/bat

# Run all binary downloads in parallel — they're independent and each blocks on
# a network download, so sequential execution wastes wall time. URLs are pinned
# and deterministic (see the version block above); no api.github.com calls.
# Collect their PIDs so we can wait on each individually (see the wait below).
_download_pids=()
# Background a download job and record its PID so the wait loop below can reap
# each one individually.
bg() { "$@" & _download_pids+=("$!"); }
bg install_gh_release yq \
    "https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_linux_${ARCH_DEB}" \
    /usr/local/bin/yq
bg install_gh_release eza \
    "https://github.com/eza-community/eza/releases/download/v${EZA_VERSION}/eza_${ARCH_GNU}-unknown-linux-gnu.tar.gz"
bg install_gh_release tealdeer \
    "https://github.com/dbrgn/tealdeer/releases/download/v${TEALDEER_VERSION}/tealdeer-linux-${ARCH_GNU}-musl" \
    /usr/local/bin/tldr
# zoxide's upstream install.sh only resolves versions via api.github.com (no
# pin flag), so download the release tarball directly instead. It bundles man
# pages/completions/README alongside the binary, so extract just `zoxide`.
bg install_gh_release zoxide \
    "https://github.com/ajeetdsouza/zoxide/releases/download/v${ZOXIDE_VERSION}/zoxide-${ZOXIDE_VERSION}-${ARCH_GNU}-unknown-linux-musl.tar.gz" \
    "" zoxide
bg install_gh_release gitleaks \
    "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_${ARCH_SHORT}.tar.gz"
# delta (better git diffs) - its tarball nests LICENSE/README/delta under a
# top-level delta-<ver>-<arch> dir, so extract just the binary and strip that
# leading component (strip=1) to land it directly on /usr/local/bin. No aarch64
# musl asset is published, so use the gnu tarball for both arches. Wired up as
# git's pager via `git config --system` below.
bg install_gh_release delta \
    "https://github.com/dandavison/delta/releases/download/${DELTA_VERSION}/delta-${DELTA_VERSION}-${ARCH_GNU}-unknown-linux-gnu.tar.gz" \
    "" "delta-${DELTA_VERSION}-${ARCH_GNU}-unknown-linux-gnu/delta" 1
# lazygit (TUI git client) - ships the bare binary at the tarball root alongside
# LICENSE/README, so extract just `lazygit` (like zoxide).
bg install_gh_release lazygit \
    "https://github.com/jesseduffield/lazygit/releases/download/v${LAZYGIT_VERSION}/lazygit_${LAZYGIT_VERSION}_linux_${ARCH_LAZYGIT}.tar.gz" \
    "" lazygit
# qsv (CSV data toolkit) - ships a .zip bundling many binaries, so it uses its
# own installer (see install_qsv above) which extracts only the `qsv` binary.
# ARCH_QSV selects the per-arch target (static musl on amd64, gnu on arm64).
bg install_qsv \
    "https://github.com/dathere/qsv/releases/download/${QSV_VERSION}/qsv-${QSV_VERSION}-${ARCH_QSV}.zip"
# odoo-ls (#746) - see install_odoo_ls above for why it gets its own installer
# and why its two assets are the only digest-pinned downloads here.
bg install_odoo_ls "$ODOO_LS_VERSION" "$ODOO_LS_SHA256" "$ODOO_LS_SHA256_TYPESHED"

# CodeRabbit CLI — not published as GitHub release assets, so use the upstream
# installer (https://cli.coderabbit.ai/install.sh) pinned to /usr/local/bin.
# CI=1 suppresses the interactive post-install login prompt; the installer's
# own PATH/profile edits are harmless no-ops here since it lands on a dir
# already on PATH. The vars must be exported (not just prefixed) so the
# installer's `sh` — a separate child process — actually inherits them. The
# subshell isolates those exports from the rest of the script. Auth is
# user-specific and persisted via the mount below, so it is deliberately not
# baked in. Best-effort like the tools above.
install_coderabbit() {
    ( export CODERABBIT_INSTALL_DIR=/usr/local/bin CI=1
        run_installer https://cli.coderabbit.ai/install.sh \
        || echo "WARNING: failed to install coderabbit, skipping" >&2 )
}
bg install_coderabbit

echo "Configuring global git hooks (core.hooksPath)"
GIT_HOOKS_DIR="/usr/local/share/git-hooks"
mkdir -p "$GIT_HOOKS_DIR"

# Enforces Conventional Commits (https://www.conventionalcommits.org/)
# machine-wide, regardless of whether the repo being committed to has any
# hook tooling of its own. A repo with its own core.hooksPath (e.g. via
# Husky) overrides this as normal Git config precedence.
cat > "$GIT_HOOKS_DIR/commit-msg" << 'EOF'
#!/bin/sh
set -e

MSG_FILE="$1"
FIRST_LINE="$(head -n1 "$MSG_FILE")"

case "$FIRST_LINE" in
    Merge\ *|Revert\ *|fixup!\ *|squash!\ *)
        exit 0
        ;;
esac

if ! echo "$FIRST_LINE" | grep -qE '^(feat|fix|docs|style|refactor|perf|test|build|ci|chore)(\([a-zA-Z0-9_.-]+\))?!?: .+'; then
    echo "ERROR: commit message does not follow Conventional Commits:" >&2
    echo "  $FIRST_LINE" >&2
    echo "Expected: <type>(<optional scope>): <description>, e.g. 'fix(api): handle empty response'" >&2
    exit 1
fi
EOF

# Runs gitleaks against staged changes when it's installed.
cat > "$GIT_HOOKS_DIR/pre-commit" << 'EOF'
#!/bin/sh
set -e

if command -v gitleaks >/dev/null 2>&1; then
    gitleaks protect --staged --no-banner --redact
fi
EOF

chmod +x "$GIT_HOOKS_DIR/commit-msg" "$GIT_HOOKS_DIR/pre-commit"
git config --system core.hooksPath "$GIT_HOOKS_DIR"

# Wire delta in as git's pager machine-wide (same --system scope as the hooks
# above), so `git diff`/`git log -p`/`git show` render through it and `git add
# -p` gets syntax-highlighted hunks. Set unconditionally rather than gated on
# delta's presence: delta's download is best-effort (it may be skipped on a
# network failure), but the feature's philosophy is that these tools are always
# installed, and the feature test asserts these exact config values.
git config --system core.pager delta
git config --system interactive.diffFilter "delta --color-only"

# --- Machine-wide git excludes (#643) ---------------------------------------
# mempalace's `init` writes project-local artifacts into whatever repo it is
# pointed at - entities.json, mempalace.yaml, and a .mempalace/ dir - and none of
# them can be redirected: `init` resolves them from its --dir argument
# (cli.py, room_detector_local.py) and exposes no --output/--central/--palace-path
# option, no config.json key and no MEMPALACE_* env var moves them. Upstream's own
# answer is to append a 2-line block to each project's .gitignore, which does not
# scale across a tree of working repos and dirties every one of them.
#
# So ignore them machine-wide instead, at the same --system scope (and for the
# same "these tools are always installed" reason) as core.hooksPath and
# core.pager above. `init` is worth supporting: its mempalace.yaml `rooms` list
# is what routes mined files into rooms (miner.py); without it, mining collapses
# into a single `general` room, and config.json's topic_wings/hall_keywords are
# a keyword-to-hall map, not a substitute. The palace artifact names are listed
# too, so a palace root that ever lands inside a repo stays untracked.
#
# TRADEOFF 1 - these are generic filenames. entities.json, hallways.json and
# known_entities.json could plausibly be real tracked files in an unrelated repo.
# Ignore rules never affect ALREADY-TRACKED files, so this can only ever hide a
# NEW one; a repo that needs one back negates it in its own .gitignore
# (`!entities.json`) or uses `git add -f`.
#
# TRADEOFF 2 - setting core.excludesfile shadows git's default
# ~/.config/git/ignore, which only applies when core.excludesfile is unset at
# every scope. A user who wants their own global excludes should set
# `git config --global core.excludesfile <path>` (--global beats --system) and
# copy these entries into it.
echo "Configuring machine-wide git excludes (core.excludesfile)"
GIT_EXCLUDES_DIR="/usr/local/share/git-excludes"
mkdir -p "$GIT_EXCLUDES_DIR"
cat > "$GIT_EXCLUDES_DIR/gitignore" << 'EOF'
# Machine-wide git excludes, installed by the personal-features devcontainer
# feature (#643). See install.sh for the rationale and the two tradeoffs.

# mempalace `init` project-local artifacts
mempalace.yaml
entities.json
.mempalace/

# mempalace palace artifacts, in case a palace root ever lands inside a repo
chroma.sqlite3
knowledge_graph.sqlite3
hallways.json
known_entities.json
hook_state/
wal/
locks/
EOF
git config --system core.excludesfile "$GIT_EXCLUDES_DIR/gitignore"

echo "Installing shell enhancements (Starship prompt, aliases, persisted history)"
# --version pins the release; the installer then builds a direct
# releases/download/<tag>/ URL (no api.github.com) and resolves the right
# per-arch target itself (x86_64 gnu / aarch64 musl).
install_starship() {
    run_installer https://starship.rs/install.sh --version "v${STARSHIP_VERSION}" --bin-dir /usr/local/bin -y \
        || echo "WARNING: failed to install starship, skipping" >&2
}
bg install_starship

# Wait for all background downloads (yq, eza, tldr, zoxide, gitleaks, delta,
# lazygit, qsv, odoo-ls, coderabbit, starship). Each job already warns and exits 0 on its own failure;
# wait on each PID and guard it so an unexpected non-zero exit degrades to a
# warning instead of aborting the build under set -e. (A bare `wait` returns 0
# regardless, which would instead silently mask such a failure.)
for _pid in "${_download_pids[@]}"; do
    wait "$_pid" || echo "WARNING: a background download job failed" >&2
done

# --- odoo-ls launcher + generated odools.toml (#746) -------------------------
# Two scripts, because the language server needs two things the plugin's
# .lsp.json cannot express.
#
# odoo-ls-server  the `command` the odoo-dev plugin's .lsp.json names. Claude
#                 Code requires the command on PATH and substitutes only
#                 ${CLAUDE_PLUGIN_ROOT}/${CLAUDE_PLUGIN_DATA}/${CLAUDE_PROJECT_DIR}
#                 into it - verified against the 2.1.252 bundle, and notably NOT
#                 the ${workspaceFolder} #746 assumed. Choosing between the
#                 generated config and a project's own is a conditional, so it
#                 needs a script either way.
# odoo-ls-config  writes the generated odools.toml from the container's real
#                 paths at create time, when $ODOO_VERSION and the mounted
#                 checkout are finally knowable. Same reason resolve-mempal-dir
#                 exists: a Feature build cannot see any of this (#485).
cat > /usr/local/bin/odoo-ls-server << 'ODOO_LS_SERVER'
#!/bin/sh
set -u
# odoo-ls-server - launch the Odoo language server for one Claude Code session.
#
# Named on PATH because Claude Code's plugin LSP loader requires `command` to be
# resolvable there and will not run a bundled binary; the server itself lives in
# /usr/local/share/odoo-ls next to the typeshed stubs it resolves relative to its
# own path (see install_odoo_ls).
#
# NEVER FATAL, and that is a deliberate trade. Exiting non-zero here reads to
# Claude Code as a crashed server, which it then restarts up to maxRestarts
# times; exiting 0 without speaking LSP reads the same way. Neither is worth it
# for "this container has no Odoo in it", so the no-server cases below exec
# nothing, say why on stderr (Claude Code captures it) and exit 0 once.
#
# Overrides:
#   ODOO_LS_DISABLE=1    do not start the server at all (the kill switch; the
#                        other one is `diagnostics: false` or disabling the
#                        odoo-dev plugin, both of which need a restart)
#   ODOO_LS_BIN          server binary
#   ODOO_LS_CONFIG       generated config file
#   ODOO_LS_LOG_LEVEL    trace|debug|info|warn|error (default: warn)
#   ODOO_LS_LOGS_DIR     log directory

BIN="${ODOO_LS_BIN:-/usr/local/share/odoo-ls/odoo_ls_server}"
CONFIG="${ODOO_LS_CONFIG:-/usr/local/share/odoo-ls/odools.toml}"
# The server defaults to --log-level trace and rotates hourly keeping 5 files,
# which is megabytes an hour per session for output nobody reads. warn keeps the
# failures and drops the rest.
LEVEL="${ODOO_LS_LOG_LEVEL:-warn}"
LOGS_DIR="${ODOO_LS_LOGS_DIR:-${TMPDIR:-/tmp}/odoo-ls-logs}"

if [ -n "${ODOO_LS_DISABLE:-}" ]; then
    echo "odoo-ls-server: ODOO_LS_DISABLE is set; not starting the Odoo language server" >&2
    exit 0
fi

if [ ! -x "$BIN" ]; then
    echo "WARNING: odoo-ls-server: $BIN is missing or not executable (its install is best-effort); this session gets no Odoo language intelligence" >&2
    exit 0
fi

# CLAUDE_PROJECT_DIR is injected into every plugin LSP server's environment by
# Claude Code itself (no `env` block needed), and is the same directory it sends
# as the LSP workspace folder. $PWD is the fallback for a hand-run server.
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"

# Does the project already carry its own odools.toml, at its root or anywhere
# above it? The server walks exactly that chain itself and merges what it finds
# (deeper wins), so when one exists it must be the ONLY source: passing
# --config-path as well makes the generated file a second, sibling source, and
# sources are merged agree-or-error for scalars - a project that legitimately
# overrides odoo_path or python_path would get a config error instead of an
# override. So the generated file steps aside entirely, and a project config is
# expected to be self-contained.
project_config() {
    dir="$1"
    while :; do
        [ -f "$dir/odools.toml" ] && { echo "$dir/odools.toml"; return 0; }
        [ "$dir" = "/" ] && return 1
        parent="$(dirname "$dir")"
        [ "$parent" = "$dir" ] && return 1
        dir="$parent"
    done
}

# Each block below PREPENDS its flags, so whatever the caller passed stays last
# and still wins - both the .lsp.json `args` list and a hand-run
# `odoo-ls-server --version`.
if found="$(project_config "$PROJECT_DIR")"; then
    echo "odoo-ls-server: using the project's own $found (the generated $CONFIG is not passed)" >&2
elif [ -f "$CONFIG" ]; then
    set -- --config-path "$CONFIG" "$@"
else
    # Start NOTHING rather than a server with no Odoo source. The plugin
    # registers .py for every project, not only Odoo ones, so this is the normal
    # case in a container that has no Odoo in it - and there the server would
    # index the whole tree to answer nothing, because without odoo_path it
    # resolves no model, no field and no xmlid.
    echo "odoo-ls-server: no $CONFIG and no odools.toml at or above $PROJECT_DIR; not starting the Odoo language server. Run odoo-ls-config in an Odoo container, or add an odools.toml to the project." >&2
    exit 0
fi

# --logs-directory is honoured only when the directory ALREADY EXISTS: the
# server checks path.exists() and otherwise falls back to <binary dir>/logs
# (which install.sh pre-creates 0777 for exactly this reason). So create it.
#
# The writability test is not belt-and-braces. A log directory the server cannot
# write to is a PANIC before it ever speaks LSP - measured against 1.6.0, exit
# 101, "failed to initialize rolling file appender ... PermissionDenied" - and
# the default path is shared across accounts, so one session run as root leaves
# a directory the next session cannot write. That would crash-loop through
# maxRestarts and end with no language server at all. Dropping the flag instead
# falls back to the 0777 directory next to the binary, which always works.
if mkdir -p "$LOGS_DIR" 2>/dev/null && [ -w "$LOGS_DIR" ]; then
    set -- --logs-directory "$LOGS_DIR" "$@"
fi

# No --stdio flag exists and none is needed: stdio is the server's default
# transport and --use-tcp is what switches away from it (#746 asked; args.rs and
# main.rs answer). Logs never reach stdout in this mode either - the stdout
# subscriber is installed only under --parse or --use-tcp - so stdout carries
# LSP protocol and nothing else, which is what Claude Code requires.
exec "$BIN" --log-level "$LEVEL" "$@"
ODOO_LS_SERVER
chmod 0755 /usr/local/bin/odoo-ls-server

cat > /usr/local/bin/odoo-ls-config << 'ODOO_LS_CONFIG'
#!/bin/sh
set -u
# odoo-ls-config - write the container-wide odools.toml the Odoo language server
# reads, from paths that only exist once the container does.
#
# Run from postCreateCommand. Regenerates unconditionally: the file is derived
# state, and $ODOO_VERSION or the mounted checkout can change across a rebuild.
# Hand edits belong in a project's OWN odools.toml, which odoo-ls-server honours
# instead of this one - see that script for why the two are never both in play.
#
# Never fatal: a container that cannot be configured for Odoo gets no language
# server, which is the pre-#746 state, not an outage worth failing create over.
#
# Overrides (all default to this devcontainer's documented paths):
#   ODOO_LS_SKIP_CONFIG=1  write nothing
#   ODOO_LS_CONFIG         output file
#   ODOO_LS_ODOO_PATH      Odoo community source
#   ODOO_LS_ENTERPRISE     enterprise addons directory
#   ODOO_LS_WORKSPACE      the mounted customization checkout
#   ODOO_LS_PYTHON         interpreter whose site-packages the server reads

OUT="${ODOO_LS_CONFIG:-/usr/local/share/odoo-ls/odools.toml}"
ODOO_PATH="${ODOO_LS_ODOO_PATH:-/usr/lib/python3/dist-packages/odoo}"
# Enterprise addons are per-series (/var/lib/odoo/addons/<series>), so with no
# $ODOO_VERSION there is no directory to name - and naming the parent would hand
# the server a directory of series directories, not of modules.
ENTERPRISE="${ODOO_LS_ENTERPRISE:-}"
if [ -z "$ENTERPRISE" ] && [ -n "${ODOO_VERSION:-}" ]; then
    ENTERPRISE="/var/lib/odoo/addons/$ODOO_VERSION"
fi
WORKSPACE="${ODOO_LS_WORKSPACE:-/mnt/extra-addons}"
VENV_PYTHON="$WORKSPACE/.venv/bin/python"

if [ -n "${ODOO_LS_SKIP_CONFIG:-}" ]; then
    echo "odoo-ls-config: ODOO_LS_SKIP_CONFIG is set, skipping"
    exit 0
fi

# No Odoo source means this is not an Odoo container. Remove any file a previous
# create wrote rather than leaving one behind: every path setting is resolved
# against the filesystem and a stale entry is a hard config error, which is a
# worse outcome than no config at all.
if [ ! -d "$ODOO_PATH" ]; then
    if [ -f "$OUT" ]; then
        rm -f "$OUT" && echo "odoo-ls-config: no Odoo source at $ODOO_PATH; removed the stale $OUT"
    else
        echo "odoo-ls-config: no Odoo source at $ODOO_PATH; this container gets no Odoo language server"
    fi
    exit 0
fi

# A venv's own interpreter, when the checkout has one: the server reads the
# site-packages that interpreter resolves, so pointing it at the system python3
# in a container whose dependencies live in the venv loses every third-party
# import. Left unset when neither exists, which makes the server fall back to
# its own default rather than resolving a path that is not there.
PYTHON="${ODOO_LS_PYTHON:-}"
if [ -z "$PYTHON" ]; then
    if [ -x "$VENV_PYTHON" ]; then
        PYTHON="$VENV_PYTHON"
    else
        PYTHON="$(command -v python3 2>/dev/null || true)"
    fi
fi

mkdir -p "$(dirname "$OUT")" || {
    echo "WARNING: odoo-ls-config: cannot create $(dirname "$OUT"); no Odoo language server config was written" >&2
    exit 0
}

TMP="$OUT.tmp.$$"
{
    echo "# GENERATED by odoo-ls-config at container create - do not edit."
    echo "# Edits are lost on the next rebuild. To override any of this for one"
    echo "# project, put an odools.toml in that project instead: odoo-ls-server"
    echo "# then uses it alone, so it has to restate everything it still wants."
    echo "[[config]]"
    echo 'name = "default"'
    printf 'odoo_path = "%s"\n' "$ODOO_PATH"
    echo "addons_paths = ["
    printf '  "%s",\n' "$ODOO_PATH/addons"
    [ -n "$ENTERPRISE" ] && [ -d "$ENTERPRISE" ] && printf '  "%s",\n' "$ENTERPRISE"
    [ -d "$WORKSPACE" ] && printf '  "%s",\n' "$WORKSPACE"
    echo "]"
    [ -n "$PYTHON" ] && printf 'python_path = "%s"\n' "$PYTHON"
    # The JS/OWL half of the server shells out to tsserver and reports a
    # diagnostic on every session when it is absent. typescript is not installed
    # here and installing it to silence a warning is the wrong trade, so turn
    # that half off; Python/XML/CSV - what the .lsp.json actually registers - is
    # unaffected.
    echo "disable_javascript = true"
} > "$TMP" || {
    rm -f "$TMP"
    echo "WARNING: odoo-ls-config: could not write $TMP; no Odoo language server config was written" >&2
    exit 0
}
mv "$TMP" "$OUT" || {
    rm -f "$TMP"
    echo "WARNING: odoo-ls-config: could not move $TMP to $OUT" >&2
    exit 0
}
# Readable by whichever account ends up running a Claude Code session, for the
# same reason the log directory is world-writable: install.sh cannot know it.
chmod 0644 "$OUT"

# NOT listed here: the task worktrees under $WORKSPACE/.worktrees. The server
# infers addon paths from the LSP workspace folder itself when the profile it
# resolves for that folder sets no addons_paths (config/stages.rs infer_addons,
# which runs per workspace), so a session opened inside a worktree picks that
# worktree up on its own - and the list merges across sources rather than
# replacing. Enumerating them here would instead bake in paths that are created
# and removed constantly during task work, and every removed one becomes a hard
# config error on the next session.
echo "odoo-ls-config: wrote $OUT (odoo_path=$ODOO_PATH, python_path=${PYTHON:-<unset>})"
ODOO_LS_CONFIG
chmod 0755 /usr/local/bin/odoo-ls-config

# Starship config: single-char Unicode symbols throughout (no emoji, no Nerd
# Font glyphs), extra modules useful for Odoo dev work.
cp "$(dirname "$0")/starship.toml" /usr/local/share/starship.toml

# Shell history is bind-mounted as a *directory*, not a single file (#198):
# Docker Desktop materialises a missing single-file mount source as a directory
# on the host, which then fails the mount. Nothing written here survives at
# runtime - the mount masks this whole dir - but creating it keeps the feature
# working in test containers, which run with no mounts active. Against an empty
# host dir the symlink below is briefly dangling; that is fine and self-heals,
# because bash opens HISTFILE with O_CREAT, which follows the symlink and
# creates the target. (readlink -f resolves it either way: only the components
# *before* the last have to exist.)
SHELL_HISTORY_DIR="/usr/local/share/shell-history"
BASH_HISTORY_FILE="$SHELL_HISTORY_DIR/bash_history"
mkdir -p "$SHELL_HISTORY_DIR"
touch "$BASH_HISTORY_FILE"
rm -f "$_REMOTE_USER_HOME/.bash_history"
ln -s "$BASH_HISTORY_FILE" "$_REMOTE_USER_HOME/.bash_history"
chown -R "$_REMOTE_USER" "$SHELL_HISTORY_DIR"
chown -h "$_REMOTE_USER" "$_REMOTE_USER_HOME/.bash_history"

# HISTFILE is set system-wide rather than relying on the symlink above, which
# only covers $_REMOTE_USER's home: this way history is persisted for every user
# in the container (root, vscode, su'd shells). histappend is a correctness fix,
# not polish - without it bash *truncates* HISTFILE on exit and rewrites it from
# its in-memory list, so with the file now shared across concurrent containers
# one shell exiting would wipe another's history. `history -a` flushes after
# each command, so history also survives a killed container, not just a clean
# exit.
if ! grep -qF "# >>> personal-features >>>" /etc/bash.bashrc 2>/dev/null; then
    printf '\n' >> /etc/bash.bashrc
    cat >> /etc/bash.bashrc << 'EOF'
# >>> personal-features >>>
export STARSHIP_CONFIG=/usr/local/share/starship.toml
export HISTFILE=/usr/local/share/shell-history/bash_history
export HISTSIZE=10000
export HISTFILESIZE=100000
shopt -s histappend
# mempalace's mine root, resolved at container-create time by
# resolve-mempal-dir (the workspace path is unknowable at image-build time, so
# it cannot be a containerEnv value - #485). Sourced rather than exported here
# so every shell picks up the resolved value, and Claude Code - and hence the
# mempalace hooks it spawns - inherits it.
[ -r /usr/local/share/personal-features/mempal-dir.sh ] && . /usr/local/share/personal-features/mempal-dir.sh
command -v starship >/dev/null 2>&1 && eval "$(starship init bash)"
command -v zoxide >/dev/null 2>&1 && eval "$(zoxide init bash)"
command -v bat >/dev/null 2>&1 && alias cat=bat
command -v fd >/dev/null 2>&1 && alias find=fd
command -v eza >/dev/null 2>&1 && alias ls=eza
# Must come last: `starship init` and `zoxide init` both *overwrite*
# PROMPT_COMMAND, so setting this any earlier silently loses the flush and
# history would only be written on a clean exit.
PROMPT_COMMAND="history -a${PROMPT_COMMAND:+;$PROMPT_COMMAND}"
# <<< personal-features <<<
EOF
fi
