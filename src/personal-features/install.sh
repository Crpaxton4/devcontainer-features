#!/bin/sh
set -e

echo "Activating feature 'personal-features'"

# install.sh always runs as root. _REMOTE_USER/_REMOTE_USER_HOME come from the
# dev container CLI; default them so this also works under harnesses that run
# as root without setting them (e.g. this repo's own --remote-user root tests).
: "${_REMOTE_USER:=root}"
: "${_REMOTE_USER_HOME:=/root}"

CLAUDE_HOME_VOLUME="/usr/local/share/claude-code-home"
GH_CONFIG_VOLUME="/usr/local/share/gh-cli-config"

# The volumes declared in devcontainer-feature.json mount at fixed,
# user-independent paths (Feature `mounts` can't reference ${localEnv:HOME}).
# Symlink the real per-user config locations into them so auth persists
# across rebuilds and across every project that uses this Feature.
mkdir -p "$CLAUDE_HOME_VOLUME/dot-claude" "$GH_CONFIG_VOLUME"
touch "$CLAUDE_HOME_VOLUME/dot-claude.json"

rm -rf "$_REMOTE_USER_HOME/.claude" "$_REMOTE_USER_HOME/.claude.json"
ln -s "$CLAUDE_HOME_VOLUME/dot-claude" "$_REMOTE_USER_HOME/.claude"
ln -s "$CLAUDE_HOME_VOLUME/dot-claude.json" "$_REMOTE_USER_HOME/.claude.json"

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
