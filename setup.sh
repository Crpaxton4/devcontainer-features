#!/bin/sh
# One-time host setup for personal-features dev container volumes.
#
# Run this once per machine (or after a factory reset) before starting any
# dev container that uses the personal-features Feature. It creates the three
# named Docker volumes and seeds them with the correct ownership for your user,
# so that the first container start doesn't race to claim them under the wrong
# uid.
#
# Safe to re-run: docker volume create is a no-op if the volume already exists,
# and the chown step only touches the root of each volume (not files inside it).

set -e

VOLUMES="
personal-features-claude-home
personal-features-gh-config
personal-features-shell-history
"

echo "Creating personal-features Docker volumes (no-op if they already exist)..."
for VOL in $VOLUMES; do
    docker volume create "$VOL"
done

echo "Setting ownership to $(id -u):$(id -g) on each volume root..."
for VOL in $VOLUMES; do
    docker run --rm \
        -v "${VOL}:/vol" \
        --user root \
        alpine \
        chown "$(id -u):$(id -g)" /vol
done

echo "Done. Volumes are ready for use."
