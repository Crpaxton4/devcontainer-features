#!/usr/bin/env bash
# test-merge.sh
#
# Dry-run wrapper for merge-odoo-sdk.sh.
# Copies both repos to an isolated temp dir, runs the merge, then verifies the result.
# The original repos are never modified.
#
# Usage:
#   bash test-merge.sh [--keep]
#
#   --keep  preserve the temp dir after a successful run (always kept on failure)
#
# Prerequisites: same as merge-odoo-sdk.sh (pip install git-filter-repo)

set -euo pipefail

KEEP=false
[[ "${1:-}" == "--keep" ]] && KEEP=true

# ─── Setup ───────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORIGINAL_DCF="$SCRIPT_DIR"
ORIGINAL_ODOO="$(cd "$SCRIPT_DIR/../odoo_sdk" && pwd)"
TEMP_DIR="$(mktemp -d)"
TEMP_DCF="$TEMP_DIR/devcontainer-features"
TEMP_ODOO="$TEMP_DIR/odoo_sdk"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
FAILURES=0

info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[✓]${NC}    $*"; }
fail()  { echo -e "${RED}[✗]${NC}    $*"; FAILURES=$((FAILURES + 1)); }
warn()  { echo -e "${YELLOW}[!]${NC}    $*"; }

cleanup() {
  local rc=$?
  echo ""
  if [[ $rc -ne 0 || $FAILURES -gt 0 || "$KEEP" == "true" ]]; then
    warn "Temp dir preserved: $TEMP_DIR"
    warn "  Inspect: cd '$TEMP_DCF' && git log --oneline | head -10"
  else
    rm -rf "$TEMP_DIR"
  fi
}
trap cleanup EXIT

echo ""
echo -e "${BLUE}════════════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  merge-odoo-sdk — isolated test run${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════════════${NC}"
info "Temp dir: $TEMP_DIR"
echo ""

# ─── Step 1: Copy both repos ─────────────────────────────────────────────────
# Full filesystem copy including .git so both histories are self-contained.
# Note: this copies .venv/ if present in odoo_sdk; add --exclude or rsync if slow.

info "Step 1: Copying repos to isolated temp dir..."
cp -r "$ORIGINAL_DCF" "$TEMP_DCF"
# Suppress symlink errors from .venv (symlinks to Python executables fail in temp dirs)
cp -r "$ORIGINAL_ODOO" "$TEMP_ODOO" 2>/dev/null || true
rm -rf "$TEMP_ODOO/.venv"  # Remove broken venv; uv sync will recreate it during tests
[[ -d "$TEMP_ODOO/.git" ]] || { echo "[ERROR] odoo_sdk copy failed (.git missing)"; exit 1; }
ok "devcontainer-features → $TEMP_DCF"
ok "odoo_sdk              → $TEMP_ODOO  (.venv excluded)"

# ─── Step 2: Commit untracked scripts ────────────────────────────────────────
# merge-odoo-sdk.sh requires a clean working tree before running.
# The merge scripts are currently untracked; commit them in the temp copy.

info "Step 2: Preparing temp devcontainer-features (committing pending changes)..."
if [[ -n "$(git -C "$TEMP_DCF" status --porcelain)" ]]; then
  git -C "$TEMP_DCF" add -A
  git -C "$TEMP_DCF" \
    -c user.email="test@test.local" \
    -c user.name="Test Runner" \
    commit -q -m "test: stage merge scripts"
  ok "Committed all pending changes in temp copy"
else
  ok "Working tree already clean"
fi

# ─── Step 3: Run merge script ────────────────────────────────────────────────

info "Step 3: Running merge-odoo-sdk.sh on temp copy..."
echo ""
bash "$TEMP_DCF/merge-odoo-sdk.sh"
echo ""
ok "merge-odoo-sdk.sh completed"

# ─── Step 4: Commit merge result ─────────────────────────────────────────────

info "Step 4: Committing merge result in temp copy..."
git -C "$TEMP_DCF" \
  -c user.email="test@test.local" \
  -c user.name="Test Runner" \
  commit -q -m "chore: consolidate odoo_sdk into devcontainer-features monorepo [test]"
ok "Merge committed"

# ─── Step 5: Checks ──────────────────────────────────────────────────────────

cd "$TEMP_DCF"

echo ""
echo -e "${BLUE}─── Checks ──────────────────────────────────────────────────────────${NC}"

