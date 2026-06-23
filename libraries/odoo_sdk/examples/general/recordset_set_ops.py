"""Phase D4 reference example — recordset set operations.

Demonstrates ``|`` (union), ``&`` (intersection), ``-`` (difference),
``in`` (membership), and subset/superset comparisons (``<=``, ``<``, ``>=``,
``>``) on ``res.partner`` recordsets.

Run from the repository root:

    uv run python examples/general/recordset_set_ops.py --allow-live-production

Safety: read-only — no records are created, updated, or deleted.
"""

import sys

from odoo_sdk import OdooClient


def run(client: OdooClient) -> None:
    partners = client["res.partner"]

    # Fetch two overlapping slices to exercise all set operators
    first_ten = partners.search([], limit=10, order="id asc")
    last_ten = partners.search([], limit=10, order="id desc")
    # Make last_ten id-ascending so output is predictable
    last_ten = last_ten.sorted(key="id ASC")

    print(f"first_ten ids : {list(first_ten.ids)}")
    print(f"last_ten  ids : {list(last_ten.ids)}")

    # ------------------------------------------------------------------ D4-1
    # Union: all ids from both, order preserved, deduplicated
    # ------------------------------------------------------------------ D4-1
    union = first_ten | last_ten
    print(f"union  ({len(union.ids)} ids): {list(union.ids)}")

    # ------------------------------------------------------------------ D4-2
    # Intersection: ids present in both operands
    # ------------------------------------------------------------------ D4-2
    intersection = first_ten & last_ten
    print(f"intersection ({len(intersection.ids)} ids): {list(intersection.ids)}")

    # ------------------------------------------------------------------ D4-3
    # Difference: ids in first_ten that are not in last_ten
    # ------------------------------------------------------------------ D4-3
    difference = first_ten - last_ten
    print(f"difference ({len(difference.ids)} ids): {list(difference.ids)}")

    # ------------------------------------------------------------------ D4-4
    # Membership: test whether a singleton is contained in a recordset
    # ------------------------------------------------------------------ D4-4
    singleton = first_ten.browse(first_ten.ids[0])
    print(f"singleton id={singleton.id!r} in first_ten: {singleton in first_ten}")
    print(f"singleton id={singleton.id!r} in last_ten : {singleton in last_ten}")

    # ------------------------------------------------------------------ D4-5
    # Subset / superset comparisons
    # ------------------------------------------------------------------ D4-5
    subset = first_ten.browse(list(first_ten.ids[:3]))
    print(f"subset (first 3 of first_ten) <= first_ten : {subset <= first_ten}")
    print(f"subset <  first_ten                        : {subset < first_ten}")
    print(f"first_ten >= subset                        : {first_ten >= subset}")
    print(f"first_ten >  subset                        : {first_ten > subset}")
    print(f"first_ten <= first_ten (equal sets)        : {first_ten <= first_ten}")
    print(f"first_ten <  first_ten (strict subset)     : {first_ten < first_ten}")


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
