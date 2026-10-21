#!/usr/bin/env python3
"""post-update-check.py — what `-u` leaves behind in the stored data of an upgraded dump.

Usage: post-update-check.py --db DB --worktree W --base REF --out FILE [--dry-run]

Reads `git -C W diff REF...HEAD` and reports two things (#1163):

  recompute[]      stored computed fields whose `@api.depends(...)` changed. Old rows keep
                   the old value and nothing recomputes them; the row count of NULL values
                   (`select count(*) from <model with . as _> where <field> is null`).
  removed_names[]  identifiers the diff removed: a `fields.X` definition, or a `_name` or
                   `_inherit` model name. Every one is searched in stored code and domains:
                   ir_act_server.code, ir_cron.code (when the column exists),
                   base_automation filter_domain, filter_pre_domain and code, ir_filters
                   domain and context, ir_rule domain_force.
  hits[]           one row per match: {table, id, column, name}.

The diff parser is a best effort over the hunks, not a Python parser. It sees a changed
`@api.depends(` line (not a changed continuation line of a multi-line decorator), takes
the first `def` after it, and finds the `fields.X(... compute='<def>' ... store=True)`
definition in the file of HEAD. The model is the last `_name` or `_inherit` string before
the field in its class. A field renamed in the same diff is not reported as removed.

--dry-run prints recompute and removed_names without a database.
Uses psycopg2 when it imports, else `psql -Atqc`.
Exit codes: 0 no hits and no NULL rows | 1 any hit or NULL row | 2 usage or connection error
"""

import argparse
import json
import os
import re
import subprocess
import sys

DEPENDS = re.compile(r"^[+-]\s*@api\.depends\(")
DEF = re.compile(r"^[ +-]\s*def\s+(\w+)\s*\(")
FIELD_DEF = re.compile(r"^[+-]\s*(\w+)\s*=\s*fields\.\w+\(")
MODEL_NAME = re.compile(r"^[+-]\s*_(?:name|inherit)\s*=\s*['\"]([\w.]+)['\"]")
CLASS_START = re.compile(r"^class\s", re.M)
MODEL_ATTR = re.compile(r"^\s*_(?:name|inherit)\s*=\s*['\"]([\w.]+)['\"]", re.M)

# table -> columns that hold code, a domain or a context
SEARCHED = {
    "ir_cron": ["code"],
    "ir_act_server": ["code"],
    "base_automation": ["filter_domain", "filter_pre_domain", "code"],
    "ir_filters": ["domain", "context"],
    "ir_rule": ["domain_force"],
}


