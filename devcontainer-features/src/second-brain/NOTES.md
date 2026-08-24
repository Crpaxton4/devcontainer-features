## Contents

- [One-time host setup](#one-time-host-setup)
- [What gets mounted, and where](#what-gets-mounted-and-where)
- [Fail-fast when `SECOND_BRAIN_DIR` is missing](#fail-fast-when-second_brain_dir-is-missing)
- [Why there are no options](#why-there-are-no-options)
- [No content conventions](#no-content-conventions)

## One-time host setup

Before building any container that includes this Feature:

1. Create (or pick) the knowledge-base directory on the host, e.g. `mkdir -p ~/SecondBrain`. The directory **must exist**: Docker's `--mount` (which the dev container CLI uses for Feature mounts) does not auto-create missing bind sources — a missing source is a hard container-create failure (`bind source path does not exist`).
2. Export `SECOND_BRAIN_DIR` in the environment the dev container tooling runs in, e.g. in `~/.profile` / `~/.zshenv`:

   ```sh
   export SECOND_BRAIN_DIR="$HOME/SecondBrain"
   ```

   On Windows, set it as a user environment variable. Note that GUI-launched editors (VS Code from the dock/Start menu) may not see shell-profile exports; set the variable at the OS/user level, or launch the editor from a shell that has it.

## What gets mounted, and where

| Host | Container | Mode |
|------|-----------|------|
| `$SECOND_BRAIN_DIR` | `/mnt/second-brain` | read-write bind mount |

The Feature also sets the container environment variable `SECOND_BRAIN_DIR=/mnt/second-brain`, so tools *inside* the container can locate the knowledge base without hardcoding the path. (Same variable name as on the host, container-appropriate value — mirroring how this repo's other Features expose their mounts.)

The mount is read-write and shared across every container that includes the Feature: knowledge recorded in one project's container is visible in all others and survives rebuilds.

## Fail-fast when `SECOND_BRAIN_DIR` is missing

The Feature guarantees the build fails rather than silently mounting nothing:

1. **Mount declaration.** The mount source is `${localEnv:SECOND_BRAIN_DIR}`. When the variable is unset or empty, the dev container CLI substitutes an empty string and Docker refuses to create the container (an empty bind-mount source is invalid). The build stops before anything runs — but with Docker's own terse error.
2. **`second-brain-verify` (onCreateCommand).** `install.sh` cannot perform this check — it runs at image build time, where neither the host environment nor runtime mounts are visible. So the Feature installs `second-brain-verify` and runs it as its `onCreateCommand`, the earliest lifecycle hook inside the created container. It asserts `/mnt/second-brain` is a *real mountpoint* (not just a directory) and otherwise aborts container creation with the canonical message:

   ```text
   ERROR: SECOND_BRAIN_DIR is not set on the host; export it to the knowledge-base directory before building
   ```

   This catches any tooling path where the container comes up without the mount instead of erroring at Docker level.

## Why there are no options

The issue that motivated this Feature proposed three options: container path, read-only flag, and env-var-name override. None of them is implementable: per the [Features specification](https://containers.dev/implementors/features/), a Feature's `mounts` are **static metadata** — the only substitution supported there is `${devcontainerId}`. There is no `${featureOption:...}` substitution, so option values can never reach the `source`, `target`, or `type`/`readonly` of a declared mount. (Even the `${localEnv:...}` used for the source is dev container CLI behaviour on merged Feature metadata, not something the spec extends to option values.)

Consequences, and the escape hatch:

- **Container path** is fixed at `/mnt/second-brain`.
- **The mount is always read-write.**
- **The host variable name is fixed** as `SECOND_BRAIN_DIR`.

A consumer who needs a different shape can skip this Feature and declare the mount directly in `devcontainer.json` (where `mounts` accepts whatever `localEnv` variable, target, and `readonly` flag they want) — at the cost of the fail-fast verification this Feature adds.

## No content conventions

The Feature deliberately enforces no structure inside the knowledge base — no index files, no naming scheme, no required layout. Conventions and the skills that read/write the knowledge base are developed separately.
