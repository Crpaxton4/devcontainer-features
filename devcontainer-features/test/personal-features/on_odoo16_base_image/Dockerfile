# Base image for the 'on_odoo16_base_image' scenario (see ../scenarios.json).
#
# WHY THIS FILE EXISTS AT ALL
#
# odoo:16 is built on debian:bullseye-slim, and Debian 11 has passed end of life:
# its packages are being pulled out of the live deb.debian.org pools, so apt in
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
# afterwards, by which point the failing apt-get has already killed the build.
#
# WHY archive.debian.org, RATHER THAN A NEWER BASE OR A RETIRED SCENARIO
#
# This is a deliberate choice, not a hack around a broken environment. Odoo 16 is
# outside Odoo SA's support window and its official image is - and will stay -
# bullseye; nobody is going to rebase it onto a supported Debian. So anyone still
# running Odoo 16 in production today is running it on an EOL Debian 11 whose
# packages come from exactly this archive. Testing against archived packages IS
# production fidelity for that audience, and it is what their next rebuild will
# actually resolve.
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

# Line by line, and why each is the way it is:
#
#  * main and updates come from archive.debian.org, Debian's frozen EOL archive.
#    bullseye there is the final 11.11 point release (2024-08-31); that pool is
#    complete and immutable, which is the entire point - nothing in it can be
#    pruned out from under CI later, the way the live mirrors just were.
#
#  * bullseye-security is dropped rather than repointed. archive.debian.org's
#    debian-security tree currently stops at buster, so there is no archived
#    bullseye-security to point at; listing one would 404 on the Release file and
#    fail `apt-get update` outright. The 11.11 point release folded the security
#    updates published up to its date into main, so what is genuinely lost is the
#    LTS-era deltas - acceptable for a CI base image, and the most the archive can
#    serve. If Debian later archives the suite, add it back as:
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
#  * Acquire::Check-Valid-Until is disabled because archived Release files are
#    frozen and never re-signed: the moment one carries a Valid-Until that has
#    passed, apt rejects the whole repository with "Release file ... is expired"
#    and no amount of correct URLs helps. bullseye's archived Release happens not
#    to carry Valid-Until today, so this is defensive - but it is the classic way
#    an archive.debian.org switch silently fails, and it costs nothing.
#
#  * Acquire::Retries because archive.debian.org is a single origin host with no
#    CDN in front of it, and this runs on every pull request.
#
#  * The apt lists baked into the image were fetched from deb.debian.org, so they
#    are dropped: a stale index would otherwise keep pointing at pool paths the
#    live mirrors no longer have, which is the original failure.
#
# The trailing `apt-get update` is not decorative. It makes a broken archive fail
# right here with apt's own error message, instead of resurfacing hundreds of
# lines later as an opaque "Feature ... failed to install".
RUN set -eux; \
    printf '%s\n' \
        'deb http://archive.debian.org/debian bullseye main' \
        'deb http://archive.debian.org/debian bullseye-updates main' \
        > /etc/apt/sources.list; \
    printf '%s\n' \
        'Acquire::Check-Valid-Until "false";' \
        'Acquire::Retries "3";' \
        > /etc/apt/apt.conf.d/99archive-debian; \
    rm -rf /var/lib/apt/lists/*; \
    apt-get update

USER odoo
