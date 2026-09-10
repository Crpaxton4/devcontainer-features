#!/usr/bin/env bash
# artifact.sh — the only sanctioned way to write a handoff artifact.
#
# Agents in the odoo-dev chain hand work to each other through append-only JSON
# files in a per-task directory, not through prompt returns. Artifacts survive
# compaction, a session boundary, a killed subagent, and a human taking over
# mid-chain; a JSON blob in a prompt survives none of those.
#
# Usage:
#   artifact.sh put  <artifacts_dir> <stage> <file>   # <file> may be - for stdin
#   artifact.sh get  <artifacts_dir> <stage> [--first|--latest|--rev N]
#   artifact.sh list <artifacts_dir>
#   artifact.sh stages
#
# NEVER OVERWRITES. A second put of the same stage lands at <stage>.2.json, then
# .3.json. This is the point, not a limitation. A chain must be able to recover
# from a red test — the builder fixes and re-runs — so `get` and gate.sh read the
# LATEST revision. What append-only buys is that the recovery is never QUIET: every
# earlier revision stays on disk, `list` shows them all, and gate.sh reports the
# revision count per stage, so "green on the third try" can never read as "green".
#
# Validation is required-field presence plus cheap type checks, plus optional
# per-element checks for array fields, all run before the file is named — a
# malformed artifact never becomes a stage. Writes are atomic
# (tmp in the same dir, then rename), so a killed agent leaves no half file.
#
# Exit codes: 0 ok | 2 usage | 3 unknown stage | 4 invalid payload | 5 nothing to get
set -euo pipefail

die() { echo "artifact.sh: $*" >&2; exit "${2:-2}"; }

SCHEMA='
const SCHEMA = {
  "00-context": {
    required: ["project","repo","default_branch","odoo_version","branch_flow","flow_confirmed"],
    types: { branch_flow: "array", flow_confirmed: "boolean" },
  },
  "05-scope": {
    required: ["prior_art","estimate","acceptance_criteria"],
    types: { acceptance_criteria: "array" },
  },
  "10-env": {
    required: ["existing_work","worktree_ensure","stack_ensure"],
    types: { existing_work: "object", worktree_ensure: "object" },
  },
  "20-build": {
    required: ["worktree","branch","modules","claims","verify_steps","diff_summary"],
    types: { modules: "array", claims: "array", verify_steps: "array" },
  },
  "30-test": {
    required: ["passed","tests_run","tours_declared","tours_run","failures","log_file"],
    types: { passed: "boolean", tests_run: "number", tours_declared: "number",
             tours_run: "number", failures: "array" },
  },
  "35-review": {
    required: ["findings","criteria_results"],
    types: { findings: "array", criteria_results: "array" },
  },
  "40-coderabbit": {
    required: ["status","findings"],
    types: { findings: "array" },
  },
  // A waiver is an auditable refusal to fix: one entry per CodeRabbit finding the
  // gate should stop blocking on, each carrying a reason a human will read. It is
  // its own stage so agent judgement never lands in the same file as tool output,
  // and every element is checked so an empty reason cannot pass as one.
  "45-waiver": {
    required: ["waived"],
    types: { waived: "array" },
    elements: {
      waived: {
        file: { type: "string", nonEmpty: true },
        line: { type: ["number","null"] },
        reason: { type: "string", nonEmpty: true },
      },
    },
  },
  "50-pr": {
    required: ["pr_url","pr_number","draft","base","head","title"],
    types: { draft: "boolean" },
  },
  "60-release": {
    required: ["from","to","prs","unresolved","tasks"],
    types: { prs: "array", unresolved: "array", tasks: "array" },
  },
};
'

case "${1:-}" in
  stages)
    node --input-type=module -e "$SCHEMA
      console.log(Object.keys(SCHEMA).join('\n'));"
    exit 0 ;;
  put|get|list) cmd="$1"; shift ;;
  *) die "usage: artifact.sh put|get|list|stages <artifacts_dir> [stage] [file]" ;;
esac

DIR="${1:-}"; [ -n "$DIR" ] || die "missing <artifacts_dir>"; shift

if [ "$cmd" = list ]; then
  [ -d "$DIR" ] || die "artifacts dir not found: $DIR" 5
  node --input-type=module -e '
    import { readdirSync, readFileSync } from "fs";
    const dir = process.argv[1];
    const files = readdirSync(dir).filter(f => f.endsWith(".json")).sort();
    console.log(JSON.stringify({ dir, artifacts: files.map(f => {
      let ok = true; try { JSON.parse(readFileSync(`${dir}/${f}`, "utf8")); } catch { ok = false; }
      return { file: f, stage: f.replace(/(\.\d+)?\.json$/, ""), parseable: ok };
    }) }));
  ' -- "$DIR"
  exit 0
