#!/bin/bash
set -eou pipefail

session_name="session_$(date +%Y%m%d_%H%M%S)"
session_file=".cosmic-ray/${session_name}.sqlite"
baseline_session=".cosmic-ray/${session_name}-baseline.sqlite"
report_file=".cosmic-ray/report_${session_name}.html"
min_kill_rate="${COSMIC_RAY_MIN_KILL_RATE:-90}"
repo_root="$PWD"
temp_config=""
worker_root=""
worker_pids=()
cleanup_done=0
ports=(18101 18102 18103 18104 18105 18106 18107 18108)

cleanup() {
	if [[ "$cleanup_done" -eq 1 ]]; then
		return
	fi
	cleanup_done=1

	trap - EXIT INT TERM

	if [[ ${#worker_pids[@]} -gt 0 ]]; then
		for pid in "${worker_pids[@]}"; do
			if kill -0 "$pid" 2>/dev/null; then
				kill "$pid" 2>/dev/null || true
			fi
		done
		wait "${worker_pids[@]}" 2>/dev/null || true
	fi

	if [[ -n "$temp_config" && -f "$temp_config" ]]; then
		rm -f "$temp_config"
	fi

	if [[ -n "$worker_root" && -d "$worker_root" ]]; then
		rm -rf "$worker_root"
	fi
}

on_signal() {
	local signal_name="$1"
	echo "Received $signal_name, shutting down Cosmic Ray workers" >&2
	exit 130
}

wait_for_port() {
	local port="$1"
	local deadline=$((SECONDS + 15))
	while [[ $SECONDS -lt $deadline ]]; do
		if (echo > /dev/tcp/127.0.0.1/"$port") 2>/dev/null; then
			return 0
		fi
		sleep 0.2
	done
	return 1
}

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

	ln -sfn src "$worker_dir/odoo_sdk"
}

trap cleanup EXIT
trap 'on_signal INT' INT
trap 'on_signal TERM' TERM

# ── Init ──────────────────────────────────────────────────────────────────────
echo "Initialising Cosmic Ray session: $session_name"
mkdir -p .cosmic-ray
cosmic-ray --verbosity=INFO init cosmic-ray.toml "$session_file"
echo "Session initialised at $session_file"

# ── Baseline ──────────────────────────────────────────────────────────────────
echo "Running baseline..."
temp_config="$(mktemp "/tmp/odoo-sdk-cosmic-ray-baseline.${session_name}.XXXXXX.toml")"
sed '/^\[cosmic-ray\.distributor\]/,$d' cosmic-ray.toml > "$temp_config"
printf '\n[cosmic-ray.distributor]\nname = "local"\n' >> "$temp_config"
cosmic-ray --verbosity=INFO baseline --session-file "$baseline_session" "$temp_config"
rm -f "$temp_config"
temp_config=""
echo "Baseline completed at $baseline_session"

# ── Workers + Exec ────────────────────────────────────────────────────────────
echo "Setting up Cosmic Ray workers..."
worker_root="$(mktemp -d "/tmp/odoo-sdk-cosmic-ray.${session_name}.XXXXXX")"
mkdir -p "$worker_root/logs"

for n in 01 02 03 04 05 06 07 08; do
	copy_worker "$n"
done

for index in "${!ports[@]}"; do
	worker_num="$(printf '%02d' "$((index + 1))")"
	worker_dir="$worker_root/worker-$worker_num"
	worker_log="$worker_root/logs/worker-$worker_num.log"
	port="${ports[$index]}"

	(
		cd "$worker_dir"
		cosmic-ray --verbosity=INFO http-worker --port "$port"
	) >"$worker_log" 2>&1 &
	worker_pids+=("$!")

	if ! wait_for_port "$port"; then
		echo "Cosmic Ray worker failed to start on port $port" >&2
		cat "$worker_log" >&2
		exit 1
	fi
done

echo "Workers started from $worker_root"
cosmic-ray --verbosity=INFO exec cosmic-ray.toml "$session_file"
echo "Execution completed for $session_file"

# ── Report ────────────────────────────────────────────────────────────────────
echo "Generating report..."
cr-report --surviving-only "$session_file"
cr-html "$session_file" > "$report_file"

stats="$(
	cosmic-ray dump "$session_file" \
	| jq -rn '
		reduce (inputs | select(length > 0)) as $row (
			{pending: 0, completed: 0, killed: 0};
			if $row[1] == null
			then .pending += 1
			else .completed += 1 |
				if ($row[1].test_outcome // "unknown") == "killed"
				then .killed += 1
				else .
				end
			end
		) | "\(.pending) \(.completed) \(.killed)"
	'
)"
read -r pending completed killed <<< "$stats"

if [[ "$pending" -gt 0 ]]; then
	echo "Mutation session incomplete: $pending work items still pending" >&2
	exit 1
fi

if [[ "$completed" -eq 0 ]]; then
	echo "No completed mutation results were found" >&2
	exit 1
fi

awk -v killed="$killed" -v completed="$completed" -v min_rate="$min_kill_rate" 'BEGIN {
	kill_rate = (killed / completed) * 100
	printf "Cosmic Ray kill rate: %.2f%% (%d/%d)\n", kill_rate, killed, completed
	if (kill_rate < min_rate + 0) {
		printf "Kill rate %.2f%% is below required %.2f%%\n", kill_rate, min_rate > "/dev/stderr"
		exit 1
	}
}'

# ── JSON Export ───────────────────────────────────────────────────────────────
echo "Exporting JSON report..."
mkdir -p reports/mutation
cosmic-ray dump "$session_file" \
	| jq -sn '
		[
			inputs | select(length > 0) | {
				job_id: .[0],
				module: .[1].module_path,
				operator: .[1].operator_name,
				occurrence: .[1].occurrence,
				start_pos: .[1].start_pos,
				end_pos: .[1].end_pos,
				worker_outcome: (.[2].worker_outcome // null),
				test_outcome: (.[2].test_outcome // null)
			}
		]
	' > reports/mutation/mutation.json
echo "JSON report written to reports/mutation/mutation.json"

echo "Report generated at $report_file"
