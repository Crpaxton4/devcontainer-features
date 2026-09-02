
# Second Brain (second-brain)

Bind-mounts a host 'second brain' knowledge-base directory - taken from the host environment variable SECOND_BRAIN_DIR - into the container at /mnt/second-brain, so session-derived knowledge (notes, decisions, environment quirks) survives container rebuilds and is editable from both host and container. The variable MUST be set on the host: when it is unset or empty the container build fails fast with a clear error instead of silently mounting nothing. The Feature enforces no content conventions - structure and the skills that read/write the knowledge base come separately.

## Example Usage

```json
"features": {
    "ghcr.io/Crpaxton4/devcontainer-features/second-brain:1": {}
}
```

## Options

| Options Id | Description | Type | Default Value |
|-----|-----|-----|-----|


## Contents

- [One-time host setup](#one-time-host-setup)
- [What gets mounted, and where](#what-gets-mounted-and-where)
- [Fail-fast when `SECOND_BRAIN_DIR` is missing](#fail-fast-when-second_brain_dir-is-missing)
- [Why there are no options](#why-there-are-no-options)
- [No content conventions](#no-content-conventions)

## One-time host setup

Before building any container that includes this Feature:

1. Create (or pick) the knowledge-base directory on the host, e.g. `mkdir -p ~/SecondBrain`. The directory **must exist** — what happens when it does not depends on how the dev container CLI runs the container:
   - **`docker run`** (configs built from an `image` or `Dockerfile`): Feature mounts become `--mount` arguments, which do not auto-create missing bind sources — a missing source is a hard container-create failure (`bind source path does not exist`).
   - **`dockerComposeFile`** configs: Feature mounts are written into a generated compose override as `<source>:/mnt/second-brain`, and compose's short volume syntax **auto-creates a missing host path** as an empty directory — the container comes up with a real but empty bind mount and no error. `second-brain-verify` warns (non-fatally) when the target is empty.
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

1. **Mount declaration.** The mount source is `${localEnv:SECOND_BRAIN_DIR}`. When the variable is unset or empty in the environment that ran the dev container CLI, the CLI substitutes an empty string — and what happens next depends on the runtime path:
   - **`docker run`** (`image`/`Dockerfile` configs): Docker refuses to create the container (an empty bind-mount source is invalid). The build stops before anything runs — but with Docker's own terse error.
   - **`dockerComposeFile`** configs: the CLI writes the Feature mount into a generated compose override with no source — `- /mnt/second-brain` — which compose satisfies with an **anonymous volume**. No error anywhere: the container comes up with a real, empty mountpoint at the target that contains nothing from the host.
2. **`second-brain-verify` (onCreateCommand).** `install.sh` cannot perform this check — it runs at image build time, where neither the host environment nor runtime mounts are visible. So the Feature installs `second-brain-verify` and runs it as its `onCreateCommand`, the earliest lifecycle hook inside the created container. It reads `/proc/self/mountinfo` for the mount backing `/mnt/second-brain` (the last entry wins, so over-mounts are honoured) and aborts container creation when:

   - **nothing is mounted there** (the target is a plain directory, or absent) — the canonical message:

     ```text
     ERROR: SECOND_BRAIN_DIR is not set on the host; export it to the knowledge-base directory before building
     ```

   - **the mount is volume-backed** — its mount root has the `…/volumes/<name>/_data` shape that Docker, rootless Docker and Podman use for volumes, whereas a host bind shows the host directory itself. An anonymous volume (64-hex name) reports:

     ```text
     ERROR: /mnt/second-brain is an anonymous container volume, not the host knowledge base.
     SECOND_BRAIN_DIR was empty in the environment that ran the dev container CLI; on compose-based configs the CLI silently turns an empty bind source into an anonymous volume. Set SECOND_BRAIN_DIR and rebuild.
     ```

     and a named volume reports `ERROR: /mnt/second-brain is a container volume (<name>), not a host bind mount.`

   On success it prints the mount root (`second-brain: knowledge base mounted at /mnt/second-brain (mount root: <host path>)`). An empty knowledge base only earns `WARNING: /mnt/second-brain is empty` — non-fatal, because the Feature has no content conventions (see below) and mount inspection cannot tell a freshly created vault from a host path compose auto-created for a mistyped `SECOND_BRAIN_DIR`.

   This catches the compose path, and any other tooling path where the container comes up without the host directory, instead of relying on a Docker-level error.

`second-brain-verify` honours two environment variables so the Feature's tests can exercise every branch against fixture files without provisioning a container per case: `SECOND_BRAIN_VERIFY_TARGET` (mount point to look for; default `/mnt/second-brain`) and `SECOND_BRAIN_VERIFY_MOUNTINFO` (mountinfo file to read; default `/proc/self/mountinfo`). They are test-only overrides — the shipped mount shape is fixed, for the reasons below.

## Why there are no options

The issue that motivated this Feature proposed three options: container path, read-only flag, and env-var-name override. None of them is implementable: per the [Features specification](https://containers.dev/implementors/features/), a Feature's `mounts` are **static metadata** — the only substitution supported there is `${devcontainerId}`. There is no `${featureOption:...}` substitution, so option values can never reach the `source`, `target`, or `type`/`readonly` of a declared mount. (Even the `${localEnv:...}` used for the source is dev container CLI behaviour on merged Feature metadata, not something the spec extends to option values.)

Consequences, and the escape hatch:

- **Container path** is fixed at `/mnt/second-brain`.
- **The mount is always read-write.**
- **The host variable name is fixed** as `SECOND_BRAIN_DIR`.

A consumer who needs a different shape can skip this Feature and declare the mount directly in `devcontainer.json` (where `mounts` accepts whatever `localEnv` variable, target, and `readonly` flag they want) — at the cost of the fail-fast verification this Feature adds.

## No content conventions

The Feature deliberately enforces no structure inside the knowledge base — no index files, no naming scheme, no required layout. Conventions and the skills that read/write the knowledge base are developed separately.


---

_Note: This file was auto-generated from the [devcontainer-feature.json](https://github.com/Crpaxton4/devcontainer-features/blob/main/devcontainer-features/src/second-brain/devcontainer-feature.json).  Add additional notes to a `NOTES.md`._
