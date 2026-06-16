
# Personal Features (personal-features)

Installs the owner's personal dev container tooling (currently Claude Code, Anthropic's CLI) for use inside a VS Code dev container, wraps the `claude` command so it auto-connects to the IDE, and persists Claude's and the gh CLI's auth/config across container rebuilds and across projects.

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

This Feature currently only installs Claude Code. Pair it with the official Node.js and GitHub CLI Features in your `devcontainer.json`:

```jsonc
{
    "features": {
        "ghcr.io/devcontainers/features/node:1": {
            "version": "lts"
        },
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


---

_Note: This file was auto-generated from the [devcontainer-feature.json](https://github.com/Crpaxton4/devcontainer-features/blob/main/src/personal-features/devcontainer-feature.json).  Add additional notes to a `NOTES.md`._
