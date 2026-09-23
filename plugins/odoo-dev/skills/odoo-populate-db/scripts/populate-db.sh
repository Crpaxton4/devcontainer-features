#!/usr/bin/env bash
# populate-db.sh — seed a local Odoo database from a named model profile, and
# fail out loud when the population does not finish.
#
# Usage: populate-db.sh --database <db> [--profile business|sales|inventory|accounting|all]
#                       [--size small|medium|large] [--exclude <model>]... [--dry-run]
#
# A thin wrapper around `odoo populate`. It curates the model list, surfaces the
# resolved order BEFORE the run, and reports what actually landed. It is not a
# replacement for the CLI: --size goes straight through, the `_populate_sizes`
# tiers stay the CLI's contract (no custom row-count targets), and there is no
# resume — `odoo populate` initialises registry.populated_models empty on every
# invocation, so there is no partial state a wrapper could pick back up.
#
# WHY THIS EXISTS — `odoo populate` cannot report a failure.
#   odoo/cli/populate.py wraps its whole model loop in a bare `except:`, logs
#   "Something went wrong populating database" with the traceback, and returns
#   normally. The process exits 0. Every model after the failing one is silently
#   skipped and the shell sees success — which is how a 30-minute run ends with
#   most of the database empty and nothing saying so. So this script never trusts
#   the exit code: it reads the log, names the model that raised, prints the
#   traceback that was logged and then swallowed, and exits 4.
#
# WHY THE ORDER IS RESOLVED HERE TOO.
#   `_get_ordered_models` pulls in each model's `_populate_dependencies` behind
#   the scenes, so the set that runs is never the set you asked for. Rather than
#   infer that from the log afterwards, the same walk is done up front through
#   `odoo shell` — against the real registry of the real database, never a table
#   hard-coded in this file — and printed before anything is written.
#
# WHY --exclude ALSO DROPS DEPENDENTS.
#   `--models` does not suppress a dependency: a model reached through some other
#   model's `_populate_dependencies` is populated whether or not it is in the
#   list. So excluding `account.move` while `account.move.line` stays would
#   populate `account.move` anyway and the exclusion would be a lie. Every model
#   that depends on an excluded one is dropped with it, transitively, and each
#   one is named on stderr and in `excluded_dependents`.
#
# One execution mode: this runs INSIDE the stack, where `odoo` and `psql` are on
# PATH. There is deliberately no docker-exec path — a populate run is an act
# against one named long-lived database you already own a shell in, not a
# throwaway a gate creates. Absent tools are exit 2 with the reason, never a
# silent skip.
#
# Last stdout line: {"ok","database","profile","size","dry_run","excluded",
#   "excluded_dependents","excluded_unknown","dependency_added","unavailable",
#   "models":[{"model","table","requested","factories","rows_before","rows_after",
#   "rows_added","seconds"}],"models_planned","models_started",
#   "models_not_started","failed_model","traceback","elapsed_seconds",
#   "exit_code","log_file"}
# Everything human-readable goes to stderr, so `| tail -1` is always the JSON.
#
# Exit codes: 0 every planned model populated | 2 usage or environment
#           | 3 the model order could not be resolved
#           | 4 the run aborted mid-way (odoo's swallowed exception) or odoo failed
set -euo pipefail

usage() {
  echo "usage: populate-db.sh --database <db> [--profile business|sales|inventory|accounting|all]" >&2
  echo "                     [--size small|medium|large] [--exclude <model>]... [--dry-run]" >&2
}

database=""; profile="business"; size="medium"; dry_run=false
excludes=()
while [ $# -gt 0 ]; do
  case "$1" in
    --database|-d) database="${2:?--database needs a database name}"; shift 2 ;;
    --profile)     profile="${2:?--profile needs a profile name}"; shift 2 ;;
    --size)        size="${2:?--size needs small, medium or large}"; shift 2 ;;
    --exclude)     excludes+=("${2:?--exclude needs a model name}"); shift 2 ;;
    --dry-run)     dry_run=true; shift ;;
    -h|--help)     usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

[ -n "$database" ] || { echo "--database is required" >&2; usage; exit 2; }