def git(worktree, *args):
    proc = subprocess.run(
        ["git", "-C", worktree, *args], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git failed")
    return proc.stdout


def read_head(worktree, path):
    try:
        with open(
            os.path.join(worktree, path), encoding="utf-8", errors="replace"
        ) as handle:
            return handle.read()
    except OSError:
        return ""


def model_of(text, offset):
    """The last _name or _inherit string of the class that holds `offset`."""
    start = 0
    for found in CLASS_START.finditer(text):
        if found.start() <= offset:
            start = found.start()
    models = [m.group(1) for m in MODEL_ATTR.finditer(text[start:offset])]
    return models[-1] if models else None


def stored_fields_of(text, method):
    """[(model, field)] for each stored field computed by `method` in `text`."""
    pattern = re.compile(
        r"(\w+)\s*=\s*fields\.\w+\((?:[^()]|\([^()]*\))*?compute\s*=\s*['\"]%s['\"](?:[^()]|\([^()]*\))*\)"
        % re.escape(method),
        re.S,
    )
    rows = []
    for found in pattern.finditer(text):
        if re.search(r"store\s*=\s*True", found.group(0)):
            model = model_of(text, found.start())
            if model:
                rows.append((model, found.group(1)))
    return rows


def parse_diff(diff, read_file):
    """Return (recompute, removed_names) from a unified diff.

    read_file(path) returns the text of that file at HEAD.
    """
    path = None
    lines = diff.splitlines()
    methods = []  # (path, method) of changed @api.depends
    added, removed = set(), set()
    for index, line in enumerate(lines):
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
            continue
        if line.startswith("--- ") or path is None or not path.endswith(".py"):
            continue
        if DEPENDS.match(line):
            for follow in lines[index + 1 : index + 9]:
                found = DEF.match(follow)
                if found:
                    if (path, found.group(1)) not in methods:
                        methods.append((path, found.group(1)))
                    break
        for pattern in (FIELD_DEF, MODEL_NAME):
            found = pattern.match(line)
            if found:
                (added if line[0] == "+" else removed).add(found.group(1))
    recompute = []
    for file_path, method in methods:
        for model, field in stored_fields_of(read_file(file_path), method):
            row = {"model": model, "field": field}
            if row not in recompute:
                recompute.append(row)
    return recompute, sorted(removed - added)


class Database:
    def __init__(self, name):
        self.name = name
        self.conn = None
        try:
            import psycopg2  # noqa: PLC0415

            self.conn = psycopg2.connect(dbname=name)
            self.conn.autocommit = True
        except ImportError:
            pass
        except Exception as error:  # connection refused, no such database
            raise ConnectionError(str(error)) from error
        self.rows("select 1")

    def rows(self, sql):
        if self.conn is not None:
            with self.conn.cursor() as cursor:
                cursor.execute(sql)
                return [[str(v) for v in row] for row in cursor.fetchall()]
        proc = subprocess.run(
            ["psql", "-Atqc", sql, "-d", self.name], capture_output=True, text=True
        )
        if proc.returncode != 0:
            raise ConnectionError(proc.stderr.strip() or "psql failed")
        return [line.split("|") for line in proc.stdout.splitlines() if line != ""]

    def has_column(self, table, column):
        sql = (
            "select 1 from information_schema.columns "
            "where table_name='%s' and column_name='%s'" % (table, column)
        )
        return bool(self.rows(sql))


def check_database(db, recompute, removed):
    for row in recompute:
        table = row["model"].replace(".", "_")
        try:
            sql = 'select count(*) from "%s" where "%s" is null' % (table, row["field"])
            row["null_rows"] = int(db.rows(sql)[0][0])
        except Exception as error:
            # a missing table or column is a finding, not a crash
            row["null_rows"] = None
            row["error"] = str(error)[:200]
    hits = []
    for name in removed:
        if not re.fullmatch(r"[\w.]+", name):
            continue
        pattern = "\\m%s\\M" % name.replace(".", "\\.")
        for table, columns in SEARCHED.items():
            for column in columns:
                if not db.has_column(table, column):
                    continue
                sql = (
                    f"select id from {table} where cast({column} as text) ~ '{pattern}'"
                )
                for found in db.rows(sql):
                    hit = {"table": table, "id": int(found[0])}
                    hits.append({**hit, "column": column, "name": name})
    return hits


def main():
    parser = argparse.ArgumentParser(
        description="Check stored data after -u on an upgraded dump."
    )
    parser.add_argument("--db")
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--base", required=True, help="git ref of the source series")
    parser.add_argument("--out")
    parser.add_argument(
        "--dry-run", action="store_true", help="print recompute and removed_names only"
    )
    args = parser.parse_args()
    if not args.dry_run and not (args.db and args.out):
        parser.error("--db and --out are required without --dry-run")
    try:
        diff = git(args.worktree, "diff", "%s...HEAD" % args.base)
    except RuntimeError as error:
        print("post-update-check.py: %s" % error, file=sys.stderr)
        return 2
    recompute, removed = parse_diff(diff, lambda p: read_head(args.worktree, p))
    if args.dry_run:
        print(json.dumps({"recompute": recompute, "removed_names": removed}, indent=2))
        return 0
    try:
        db = Database(args.db)
        hits = check_database(db, recompute, removed)
    except ConnectionError as error:
        print(
            "post-update-check.py: cannot query %s: %s" % (args.db, error),
            file=sys.stderr,
        )
        return 2
    report = {"recompute": recompute, "removed_names": removed, "hits": hits}
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")
    print(json.dumps(report, indent=2))
    dirty = hits or any(r["null_rows"] != 0 for r in recompute)
    return 1 if dirty else 0


if __name__ == "__main__":
    sys.exit(main())
