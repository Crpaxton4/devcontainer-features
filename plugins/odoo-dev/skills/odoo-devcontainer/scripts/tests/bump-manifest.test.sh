#!/usr/bin/env bash
# bump-manifest.test.sh — plant a module whose __manifest__.py carries a known
# version, drive bump_manifest_version.py over it, and read back the version it
# wrote.
#
# The version *form* is the whole subject (#980). Odoo accepts four spellings
# (`A.B`, `A.B.C`, `<series>.A.B`, `<series>.A.B.C`) and the script used to
# accept one, so a client repo on 4-part versions could not run the script the
# session rule makes mandatory. Each case below is one spelling, and asserts
# the two things a caller depends on: the version left in the manifest, and the
# series it belongs to.
#
# The bump itself is read off real commits (`git log <base>..HEAD`, falling
# back to the last commit), so the fixture is a real git repo and the commit
# subject is how each case selects patch / feat / breaking. No stub: that
# detection is unchanged by #980 and is worth holding still.
#
# Stdlib python3 and git only — no Odoo, no network, no database.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$(cd "$SCRIPT_DIR/.." && pwd)/bump_manifest_version.py"
work="$(mktemp -d "${TMPDIR:-/tmp}/bump-manifest-test.XXXXXX")"
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

if ! command -v git >/dev/null 2>&1; then
  echo "bump-manifest.test.sh: git is required" >&2
  exit 1
fi

# --- fixture: one repo, one module per case ----------------------------------
repo="$work/repo"
mkdir -p "$repo"
git -C "$repo" init -q
git -C "$repo" config user.email test@example.com
git -C "$repo" config user.name "Test"
git -C "$repo" config commit.gpgsign false
# The host's global hooksPath must not run inside the fixture: a developer
# machine with a global pre-commit hook would otherwise decide whether this
# suite can commit at all.
mkdir -p "$work/nohooks"
git -C "$repo" config core.hooksPath "$work/nohooks"
printf 'seed\n' > "$repo/README"
git -C "$repo" add -A
git -C "$repo" commit -q -m "chore: seed"

# A module whose manifest holds exactly the version under test, plus a commit
# whose subject is what determine_bump() reads.
plant() {  # <name> <version> <commit subject> -> echoes the module dir
  local dir="$repo/$1"
  mkdir -p "$dir"
  cat > "$dir/__manifest__.py" <<MANIFEST
{
    'name': '$1',
    'version': '$2',
    'depends': ['base'],
    'license': 'LGPL-3',
}
MANIFEST
  git -C "$repo" add -A >/dev/null
  git -C "$repo" commit -q -m "$3"
  printf '%s' "$dir"
}

# ast.literal_eval, the same read the script does — never a grep, so a quoting
# change in the fixture cannot pass by accident.
version_of() {  # <module dir>
  python3 - "$1/__manifest__.py" <<'PY'
import ast, sys
print(ast.literal_eval(open(sys.argv[1]).read()).get("version", "MISSING"))
PY
}

run() {  # <module dir> [<ODOO_VERSION>] -> sets out and status
  local dir="$1"; shift
  if [ "$#" -gt 0 ]; then
    out="$(ODOO_VERSION="$1" python3 "$SUT" "$dir" 2>&1)"
  else
    out="$(env -u ODOO_VERSION python3 "$SUT" "$dir" 2>&1)"
  fi
  status=$?
}

# --- A.B: series comes from ODOO_VERSION, C padded, then bumped --------------
dir="$(plant mod_two_part 1.0 "fix: two-part version")"
run "$dir" 18.0
expect "A.B exits 0" "$status" "0"
expect "A.B is normalized then patched" "$(version_of "$dir")" "18.0.1.0.1"
contains "A.B normalization is printed" "$out" "1.0 → 18.0.1.0.0"
contains "A.B bump is printed" "$out" "18.0.1.0.0 → 18.0.1.0.1  (patch)"

# --- A.B.C: same, nothing to pad ---------------------------------------------
dir="$(plant mod_three_part 1.2.3 "fix: three-part version")"
run "$dir" 18.0
expect "A.B.C exits 0" "$status" "0"
expect "A.B.C is normalized then patched" "$(version_of "$dir")" "18.0.1.2.4"
contains "A.B.C normalization is printed" "$out" "1.2.3 → 18.0.1.2.3"

# --- <series>.A.B: the #980 report, 18.0.0.4 -> 18.0.0.4.0 -> 18.0.0.4.1 -----
dir="$(plant mod_four_part 18.0.0.4 "fix: four-part version")"
run "$dir" 18.0
expect "<series>.A.B exits 0" "$status" "0"
expect "<series>.A.B is normalized then patched" "$(version_of "$dir")" \
  "18.0.0.4.1"
contains "<series>.A.B normalization is printed" "$out" \
  "18.0.0.4 → 18.0.0.4.0  (normalized to <series>.A.B.C)"

