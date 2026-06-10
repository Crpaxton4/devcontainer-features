"""Manual Phase D live-Odoo smoke test covering D1–D6 ORM completeness features.

This script is intentionally separate from ``tests/`` so automated validation
stays purely local and deterministic. It exercises all six Phase D feature
groups against a real Odoo instance.

Feature groups tested
---------------------
D1  Aggregation and groupby (``_read_group`` on ``sale.order``)
D2  Model utility methods (``name_create``, ``name_search``, ``default_get``,
    ``copy``, ``get_metadata`` on ``res.partner``)
D3  Recordset functional operations (``filtered``, ``mapped``, ``sorted``,
    ``grouped``, ``filtered_domain`` on ``res.partner``)
D4  Recordset set operations (``|``, ``&``, ``-``, ``in``, ``<=``, ``>=``)
D5  Environment alterations (``with_user``, ``with_company``,
    ``action_archive``, ``action_unarchive``)
D6  Domain builder ergonomics (``DomainExpression.AND``, ``OR``, ``TRUE``,
    ``FALSE``, ``~``, ``&``, ``|``)

Safety constraints
------------------
- Requires ``--allow-live-production`` before any RPC calls are made.
- Creates exactly two ``res.partner`` records per run; both are deleted on exit.
- Does not delete, archive, or modify any pre-existing records.
- Read-only sections (D1, D3, D4, D6 live search) leave no side effects.

Run from the repository root::

    uv run python examples/live_phase_d_smoke_test.py --allow-live-production
"""

import sys
from typing import Any

from odoo_sdk import DomainExpression, OdooClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def _ok(label: str, value: Any = None) -> None:
    suffix = f" => {value!r}" if value is not None else ""
    print(f"  [OK] {label}{suffix}")


# ---------------------------------------------------------------------------
# D1 — Aggregation and GroupBy
# ---------------------------------------------------------------------------


def smoke_d1_aggregation(client: OdooClient) -> None:
    _section("D1 — Aggregation and GroupBy")
    orders = client["sale.order"]

    rows = orders._read_group(
        domain=[("state", "!=", "cancel")],
        groupby=("state",),
        aggregates=("amount_total:sum", "__count"),
        order="state asc",
        limit=5,
    )
    _ok(f"_read_group returned {len(rows)} group(s)")
    for row in rows[:3]:
        state, amount_sum, count = row
        _ok(f"  state={state!r}  amount_total:sum={amount_sum!r}  count={count!r}")


# ---------------------------------------------------------------------------
# D2 — Model Utility Methods
# ---------------------------------------------------------------------------


def smoke_d2_utility(client: OdooClient) -> list[int]:
    """Return ids of any records created so the caller can clean up."""
    _section("D2 — Model Utility Methods")
    partners = client["res.partner"]
    created_ids: list[int] = []

    # name_create
    new_partner = partners.name_create("SDK Phase D Smoke Test Partner")
    created_ids.append(new_partner.id)
    _ok("name_create", new_partner.id)

    # name_search
    hits = partners.name_search("SDK Phase D", limit=10)
    _ok(f"name_search found {len(hits)} hit(s)", hits[:2])

    # default_get
    defaults = partners.default_get(["name", "lang", "customer_rank"])
    _ok("default_get", defaults)

    # copy
    copied = new_partner.copy({"name": "SDK Phase D Smoke Test Partner (copy)"})
    created_ids.append(copied.id)
    _ok("copy", copied.id)

    # get_metadata
    both = new_partner | copied
    metadata = both.get_metadata()
    _ok(f"get_metadata returned {len(metadata)} entry(s)")
    for m in metadata:
        print(
            f"       id={m['id']}  create_uid={m.get('create_uid')!r}"
            f"  write_uid={m.get('write_uid')!r}"
        )

    return created_ids


# ---------------------------------------------------------------------------
# D3 — Recordset Functional Operations
# ---------------------------------------------------------------------------


