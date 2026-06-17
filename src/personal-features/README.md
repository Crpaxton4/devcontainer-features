
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
~/.claude        — Claude Code auth and settings
~/.config/gh     — gh CLI auth and settings
~/.bash_history  — bash history file
~/.zsh_history   — zsh history file
```

It's safe to re-run — `mkdir -p` and `touch` are no-ops when targets already exist.

If you skip this step, `install.sh` creates these paths as fallbacks at build time, so the feature still works — but they'll be local to the container image layer rather than bind-mounted from your host home directory, and they won't persist across rebuilds.

## What persists, and where

Config and history are bind-mounted from your host home directory into fixed container paths, so they survive container rebuilds, follow you across projects on the same machine, and are safe from `docker volume prune`:

- `~/.claude` (host) → `/usr/local/share/claude-home` (container) — `CLAUDE_CONFIG_DIR` points here, so Claude Code's auth and settings survive rebuilds.
- `~/.config/gh` (host) → `/usr/local/share/gh-cli-config` (container) — `GH_CONFIG_DIR` points here, so `gh auth login` only needs to happen once per machine.
- `~/.bash_history` / `~/.zsh_history` (host) → `/usr/local/share/shell-history/bash_history|zsh_history` (container) — symlinked from `~/.bash_history` / `~/.zsh_history` in the container, so shell history follows you across rebuilds.

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
- Standards enforced **machine-wide** rather than per-repo, since most of this owner's projects aren't mature enough to have their own hook config checked in. Sets `git config --system core.hooksPath` to a Feature-installed directory (`/usr/local/share/git-hooks`) containing:
  - `commit-msg` — rejects commits whose subject line doesn't follow [Conventional Commits](https://www.conventionalcommits.org/).
  - `pre-commit` — runs `gitleaks protect --staged`.

  This is native Git config, so it applies to *every* repo on the machine with zero per-repo opt-in. A repo that sets its own `core.hooksPath` locally (e.g. via Husky) overrides this as normal Git config precedence — this only fills the gap for repos that don't.
- The [Starship](https://starship.rs) prompt and `zoxide`'s shell hook, plus aliasing `cat`/`find`/`ls` to `bat`/`fd`/`eza`, and persisted shell history (see above).


---

_Note: This file was auto-generated from the [devcontainer-feature.json](https://github.com/Crpaxton4/devcontainer-features/blob/main/src/personal-features/devcontainer-feature.json).  Add additional notes to a `NOTES.md`._
