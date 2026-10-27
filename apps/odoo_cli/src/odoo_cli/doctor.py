"""``odoo-dev doctor``: report on, or migrate, the ``ODOO_DEV_HOME`` state root.

State maintenance runs before any config exists and carries no business logic,
so this module calls the data layer directly and never loads the logic layer.
"""

import argparse
import json
import sys

from odoo_dev_data.doctor import doctor, render
from odoo_dev_data.migrate import migrate, render_migrate


def run_doctor(args: argparse.Namespace) -> int:
    """Print the state-root report (or the migration report); exit 0 when ok."""
    if args.dry_run and not args.migrate:
        print("Error: --dry-run needs --migrate", file=sys.stderr)
        return 2
    if args.migrate:
        result = migrate(dry_run=args.dry_run)
        text = render_migrate(result)
    else:
        result = doctor()
        text = render(result)
    print(json.dumps(result.to_dict(), indent=2) if args.as_json else text)
    return 0 if result.ok else 1
