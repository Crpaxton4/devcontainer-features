# Base image for the 'on_odoo16_base_image' scenario (see ../scenarios.json).
#
# WHY THIS FILE EXISTS AT ALL
#
# odoo:16 is built on debian:bullseye-slim, and Debian 11 reached end of life on
# 2026-08-31. Two things rotted under this scenario as a result, and this file
# answers both without changing what the scenario proves:
#
#  1. deb.debian.org tore bullseye down: apt-get update still succeeds, every
#     .deb 404s (#688). Since 2026-10-02 that is personal-features' problem to
#     solve - it depends on the debian-eol-archives Feature, which repoints apt
#     at the archives before docker-outside-of-docker, github-cli and node run.
#     So this image deliberately hands the Features the REAL, broken, live
#     mirrors: the archive sources below are used only to install postgres and
#     are put back exactly as odoo:16 ships them at the bottom. If the
#     dependsOn stops working, this leg goes red. That is the point.
#
#  2. apt.postgresql.org dropped bullseye-pgdg (it lives on
#     apt-archive.postgresql.org now). The postgresql Feature the other odoo
#     scenarios use hardcodes apt.postgresql.org, falls back to a zip install
#     when the apt route fails, and leaves a dead pgdg.list behind that breaks
#     every later apt-get update - including personal-features' own. So on this
#     base postgres comes from PGDG's archive instead: postgresql-17, the same
#     major as the PGDG client odoo:16 already ships (Debian's own
#     postgresql-13 is not installable beside that client's postgresql-common),
#     with a /usr/local/share/pq-init.sh that keeps the Feature's contract so
#     on_odoo16_base_image.sh runs unchanged: trust auth, start, pg_isready.
#     Nothing was dropped from the checks.
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

# The three archive sources, and why each one is where it is:
#
#  * main and updates come from archive.debian.org, Debian's frozen EOL archive.
#    bullseye there is the final 11.11 point release (2024-08-31) - there will
#    never be an 11.12 - and its pool is complete and immutable.
#
#  * bullseye-security comes from a PINNED snapshot.debian.org timestamp, and it
#    is not optional. The image already carries packages NEWER than 11.11,
#    because it was built while bullseye still had LTS security support - so a
#    main-only archive leaves every companion package unsatisfiable
#    (libc6-dev : Depends: libc6 (= 2.31-13+deb11u11) but 2.31-13+deb11u13 is to
#    be installed). archive.debian.org's debian-security tree stops at buster;
#    snapshot.debian.org is the only permanent copy, serving the byte-identical
#    signed Release, so the keyring already in the image verifies it. The
#    20260901T000000Z pin is the day after LTS end: the FINAL state of the
#    suite, which can never change, so the pin cannot go stale.
#
#  * Only 'main', because that is all debian:bullseye-slim configured.
#
#  * bullseye-pgdg comes from apt-archive.postgresql.org, where PGDG parks the
#    suites it has retired from apt.postgresql.org. The signing key is already
#    trusted in the image (odoo:16 installed its client from PGDG).
#
# Acquire::Check-Valid-Until is load-bearing: that frozen bullseye-security
# Release carries an expired Valid-Until, and an apt that honours it rejects the
# whole repository. Acquire::Retries because archive and snapshot are single
# origin hosts with no CDN in front of them.
#
# The live sources.list is saved first and restored at the end (see point 1 in
# the header). postgresql-common's postinst creates the 17/main cluster at
# install time, which is what pq-init.sh starts.
RUN set -eux; \
    cp /etc/apt/sources.list /tmp/sources.list.live; \
    printf '%s\n' \
        'deb http://archive.debian.org/debian bullseye main' \
        'deb http://archive.debian.org/debian bullseye-updates main' \
        'deb http://snapshot.debian.org/archive/debian-security/20260901T000000Z/ bullseye-security main' \
        'deb https://apt-archive.postgresql.org/pub/repos/apt bullseye-pgdg main' \
        > /etc/apt/sources.list; \
    printf '%s\n' \
        'Acquire::Check-Valid-Until "false";' \
        'Acquire::Retries "3";' \
        > /etc/apt/apt.conf.d/99archive-debian; \
    rm -rf /var/lib/apt/lists/*; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends postgresql-17; \
    test -d /etc/postgresql/17/main; \
    mv /tmp/sources.list.live /etc/apt/sources.list; \
    rm -f /etc/apt/apt.conf.d/99archive-debian; \
    rm -rf /var/lib/apt/lists/*

# Same contract as the postgresql Feature's pq-init.sh (trust auth everywhere,
# start the cluster, wait for pg_isready, exec any trailing command), on
# Debian's cluster layout instead of a Feature-initialised $PGDATA.
COPY --chmod=0755 <<'EOF' /usr/local/share/pq-init.sh
#!/bin/sh
set -e
conf=/etc/postgresql/17/main
echo "listen_addresses = '*'" >> "$conf/postgresql.conf"
printf '%s\n' \
    'host    all all 0.0.0.0/0        trust' \
    'host    all all ::/0             trust' \
    'local   all all                  trust' \
    > "$conf/pg_hba.conf"
echo "Starting PostgreSQL..."
pg_ctlcluster 17 main start
pg_isready -t 60
exec "$@"
EOF

USER odoo