fi

STAGE="${1:-}"; [ -n "$STAGE" ] || die "missing <stage>"; shift

if [ "$cmd" = get ]; then
  which="${1:---latest}"
  case "$which" in
    --first)  f="$DIR/$STAGE.json" ;;
    --rev)    n="${2:?--rev needs N}"
              f="$DIR/$STAGE.json"; [ "$n" -gt 1 ] && f="$DIR/$STAGE.$n.json" ;;
    --latest) f=""
              n=1
              while :; do
                cand="$DIR/$STAGE.json"; [ "$n" -gt 1 ] && cand="$DIR/$STAGE.$n.json"
                [ -f "$cand" ] || break
                f="$cand"; n=$((n + 1))
              done ;;
    *) die "get: unknown option $which" ;;
  esac
  [ -n "$f" ] && [ -f "$f" ] || die "no artifact for stage $STAGE in $DIR" 5
  cat "$f"
  exit 0
fi

SRC="${1:-}"; [ -n "$SRC" ] || die "missing <file> (use - for stdin)"
mkdir -p "$DIR"

if [ "$SRC" = "-" ]; then
  payload="$(cat)"
else
  [ -f "$SRC" ] || die "payload file not found: $SRC"
  payload="$(cat "$SRC")"
fi

# Validate, pick the target name, and write atomically in one node pass so the
# name can never be chosen from a state that changed before the rename.
node --input-type=module -e "$SCHEMA"'
  import { existsSync, writeFileSync, renameSync } from "fs";
  const [dir, stage, payload] = process.argv.slice(1);

  const spec = SCHEMA[stage];
  if (!spec) {
    console.error(`artifact.sh: unknown stage ${stage}. Known: ${Object.keys(SCHEMA).join(", ")}`);
    process.exit(3);
  }

  let obj;
  try { obj = JSON.parse(payload); }
  catch (e) { console.error(`artifact.sh: payload is not JSON: ${e.message}`); process.exit(4); }
  if (obj === null || typeof obj !== "object" || Array.isArray(obj)) {
    console.error("artifact.sh: payload must be a JSON object"); process.exit(4);
  }

  const problems = [];
  for (const k of spec.required) if (!(k in obj)) problems.push(`missing required field: ${k}`);
  for (const [k, want] of Object.entries(spec.types ?? {})) {
    if (!(k in obj)) continue;
    const got = Array.isArray(obj[k]) ? "array" : obj[k] === null ? "null" : typeof obj[k];
    if (got !== want) problems.push(`field ${k}: expected ${want}, got ${got}`);
  }
  // Optional per-element checks for array fields, so a stage whose value is a list
  // of records can require shape inside the list. Every bad element is reported
  // with its index rather than stopping at the first.
  const kind = v => (Array.isArray(v) ? "array" : v === null ? "null" : typeof v);
  for (const [field, elemSpec] of Object.entries(spec.elements ?? {})) {
    if (!Array.isArray(obj[field])) continue;
    obj[field].forEach((el, i) => {
      const at = `${field}[${i}]`;
      if (kind(el) !== "object") { problems.push(`${at}: expected object, got ${kind(el)}`); return; }
      for (const [k, rule] of Object.entries(elemSpec)) {
        if (!(k in el)) { problems.push(`${at}: missing required field: ${k}`); continue; }
        const want = Array.isArray(rule.type) ? rule.type : [rule.type];
        const got = kind(el[k]);
        if (!want.includes(got)) { problems.push(`${at}.${k}: expected ${want.join(" or ")}, got ${got}`); continue; }
        if (rule.nonEmpty && got === "string" && el[k].trim() === "")
          problems.push(`${at}.${k}: must not be empty`);
      }
    });
  }
  if (problems.length) {
    console.error(`artifact.sh: invalid ${stage} payload\n  ` + problems.join("\n  "));
    process.exit(4);
  }

  // Append-only: never clobber. The first write owns the bare name.
  let target = `${dir}/${stage}.json`, n = 1;
  while (existsSync(target)) { n += 1; target = `${dir}/${stage}.${n}.json`; }

  const tmp = `${dir}/.${stage}.${process.pid}.tmp`;
  writeFileSync(tmp, JSON.stringify(obj, null, 2) + "\n");
  renameSync(tmp, target);

  console.log(JSON.stringify({ stage, file: target, revision: n,
    superseded: n > 1 ? `${dir}/${stage}.json` : null }));
' -- "$DIR" "$STAGE" "$payload"
