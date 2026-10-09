#!/bin/bash

# Executed against the 'on_odoo16_base_image' scenario in scenarios.json.
# odoo:16 is Debian 11 (bullseye) and ships only Python 3.9.2 - below odoo_sdk's
# requires-python (>=3.10). install.sh used to gate the ENTIRE Python block on
# the base image's python3, so this image silently got no uv, no tool env, no
# odoo-sdk/odoo-mcp/odoo-tui and no mempalace, surfacing weeks later as a
# confusing ENOENT from a stale bind-mounted MCP registration (#674).
#
# Since #674 the venv carries its own uv-managed CPython (pinned in install.sh),
# so the base image's Python is irrelevant and odoo:16 gets the FULL toolchain.
# This scenario is the canonical proof of that interpreter independence: the
# system Python here is the oldest of any supported base image, so if the
# toolchain works here, the pin is doing its job everywhere.
#
# Since 2026-10-02 it is also the proof that personal-features builds on an EOL
# Debian base at all: the scenario image hands the Features odoo:16's real,
# torn-down mirrors, and the debian-eol-archives dependency has to repair them
# before docker-outside-of-docker, github-cli and node apt-get (#688). Postgres
# here is PGDG's postgresql-17 from apt-archive.postgresql.org, installed by
# the scenario Dockerfile with a pq-init.sh that keeps the postgresql Feature's
# contract (apt.postgresql.org dropped bullseye-pgdg); the checks are unchanged.
#
# And since #993 it is the proof that the Odoo language server degrades LOUDLY
# on an EOL base rather than silently: bullseye ships glibc 2.31, the pinned
# odoo-ls release binary needs 2.34, and every other leg runs on bookworm or
# trixie - so this is the only place that failure can be caught at all.

set -e

# shellcheck source=/dev/null  # dev-container-features-test-lib is injected by the test harness at runtime; not resolvable statically. check()/reportResults() come from it.
source dev-container-features-test-lib

# Claude Code: the primary feature output
check "claude is on PATH and executable" bash -c "test -x \"\$(command -v claude)\""
check "claude reports a version" claude --version
check "wrapper injects --ide for default sessions" bash -c "grep -q -- '--ide' \"\$(command -v claude)\""

# Interpreter independence (#674): the base image still ships Python <3.10 -
# this is the precondition that makes the checks below meaningful. If odoo:16
# ever moves to a newer Python this scenario stops being the canonical
# old-Python proof and should be re-pointed at whatever image takes that role.
check "base image still ships the pre-3.10 system Python this scenario exists for" \
    bash -c "python3 -c 'import sys; assert sys.version_info < (3, 10), sys.version'"

# The full toolchain must now be present despite the old system Python.
check "uv is installed" bash -c "command -v uv"
check "odoo-sdk tool env exists" bash -c "test -d /usr/local/share/uv/tools/odoo-sdk"

# The tool env must run the uv-managed pinned interpreter, not the system 3.9
# (which could not even import the SDK's dependency tree).
check "tool env runs the pinned uv-managed CPython 3.11, not the system 3.9 (#674)" \
    bash -c "/usr/local/share/uv/tools/odoo-sdk/bin/python -c '
import sys
assert sys.version_info[:2] == (3, 11), sys.version
'"

# odoo_sdk: installed into the isolated tool environment.
check "odoo_sdk is importable in tool env" \
    bash -c "/usr/local/share/uv/tools/odoo-sdk/bin/python -c 'import odoo_sdk'"

check "odoo_sdk core API is accessible in tool env" \
    bash -c "/usr/local/share/uv/tools/odoo-sdk/bin/python -c '
from odoo_sdk import (
    OdooClient,
    OdooConnectionSettings,
    OdooExecutor,
    OdooRecordset,
    Domain,
    DomainExpression,
)
'"

