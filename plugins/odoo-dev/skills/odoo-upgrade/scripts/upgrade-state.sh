#!/usr/bin/env bash
# upgrade-state.sh — the one writer of the `upgrade` section of task.json.
#
# A layer over artifact.sh: it reads and writes through `artifact.sh get|set`, never
# the file. The upgrader calls it after each step, and `next` prints the one action
# the upgrader takes now, so a resume needs no prose rule.
#
# Usage:
#   upgrade-state.sh <artifacts> init --task ID --from S --to T --worktree W --branch B
#                      --base-branch BB [--ssh H] --plan plan.json --preflight pf.json
#                      [--max-minutes N] [--max-install-runs N]
#   upgrade-state.sh <artifacts> next
#   upgrade-state.sh <artifacts> mark <module> status <state>
#   upgrade-state.sh <artifacts> mark <module> verdict|port|tests --json '<object>'
#     mark port needs {commit, manifest}; it also stores an optional
#     upgrade_code_commit {sha, diffstat} (#1168) as modules.<m>.port.upgrade_code_commit.
#   upgrade-state.sh <artifacts> drop <module> --config-step TEXT --prod-uninstall --assumption ID
#                      [--question T] [--reason T] [--alternative T]
#     One transaction: record the drop (status, drop{}, enrich_<module>.json, the
#     assumption), then git rm the module and commit it. A failure after the record
#     restores the module and the record and exits 4.
#   upgrade-state.sh <artifacts> gate workbook --problems N --path P
#   upgrade-state.sh <artifacts> gate install --summary summary.json
#   upgrade-state.sh <artifacts> friction add --tool T --expected E --observed O --did D
#                      --class tooling|porting_fact
#                      [--series-from S --series-to T2 --symptom-grep G --fix F]
#     Appends {tool, expected, observed, did, class, at, porting_fact} to
#     upgrade.friction[]. observed must quote the tool output verbatim (empty: exit 2).
#     A porting_fact needs all four of the bracketed flags, else porting_fact is null.
#   upgrade-state.sh <artifacts> friction list     (the friction[] array as JSON)
#   upgrade-state.sh <artifacts> build
#
# Module status runs inventoried, verdict, ported, dropped, installed, tested in that
# order. A move forward (or to the same state) is legal. A move back exits 4. The
# state `failed` is legal from any state. `mark <m> status ported` checks that
# port.commit exists in the worktree and that the manifest version starts with the
# target series.
#
# `next` prints PREFLIGHT, PORT <module>, GATE workbook, INSTALL, TEST, PR,
# STOP done, STOP blocked_env, STOP killed, STOP budget or STOP failed.
#
# run.budget is {max_minutes, max_install_runs}, both null (no limit) unless init got
# the flags. `STOP budget` means more than max_minutes passed since run.started, or
# install.runs reached max_install_runs and the last install was not green.
# run.kill_file is <artifacts>/STOP: while that file exists, `next` prints
# `STOP killed`, and the unattended guard denies every tool but this script.
# Every mark, gate and drop writes run.updated and modules.<m>.updated (ISO-8601).
# Test-only: UPGRADE_STATE_NOW (an ISO-8601 time) replaces the clock.
# preflight.env_fingerprint_parts is copied from the preflight JSON by init.
#
# Exit codes: 0 ok | 2 usage | 4 invalid transition or failed check | 5 no upgrade section
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARTIFACT_SH="$HERE/../../../scripts/artifact.sh"
# shellcheck source=../../../scripts/state-dir.sh
. "$HERE/../../../scripts/state-dir.sh"

die() { echo "upgrade-state.sh: $*" >&2; exit "${2:-2}"; }

[ $# -ge 2 ] || die "usage: upgrade-state.sh <artifacts> init|next|mark|drop|gate|friction|build ..."
ARTS_ARG="$1"; shift
ARTS="$(odoo_dev_artifacts_dir "$ARTS_ARG")"
CMD="$1"; shift
case "$CMD" in init|next|mark|drop|gate|friction|build) ;; *) die "unknown command $CMD" ;; esac

exec node --input-type=module -e '
import { execFileSync, spawnSync } from "child_process";
import { readFileSync, writeFileSync, existsSync, mkdirSync, rmSync } from "fs";

const [artSh, arts, cmd, ...args] = process.argv.slice(1);
const fail = (msg, code = 2) => { console.error(`upgrade-state.sh: ${msg}`); process.exit(code); };
const now = () => (process.env.UPGRADE_STATE_NOW ? new Date(process.env.UPGRADE_STATE_NOW) : new Date()).toISOString();
const ORDER = ["inventoried", "verdict", "ported", "dropped", "installed", "tested"];