# The series is the module's, not the container's: a 19.0 devcontainer must not
# re-series an 18.0 client module.
dir="$(plant mod_series_wins 18.0.0.4 "fix: series comes off the version")"
run "$dir" 19.0
expect "the version's own series wins over ODOO_VERSION" \
  "$(version_of "$dir")" "18.0.0.4.1"

# --- <series>.A.B.C: canonical already, so no normalization step -------------
dir="$(plant mod_five_part 18.0.1.2.3 "fix: canonical version")"
run "$dir" 18.0
expect "canonical exits 0" "$status" "0"
expect "canonical is patched" "$(version_of "$dir")" "18.0.1.2.4"
expect "canonical prints one line, not two" "$(printf '%s' "$out" | wc -l)" "0"
contains "canonical bump is printed" "$out" "18.0.1.2.3 → 18.0.1.2.4  (patch)"

# A canonical version needs no series from anywhere.
dir="$(plant mod_five_no_env 18.0.1.2.3 "fix: no env needed")"
run "$dir"
expect "canonical needs no ODOO_VERSION" "$status" "0"
expect "canonical patch without ODOO_VERSION" "$(version_of "$dir")" \
  "18.0.1.2.4"

# --- the detection #980 leaves alone -----------------------------------------
dir="$(plant mod_feat 18.0.1.2.3 "feat(mod_feat): a new field")"
run "$dir" 18.0
expect "feat resets the patch" "$(version_of "$dir")" "18.0.1.3.0"
contains "feat is named as the bump" "$out" "(minor)"

dir="$(plant mod_breaking 18.0.1.2.3 "feat!: renamed a field on mod_breaking")"
run "$dir" 18.0
expect "a ! subject resets minor and patch" "$(version_of "$dir")" "18.0.2.0.0"
contains "breaking is named as the bump" "$out" "(major)"

# A short version and a feat bump together: normalization happens first, so the
# feat lands on the padded C and not on a 4-part version.
dir="$(plant mod_short_feat 18.0.0.4 "feat(mod_short_feat): a new field")"
run "$dir" 18.0
expect "normalization precedes a feat bump" "$(version_of "$dir")" "18.0.0.5.0"

# --- series-less with no series to be had: stop, do not guess ----------------
dir="$(plant mod_no_series 1.0 "fix: no series anywhere")"
run "$dir"
expect "series-less + unset ODOO_VERSION exits non-zero" "$status" "1"
contains "the failure names the manifest" "$out" "$dir/__manifest__.py"
contains "the failure names the version read" "$out" "'1.0'"
contains "the failure names the env var that fixes it" "$out" \
  "ODOO_VERSION=<NN.0>"
expect "a refused run leaves the manifest alone" "$(version_of "$dir")" "1.0"

# An ODOO_VERSION that is not a series is not a series.
dir="$(plant mod_bad_env 1.0 "fix: bad env")"
run "$dir" master
expect "a non-series ODOO_VERSION exits non-zero" "$status" "1"
contains "a non-series ODOO_VERSION is named" "$out" "ODOO_VERSION='master'"
expect "a non-series ODOO_VERSION changes nothing" "$(version_of "$dir")" "1.0"

# --- four parts that are not <series>.A.B: invalid in every series -----------
dir="$(plant mod_bad_four 1.2.3.4 "fix: four parts, no series")"
run "$dir" 18.0
expect "a series-less 4-part exits non-zero" "$status" "1"
contains "the 4-part failure explains the shape" "$out" \
  "does not start with an Odoo series"
expect "a series-less 4-part changes nothing" "$(version_of "$dir")" "1.2.3.4"

# --- parse_version, straight on the function ---------------------------------
# The padding and the 4-tuple shape are apply_bump's contract, and only a
# direct call shows the tuple rather than the string built from it.
tuples="$(python3 - "$SUT" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("bmv", sys.argv[1])
bmv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bmv)
cases = [("1.0", "18.0"), ("1.2.3", "18.0"), ("18.0.0.4", None),
         ("18.0.1.2.3", None), ("  18.0.1.2.3  ", None)]
print(" ".join(repr(bmv.parse_version(v, h)) for v, h in cases))
PY
)"
expect "every form returns the same 4-tuple shape" "$tuples" \
  "('18.0', 1, 0, 0) ('18.0', 1, 2, 3) ('18.0', 0, 4, 0) ('18.0', 1, 2, 3) ('18.0', 1, 2, 3)"

rejects="$(python3 - "$SUT" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("bmv", sys.argv[1])
bmv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bmv)
bad = ["18", "1.2.3.4", "18.0.1.2.3.4", "18.0.x.1", "", "18.0.1"]
out = []
for v in bad:
    try:
        bmv.parse_version(v, "18.0")
        out.append(f"{v!r}:ACCEPTED")
    except ValueError:
        out.append(f"{v!r}:rejected")
print(" ".join(out))
PY
)"
expect "what is not an Odoo version is refused" "$rejects" \
  "'18':rejected '1.2.3.4':rejected '18.0.1.2.3.4':rejected '18.0.x.1':rejected '':rejected '18.0.1':rejected"

echo "{\"passed\": $pass, \"failed\": $fail}"
[ "$fail" -eq 0 ]
