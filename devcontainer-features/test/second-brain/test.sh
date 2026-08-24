#!/bin/bash

# This test file is executed against an auto-generated devcontainer.json that
# includes the 'second-brain' Feature alone (it has no options - the mount
# shape is fixed; see src/second-brain/NOTES.md for why).
#
# PREREQUISITE: the Feature's mount source is ${localEnv:SECOND_BRAIN_DIR}, so
# SECOND_BRAIN_DIR must be exported (and the directory must exist) in the
# environment that runs the devcontainer CLI - the dedicated jobs in
# .github/workflows/test.yaml set it to a runner-local directory. Without it
# the container never comes up (empty bind source), so these checks cannot
# even run - which is itself the fail-fast behaviour the Feature guarantees.
#
# This test can be run with the following commands:
#
#    export SECOND_BRAIN_DIR="$(mktemp -d)"
#    devcontainer features test \
#               --features second-brain \
#               --skip-scenarios \
#               --base-image mcr.microsoft.com/devcontainers/base:ubuntu \
#               /path/to/this/repo

set -e

# shellcheck source=/dev/null  # dev-container-features-test-lib is injected by the test harness at runtime; not resolvable statically. check()/reportResults() come from it.
source dev-container-features-test-lib

check "verify script is on PATH and executable" bash -c "test -x \"\$(command -v second-brain-verify)\""

# The container target must be a real mountpoint, not a plain directory the
# runtime materialised - silently mounting nothing is the failure mode the
# Feature exists to prevent.
check "knowledge base target exists" bash -c "test -d /mnt/second-brain"
check "knowledge base target is a real mountpoint" bash -c \
  "awk -v t=/mnt/second-brain '\$5 == t { found = 1 } END { exit !found }' /proc/self/mountinfo"
check "second-brain-verify passes against the live mount" second-brain-verify

# The mount is read-write by design (see NOTES.md: a read-only option is not
# representable in static Feature mounts). Round-trip a file to prove it.
check "knowledge base is writable and readable" bash -c \
  "marker=/mnt/second-brain/.test-write-\$\$; echo second-brain-rw > \"\$marker\" && grep -q second-brain-rw \"\$marker\" && rm \"\$marker\""

# In-container discovery variable points at the container-side path.
check "SECOND_BRAIN_DIR (containerEnv) points at the mount target" bash -c \
  "[ \"\$SECOND_BRAIN_DIR\" = '/mnt/second-brain' ]"

# Report result
reportResults
