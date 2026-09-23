# PR Body Template

Copy this into the body file verbatim. Every section is present in every PR, and a
section with nothing to say renders `- n/a` rather than losing its heading.

That is a rule, not tidiness. When headings come and go, the reader has to work out
whether a missing section means "there was nothing to say" or "the author skipped
it". A fixed-shape body is scannable in seconds and its gaps are explicit.

````markdown
## Odoo task [{task_id}: {task_name}]({task_url})

### Install
`{new_module_a,new_module_b}`

### Update
`{changed_module_c,changed_module_d}`

### Deploy
{one sentence: new modules only / update only / both / nothing to run}

```bash
odoo-bin -i {install_list} -u {update_list} --stop-after-init
```

### EXTREMELY IMPORTANT
- [x] **Manifest version** — `{module}` bumped `{version_from}` → `{version_to}`, once for this PR.
- [ ] {precondition a human has to confirm before this merges}

### Changes
**{module}** — {what changed and why, one short paragraph}
````

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

It returns
`{"install":[],"update":[],"removed":[],"details":[{"name","status","version_from","version_to","bumped"}]}`.
Every generated section of the body — both lists, the command, and the
manifest-version line — is filled from that one payload, so nothing here needs
collecting a second time.

Copy the `install` array into
**Install** and the `update` array into **Update**, exactly as given. Do not
reorder them, do not edit them by hand, and do not put the `removed` array in
either list. Deleting a module directory removes its code and does not uninstall
anything, so there is no command to write down;
`odoo-release/references/module-commands.md` explains why uninstalling is a
separate, deliberate, human-run job.

**Install and update are separate lists because Odoo treats them differently.** A
module the database has never seen needs `-i`; one that is already installed needs
`-u`. Each list is a single inline code block of comma-separated names with no
spaces, so that it pastes straight into the **Deploy** command underneath it.

**An empty list renders `- n/a`, and the heading stays.** "This PR installs
nothing" and "the author did not fill this in" have to look different, because the
person running the release reads these two lists to decide what to type.

**Deploy carries the command itself, and that is the point of the section.** Two
lists of module names are not a command, and the release manager should never have
to assemble one. One `odoo-bin` invocation, operands lifted verbatim from the same
two arrays:

```bash
odoo-bin -i new_module_a,new_module_b -u changed_module_c,changed_module_d --stop-after-init
```

**Emit only the flags that have operands.** Nothing to install renders
`odoo-bin -u changed_module_c,changed_module_d --stop-after-init`; nothing to
update renders `odoo-bin -i new_module_a,new_module_b --stop-after-init`. A flag
with no operand after it is a syntax error somebody has to debug at the worst
possible moment. The one sentence above the block says which of the three cases
this PR is, so the reader knows before parsing flags.

**A PR that only removes modules renders no code block at all.** There are no
operands, so **Deploy** carries the explanation instead: deleting the code does not
uninstall anything, and `odoo-release/references/module-commands.md` says why
uninstalling stays a separate, deliberate, human-run job.

**EXTREMELY IMPORTANT leads with the manifest version, because that is what decides
whether the deploy does anything at all.** On odoo.sh a module is only updated when
the commit bumps its `__manifest__.py`; an unbumped module deploys and then sits
inert until a human runs `-u` by hand. `details[]` already answers this per module
— `version_from`, `version_to`, `bumped` — so the line is written from the same
JSON as the lists, never from memory:

```markdown
- [x] **Manifest version** — `sale_custom` bumped `17.0.1.0.1` → `17.0.1.0.2`, once for this PR.
- [x] **Manifest version** — `new_module_a` is new at `17.0.1.0.0`.
```

`bumped: false` on a module in the `update` list is not a box to leave unchecked
and ship. It is the *Preconditions* gate in step 1 of this skill failing, and the
fix is the manifest, not the wording. "Once for this PR" is the other half of the
rule: one bump per pull request, not one per commit, or the version number stops
meaning anything.

This is the same fact `odoo-dev:odoo-release` publishes as the *Auto-updates on
merge* column of its module table (`odoo-release/references/release-table.md`),
computed from the same classifier. A PR and the release that ships it state it once
each, in their own shape, and cannot disagree.

**Unchecked boxes are for preconditions a human has to confirm**, never for work
you skipped — an external system that has to land first, a temporary behaviour
scheduled for removal, a setting somebody has to flip before the deploy runs. In
practice those are the lines a reviewer actually acts on, so each one names the
thing and who confirms it. A PR with no such precondition carries only the manifest
line.

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

The body carries the task link, the module lists, the deploy command, the
manifest-version checklist, and the per-module description.
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
