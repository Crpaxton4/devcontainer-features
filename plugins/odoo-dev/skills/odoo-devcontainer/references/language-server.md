# Odoo Language Server (odoo-ls)

Claude Code sessions in this container talk to upstream
[odoo-ls](https://github.com/odoo/odoo-ls) over LSP, which is where Odoo-aware
diagnostics, go-to-definition, references and hover for Python, XML and CSV come
from. Upstream flags the project "in development"; the kill switches below exist
because of that.

## What is installed where

| What                     | Path                                         |
| ------------------------ | -------------------------------------------- |
| Server binary            | `/usr/local/share/odoo-ls/odoo_ls_server`    |
| Typeshed stubs           | `/usr/local/share/odoo-ls/typeshed`          |
| Generated config         | `/usr/local/share/odoo-ls/odools.toml`       |
| Launcher (on PATH)       | `/usr/local/bin/odoo-ls-server`              |
| Config generator         | `/usr/local/bin/odoo-ls-config`              |
| Server logs              | `$TMPDIR/odoo-ls-logs` (default `/tmp`)      |

The binary is NOT `/usr/local/bin/odoo_ls_server`. It resolves its stdlib and
stub roots relative to its own path, so it lives next to the 35 MiB typeshed
tree instead of dragging that into `/usr/local/bin`. `odoo-ls-server` is the
launcher that goes on PATH, and it is what the plugin's `.lsp.json` names.

## How a session picks up a config

`odoo-ls-config` runs from the Feature's `postCreateCommand` and writes
`/usr/local/share/odoo-ls/odools.toml` from the container's real paths: the
community source as `odoo_path`, community + enterprise + `/mnt/extra-addons` as
`addons_paths`, and the checkout's `.venv` interpreter as `python_path`. It is
regenerated on every container create; **hand edits there are lost**.

The launcher then picks ONE config source per session:

1. If an `odools.toml` exists at, or anywhere above, the session's project
   directory, that file wins and the generated one is not passed at all.
2. Otherwise the generated one is passed with `--config-path`.
3. Neither? No server starts. The plugin registers `.py` for every project, not
   only Odoo ones, and without an `odoo_path` the server would index a whole
   tree to resolve no model, no field and no xmlid.

Never both. The server merges its config sources agree-or-error for scalar
settings, so passing a project file *and* the generated file turns a legitimate
override of `odoo_path` or `python_path` into a config error rather than an
override. The cost of the rule is that a project `odools.toml` has to be
self-contained — copy the generated file as the starting point:

```bash
cp /usr/local/share/odoo-ls/odools.toml /mnt/extra-addons/odools.toml
```

Task worktrees under `/mnt/extra-addons/.worktrees/` need no entry anywhere. The
server infers addon paths from the LSP workspace folder when the profile it
resolves for that folder sets none, and Claude Code sends the project directory
as that folder — so a session started inside a worktree indexes the worktree.
Listing them in the generated config would instead bake in paths that come and
go with every task, and a removed one is a hard config error.

The enterprise addons path is found by **probing** `/var/lib/odoo/addons/` for
series directories (`16.0`, `17.0`, …), not by trusting `$ODOO_VERSION`: the
variable picks between the ones that are there, and with it unset and exactly
one series present that one is used. Several present and no `$ODOO_VERSION`
(`sudo` strips it) is the one case that still omits the path, and it warns on
stderr naming what it found — set `ODOO_VERSION` or `ODOO_LS_ENTERPRISE` to
pick. The parent directory is never named: it is a directory of series
directories, not of modules.

## `startupTimeout` does not bound indexing

`startupTimeout: 60000` in `.lsp.json` bounds the LSP `initialize` handshake and
nothing else. The server answers `initialize` almost immediately and then builds
the workspace **asynchronously** — `Odoo: Indexing modules`, `Building
Database`, `core/build_scheduler.rs`. On a tree this size (300+ modules under
`/mnt/extra-addons`) that build is minutes, and raising `startupTimeout` does
not wait for it.

So an early `workspaceSymbol` or `goToDefinition` can come back **empty or
partial, and nothing in the reply distinguishes "that symbol does not exist"
from "not indexed yet"** — a wrong answer rather than a slow one. Before the
first query a session actually depends on, either warm up (one throwaway
`workspaceSymbol`, then re-ask) or wait for the indexing lines in the log
directory:

```bash
# $ODOO_LS_LOGS_DIR, default $TMPDIR/odoo-ls-logs; the 0777 directory next to
# the binary is the fallback when that one is not writable.
tail -F "$(ls -t /tmp/odoo-ls-logs/* /usr/local/share/odoo-ls/logs/* 2>/dev/null | head -1)" \
    | grep -m1 -E 'Building Database|Indexing modules'
```

Note that `ODOO_LS_LOG_LEVEL` defaults to `warn`, which drops those lines —
rerun the launcher at `info` to see them.

## Turning it off

Three switches, coarsest last:

| Want                                        | Do                                                                |
| ------------------------------------------- | ----------------------------------------------------------------- |
| Navigation, but no diagnostics in context   | `"diagnostics": false` in the plugin's `.lsp.json`                 |
| The server up but indexing nothing          | `"selectedProfile": "Disabled"` in `.lsp.json`'s `settings.Odoo`   |
| No server process at all                    | `ODOO_LS_DISABLE=1` in the environment, or disable the plugin      |

All three need the session restarted. `restartOnCrash` with `maxRestarts: 3`
covers a server that dies on its own; past three crashes Claude Code leaves it
stopped and the session carries on without it.

## Launcher environment overrides

| Variable            | Default                                    |
| ------------------- | ------------------------------------------ |
| `ODOO_LS_DISABLE`   | unset — set to anything to start nothing   |
| `ODOO_LS_BIN`       | `/usr/local/share/odoo-ls/odoo_ls_server`  |
| `ODOO_LS_CONFIG`    | `/usr/local/share/odoo-ls/odools.toml`     |
| `ODOO_LS_LOG_LEVEL` | `warn` (server's own default is `trace`)   |
| `ODOO_LS_LOGS_DIR`  | `$TMPDIR/odoo-ls-logs`                     |

`odoo-ls-config` takes `ODOO_LS_SKIP_CONFIG`, `ODOO_LS_ODOO_PATH`,
`ODOO_LS_ENTERPRISE`, `ODOO_LS_ENTERPRISE_ROOT` (default `/var/lib/odoo/addons`,
the directory the series dirs are probed in), `ODOO_LS_WORKSPACE` and
`ODOO_LS_PYTHON`.

## Facts worth not re-deriving

- **stdio is the default transport.** There is no `--stdio` flag; `--use-tcp` is
  what switches away from it, and passing a flag that does not exist is a
  startup error.
- **Logs never reach stdout.** The server installs a stdout log subscriber only
  under `--parse` or `--use-tcp`; over stdio everything goes to the rolling file
  appender. stdout carries LSP protocol and nothing else, which is what Claude
  Code requires.
- **`--logs-directory` must already exist.** The server checks the path and
  silently falls back to `<binary dir>/logs` rather than creating it. The
  launcher creates the directory it names.
- **JS/OWL support is off.** The generated config sets `disable_javascript =
  true`: that half of the server shells out to `tsserver`, which is not
  installed here, and without this it reports the same diagnostic every session.
  Python, XML and CSV are unaffected — they are what the plugin registers.
- **The release binaries need glibc 2.34.** Debian 11 (bullseye), which every
  `odoo:16` image is, ships 2.31, so the dynamic loader rejects the binary
  before `main()` and before the server has any logging of its own (#993). The
  Feature smoke-tests `--version` at build time and *removes* a binary that will
  not run, so such a container reports `no Odoo language intelligence` once per
  session instead of crash-looping through `maxRestarts`. A bookworm-or-newer
  base is the fix; there is no language server on bullseye.
- **There is no on-disk index cache.** 1.6.0 builds the database from scratch
  per server process, and `--parse` cannot pre-seed an image (it writes
  diagnostics to `output.json` and exits). Amortizing the build would mean a
  long-lived daemon; see #993 for the `--use-tcp` caveats.

## Reading the diagnostics

Codes are `OLS<nnnnn>` with `source: "Odoo"`, e.g. `OLS02001` for an unresolved
`odoo.*` import and `OLS01001` for an unknown model reference. A wall of
`OLS02001` on `from odoo import ...` means the config is not resolving the Odoo
source — check `/usr/local/share/odoo-ls/odools.toml` exists and that a project
`odools.toml` is not shadowing it with a wrong `odoo_path`.

Server logs, when a session's language intelligence is silently missing:

```bash
ls -t /tmp/odoo-ls-logs/ | head -1     # newest log for this container
ODOO_LS_LOG_LEVEL=info odoo-ls-server  # rerun by hand at a louder level
```
