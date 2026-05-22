#!/bin/bash
set -eou pipefail

session_name="${1:?session name is required}"

uv run cosmic-ray --verbosity=INFO exec cosmic-ray.toml ".cosmic-ray/${session_name}.sqlite"
echo "Cosmic Ray execution completed for .cosmic-ray/${session_name}.sqlite"
