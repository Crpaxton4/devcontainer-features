# Post-Rebuild Verification Checklist

Run after rebuilding a container, or on a new machine, before trusting the
delivery skills on real work. Every step is read-only or cleans up after itself.

Two contexts, and they need different things:

| Context | What it is | Sections |
|---|---|---|
| **Host** | Where the repos tree, docker, and the devcontainer CLI live. Worktrees and stacks are managed from here | all |
| **Devcontainer** | An Odoo container with the checkout bind-mounted at `/mnt/extra-addons` | 1, 4, 5, 6 |

Every command below names its script through `<plugin root>`. That is the plugin's
own directory: two levels above the absolute path on the `Base directory for this
skill:` line injected above the `odoo-devcontainer` skill body, because the base
directory's parent is `skills/` and its parent is the plugin root. Each skill's
scripts are then at `<plugin root>/skills/<skill name>/scripts`, and the plugin's
own at `<plugin root>/scripts`.

Substitute that absolute path into each command as you run it. A Bash call inherits
no environment and keeps no state from the call before it, so a variable set in one
call is empty in the next. A person working through this checklist in a terminal
can of course set one, and should use their own plugin root.

## 0. One-shot setup and preflight

```bash
<plugin root>/scripts/setup.sh --check   # report only; creates no state dir, installs nothing
<plugin root>/scripts/setup.sh           # take the safe unattended steps
```

Start here. `setup.sh` calls `bootstrap-state.sh` for you, so the state dir is
seeded if it is absent, and it finishes by running `preflight.sh` and printing its
JSON — setup and readiness in one pass. It verifies the tools every delivery skill
assumes (`node`, `git`, `gh` and its token scopes, the CodeRabbit CLI and its
login, `python3`, the headless browser; docker and the devcontainer CLI on the
host only), and it never logs in for you: anything interactive comes back as a
numbered list of commands to run yourself. Add `--yes` to also fetch the headless
browser (~100 MB, network) — without one, tours skip, the suite still reports
green, and the gate blocks on `tours_skipped`.

Want `ok: true` with empty `missing` and `manual_steps` arrays, or work the list
it prints before going further.

The two steps it wraps still stand alone, when only one of them is wanted:

```bash
<plugin root>/scripts/bootstrap-state.sh   # idempotent; seeds the state dir if absent
<plugin root>/skills/odoo-task-env/scripts/preflight.sh
```

`preflight.sh` runs every check rather than stopping at the first, and detects
which context it is in. `ok: true` and an empty `failures` array, or fix what it
names before going further. On the host it also reports the resolved `repos_dir`.

## 1. odoo-mcp

- `claude mcp list` shows `odoo-mcp` connected.
- `ODOO_SDK_CONFIG` (directory containing `config.ini`) and
  `ODOO_TASK_TRACKER_DIR` (holding `tracker.db`) are both set. The older
  `~/.odoo_sdk.ini` and `~/.config/odoo-task-tracker` paths are not used.
- One live round trip: `get_task` on a known task id returns identity fields.

MCP is not callable from bash, which is why `preflight.sh` does not check it.

## 2. Docker and the framework *(host)*

- `docker info` works, and `docker ps` shows the expected stacks:
  `<project>-odoo-1` per project, plus the shared `odoo-shared-proxy-1`,
  `odoo-shared-db-1`, `odoo-shared-pgweb-1`.
- `devcontainer --version` works.
- Stop stale stacks to reclaim RAM: `docker compose -p <project> stop` — volumes
  persist.

## 3. Repos mount, path parity *(host)*

The repos tree must be mounted with **container path == host path**, so worktree
`.git` pointer files and host-daemon bind mounts resolve identically from both
sides. Then:

```bash
git -C "$(REPOS_DIR= <plugin root>/skills/odoo-repo-map/scripts/repos-dir.sh --raw)"/<any repo> status
```

## 4. GitHub

```bash
gh auth status
gh pr list --repo <a real repo> --limit 1
```

## 5. Offline test suites

No network, no docker, no Odoo — these should pass anywhere:

One call runs every plugin gate — manifest, inventory, frontmatter limits, router
completeness, agent definitions, namespacing, stray feature-managed skills, every
offline suite, and shell syntax:

```bash
bash <plugin root>/scripts/validate.sh
```

Individually, if one of them fails and you want it alone:

```bash
bash <plugin root>/skills/odoo-repo-map/scripts/tests/repo-map.test.sh
bash <plugin root>/skills/odoo-task-env/scripts/tests/existing-work.test.sh
bash <plugin root>/skills/odoo-task-env/scripts/tests/task-env.test.sh
bash <plugin root>/skills/odoo-test-run/scripts/tests/run-tests.test.sh
bash <plugin root>/skills/odoo-pr/scripts/tests/pr-open.test.sh
bash <plugin root>/skills/odoo-release/scripts/tests/release-manifest.test.sh
bash <plugin root>/scripts/tests/gate.test.sh
bash <plugin root>/scripts/tests/setup.test.sh
```

`studio-inventory.test.sh` (under `odoo-upgrade/scripts/tests/`) additionally
needs a postgres it can create a database on; it skips cleanly without one.

## 6. Manual one-repo cycle

Proves the parts that only fail on real hardware. Inside a devcontainer, add
`--repo-path /mnt/extra-addons` to the first two commands.