function read(section) {
  const r = spawnSync("bash", [artSh, "get", arts, section], { encoding: "utf8" });
  if (r.status === 0) return JSON.parse(r.stdout);
  if (r.status === 5) return null;
  fail(r.stderr.trim(), 4);
}
function write(section, value) {
  const r = spawnSync("bash", [artSh, "set", arts, section, "--json", JSON.stringify(value)], { encoding: "utf8" });
  if (r.status !== 0) fail(r.stderr.trim(), 4);
}
function opts(list, allowed) {
  const o = {};
  for (let i = 0; i < list.length; i += 2) {
    const k = list[i];
    if (!k.startsWith("--") || !allowed.includes(k.slice(2)) || list[i + 1] === undefined) fail(`bad option ${k}`);
    o[k.slice(2)] = list[i + 1];
  }
  return o;
}
const json = f => JSON.parse(readFileSync(f, "utf8"));
const need = () => { const u = read("upgrade"); if (!u) fail("no upgrade section; run init", 5); return u; };
const touch = (u, m) => { u.run.updated = now(); if (m) u.modules[m].updated = u.run.updated; };
const mod = (u, m) => { if (!u.modules[m]) fail(`unknown module ${m}`); return u.modules[m]; };

if (cmd === "init") {
  const o = opts(args, ["task","from","to","worktree","branch","base-branch","ssh","plan","preflight","max-minutes","max-install-runs"]);
  for (const k of ["task","from","to","worktree","branch","base-branch","plan","preflight"]) if (!o[k]) fail(`init needs --${k}`);
  for (const k of ["max-minutes","max-install-runs"]) if (o[k] !== undefined && !/^[0-9]+$/.test(o[k])) fail(`--${k} needs a whole number`);
  const budget = { max_minutes: o["max-minutes"] === undefined ? null : Number(o["max-minutes"]),
                   max_install_runs: o["max-install-runs"] === undefined ? null : Number(o["max-install-runs"]) };
  const plan = json(o.plan), pf = json(o.preflight), old = read("upgrade");
  if (old) {
    if (old.preflight.env_fingerprint !== pf.env_fingerprint) {
      old.run.state = "blocked_env"; touch(old);
      write("upgrade", old);
      console.log("STOP blocked_env"); process.exit(0);
    }
    old.run.budget = old.run.budget || { max_minutes: null, max_install_runs: null };
    for (const k of Object.keys(budget)) if (budget[k] !== null) old.run.budget[k] = budget[k];
    for (const m of Object.values(old.modules)) m.updated = m.updated || now();
    old.run.step = old.run.state === "running" ? "RESUME" : old.run.step;
    touch(old); write("upgrade", old);
    console.log("resumed"); process.exit(0);
  }
  const t = now(), modules = {};
  for (const m of plan.order) modules[m] = { status: "inventoried", kind: "custom", updated: t };
  write("upgrade", {
    inputs: { task: o.task, from: o.from, to: o.to, ssh_host: o.ssh || null, base_branch: o["base-branch"], worktree: o.worktree, branch: o.branch },
    run: { state: "running", step: "PREFLIGHT", started: t, updated: t, budget, kill_file: `${arts}/STOP` },
    preflight: { ok: !!pf.ok, checks: pf.failures || [], env_fingerprint: pf.env_fingerprint || null, env_fingerprint_parts: pf.env_fingerprint_parts || null },
    plan: { order: plan.order },
    modules,
    install: { runs: 0, last: null },
    workbook: { path: null, problems: null, attempts: 0 },
    assumptions: [], friction: [], pr: { url: null },
  });
  process.exit(0);
}

const u = need();

if (cmd === "next") {
  const mods = u.plan.order.map(n => [n, u.modules[n].status]);
  let out;
  const b = u.run.budget || {}, lastRun = u.install.last;
  const overTime = b.max_minutes != null && Date.parse(now()) - Date.parse(u.run.started) > b.max_minutes * 60000;
  const overInstalls = b.max_install_runs != null && (u.install.runs || 0) >= b.max_install_runs && !(lastRun && lastRun.green);
  if (u.run.state === "blocked_env") out = "STOP blocked_env";
  else if (u.run.kill_file && existsSync(u.run.kill_file)) out = "STOP killed";
  else if (u.run.state === "failed" || mods.some(([, s]) => s === "failed")) out = "STOP failed";
  else if (u.pr && u.pr.url) out = "STOP done";
  else if (overTime || overInstalls) out = "STOP budget";
  else if (!u.preflight.ok) out = "PREFLIGHT";
  else {
    const todo = mods.find(([, s]) => s === "inventoried" || s === "verdict");
    const last = u.install.last;
    if (todo) out = `PORT ${todo[0]}`;
    else if (u.workbook.problems !== 0) out = "GATE workbook";
    else if (!last || !last.green) out = "INSTALL";
    else if (mods.some(([, s]) => s !== "tested")) out = "TEST";
    else out = "PR";
  }
  console.log(out); process.exit(0);
}

