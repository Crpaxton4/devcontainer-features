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
# on the Node.js runtime provided by the official node Feature (installsAfter).
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
