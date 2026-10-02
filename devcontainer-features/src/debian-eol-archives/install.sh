#!/usr/bin/env bash
set -euo pipefail

echo "Activating feature 'debian-eol-archives'"

# --- What this is for --------------------------------------------------------
# When a Debian release leaves LTS, Debian removes it from the live mirrors
# (deb.debian.org / security.debian.org) and keeps it only in the frozen
# archives. The removal is not atomic: the Release files keep being served for a
# while, so `apt-get update` succeeds, and then every `apt-get install` 404s on
# the pool. That is what took out every Odoo 16 devcontainer rebuild on
# 2026-10-02 - odoo:16 is bullseye, bullseye left LTS on 2026-08-31, and the
# first Feature to run apt-get (docker-outside-of-docker) died with
#   E: Failed to fetch .../gnupg2_2.2.27-2+deb11u3_all.deb  404  Not Found
#
# Features install in dependency rounds, and this Feature is built to land in
# the first round ahead of the official devcontainers/features (see NOTES.md
# for exactly why the ordering holds), so by the time anything else apt-gets,
# apt already points at the archives.
#
# Everything below is gated on the image actually being an EOL Debian. On a
# supported release, or anything that is not Debian, it prints why and exits 0.

if [ ! -r /etc/os-release ]; then
    echo "debian-eol-archives: no /etc/os-release, so not Debian; nothing to do."
    exit 0
fi
# shellcheck source=/dev/null  # /etc/os-release is the image's own, not a file in this repo.
. /etc/os-release

if [ "${ID:-}" != "debian" ]; then
    echo "debian-eol-archives: ID=${ID:-?} is not debian; nothing to do."
    exit 0
fi
codename="${VERSION_CODENAME:-}"

# --- The EOL table -------------------------------------------------------------
# One case per release that has left LTS. Each sets:
#   archive_main      where <codename> and <codename>-updates live now
#   archive_security  where the security suite lives now
#   security_suite    the security suite's name (it changed shape over time:
#                     buster/updates, bullseye-security, ...)
#
# bullseye: main and updates are on archive.debian.org, Debian's permanent EOL
# archive. Security is NOT: archive.debian.org/debian-security stops at buster,
# so bullseye-security comes from a snapshot.debian.org timestamp instead. The
# pin is the day after LTS ended, which is the final state of that suite and is
# immutable (nothing newer can ever be published for an EOL release), so it
# cannot go stale. If Debian archives bullseye-security properly one day, this
# can become http://archive.debian.org/debian-security.
#
# bookworm (odoo:17, odoo:18) ages out around 2028; add its row here then.
case "$codename" in
    bullseye)
        archive_main="http://archive.debian.org/debian"
        archive_security="http://snapshot.debian.org/archive/debian-security/20260901T000000Z/"
        security_suite="bullseye-security"
        ;;
    *)
        echo "debian-eol-archives: Debian ${codename:-?} is not in the EOL table; apt sources left untouched."
        exit 0
        ;;
esac

sources=/etc/apt/sources.list

# Idempotent: an image whose sources already point at the archive (a base that
# shipped its own repair, or this Feature applied twice) is left alone.
if [ -f "$sources" ] && grep -q "^deb ${archive_main} ${codename} " "$sources"; then
    echo "debian-eol-archives: ${sources} already points at ${archive_main}; nothing to do."
    exit 0
fi

# Keep whatever components the image configured for its main suite (the slim
# bases configure only main; adding contrib/non-free here would be testing
# something the real image does not have).
components="main"
if [ -f "$sources" ]; then
    configured="$(awk -v s="$codename" '$1 == "deb" && $3 == s { $1 = ""; $2 = ""; $3 = ""; print; exit }' "$sources" | xargs || true)"
    if [ -n "$configured" ]; then
        components="$configured"
    fi
fi

printf '%s\n' \
    "deb ${archive_main} ${codename} ${components}" \
    "deb ${archive_main} ${codename}-updates ${components}" \
    "deb ${archive_security} ${security_suite} ${components}" \
    > "$sources"

# Any extra list file still naming the live mirrors for this release is
# disabled rather than deleted: apt would otherwise keep trying the dead pool
# for packages both sources carry.
for extra in /etc/apt/sources.list.d/*.list; do
    [ -f "$extra" ] || continue
    if grep -Eq "^deb .*(deb|security)\.debian\.org.* ${codename}[ /-]" "$extra"; then
        sed -i -E "s|^(deb .*(deb\|security)\.debian\.org.* ${codename}[ /-].*)$|# disabled by debian-eol-archives (EOL mirror): \1|" "$extra"
        echo "debian-eol-archives: disabled live-mirror entries for ${codename} in ${extra}"
    fi
done

# Check-Valid-Until is load-bearing, not tidiness: the frozen security Release
# carries an expired Valid-Until, and an apt that honours it rejects the whole
# repository ("Release file ... is expired"). Retries because archive and
# snapshot are single-origin hosts with no CDN in front of them.
printf '%s\n' \
    'Acquire::Check-Valid-Until "false";' \
    'Acquire::Retries "3";' \
    > /etc/apt/apt.conf.d/99debian-eol-archives

# The lists baked into the image were fetched from the live mirrors and still
# point at pool paths that no longer exist, which is the original failure.
rm -rf /var/lib/apt/lists/*

# Not decorative: a broken archive fails right here with apt's own message,
# instead of hundreds of lines later as an opaque "Feature ... failed to install".
apt-get update

echo "debian-eol-archives: Debian ${codename} is EOL; apt now reads ${archive_main} and ${archive_security}."
