# Orchestrator stack

A compose project (`odoo-orchestrator`) that runs the dispatcher in a container.
Start it through `plugins/odoo-orchestrator/scripts/dispatch.sh`.

## Host prerequisites

Docker with the compose plugin. Nothing else: Python, Node, the devcontainer CLI,
git and gh are in the image. The image builds on the first `up` or `run`.

## Variables

`ODOO_ORCHESTRATOR_CHECKOUT` is the host path of the checkout that holds
`stack/compose.yaml`. `dispatch.sh` needs it. It is also mounted, so
`DISPATCH_PLUGIN_ROOT` is `${ODOO_ORCHESTRATOR_CHECKOUT}/plugins/odoo-dev`.

`dispatch.sh` sets `ODOO_ORCHESTRATOR_UID`, `ODOO_ORCHESTRATOR_GID` and
`ODOO_ORCHESTRATOR_DOCKER_GID` (the group of `/var/run/docker.sock`). Set them
yourself when you call `docker compose` directly.

Required: `HOME`, `SECOND_BRAIN_DIR`, `DISPATCH_REPOS_DIR`, `DISPATCH_CONFIG_ROOT`,
`DISPATCH_REPO_OWNER`. An unset one stops compose with a message.

Optional, passed through when set: `DISPATCH_CLAUDE_HOME` (default `~/.claude`),
`ODOO_DISPATCH_STATE_DIR` (default `$XDG_STATE_HOME` or `~/.local/state`, then
`odoo-task-tracker`), `ODOO_ORCHESTRATOR_HOME` (default
`~/.local/state/odoo-orchestrator`), `MIN_FREE_GB`, `DISPATCH_SETTLE_S`,
`DISPATCH_POLL_S`, `DISPATCH_DEADLINE_S`, `DISPATCH_ADMIT_TIMEOUT_S`,
`DISPATCH_INVARIANTS_FILE`, `DISPATCH_ENTRY`, `DISPATCH_ADDITIONAL_FEATURES`,
`DISPATCH_ARTIFACTS_DIR`, `USERPROFILE`.

Compose also reads an optional `stack/.env`. Do not commit it.

## The identical-path rule

Every bind mount uses the same path on the host and in the container. The
devcontainer CLI and `docker` resolve bind sources on the host daemon, and the
dispatcher compares the host paths with Docker's bind sources. The mounts are the
checkout (read-only), the repos dir, the config root, the Claude home,
`~/.config/odoo-dev`, the dispatch state dir, the orchestrator home and
`~/.local/state/usage-log`. Do not nest one of these paths inside another.

## Web UI

`dispatch.sh` starts the `webui` service with the dispatcher. It stays up after the
dispatcher exits (`restart: unless-stopped`). Open `http://127.0.0.1:8765`. The port
is published on the loopback interface only. The pages are read-only:

- Jobs: every job by state, refreshed every 3 s, and a detail page per job with
  the run outcome, the token and cost figures and the files of its artifact dir.
- Queue: briefs and approved tasks in the orchestrator home that have no job yet.
- Friction: the `friction/<date>.md` files of the state dir and the orchestrator home.

The service mounts the state dir, the orchestrator home and the usage-log dir
read-write (a `:ro` bind breaks a WAL reader) and the artifacts dir read-only.
It does not mount the docker socket. Stop it with
`docker compose -p odoo-orchestrator stop webui`.

## Orchestrator helper

`plugins/odoo-orchestrator/scripts/orchestrator.sh` runs `orchestrator.py` in the
`orchestrator` service (`docker compose run --rm -T`, standard input passes
through). The service reuses the `dispatcher` image target, runs as the host
UID:GID and has no docker socket. It mounts three paths at identical paths: the
orchestrator home, the scripts dir of the plugin copy that holds the wrapper
(read-only, set as `ODOO_ORCHESTRATOR_SCRIPTS_DIR`) and the artifacts dir
(read-only). Without `ODOO_ORCHESTRATOR_SCRIPTS_DIR` the scripts dir of
`ODOO_ORCHESTRATOR_CHECKOUT` is used.

## Notes

- The container has no memory limit. The admission gate reads `/proc/meminfo`,
  which must show the host. Do not run on a host with lxcfs.
- The dispatcher container and the host share one kernel, so the SQLite WAL files
  in the state dir are safe to share. Do not put the state dir on a network share.
- `docker compose -p odoo-orchestrator stop dispatcher` stops the dispatcher. The
  jobs it was running become `KILLED_BY_SIGNAL` dead letters.
- `git ls-remote` of the reap step runs inside the dispatched devcontainer, which
  has its own credentials. The dispatcher container holds no git or gh credentials.
