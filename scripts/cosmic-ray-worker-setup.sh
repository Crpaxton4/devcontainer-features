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
		--exclude '.git/' \
		--exclude '.venv/' \
		--exclude '.cosmic-ray/' \
		--exclude 'htmlcov/' \
		--exclude 'build/' \
		--exclude 'odoo_sdk.egg-info/' \
		--exclude '__pycache__/' \
		--exclude '*.py[cod]' \
		--exclude '.hypothesis/' \
		--exclude '.coverage' \
		--exclude 'coverage.xml' \
		"$repo_root/" "$worker_dir/"
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