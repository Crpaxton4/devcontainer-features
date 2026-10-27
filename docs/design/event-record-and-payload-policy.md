# Event record and payload policy

This page states what one row of the local `events` time series carries, and
what it does not carry. The aim is traceability: a reader must be able to check
the attribution and the origin of an event from the row alone. The code is
`odoo_dev_data.events` (the record and the store) and `odoo_dev_core`
(`builtins/log_event.py`, `attribution.py`, `telemetry.py`).

## The record

An event is an `odoo_dev_data.events.EventRecord`. The `EventStore` appends it
to the `events` table of `$ODOO_DEV_HOME/state.db`.

| Field | Type | Meaning |
|-------|------|---------|
| `id` | `int` or none | Row identity. SQLite assigns it. |
| `source` | `str` | The typing key: `commit`, `merge`, `review`, `comment`, `pr_opened`, `chatter`, `agent`, or `claude:<HookName>`. |
| `timestamp` | `datetime` | The point in time. The clock sets it when the caller gives none. |
| `task_ids` | `list[str]` | The attribution scope. |
| `repo` | `str` | The `owner/repo` label. Display metadata. |
| `branch` | `str` | The branch label. |
| `pr_num` | `int` | The pull request number. `0` when unknown. |
| `subject` | `str` | A human-readable label. For agent events it is the tool name. |
| `payload` | `dict` or none | Extra fields that do not affect typing or billing. |
| `external_id` | `str` or none | The idempotency key of an external event. |

One command writes local events: `log_event` (`odoo_dev_core.builtins.log_event`).
It is on the `cli` surface only. It is not an MCP tool, so an agent cannot write
events by hand. Two callers use it:

- The `odoo-dev cmd log_event` command, which the Claude Code hook shim of the
  `odoo-dev` plugin calls. It writes `claude:<HookName>` events.
- `odoo_dev_core.telemetry.emit_tool_event`, which the MCP server calls once
  after each successful tool call. It writes `source="agent"` events.

A dispatched, unattended run (`ODOO_UNATTENDED` set to any non-empty value) writes
no agent event. The job row measures that run (see {doc}`jobs`).

## Attribution

`odoo_dev_core.attribution.resolve_task_ids` decides the `task_ids` of an event.
The rule has three steps:

1. An explicit hint wins. `normalize_task_ids` drops values that are not
   integers and writes `5`, `"5"` and `" 5 "` all as `"5"`.
2. If there is no hint and `attach_active_run` is true, the task id comes from
   the `<task-id>-<slug>` convention of the branch that the caller states
   (`task_ids_from_branch`). The id needs at least four digits. A branch whose
   first segment is a conventional-commit type (`docs/`, `fix/`, `feat/`) is
   ignored, because its number is a forge issue number.
3. Otherwise the scope is empty. An event with an empty scope is untargeted
   activity. It is never derived into a session and it never bills.

The working-tree branch fills the `branch` column only. It never changes the
attribution of an event.

`emit_tool_event` hands over the `task_id` argument as a hint only for a tool in
`ATTRIBUTING_TOOLS`: `task_note`, `task_question`, `create_task`,
`schedule_activity` and `mark_activity_done`. These tools change the task that
they name. For any other tool, the queried id goes into the payload under
`read_task_ids`. It is evidence only. Reading a task is not work on the task.

## Provenance and identity

`repo` and `branch` come from the working tree at write time, unless the caller
states them. Both are best effort. A failure gives an empty string, and the tool
call does not fail. `pr_num` is never inferred. Agent and hook events write `0`.

The resync pullers set `external_id` (`git:<sha>`, `gh:pr:<n>`, `gh:review:<id>`,
`odoo:mail:<id>`). A partial unique index on `events(external_id)` makes a second
run insert nothing new. Events that the suite emits locally leave `external_id`
empty and never deduplicate.

## Sessions and the session key

The `EventStore` derives sessions from events at query time. It does not store
them. A session is a `SessionWindow` for one task. `session_key(window)` gives
its stable identity:

- `<task_id>|run:<session_id>` for a window that comes from a finished dispatcher
  job. The `session_id` is the job session, which is written once.
- `<task_id>|<id>` for a window from events only. The `id` is the lowest event id
  of the window. A closed session keeps its first event id when later events are
  appended, so the key stays stable.

The `session_uploads` ledger uses the key to make an upload idempotent. A second
upload of a window rewrites the same timesheet row.

The typing of an event depends on `source` alone.
`odoo_dev_core.sessionization.persistence.source_to_event_type` maps the known
sources, and maps each `claude:<HookName>` to `EventType.CLAUDE_HOOK`. An unknown
source raises `UnknownEventSourceError`. It never becomes a commit by default.
The SQL derivation in `EventStore` uses matching source predicates. `subject` and
`payload` take no part in typing, attribution or billing.

## Payload policy

The store never keeps the value of a free-text input. Note bodies, questions and
search queries go to Odoo only. The payload of an agent event has this shape:

- `tool`: the tool name.
- `args`: the sorted names of the arguments. Names show the shape of a call, not
  its content.
- `outcome`: a one-line result.
- `via`: the dispatch surface, when the CLI dispatcher sets it.
- `read_task_ids`: the task ids that a read tool queried.
- The identifier fields that `RESULT_PAYLOAD_KEYS` lists, lifted from the result
  (`task_id`, `branch_name`, `pr_url`, `pr_num`, `commit_sha`, `message_id`, and
  others).

Other sources carry what their origin gives. A resync review or pull request event
carries `pr_title` and `pr_body`, because they come from a public forge artifact.
A calendar tick carries a `synthetic` marker. A hook event carries the JSON object
that the shim passes in `--payload`.

The 500-character limit (`enforce_chatter_body_limit`) applies only to chatter
bodies that `task_note` and `task_question` post to Odoo. Event payloads and
derived summaries have no length limit, and they must not use the chatter limit.

## Known gaps

1. A row does not record why it has its `task_ids`: a hint or a branch.
2. `pr_num` is `0` for every event that the suite emits locally.
3. An event does not name the job that was running when it was emitted. The link
   from events to a job is built at derive time from the task id and the times.
4. Local events never deduplicate, so a double emit gives two rows.

## Non-goals

Do not store argument values (note bodies, question text, search queries). Any
change to this policy must add identifiers, names or scope, never free text.
