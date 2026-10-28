---
name: odoo-devcontainer
description: "Working reference for this Odoo devcontainer: paths, odoo-bin, odoo.conf, the ORM, security, views and XPath, controllers, QWeb, OWL assets, tests and tours, commit conventions. Use whenever Odoo module code is written, read, run, or debugged here."
allowed-tools: Bash(echo "${ODOO_VERSION:-not set}")
---

# Odoo Dev Env Guide

The Odoo customization dev environment. Everything is preconfigured. Load references as
needed, never all at once.

- **Odoo series of this shell**: !`echo "${ODOO_VERSION:-not set}"` — `not set` means a
  host shell; take the series from the project.

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
| Language Server     | Odoo diagnostics/definitions missing or wrong, odools.toml, turning odoo-ls off | [language-server.md](./references/language-server.md) |
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

Write every path in full: `<base directory>/scripts/<script>`.

| Script and arguments | Prints | Exit |
| --- | --- | --- |
| `bump_manifest_version.py [--series NN.0] <module dir>` | the new manifest version. `--series` is for a port: it moves the series prefix to NN.0, keeps A.B.C, then bumps | 0 |
| `list_modules.sh` | a comma-separated list of all modules | 0 |

## Rules

- Never pass `-c /etc/odoo/odoo.conf` or `-d odoo`. The config is auto-loaded.
- Never search base code with workspace search tools. Use terminal grep: community and
  enterprise source live outside `/mnt/extra-addons`.
- Custom addon code is in `/mnt/extra-addons`. Use workspace tools there.
- One database instance: `odoo`. No multi-DB awareness is needed.
- The venv is active in the odoo binary. Activate it by hand only for pip operations.
- Enterprise addons are at `/var/lib/odoo/addons/$ODOO_VERSION`, already in `addons_path`.
- Run `bump_manifest_version.py` before you commit a module change. A `PreToolUse` hook
  denies a commit whose manifest version did not move.
