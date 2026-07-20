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

# fetch(url, dest): download url to dest, retrying transient failures. Fails
# loudly (non-zero exit) so callers can react.
fetch() {
    curl -fsSL --retry 3 --retry-delay 2 -o "$2" "$1"
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

CLAUDE_HOME="/usr/local/share/claude-home"

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

# --- Claude consulting skills (feature-owned namespace) ---------------------
# Stage the shipped Claude skills at a build-time location that is NOT under the
# CLAUDE_CONFIG_DIR bind mount, then install sync-claude-skills to publish them
# into the live (mounted) $CLAUDE_CONFIG_DIR/skills at container-create time
# (wired up as a feature-contributed postCreateCommand in the Feature JSON).
# Writing them straight into CLAUDE_CONFIG_DIR here would be pointless: the
# host's ~/.claude bind mount shadows that directory at runtime. See
# sync-claude-skills and skills/README.md.
SKILLS_SRC="$(dirname "$0")/skills"
SKILLS_STAGE="/usr/local/share/personal-features/skills"
# Rebuild the staging dir from scratch so a re-provision can't leave a skill
# removed upstream lingering here. `cp -R "$SKILLS_SRC/."` copies the directory
# *contents*, so this stays correct - and non-fatal under set -e - whether the
# source has no skills yet (just its README) or is fully populated.
rm -rf "$SKILLS_STAGE"
mkdir -p "$SKILLS_STAGE"
if [ -d "$SKILLS_SRC" ]; then
    cp -R "$SKILLS_SRC/." "$SKILLS_STAGE/"
fi

# sync-claude-skills: at runtime (postCreateCommand) copies each staged skill
# into $CLAUDE_CONFIG_DIR/skills, replacing only the feature-owned names.
# Installed like create-pr.
install -m 0755 "$(dirname "$0")/sync-claude-skills" /usr/local/bin/sync-claude-skills

# --- Claude Code lifecycle hooks (#327) -------------------------------------
# claude-event-hook: the hook shim invoked by every feature-owned hook entry; it
# forwards each Claude Code lifecycle event to `odoo-sdk log-event`. Installed to
# /usr/local/bin (outside the CLAUDE_CONFIG_DIR bind mount) so it is always on
# PATH at runtime. sync-claude-hooks: at runtime (postCreateCommand) merges the
# feature-owned hooks block into the live, mounted $CLAUDE_CONFIG_DIR/
# settings.json — the build-time directory is shadowed by the ~/.claude mount,
# same reason as the skills sync above.
install -m 0755 "$(dirname "$0")/claude-event-hook" /usr/local/bin/claude-event-hook
install -m 0755 "$(dirname "$0")/sync-claude-hooks" /usr/local/bin/sync-claude-hooks

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

export PATH
npm install -g @anthropic-ai/claude-code

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
cat > "$WRAPPER_PATH" << EOF
#!/bin/sh
set -e

REAL="$REAL_CLAUDE_BIN"

if [ \$# -eq 0 ] && [ -t 0 ]; then
    exec "\$REAL" --ide
else
    exec "\$REAL" "\$@"
fi
EOF
chmod +x "$WRAPPER_PATH"

# --- Python libraries -------------------------------------------------------
# Wheels are bundled into this feature at release time. Installed into an
# isolated uv-managed venv to avoid touching system cryptography, which would
# break pyOpenSSL on odoo:17 (cryptography 41+ uses OpenSSL 3.x CFFI bindings
# that removed X509_V_FLAG_NOTIFY_POLICY). odoo_sdk requires Python 3.10+;
# skip silently on older images (e.g. odoo:16).
FEATURE_DIR="$(dirname "$0")"
if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
    # Give uv's downloads plenty of headroom: the odoo_sdk dependency tree pulls
    # large wheels (onnxruntime 17.8 MiB, numpy 15.9 MiB, ...) that can exceed
    # uv's default 30s HTTP timeout on a slow link and fail the whole image
    # build. Paired with the retry() wrapper on the install calls below.
    export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-300}"
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
        [ -d "$_SDK_ENV" ] || retry uv venv "$_SDK_ENV"
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
        for _entry in "${_SDK_ENTRYPOINTS[@]}"; do
            if [ ! -x "/usr/local/bin/$_entry" ] || ! command -v "$_entry" >/dev/null 2>&1; then
                echo "ERROR: odoo_sdk console script '$_entry' is not executable on PATH after install (expected $_SDK_ENV/bin/$_entry -> /usr/local/bin/$_entry)." >&2
                echo "The bundled wheel installed but did not provide this entry point; hooks and CLI tooling that depend on it would silently no-op, so failing the build instead." >&2
                exit 1
            fi
        done
    fi

    # mempalace: global cross-project memory palace, auto-mined via Claude Code
    # hooks (MEMPAL_DIR below). Install its venv under the same shared uv tools
    # dir as odoo-sdk and link the entry point onto /usr/local/bin so it lands
    # on every user's PATH (install.sh runs as root, so uv's default ~/.local
    # would be root-only). Best-effort - a PyPI/network hiccup shouldn't fail
    # the whole build, matching the other optional-tool installs.
    UV_TOOL_DIR=/usr/local/share/uv/tools UV_TOOL_BIN_DIR=/usr/local/bin \
        retry uv tool install mempalace \
        || echo "WARNING: failed to install mempalace, skipping" >&2
else
    echo "WARNING: Python 3.10+ required for odoo_sdk (fastmcp dependency); skipping on $(python3 --version 2>&1 || echo 'unknown Python')" >&2
fi

# --- Claude Code integrations: MCP server + mempalace plugin (#486, #484) ----
# sync-claude-mcp registers the odoo-mcp MCP server and the mempalace plugin at
# user scope. Installed to /usr/local/bin and run at container-create time
# (postCreateCommand), NOT here: $CLAUDE_HOME is shadowed at runtime by the
# feature's bind mount of the host's ~/.claude, so a registration written during
# the image build is discarded the moment the mount goes live. That is why the
# previous build-time `claude mcp add` never showed up in the persisted config
# (#486) - the write landed in the image layer the mount then covered.
cat > /usr/local/bin/sync-claude-mcp << 'EOF'
#!/bin/sh
# sync-claude-mcp - register the feature-owned Claude Code integrations (the
# odoo-mcp MCP server and the mempalace plugin) at user scope, idempotently.
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
fi

# mempalace ships its Claude Code hooks (Stop/SessionEnd/PreCompact) as a
# plugin; without this registration the palace is installed and on PATH but
# nothing ever mines into it (#484).
if command -v mempalace >/dev/null 2>&1; then
    if claude plugin list 2>/dev/null | grep -q mempalace; then
        echo "sync-claude-mcp: plugin 'mempalace' is already installed"
    elif claude plugin install --scope user mempalace; then
        echo "sync-claude-mcp: installed plugin 'mempalace'"
    else
        echo "WARNING: sync-claude-mcp: failed to install the 'mempalace' plugin" >&2
    fi
fi
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
# Pre-create it owned by the remote user: postCreateCommand runs as that user,
# who cannot otherwise write under /usr/local/share.
: > "$MEMPAL_ENV_FILE"
chown "$_REMOTE_USER" "$MEMPAL_ENV_FILE"
chmod 0644 "$MEMPAL_ENV_FILE"

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
    echo "Without it no shell picks the value up and mempalace mines nothing. Check the file's ownership (install.sh chowns it to the remote user)." >&2
    exit 1
fi
echo "resolve-mempal-dir: MEMPAL_DIR=$RESOLVED"
EOF
chmod 0755 /usr/local/bin/resolve-mempal-dir

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
# lazygit, qsv, coderabbit, starship). Each job already warns and exits 0 on its own failure;
# wait on each PID and guard it so an unexpected non-zero exit degrades to a
# warning instead of aborting the build under set -e. (A bare `wait` returns 0
# regardless, which would instead silently mask such a failure.)
for _pid in "${_download_pids[@]}"; do
    wait "$_pid" || echo "WARNING: a background download job failed" >&2
done

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
