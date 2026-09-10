# odoo-dev

Odoo consulting and delivery, packaged as one Claude Code plugin: 15 skills, 5
subagents, 5 slash commands, and a fail-closed evidence gate.

The plugin lives at `plugins/odoo-dev/` in the
[devcontainer-features](https://github.com/Crpaxton4/devcontainer-features) monorepo,
whose root [`.claude-plugin/marketplace.json`](../../.claude-plugin/marketplace.json)
is the marketplace that serves it. It was imported as a snapshot of
`Crpaxton4/odoo-dev-claude-plugin` @ `7e9dea0`; that repo's history stayed behind.

Start at **[`odoo-dev:odoo-dev-map`](skills/odoo-dev-map/SKILL.md)** — it routes work
to the skill or agent that owns it, and never does the stage work itself.

---

## Skills

Reference a skill as `odoo-dev:<name>`. Frontmatter names stay bare; the namespace
is derived.

### Delivery — the doer chain

One step per skill, no orchestrator. Run one, several, or all.

| Skill | Does | Use when |
|---|---|---|
| [`odoo-repo-map`](skills/odoo-repo-map/SKILL.md) | Project → repo, base branch, Odoo series, remote, environment chain; `repo-map.sh` also maintains the map itself | You need WHICH repo, WHICH branch, WHICH version — before cutting a branch or opening a PR; or a project must be added to the map or a branch flow corrected |
| [`odoo-prior-art`](skills/odoo-prior-art/SKILL.md) | Does standard Odoo or an OCA module already do it? Verdicts from real greps, never memory | Before quoting, designing, or building anything |
| [`odoo-task-env`](skills/odoo-task-env/SKILL.md) | Existing-work check → worktree on the task branch → running stack | Starting or resuming a task |
| [`odoo-test-run`](skills/odoo-test-run/SKILL.md) | Unit tests **and** browser tours on a throwaway DB; fail-closed evidence | Before any PR; "does it pass?"; a green result that never said how many tests ran |
| [`odoo-code-review`](skills/odoo-code-review/SKILL.md) | Odoo domain lens: ORM anti-patterns, `sudo()`, raw SQL, N+1, access rights, upgrade safety | Reviewing an addon — stacks on top of generic review, does not replace it |
| [`odoo-dev:odoo-pr`](skills/odoo-pr/SKILL.md) | The PR standard: CodeRabbit gate, body, draft self-assigned PR, chatter note | A branch is finished and verified. Follow this rather than calling `gh pr create` |
| [`odoo-release`](skills/odoo-release/SKILL.md) | Manifest → draft release PR → release note on every included task | Promoting one hop up the chain; "what would this merge actually ship?" |

### Consulting

| Skill | Does | Use when |
|---|---|---|
| [`discovery-notes`](skills/discovery-notes/SKILL.md) | Structure discovery: process, actors, volumes, integrations, pains | Discovery call, requirements session, gap analysis |
| [`odoo-quote`](skills/odoo-quote/SKILL.md) | Line-item quote: hours, assumptions, exclusions, risk | "quote this", "price this change request" |
| [`fibonacci-estimate`](skills/fibonacci-estimate/SKILL.md) | Every estimate leaf snaps to the Fibonacci hour ladder (1, 2, 3, 5, 8, 13…) | Estimating, splitting, or re-cutting in hours — even when nobody says "Fibonacci" |
| [`odoo-design-doc`](skills/odoo-design-doc/SKILL.md) | Design doc: models and fields, views, security, data migration, rollout | Speccing a feature or module before build |

### Platform

| Skill | Does | Use when |
|---|---|---|
| [`odoo-devcontainer`](skills/odoo-devcontainer/SKILL.md) | Devcontainer env map, CLI, paths, and ORM/frontend/testing references | Always, inside an Odoo devcontainer |
| [`odoo-upgrade`](skills/odoo-upgrade/SKILL.md) | Port modules 16 → 17 → 18 → 19; full lifecycle SOP; Studio inventory | "upgrade this module", "what breaks?", upgrade estimate or plan |
| [`principles`](skills/principles/SKILL.md) | Engineering principles for design decisions | Any code generation or review |
| [`odoo-dev-map`](skills/odoo-dev-map/SKILL.md) | **The router.** Skill map, agent map, workflows, spawn-prompt template, gate rules | Work spans more than one step, or you need to know who owns it |

---

## Agents

Plain subagents, not Agent Teams — so `skills:` frontmatter preloading works as
documented. Names carry the `odoo-dev-` prefix because plugin agent names are
**not** auto-namespaced and would otherwise collide across plugins.

| Agent | Owns | Writes | Model / effort / maxTurns |
|---|---|---|---|
| [`odoo-dev-scoper`](agents/odoo-dev-scoper.md) | Discovery, prior-art verdict, estimate, design doc. Never touches a repo | `05-scope.json` | opus / high / 30 turns |
| [`odoo-dev-builder`](agents/odoo-dev-builder.md) | One task, one worktree: code, tests, conventional commits | `10-env.json`, `20-build.json` | opus / high / 30 turns |
| [`odoo-dev-tester`](agents/odoo-dev-tester.md) | Independent evidence: tests, tours, Odoo review lens. **Cannot edit code** | `30-test.json`, `35-review.json` | opus / xhigh / 40 turns |
| [`odoo-dev-pr`](agents/odoo-dev-pr.md) | CodeRabbit loop, draft PR, release, chatter notes | `40-coderabbit.json`, `45-waiver.json`, `50-pr.json`, `60-release.json` | sonnet / high / 30 turns |
| [`odoo-dev-upgrader`](agents/odoo-dev-upgrader.md) | Cross-version porting lifecycle, 16 → 17 → 18 → 19 | `10-env.json`, `20-build.json` | opus / xhigh / 40 turns |

The turn cap is the runaway stop, not a budget. `odoo-dev-tester` and
`odoo-dev-upgrader` get 40 because both iterate — the tester re-runs a suite until
it has a countable result, the upgrader works four version hops in sequence — while
the scoper, the builder and the PR agent each make one pass and get 30.

`odoo-dev-tester` carries `disallowedTools: Edit, Write, NotebookEdit`, so all
three editing tools are gone from it. Bash stays, because the agent needs it to
run `artifact.sh` and `run-tests.sh` — and a shell can still write a file. That
gap is now closed structurally: the [`bash-allowlist.sh`](hooks/bash-allowlist.sh)
`PreToolUse` hook lets this one agent run five sanctioned scripts and read-only
`git`, and denies everything else at the tool boundary. So "cannot edit code" is a
property of the harness here, not a promise in a prompt — enforced by that hook,
not by `disallowedTools`, which never covered Bash and still does not.

`odoo-dev-tester` is the single definition of "passes" for **both** delivery and
upgrade, so there is exactly one bar.

### What `skills:` frontmatter actually does

Measured on 2026-09-07 against Claude Code **2.1.247**, by reading the CLI's own
subagent-spawn path rather than by inference. When a plugin agent is spawned the
runtime walks the `skills:` list from its frontmatter, resolves each entry, renders
that skill exactly as the `Skill` tool would, and pushes the result into the
subagent's opening messages. So the whole `SKILL.md` body is already in the agent's
context before it reads its first instruction, and "preloaded in full — do not
re-read" is an accurate thing to tell it. The frontmatter schema in the same build
describes the field as *"Skills preloaded for this agent"*, and the spawn path logs
`[Agent: <type>] Preloaded skill '<name>'` once per entry.

Four details that matter when authoring an agent here:

- **Bare names resolve.** An entry is matched literally first, then as
  `<plugin>:<entry>` using the spawning agent's own plugin prefix, then against any
  skill whose name ends in `:<entry>`. `principles` finds `odoo-dev:principles`, so
  ship bare names.
- **Only `SKILL.md` is injected.** The body arrives prefixed with a single
  `Base directory for this skill: <path>` line and nothing more. Files under
  `references/` and `scripts/` are **not** preloaded — the agent has to read them
  itself, from that base directory.
- **An entry that resolves to nothing is skipped silently.** It produces a
  `warn`-level line in the debug log and no other signal: the agent spawns anyway,
  quietly missing that skill. The same goes for an entry that is not a prompt-based
  skill, is disabled by policy, or is an account-synced skill while skills sync is
  off. That is why `validate.sh` checks every `skills:` entry against a real bundled
  skill directory instead of trusting the runtime to complain.
- **Preloading runs the skill's `!`-prefixed shell commands**, because the render
  path is the ordinary skill render path. Six skills here rely on that
  deliberately; see [Dynamic injection in skill bodies](#dynamic-injection-in-skill-bodies)
  for what those commands are allowed to do, since every one of them sits on the
  critical path of a spawn.

`skills:` is a preload, not a permission — it neither allow-lists nor restricts what
the `Skill` tool can reach. This is runtime behaviour and can change under the
plugin, so re-check it against the CLI version in use before relying on it.

### What a subagent's Bash call actually sees

Measured on the same day, 2026-09-07, against the same build, Claude Code
**2.1.247**, by probing from inside a real plugin subagent's Bash tool calls rather
than by inference. Three facts, and between them they decide how every prompt in
this plugin is written.

- **`CLAUDE_PLUGIN_ROOT` is not set in an agent's Bash environment.** It read back
  as unset. The runtime does substitute it in a plugin **hook** command, which is
  why `hooks/hooks.json` uses it and is correct — but that is a different mechanism,
  applied to the command string the runtime builds for a hook. A Bash tool call is
  not that, and it sees nothing. The asymmetry is the whole trap: the same spelling
  works in one file and expands to nothing in the file next to it.
- **`ODOO_DEV_STATE_DIR` is not set either.** Writing
  `${ODOO_DEV_STATE_DIR:-$HOME/.local/share/odoo-dev}` into a prompt is therefore not
  something an agent can be relied on to resolve to the dispatcher's value. Only a
  shell that runs the expression resolves it, and only within that one call.
- **Shell state does not persist between Bash tool calls.** A variable exported in
  one call read back empty in the next. Every call is a new shell.

Together they mean that an assignment on one line and a use on the next are two
different shells, so `"$ARTIFACT" put "$ARTIFACTS" 30-test out.json` runs as
`put out.json` — the same failure as the bare `artifact.sh put …` it was meant to
fix. The answer here is absolute literal paths: the router resolves them once at
dispatch, writes them into the spawn prompt in full, and every agent body either
spells the path out or names it with an angle-bracket placeholder such as
`<ARTIFACT path from your prompt>`, which cannot be mistaken for something a shell
expands. A `validate.sh` gate fails any agent body that goes back to a variable.

A skill body has exactly one thing it can rely on at runtime: the
`Base directory for this skill:` line injected above it. That is a real absolute
path, which is why every skill here states that its own scripts are at
`<base directory>/scripts` and that the plugin's own scripts are at
`<plugin root>/scripts`, two levels above the base directory.

### Dynamic injection in skill bodies

Six skills open with a short list of live values that the runtime fills in by
running a shell command, written `` !`cmd` `` in the body. The point is situational
awareness the model cannot invent: the projects that are actually in the repo map,
the worktrees that already exist, whether a browser is installed. `odoo-upgrade`
did this first, and it is the reason that skill does not guess at its environment.

| Skill | What it injects |
|---|---|
| [`odoo-upgrade`](skills/odoo-upgrade/SKILL.md) | Source series, custom-addon count and git state, enterprise checkouts, whether `gh` holds a token |
| [`odoo-repo-map`](skills/odoo-repo-map/SKILL.md) | Every project name in the map, so an unmapped project is visible rather than guessed at |
| [`odoo-task-env`](skills/odoo-task-env/SKILL.md) | Worktrees in this checkout, and open pull requests — the early warning against a duplicate branch |
| [`odoo-test-run`](skills/odoo-test-run/SKILL.md) | Browser presence, which predicts a `tours_run: 0` gate failure before a run is spent on it |
| [`odoo-devcontainer`](skills/odoo-devcontainer/SKILL.md) | The Odoo series and any running project stack — together, whether this is the host or the inside of a container |
| [`odoo-pr`](skills/odoo-pr/SKILL.md) | Current branch, the remote's default branch, and whether a pull request is already open for this head |

**These commands run at preload as well as at invocation.** Every one of the six is
named in some agent's `skills:` list, so their injections execute on the critical
path of every spawn of that agent, not only when a human types the skill name.
`odoo-dev-upgrader` carries the most, at seven commands per spawn; then
`odoo-dev-builder` at five, `odoo-dev-pr` at four, `odoo-dev-tester` at three and
`odoo-dev-scoper` at one. The runtime starts them together rather than in sequence,
so what a spawn actually waits for is the slowest one, not the sum — but the sum is
what gets paid for, and four rules follow. All four are load-bearing rather than
stylistic.

- **Fast.** Under a second each, measured, or it does not go in. Anything that can
  touch the network or a socket is wrapped in `timeout 1` — every `gh` call, and
  anything that talks to docker. `gh` in particular will sit and wait on auth
  otherwise.
- **Fail-soft.** Every injection ends in a fallback such as `|| echo "none"`, or a
  more informative sentence where one helps, because **a non-zero exit aborts the
  whole render**. That tail is not decoration; it is what keeps a logged-out `gh` or
  an absent repo map from stopping the agent from spawning at all. Each one here was
  checked against a deliberately broken precondition — no `gh` on `PATH`, `gh`
  logged out, no docker, no repo map, no browser — and exits 0 in every case.
- **Never installs anything.** `browser-ensure.sh` grew a `--check` mode for exactly
  this: it reports what it finds in one line and always exits 0, where `--install`
  pulls about 100 MB. A load path may look; it may not fetch.
- **A space before every marker.** The `!` is only recognised at the start of a line
  or after whitespace. Written flush against other text — `Branch:!` and then the
  command — it is passed through as literal text, and the model then invents a
  plausible-looking value, which is worse than having no injection at all.

Two things that are true of the runtime and worth not re-deriving. Both were read
out of the Claude Code **2.1.247** binary and then confirmed by rendering a probe
skill for real:

- **`allowed-tools` is not required for injections to run.** The renderer checks each
  injected command against the session's ordinary permission context, with the
  skill's `allowed-tools` entries merged in as extra always-allow command rules for
  the duration of that render. Declaring the key only widens what is allowed without
  a prompt; it is not a precondition, which is why `odoo-upgrade` has always worked
  without one, and none of the six declares it. The one case where it earns its
  place is a session whose rules do not already cover a command: the render then
  *asks* rather than failing, and a non-interactive run stalls on the question. If
  that ever happens here, the fix is to declare the sub-commands in `allowed-tools`
  on the skill that owns the injection — not to widen the session.
- **`${CLAUDE_SKILL_DIR}` is substituted before the shell sees the command**, so an
  injection can call the skill's own scripts by absolute path. It is a renderer-level
  text substitution, not an environment variable — a Bash tool call is not rendered,
  so the same spelling expands to nothing there. The skills that use it say so in
  their own bodies.

### Workflows

| Workflow | Chain |
|---|---|
| Scoping and quoting | `scoper` → `05-scope.json` → `builder` |
| Task delivery | `builder` → `odoo-dev-tester` → `gate.sh --for pr` → `odoo-dev-pr` |
| Version upgrade | `upgrader` → `odoo-dev-tester` → `gate.sh --for pr` → `odoo-dev-pr` |
| Release | `odoo-dev-pr` → `gate.sh --for release` |

---

## Commands

One slash command per agent, and two routes through the one that opens pull
requests. Each command resolves the paths the agent needs, writes them into the
spawn prompt as absolute literals, and dispatches that agent — so the hand-filled
spawn template in the router is now the fallback rather than the normal path.

| Type | Dispatches | Resolves for you |
|---|---|---|
| [`/odoo-dev:quote <task-id> [request]`](commands/quote.md) | `odoo-dev-scoper` | Artifacts dir (created if new), `artifact.sh`, `gate.sh` |
| [`/odoo-dev:task <task-id>`](commands/task.md) | `odoo-dev-builder` | Artifacts dir (created if new), `artifact.sh`, `gate.sh` |
| [`/odoo-dev:upgrade <task-id> <target series>`](commands/upgrade.md) | `odoo-dev-upgrader` | Artifacts dir (created if new), `artifact.sh`, `gate.sh` |
| [`/odoo-dev:test <task-id>`](commands/test.md) | `odoo-dev-tester` | Artifacts dir (must already exist), `artifact.sh`, `gate.sh` |
| [`/odoo-dev:pr <task-id>`](commands/pr.md) | `odoo-dev-pr` | Artifacts dir (must already exist), `artifact.sh`, `gate.sh`, **and runs `gate.sh --for pr` first** |
| [`/odoo-dev:pr release <from-branch> <to-branch>`](commands/pr.md) | `odoo-dev-pr` | Release dir `releases/<from>-to-<to>` (created if new), `artifact.sh`, `gate.sh`, **and deliberately runs no pre-gate** |

**No command chains to another.** You drive the sequence, one environment per task:
you type the next command when you have read what the last one returned and run the
gate over it. A chain that dispatched its own successor would decide on your behalf
that the evidence was good enough, which is the one judgement this plugin never
delegates. The command files say the same thing to the agent they dispatch, and no
command names another.

### What a command actually does

The frontmatter carries `context: fork` plus `agent: odoo-dev:<agent-name>`, which
runs the command body as the prompt of that subagent. The body is written as that
prompt, not as instructions to the session that typed it.

Path resolution happens in the body through `` !`command` `` injection, which runs
in the **typing session's** shell before the fork — so `$HOME` and
`$ODOO_DEV_STATE_DIR` resolve there, and `${CLAUDE_PLUGIN_ROOT}` is substituted by
the runtime. The subagent receives finished absolute paths and never has to expand
anything, which is the whole point: a variable name in a spawn prompt expands to
nothing in a subagent's Bash call.

Three properties follow from that mechanism and are worth keeping:

- **A failed injection aborts the command, so no injection is allowed to fail.** A
  non-zero exit inside `` !`…` `` throws before the fork, so the agent is never
  dispatched and the shell's own output becomes the error the typist sees. Guarding
  the task id with `test -n` was not enough: it asks whether an argument was typed,
  not whether it is a task id, so `/odoo-dev:pr Create a release PR from UAT to
  main` came back as a raw `ls: cannot access '.../tasks/Create'`, and the `mkdir -p`
  commands failed the other way by silently creating an artifacts directory named
  after a word of prose. Every injection in `commands/` now validates the id as
  numeric, reaches `mkdir -p` only once it has, and ends in a fail-soft tail that
  emits a marker such as `NO ARTIFACTS DIRECTORY` and exits 0. Each command body
  says what the dispatched agent must do when it reads a marker instead of a path:
  stop, and give the person the form the command takes. `/odoo-dev:test` and
  `/odoo-dev:pr` still resolve their task directory with `ls -d` rather than
  creating it. Gate 18 in [`validate.sh`](scripts/validate.sh) runs every extracted
  injection against hostile arguments and fails if one exits non-zero or leaves a
  junk directory behind.
- **The gate in `/odoo-dev:pr` is not the enforcement.** It is the fast, legible
  failure, seconds after typing: the `--for pr` verdict is injected into the body
  before the fork, and a red one arrives as its blockers followed by a
  `NO PASSING --for pr GATE` marker, which the body treats as a full stop. The
  enforcement stays where it was: the [`gate-hook.sh`](hooks/gate-hook.sh)
  `PreToolUse` hook, which re-runs the gate at the tool boundary and denies the
  push. Removing either because the other exists is a regression. The release route
  is pre-gated by nothing at all, on purpose — `--for release` reads
  `00-context.json` with `flow_confirmed: true` and a `60-release.json` manifest,
  and neither exists until the agent has built the manifest, so a pre-gate would
  fail every promotion before it started. That route gates itself after writing
  `60-release.json`, and the hook re-runs `--for release` when `release-pr.sh` is
  actually called, so the enforcement is the same machine stop on both routes.
- **Every command needs the task id, except the release route.** The artifacts
  directory is keyed by task id everywhere else in this plugin — `artifact.sh`,
  `gate.sh`, and the hook that derives it from the branch name — so there is nothing
  for a command to resolve without one, and the id has to be the numeric Odoo id
  rather than merely a non-empty string. A promotion belongs to no single task, so
  `/odoo-dev:pr release <from-branch> <to-branch>` keys its directory by the two
  branch names instead, at `releases/<from>-to-<to>`, and validates both of them
  before either reaches a path. Both scripts take a directory path and neither cares
  how it was named.

### Naming

A command, a skill and an agent are three different things with three different
spellings, and the prose here keeps them apart on sight: the command with its
leading slash (`/odoo-dev:pr`), the skill with its namespace prefix
(`odoo-dev:odoo-pr`), the agent bare (`odoo-dev-pr`).

Commands and skills are **not** in separate namespaces, despite appearances. Both
register as `odoo-dev:<file stem>`, and the loader keeps one entry per name, so
`commands/odoo-pr.md` would shadow the `odoo-dev:odoo-pr` skill rather than sit
beside it — and any agent preloading that skill by name would silently get the
command body instead. Measured against Claude Code **2.1.247**;
`claude plugin validate --strict` does not catch it, so `validate.sh` does. The
command stems are therefore `quote`, `task`, `upgrade`, `test` and `pr`, which
collide with no skill and read well under the prefix the runtime adds anyway.

Do not add a `name:` key to a command file. A `name:` without a colon registers a
bare alias, which would put `/pr` and `/test` in the global namespace.

---

## Scripts

| Script | Usage | Does |
|---|---|---|
| [`setup.sh`](scripts/setup.sh) | `setup.sh [--check] [--yes]` | Makes a fresh machine or a rebuilt container ready. Idempotent; never logs in for you |
| [`artifact.sh`](scripts/artifact.sh) | `artifact.sh put\|get\|list\|stages <dir> [stage] [file]` | The only sanctioned way to write a handoff artifact. Schema-validates, writes atomically, never overwrites |
| [`gate.sh`](scripts/gate.sh) | `gate.sh <dir> [--for pr\|release]` | Fail-closed evidence check. Exits 1 on any blocker so `&&` cannot skip it |
| [`bootstrap-state.sh`](scripts/bootstrap-state.sh) | `bootstrap-state.sh [--dir <path>]` | Seeds the external state dir. Idempotent; seeds only what is absent |
| [`check-stray-skills.sh`](scripts/check-stray-skills.sh) | `check-stray-skills.sh [--json]` | Reports feature-managed skills reinstalled loose by a rebuild. Never deletes |
| [`validate.sh`](scripts/validate.sh) | `validate.sh [--quiet]` | Every CI gate in one call. Offline. Skips the `claude plugin validate` gate visibly when that CLI is absent; `REQUIRE_CLAUDE=1` turns the skip into a failure |
| [`tests/gate.test.sh`](scripts/tests/gate.test.sh) | `bash scripts/tests/gate.test.sh` | 24 gate assertions, one fixture per blocker |
| [`tests/setup.test.sh`](scripts/tests/setup.test.sh) | `bash scripts/tests/setup.test.sh` | 34 assertions over `setup.sh`, each running it with a PATH that genuinely lacks the tool under test |
| [`tests/hooks.test.sh`](scripts/tests/hooks.test.sh) | `bash scripts/tests/hooks.test.sh` | 68 assertions over both `PreToolUse` hooks, driven by synthetic payloads |

---

## Handoff artifacts

Append-only JSON in a per-task directory, written only through `artifact.sh`.
Chosen over in-prompt returns because artifacts survive compaction, a session
boundary, a killed subagent, and a human taking over mid-chain.

The router resolves the three paths once, at dispatch, and writes them into the
spawn prompt as absolute paths. Every call afterwards types them out in full — a
subagent's Bash call inherits no environment and keeps no state from the call before
it, so a variable would be empty. With a plugin root of `/home/dev/plugins/odoo-dev`
and task `4821`:

```bash
/home/dev/plugins/odoo-dev/scripts/artifact.sh put /home/dev/.local/share/odoo-dev/tasks/4821 20-build build.json
/home/dev/plugins/odoo-dev/scripts/artifact.sh get /home/dev/.local/share/odoo-dev/tasks/4821 30-test
/home/dev/plugins/odoo-dev/scripts/gate.sh         /home/dev/.local/share/odoo-dev/tasks/4821 --for pr
```

A prompt names the two script files outright rather than one scripts directory,
because the plugin's `scripts/` and a skill's `scripts/` are different directories.
Each skill documents its own as `<base directory>/scripts` and labels it with a
namespaced name (`PR_SCRIPTS`, `TEST_SCRIPTS`, …), so nothing a skill says can be
mistaken for the plugin's scripts.

| Stage | Written by | Payload |
|---|---|---|
| `00-context.json` | scoper, builder, upgrader — whichever runs first | Verbatim `odoo-repo-map` output: project, repo, default_branch, odoo_version, remote, branch_flow, flow_confirmed |
| `05-scope.json` | scoper | Prior-art verdict + evidence, estimate lines and totals, design_doc_path, acceptance_criteria |
| `10-env.json` | builder, upgrader | Verbatim `existing-work.sh` + `worktree-ensure.sh` + `stack-ensure.sh` output |
| `20-build.json` | builder, upgrader | worktree, branch, modules, claims, verify_steps, diff_summary |
| `30-test.json` | `odoo-dev-tester` | Verbatim `run-tests.sh` JSON: passed, tests_run, tours_declared, tours_run, failures, log_file |
| `35-review.json` | `odoo-dev-tester` | Odoo review findings, criteria_results |
| `40-coderabbit.json` | `odoo-dev-pr` | Verbatim `coderabbit-local.sh` JSON |
| `45-waiver.json` | `odoo-dev-pr` | `waived[]` of `{file, line, reason}` — one auditable "won't fix" per CodeRabbit finding that is not going to be fixed, kept out of the tool output it excuses |
| `50-pr.json` | `odoo-dev-pr` | pr_url, pr_number, draft, base, head, title, assigned |
| `60-release.json` | `odoo-dev-pr` | from, to, prs, unresolved, tasks, release_pr_url |

`artifact.sh` never overwrites — a second put of a stage lands at `<stage>.2.json`.
`get` and `gate.sh` read the latest revision and `gate.sh` reports the revision
count, so a chain can recover from a red test but never quietly.

---

## The gate

[`scripts/gate.sh`](scripts/gate.sh) is the one piece of determinism in an
otherwise composable chain: pure arithmetic over the artifacts on disk, never
agent judgement. **Run it between stages. Never ask an agent whether its own work
passed.**

| Blocker | Fires when |
|---|---|
| `no_tests` | `30-test.json` missing, or `tests_run < 1`. Zero is never a pass, and an unparsed log also reports 0 — a broken parser fails closed |
| `tours_skipped` | `tours_declared > 0 && tours_run == 0`. Odoo skips tours without a browser and logs the skip as a pass |
| `tests_failed` | `passed` is anything other than literally `true` |
| `review_incomplete` | CodeRabbit artifact missing, `status != "complete"`, `findings_count` non-numeric or disagreeing with `findings[]`, or a finding with no matching entry in `45-waiver.json`. A waiver entry is consumed per finding, so two findings in one file need two entries, and no waiver rescues a review that never finished |
| `worktree_drift` | `20-build.json` worktree or branch ≠ `10-env.json`. A fix that landed somewhere nobody verified |
| `unconfirmed_flow` | Release only: `flow_confirmed != true`. Never self-confirm a chain whose last element is production |
| `untagged_pr` | Release only, and only when `60-release.json` is missing entirely — there is nothing to report and nothing to ship |

Every one exists because a real run once reported green without it.

There is also a **warnings** channel, reported alongside the blockers and driving
neither `ok` nor the exit code, so `gate.sh "$A" && ship` still ships on a warning:

| Warning | Fires when |
|---|---|
| `untagged_pr` | Release only: a merged PR title with no `[task NNN]`, or a task id inferred rather than tagged. Names the PR numbers and the inferred ids |

`untagged_pr` warns rather than blocks because "a merged PR carries no task" is the
ordinary case, not an exception: some developers never put a task on a PR by policy,
so no human answer could clear it and every release carrying one would be a permanent
dead end. What the blocker was thought to protect — a release note landing on the
wrong task — is protected by the rule that owns it instead: `odoo-dev:odoo-release`
posts a note only to `tasks[]` minus `inferred_task_ids[]` minus `unresolved[]`, a
set that is routinely empty.

**Published surface:** `failures[].error` — the single extracted exception line — is
the only test output that may reach a PR body or Odoo chatter. Raw logs,
tracebacks, machine paths and CodeRabbit text stay internal.

---

## Hooks

Two `PreToolUse` hooks on `Bash`, declared in [`hooks/hooks.json`](hooks/hooks.json)
and shipped inside the plugin. Nothing is written to your `settings.json`; enabling
or disabling the plugin turns them on and off with everything else. Both stay
completely silent unless they have something to say, so an ordinary session never
notices them.

| Hook | Fires on | Denies |
|---|---|---|
| [`bash-allowlist.sh`](hooks/bash-allowlist.sh) | Every `Bash` call whose payload reports `agent_type` `odoo-dev-tester` | Anything outside the allowlist below |
| [`gate-hook.sh`](hooks/gate-hook.sh) | Any `Bash` call that invokes `pr-open.sh`, `release-pr.sh` or `gh pr create`, from any agent | A PR whose task artifacts fail `gate.sh --for pr`, or a promotion whose release directory fails `gate.sh --for release`. Also, for the two scripts of our own, a call whose artifacts or release dir cannot be resolved |

**The tester allowlist.** `odoo-dev-tester` may run `artifact.sh`, `run-tests.sh`,
`browser-ensure.sh`, `gate.sh`, `module-classify.sh`, and read-only `git`
(`status`, `diff`, `log`, `show`, `rev-parse`, `ls-files`, `branch` with read-only
flags only, `worktree list`, `remote -v`, `cat-file`). One command per call: a
backtick, a `$(`, a `<(`, a `;`, a `|`, an `&` or a second line is refused, so an
allowlisted first token cannot carry `; rm -rf` in behind it. Redirection is
permitted only to `/dev/null`. Name a script by its path — a bare `$VAR` is denied,
because the hook cannot see what it expands to.

It is an allowlist and not a blocklist for a reason. A blocklist on write patterns
is wrong in both directions at once: it denies `existing-work.sh … 2>/dev/null`
because the string contains `>`, and it waves through
`python -c "open('f','w')"` because the string contains nothing it recognises. The
allowlist gets both right — the first is refused for not being a sanctioned script,
which is the true reason, and the second is refused for the same reason.

Scope is deliberate and narrow. Every other agent and the main session pass through
untouched, with no output at all, because this hook loads in real sessions and
policing anything beyond the one agent it was written for would be a bug. Inside
that scope it fails closed: a payload it cannot parse that names the tester is
denied, while the same unparseable payload for anyone else is allowed.

**The gate at the tool boundary.** `gate.sh` was always the one deterministic step
in the chain, and nothing made anybody run it. `gate-hook.sh` runs it where the PR
is actually opened, on three command shapes.

On the two task-keyed shapes it takes the worktree from `pr-open.sh`'s first
argument (or the payload `cwd` for `gh pr create`), reads the branch, takes the
leading digits as the task id, and checks
`${ODOO_DEV_STATE_DIR:-$HOME/.local/share/odoo-dev}/tasks/<task_id>`. When that
resolves, the gate decides: `gate.sh --for pr` exits 1 and the PR is denied with
the blockers named, or it exits 0 and the call goes through.

The third shape is `release-pr.sh`, which carries no worktree and belongs to no
single task. Its release directory comes from its own `<from>` and `<to>`
arguments, as `${ODOO_DEV_STATE_DIR:-$HOME/.local/share/odoo-dev}/releases/<from>-to-<to>`,
and it is judged by `gate.sh --for release`. It needs the hook more than the other
two, not less: the last element of a branch chain is production, and `release-pr.sh`
calls `gh pr create` *inside itself*, so the bare-`gh` shape never sees that call and
this shape is the only machine stop in front of it.

The shapes part company when the directory cannot be resolved, and the split is
deliberate.

| Shape | When the directory cannot be resolved |
|---|---|
| `pr-open.sh` | **Denied**, with the reason. It is this plugin's own script, so every invocation of it is inside the workflow, and a PR that cannot be traced back to its evidence does not open |
| `release-pr.sh` | **Denied**, same reasoning, same reason text — plus its own failures: the positional arguments missing, or a branch pair that is not two plain branch names |
| `gh pr create` | **Allowed** in silence — no task id in the branch name, no such directory, no worktree, or not a git repository at all |

`gh pr create` is an ordinary command that any repository on the machine may
reasonably run, so the hook gates it only when it can attribute the PR to a task.
Denying every unattributable `gh pr create` would police unrelated work in
unrelated repositories, which is a larger harm than the one it prevents. The cost
is real and accepted: a task branch named without its task id can bypass the gate
through a hand-run `gh pr create`. Such a PR is invisible to `odoo-dev:odoo-release`
anyway, since the manifest files it under `unresolved` and its task never learns
that it shipped.

One resolution failure denies under both task-keyed shapes: a task id that matches more than
one artifacts dir. The task id resolved, so the PR is inside the workflow whichever
command opened it, and one task with its evidence filed in two places is an
inconsistency the hook names rather than guesses past.

```bash
bash scripts/tests/hooks.test.sh    # 68 assertions, offline
```

---

## Installing

The devcontainer-features repo is the marketplace: its root
[`.claude-plugin/marketplace.json`](../../.claude-plugin/marketplace.json) offers this
plugin from `./plugins/odoo-dev`, so one `marketplace add` both registers the
marketplace and offers the plugin it contains. There is no server and no registry —
the repo is the whole install path.

```bash
claude plugin marketplace add Crpaxton4/devcontainer-features
claude plugin install odoo-dev@devcontainer-features
```

The repo is private, so both commands go through your existing `gh`/git credentials.

### Updating

```bash
claude plugin update odoo-dev
```

Updates are **version-triggered**: `plugin update` refetches only when `version` in
[`.claude-plugin/plugin.json`](.claude-plugin/plugin.json) differs from the installed
copy's. A merge to `main` that does not move that string ships nothing to anyone, and
says nothing about it. That is the whole reason the repo-root
[`release-please`](../../.github/workflows/release-please.yaml) — where this plugin is
registered as the `plugins/odoo-dev` package — bumps it on every release, and the
reason `validate.sh` refuses to let it drift from the repo-root
[`.release-please-manifest.json`](../../.release-please-manifest.json).

Updating is a deliberate act, not a background one. `known_marketplaces.json` carries a
per-marketplace `autoUpdate` flag; set it only if unattended updates are actually
wanted.

### Local development

A checkout under `<config>/skills/odoo-dev/` still loads in place as
`odoo-dev@skills-dir`: the scan of `<config>/skills/*/` finds its `plugin.json` and
loads that directory directly — no marketplace, no install step, no `enabledPlugins`
entry. Editing the working tree *is* the update mechanism there, which is what makes it
the fast edit loop: change a file, then `/reload-plugins`.

**A checkout there must not coexist with an installed copy.** Both load, and every
skill, agent and command registers twice, competing for the same triggers — the same
duplicate-skill failure [`check-stray-skills.sh`](scripts/check-stray-skills.sh) exists
to catch. Pick one: develop out of `<config>/skills/`, or install from the marketplace
and keep the checkout anywhere else. `claude plugin details` says which you have — the
source reads `odoo-dev@devcontainer-features` when installed and
`odoo-dev@skills-dir` when loaded in place.

### First, make the machine ready

```bash
scripts/setup.sh --check   # report only; non-zero if anything is missing
scripts/setup.sh           # take the safe unattended steps, print the rest
scripts/setup.sh --yes     # and fetch the headless browser (~100 MB, network)
```

Run this on a new machine and after every container rebuild, before trusting any
delivery skill. It verifies `node`, `git`, `gh` and its token scopes, the CodeRabbit
CLI and its login, `python3`, and the headless browser; on the host it also checks
docker and the devcontainer CLI, and inside a container it says so rather than
reporting them missing. It seeds the state dir through
[`bootstrap-state.sh`](scripts/bootstrap-state.sh), reports whether `odoo-mcp` is
declared in your global MCP config, and finishes by running
[`preflight.sh`](skills/odoo-task-env/scripts/preflight.sh) and printing its JSON,
so setup and readiness are one pass.

It never logs in for you. Device-flow and browser auth cannot be driven from a
script without hanging, so everything interactive comes back as a numbered list of
commands to run yourself, and every command it does run is bounded by a `timeout`.
Skipping the browser is a real choice with a real consequence: tours skip, the
suite still reports green, and the gate blocks on `tours_skipped`.

```bash
claude plugin validate  .           --strict     # any checkout, by path
claude plugin details   odoo-dev                 # Skills (20) — 15 skills + 5 commands, which
                                                 # plugin details counts together — Agents (5),
                                                 # Hooks (1)
claude plugin disable   odoo-dev                 # the only escape hatch, all-or-nothing
```

`plugin details` is also how you confirm an install is clean: one entry per skill, per
agent and per command, and a source of `odoo-dev@devcontainer-features`.

**Never put a `SKILL.md` at this directory's root.** That triggers the
single-skill-plugin path and collapses every bundled skill into one. `validate.sh` checks
for it. Bundled skills sit three levels deep at `skills/<name>/SKILL.md`; the
personal-skill scan matches exactly one level deep, so it cannot see them — no
double loading.

```
devcontainer-features/
├── .claude-plugin/         marketplace.json — the marketplace that offers this plugin
├── .github/workflows/      plugin-odoo-dev.yaml — every gate, paths-filtered to the plugin
│                           release-please.yaml — cuts the release that bumps the version
├── libraries/odoo_sdk/     src/odoo_sdk/skills/ — source of truth for the 5 consulting skills
└── plugins/odoo-dev/
    ├── .claude-plugin/  plugin.json — the plugin manifest
    ├── skills/     15 skills; odoo-dev-map is the router
    ├── agents/     5 subagents, all named odoo-dev-*
    ├── scripts/    artifact.sh, gate.sh, bootstrap-state.sh, check-stray-skills.sh, validate.sh
    ├── evals/      20 trigger-accuracy cases
    └── README.md
```

---

## State

Mutable state lives **outside** this tree, at
`${ODOO_DEV_STATE_DIR:-$HOME/.local/share/odoo-dev}`:

| Path | Holds |
|---|---|
| `repo-map.json` | Project → repo/branch/series map. Edit only via `repo-map.sh` |
| `upgrade-lessons/` | Raw per-project upgrade capture |
| `tasks/<task_id>/` | Handoff artifacts |

```bash
scripts/bootstrap-state.sh     # idempotent; seeds only what is absent
```

`${CLAUDE_PLUGIN_DATA}` was rejected on purpose: it substitutes only in content the
model receives, so `repo-map.sh` run from a terminal would resolve it to nothing and
silently fall back to an in-tree default. A real path both the model and a bare
shell resolve is the honest mechanism.

---

## Dependencies

- **`odoo-mcp`**, configured globally. This plugin ships no credentials and declares
  no MCP server; it depends on that server being reachable for task, chatter, and
  timesheet reads.
- `node`, `git`, `gh`, `python3`, the CodeRabbit CLI, and a headless browser for
  tours. Docker and the devcontainer CLI on the host only.

[`setup.sh`](scripts/setup.sh) names every one of these, says which are missing, and
prints the exact command for each: `scripts/setup.sh --check`.

---

## Generated consulting skills

Five bundled skills — `discovery-notes`, `fibonacci-estimate`, `odoo-code-review`,
`odoo-design-doc`, `odoo-quote` — are **committed generated copies**. Their source of
truth is the `odoo_sdk` package in this same repo, at
[`libraries/odoo_sdk/src/odoo_sdk/skills/`](../../libraries/odoo_sdk/src/odoo_sdk/skills/).
Edit them THERE, never here, then regenerate the plugin copies from the repo root:

```bash
odoo-sdk sync-skills --dest plugins/odoo-dev/skills
```

The sync is delete-then-copy for exactly those five names and never touches the ten
native script-backed skills. CI regenerates and diffs, so a hand edit to a generated
copy fails the build instead of silently forking the skill body.

The same five skills were also historically shipped loose by a devcontainer feature
into `<config>/skills/`, where a loose copy loads *alongside* its bundled twin: two
near-identical descriptions competing for the same triggers. Interim defense,
reporting only — it never deletes, because deleting a file the feature recreates is
a loop:

```bash
scripts/check-stray-skills.sh
```

Wired into [`preflight.sh`](skills/odoo-task-env/scripts/preflight.sh) and the
[post-rebuild checklist](skills/odoo-devcontainer/references/post-rebuild-checklist.md).

---

## Verify

```bash
scripts/validate.sh
```

Manifest, inventory, frontmatter limits, body size, router completeness, agent
definitions, namespacing, hard-coded paths, stray skills, eval-suite structure,
ten offline test suites, shell syntax, and release-version drift. Offline: no
network, no docker, no Odoo, no repos tree.

### Continuous integration

[`plugin-odoo-dev.yaml`](../../.github/workflows/plugin-odoo-dev.yaml) runs at the
repo root, paths-filtered to `plugins/odoo-dev/**`, the root marketplace.json, the
workflow itself, and the SDK skill sources. Its `validate` job runs
`plugins/odoo-dev/scripts/validate.sh` plus the generated-skill parity diff; sibling
jobs run the per-skill script test suites and a pinned shellcheck. It carries no
secrets, asks for nothing beyond the read-only default token, and makes no network
call except to install its tooling.

No gate is local-only. Eighteen of the nineteen need nothing but bash, coreutils,
git and node, all of which a stock runner already has. The remaining one shells out to
`claude plugin validate --strict`, and the workflow installs the Claude Code CLI so
that one runs on the runner too: `plugin validate` reads the manifest off disk, needs
no account, and passes with an empty `HOME`, which is what lets the workflow stay
credential-free.

Off CI, a machine without that CLI gets a **SKIP** rather than a failure. The skip is
counted apart from the passes and the run closes with `every gate that ran passed,
but 1 did NOT run — this is not a full validation`, because a skip is not a pass. The
workflow sets `REQUIRE_CLAUDE=1`, which turns that skip back into a hard failure, so a
green CI run can never be one where the manifest gate quietly did not happen.

Two gates do check less on a runner than they do on a developer's machine, and
neither can do otherwise:

- **stray feature-managed skills** inspects `<config>/skills/`, which a runner does not
  have, so it passes vacuously. Its real subject is a rebuilt devcontainer, so keep
  running it locally after a rebuild.
- **`setup.test.sh`** hands each case a curated `PATH` built from whatever the machine
  actually has, so a tool the runner lacks is simply left out of that case's bin dir
  instead of failing it. That is the design — what a case omits is the point of it.

### Releases

The repo-root [`release-please.yaml`](../../.github/workflows/release-please.yaml)
runs on every push to `main`. This plugin is the `plugins/odoo-dev` package in
[`release-please-config.json`](../../release-please-config.json) (component
`odoo-dev`, seeded at 1.1.0 — the version the old repo last released, so
`claude plugin update` keeps working across the move): Conventional Commits touching
this tree open a release PR that bumps `version` in
[`.claude-plugin/plugin.json`](.claude-plugin/plugin.json), and on merge cut an
`odoo-dev-vX.Y.Z` tag and GitHub release. That version bump is the only thing
`claude plugin update` reacts to, so a release is what makes work reach an installed
copy at all.

The monorepo squash-merges, and a squash makes the *PR title* the commit
release-please reads — which is why `pr-title-lint.yaml` enforces Conventional
Commit titles on every PR. A non-conventional title would cut no release.

### Trigger accuracy

[`evals/`](evals/) holds 20 cases — 10 that should fire a specific skill and 10
near-misses that share a trigger word but are out of domain ("upgrade the npm
dependencies", "quote this sentence as a blockquote"), split train/validation. They
catch descriptions cannibalizing each other before real work does.

```bash
claude plugin eval odoo-dev@devcontainer-features --ablation with-without
```

`claude plugin eval` is in early access and was not enabled on this account as of
2026-09-07, so the cases are authored and structurally validated but have not been
run. `validate.sh` checks their structure; it deliberately does not probe the
runner, because `claude plugin eval init` writes into the tree being validated.
