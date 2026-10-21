#!/usr/bin/env python3
"""Merge inventory CSV + agent JSON into the upgrade-preparation workbook.

Inputs, all in one working dir (see references/inventory.md,
references/oca-base-review.md):

    inventory.csv     seeded by module_inventory.py, OCA repo column filled by
                      oca_check.py
    enrich_*.json     phase 1 — per-module enrichment + "<major> native?"
    oca_alt_*.json    phase 2 — per-module "OCA <major> alternative"
    studio.csv        optional — studio_inventory.py output (UI-built artifacts)

Both agent groups are merged FIELD BY FIELD, per module, across every file in
the group: an agent may deliver a partial record, and a second file naming the
same module fills the keys the first left out instead of replacing the record.
A key carrying different non-empty values in two files is a problem line naming
the module, the key and both files; the first file read (files sorted by name)
keeps the cell.

Output: one xlsx with Module Inventory / Inventory Evidence, plus Studio
Inventory when that CSV is present, plus CSV siblings. Everything else the
reader needs (column semantics, writing rules) lives in the skill, not in the
file.

    python3 build_workbook.py --workdir /tmp/inv --target-version 19.0 \
            -o <ARTIFACTS>/inventory/acme-upgrade-19.0.xlsx

Prints a validation report — unenriched modules, conflicting enrichment
files, `TODO-AI` sentinels in a delivered column, over-long cells, and
inventory cells outside their documented vocabulary (Functional area, native
verdict, OCA repo, OCA alternative). Read it: it is the only check that the
fan-out agents followed the briefs. `problems: 0` means complete AND
schema-valid: a column still carrying the sentinel is counted, reported and
exits non-zero.
"""

import argparse
import csv
import itertools
import json
import re
import sys
from collections import Counter
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

BASE_COLUMNS = ["Module Name", "Display Name", "Module Purpose",
                "Source version", "LoC", "Dependencies", "Dependency origin",
                "3rd party app?", "3rd party app link", "OCA repo",
                "Complexity/risk", "Upgrade action", "Functional area"]
EVIDENCE_COLUMNS = ["Module", "native? evidence", "OCA alternative evidence",
                    "Vendor release", "Complexity rationale",
                    "Upgrade action rationale", "Notes"]
# The ONE closed functional-area vocabulary: the inventory's "Functional area"
# column is validated against this list.
AREA_ORDER = ["Sales", "CRM", "Purchasing", "Inventory", "Manufacturing",
              "Accounting", "HR", "Website/Portal", "Reporting", "Integration",
              "Technical/Base"]
