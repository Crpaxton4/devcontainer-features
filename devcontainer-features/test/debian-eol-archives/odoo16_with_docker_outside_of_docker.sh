#!/bin/bash

# Executed against the 'odoo16_with_docker_outside_of_docker' scenario in
# scenarios.json: plain upstream odoo:16 (Debian 11 bullseye, EOL) with this
# Feature and docker-outside-of-docker. The build itself is the real test -
# docker-outside-of-docker apt-gets gnupg2 and ca-certificates from the
# bullseye-security pool that deb.debian.org no longer serves, so the image
# only builds if debian-eol-archives repointed apt before it ran. The checks
# below confirm the pieces are actually there.

set -e

# shellcheck source=/dev/null  # dev-container-features-test-lib is injected by the test harness at runtime; not resolvable statically. check()/reportResults() come from it.
source dev-container-features-test-lib

check "image is odoo:16 on bullseye" \
    bash -c '. /etc/os-release && [ "$VERSION_CODENAME" = bullseye ] && case "$ODOO_VERSION" in 16.*) ;; *) exit 1;; esac'

check "apt sources were repointed at the archives" \
    grep -q "^deb http://archive.debian.org/debian bullseye main" /etc/apt/sources.list
check "no live mirror remains in sources.list" \
    bash -c "! grep -Eq '^deb .*(deb|security)\.debian\.org' /etc/apt/sources.list"

# docker-outside-of-docker installed after us and succeeded.
check "docker CLI is installed (docker-outside-of-docker)" docker --version
check "gnupg2 (the package the 404 was on) is installed" bash -c "command -v gpg"

# And apt is still usable for whatever installs next.
check "apt can still install from the archives" \
    bash -c "apt-get update -qq && apt-get install -y -qq --no-install-recommends jq >/dev/null && command -v jq"

reportResults
