#!/bin/bash
set -eou pipefail

uv run cosmic-ray --verbosity=INFO baseline cosmic-ray.toml
echo "Cosmic Ray baseline completed"
