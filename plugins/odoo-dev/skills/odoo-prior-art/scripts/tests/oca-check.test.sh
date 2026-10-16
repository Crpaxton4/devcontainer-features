#!/usr/bin/env bash
# oca-check.test.sh — offline tests for oca_check.py's --csv handling (#1057).
#
# A bad --csv used to surface only AFTER the whole org scan, as a raw
# FileNotFoundError / TypeError / KeyError traceback, so the scan was wasted.
# The contract tested here: --csv is validated BEFORE the scan, and an absent
# file, an empty file, a directory, or a header without "Module Name" or
# "OCA repo" is a one-line usage error on stderr with exit 2 and no scan.
#
# No network. A stub `git` on PATH answers ls-remote / clone / ls-tree from a
# canned repo and logs every call, so "no scan ran" is an assertion on the log,
# and the valid-CSV case runs the real script end to end through update_csv.
# --repos skips the gh enumeration, so `gh` is never needed.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$(cd "$SCRIPT_DIR/.." && pwd)/oca_check.py"
work="$(mktemp -d "${TMPDIR:-/tmp}/oca-check-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
expect() {
  if [ "$2" = "$3" ]; then pass=$((pass + 1)); else
    fail=$((fail + 1)); echo "FAIL $1: wanted '$3', got '$2'" >&2
  fi
}
contains() {  # <label> <haystack> <needle>
  case "$2" in
    *"$3"*) pass=$((pass + 1)) ;;
    *) fail=$((fail + 1)); echo "FAIL $1: wanted to contain '$3', got '$2'" >&2 ;;
  esac
}

# ---------- stubbed environment ------------------------------------------------
bin="$work/bin"; mkdir -p "$bin"
cat > "$bin/git" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$GIT_FAKE_LOG"
case "$1" in
  ls-remote) printf 'deadbeef\trefs/heads/18.0\n' ;;
  clone) for last; do :; done; mkdir -p "$last" ;;
  -C) printf 'mod_a/__manifest__.py\nmod_a/models.py\n' ;;
esac
exit 0
STUB
chmod +x "$bin/git"
export GIT_FAKE_LOG="$work/git.log"

addons="$work/addons"; mkdir -p "$addons/mod_a" "$addons/mod_b"
printf '{"name": "A", "author": "Odoo Community Association (OCA)"}\n' \
  > "$addons/mod_a/__manifest__.py"
printf '{"name": "B", "author": "Acme"}\n' > "$addons/mod_b/__manifest__.py"

# run <csv> : the real script, a canned one-repo scan, stderr captured apart.
run() {
  : > "$GIT_FAKE_LOG"; rm -f "$work/index.json"
  PATH="$bin:$PATH" python3 "$SUT" "$addons" --series 18.0 \
    --repos stubrepo --cache "$work/cache" -o "$work/index.json" \
    --csv "$1" > /dev/null 2> "$work/err"
  status=$?
  err="$(cat "$work/err")"
}
scanned() { [ -s "$GIT_FAKE_LOG" ] && echo yes || echo no; }
indexed() { [ -f "$work/index.json" ] && echo yes || echo no; }

# ---------- absent file ---------------------------------------------------------
absent="$work/does-not-exist.csv"
run "$absent"
expect "an absent --csv exits 2" "$status" "2"
contains "the message names the path" "$err" "--csv $absent"
contains "the message gives the reason" "$err" "file not found"
expect "the message is one line" "$(printf '%s\n' "$err" | wc -l | tr -d ' ')" "1"
expect "no traceback" "$(printf '%s' "$err" | grep -c Traceback)" "0"
expect "no scan ran for an absent --csv" "$(scanned)" "no"
expect "no index is written for an absent --csv" "$(indexed)" "no"

# ---------- empty file ----------------------------------------------------------
empty="$work/empty.csv"; : > "$empty"
run "$empty"
expect "an empty --csv exits 2" "$status" "2"
contains "the message names the empty path" "$err" "--csv $empty"
contains "the message says the file is empty" "$err" "empty file"
expect "no traceback for an empty file" \
  "$(printf '%s' "$err" | grep -c Traceback)" "0"
expect "no scan ran for an empty --csv" "$(scanned)" "no"

blank="$work/blank.csv"; printf '\n\n' > "$blank"
run "$blank"
expect "a blank-lines-only --csv exits 2" "$status" "2"
contains "a blank-lines-only file is empty" "$err" "empty file"

# ---------- a directory ---------------------------------------------------------
run "$work"
expect "a directory as --csv exits 2" "$status" "2"
contains "the message says it is not a file" "$err" "not a file"

# ---------- header without a required column ------------------------------------
nooca="$work/no-oca-column.csv"
printf 'Module Name,Author\nmod_a,OCA\n' > "$nooca"
run "$nooca"
expect "a header without 'OCA repo' exits 2" "$status" "2"
contains "the message names the missing column" "$err" "'OCA repo'"
contains "the message names the path" "$err" "--csv $nooca"
expect "no scan ran without the OCA repo column" "$(scanned)" "no"

nomod="$work/no-module-column.csv"
printf 'Name,OCA repo\nmod_a,x\n' > "$nomod"
run "$nomod"
expect "a header without 'Module Name' exits 2" "$status" "2"
contains "the message names the missing join key" "$err" "'Module Name'"
expect "no scan ran without the Module Name column" "$(scanned)" "no"

# ---------- a valid CSV passes validation and is filled in ----------------------
good="$work/good.csv"
printf 'Module Name,OCA repo\nmod_a,claimed\nmod_b,keep\n' > "$good"
run "$good"
expect "a valid --csv exits 0" "$status" "0"
expect "a valid --csv is scanned" "$(scanned)" "yes"
expect "a valid --csv writes the index" "$(indexed)" "yes"
expect "no --csv error for a valid file" \
  "$(printf '%s' "$err" | grep -c -- '--csv')" "0"
expect "the found module's cell is filled" \
  "$(sed -n 2p "$good" | tr -d '\r')" "mod_a,OCA/stubrepo (18.0)"
expect "a --repos subset scan leaves an unproven cell alone" \
  "$(sed -n 3p "$good" | tr -d '\r')" "mod_b,keep"

# ---------- update_csv stays defensive on its own --------------------------------
# main() validates first, so this path is a file that changed under the run.
# Call the function directly: the same one-line error, never a KeyError.
direct="$work/direct.csv"
printf 'Name,OCA repo\nmod_a,x\n' > "$direct"
direct_err="$(python3 -c '
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import oca_check
oca_check.update_csv(Path(sys.argv[2]), {"mod_a": "OCA/r (18.0)"}, True)
' "$(dirname "$SUT")" "$direct" 2>&1 >/dev/null)"
direct_status=$?
expect "update_csv exits 2 on a missing column" "$direct_status" "2"
contains "update_csv gives the same message" "$direct_err" \
  "oca_check.py: --csv $direct: header lacks column 'Module Name'"
expect "update_csv raises no KeyError" \
  "$(printf '%s' "$direct_err" | grep -c KeyError)" "0"
expect "update_csv leaves the bad file untouched" \
  "$(cat "$direct")" "$(printf 'Name,OCA repo\nmod_a,x')"

echo "{\"passed\": $pass, \"failed\": $fail}"
[ "$fail" -eq 0 ]
