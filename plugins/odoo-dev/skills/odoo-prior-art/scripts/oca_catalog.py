#!/usr/bin/env python3
"""Build a searchable catalog of every OCA module on one series branch.

`oca_check.py` answers "is THIS local module an OCA module?". This script
answers the opposite question — "does ANY OCA module already do what this
customization does?" — by fetching the manifest of every module on the
target-series branch of every OCA addon repo into one greppable CSV.

Reuses the branch listings `oca_check.py` cached (default
~/.cache/oca_check/<repo>@<series>.json); run `oca_check.py --series <target>`
first, or pass --cache at the dir it wrote. Manifests are read through the
GitHub GraphQL API in batches (~60 modules per request) using the `gh` CLI, so
a full 19.0 scan is ~25 requests, and results are cached in the output JSON:
re-runs only fetch modules missing from it.

    python3 oca_catalog.py --series 19.0 -o /tmp/inv/oca19_catalog.csv

CSV columns: repo, module, name, summary, description, depends, license,
installable, development_status.
"""

import argparse
import ast
import json
import subprocess
import sys
import time
from pathlib import Path

BATCH = 60
ORG = "OCA"
CSV_COLUMNS = ["repo", "module", "name", "summary", "description", "depends",
               "license", "installable", "development_status"]


def gql(query, attempts=4):
    for attempt in range(attempts):
        proc = subprocess.run(["gh", "api", "graphql", "-f", f"query={query}"],
                              capture_output=True, text=True)
        if proc.returncode == 0:
            return json.loads(proc.stdout)
        time.sleep(3 * (attempt + 1))
    raise SystemExit(f"graphql failed: {proc.stderr[:500]}")


def first_description_line(description):
    for line in (description or "").splitlines():
        line = line.strip().strip("=").strip()
        if line and not line.startswith("..") and not line.startswith("!["):
            return line
    return ""


def catalog_entry(repo, module, manifest_text):
    entry = {"repo": repo, "module": module}
    if not manifest_text:
        entry["missing"] = True
        return entry
    try:
        manifest = ast.literal_eval(manifest_text)
    except (ValueError, SyntaxError):
        entry["missing"] = True
        return entry
    entry.update({
        "name": manifest.get("name", ""),
        "summary": (manifest.get("summary") or "").strip(),
        "description": first_description_line(manifest.get("description")),
        "depends": ";".join(manifest.get("depends") or []),
        "license": manifest.get("license", ""),
        "installable": manifest.get("installable", True),
        "development_status": manifest.get("development_status", ""),
    })
    return entry


def main():
    parser = argparse.ArgumentParser(
        description="Catalog every OCA module on one series branch.")
    parser.add_argument("--series", default="19.0",
                        help="Target series branch (default: 19.0)")
    parser.add_argument("--cache", type=Path,
                        default=Path.home() / ".cache" / "oca_check",
                        help="Branch-listing cache written by oca_check.py")
    parser.add_argument("-o", "--output", type=Path, required=True,
                        help="Output CSV path (JSON cache written alongside)")
    args = parser.parse_args()

    listings = sorted(args.cache.glob(f"*@{args.series}.json"))
    if not listings:
        raise SystemExit(
            f"No {args.series} listings in {args.cache}. Run: oca_check.py "
            f"--series {args.series} --cache {args.cache}")

    pairs = []
    for listing in listings:
        repo = listing.name.split("@")[0]
        for module in json.loads(listing.read_text()).get("modules", []):
            pairs.append((repo, module))
    print(f"{len(pairs)} modules on {args.series} across "
          f"{len({r for r, _ in pairs})} repos", file=sys.stderr)

    index_path = args.output.with_suffix(".json")
    results = json.loads(index_path.read_text()) if index_path.is_file() else {}
    todo = [(r, m) for r, m in pairs if f"{r}/{m}" not in results]
    print(f"{len(todo)} manifests to fetch", file=sys.stderr)

    for start in range(0, len(todo), BATCH):
        chunk = todo[start:start + BATCH]
        query = " ".join(
            f'r{n}: repository(owner:"{ORG}", name:"{repo}") '
            f'{{ object(expression:"{args.series}:{module}/__manifest__.py") '
            f'{{ ... on Blob {{ text }} }} }}'
            for n, (repo, module) in enumerate(chunk))
        data = gql("query { " + query + " }").get("data") or {}
        for n, (repo, module) in enumerate(chunk):
            blob = ((data.get(f"r{n}") or {}).get("object") or {}).get("text")
            results[f"{repo}/{module}"] = catalog_entry(repo, module, blob)
        index_path.write_text(json.dumps(results))
        print(f"  {min(start + BATCH, len(todo))}/{len(todo)}", file=sys.stderr)

    import csv
    usable = [e for e in results.values() if not e.get("missing")]
    with args.output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS,
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted(usable, key=lambda e: (e["repo"], e["module"])))
    print(f"{len(usable)} modules -> {args.output} "
          f"({len(results) - len(usable)} manifests unreadable)")


if __name__ == "__main__":
    main()
