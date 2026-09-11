#!/usr/bin/env python3
"""Merge inventory CSV + agent JSON into the upgrade-preparation workbook.

Inputs, all in one working dir (see references/inventory.md,
references/oca-base-review.md, references/functional-requirements.md):

    inventory.csv     seeded by module_inventory.py, OCA repo column filled by
                      oca_check.py
    enrich_*.json     phase 1 — per-module enrichment + "<major> native?"
    oca_alt_*.json    phase 2 — per-module "OCA <major> alternative"
    fr_*.json         phase 3 — functional requirements (optional)
    fr_merges.json    optional {drop_tmp_id: {keep, note, requirement}}
    fr_notes.json     optional {tmp_id: "note appended to Notes"}
    studio.csv        optional — studio_inventory.py output (UI-built artifacts)
    tickets.csv       optional — Odoo support register, see references/support-tickets.md

Output: one xlsx with Module Inventory / Inventory Evidence / Functional
Requirements / Traceability, plus Studio and Tickets when those CSVs are
present, plus CSV siblings. Everything else the reader
needs (column semantics, writing rules) lives in the skill, not in the file.

    python3 build_workbook.py --workdir /tmp/inv --target-version 19.0 \
            -o /mnt/extra-addons/acme_upgrade_16_to_19_workbook.xlsx

Prints a validation report — unenriched modules, over-long cells, requirements
with weak or non-"shall" wording, unknown source modules, modules with no
requirement, and near-duplicate requirements across agent groups. Read it: it
is the only check that the fan-out agents followed the briefs.
"""

import argparse
import csv
import itertools
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

BASE_COLUMNS = ["Module Name", "Module Purpose", "Source version", "LoC",
                "Dependencies", "3rd party app?", "3rd party app link",
                "OCA repo", "Complexity/risk", "Upgrade action",
                "Functional area"]
EVIDENCE_COLUMNS = ["Module", "native? evidence", "OCA alternative evidence",
                    "Vendor release", "Complexity rationale",
                    "Upgrade action rationale", "Notes"]
FR_KEYS = {"tmp_id", "requirement", "type", "functional_area", "actor",
           "sources", "evidence", "status_source", "status_note", "handled",
           "handled_by", "handled_notes", "verification", "notes"}
AREA_ORDER = ["Sales", "CRM", "Purchasing", "Inventory", "Manufacturing",
              "Accounting", "HR", "Website/Portal", "Reporting", "Integration",
              "Technical/Base"]
STATUSES = {"active", "dead", "broken"}
STUDIO_COLUMNS = ["kind", "technical_name", "model", "label", "owner_module",
                  "active", "classification", "detail"]
TICKET_COLUMNS = ["id", "date", "subject", "token", "status",
                  "blocking_module", "resolution", "link"]
STUDIO_CLASSIFICATIONS = {"purge", "convert-to-code", "keep-as-data", "review"}
TICKET_STATUSES = {"open", "waiting-odoo", "waiting-us", "resolved", "waived"}
NATIVE_MAX = 50
WEAK_WORDS = re.compile(
    r"\b(should|must|will|may|fast|easy|user-friendly|efficient|appropriate|"
    r"normal|few|most|timely|properly|quickly|reliable|intuitive|seamless|"
    r"robust|as needed|if possible)\b", re.I)

HEADER_FONT = Font(bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor="305496")
TOP_WRAP = Alignment(vertical="top", wrap_text=True)


def load_group_json(workdir, pattern):
    merged = []
    for path in sorted(workdir.glob(pattern)):
        merged.extend(json.loads(path.read_text()))
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

    Studio rows and support tickets are produced elsewhere (studio_inventory.py,
    a human keeping the register). The workbook is where they get READ, next to
    the module inventory they change the cost of, so they are copied in rather
    than regenerated. A missing file is not an error: both sheets are optional.
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
    if not rows:
        return None
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


