# Logging

```bash
# Set log level
odoo --log-level=debug

# Log specific module (LOGGER:LEVEL format, colon required)
odoo --log-handler=odoo.addons.module_name:DEBUG

# Shortcuts
odoo --log-sql                   # SQL queries (= odoo.sql_db:DEBUG)
odoo --log-web                   # HTTP requests (= odoo.http:DEBUG)

# Combine handlers
odoo --log-handler=odoo.addons.sale:DEBUG --log-handler=werkzeug:CRITICAL

# Log levels: critical, error, warning, info, debug
# Pseudo-levels: debug_sql, debug_rpc, debug_rpc_answer

# Log to file
odoo --logfile=/tmp/odoo.log
```

Odoo logs to stderr by default in container. Filter w/ grep:

```bash
odoo 2>&1 | grep -i "error\|warning\|traceback"
```
