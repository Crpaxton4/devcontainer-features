#!/bin/bash
set -eou pipefail

mkdir -p .cosmic-ray
uv run cosmic-ray --verbosity=INFO init cosmic-ray.toml ".cosmic-ray/$1.sqlite"
echo "Cosmic Ray session initialized at .cosmic-ray/$1.sqlite"
