# Python Venv

Venv at `/mnt/extra-addons/.venv`. Odoo binary shebang already points here.

```bash
# Install package in venv
source /mnt/extra-addons/.venv/bin/activate && python -m pip install <package>

# Install requirements in venv
source /mnt/extra-addons/.venv/bin/activate && python -m pip install -r /mnt/extra-addons/requirements.txt
```
