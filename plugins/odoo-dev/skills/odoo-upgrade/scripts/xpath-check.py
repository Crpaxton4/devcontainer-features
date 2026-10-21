#!/usr/bin/env python3
"""
Apply every custom view inherit and Owl t-inherit against the target arches.

Usage:
    xpath-check.py --worktree W --series T --source DIR[,DIR...] [--modules a,b]

--source lists the addons directories of the target series (community and
enterprise). Every view (`<record model="ir.ui.view">` with its `arch` field,
`<template id>`) and every Owl template (`<t t-name>` in `static/src/**/*.xml`)
in them is indexed by xmlid. For each custom view in W with `inherit_id`, the
parent arch is loaded and the specs are applied with
`odoo.tools.template_inheritance.apply_inheritance_specs`, each spec wrapped in
a `<data>` root, cumulatively on one copy of the parent arch. The parent is
the base arch of the xmlid: a chain of inherit_id inside the sources is not
resolved. A parent that is a custom view in W is found there.

Owl: for each `t-inherit="module.template"` with `t-inherit-mode="extension"`
the specs are applied to the parent `t-name` template. This is a STATIC
APPROXIMATION: the browser applies the same xpaths at run time, and this
check does not run the Owl compiler.

XML is parsed with lxml.etree when it can be imported, else with
xml.etree.ElementTree. Odoo's function needs lxml elements. The offline test
stub of `odoo.tools.template_inheritance` accepts ElementTree elements.

stdout: one JSON line
    {"series", "checked", "missing", "rows": [{"view", "module", "xpath",
     "status": "ok"|"missing", "error"}]}

Exit: 0 all ok, 1 any miss, 2 usage error or `odoo.tools.template_inheritance`
cannot be imported (run inside the target-series devcontainer).
"""

import argparse
import copy
import json
import os
import sys

try:
    from lxml import etree as ET
except ImportError:  # pragma: no cover - depends on the host
    import xml.etree.ElementTree as ET


def parse(path):
    try:
        return ET.parse(path).getroot()
    except (ET.ParseError, OSError, ValueError):
        return None


def xml_files(root):
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "tests")]
        for name in sorted(files):
            if name.endswith(".xml"):
                yield os.path.join(base, name)


def modules_in(addons_dir):
    for name in sorted(os.listdir(addons_dir)):
        path = os.path.join(addons_dir, name)
        if os.path.isfile(os.path.join(path, "__manifest__.py")):
            yield name, path


def is_owl(path):
    return os.sep + "static" + os.sep + "src" + os.sep in path


def qualify(module, ref):
    return ref if "." in ref else module + "." + ref


def arch_of(record):
    for field in record.findall("field"):
        if field.get("name") == "arch":
            kids = [k for k in field if isinstance(k.tag, str)]
            return kids[0] if kids else None
    return None


def inherit_ref(record):
    for field in record.findall("field"):
        if field.get("name") == "inherit_id":
            return field.get("ref")
    return None


def walk_units(module, path):
    """Yield (xmlid, parent_ref, element) for each view or template in a file."""
    root = parse(path)
    if root is None:
        return
    if is_owl(path):
        for el in root.iter("t"):
            name = el.get("t-name")
            if name:
                yield name, el.get("t-inherit"), el
            elif el.get("t-inherit"):
                yield None, el.get("t-inherit"), el
        return
    for rec in root.iter("record"):
        if rec.get("model") == "ir.ui.view" and rec.get("id"):
            yield qualify(module, rec.get("id")), inherit_ref(rec), arch_of(rec)
    for tpl in root.iter("template"):
        if tpl.get("id"):
            yield qualify(module, tpl.get("id")), tpl.get("inherit_id"), tpl


def index_tree(addons_dir, only=None):
    """Return {xmlid: element} for base views and templates."""
    found = {}
    for module, mpath in modules_in(addons_dir):
        if only is not None and module not in only:
            continue
        for path in xml_files(mpath):
            for xmlid, parent, el in walk_units(module, path):
                if xmlid and el is not None and parent is None:
                    found.setdefault(xmlid, el)
                elif xmlid and el is not None and is_owl(path):
                    found.setdefault(xmlid, el)
    return found


def spec_label(spec):
    if spec.tag == "xpath":
        return spec.get("expr") or "xpath"
    name = spec.get("name")
    return spec.tag + ("[@name='%s']" % name if name else "")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--worktree", required=True)
    ap.add_argument("--series", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--modules")
    args = ap.parse_args()

    if not os.path.isdir(args.worktree):
        print("xpath-check: no such worktree: %s" % args.worktree, file=sys.stderr)
        return 2
    sources = [s for s in args.source.split(",") if s]
    for s in sources:
        if not os.path.isdir(s):
            print("xpath-check: no such source dir: %s" % s, file=sys.stderr)
            return 2
    try:
        from odoo.tools.template_inheritance import apply_inheritance_specs
    except Exception as exc:  # ImportError or an Odoo import side effect
        print(
            "xpath-check: cannot import odoo.tools.template_inheritance (%s). "
            "Run inside the target-series devcontainer." % exc,
            file=sys.stderr,
        )
        return 2

    wanted = set(args.modules.split(",")) if args.modules else None
    index = {}
    for s in sources:
        for k, v in index_tree(s).items():
            index.setdefault(k, v)
    custom = index_tree(args.worktree)

    rows = []
    for module, mpath in modules_in(args.worktree):
        if wanted is not None and module not in wanted:
            continue
        for path in xml_files(mpath):
            for xmlid, parent_ref, el in walk_units(module, path):
                if not parent_ref or el is None:
                    continue
                if is_owl(path):
                    if el.get("t-inherit-mode") != "extension":
                        continue
                    parent_id = parent_ref
                else:
                    parent_id = qualify(module, parent_ref)
                view = xmlid or parent_id
                parent = index.get(parent_id)
                if parent is None:
                    parent = custom.get(parent_id)
                if parent is None:
                    rows.append(
                        {
                            "view": view,
                            "module": module,
                            "xpath": "",
                            "status": "missing",
                            "error": "parent not found: %s" % parent_id,
                        }
                    )
                    continue
                source = copy.deepcopy(parent)
                for spec in [c for c in el if isinstance(c.tag, str)]:
                    data = ET.Element("data")
                    data.append(copy.deepcopy(spec))
                    row = {
                        "view": view,
                        "module": module,
                        "xpath": spec_label(spec),
                        "status": "ok",
                        "error": "",
                    }
                    try:
                        source = apply_inheritance_specs(source, data)
                    except Exception as exc:
                        row["status"] = "missing"
                        row["error"] = str(exc)
                    rows.append(row)

    missing = sum(1 for r in rows if r["status"] == "missing")
    print(
        json.dumps(
            {
                "series": args.series,
                "checked": len(rows),
                "missing": missing,
                "rows": rows,
            }
        )
    )
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