# The named profiles. Curated model NAMES (fnmatch patterns work too, exactly as
# `odoo populate --models` accepts them); dependencies are resolved from the
# registry on top and are never listed here. `all` is the empty pattern set and
# means "every model that overrides _populate_factories" — the set that can
# actually generate rows, rather than every model in the registry.
case "$profile" in
  business)
    patterns="res.partner,res.users,product.category,product.template,product.product,sale.order,sale.order.line,stock.picking,stock.move,stock.move.line,account.move,account.move.line" ;;
  sales)
    patterns="res.partner,product.category,product.template,product.product,sale.order,sale.order.line" ;;
  inventory)
    patterns="product.category,product.template,product.product,stock.warehouse,stock.location,stock.quant,stock.picking,stock.move,stock.move.line" ;;
  accounting)
    patterns="res.partner,account.account,account.journal,account.move,account.move.line" ;;
  all)
    patterns="" ;;
  *)
    echo "unknown profile: $profile (business, sales, inventory, accounting, all)" >&2; exit 2 ;;
esac

case "$size" in
  small|medium|large) : ;;
  # Not rejected: --size is passed through verbatim and _populate_sizes is the
  # CLI's contract, so a tier added upstream has to keep working here.
  *) echo "WARN unrecognized --size '$size' — passing it through to odoo populate anyway" >&2 ;;
esac

ODOO_BIN="${ODOO_BIN:-odoo}"
PSQL_ROLE="${POPULATE_DB_ROLE:-}"

command -v "$ODOO_BIN" >/dev/null 2>&1 \
  || { echo "'$ODOO_BIN' is not on PATH — run this inside the Odoo stack (odoo-dev:odoo-task-env brings one up)" >&2; exit 2; }
command -v psql >/dev/null 2>&1 \
  || { echo "psql is not on PATH — the row counts come from postgres, so there is nothing to report without it" >&2; exit 2; }

psql_run() { psql ${PSQL_ROLE:+-U "$PSQL_ROLE"} -d "$database" -Atq "$@"; }

psql_run -c 'select 1' >/dev/null 2>&1 \
  || { echo "cannot connect to database '$database' — create it first, or check PGHOST/PGUSER" >&2; exit 2; }

work="$(mktemp -d "${TMPDIR:-/tmp}/populate-db.XXXXXX")"
trap 'rm -rf "$work"' EXIT

# ---- resolve the model order, against the real registry -------------------------
# `odoo shell` execs a script read from stdin against one namespace dict, so `env`
# is in scope. The walk below mirrors _get_ordered_models: post-order over
# _populate_dependencies, so a dependency always precedes what depends on it. It
# is iterative rather than recursive on purpose — a nested function defined inside
# exec() cannot be relied on to see names from the enclosing namespace.
cat > "$work/resolve.py" <<'PY'
import fnmatch
import json
import os

from odoo.models import BaseModel

patterns = [p for p in os.environ.get("POPULATE_PATTERNS", "").split(",") if p]
want_all = not patterns
size = os.environ.get("POPULATE_SIZE", "small")


def has_factories(model):
    """True when the model overrides _populate_factories, i.e. can make rows."""
    try:
        return type(model)._populate_factories is not BaseModel._populate_factories
    except Exception:
        return False


def deps_of(model):
    try:
        return list(getattr(model, "_populate_dependencies", None) or [])
    except Exception:
        return []


try:
    model_names = sorted(env.registry.models)  # noqa: F821  (odoo shell namespace)
except Exception:
    model_names = sorted(env.registry)  # noqa: F821

seeds = []
for name in model_names:
    try:
        model = env[name]  # noqa: F821
    except Exception:
        continue
    if model._abstract or model._transient:
        continue
    if want_all:
        if has_factories(model):
            seeds.append(model)
    elif any(fnmatch.fnmatch(name, pattern) for pattern in patterns):
        seeds.append(model)

ordered, visited = [], set()
for seed in seeds:
    stack = [(seed, False)]
    while stack:
        model, expanded = stack.pop()
        if expanded:
            if model._name not in visited:
                visited.add(model._name)
                ordered.append(model)
            continue
        if model._name in visited:
            continue
        stack.append((model, True))
        for dep in reversed(deps_of(model)):
            try:
                if dep in env and dep not in visited:  # noqa: F821
                    stack.append((env[dep], False))  # noqa: F821
            except Exception:
                continue

