#!/bin/bash

# Executed against the 'with_node_and_gh' scenario in scenarios.json, which
# combines the 'personal-features' Feature with the official node and
# github-cli Features the way a real devcontainer.json would use them together.

set -e

# shellcheck source=/dev/null  # dev-container-features-test-lib is injected by the test harness at runtime; not resolvable statically. check()/reportResults() come from it.
source dev-container-features-test-lib

check "claude code was installed as a global npm package" bash -c "npm list -g @anthropic-ai/claude-code"
check "gh cli is installed" gh --version
check "claude reports a version" claude --version

reportResults
