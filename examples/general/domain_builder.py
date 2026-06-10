"""Phase D6 reference example — domain builder ergonomics.

Demonstrates ``DomainExpression.AND``, ``DomainExpression.OR``,
``DomainExpression.TRUE``, ``DomainExpression.FALSE``, ``~`` (invert), ``&``
(pairwise AND), and ``|`` (pairwise OR).

The builder examples are in-memory — no Odoo connection is required to
demonstrate domain composition.  A live search section at the end is gated on
``--allow-live-production`` and shows the composed domains passed to
``OdooRecordset.search``.

Run (builder demos only, no live call):

    uv run python examples/general/domain_builder.py

Run (builder demos + live search):

    uv run python examples/general/domain_builder.py --allow-live-production
"""

import sys

from odoo_sdk import DomainExpression, OdooClient


def demo_builder() -> None:
    """Demonstrate domain composition without a live Odoo connection."""

    # ------------------------------------------------------------------ D6-1
    # TRUE and FALSE constants
    # ------------------------------------------------------------------ D6-1
    true_domain = DomainExpression.TRUE
    false_domain = DomainExpression.FALSE
    print(f"TRUE  serialize => {true_domain.serialize()!r}")
    print(f"FALSE serialize => {false_domain.serialize()!r}")

    # ------------------------------------------------------------------ D6-2
    # DomainExpression.AND — combine an iterable with logical AND
    # ------------------------------------------------------------------ D6-2
    d_company = DomainExpression.normalize([("is_company", "=", True)])
    d_active = DomainExpression.normalize([("active", "=", True)])
    d_and = DomainExpression.AND([d_company, d_active])
    print(f"AND([company, active]) => {d_and.serialize()!r}")

    # AND with empty iterable → TRUE
    d_and_empty = DomainExpression.AND([])
    print(f"AND([]) => {d_and_empty.serialize()!r}  (is_empty={d_and_empty.is_empty()!r})")

    # ------------------------------------------------------------------ D6-3
    # DomainExpression.OR — combine an iterable with logical OR
    # ------------------------------------------------------------------ D6-3
    d_customer = DomainExpression.normalize([("customer_rank", ">", 0)])
    d_or = DomainExpression.OR([d_company, d_customer])
    print(f"OR([company, customer]) => {d_or.serialize()!r}")

    # OR with empty iterable → FALSE
    d_or_empty = DomainExpression.OR([])
    print(f"OR([]) => {d_or_empty.serialize()!r}  (is_empty={d_or_empty.is_empty()!r})")

    # ------------------------------------------------------------------ D6-4
    # ~ (invert / NOT) operator
    # ------------------------------------------------------------------ D6-4
    d_not_company = ~d_company
    print(f"~company => {d_not_company.serialize()!r}")

    # Inverting TRUE yields FALSE and vice versa
    print(f"~TRUE  => is_empty={( ~true_domain).is_empty()!r}")
    print(f"~FALSE => {( ~false_domain).serialize()!r}")

    # ------------------------------------------------------------------ D6-5
    # & (pairwise AND) and | (pairwise OR) operators
    # ------------------------------------------------------------------ D6-5
    d_pairwise_and = d_company & d_active
    d_pairwise_or = d_company | d_customer
    print(f"company & active => {d_pairwise_and.serialize()!r}")
    print(f"company | customer => {d_pairwise_or.serialize()!r}")

    # ------------------------------------------------------------------ D6-6
    # Nested composition
    # ------------------------------------------------------------------ D6-6
    d_complex = (d_company & d_active) | d_customer
    print(f"(company & active) | customer => {d_complex.serialize()!r}")

    # ------------------------------------------------------------------ D6-7
    # Dynamic time values pass through to the server unchanged
    # ------------------------------------------------------------------ D6-7
    d_recent = DomainExpression.normalize([("create_date", ">=", "-3d")])
    print(f"dynamic time '-3d' => {d_recent.serialize()!r}")
    d_this_week = DomainExpression.normalize([("create_date", ">=", "=monday -1w")])
    print(f"dynamic time '=monday -1w' => {d_this_week.serialize()!r}")


def demo_live(client: OdooClient) -> None:
    """Show composed domains passed to a live search."""
    partners = client["res.partner"]

    d_company = DomainExpression.normalize([("is_company", "=", True)])
    d_active = DomainExpression.normalize([("active", "=", True)])
    d_combined = d_company & d_active

    results = partners.search(d_combined, limit=5, order="id asc")
    print(f"\nLive search with AND domain: found {len(results.ids)} partner(s): {list(results.ids)}")

    d_or = DomainExpression.OR([
        DomainExpression.normalize([("is_company", "=", True)]),
        DomainExpression.normalize([("customer_rank", ">", 0)]),
    ])
    results_or = partners.search(d_or, limit=5, order="id asc")
    print(f"Live search with OR domain : found {len(results_or.ids)} partner(s): {list(results_or.ids)}")


def main() -> None:
    demo_builder()

    if "--allow-live-production" in sys.argv:
        client = OdooClient()
        if not client.authenticated:
            print(f"\nAuthentication failed (uid={client.uid!r}); skipping live demo.")
            return
        demo_live(client)


if __name__ == "__main__":
    main()