resolved = []
for model in ordered:
    resolved.append({
        "model": model._name,
        "table": model._table,
        "requested": want_all or any(
            fnmatch.fnmatch(model._name, pattern) for pattern in patterns
        ),
        "factories": has_factories(model),
        "dependencies": deps_of(model),
        "size_rows": (getattr(model, "_populate_sizes", None) or {}).get(size, 0),
    })

unavailable = [
    pattern
    for pattern in patterns
    if not any(fnmatch.fnmatch(entry["model"], pattern) for entry in resolved)
]
print("##ODOO-POPULATE-RESOLVED##" + json.dumps(
    {"resolved": resolved, "unavailable": unavailable}))
PY

echo "resolving the model order against $database ..." >&2
set +e
POPULATE_PATTERNS="$patterns" POPULATE_SIZE="$size" \
  "$ODOO_BIN" shell -d "$database" --log-level=warn --max-cron-threads=0 \
  <"$work/resolve.py" >"$work/shell.out" 2>"$work/shell.err"
shell_rc=$?
set -e

resolved_line="$(grep -m1 '^##ODOO-POPULATE-RESOLVED##' "$work/shell.out" 2>/dev/null || true)"
if [ -z "$resolved_line" ]; then
  echo "could not resolve the model order (odoo shell exited $shell_rc)" >&2
  tail -20 "$work/shell.err" | sed 's/^/       /' >&2
  exit 3
fi
printf '%s\n' "$resolved_line" | sed 's/^##ODOO-POPULATE-RESOLVED##//' > "$work/resolved.json"

# ---- the plan: exclusions applied, order printed before anything is written ------
if [ "${#excludes[@]}" -gt 0 ]; then
  printf '%s\n' "${excludes[@]}" > "$work/excludes.txt"
else
  : > "$work/excludes.txt"
fi

node -e '
  const fs = require("fs");
  const [resolvedFile, excludeFile, size] = process.argv.slice(1);
  const { resolved, unavailable } = JSON.parse(fs.readFileSync(resolvedFile, "utf8"));
  const excludes = fs.readFileSync(excludeFile, "utf8")
    .split("\n").map((s) => s.trim()).filter(Boolean);
  const names = new Set(resolved.map((m) => m.model));
  const excluded = excludes.filter((e) => names.has(e));
  const excluded_unknown = excludes.filter((e) => !names.has(e));

  // --models never suppresses a dependency: anything reached through some other
  // model gets populated whether or not it is in the list. So an exclusion has to
  // take its dependents with it, transitively, or it does not exclude anything.
  const dropped = new Set(excluded);
  for (let changed = true; changed; ) {
    changed = false;
    for (const m of resolved) {
      if (dropped.has(m.model)) continue;
      if ((m.dependencies || []).some((d) => dropped.has(d))) { dropped.add(m.model); changed = true; }
    }
  }
  const excluded_dependents = [...dropped].filter((n) => !excluded.includes(n));
  const models = resolved.filter((m) => !dropped.has(m.model));

  const w = (s) => process.stderr.write(s + "\n");
  w("");
  w(`resolved model order (${models.length} models, --size ${size}):`);
  models.forEach((m, i) => {
    const tags = [];
    if (!m.requested) tags.push("dependency");
    if (!m.factories) tags.push("no factories, makes nothing");
    if (m.size_rows) tags.push(`~${m.size_rows} rows`);
    w(`  ${String(i + 1).padStart(3)}. ${m.model}${tags.length ? "   [" + tags.join(", ") + "]" : ""}`);
  });
  if (excluded.length) w(`excluded: ${excluded.join(", ")}`);
  if (excluded_dependents.length)
    w(`also dropped, because they depend on an excluded model: ${excluded_dependents.join(", ")}`);
  if (excluded_unknown.length)
    w(`--exclude named models that are not in the resolved order: ${excluded_unknown.join(", ")}`);
  if (unavailable.length)
    w(`asked for but not in this database (module not installed?): ${unavailable.join(", ")}`);
  w("");

  console.log(JSON.stringify({
    models, excluded, excluded_dependents, excluded_unknown, unavailable,
    dependency_added: models.filter((m) => !m.requested).map((m) => m.model),
  }));
