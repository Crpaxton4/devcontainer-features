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
`ODOO_LS_ENTERPRISE`, `ODOO_LS_WORKSPACE` and `ODOO_LS_PYTHON`.

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
