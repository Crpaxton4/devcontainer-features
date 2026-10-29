# _gh-account.sh — sourced, never executed. Run the gh calls of one script as the
# configured GitHub account instead of the account that is active on the host.
#
# The account is `[github] account` in
# ${ODOO_DEV_HOME:-$HOME/.config/odoo-dev}/config.toml, else ODOO_GITHUB_ACCOUNT
# (the same order as LocalConfig.github_account: the file wins). With no account the active gh account is used.
# The token goes into GH_TOKEN of the calling script only: never argv, a URL,
# a file or git config.
#
# Sourced as:
#   . "<plugin root>/scripts/_gh-account.sh"

# gh_account -> the configured login on stdout, or nothing.
gh_account() {
  local account
  account="$(python3 -c '
import sys, tomllib
try:
    with open(sys.argv[1], "rb") as f:
        print(tomllib.load(f).get("github", {}).get("account") or "")
except Exception:
    pass
' "${ODOO_DEV_HOME:-$HOME/.config/odoo-dev}/config.toml" 2>/dev/null || true)"
  printf '%s\n' "${account:-${ODOO_GITHUB_ACCOUNT:-}}"
}

# gh_auth_as_account -> 0 (no account set, or GH_TOKEN now holds its token) | 1
gh_auth_as_account() {
  local account token
  account="$(gh_account)"
  [ -n "$account" ] || return 0
  token="$(gh auth token --user "$account" 2>/dev/null)" && [ -n "$token" ] || {
    echo "no gh token for GitHub account '$account': run gh auth login as it, or change [github] account in config.toml / ODOO_GITHUB_ACCOUNT" >&2
    return 1
  }
  export GH_TOKEN="$token"
}
