# Base image for the 'on_odoo16_base_image' scenario (see ../scenarios.json).
#
# WHY THIS FILE EXISTS AT ALL
#
# odoo:16 is built on debian:bullseye-slim, and Debian 11 reached end of life on
# 2026-08-31. Its packages are being torn down from the live mirrors, so apt in
# this image started 404ing part-way through the container build (#688):
#
#   E: Failed to fetch http://deb.debian.org/debian-security/pool/updates/main/g/gnupg2/gpg_2.2.27-2+deb11u3_amd64.deb  404  Not Found
#   ERROR: Feature "GitHub CLI" (ghcr.io/devcontainers/features/github-cli) failed to install!
#
# github-cli is not listed in the scenario. 'personal-features' declares it in
# dependsOn, so every scenario installs it and there is no edit to the scenario's
# feature list that avoids that apt call - the sources have to be fixed in the
# image itself.
#
# And it has to happen HERE, in the image build. Features install as part of the
# build; onCreateCommand/postCreateCommand and the rest of the lifecycle only run
# afterwards, by which point the failed apt-get has already killed the build.
#
# WHY EOL ARCHIVES, RATHER THAN A NEWER BASE OR A RETIRED SCENARIO
#
# Deliberate, not a hack around a broken environment. Odoo 16 is outside Odoo
# SA's support window and its official image is - and will stay - bullseye;
# nobody is going to rebase it onto a supported Debian. So anyone still running
# Odoo 16 in production today is running it on an EOL Debian 11 whose packages
# come from exactly these archives. Testing against archived packages IS
# production fidelity for that audience, and it is what their next rebuild
# actually resolves.
#
# Do NOT "fix" this by modernising the base image or deleting the scenario. This
# leg is the only coverage of the oldest supported base, and specifically of the
# pre-3.10 system Python that on_odoo16_base_image.sh exists to prove the
# toolchain is independent of (#674).
FROM odoo:16

# odoo:16 ends on `USER odoo`; rewriting apt configuration needs root. The
# original user is restored at the bottom so this derived image's default user -
# which the devcontainer CLI reads when deciding containerUser - still matches
# odoo:16 exactly, and this scenario keeps behaving like the plain-image ones.
USER root

# The three sources, and why each one is where it is:
#
#  * main and updates come from archive.debian.org, Debian's frozen EOL archive.
#    bullseye there is the final 11.11 point release (2024-08-31) - there will
#    never be an 11.12 - and its pool is complete and immutable, which is the
#    whole point: nothing in it can be pruned out from under CI the way the live
#    mirrors just were.
#
#  * bullseye-security comes from a PINNED snapshot.debian.org timestamp, and it
#    is not optional. The image already carries packages NEWER than 11.11,
#    because it was built while bullseye still had LTS security support - so a
#    main-only archive leaves every companion package unsatisfiable. The node
#    feature trips over it immediately:
#
#      libc6-dev : Depends: libc6 (= 2.31-13+deb11u11) but 2.31-13+deb11u13 is to be installed
#
#    and there is nowhere else to get that glibc from. archive.debian.org's
#    debian-security tree still stops at buster - bullseye-security has not been
#    archived yet - and deb.debian.org's copy is the one actively being torn
#    down: its Release stopped being refreshed on 2026-08-31, expired on
#    2026-09-07, and its pool objects are already going missing. snapshot.debian.org
#    is the only permanent, immutable copy, and it serves the byte-identical
#    signed Release, so the keyring already in the image verifies it unchanged.
#
#    The 20260901T000000Z pin is the day after bullseye's LTS end, so it captures
#    the FINAL state of the security archive. Because bullseye is EOL nothing
#    newer can ever be published, so no future rebuild of odoo:16 can ship a
#    package this snapshot does not have - the pin cannot go stale, which is why
#    it is pinned rather than tracking "latest". If Debian eventually archives
#    the suite properly, this line can become:
#      deb http://archive.debian.org/debian-security bullseye-security main
#
#  * Only 'main', because that is all debian:bullseye-slim configured; adding
#    components here would test something the real base image does not have.
#
#  * /etc/apt/sources.list.d is left alone: odoo's own Dockerfile deletes the
#    pgdg list in the same RUN that uses it, so it is already empty and
#    sources.list is the whole story. (The postgresql feature adds its own list
#    at install time, and apt.postgresql.org still serves bullseye-pgdg.)
#
# Acquire::Check-Valid-Until is load-bearing here, not tidiness: that frozen
# bullseye-security Release carries `Valid-Until: Mon, 07 Sep 2026`, and an apt
# that honours it rejects the whole repository with "Release file ... is
# expired" - which lands us straight back on unsatisfiable glibc. Every archived
# suite acquires this problem sooner or later, so it is set once, globally,
# rather than per-source.
#
# Acquire::Retries because archive.debian.org and snapshot.debian.org are both
# single origin hosts with no CDN in front of them, and this runs on every PR.
#
# The apt lists baked into the image were fetched from the live mirrors, so they
# are dropped: a stale index would otherwise keep pointing at pool paths that no
# longer exist, which is the original failure.
#
# The trailing `apt-get update` is not decorative. It makes a broken archive fail
# right here with apt's own error message, instead of resurfacing hundreds of
# lines later as an opaque "Feature ... failed to install".
RUN set -eux; \
    printf '%s\n' \
        'deb http://archive.debian.org/debian bullseye main' \
        'deb http://archive.debian.org/debian bullseye-updates main' \
        'deb http://snapshot.debian.org/archive/debian-security/20260901T000000Z/ bullseye-security main' \
        > /etc/apt/sources.list; \
    printf '%s\n' \
        'Acquire::Check-Valid-Until "false";' \
        'Acquire::Retries "3";' \
        > /etc/apt/apt.conf.d/99archive-debian; \
    rm -rf /var/lib/apt/lists/*; \
    apt-get update

USER odoo
