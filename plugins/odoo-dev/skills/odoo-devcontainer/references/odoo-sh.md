# Odoo.sh

ssh keys are already configured for passwordless access

```bash
ssh -o StrictHostKeyChecking=accept-new <REMOTE_INSTANCE>@<REMOTE_DB_NAME>-<REMOTE_INSTANCE>.dev.odoo.com
```

**CRITICAL**: You must request <REMOTE_INSTANCE> and <REMOTE_DB_NAME> if not explicitly provided.
**CRITICAL**: `-o StrictHostKeyChecking=accept-new` is required to avoid "Host key verification failed" errors.

Odoo version on odoo.sh always matches local devcontainer version (set by `ODOO_VERSION` env var).

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
