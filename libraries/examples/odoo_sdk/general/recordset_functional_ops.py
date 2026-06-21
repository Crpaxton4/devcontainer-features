"""Phase D3 reference example — recordset functional operations.

Demonstrates ``filtered``, ``mapped``, ``sorted``, ``grouped``, and
``filtered_domain`` on a ``res.partner`` recordset fetched from a live Odoo
instance.

Run from the repository root:

    uv run python examples/general/recordset_functional_ops.py --allow-live-production

Safety: read-only — no records are created, updated, or deleted.
"""

import sys

from odoo_sdk import OdooClient


def run(client: OdooClient) -> None:
    partners = client["res.partner"]

    # Fetch a modest set of partners to work with in-memory
    sample = partners.search([("active", "=", True)], limit=20, order="id asc")
    print(f"Fetched {len(sample.ids)} partner ids: {list(sample.ids)}")

    # ------------------------------------------------------------------ D3-1
    # filtered with a callable predicate
    # ------------------------------------------------------------------ D3-1
    companies = sample.filtered(lambda r: r.is_company)
    print(f"filtered(is_company) => {list(companies.ids)}")

    # ------------------------------------------------------------------ D3-2
    # filtered with a dotted field-path string
    # ------------------------------------------------------------------ D3-2
    named = sample.filtered("name")
    print(f"filtered('name') => {len(named.ids)} records with a non-empty name")

    # ------------------------------------------------------------------ D3-3
    # filtered_domain: in-memory domain evaluation on already-fetched fields
    # ------------------------------------------------------------------ D3-3
    company_typed = sample.filtered_domain([("is_company", "=", True)])
    print(f"filtered_domain([('is_company','=',True)]) => {list(company_typed.ids)}")

    # ------------------------------------------------------------------ D3-4
    # mapped with a callable
    # ------------------------------------------------------------------ D3-4
    names = sample.mapped(lambda r: r.name)
    print(f"mapped(lambda r: r.name) => {names!r}")

    # ------------------------------------------------------------------ D3-5
    # mapped with a field path string — returns a list of scalar values
    # ------------------------------------------------------------------ D3-5
    langs = sample.mapped("lang")
    print(f"mapped('lang') => {langs!r}")

    # ------------------------------------------------------------------ D3-6
    # sorted with a callable key
    # ------------------------------------------------------------------ D3-6
    sorted_by_name = sample.sorted(key=lambda r: (r.name or ""))
    print(f"sorted(name) first 5 ids: {list(sorted_by_name.ids[:5])}")

    # ------------------------------------------------------------------ D3-7
    # sorted with a field spec string
    # ------------------------------------------------------------------ D3-7
    sorted_by_id_desc = sample.sorted(key="id DESC")
    print(f"sorted('id DESC') first id: {list(sorted_by_id_desc.ids[:1])}")

    # ------------------------------------------------------------------ D3-8
    # grouped by field name
    # ------------------------------------------------------------------ D3-8
    by_company_flag = sample.grouped("is_company")
    for flag, group_rs in by_company_flag.items():
        print(f"grouped(is_company={flag!r}) => {len(group_rs.ids)} records")

    # ------------------------------------------------------------------ D3-9
    # grouped by callable
    # ------------------------------------------------------------------ D3-9
    by_lang = sample.grouped(lambda r: r.lang or "none")
    for lang_key, group_rs in by_lang.items():
        print(f"grouped(lang={lang_key!r}) => {len(group_rs.ids)} records")


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
