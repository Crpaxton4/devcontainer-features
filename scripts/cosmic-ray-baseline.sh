#!/bin/bash
set -eou pipefail

session_name="${1:?session name is required}"
baseline_session=".cosmic-ray/${session_name}-baseline.sqlite"
temp_config="$(mktemp "/tmp/odoo-sdk-cosmic-ray-baseline.${session_name}.XXXXXX.toml")"

cleanup() {
	rm -f "$temp_config"
}

trap cleanup EXIT

mkdir -p .cosmic-ray
python3 - "$temp_config" <<'PY'
from pathlib import Path
import re
import sys

config = Path("cosmic-ray.toml").read_text()
config, replacements = re.subn(
	r"\[cosmic-ray\.distributor\][\s\S]*$",
	"[cosmic-ray.distributor]\nname = \"local\"\n",
	config,
	count=1,
)

if replacements != 1:
	raise SystemExit("Unable to rewrite Cosmic Ray distributor for baseline")

Path(sys.argv[1]).write_text(config)
PY
python scripts/run_cosmic_ray.py --verbosity=INFO baseline --session-file "$baseline_session" "$temp_config"
echo "Cosmic Ray baseline completed at $baseline_session"
