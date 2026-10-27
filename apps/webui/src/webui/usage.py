"""Usage page: filters and aggregates over the usage and declaration stores.

The page knows only the line format: ``usage(ts, kind, name, context)`` and
``declarations(kind, name, declared_at, context)``. Every choice on the page
(kinds, names, context keys, values) is read from the data. A group-by or sort
value is accepted only when it is in a set built from the data. Filter values
and context keys are bound parameters: no SQL contains text from the request.
This module does not import the producers.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, Response, abort, render_template, request

from webui import paths

bp = Blueprint("usage", __name__)

VIEWS = (
    ("counts", "Counts by kind or name"),
    ("last", "Last used per feature"),
    ("never", "Never used in N days"),
    ("context", "Breakdown by context key"),
)
FIXED_GROUPS = ("kind", "name")
CTX = "(CASE WHEN json_valid(t.context) THEN t.context ELSE '{}' END)"
VALUE_LIMIT = 200


def _uri(path: Path) -> str:
    return f"{path.absolute().as_uri()}?mode=ro"


class Store:
    """Both stores on one read-only connection: schema ``u`` and schema ``d``.

    A missing file is attached as an empty in-memory schema, so each query
    meets "no such table" and ``run`` converts that to ``[]``.
    """

    def __init__(self, directory: Path | None):
        self.conn = sqlite3.connect(":memory:", uri=True, timeout=5)
        self.conn.row_factory = sqlite3.Row
        for schema, name in (("u", "usage.db"), ("d", "declarations.db")):
            target = directory / name if directory else None
            location = (
                _uri(target) if target is not None and target.is_file() else ":memory:"
            )
            self.conn.execute(f"ATTACH DATABASE ? AS {schema}", (location,))

    def run(self, sql, args=()):
        try:
            return [dict(r) for r in self.conn.execute(sql, tuple(args))]
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return []
            raise

    def close(self):
        self.conn.close()


def _first_column(rows):
    return [next(iter(r.values())) for r in rows]


def discover(store: Store) -> dict:
    """Every choice the form offers, read from the data."""
    kinds = _first_column(store.run("SELECT DISTINCT kind FROM u.usage")) + (
        _first_column(store.run("SELECT DISTINCT kind FROM d.declarations"))
    )
    names = _first_column(store.run("SELECT DISTINCT name FROM u.usage")) + (
        _first_column(store.run("SELECT DISTINCT name FROM d.declarations"))
    )
    keys = _first_column(
        store.run(f"SELECT DISTINCT j.key FROM u.usage t, json_each({CTX}) j")
    )
    numeric = _first_column(
        store.run(
            f"SELECT DISTINCT j.key FROM u.usage t, json_each({CTX}) j "
            "WHERE j.type IN ('integer', 'real')"
        )
    )
    return {
        "kinds": sorted({str(k) for k in kinds if k is not None}),
        "names": sorted({str(n) for n in names if n is not None}),
        "context_keys": sorted(str(k) for k in keys),
        "numeric": sorted(str(k) for k in numeric),
    }


def _filters(args: dict, alias: str = "t"):
    """WHERE fragments and bound parameters for the usage filters."""
    clauses, params = [], []
    if args["kind"]:
        clauses.append(f"{alias}.kind = ?")
        params.append(args["kind"])
    if args["name"]:
        clauses.append(f"{alias}.name = ?")
        params.append(args["name"])
    if alias == "t":
        if args["since"]:
            clauses.append("substr(t.ts, 1, 10) >= ?")
            params.append(args["since"])
        if args["until"]:
            clauses.append("substr(t.ts, 1, 10) <= ?")
            params.append(args["until"])
        if args["ctx_key"]:
            clauses.append(
                f"EXISTS (SELECT 1 FROM json_each({CTX}) c "
                "WHERE c.key = ? AND CAST(c.value AS TEXT) = ?)"
            )
            params.extend([args["ctx_key"], args["ctx_value"]])
    return clauses, params


def _where(clauses):
    return (" WHERE " + " AND ".join(clauses)) if clauses else ""


def _group_expr(group: str):
    """SQL expression and parameters for a validated group."""
    if group in FIXED_GROUPS:
        return f"t.{group}", []
    key = group[len("ctx:") :]
    return (
        f"(SELECT g.value FROM json_each({CTX}) g WHERE g.key = ?)",
        [key],
    )


def grouped(store: Store, args: dict, group: str) -> tuple[list[dict], list[str]]:
    """Rows grouped by ``group``: count, last ts and the sum of each numeric key."""
    clauses, params = _filters(args)
    expr, gparams = _group_expr(group)
    base = store.run(
        f"SELECT {expr} AS g, count(*) AS n, max(t.ts) AS last "
        f"FROM u.usage t{_where(clauses)} GROUP BY g",
        gparams + params,
    )
    numeric_only = "s.type IN ('integer', 'real')"
    sums = store.run(
        f"SELECT {expr} AS g, s.key AS k, SUM(s.value) AS v "
        f"FROM u.usage t, json_each({CTX}) s "
        f"{_where(clauses + [numeric_only])} GROUP BY g, k",
        gparams + params,
    )
    result = {
        r["g"]: {"group": r["g"], "count": r["n"], "last": r["last"], "sums": {}}
        for r in base
    }
    keys = set()
    for row in sums:
        result[row["g"]]["sums"][row["k"]] = row["v"]
        keys.add(row["k"])
    return list(result.values()), sorted(keys)


def last_used(store: Store, args: dict) -> list[dict]:
    clauses, params = _filters(args)
    return store.run(
        "SELECT t.kind AS kind, t.name AS name, max(t.ts) AS last, count(*) AS n "
        f"FROM u.usage t{_where(clauses)} GROUP BY t.kind, t.name "
        "ORDER BY t.kind, t.name",
        params,
    )


def never_used(store: Store, args: dict, days: int) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    clauses, params = _filters(args, alias="x")
    clauses.append("(l.last IS NULL OR substr(l.last, 1, 19) < ?)")
    has_usage = store.run("SELECT 1 FROM u.sqlite_master WHERE name = 'usage'")
    used = (
        "SELECT kind, name, max(ts) AS last, count(*) AS n "
        "FROM u.usage GROUP BY kind, name"
        if has_usage
        else "SELECT NULL AS kind, NULL AS name, NULL AS last, 0 AS n WHERE 0"
    )
    return store.run(
        "SELECT x.kind AS kind, x.name AS name, x.declared_at AS declared_at, "
        f"l.last AS last, coalesce(l.n, 0) AS n FROM d.declarations x LEFT JOIN ({used}) l "
        f"ON l.kind = x.kind AND l.name = x.name{_where(clauses)} "
        "ORDER BY x.kind, x.name",
        params + [cutoff],
    )


def _parse(raw) -> dict:
    return {
        "view": raw.get("view", "counts"),
        "kind": raw.get("kind", ""),
        "name": raw.get("name", ""),
        "since": raw.get("since", ""),
        "until": raw.get("until", ""),
        "ctx_key": raw.get("ctx_key", ""),
        "ctx_value": raw.get("ctx_value", ""),
        "group": raw.get("group", ""),
        "sort": raw.get("sort", "count"),
        "order": raw.get("order", "desc"),
        "days": raw.get("days", "30"),
    }


def _refuse(message: str):
    abort(Response(message, status=400, mimetype="text/plain"))


def _sorted(items, sort, order, numeric):
    if sort == "count":
        key = lambda r: r["count"]  # noqa: E731
    elif sort == "last":
        key = lambda r: r["last"] or ""  # noqa: E731
    elif sort == "group":
        key = lambda r: str(r["group"])  # noqa: E731
    else:
        name = sort[len("sum:") :]
        key = lambda r: r["sums"].get(name, 0)  # noqa: E731
    return sorted(items, key=key, reverse=(order == "desc"))


@bp.get("/usage")
def index():
    args = _parse(request.args)
    directory = paths.usage_log_dir()
    store = Store(directory)
    try:
        choices = discover(store)
        view = args["view"]
        if view not in dict(VIEWS):
            _refuse(f"Unknown view: {view!r}")
        groups = (
            list(FIXED_GROUPS)
            if view == "counts"
            else [f"ctx:{k}" for k in choices["context_keys"]]
        )
        sorts = ["count", "last", "group"] + [f"sum:{k}" for k in choices["numeric"]]
        if args["order"] not in ("asc", "desc"):
            _refuse(f"Unknown order: {args['order']!r}")
        if args["sort"] not in sorts:
            _refuse(f"Unknown sort: {args['sort']!r}")
        if view in ("counts", "context"):
            args["group"] = args["group"] or (groups[0] if groups else "")
            if groups and args["group"] not in groups:
                _refuse(f"Unknown group: {args['group']!r}")
        if args["ctx_key"] and args["ctx_key"] not in choices["context_keys"]:
            _refuse(f"Unknown context key: {args['ctx_key']!r}")
        try:
            days = int(args["days"])
            if days < 0:
                raise ValueError
        except ValueError:
            _refuse(f"Days must be a whole number: {args['days']!r}")
        table, sum_keys, items = view, [], []
        if view in ("counts", "context") and groups:
            items, sum_keys = grouped(store, args, args["group"])
            items = _sorted(items, args["sort"], args["order"], choices["numeric"])
        elif view == "last":
            items = last_used(store, args)
        elif view == "never":
            items = never_used(store, args, days)
        values = []
        if args["ctx_key"]:
            values = _first_column(
                store.run(
                    f"SELECT DISTINCT CAST(j.value AS TEXT) FROM u.usage t, "
                    f"json_each({CTX}) j WHERE j.key = ? LIMIT {VALUE_LIMIT}",
                    [args["ctx_key"]],
                )
            )
    finally:
        store.close()
    template = (
        "_usage_table.html" if request.headers.get("HX-Request") else "usage.html"
    )
    response = Response(
        render_template(
            template,
            args=args,
            views=VIEWS,
            choices=choices,
            groups=groups,
            sorts=sorts,
            values=values,
            table=table,
            items=items,
            sum_keys=sum_keys,
            have_store=bool(directory and directory.is_dir()),
        ),
        mimetype="text/html",
    )
    response.headers["Vary"] = "HX-Request"
    return response
