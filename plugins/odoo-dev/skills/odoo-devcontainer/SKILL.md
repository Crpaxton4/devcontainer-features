---
name: odoo-devcontainer
description: "Working reference for this Odoo devcontainer: paths, odoo-bin, odoo.conf, the ORM, security, views and XPath, controllers, QWeb, OWL assets, tests and tours, commit conventions. Use whenever Odoo module code is written, read, run, or debugged here."
---

# Odoo Dev Env Guide

Odoo customization dev env. Everything preconfigured. No setup.

Load refs as needed. Do NOT load all at once.

Live values, injected every time this skill is rendered — at invocation and at every
agent spawn that preloads it. Trust them; do not re-derive them.

- **Odoo series**: !`echo "${ODOO_VERSION:-not set}"`
- **Running project stacks**: !`command -v docker >/dev/null 2>&1 && (timeout 1 docker ps --format '{{.Names}}' 2>/dev/null | grep -e '-odoo-1$' | paste -sd ' ' - | grep . || echo "docker is here, no project stack up") || echo "no docker on PATH"`

Read the two together, because they say where you are and each is meaningless
alone. A series with no docker is the normal, healthy shape of a shell inside the
container: there is no stack to bring up from in there, and the second line is a
fact about the container, not a fault. Docker present and no series is a host
shell, where the second line is the real answer and the series has to come from
the project rather than from the environment.

## When to use

Anything is being written, read, run, or debugged inside an Odoo devcontainer — a model, a view, a controller, an OWL component, a test, a tour, a report; a path, an odoo-bin flag, a field kwarg, a decorator, an XPath, or an asset bundle has to be exact rather than remembered; the container was just rebuilt or the machine is new; or a commit is about to be made and the manifest version has to be bumped first.

## Index

