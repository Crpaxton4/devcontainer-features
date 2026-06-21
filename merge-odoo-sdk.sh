#!/usr/bin/env bash
# merge-odoo-sdk.sh
#
# Consolidates odoo_sdk into devcontainer-features while preserving git history.
# Merges from the mcp-server branch (the active development branch of odoo_sdk).
#
# Path mapping:
#   odoo_sdk/src/odoo_sdk/    → src/odoo_sdk/         (no rename needed)
#   odoo_sdk/tests/           → test/odoo_sdk/         (renamed via filter-repo)
#   odoo_sdk root files       → repo root              (pyproject.toml, Makefile, etc.)
#
# Conflicts resolved automatically:
#   README.md, LICENSE        → keep devcontainer-features version
#   .gitignore                → keep dcf version + append Python entries
#   .devcontainer/*.json      → write merged version (Python 3.13 + Node + docker-in-docker)
#
# After this script completes, review staged changes and commit:
#   git diff --cached
#   git commit
#
# Prerequisites:
#   pip install git-filter-repo

set -euo pipefail

# On Windows with Git Bash, pip --user installs scripts to a location not always in PATH.
if ! command -v git-filter-repo >/dev/null 2>&1; then
  _py_scripts="$(python -c 'import sysconfig; print(sysconfig.get_path("scripts", "nt_user"))' 2>/dev/null || true)"
  if command -v cygpath >/dev/null 2>&1 && [[ -n "$_py_scripts" ]]; then
    export PATH="$(cygpath -u "$_py_scripts"):$PATH"
  fi
fi

# ─── Paths ───────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODOO_SDK_PATH="$(cd "$SCRIPT_DIR/../odoo_sdk" && pwd)"
TEMP_CLONE="$SCRIPT_DIR/../odoo_sdk_filtered_temp"
REMOTE_NAME="odoo_sdk_merge"
ODOO_SDK_BRANCH="mcp-server"