' "$work/resolved.json" "$work/excludes.txt" "$size" > "$work/plan.json"

models_csv="$(node -e '
  const plan = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"));
  process.stdout.write(plan.models.map((m) => m.model).join(","));
' "$work/plan.json")"

if [ -z "$models_csv" ]; then
  echo "nothing left to populate after the exclusions" >&2
  exit 2
fi

# ---- the report, in both shapes: human on stderr, JSON as the last stdout line ---
report() { # report <dry_run> <exit-code> <elapsed> <before.tsv> <after.tsv> <log>
  node -e '
    const fs = require("fs");
    const [planFile, database, profile, size, dryRun, rcArg, elapsedArg,
           beforeFile, afterFile, logFile] = process.argv.slice(1);
    const plan = JSON.parse(fs.readFileSync(planFile, "utf8"));
    const read = (f) => { try { return fs.readFileSync(f, "utf8"); } catch { return ""; } };

    const counts = (f) => new Map(read(f).split("\n").filter(Boolean).map((line) => {
      const [model, , count] = line.split("\t");
      return [model, count === "" || count == null ? null : Number(count)];
    }));
    const before = counts(beforeFile), after = counts(afterFile);

    // Odoo stamps every log RECORD; a traceback continuation line carries none.
    const lines = read(logFile).split("\n");
    const STAMPED = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(?:[,.](\d{1,3}))?/;
    const at = (line) => {
      const m = line.match(STAMPED);
      if (!m) return null;
      return Date.parse(`${m[1]}T${m[2]}Z`) + Number(m[3] ?? 0);
    };

    const started = [];
    let errorIndex = -1;
    lines.forEach((line, i) => {
      const m = line.match(/Populating database for model ([A-Za-z0-9_.]+)/);
      if (m) started.push({ model: m[1], at: at(line), index: i });
      if (errorIndex < 0 && /Something went wrong populating database/.test(line)) errorIndex = i;
    });

    let lastAt = null;
    for (let i = lines.length - 1; i >= 0 && lastAt == null; i--) lastAt = at(lines[i]);
    const endAt = errorIndex >= 0 ? (at(lines[errorIndex]) ?? lastAt) : lastAt;

    const seconds = new Map();
    started.forEach((s, i) => {
      const next = i + 1 < started.length ? started[i + 1].at : endAt;
      if (s.at != null && next != null) seconds.set(s.model, Math.max(0, (next - s.at) / 1000));
    });

    // The bare `except:` logs the traceback and then returns normally. The block
    // belonging to it is everything between the ERROR line and the next stamped
    // record; the model that raised is the last one this run announced.
    let traceback = null, failed_model = null;
    if (errorIndex >= 0) {
      const block = [];
      for (let j = errorIndex + 1; j < lines.length && !STAMPED.test(lines[j]); j++) block.push(lines[j]);
      traceback = block.join("\n").trim() || null;
      const priors = started.filter((s) => s.index < errorIndex);
      failed_model = priors.length ? priors[priors.length - 1].model : null;
    }

    const startedNames = new Set(started.map((s) => s.model));
    // Only factory-bearing models are held to "must have started": a model with
    // no _populate_factories can make nothing, so its absence proves nothing.
    const models_not_started = plan.models
      .filter((m) => m.factories && !startedNames.has(m.model)).map((m) => m.model);

    const rc = Number(rcArg);
    const ok = dryRun === "true"
      ? true
      : rc === 0 && errorIndex < 0 && models_not_started.length === 0 && startedNames.size > 0;

    const models = plan.models.map((m) => {
      const b = before.get(m.model) ?? null, a = after.get(m.model) ?? null;
      return {
        model: m.model, table: m.table, requested: m.requested, factories: m.factories,
        rows_before: b, rows_after: a,
        rows_added: b == null || a == null ? null : a - b,
        seconds: seconds.has(m.model) ? Number(seconds.get(m.model).toFixed(1)) : null,
      };
    });

    if (dryRun !== "true") {
      const w = (s) => process.stderr.write(s + "\n");
      w("");
      w(`populated ${database} (profile ${profile}, --size ${size}) in ${elapsedArg}s`);
      w("  model                                rows before   rows after      added   seconds");
      for (const m of models) {
        const n = (v) => (v == null ? "?" : String(v));
        w(`  ${m.model.padEnd(36)}${n(m.rows_before).padStart(11)}${n(m.rows_after).padStart(13)}` +
          `${n(m.rows_added).padStart(11)}${n(m.seconds).padStart(10)}`);
      }
      if (!ok) {
        w("");
        w("POPULATION DID NOT COMPLETE. `odoo populate` swallows this and exits 0.");
        if (failed_model) w(`  failing model: ${failed_model}`);
        if (models_not_started.length)
          w(`  never started: ${models_not_started.join(", ")}`);
        if (rc !== 0) w(`  odoo populate exited ${rc}`);
        if (traceback) { w("  traceback the CLI logged and then discarded:"); w(traceback.replace(/^/gm, "    ")); }
        w(`  full log: ${logFile}`);
        w(`  re-run without the failing model: --exclude ${failed_model || "<model>"}`);
      }
      w("");
    }

    console.log(JSON.stringify({
      ok, database, profile, size, dry_run: dryRun === "true",
      excluded: plan.excluded, excluded_dependents: plan.excluded_dependents,
      excluded_unknown: plan.excluded_unknown, dependency_added: plan.dependency_added,
      unavailable: plan.unavailable, models,
      models_planned: plan.models.length, models_started: startedNames.size,
      models_not_started, failed_model, traceback,
      elapsed_seconds: Number(elapsedArg), exit_code: rc, log_file: logFile || null,
    }));
  ' "$work/plan.json" "$database" "$profile" "$size" "$1" "$2" "$3" "$4" "$5" "$6"
}

