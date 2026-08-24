#!/usr/bin/env bash
set -euo pipefail

echo "Activating feature 'second-brain'"

# --- Why the SECOND_BRAIN_DIR fail-fast check is NOT here ------------------
# install.sh runs at IMAGE BUILD time, where neither the host environment nor
# the container's runtime bind mounts are visible, so the required "fail fast
# when SECOND_BRAIN_DIR is unset" behaviour cannot live in this script. It is
# provided by two layers instead:
#
#   1. The mount declaration itself. `${localEnv:SECOND_BRAIN_DIR}` in
#      devcontainer-feature.json expands to an empty string when the variable
#      is unset on the host, and Docker refuses an empty (or non-existent)
#      bind-mount source - container creation fails before any lifecycle hook
#      runs, albeit with Docker's own, less descriptive, error message.
#
#   2. `second-brain-verify`, installed below and wired as the Feature's
#      onCreateCommand - the earliest container-side lifecycle hook, i.e. the
#      first point at which runtime mounts are observable. It asserts the
#      target is a real mountpoint and prints the canonical
#      "ERROR: SECOND_BRAIN_DIR is not set on the host; ..." message
#      otherwise, catching any path where the container comes up without the
#      mount (e.g. a tool that drops mounts with empty sources instead of
#      erroring).

FEATURE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

install -m 0755 "$FEATURE_DIR/second-brain-verify" /usr/local/bin/second-brain-verify

echo "Feature 'second-brain' installed; mount verification runs at container creation (onCreateCommand)."
