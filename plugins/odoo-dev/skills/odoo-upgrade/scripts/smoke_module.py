#!/usr/bin/env python3
"""smoke_module.py - generate the throwaway test module `upgrade_smoke`.

Usage: smoke_module.py --worktree W --series T --out DIR [--modules a,b]

Why: the custom modules often have no tests, so a green install proves little.
The generated module depends on the custom modules and holds:
  - an HttpCase with one test per entry: the web client (`/odoo` from 17.0,
    `/web` before), `/shop` when a dependency is `website_sale`, and each custom
    `ir.actions.act_window` record (`/odoo/action-<module>.<id>` from 17.0,
    `/web#action=<module>.<id>` before). Each test loads the url in the headless
    browser and fails on a console error, so a missing template or a JS import
    error is a failure;
  - a TransactionCase with one test per report template the custom modules
    inherit (`<template id=... inherit_id=...>` under `report/` or `reports/`):
    it renders the report for one record, and skips when the model has none.

Output: the module path on the first line, then one JSON line
{module_dir, http_tests, report_tests}.
Exit: 0 generated | 2 usage.
"""

import argparse
import ast
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

NAME = "upgrade_smoke"


def manifest(path):
    try:
        with open(os.path.join(path, "__manifest__.py"), encoding="utf-8") as fh:
            data = ast.literal_eval(fh.read())
    except (OSError, ValueError, SyntaxError):
        return None
    return data if isinstance(data, dict) else None


def custom_modules(worktree):
    mods = {}
    for entry in sorted(os.listdir(worktree)):
        path = os.path.join(worktree, entry)
        data = manifest(path) if os.path.isdir(path) else None
        if data is not None:
            mods[entry] = data
    return mods


def xml_files(root):
    for base, _dirs, files in os.walk(root):
        for name in sorted(files):
            if name.endswith(".xml"):
                yield os.path.join(base, name)


def records(path):
    try:
        return list(ET.parse(path).getroot().iter())
    except ET.ParseError:
        return []


def window_actions(module, root):
    found = []
    for path in xml_files(root):
        for node in records(path):
            if node.tag == "record" and node.get("model") == "ir.actions.act_window":
                if node.get("id"):
                    found.append(node.get("id"))
    return sorted(set("%s.%s" % (module, i) if "." not in i else i for i in found))


def report_templates(module, root):
    """Return the qualified xmlids of the report templates the module inherits."""
    found = []
    for path in xml_files(root):
        parts = path.split(os.sep)
        if "report" not in parts and "reports" not in parts:
            continue
        for node in records(path):
            if node.tag == "template" and node.get("inherit_id"):
                ref = node.get("inherit_id")
                found.append(ref if "." in ref else "%s.%s" % (module, ref))
    return sorted(set(found))


def ident(text):
    return re.sub(r"\W", "_", text)


def render(series, deps, actions, reports):
    major = float(series.split(".")[0])
    urls = []
    urls.append(("web_client", "/odoo" if major >= 17 else "/web"))
    if "website_sale" in deps:
        urls.append(("shop", "/shop"))
    for xmlid in actions:
        if major >= 17:
            urls.append(("action_" + ident(xmlid), "/odoo/action-" + xmlid))
        else:
            urls.append(("action_" + ident(xmlid), "/web#action=" + xmlid))
    lines = [
        "from odoo.tests import HttpCase, TransactionCase, tagged",
        "",
        "",
        '@tagged("post_install", "-at_install")',
        "class TestSmokeHttp(HttpCase):",
        "    def _load(self, url):",
        '        self.authenticate("admin", "admin")',
        "        self.assertEqual(self.url_open(url).status_code, 200)",
        "        self.browser_js(",
        '            url, code="console.log(\'ok\')", ready="true", login="admin"',
        "        )",
    ]
    for name, url in urls:
        lines += ["", "    def test_%s(self):" % name, "        self._load(%r)" % url]
    lines += [
        "",
        "",
        '@tagged("post_install", "-at_install")',
        "class TestSmokeReports(TransactionCase):",
    ]
    if not reports:
        lines += ["    pass"]
    for rid in reports:
        lines += [
            "",
            "    def test_report_%s(self):" % ident(rid),
            "        report = self.env['ir.actions.report'].search(",
            "            [('report_name', '=', %r)], limit=1" % rid,
            "        )",
            "        if not report:",
            "            print('upgrade_smoke: %s is no report name, skipped')" % rid,
            "            return",
            "        record = self.env[report.model].search([], limit=1)",
            "        if not record:",
            "            print('upgrade_smoke: no %s record, skipped' % report.model)",
            "            return",
            "        self.env['ir.actions.report']._render_qweb_html(",
            "            report.report_name, [record.id]",
            "        )",
        ]
    return "\n".join(lines) + "\n", len(urls)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--worktree", required=True)
    ap.add_argument("--series", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--modules")
    try:
        args = ap.parse_args()
    except SystemExit:
        return 2
    if not os.path.isdir(args.worktree) or not re.match(r"^\d+\.\d$", args.series):
        print("smoke_module.py: bad --worktree or --series", file=sys.stderr)
        return 2
    mods = custom_modules(args.worktree)
    if args.modules:
        wanted = [m for m in args.modules.split(",") if m]
        mods = {m: mods[m] for m in wanted if m in mods}
    deps = set()
    actions, reports = [], []
    for name in mods:
        root = os.path.join(args.worktree, name)
        deps.update(mods[name].get("depends", []))
        actions += window_actions(name, root)
        reports += report_templates(name, root)
    depends = sorted(mods) + ["web"]
    test_src, n_http = render(
        args.series, deps | set(mods), actions, sorted(set(reports))
    )
    out = os.path.join(args.out, NAME)
    os.makedirs(os.path.join(out, "tests"), exist_ok=True)
    files = {
        "__manifest__.py": repr(
            {
                "name": "Upgrade smoke",
                "version": args.series + ".1.0.0",
                "depends": depends,
                "installable": True,
                "license": "LGPL-3",
            }
        )
        + "\n",
        "__init__.py": "from . import tests\n",
        os.path.join("tests", "__init__.py"): "from . import test_smoke\n",
        os.path.join("tests", "test_smoke.py"): test_src,
    }
    for rel, text in files.items():
        with open(os.path.join(out, rel), "w", encoding="utf-8") as fh:
            fh.write(text)
    print(out)
    print(
        json.dumps(
            {
                "module_dir": out,
                "http_tests": n_http,
                "report_tests": len(set(reports)),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