# All three console scripts must be linked onto PATH (#496): odoo-sdk is what
# claude-event-hook shells out to, odoo-mcp is what the MCP registration spawns
# (the ENOENT in #674), and odoo-tui is the operator TUI (#120).
check "odoo-sdk console script is on PATH" bash -c "command -v odoo-sdk"
check "odoo-sdk entrypoint is executable" bash -c "test -x \"\$(command -v odoo-sdk)\""
check "odoo-mcp console script is on PATH" bash -c "command -v odoo-mcp"
check "odoo-mcp entrypoint is executable" bash -c "test -x \"\$(command -v odoo-mcp)\""
check "odoo-tui console script is on PATH" bash -c "command -v odoo-tui"
check "odoo-tui entrypoint is executable" bash -c "test -x \"\$(command -v odoo-tui)\""

# System-Python isolation guard: the isolated install must leave the Debian
# packages untouched - odoo:16's own pyOpenSSL must keep importing from the
# system 3.9 exactly as before.
check "system OpenSSL is intact (isolated install didn't touch cryptography)" \
    python3 -c "from OpenSSL import SSL, crypto"

# --- the Odoo language server on an EOL base (#993) ---------------------------
# This is the ONLY bullseye leg, and bullseye is where odoo-ls fails: the pinned
# release binaries are linked against glibc 2.34 and this base ships 2.31, so
# the dynamic loader rejects the binary before main() and before the server has
# any logging of its own. Nothing detected that - the other legs run on bookworm
# and trixie, where the binary runs, so CI was green while this image shipped a
# server that could not start and a log directory that stayed empty.
#
# install.sh now runs the installed binary once at build time and REMOVES it when
# it will not execute. So two outcomes are correct here and both pass: no binary
# at all (today, on bullseye), or a binary that really runs (a future
# ODOO_LS_VERSION with a lower glibc floor, which is the better outcome and must
# not be blocked by this check). The one state that must never pass is an
# INSTALLED binary that cannot execute - that is what crash-looped a session
# through maxRestarts and left nothing to debug it with.
# shellcheck disable=SC2016  # single quotes are deliberate: $bin is the inner script's own variable and must not be expanded by this file.
check "odoo-ls either runs here or was not installed at all - never a binary that cannot execute" \
    bash -c '
bin=/usr/local/share/odoo-ls/odoo_ls_server
if [ -x "$bin" ]; then
    "$bin" --version || {
        echo "FAIL: $bin is installed but will not execute on bullseye; the build-time smoke test in install_odoo_ls should have removed it (#993)"
        exit 1
    }
    echo "odoo-ls runs on this base image - the glibc 2.34 floor no longer applies here"
else
    echo "odoo-ls was not installed, as expected on bullseye: the pinned release binary needs glibc 2.34 and this base ships 2.31"
fi'

# Degrading has to be LOUD but not fatal. The launcher is what Claude Code
# execs, and a non-zero exit from it reads as a crash and burns the restart
# budget for a container that simply has no language server - so it must still
# be on PATH, still say why on stderr, and still exit 0.
check "odoo-ls-server is still on PATH even with no server binary" \
    bash -c "test -x /usr/local/bin/odoo-ls-server"
check "odoo-ls-server warns about the missing server and exits 0" \
    bash -c "ODOO_LS_BIN=/definitely/not/here /usr/local/bin/odoo-ls-server 2>&1 >/dev/null | grep -q 'no Odoo language intelligence'"

# The second half of #993, independent of the glibc problem: odoo-ls-config runs
# from postCreateCommand as the remote user (uid 1002 here, not root) and
# publishes odools.toml by writing a temp file into this directory and moving it
# into place. Root-owned 0755 made that a guaranteed Permission denied, and the
# generator warns and exits 0 by design - so create reported success while every
# LSP call in the session died on a 60-second initialization timeout.
check "the odoo-ls share directory is writable by any uid (the config generator writes there)" \
    bash -c "[ \"\$(stat -c '%a' /usr/local/share/odoo-ls)\" = '777' ]"
check "odoo-ls-config is on PATH and parses" \
    bash -c "test -x /usr/local/bin/odoo-ls-config && sh -n /usr/local/bin/odoo-ls-config"

check "postgresql starts and is ready" /usr/local/share/pq-init.sh

check "odoo postgresql role created" \
    bash -c "createuser -U postgres --superuser odoo"

check "odoo initializes base module without error" \
    bash -c "odoo -d odoo -i base --stop-after-init --db_host localhost --db_user odoo"

reportResults
