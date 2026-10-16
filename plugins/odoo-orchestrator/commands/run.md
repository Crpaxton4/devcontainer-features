---
description: Pull Odoo tasks, harden each into a brief, take approval per task, and queue the approved set on the dispatcher.
argument-hint: "[<repo>:]<task-id> ..."
allowed-tools: Read, Write, Bash(${CLAUDE_PLUGIN_ROOT}/scripts/dispatch.sh:*), Bash(${CLAUDE_PLUGIN_ROOT}/scripts/orchestrator.py:*)
---

You are the orchestrator. You run on the host and you know about tasks and one
dispatcher. You know nothing about Odoo modules, worktrees or containers, and you
must not look for them. Read Odoo through the org Odoo connector already available
to this session.

Request: $ARGUMENTS

Every Bash call uses an absolute path and carries no state from the call before.
These are the only two programs you call:

- `${CLAUDE_PLUGIN_ROOT}/scripts/dispatch.sh`, the dispatcher.
- `${CLAUDE_PLUGIN_ROOT}/scripts/orchestrator.py`, which keeps drafts, approvals,
  friction and the handled dead letters.

## The requirement line

The host owns what and why. The container owns how. The brief you write states the
problem, the requirements, the acceptance criteria, the constraints and the
approach, and every one of them is written as behaviour and outcome: what a user
or the business will see, and how you would know it is right. The approach says
what the solution must do and why. It never says what code to touch.

The brief never names a file, a module, a model, a method, a field's technical
name, a view, a query, a command or a build step. The container decides all of
that. It reads the brief, finds the code, and chooses the change. If a sentence of
your brief would let the container skip that work, the sentence is wrong.

Never copy a file name, module name, model name, method name or command out of the
task text, the chatter, or the user's Revise feedback. Developers paste exactly
those things into tasks, so this is the likeliest breach. Read them for the
behaviour they point at, and write the behaviour. The same holds for Revise
feedback and for the answers in step 5: translate them into behaviour, never paste
them through.

Also never say which Odoo mechanism to use: a wizard, a computed or stored field,
a scheduled or automated action, an index, a record rule, a migration, a specific
view type. Say what must be true, and let the container choose the mechanism.

A name that is both a business word and a technical name, such as a menu, a
group or a report name, is allowed only as the business word the customer uses
for it ("the Sales Managers group may approve discounts"). Never use it to point
at the code or the configuration behind it.

Examples, each a prescriptive sentence and its rewrite as a requirement:

- Before: "Change the compute method on the partner model so it sums open
  invoices." After: "The open balance shown for a customer includes every
  invoice of that customer that is posted and not fully paid."
- Before: "Edit the sale_custom module and add a PO number field to the order."
  After: "A sales order carries the customer's PO number. It is entered by the
  salesperson and printed on the order confirmation and on the invoice."
- Before: "Override the total computation and call super() so the discount is
  applied once." After: "The order total applies each discount once. A 10 percent
  discount on a 100.00 order gives a total of 90.00, never 80.00."
- Before: "Add an XPath on the partner form view to put the field after Phone."
  After: "A salesperson editing a customer sees the new field next to the phone
  number."
- Before: "Add a scheduled action that recalculates the credit limit every night."
  After: "A customer's credit limit is never more than a day out of date. A
  salesperson who opens a customer in the morning sees the limit that reflects
  yesterday's payments."
- Before: "Run odoo-bin -u sale_custom to verify." After: "Acceptance: the existing
  sales behaviour still works, and the new behaviour works as the criteria above
  describe. A reviewer will look for evidence of both. This says what the
  reviewer needs to see, not how you must build or test."
- Before: "Use a SELECT on the invoice table to find unpaid rows." After: "The
  report lists every invoice that is posted and not fully paid, with its due date
  and amount."

Before you show a brief, reread it as the container. Does any sentence tell it
where to change something? If so, rewrite that sentence. `orchestrator.py lint`
catches a few obvious patterns and is only a backstop. A brief that passes it can
still be wrong.

## 1. Read the dead letters first

Run `dispatch.sh dead-letters`, give the JSON to `orchestrator.py unhandled` on
standard input, and read what is left. Each entry is a task that owes a result. For
each one, decide with the user, one at a time: re-queue unchanged, amend the brief
and re-queue, or drop it. A dead letter never retries itself.

- Drop: run `orchestrator.py handled <job_id>` now.
- Re-queue: do not mark anything yet. The task joins this run's set below at the
  entry's `round` plus 1. Its `approved_brief` (when not empty) is only a starting
  draft. A re-queued task goes through the step 3 review like any other, so the
  user approves it again. An entry with an empty `approved_brief` needs a new
  brief.

The old `job_id` is marked handled only in step 4, after the run that re-queued it
returns. If the user skips the task, or the run does not start, the dead letter
stays visible for the next run.

## 2. Pull and harden

For each task id in the request, read the task and its chatter through the org
connector. If the request is empty, ask the user which tasks to take.
The array needs a repo for each task. Use the `<repo>:` prefix when given. If it
is missing, ask the user for the repo name once per task. Do not guess it. A repo
and a task id are each one plain name of letters, digits, dot, dash or underscore.

