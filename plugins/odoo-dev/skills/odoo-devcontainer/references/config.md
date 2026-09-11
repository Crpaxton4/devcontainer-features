# Config Ref

`/etc/odoo/odoo.conf` key settings:

## Devcontainer Defaults

| Key | Value | Notes |
|-----|-------|-------|
| `addons_path` | (auto) | Custom addons already included |
| `db_name` | `<project>` | This project's database on the shared postgres (`odoo-shared-db-1`); same name as the compose project / repo |
| `db_host`, `db_user` | (unset) | Not in the file: Odoo falls back to the container env `PGHOST=odoo-shared-db-1`, `PGUSER=<project>` (trust auth, no password). `psql` with no args opens the project DB |
| `admin_passwd` | `admin` | Master password |
| `shell_interface` | `ipython` | Used by `odoo shell` |
| `limit_time_real` | `99999` | No timeout for debugging |

## Common Additional Settings

| Key | Example | Notes |
|-----|---------|-------|
| `logfile` | `/var/log/odoo/odoo.log` | Write logs to file; omit for stdout |
| `log_level` | `info` | `debug` / `info` / `warn` / `error` / `critical` |
| `workers` | `0` | `0` = single threaded (required for debugging); ≥2 for production |
| `max_cron_threads` | `1` | Cron worker count (set `0` to disable cron) |
| `proxy_mode` | `True` | Enable when behind nginx/caddy (reads `X-Forwarded-*` headers) |
| `http_interface` | `0.0.0.0` | Interface to bind; `127.0.0.1` for localhost-only |
| `http_port` | `8069` | HTTP port |
| `longpolling_port` | `8072` | Long-polling/gevent port |
| `db_maxconn` | `64` | Max DB connections per worker |
| `list_db` | `False` | Hide DB list on login page (security) |
| `without_demo` | `all` | Skip demo data on install |

## CLI Override

Any conf key can be overridden per-run via `--key=value` CLI flag:

```bash
odoo --workers=0 --log-level=debug
odoo --http-port=8070
odoo --max-cron-threads=0   # disable cron
```

Note: CLI flags use `--log-level` (hyphens), conf file uses `log_level` (underscores).

## Version Detection

```bash
echo $ODOO_VERSION          # major version e.g. "19.0"
odoo --version              # full version string
```
