# dispatcher

The host-side task dispatcher (LOGIC layer). It runs one Odoo task per
devcontainer session. It stores each job as a row in `state.db` and keeps the
files of each job in a bundle under `ODOO_DEV_HOME`. In production it runs in
the `dispatcher` service of the compose stack (see `stack/README.md`).

## Entry point

`dispatch` (`dispatcher.cli:main`, also `python -m dispatcher`).

| Verb | Does |
|---|---|
| `dispatch run ['<JSON array>']` | Submit the jobs, then admit and run until nothing is pending. With no argument it resumes the `PENDING` jobs. Takes the singleton lock. |
| `dispatch submit '<JSON array>'` | Write the `PENDING` rows and exit. Takes no lock. All or nothing: exit 2 on a duplicate task or on a repo with no checkout. |
| `dispatch report <batch_id>` | Print one batch report as JSON (read only). |
| `dispatch dead-letters` | Print the dead letters as JSON (read only). |
| `dispatch stop <job_id>` | Ask a running job to stop. Writes the `STOP` file in the job bundle. |

Each array element is `{"repo", "task", "brief"}` with an optional `"round"` and
`"entry"` (`/odoo-dev:implement` or `/odoo-dev:upgrade-implement`).
Exit codes: 0 the queue drained, 1 a job raised or one is still `PENDING`,
2 bad arguments or settings, 3 another dispatcher holds the lock.

## Settings (environment)

Required: `DISPATCH_REPOS_DIR` (or `REPOS_DIR`) and `DISPATCH_CONFIG_ROOT`.

| Variable | Default |
|---|---|
| `DISPATCH_REPO_OWNER` | unset. Picks one owner when a repo name exists under two. |
| `DISPATCH_ENTRY` | `/odoo-dev:implement` |
| `DISPATCH_PLUGIN_ROOT` | the `plugins/odoo-dev` directory |
| `DISPATCH_CLAUDE_HOME` | `~/.claude` |
| `DISPATCH_INVARIANTS_FILE` | `plugins/odoo-dev/prompts/unattended-invariants.md` |
| `DISPATCH_ADDITIONAL_FEATURES` | the `personal-features` Feature |
| `MIN_FREE_GB`, `DISPATCH_SETTLE_S`, `DISPATCH_POLL_S` | `6`, `5`, `15` |
| `DISPATCH_DEADLINE_S`, `DISPATCH_ADMIT_TIMEOUT_S` | `21600`, `1800` |

`ODOO_DEV_HOME` sets the state root. A second dispatcher needs a second root.

## GitHub account

The account the agents push and open pull requests as is `[github] account` in the state root `config.toml`, or `ODOO_GITHUB_ACCOUNT`. Log in both accounts to the host `gh` (`gh auth status` lists them). Do not use `gh auth switch`. At admission the dispatcher runs `gh auth token --user <account>` in the container. A job with no account, or with an account that has no token, is dead-lettered as `CONTAINER_START_FAILED` with the setting named.

## Job bundle

`$ODOO_DEV_HOME/tasks/<task_id>/jobs/<job_id>/` holds the files of one job. When
the job ends, the dispatcher writes `session.jsonl` (the transcript) and
`envelope.json` (the result of `claude -p`) into it. The `STOP` file appears when
`dispatch stop` was called. The agent writes `tasks/<task_id>/task.json` itself.
The dispatcher only checks that it exists.

## Dependencies

`odoo_dev_data`, `usage_log`. It imports no app (import-linter contract).
`dispatcher.reporting` is the read API that `webui` and the orchestrator use.

## Test

```
uv run --package dispatcher pytest libraries/dispatcher/tests
make -C libraries/dispatcher coverage    # fails below fail_under in pyproject.toml
```
