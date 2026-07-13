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

Run the repo's setup script once per machine, **before** starting any dev container that uses this Feature. Use the one that matches the host you launch VS Code from:

```sh
./setup.sh      # Linux, WSL, macOS
```

```powershell
.\setup.ps1     # native Windows host
```

If PowerShell refuses to run it ("running scripts is disabled on this system"), use `powershell -ExecutionPolicy Bypass -File .\setup.ps1`.

This creates the host-side directories that are bind-mounted into the container:

```
~/.claude                            — Claude Code auth and settings
~/.config/gh                         — gh CLI auth and settings
~/.config/odoo_sdk                   — odoo_sdk connection config
~/.config/pr-automation              — create-pr config (global.yaml + projects/)
~/.config/coderabbit                 — CodeRabbit CLI config/auth state
~/.config/devcontainer/shell-history — bash history
```

It's safe to re-run — creating an existing directory is a no-op.

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
- `~/.config/odoo_sdk` (host) → `/usr/local/share/odoo-sdk-config` (container) — `odoo_sdk_CONFIG` points at `config.ini` inside it.
- `~/.config/pr-automation` (host) → `/usr/local/share/pr-automation` (container) — `PR_AUTOMATION_CONFIG_DIR` points here, so `create-pr` picks up your global and per-project PR config across rebuilds. Optional: `create-pr` still works with no config mounted.
- `~/.config/coderabbit` (host) → `/usr/local/share/coderabbit-config` (container) — `CODERABBIT_CONFIG_DIR` points here, so CodeRabbit CLI config/auth state can persist across rebuilds.
- `~/.config/devcontainer/shell-history` (host) → `/usr/local/share/shell-history` (container) — `HISTFILE` points at `bash_history` inside it, and `~/.bash_history` is symlinked to it, so bash history follows you across rebuilds and is shared across containers. Only bash is wired up; there is no zsh support.

A caveat on the history mount: bash silently drops history if it can't write `HISTFILE`. That's fine by default — Docker Desktop bind mounts are world-writable, and on Linux the dev container CLI remaps the container user to your host uid. But if you set `"updateRemoteUserUID": false` with a non-root `remoteUser` whose uid doesn't match yours on the host, history writes fail without an error.

## Windows and WSL

Mount sources are written as `${localEnv:HOME}${localEnv:USERPROFILE}/...`. The devcontainer spec has no conditional, so this relies on exactly one of the two being defined: Windows sets `USERPROFILE`, Linux/WSL/macOS set `HOME`. They concatenate into a valid path either way.

That leaves one sharp edge. **If `HOME` is set on Windows too, both expand** and the mount source becomes garbage — `/c/Users/you` + `C:\Users\you` + `/.claude` — and the container fails to start with a mount error naming a source that contains *both* `/c/Users/...` and `C:\Users\...`. Git Bash sets `HOME` inside its own shell, so launching VS Code with `code .` from Git Bash leaks it; a `HOME` persisted as a User/Machine variable leaks into every launch. Fix it by removing the persisted `HOME`, or by launching VS Code from PowerShell or the Start menu. `setup.ps1` warns when it detects this.

Mounts resolve against whatever environment launches VS Code, not against where the repo lives. If you open a folder through Remote-WSL, the container mounts the **WSL** home directory — so run `setup.sh` inside WSL, not `setup.ps1` in PowerShell.

## Migrating shell history

Shell history used to be a single-file bind mount of the host's own `~/.bash_history`. It's now a directory mount (`~/.config/devcontainer/shell-history`), because Docker Desktop materialises a missing single-file mount source as a *directory*, which then fails the mount.

Two consequences: your existing history won't appear in the container, and container history no longer writes back into your host shell's history. To carry the old history over:

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

- **Auth persistence is best-effort.** On Linux the CLI prefers a system keyring (libsecret/Secret Service), which typically isn't running in a container — in that case it falls back to on-disk state. `CODERABBIT_CONFIG_DIR` is set to the bind-mounted dir above so any on-disk config/auth survives rebuilds, but the exact config-dir env var / path the CLI honors isn't formally documented and may change; the reliable headless path is to expose `CODERABBIT_API_KEY` via `remoteEnv` and re-run `coderabbit auth login --api-key` (cheap, one command). Verify with `coderabbit doctor`.
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

`claude` is wrapped so that a default session (bare `claude`, `claude "prompt"`, `-p`, `-c`, `-r`, etc.) automatically passes `--ide`, since this Feature is meant purely for use inside a VS Code dev container. Subcommands (`claude mcp`, `claude auth login`, `claude update`, etc.) are passed through unmodified.

## Additional tooling

This Feature is the owner's own personal, opinionated setup, not a configurable toolkit — there are no options to turn pieces on or off. If a tool stops earning its place here, it gets removed outright rather than gated behind a flag. Everything below installs via apt or static binaries, with no dependency on the node Feature.

- Language-agnostic productivity/navigation CLIs: `ripgrep`, `fd`, `fzf`, `bat`, `jq`, `yq`, `eza`, `zoxide`, `tldr` (tealdeer).
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
