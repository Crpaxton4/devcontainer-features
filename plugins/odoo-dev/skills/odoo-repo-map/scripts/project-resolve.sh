#!/usr/bin/env bash
# project-resolve.sh — one Odoo project (or repo folder) -> everything the rest
# of the delivery suite needs to act on it.
#
# Usage: project-resolve.sh "<project name>|<repo folder>" [--next-after <branch>]
#
# Accepting a repo folder as well as a project name is deliberate: half the
# callers start from an Odoo task (project name) and half from a checkout the
# user is standing in (folder). When a folder maps to SEVERAL projects — one
# repo commonly carries both the delivery project and its upgrade project —
# that is reported as ambiguous rather than resolved to the first hit, because
# the two entries can disagree on odoo_version and on the branch chain.
#
# --next-after <branch> answers "where does work on <branch> get promoted to",
# reading branch_flow. It refuses to answer from an unconfirmed flow only when
# the answer would be production (the last element): being wrong about a staging
# hop costs a re-run, being wrong about production costs a customer.
#
# The argument is an ELEMENT OF THE CHAIN, never an arbitrary branch. For the
# first hop — out of a per-task branch and into whatever that task PR targets —
# pass the reserved token :task, which is what a chain records at index 0 when
# task work starts from a branch cut fresh per task rather than from a shared
# one. That answers the first hop with the same arithmetic as every other hop,
# and it is why the token exists: a chain beginning with an ordinary name (the
# old "dev" convention) made the first hop a question about a branch that was on
# no remote. A branch that is not on the chain is still an error rather than an
# assumed task branch: silently treating an unrecognised name as "must be a task
# branch" would answer a typo'd environment name with default_branch.
#
# Last stdout line: {"project","repo","repo_path","default_branch","odoo_version",
#   "branch_flow":[],"flow_confirmed","remote","next_env","at_production",
#   "release_assignee","release_reviewer"}
#
# release_assignee and release_reviewer are null when the project has recorded
# none. Null means unanswered and the caller has to ask; it is not a licence to
# infer one from commit authorship.
#
# repo_path is the checkout when one is present on this machine, and null when
# there is none. An absolute repo_path recorded on the map entry wins over
# $REPOS_DIR/$repo: the flat tree is a convention, and a bind mount whose folder
# name cannot match the repo field (the Odoo devcontainer's /mnt/extra-addons)
# has no tree to resolve. The origin sniff below reads that same path, so a
# project pinned this way gets its remote filled in rather than left null.
#
# Exit codes: 0 ok | 2 usage | 3 unmapped or ambiguous | 4 flow missing/unusable
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAP="${REPO_MAP_FILE:-${ODOO_DEV_STATE_DIR:-$HOME/.local/share/odoo-dev}/repo-map.json}"

