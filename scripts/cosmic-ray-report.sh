#!/bin/bash
set -eou pipefail

session_name="${1:?session name is required}"
session_file=".cosmic-ray/${session_name}.sqlite"
report_file=".cosmic-ray/report_${session_name}.html"
min_kill_rate="${COSMIC_RAY_MIN_KILL_RATE:-90}"

cr-report --surviving-only "$session_file"
cr-html "$session_file" > "$report_file"
python scripts/check_cosmic_ray_threshold.py "$session_file" "$min_kill_rate"
echo "Cosmic Ray report generated at $report_file"
