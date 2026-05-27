#!/bin/bash
set -eou pipefail

session_name="${1:?session name is required}"
repo_root="${2:-$PWD}"
worker_root="$(mktemp -d "/tmp/odoo-sdk-cosmic-ray.${session_name}.XXXXXX")"

mkdir -p "$worker_root/logs"

copy_worker() {
	local worker_num="$1"
	local worker_dir="$worker_root/worker-$worker_num"

	mkdir -p "$worker_dir"
	rsync -a \
		--exclude '/.*' \
		--exclude '/build/' \
		--exclude '/htmlcov/' \
		--exclude '/odoo_sdk.egg-info/' \
		--exclude '/coverage.xml' \
		--exclude '__pycache__/' \
		--exclude '*.py[cod]' \
		"$repo_root/" "$worker_dir/"

	# Ensure imports inside worker processes resolve to the copied mutable
	# source tree rather than the root editable install.
	ln -sfn src "$worker_dir/odoo_sdk"
}

copy_worker "01"
copy_worker "02"
copy_worker "03"
copy_worker "04"
copy_worker "05"
copy_worker "06"
copy_worker "07"
copy_worker "08"

printf '%s\n' "$worker_root"
