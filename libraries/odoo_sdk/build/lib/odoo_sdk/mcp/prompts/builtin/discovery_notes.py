"""MCP ``discovery_notes`` prompt surface.

Ports the personal-features ``discovery-notes`` skill (source of truth:
``devcontainer-features/src/personal-features/skills/discovery-notes/SKILL.md``) to a
built-in MCP prompt, so any MCP client gets it without the mounted-SKILL.md
delivery path. The prompt takes no arguments and returns the skill's
instructional body verbatim for the caller to act on with its own (read-only)
Odoo tool calls; it never calls into the command registry itself.
"""

from odoo_sdk.commands import Registry

from ._registration import builtin_prompt

__all__ = ["make_discovery_notes_prompt", "discovery_notes"]

# Skill body embedded verbatim (frontmatter and the feature-managed HTML comment
# stripped) so the prompt ships with the SDK package rather than a mounted file.
_BODY = """\
# Client discovery capture

Turn a discovery conversation into structured, reusable notes: what the client
does today, who does it, how much, what hurts, and where Odoo fits. Feeds
`odoo-quote` and `odoo-design-doc`. All tools below are read-only `odoo-sdk`
MCP tools.

## 1. Mine existing context before asking (read-only)

Do not ask the client what the record already tells you. First:

- `search_chatter(query="<client / topic keyword>")` → prior conversations,
  stated requirements, and complaints. Narrow with `model="project.task"` or a
  `date_from="YYYY-MM-DD"`.
- `search_knowledge_articles(query="<process or client>")` +
  `read_knowledge_article(article_id=<id>)` → existing process docs and prior
  discovery (Enterprise-only; skip on error).
- `search_projects(query=...)` → `get_task(task_id=<id>, include=["description",
  "chatter"])` for any existing tasks that frame the engagement.

Bring what you found *into* the session as things to confirm, not re-ask.

## 2. Question checklist

Work these groups; capture answers verbatim where wording matters. Mark any
answer you did not get as `UNKNOWN — follow up`.

**Current process**
- Walk the end-to-end process step by step. What triggers it? What ends it?
- Which steps are manual, spreadsheet-based, or outside any system today?

**Actors and roles**
- Who performs each step? Which Odoo groups/departments do they map to?
- Who approves? Who is accountable for the outcome?

**Volumes and frequency**
- How many transactions/records per day/week/month? Peak vs. average?
- Data volumes to migrate (records, documents, history depth)?

**Systems and integrations**
- What systems are in play (Odoo modules + external)? Source of truth for each?
- Required integrations, data flows, direction, and frequency (real-time/batch)?

**Pain points**
- Where does it break, slow down, or cause rework/errors today?
- What is the cost of the pain (time, money, risk, compliance)?

**Constraints and success**
- Odoo version/edition, deadlines, budget signals, compliance/regulatory needs.
- What does "done well" look like to the client? How will they measure it?

## 3. Current-process mapping

Map the as-is flow as a table so gaps are visible:

| Step | Actor | System/tool | Input | Output | Pain / risk |
|------|-------|-------------|-------|--------|-------------|

## 4. Gap analysis

For each desired capability:

| Need | Today (as-is) | Odoo standard? | Gap → config / custom / integration | Priority |
|------|---------------|----------------|-------------------------------------|----------|

Prefer standard Odoo; flag customizations explicitly with why standard does not
fit.

## Output template

```
# Discovery notes — <client> — <date>
Context reviewed: <chatter/articles/tasks pulled>

## Attendees & roles
## Current process (as-is)   <table>
## Volumes & data
## Systems & integrations
## Pain points (ranked)
## Gap analysis              <table>
## Constraints & success criteria
## Open questions / follow-ups
## Recommended next step
```

## Do-not

- Do not invent answers or infer volumes — record `UNKNOWN` and follow up.
- Distinguish what the client **stated** from what you **observed** or assumed.
- Do not jump to a solution mid-discovery; capture the problem fully first.
- Do not lose exact wording on requirements, compliance, and success metrics —
  paraphrase loses nuance that later scope depends on.
"""


def discovery_notes() -> list[str]:
    """Capture and structure client discovery for an Odoo engagement. Use when the user is running a discovery or requirements session, documenting a client's current process, actors, volumes, integrations, and pain points, or doing a gap analysis before scoping. Mines existing Odoo chatter and knowledge articles for context first."""
    return [_BODY]


@builtin_prompt("discovery_notes")
def make_discovery_notes_prompt(command_registry: Registry):
    """Register :func:`discovery_notes` as a built-in prompt.

    The skill returns static instructional content and never calls into the
    command registry, so ``command_registry`` is accepted (and ignored) purely
    to keep the prompt-factory interface uniform with registry-consuming prompts.

    :param command_registry: Command registry, unused by this prompt.
    :type command_registry: Registry
    :return: The :func:`discovery_notes` prompt callable, unchanged.
    """
    return discovery_notes