if (cmd === "mark") {
  const [m, field, ...rest] = args;
  if (!m || !field) fail("mark needs <module> <field> <value>");
  const rec = mod(u, m);
  if (field === "status") {
    const to = rest[0];
    if (![...ORDER, "failed"].includes(to)) fail(`status must be one of ${ORDER.join("|")}|failed`);
    if (to !== "failed" && ORDER.indexOf(to) < ORDER.indexOf(rec.status))
      fail(`cannot move ${m} from ${rec.status} back to ${to}`, 4);
    if (to === "ported") {
      const c = rec.port && rec.port.commit;
      if (!c) fail(`${m}: no port.commit recorded; mark port first`, 4);
      const g = spawnSync("git", ["-C", u.inputs.worktree, "cat-file", "-e", `${c}^{commit}`]);
      if (g.status !== 0) fail(`${m}: commit ${c} is not in ${u.inputs.worktree}`, 4);
      let text = "";
      try { text = readFileSync(`${u.inputs.worktree}/${m}/__manifest__.py`, "utf8"); }
      catch { fail(`${m}: no __manifest__.py in the worktree`, 4); }
      const v = /["\x27]version["\x27]\s*:\s*["\x27]([^"\x27]+)["\x27]/.exec(text);
      if (!v || !v[1].startsWith(`${u.inputs.to}.`) && v[1] !== u.inputs.to)
        fail(`${m}: manifest version ${v ? v[1] : "missing"} does not start with ${u.inputs.to}`, 4);
    }
    rec.status = to;
  } else if (["verdict", "port", "tests"].includes(field)) {
    if (rest[0] !== "--json" || !rest[1]) fail(`mark ${field} needs --json <object>`);
    let val;
    try { val = JSON.parse(rest[1]); } catch { fail("--json is not JSON", 4); }
    if (field === "port" && !(val && val.commit && val.manifest)) fail("port needs commit and manifest", 4);
    const uc = field === "port" && val ? val.upgrade_code_commit : undefined;
    if (uc !== undefined && !(uc && typeof uc === "object" && !Array.isArray(uc) && uc.sha)) fail("port upgrade_code_commit must be an object with sha", 4);
    rec[field] = val;
  } else fail(`unknown field ${field}`);
  touch(u, m); write("upgrade", u); process.exit(0);
}

if (cmd === "drop") {
  const [m, ...rest] = args;
  if (!m) fail("drop needs <module>");
  const o = {};
  for (let i = 0; i < rest.length; i++) {
    const k = rest[i];
    if (k === "--prod-uninstall") { o["prod-uninstall"] = true; continue; }
    if (!["--config-step", "--assumption", "--question", "--reason", "--alternative"].includes(k) || rest[i + 1] === undefined) fail(`bad option ${k}`);
    o[k.slice(2)] = rest[++i];
  }
  for (const k of ["config-step", "assumption"]) if (!o[k]) fail(`drop needs --${k}`);
  if (!o["prod-uninstall"]) fail("drop needs --prod-uninstall");
  const rec = mod(u, m);
  if (ORDER.indexOf(rec.status) > ORDER.indexOf("dropped") && rec.status !== "failed") fail(`cannot drop ${m} from ${rec.status}`, 4);
  const wt = u.inputs.worktree;
  const enrichPath = `${arts}/inventory/enrich_${m}.json`;
  const ENRICH_KEYS = ["module","purpose","third_party","third_party_link","complexity","upgrade_action","functional_area","native","native_notes","native_evidence","vendor_release","complexity_rationale","upgrade_action_rationale","notes","oca_alt","oca_alt_evidence"];
  const oldEnrich = existsSync(enrichPath) ? readFileSync(enrichPath, "utf8") : null;
  const oldRec = JSON.stringify(rec), oldAssumptions = JSON.stringify(u.assumptions);
  const undo = (step, detail) => {
    spawnSync("git", ["-C", wt, "restore", "--staged", "--worktree", "--", m]);
    for (const k of Object.keys(rec)) delete rec[k];
    Object.assign(rec, JSON.parse(oldRec));
    u.assumptions = JSON.parse(oldAssumptions);
    write("upgrade", u);
    if (oldEnrich === null) rmSync(enrichPath, { force: true }); else writeFileSync(enrichPath, oldEnrich);
    fail(`drop ${m} failed at ${step}: ${detail}`, 4);
  };
  // (1) the record first
  const step = o["config-step"];
  let enrich;
  try { enrich = oldEnrich === null ? Object.fromEntries(ENRICH_KEYS.map(k => [k, ""])) : JSON.parse(oldEnrich); }
  catch { fail(`${enrichPath} is not JSON`, 4); }
  enrich.module = m;
  enrich.upgrade_action = "merge-into-standard";
  enrich.notes = enrich.notes ? `${enrich.notes}\n${step}` : step;
  mkdirSync(`${arts}/inventory`, { recursive: true });
  writeFileSync(enrichPath, JSON.stringify(enrich, null, 2) + "\n");
  if (!u.assumptions.some(a => a.id === o.assumption))
    u.assumptions.push({ id: o.assumption, module: m, question: o.question || null, assumption: step, reason: o.reason || null, alternative: o.alternative || null, answer: null, answered_by: null });
  rec.status = "dropped";
  rec.drop = { config_step: step, prod_uninstall: true, assumption: o.assumption, commit: null };
  touch(u, m); write("upgrade", u);
  // (2) remove and (3) commit
  const rm = spawnSync("git", ["-C", wt, "rm", "-r", "-q", "--", m], { encoding: "utf8" });
  if (rm.status !== 0) undo("git rm", rm.stderr.trim());
  const cm = spawnSync("git", ["-C", wt, "commit", "-q", "-m", `chore(${m}): drop, replaced by standard — ${step.slice(0, 60)}`], { encoding: "utf8" });
  if (cm.status !== 0) undo("git commit", (cm.stderr || cm.stdout).trim());
  rec.drop.commit = execFileSync("git", ["-C", wt, "rev-parse", "HEAD"], { encoding: "utf8" }).trim();
  write("upgrade", u); process.exit(0);
}

