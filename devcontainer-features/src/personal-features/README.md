
# Personal Features (personal-features)

Installs the owner's personal dev container tooling: Claude Code (wrapped to auto-connect to the IDE), productivity/navigation CLIs, the qsv CSV toolkit, gitleaks secret scanning, machine-wide Conventional Commits + secret-scanning git hooks, Claude Code lifecycle hooks that record session/tool-call activity into the odoo-sdk local state DB, and shell enhancements (Starship, aliases, persisted history) - and persists Claude's, the gh CLI's, and shell history config across container rebuilds and across projects. This is the owner's personal, opinionated setup, not a configurable toolkit, so it has no options: tools are added or removed outright rather than gated behind flags.

## Example Usage

```json
"features": {
    "ghcr.io/Crpaxton4/devcontainer-features/personal-features:4": {}
}
```

## Options

| Options Id | Description | Type | Default Value |
|-----|-----|-----|-----|


## Contents

- [Companion Features](#companion-features)
- [One-time host setup](#one-time-host-setup)
- [What persists, and where](#what-persists-and-where)
- [Windows and WSL](#windows-and-wsl)
- [Migrating shell history](#migrating-shell-history)
- [CodeRabbit CLI](#coderabbit-cli)
- [The `create-pr` command](#the-create-pr-command)
- [The `claude` command](#the-claude-command)
- [Claude Code lifecycle hooks (odoo-sdk event capture)](#claude-code-lifecycle-hooks-odoo-sdk-event-capture)
- [Odoo consulting skills (two delivery paths)](#odoo-consulting-skills-two-delivery-paths)
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
   ~/.config/coderabbit                 — CodeRabbit CLI config/auth state
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
- `~/.config/coderabbit` (host) → `/usr/local/share/coderabbit-config` (container) — `CODERABBIT_CONFIG_DIR` points here, so CodeRabbit CLI config/auth state can persist across rebuilds.
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

- **Auth persistence is best-effort.** On Linux the CLI prefers a system keyring (libsecret/Secret Service), which usually isn't running in a container, so it falls back to on-disk state. `CODERABBIT_CONFIG_DIR` points at the bind-mounted dir so that state survives rebuilds — but the exact config-dir path the CLI honors isn't documented and may change. The reliable headless path is to expose `CODERABBIT_API_KEY` via `remoteEnv` and re-run `coderabbit auth login --api-key`. Verify with `coderabbit doctor`.
- `.coderabbit.yaml` is a per-repo config file read by CodeRabbit's cloud service. It's authored by the user at the repo root and is **not** managed by this Feature.
- The Claude Code CodeRabbit plugin / Agentic API access may require a paid CodeRabbit plan — check your account's plan if `/coderabbit:review` reports auth or entitlement errors.

## The `create-pr` command

`create-pr` opens a pull request for the current branch with `gh pr create --fill --assignee @me`, applying config-driven defaults so PR creation stays consistent across projects without interactive prompts. Every layer is optional — with no config at all it still creates a PR using `gh`'s own defaults.

What it does:

- **Repo path** — derived from `git config remote.origin.url` (`OWNER/REPO`, scheme/host and trailing `.git` stripped).
- **PR title** — derived from the branch name. A branch named `<num>#<slug>` (e.g. `20545#fast-follow-cleanup`) becomes the title `20545: fast follow cleanup` (hyphens → spaces). Other branch names fall back to `--fill` (commit subject).
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

## The `claude` command

`claude` is wrapped so that a **bare interactive session** — plain `claude` with no arguments, run from a terminal — automatically passes `--ide`, since this Feature is meant purely for use inside a VS Code dev container. **Everything else is passed through unmodified**: subcommands (`claude mcp`, `claude auth login`, `claude update`, etc.), any flags or a prompt (`claude "prompt"`, `-p`, `-c`, `-r`), and non-interactive/piped invocations (`echo … | claude`).

The wrapper injects `--ide` only for the zero-argument TTY case (`[ $# -eq 0 ] && [ -t 0 ]`) rather than maintaining an allowlist of subcommands to *exclude*. The old allowlist had to be hand-edited for every new subcommand, and any subcommand it hadn't been taught about was silently turned into `claude --ide <subcommand>`; the inverted rule can never break a new Claude Code subcommand. **Accepted trade-off:** `claude -c`, `claude -r`, and `claude "prompt"` no longer auto-get `--ide` — pass it explicitly if you want it there.

## Claude Code lifecycle hooks (odoo-sdk event capture)

This Feature provisions a set of Claude Code lifecycle hooks that record session
and tool-call activity into the odoo-sdk local state DB, so time/activity
sessionization has an automatic event stream to work from. This is
infrastructure-level capture: the agent never has to think about logging its own
activity — the hooks fire automatically around every session transition and tool
call.

The hooks are wired into `$CLAUDE_CONFIG_DIR/settings.json` at container-create
time by the feature-contributed `postCreateCommand` (which runs
`sync-claude-skills && sync-claude-hooks`). Build-time writes under
`CLAUDE_CONFIG_DIR` are shadowed by the `~/.claude` bind mount, so the merge has
to happen at runtime — the same pattern the skills sync uses.

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
or `tool_input` contents. Events are attributed to the active odoo-sdk run's
task id when one exists in the current project (via `--attach-active-run`), and
are left untargeted (session-level) otherwise.

**`PreToolUse` excludes `mcp__odoo__*` tools.** The odoo-sdk MCP server already
logs its own tool dispatches server-side, so `claude-event-hook` skips those to
avoid double-counting. That server-side event mirrors this shim's payload
stance: it records only the tool name and task id — never argument values (note
bodies, questions, search queries) — so no free-text inputs are written to the
local events store on either path.

**Never blocks a session.** `claude-event-hook` always exits 0, never writes to
stdout (which the hooks contract could interpret as a permission decision),
runs the SDK under a short timeout, and no-ops cleanly when `odoo-sdk` isn't
installed (e.g. Python <3.10 base images) or the cwd isn't a git repo.

**Opting out.** The merge only ever replaces its own entries (identified by the
`claude-event-hook` command) and preserves all your other settings and hooks. To
disable the capture, remove the `claude-event-hook` entries from
`~/.claude/settings.json` (they will be re-added on the next container create) —
or, to disable it permanently, drop the `sync-claude-hooks` step from the
Feature's `postCreateCommand`. A corrupt/unparseable `settings.json` is left
untouched (and a warning printed) rather than overwritten.

## Odoo consulting skills (two delivery paths)

This Feature ships the owner's Odoo consulting playbook — quote drafting
(`odoo-quote`), Fibonacci estimating (`fibonacci-estimate`), discovery capture
(`discovery-notes`), solution design (`odoo-design-doc`), Odoo code review
(`odoo-code-review`), and weekly client status reports (`client-status-report`).
The source of truth for every skill's content is `skills/<name>/SKILL.md` in
this directory (see `skills/README.md`), and the content reaches an agent by two
independent, deliberately-parallel paths:

1. **Mounted `SKILL.md` files (Claude Code only).** `install.sh` stages the
   `skills/` tree at build time to `/usr/local/share/personal-features/skills`
   (a path *not* under the `~/.claude` bind mount), and the feature-contributed
   `postCreateCommand` runs `sync-claude-skills` at container-create time to copy
   each skill into `$CLAUDE_CONFIG_DIR/skills/<name>`, where Claude Code
   discovers user-scope skills. This preserves Claude Code's native
   skill-discovery / slash-command UX, but only inside a live container with this
   Feature installed and a working bind mount.

2. **`odoo-mcp` built-in prompts (any MCP client).** Since #455, each of the six
   skills is *also* exposed as a built-in MCP prompt by the `odoo-sdk` MCP server
   (`libraries/odoo_sdk/src/odoo_sdk/mcp/prompts/builtin/<name>.py`, one module
   per skill, underscored — `odoo-quote` → `odoo_quote`). Each module embeds its
   `SKILL.md` body verbatim (frontmatter `description` becomes the prompt
   description; the markdown body becomes the returned prompt message) and is
   registered through the same `@builtin_prompt` decorator as `implement_task`
   and `report_incident`. Because the prompts ship inside the SDK package, any
   MCP client gets them for free — no mount, no `postCreateCommand`, no live
   personal-features container required.

Both paths are kept in parallel on purpose: the mount path retains Claude Code's
skill UX, and the MCP-prompt path removes the mount fragility and reaches
non-Claude-Code clients. The trade-off is a manual sync — the prompt modules
embed the `SKILL.md` bodies as string literals, so **an edit to a `SKILL.md`
must be mirrored into its prompt module** (the two are not auto-generated from
each other). If Claude Code's mounted-skill UX is ever retired, the
`install.sh` skill-staging block and `sync-claude-skills` can be removed and the
MCP-prompt path becomes the sole source.

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
- The [Starship](https://starship.rs) prompt and `zoxide`'s shell hook, plus aliasing `cat`/`find`/`ls` to `bat`/`fd`/`eza`, and persisted shell history (see above).
- [`mempalace`](https://github.com/mempalace/mempalace) — a global, cross-project memory palace installed via `uv tool install`. `MEMPAL_DIR=/workspaces` is set in `containerEnv`, so once the Claude Code plugin is registered its hooks auto-mine `/workspaces` in the background; nothing is ever written to a project directory (no `mempalace init`). Two manual steps are required because devcontainer Features cannot add mounts or run per-user Claude commands:

  1. **Host mount** — add a bind mount for `~/.mempalace` to your `devcontainer.json` so the palace, config, and hook state persist across rebuilds and are shared across projects. Target the container user's home (`/root` when running as root). Note the `${localEnv:HOME}${localEnv:USERPROFILE}` prefix — same reason as the Feature's own mounts, see [Windows and WSL](#windows-and-wsl):

     ```jsonc
     "mounts": [
         "source=${localEnv:HOME}${localEnv:USERPROFILE}/.mempalace,target=/root/.mempalace,type=bind,consistency=cached"
     ]
     ```

     Create the host directory once before first launch — `mkdir -p ~/.mempalace`, or `New-Item -ItemType Directory -Force "$env:USERPROFILE\.mempalace"` in PowerShell.

  2. **Plugin registration (one-time)** — inside the container, register the Claude Code plugin at user scope so its Stop/SessionEnd/PreCompact hooks fire automatically:

     ```sh
     claude plugin install --scope user mempalace
     ```

     This writes only to `~/.claude/` (user scope), never to a project directory.


---

_Note: This file was auto-generated from the [devcontainer-feature.json](https://github.com/Crpaxton4/devcontainer-features/blob/main/devcontainer-features/src/personal-features/devcontainer-feature.json).  Add additional notes to a `NOTES.md`._
