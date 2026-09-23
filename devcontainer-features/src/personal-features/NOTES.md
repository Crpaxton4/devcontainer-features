## Contents

- [Companion Features](#companion-features)
- [One-time host setup](#one-time-host-setup)
- [What persists, and where](#what-persists-and-where)
- [Windows and WSL](#windows-and-wsl)
- [Migrating shell history](#migrating-shell-history)
- [CodeRabbit CLI](#coderabbit-cli)
- [The `create-pr` command](#the-create-pr-command)
- [The `gh-as-owner` command](#the-gh-as-owner-command)
- [The `claude` command](#the-claude-command)
- [Claude Code lifecycle hooks (odoo-sdk event capture)](#claude-code-lifecycle-hooks-odoo-sdk-event-capture)
- [Odoo consulting skills (two delivery paths)](#odoo-consulting-skills-two-delivery-paths)
- [Python toolchain (odoo-sdk, odoo-mcp, mempalace)](#python-toolchain-odoo-sdk-odoo-mcp-mempalace)
- [The shared mempalace MCP hub](#the-shared-mempalace-mcp-hub)
- [The Odoo language server (odoo-ls)](#the-odoo-language-server-odoo-ls)
- [Additional tooling](#additional-tooling)

## Companion Features

This Feature `dependsOn` the official Node.js Feature, so it's installed automatically even if a consumer only adds `personal-features` (e.g. via `dev.containers.defaultFeatures`) — npm installs Claude Code, so a base image's own system Node can't be relied on (some base images, like Odoo's, bundle an ancient one ahead of it on PATH; `install.sh` also hard-fails with a clear error if it ends up on Node <18 for any reason).

Optionally pair it with the official GitHub CLI Feature too, so `gh auth login` has something to persist:

```jsonc
{
    "features": {
        "ghcr.io/devcontainers/features/github-cli:1": {},
        "ghcr.io/<owner>/<repo>/personal-features:1": {}
    }
}
```

## One-time host setup

Run the setup script once per machine, **before** starting any dev container that uses this Feature:

1. From the host you launch VS Code on, run the script for your platform:

   ```sh
   ./setup.sh      # Linux, WSL, macOS
   ```

   ```powershell
   .\setup.ps1     # native Windows host
   ```

   If PowerShell blocks it ("running scripts is disabled on this system"), use `powershell -ExecutionPolicy Bypass -File .\setup.ps1`.

2. Confirm it created the host-side directories that are bind-mounted into the container:

   ```
   ~/.claude                            — Claude Code auth and settings
   ~/.config/gh                         — gh CLI auth and settings
   ~/.config/odoo_sdk                   — odoo_sdk connection config
   ~/.config/pr-automation              — create-pr config (global.yaml + projects/)
   ~/.config/coderabbit                 — CodeRabbit VS Code extension state
   ~/.coderabbit                        — CodeRabbit CLI auth/state (auth.json, machine-id)
   ~/.config/devcontainer/shell-history — bash history
   ```

Re-running is safe — creating an existing directory is a no-op.

**This step is not optional.** A bind mount whose source doesn't exist on the host is a hard container-create failure, not a fallback:

```
docker: Error response from daemon: invalid mount config for type "bind":
bind source path does not exist: /home/you/.claude
```

## What persists, and where

The persisted paths below are defined once in `persisted-paths.tsv` (next to `install.sh`), the single source of truth: `install.sh` creates the container targets from it, `setup.sh` creates the host sources from it, and `.github/scripts/check_persisted_paths.py` fails CI if `devcontainer-feature.json` drifts from it. Adding a persisted path is a one-row edit to that manifest (plus the matching JSON mount/env, which the check enforces).

Config and history are bind-mounted from your host home directory into fixed container paths, so they survive container rebuilds, follow you across projects on the same machine, and are safe from `docker volume prune`:

- `~/.claude` (host) → `/usr/local/share/claude-home` (container) — `CLAUDE_CONFIG_DIR` points here, so Claude Code's auth and settings survive rebuilds.
- `~/.config/gh` (host) → `/usr/local/share/gh-cli-config` (container) — `GH_CONFIG_DIR` points here, so `gh auth login` only needs to happen once per machine.
- `~/.config/odoo_sdk` (host) → `/usr/local/share/odoo-sdk-config` (container) — `ODOO_SDK_CONFIG` points at this directory; the SDK probes it for `config.toml` then `config.ini`.
- `~/.config/pr-automation` (host) → `/usr/local/share/pr-automation` (container) — `PR_AUTOMATION_CONFIG_DIR` points here, so `create-pr` picks up your global and per-project PR config across rebuilds. Optional: `create-pr` still works with no config mounted.
- `~/.config/coderabbit` (host) → `/usr/local/share/coderabbit-config` (container) — `CODERABBIT_CONFIG_DIR` points here; it persists the VS Code CodeRabbit extension state (`user-data.json`). The CLI itself ignores `CODERABBIT_CONFIG_DIR` (#661).
- `~/.coderabbit` (host) → `/home/vscode/.coderabbit` (container) — CodeRabbit **CLI** auth/state (`auth.json`, `machine-id`). Unlike every other row, the target is a literal home path, not `/usr/local/share/...`: the CLI hardcodes its state dir as `join(homedir(), ".coderabbit")` and honors no env override, so the only way to persist its auth is to mount at exactly that path (#661). `/home/vscode` matches the primary consumers (`remoteUser: vscode`); under a root `remoteUser` the dir is provisioned but inert — the CLI resolves `/root/.coderabbit` there and auth does not persist.
- `~/.config/devcontainer/shell-history` (host) → `/usr/local/share/shell-history` (container) — `HISTFILE` points at `bash_history` inside it, and `~/.bash_history` is symlinked to it, so bash history follows you across rebuilds and is shared across containers. Bash is the only supported shell.

A note on the history mount: bash writes `HISTFILE` after every command, and the container user's uid need not match the owner of the host directory the mount exposes (e.g. with `"updateRemoteUserUID": false` and a non-root `remoteUser`, or from a root shell). To keep history writable regardless of uid, `setup.sh` makes the host `shell-history` directory world-writable (mode `0777`); the container inherits that mode through the bind mount, so any user can create and append `bash_history`. Without it, `history -a` fails on every command with `bash: history: .../bash_history: cannot create: Permission denied` (#323).

## Windows and WSL

Mount sources use the prefix `${localEnv:HOME}${localEnv:USERPROFILE}`. The spec has no conditional, so this relies on exactly one being defined — Windows sets `USERPROFILE`, Linux/WSL/macOS set `HOME` — and they concatenate into a valid path either way.

**If `HOME` is also set on Windows, both expand** and the mount source becomes garbage (`/c/Users/you` + `C:\Users\you` + `/.claude`), so the container fails to start. Git Bash sets `HOME` in its own shell (launching with `code .` from Git Bash leaks it); a persisted User/Machine `HOME` leaks into every launch. Fix by removing the persisted `HOME`, or launch VS Code from PowerShell or the Start menu. `setup.ps1` warns when it detects this.

Mounts resolve against whatever environment launches VS Code, not where the repo lives: open a folder through Remote-WSL and the container mounts the **WSL** home — so run `setup.sh` inside WSL, not `setup.ps1` in PowerShell.

## Migrating shell history

History is now a directory mount (`~/.config/devcontainer/shell-history`), not a single-file mount of `~/.bash_history` — Docker Desktop materialises a missing single-file source as a *directory*, which then fails the mount. So your old host history won't appear in the container, and container history no longer writes back to the host. To carry the old history over:

```sh
cp ~/.bash_history ~/.config/devcontainer/shell-history/bash_history
```

## CodeRabbit CLI

The [CodeRabbit CLI](https://docs.coderabbit.ai/cli) (`coderabbit`, alias `cr`) is installed to `/usr/local/bin` via the upstream installer (it isn't published as GitHub release assets, so the usual `install_gh_release` path doesn't apply). It powers the Claude Code CodeRabbit plugin (`/plugin install coderabbit`); plugin state persists for free via the existing `~/.claude` mount.

Authentication is user-specific and is deliberately **not** baked into the image. Run it once after the container is created:

```sh
coderabbit auth login                       # browser-based, or:
coderabbit auth login --api-key "$CODERABBIT_API_KEY"   # headless
```

To make `CODERABBIT_API_KEY` available inside the container from your host env, add it to `remoteEnv` in your `devcontainer.json`:

```jsonc
"remoteEnv": {
    "CODERABBIT_API_KEY": "${localEnv:CODERABBIT_API_KEY}"
}
```

Caveats:

- **Auth persists via the `~/.coderabbit` home-path mount (#661).** The CLI hardcodes its state dir as `join(homedir(), ".coderabbit")` (verified against v0.7.5) and reads **no** env override — not `CODERABBIT_CONFIG_DIR` (that only ever covered the VS Code extension state) and not `CODERABBIT_API_KEY` either (the headless docs' env var is plain shell interpolation into `--api-key`, not something the CLI reads itself). So the Feature bind-mounts host `~/.coderabbit` at `/home/vscode/.coderabbit`, and `coderabbit auth login` survives rebuilds under a `vscode` remoteUser. One-time migration: if you authenticated under the old volume-backed home, re-run `coderabbit auth login` once — pre-existing volume content at that path is shadowed by the bind mount, not merged (it only held `machine-id`/logs). Verify with `coderabbit auth status`.
- `.coderabbit.yaml` is a per-repo config file read by CodeRabbit's cloud service. It's authored by the user at the repo root and is **not** managed by this Feature.
- The Claude Code CodeRabbit plugin / Agentic API access may require a paid CodeRabbit plan — check your account's plan if `/coderabbit:review` reports auth or entitlement errors.

## The `create-pr` command

`create-pr` opens a pull request for the current branch with `gh pr create --fill --assignee @me`, applying config-driven defaults so PR creation stays consistent across projects without interactive prompts. Every layer is optional — with no config at all it still creates a PR using `gh`'s own defaults.

What it does:

- **Repo path** — derived from `git config remote.origin.url` (`OWNER/REPO`, scheme/host and trailing `.git` stripped).
- **PR title** — derived from the branch name. A branch named `<num>-<slug>` (e.g. `20545-fast-follow-cleanup`) becomes the title `20545: fast follow cleanup` (hyphens → spaces). Other branch names fall back to `--fill` (commit subject).
- **Config** — reads `$PR_AUTOMATION_CONFIG_DIR/global.yaml` and `$PR_AUTOMATION_CONFIG_DIR/projects/<OWNER>/<REPO>.yaml` via `yq`. A per-project file *replaces* the global defaults (no merging). Supported keys: `base_branch`, `reviewers` (list), and `github_templates.pull_request` (path within the repo's `.github/`).
- **PR template** — if the project config maps `github_templates.pull_request` and the file exists under the repo's `.github/`, its contents are passed as `--body-file`. Otherwise `gh`'s default template handling applies.
- **Existing PR** — if a PR already exists for the branch, `create-pr` runs `gh pr edit` to update the title/base/reviewers and prints a notice; it does **not** overwrite the existing body.

Host config layout (bind-mounted from `~/.config/pr-automation`):

```
~/.config/pr-automation/
├── global.yaml                 # base_branch, reviewers (optional)
└── projects/
    └── CoreFXIngredients/
        └── my-repo.yaml        # per-project overrides (optional)
```

Example `projects/CoreFXIngredients/my-repo.yaml`:

```yaml
base_branch: UAT
reviewers:
  - other-team-handle
github_templates:
  pull_request: PULL_REQUEST_TEMPLATE/default.md
```

## The `gh-as-owner` command

`gh-as-owner` runs a push, a PR creation, or any other `gh` call **as the account that owns the checkout's `origin` remote**, with the identity derived from that remote rather than decided on the command line.

It exists because two `gh` accounts share one config here — one for reading, one with write access to the repos that matter — so every push and every PR used to need an identity decision made by hand, in the command, every time. The spellings that make that decision by hand are also the ones that put a token somewhere it should not be: in `argv`, in a remote URL, or in a shared `.git/config` (#810).

```sh
gh-as-owner push      [<repo-path>] <branch> [git push args ...]
gh-as-owner pr-create [<repo-path>] [gh pr create args ...]
gh-as-owner gh        [<repo-path>] <gh args ...>
gh-as-owner whoami    [<repo-path>]
```

- **Owner** — `OWNER/REPO` is parsed out of `git remote get-url origin` and `OWNER` is the identity used. An argument can be stale; a remote cannot. This is also why there is no write-verb/read-verb list: a token is only ever used for the repo whose owner it belongs to.
- **Token** — resolved with `gh auth token --user <owner>` *inside the script*, kept in a shell variable, and exported to the single child process that needs it. It is never an argument, never written to a file, never put in a URL, and never written into any `git config`. For `push`, `credential.helper='!gh auth git-credential'` is passed with `git -c`, per command, so nothing is persisted into a `.git/config` that sibling worktrees share.
- **No fallback** — an owner with no authenticated account is exit 2 with an actionable message, never a silent fall back to whichever account happens to be active. Landing work under the wrong identity is the failure this replaces.
- **`<repo-path>`** — optional everywhere, defaults to the current directory. It is only recognised when it contains a `/` (or is `.` / `..`) **and** names an existing directory, so a branch name or a `gh` subcommand can never be swallowed as one.
- **`whoami`** — prints the repo, the derived owner, and whether a token for it resolves (never the token itself), so the identity can be checked *before* a push rather than diagnosed after a rejected one.

**Shape matters as much as behaviour.** Claude Code's permission classifier accepts a plain command — a literal program path, literal arguments, no command substitution, no environment prefix, no chaining — and refuses the inline `GH_TOKEN="$(gh auth token --user X)" git …` form inside a worktree-isolated session. Everything that has to be computed is therefore computed inside the script, where a single bash process makes the environment ordinary. Invoke it plainly; don't wrap it.

This script was proven first as `.claude/commands/implement-issues/gh-as-owner.sh` in this repo, which is why its header carries the history of the two mechanisms that failed before it. That path still exists and still works — it is now a **thin delegator** to this one (in-repo source first, `/usr/local/bin/gh-as-owner` second), because `/implement-issues` worker prompts name it literally. There is deliberately only one implementation: a fresh wrapper per attempt is the failure mode that produced all three issues.

## The `claude` command

`claude` is wrapped so that a **bare interactive session** — plain `claude` with no arguments, run from a terminal — automatically passes `--ide`, since this Feature is meant purely for use inside a VS Code dev container. **Everything else is passed through unmodified**: subcommands (`claude mcp`, `claude auth login`, `claude update`, etc.), any flags or a prompt (`claude "prompt"`, `-p`, `-c`, `-r`), and non-interactive/piped invocations (`echo … | claude`).

The wrapper injects `--ide` only for the zero-argument TTY case (`[ $# -eq 0 ] && [ -t 0 ]`) rather than maintaining an allowlist of subcommands to *exclude*. The old allowlist had to be hand-edited for every new subcommand, and any subcommand it hadn't been taught about was silently turned into `claude --ide <subcommand>`; the inverted rule can never break a new Claude Code subcommand. **Accepted trade-off:** `claude -c`, `claude -r`, and `claude "prompt"` no longer auto-get `--ide` — pass it explicitly if you want it there.

The same wrapper also passes `--append-system-prompt-file "$CLAUDE_CONFIG_DIR/system-prompt-append.md"` (#740) for **session** invocations — no arguments at all, or a first argument that is a flag — so session-wide style and policy rules arrive as *system* prompt rather than as user-turn context that drifts over a long session. That file is **local-only and hand-maintained** in the bind-mounted claude-home: the Feature never ships it and never creates it, and the flag is injected only when it is actually present, so a container without one behaves exactly as it did before. Subcommands are skipped deliberately — `claude plugin …`, `claude mcp …`, anything whose first argument is not a flag, reject the option outright. No VS Code setting is involved: the wrapper **replaces the npm `claude` binary in place**, so everything that resolves `claude` through `PATH` — an IDE-launched session included — already runs it.

**Where the wrapper itself lives (#807).** For a while the two halves of that
mechanism updated on different clocks: `system-prompt-append.md` lives in the
bind mount, so every edit to it is live immediately, while the wrapper that reads
it was baked into the image and changed only on rebuild. A container whose image
predated `--append-system-prompt-file` therefore passed no flag at all — every
standing rule in the file absent from every session, for the better part of two
weeks here, with nothing reporting it. Editing the file appeared to work and did
nothing.

Two things close that gap, neither of them new machinery:

- **The wrapper is published into the mount, beside the rules it delivers.**
  `publish-claude-wrapper` (feature-contributed `postCreateCommand`, before the
  hook sync) copies the image's wrapper to
  `$CLAUDE_CONFIG_DIR/personal-features/claude-wrapper` with the same
  stage-`chmod`-rename discipline `sync-claude-hooks` uses for the #803 hook
  shim, and the wrapper on `PATH` execs that copy when one is there. A wrapper
  published by **any** container on this machine is then the wrapper every other
  container runs, without a rebuild. The real Claude binary cannot travel with
  the copy — its path carries the publishing image's Node version — so the
  on-`PATH` stub hands its own over in `CLAUDE_REAL_BIN`, and re-entry is ruled
  out by comparing `$0` against the shared path rather than by an environment
  flag (a flag would leak into the session and make a nested `claude` skip the
  shared copy). With no published copy the invocation is byte-identical to the
  pre-#807 wrapper.
- **Staleness is reported through the marker that already exists.** The wrapper
  is fingerprinted by the #806 provision marker
  (`$CLAUDE_CONFIG_DIR/personal-features-provision.json`), so an image older than
  one this config dir has already seen announces itself there — one breadcrumb,
  not a second one for this defect. `publish-claude-wrapper` adds the direct
  check the issue asked for on top: one `grep` against the file `claude`
  resolves to, warning loudly at container create when it carries no
  `--append-system-prompt-file`, louder still when `system-prompt-append.md`
  exists and is therefore being dropped right now.

**What this does not fix.** A container whose image predates this change has
neither the delegating wrapper nor the publisher, so nothing here reaches it —
the same limit #806 documented ("a checker shipped in the image is exactly as
absent from an old image as the step it would check"). Both halves above are
forward-looking; the cure for an already-stale container is still a no-cache
rebuild, and the point of the warning is that you now find out you need one.

## Claude Code lifecycle hooks (odoo-sdk event capture)

This Feature provisions a set of Claude Code lifecycle hooks that record session
and tool-call activity into the odoo-sdk local state DB, so time/activity
sessionization has an automatic event stream to work from. This is
infrastructure-level capture: the agent never has to think about logging its own
activity — the hooks fire automatically around every session transition and tool
call.

The hooks are wired into `$CLAUDE_CONFIG_DIR/settings.json` at container-create
time by the feature-contributed `postCreateCommand` (which runs
`sync-claude-hooks` first). Build-time writes under
`CLAUDE_CONFIG_DIR` are shadowed by the `~/.claude` bind mount, so the merge has
to happen at runtime — the same pattern the skills sync uses.

**Where the hook command points (#803).** That `settings.json` is the host's real
`~/.claude/settings.json`, so the **host** runs the very same hook commands this
container writes. A container-absolute command therefore resolves on exactly one
side: for as long as the entries said `/usr/local/bin/claude-event-hook`, every
host session's hooks failed with `/bin/sh: 1: …: not found` and dropped every
event they were meant to record — 11,965 of them, all host-side, none from a
container. So `sync-claude-hooks` now **publishes the shim into the shared config
directory** as `$CLAUDE_CONFIG_DIR/hooks/claude-event-hook` (refreshed from the
image on every container create) and writes the entries as

```
"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/claude-event-hook" <EventName>
```

Claude Code runs hook commands through `/bin/sh -c`, so that expansion is
resolved **per machine at hook time**: `/usr/local/share/claude-home/hooks/…` in
the container, `~/.claude/hooks/…` on a host that sets no `CLAUDE_CONFIG_DIR` —
the two ends of the same bind mount, hence the same file. Nothing has to be
installed on the host, and because the published file is the real shim rather
than a stub, host sessions **record** their events (or no-op silently where
`odoo-sdk` isn't installed) instead of failing. Stale absolute-path entries left
in a settings.json by an older container are stripped and replaced on the next
sync. If no shim can be published and none is already in place, the sync writes
no entries at all rather than hand a session a command that resolves nowhere.

**Every hook command is resolved, not just the ones that already broke (#805).**
A hook entry naming a command that cannot be executed is not an inert entry:
Claude Code runs it through `/bin/sh -c`, gets a 127, and — for a `PreToolUse`
guard — reads that failure as an **allow**. `odoo-api-guard.sh`, the enforcement
point for this repo's own prohibition on Odoo RPC and credential access, failed
exactly that way 73 times across five sessions with the file present and
executable, and nothing anywhere reported it. Against ten commands referenced
from the shared `settings.json` the Feature asserted precisely two — the
`odoo-sdk` console scripts (#496) and `mempalace-recall.sh` (#744) — each added
reactively, after the thing it guarded had already broken. Both one-offs are
gone, replaced by one routine in `sync-claude-hooks` that resolves the lot:

- **Provision time**, after the merge: every command in the hooks block of the
  file that was just written, ours and the user's, is expanded to the program it
  will exec and resolved (`-x` for a path, `command -v` for a bare name). One of
  **ours** that does not resolve is fatal — it can only mean the shim guard above
  was defeated. One of the **user's** is reported, loudly and by name, and never
  repaired: the Feature does not own those files and writing a stub would look
  like a working hook while doing nothing (the #744 decision, kept verbatim). A
  hand-maintained hook the user has yet to install must not fail container
  create.
- **Build time**, via `sync-claude-hooks --check-deps` called from `install.sh`:
  the programs a feature hook command goes on to exec (`HOOK_DEPS` —
  `odoo-sdk`, which `claude-event-hook` shells out to and silently skips when it
  is missing, plus the two console scripts from the same wheel). This half
  **fails the build**, which is what replaced the hand-written entry-point loop.

**The split is forced, not stylistic.** `settings.json` lives in the
bind-mounted config dir, which does not exist while the image is being built —
that is the entire reason `sync-claude-hooks` runs from `postCreateCommand` — so
"resolve every command in `settings.json`" cannot be a build-time check however
much one would prefer it there. `HOOK_DEPS` is the mirror image: those programs
live in the image and nowhere else, so resolving them is cheapest and loudest at
build time. Each half runs at the only moment its subject exists.

**Necessary, not sufficient.** This resolves a command *at provision time*. It
cannot say the command will still resolve, or still work, when a hook actually
fires — `odoo-api-guard.sh` resolved fine at provision time and exit-127'd
anyway, for reasons still undiagnosed. The runtime half of that problem is #804,
below.

**Resolution never executes anything.** The command strings are user config, and
expanding them means handing them to a shell. Any command carrying a control
operator, a redirection or a command substitution is therefore **refused and
reported as unverifiable** rather than expanded; with those screened out and
globbing disabled, the expansion can perform parameter and tilde expansion and
word splitting, and nothing else.

**A hook that stops working says so (#804).** The shim's contract is to exit 0 on
every path — a tracker that blocks a session because its own database is
unreachable is worse than a tracker that misses a row — but failing *open* had
become the same thing as failing *silently*. 3,506 events were dropped over
roughly three months from two unrelated causes (#496's unlinked console scripts,
#803's container-absolute command) and both presented identically: nothing at
all. The tracker's own tables cannot show it, because the signature is the
**absence** of rows, which is indistinguishable from a quiet week.

A failure counter written by the shim cannot close that gap — the shim is the
thing that is broken, and in both of those outages not one line of it ran. So the
breadcrumb is written by the path that **succeeds**, and read by a different
program at a different time:

- `claude-event-hook` stamps `last_event_at` / `last_event_epoch` /
  `last_event_hook` into the provision marker, chained onto the `odoo-sdk` call
  with `&&` so it attests the *whole* path — hook resolved, shim ran, SDK exited
  0 — rather than just the first link. It rides the same detached background job,
  so it costs the session nothing, and is throttled to one write a minute so a
  `PreToolUse`-per-tool-call workload does not churn the bind-mounted config dir.
- `sync-claude-hooks` compares that stamp against now on every container create
  and reports a silence longer than `PERSONAL_FEATURES_HOOK_STALE_SECONDS`
  (default seven days), naming the last successful write and pointing at the two
  causes worth checking first. A config dir that has never recorded an event is
  not evidence of an outage on the first create that looks, so `hook_watch_since`
  records the zero point and the *next* create measures from it. The report runs
  before anything that can exit early, because a shim that cannot be published is
  itself one of the reasons the stamp would have stopped.

**It reuses #806's marker rather than adding a second one.** Both facts are about
the same subject — what this machine's feature scripts have actually done — and
`personal-features-provision.json` already lives in the one directory the
container and the host share, already outlives the image, and already needs no
mount and no `containerEnv` var of its own. `sync-claude-mcp` rebuilds that
record from scratch on every create, so it explicitly carries the runtime fields
forward; without that the provision-time write would erase the evidence the
runtime check reads, restoring the defect by accident. Nothing is added to
`settings.json`: no new hook entry, no new marker, no new program — the
mechanism is two existing scripts writing and reading one existing file.

**Still not fail-closed.** #804 asks only that the silence stop being
indistinguishable from success. A stale heartbeat is a warning on stderr; the
merge still runs, the hooks are still wired up, and the container create still
exits 0.

**What's captured.** Each hook invokes `claude-event-hook <EventName>`, which
forwards one event to `odoo-sdk log-event --source claude:<EventName>`. The
following events are wired (verified against the current Claude Code hooks
reference):

- `SessionStart`, `SessionEnd` — session boundaries.
- `UserPromptSubmit` — a prompt was submitted.
- `PreToolUse` — a tool is about to run (subject = the tool name).
- `SubagentStart`, `SubagentStop` — subagent boundaries (subject = agent type).
- `Stop` — the assistant finished responding.

Only a small, non-sensitive payload is forwarded (`session_id`, plus
`tool_name`/`agent_type`/`agent_id`/`source` where present) — never prompt text
or `tool_input` contents. Events are attributed via `--attach-active-run` to
**every** active odoo-sdk run, not just the current project's: there is one
host-provisioned central `tracker.db` shared by all projects and the lookup
applies no repo filter (#388), so a hook firing in project A also attaches
project B's task id when both have runs in flight. Runs whose last activity
predates the reap threshold (`ODOO_REAP_THRESHOLD_HOURS`, default 12h) are
skipped, so a wedged orphan from a dead devcontainer stops accruing phantom
billable wall-clock (#366). With no active run, the event is left untargeted
(session-level).

**`PreToolUse` excludes `mcp__odoo-mcp__*` tools** — the prefix Claude Code
derives from the `odoo-mcp` server name `install.sh` registers. The odoo MCP
server already logs its own tool dispatches server-side, so `claude-event-hook`
skips those to avoid double-counting. That server-side event mirrors this shim's
payload stance: it records only the tool name and task id — never argument
values (note bodies, questions, search queries) — so no free-text inputs are
written to the local events store on either path.

**Never blocks a session.** `claude-event-hook` always exits 0, never writes to
stdout (which the hooks contract could interpret as a permission decision),
runs the SDK under a short timeout, and no-ops cleanly when `odoo-sdk` isn't
installed (e.g. a build with no bundled SDK wheel) or the cwd isn't a git repo.

**A second, unrelated `SessionStart` hook: the worktree Bash constraint (#809).**
The same sync also registers `worktree-context-hook`, which has nothing to do
with event capture. A session whose cwd is inside a `.claude/worktrees` checkout
has its Bash calls screened by Claude Code's worktree sandbox, which refuses
anything it cannot verify stays inside the worktree. That rule is stated
**nowhere** up front — a scan of every injected attachment in the local
transcript corpus (hook context, instructions, skill listings, system reminders)
finds no statement of it — so it is discoverable only by being refused, and it is
rediscovered from scratch session after session: 252 refusals across 99
transcripts, **54% of every transcript that ever runs Bash from a worktree**, on
4.9% of their Bash calls, with no decline over a month. The hook emits a
`hookSpecificOutput.additionalContext` envelope naming the constraint *before*
the first command, and emits **nothing at all** for any other cwd (a
`SessionStart` hook's stdout is added to the session context verbatim, so a stray
byte would be noise in every session). It always exits 0 — `exit 2` from
`SessionStart` blocks the session from starting.

It is a separate program from `claude-event-hook` on purpose: that shim's
contract is "never write to stdout", which is the exact opposite of this one's
job, and it fires on every session rather than only worktree ones. It gets the
same publish-then-reference treatment as the event shim (#803) — copied to
`$CLAUDE_CONFIG_DIR/hooks/worktree-context-hook`, referenced through the
`${CLAUDE_CONFIG_DIR:-$HOME/.claude}` expansion — so the host side of the mount
resolves it too. If its shim cannot be published the entry is simply omitted;
unlike the event shim that is not worth refusing the whole merge over, because
the fallback is only the status quo.

**The text it ships is a declaration, not the sandbox's own rules.** The rules
live in the Claude Code binary and are published nowhere this repo can read
(`grep -rn 'isolated in the worktree'` over the tree returns nothing), so the
list was written from refusals actually observed — compound commands and
pipelines, `$( )` substitution, `source` of a computed string, `git` in a form
too complex to verify or `git -C` pointing outside the worktree, `sed` with a
runtime-computed value, `GIT_CONFIG_GLOBAL` (refused separately as "git-config
injection"), `gh auth switch` (refused in some sessions and not others), and even
a bare `bash` token inside an otherwise plain command. The shipped text says in
so many words that this list is **indicative, not exhaustive**, and that it can
go stale when the binary changes; a confidently wrong list would be worse than a
short honest one. When a new refusal shape turns up, add it to
`src/personal-features/worktree-context-hook`.

**Opting out.** The merge only ever replaces its own entries (identified by the
`HOOK_MARKERS` substrings — `claude-event-hook` and `worktree-context-hook`) and
preserves all your other settings and hooks. To
disable the capture, remove the `claude-event-hook` entries from
`~/.claude/settings.json` (they — and the published
`~/.claude/hooks/claude-event-hook` — will be re-added on the next container
create) — or, to disable it permanently, drop the `sync-claude-hooks` step from
the Feature's `postCreateCommand`. A corrupt/unparseable `settings.json` is left
untouched (and a warning printed) rather than overwritten.

## Odoo consulting skills (where they live now)

This Feature ships no Odoo consulting skills, and as of #738 no machinery for
delivering them either. The playbook that lived here — quote drafting,
Fibonacci estimating, discovery capture, solution design and Odoo code review —
moved to the `odoo-dev` plugin, which bundles each one as `odoo-dev:<name>`
(#695-#699); weekly client status reporting was retired outright (#700).
`sync-claude-mcp` installs that plugin from this repo's own marketplace at
container-create time (#723).

Historically the content reached an agent by two independent,
deliberately-parallel paths. Only the second survives:

1. **Mounted `SKILL.md` files (Claude Code only) — removed (#738).**
   `install.sh` used to stage the `skills/` tree at build time to
   `/usr/local/share/personal-features/skills` (a path *not* under the
   `~/.claude` bind mount), and the feature-contributed `postCreateCommand` ran
   `sync-claude-skills` to copy each skill into
   `$CLAUDE_CONFIG_DIR/skills/<name>`, where Claude Code discovers user-scope
   skills. The `skills/` tree, the staging dir and the sync script are all gone:
   a loose copy of a plugin skill loads as a *personal* skill alongside its
   plugin twin, two near-identical descriptions competing for the same triggers.
   The loose-copy path preserved Claude Code's native skill-discovery /
   slash-command UX, but only inside a live container with this Feature
   installed and a working bind mount — the plugin install provides the same UX
   everywhere.

2. **`odoo-mcp` built-in prompts (any MCP client).** Since #455, each skill was
   *also* exposed as a built-in MCP prompt by the `odoo-sdk` MCP server
   (`libraries/odoo_sdk/src/odoo_sdk/mcp/prompts/builtin/<name>.py`, one module
   per skill, underscored — `<skill-name>` → `<skill_name>`). Each module embeds
   its `SKILL.md` body verbatim (frontmatter `description` becomes the prompt
   description; the markdown body becomes the returned prompt message) and is
   registered through the same `@builtin_prompt` decorator as `implement_task`
   and `report_incident`. Because the prompts ship inside the SDK package, any
   MCP client gets them for free — no mount, no `postCreateCommand`, no live
   personal-features container required. **All six prompt modules outlived their
   skills**: the `skills/` tree is gone, so each module's embedded body is the
   only copy of that text left outside the plugin.

**Stale loose copies are cleaned up, not just reported (#738).**
`$CLAUDE_CONFIG_DIR/skills` is a host bind mount, so copies written by
pre-migration containers outlive the image that wrote them, and no rebuild
removes them. Once `sync-claude-mcp` has the `odoo-dev` plugin in place it
deletes exactly the six names the retired sync used to seed — the five the
plugin now carries (`discovery-notes`, `fibonacci-estimate`,
`odoo-code-review`, `odoo-design-doc`, `odoo-quote`) plus the retired
`client-status-report` — and only where `<name>/SKILL.md` exists,
the same definition of a stray that
`plugins/odoo-dev/scripts/check-stray-skills.sh` reports on. Every other
directory under `skills/` — including every user-authored skill, and including
a same-named directory carrying no `SKILL.md` — is left alone. A failed plugin
install skips the cleanup rather than leaving the machine with neither copy,
and each removal is logged on its own line; like everything else in
`sync-claude-mcp`, the cleanup is best-effort and never fails container create.

**One set of names, two groups, one gate (#778).** The deleter here and the
reporter in `plugins/odoo-dev/scripts/check-stray-skills.sh` used to disagree:
this list carried six names, that one carried five — the same five minus
`client-status-report` — and nothing reconciled them. That is silent in both
directions. A name only the deleter knows about is `rm -rf`'d by a script that
never mentions it, and a name only the reporter knows about keeps the gate red
forever because nothing removes it. Both files now carry the same six, split
into the two groups that actually need different handling:

| Group | Names | Why it is on the list | Advice when found |
|---|---|---|---|
| Plugin-shadowed | `discovery-notes`, `fibonacci-estimate`, `odoo-code-review`, `odoo-design-doc`, `odoo-quote` | Moved into `odoo-dev` (#695-#699, #701-#708); the plugin still ships them, so a loose copy loads *alongside* a live twin | Delete the loose copy; the plugin twin stays |
| Retired, no twin | `client-status-report` | Retired outright (#700). No plugin copy, no packaged source, nothing replaces it — it shadows nothing but is still feature-seeded debris spending description budget every turn | Delete; there is nothing to fall back on |

The plugin-shadowed five are not a fourth hand-maintained copy: they are
`odoo_sdk.skills.PACKAGED_SKILL_NAMES`, the packaged sources the plugin's copies
are generated from and the list `check-skill-parity.sh` already checks against.
`.github/scripts/test_stray_skill_parity.py` gates all of it — the two lists
against each other, both against the packaged names, and each claimed group
against what the plugin actually ships on disk — so editing one file alone now
fails CI instead of drifting.

**Not on either list, deliberately: `ingest`, `lint`, `llm-wiki-workspace`,
`process`, `query`.** These turn up in `$CLAUDE_CONFIG_DIR/skills` beside the
strays on a real machine, which is why #778 listed them, but this Feature never
seeded them and no plugin ships them — they are the user's own personal skills
(`plugins/odoo-dev/skills/odoo-dev-map/SKILL.md` records them as Second Brain
skills, outside every Odoo workflow). Reporting them would be a false positive
and deleting them would be data loss, since the remedy here is `rm -rf`. The
parity gate asserts neither list ever acquires one, and the Feature test seeds
two of them as decoys that must survive a cleanup run.

## Provision marker: telling a stale image from a current one (#806)

Every step in `sync-claude-mcp` reports what it *did*. None of them can report
what an older copy of the script *would* have done — a container whose image
predates a migration simply runs a script that lacks it, prints nothing about
it, and looks exactly like a container where that migration ran and succeeded.
That is what #806 turned out to be: the `#723` marketplace migration was
correct and had simply never been in the image on that machine, so every
Claude session there kept loading `odoo-dev@odoo-dev` from a repo this project
retired, three weeks behind the tree in front of it — and the container-create
log that would have said so was long gone.

**No checker shipped inside the image can close that gap**, because a checker
baked into the image is exactly as absent from an old image as the step it
would check. The only state that outlives the image is `$CLAUDE_CONFIG_DIR` —
the host's bind-mounted `~/.claude`, shared by every container the machine
builds. So `sync-claude-mcp` records the provenance of *this* container's
feature scripts there, in `~/.claude/personal-features-provision.json`, and
compares it against the newest set that ever provisioned the same config dir:

```
$ cat ~/.claude/personal-features-provision.json
{
  "provisioned_at": "2026-09-23T03:06:08Z",
  "script_digest": "fef269073a2a…",
  "script_epoch": 1790128289,
  "scripts": { "sync-claude-mcp": "a07e942c…", "claude-event-hook": "08f7e708…", … },
  "newest_script_epoch": 1790128289,
  "newest_seen_at": "2026-09-23T03:06:08Z",
  "stale_image": false
}
```

An image whose scripts are older than a set already seen in that config dir
gets a loud `WARNING` on every container create, naming both build timestamps
and pointing at the marker — rather than the `ls -la /usr/local/bin/…` plus
`grep -c` archaeology #806 needed to establish the same fact. The high-water
mark (`newest_*`) is carried forward independently of the current run, so a
stale container writing its own provenance cannot erase the evidence that
something newer was here and go quiet on the next create.

**Deliberately not odoo-dev-specific.** It fingerprints the feature-owned
scripts themselves — `sync-claude-mcp`, `sync-claude-hooks`,
`claude-event-hook`, `mempalace-repair`, `resolve-mempal-dir`, `create-pr` —
by SHA-256 content hash plus newest mtime, so *every* step any of them ever
gains is covered by the same marker, with no per-migration assertion to
remember to add. (The alternative #806 floats, an assertion against the
retired-marketplace list, only ever catches the one migration already written
down.) Content hash *and* mtime are both needed: the mtime says which build is
older, the hash keeps a rebuild of unchanged scripts from being reported as
drift. A 60-second slack absorbs filesystem timestamp granularity.

It adds no mount and no `containerEnv` variable — the marker lives inside a
directory the Feature already mounts — and, like everything else in
`sync-claude-mcp`, it is best-effort: a marker that cannot be read, written or
parsed warns and the run still exits 0. No step was added to the
`postCreateCommand` chain, which is an `&&` chain where any failing step aborts
container create and suppresses `postStartCommand`.

**It also carries the runtime hook heartbeat (#804).** `claude-event-hook`
stamps `last_event_*` into this same marker between provisions and
`sync-claude-hooks` reads it on the next create — one file, two facts about what
this machine's feature scripts have actually done, rather than a second
breadcrumb with its own filename and its own staleness rule.

**The marker is shared state, and preserve is the default (#868).** The record
is rewritten on every provision, so `sync-claude-mcp` starts from the marker it
found and overwrites only the fields *it* owns — `schema`, `issue`,
`provisioned_at`, `script_epoch`, `script_digest`, `scripts`, the three
`newest_*` high-water-mark fields and `stale_image`, all of which describe the
scripts in *this* container and would be a lie if inherited. Every other key
belongs to another writer and is carried forward untouched; nothing is dropped.
Adding a key to this marker from anywhere else therefore needs **no edit in
`sync-claude-mcp`**. It used to: a hand-maintained allowlist of key names lived
in `sync-claude-mcp`, so a key its owner forgot to add there was silently erased
on the next container create and its reader then reported "never seen" where the
truth was "erased" — the very failure mode this marker exists to make visible.

## Python toolchain (odoo-sdk, odoo-mcp, mempalace)

The Feature's Python tooling — the `odoo_sdk` wheel (providing the `odoo-sdk` CLI, the `odoo-mcp` MCP server, and the `odoo-tui` TUI) and `mempalace` — installs into isolated `uv`-managed environments under `/usr/local/share/uv/tools`, never into the base image's site-packages (which would break odoo:17's pyOpenSSL, among other things).

**The interpreter is pinned, not inherited (#674).** Each environment carries its own `uv`-managed CPython 3.11 (`UV_PYTHON_PIN` in `install.sh`), downloaded by `uv` when the image doesn't already provide that exact version. The base image's Python is irrelevant: `odoo_sdk` is a pure RPC client that never imports Odoo core, so its interpreter has no reason to match the container's Odoo Python, and `uv` itself is a static binary that runs on every supported base (odoo:16's bullseye included). This means **odoo:16 — Debian 11, system Python 3.9 — is fully supported**: it gets the same complete toolchain as every newer image.

**That downloaded interpreter lives in a shared location, not root's home.** `install.sh` sets `UV_PYTHON_INSTALL_DIR=/usr/local/share/uv/python`, alongside the tool environments. `uv`'s default is `$HOME/.local/share/uv/python`, and the Feature installs as root — so the interpreter would sit under `/root` (mode `0700`) while every console script's shebang chain resolves through it. On any base image with a non-root `remoteUser`, running `odoo-sdk`/`odoo-mcp`/`mempalace` then failed with `bad interpreter: Permission denied` (an `exec` `EACCES`, not a `PATH` problem). Both the shared path and a follow-up `chmod -R a+rX` keep the interpreter readable and executable for whichever user the container ends up running as.

It didn't used to be. `install.sh` previously gated the whole Python block on the *base image* shipping `python3 >= 3.10` and skipped it silently on older images, so an odoo:16 container had no `odoo-mcp` at all while the bind-mounted `~/.claude` could still carry an `odoo-mcp` MCP registration written by a newer container — Claude Code then reported a baffling `ENOENT` for a binary that was never installed. The gate is gone; the only remaining skip is a build with no bundled SDK wheel (a plain dev checkout — wheels are bundled at release/CI time), which now warns loudly, and `sync-claude-mcp` deregisters a stale user-scope `odoo-mcp` entry at container-create time whenever the binary isn't installed, so the persisted registration state stays consistent with what the container actually ships.

## The shared mempalace MCP hub

**One mempalace MCP server per container, not one per session (#764).** mempalace grants the *MCP writer lease* to exactly one process per palace. Every Claude Code session used to start its own stdio server against the same palace, so the first session to mutate kept the lease and every other session's mutating tools failed with:

```
MCP error -32001: Peer MCP writer active; this server is read-only for mutating tools
```

Reads kept working, which is what made it expensive: the failure surfaced only at **write** time — usually at session end, after the work that produced the memory was already done. N live sessions meant N−1 sessions that could remember nothing.

The Feature now starts one long-lived hub — `mempalace serve`, bound to `127.0.0.1:8765` — and that is the entire fix.

**No session MCP config is rewritten, because none has to be.** Verified by reading the pinned 3.9.0 in site-packages, not the docs: the `mempalace-mcp` console script is no longer the server. Its entry point is `mempalace.mcp_proxy:main`, which resolves the palace, looks that palace's live hub up in a per-palace registry (`~/.mempalace/server/<sha256 of the canonical palace path>/serverinfo.json`, trusted only while the recorded pid is alive) and forwards every JSON-RPC request to it over HTTP — importing the ~77 MB storage stack only when no hub answers. The same discovery is wired into the CLI (`_forward_mine_to_hub`, `_forward_search_to_hub`), so `mempalace mine` — which the plugin's Stop/SessionEnd/PreCompact save hooks spawn, and which the hub's own lease would otherwise refuse — is forwarded too.

That matters because the stdio registration is **not ours to change**: it lives in the upstream plugin's own `.mcp.json` under `$CLAUDE_CONFIG_DIR/plugins/cache/mempalace/…`, a path this repo does not ship and that `sync-claude-mcp`'s `claude plugin update mempalace@mempalace` overwrites on every container create. Patching it would be undone on the next rebuild. Starting a hub turns that unchanged registration into a proxy by itself, and the memory saving is a bonus: a proxied session runs at roughly 22 MB instead of ~100 MB.

**Supervision: the Feature's `postStartCommand`, and deliberately nothing heavier.** This repo had no service-supervision pattern of any kind before this, so the choice sets the precedent:

- `systemd` is not PID 1 in a dev container. Upstream ships a unit file; it is not usable here.
- `supervisord`/`s6` would mean a new package, a new config file and a new failure mode, for one process.
- `postStartCommand` is the only lifecycle hook that fires on **every** container start — create, a stop/start of an existing container, and a host reboot — which is exactly the "survives a container restart" requirement, and it costs one JSON key. It is declared on the Feature (a [documented Feature property](https://containers.dev/implementors/features/#lifecycle-hooks), collected alongside any the consuming `devcontainer.json` declares rather than overriding them) and runs as the `remoteUser`, which is what the registry lookup needs: the hub and its clients must agree on `$HOME`, or `~/.mempalace/server/…` names two different directories and the client concludes there is no hub. Everything that matters here — Claude Code, its plugin hooks, your shells — runs as the `remoteUser` too, so they agree; a session `su`'d to another account would not find the hub and would quietly serve its own palace copy instead.

What `postStartCommand` does **not** give is restart-on-crash within a single container run. The honest mitigation is that a dead hub is not an outage: `mcp_proxy` falls back to serving the session locally and says so on the tool result itself, so the agent driving the session is told its memory backend changed shape. `mempalace-hub` can also be re-run by hand at any time. One real consequence of the lifecycle ordering is worth knowing: a failing `postCreateCommand` skips `postStartCommand` entirely, so a broken `mempalace-repair` takes the hub down with it.

**`mempalace-hub` is idempotent and never fatal.** `start` (the default) probes `/healthz` — mempalace's own liveness route, and the only credential-free one — before doing anything, so running it on every container start can never produce two hubs. Then it truncates its log (nothing rotates a log inside a container, and one hub run is the only bounded unit that keeps the diagnostics), launches `mempalace serve` under `setsid` (or `nohup` on an image without util-linux) with stdin on `/dev/null` and both output streams on the log — a background child still holding the lifecycle command's pipes would keep the dev container CLI waiting on it forever — and then polls `/healthz` until it answers. It polls rather than watching a pid because `setsid` may or may not fork, so `$!` answers a different question than the one that matters.

Every failure path exits 0 with a warning naming the consequence: no `mempalace` on PATH, an unwritable log, or a bind that never answers. A container with no hub is exactly the pre-#764 behaviour — degraded, not broken — and not worth failing container start over. `mempalace-hub status` reports the endpoint, and `MEMPALACE_SKIP_HUB=1` opts out; `MEMPALACE_HUB_CMD`/`_HOST`/`_PORT`/`_LOG`/`_WAIT` exist for the feature test and for debugging.

**`MEMPALACE_MCP_IDLE_HOURS=0` is set for the hub, and only for the hub.** mempalace's MCP server self-terminates after 8 idle hours so abandoned *per-session* servers stop accumulating ChromaDB file handles. Applied to the one process the whole container shares, that watchdog is a self-inflicted outage whose next repair is the next container start — possibly days away. It is set in the launcher's environment rather than in `containerEnv` precisely so per-session servers keep the watchdog they were designed for.

**No new persisted path.** The hub's registry record, its bearer token (never generated for a loopback bind) and the palace itself all live under the existing `~/.mempalace` → `/usr/local/share/mempalace` mount, so `persisted-paths.tsv`, `devcontainer-feature.json`'s `mounts`/`containerEnv`, `setup.sh` and `setup.ps1` are all untouched by this. The log is container-local, under `/usr/local/share/personal-features/`, pre-created mode `0666` for the same reason `mempal-dir.sh` is: `install.sh` cannot know which account will run the lifecycle command.

## The Odoo language server (odoo-ls)

**Claude Code sessions get Odoo-aware diagnostics, go-to-definition, references and hover (#746).** Upstream [odoo-ls](https://github.com/odoo/odoo-ls) is the server; the Feature installs and configures it, and the `odoo-dev` plugin's `.lsp.json` is what tells Claude Code to launch it. Without it, an invalid XPath, a misspelled field name or a bad import surfaces only when `odoo-bin -i/-u` runs.

**Pinned to 1.6.0 and checksum-verified — the only download here that is.** #746 named 1.4.0, which was current when the issue was written; 1.6.0 is the current non-prerelease (every 1.5.x is marked prerelease). Both assets — the per-arch `odoo-linux-<arch>-<ver>.tar.gz` and the shared `typeshed.zip` — are verified against a pinned SHA-256 before anything is published, and a partial install is never published: a server with no stubs starts, answers, and silently resolves nothing. Verification is what `fetch`'s optional third argument now does; it stays opt-in per call because the other downloads here fetch installer scripts and tarballs whose publishers re-cut assets under the same tag, where a pinned digest would break a working install on every upstream re-tag. Bumping means moving `ODOO_LS_VERSION`, both per-arch digests and the typeshed digest together.

**It does not live in `/usr/local/bin`.** The server resolves its stdlib and stub roots *relative to its own binary* (`typeshed/stdlib`, `typeshed/stubs` next to `current_exe()`), so binary and the 35 MiB typeshed tree sit together in `/usr/local/share/odoo-ls` and a launcher, `odoo-ls-server`, is what goes on `PATH` — which is where Claude Code requires `command` to resolve, since it will not run a bundled binary. `--stdlib` could override the path instead; co-locating means the default is already right and one fewer flag can drift.

**Two scripts, because `.lsp.json` cannot express either job.**

- `odoo-ls-config` (from `postCreateCommand`) writes `/usr/local/share/odoo-ls/odools.toml` from paths that only exist once the container does: `odoo_path`, the community/enterprise/`/mnt/extra-addons` `addons_paths`, and the checkout's `.venv` interpreter as `python_path`. Same reason `resolve-mempal-dir` exists — a Feature build cannot see any of this (#485). Not an Odoo container? It writes nothing and *removes* a stale file from a previous create, because every path setting is resolved against the filesystem and a stale entry is a hard config error, which is worse than no config.
- `odoo-ls-server` picks exactly one config source per session: a project's own `odools.toml` at or above `$CLAUDE_PROJECT_DIR` if there is one, otherwise the generated file via `--config-path`, and with neither it starts nothing at all — the plugin registers `.py` for every project, not only Odoo ones, and without an `odoo_path` the server would index a whole tree to resolve no model, no field and no xmlid. **Never both** — the server merges its sources agree-or-error for scalars, so passing both turns a legitimate per-project override of `odoo_path` or `python_path` into a config error instead of an override. The cost is that a project config has to be self-contained; the generated file is the copy-paste starting point. Task worktrees under `.worktrees/` need no entry anywhere: the server infers addon paths from the LSP workspace folder when the profile for that folder sets none, and Claude Code sends the project directory as that folder, so a session started in a worktree indexes it. Enumerating them would instead bake in paths that come and go with every task.

**Facts checked against the 1.6.0 source and the running binary, not the docs.**

- **stdio is the default transport.** #746 asked whether `--stdio` is needed; there is no such flag. `--use-tcp` is what switches away from stdio, and passing a flag that does not exist is a startup error.
- **Logs never reach stdout.** The stdout log subscriber is installed only under `--parse` or `--use-tcp`; over stdio everything goes to a rolling file appender. So the wrapper is *not* needed to keep stdout clean — it is needed for config selection and for the log directory.
- **`--logs-directory` must already exist.** The server checks the path and falls back to `<binary dir>/logs` rather than creating it, and that fallback's construction is an `.expect()` — a panic before the server ever speaks LSP. The launcher creates the directory it names, and `install.sh` pre-creates `/usr/local/share/odoo-ls/logs` mode `0777` (server logs, no secret) so the fallback can never be the thing that kills a session. `--log-level` is `warn`, not the server's own `trace`, which is megabytes an hour per session.
- **`${workspaceFolder}` is not a thing in a plugin LSP config.** #746 flagged this as unverified; it is false. Claude Code substitutes `${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PLUGIN_DATA}` and `${CLAUDE_PROJECT_DIR}` into `command`/`args`/`env`/`workspaceFolder`, and injects those three into the server's environment as well — which is how the launcher knows the project directory with no `env` block at all.
- **JS/OWL support is off** (`disable_javascript = true`). That half of the server shells out to `tsserver`; TypeScript is not installed here, and without the flag it reports the same diagnostic on every session. Python, XML and CSV — what the plugin registers — are unaffected.

**Kill switches, coarsest last**, because upstream flags the project "in development": `"diagnostics": false` in `.lsp.json` keeps navigation and stops diagnostics being pushed into context; `settings.Odoo.selectedProfile = "Disabled"` is the server's own in-protocol off switch (it logs `OdooLS is disabled. Exiting...` and indexes nothing); `ODOO_LS_DISABLE=1` stops the process starting; disabling the plugin removes the registration. `restartOnCrash` with `maxRestarts: 3` covers a server that dies on its own — both keys need Claude Code >= 2.1.205, and the pinned version here is well past that.

**Every failure path exits 0.** A non-zero exit from the launcher reads to Claude Code as a crash and burns the restart budget, and "this container has no Odoo in it" is not worth that. No binary, no config, an unwritable log directory: each says why on stderr, which Claude Code captures, and starts nothing.

**No new persisted path.** The binary and stubs are baked into the image, the generated config is derived state regenerated on every create, and the logs are container-local — so `persisted-paths.tsv`, `devcontainer-feature.json`'s `mounts`/`containerEnv`, `setup.sh` and `setup.ps1` are all untouched.

**One name links two trees.** `plugins/odoo-dev/.lsp.json` names `odoo-ls-server` as its `command`, and this Feature is what puts a script by that name on `PATH`. A gate in `plugins/odoo-dev/scripts/validate.sh` now holds the two together: it extracts every `command` in `.lsp.json` and fails unless `install.sh` writes a file of that name into a `bin` directory (#787). It is a name check and cannot prove the generated script runs, but it catches the rename — whose failure mode is otherwise a language server that never starts and never says why. `claude plugin validate` is no help here: it reads only the manifest and does not look at `.lsp.json` at all (measured against 2.1.252). Renaming the launcher still means editing the plugin in the same change; the gate is what makes forgetting loud.

## Additional tooling

This Feature is the owner's own personal, opinionated setup, not a configurable toolkit — there are no options to turn pieces on or off. If a tool stops earning its place here, it gets removed outright rather than gated behind a flag. Everything below installs via apt or static binaries, with no dependency on the node Feature.

- Language-agnostic productivity/navigation CLIs: `ripgrep`, `fd`, `fzf`, `bat`, `jq`, `yq`, `eza`, `zoxide`, `tldr` (tealdeer).
- [`delta`](https://github.com/dandavison/delta) for syntax-highlighted git diffs, and [`lazygit`](https://github.com/jesseduffield/lazygit) as a terminal git UI. `delta` is wired in machine-wide via `git config --system core.pager delta` and `interactive.diffFilter "delta --color-only"`, so `git diff`/`git log -p`/`git show` render through it and `git add -p` hunks are highlighted, in every repo with no per-repo setup.
- [`qsv`](https://github.com/dathere/qsv) — a fast CSV data-wrangling toolkit for slicing, filtering, joining, and profiling the CSV exports that Odoo work throws off. Ships as a bundle of binaries; only the `qsv` binary is put on `PATH` (the static musl build on x86_64, the gnu build on arm64).
- [`gitleaks`](https://github.com/gitleaks/gitleaks) for secret scanning. Usable manually, and invoked automatically by the global `pre-commit` hook below.
- [`coderabbit`](https://docs.coderabbit.ai/cli) (CodeRabbit CLI) for AI code review, and to back the Claude Code CodeRabbit plugin — see the [CodeRabbit CLI](#coderabbit-cli) section above for auth and config-persistence details.
- Standards enforced **machine-wide** rather than per-repo, since most of this owner's projects aren't mature enough to have their own hook config checked in. Sets `git config --system core.hooksPath` to a Feature-installed directory (`/usr/local/share/git-hooks`) containing:
  - `commit-msg` — rejects commits whose subject line doesn't follow [Conventional Commits](https://www.conventionalcommits.org/).
  - `pre-commit` — runs `gitleaks protect --staged`.

  This is native Git config, so it applies to *every* repo on the machine with zero per-repo opt-in. A repo that sets its own `core.hooksPath` locally (e.g. via Husky) overrides this as normal Git config precedence — this only fills the gap for repos that don't.

  Also set `--system`, for the same reason: `core.pager`/`interactive.diffFilter` (so `delta` renders every diff), and `core.excludesfile` pointing at `/usr/local/share/git-excludes/gitignore`, which keeps `mempalace init`'s project-local artifacts out of every repo on the machine — see the `mempalace` bullet below for the rationale and its two tradeoffs.
- The [Starship](https://starship.rs) prompt and `zoxide`'s shell hook, plus aliasing `cat`/`find`/`ls` to `bat`/`fd`/`eza`, and persisted shell history (see above).
- [`mempalace`](https://github.com/mempalace/mempalace) — a global, cross-project memory palace installed via `uv tool install`, pinned to the same `uv`-managed CPython as the odoo-sdk env (see [Python toolchain](#python-toolchain-odoo-sdk-odoo-mcp-mempalace)). `MEMPAL_DIR` (which project tree to mine) is resolved at container-create time by the Feature's `resolve-mempal-dir`, not hardcoded in `containerEnv`: a Feature cannot know the workspace path at image-build time, and mempalace treats an unresolvable `MEMPAL_DIR` as a reason to no-op. Once the Claude Code plugin is registered, its Stop/SessionEnd/PreCompact hooks auto-mine that tree in the background.

  Both of the steps this section used to list as "still manual" are now automated: `devcontainer-feature.json` declares the `~/.mempalace` → `/usr/local/share/mempalace` bind mount and sets `MEMPALACE_PALACE_PATH`, and `sync-claude-mcp` registers the plugin at user scope from `postCreateCommand`. Nothing is left to do by hand after a rebuild.

  Concurrent sessions share **one** MCP server, started from `postStartCommand` — see [The shared mempalace MCP hub](#the-shared-mempalace-mcp-hub).

  **`mempalace-repair` reconciles the palace root (#596, #643).** mempalace holds several disagreeing ideas of where the palace lives, so the Feature installs one idempotent script that settles all of them. It runs twice — from `install.sh` at image-build time, and again from `postCreateCommand` — because the two passes see different filesystems: the bind mount is not attached during the build, so the host's palace only becomes visible at container-create time. It does three things, then asserts a fourth:

  1. **Symlinks `~/.mempalace` onto the mount.** A large class of mempalace state ignores `MEMPALACE_PALACE_PATH` and is hardcoded under `$HOME/.mempalace` — `config.json` and `people_map.json`, `locks/`, `wal/`, `hook_state/`, `known_entities.json` — and the hooks CLI additionally treats an absent `~/.mempalace` as the user's kill-switch. If it finds a **real directory** there (left by an earlier build, whose contents would otherwise be discarded on the next rebuild — the exact failure #596 fixed and #643 found reintroduced) it migrates the contents onto the mount, never clobbering a file the mount already has, and replaces it with the link. A regular file at that path is a user artifact and is left untouched with a warning.
  2. **Removes the stray `~` directory.** mempalace's MCP server `abspath()`s a literal `~/…` `--palace` argument without `expanduser()`, unlike its CLI sibling, so it resolves against the process cwd and the chroma backend then creates a real directory *named* `~` at the palace root, stranding memories where nothing will ever read them. Matched narrowly on that exact shape, so real palace data is never at risk.
  3. **Reconciles `config.json`'s `palace_path` with `MEMPALACE_PALACE_PATH`.** `Config.palace_path` returns on the env-var branch *before* consulting `config.json` and says nothing when the two disagree, so a `config.json` carried in on the mount from another machine can name a host path that does not exist here and sit there indefinitely — misleading anyone who reads the file, and silently deciding the answer for any code path that reads it directly. Only that key is rewritten; `topic_wings` and `hall_keywords` in the same file are user content and are never touched.
  4. **Asserts what mempalace-as-only-memory needs, and repairs none of it (#744).** Native Claude auto-memory is switched off in the shared `settings.json` (`autoMemoryEnabled: false`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`) and the native memory files were mined into the palace, so there is no longer a fallback when mempalace is misconfigured — and every one of these failures is silent. Two things are checked here and **warned** about: `config.json`'s `hooks.auto_save` is `true` (the plugin's Stop/SessionEnd/PreCompact hooks consult that key and save nothing when it is false — which it was, unnoticed, until #744); and `identity.txt` exists on the mount (the L0 context `mempalace wake-up` reads). A third used to live here — `$CLAUDE_CONFIG_DIR/hooks/mempalace-recall.sh` exists and is executable — and moved to `sync-claude-hooks` with #805, which resolves **every** command `settings.json` references rather than that one alone, and so reports the recall hook exactly when the file actually names it. `config.json` and the hook script are hand-maintained and are never written here — a generated value or a stubbed hook would mask the loss of the real one, which is the same silent failure in a better disguise. The one exception is a **wholly absent** `identity.txt`: absent means there is no user content to preserve, so a minimal template naming agent id `devcontainer-claude` is seeded and logged. An existing one is never touched. Nothing in this step is fatal.

  **`mempalace init` runs once per workspace, and its artifacts are ignored machine-wide (#643).** `init` is worth running: the `rooms` list in the `mempalace.yaml` it writes is what routes mined files into rooms, and without it mining collapses into a single `general` room. (`config.json`'s `topic_wings`/`hall_keywords` are a keyword→hall map, not a substitute.) But `init`'s artifacts **cannot be redirected** — it resolves `entities.json`, `mempalace.yaml` and `.mempalace/` from its `--dir` argument, and exposes no `--output`/`--central`/`--palace-path` option, no `config.json` key, and no `MEMPALACE_*` env var that moves them. Upstream's own answer is appending a two-line block to each project's `.gitignore`, which dirties every repo it touches and does not scale across a tree of working repos.

  So the Feature ignores them **machine-wide** instead, at the same `--system` scope as `core.hooksPath` and `core.pager`:

  ```sh
  git config --system core.excludesfile /usr/local/share/git-excludes/gitignore
  ```

  That file lists `init`'s project artifacts (`mempalace.yaml`, `entities.json`, `.mempalace/`) plus the palace artifact names (`chroma.sqlite3`, `knowledge_graph.sqlite3`, `hallways.json`, `known_entities.json`, `hook_state/`, `wal/`, `locks/`), so a palace root that ever lands inside a repo stays untracked too. Two tradeoffs come with it, and both are real:

  1. **These are generic filenames.** `entities.json`, `hallways.json` and `known_entities.json` could plausibly be genuinely tracked files in an unrelated repo, and a global ignore would keep a *new* one out of `git status`. Ignore rules never affect **already-tracked** files, so this can only ever mask a file that isn't committed yet. A repo that needs one back negates it in its own `.gitignore` (`!entities.json`) or uses `git add -f`.
  2. **Setting `core.excludesfile` shadows Git's default `~/.config/git/ignore`.** That default applies only when `core.excludesfile` is unset at *every* scope. If you want your own global excludes, set `git config --global core.excludesfile <path>` — `--global` beats `--system` — and copy these entries into it.

  With the artifacts contained, `mempalace-init-workspace` actually runs it. From `postCreateCommand`, after `resolve-mempal-dir`, it inits **the primary workspace repo** — the `MEMPAL_DIR` that script already resolved (enclosing git worktree root, else the workspace folder), so the resolution logic is not duplicated.

  **The headless contract.** `init` is interactive by default and `postCreateCommand` has no usable stdin, so three independent guards are needed (verified against MemPalace 3.7.1):

  | Guard | Covers |
  |---|---|
  | `--yes` | The entity-confirmation and room-approval prompts. Neither catches `EOFError`, so this — not stdin handling — is what keeps them from raising. (Room approval was only fixed upstream in [#179](https://github.com/MemPalace/mempalace/issues/179); `--yes` did not always cover it.) |
  | `--no-llm` | Provider acquisition and the external-LLM consent gate. `init` defaults to an Ollama provider that isn't running here. |
  | `< /dev/null` | The post-init `Mine this directory now? [Y/n]` prompt, which `--yes` deliberately does **not** cover — upstream scopes `--yes` to entity auto-accept and says so. It *does* catch `EOFError` and treats it as decline, so an immediately-EOF stdin answers it deterministically. |

  **Why not `yes | mempalace init …`.** Two reasons, both reproduced against 3.7.1. It answers `y` to the mine prompt, running a full synchronous mine inside `postCreateCommand` — minutes on a real corpus, and duplicated work since the plugin hooks already mine. And a `yes` pipe never closes, so if a prompt ever escapes `--yes` again (as one did before #179) the `Name (or enter to stop):` loop consumes `y` forever: a local repro produced 160 MB of output in 20 s and had to be killed. EOF is the only input that can neither say yes to something expensive nor fail to terminate a loop. A `timeout` backstop (`MEMPALACE_INIT_TIMEOUT`, default 300 s) covers whatever this analysis missed — a wedged `init` degrades to a warning instead of hanging container creation.

  `--auto-mine` is likewise not passed: the plugin's own hooks already drive mining.

  **`init`'s `.gitignore` append is reverted.** `init` appends its own two-line block to `<repo>/.gitignore` ([upstream #185](https://github.com/MemPalace/mempalace/issues/185)) — exactly the per-repo dirtying the machine-wide `core.excludesfile` above exists to avoid. Those two names are already ignored globally, so the append is redundant *and* leaves an uncommitted diff in your workspace on every fresh container. The script snapshots `.gitignore` and restores it afterwards; if `init` created the file, it is removed — but only after confirming every meaningful line is one MemPalace put there, so a `.gitignore` carrying real content is never destroyed.

  **It is run-once, and never clobbers.** Re-running `init` is *overwrite, not merge* — `save_config()` rebuilds `mempalace.yaml` from freshly detected rooms and writes it `"w"`, and `entities.json` is written the same way, so any hand-tuned room name, description or keyword list would be destroyed. So it inits only when `mempalace.yaml` is **absent**. Once the file exists it is yours to curate, and re-detection is an explicit `rm mempalace.yaml` away. The consequence is the honest tradeoff: **new top-level folders are not picked up automatically** — that is the price of hand-edits surviving. `MEMPALACE_SKIP_INIT=1` opts out entirely.

  Every failure here is a **warning, never fatal**: a missing mine root, a missing binary or a failing `init` all exit 0, because mining still works via the flat `general` fallback. (Contrast `resolve-mempal-dir`, which fails loudly — an unresolvable `MEMPAL_DIR` means mining *nothing*, see #485.)
