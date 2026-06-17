
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

Three named Docker volumes must exist on the host before the first container start. Run the repo's `setup.sh` once per machine:

```sh
./setup.sh
```

This creates the volumes and seeds their root directory with the correct ownership for your uid. It's safe to re-run — `docker volume create` is a no-op if the volume already exists.

If you skip this step, Docker auto-creates the volumes on first container start, which may seed them under the wrong uid (e.g. if the first container to use them runs as a different user). The Feature's `postStartCommand` corrects this automatically via `sudo chown` on each start, so things will still work — but running `setup.sh` first avoids the need for sudo at runtime.

## What persists, and where

Three named Docker volumes are mounted at fixed container paths and reused by every project/container on the same machine:

- `personal-features-claude-home` → `/usr/local/share/claude-home` — `CLAUDE_CONFIG_DIR` points here, so Claude Code's auth and settings survive rebuilds and follow you across projects.
- `personal-features-gh-config` → `/usr/local/share/gh-cli-config` — `GH_CONFIG_DIR` points here, so `gh auth login` only needs to happen once per machine.
- `personal-features-shell-history` → `/usr/local/share/shell-history` — `~/.bash_history`/`~/.zsh_history` are symlinked here, so shell history follows you across rebuilds and projects.

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
