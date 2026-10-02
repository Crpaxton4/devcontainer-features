#!/bin/bash

# This test file is executed against an auto-generated devcontainer.json that
# includes the 'debian-eol-archives' Feature alone, on several base images (see
# the test-debian-eol-archives job in .github/workflows/test.yaml):
#
#   debian:bullseye   EOL - the Feature must repoint apt and apt must then work
#   debian:bookworm   supported - the Feature must leave apt untouched
#   base:ubuntu       not Debian - the Feature must leave apt untouched
#
# The branches below key off the image's own /etc/os-release, so one script
# covers all three. The checks run as the image's default user; apt is wrapped
# so a non-root default user (the ubuntu base) still exercises it.
#
# This test can be run with:
#
#    devcontainer features test \
#               --features debian-eol-archives \
#               --skip-scenarios \
#               --base-image debian:bullseye \
#               /path/to/this/repo

set -e

# shellcheck source=/dev/null  # dev-container-features-test-lib is injected by the test harness at runtime; not resolvable statically. check()/reportResults() come from it.
source dev-container-features-test-lib

# shellcheck source=/dev/null  # the image's own /etc/os-release
. /etc/os-release

as_root() {
    if [ "$(id -u)" = 0 ]; then "$@"; else sudo -n "$@"; fi
}

case "${ID:-}/${VERSION_CODENAME:-}" in
    debian/bullseye)
        check "sources.list points main at archive.debian.org" \
            grep -q "^deb http://archive.debian.org/debian bullseye main" /etc/apt/sources.list
        check "sources.list points updates at archive.debian.org" \
            grep -q "^deb http://archive.debian.org/debian bullseye-updates main" /etc/apt/sources.list
        check "bullseye-security comes from the pinned snapshot" \
            grep -q "^deb http://snapshot.debian.org/archive/debian-security/20260901T000000Z/ bullseye-security main" /etc/apt/sources.list
        check "no live mirror remains in sources.list" \
            bash -c "! grep -Eq '^deb .*(deb|security)\.debian\.org' /etc/apt/sources.list"
        check "Check-Valid-Until is off for the frozen Release files" \
            grep -q 'Acquire::Check-Valid-Until "false";' /etc/apt/apt.conf.d/99debian-eol-archives
        # The point of all of the above: a package this image does not ship
        # (gnupg2 is the one docker-outside-of-docker died on) installs.
        check "apt can install a package from the archives" \
            bash -c "$(declare -f as_root); as_root apt-get update -qq && as_root apt-get install -y -qq --no-install-recommends gnupg2 >/dev/null && command -v gpg"
        ;;
    *)
        check "supported or non-Debian base: sources.list untouched" \
            bash -c "! grep -rqs 'archive.debian.org\|snapshot.debian.org' /etc/apt/sources.list /etc/apt/sources.list.d"
        check "supported or non-Debian base: no apt.conf fragment written" \
            bash -c "test ! -e /etc/apt/apt.conf.d/99debian-eol-archives"
        check "apt still works" \
            bash -c "$(declare -f as_root); as_root apt-get update -qq"
        ;;
esac

reportResults
