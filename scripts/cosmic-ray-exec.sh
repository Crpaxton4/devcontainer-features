#!/bin/bash
set -eou pipefail

session_name="${1:?session name is required}"
session_file=".cosmic-ray/${session_name}.sqlite"
worker_root=""
worker_pids=()
ports=(18101 18102 18103 18104)
repo_root="$PWD"
python_bin="$repo_root/.venv/bin/python"

if [[ ! -x "$python_bin" ]]; then
	echo "Expected virtualenv interpreter at $python_bin" >&2
	exit 1
fi

export VIRTUAL_ENV="$repo_root/.venv"
export PATH="$VIRTUAL_ENV/bin:$PATH"

cleanup() {
	local exit_code=$?

	trap - EXIT INT TERM

	if [[ ${#worker_pids[@]} -gt 0 ]]; then
		for pid in "${worker_pids[@]}"; do
			if kill -0 "$pid" 2>/dev/null; then
				kill "$pid" 2>/dev/null || true
			fi
		done
		wait "${worker_pids[@]}" 2>/dev/null || true
	fi

	if [[ -n "$worker_root" && -d "$worker_root" ]]; then
		rm -rf "$worker_root"
	fi

	exit "$exit_code"
}

wait_for_port() {
	local port="$1"
	python3 - "$port" <<'PY'
import socket
import sys
import time

port = int(sys.argv[1])
deadline = time.time() + 15

while time.time() < deadline:
	with socket.socket() as sock:
		sock.settimeout(0.2)
		if sock.connect_ex(("127.0.0.1", port)) == 0:
			sys.exit(0)
	time.sleep(0.2)

sys.exit(1)
PY
}

trap cleanup EXIT INT TERM

if [[ ! -f "$session_file" ]]; then
	echo "Cosmic Ray session file not found: $session_file" >&2
	exit 1
fi

worker_root="$(bash ./scripts/cosmic-ray-worker-setup.sh "$session_name")"

for index in "${!ports[@]}"; do
	worker_num="$(printf '%02d' "$((index + 1))")"
	worker_dir="$worker_root/worker-$worker_num"
	worker_log="$worker_root/logs/worker-$worker_num.log"
	port="${ports[$index]}"

	(
		cd "$worker_dir"
		export VIRTUAL_ENV="$repo_root/.venv"
		export PATH="$VIRTUAL_ENV/bin:$PATH"
		"$python_bin" scripts/run_cosmic_ray.py --verbosity=INFO http-worker --port "$port"
	) >"$worker_log" 2>&1 &
	worker_pids+=("$!")

	if ! wait_for_port "$port"; then
		echo "Cosmic Ray worker failed to start on port $port" >&2
		cat "$worker_log" >&2
		exit 1
	fi
done

echo "Cosmic Ray workers started from $worker_root"
"$python_bin" scripts/run_cosmic_ray.py --verbosity=INFO exec cosmic-ray.toml "$session_file"
echo "Cosmic Ray execution completed for $session_file"
