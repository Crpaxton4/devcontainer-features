"""Odoo activity (``mail.activity``) scheduling, listing, and completion helpers.

Before #677 the MCP surface had no way to reach ``mail.activity`` at all: the
write surface was task-shaped (``create_task``, ``task_note``, the session
lifecycle tools) and no generic create/write passthrough exists, so scheduling a
follow-up on a record meant dropping out of the server entirely and driving
XML-RPC (or ``odoo-bin shell``) by hand. This module is the single Odoo-facing
implementation behind the four activity tools:

* :func:`schedule_activity` — create one activity on any record (defaulting to
  ``project.task``, the model that motivated the request).
* :func:`get_activities` — list the *open* activities on a record and/or for a
  user. ``mail.activity`` only ever holds open activities: completing one
  deletes the row and leaves a ``mail.message`` behind, so "open" needs no
  filter — it is the whole model.
* :func:`mark_activity_done` — the mark-done action, wrapping Odoo's
  ``action_feedback(feedback=...)``.
* :func:`resolve_activity_type_id` / :func:`search_activity_types` — so callers
  address a type as ``"Call"`` rather than as a raw database id.

Two Odoo facts drive the shape of the write path:

* ``mail.activity.res_model_id`` (an ``ir.model`` many2one) is **required**;
  ``res_model`` is only a stored *related* mirror of it and is read-only. So a
  create has to supply the model *id*, and there is no way around that: the one
  alternative — calling ``mail.thread.activity_schedule`` on the record — is not
  reachable over RPC at all, because it returns a recordset, which no transport
  can marshal.

  That id comes from **configuration, never from Odoo**. The first cut of this
  module (#677) resolved it with a ``search_read`` on ``ir.model``, which made
  the entire write path unusable for exactly the accounts the SDK exists to
  serve: that administrative table must never be granted to a least-privileged
  service account, so the lookup was denied and ``mail.activity.create`` was
  never reached (#686). It is the same defect #444 removed from
  :mod:`odoo_sdk.utilities.knowledge`, which for that reason deliberately runs
  its real query with no ``ir.model`` pre-flight probe at all. "Ask an Odoo admin
  to grant read access on ir.model" is not an available remedy here; it is the
  privilege escalation the constraint exists to prevent.

  So :func:`_res_model_id` reads the hand-managed ``[model_ids]`` map instead,
  via :meth:`~odoo_sdk.state.config.LocalConfig.require_model_id`. The
  :class:`~odoo_sdk.state.config.LocalConfig` is threaded in from the command
  layer (every :class:`~odoo_sdk.commands.command.Command` already exposes a
  lazily-resolved ``self.config``) rather than loaded inside this module, so the
  dependency is explicit at the call site and a test can supply its own map
  without reaching for the developer's real config file. A model absent from the
  map raises a typed :class:`ValueError` naming the exact entry to add — because
  the map is maintained by hand, that message is the only discovery mechanism a
  caller without server access gets. There is deliberately no fallback to an
  ``ir.model`` read when the map misses: the fallback *is* the bug, and it would
  reintroduce it for precisely the accounts the map exists to serve.
* ``mail.activity.note`` is an HTML field, so caller-supplied Markdown is
  rendered with :func:`~odoo_sdk.utilities.html.markdown_to_html` on the way in
  and converted back with :func:`~odoo_sdk.utilities.html.html_to_markdown` on
  the way out — the same in/out contract ``task_note`` uses for chatter bodies
  (#324).
"""

from typing import Any, Optional, Union

from odoo_sdk.client import OdooClient
from odoo_sdk.state import LocalConfig

from .html import html_to_markdown, markdown_to_html

# ``_validate_iso_date`` is the SDK's one ``YYYY-MM-DD`` guard (it rejects the
# basic-ISO forms ``date.fromisoformat`` accepts but Odoo would mis-compare).
# Imported rather than re-implemented so every date-bounded read/write in the SDK
# rejects exactly the same inputs.
from .odoo_helpers import _validate_iso_date, m2o_id, resolve_many2one

#: Model activities are scheduled on when the caller does not name one. The
#: request behind #677 is task-shaped ("primarily tasks"), so ``project.task`` is
#: the convenience default while every other model stays one argument away.
DEFAULT_ACTIVITY_RES_MODEL = "project.task"

#: Maximum activities returned by one :func:`get_activities` call by default.
DEFAULT_ACTIVITY_LIMIT = 50

#: Maximum activity types returned by one :func:`search_activity_types` call.
DEFAULT_ACTIVITY_TYPE_LIMIT = 20

