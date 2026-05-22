#!/bin/bash
set -eou pipefail

session_name="${1:?session name is required}"
baseline_session=".cosmic-ray/${session_name}-baseline.sqlite"

mkdir -p .cosmic-ray
uv run cosmic-ray --verbosity=INFO baseline --session-file "$baseline_session" cosmic-ray.toml
echo "Cosmic Ray baseline completed at $baseline_session"
