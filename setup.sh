#!/bin/sh
# One-time host setup for personal-features bind mounts.
#
# Run this once per machine before starting any dev container that uses the
# personal-features Feature. It creates the host-side directories and files
# that are bind-mounted into the container, so Docker doesn't create them as
# root or as directories when they should be files.
#
# Safe to re-run: mkdir -p and touch are no-ops when the targets already exist.

set -e

echo "Creating host directories and files for personal-features bind mounts..."
mkdir -p "$HOME/.claude"
mkdir -p "$HOME/.config/gh"
touch "$HOME/.bash_history"
touch "$HOME/.zsh_history"

echo "Done. Host paths are ready for use."