#: ``mail.activity`` fields read for every returned activity. ``state`` is Odoo's
#: computed urgency bucket (``overdue`` / ``today`` / ``planned``) and is the
#: single most useful field for triage, so it is always projected.
_ACTIVITY_FIELDS = [
    "id",
    "res_model",
    "res_id",
    "res_name",
    "activity_type_id",
    "summary",
    "note",
    "date_deadline",
    "user_id",
    "create_date",
    "state",
]

#: ``mail.activity.type`` fields read by the type resolver/search. Kept to the
#: three fields present on every supported Odoo version: a type's ``res_model``
#: is ``False`` for the generic types (To Do, Call, Meeting, Email) and a model
#: name for the ones a module scopes to one document type.
_ACTIVITY_TYPE_FIELDS = ["id", "name", "res_model"]


def _shape_activity(record: dict) -> dict[str, Any]:
    """Project one raw ``mail.activity`` row into the reported entry.

    Many2one pairs are split into the id *and* the display name (callers need
    the id to act on the activity and the name to show a human), and the HTML
    ``note`` is converted back to Markdown so a round trip through
    :func:`schedule_activity` returns what the caller wrote.
    """
    return {
        "activity_id": record["id"],
        "res_model": record.get("res_model"),
        "res_id": record.get("res_id"),
        "res_name": record.get("res_name"),
        "activity_type_id": m2o_id(record.get("activity_type_id")) or None,
        "activity_type": resolve_many2one(record.get("activity_type_id")) or "",
        "summary": record.get("summary") or "",
        "note": html_to_markdown(record.get("note") or ""),
        "date_deadline": record.get("date_deadline"),
        "user_id": m2o_id(record.get("user_id")) or None,
        "user": resolve_many2one(record.get("user_id")) or "",
        "state": record.get("state"),
        "create_date": record.get("create_date"),
    }


def _read_activity(client: OdooClient, activity_id: int) -> dict[str, Any]:
    """Read and shape one activity by id.

    :raises ValueError: When no activity with ``activity_id`` exists — the id
        may be wrong, or the activity may already have been completed (feedback
        deletes the row), which is exactly the distinction a caller needs.
    """
    records = client.execute(
        "mail.activity", "read", [activity_id], fields=_ACTIVITY_FIELDS
    )
    if not records:
        raise ValueError(
            f"mail.activity {activity_id} not found. It may already have been "
            "marked done (completing an activity deletes the record and leaves "
            "a chatter message behind)."
        )
    return _shape_activity(records[0])


def _activity_type_domain(
    predicate: Optional[tuple], res_model: Optional[str]
) -> list[Any]:
    """Build a ``mail.activity.type`` domain from a name predicate and a model.

    ``res_model`` narrows the search to the types actually applicable to that
    document type: the generic types carry ``res_model = False`` and stay
    eligible, while a type scoped to a *different* model is excluded. Terms are
    combined with Odoo's implicit AND, so the OR only spans the model pair.
    """
    domain: list[Any] = []
    if predicate is not None:
        domain.append(predicate)
    if res_model is not None:
        domain.extend(["|", ("res_model", "=", False), ("res_model", "=", res_model)])
    return domain


def _search_activity_type_rows(
    client: OdooClient,
    predicate: Optional[tuple],
    res_model: Optional[str],
    limit: int,
) -> list[dict]:
    """Read the ``mail.activity.type`` rows matching a domain, by name."""
    return client.execute(
        "mail.activity.type",
        "search_read",
        _activity_type_domain(predicate, res_model),
        fields=_ACTIVITY_TYPE_FIELDS,
        order="name asc, id asc",
        limit=limit,
    )


def search_activity_types(
    client: OdooClient,
    query: Optional[str] = None,
    res_model: Optional[str] = None,
    limit: int = DEFAULT_ACTIVITY_TYPE_LIMIT,
) -> list[dict[str, Any]]:
    """List the activity types available for scheduling, name-ordered.

    The discovery counterpart to :func:`resolve_activity_type_id`: it answers
    "what can I schedule?" without the caller guessing a name. ``query`` is an
    optional case-insensitive substring match on the type name and ``res_model``
    restricts the result to the types applicable to that document type (the
    generic ones plus any scoped to exactly that model). Read-only.

    :return: ``{"id", "name", "res_model"}`` dicts; ``res_model`` is ``None`` for
        a generic type rather than Odoo's ``False``.
    """
    predicate = ("name", "ilike", query) if query else None
    rows = _search_activity_type_rows(client, predicate, res_model, limit)
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "res_model": row.get("res_model") or None,
        }
        for row in rows
    ]


