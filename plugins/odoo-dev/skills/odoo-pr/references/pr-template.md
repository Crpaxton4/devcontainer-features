# PR Body Template

Copy this into the body file verbatim. Every section is present in every PR, and a
section with nothing to say renders `- n/a` rather than losing its heading.

That is a rule, not tidiness. When headings come and go, the reader has to work out
whether a missing section means "there was nothing to say" or "the author skipped
it". A fixed-shape body is scannable in seconds and its gaps are explicit.

```markdown
## Odoo task [{task_id}: {task_name}]({task_url})

### Install
`{new_module_a,new_module_b}`

### Update
`{changed_module_c,changed_module_d}`

### Changes
**{module}** — {what changed and why, one short paragraph}
```

## Filling it

**Task link.** `{task_url}` is the Odoo task. Every PR links its task, because a PR
without one loses all of its context the moment the branch is deleted.

**The two module lists come from the classifier, never from memory:**

```bash
<plugin root>/scripts/module-classify.sh <worktree> <base> <head>
```

`module-classify.sh` belongs to the plugin rather than to this skill, so it sits at
`<plugin root>/scripts`, two directory levels above the `Base directory for this
skill:` path injected above the skill body. Write that absolute path out in full in
the Bash call: a Bash call inherits no environment and keeps no state from the call
before it, so there is no variable standing in for it.

It returns `{"install":[],"update":[],"removed":[]}`. Copy the `install` array into
**Install** and the `update` array into **Update**, exactly as given. Do not
reorder them, do not edit them by hand, and do not put the `removed` array in
either list. Deleting a module directory removes its code and does not uninstall
anything, so there is no command to write down;
`odoo-release/references/module-commands.md` explains why uninstalling is a
separate, deliberate, human-run job.

**Install and update are separate lists because Odoo treats them differently.** A
module the database has never seen needs `-i`; one that is already installed needs
`-u`. Each list is a single inline code block of comma-separated names with no
spaces, so that it pastes straight into a command:

```bash
odoo-bin -i new_module_a,new_module_b --stop-after-init
odoo-bin -u changed_module_c,changed_module_d --stop-after-init
```

**An empty list renders `- n/a`, and the heading stays.** "This PR installs
nothing" and "the author did not fill this in" have to look different, because the
person running the release reads these two lists to decide what to type.

**Changes** is one entry per module: the module name in bold, then one short
paragraph saying what changed and why, in the reviewer's terms. Describe the
behaviour that is different now. The diff already lists the files.

**Commits are deliberately absent.** The PR timeline lists them already, and a
hand-maintained copy goes stale within one push.

**The test numbers are deliberately absent too, and testing still gates.**
`odoo-dev:odoo-test-run` must report tests actually executed, tours actually run,
and `passed: true` before a PR is opened at all, and `gate.sh` blocks on
`no_tests`, `tours_skipped` and `tests_failed`. Those numbers live in
`30-test.json` and stay internal. A branch still cannot ship untested; the evidence
is simply not something the client needs to read.

## Published-surface rule

The PR body is client-visible, and so is Odoo chatter.

The body carries the task link, the module lists, and the per-module description.
Nothing else: no test output, no logs, no tracebacks, no machine paths, no
CodeRabbit text. Findings you acted on are described in your own words under
*Changes*.

Full logs stay at `log_file`, for you and for whoever debugs this next.

## Title convention

```
type(module): <task name> [task <id>]
```

Examples:

```
feat(sale_custom): add margin field to sale order line [task 30412]
fix(account_extend): correct tax computation rounding [task 30455]
```

`type` and module scope follow `odoo-devcontainer/references/commits.md`.

**The `[task <id>]` suffix load-bearing.** `odoo-dev:odoo-release` builds release manifest by extracting `[task NNN]` from merged PR titles. PR without it lands in manifest `unresolved` list, and its task never gets chatter note saying it shipped. Put id in title even when body links task.

One module per PR wherever change allows. When change genuinely spans modules, name primary one in scope and list rest under *Changes*.