if [ "$dry_run" = true ]; then
  echo "--dry-run: nothing was written" >&2
  report true 0 0 /dev/null /dev/null ""
  exit 0
fi

# ---- row counts, before and after ------------------------------------------------
# Rows ADDED is the only number a run can honestly claim: a database is rarely
# empty to begin with, and `odoo populate` hands the ids it created to a caller
# the CLI throws away. Table names come from the registry (model._table) and are
# re-checked against an identifier shape before they reach a query.
count_rows() { # count_rows <plan.json>  ->  <model>\t<table>\t<count>
  local model table count
  while IFS=$'\t' read -r model table; do
    count=""
    if [[ "$table" =~ ^[a-z_][a-z0-9_]*$ ]]; then
      count="$(psql_run -c "select count(*) from \"$table\"" 2>/dev/null || true)"
    fi
    printf '%s\t%s\t%s\n' "$model" "$table" "$count"
  done < <(node -e '
    const plan = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"));
    for (const m of plan.models) console.log(`${m.model}\t${m.table}`);
  ' "$1")
}

count_rows "$work/plan.json" > "$work/before.tsv"

# Kept, not trapped away: the reported path has to outlive this script, because
# the traceback the CLI swallowed is the whole reason anyone opens it.
log="$(mktemp "${TMPDIR:-/tmp}/populate-db.XXXXXX.log")"

echo "populating $database (--size $size) — log: $log" >&2
started_at="$(date +%s)"
set +e
"$ODOO_BIN" populate -d "$database" --size="$size" --models="$models_csv" \
  --log-level=info --max-cron-threads=0 >"$log" 2>&1
rc=$?
set -e
elapsed=$(( $(date +%s) - started_at ))

count_rows "$work/plan.json" > "$work/after.tsv"

report false "$rc" "$elapsed" "$work/before.tsv" "$work/after.tsv" "$log" > "$work/report.json"
cat "$work/report.json"

# The fail-loud rule, and the only reason this wrapper exists: `odoo populate`
# exits 0 after swallowing the exception, so the verdict comes from the log.
ok="$(node -e '
  const fs = require("fs");
  process.stdout.write(String(JSON.parse(fs.readFileSync(process.argv[1], "utf8")).ok));
' "$work/report.json")"
[ "$ok" = true ] || exit 4
