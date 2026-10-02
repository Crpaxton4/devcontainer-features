## Contents

- [What it does](#what-it-does)
- [Why a separate Feature](#why-a-separate-feature)
- [Why it installs first](#why-it-installs-first)
- [The EOL table](#the-eol-table)
- [What it deliberately does not do](#what-it-deliberately-does-not-do)

## What it does

At image build time, before any other Feature runs `apt-get`, it reads `/etc/os-release` and, if the image is a Debian release that has left LTS, rewrites `/etc/apt/sources.list` to the frozen archives, disables live-mirror entries for that release in `/etc/apt/sources.list.d/*.list`, switches `Acquire::Check-Valid-Until` off, drops the baked apt lists and runs `apt-get update`. On a supported Debian release, or on anything that is not Debian, it prints why and does nothing.

The failure it exists for: Debian tears an EOL release down from `deb.debian.org` after its LTS ends, and the teardown is not atomic. The `Release` files keep being served, so `apt-get update` succeeds, while every `.deb` in the pool 404s. On 2026-10-02 that failed every Odoo 16 devcontainer rebuild (odoo:16 is bullseye, EOL 2026-08-31) in the first Feature that runs `apt-get`:

```
E: Failed to fetch http://deb.debian.org/debian-security/pool/updates/main/g/gnupg2/gnupg2_2.2.27-2%2bdeb11u3_all.deb  404  Not Found
ERROR: Feature "Docker (docker-outside-of-docker)" (ghcr.io/devcontainers/features/docker-outside-of-docker) failed to install!
```

## Why a separate Feature

`personal-features` declares `docker-outside-of-docker`, `github-cli` and `node` in `dependsOn`, and all three run `apt-get install`. Dependencies install **before** the Feature that depends on them, so nothing inside `personal-features/install.sh` can run early enough to repair apt for them. The repair has to be its own Feature, placed ahead of those three in the installation order. `personal-features` depends on this one for exactly that reason.

The same repair also belongs in the published base image (QOC-Innovations/.devcontainer#128 put it in `images/odoo/Dockerfile.base`). The two are not redundant: the image repair makes the image build; this Feature makes the Features build on any EOL Debian base, including an image built before the repair, a plain upstream `odoo:16`, or a base nobody maintains. On an already-repaired image it detects the archive sources and does nothing.

## Why it installs first

The dev container CLI resolves `dependsOn`/`installsAfter` into rounds: a Feature is placed in the first round in which everything it depends on is already placed. This Feature depends on nothing, so it is in round 1 alongside `docker-outside-of-docker`, `github-cli` and `node` (their only `installsAfter` is `common-utils`, which is dropped when not requested). Within a round the CLI orders Features by their resolved identifier: for OCI Features it compares `registry/namespace` first, so `ghcr.io/crpaxton4/...` sorts before `ghcr.io/devcontainers/...`; a local `./debian-eol-archives` (type `file-path`) sorts before every OCI Feature. Either way this Feature is first. That is the same rule that put `second-brain` ahead of `docker-outside-of-docker` in the failing build's log.

The ordering is therefore a property of the identifiers, not of anything declared here. The `odoo16_with_docker_outside_of_docker` scenario pins it: plain `odoo:16` plus this Feature plus `docker-outside-of-docker`, which only builds if this Feature really ran first.

## The EOL table

| Release | main, updates | security |
| --- | --- | --- |
| bullseye (Debian 11, odoo:16) | `http://archive.debian.org/debian` | `http://snapshot.debian.org/archive/debian-security/20260901T000000Z/` `bullseye-security` |

Security is on `snapshot.debian.org` because `archive.debian.org/debian-security` stops at buster. The snapshot timestamp is the day after bullseye's LTS end, i.e. the final state of that suite; nothing newer can ever be published for an EOL release, so the pin cannot go stale. `Acquire::Check-Valid-Until "false"` is required, not defensive: that frozen `Release` carries an expired `Valid-Until`, and an apt that honours it rejects the whole repository, which lands back on unsatisfiable packages (`libc6-dev` vs `libc6`, because the image carries packages newer than the final point release).

Adding a release is one `case` arm in `install.sh` plus a row here. bookworm (Debian 12: odoo:17, odoo:18) ages out around 2028.

## What it deliberately does not do

- It does not touch supported releases, so there is nothing to undo when a base image moves to a newer Debian.
- It does not handle the deb822 `/etc/apt/sources.list.d/*.sources` format: no EOL Debian uses it yet (bookworm introduced it), so support can arrive with bookworm's row.
- It does not keep `deb-src` lines; the base images do not configure any.
- It has no options. The archive locations are facts about Debian, not preferences, and this repo's Features are the owner's opinionated setup rather than configurable toolkits.
