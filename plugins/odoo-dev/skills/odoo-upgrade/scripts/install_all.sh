#!/usr/bin/env bash
# Verification loop for an upgrade pass: drop the dev DB and install EVERY
# custom module on a fresh one. Run inside the target-series devcontainer.
# Any traceback / "invalid view" in the output = porting work left.
#
# Usage: install_all.sh [ADDONS_PATH]   (default: /mnt/extra-addons)
set -euo pipefail
cd "${1:-/mnt/extra-addons}"
odoo db drop odoo && odoo -i $(find . -maxdepth 1 -mindepth 1 -type d -not -name '.*' -printf '%f\n' | paste -sd, -) --stop-after-init