| Topic               | Load When                                                     | Ref                                                     |
| ------------------- | ------------------------------------------------------------- | ------------------------------------------------------- |
| Key Paths           | Need filesystem path                                          | [paths.md](./references/paths.md)                       |
| CLI                 | Run odoo commands, install/upgrade/test/scaffold/shell/db ops | [cli.md](./references/cli.md)                           |
| Searching Base Code | Find code in community/enterprise source                      | [searching.md](./references/searching.md)               |
| Python Venv         | pip install, package mgmt                                     | [venv.md](./references/venv.md)                         |
| Config              | odoo.conf settings, workers, logfile, proxy_mode              | [config.md](./references/config.md)                     |
| Module Structure    | Create/read module layout, directory conventions              | [module-structure.md](./references/module-structure.md) |
| Logging             | Log levels, filtering, log files                              | [logging.md](./references/logging.md)                   |
| Debugging           | VSCode launch configs, attach, pdb/ipdb, --workers=0          | [debugging.md](./references/debugging.md)               |
| Odoo.sh             | Connecting to odoo.sh to troubleshoot                         | [odoo-sh.md](./references/odoo-sh.md)                   |
| Commits             | Writing commit messages, versioning                           | [commits.md](./references/commits.md)                   |
| ORM Fields          | Define fields, compute/related/store, field kwargs            | [orm/fields.md](./references/orm/fields.md)             |
| ORM Decorators      | @api.depends, @api.onchange, @api.constrains, create overrides | [orm/decorators.md](./references/orm/decorators.md)    |
| ORM Recordsets      | search, create, write, unlink, filtered, mapped, sorted       | [orm/recordsets.md](./references/orm/recordsets.md)     |
| ORM Environment     | env.user/ref/company, sudo(), with_context/user/company       | [orm/environment.md](./references/orm/environment.md)   |
| ORM Inheritance     | _inherit (extend), _name+_inherit (copy), _inherits (delegation) | [orm/inheritance.md](./references/orm/inheritance.md) |
| Security            | ir.model.access.csv, record rules, group definitions          | [security.md](./references/security.md)                 |
| Manifest Keys       | Module dependencies, assets bundles, license, lifecycle hooks | [manifest.md](./references/manifest.md)                 |
| Views               | Write/inherit form/list/kanban/search/graph/pivot XML, XPath  | [views.md](./references/views.md)                       |
| Actions             | Window/server actions, bind to buttons or menus               | [actions.md](./references/actions.md)                   |
| Controllers         | HTTP routes, JSON endpoints, auth modes                       | [controllers.md](./references/controllers.md)           |
| Data Files          | XML/CSV data loading, noupdate, ref(), eval=""                | [data-files.md](./references/data-files.md)             |
| Mixins              | Chatter, activities, aliases, ratings, website publishing     | [mixins.md](./references/mixins.md)                     |
| Testing             | Write/run tests, base classes, @tagged, CLI flags, running TOURS | [testing.md](./references/testing.md)                |
| Post-Rebuild Check  | After a container rebuild or on a new machine — verify the env before trusting the delivery skills | [post-rebuild-checklist.md](./references/post-rebuild-checklist.md) |
| Reports             | QWeb PDF reports, paper_format, programmatic render           | [reports.md](./references/reports.md)                   |
| Performance         | Prefetch, N+1 fixes, read_group, sql_constraints, profiling   | [performance.md](./references/performance.md)           |
| External API        | Integrate from outside Odoo (JSON-2 + XML-RPC)               | [external-api.md](./references/external-api.md)         |
| API Client: Key     | Odoo calls OUT to a service with static token/key auth       | [api-client-key.md](./references/api-client-key.md)     |
| API Client: OAuth   | Odoo calls OUT with OAuth2 refresh-token auth                | [api-client-oauth.md](./references/api-client-oauth.md) |
| OWL Components      | Component anatomy, setup(), OWL hooks, t-* directives         | [frontend/owl.md](./references/frontend/owl.md)         |
| JS Assets           | Bundles, adding files, directives, lazy loading               | [frontend/assets.md](./references/frontend/assets.md)   |
| JS Registries       | registry.category().add(), all built-in categories            | [frontend/registries.md](./references/frontend/registries.md) |
| JS Services         | useService(), defining services, built-in services            | [frontend/services.md](./references/frontend/services.md) |
| JS Hooks            | useBus, usePager, usePosition, useSpellCheck, useAssets       | [frontend/hooks.md](./references/frontend/hooks.md)     |
| JS Patching         | patch() for components and classes, super, gotchas            | [frontend/patching.md](./references/frontend/patching.md) |

## Scripts

This skill's scripts (`DEVCONTAINER_SCRIPTS`) live at `<base directory>/scripts`,
where `<base directory>` is the absolute path on the `Base directory for this
skill:` line injected above this body. The name is a label for that directory, not a
shell variable to set and reuse: every Bash call has to spell the absolute path out
in full, because a Bash call inherits no environment and keeps no state from the
call before it.

| Script                      | Run When                                  |
| --------------------------- | ----------------------------------------- |
| `bump_manifest_version.py`  | Before committing any module change       |
| `list_modules.sh`           | Need comma-separated list of all modules  |

## Rules

- NEVER suggest `-c /etc/odoo/odoo.conf` or `-d odoo` -> omit both flags, the config
  is auto-loaded and naming it again only invites a stale copy
- NEVER search base code with workspace search tools -> use terminal grep, because
  community and enterprise source live outside `/mnt/extra-addons` and the workspace
  index does not reach them, so the search returns nothing and reads as an answer
- Custom addon code in `/mnt/extra-addons` -> use workspace tools
- Single DB instance: `odoo`. No multi-DB awareness needed
- Venv already active in odoo binary. Manual activation only for pip ops
- Enterprise addons at `/var/lib/odoo/addons/$ODOO_VERSION` -> auto-cloned, already in `addons_path`
- Always run `bump_manifest_version.py` before committing module changes
