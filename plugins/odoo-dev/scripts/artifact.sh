#!/usr/bin/env bash
# artifact.sh — the only sanctioned way to read and write a task's artifact file.
#
# One file per task, four sections. Every stage of the odoo-dev chain reads and
# writes <artifacts_dir>/task.json, and each command touches only its own section:
#
#   plan    written by /odoo-dev:plan after approval  ({"markdown": "..."})
#   build   written by the builder and the upgrader   (worktree, branch, modules,
#                                                      odoo_version, default_branch)
#   test    written by run-tests.sh --put, one entry per module under test.<module>
#   review  written by the tester                     (findings, criteria_results)
#
# The test section is written by the test script itself, never re-typed by an agent:
# that is what lets a reader tell a real run from a hand-assembled one.
#
# Usage:
#   artifact.sh set  <artifacts_dir> <section> <file|->          # whole section
#   artifact.sh set  <artifacts_dir> <section> --json '<payload>' # payload inline
#   artifact.sh set  <artifacts_dir> <section> --key <k> <file|-> # one key only
#   artifact.sh set  <artifacts_dir> plan --text <file>           # {"markdown": <file>}
#   artifact.sh get  <artifacts_dir> [<section> [--key <k>]] [--raw]
#   artifact.sh list <artifacts_dir>
#   artifact.sh sections
#
# <artifacts_dir> may be given as a bare Odoo task id — anything matching ^[0-9]+$ —
# which resolves against the state dir to <state>/tasks/<id>. Anything else is a path
# and is used exactly as given. The rule comes from state-dir.sh.
#
# `set` validates the payload against SCHEMA (JSON object, required keys present and
# not null, cheap type checks), then replaces the section in place. The file holds
# the latest state only. Writes are atomic (tmp in the same dir, then rename), so a
# killed agent leaves no half file.
#
# `get --raw` prints a string value unquoted. On an object with exactly one key whose
# value is a string (the plan section), it prints that string.
#
# Exit codes: 0 ok | 2 usage | 3 unknown section | 4 invalid payload or file
#             5 nothing to get
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=state-dir.sh
. "$HERE/state-dir.sh"

die() { echo "artifact.sh: $*" >&2; exit "${2:-2}"; }

# required: keys that must be present and non-null.
# types:    cheap type checks on keys that are present.
# fields:   documentation only — the optional keys a reader may name. `set` ignores it;
#           validate.sh gate 17 reads it as part of the token allow-list.
# keyed:    the section is a map of entries written one at a time with --key, and
#           `required` applies per entry.
SCHEMA='
const SCHEMA = {
  plan:   { required: ["markdown"], types: { markdown: "string" }, fields: [] },
  build:  { required: ["worktree","branch","modules","odoo_version","default_branch"],
            types: { modules: "array" }, fields: ["claims","diff_summary"] },
  test:   { required: [], types: {}, keyed: true,
            fields: ["passed","status","error","tests_run","tours_declared","tours_run",
                     "failures","log_file","produced_by","odoo_version",
                     "module_target_series"] },
  review: { required: [], types: {}, fields: ["findings","criteria_results"] },
};
'

FILE_NAME="task.json"

case "${1:-}" in
  sections)
    node --input-type=module -e "$SCHEMA
      console.log(Object.keys(SCHEMA).join('\n'));"
    exit 0 ;;
  set|get|list) cmd="$1"; shift ;;
  *) die "usage: artifact.sh set|get|list|sections <artifacts_dir> [section] [options]" ;;
esac

DIR="${1:-}"; [ -n "$DIR" ] || die "missing <artifacts_dir>"; shift
DIR="$(odoo_dev_artifacts_dir "$DIR")"
FILE="$DIR/$FILE_NAME"

# ---- list -------------------------------------------------------------------------
if [ "$cmd" = list ]; then
  [ $# -eq 0 ] || die "list takes only <artifacts_dir>"
  node --input-type=module -e '
    import { existsSync, readFileSync } from "fs";
    const file = process.argv[1];
    let sections = [];
    if (existsSync(file)) {
      let obj = {};
      try { obj = JSON.parse(readFileSync(file, "utf8")); } catch { obj = {}; }
      if (obj && typeof obj === "object" && !Array.isArray(obj)) {
        sections = Object.entries(obj).map(([name, v]) => ({
          name, keys: v && typeof v === "object" && !Array.isArray(v) ? Object.keys(v) : [],
        }));
      }
    }
    console.log(JSON.stringify({ file, sections }));
  ' -- "$FILE"
  exit 0
fi

# ---- get --------------------------------------------------------------------------
if [ "$cmd" = get ]; then
  section=""; key=""; raw=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --key) key="${2:?--key needs a name}"; shift 2 ;;
      --raw) raw=1; shift ;;
      -*) die "get: unknown option $1" ;;
      *) [ -z "$section" ] || die "get: one section at most"; section="$1"; shift ;;
    esac
  done
  [ -n "$section" ] || [ -z "$key" ] || die "get: --key needs a section"
  [ -f "$FILE" ] || die "no artifact file at $FILE" 5
  node --input-type=module -e '
    import { readFileSync } from "fs";
    const [file, section, key, raw] = process.argv.slice(1);
    let obj;
    try { obj = JSON.parse(readFileSync(file, "utf8")); }
    catch (e) { console.error(`artifact.sh: ${file} is not JSON: ${e.message}`); process.exit(4); }
    let v = obj;
    if (section) {
      if (!(section in obj)) { console.error(`artifact.sh: no ${section} section in ${file}`); process.exit(5); }
      v = obj[section];
      if (key) {
        if (!v || typeof v !== "object" || !(key in v)) {
          console.error(`artifact.sh: no key ${key} in section ${section} of ${file}`); process.exit(5);
        }
        v = v[key];
      }
    }
    if (raw === "1") {
      if (typeof v === "string") { process.stdout.write(v.endsWith("\n") ? v : v + "\n"); process.exit(0); }
      if (v && typeof v === "object" && !Array.isArray(v)) {
        const ks = Object.keys(v);
        if (ks.length === 1 && typeof v[ks[0]] === "string") {
          const s = v[ks[0]]; process.stdout.write(s.endsWith("\n") ? s : s + "\n"); process.exit(0);
        }
      }
    }
    console.log(JSON.stringify(v, null, 2));
  ' -- "$FILE" "$section" "$key" "$raw"
  exit 0
