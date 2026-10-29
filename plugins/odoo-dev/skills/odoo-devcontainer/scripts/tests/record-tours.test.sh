#!/usr/bin/env bash
# record-tours.test.sh — drive the record-tours entry command and
# stitch_tours.py without Odoo, without ffmpeg and without network.
#
# record-tours: ODOO_VERSION selects the patch set (#1049). The cases cover the
# refusals (unset, malformed, a series with no implementation) and the two
# series that have one (17.0 and 18.0). A stub `odoo` package on PYTHONPATH
# stands in for Odoo: odoo.cli.main records the arguments it receives, and
# odoo.tests.common carries plain HttpCase and ChromeBrowser classes whose
# patched state the stub reports.
#
# stitch_tours.py: fake ffmpeg and ffprobe on PATH log their argv and write an
# empty output file. The suite checks the files stitch_tours.py writes into the
# work directory. These cases need Pillow and PyYAML in python3 and one TrueType
# font to stand in for Roboto; without them they are skipped with a reason.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS="$(cd "$SCRIPT_DIR/.." && pwd)"
SUT="$SCRIPTS/record-tours"
STITCH="$SCRIPTS/stitch_tours.py"
work="$(mktemp -d "${TMPDIR:-/tmp}/record-tours-test.XXXXXX")"
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
    *) fail=$((fail + 1))
       echo "FAIL $1: no '$3' in:" >&2
       echo "$2" | sed 's/^/       /' >&2 ;;
  esac
}

if ! command -v python3 >/dev/null 2>&1; then
  echo "record-tours.test.sh: python3 is required" >&2
  exit 1
fi

# --- fixture: a stub odoo package --------------------------------------------
stub="$work/stub"
mkdir -p "$stub/odoo/cli" "$stub/odoo/tests"
: > "$stub/odoo/__init__.py"
: > "$stub/odoo/tests/__init__.py"
cat > "$stub/odoo/cli/__init__.py" <<'EOF'
import sys

from odoo.tests import common


def main():
    print('argv=%s' % ' '.join(sys.argv[1:]))
    print('start_tour=%s' % common.HttpCase.start_tour.__module__)
    print('stop=%s' % common.ChromeBrowser.stop.__module__)
EOF
cat > "$stub/odoo/tests/common.py" <<'EOF'
class HttpCase:
    def start_tour(self, url_path, tour_name, step_delay=None, **kwargs):
        pass


class ChromeBrowser:
    def stop(self):
        pass
EOF

run_sut() {  # <ODOO_VERSION or -> <args...>; stdout and stderr, then the exit code
  local version="$1"; shift
  local out rc
  if [ "$version" = "-" ]; then
    out="$(env -u ODOO_VERSION -u ODOO_VENV_PYTHON PYTHONPATH="$stub" python3 "$SUT" "$@" 2>&1)"
  else
    out="$(env -u ODOO_VENV_PYTHON ODOO_VERSION="$version" PYTHONPATH="$stub" python3 "$SUT" "$@" 2>&1)"
  fi
  rc=$?
  printf '%s\nrc=%s\n' "$out" "$rc"
}

# --- record-tours: refusals --------------------------------------------------
out="$(run_sut - -d db)"
contains "unset: names the variable" "$out" "ODOO_VERSION is not set"
case "$out" in *"rc=0") fail=$((fail + 1)); echo "FAIL unset: exit 0" >&2 ;; *) pass=$((pass + 1)) ;; esac

out="$(run_sut 16.0 -d db)"
contains "16.0: names the series" "$out" "no implementation for Odoo series 16.0"
case "$out" in *"rc=0") fail=$((fail + 1)); echo "FAIL 16.0: exit 0" >&2 ;; *) pass=$((pass + 1)) ;; esac

for series in 19.0 20.0; do
  out="$(run_sut "$series" -d db)"
  contains "$series: names the series" "$out" "no implementation for Odoo series $series"
  case "$out" in *"rc=0") fail=$((fail + 1)); echo "FAIL $series: exit 0" >&2 ;; *) pass=$((pass + 1)) ;; esac
done

out="$(run_sut banana -d db)"
contains "malformed: names the value" "$out" "ODOO_VERSION 'banana' is not an Odoo series"
case "$out" in *"rc=0") fail=$((fail + 1)); echo "FAIL malformed: exit 0" >&2 ;; *) pass=$((pass + 1)) ;; esac

