
# Personal Features (personal-features)

Installs the owner's personal dev container tooling: Claude Code (wrapped to auto-connect to the IDE), productivity/navigation CLIs, gitleaks secret scanning, machine-wide Conventional Commits + secret-scanning git hooks, and shell enhancements (Starship, aliases, persisted history) - and persists Claude's, the gh CLI's, and shell history config across container rebuilds and across projects. This is the owner's personal, opinionated setup, not a configurable toolkit, so it has no options: tools are added or removed outright rather than gated behind flags.

## Example Usage

```json
"features": {
    "ghcr.io/Crpaxton4/devcontainer-features/personal-features:1": {}
}
```

## Options

| Options Id | Description | Type | Default Value |
|-----|-----|-----|-----|


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

Run the repo's `setup.sh` once per machine before starting any dev container that uses this Feature:

```sh
./setup.sh
```

This creates the host-side directories and files that are bind-mounted into the container:

```
~/.claude              — Claude Code auth and settings
~/.config/gh           — gh CLI auth and settings
~/.config/pr-automation — create-pr config (global.yaml + projects/)
~/.config/coderabbit   — CodeRabbit CLI config/auth state
~/.bash_history        — bash history file
~/.zsh_history         — zsh history file
```

It's safe to re-run — `mkdir -p` and `touch` are no-ops when targets already exist.

If you skip this step, `install.sh` creates these paths as fallbacks at build time, so the feature still works — but they'll be local to the container image layer rather than bind-mounted from your host home directory, and they won't persist across rebuilds.

## What persists, and where

Config and history are bind-mounted from your host home directory into fixed container paths, so they survive container rebuilds, follow you across projects on the same machine, and are safe from `docker volume prune`:

- `~/.claude` (host) → `/usr/local/share/claude-home` (container) — `CLAUDE_CONFIG_DIR` points here, so Claude Code's auth and settings survive rebuilds.
- `~/.config/gh` (host) → `/usr/local/share/gh-cli-config` (container) — `GH_CONFIG_DIR` points here, so `gh auth login` only needs to happen once per machine.
- `~/.config/pr-automation` (host) → `/usr/local/share/pr-automation` (container) — `PR_AUTOMATION_CONFIG_DIR` points here, so `create-pr` picks up your global and per-project PR config across rebuilds. Optional: `create-pr` still works with no config mounted.
- `~/.bash_history` / `~/.zsh_history` (host) → `/usr/local/share/shell-history/bash_history|zsh_history` (container) — symlinked from `~/.bash_history` / `~/.zsh_history` in the container, so shell history follows you across rebuilds.
- `~/.config/coderabbit` (host) → `/usr/local/share/coderabbit-config` (container) — `CODERABBIT_CONFIG_DIR` points here, so CodeRabbit CLI config/auth state can persist across rebuilds.

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

## Migrating from the old named-volume scheme

If you were previously using this Feature before it switched to bind mounts, copy your existing data out of the old Docker volumes before rebuilding:

```sh
docker run --rm \
    -v personal-features-claude-home:/src \
    -v "$HOME/.claude:/dst" \
    alpine sh -c "cp -a /src/. /dst/"

docker run --rm \
    -v personal-features-gh-config:/src \
    -v "$HOME/.config/gh:/dst" \
    alpine sh -c "cp -a /src/. /dst/"
```

Then remove the old volumes if you no longer need them:

```sh
docker volume rm personal-features-claude-home personal-features-gh-config personal-features-shell-history
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

  1. **Host mount** — add a bind mount for `~/.mempalace` to your `devcontainer.json` so the palace, config, and hook state persist across rebuilds and are shared across projects. Target the container user's home (`/root` when running as root):

     ```jsonc
     "mounts": [
         "source=${localEnv:HOME}/.mempalace,target=/root/.mempalace,type=bind,consistency=cached"
     ]
     ```

     Create the host directory once (`mkdir -p ~/.mempalace`) before first launch.

  2. **Plugin registration (one-time)** — inside the container, register the Claude Code plugin at user scope so its Stop/SessionEnd/PreCompact hooks fire automatically:

     ```sh
     claude plugin install --scope user mempalace
     ```

     This writes only to `~/.claude/` (user scope), never to a project directory.


---

_Note: This file was auto-generated from the [devcontainer-feature.json](https://github.com/Crpaxton4/devcontainer-features/blob/main/devcontainer-features/src/personal-features/devcontainer-feature.json).  Add additional notes to a `NOTES.md`._