def _no_type_match_error(
    client: OdooClient, name: str, res_model: Optional[str]
) -> ValueError:
    """Build the "no such activity type" error, naming what *is* available.

    Listing the real candidates turns a dead end into a retryable call: an LLM
    caller that guessed "Todo" is told the type is spelled "To Do" instead of
    being left to guess again.
    """
    available = [
        row["name"] for row in search_activity_types(client, res_model=res_model)
    ]
    suffix = f" Available types: {', '.join(available)}." if available else ""
    return ValueError(f"No activity type matches {name!r}.{suffix}")


def resolve_activity_type_id(
    client: OdooClient,
    activity_type: Union[int, str],
    res_model: Optional[str] = None,
) -> int:
    """Resolve an activity type given either its id or its name.

    An ``int`` passes straight through (already an id). A ``str`` is matched
    against ``mail.activity.type.name`` in two passes: an exact case-insensitive
    match (``=ilike``) first, so ``"Call"`` never collides with a longer
    ``"Call back"``, then a substring match (``ilike``) as a convenience
    fallback. ``res_model`` narrows both passes to the types applicable to that
    document type.

    :raises ValueError: When the name matches no type (the message names the
        available types) or matches several (the message names the candidates so
        the caller can pick one), and when it is blank.
    """
    if isinstance(activity_type, int) and not isinstance(activity_type, bool):
        return activity_type
    name = str(activity_type or "").strip()
    if not name:
        raise ValueError(
            "activity_type must be a mail.activity.type id or a non-empty name."
        )
    matches = _search_activity_type_rows(
        client, ("name", "=ilike", name), res_model, DEFAULT_ACTIVITY_TYPE_LIMIT
    ) or _search_activity_type_rows(
        client, ("name", "ilike", name), res_model, DEFAULT_ACTIVITY_TYPE_LIMIT
    )
    if not matches:
        raise _no_type_match_error(client, name, res_model)
    if len(matches) > 1:
        candidates = ", ".join(f"{row['name']} (id={row['id']})" for row in matches)
        raise ValueError(
            f"Activity type {name!r} is ambiguous; it matches: {candidates}. "
            "Pass an exact name or the id."
        )
    return matches[0]["id"]


def _res_model_id(config: LocalConfig, res_model: str) -> int:
    """Resolve a model name to its ``ir.model`` id for ``res_model_id``.

    The id is read from the hand-managed ``[model_ids]`` config map and never
    from ``ir.model`` itself — see the module docstring for why (#444, #686).
    Kept as a named seam rather than inlined into :func:`_schedule_values` so the
    write path has one documented place where a model id enters it, and so the
    "this module never reads ``ir.model``" guarantee has a single function to
    point at.

    A model missing from the map and a model that does not exist on the database
    are indistinguishable from here, deliberately: telling them apart is exactly
    the ``ir.model`` read this avoids. Both surface as the one actionable message.

    :raises ValueError: When ``res_model`` has no ``[model_ids]`` entry. The
        message names the model and the exact entry to add; it never suggests
        granting read access on ``ir.model``.
    """
    return config.require_model_id(res_model)


def _schedule_values(
    client: OdooClient,
    config: LocalConfig,
    res_model: str,
    res_id: int,
    activity_type: Optional[Union[int, str]],
    summary: str,
    note: str,
    date_deadline: Optional[str],
    user_id: Optional[int],
) -> dict[str, Any]:
    """Build the ``mail.activity`` create values.

    Only the mandatory keys are always present. Optional ones are omitted when
    unset rather than written as empty strings, so Odoo's own defaults still
    apply — notably ``date_deadline``, which defaults to today, and the type's
    configured delay.

    ``config`` sits beside ``client`` because the two are peer dependencies of
    the write path: the client resolves the activity type and the assignee, the
    config supplies ``res_model_id`` from ``[model_ids]`` (#686).
    """
    values: dict[str, Any] = {
        "res_model_id": _res_model_id(config, res_model),
        "res_id": res_id,
        # The activity is the caller's own follow-up unless they hand it off, so
        # an unset assignee resolves to the authenticated user, not to Odoo's
        # default (which would be the same uid, but only implicitly).
        "user_id": client.uid if user_id is None else user_id,
    }
    if activity_type is not None:
        values["activity_type_id"] = resolve_activity_type_id(
            client, activity_type, res_model
        )
    if summary:
        values["summary"] = summary
    if note:
        # ``note`` is an HTML field: Markdown posted verbatim renders as literal
        # text with collapsed newlines (#324).
        values["note"] = markdown_to_html(note)
    if date_deadline:
        values["date_deadline"] = date_deadline
    return values


