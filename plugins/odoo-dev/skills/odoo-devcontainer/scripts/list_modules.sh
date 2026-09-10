#!/usr/bin/env bash
# Print all Odoo module directories in cwd as a comma-separated list.
# Usage: odoo -i $(bash list_modules.sh) --stop-after-init
find . -maxdepth 1 -mindepth 1 -type d -not -name '.*' -printf '%f\n' | paste -sd, -
