# Module install / update commands

What `release-pr.sh` puts in the PR body, and why it is shaped that way. This skill generates these commands. It never runs them.

## Classification

`release-manifest.sh` compares the two git trees, not the PR file lists. A module can be added by one PR and removed by another inside the same delta, and only the trees know the net result. A top-level directory is a module when it carries `__manifest__.py` on either side.

| Status | Test | Command |
|---|---|---|
| new | manifest in `from`, absent in `to` | `-i` |
| updated | manifest in both, files changed | `-u` |
| removed | manifest in `to`, absent in `from` | **none** |

## Command shapes

**odoo.sh.** `odoo-bin` takes no `-d`; the build's own database is implicit.

```bash
odoo-bin -i new_mod_a,new_mod_b --stop-after-init
odoo-bin -u existing_mod_c,existing_mod_d --stop-after-init
```

**On-premise.** Nothing is implicit, so the database is named. `release-pr.sh` requires `--database` for this hosting rather than emitting a `<db>` placeholder: a command the reader has to edit is a command the reader gets wrong.

```bash
sudo -u odoo odoo -c /etc/odoo/odoo.conf -d <db> -i new_mod_a --stop-after-init
sudo -u odoo odoo -c /etc/odoo/odoo.conf -d <db> -u existing_mod_c --stop-after-init
```

Always the explicit module list, never `-u all`. `-u all` re-runs every module's update and turns a short job into an outage of unpredictable length.

`--stop-after-init` because long-running SSH sessions are not guaranteed on odoo.sh; anything that must complete should not depend on the connection staying up.

## The manifest-bump rule

On odoo.sh a push deploys code. It does **not** update modules. The platform runs a module update — migration scripts, data file reload — only when the commit **increases the version in `__manifest__.py`**.

So a view change, a new field on an existing model, or a data-file edit merged without a version bump deploys code that never takes effect. It looks like the release did nothing.

This is why the PR body carries a version table for updated modules with an `Auto-updates on merge` column. `no` means the `-u` line above it is not optional, it is the only thing that will apply the change.

The same rule governs odoo.sh automatic backups: an `Update`-type backup is taken only when the merged commit bumps a manifest version or changes `requirements.txt`.

## Removed modules are out of scope

Deleting a module's directory removes the code. It does **not** uninstall the module. The records stay in `ir_module_module` as installed, pointing at code that is gone.

Uninstalling drops the module's tables and data. That is destructive, irreversible without a restore, and frequently has ordering constraints against other modules. It is a separate, deliberate, human-run job.

`release-pr.sh` therefore lists removed modules and generates no command for them. Do not add one, and do not run one.

## Config does not travel

A merge carries source code only. Configuration made in a staging database does not move with it. To promote configuration, express it as XML data files **and** bump the module version so the update runs, or re-enter it by hand in the target. Either way, say which in the PR.
