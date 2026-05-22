#!/bin/bash
set -eou pipefail

uv run cosmic-ray --verbosity=INFO exec cosmic-ray.toml ".cosmic-ray/$1.sqlite"
echo "Cosmic Ray execution completed"
