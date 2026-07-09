#!/bin/sh
set -e

echo "Activating feature 'personal-features'"

# install.sh always runs as root. _REMOTE_USER/_REMOTE_USER_HOME come from the
# dev container CLI; default them so this also works under harnesses that run
# as root without setting them (e.g. this repo's own --remote-user root tests).
: "${_REMOTE_USER:=root}"
: "${_REMOTE_USER_HOME:=/root}"

CLAUDE_HOME="/usr/local/share/claude-home"
GH_CONFIG="/usr/local/share/gh-cli-config"
ODOO_SDK_CONFIG="/usr/local/share/odoo-sdk-config"
TASK_TRACKER_DIR="/usr/local/share/odoo-task-tracker"

# Create the fixed container-side paths that CLAUDE_CONFIG_DIR/GH_CONFIG_DIR
# point at and that the bind mounts overlay at runtime. Creating them here
# means the feature still works in test containers where no bind mounts are
# active (e.g. the devcontainer features test harness).
mkdir -p "$CLAUDE_HOME" "$GH_CONFIG" "$ODOO_SDK_CONFIG" "$TASK_TRACKER_DIR"
chown "$_REMOTE_USER" "$CLAUDE_HOME" "$GH_CONFIG" "$ODOO_SDK_CONFIG" "$TASK_TRACKER_DIR"

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

# Wrap the real binary so a plain session auto-connects to the IDE (`--ide`),
# while subcommands like `claude mcp` or `claude auth login` are left
# untouched since they don't all accept that flag.
cat > "$WRAPPER_PATH" << EOF
#!/bin/sh
set -e

REAL="$REAL_CLAUDE_BIN"

case "\$1" in
    update|install|auth|agents|attach|auto-mode|daemon|logs|mcp|plugin|plugins|project|remote-control|respawn|rm|setup-token|stop|kill|ultrareview)
        exec "\$REAL" "\$@"
        ;;
    *)
        exec "\$REAL" --ide "\$@"
        ;;
esac
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
        curl -fsSL https://astral.sh/uv/install.sh | sh
        unset UV_INSTALL_DIR
    fi
    for wheel in "$FEATURE_DIR"/odoo_sdk-*.whl; do
        [ -f "$wheel" ] || continue
        _SDK_ENV=/usr/local/share/uv/tools/odoo-sdk
        uv venv "$_SDK_ENV"
        uv pip install --python "$_SDK_ENV/bin/python" "$wheel"
        ln -sf "$_SDK_ENV/bin/odoo-mcp" /usr/local/bin/odoo-mcp
    done

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

# --- Additional personal tooling --------------------------------------------
# Opinionated, always installed - this Feature is the owner's own personal
# config, not a general-purpose toolkit, so none of this is optional. If a
# tool stops being useful here, remove it instead of gating it behind an
# option. Independent of the Claude/Node logic above: installs via apt or
# static binaries, no dependency on Node being present.

DPKG_ARCH="$(dpkg --print-architecture)" # amd64 | arm64
case "$DPKG_ARCH" in
    amd64) ARCH_DEB=amd64; ARCH_GNU=x86_64; ARCH_SHORT=x64 ;;
    arm64) ARCH_DEB=arm64; ARCH_GNU=aarch64; ARCH_SHORT=arm64 ;;
    *) echo "ERROR: unsupported architecture: $DPKG_ARCH" >&2; exit 1 ;;
esac

# Looks up the first release asset URL matching a regex against the latest
# GitHub release of $1.
gh_latest_asset_url() {
    curl -fsSL "https://api.github.com/repos/$1/releases/latest" \
        | grep -o '"browser_download_url": *"[^"]*"' \
        | cut -d'"' -f4 \
        | grep -E "$2" \
        | head -1
}

# Installs a GitHub release asset. With $3, downloads a single binary to that
# path; without $3, extracts a .tar.gz directly into /usr/local/bin.
# Best-effort: these are optional, non-Claude tools, so a failure here warns
# and continues rather than failing the whole install.
install_gh_release() {
    URL="$(gh_latest_asset_url "$1" "$2")"
    if [ -z "$URL" ]; then
        echo "WARNING: failed to install $1, skipping" >&2
        return
    fi
    if [ -n "${3-}" ]; then
        if curl -fsSL "$URL" -o "$3"; then
            chmod +x "$3"
        else
            echo "WARNING: failed to install $1, skipping" >&2
        fi
    else
        curl -fsSL "$URL" | tar -xz -C /usr/local/bin || echo "WARNING: failed to install $1, skipping" >&2
    fi
}

echo "Installing productivity/navigation CLI tools"
apt-get update -y
apt-get install -y --no-install-recommends ripgrep fd-find fzf bat jq

# Debian/Ubuntu's apt packages ship these under different binary names to
# avoid clashing with existing system commands.
[ -x /usr/local/bin/fd ] || ln -s "$(command -v fdfind)" /usr/local/bin/fd
[ -x /usr/local/bin/bat ] || ln -s "$(command -v batcat)" /usr/local/bin/bat

# Run all binary downloads in parallel — they're independent and each blocks
# on a GitHub API call + download, so sequential execution wastes wall time.
install_gh_release mikefarah/yq "yq_linux_${ARCH_DEB}\$" /usr/local/bin/yq &
install_gh_release eza-community/eza "eza_${ARCH_GNU}-unknown-linux-gnu\\.tar\\.gz\$" &
install_gh_release dbrgn/tealdeer "tealdeer-linux-${ARCH_GNU}-musl\$" /usr/local/bin/tldr &
( curl -fsSL https://raw.githubusercontent.com/ajeetdsouza/zoxide/main/install.sh | sh -s -- --bin-dir /usr/local/bin \
    || echo "WARNING: failed to install zoxide, skipping" >&2 ) &
install_gh_release gitleaks/gitleaks "linux_${ARCH_SHORT}\\.tar\\.gz\$" &

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

echo "Installing shell enhancements (Starship prompt, aliases, persisted history)"
( curl -fsSL https://starship.rs/install.sh | sh -s -- --bin-dir /usr/local/bin -y \
    || echo "WARNING: failed to install starship, skipping" >&2 ) &

# Wait for all background downloads (yq, eza, tldr, zoxide, gitleaks, starship)
wait

# Starship config: single-char Unicode symbols throughout (no emoji, no Nerd
# Font glyphs), extra modules useful for Odoo dev work.
cp "$(dirname "$0")/starship.toml" /usr/local/share/starship.toml

SHELL_HISTORY_DIR="/usr/local/share/shell-history"
mkdir -p "$SHELL_HISTORY_DIR"
touch "$SHELL_HISTORY_DIR/bash_history"
rm -f "$_REMOTE_USER_HOME/.bash_history"
ln -s "$SHELL_HISTORY_DIR/bash_history" "$_REMOTE_USER_HOME/.bash_history"
chown -R "$_REMOTE_USER" "$SHELL_HISTORY_DIR"

if ! grep -qF "# >>> personal-features >>>" /etc/bash.bashrc 2>/dev/null; then
    printf '\n' >> /etc/bash.bashrc
    cat >> /etc/bash.bashrc << 'EOF'
# >>> personal-features >>>
export STARSHIP_CONFIG=/usr/local/share/starship.toml
command -v starship >/dev/null 2>&1 && eval "$(starship init bash)"
command -v zoxide >/dev/null 2>&1 && eval "$(zoxide init bash)"
command -v bat >/dev/null 2>&1 && alias cat=bat
command -v fd >/dev/null 2>&1 && alias find=fd
command -v eza >/dev/null 2>&1 && alias ls=eza
# <<< personal-features <<<
EOF
fi