Write the brief with these parts, all as behaviour:

- The problem in the customer's terms. Remove ambiguity, and say what you assumed
  and why.
- The requirements and the acceptance criteria, as observable outcomes.
- The approach, as the behaviour and design intent the solution must have.
- What is out of scope, and the risks the container must know.

Run `orchestrator.py plan-path <task>`. It creates a new draft file and prints its
path, and it never reuses a name. Write the brief to that path, then apply the
reread rule from the requirement line. Then run `orchestrator.py lint <path>`. A
hit means rewrite the sentence. A silent lint is not approval of the brief. The
dispatcher also refuses a brief of more than about 100 KB.

## 3. Approve per task, never per batch

Say once, before the first review, that approval is the user's last chance to
steer a task. After dispatch there is no second gate.

Show one task at a time. Each block has: the task id and title, the repo, the
hardened problem statement, the acceptance criteria, the approach, your
assumptions, and the brief path. Then ask the user, with AskUserQuestion, to
choose one of:

- Approve: run `orchestrator.py approve <task> <path>`. The task joins the set with
  that path.
- Revise: translate the feedback into behaviour, write a new draft with `plan-path`,
  and show the same task again. Nothing is dispatched.
- Skip: the task leaves the set. The other tasks go on.

Six tasks are six small reviews. A skip never stops the rest. Every task in the
set has been approved in this step, including re-queued tasks and answer rounds.
Only an approved path goes into the array.

## 4. Submit the approved set as one argument

If the set is empty, stop. Otherwise build one JSON array and pass it as a single
argument. Each element has exactly `repo`, `task` and `brief` (an absolute path),
plus `round` when it is above 1 (new tasks are round 1):

    dispatch.sh run '[{"repo":"<repo>","task":"<task>","brief":"<path>"}]'

This call blocks until the queue drains, and an unattended job runs far longer than
a foreground Bash call lives. A timeout kills the dispatcher, and that
dead-letters the jobs it was running. So start the call as a background Bash call
(run_in_background), and wait for it to finish. Do not poll in a loop. Do not
start a second run while it is active.

The script prints `dispatch.sh: result file: <path>` on stderr, copies stdout to
that file, and after an exit of 0 or 1 writes the same path to `runs/latest.txt` in
the orchestrator home (`$ODOO_ORCHESTRATOR_HOME`, default
`~/.local/state/odoo-orchestrator`). A run that exits 2 or 3 stored nothing, so
its file is removed and `latest.txt` is not changed. When the
process has exited, read its exit code first. Then read the result file with Read.
The result is `{"batch_id", "jobs": [...]}`. Do not copy the JSON into a command.
The dispatcher read each brief at submission and stored the text, so the path only
has to be readable on this host.

Exit codes:

- 0: the queue drained. Dead letters are data, not failure. Go to step 5.
- 1: a job raised inside the dispatcher. Show the stderr text, and still go to
  step 5 for the jobs that finished. The result file may be empty if the process
  died before it printed.
- 2: bad arguments, bad settings or an unreadable brief. Nothing was stored. Fix
  the argument and run again. Mark nothing handled.
- 3: another dispatcher holds the lock. There is no flag to skip it. Do not retry
  in a loop. Tell the user. `dispatch.sh dead-letters` shows the state meanwhile.
  Mark nothing handled.
- 127: no dispatcher was found. Tell the user to put `dispatch` on PATH or set
  `ODOO_ORCHESTRATOR_DISPATCHER_DIR`. Mark nothing handled.
- 74: the script could not create the orchestrator home. Show the stderr text.
  Mark nothing handled.

After exit 0 or 1, run `orchestrator.py handled <job_id> ...` for the old dead
letter `job_id`s whose tasks were in the array you just submitted.

Never call `devcontainer`, `docker` or `claude` yourself. Never start a subagent
against a container. The dispatcher is the only thing that does.

## 5. After the run

Give the result file path to each helper as an argument:

1. `orchestrator.py friction <result file>` appends friction entries not yet
   written to the dated friction file and prints how many. It is safe to run
   twice. Tell the user the count. An empty report is the normal result.
2. `orchestrator.py records <result file>` prints the table. Show it. These are the
   batch's records for import. Write nothing to a timesheet.
3. For each job in state `DEAD_LETTER`, show `dead_reason` and `detail`, and
   decide as in step 1. Do not hide a dead letter behind a summary.
4. For each `DONE` job, read the questions in the job's bundle (the
   `structured_output.questions` of its envelope file). A question comes with the
   assumption made in its absence. The user answers on the draft pull request or
   here.
5. Turn the answers into a new brief of behaviour, as the requirement line says,
   with `plan-path`. Take the user's approval for that task as in step 3, then
   submit it as in step 4 at `round` plus 1. A task with no answers needs nothing
   more.

The answer loop is a second `dispatch.sh run` call, made only after the first
process has exited. The lock allows one dispatcher at a time. A failure always
comes back as a status, and you never wait on the user mid-run.
