#!/usr/bin/env bash
# state-dir.test.sh — the state-dir CLI: bare task ids, release keys, fail-soft.
#
# Offline: no network, no docker, no Odoo, no repos tree. Everything is built under
# mktemp and torn down at the end.
#
# The commands used to open with an inline mkdir/grep/&&/|| preamble that a
# worktree-isolated session refuses to run, which made /odoo-dev:pr unreachable from
# the one place odoo-dev-builder is documented to work. The shell moved into
# state-dir.sh, so these cases are what stops it moving back out.
#
# These cases lived in gate.test.sh until the PR gate was removed (#904). They are
# about state-dir.sh and never were about the gate, so they moved here rather than
# going out with it.
#
# The `progress` stage (#961) is asserted at the bottom, by running artifact.sh for
# real. It lands here because this is the one suite that already invokes artifact.sh
# end to end, and a new suite for one stage would be a worse trade than a second
# section in this one. What those cases are about is the closed `status` vocabulary
# and the append-only revision numbering a checkpoint depends on — a flush that
# overwrote the previous one would lose exactly the history it exists to keep.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SD="$HERE/../state-dir.sh"
ART="$HERE/../artifact.sh"

pass=0; fail=0
ok()  { echo "PASS $*"; pass=$((pass + 1)); }
bad() { echo "FAIL $*"; fail=$((fail + 1)); }

STATE="$(mktemp -d)"
mkdir -p "$STATE/tasks/30412" "$STATE/tasks/30413"

# sd <args...> — state-dir.sh against the scratch state dir. Prints "rc|output".
sd() {
  local out rc
  out="$(env ODOO_DEV_STATE_DIR="$STATE" bash "$SD" "$@" 2>/dev/null)"; rc=$?
  printf '%s|%s' "$rc" "$out"
}

# expect_sd <label> <want rc|output> <args...>
expect_sd() {
  local label="$1" want="$2"; shift 2
  local got; got="$(sd "$@")"
  if [ "$got" = "$want" ]; then ok "state-dir.sh: $label"
  else bad "state-dir.sh: $label — wanted [$want], got [$got]"; fi
}

expect_sd "resolves a bare task id"      "0|$STATE/tasks/30412" task --else M -- 30412
expect_sd "creates on demand"            "0|$STATE/tasks/777"   task --create --else M -- 777
expect_sd "will not invent a task dir"   "0|M"                  task --else M -- 999
expect_sd "prose is not a task id"       "0|M"                  task --create --else M -- Create
expect_sd "an empty argument is not one" "0|M"                  task --create --else M -- ''
expect_sd "a path is not a task id"      "0|M"                  task --create --else M -- /tmp
expect_sd "release route resolves"       "0|$STATE/releases/UAT-to-main" \
  release --create --else M -- release UAT main
expect_sd "release needs its sentinel"   "0|M" release --create --else M -- 30412 UAT main
expect_sd "release refuses traversal"    "0|M" release --create --else M -- release ../etc main
expect_sd "release refuses a dotted-out branch" "0|M" release --create --else M -- release a..b main
expect_sd "the state dir itself"         "0|$STATE"

# Nothing above may have created a directory a well-formed invocation could not ask
# for. `tasks/Create` is the bug this assertion is named after.
junk="$(find "$STATE" -mindepth 1 -maxdepth 2 -type d \
        -not -name tasks -not -name releases \
        -not -name '3041[23]' -not -name 777 -not -name UAT-to-main | sort)"
if [ -z "$junk" ]; then ok "state-dir.sh: created no directory a bad argument asked for"
else bad "state-dir.sh: junk under the state dir: $junk"; fi

# A malformed CLI call is a bug in the command file, not something a user typed, so
# it is the one thing that still exits non-zero.
env ODOO_DEV_STATE_DIR="$STATE" bash "$SD" bogus >/dev/null 2>&1; rc=$?
[ "$rc" -eq 2 ] && ok "state-dir.sh: an unknown mode is still a usage error" \
  || bad "state-dir.sh: unknown mode exited $rc, wanted 2"

