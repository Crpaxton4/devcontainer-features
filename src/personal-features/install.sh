#!/bin/sh
set -e

echo "Activating feature 'personal-features'"

# install.sh always runs as root. _REMOTE_USER/_REMOTE_USER_HOME come from the
# dev container CLI; default them so this also works under harnesses that run
# as root without setting them (e.g. this repo's own --remote-user root tests).
: "${_REMOTE_USER:=root}"
: "${_REMOTE_USER_HOME:=/root}"

CLAUDE_HOME_VOLUME="/usr/local/share/claude-home"
GH_CONFIG_VOLUME="/usr/local/share/gh-cli-config"

# CLAUDE_CONFIG_DIR (set in devcontainer-feature.json) points Claude Code
# directly at the named volume mount — no symlinks needed for ~/.claude.
# gh CLI uses the same pattern via GH_CONFIG_DIR; ensure both volume dirs
# exist and are owned by the remote user so the tools can write to them at
# runtime (and so the dirs are present even when the volume isn't mounted,
# e.g. during feature tests).
mkdir -p "$CLAUDE_HOME_VOLUME" "$GH_CONFIG_VOLUME"
chown -R "$_REMOTE_USER" "$CLAUDE_HOME_VOLUME" "$GH_CONFIG_VOLUME"

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
NODE_MAJOR="$( [ -n "$NODE_BIN" ] && "$NODE_BIN" -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
if [ -z "$NODE_BIN" ] || [ "$NODE_MAJOR" -lt 18 ] 2>/dev/null; then
    echo "ERROR: personal-features requires Node.js >=18 on PATH to install @anthropic-ai/claude-code, but found: ${NODE_BIN:-no node on PATH} ($("$NODE_BIN" --version 2>/dev/null || echo 'n/a'))." >&2
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

# --- Additional personal tooling --------------------------------------------
# Opinionated, always installed - this Feature is the owner's own personal
# config, not a general-purpose toolkit, so none of this is optional. If a
# tool stops being useful here, remove it instead of gating it behind an
# option. Independent of the Claude/Node logic above: installs via apt or
# static binaries, no dependency on Node being present.

APT_UPDATED=false
apt_update_once() {
    if [ "$APT_UPDATED" = false ]; then
        apt-get update -y
        APT_UPDATED=true
    fi
}

DPKG_ARCH="$(dpkg --print-architecture)" # amd64 | arm64

# Maps DPKG_ARCH to the arch string a given tool's release assets use, e.g.
# `tool_arch amd64 arm64` echoes "amd64" or "arm64" depending on DPKG_ARCH;
# `tool_arch x86_64 aarch64` echoes the GNU-triple style some tools use.
tool_arch() {
    case "$DPKG_ARCH" in
        amd64) echo "$1" ;;
        arm64) echo "$2" ;;
    esac
}

# Looks up the first release asset URL matching a regex against the latest
# GitHub release of $1.
gh_latest_asset_url() {
    curl -fsSL "https://api.github.com/repos/$1/releases/latest" \
        | grep -o '"browser_download_url": *"[^"]*"' \
        | cut -d'"' -f4 \
        | grep -E "$2" \
        | head -1
}

# Installs a single-file GitHub release asset as an executable at $3.
# Best-effort: these are optional, non-Claude tools, so a failure here warns
# and continues rather than failing the whole install.
install_gh_release_bin() {
    URL="$(gh_latest_asset_url "$1" "$2")"
    if [ -z "$URL" ] || ! curl -fsSL "$URL" -o "$3"; then
        echo "WARNING: failed to install $3 from $1, skipping" >&2
        return
    fi
    chmod +x "$3"
}

# Installs a .tar.gz GitHub release asset by extracting it directly into
# /usr/local/bin. Same best-effort behavior as install_gh_release_bin.
install_gh_release_tar() {
    URL="$(gh_latest_asset_url "$1" "$2")"
    if [ -z "$URL" ] || ! curl -fsSL "$URL" | tar -xz -C /usr/local/bin; then
        echo "WARNING: failed to install $1, skipping" >&2
    fi
}

echo "Installing productivity/navigation CLI tools"
apt_update_once
apt-get install -y --no-install-recommends ripgrep fd-find fzf bat jq

# Debian/Ubuntu's apt packages ship these under different binary names to
# avoid clashing with existing system commands.
[ -x /usr/local/bin/fd ] || ln -s "$(command -v fdfind)" /usr/local/bin/fd
[ -x /usr/local/bin/bat ] || ln -s "$(command -v batcat)" /usr/local/bin/bat

install_gh_release_bin mikefarah/yq "yq_linux_$(tool_arch amd64 arm64)\$" /usr/local/bin/yq
install_gh_release_tar eza-community/eza "eza_$(tool_arch x86_64 aarch64)-unknown-linux-gnu\\.tar\\.gz\$"
install_gh_release_bin dbrgn/tealdeer "tealdeer-linux-$(tool_arch x86_64 aarch64)-musl\$" /usr/local/bin/tldr

curl -fsSL https://raw.githubusercontent.com/ajeetdsouza/zoxide/main/install.sh | sh -s -- --bin-dir /usr/local/bin \
    || echo "WARNING: failed to install zoxide, skipping" >&2

echo "Installing gitleaks for secret scanning"
install_gh_release_tar gitleaks/gitleaks "linux_$(tool_arch x64 arm64)\\.tar\\.gz\$"

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
curl -fsSL https://starship.rs/install.sh | sh -s -- --bin-dir /usr/local/bin -y \
    || echo "WARNING: failed to install starship, skipping" >&2

SHELL_HISTORY_VOLUME="/usr/local/share/shell-history"
mkdir -p "$SHELL_HISTORY_VOLUME"
for HIST in bash_history zsh_history; do
    touch "$SHELL_HISTORY_VOLUME/$HIST"
    rm -f "$_REMOTE_USER_HOME/.$HIST"
    ln -s "$SHELL_HISTORY_VOLUME/$HIST" "$_REMOTE_USER_HOME/.$HIST"
done
chown -R "$_REMOTE_USER" "$SHELL_HISTORY_VOLUME"

SNIPPET_BEGIN="# >>> personal-features >>>"
# $SHELL_HISTORY_VOLUME is expanded now (install time); $-escaped parts
# are left literal so they're evaluated later, in the user's shell.
SNIPPET="$(cat << EOF
$SNIPPET_BEGIN
command -v starship >/dev/null 2>&1 && eval "\$(starship init \$(basename "\$SHELL"))"
command -v zoxide >/dev/null 2>&1 && eval "\$(zoxide init \$(basename "\$SHELL"))"
command -v bat >/dev/null 2>&1 && alias cat=bat
command -v fd >/dev/null 2>&1 && alias find=fd
command -v eza >/dev/null 2>&1 && alias ls=eza
export HISTFILE="$SHELL_HISTORY_VOLUME/\$(basename "\$SHELL")_history"
# <<< personal-features <<<
EOF
)"

for RC_FILE in "$_REMOTE_USER_HOME/.bashrc" "$_REMOTE_USER_HOME/.zshrc"; do
    [ -f "$RC_FILE" ] || continue
    grep -qF "$SNIPPET_BEGIN" "$RC_FILE" 2>/dev/null && continue
    printf '\n%s\n' "$SNIPPET" >> "$RC_FILE"
    chown "$_REMOTE_USER" "$RC_FILE"
done