if (cmd === "gate") {
  const [which, ...rest] = args;
  if (which === "workbook") {
    const o = opts(rest, ["problems", "path"]);
    if (o.problems === undefined || !o.path || !/^[0-9]+$/.test(o.problems)) fail("gate workbook needs --problems N --path P");
    u.workbook = { path: o.path, problems: Number(o.problems), attempts: (u.workbook.attempts || 0) + 1 };
  } else if (which === "install") {
    const o = opts(rest, ["summary"]);
    if (!o.summary) fail("gate install needs --summary FILE");
    const s = json(o.summary);
    const green = s.passed === true;
    u.install = { runs: (u.install.runs || 0) + 1, last: { summary: s.status || null, green, skipped: s.skipped || [] } };
    if (green) {
      const skip = new Set((s.skipped || []).map(x => x.module));
      for (const [n, rec] of Object.entries(u.modules)) {
        if (rec.kind === "custom" && rec.status === "ported" && !skip.has(n)) { rec.status = "installed"; rec.updated = now(); }
      }
    }
  } else fail("gate needs workbook or install");
  touch(u); write("upgrade", u); process.exit(0);
}

if (cmd === "friction") {
  const [sub, ...rest] = args;
  if (sub === "list") { console.log(JSON.stringify(u.friction || [])); process.exit(0); }
  if (sub !== "add") fail("friction needs add or list");
  const o = opts(rest, ["tool","expected","observed","did","class","series-from","series-to","symptom-grep","fix"]);
  for (const k of ["tool","expected","observed","did"]) {
    if (!o[k] || !o[k].trim()) fail(k === "observed" ? "friction add: observed must quote the tool" + String.fromCharCode(39) + "s output verbatim" : `friction add needs --${k}`);
  }
  if (!["tooling","porting_fact"].includes(o.class)) fail("friction add: class must be tooling or porting_fact");
  const pf = ["series-from","series-to","symptom-grep","fix"];
  const given = pf.filter(k => o[k]);
  if (given.length && given.length < pf.length) fail("friction add: a porting_fact needs --series-from, --series-to, --symptom-grep and --fix");
  u.friction = u.friction || [];
  u.friction.push({ tool: o.tool, expected: o.expected, observed: o.observed, did: o.did, class: o.class, at: now(),
    porting_fact: given.length ? { series_from: o["series-from"], series_to: o["series-to"], symptom_grep: o["symptom-grep"], fix: o.fix } : null });
  touch(u); write("upgrade", u); process.exit(0);
}

if (cmd === "build") {
  const live = Object.entries(u.modules).filter(([, r]) => ["ported", "installed", "tested"].includes(r.status)).map(([n]) => n);
  write("build", { worktree: u.inputs.worktree, branch: u.inputs.branch, modules: live, odoo_version: u.inputs.to, default_branch: u.inputs.base_branch });
  process.exit(0);
}
' -- "$ARTIFACT_SH" "$ARTS" "$CMD" "$@"
