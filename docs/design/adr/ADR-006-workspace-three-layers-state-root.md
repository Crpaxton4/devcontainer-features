# ADR-006 - One Workspace, Three Layers, One State Root

Status: Accepted

Date: 2026-10-27

Supersedes: ADR-005 (the layer model and the frozen-test-suite rule)

## Context

- The repository started as devcontainer features and grew into a tooling
  suite: a Python SDK with three front ends, a host-side dispatcher and
  orchestrator, a web UI, a usage log, two Claude plugins and three features.
- The 2026-10 audits found that the SDK's layer model (ADR-005) was enforced
  only on direct imports. The surfaces bypassed the command registry in more
  than twenty places, one MCP tool carried ~670 lines of git logic, and the
  "frozen test suite as regression oracle" rule of ADR-005 had produced fifteen
  shim modules, pinned lazy fallbacks and adapter bodies that could not move
  because tests patched module seams.
- Local state was split across ten stores under four host parents with five
  hand-copied path resolvers. Two task state machines existed (the SDK run
  FSM and the dispatcher job queue) and nothing linked them. Run records never
  reached sessionization inside a container.
- The dispatcher kept ~1,300 lines of storage inside the SDK, the web UI copied
  SQL schemas and path rules, and the plugin hooks carried three inline
  JavaScript parsers and a manifest-bump gate that fired on every commit.

## Decision

### Topology: a uv workspace of focused packages

```
pyproject.toml          uv workspace: one uv.lock, root import-linter contracts, dev tools
libraries/
  odoo_client/     DATA   the Odoo RPC client only: transport, client, records, query,
                          fields, env, settings, errors (renamed in 9.0, see "The rename")
  odoo_dev_data/   DATA   ODOO_DEV_HOME resolver, config.toml, state.db + migrations,
                          EventStore, JobStore, repo map, task records, locks,
                          doctor / migrate, git / github / chatter adapters
  odoo_dev_core/   LOGIC  command registry and builtins, ports, workflows,
                          sessionization, billing, telemetry
  dispatcher/      LOGIC  engine, lifecycle, admission, collect, dead letters,
                          reporting, queue, gates, reap (plus its `dispatch` CLI)
  usage_log/       DATA   stdlib only; writes $ODOO_DEV_HOME/usage.db and declarations.db
apps/
  odoo_cli/  (`odoo-dev`)   odoo_mcp/ (`odoo-mcp`)   odoo_tui/ (`odoo-tui`)   webui/
plugins/odoo-dev, plugins/odoo-orchestrator      packaging and the shell/agent surface
devcontainer-features/src/*                      packaging for devcontainers
stack/                                           packaging for the host (compose)
```

Every member has one `pyproject.toml` and declares its dependencies by name.
The root `pyproject.toml` holds the workspace, the `dev` dependency group, the
pytest configuration (`--import-mode=importlib`, `testpaths = ["libraries",
"apps"]`) and the four import-linter contracts below over all nine root
packages. The Sphinx documentation lives in the root `docs/` and has its own
`docs` dependency group.

### Rules, enforced by root import-linter contracts

1. **Apps never import a data package.** An app imports `odoo_dev_core` (and
   `dispatcher` for the web UI) only. The one exception per app is its
   `bootstrap` module, which is the only place that names data constructors.
   The CLI's `doctor` and `repo-map` subcommands are state-maintenance
   commands and are listed as permanent exceptions.
2. **Data never imports logic or apps.**
3. **Logic never imports apps.**
4. **Logic names no data constructor.** `odoo_dev_core` may import only the
   event vocabulary (`odoo_dev_data.events`) and the path constants
   (`odoo_dev_data.home`); everything else reaches it through the ports in
   `odoo_dev_core.ports` and the frozen `Services` bundle. Two permanent
   exceptions: `odoo_dev_core.ports` imports `odoo_dev_data.repo_map` and
   `odoo_dev_data.tasks` for type annotations only (under `TYPE_CHECKING`).

The apps contract sets `unmatched_ignore_imports_alerting = "error"`, so a stale
exception in its list fails the build. There is no layer contract
inside `odoo_dev_core`: the order `builtins` above `workflows` above `ports` is a
convention that the reviewer checks.

### The command registry

`odoo_dev_core.registry` keeps a string-keyed registry in the style of the VS
Code command API: `CommandSpec(id, handler, args, description, gated,
surfaces)`, the `@command` decorator, `default_registry()` and
`CommandService(registry, services).execute(id, **kwargs)`. A builtin is one
function `handler(services, args)` in `builtins/<id>.py`. It prints nothing and
resolves no configuration or state by itself. The MCP server generates its tools
from `registry.specs("mcp")`; the CLI and the TUI call `execute` and format.

The run state machine and its fourteen commands (start, stop, resume, abort and
close of a run, the run status and list commands, the run report, `reap` and
`prune`), both MCP prompts and the gated sixteen-tool MCP surface are deleted. The orchestrator pattern is the workflow: the implementation state
machine is the agent inside a dispatched job session, not a command.

### One state root

`ODOO_DEV_HOME` is the only state root. The host default is `~/.config/odoo-dev`;
every container sets it to the bind-mount target `/usr/local/share/odoo-dev`;
the compose stack mounts the host path at the same path.

