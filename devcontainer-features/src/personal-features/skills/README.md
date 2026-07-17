# Feature-owned Claude skills

This directory is the source of truth for the Claude Code **skills** that
`personal-features` ships into every container — the owner's consulting
playbook (quote drafting, design docs, discovery notes, Odoo code review,
client status reports). Each skill is a subdirectory holding a `SKILL.md`
(plus any supporting files):

```
skills/
  <skill-name>/
    SKILL.md
```

The actual skill content is authored separately (issue #251); this directory
may therefore be empty of skills apart from this README, and the delivery
mechanism handles both the empty and the populated case.

## Two delivery paths (both live)

These skills reach an agent by **two independent paths**, and this directory
stays the source of truth for both:

1. **Mounted `SKILL.md` files** (Claude Code only) — staged at build time and
   copied into `$CLAUDE_CONFIG_DIR/skills/<name>` at container-create time. See
   [How these skills reach `claude`](#how-these-skills-reach-claude) below.
2. **`odoo-mcp` built-in prompts** (any MCP client) — since #455, each of the
   six skills is also exposed as a built-in MCP prompt by the `odoo-sdk` MCP
   server, so any MCP client gets it without the mount/copy machinery or even a
   live personal-features container. The prompt modules live at
   `libraries/odoo_sdk/src/odoo_sdk/mcp/prompts/builtin/<name>.py` (one per
   skill, underscored: `odoo-quote` → `odoo_quote`, etc.); each embeds this
   directory's `SKILL.md` body verbatim (frontmatter → prompt description,
   markdown body → prompt message) and is registered via the same
   `@builtin_prompt` decorator as `implement_task`/`report_incident`.

Both paths are kept deliberately: the mounted path preserves Claude Code's
native slash-command/skill-discovery UX, while the MCP-prompt path removes the
mount dependency and reaches non-Claude-Code clients. **When you edit a
`SKILL.md` here, mirror the change into the matching prompt module** (the module
embeds the body as a string literal — they are not auto-synced), or the two
paths will drift.

## How these skills reach `claude`

Claude Code discovers user-scope skills at `$CLAUDE_CONFIG_DIR/skills/<name>/SKILL.md`
(`CLAUDE_CONFIG_DIR` overrides the default `~/.claude` config root). In this
Feature `CLAUDE_CONFIG_DIR=/usr/local/share/claude-home` is **bind-mounted from
the host's `~/.claude` at runtime**, so anything `install.sh` writes there at
build time is shadowed by the mount. Delivery is therefore two-stage:

1. **Build time** — `install.sh` copies this tree to
   `/usr/local/share/personal-features/skills/`, a location that is *not* under
   the bind mount.
2. **Container create** — the feature-contributed `postCreateCommand` runs
   `sync-claude-skills` (see the script next to `install.sh`) *after* the mount
   is active. It copies each shipped skill into `$CLAUDE_CONFIG_DIR/skills/<name>`,
   replacing only the feature-owned names and leaving user-authored skills
   untouched. Because the destination is the persisted mount, the skills also
   land on the host's `~/.claude/skills` and follow the user across projects.

**Copy, not symlink** (Epic I decision): a symlink into the bind mount would
dangle on the host and in containers built without this Feature, so the skills
are copied outright.

## Feature-managed skills are overwritten on every container create

The names under this directory are a **feature-owned namespace**:
`sync-claude-skills` deletes and re-copies each one on every container create,
so any local edit to a shipped skill is discarded. Edits belong in this repo.
To make that unmistakable in-session, every shipped `SKILL.md` must carry a
feature-managed header note, e.g.:

> feature-managed; overwritten on container create — edit in the
> devcontainer-features repo.

(The header lands with the skill content added by #251.)
