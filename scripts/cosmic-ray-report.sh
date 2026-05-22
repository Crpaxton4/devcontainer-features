#!/bin/bash
set -eou pipefail

uv run cr-report --surviving-only ".cosmic-ray/$1.sqlite"
uv run cr-html ".cosmic-ray/$1.sqlite" > ".cosmic-ray/report_$1.html"
echo "Cosmic Ray report generated at .cosmic-ray/report_$1.html"
