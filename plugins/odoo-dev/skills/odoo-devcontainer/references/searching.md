# Searching Base Code

Base code not in workspace -> use terminal. Same grep/find patterns, different roots:

| Codebase          | Root path                                     |
| ----------------- | --------------------------------------------- |
| Community core    | `/usr/lib/python3/dist-packages/odoo/`        |
| Community addons  | `/usr/lib/python3/dist-packages/odoo/addons/` |
| Enterprise addons | `/var/lib/odoo/addons/$ODOO_VERSION/`         |

```bash
# Text search (swap <ROOT> for any path above)
grep -r "pattern" <ROOT>

# Find files by name then search
find <ROOT> -name "*.py" | xargs grep "pattern"

# Find model definitions in a module
grep -r "class.*models.Model" <ROOT>/sale/

# List modules
ls <ROOT>
```
