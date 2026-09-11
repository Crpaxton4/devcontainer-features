# PR Guidelines (Odoo)

Start from `principles/references/pr-etiquette.md` — general rules plus reasoning:

- **Small.** Under 500 changed lines best, under 1000 OK. Beyond that review quality collapse; split into stacked PRs, each independently reviewable, each leave codebase working.
- **Plain conventional titles.** Scannable in notification list, no flourish.
- **Every PR links its task.** No orphan PRs.
- **Minimal, structured descriptions.** Bullets, not paragraphs.
- **Draft by default.** Mark ready when review actually wanted.
- **Tidy history before opening.** Rebase fixups away; aim one succinct commit, or several that tell sequential story.

Below: what Odoo work add on top.

## Base branch comes from the repo map, never from GitHub

GitHub default branch routinely *not* where task PR belongs. Seen in a live client repo: GitHub default `Odoov18` while PRs target `staging`. Take base from `odoo-dev:odoo-repo-map` `default_branch` (validated against project `branch_flow`) and pass explicit. `gh pr create` without `--base` silently target GitHub default.

## One module per PR where possible

Odoo module = unit of install, test, release. PR confined to one module can test on throwaway DB with `-i <module>`, review against one manifest, name with one commit scope. PR spanning four modules can do none of those.

When change genuinely span modules — shared mixin, renamed field its dependants follow — keep in one PR (splitting leave tree broken) and say so in body.

## Manifest version bump

Bump ported/changed module manifest version. On odoo.sh not bookkeeping: push update code, but platform only **updates the module** — run its migration scripts, reload its data files — when commit bump version in `__manifest__.py`. View change merged without bump deploy code that never take effect.

`odoo-devcontainer/references/commits.md` point at bump script.

## Coding guidelines conformance

Odoo [coding
guidelines](https://www.odoo.com/documentation/18.0/contributing/development/coding_guidelines.html)
are reviewer baseline. Most common:

- File/directory layout: `models/<main_model>.py`, `views/<model>_views.xml`,
  `security/ir.model.access.csv`, `wizard/`, `report/`, `data/`.
- XML id conventions: `<model_name>_view_<type>`, `<model_name>_action`,
  `<module>_group_<name>`, `<model_name>_rule_<group>`. Inherited view reuse
  original xml id, add `.inherit.<details>` to its `name`.
- `<record>`: `id` before `model`; inside `<field>`, `name` first.
- Imports in three alphabetical groups: stdlib/third-party, `odoo`, `odoo.addons.*`.
- Translations: pass arguments **to** `_()`, never format before or outside it —
  `_('Record %s cannot be modified!', record)`, not `_('...' % record)`.
- Never call `cr.commit()` unless you opened cursor yourself; if you must, comment why.
- Never restyle untouched code in stable branch. Diff stay minimal.

Domain review beyond style — ORM anti-patterns, `sudo()` scope, N+1, access rights, upgrade safety — belong to `odoo-dev:odoo-code-review` skill.

## Draft, and self-assigned

Open as draft. Assign yourself (`--assign-me`): unassigned PR has no owner in review queue, and "who is this waiting on" is question queue exist to answer.

Mark ready only when the CodeRabbit round has settled and the final commit has passed a full test run. That run still gates the PR; its numbers stay internal and never appear in the body.