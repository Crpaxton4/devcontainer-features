---
name: odoo-test-run
description: "Run an Odoo module's unit tests and HttpCase browser tours on a throwaway database and return machine-checkable evidence: tests executed, tours executed, failure lines. Use before a PR, after a fix, or when a green run never said how many tests ran."
user-invocable: false
---
# Odoo Test Run

Run module tests on disposable database. Report what happened in shape gate can read. Point not "did it print OK" — it **how many tests executed**, and **did tours run at all**.

## When to use

An Odoo module has to be proved to work before a pull request opens or after a fix; someone asks whether a module passes; a previous run reported green without saying how many tests it executed; tours are declared but are being skipped, or the headless run has no browser; or a gate needs numbers it can trust.

## Why the evidence is shaped this way

Odoo make two kinds of fake green easy:

- Run that install nothing and execute nothing print no failures. So `tests_run: 0` block PR. Unrecognized log format also report `0` — parsing change fail closed, not pass on assumption.
- **Tours skip silent when browser missing.** Every path in `odoo/tests/common.py` raise `unittest.SkipTest` — no Chrome, Chrome that never open devtools port, no `websocket-client` — and skip log at INFO, count as pass. Suite full of tours with no browser stay green forever, invisible.

So skill count `start_tour()` calls in module own source (`tours_declared`) *before* run anything. Declared tours plus zero executed tours = **failure**, not pass.

Live values, injected every time this skill is rendered — at invocation and at every
agent spawn that preloads it. Trust them; do not re-derive them.

- **Browser for tours**: !`bash ${CLAUDE_SKILL_DIR}/scripts/browser-ensure.sh --check 2>/dev/null || echo "browser-ensure.sh did not run — treat the tours as unproven"`

That line predicts the `tours_run: 0` gate failure before a run is spent on it. When
it reports no browser, `--with-tours` refuses and exits 6, and a run without the flag
leaves `tours_run` at 0, which the gate rejects for any module whose
`tours_declared` is above zero. Fix the browser first. The check never installs
anything, because it also runs at load.

## Preconditions

Worktree with code in it — from **`odoo-dev:odoo-task-env`**. Running stack, unless already inside one. `odoo-devcontainer/references/testing.md` for how to write tests being run.

## Scripts

This skill's scripts (`TEST_SCRIPTS`) live at `<base directory>/scripts`, where
`<base directory>` is the absolute path on the `Base directory for this skill:`
line injected above this body. The name is a label for that directory, not a shell
variable to set and reuse: every Bash call has to spell the absolute path out in
full, because a Bash call inherits no environment and keeps no state from the call
before it.

`${CLAUDE_SKILL_DIR}` in the injected bullet above is not a shell variable either.
The renderer substitutes it into this body before any shell sees it, and a Bash tool
call is not rendered, so the same spelling expands to nothing in one of your own
calls. Spell the base directory out in full there.

### Run the tests

```bash
<base directory>/scripts/run-tests.sh <repo> <task_id> <module> \
  [--test-tags T] [--db-suffix S] [--with-tours] [--repo-path DIR]
```

→ ```json
{"passed":true,"db":"...","module":"...","odoo_version":"18.0","mode":"container",
 "tests_run":12,"tours_declared":1,"tours_run":1,"tours_passed":1,
 "failures":[{"test":"...","error":"AssertionError: 2 != 1"}],
 "log_file":"/tmp/...","log_excerpt":"...","db_dropped":true}
```

The database is `<project>_test_<id><suffix>`, created fresh and dropped by a trap on every exit path. The worktree goes **first** on the addons path so its modules shadow the originals. Exit code is `0` whenever the run completed — **a test failure is a result, not a script error**, so read `passed`, never `$?`.

`--test-tags` takes Odoo's own grammar: `/module`, `/module:Class`, `/module:Class.method`, `-tag` to exclude, comma-separated.

Two execution modes, detected not configured: `host` (docker + a running `<project>-odoo-1`) and `container` (you are inside the stack). Exit 2 means neither is available.

### Run the tours

Add `--with-tours`. It resolves a browser first and **exits 6 rather than running** if there is none — running blind would produce a green report for tests that never executed.

```bash
<base directory>/scripts/browser-ensure.sh [--install | --check]
```

→ `{"browser_bin","source","version","websocket_client","screenshots_dir","screencasts_dir"}`

Search order mirror Odoo own: `$ODOO_BROWSER_BIN`, then `google-chrome`/`chromium`/`chromium-browser`/`google-chrome-stable` on PATH, then Playwright cache (`chrome-headless-shell` preferred). `--install` fetch one. Off by default — test run should not quiet download 100 MB.

Also check `websocket-client` importable — other silent-skip path — and prepare writable screenshot/screencast dirs, because devcontainer config point those at host bind mount that need not exist here.

`--check` answers the same question as one line of prose instead of JSON, installs
nothing whatever it finds, and always exits 0. It is what the injected bullet at the
top of this file runs, so the load path can report a missing browser without ever
pulling 100 MB over the network or failing the render.

## Reading the result

| Field | Gate rule |
|---|---|
| `tests_run` | **Must be > 0.** Zero never green, whatever `passed` say elsewhere |
| `tours_declared` vs `tours_run` | Declared > 0 and run == 0 ⇒ tours did not run. Re-run with `--with-tours` |
| `failures[].error` | **Extracted exception line** (`AssertionError: 2 != 1`). What you hand back to whoever fixes it |
| `log_excerpt`, `log_file` | **Internal only.** Never paste into PR body or Odoo chatter |

None of this is published. A PR body carries the task link, the module lists, and the per-module description, and nothing here belongs in it. Full logs stay at `log_file` for you.

## Typical use

```bash
# unit tests only, fast loop while fixing
<base directory>/scripts/run-tests.sh qocinnovations 30412 my_module --test-tags /my_module

# the run that gates the PR
<base directory>/scripts/run-tests.sh qocinnovations 30412 my_module --with-tours
```

Run the second one before `odoo-dev:odoo-pr`. Its JSON is what `gate.sh` reads and what `30-test.json` records. It gates the PR; it does not go in the PR body.

## Environment knobs

| Variable | Default | Why |
|---|---|---|
| `WORKTREE_ADDONS_BASE` | `/mnt/extra-addons` | Where worktree appear to Odoo process |
| `WORKTREE_SUBDIR` | `.worktrees` | Match `odoo-dev:odoo-task-env` |
| `TEST_DB_ROLE` | project name (host mode) | Postgres role that own test databases |
| `ODOO_SHARED_DB_CONTAINER` | `odoo-shared-db-1` | Host mode only |
| `ODOO_TEST_ARTIFACTS_DIR` | `$TMPDIR/odoo-test-run` | Where screenshots/screencasts land |
| `DB_MAXCONN`, `HTTP_PORT_BASE` | `8`, `18000` | Concurrency isolation |

## Verify

```bash
bash <base directory>/scripts/tests/run-tests.test.sh   # offline: stubbed odoo, canned logs, real extraction
```