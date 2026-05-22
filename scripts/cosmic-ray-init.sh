#!/bin/bash
set -eou pipefail

session_name="${1:?session name is required}"
python_bin="$PWD/.venv/bin/python"

if [[ ! -x "$python_bin" ]]; then
	echo "Expected virtualenv interpreter at $python_bin" >&2
	exit 1
fi

export VIRTUAL_ENV="$PWD/.venv"
export PATH="$VIRTUAL_ENV/bin:$PATH"

mkdir -p .cosmic-ray
"$python_bin" scripts/run_cosmic_ray.py --verbosity=INFO init cosmic-ray.toml ".cosmic-ray/${session_name}.sqlite"
echo "Cosmic Ray session initialized at .cosmic-ray/${session_name}.sqlite"
