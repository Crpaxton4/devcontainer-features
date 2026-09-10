#!/usr/bin/env python3
"""Deterministically identify which custom addons are OCA-maintained modules.

Ground truth = the module's technical name exists as a top-level directory
containing __manifest__.py in a repository of the OCA GitHub organization,
on at least one requested series branch. Manifest author strings are used
only to corroborate and to flag mismatches — never as the primary signal.

Stdlib + git + gh. Repos are enumerated via the gh CLI (required —
~3 paginated API requests). Branch contents are read from shallow
blobless clones (no API rate limits); listings are cached by branch
tip SHA, clone dirs are deleted after extraction.

Usage:
    python3 oca_check.py [ADDONS_PATH ...] [--series 16.0,17.0,18.0,19.0]
                         [--csv inventory.csv] [-o oca_index.json]
                         [--cache DIR] [--repos repo1,repo2]

ADDONS_PATH defaults to /mnt/extra-addons. With --csv, fills the
"OCA repo" column of a module_inventory.py CSV in place. Exit code 0
on success even when mismatches are reported; exit 2 on a partial scan
(network failures — CSV left untouched, absence unproven).
"""

import argparse
import ast
import csv
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DEFAULT_ADDONS_PATH = "/mnt/extra-addons"
ORG = "OCA"
OCA_AUTHOR = "Odoo Community Association (OCA)"
DEFAULT_SERIES = "16.0,17.0,18.0,19.0"
# OCB: full odoo/odoo fork (core modules at depth 2, huge clone).
# OpenUpgrade: full fork through 13.0; >= 14.0 it ships only the
# openupgrade_framework/openupgrade_scripts tooling addons — migration
# tooling, not client modules, so identifying them adds nothing.
EXCLUDE_REPOS = {"OCB", "OpenUpgrade"}
# Addon repos have a series default branch (e.g. "18.0"); master/main
# defaults mark tooling/infra repos. Some tooling repos DO carry stray
# numeric branches (pylint-odoo 8.0, odoo-test-helper 16.0, ...), so
# this filter is required, not just an optimization.
SERIES_RE = r"^[0-9]+\.[0-9]+$"


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


# network failures during the scan (list.append is thread-safe under the
# GIL); drives the partial-scan guard in main()
NET_FAILURES = []


def addon_repo(repo):
    import re
    return (not repo["archived"]
            and re.match(SERIES_RE, repo["default_branch"] or "")
            and repo["name"] not in EXCLUDE_REPOS)


def list_addon_repos():
    gh = shutil.which("gh")
    if not gh:
        sys.exit("ERROR: gh CLI not found — required for org enumeration "
                 "(devcontainer ships it; install/authenticate gh).")
    res = run([gh, "api", f"orgs/{ORG}/repos", "--paginate",
               "-q", ".[] | [.name, .default_branch, .archived] | @tsv"])
    if res.returncode != 0 or not res.stdout.strip():
        sys.exit(f"ERROR: gh api orgs/{ORG}/repos failed — check gh auth "
                 f"status / network: {res.stderr.strip()}")
    repos = [{"name": n, "default_branch": d, "archived": a == "true"}
             for n, d, a in (ln.split("\t")
                             for ln in res.stdout.splitlines())]
    return sorted(r["name"] for r in repos if addon_repo(r))


def remote_series_heads(repo, series):
    """{series: sha} for the requested series branches, one network call."""
    res = run(["git", "ls-remote", "--heads",
               f"https://github.com/{ORG}/{repo}.git"]
              + [f"refs/heads/{s}" for s in series])
    heads = {}
    if res.returncode != 0:
        NET_FAILURES.append(f"ls-remote {repo}")
        print(f"WARNING: ls-remote failed for {repo}: "
              f"{res.stderr.strip()}", file=sys.stderr)
        return heads
    for line in res.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        heads[ref.rsplit("/", 1)[-1]] = sha
    return heads


def branch_modules(repo, branch, sha, cache_dir):
    """Top-level dirs containing __manifest__.py on repo@branch (cached)."""
    cache_file = cache_dir / f"{repo}@{branch}.json"
    if cache_file.is_file():
        cached = json.loads(cache_file.read_text())
        if cached.get("sha") == sha:
            return cached["modules"]
    clone_dir = cache_dir / f"_clone_{repo}@{branch}"
    shutil.rmtree(clone_dir, ignore_errors=True)
    res = run(["git", "clone", "--quiet", "--depth", "1",
               "--filter=blob:none", "--single-branch", "--branch", branch,
               f"https://github.com/{ORG}/{repo}.git", str(clone_dir)])
    if res.returncode != 0:
        NET_FAILURES.append(f"clone {repo}@{branch}")
        print(f"WARNING: clone failed for {repo}@{branch}: "
              f"{res.stderr.strip()}", file=sys.stderr)
        return []
    res = run(["git", "-C", str(clone_dir), "ls-tree", "-r",
               "--name-only", "HEAD"])
    modules = sorted({p.split("/", 1)[0] for p in res.stdout.splitlines()
                      if p.count("/") == 1
                      and p.endswith("/__manifest__.py")})
    shutil.rmtree(clone_dir, ignore_errors=True)
    cache_file.write_text(json.dumps({"sha": sha, "modules": modules}))
    return modules


