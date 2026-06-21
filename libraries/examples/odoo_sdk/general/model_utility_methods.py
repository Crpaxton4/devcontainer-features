"""Phase D2 reference example — model utility methods on OdooRecordset.

Demonstrates ``name_create``, ``name_search``, ``default_get``, ``copy``, and
``get_metadata`` on ``res.partner``.

This script requires a live Odoo connection configured through the standard
environment variables (``ODOO_URL``, ``ODOO_DB``, ``ODOO_USERNAME``,
``ODOO_PASSWORD`` or ``ODOO_API_KEY``).

Run from the repository root:

    uv run python examples/general/model_utility_methods.py --allow-live-production

Safety: creates two ``res.partner`` records and immediately unlinks them.
"""

import sys

from odoo_sdk import OdooClient


def run(client: OdooClient) -> None:
    partners = client["res.partner"]

    # ------------------------------------------------------------------ D2-1
    # name_create: create a record from a display name and get a recordset
    # ------------------------------------------------------------------ D2-1
    new_partner = partners.name_create("SDK D2 Demo Partner")
    print(f"name_create => id={new_partner.id!r}")

    # ------------------------------------------------------------------ D2-2
    # name_search: find records matching a display name fragment
    # ------------------------------------------------------------------ D2-2
    hits = partners.name_search("SDK D2", limit=5)
    print(f"name_search => {hits!r}")

    # ------------------------------------------------------------------ D2-3
    # default_get: retrieve server-side defaults for selected fields
    # ------------------------------------------------------------------ D2-3
    defaults = partners.default_get(["name", "lang", "customer_rank"])
    print(f"default_get => {defaults!r}")

    # ------------------------------------------------------------------ D2-4
    # copy: duplicate the record and return the copy as a new singleton
    # ------------------------------------------------------------------ D2-4
    copied = new_partner.copy({"name": "SDK D2 Demo Partner (copy)"})
    print(f"copy => id={copied.id!r}")

    # ------------------------------------------------------------------ D2-5
    # get_metadata: retrieve audit fields for both records
    # ------------------------------------------------------------------ D2-5
    both = new_partner | copied
    metadata = both.get_metadata()
    for entry in metadata:
        print(
            f"get_metadata id={entry['id']}"
            f"  create_uid={entry.get('create_uid')!r}"
            f"  write_uid={entry.get('write_uid')!r}"
        )

    # Cleanup — remove the two demo partners created during this run
    both.unlink()
    print("cleanup: unlinked demo partners")


def main() -> None:
    if "--allow-live-production" not in sys.argv:
        print(
            "Pass --allow-live-production to run this example against a live Odoo instance."
        )
        return

    client = OdooClient()
    if not client.authenticated:
        print(f"Authentication failed (uid={client.uid!r}); check connection settings.")
        return

    run(client)


if __name__ == "__main__":
    main()