# artifact.sh takes the same argument the same way, through the same sourced
# helpers, so a chain never has to spell the state dir out twice.
if env ODOO_DEV_STATE_DIR="$STATE" bash "$ART" list 30412 2>/dev/null \
     | grep -q "\"dir\":\"$STATE/tasks/30412\""; then
  ok "artifact.sh: a bare task id resolves against the state dir"
else
  bad "artifact.sh: bare task id did not resolve"
fi

# --- artifact.sh: the progress stage -----------------------------------------------
# The checkpoint an agent with no Edit/Write tool writes, so the inline --json form
# is the one exercised here: it is the only form such an agent can reach.
A="$STATE/tasks/30413"
ROW='{"unit":"acme_sale_pricing","kind":"module-under-test","status":"in-progress","note":"unit tests green"}'

out="$(bash "$ART" put "$A" progress --json "{\"run\":\"r\",\"updated\":\"u\",\"units\":[$ROW]}" 2>&1)"; rc=$?
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -qF '"file":"'"$A"'/progress.json"' \
   && printf '%s' "$out" | grep -qF '"revision":1'; then
  ok "artifact.sh: a valid progress payload lands at revision 1"
else
  bad "artifact.sh: valid progress payload: rc=$rc out=$out"
fi

# A flush is a new revision, never an overwrite, and --latest is the checkpoint.
out="$(bash "$ART" put "$A" progress --json '{"units":[{"unit":"acme_sale_pricing","kind":"module-under-test","status":"done","note":"41 tests"}]}' 2>&1)"; rc=$?
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -qF '"revision":2' \
   && printf '%s' "$out" | grep -qF '"superseded":"'"$A"'/progress.json"'; then
  ok "artifact.sh: a second progress put is revision 2, not an overwrite"
else
  bad "artifact.sh: second progress put: rc=$rc out=$out"
fi
if bash "$ART" get "$A" progress --latest 2>/dev/null | grep -qF '"status": "done"' \
   && bash "$ART" get "$A" progress --first 2>/dev/null | grep -qF '"status": "in-progress"'; then
  ok "artifact.sh: get --latest reads the newest progress revision, --first the oldest"
else
  bad "artifact.sh: progress revisions did not read back in order"
fi

# A fifth status word is refused, and the refusal names all four permitted ones —
# "exits 4" alone would stay green if the enum rule started rejecting everything.
out="$(bash "$ART" put "$A" progress --json '{"units":[{"unit":"a","kind":"suite","status":"partial","note":""}]}' 2>&1)"; rc=$?
if [ "$rc" -eq 4 ] \
   && printf '%s' "$out" | grep -qF 'units[0].status' \
   && printf '%s' "$out" | grep -qF 'done, in-progress, not-started, failed'; then
  ok "artifact.sh: a status outside the four words exits 4 naming all four"
else
  bad "artifact.sh: bad progress status: rc=$rc out=$out"
fi

# The shape of a row is checked too: an empty unit name and a missing units list are
# both payloads nobody can resume from.
out="$(bash "$ART" put "$A" progress --json '{"units":[{"unit":"  ","kind":"suite","status":"done","note":""}]}' 2>&1)"; rc=$?
if [ "$rc" -eq 4 ] && printf '%s' "$out" | grep -qF 'units[0].unit: must not be empty'; then
  ok "artifact.sh: an empty unit name is refused"
else
  bad "artifact.sh: empty unit name: rc=$rc out=$out"
fi
out="$(bash "$ART" put "$A" progress --json '{"run":"r"}' 2>&1)"; rc=$?
if [ "$rc" -eq 4 ] && printf '%s' "$out" | grep -qF 'missing required field: units'; then
  ok "artifact.sh: progress without units is refused"
else
  bad "artifact.sh: progress without units: rc=$rc out=$out"
fi

# Nothing above may have written a file for a payload that was refused: validation
# runs before the name is chosen.
revs="$(find "$A" -maxdepth 1 -name 'progress*.json' | wc -l | tr -d ' ')"
[ "$revs" = 2 ] && ok "artifact.sh: a refused progress payload wrote no file" \
  || bad "artifact.sh: wanted 2 progress revisions on disk, found $revs"

rm -rf "$STATE"

echo
echo "state-dir.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