def local_modules(addons_paths):
    """{technical_name: author string} across the addons dirs."""
    out = {}
    for addons_path in addons_paths:
        for child in sorted(addons_path.iterdir()):
            manifest = child / "__manifest__.py"
            if not (child.is_dir() and manifest.is_file()):
                continue
            try:
                man = ast.literal_eval(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError, SyntaxError):
                man = {}
            author = man.get("author", "")
            if not isinstance(author, str):  # some manifests use a list
                author = ", ".join(map(str, author))
            out[child.name] = author
    return out


def update_csv(csv_path, results, clear_absent):
    """Fill the 'OCA repo' column. clear_absent=False on --repos subset
    scans: a subset proves presence, never absence, so verified cells
    from earlier full scans must survive."""
    # utf-8-sig on both ends so an Excel BOM survives the round-trip
    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames)
        rows = list(reader)
    if "OCA repo" not in fieldnames:
        print(f"ERROR: no 'OCA repo' column in {csv_path} — regenerate the "
              f"CSV with the current module_inventory.py", file=sys.stderr)
        sys.exit(1)
    for row in rows:
        technical = row["Module Name"].split(" (")[0].strip()
        if technical in results and (results[technical] or clear_absent):
            row["OCA repo"] = results[technical]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(
        description="Check which addons exist in the OCA GitHub org.")
    parser.add_argument("addons_paths", type=Path, nargs="*",
                        default=[Path(DEFAULT_ADDONS_PATH)],
                        help="Custom addons directories (default: "
                             f"{DEFAULT_ADDONS_PATH})")
    parser.add_argument("--series", default=DEFAULT_SERIES,
                        help=f"Comma-separated series branches to scan "
                             f"(default: {DEFAULT_SERIES})")
    parser.add_argument("--csv", type=Path,
                        help="module_inventory.py CSV whose 'OCA repo' "
                             "column gets filled in place")
    parser.add_argument("-o", "--output", type=Path,
                        default=Path("oca_index.json"))
    parser.add_argument("--cache", type=Path,
                        default=Path.home() / ".cache" / "oca_check",
                        help="Listing cache dir (default: ~/.cache/oca_check)")
    parser.add_argument("--repos",
                        help="Comma-separated repo names to scan instead of "
                             "the whole org (re-check / testing)")
    args = parser.parse_args()

    addons_paths = [p.resolve() for p in args.addons_paths]
    for path in addons_paths:
        if not path.is_dir():
            parser.error(f"not a directory: {path}")
    series = [s.strip() for s in args.series.split(",") if s.strip()]
    args.cache.mkdir(parents=True, exist_ok=True)

    local = local_modules(addons_paths)
    if not local:
        print(f"No modules found in {', '.join(map(str, addons_paths))}",
              file=sys.stderr)
        sys.exit(1)

    if args.repos:
        repos = [r.strip() for r in args.repos.split(",") if r.strip()]
    else:
        repos = list_addon_repos()
    print(f"{len(local)} local modules; scanning {len(repos)} {ORG} repos "
          f"on series {', '.join(series)}", file=sys.stderr)

    # module -> {repo -> [series...]} across the whole org
    def scan(repo):
        heads = remote_series_heads(repo, series)
        return repo, {branch: branch_modules(repo, branch, sha, args.cache)
                      for branch, sha in sorted(heads.items())}

    org_index, done = {}, 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for repo, per_branch in pool.map(scan, repos):
            for branch, mods in per_branch.items():
                for mod in mods:
                    org_index.setdefault(mod, {}).setdefault(
                        repo, []).append(branch)
            done += 1
            if done % 25 == 0:
                print(f"  ...{done}/{len(repos)} repos scanned",
                      file=sys.stderr)

    full_scan = not args.repos
    # Partial-scan guard: offline runs or heavy failure rates leave holes
    # in the index; treating a hole as "not in OCA" would emit bogus
    # mismatches and wipe verified CSV cells.
    failure_limit = max(1, len(repos) // 10)
    partial = not org_index or len(NET_FAILURES) > failure_limit

    results, mismatches = {}, []
    for mod, author in local.items():
        claimed = OCA_AUTHOR in author
        hits = org_index.get(mod, {})
        if hits:
            cell = "; ".join(f"{ORG}/{repo} ({','.join(br)})"
                             for repo, br in sorted(hits.items()))
            results[mod] = cell
            if not claimed:
                mismatches.append(
                    f"{mod}: found in {cell} but manifest author lacks "
                    f"'{OCA_AUTHOR}' — possible name collision, verify "
                    f"the code actually matches the OCA module")
        else:
            results[mod] = ""
            # absence is only proven by a full, healthy scan
            if claimed and full_scan and not partial:
                mismatches.append(
                    f"{mod}: manifest author claims OCA but no {ORG} repo "
                    f"ships it on {', '.join(series)} — fork, rename, or "
                    f"externally-maintained module")

    args.output.write_text(json.dumps(
        {"series": series,
         "modules": {m: v for m, v in sorted(results.items()) if v},
         "mismatches": mismatches}, indent=2))
    found = sum(1 for v in results.values() if v)
    print(f"{found}/{len(local)} modules found in {ORG} org -> {args.output}")
    for m in mismatches:
        print(f"MISMATCH {m}")

    if partial:
        print(f"PARTIAL SCAN — {len(NET_FAILURES)} network failures, "
              f"{len(org_index)} org modules indexed: CSV untouched, "
              f"absence unproven. Retry when the network is healthy.",
              file=sys.stderr)
        sys.exit(2)

    if args.csv:
        update_csv(args.csv, results, clear_absent=full_scan)
        print(f"'OCA repo' column updated in {args.csv}")


if __name__ == "__main__":
    main()
