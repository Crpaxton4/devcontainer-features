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