# --- record-tours: 17.0 and 18.0 ---------------------------------------------
for series in 17.0 18.0; do
  out="$(run_sut "$series" -d mydb --test-enable --stop-after-init)"
  contains "$series: odoo.cli.main gets the remaining args" "$out" "argv=-d mydb --test-enable --stop-after-init"
  contains "$series: start_tour patched" "$out" "start_tour=record_tours.odoo17"
  contains "$series: ChromeBrowser.stop patched" "$out" "stop=record_tours.odoo17"
  contains "$series: exit 0" "$out" "rc=0"
done

# --- record-tours: options parse and are removed from the odoo args -----------
out="$(run_sut 17.0 --no-gif --ffmpeg /nonexistent/ffmpeg --timeout 5 --gif-filter fps=2 -d mydb)"
contains "options: only the odoo args remain" "$out" "argv=-d mydb"
contains "options: exit 0" "$out" "rc=0"

out="$(run_sut 17.0 --python /nonexistent/python -d mydb)"
contains "--python missing: refused" "$out" "interpreter /nonexistent/python is not executable"

# --- stitch_tours.py ---------------------------------------------------------
font_src="$(find /usr/share/fonts -name '*.ttf' 2>/dev/null | head -n 1)"
skip_reason=""
if ! python3 -c 'import PIL, yaml' >/dev/null 2>&1; then
  skip_reason="Pillow or PyYAML is not importable by python3"
elif [ -z "$font_src" ]; then
  skip_reason="no TrueType font under /usr/share/fonts to stand in for Roboto"
fi

if [ -n "$skip_reason" ]; then
  echo "SKIP stitch_tours.py cases: $skip_reason"
else
  fonts="$work/fonts"; bin="$work/bin"; rec="$work/rec"; wd="$work/wd"
  mkdir -p "$fonts" "$bin" "$rec"
  for style in Regular Bold Light; do cp "$font_src" "$fonts/Roboto-$style.ttf"; done
  : > "$rec/tour_one_20260101_000000.mp4"
  : > "$rec/tour_two_20260101_000000.mp4"
  cat > "$bin/ffmpeg" <<'EOF'
#!/bin/sh
echo "$*" >> "${FAKE_LOG:?}"
for last; do :; done
: > "$last"
EOF
  cat > "$bin/ffprobe" <<'EOF'
#!/bin/sh
echo "10.0"
EOF
  chmod +x "$bin/ffmpeg" "$bin/ffprobe"
  cat > "$work/chapters.yaml" <<EOF
title: Walkthrough
subtitle: Test
end_card: The end
recordings_dir: $rec
output: $work/out_{date}.mp4
splash_seconds: 5
hold_seconds: 3
fade_seconds: 0.5
width: 640
height: 360
chapters:
  - tour: tour_one
    title: First chapter
    description: The first tour.
  - tour: tour_two
    title: Second chapter
    description: The second tour.
EOF
  out="$(FAKE_LOG="$work/fake.log" python3 "$STITCH" "$work/chapters.yaml" \
    --work-dir "$wd" --font-dir "$fonts" --ffmpeg "$bin/ffmpeg" --ffprobe "$bin/ffprobe" 2>&1)"
  rc=$?
  expect "stitch: exit code" "$rc" "0"
  [ "$rc" -ne 0 ] && echo "$out" | sed 's/^/       /' >&2
  expect "stitch: two chapter blocks" "$(grep -c '^\[CHAPTER\]' "$wd/chapters.ffmeta" 2>/dev/null)" "2"
  contains "stitch: chapter title in ffmeta" "$(cat "$wd/chapters.ffmeta" 2>/dev/null)" "title=Second chapter"
  # title + 2 x (splash + tour) + end = 6 segments
  expect "stitch: concat list has six segments" "$(grep -c '^file ' "$wd/list.txt" 2>/dev/null)" "6"
  contains "stitch: concat list names a tour segment" "$(cat "$wd/list.txt" 2>/dev/null)" "ch02_tour.mp4"
  contains "stitch: final encode uses faststart" "$(cat "$work/fake.log" 2>/dev/null)" "-movflags +faststart"
  [ -f "$wd/ch01_splash.png" ] && pass=$((pass + 1)) || { fail=$((fail + 1)); echo "FAIL stitch: no splash png" >&2; }
fi

echo "record-tours.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
