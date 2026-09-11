# _common.sh — sourced, never executed. Repo-location resolution shared by every
# script in this skill.
#
# The lifted originals hard-coded repo_dir = "$REPOS_DIR/$repo". That holds on
# the host, where the repos tree exists, and fails everywhere else — most
# importantly inside the Odoo devcontainer, where the checkout is bind-mounted
# at /mnt/extra-addons and no repos tree is visible at all. --repo-path is the
# escape hatch: give a path and no tree resolution is attempted.
#
# ODOO_REPO_MAP_SCRIPTS lets the sibling skill move without editing five files.

REPO_MAP_SCRIPTS="${ODOO_REPO_MAP_SCRIPTS:-$(cd "$SCRIPT_DIR/../../odoo-repo-map/scripts" 2>/dev/null && pwd)}"

# resolve_repo_dir <repo_name> <explicit_path_or_empty> -> echoes the repo dir
resolve_repo_dir() {
  local repo="$1" explicit="${2:-}" dir
  if [ -n "$explicit" ]; then
    dir="$explicit"
  else
    [ -n "${REPOS_DIR:-}" ] || {
      [ -x "$REPO_MAP_SCRIPTS/repos-dir.sh" ] || {
        echo "cannot locate odoo-repo-map/scripts/repos-dir.sh (set ODOO_REPO_MAP_SCRIPTS) and no --repo-path given" >&2
        return 2
      }
      REPOS_DIR="$("$REPO_MAP_SCRIPTS/repos-dir.sh" --raw)" || return 2
    }
    dir="$REPOS_DIR/$repo"
  fi
  [ -e "$dir/.git" ] || { echo "not a git repo: $dir" >&2; return 2; }
  # A worktree's .git is a file, and a bind-mounted checkout may be a symlink;
  # rev-parse is the only honest test.
  git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
    echo "not a git work tree: $dir" >&2; return 2; }
  printf '%s\n' "$dir"
}

# owner/repo out of either remote form: git@host:owner/repo.git or https://host/owner/repo
remote_slug() {
  printf '%s' "$1" | sed -E 's#^[^@/]+@[^:]+:##; s#^[a-z]+://[^/]+/##; s#/+$##; s#\.git$##'
}
