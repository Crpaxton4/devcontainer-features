# Odoo.sh

ssh keys are already configured for passwordless access

```bash
ssh -o StrictHostKeyChecking=accept-new <REMOTE_INSTANCE>@<REMOTE_DB_NAME>-<REMOTE_INSTANCE>.dev.odoo.com
```

**CRITICAL**: You must request <REMOTE_INSTANCE> and <REMOTE_DB_NAME> if not explicitly provided.
**CRITICAL**: `-o StrictHostKeyChecking=accept-new` is required to avoid "Host key verification failed" errors.

`ODOO_VERSION` is the container's core series. A project can run another series (side core, e.g. `/var/lib/odoo/src/odoo18-bin`).
Get the Odoo.sh series from the repo map (`odoo-dev:odoo-repo-map` skill, `repo-map.sh`), or on the remote: `SELECT latest_version FROM ir_module_module WHERE name='base'`.

## Commands

### `odoo-bin`

On remote, `odoo-bin` configured for passwordless access

**NOTE**: `odoo` command is local only. You must use `odoo-bin` over ssh.

```bash
# Common Commands
ssh -o StrictHostKeyChecking=accept-new <REMOTE_INSTANCE>@<REMOTE_DB_NAME>-<REMOTE_INSTANCE>.dev.odoo.com odoo-bin shell -c "<python_code>"
```

### `psql`

On remote, configured for passwordless access.

```bash
ssh -o StrictHostKeyChecking=accept-new <REMOTE_INSTANCE>@<REMOTE_DB_NAME>-<REMOTE_INSTANCE>.dev.odoo.com psql -c "<query>"
```

## Read-only data check

One ssh call. The transaction is read-only and always rolled back. SELECT only.

```bash
ssh -o StrictHostKeyChecking=accept-new <REMOTE_INSTANCE>@<REMOTE_DB_NAME>-<REMOTE_INSTANCE>.dev.odoo.com psql -X -v ON_ERROR_STOP=1 <<'SQL'
BEGIN TRANSACTION READ ONLY;
SELECT latest_version FROM ir_module_module WHERE name='base';
ROLLBACK;
SQL
```

**NOTE**: Temp tables and `CREATE TABLE` are refused in a read-only transaction: `ERROR:  cannot execute CREATE TABLE in a read-only transaction`. Do not dry-run a hook that creates tables this way.

Alternative: pipe Python into `odoo-bin shell` and end with `env.cr.rollback()`.
