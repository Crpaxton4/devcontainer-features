#!/usr/bin/env bash
# usage-log-hook.sh - records one usage line per use of a feature this repo owns (#1204).
#
# One script, three hook registrations (see sync-claude-hooks):
#   PreToolUse (matcher Skill)  the model loaded a skill; name = tool_input.skill
#   UserPromptExpansion         the user typed a skill or slash command;
#                               name = command_name (expansion_type slash_command)
#   SubagentStart               a subagent started; name = agent_type
# and, on SessionStart, it DECLARES the odoo-dev skills, agents, commands and hooks
# and the personal-features hooks, so a reader can compute declared minus used.
#
# Payload field names, checked against https://code.claude.com/docs/en/hooks:
#   UserPromptExpansion: expansion_type, command_name, command_args,
#                        command_source, prompt  (all verified in the page's example)
#   SubagentStart:       agent_id, agent_type    (verified)
#   PreToolUse:          tool_name, tool_input   (verified); the Skill tool_input key
#                        is NOT documented on that page. `.skill` is the key Claude
#                        Code uses; `.name` is read as a fallback.
# Only NAMES are read. tool_input is never logged beyond the skill name, and
# mcp__odoo-mcp__* calls are not logged here (odoo-mcp logs them itself).
#
# Only names in this repo's namespaces are kept: `odoo-dev:` for skills and
# commands; `odoo-dev:`-prefixed or bare `odoo-dev-*` for agent types.
#
# CONTRACT: never write to stdout (SessionStart stdout becomes session context),
# never exit non-zero, and do nothing when ODOO_DEV_USAGE_LOG=0.
set -u

[ "${ODOO_DEV_USAGE_LOG:-1}" != 0 ] || exit 0
command -v usage-log >/dev/null 2>&1 || exit 0
command -v jq >/dev/null 2>&1 || exit 0

payload="$(cat)"
event="$(printf '%s' "$payload" | jq -r '.hook_event_name // empty' 2>/dev/null)"
session="$(printf '%s' "$payload" | jq -r '.session_id // empty' 2>/dev/null)"

record() { # record <kind> <name> [key=value ...]
    usage-log record "$@" "session_id=$session" >/dev/null 2>&1 || true
}

declare_plugin() {
    local cfg plugin items
    cfg="${CLAUDE_CONFIG_DIR:-${HOME:-}/.claude}"
    plugin="$cfg/plugins/marketplaces/devcontainer-features/plugins/odoo-dev"
    [ -d "$plugin" ] || return 0
    items="$(
        {
            for d in "$plugin"/skills/*/; do [ -d "$d" ] && printf 'skill\todoo-dev:%s\n' "$(basename "$d")"; done
            for f in "$plugin"/agents/*.md; do [ -f "$f" ] && printf 'agent\todoo-dev:%s\n' "$(basename "$f" .md)"; done
            for f in "$plugin"/commands/*.md; do [ -f "$f" ] && printf 'command\todoo-dev:%s\n' "$(basename "$f" .md)"; done
            for f in "$plugin"/hooks/*.sh; do [ -f "$f" ] && printf 'hook\t%s\n' "$(basename "$f")"; done
        } | jq -R -s -c --arg src odoo-dev '
            split("\n") | map(select(length > 0) | split("\t")
            | {kind: .[0], name: .[1], context: {source: $src}})' 2>/dev/null
    )"
    [ -n "$items" ] && printf '%s' "$items" | usage-log declare >/dev/null 2>&1
    return 0
}

declare_feature_hooks() {
    local names='["claude-event-hook","worktree-context-hook","odoo-api-guard.sh","force-push-guard.sh","mempalace-recall.sh","odoo-dev-plugin-drift-check.sh"]'
    jq -n -c --argjson n "$names" \
        '$n | map({kind: "hook", name: ., context: {source: "personal-features"}})' 2>/dev/null |
        usage-log declare >/dev/null 2>&1
    return 0
}

case "$event" in
SessionStart)
    declare_plugin
    declare_feature_hooks
    ;;
PreToolUse)
    tool="$(printf '%s' "$payload" | jq -r '.tool_name // empty' 2>/dev/null)"
    [ "$tool" = "Skill" ] || exit 0
    name="$(printf '%s' "$payload" | jq -r '.tool_input.skill // .tool_input.name // empty' 2>/dev/null)"
    case "$name" in odoo-dev:*) record skill "$name" source=odoo-dev via=model ;; esac
    ;;
UserPromptExpansion)
    kind="$(printf '%s' "$payload" | jq -r '.expansion_type // empty' 2>/dev/null)"
    [ "$kind" = "slash_command" ] || exit 0
    name="$(printf '%s' "$payload" | jq -r '.command_name // empty' 2>/dev/null)"
    case "$name" in odoo-dev:*) record command "$name" source=odoo-dev via=user ;; esac
    ;;
SubagentStart)
    agent="$(printf '%s' "$payload" | jq -r '.agent_type // empty' 2>/dev/null)"
    case "$agent" in
    odoo-dev:*) record agent "$agent" source=odoo-dev agent_type="$agent" ;;
    odoo-dev-*) record agent "odoo-dev:$agent" source=odoo-dev agent_type="$agent" ;;
    esac
    ;;
esac
exit 0
