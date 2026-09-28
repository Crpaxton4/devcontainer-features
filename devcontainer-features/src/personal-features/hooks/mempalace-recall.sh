#!/usr/bin/env bash
# mempalace-recall.sh — SessionStart hook: inject palace memories for the current
# repo / branch / task as additionalContext.
#
# SHIPPED BY THIS FEATURE (#811). #744 settled on asserting this file's existence
# rather than writing it, on the sound reasoning that "a generated value or a
# stubbed hook would mask the loss of the real one". That is an argument against
# STUBBING, and #811 records that it had been conflated with an argument against
# the Feature owning the file at all. Shipping the REAL script masks nothing — the
# behaviour is present by construction. install.sh stages it into
# /usr/local/share/personal-features/hooks and sync-claude-hooks publishes it into
# $CLAUDE_CONFIG_DIR/hooks, so it resolves on both sides of the ~/.claude bind
# mount (#803) and the #805 audit checks it like any other hook command.
#
# It reads the palace, never writes it: the palace data itself stays local and
# unshipped, so on a machine with no palace this hook prints nothing and the
# session is exactly as it was.
#
# Always exits 0 and prints nothing on any failure — a SessionStart hook that
# exits 2 blocks the session from starting, and a stray byte on stdout lands in
# the session context verbatim.
set -u
IN=$(cat 2>/dev/null || true)
CWD=$(printf '%s' "$IN" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("cwd",""))' 2>/dev/null || true)
[ -z "$CWD" ] && CWD=$PWD
REPO=$(basename "$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null || echo "$CWD")")
BRANCH=$(git -C "$CWD" symbolic-ref --short HEAD 2>/dev/null || true)
TASK=$(printf '%s' "$BRANCH" | grep -oE '^[0-9]+' || true)
PY=/usr/local/share/uv/tools/mempalace/bin/python
[ -x "$PY" ] || exit 0
timeout 18 "$PY" - "$REPO" "$BRANCH" "$TASK" <<'PYEOF' 2>/dev/null || true
import json, sys, os
repo, branch, task = sys.argv[1:4]
try:
    from mempalace.layers import Layer3
    from mempalace.config import MempalaceConfig
    stack = Layer3(palace_path=MempalaceConfig().palace_path)
except Exception:
    sys.exit(0)
query = " ".join(x for x in (repo, branch.replace("-", " "), task) if x)
def hits(wing, k):
    try:
        return stack.search_raw(query, wing=wing, n_results=k)
    except Exception:
        return []
rows = hits("memories", 8) + hits("sessions", 4)
budget = 6000  # chars ~ 1500 tokens
out, seen = [], set()
for h in rows:
    doc = (h.get("text") or "").strip()
    key = (h.get("wing"), h.get("room"), h.get("source_file"), doc[:80])
    if not doc or key in seen: continue
    seen.add(key)
    head = f"[{h.get('wing','?')}/{h.get('room','?')}] {h.get('source_file','')}"
    body = doc[:900]
    chunk = f"{head}\n{body}\n"
    if len(chunk) > budget: break
    budget -= len(chunk); out.append(chunk)
if not out: sys.exit(0)
ctx = (f"MemPalace recall for repo={repo} branch={branch} task={task or '-'} "
       f"(verbatim drawers; search the palace for more):\n\n" + "\n".join(out))
print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}))
PYEOF
exit 0