# "OCA repo": verified org location, or the negative literal. Anything else is
# provenance prose — it belongs in the Evidence sheet, not a client-facing cell.
# The two literals are written by module_inventory.oca_seed (same names there)
# and by odoo-prior-art/scripts/oca_check.py; this is the reader of both.
# Mirrors of the OCA literal contract — owner is the commented OCA_NONE in
# ../../odoo-prior-art/scripts/oca_check.py (sole producer of "already OCA: ");
# spec is ../references/inventory.md:93. Kept in parity by
# tests/module-inventory.test.sh.
OCA_NONE = "none"
OCA_UNVERIFIED = "claimed — run oca_check.py"
OCA_REPO_RE = re.compile(r"^OCA/[A-Za-z0-9._-]+(?: \([^()]*\))?$")
# "OCA <major> alternative": none, an already-OCA statement for a module that
# IS the upstream module, or up to three candidates. The "already OCA: " prefix
# is the contract literal; oca_check.py writes it, this only validates it.
OCA_ALREADY_RE = re.compile(
    r"^already OCA: [A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
OCA_ALT_RE = re.compile(
    r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+ \((?:full|partial)\)$")
STUDIO_COLUMNS = ["kind", "technical_name", "model", "label", "owner_module",
                  "active", "classification", "populated", "detail"]
STUDIO_CLASSIFICATIONS = {"purge", "convert-to-code", "keep-as-data", "review"}
# Blank is a legal value: only field rows answer the populated question at all.
STUDIO_POPULATED = {"", "populated", "empty", "n/a"}
# A view a module ships whose arch was edited in the database is never safe to
# port unread — the classification is part of the finding, not a suggestion.
STUDIO_REVIEW_KINDS = {"view-inline-edit"}
# "<major> native?" is a VERDICT and nothing else: a closed vocabulary can be
# filtered and counted in a spreadsheet, which is the single biggest cost lever
# in the exercise. The explanation lives in "<major> native notes" beside it.
NATIVE_VERDICTS = ("yes", "yes/partial", "partial", "no")
NATIVE_NOTES_MAX = 300
# Every evidence cell is read inside one spreadsheet cell, and it is the
# reader's only audit trail back to the source tree; a runaway entry displaces
# the columns beside it. Same cap as references/inventory.md documents.
EVIDENCE_MAX = 300
# What the seeding script writes when only an agent can answer. In a column the
# workbook presents as an answer it is an unanswered question, not a value.
SENTINEL = "TODO-AI"

HEADER_FONT = Font(bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor="305496")
TOP_WRAP = Alignment(vertical="top", wrap_text=True)


def load_group_json(workdir, pattern):
    """Every entry in the group, tagged with the file it came from.

    The file name is carried because it is the only thing that makes a
    conflict actionable: "two files disagree" is useless without their names.
    """
    merged = []
    for path in sorted(workdir.glob(pattern)):
        for entry in json.loads(path.read_text()):
            merged.append((entry, path.name))
    return merged


def merge_group_json(workdir, pattern, problems):
    """Field-wise merge of one agent group, keyed by module.

    A fan-out writes one file per group, and nothing stops two groups from
    covering the same module — a record-wise merge would keep whichever file
    sorted last and silently drop every key the loser held (evidence keys have
    no seed CSV to fall back on, so they would simply ship empty).

    So: a key set by exactly one file wins; the same value in several files is
    fine; different non-empty values are a problem line naming the module, the
    key and both files, and the first file read keeps the cell. An empty value
    never overwrites a filled one.
    """
    merged, source = {}, {}
    for entry, name in load_group_json(workdir, pattern):
        module = (entry.get("module") or "").strip()
        if not module:
            problems.append(f"{name}: entry with no module key — dropped")
            continue
        record = merged.setdefault(module, {})
        origin = source.setdefault(module, {})
        for key, value in entry.items():
            if value in ("", None):
                continue
            if key not in record or record[key] in ("", None):
                record[key], origin[key] = value, name
            elif record[key] != value:
                problems.append(
                    f"{module}: {origin[key]} and {name} disagree on {key} "
                    f"— keeping {origin[key]}")
    return merged


def style_sheet(sheet, widths, freeze):
    for row in sheet.iter_rows(min_row=1):
        for cell in row:
            cell.alignment = TOP_WRAP
    for cell in sheet[1]:
        cell.font, cell.fill = HEADER_FONT, HEADER_FILL
    for index, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.freeze_panes = freeze
    sheet.auto_filter.ref = sheet.dimensions


def write_csv(path, columns, rows):
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        writer.writerows(rows)


def build_passthrough_sheet(workbook, path, title, columns, widths, problems,
                            validate=None):
    """Fold a flat CSV in as its own sheet, validating what can be validated.

    Studio rows are produced elsewhere (studio_inventory.py). The workbook is
    where they get READ, next to the module inventory they change the cost of,
    so they are copied in rather than regenerated. A missing file is not an
    error: the sheet is optional.

    A headers-only file is a register that was STAGED and is still empty, so
    it gets its sheet, its CSV sibling and its `0` summary line. Only a file
    that is not there at all returns None.
    """
    if not path or not path.exists():
        return None
    rows = []
    with path.open(encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in columns if c not in (reader.fieldnames or [])]
        if missing:
            problems.append(f"{title}: {path.name} is missing column(s) "
                            f"{', '.join(missing)} — sheet skipped")
            return None
        for line_no, row in enumerate(reader, 2):
            if validate:
                for message in validate(row):
                    problems.append(f"{title}: {path.name}:{line_no} {message}")
            rows.append([row.get(c, "") for c in columns])
    sheet = workbook.create_sheet(title)
    sheet.append(columns)
    for row in rows:
        sheet.append(row)
    style_sheet(sheet, widths, "A2")
    return columns, rows


def validate_studio(row):
    classification = (row.get("classification") or "").strip()
    if classification not in STUDIO_CLASSIFICATIONS:
        yield (f"unknown classification {classification!r} "
               f"(expected one of {', '.join(sorted(STUDIO_CLASSIFICATIONS))})")
    if not (row.get("technical_name") or "").strip():
        yield "row has no technical_name"
    populated = (row.get("populated") or "").strip()
    if populated not in STUDIO_POPULATED:
        yield (f"unknown populated value {populated!r} (expected one of "
               f"{', '.join(sorted(v or '(blank)' for v in STUDIO_POPULATED))})")
    kind = (row.get("kind") or "").strip()
    if kind in STUDIO_REVIEW_KINDS and classification != "review":
        yield (f"{kind} row classified {classification!r} — a module view "
               f"edited in the database is a review row")
    if kind in STUDIO_REVIEW_KINDS and not (row.get("owner_module") or "").strip():
        yield f"{kind} row does not name the module whose view was edited"


def validate_area(area):
    """The inventory column answers to one closed list (AREA_ORDER).
    Compound values (`Sales / Reporting`) fail here on purpose: assign exactly
    one area per module."""
    if area not in AREA_ORDER:
        yield (f"Functional area {area!r} is not one of the "
               f"{len(AREA_ORDER)} buckets ({', '.join(AREA_ORDER)}) — "
               f"assign exactly one; secondary areas go in Module Purpose")


def validate_oca_repo(cell):
    """`none`, or `OCA/<repo>` (optionally `(series,…)`), `; `-joined."""
    if cell == OCA_NONE:
        return
    if cell == OCA_UNVERIFIED:
        yield ("OCA repo still carries the offline seed "
               f"{OCA_UNVERIFIED!r} — run oca_check.py before building")
        return
    if not cell:
        yield f"OCA repo is empty — write {OCA_NONE!r} when nothing matched"
        return
    for part in cell.split("; "):
        if not OCA_REPO_RE.match(part):
            yield (f"OCA repo part {part!r} is neither {OCA_NONE!r} nor "
                   f"OCA/<repo> — scan provenance goes to the Evidence sheet")


def validate_oca_alt(cell, column, oca_repo):
    """`none`, `already OCA: <repo>/<module>`, or `<repo>/<module>
    (full|partial)` — up to three, `; `-joined.

    A module that IS the OCA module is not an alternative to itself: when
    the OCA repo column names a verified upstream, `none` is false.

    The sentinel is NOT tolerated here: an unanswered column is reported by
    the sentinel count, which is what makes it exit non-zero.
    """
    if cell == OCA_NONE:
        if OCA_REPO_RE.match(oca_repo):
            yield (f"{column} is {OCA_NONE!r} but OCA repo names {oca_repo} "
                   f"— this module IS the OCA module: "
                   f"already OCA: <repo>/<module>")
        return
    for part in cell.split("; "):
        if not (OCA_ALREADY_RE.match(part) or OCA_ALT_RE.match(part)):
            yield (f"{column} part {part!r} is not {OCA_NONE!r}, "
                   f"'already OCA: <repo>/<module>', or "
                   f"'<repo>/<module> (full|partial)'")


def validate_native(verdict, column, notes_column):
    """The verdict column answers to a closed four-value list, so the sheet can
    be filtered on it and the summary can count it."""
    if verdict not in NATIVE_VERDICTS:
        yield (f"{column} is {verdict!r} — expected exactly one of "
               f"{', '.join(NATIVE_VERDICTS)}; the explanation goes in "
               f"{notes_column!r}")


def check_cap(label, value, cap):
    """One problem line for every documented length cap, in one shape."""
    if len(value) > cap:
        yield (f"{label} is {len(value)} chars (max {cap}): {value[:60]}…")


def build_inventory(workbook, workdir, source, native_column, notes_column,
                    oca_column, problems, review):
    enrich = merge_group_json(workdir, "enrich_*.json", problems)
    alternatives = merge_group_json(workdir, "oca_alt_*.json", problems)
    columns = BASE_COLUMNS + [native_column, notes_column, oca_column]
    # The columns the workbook presents as answers. A sentinel in any of them
    # is an unanswered question shipped as a verdict.
    delivered = ["Module Purpose", "3rd party app?", "Complexity/risk",
                 "Upgrade action", "Functional area", native_column,
                 notes_column, oca_column]
    sentinels = Counter()

    inventory_rows, evidence_rows, by_module = [], [], {}
    for seeded in csv.DictReader(source.open(encoding="utf-8-sig")):
        # Module Name is the technical name and nothing else — it is the key
        # the enrichment JSON joins on, so it is used whole, never split.
        module = seeded["Module Name"].strip()
        enriched = enrich.get(module)
        alternative = alternatives.get(module)
        if not enriched:
            problems.append(f"{module}: no enrichment row (phase 1)")
        if not alternative:
            problems.append(f"{module}: no OCA alternative row (phase 2)")
        enriched, alternative = enriched or {}, alternative or {}

        # `native` is the verdict and nothing else; `native_notes` is the
        # explanation. A cell that is not exactly one verdict is a problem.
        verdict = (enriched.get("native") or SENTINEL).strip()
        native_notes = (enriched.get("native_notes") or "").strip()
        native_evidence = (enriched.get("native_evidence") or "").strip()
        alt_evidence = (alternative.get("oca_alt_evidence") or "").strip()

        row = dict(seeded)
        row["Module Purpose"] = enriched.get("purpose") or row["Module Purpose"]
        row["3rd party app?"] = (enriched.get("third_party")
                                 or row["3rd party app?"])
        row["3rd party app link"] = (enriched.get("third_party_link")
                                     or row["3rd party app link"])
        row["Complexity/risk"] = (enriched.get("complexity")
                                  or row["Complexity/risk"])
        row["Upgrade action"] = (enriched.get("upgrade_action")
                                 or row["Upgrade action"])
        row["Functional area"] = (enriched.get("functional_area")
                                  or row["Functional area"])
        row[native_column] = verdict
        row[notes_column] = native_notes
        row[oca_column] = alternative.get("oca_alt", SENTINEL)

        for column in delivered:
            if (row.get(column) or "").strip() == SENTINEL:
                sentinels[column] += 1
                problems.append(f"{module}: {column} is {SENTINEL}")

        area = (row.get("Functional area") or "").strip()
        oca_repo = (row.get("OCA repo") or "").strip()
        oca_alt = (row.get(oca_column) or "").strip()
        # A sentinel cell has already been reported, once, by the count above;
        # running the vocabulary checks on it would say the same thing twice.
        checks = itertools.chain(
            validate_area(area) if area != SENTINEL else (),
            validate_oca_repo(oca_repo),
            validate_native(verdict, native_column, notes_column)
            if verdict != SENTINEL else (),
            check_cap(notes_column, native_notes, NATIVE_NOTES_MAX),
            check_cap("native? evidence", native_evidence, EVIDENCE_MAX),
            check_cap("OCA alternative evidence", alt_evidence, EVIDENCE_MAX),
            validate_oca_alt(oca_alt, oca_column, oca_repo)
            if oca_alt != SENTINEL else ())
        for message in checks:
            problems.append(f"{module}: {message}")

        inventory_rows.append([row.get(c, "") for c in columns])
        by_module[module] = row

        notes = " | ".join(x for x in [enriched.get("notes", ""),
                                       alternative.get("notes", "")] if x)
        evidence_rows.append([
            module, native_evidence, alt_evidence,
            enriched.get("vendor_release", ""),
            enriched.get("complexity_rationale", ""),
            enriched.get("upgrade_action_rationale", ""), notes])

    sheet = workbook.create_sheet("Module Inventory")
    sheet.append(columns)
    for row in inventory_rows:
        sheet.append(row)
    style_sheet(sheet, [28, 36, 70, 14, 8, 40, 18, 12, 34, 34, 12, 16, 16,
                        14, 60, 50], "B2")

    evidence = workbook.create_sheet("Inventory Evidence")
    evidence.append(EVIDENCE_COLUMNS)
    for row in evidence_rows:
        evidence.append(row)
    style_sheet(evidence, [30, 70, 70, 14, 44, 60, 60], "B2")

    return columns, inventory_rows, evidence_rows, by_module, sentinels


def default_if_present(workdir, name):
    """Optional inputs are picked up when they exist, never demanded."""
    candidate = workdir / name
    return candidate if candidate.exists() else None


def main():
    parser = argparse.ArgumentParser(
        description="Build the upgrade-preparation workbook.")
    parser.add_argument("--workdir", type=Path, required=True,
                        help="Dir holding inventory.csv and the agent JSON")
    parser.add_argument("--inventory", type=Path,
                        help="Seeded inventory CSV "
                             "(default: <workdir>/inventory.csv)")
    parser.add_argument("--target-version", default="19.0",
                        help="Target series, e.g. 19.0 (default: 19.0)")
    parser.add_argument("-o", "--output", type=Path, required=True,
                        help="Output xlsx path")
    parser.add_argument("--studio", type=Path,
                        help="studio_inventory.py CSV "
                             "(default: <workdir>/studio.csv if present)")
    args = parser.parse_args()

    major = args.target_version.split(".")[0]
    native_column = f"{major} native?"
    notes_column = f"{major} native notes"
    oca_column = f"OCA {major} alternative"
    source = args.inventory or args.workdir / "inventory.csv"
    problems, review = [], []

    workbook = Workbook()
    workbook.remove(workbook.active)
    inv_columns, inv_rows, evidence_rows, inventory, sentinels = \
        build_inventory(workbook, args.workdir, source, native_column,
                        notes_column, oca_column, problems, review)

    studio = build_passthrough_sheet(
        workbook, args.studio or default_if_present(args.workdir, "studio.csv"),
        "Studio Inventory", STUDIO_COLUMNS, [18, 34, 24, 30, 20, 8, 18, 12, 40],
        problems, validate_studio)

    # The Studio sheet is appended after the inventory ones; keep the
    # workbook inventory-first.
    order = ["Module Inventory", "Inventory Evidence", "Studio Inventory"]
    workbook._sheets.sort(
        key=lambda ws: order.index(ws.title) if ws.title in order else 99)
    workbook.save(args.output)

    stem = args.output.with_suffix("")
    write_csv(Path(f"{stem}_inventory.csv"), inv_columns, inv_rows)
    write_csv(Path(f"{stem}_evidence.csv"), EVIDENCE_COLUMNS, evidence_rows)
    if studio:
        write_csv(Path(f"{stem}_studio_inventory.csv"), studio[0], studio[1])

    print(f"saved {args.output}: "
          f"{[(ws.title, ws.max_row - 1) for ws in workbook]}")
    if studio:
        print("studio:", dict(Counter(r[STUDIO_COLUMNS.index("classification")]
                                      for r in studio[1])))
    # Per-column sentinel counts: the difference between "schema-valid" and
    # "answered". Printed even when empty, so a clean run says so.
    print("sentinels:", dict(sentinels))
    print(f"problems: {len(problems)}")
    for problem in problems[:80]:
        print("  ", problem)
    if len(problems) > 80:
        print(f"   ... {len(problems) - 80} more")
    print(f"to review (not blocking): {len(review)}")
    for item in review[:40]:
        print("  ", item)
    if len(review) > 40:
        print(f"   ... {len(review) - 40} more")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
