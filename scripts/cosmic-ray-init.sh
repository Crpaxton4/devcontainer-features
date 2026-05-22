#!/bin/bash
set -eou pipefail

session_name="${1:?session name is required}"

mkdir -p .cosmic-ray
uv run cosmic-ray --verbosity=INFO init cosmic-ray.toml ".cosmic-ray/${session_name}.sqlite"
echo "Cosmic Ray session initialized at .cosmic-ray/${session_name}.sqlite"
