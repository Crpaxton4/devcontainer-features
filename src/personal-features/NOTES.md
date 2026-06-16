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

## What persists, and where

Two named Docker volumes are mounted at fixed container paths and reused by every project/container on the same machine:

- `personal-features-claude-config` (`/usr/local/share/claude-code-home`) — `~/.claude` and `~/.claude.json` are symlinked here, so Claude Code's auth (`~/.claude/.credentials.json`) and settings survive rebuilds and follow you across projects.
- `personal-features-gh-config` (`/usr/local/share/gh-cli-config`) — set as `GH_CONFIG_DIR`, so `gh auth login` only needs to happen once per machine.

## The `claude` command

`claude` is wrapped so that a default session (bare `claude`, `claude "prompt"`, `-p`, `-c`, `-r`, etc.) automatically passes `--ide`, since this Feature is meant purely for use inside a VS Code dev container. Subcommands (`claude mcp`, `claude auth login`, `claude update`, etc.) are passed through unmodified.

## Optional tooling options

The Claude/gh logic above always runs; everything below is additional tooling, each independently toggleable via a boolean option (`cliTools`, `gitHooks`, `secretsScanning`, `shellEnhancements`). All of it installs via apt or static binaries, with no dependency on the node Feature.

- **`cliTools`** (default `true`) — language-agnostic productivity/navigation CLIs: `ripgrep`, `fd`, `fzf`, `bat`, `jq`, `yq`, `eza`, `zoxide`, `tldr` (tealdeer).
- **`secretsScanning`** (default `true`) — installs [`gitleaks`](https://github.com/gitleaks/gitleaks). Usable manually, and invoked automatically by the global `pre-commit` hook below when `gitHooks` is also enabled.
- **`gitHooks`** (default `true`) — enforces standards **machine-wide** rather than per-repo, since most of this owner's projects aren't mature enough to have their own hook config checked in. Sets `git config --system core.hooksPath` to a Feature-installed directory (`/usr/local/share/git-hooks`) containing:
  - `commit-msg` — rejects commits whose subject line doesn't follow [Conventional Commits](https://www.conventionalcommits.org/).
  - `pre-commit` — runs `gitleaks protect --staged` if gitleaks is installed.

  This is native Git config, so it applies to *every* repo on the machine with zero per-repo opt-in. A repo that sets its own `core.hooksPath` locally (e.g. via Husky) overrides this as normal Git config precedence — this only fills the gap for repos that don't.
- **`shellEnhancements`** (default `false`, opinionated) — installs the [Starship](https://starship.rs) prompt and `zoxide`'s shell hook, aliases `cat`/`find`/`ls` to `bat`/`fd`/`eza`, and persists shell history the same way Claude/gh auth is persisted: a third named volume, `personal-features-shell-history` (`/usr/local/share/shell-history`), with `~/.bash_history`/`~/.zsh_history` symlinked into it so command history follows you across rebuilds and projects.
