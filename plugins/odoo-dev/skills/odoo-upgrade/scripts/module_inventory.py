#!/usr/bin/env python3
"""Generate a module inventory CSV for Odoo custom addons directories.

Stdlib only — no third-party dependencies. Manifests are parsed with
ast.literal_eval, never imported.

Usage:
    python3 module_inventory.py [ADDONS_PATH ...] [--target-version 19.0]
                                [-o inventory.csv]

ADDONS_PATH defaults to /mnt/extra-addons (the devcontainer custom addons
dir); pass several paths to inventory them into one CSV.

Columns (11):
    Module Name, Module Purpose, Source version, LoC, Dependencies,
    3rd party app?, 3rd party app link, OCA repo, Complexity/risk,
    Upgrade action, Functional area

Dependencies are classified against the devcontainer: bare = core
(odoo/addons), [E] = enterprise (/var/lib/odoo/addons/$ODOO_VERSION),
[C] = local custom (one of the scanned paths), [?] = not found.

"TODO-AI" cell values mark columns the AI must fill or refine by reading
the module code (see references/inventory.md).
"""

import argparse
import ast
import csv
import fnmatch
import importlib.util
import os
import sys
from pathlib import Path

DEFAULT_ADDONS_PATH = "/mnt/extra-addons"
ENTERPRISE_ROOT = Path("/var/lib/odoo/addons")

LOC_EXTENSIONS = {".py", ".xml", ".js", ".css", ".scss", ".csv"}
SKIP_DIR_NAMES = {"__pycache__", ".git", "node_modules"}
# static/lib holds vendored third-party JS; never count it
SKIP_PATH_PARTS = ("static/lib/",)

TODO_AI = "TODO-AI"
OCA_AUTHOR = "Odoo Community Association (OCA)"


def parse_manifest(manifest_path):
    try:
        return ast.literal_eval(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, SyntaxError) as exc:
        print(f"WARNING: cannot parse {manifest_path}: {exc}", file=sys.stderr)
        return None


def iter_modules(addons_path):
    for child in sorted(addons_path.iterdir()):
        manifest = child / "__manifest__.py"
        if child.is_dir() and manifest.is_file():
            yield child, manifest


def module_names(path):
    """Technical names of modules (dirs holding __manifest__.py) under path."""
    if not path.is_dir():
        return set()
    return {child.name for child, _ in iter_modules(path)}


def dep_universe(local_names):
    """(core, enterprise, local) name sets for dependency classification.

    Core is located via find_spec (no odoo import executed); enterprise
    via the devcontainer per-series checkout. Missing sources yield empty
    sets, so unknown deps degrade to [?] instead of crashing.
    """
    core = set()
    spec = importlib.util.find_spec("odoo")
    if spec and spec.submodule_search_locations:
        for loc in spec.submodule_search_locations:
            core |= module_names(Path(loc) / "addons")
    series = os.environ.get("ODOO_VERSION", "")
    enterprise = module_names(ENTERPRISE_ROOT / series) if series else set()
    return core, enterprise, set(local_names)


def annotate_deps(depends, core, enterprise, local):
    out = []
    for dep in depends:
        if dep in core:
            out.append(dep)
        elif dep in enterprise:
            out.append(f"{dep}[E]")
        elif dep in local:
            out.append(f"{dep}[C]")
        else:
            out.append(f"{dep}[?]")
    return "; ".join(out)


def cloc_excluded(rel_str, patterns):
    """Match a module-relative posix path against manifest cloc_exclude
    globs. fnmatch's * crosses path separators, which safely
    over-approximates Odoo's ** glob semantics for exclusion purposes."""
    return any(fnmatch.fnmatch(rel_str, pat) for pat in patterns)


def count_loc(module_dir, manifest):
    patterns = manifest.get("cloc_exclude") or []
    total = 0
    for path in module_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in LOC_EXTENSIONS:
            continue
        rel = path.relative_to(module_dir)
        rel_str = rel.as_posix()
        if any(part in SKIP_DIR_NAMES for part in rel.parts):
            continue
        if any(rel_str.startswith(prefix) for prefix in SKIP_PATH_PARTS):
            continue
        if patterns and cloc_excluded(rel_str, patterns):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        total += sum(1 for line in text.splitlines() if line.strip())
    return total


def module_purpose(manifest):
    summary = (manifest.get("summary") or "").strip()
    if summary:
        return summary
    description = (manifest.get("description") or "").strip()
    if description:
        first_line = next(
            (ln.strip() for ln in description.splitlines() if ln.strip()), "")
        if first_line:
            return first_line
    return TODO_AI


def third_party_flag(manifest):
    """"Yes" when the manifest carries an app-store price key (even 0 —
    free store apps are still third-party). OPL-1 without a price is
    ambiguous: many shops license in-house modules OPL-1, so hand those
    rows to the AI to settle via author/website."""
    if "price" in manifest:
        return "Yes"
    if str(manifest.get("license", "")).upper().startswith("OPL"):
        return TODO_AI
    return "No"