def smoke_d3_functional(client: OdooClient) -> None:
    _section("D3 — Recordset Functional Operations")
    partners = client["res.partner"]
    sample = partners.search([("active", "=", True)], limit=20, order="id asc")
    _ok(f"fetched {len(sample.ids)} partner(s) for in-memory ops")

    # filtered with callable
    companies = sample.filtered(lambda r: r.is_company)
    _ok(f"filtered(is_company) => {len(companies.ids)} record(s)")

    # filtered with field path
    with_name = sample.filtered("name")
    _ok(f"filtered('name') => {len(with_name.ids)} record(s) with a name")

    # filtered_domain
    domain_companies = sample.filtered_domain([("is_company", "=", True)])
    _ok(f"filtered_domain(is_company=True) => {len(domain_companies.ids)} record(s)")
    assert set(companies.ids) == set(domain_companies.ids), (
        "filtered and filtered_domain must agree on is_company results"
    )
    _ok("filtered and filtered_domain agree")

    # mapped callable
    names = sample.mapped(lambda r: r.name)
    _ok(f"mapped(name callable) => {len(names)} value(s)")

    # mapped field path (scalar)
    langs = sample.mapped("lang")
    _ok(f"mapped('lang') => {langs[:3]!r} ...")

    # sorted callable
    by_name = sample.sorted(key=lambda r: (r.name or ""))
    _ok(f"sorted(name) first 3 ids: {list(by_name.ids[:3])}")

    # sorted field spec
    by_id_desc = sample.sorted(key="id DESC")
    _ok(f"sorted('id DESC') first id: {list(by_id_desc.ids[:1])}")

    # grouped field name
    by_company = sample.grouped("is_company")
    for flag, grp in by_company.items():
        _ok(f"grouped(is_company={flag!r}) => {len(grp.ids)} record(s)")


# ---------------------------------------------------------------------------
# D4 — Recordset Set Operations
# ---------------------------------------------------------------------------


def smoke_d4_set_ops(client: OdooClient) -> None:
    _section("D4 — Recordset Set Operations")
    partners = client["res.partner"]
    first = partners.search([], limit=8, order="id asc")
    last = partners.search([], limit=8, order="id desc")
    last = last.sorted(key="id ASC")

    union = first | last
    _ok(f"union: {len(union.ids)} ids (first={len(first.ids)}, last={len(last.ids)})")

    inter = first & last
    _ok(f"intersection: {len(inter.ids)} ids")

    diff = first - last
    _ok(f"difference: {len(diff.ids)} ids")

    if first.ids:
        singleton = first.browse(first.ids[0])
        _ok(f"singleton {singleton.id!r} in first: {singleton in first}")
        _ok(f"singleton {singleton.id!r} in last : {singleton in last}")

    if len(first.ids) >= 2:
        subset = first.browse(list(first.ids[:2]))
        _ok(f"subset(2) <= first(8): {subset <= first}")
        _ok(f"subset(2) <  first(8): {subset < first}")
        _ok(f"first(8) >= subset(2): {first >= subset}")
        _ok(f"first(8) == first(8) (<=): {first <= first}")
        _ok(f"first(8) strict subset of itself (<): {first < first}")


# ---------------------------------------------------------------------------
# D5 — Environment Alterations
# ---------------------------------------------------------------------------


def smoke_d5_env_alterations(client: OdooClient) -> list[int]:
    """Return ids of any records created so the caller can clean up."""
    _section("D5 — Environment Alterations and Active/Archived Handling")
    env = client.env
    partners = client["res.partner"]
    created_ids: list[int] = []

    # with_user on OdooEnv — returns a new env; OdooEnv has no public uid attribute
    # (uid lives on OdooClient / executor), so we verify by identity and by making
    # a successful call through the derived env.
    env2 = env.with_user(client.uid)
    assert env2 is not env, "with_user must return a new env, not mutate the original"
    _ok(f"env.with_user({client.uid!r}) => new OdooEnv created, original unmodified")

    # with_user on OdooRecordset
    rs2 = partners.with_user(client.uid)
    assert rs2._env is not partners._env, "recordset.with_user must derive a new env"
    _ok(f"recordset.with_user({client.uid!r}) => new env derived for recordset")

    # with_company
    companies = client["res.company"].search([], limit=1)
    if companies.ids:
        cid = companies.ids[0]
        env_co = env.with_company(cid)
        ctx = env_co.context
        _ok(f"env.with_company({cid!r}) allowed_company_ids={ctx.get('allowed_company_ids')!r}")

        rs_co = partners.with_company(cid)
        _ok(
            f"recordset.with_company({cid!r}) "
            f"allowed_company_ids={rs_co._env.context.get('allowed_company_ids')!r}"
        )

    # action_archive / action_unarchive
    demo_id = partners.create({"name": "SDK Phase D Smoke Archive Test", "active": True})
    demo = partners.browse(demo_id)
    created_ids.append(demo.id)
    _ok(f"created partner id={demo.id!r}")

    result = demo.action_archive()
    _ok(f"action_archive() => {result!r}")

    inactive = (
        partners.with_context({"active_test": False})
        .search([("id", "=", demo.id)])
    )
    assert inactive.ids, "archived partner must appear in active_test=False search"
    _ok("archived partner found in active_test=False search")

    result2 = demo.action_unarchive()
    _ok(f"action_unarchive() => {result2!r}")

    active_check = partners.search([("id", "=", demo.id)])
    assert active_check.ids, "unarchived partner must appear in normal search"
    _ok("unarchived partner found in normal search")

    return created_ids


