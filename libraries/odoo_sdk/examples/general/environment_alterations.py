"""Phase D5 reference example — environment alterations.

Demonstrates ``with_user``, ``with_company``, ``action_archive``, and
``action_unarchive`` on ``OdooRecordset``.

Run from the repository root:

    uv run python examples/general/environment_alterations.py --allow-live-production

Safety: creates one ``res.partner`` record, archives it, unarchives it, then
deletes it.  No other records are touched.
"""

import sys

from odoo_sdk import OdooClient


def run(client: OdooClient) -> None:
    partners = client["res.partner"]

    # ------------------------------------------------------------------ D5-4
    # with_company on OdooRecordset — inject allowed_company_ids into context
    # ------------------------------------------------------------------ D5-4
    companies = client["res.company"].search([], limit=1)
    if companies.ids:
        company_id = companies.ids[0]
        rs_co = partners.with_company(company_id)
        print(
            f"recordset with_company({company_id!r}) allowed_company_ids="
            f"{rs_co.context.get('allowed_company_ids')!r}"
        )

    # ------------------------------------------------------------------ D5-5
    # action_archive / action_unarchive
    # ------------------------------------------------------------------ D5-5
    demo = partners.create(
        {"name": "SDK D5 Demo Partner (archive test)", "active": True}
    )
    print(f"created partner id={demo.id!r}")

    archive_result = demo.action_archive()
    print(f"action_archive() => {archive_result!r}")

    # Verify archived: search with active_test=False to include archived records
    inactive_rs = partners.with_context({"active_test": False}).search(
        [("id", "=", demo.id)]
    )
    archived_meta = inactive_rs.get_metadata()
    print(
        f"partner after archive: id={demo.id!r}, found in inactive search: {bool(inactive_rs.ids)}"
    )

    unarchive_result = demo.action_unarchive()
    print(f"action_unarchive() => {unarchive_result!r}")

    # Cleanup
    demo.unlink()
    print("cleanup: unlinked demo partner")


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
