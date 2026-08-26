#!/bin/bash

# This test file is executed against an auto-generated devcontainer.json that
# includes the 'second-brain' Feature alone (it has no options - the mount
# shape is fixed; see src/second-brain/NOTES.md for why).
#
# PREREQUISITE: the Feature's mount source is ${localEnv:SECOND_BRAIN_DIR}, so
# SECOND_BRAIN_DIR must be exported (and the directory must exist) in the
# environment that runs the devcontainer CLI - the dedicated jobs in
# .github/workflows/test.yaml set it to a runner-local directory. Without it
# the container never comes up (the harness uses `docker run`, which refuses
# an empty bind source), so these checks cannot even run - which is itself the
# fail-fast behaviour the Feature guarantees on that path.
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
# ... and a HOST bind, not a container volume: on dockerComposeFile configs an
# empty SECOND_BRAIN_DIR silently yields an anonymous volume, whose mount root
# (mountinfo field 4) has the <...>/volumes/<name>/_data shape.
check "knowledge base target is backed by a host bind, not a container volume" bash -c \
  "awk -v t=/mnt/second-brain '\$5 == t { r = \$4 } END { exit (r ~ /(^|\/)volumes\/[^\/]+\/_data$/) }' /proc/self/mountinfo"
check "second-brain-verify passes against the live mount" second-brain-verify

# The mount is read-write by design (see NOTES.md: a read-only option is not
# representable in static Feature mounts). Round-trip a file to prove it.
check "knowledge base is writable and readable" bash -c \
  "marker=/mnt/second-brain/.test-write-\$\$; echo second-brain-rw > \"\$marker\" && grep -q second-brain-rw \"\$marker\" && rm \"\$marker\""

# In-container discovery variable points at the container-side path.
check "SECOND_BRAIN_DIR (containerEnv) points at the mount target" bash -c \
  "[ \"\$SECOND_BRAIN_DIR\" = '/mnt/second-brain' ]"

# --- second-brain-verify against mountinfo fixtures ----------------------------
# The live mount above only ever exercises the success path. Each fixture below
# is a one-line /proc/self/mountinfo stand-in (fields: id parent maj:min ROOT
# MOUNTPOINT opts - fstype source superopts) fed in through the script's
# test-only SECOND_BRAIN_VERIFY_MOUNTINFO override, so every rejection branch
# is proven without provisioning a container per case.
FIXTURES="$(mktemp -d)"
ANON_ID=7ce3c2b58351c474a3ab0841c13113c8856f3fce2c5950da7bdb08f6609048ac
line() { printf '100 1 259:2 %s /mnt/second-brain rw,relatime - ext4 /dev/nvme0n1p2 rw\n' "$1"; }
line "/var/lib/docker/volumes/$ANON_ID/_data"                         > "$FIXTURES/anonymous-volume"
line "/var/lib/docker/volumes/kb/_data"                               > "$FIXTURES/named-volume"
line "/home/u/.local/share/containers/storage/volumes/kb/_data"       > "$FIXTURES/podman-volume"
line "/volumes/$ANON_ID/_data"                                        > "$FIXTURES/unprefixed-volume"
line "/home/user/Documents/SecondBrain"                               > "$FIXTURES/host-bind"
line '/home/user/My\040Notes'                                         > "$FIXTURES/host-bind-escaped"
{ line "/var/lib/docker/volumes/$ANON_ID/_data"; line "/home/user/Documents/SecondBrain"; } > "$FIXTURES/over-mounted"
printf '1 0 259:2 / / rw,relatime - ext4 /dev/nvme0n1p2 rw\n'         > "$FIXTURES/target-absent"

# verify_fixture <fixture> <expected exit code> <expected output substring>
verify_fixture() {
  local fixture=$1 want_status=$2 want_text=$3 out status=0
  out="$(SECOND_BRAIN_VERIFY_MOUNTINFO="$fixture" second-brain-verify 2>&1)" || status=$?
  if [ "$status" -ne "$want_status" ]; then
    echo "expected exit $want_status, got $status; output: $out" >&2
    return 1
  fi
  if ! grep -qF -- "$want_text" <<<"$out"; then
    echo "expected output to contain '$want_text'; output: $out" >&2
    return 1
  fi
}

check "verify rejects an anonymous Docker volume" \
  verify_fixture "$FIXTURES/anonymous-volume" 1 "anonymous container volume"
check "verify rejects a named Docker volume" \
  verify_fixture "$FIXTURES/named-volume" 1 "container volume (kb)"
check "verify rejects a Podman volume" \
  verify_fixture "$FIXTURES/podman-volume" 1 "container volume (kb)"
check "verify rejects a volume whose filesystem root is the volumes dir" \
  verify_fixture "$FIXTURES/unprefixed-volume" 1 "anonymous container volume"
check "verify accepts a host bind and reports its mount root" \
  verify_fixture "$FIXTURES/host-bind" 0 "(mount root: /home/user/Documents/SecondBrain)"
check "verify unescapes mountinfo octal escapes in the mount root" \
  verify_fixture "$FIXTURES/host-bind-escaped" 0 "(mount root: /home/user/My Notes)"
check "verify honours the last mount on the target (over-mount)" \
  verify_fixture "$FIXTURES/over-mounted" 0 "(mount root: /home/user/Documents/SecondBrain)"
check "verify fails with the canonical message when nothing is mounted" \
  verify_fixture "$FIXTURES/target-absent" 1 "SECOND_BRAIN_DIR is not set"

# Report result
reportResults
