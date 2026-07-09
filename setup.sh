#!/bin/sh
# One-time host setup for personal-features bind mounts.
#
# Run this once per machine before starting any dev container that uses the
# personal-features Feature. Creates the host-side paths that are bind-mounted
# into the container. The tools may not be installed locally (only used inside
# dev containers), so their config dirs may not exist yet.
#
# Safe to re-run: mkdir -p and touch are no-ops when the targets already exist.

set -e

mkdir -p "$HOME/.claude"
mkdir -p "$HOME/.config/gh"
mkdir -p "$HOME/.config/pr-automation/projects"
touch "$HOME/.bash_history"
touch "$HOME/.zsh_history"

echo "Done. Bind mount paths are ready."
