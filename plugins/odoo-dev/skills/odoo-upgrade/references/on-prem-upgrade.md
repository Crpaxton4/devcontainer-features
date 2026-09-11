# On-Premise Upgrade (Enterprise)

upgrade.odoo.com serve **Enterprise** DB only. Community on-premise use OCA OpenUpgrade — see Variant note in [sop.md](./sop.md).

All here `[MANUAL]`-gated in SOP. This file = technical detail behind those steps: what client do, what it not do, what must be done by hand around it.

Source: [Upgrade](https://www.odoo.com/documentation/19.0/administration/upgrade.html),
[Upgrade a customized database](https://www.odoo.com/documentation/18.0/developer/howtos/upgrade_custom_db.html).
18.0 and 19.0 upgrade pages byte-identical except edit link.

## The client

```bash
python3 <(curl -s https://upgrade.odoo.com/upgrade) test -d <db> -t <target>
python3 <(curl -s https://upgrade.odoo.com/upgrade) production -d <db> -t <target>
```

Subcommands: `test`, `production`, `restore`, `status`, `log`, `wipe`.

Options on `test`/`production`:

| Flag | Meaning |
|---|---|
| `-d, --dbname` **XOR** `-i, --dump` | Live DB to dump, or dump file. Mutually exclusive, one required |
| `-t, --target` | Target version. `18`, `v18`, `18.0` all normalize to `18.0` |
| `-c, --contract` | Subscription code. **Mandatory with `-i`** |
| `-r, --restore-name` | Name for restored upgraded DB. Do not pre-create it |
| `-x, --no-restore` | Download only; skip restore and filestore merge |
| `-j, --core-count` | Parallel jobs for dump/restore (default 4) |
| `-s, --ssh-key` | Transfer key (default `/tmp/<uid>_upgrade_ssh_key`, auto-generated) |

**No `--db-host`, `--db-user`, `--db-password`, `--filestore`, or `--timeout`. `-c` is `--contract`, not config file.** DB connection come from libpq env (`PGHOST`/`PGPORT`/`PGUSER`/`PGPASSWORD`) or `~/.pgpass` — nothing else. Do not run as root.

`scripts/upgrade_service.sh` wrap both shapes (over SSH to customer server, or locally against fetched dump) and add guards below.

## Authentication is the subscription code

No login. Client run

```sql
SELECT value FROM ir_config_parameter WHERE key = 'database.enterprise_code'
```

against `-d <db>`. If DB have one it **win**, even over disagreeing `--contract` (warns, use DB value). With `-i <dump>` no DB to read — why `--contract` mandatory.

Unregistered DB ⇒ `Unable to get the subscription code of your database.
Your database must be registered to be eligible for an upgrade.`

## The token is the identifier

Client print:

```
Creating new upgrade request
Assigned host's server uri '<host>'
The secret token is '<token>'
```

`request_id` from API validated, never printed. **Token** is what `status`, `log`, `restore`, `wipe` take, and what Odoo support need. Record it moment it appear; `upgrade_service.sh` extract into its JSON on success *and* failure — failed upgrade with lost token cannot be asked about.

Interrupted run resumable: token persisted at `/tmp/odoo-upgrade-<aim>-<token_name>-<target>`, re-run prompt `This upgrade request seems to have been interrupted. Do you want to resume it?`

## What comes back, and what does not

Uploaded: `pg_dump --no-owner --format d --jobs <n> --file origin.dump <db>`, rsync'd over SSH.

Downloaded into working dir: upgraded dump, `filestore/` folder, `upgrade-report.html`, `upgrade.log`.

Then, unless `-x`:

```bash
createdb <upgraded_db>
pg_restore --no-owner --exit-on-error --format d <dump> --dbname <upgraded_db> --jobs <n>
```

Default restored names: test ⇒ `<db>_test_<target>_<YYYY_MM_DD_HH_MM>`, production ⇒ `<db>_backup_<YYYY_MM_DD_HH_MM>`.

### Filestore — three traps

> "For storage reasons, the database's copy is submitted **without a
> filestore**… the upgraded database does not contain the production
> filestore."

Returned `filestore/` hold files extracted from records into attachments plus new standard files for target version. Must be **merged with** production one, not substituted for it.

1. Client filestore path **hardcoded** to `~/.local/share/Odoo/filestore`. Custom `data_dir` ignored, you get `The original filestore of '<db>' has not been found in <path>`.
2. Its `copytree` have no `dirs_exist_ok`: fail if destination filestore dir already exist.
3. **`restore` subcommand never merge filestore at all.** Its `input_source` is `None`, so merge branch unreachable, despite `-d` help text. After `restore`, merge by hand:
   ```bash
   rsync -a filestore/ ~/.local/share/Odoo/filestore/<upgraded_db>/
   ```

## Neutralize every test restore, before opening it

```bash
odoo-bin --addons-path <PATH,...> neutralize -d <restored_db>
odoo-bin --addons-path <PATH,...> neutralize -d <restored_db> --stdout   # review the SQL first
```

Also available as `odoo-bin db load -n` and `odoo-bin db duplicate -n`.

It disables scheduled actions, outgoing mail, bank synchronization, payment
providers, delivery methods, IAP tokens, and search-engine indexing, and shows a
red banner. Platform-delivered test databases arrive neutralized; **a restore
you performed yourself does not.** Mail leaving a test restore reaches real
customers, and that cannot be undone.

`upgrade_service.sh` neutralizes a local test restore before it returns, and
refuses `--update-modules` on a test restore that is not neutralized.

## After the restore

```bash
odoo-bin -d <upgraded_db> -u <comma,separated,custom,modules> --stop-after-init
```

The documentation says update **your custom modules**, naming them. It does not
recommend `-u all`, and `-u all` on a freshly upgraded database re-runs every
core module's update for no benefit.

Verify:

```bash
grep -iE "ERROR|CRITICAL" upgrade.log | head -50
psql -Atc "select name, latest_version, state from ir_module_module
           where state not in ('installed','uninstalled','uninstallable')" <upgraded_db>
```

Read `upgrade-report.html` — it is also emailed and posted to Discuss for the
Administration/Settings group.

## Requirements and limits

| | |
|---|---|
| Dump formats for `-i` | `.sql`, `.dump`, `.zip` (must contain `dump.sql`), `.sql.gz`, or a directory with `toc.dat` |
| Binaries needed | `ssh-keygen`, `rsync`, `psql`, `createdb`, `pg_restore`, `pg_dump` |
| Network | TCP 443 **plus an arbitrary TCP port in 32768–60999** for the rsync-over-SSH data channel — restrictive firewalls need an exception |
| PostgreSQL | Odoo 18.0 needs 12.0+; **19.0 needs 13.0+**. Checked server-side: too old ⇒ the dump downloads but is not restored |
| Size | No documented limit. The only client timeout is a 5-minute transfer-connection message; re-run and resume |
| Test→production | Odoo states the production upgrade should follow within **3 days** of the test upgrade |

## Production

```bash
python3 <(curl -s https://upgrade.odoo.com/upgrade) production -d <db> -t <target>
```

- **Stop using the database first.** Any modification made after the upload is
  lost; the docs recommend not using it during the process.
- The database is unavailable for the duration, and once complete it is
  **impossible to revert** to the previous version.
- The copy is submitted without a filestore here too — merge before deploying.
- Only the person who submitted the request can download it.

`upgrade_service.sh` requires `--yes-production` and exits **7** without it.
sop.md keeps the step `[MANUAL]`; the flag is the mechanical half of that, not a
replacement for the human one.

## Rollback

The service does not roll back an on-premise database. Rollback is your
pre-upgrade backup:

1. Stop Odoo.
2. `dropdb <db>` (or rename it aside) and `pg_restore` the pre-upgrade dump.
3. Restore the pre-upgrade filestore alongside it.
4. **Revert the code** to the pre-upgrade commit — a restored old database under
   new code re-runs migration scripts against already-migrated data.
5. Start Odoo; verify no module is left in `to upgrade`/`to install`.

Take and **verify** the backup (`pg_restore -l` over it) before the production
run, not after. An unverified backup is not a backup.

## Upgrade scripts

`migrations/<version>/{pre,post,end}-*.py`, or `upgrades/<version>/` from 13.0
(preferred). `<version>` is the full manifest version including the Odoo major
(`17.0.2.0`). Scripts run only when the module is actually updated, and only when
the directory version is greater than the installed version and not greater than
the version being updated to.

`odoo/upgrade-util` helpers are loaded with `--upgrade-path`, **not**
`server_wide_modules`:

```bash
./odoo-bin --upgrade-path=/path/to/upgrade-util/src,/path/to/your/scripts [...]
```

```python
from odoo.upgrade import util

def migrate(cr, version):
    util.rename_field(cr, "sale.order", "x_studio_ref", "customer_ref")
```

`--pre-upgrade-scripts` (16.0+) runs before `base` loads — the right place for
module rename/merge/remove. Helper signatures and the wider list are in
[migrations.md](./migrations.md).