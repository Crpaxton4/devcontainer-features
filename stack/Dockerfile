# syntax=docker/dockerfile:1
# Build context is the repository root (see compose.yaml). Stages:
#   base       Python and uv only.
#   dispatcher the dispatcher plus the tools it runs: devcontainer CLI, docker
#              CLI with the compose plugin, git and gh. It has no ENTRYPOINT,
#              so a service chooses its own command.
#   webui      the read-only web interface. Flask only: no Node, docker or
#              devcontainer CLI.

ARG PYTHON_VERSION=3.12
ARG NODE_VERSION=22
ARG UV_VERSION=0.13.4
ARG DEVCONTAINERS_CLI_VERSION=0.89.0

FROM node:${NODE_VERSION}-bookworm-slim AS node

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:${PYTHON_VERSION}-slim-bookworm AS base
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1

FROM base AS dispatcher
ARG DEVCONTAINERS_CLI_VERSION

# docker-ce-cli and docker-compose-plugin from Docker's apt repository, gh from
# GitHub's. Both repositories are signed; the keys are fetched once here.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git gnupg \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
        -o /etc/apt/keyrings/docker.asc \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /etc/apt/keyrings/githubcli.gpg \
    && . /etc/os-release \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        docker-ce-cli docker-compose-plugin gh \
    && rm -rf /var/lib/apt/lists/*

# Node from the official image (only node and npm, so /usr/local keeps Python),
# then the devcontainer CLI.
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && npm install -g "@devcontainers/cli@${DEVCONTAINERS_CLI_VERSION}" \
    && npm cache clean --force

# Copy all of libraries/ so every [tool.uv.sources] path dependency of the
# dispatcher resolves. uv pip honours those sources; plain pip would not.
# --prerelease=allow mirrors [tool.uv] in the dispatcher pyproject (toon-format).
COPY libraries /src/libraries
RUN uv pip install --system --no-cache --prerelease=allow /src/libraries/dispatcher

# Only libraries/webui is copied, so a dispatcher change does not rebuild this layer.
# The container user is set by compose (a numeric uid), so no USER line here.
FROM base AS webui
COPY libraries/webui /src/libraries/webui
RUN uv pip install --system --no-cache /src/libraries/webui
EXPOSE 8765
CMD ["python", "-m", "webui"]
