"""Usage page tests. The stores follow the plan's fixed contract, not a producer."""

import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from webui import create_app

SRC = Path(__file__).parent.parent / "src" / "webui"

USAGE_DDL = (
    "CREATE TABLE usage(ts TEXT, kind TEXT, name TEXT, context TEXT)",
    "CREATE INDEX usage_idx ON usage(kind, name, ts)",
)
DECL_DDL = (
    "CREATE TABLE declarations(kind TEXT, name TEXT, declared_at TEXT, "
    "context TEXT, PRIMARY KEY (kind, name))"
)


def _ago(days):
    moment = datetime.now(timezone.utc) - timedelta(days=days)
    return moment.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _make(directory, usage, declarations):
    conn = sqlite3.connect(directory / "usage.db")
    for ddl in USAGE_DDL:
        conn.execute(ddl)
    conn.executemany(
        "INSERT INTO usage VALUES (?, ?, ?, ?)",
        [
            (ts, k, n, json.dumps(c) if not isinstance(c, str) else c)
            for ts, k, n, c in usage
        ],
    )
    conn.commit()
    conn.close()
    conn = sqlite3.connect(directory / "declarations.db")
    conn.execute(DECL_DDL)
    conn.executemany(
        "INSERT INTO declarations VALUES (?, ?, ?, '{}')",
        [(k, n, _ago(100)) for k, n in declarations],
    )
    conn.commit()
    conn.close()


@pytest.fixture
def usage_dir(tmp_path, monkeypatch):
    directory = tmp_path / "usage-log"
    directory.mkdir()
    monkeypatch.setenv("USAGE_LOG_DIR", str(directory))
    _make(
        directory,
        [
            (_ago(1), "alpha", "one", {"run": "r1", "tokens": 10, "cost": 0.5}),
            (_ago(2), "alpha", "one", {"run": "r1", "tokens": 5, "cost": 0.25}),
            (_ago(3), "alpha", "two", {"run": "r2", "tokens": 7}),
            (_ago(60), "beta", "old", {"run": "r3", "flag": True}),
            (_ago(1), "beta", "bad", "not json"),
        ],
        [
            ("alpha", "one"),
            ("alpha", "two"),
            ("beta", "old"),
            ("beta", "never-seen"),
        ],
    )
    return directory


@pytest.fixture
def uclient(usage_dir):
    return create_app().test_client()


def _get(client, **query):
    return client.get("/usage", query_string=query)


def _text(response):
    return response.get_data(as_text=True)


def test_counts_by_kind_and_name(uclient):
    page = _text(_get(uclient, view="counts", group="kind"))
    assert re.search(r"<td>alpha</td><td>3</td>", page)
    assert re.search(r"<td>beta</td><td>2</td>", page)
    page = _text(_get(uclient, group="name", kind="alpha"))
    assert re.search(r"<td>one</td><td>2</td>", page)
    assert "<td>old</td>" not in page


def test_date_filter(uclient):
    since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    page = _text(_get(uclient, group="name", since=since))
    assert "<td>old</td>" not in page
    assert "<td>two</td>" in page
    until = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    page = _text(_get(uclient, group="name", until=until))
    assert "<td>old</td>" in page and "<td>two</td>" not in page


def test_context_value_filter_is_bound(uclient):
    page = _text(_get(uclient, group="name", ctx_key="run", ctx_value="r2"))
    assert "<td>two</td>" in page and "<td>one</td>" not in page
    page = _text(_get(uclient, group="name", ctx_key="run", ctx_value="x' OR '1'='1"))
    assert "No rows" in page


def test_sort_by_count_and_group(uclient):
    page = _text(_get(uclient, group="name", sort="count", order="desc"))
    assert page.index("<td>one</td>") < page.index("<td>two</td>")
    page = _text(_get(uclient, group="name", sort="group", order="asc"))
    assert page.index("<td>bad</td>") < page.index("<td>one</td>")


def test_last_used_view(uclient):
    page = _text(_get(uclient, view="last"))
    row = re.search(r"<td>alpha</td><td>one</td><td>([^<]*)</td><td>(\d+)</td>", page)
    assert row and row.group(2) == "2"
    assert row.group(1) == _ago(1)[:10] + row.group(1)[10:]


def test_never_used_view(uclient):
    page = _text(_get(uclient, view="never", days="30"))
    assert "<td>never-seen</td>" in page  # declared, never used
    assert "<td>old</td>" in page  # last use 60 days ago
    assert "<td>one</td>" not in page  # used inside the window
    assert "<td>two</td>" not in page
    page = _text(_get(uclient, view="never", days="90"))
    assert "<td>old</td>" not in page and "<td>never-seen</td>" in page


def test_context_breakdown_sums_numeric_keys(uclient):
    page = _text(_get(uclient, view="context", group="ctx:run"))
    assert "sum tokens" in page and "sum cost" in page
    assert "sum flag" not in page and "sum run" not in page
    row = re.search(
        r"<td>r1</td><td>2</td><td>[^<]*</td>\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>",
        page,
    )
    assert (
        row
        and (row.group(2), row.group(1)) == ("15", "0.75")
        or (row and (row.group(1), row.group(2)) == ("0.75", "15"))
    )