fi

# ---- set --------------------------------------------------------------------------
SECTION="${1:-}"; [ -n "$SECTION" ] || die "missing <section>"; shift

key=""; src=""; payload=""; have_payload=0; text=0
while [ $# -gt 0 ]; do
  case "$1" in
    --key)  key="${2:?--key needs a name}"; shift 2 ;;
    --json) payload="${2:-}"; have_payload=1
            [ -n "$payload" ] || die "--json needs the JSON payload as the next single argument"
            shift 2 ;;
    --text) src="${2:?--text needs a file}"; text=1; shift 2 ;;
    -)      src="-"; shift ;;
    -*)     die "set: unknown option $1" ;;
    *)      [ -z "$src" ] || die "set: one payload at most"; src="$1"; shift ;;
  esac
done

if [ "$have_payload" -eq 0 ]; then
  [ -n "$src" ] || die "missing <file> (use - for stdin, --json for inline, --text for a markdown file)"
  if [ "$src" = "-" ]; then
    payload="$(cat)"
  else
    [ -f "$src" ] || die "payload file not found: $src" 4
    payload="$(cat "$src")"
  fi
fi

mkdir -p "$DIR"

# Validate, merge and write atomically in one node pass.
node --input-type=module -e "$SCHEMA"'
  import { existsSync, readFileSync, writeFileSync, renameSync } from "fs";
  const [file, section, key, payload, text] = process.argv.slice(1);

  const spec = SCHEMA[section];
  if (!spec) {
    console.error(`artifact.sh: unknown section ${section}. Known: ${Object.keys(SCHEMA).join(", ")}`);
    process.exit(3);
  }

  const kind = v => (Array.isArray(v) ? "array" : v === null ? "null" : typeof v);
  let value;
  if (text === "1") {
    if (key) { console.error("artifact.sh: --text and --key do not combine"); process.exit(2); }
    if (section !== "plan") { console.error("artifact.sh: --text is for the plan section only"); process.exit(2); }
    value = { markdown: payload };
  } else {
    try { value = JSON.parse(payload); }
    catch (e) { console.error(`artifact.sh: payload is not JSON: ${e.message}`); process.exit(4); }
    if (kind(value) !== "object") {
      console.error("artifact.sh: payload must be a JSON object"); process.exit(4);
    }
  }

  // The entries `required` and `types` apply to: the whole section, or each entry of
  // a keyed section, or the one entry being written with --key.
  const check = (obj, at) => {
    const problems = [];
    for (const k of spec.required) {
      if (!(k in obj) || obj[k] === null) problems.push(`${at}missing required field: ${k}`);
    }
    for (const [k, want] of Object.entries(spec.types ?? {})) {
      if (!(k in obj)) continue;
      const got = kind(obj[k]);
      // An OPTIONAL typed field may be explicitly null; a REQUIRED one may not.
      if (got === "null" && !spec.required.includes(k)) continue;
      if (got !== want) problems.push(`${at}field ${k}: expected ${want}, got ${got}`);
    }
    return problems;
  };
  let problems = [];
  if (key) {
    problems = check(value, `${key}: `);
  } else if (spec.keyed) {
    for (const [k, v] of Object.entries(value)) {
      if (kind(v) !== "object") { problems.push(`${k}: expected object, got ${kind(v)}`); continue; }
      problems.push(...check(v, `${k}: `));
    }
  } else {
    problems = check(value, "");
  }
  if (problems.length) {
    console.error(`artifact.sh: invalid ${section} payload\n  ` + problems.join("\n  "));
    process.exit(4);
  }

  let doc = {};
  if (existsSync(file)) {
    try { doc = JSON.parse(readFileSync(file, "utf8")); }
    catch (e) { console.error(`artifact.sh: ${file} is not JSON: ${e.message}`); process.exit(4); }
    if (kind(doc) !== "object") { console.error(`artifact.sh: ${file} is not a JSON object`); process.exit(4); }
  }
  if (key) {
    const cur = doc[section];
    doc[section] = kind(cur) === "object" ? cur : {};
    doc[section][key] = value;
  } else {
    doc[section] = value;
  }

  const dir = file.slice(0, file.lastIndexOf("/"));
  const tmp = `${dir}/.task.${process.pid}.tmp`;
  writeFileSync(tmp, JSON.stringify(doc, null, 2) + "\n");
  renameSync(tmp, file);

  console.log(JSON.stringify({ section, key: key || null, file }));
' -- "$FILE" "$SECTION" "$key" "$payload" "$text"