def schedule_activity(
    client: OdooClient,
    res_id: int,
    res_model: str = DEFAULT_ACTIVITY_RES_MODEL,
    activity_type: Optional[Union[int, str]] = None,
    summary: str = "",
    note: str = "",
    date_deadline: Optional[str] = None,
    user_id: Optional[int] = None,
    *,
    config: LocalConfig,
) -> dict[str, Any]:
    """Schedule one ``mail.activity`` on a record and return the created entry.

    ``res_model`` defaults to :data:`DEFAULT_ACTIVITY_RES_MODEL` so scheduling on
    a task is a two-argument call, while any other model stays reachable.
    ``activity_type`` accepts an id *or* a name (see
    :func:`resolve_activity_type_id`); omitting it leaves Odoo's default type in
    place. ``user_id`` defaults to the authenticated user. ``note`` is Markdown
    in / HTML out; ``date_deadline`` is an inclusive ``YYYY-MM-DD`` date and, when
    omitted, Odoo applies the type's own delay (today, for a type with none).

    The created activity is read back so the return value carries the resolved
    type and assignee names alongside their ids, rather than only the new id.

    ``config`` is keyword-only and required. It supplies ``res_model_id`` from
    the ``[model_ids]`` map (#686), and being explicit keeps this module from
    ever loading a config of its own: the command layer hands down its injected
    ``self.config``. It is deliberately not defaultable, because the only thing
    to fall back *to* is the ``ir.model`` read this signature exists to remove.

    :raises ValueError: On a malformed ``date_deadline``, an unresolvable
        ``activity_type``, or a ``res_model`` with no ``[model_ids]`` entry (that
        message names the exact entry to add).
    """
    _validate_iso_date(date_deadline, "date_deadline")
    values = _schedule_values(
        client,
        config,
        res_model,
        res_id,
        activity_type,
        summary,
        note,
        date_deadline,
        user_id,
    )
    activity_id = client.execute("mail.activity", "create", values)
    return _read_activity(client, activity_id)


def _activity_domain(
    res_model: Optional[str], res_id: Optional[int], user_id: Optional[int]
) -> list[Any]:
    """Build the ``mail.activity`` search domain from the optional filters."""
    domain: list[Any] = []
    if res_model is not None:
        domain.append(("res_model", "=", res_model))
    if res_id is not None:
        domain.append(("res_id", "=", res_id))
    if user_id is not None:
        domain.append(("user_id", "=", user_id))
    return domain


def get_activities(
    client: OdooClient,
    res_id: Optional[int] = None,
    res_model: Optional[str] = None,
    user_id: Optional[int] = None,
    limit: int = DEFAULT_ACTIVITY_LIMIT,
) -> list[dict[str, Any]]:
    """List open activities on a record and/or for a user, soonest deadline first.

    Every ``mail.activity`` row is by definition open — marking one done deletes
    it and leaves a ``mail.message`` — so no "open" filter exists or is needed.

    Two conveniences keep the common calls short and the result useful:

    * a ``res_id`` with no ``res_model`` is read as a
      :data:`DEFAULT_ACTIVITY_RES_MODEL` id, matching
      :func:`schedule_activity`'s default;
    * a call with *no* filter at all scopes to the authenticated user's own
      activities, since an unfiltered read would return every open activity in
      the database. Pass an explicit ``user_id`` to look at someone else's, or
      any ``res_model``/``res_id`` filter to see a record's full list.

    Read-only: one ``search_read``.
    """
    if res_id is not None and res_model is None:
        res_model = DEFAULT_ACTIVITY_RES_MODEL
    if res_model is None and res_id is None and user_id is None:
        user_id = client.uid
    rows = client.execute(
        "mail.activity",
        "search_read",
        _activity_domain(res_model, res_id, user_id),
        fields=_ACTIVITY_FIELDS,
        order="date_deadline asc, id asc",
        limit=limit,
    )
    return [_shape_activity(row) for row in rows]


def mark_activity_done(
    client: OdooClient, activity_id: int, feedback: str = ""
) -> dict[str, Any]:
    """Complete an activity via ``action_feedback``, returning what was closed.

    Odoo's ``action_feedback`` posts ``feedback`` to the record's chatter and
    then **deletes** the activity row, so the activity is read *first*: the
    returned entry describes what was completed, which would otherwise be
    unrecoverable by the time the call returns.

    ``action_feedback`` returns the id of the chatter message it posted, or a
    falsy value when it posted none; only a genuine id is surfaced as
    ``message_id``.

    :raises ValueError: When no activity with ``activity_id`` exists (a wrong id,
        or one already marked done).
    """
    activity = _read_activity(client, activity_id)
    message_id = client.execute(
        "mail.activity", "action_feedback", [activity_id], feedback=feedback
    )
    posted = isinstance(message_id, int) and not isinstance(message_id, bool)
    return {
        **activity,
        "done": True,
        "feedback": feedback,
        "message_id": message_id if posted else None,
    }
