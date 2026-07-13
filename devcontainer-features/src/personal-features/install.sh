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
# trailing slash marks a file (touch its parent, then the file). The odoo-sdk
# task-tracker *state* dir is deliberately absent from the manifest: chown'ing it
# to the build-time $_REMOTE_USER doesn't map to the runtime uid (the MCP server
# runs as odoo/uid 1002), leaving it unwritable at runtime (#115); it has no bind
# mount, so the SDK creates it lazily under the runtime user instead.
_MANIFEST="$(dirname "$0")/persisted-paths.tsv"
_TAB="$(printf '\t')"
while IFS="$_TAB" read -r _name _host_source _container_target _env_var _env_value _mode; do
    case "$_name" in ''|'#'*) continue ;; esac  # skip blank/comment lines
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
        [ -d "$_SDK_ENV" ] || uv venv "$_SDK_ENV"
        for wheel in "${_sdk_wheels[@]}"; do
            uv pip install --python "$_SDK_ENV/bin/python" "$wheel"
        done
        # Link the entry points once, after all wheels are installed.
        ln -sf "$_SDK_ENV/bin/odoo-mcp" /usr/local/bin/odoo-mcp
        ln -sf "$_SDK_ENV/bin/odoo-tui" /usr/local/bin/odoo-tui
    fi

    # mempalace: global cross-project memory palace, auto-mined via Claude Code
    # hooks (MEMPAL_DIR below). Install its venv under the same shared uv tools
    # dir as odoo-sdk and link the entry point onto /usr/local/bin so it lands
    # on every user's PATH (install.sh runs as root, so uv's default ~/.local
    # would be root-only). Best-effort - a PyPI/network hiccup shouldn't fail
    # the whole build, matching the other optional-tool installs.
    UV_TOOL_DIR=/usr/local/share/uv/tools UV_TOOL_BIN_DIR=/usr/local/bin \
        uv tool install mempalace \
        || echo "WARNING: failed to install mempalace, skipping" >&2
else
    echo "WARNING: Python 3.10+ required for odoo_sdk (fastmcp dependency); skipping on $(python3 --version 2>&1 || echo 'unknown Python')" >&2
fi

# Register the odoo-sdk MCP server at user scope so it's available in every
# container without users running `claude mcp add` by hand. Idempotent: skip if
# already registered (e.g. re-provisioned container over a persisted config).
if command -v claude >/dev/null 2>&1 && [ -x /usr/local/bin/odoo-mcp ]; then
    CLAUDE_CONFIG_DIR="$CLAUDE_HOME" claude mcp get odoo-sdk >/dev/null 2>&1 || \
        CLAUDE_CONFIG_DIR="$CLAUDE_HOME" claude mcp add --scope user odoo-sdk odoo-mcp
fi

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
case "$DPKG_ARCH" in
    amd64) ARCH_DEB=amd64; ARCH_GNU=x86_64;  ARCH_SHORT=x64;   ARCH_LAZYGIT=x86_64 ;;
    arm64) ARCH_DEB=arm64; ARCH_GNU=aarch64; ARCH_SHORT=arm64; ARCH_LAZYGIT=arm64  ;;
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

echo "Installing productivity/navigation CLI tools"
apt-get update -y
apt-get install -y --no-install-recommends ripgrep fd-find fzf bat jq unzip

# Debian/Ubuntu's apt packages ship these under different binary names to
# avoid clashing with existing system commands.
[ -x /usr/local/bin/fd ] || ln -s "$(command -v fdfind)" /usr/local/bin/fd
[ -x /usr/local/bin/bat ] || ln -s "$(command -v batcat)" /usr/local/bin/bat

# Run all binary downloads in parallel — they're independent and each blocks on
# a network download, so sequential execution wastes wall time. URLs are pinned
# and deterministic (see the version block above); no api.github.com calls.
# Collect their PIDs so we can wait on each individually (see the wait below).
_download_pids=()
install_gh_release yq \
    "https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_linux_${ARCH_DEB}" \
    /usr/local/bin/yq &
_download_pids+=("$!")
install_gh_release eza \
    "https://github.com/eza-community/eza/releases/download/v${EZA_VERSION}/eza_${ARCH_GNU}-unknown-linux-gnu.tar.gz" &
_download_pids+=("$!")
install_gh_release tealdeer \
    "https://github.com/dbrgn/tealdeer/releases/download/v${TEALDEER_VERSION}/tealdeer-linux-${ARCH_GNU}-musl" \
    /usr/local/bin/tldr &
_download_pids+=("$!")
# zoxide's upstream install.sh only resolves versions via api.github.com (no
# pin flag), so download the release tarball directly instead. It bundles man
# pages/completions/README alongside the binary, so extract just `zoxide`.
install_gh_release zoxide \
    "https://github.com/ajeetdsouza/zoxide/releases/download/v${ZOXIDE_VERSION}/zoxide-${ZOXIDE_VERSION}-${ARCH_GNU}-unknown-linux-musl.tar.gz" \
    "" zoxide &
_download_pids+=("$!")
install_gh_release gitleaks \
    "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_${ARCH_SHORT}.tar.gz" &
_download_pids+=("$!")
# delta (better git diffs) - its tarball nests LICENSE/README/delta under a
# top-level delta-<ver>-<arch> dir, so extract just the binary and strip that
# leading component (strip=1) to land it directly on /usr/local/bin. No aarch64
# musl asset is published, so use the gnu tarball for both arches. Wired up as
# git's pager via `git config --system` below.
install_gh_release delta \
    "https://github.com/dandavison/delta/releases/download/${DELTA_VERSION}/delta-${DELTA_VERSION}-${ARCH_GNU}-unknown-linux-gnu.tar.gz" \
    "" "delta-${DELTA_VERSION}-${ARCH_GNU}-unknown-linux-gnu/delta" 1 &
_download_pids+=("$!")
# lazygit (TUI git client) - ships the bare binary at the tarball root alongside
# LICENSE/README, so extract just `lazygit` (like zoxide).
install_gh_release lazygit \
    "https://github.com/jesseduffield/lazygit/releases/download/v${LAZYGIT_VERSION}/lazygit_${LAZYGIT_VERSION}_linux_${ARCH_LAZYGIT}.tar.gz" \
    "" lazygit &
_download_pids+=("$!")

# CodeRabbit CLI — not published as GitHub release assets, so use the upstream
# installer (https://cli.coderabbit.ai/install.sh) pinned to /usr/local/bin.
# CI=1 suppresses the interactive post-install login prompt; the installer's
# own PATH/profile edits are harmless no-ops here since it lands on a dir
# already on PATH. The vars must be exported (not just prefixed) so the
# installer's `sh` — a separate child process — actually inherits them. Auth is
# user-specific and persisted via the mount below, so it is deliberately not
# baked in. Best-effort like the tools above.
( export CODERABBIT_INSTALL_DIR=/usr/local/bin CI=1
    run_installer https://cli.coderabbit.ai/install.sh \
    || echo "WARNING: failed to install coderabbit, skipping" >&2 ) &
_download_pids+=("$!")

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
( run_installer https://starship.rs/install.sh --version "v${STARSHIP_VERSION}" --bin-dir /usr/local/bin -y \
    || echo "WARNING: failed to install starship, skipping" >&2 ) &
_download_pids+=("$!")

# Wait for all background downloads (yq, eza, tldr, zoxide, gitleaks, delta,
# lazygit, coderabbit, starship). Each job already warns and exits 0 on its own failure;
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