check_file() {
  [[ -f "$1" ]] && ok "file exists:   $1" || fail "MISSING file:  $1"
}
check_dir() {
  [[ -d "$1" ]] && ok "dir exists:    $1" || fail "MISSING dir:   $1"
}
check_dir_absent() {
  [[ ! -d "$1" ]] && ok "dir absent:    $1 (expected)" || fail "dir present:   $1 (should be gone)"
}
# check_contains: grep -E pattern
check_contains() {
  local desc="$1" file="$2" pattern="$3"
  grep -qE "$pattern" "$file" 2>/dev/null \
    && ok "$desc" \
    || fail "$desc  →  pattern '$pattern' not found in $file"
}
# check_absent_literal: grep -F fixed string
check_absent_literal() {
  local desc="$1" file="$2" string="$3"
  ! grep -qF "$string" "$file" 2>/dev/null \
    && ok "$desc" \
    || fail "$desc  →  unexpected string '$string' found in $file"
}

# ── devcontainer-features side ───────────────────────────────────────────────

echo ""
echo "  [devcontainer-features side — originals intact]"
check_file "src/personal-features/devcontainer-feature.json"
check_file "src/personal-features/install.sh"
check_file "src/personal-features/starship.toml"
check_file "test/personal-features/test.sh"
check_file "test/personal-features/scenarios.json"
check_file ".github/workflows/test.yaml"
check_file ".github/workflows/release.yaml"
check_file "package.json"
check_file "setup.sh"

# ── odoo_sdk side ────────────────────────────────────────────────────────────

echo ""
echo "  [odoo_sdk side — brought in from mcp-server branch]"
check_file "src/odoo_sdk/__init__.py"
check_file "src/odoo_sdk/client/client.py"
check_file "src/odoo_sdk/transport/rpc.py"
check_file "src/odoo_sdk/transport/json2.py"
check_file "src/odoo_sdk/mcp/server.py"
check_file "src/odoo_sdk/query/domain.py"
check_file "src/odoo_sdk/records/recordset.py"
check_file "test/__init__.py"
check_file "test/odoo_sdk/__init__.py"
check_file "test/odoo_sdk/test_records/test_odoo_recordset.py"
check_file "test/odoo_sdk/test_client/test_odoo_client.py"
check_file "test/odoo_sdk/test_mcp/test_server.py"
check_file "test/odoo_sdk/test_query/test_domain_expression.py"
check_dir  "docs/"
check_dir  "examples/"
check_dir  "tools/"
check_file "tools/coverage.py"
check_file "tools/static_analysis.py"
check_file "pyproject.toml"
check_file "Makefile"
check_file "uv.lock"
check_file ".devcontainer/vscode-config/tasks.json"

# ── Path rename ───────────────────────────────────────────────────────────────

echo ""
echo "  [path rename: tests/ → test/odoo_sdk/]"
check_dir_absent "tests/"

# ── devcontainer-lock.json ───────────────────────────────────────────────────

echo ""
echo "  [devcontainer artifacts]"
if [[ ! -f ".devcontainer/devcontainer-lock.json" ]]; then
  ok "devcontainer-lock.json absent (removed; will regenerate on next container build)"
else
  fail "devcontainer-lock.json should have been removed by merge script"
fi

# ── Content ───────────────────────────────────────────────────────────────────

echo ""
echo "  [content correctness]"
check_contains   "devcontainer.json: Python 3.13 base image" \
  ".devcontainer/devcontainer.json" "python:3\\.13"
check_contains   "devcontainer.json: docker-in-docker feature" \
  ".devcontainer/devcontainer.json" "docker-in-docker"
check_contains   "devcontainer.json: uv feature" \
  ".devcontainer/devcontainer.json" "/uv:"
check_contains   "devcontainer.json: node feature" \
  ".devcontainer/devcontainer.json" "/node:"
check_contains   "devcontainer.json: test/odoo_sdk in unittestArgs" \
  ".devcontainer/devcontainer.json" "test/odoo_sdk"
check_contains   ".gitignore: __pycache__ deny entry added" \
  ".gitignore" "__pycache__"
check_contains   ".gitignore: .venv deny entry added" \
  ".gitignore" "\.venv"
check_contains   "pyproject.toml: coverage omit updated to test/odoo_sdk" \
  "pyproject.toml" "test/odoo_sdk"
check_absent_literal "pyproject.toml: old '*/tests/*' omit pattern removed" \
  "pyproject.toml" '"*/tests/*"'

# ── No conflict markers ───────────────────────────────────────────────────────