1. **Resolve the project.** Unknown project must stop, not guess:
   ```bash
   <plugin root>/skills/odoo-repo-map/scripts/project-resolve.sh "<an Odoo project name>"
   ```
2. **Worktree, twice.**
   ```bash
   <plugin root>/skills/odoo-task-env/scripts/worktree-ensure.sh <repo> 999 smoke-test <default_branch>
   <plugin root>/skills/odoo-task-env/scripts/worktree-ensure.sh <repo> 999 smoke-test <default_branch>
   ```
   First returns `"status": "created"`, second `"reused"`. Confirm
   `.worktrees/` landed in `.git/info/exclude`, and (on the host) that the
   worktree is visible **inside** the project container at
   `/mnt/extra-addons/.worktrees/task-999`.
3. **Stack, twice.**
   ```bash
   <plugin root>/skills/odoo-task-env/scripts/stack-ensure.sh <repo>
   <plugin root>/skills/odoo-task-env/scripts/stack-ensure.sh <repo>
   ```
   Both `"reused"` — the second call is the point. Container names come from the
   compose project name (lowercased, stripped), so `QOC` runs as `qoc-odoo-1`;
   before that normalization the anchored `docker ps` filter never matched and
   every call fell through to `devcontainer up`. Inside a container this returns
   `"status": "in-container"` and does nothing, which is correct.
4. **Tests, with tours.**
   ```bash
   <plugin root>/skills/odoo-test-run/scripts/browser-ensure.sh
   <plugin root>/skills/odoo-test-run/scripts/run-tests.sh <repo> 999 <a module that has tours> --with-tours
   ```
   Confirms the addons-path override beats `/etc/odoo/odoo.conf`, that the
   project role can create and drop databases on the shared postgres
   (`db_dropped: true`), and — the part that silently regresses — that
   `tours_run` is greater than zero. Without a browser Odoo *skips* tours and
   reports the suite green, so a run where `tours_declared > 0` and
   `tours_run == 0` must come back `passed: false`.
5. **Prior-work discovery** (read-only, safe on a real repo):
   ```bash
   <plugin root>/skills/odoo-task-env/scripts/existing-work.sh <repo> <a task id with a known branch> <default_branch>
   ```
   Expect `state: resume` with a branch, or `complete` for merged work. It probes
   **both** `<id>#<slug>` (human) and `<id>-<slug>` (automation) forms. A
   `gh_error` is reported, never fatal.
6. **Adopting an existing branch.**
   ```bash
   <plugin root>/skills/odoo-task-env/scripts/worktree-ensure.sh <repo> <id> <slug> <default_branch> '<id>#<slug>'
   ```
   Expect `"status": "created"` on a worktree carrying that exact ref with its
   commits intact (`git -C <worktree> log --oneline <base>..HEAD`) and nothing
   reset.
7. **Release manifest, read-only.**
   ```bash
   <plugin root>/skills/odoo-release/scripts/release-manifest.sh <owner/repo> <repo_path> <from> <to>
   ```
   Opens and merges nothing. Check the `table_md`, and that PRs without a
   `[task NNN]` title land in `unresolved` rather than being guessed at.
8. **Clean up.**
   ```bash
   git -C <repo> worktree remove --force .worktrees/task-999
   git -C <repo> branch -D 999-smoke-test
   ```

## 7. Concurrency *(host, before running several tasks at once)*

`run-tests.sh` isolates every run explicitly — own database, own log file, own
HTTP port, `--max-cron-threads=0`, `--db_maxconn=8` — because several runs can
hit one shared odoo container at the same time. Confirm on live hardware:

1. **Two runs, one repo, different task ids.** Create both worktrees, then:
   ```bash
   <plugin root>/skills/odoo-test-run/scripts/run-tests.sh <repo> 998 <module> > /tmp/a.json 2>/tmp/a.err &
   <plugin root>/skills/odoo-test-run/scripts/run-tests.sh <repo> 999 <module> > /tmp/b.json 2>/tmp/b.err &
   wait
   ```
   Both must complete with non-zero `tests_run`, `db_dropped: true`, distinct
   `log_file` paths, and no port-binding or connection errors in stderr.

   Open question a real run has to settle: whether the gevent/longpolling port
   also needs a per-task value. Its flag name differs across Odoo 16–19, so the
   script deliberately does not guess. A bind failure on that port in stderr
   means adding the version-correct flag beside `--http-port`.
2. **Memory.** `docker stats --no-stream <repo>-odoo-1` during the pair, plus
   `free -g`. Both processes run `--workers=0`.
3. **Connections**, at peak:
   ```bash
   docker exec odoo-shared-db-1 psql -U postgres -tAc \
     "SELECT count(*), current_setting('max_connections') FROM pg_stat_activity"
   ```
   The count is fleet-wide (one postgres serves every project) and must stay well
   under `max_connections`. Adjust `DB_MAXCONN` if this says otherwise.
4. **Two `stack-ensure.sh` calls for the same repo with the stack down,
   concurrently.** Exactly one `devcontainer up` runs (host-global flock), both
   return, and both only once `pg_isready` and `odoo --version` succeed inside
   the container.

RAM safety rests on `stack-ensure.sh`'s `MIN_FREE_GB` eviction. Protect the
stacks you are using with `ODOO_ACTIVE_REPOS=repoA,repoB` — omit it and an
eviction can stop a stack someone else is testing on.