def validate_ticket(row):
    status = (row.get("status") or "").strip()
    if status not in TICKET_STATUSES:
        yield (f"unknown status {status!r} "
               f"(expected one of {', '.join(sorted(TICKET_STATUSES))})")
    # A ticket without a token cannot be chased: the token is the only
    # identifier the upgrade client prints and the only one support can act on.
    if not (row.get("token") or "").strip():
        yield f"ticket {row.get('id') or '(unnumbered)'} has no upgrade token"
    # sop.md 7.2 makes an unresolved ticket a go-live blocker, and a waiver has
    # to point at the written waiver.
    if status == "waived" and not (row.get("resolution") or "").strip():
        yield (f"ticket {row.get('id') or '(unnumbered)'} is waived with no "
               f"resolution — record the written waiver")


def build_inventory(workbook, workdir, source, native_column, oca_column,
                    problems):
    enrich = {e["module"]: e for e in load_group_json(workdir, "enrich_*.json")}
    alternatives = {e["module"]: e
                    for e in load_group_json(workdir, "oca_alt_*.json")}
    columns = BASE_COLUMNS + [native_column, oca_column]

    inventory_rows, evidence_rows, by_module = [], [], {}
    for seeded in csv.DictReader(source.open(encoding="utf-8-sig")):
        module = seeded["Module Name"].split(" ")[0]
        enriched = enrich.get(module)
        alternative = alternatives.get(module)
        if not enriched:
            problems.append(f"{module}: no enrichment row (phase 1)")
        if not alternative:
            problems.append(f"{module}: no OCA alternative row (phase 2)")
        enriched, alternative = enriched or {}, alternative or {}

        native = enriched.get("native", "TODO-AI")
        if len(native) > NATIVE_MAX:
            problems.append(
                f"{module}: {native_column} is {len(native)} chars "
                f"(max {NATIVE_MAX}): {native}")

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
        row[native_column] = native
        row[oca_column] = alternative.get("oca_alt", "TODO-AI")
        inventory_rows.append([row.get(c, "") for c in columns])
        by_module[module] = row

        notes = " | ".join(x for x in [enriched.get("notes", ""),
                                       alternative.get("notes", "")] if x)
        evidence_rows.append([
            module, enriched.get("native_evidence", ""),
            alternative.get("oca_alt_evidence", ""),
            enriched.get("vendor_release", ""),
            enriched.get("complexity_rationale", ""),
            enriched.get("upgrade_action_rationale", ""), notes])

    sheet = workbook.create_sheet("Module Inventory")
    sheet.append(columns)
    for row in inventory_rows:
        sheet.append(row)
    style_sheet(sheet, [40, 70, 14, 8, 40, 12, 34, 34, 12, 16, 16, 44, 50],
                "B2")

    evidence = workbook.create_sheet("Inventory Evidence")
    evidence.append(EVIDENCE_COLUMNS)
    for row in evidence_rows:
        evidence.append(row)
    style_sheet(evidence, [30, 70, 70, 14, 44, 60, 60], "B2")

    return columns, inventory_rows, evidence_rows, by_module


def load_requirements(workdir, inventory, handled_values, problems):
    requirements = []
    for path in sorted(workdir.glob("fr_*.json")):
        if path.name in {"fr_groups.json", "fr_merges.json", "fr_notes.json"}:
            continue
        for entry in json.loads(path.read_text()):
            tmp_id = entry.get("tmp_id", path.name)
            if set(entry) != FR_KEYS:
                problems.append(f"{tmp_id}: key mismatch "
                                f"{sorted(set(entry) ^ FR_KEYS)}")
            if entry.get("handled") not in handled_values:
                problems.append(f"{tmp_id}: handled={entry.get('handled')!r}")
            if entry.get("status_source") not in STATUSES:
                problems.append(
                    f"{tmp_id}: status={entry.get('status_source')!r}")
            if entry.get("functional_area") not in AREA_ORDER:
                problems.append(
                    f"{tmp_id}: area={entry.get('functional_area')!r}")
            text = entry.get("requirement", "")
            weak = WEAK_WORDS.search(text)
            if weak:
                problems.append(
                    f"{tmp_id}: weak/non-shall word {weak.group(0)!r}: "
                    f"{text[:80]}")
            if not re.search(r"\bshall\b", text):
                problems.append(f'{tmp_id}: no "shall": {text[:80]}')
            for module in entry.get("sources", []):
                if module not in inventory:
                    problems.append(f"{tmp_id}: unknown source {module}")
            requirements.append(entry)
    return requirements