echo ""
echo "  [no conflict markers]"
CONFLICT_FILES=$(grep -rl "^<<<<<<< " \
  src/ test/ pyproject.toml .gitignore .devcontainer/ Makefile 2>/dev/null || true)
if [[ -z "$CONFLICT_FILES" ]]; then
  ok "No merge conflict markers found"
else
  fail "Conflict markers in: $(echo "$CONFLICT_FILES" | tr '\n' ' ')"
fi

# ── Git health ────────────────────────────────────────────────────────────────

echo ""
echo "  [git health]"
INDEX_CONFLICTS=$(git status --short | grep -cE "^(AA|UU|DD)" || true)
if [[ "$INDEX_CONFLICTS" -eq 0 ]]; then
  ok "No unresolved conflicts in index"
else
  fail "$INDEX_CONFLICTS unresolved conflict(s) remain in index"
fi

if ! git remote | grep -q "^odoo_sdk_merge$" 2>/dev/null; then
  ok "No stale odoo_sdk_merge remote"
else
  fail "Stale remote 'odoo_sdk_merge' still present"
fi

if [[ ! -f ".git/MERGE_HEAD" ]]; then
  ok "MERGE_HEAD absent (merge commit was finalized)"
else
  fail "MERGE_HEAD still present — merge was not committed"
fi

# ── Git history ───────────────────────────────────────────────────────────────

echo ""
echo "  [git history preservation]"
ODOO_COMMITS=$(git log --oneline src/odoo_sdk/ 2>/dev/null | wc -l | tr -d ' ')
if [[ "$ODOO_COMMITS" -gt 5 ]]; then
  ok "odoo_sdk history preserved: $ODOO_COMMITS commits touch src/odoo_sdk/"
else
  fail "odoo_sdk history: only $ODOO_COMMITS commits — expected >5"
fi

DCF_COMMITS=$(git log --oneline src/personal-features/ 2>/dev/null | wc -l | tr -d ' ')
if [[ "$DCF_COMMITS" -gt 5 ]]; then
  ok "devcontainer-features history intact: $DCF_COMMITS commits touch src/personal-features/"
else
  fail "devcontainer-features history: only $DCF_COMMITS commits — expected >5"
fi

# Verify the merge commit is a true merge (two parents)
PARENT_COUNT=$(git log -1 --format="%P" | wc -w | tr -d ' ')
if [[ "$PARENT_COUNT" -eq 2 ]]; then
  ok "Merge commit has 2 parents (true merge, not squash)"
else
  fail "Merge commit parent count is $PARENT_COUNT — expected 2"
fi

# ── Python tests ─────────────────────────────────────────────────────────────

echo ""
echo "  [python tests]"
if command -v uv >/dev/null 2>&1; then
  info "uv found — syncing deps..."
  uv sync --quiet 2>&1 | tail -2 || { fail "uv sync failed"; }

  info "Running test/odoo_sdk/ test suite..."
  TEST_OUTPUT=$(uv run python -m unittest discover \
    -s test/odoo_sdk -p "test_*.py" -t . 2>&1) && TEST_EXIT=0 || TEST_EXIT=$?

  TESTS_RAN=$(echo "$TEST_OUTPUT" | grep -oE "Ran [0-9]+ test" | grep -oE "[0-9]+" || echo "?")

  if [[ $TEST_EXIT -eq 0 ]]; then
    ok "Python tests passed ($TESTS_RAN tests)"
  else
    fail "Python tests failed ($TESTS_RAN tests ran)"
    echo ""
    echo "$TEST_OUTPUT" | tail -15
    echo ""
  fi
else
  warn "uv not found — skipping Python tests"
  warn "  Run manually: uv sync && uv run python -m unittest discover -s test/odoo_sdk -t ."
fi

# ─── Summary ─────────────────────────────────────────────────────────────────

echo ""
echo -e "${BLUE}════════════════════════════════════════════════════════════════════${NC}"
if [[ $FAILURES -eq 0 ]]; then
  echo -e "${GREEN}  All checks passed.${NC}"
  echo ""
  echo "  Run the actual merge:"
  echo "    cd '$ORIGINAL_DCF'"
  echo "    bash merge-odoo-sdk.sh"
  echo "    git diff --cached"
  echo "    git commit"
else
  echo -e "${RED}  $FAILURES check(s) failed — see output above.${NC}"
  echo "  Temp dir preserved for inspection: $TEMP_DIR"
fi
echo -e "${BLUE}════════════════════════════════════════════════════════════════════${NC}"
echo ""

[[ $FAILURES -eq 0 ]]