```
config.toml                       user-edited [connection] [behavior] [model_ids]
repo-map.json                     user-edited, simplified schema
state.db                          ONE SQLite (WAL, numbered migrations): events,
                                  session_uploads, chatter_dedupe, settings, jobs
usage.db                          usage_log (stdlib), a separate file so usage_log
                                  stays independent of odoo_dev_data
locks/<name>.lock                 flock files (dispatcher singleton, per-task writes)
tasks/<task_id>/task.json         stage evidence and the ONE friction[] list
tasks/<task_id>/brief.md          the approved orchestrator brief (draft-N.md before)
tasks/<task_id>/jobs/<job_id>/    one bundle per dispatched job: envelope.json,
                                  session.jsonl, STOP
```

`odoo_dev_data.home` is the one resolver and `ensure_home` the one `mkdir`
site. `odoo-dev doctor` prints every path and the schema version;
`odoo-dev doctor --migrate` is the only migration path: it is idempotent, never
deletes a legacy store, prints each step and refuses while `jobs.db` holds
RUNNING rows.

Retired variables: `ODOO_DEV_STATE_DIR`, `ODOO_TASK_TRACKER_DIR`,
the SDK config-file variable, `ODOO_DISPATCH_STATE_DIR`, `DISPATCH_ARTIFACTS_DIR`,
`DISPATCH_REPO_MAP_FILE`, `ODOO_ORCHESTRATOR_HOME`, `USAGE_LOG_DIR`. Only
`odoo_dev_data.migrate` still reads them, to find legacy stores. The usage log
writes into the root when the root exists and never creates it;
`ODOO_DEV_USAGE_LOG=0` turns it off. At the date of this ADR the MCP server
(`odoo_mcp.server`) still tests `USAGE_LOG_DIR` before it declares its tools to
the usage log, so the retirement of that one variable is not complete.

### Boundaries: task, job, orchestrator, time

- A **task** is the Odoo task id. `tasks/<id>/` holds every human-readable
  artifact for it.
- A **job** is one dispatched run of a task: a row in `jobs` (queue state,
  batch, timestamps, outcome, tokens, cost, bundle index) plus one bundle
  directory under the task. There is no `runs.db`, `harvest.json` or
  `run_artifacts` table.
- The **orchestrator** owns no directory. Its lists are columns
  (`approved_us`, `reported_us`, `friction_recorded_us`, `batch_id`) and files
  under the task (`brief.md`, `draft-N.md`).
- **Time** has one record: the job row (`started_us`, `ended_us`, `task_id`,
  `question_count`). Sessionization reads job rows as attributed windows next
  to the sessionized hook, git, GitHub and chatter events. `task_note` and
  `task_question` post to chatter without an active-run check;
  `task_question` increments `jobs.question_count` when `ODOO_JOB_ID` is set.
  Branch and worktree handling belongs to the plugin (`worktree-ensure.sh`),
  never to a command.

### Repo map

One parser, `odoo_dev_data.repo_map`, exposed to shell as
`odoo-dev repo-map list|get|resolve|set|remove|validate|defaults --json`.

```json
{"defaults": {"branch_flow": ["task", "E2E", "UAT", "main"],
              "release_assignee": "", "release_reviewer": ""},
 "projects": {"<project name>": {"repo": "<folder under the repos dir, or an absolute path>",
   "odoo_version": "17.0", "aliases": [], "branch_flow": [],
   "upgrade_branch": {"19.0": "19-upgrade"},
   "release_assignee": "", "release_reviewer": ""}}}
```

Removed keys: `repo_path`, `default_branch` (the last element of the effective
flow), `flow_confirmed`, `remote`, `notes`. A reader tolerates a missing entry
and falls back to `git remote get-url origin` with a warning. Validation runs
on write only. Assignee and reviewer never block.

### Gates

The blocking gates that stay: irreversible git and GitHub actions, the tester
read-only allowlist, the write-scope and tree-pinning rules, production access.
The manifest bump runs once per pull request in `pr-open.sh`, not on every
commit. The STOP file is scoped to the job
(`tasks/<id>/jobs/$ODOO_JOB_ID/STOP`). The plugin has two hooks,
`bash-allowlist.sh` and `unattended-guard.sh`. Each is a 22-line bash wrapper
over `hooks/lib/*.js`, and both share one payload parser (`parse-payload.js`).
A wrapper runs the `usage-log` console script, a Python entry point, for one
best-effort usage record, and nothing else in the hook calls Python. When
`node` is missing, a wrapper denies if the guard is engaged (the payload names
`odoo-dev-tester`, or `ODOO_UNATTENDED` is set). When the guard code throws, it
denies.

### The rename

The pre-9.0 package name was a bad name for a package that held a client, a state
store, a command registry and three front ends. The client is `odoo_client` and
holds only client functionality. The CLI console script is `odoo-dev`; there is
no alias for the old console script. Google Calendar, Gmail and OAuth integration is dropped.

## Consequences

- Positive: one lock file, one state root, one time record, one resolver, one
  parser per format. The tracked line count fell from 140,293 to about 109,600
  (measured on the branch that moves the documentation, without changelogs, HTML,
  notebooks and lock files) while the test count stayed at 2,500.
- Positive: the layer rules are machine-checked across packages, not only
  inside one package. An app that reaches for a data constructor fails
  `lint-imports`.
- Negative: 9.0 is a breaking release. Every caller of the old console script, every
  retired variable and every legacy store needs the one-time
  `odoo-dev doctor --migrate`.
- Negative: WAL over a bind mount is correct on Linux. On Docker Desktop the
  `-shm` index is not coherent across the VM; `doctor` warns there and the
  TUI must run inside the container.
- The frozen-test-suite rule of ADR-005 is withdrawn. Tests move with the
  modules they test and are rewritten against the ports' in-memory fakes.