def apply_reconciliation(workdir, requirements):
    by_tmp = {e["tmp_id"]: e for e in requirements}
    merges_path = workdir / "fr_merges.json"
    if merges_path.is_file():
        for drop, spec in json.loads(merges_path.read_text()).items():
            dropped, kept = by_tmp.get(drop), by_tmp.get(spec["keep"])
            if not dropped or not kept:
                continue
            for module in dropped["sources"]:
                if module not in kept["sources"]:
                    kept["sources"].append(module)
            kept["evidence"] = (kept["evidence"] + " | "
                                + dropped["evidence"])[:400]
            kept["notes"] = " | ".join(
                x for x in [kept["notes"], spec.get("note", "")] if x)
            if spec.get("requirement"):
                kept["requirement"] = spec["requirement"]
            requirements.remove(dropped)
    notes_path = workdir / "fr_notes.json"
    if notes_path.is_file():
        for tmp_id, note in json.loads(notes_path.read_text()).items():
            entry = by_tmp.get(tmp_id)
            if entry:
                entry["notes"] = " | ".join(
                    x for x in [entry["notes"], note] if x)


def near_duplicates(requirements, threshold=0.33):
    stop = {"system", "shall", "when", "while", "user", "order", "where"}

    def tokens(text):
        return {w for w in re.findall(r"[a-z]+", text.lower())
                if len(w) > 3 and w not in stop}

    pairs = []
    for left, right in itertools.combinations(requirements, 2):
        if left["tmp_id"][0] == right["tmp_id"][0]:
            continue
        a, b = tokens(left["requirement"]), tokens(right["requirement"])
        if a and b:
            score = len(a & b) / len(a | b)
            if score >= threshold:
                pairs.append((round(score, 2), left["id"], right["id"],
                              left["requirement"][:70],
                              right["requirement"][:70]))
    return sorted(pairs, reverse=True)