[ $# -ge 1 ] || { echo "usage: project-resolve.sh \"<project|repo>\" [--next-after <branch>]" >&2; exit 2; }
key="$1"; shift
next_after=""
while [ $# -gt 0 ]; do
  case "$1" in
    --next-after) next_after="${2:?}"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

bash "$SCRIPT_DIR/repo-map.sh" validate >/dev/null

# Resolved lazily and non-fatally: metadata is still worth returning on a machine
# where the repos tree is not present (planning, estimating, reviewing a map).
repos_dir="$(REPOS_DIR="${REPOS_DIR:-}" "$SCRIPT_DIR/repos-dir.sh" --raw 2>/dev/null || true)"

entry_json="$(node --input-type=module -e '
  import { readFileSync } from "fs";
  const [file, key] = process.argv.slice(1);
  const data = JSON.parse(readFileSync(file, "utf8"));
  const projects = data.projects;
  if (projects[key]) { console.log(JSON.stringify({ project: key, ...projects[key] })); process.exit(0); }
  const lower = key.toLowerCase();
  const byRepo = Object.entries(projects).filter(([, e]) => (e.repo || "").toLowerCase() === lower);
  if (byRepo.length === 1) {
    const [name, entry] = byRepo[0];
    console.log(JSON.stringify({ project: name, ...entry }));
    process.exit(0);
  }
  if (byRepo.length > 1) {
    console.error("repo \"" + key + "\" maps to several projects — name the project instead:\n  " +
      byRepo.map(([n]) => n).join("\n  "));
    process.exit(3);
  }
  console.error("unmapped project or repo: " + key + "\nknown projects:\n  " + Object.keys(projects).join("\n  ") +
    "\nStop and ask the user; never guess a repo for an unknown project.");
  process.exit(3);
' -- "$MAP" "$key")"

node --input-type=module -e '
  import { existsSync } from "fs";
  import { execFileSync } from "child_process";
  const [entryRaw, reposDir, nextAfter] = process.argv.slice(1);
  const entry = JSON.parse(entryRaw);
  // branch_flow index 0 when task work starts from a per-task branch. Spelled
  // with a colon, which git forbids in a ref name, so it can never be a branch.
  const TASK_BRANCH = ":task";
  const flow = Array.isArray(entry.branch_flow) ? entry.branch_flow : [];
  const confirmed = entry.flow_confirmed === true;

  // An explicit repo_path is where the checkout actually is, so it wins outright
  // over the $REPOS_DIR/$repo convention — including on machines where no repos
  // tree resolves at all and reposDir is empty.
  const repoPath = entry.repo_path || (reposDir ? `${reposDir}/${entry.repo}` : null);
  const repoPresent = Boolean(repoPath) && existsSync(`${repoPath}/.git`);

  // Map first, git second: the map is what a human vouched for, and a checkout
  // can sit on a fork or a stale remote. Gated on repoPresent, so pinning
  // repo_path is what makes this fire where no tree resolves.
  let remote = entry.remote || null;
  if (!remote && repoPresent) {
    try {
      // stderr is ignored rather than inherited: a checkout with no origin — or a
      // repo_path that is not a git repo at all — means a null remote, not a line
      // of git noise beside the JSON this script prints.
      const url = execFileSync("git", ["-C", repoPath, "remote", "get-url", "origin"],
        { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
      const m = url.replace(/\.git$/, "").match(/[:/]([^/:]+\/[^/]+)$/);
      if (m) remote = m[1];
    } catch { /* no origin — remote stays null */ }
  }

  let nextEnv = null;
  let atProduction = null;
  if (nextAfter) {
    if (!flow.length) {
      console.error(`no branch_flow recorded for "${entry.project}" — confirm the environment chain with the user, then: repo-map.sh set-flow "${entry.project}" "${TASK_BRANCH},UAT,main" --flow-confirmed`);
      process.exit(4);
    }
    const i = flow.indexOf(nextAfter);
    if (i === -1) {
      // A task branch is never an element of the chain: the chain names shared
      // environments, plus the one reserved token standing in for whichever
      // branch this task happens to be on. Naming it is what makes the first hop
      // answerable without assuming an unrecognised name must be a task branch.
      const hint = flow[0] === TASK_BRANCH
        ? ` — for the first hop out of a per-task branch pass the reserved placeholder: --next-after ${TASK_BRANCH}`
        : "";
      console.error(`branch "${nextAfter}" is not in branch_flow [${flow.join(", ")}] for "${entry.project}"${hint}`);
      process.exit(4);
    }
    atProduction = i === flow.length - 1;
    nextEnv = atProduction ? null : flow[i + 1];
    // An unconfirmed chain is a guess parsed out of prose. Guessing a staging hop
    // wastes a run; guessing production deploys to a customer.
    if (!confirmed && nextEnv === flow[flow.length - 1]) {
      console.error(`branch_flow for "${entry.project}" is unconfirmed and the next hop (${nextEnv}) is the production element — confirm with the user first: repo-map.sh set-flow "${entry.project}" "${flow.join(",")}" --flow-confirmed`);
      process.exit(4);
    }
  }

  console.log(JSON.stringify({
    project: entry.project,
    repo: entry.repo,
    repo_path: repoPresent ? repoPath : null,
    default_branch: entry.default_branch ?? null,
    odoo_version: entry.odoo_version ?? null,
    branch_flow: flow,
    flow_confirmed: confirmed,
    remote,
    next_env: nextEnv,
    at_production: atProduction,
    release_assignee: entry.release_assignee ?? null,
    release_reviewer: entry.release_reviewer ?? null,
  }));
' -- "$entry_json" "$repos_dir" "$next_after"
