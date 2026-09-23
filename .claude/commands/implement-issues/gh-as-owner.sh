#!/usr/bin/env bash
#
# gh-as-owner.sh - delegator. The implementation moved into the Feature (#810).
#
# This file used to BE the wrapper. It still exists, and must keep existing,
# because every /implement-issues worker prompt names this exact path and a
# worker that cannot authenticate invents its own mechanism - which is the
# failure #769 and #828 each produced once already.
#
# The implementation now lives at
#   devcontainer-features/src/personal-features/gh-as-owner
# and is installed by that Feature to
#   /usr/local/bin/gh-as-owner
# so it is available to every session on the machine, not only to workers that
# happen to be running out of this repo's .claude/ directory. Arguments and
# exit statuses are passed through unchanged; see `gh-as-owner --help`.
#
# Resolution order, and why: the in-repo source first, because it is the
# build-time source of truth the installed copy is made from, and because a
# checkout under review must be tested by the code in that checkout. The
# installed program second, so this path still works from a checkout that
# predates the move or from a bare copy of the commands directory.
#
# Two full copies of the wrapper is the one outcome to avoid, so there is no
# fallback implementation here - if neither is present this fails loudly.

set -euo pipefail

self_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_source=$self_dir/../../../devcontainer-features/src/personal-features/gh-as-owner

if [[ -f $repo_source ]]; then
    exec bash "$repo_source" "$@"
fi

if [[ -x /usr/local/bin/gh-as-owner ]]; then
    exec /usr/local/bin/gh-as-owner "$@"
fi

printf 'gh-as-owner.sh: no gh-as-owner implementation found (looked for %s and /usr/local/bin/gh-as-owner)\n' \
    "$repo_source" >&2
exit 2
