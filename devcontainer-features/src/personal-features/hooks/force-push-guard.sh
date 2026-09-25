#!/usr/bin/env bash
# force-push-guard.sh — PreToolUse guard for Bash: deny a force-push whose
# destination is a protected branch, or whose destination cannot be proven.
#
# SHIPPED BY THIS FEATURE (#811). It used to be hand-maintained in the shared
# claude-home, referenced from settings.json by absolute path and owned by nobody
# — the pattern #803/#805/#807 each produced an instance of. install.sh now stages
# it into /usr/local/share/personal-features/hooks and sync-claude-hooks publishes
# it into $CLAUDE_CONFIG_DIR/hooks, so it resolves on both sides of the ~/.claude
# bind mount and the #805 audit checks it like any other hook command.
#
# WHY IT EXISTS: permissions.deny entries are literal prefix matches, so
# "Bash(git push --force*)" and "Bash(git push -f*)" match only when `push` is the
# first token after `git`. Every one of these slips straight past them:
#
#   git -C /path push --force-with-lease origin main
#   git --git-dir=/path/.git push --force origin main
#   git -c user.name=x push -f origin main
#   git push origin +HEAD:main          <- the + refspec is a force push too
#
# Observed, not theorised: a stack-merge script issuing `git -C "$OPS" push
# --force-with-lease` ran clean against both deny rules.
#
# POLICY, matching the protected-branch rule in the shipped settings fragment: a
# force-push to a protected branch is denied; a force-push with no explicit
# destination is denied, because it targets whatever happens to be checked out; a
# force-push to an ordinary feature branch is allowed, since that is what
# restacking a pull request requires.
#
# THE RULES, and the id each denial names:
#
#   force-push-protected-branch     the destination refspec resolves to a branch in
#                                   PROTECTED below.
#   force-push-unknown-destination  a force-push with no explicit refspec, so the
#                                   destination is whatever is checked out.
#
# KNOWN LIMIT: THE FILE-THEN-RUN BYPASS (#832). This guard parses the `git push`
# invocation it is shown. A force-push written into a file by a file-writing tool
# (which this hook never sees) and then invoked as `bash <path>` is never shown to
# it. The same limit applies to odoo-api-guard.sh and is documented at length in
# its header; it is accepted rather than patched, because the only complete fixes
# are to forbid write-then-invoke outright or to interpret every script a command
# names. Treat this as a tripwire against the routine, not a containment boundary.
#
# Always exits 0. A denial is expressed through hookSpecificOutput, never through a
# non-zero exit status.
set -u

IN=$(cat 2>/dev/null || true)
[ -n "$IN" ] || exit 0
command -v python3 >/dev/null 2>&1 || exit 0

printf '%s' "$IN" | python3 -c '
import json, re, sys

PROTECTED = {
    "main", "master", "uat", "staging", "sandbox", "e2e",
    "production", "prod", "staging_v18_6_21_2025",
}

try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if not isinstance(d, dict) or d.get("tool_name") != "Bash":
    sys.exit(0)
cmd = (d.get("tool_input") or {}).get("command", "") or ""
if not cmd:
    sys.exit(0)

def deny(rule_id, reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            "force-push-guard: denied by rule %r. %s" % (rule_id, reason)
        ),
    }}))
    sys.exit(0)

FORCE_LONG = ("--force", "--force-with-lease", "--force-if-includes")

def is_force_flag(tok):
    if tok.startswith("--"):
        return tok.split("=", 1)[0] in FORCE_LONG
    # short-flag cluster containing f, e.g. -f, -fu, -uf
    return bool(re.fullmatch(r"-[A-Za-z]*f[A-Za-z]*", tok))

# Split into segments on shell separators so one git call is judged at a time.
for seg in re.split(r"[;&|]+|\n", cmd):
    toks = seg.split()
    if not toks:
        continue
    # Locate a git invocation, skipping VAR=value prefixes and env/sudo wrappers.
    gi = None
    for i, t in enumerate(toks):
        base = t.rsplit("/", 1)[-1]
        if base == "git":
            gi = i
            break
    if gi is None:
        continue
    rest = toks[gi + 1:]
    if "push" not in rest:
        continue
    after = rest[rest.index("push") + 1:]

    forced = any(is_force_flag(t) for t in after)
    # A leading + on a refspec is a force push regardless of flags.
    plus = [t for t in after if t.startswith("+") and len(t) > 1]
    if not forced and not plus:
        continue

    # Non-flag operands after push: first is the remote, the rest are refspecs.
    operands = [t for t in after if not t.startswith("-")]
    refspecs = operands[1:] if len(operands) > 1 else []

    if not refspecs:
        deny(
            "force-push-unknown-destination",
            "Force-push with no explicit destination refspec, so the target is "
            "whatever is checked out. Name the branch explicitly: "
            "git push --force-with-lease origin <branch>. Command: " + seg.strip()
        )

    for rs in refspecs:
        dst = rs.split(":")[-1].lstrip("+")
        dst = re.sub(r"^refs/heads/", "", dst)
        if dst.lower() in PROTECTED:
            deny(
                "force-push-protected-branch",
                "Force-push to protected branch %r. The shipped settings fragment "
                "records these as never-force-push. Command: %s"
                % (dst, seg.strip())
            )
'
exit 0