def build_requirements(workbook, workdir, inventory, native_column,
                       oca_column, handled_base, problems, review):
    handled_values = {handled_base, "OCA", "no"}
    requirements = load_requirements(workdir, inventory, handled_values,
                                     problems)
    if not requirements:
        return [], [], []

    apply_reconciliation(workdir, requirements)

    requirements.sort(key=lambda e: (
        AREA_ORDER.index(e["functional_area"])
        if e["functional_area"] in AREA_ORDER else 99, e["tmp_id"]))
    for number, entry in enumerate(requirements, 1):
        entry["id"] = f"FR-{number:03d}"

    columns = ["ID", "Requirement", "Type", "Functional area", "Actor",
               "Source customization(s)", "Status in source", "Handled?",
               "Handled by", "Handled notes", "Verification", "Evidence",
               "Notes", "Decision (Keep/Drop/Defer)"]
    rows = [[e["id"], e["requirement"], e["type"], e["functional_area"],
             e["actor"], "; ".join(e["sources"]),
             e["status_source"] + (f" — {e['status_note']}"
                               if e["status_note"] else ""),
             e["handled"], e["handled_by"], e["handled_notes"],
             e["verification"], e["evidence"], e["notes"], ""]
            for e in requirements]

    fills = {handled_base: PatternFill("solid", fgColor="E2EFDA"),
             "OCA": PatternFill("solid", fgColor="DDEBF7"),
             "no": PatternFill("solid", fgColor="FCE4D6")}
    sheet = workbook.create_sheet("Functional Requirements")
    sheet.append(columns)
    for entry, row in zip(requirements, rows):
        sheet.append(row)
        fill = fills.get(entry["handled"])
        if fill:
            sheet.cell(row=sheet.max_row, column=8).fill = fill
        if entry["status_source"] != "active":
            sheet.cell(row=sheet.max_row, column=7).font = Font(
                color="C00000", bold=True)
    style_sheet(sheet, [9, 70, 16, 14, 16, 34, 18, 10, 34, 34, 44, 34, 36, 14],
                "C2")

    by_module = defaultdict(list)
    for entry in requirements:
        for module in entry["sources"]:
            by_module[module].append(entry)
    trace_columns = ["Module", "Requirement IDs", "# reqs",
                     f"# {handled_base}", "# OCA", "# no",
                     "Inventory: Upgrade action", f"Inventory: {native_column}",
                     f"Inventory: {oca_column}"]
    trace_rows = []
    for module in sorted(inventory, key=str.lower):
        entries = by_module.get(module, [])
        counts = Counter(e["handled"] for e in entries)
        trace_rows.append([
            module, ", ".join(e["id"] for e in entries), len(entries),
            counts[handled_base], counts["OCA"], counts["no"],
            inventory[module]["Upgrade action"],
            inventory[module][native_column], inventory[module][oca_column]])
    trace = workbook.create_sheet("Traceability")
    trace.append(trace_columns)
    for row in trace_rows:
        trace.append(row)
    style_sheet(trace, [44, 40, 8, 11, 7, 6, 18, 44, 50], "B2")

    uncovered = sorted(set(inventory) - set(by_module))
    if uncovered:
        problems.append(f"modules with no requirement: {', '.join(uncovered)}")
    for score, left, right, left_text, right_text in near_duplicates(
            requirements):
        review.append(f"near-duplicate {score} {left}/{right}: "
                      f"{left_text} || {right_text}")

    return (columns, rows, requirements), (trace_columns, trace_rows), \
        requirements


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
    parser.add_argument("--tickets", type=Path,
                        help="Odoo support ticket register CSV "
                             "(default: <workdir>/tickets.csv if present)")
    args = parser.parse_args()

    major = args.target_version.split(".")[0]
    native_column = f"{major} native?"
    oca_column = f"OCA {major} alternative"
    handled_base = f"base {major}"
    source = args.inventory or args.workdir / "inventory.csv"
    problems, review = [], []

    workbook = Workbook()
    workbook.remove(workbook.active)
    inv_columns, inv_rows, evidence_rows, inventory = build_inventory(
        workbook, args.workdir, source, native_column, oca_column, problems)
    fr, trace, requirements = build_requirements(
        workbook, args.workdir, inventory, native_column, oca_column,
        handled_base, problems, review)

    studio = build_passthrough_sheet(
        workbook, args.studio or default_if_present(args.workdir, "studio.csv"),
        "Studio", STUDIO_COLUMNS, [14, 34, 24, 30, 20, 8, 18, 40],
        problems, validate_studio)
    tickets = build_passthrough_sheet(
        workbook, args.tickets or default_if_present(args.workdir, "tickets.csv"),
        "Tickets", TICKET_COLUMNS, [10, 12, 52, 26, 14, 18, 40, 34],
        problems, validate_ticket)

    # Requirements sheets are appended after the inventory ones; the workbook
    # reads inventory-first, so reorder to put requirements second.
    order = ["Module Inventory", "Functional Requirements", "Traceability",
             "Inventory Evidence", "Studio", "Tickets"]
    workbook._sheets.sort(
        key=lambda ws: order.index(ws.title) if ws.title in order else 99)
    workbook.save(args.output)

    stem = args.output.with_suffix("")
    write_csv(Path(f"{stem}_inventory.csv"), inv_columns, inv_rows)
    write_csv(Path(f"{stem}_evidence.csv"), EVIDENCE_COLUMNS, evidence_rows)
    if requirements:
        write_csv(Path(f"{stem}_requirements.csv"), fr[0], fr[1])
        write_csv(Path(f"{stem}_traceability.csv"), trace[0], trace[1])
    if studio:
        write_csv(Path(f"{stem}_studio.csv"), studio[0], studio[1])
    if tickets:
        write_csv(Path(f"{stem}_tickets.csv"), tickets[0], tickets[1])

    print(f"saved {args.output}: "
          f"{[(ws.title, ws.max_row - 1) for ws in workbook]}")
    if studio:
        print("studio:", dict(Counter(r[STUDIO_COLUMNS.index("classification")]
                                      for r in studio[1])))
    if tickets:
        open_tickets = [r for r in tickets[1]
                        if r[TICKET_COLUMNS.index("status")] not in ("resolved", "waived")]
        # sop.md 7.2: every ticket resolved or waived before a production run.
        print(f"tickets: {len(tickets[1])} total, {len(open_tickets)} still blocking")
    if requirements:
        print("handled:", dict(Counter(e["handled"] for e in requirements)))
        print("status:", dict(Counter(e["status_source"] for e in requirements)))
        print("area:", dict(Counter(e["functional_area"]
                                    for e in requirements)))
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
