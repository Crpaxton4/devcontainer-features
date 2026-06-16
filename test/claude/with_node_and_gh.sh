#!/bin/bash

# Executed against the 'with_node_and_gh' scenario in scenarios.json, which
# combines the 'claude' Feature with the official node and github-cli
# Features the way a real devcontainer.json would use them together.

set -e

source dev-container-features-test-lib

check "claude code was installed as a global npm package" bash -c "npm list -g @anthropic-ai/claude-code"
check "gh cli is installed" gh --version
check "claude reports a version" claude --version

reportResults