def test_sort_by_summed_key(uclient):
    page = _text(_get(uclient, view="context", group="ctx:run", sort="sum:tokens"))
    assert page.index("<td>r1</td>") < page.index("<td>r2</td>")


def test_new_kind_and_key_need_no_code_change(usage_dir, uclient):
    conn = sqlite3.connect(usage_dir / "usage.db")
    conn.execute(
        "INSERT INTO usage VALUES (?, 'gamma', 'fresh', ?)",
        (_ago(0), json.dumps({"zone": "z9", "amount": 3})),
    )
    conn.commit()
    conn.close()
    page = _text(_get(uclient))
    assert '<option value="gamma"' in page
    assert '<option value="fresh"' in page
    assert '<option value="zone"' in page
    page = _text(_get(uclient, view="context", group="ctx:zone", sort="sum:amount"))
    assert "<td>z9</td>" in page and "sum amount" in page


@pytest.mark.parametrize(
    "query",
    [
        {"group": "ts"},
        {"group": "name; DROP TABLE usage"},
        {"view": "context", "group": "ctx:missing"},
        {"view": "context", "group": "kind"},
        {"sort": "sum:run"},
        {"sort": "bogus"},
        {"order": "sideways"},
        {"ctx_key": "missing"},
        {"view": "nope"},
        {"view": "never", "days": "x"},
    ],
)
def test_unknown_values_are_refused(uclient, usage_dir, query):
    assert _get(uclient, **query).status_code == 400
    conn = sqlite3.connect(usage_dir / "usage.db")
    assert conn.execute("SELECT count(*) FROM usage").fetchone()[0] == 5


def test_htmx_returns_fragment_with_vary(uclient):
    full = uclient.get("/usage")
    fragment = uclient.get("/usage", headers={"HX-Request": "true"})
    assert "<html" in _text(full) and "<html" not in _text(fragment)
    assert fragment.headers["Vary"] == "HX-Request"
    assert 'hx-push-url="true"' in _text(full)


def test_missing_store_is_an_empty_state(tmp_path, monkeypatch):
    monkeypatch.setenv("USAGE_LOG_DIR", str(tmp_path / "none"))
    client = create_app().test_client()
    for view in ("counts", "last", "never", "context"):
        response = _get(client, view=view)
        assert response.status_code == 200
        assert "No usage store found" in _text(response)
    monkeypatch.delenv("USAGE_LOG_DIR")
    assert create_app().test_client().get("/usage").status_code == 200


def test_empty_directory_and_declarations_only(tmp_path, monkeypatch):
    monkeypatch.setenv("USAGE_LOG_DIR", str(tmp_path))
    conn = sqlite3.connect(tmp_path / "declarations.db")
    conn.execute(DECL_DDL)
    conn.execute("INSERT INTO declarations VALUES ('k', 'n', 'x', '{}')")
    conn.commit()
    conn.close()
    client = create_app().test_client()
    assert "<td>n</td>" in _text(_get(client, view="never"))
    assert _get(client, view="counts").status_code == 200


def test_page_renders_while_a_writer_writes_in_wal(usage_dir, uclient):
    conn = sqlite3.connect(usage_dir / "usage.db")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()
    conn.close()
    writer = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sqlite3, sys, time\n"
            "c = sqlite3.connect(sys.argv[1], timeout=5)\n"
            "for i in range(300):\n"
            "    c.execute(\"INSERT INTO usage VALUES ('2099-01-01T00:00:00', 'w', 'x', '{}')\")\n"
            "    c.commit()\n",
            str(usage_dir / "usage.db"),
        ]
    )
    try:
        for _ in range(20):
            assert _get(uclient, group="kind").status_code == 200
    finally:
        assert writer.wait(timeout=60) == 0
    assert re.search(r"<td>w</td><td>300</td>", _text(_get(uclient, group="kind")))


def test_page_code_names_no_kind_or_key_and_imports_no_producer():
    source = "\n".join(
        p.read_text()
        for p in [SRC / "usage.py", *(SRC / "templates").glob("*usage*.html")]
    )
    for literal in (
        "skill",
        "agent",
        "command",
        "hook",
        "mcp_tool",
        "sdk_command",
        "job",
        "session",
        "repo",
        "container",
        "token",
        "cost",
        "turns",
        "duration",
        "alpha",
        "beta",
        "run",
        "flag",
    ):
        assert not re.search(rf"['\"/>\s]{literal}[_a-z]*['\"<\s]", source), literal
    code = (SRC / "usage.py").read_text()
    imports = re.findall(r"^\s*(?:from|import)\s+(\S+)", code, re.M)
    assert not [m for m in imports if "usage_log" in m or "odoo_client" in m]