def third_party_link(manifest, technical_name, target_version, purchased):
    website = (manifest.get("website") or "").strip()
    if website:
        return website
    if purchased:
        return (f"https://apps.odoo.com/apps/modules/"
                f"{target_version}/{technical_name}")
    return ""


def oca_seed(manifest):
    """Offline seed only: the author string is a CLAIM, not proof — some
    externally-hosted modules carry it too. oca_check.py replaces this
    cell with the verified OCA org repo (or empties it)."""
    author = manifest.get("author", "")
    if not isinstance(author, str):
        author = ", ".join(map(str, author))
    return "claimed — run oca_check.py" if OCA_AUTHOR in author else ""


def has_surface(module_dir, patterns):
    """True if the module ships files matching any of the glob patterns."""
    return any(next(module_dir.glob(pat), None) is not None
               for pat in patterns)


def seed_complexity(loc, module_dir):
    """Low/Medium/High from LoC plus upgrade-sensitive surface area.

    Custom JS (Owl) and QWeb reports break most across majors, so their
    presence bumps the seed one level. AI may adjust after reading code.
    """
    fragile = has_surface(module_dir, ("static/src/**/*.js",
                                       "reports/*.xml", "report/*.xml"))
    if loc < 300:
        level = 0
    elif loc < 1500:
        level = 1
    else:
        level = 2
    if fragile:
        level += 1
    return ("Low", "Medium", "High")[min(level, 2)]


def build_row(module_dir, manifest, target_version, deps_sets):
    technical_name = module_dir.name
    broken = manifest is None
    if broken:
        manifest = {}

    display_name = (manifest.get("name") or "").strip()
    if display_name and display_name.lower() != technical_name.lower():
        name = f"{technical_name} ({display_name})"
    else:
        name = technical_name

    loc = count_loc(module_dir, manifest)
    flag = third_party_flag(manifest)
    depends = manifest.get("depends") or []

    action = ""
    if manifest.get("installable") is False:
        action = "drop?"

    purpose = module_purpose(manifest)
    if broken:
        purpose = f"{TODO_AI} (manifest unparseable — fix it, read the code)"

    return {
        "Module Name": name,
        "Module Purpose": purpose,
        "Source version": str(manifest.get("version", "")),
        "LoC": loc,
        "Dependencies": annotate_deps(depends, *deps_sets),
        "3rd party app?": flag,
        "3rd party app link": third_party_link(
            manifest, technical_name, target_version, flag == "Yes"),
        "OCA repo": oca_seed(manifest),
        "Complexity/risk": seed_complexity(loc, module_dir),
        "Upgrade action": action,
        "Functional area": TODO_AI,
    }


FIELDNAMES = [
    "Module Name", "Module Purpose", "Source version", "LoC",
    "Dependencies", "3rd party app?", "3rd party app link", "OCA repo",
    "Complexity/risk", "Upgrade action", "Functional area",
]


def main():
    parser = argparse.ArgumentParser(
        description="Generate an Odoo module inventory CSV.")
    parser.add_argument("addons_paths", type=Path, nargs="*",
                        default=[Path(DEFAULT_ADDONS_PATH)],
                        help="Custom addons directories (default: "
                             f"{DEFAULT_ADDONS_PATH})")
    parser.add_argument("--target-version", default="19.0",
                        help="Target Odoo series for app-store links "
                             "(default: 19.0)")
    parser.add_argument("-o", "--output", type=Path,
                        default=Path("inventory.csv"),
                        help="Output CSV path (default: ./inventory.csv)")
    args = parser.parse_args()

    addons_paths = [p.resolve() for p in args.addons_paths]
    for path in addons_paths:
        if not path.is_dir():
            parser.error(f"not a directory: {path}")

    modules = [(module_dir, manifest_path)
               for path in addons_paths
               for module_dir, manifest_path in iter_modules(path)]
    deps_sets = dep_universe(m.name for m, _ in modules)

    rows = []
    for module_dir, manifest_path in modules:
        manifest = parse_manifest(manifest_path)
        rows.append(build_row(module_dir, manifest, args.target_version,
                              deps_sets))

    if not rows:
        print(f"No modules with __manifest__.py found in "
              f"{', '.join(map(str, addons_paths))}", file=sys.stderr)
        sys.exit(1)

    # utf-8-sig so Excel detects UTF-8 and oca_check round-trips the BOM
    with args.output.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    purchased = sum(1 for r in rows if r["3rd party app?"] == "Yes")
    todo = sum(1 for r in rows
               if TODO_AI in r["Module Purpose"]
               or TODO_AI in r["Functional area"])
    print(f"{len(rows)} modules -> {args.output} "
          f"({purchased} purchased 3rd-party, {todo} rows need AI fill)")


if __name__ == "__main__":
    main()