# ─── Helpers ─────────────────────────────────────────────────────────────────

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()      { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()     { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ─── Prerequisites ───────────────────────────────────────────────────────────

info "Checking prerequisites..."

command -v git-filter-repo >/dev/null 2>&1 \
  || die "git-filter-repo not found. Install with: pip install git-filter-repo"

[[ -d "$ODOO_SDK_PATH/.git" ]] \
  || die "odoo_sdk repo not found at $ODOO_SDK_PATH"

[[ -d "$SCRIPT_DIR/.git" ]] \
  || die "devcontainer-features git repo not found at $SCRIPT_DIR"

# Ensure clean working tree so the merge state is unambiguous
if [[ -n "$(git -C "$SCRIPT_DIR" status --porcelain)" ]]; then
  die "devcontainer-features has uncommitted changes. Commit or stash first."
fi

if [[ -f "$SCRIPT_DIR/.git/MERGE_HEAD" ]]; then
  die "A merge is already in progress. Finish or abort it first: git merge --abort"
fi

ok "Prerequisites OK"

# ─── Step 1: Create Filtered Clone ───────────────────────────────────────────

info "Step 1: Cloning odoo_sdk ($ODOO_SDK_BRANCH) → temp dir..."
rm -rf "$TEMP_CLONE"
git clone --branch "$ODOO_SDK_BRANCH" --no-local "$ODOO_SDK_PATH" "$TEMP_CLONE" --quiet
ok "Cloned to $TEMP_CLONE"

info "Step 1b: Remapping tests/ → test/odoo_sdk/ via git filter-repo..."
cd "$TEMP_CLONE"
git filter-repo --path-rename tests/:test/odoo_sdk/ --quiet
ok "Path remapping complete"

# ─── Step 2: Merge into devcontainer-features ────────────────────────────────

cd "$SCRIPT_DIR"

info "Step 2: Adding filtered clone as remote and fetching..."
git remote remove "$REMOTE_NAME" 2>/dev/null || true
git remote add "$REMOTE_NAME" "$TEMP_CLONE"
git fetch "$REMOTE_NAME" --quiet
ok "Remote fetched"

info "Step 2b: Merging with --allow-unrelated-histories..."
# --no-commit: stage everything; we resolve conflicts and make post-merge edits before committing
# || true: exit code 1 when conflicts exist is expected
git merge "$REMOTE_NAME/$ODOO_SDK_BRANCH" \
  --allow-unrelated-histories \
  --no-commit \
  --no-ff \
  2>&1 || true

# Verify merge is actually in progress (MERGE_HEAD file created by git merge)
[[ -f "$SCRIPT_DIR/.git/MERGE_HEAD" ]] \
  || die "Merge did not start — something went wrong. Check 'git status'."

ok "Merge applied (conflicts expected — resolving below)"

# ─── Step 3: Resolve Conflicts ───────────────────────────────────────────────

info "Step 3: Resolving conflicts..."

# Helper: check if a path is in add/add or unmerged conflict state
in_conflict() {
  git status --short | grep -qE "^(AA|UU) $1"
}

# README.md — keep devcontainer-features version
if in_conflict "README.md"; then
  git checkout --ours README.md
  git add README.md
  ok "README.md — kept devcontainer-features version (update manually to add odoo_sdk section)"
fi

# LICENSE — keep devcontainer-features version
if in_conflict "LICENSE"; then
  git checkout --ours LICENSE
  git add LICENSE
  ok "LICENSE — kept devcontainer-features version"
fi

# .gitignore — keep dcf version as base, append Python-specific deny entries
if in_conflict ".gitignore"; then
  git checkout --ours .gitignore
  # The odoo_sdk .gitignore uses an allowlist approach (incompatible with dcf's denylist).
  # We add only the useful Python deny entries instead of merging the full file.
  cat >> .gitignore <<'PYIGNORE'

# Python (consolidated from odoo_sdk)
**/__pycache__/
*.py[cod]
.venv/
docs/build/
docs/source/api/
.hypothesis/
.cosmic-ray/
reports/
*.egg-info/
.coverage
.complexipy_cache/
PYIGNORE
  git add .gitignore
  ok ".gitignore — merged (dcf base + Python deny entries appended)"
fi

# devcontainer-lock.json — delete; it regenerates on next container build
git rm --cached .devcontainer/devcontainer-lock.json 2>/dev/null || true
rm -f .devcontainer/devcontainer-lock.json
ok ".devcontainer/devcontainer-lock.json — removed (will regenerate on next container build)"

# devcontainer.json — write merged version (Python 3.13 + Node + docker-in-docker)
info "Step 3b: Writing merged .devcontainer/devcontainer.json..."
mkdir -p .devcontainer
cat > .devcontainer/devcontainer.json <<'DEVCONTAINER'
{
  "image": "mcr.microsoft.com/devcontainers/python:3.13",
  "features": {
    "ghcr.io/devcontainers/features/docker-in-docker:3": {
      "installDockerBuildx": false
    },
    "ghcr.io/devcontainers/features/github-cli:1": {},
    "ghcr.io/devcontainers/features/node:1": {
      "version": "lts"
    },
    "ghcr.io/jsburckhardt/devcontainer-features/uv:1": {},
    "ghcr.io/crpaxton4/devcontainer-features/personal-features:1": {}
  },
  "mounts": [
    "source=${localWorkspaceFolder}/.devcontainer/vscode-config/tasks.json,target=${containerWorkspaceFolder}/.vscode/tasks.json,type=bind,consistency=cached"
  ],
  "updateContentCommand": "npm install -g @devcontainers/cli",
  "remoteUser": "vscode",
  "customizations": {
    "vscode": {
      "extensions": [
        "ms-python.autopep8",
        "ms-python.black-formatter",
        "ms-python.debugpy",
        "ms-python.vscode-python-envs",
        "ms-python.python",
        "ms-python.vscode-pylance",
        "njpwerner.autodocstring",
        "coderabbit.coderabbit-vscode",
        "ms-python.isort"
      ],
      "settings": {
        "json.schemas": [
          {
            "fileMatch": ["*/devcontainer-feature.json"],
            "url": "https://raw.githubusercontent.com/devcontainers/spec/main/schemas/devContainerFeature.schema.json"
          }
        ],
        "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python",
        "python.analysis.typeCheckingMode": "strict",
        "python.languageServer": "Pylance",
        "[python]": {
          "editor.defaultFormatter": "ms-python.black-formatter",
          "editor.formatOnSave": true,
          "editor.quickSuggestions": {
            "comments": "on",
            "other": "on",
            "strings": "on"
          },
          "editor.codeActionsOnSave": {
            "source.organizeImports": "explicit"
          }
        },
        "isort.args": ["--profile", "black"],
        "python.testing.unittestEnabled": true,
        "python.testing.pytestEnabled": false,
        "python.testing.cwd": "${workspaceFolder}",
        "python.testing.unittestArgs": ["-v", "-s", "test/odoo_sdk", "-p", "test_*.py", "-t", "."],
        "testing.coverageToolbarEnabled": false,
        "testing.gutterEnabled": true,
        "testing.showCoverageInExplorer": true,
        "explorer.decorations.badges": true,
        "explorer.decorations.colors": true
      }
    }
  }
}
DEVCONTAINER
git add .devcontainer/devcontainer.json
ok "devcontainer.json — merged version written"

# ─── Step 4: Post-Merge File Updates ─────────────────────────────────────────

info "Step 4: Updating internal path references (tests/ → test/odoo_sdk/)..."

# Create test/__init__.py so module-style test imports work (e.g. python -m unittest test.odoo_sdk....)
if [[ ! -f test/__init__.py ]]; then
  touch test/__init__.py
  git add test/__init__.py
  ok "Created test/__init__.py"
fi

# pyproject.toml: update coverage omit pattern
if [[ -f pyproject.toml ]]; then
  # Change "*/tests/*" → "*/test/odoo_sdk/*" in [tool.coverage.run] omit
  sed -i 's|"[*]/tests/[*]"|"*/test/odoo_sdk/*"|g' pyproject.toml
  git add pyproject.toml
  ok "pyproject.toml — updated coverage omit path"
fi

# Makefile: no tests/ references (delegates to tools/coverage.py) — no changes needed
ok "Makefile — no path references to update"

# ─── Step 5: Verify No Remaining Conflicts ───────────────────────────────────

info "Step 5: Checking for remaining unresolved conflicts..."
CONFLICTS=$(git status --short | grep -E "^(AA|UU|DD)" || true)
if [[ -n "$CONFLICTS" ]]; then
  warn "Unresolved conflicts remain — resolve manually before committing:"
  echo "$CONFLICTS"
else
  ok "No unresolved conflicts"
fi

# ─── Step 6: Cleanup Remote + Temp Clone ─────────────────────────────────────

info "Step 6: Cleaning up..."
git remote remove "$REMOTE_NAME"
rm -rf "$TEMP_CLONE"
ok "Cleanup complete"

# ─── Summary ─────────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Merge staged. Review before committing.${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "Verification:"
echo "  git diff --cached                              # review all staged changes"
echo "  git log --oneline src/odoo_sdk/ | head -10    # verify odoo_sdk history preserved"
echo "  git log --oneline src/personal-features/      # verify dcf history unaffected"
echo ""
echo "Manual steps (before committing):"
echo "  1. Update README.md          — add an odoo_sdk section"
echo "  2. Review .gitignore         — deduplicate any entries"
echo "  3. Copy CLAUDE.md (optional) — cp ../odoo_sdk/CLAUDE.md . (gitignored; for local AI use)"
echo ""
echo "After review:"
echo "  git commit"
echo ""
echo "Post-commit verification:"
echo "  uv run python -m unittest discover -s test/odoo_sdk   # Python tests"
echo "  make coverage                                          # 90% threshold"
echo "  devcontainer features test -f personal-features .     # devcontainer feature still works"