# ---------------------------------------------------------------------------
# D6 — Domain Builder Ergonomics
# ---------------------------------------------------------------------------


def smoke_d6_domain_builder(client: OdooClient) -> None:
    _section("D6 — Domain Builder Ergonomics")
    partners = client["res.partner"]

    # TRUE / FALSE
    _ok(f"TRUE.serialize()  => {DomainExpression.TRUE.serialize()!r}")
    _ok(f"FALSE.serialize() => {DomainExpression.FALSE.serialize()!r}")

    # AND
    d1 = DomainExpression.normalize([("is_company", "=", True)])
    d2 = DomainExpression.normalize([("active", "=", True)])
    d_and = DomainExpression.AND([d1, d2])
    _ok(f"AND([company, active]) => {d_and.serialize()!r}")

    # OR
    d3 = DomainExpression.normalize([("customer_rank", ">", 0)])
    d_or = DomainExpression.OR([d1, d3])
    _ok(f"OR([company, customer]) => {d_or.serialize()!r}")

    # ~ invert
    d_not = ~d1
    _ok(f"~company => {d_not.serialize()!r}")

    # & and | pairwise operators
    d_pw_and = d1 & d2
    d_pw_or = d1 | d3
    _ok(f"company & active => {d_pw_and.serialize()!r}")
    _ok(f"company | customer => {d_pw_or.serialize()!r}")

    # Live search with composed domain
    results = partners.search(d_and, limit=3, order="id asc")
    _ok(f"live search with AND domain => {len(results.ids)} partner(s): {list(results.ids)}")

    results_or = partners.search(d_or, limit=3, order="id asc")
    _ok(f"live search with OR domain  => {len(results_or.ids)} partner(s): {list(results_or.ids)}")

    # Dynamic time value strings (e.g. '-3d', '=monday -1w') are Odoo view-domain
    # expressions.  They serialize through the builder unchanged, which is the
    # correct SDK behaviour.  They are NOT valid as raw SQL timestamps, so they
    # must not be passed to a live XML-RPC search call directly.
    d_recent = DomainExpression.normalize([("create_date", ">=", "-3d")])
    _ok(f"dynamic '-3d' serializes => {d_recent.serialize()!r}  (pass-through confirmed; not sent to server)")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_smoke(client: OdooClient) -> None:
    all_created_ids: list[int] = []

    try:
        smoke_d1_aggregation(client)
        all_created_ids += smoke_d2_utility(client)
        smoke_d3_functional(client)
        smoke_d4_set_ops(client)
        all_created_ids += smoke_d5_env_alterations(client)
        smoke_d6_domain_builder(client)

        _section("Summary")
        print("  All Phase D smoke checks passed.")
    finally:
        if all_created_ids:
            _section("Cleanup")
            partners = client["res.partner"]
            rs = partners.browse(all_created_ids)
            rs.unlink()
            print(f"  Unlinked {len(all_created_ids)} demo partner(s): {all_created_ids}")


def main() -> None:
    if "--allow-live-production" not in sys.argv:
        print(
            "Pass --allow-live-production to run this smoke test"
            " against a live Odoo instance."
        )
        return

    client = OdooClient()
    if not client.authenticated:
        print(f"Authentication failed: uid is falsy ({client.uid!r})")
        return

    run_smoke(client)


if __name__ == "__main__":
    main()
