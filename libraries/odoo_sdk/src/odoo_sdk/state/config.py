"""Local configuration for the Odoo SDK.

This module hosts two related concerns of the local state layer:

* :class:`LocalConfig` — the single, first-class settings resolver. It discovers
  one config file (see :data:`LOCAL_CONFIG_ENV_VAR` and the default locations
  below) and resolves each setting with the precedence

      File  >  Environment Variable  >  Sensible Default

  so that consuming programs (Claude Desktop, other MCP hosts) can change SDK
  behavior by editing a local config file without touching the host launch
  command. A ``[behavior]`` section is reserved for future behavioral flags
  (profiling, log level, ...) without further structural changes, and a
  ``[model_ids]`` section holds the model-name to ``ir.model`` id map (see
  :data:`_MODEL_IDS_SECTION`). That map is the single source every caller reads
  — nothing resolves ``ir.model`` at call time (#444, #686) — but since #890 it
  no longer has to be typed by hand: :meth:`LocalConfig.set_model_id` writes one
  entry into the file in place, and the gated ``get_models`` command (which
  already reads ``ir.model``, under its own gate) calls it.
* :class:`OdooConnectionSettings` — the resolved, validated connection value
  object consumed by :class:`~odoo_sdk.client.client.OdooClient`. Its
  :meth:`~OdooConnectionSettings.from_sources` factory is a thin validator fed by
  :class:`LocalConfig`: it resolves file, environment, and default values through
  the single resolver and then overlays any explicit constructor arguments.
  Since #717 the value object and its validators live in the shared-kernel
  :mod:`odoo_sdk.settings` module (so the transports can name them without
  importing the state layer) and are re-exported here unchanged, keeping every
  historical ``odoo_sdk.state.config`` import path working.

Config discovery consults, in order, the first location that yields an existing
file:

1. ``$ODOO_SDK_CONFIG`` — a config **file** or a **directory** that is probed for
   ``config.toml`` then ``config.ini`` (so the devcontainer feature can point the
   variable at its mounted config directory regardless of which file exists).
2. ``./.odoo_sdk.toml`` / ``./.odoo_sdk.ini`` in the current working directory.
3. ``~/.config/odoo_sdk/config.toml`` / ``~/.config/odoo_sdk/config.ini``.

INI files accept ``[odoo]`` as an alias for ``[connection]`` so an already
persisted ``~/.config/odoo_sdk/config.ini`` keeps working unchanged.

``[connection]`` and ``[behavior]`` have fixed key sets resolved by
:func:`_resolve_section`. ``[model_ids]`` is open-ended — its keys are Odoo model
names — so it carries its own loader (:func:`_resolve_model_ids`) wired into
:meth:`LocalConfig.load` beside the other two.
"""

import configparser
import importlib
import os
import re
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Extracted to the shared kernel by #717 (transport must not import the state
# layer for its settings type); re-exported here so every historical
# ``odoo_sdk.state.config`` import keeps working.
from odoo_sdk.settings import (  # noqa: F401
    CONNECTION_ENV_VARS,
    DEFAULT_TIMEOUT_SECONDS,
    OdooConnectionSettings,
    _build_connection_settings,
    _coerce_non_negative_float,
    _coerce_timeout,
    _validate_required_settings,
)

# The single environment variable that overrides config discovery. It may name a
# config FILE or a DIRECTORY (the directory is probed for config.toml /
# config.ini), so the devcontainer feature can point it at the mounted config
# directory regardless of which file the user created.
LOCAL_CONFIG_ENV_VAR = "ODOO_SDK_CONFIG"

# INI section holding connection settings, plus the legacy ``[odoo]`` alias
# accepted so an already-persisted ``~/.config/odoo_sdk/config.ini`` keeps
# working, the reserved ``[behavior]`` section, and the open-ended
# ``[model_ids]`` map.
_CONNECTION_SECTION = "connection"
_CONNECTION_SECTION_ALIAS = "odoo"
_BEHAVIOR_SECTION = "behavior"
_MODEL_IDS_SECTION = "model_ids"

#: Environment variable holding ``model:id`` pairs for the ``[model_ids]`` map,
#: separated by commas and/or whitespace (the same delimited-string convention as
#: ``ODOO_RESYNC_AUTHORS``), e.g. ``"project.task:123, res.partner:77"``.
MODEL_IDS_ENV_VAR = "ODOO_MODEL_IDS"

# Default config discovery locations (see the module docstring for the full
# precedence order). ``$ODOO_SDK_CONFIG`` overrides all of these.
_CWD_CONFIG_BASENAMES = (".odoo_sdk.toml", ".odoo_sdk.ini")
DEFAULT_CONFIG_DIR = "~/.config/odoo_sdk"
_CONFIG_DIR_FILENAMES = ("config.toml", "config.ini")

# TOML parser modules probed, in order, when a ``.toml`` config is read. The
# stdlib ``tomllib`` only exists from Python 3.11; on 3.10 (the supported floor,
# shipped by ``odoo:17``) the API-compatible ``tomli`` backport stands in for it
# and is declared as a ``python_version < "3.11"`` dependency.
_TOML_MODULE_NAMES = ("tomllib", "tomli")


def _coerce_positive_int(value: Any, default: int) -> int:
    """Coerce a raw value to a positive ``int``, else ``default`` (mirrors the float form)."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _coerce_flag(value: Any) -> bool:
    """Coerce a bool or truthy string token (``1``/``true``/``yes``/``on``) to a flag."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUTHY_VALUES


# ── model ids ─────────────────────────────────────────────────────────────────


def model_id_unavailable_message(model: str) -> str:
    """Return the actionable error text for a model missing from ``[model_ids]``.

    Because the map is managed by hand, this message is the only discovery
    mechanism a user without server access gets, so it names both the missing
    model and the exact config entry to add, in every spelling that works.
    """
    return (
        f"No ir.model id is configured for {model!r}. Add it to the "
        f"[model_ids] section of the Odoo SDK config file "
        f'(TOML: "{model}" = <id>   INI: {model} = <id>) or export '
        f'{MODEL_IDS_ENV_VAR}="{model}:<id>". The id must be supplied by hand: '
        "the SDK never reads ir.model, because that administrative table must "
        "not be granted to a least-privileged service account (#444, #686). An "
        "operator who does hold the privilege can look the id up — and write it "
        "into the config file once — with the gated get_models command: "
        f"{persist_model_id_command(model)}"
    )


def persist_model_id_command(model: str) -> str:
    """Return the CLI invocation that resolves ``model``'s id and writes it down (#890).

    The one command that closes the gap the hand-managed map leaves open: it is
    named by :func:`model_id_unavailable_message` and by the odoo-dev plugin's
    readiness check, so both quote the same string rather than two drifting
    spellings of it.
    """
    return f"""odoo-sdk cmd get_models --args '{{"persist": ["{model}"]}}'"""


def model_id_not_persisted_message(model: str, path: Any, reason: str) -> str:
    """Return the warning text for an id that was resolved but could not be written.

    Persisting is best-effort by design (#890): ``get_models`` still answers the
    read it was asked for, and reports this instead of raising, so a read-only
    config directory degrades to the pre-existing hand-edit workflow rather than
    failing the command.
    """
    return (
        f"Resolved the ir.model id for {model!r} but could not write it to "
        f"{path}: {reason}. Add it by hand instead "
        f'(TOML: "{model}" = <id>   INI: {model} = <id>) or export '
        f'{MODEL_IDS_ENV_VAR}="{model}:<id>".'
    )


class ModelIdsNotWritableError(RuntimeError):
    """Raised when the ``[model_ids]`` section cannot be persisted to disk (#890).

    Distinct from the :class:`ValueError` a bad id raises: a bad id is a defect
    worth failing on, while an unwritable config file is an environment fact the
    caller is expected to report and carry on from.
    """


def _invalid_model_id_message(model: str, value: Any) -> str:
    """Return the error text for a ``[model_ids]`` value that is not a positive int."""
    return (
        f"Invalid [model_ids] entry for {model!r}: {value!r}. An ir.model id must "
        "be a positive integer."
    )


def _invalid_model_ids_env_message(token: str) -> str:
    """Return the error text for a malformed :data:`MODEL_IDS_ENV_VAR` token."""
    return (
        f"Invalid {MODEL_IDS_ENV_VAR} entry {token!r}: expected comma- or "
        f'whitespace-separated "model:id" pairs, e.g. '
        f'{MODEL_IDS_ENV_VAR}="project.task:123,res.partner:77".'
    )


def _flatten_model_id_table(
    table: Mapping[str, Any], prefix: str = ""
) -> dict[str, Any]:
    """Flatten nested TOML tables back into dotted model names.

    An unquoted ``project.task = 123`` under ``[model_ids]`` is a *dotted key* in
    TOML, so the parser yields the nested ``{"project": {"task": 123}}`` rather
    than the flat name the section is meant to hold. Flattening on load makes both
    the quoted (``"project.task" = 123``) and unquoted spellings — and an explicit
    ``[model_ids.project]`` sub-table — resolve to the same ``project.task`` key,
    rather than mandating one spelling in the docs and failing confusingly on the
    other.
    """
    flat: dict[str, Any] = {}
    for key, value in table.items():
        name = f"{prefix}{key}"
        if isinstance(value, Mapping):
            flat.update(_flatten_model_id_table(value, f"{name}."))
        else:
            flat[name] = value
    return flat


def _parse_model_ids_env(raw: str) -> dict[str, str]:
    """Parse ``model:id`` pairs from :data:`MODEL_IDS_ENV_VAR`.

    Accepts comma- and/or whitespace-separated tokens, the same delimited-string
    convention ``resync_authors`` uses. A token without a ``:`` separator or with
    an empty model name is a typo that would otherwise silently drop an entry, so
    it raises rather than being skipped.

    :raises ValueError: When a token is not a well-formed ``model:id`` pair.
    """
    parsed: dict[str, str] = {}
    for token in re.split(r"[,\s]+", raw.strip()):
        if not token:
            continue
        model, separator, id_text = token.partition(":")
        if not separator or not model.strip():
            raise ValueError(_invalid_model_ids_env_message(token))
        parsed[model.strip()] = id_text.strip()
    return parsed


def _coerce_model_id(model: str, value: Any) -> int:
    """Coerce one raw ``[model_ids]`` value to a positive ``int``.

    Unlike :func:`_coerce_positive_int`, a bad value raises instead of degrading
    to a default: there is no sensible default for a record id, and a silently
    dropped entry would resurface later as an opaque XML-RPC fault on a write.
    ``bool`` is rejected explicitly because ``int(True)`` is ``1``, which would
    turn ``= true`` into a reference to record 1.

    :raises ValueError: When the value is not a positive integer.
    """
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(_invalid_model_id_message(model, value))
    try:
        number = int(str(value).strip())
    except ValueError:
        raise ValueError(_invalid_model_id_message(model, value)) from None
    if number <= 0:
        raise ValueError(_invalid_model_id_message(model, value))
    return number


def _coerce_model_id_map(values: Mapping[str, Any]) -> dict[str, int]:
    """Coerce every entry of a raw model-id mapping, rejecting bad values at load.

    :raises ValueError: When any value is not a positive integer.
    """
    return {
        str(model).strip(): _coerce_model_id(str(model).strip(), value)
        for model, value in values.items()
    }


def _resolve_model_ids(file_values: Mapping[str, Any]) -> dict[str, int]:
    """Merge the model-id map with File > Environment Variable > Default precedence.

    Precedence applies per model name, mirroring :func:`_resolve_section` (which
    resolves each *setting* independently): the two sources union, and a model
    named by both takes the file's id. The default is an empty map. A file entry
    with an empty value counts as unset, so the environment can still supply it.

    :raises ValueError: When any resolved value is not a positive integer, or the
        environment variable is malformed.
    """
    merged: dict[str, Any] = {}
    raw_env = os.environ.get(MODEL_IDS_ENV_VAR)
    if raw_env:
        merged.update(_parse_model_ids_env(raw_env))
    merged.update(
        {
            model: value
            for model, value in _flatten_model_id_table(file_values).items()
            if value not in (None, "")
        }
    )
    return _coerce_model_id_map(merged)


# ── model ids: the writer (#890) ──────────────────────────────────────────────
#
# The map stays the single source ``schedule_activity`` reads (#444, #686 are
# unchanged: nothing resolves ``ir.model`` at call time). What #890 adds is that
# the ``get_models`` command — the one place that already reads ``ir.model``,
# under its own gate — can fill an entry in once instead of the operator typing
# it. The edit is line-based rather than a parse-and-reserialize round trip so
# that comments, key order, spelling, and every unrelated section survive it: a
# config file is something a human wrote, and a writer that reformats it is a
# writer nobody runs twice.


def _model_ids_scope(header: str, is_toml: bool) -> Optional[str]:
    """Return the dotted key prefix a section header contributes, or ``None``.

    ``[model_ids]`` contributes ``""`` (its keys are whole model names) and, in
    TOML only, ``[model_ids.account]`` contributes ``"account."`` — the same
    sub-table spelling :func:`_flatten_model_id_table` reconciles on load. Any
    other header ends the section.
    """
    name = header.strip()[1:-1].strip()
    if name == _MODEL_IDS_SECTION:
        return ""
    if is_toml and name.startswith(f"{_MODEL_IDS_SECTION}."):
        return f"{name[len(_MODEL_IDS_SECTION) + 1:]}."
    return None


def _split_key_value(line: str, is_toml: bool) -> Optional[tuple[str, str]]:
    """Return ``(raw key, separator)`` for an entry line, or ``None``.

    Blank lines, comments (``#`` in both formats, ``;`` in INI) and section
    headers are not entries. INI accepts ``:`` as a delimiter alongside ``=``,
    so whichever appears first wins; TOML only has ``=``.
    """
    stripped = line.strip()
    if not stripped or stripped[0] in "#;[":
        return None
    separators = ("=",) if is_toml else ("=", ":")
    best: Optional[tuple[str, str]] = None
    for separator in separators:
        key, found, _ = stripped.partition(separator)
        if found and (best is None or len(key) < len(best[0])):
            best = (key.strip(), separator)
    return best if best and best[0] else None


def _unquote_toml_key(key: str) -> str:
    """Strip the surrounding quotes from a quoted TOML key, if any."""
    if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
        return key[1:-1]
    return key


def _rewrite_model_id(text: str, is_toml: bool, model: str, ir_model_id: int) -> str:
    """Return ``text`` with ``model``'s ``[model_ids]`` entry set to ``ir_model_id``.

    Updates the entry in place when the file already spells it in any of the
    shapes the loader accepts (quoted TOML key, unquoted dotted TOML key, TOML
    sub-table, INI key in any casing), appends to an existing ``[model_ids]``
    section when it does not, and creates the section at the end of the file
    when the file has none. Every other line is returned untouched.
    """
    lines = text.splitlines()
    target = model if is_toml else model.lower()
    scope: Optional[str] = None
    has_section = False
    insert_at: Optional[int] = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            scope = _model_ids_scope(stripped, is_toml)
            if scope == "":
                has_section = True
                insert_at = index + 1
            continue
        if scope is None:
            continue
        entry = _split_key_value(line, is_toml)
        if entry is None:
            continue
        raw_key, separator = entry
        name = scope + (_unquote_toml_key(raw_key) if is_toml else raw_key.lower())
        if scope == "":
            insert_at = index + 1
        if name == target:
            indent = line[: len(line) - len(line.lstrip())]
            lines[index] = f"{indent}{raw_key} {separator} {ir_model_id}"
            return "\n".join(lines) + "\n"
    new_entry = f'"{model}" = {ir_model_id}' if is_toml else f"{model} = {ir_model_id}"
    if has_section and insert_at is not None:
        lines.insert(insert_at, new_entry)
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend([f"[{_MODEL_IDS_SECTION}]", new_entry])
    return "\n".join(lines) + "\n"


def _resolve_writable_config_path(config_path: Optional[str]) -> Path:
    """Return the config file the ``[model_ids]`` writer should edit or create.

    Discovery first: an existing file found by :func:`_resolve_local_config_path`
    is always the one edited, so the writer never creates a second file that the
    loader would then shadow. Only when no file exists yet is a destination
    derived, following the same precedence — the ``$ODOO_SDK_CONFIG`` override
    (a directory, or a path with no suffix, takes ``config.toml`` inside it),
    else the default ``~/.config/odoo_sdk/config.toml``. The current working
    directory is deliberately not a creation target: a config dropped beside
    whatever directory a command happened to run in is a surprise.
    """
    existing = _resolve_local_config_path(config_path)
    if existing is not None:
        return existing
    candidate = config_path or os.environ.get(LOCAL_CONFIG_ENV_VAR)
    if candidate:
        path = Path(candidate).expanduser()
        if path.is_dir() or not path.suffix:
            return path / _CONFIG_DIR_FILENAMES[0]
        return path
    return Path(DEFAULT_CONFIG_DIR).expanduser() / _CONFIG_DIR_FILENAMES[0]


def _reread_model_id(path: Path, model: str) -> Optional[int]:
    """Return what the *loader* now reads for ``model`` from ``path``, file only.

    Deliberately bypasses :func:`_resolve_model_ids` so the environment override
    cannot make an unwritten entry look written (nor a malformed
    :data:`MODEL_IDS_ENV_VAR` make a written one look unwritten). ``None`` means
    the loader does not see the entry at all.
    """
    file_values = _load_local_config_file(str(path)).get(_MODEL_IDS_SECTION, {})
    flat = {
        name: value
        for name, value in _flatten_model_id_table(file_values).items()
        if value not in (None, "")
    }
    return _coerce_model_id_map(flat).get(str(model).strip())


def write_model_id(model: str, ir_model_id: int, config_path: Optional[str]) -> Path:
    """Persist one ``[model_ids]`` entry to the config file and return its path.

    The module-level writer behind :meth:`LocalConfig.set_model_id`. Written
    through a temporary file in the same directory and :func:`os.replace`, so a
    failure part-way cannot leave a half-written config behind, and then read
    back through the loader: the loader accepts spellings this line-based editor
    does not model (a root-level ``model_ids = {...}`` inline table, say), and a
    write the loader would read differently is a corrupted config. On any
    disagreement the original file is put back and the write is reported as a
    failure rather than silently believed.

    :param model: Odoo model name, e.g. ``project.task``.
    :param ir_model_id: The ``ir.model`` id to record; must be a positive int.
    :param config_path: Explicit config file or directory, or ``None`` to use
        the same discovery the loader does.
    :raises ValueError: When ``ir_model_id`` is not a positive integer.
    :raises ModelIdsNotWritableError: When the destination cannot be written, or
        the written file does not read back as intended.
    """
    coerced = _coerce_model_id(model, ir_model_id)
    path = _resolve_writable_config_path(config_path)
    existed = path.is_file()
    text = ""
    try:
        if existed:
            text = path.read_text(encoding="utf-8")
            if not os.access(path, os.W_OK):
                raise PermissionError(f"{path} is not writable")
        path.parent.mkdir(parents=True, exist_ok=True)
        updated = _rewrite_model_id(text, path.suffix != ".ini", model, coerced)
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temporary.write_text(updated, encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        raise ModelIdsNotWritableError(
            model_id_not_persisted_message(model, path, str(exc))
        ) from exc
    try:
        confirmed = _reread_model_id(path, model) == coerced
    except (ValueError, RuntimeError):
        # A ValueError means the rewritten file no longer loads; a RuntimeError
        # means no TOML parser is importable, which the read path would have hit
        # first. Either way the write cannot be trusted.
        confirmed = False
    if not confirmed:
        if existed:
            path.write_text(text, encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
        raise ModelIdsNotWritableError(
            model_id_not_persisted_message(
                model, path, "the file did not read back with the new entry"
            )
        )
    return path


# ── LocalConfig ───────────────────────────────────────────────────────────────

# Sensible defaults applied at the lowest precedence (File > Env > Default).
_CONNECTION_DEFAULTS: dict[str, Optional[str]] = {
    "url": None,
    "db": None,
    "username": None,
    "password": None,
    "api_key": None,
    "transport": "xmlrpc",
    # The concrete numeric default lives in ``DEFAULT_TIMEOUT_SECONDS``;
    # ``_build_connection_settings`` coerces this absent value into it.
    "timeout": None,
}

# String tokens interpreted as an enabled boolean flag. INI and environment
# values arrive as strings, so behavior flags stored as strings are coerced
# against this shared set (TOML booleans and defaults arrive as real bools).
_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})

# The fixed sessionization inactivity gap, in minutes. Seeded from the pure
# core's historical default (``DEFAULT_WINDOW_GAP_SECS`` = 3600s = 60 min). This
# gap is a stable session-identity constant, not a per-run tuning knob: it is
# what the SQL-derived read path uses to decide session boundaries.
_DEFAULT_SESSION_GAP_MINS = 60

# Billing policy defaults for the derived-session upload path (issue #355).
# A derived session bills its wall-clock span, but a single-event session spans
# zero seconds and a very short session rounds toward nothing, so raw span
# silently under-bills. ``min_session_hours`` is the floor every billable
# session is raised to; ``round_session_hours`` is the multiple the span is
# rounded to (nearest, half-up). A rounding step of ``0`` disables rounding.
_DEFAULT_MIN_SESSION_HOURS = 0.25
_DEFAULT_ROUND_SESSION_HOURS = 0.05

# Google Calendar / Gmail ingestion defaults (issue #370). ``calendar_tick_mins``
# is the constant interval at which a meeting is expanded into synthetic point
# events so the existing gap derivation reconstructs it as one session; it MUST
# stay strictly below the session gap and the sweep floor (validated at resync,
# acceptance #11). ``ingest_subjects`` controls whether an ingested meeting/email
# subject is stored. ``google_sync_window_days`` is the backward/forward reconcile
# window (calendar mutates retroactively, so the window looks both ways).
_DEFAULT_CALENDAR_TICK_MINS = 5
_DEFAULT_INGEST_SUBJECTS = True
_DEFAULT_GOOGLE_SYNC_WINDOW_DAYS = 30

# Resync-capture defaults (issue #378). ``day_bucket_tz`` is the IANA timezone the
# scoring/rendering day-bucketing uses; it was a hardcoded EDT offset that
# mis-bucketed the US-Central user's midnight-crossing evening sessions, so it is
# now config-driven and defaults to US Central. ``resync_window_days`` bounds the
# git ``--since`` / GitHub / chatter resync queries so re-runs stay cheap.
# ``resync_authors`` is the list of author identities (GitHub logins and/or git
# emails) the pullers capture for; empty means "the active login / git email".
_DEFAULT_DAY_BUCKET_TZ = "America/Chicago"
_DEFAULT_RESYNC_WINDOW_DAYS = 30

# Environment variables that override behavior settings when no file value is set.
_BEHAVIOR_ENV_VARS: dict[str, str] = {
    "profiling": "ODOO_PROFILING",
    "session_gap_mins": "ODOO_SESSION_GAP_MINS",
    "min_session_hours": "ODOO_MIN_SESSION_HOURS",
    "round_session_hours": "ODOO_ROUND_SESSION_HOURS",
    "calendar_tick_mins": "ODOO_CALENDAR_TICK_MINS",
    "ingest_subjects": "ODOO_INGEST_SUBJECTS",
    "google_sync_window_days": "ODOO_GOOGLE_SYNC_WINDOW_DAYS",
    "google_token_path": "ODOO_GOOGLE_TOKEN_PATH",
    "day_bucket_tz": "ODOO_DAY_BUCKET_TZ",
    "resync_window_days": "ODOO_RESYNC_WINDOW_DAYS",
    "resync_authors": "ODOO_RESYNC_AUTHORS",
}

# Sensible defaults for the reserved [behavior] section.
_BEHAVIOR_DEFAULTS: dict[str, Any] = {
    "profiling": False,
    "session_gap_mins": _DEFAULT_SESSION_GAP_MINS,
    "min_session_hours": _DEFAULT_MIN_SESSION_HOURS,
    "round_session_hours": _DEFAULT_ROUND_SESSION_HOURS,
    "calendar_tick_mins": _DEFAULT_CALENDAR_TICK_MINS,
    "ingest_subjects": _DEFAULT_INGEST_SUBJECTS,
    "google_sync_window_days": _DEFAULT_GOOGLE_SYNC_WINDOW_DAYS,
    "google_token_path": None,
    "day_bucket_tz": _DEFAULT_DAY_BUCKET_TZ,
    "resync_window_days": _DEFAULT_RESYNC_WINDOW_DAYS,
    "resync_authors": None,
}


class LocalConfig:
    """Resolved, read-only SDK settings promoted to the local state layer.

    The single settings resolver, injected into commands as a peer dependency
    alongside ``OdooClient`` and ``LocalStateClient``. Each setting is resolved with
    the precedence **File > Environment Variable > Sensible Default**, so the local
    config file always wins when present.
    """

    def __init__(
        self,
        connection: Optional[Mapping[str, Optional[str]]] = None,
        behavior: Optional[Mapping[str, Any]] = None,
        model_ids: Optional[Mapping[str, Any]] = None,
        config_path: Optional[str] = None,
    ):
        # Remembered rather than resolved: the loader's override may name a file
        # OR a directory, and :meth:`set_model_id` has to re-run the same
        # resolution to decide what to edit or create (#890).
        self._config_path = config_path
        self._connection: dict[str, Optional[str]] = {
            **_CONNECTION_DEFAULTS,
            **(dict(connection) if connection else {}),
        }
        self._behavior: dict[str, Any] = {
            **_BEHAVIOR_DEFAULTS,
            **(dict(behavior) if behavior else {}),
        }
        # Coerced here rather than only in ``load`` so a directly constructed
        # config (tests, embedding callers) rejects a bad id just as loudly.
        self._model_ids: dict[str, int] = _coerce_model_id_map(model_ids or {})

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> "LocalConfig":
        """Resolve settings from file, environment, and defaults.

        When ``config_path`` is omitted the ``ODOO_SDK_CONFIG`` env var and the
        default discovery locations are consulted.

        :raises ValueError: When a ``[model_ids]`` entry is not a positive integer
            or ``ODOO_MODEL_IDS`` is malformed.
        """
        file_data = _load_local_config_file(config_path)
        connection = _resolve_section(
            file_data.get("connection", {}),
            CONNECTION_ENV_VARS,
            _CONNECTION_DEFAULTS,
        )
        behavior = _resolve_section(
            file_data.get("behavior", {}),
            _BEHAVIOR_ENV_VARS,
            _BEHAVIOR_DEFAULTS,
        )
        # ``[model_ids]`` has open-ended keys, so it cannot go through
        # ``_resolve_section`` (which walks a fixed key set) and carries its own
        # resolver instead.
        model_ids = _resolve_model_ids(file_data.get(_MODEL_IDS_SECTION, {}))
        return cls(
            connection=connection,
            behavior=behavior,
            model_ids=model_ids,
            config_path=config_path,
        )

    @property
    def connection(self) -> Mapping[str, Optional[str]]:
        """Return the resolved connection settings as a read-only mapping."""
        return dict(self._connection)

    @property
    def behavior(self) -> Mapping[str, Any]:
        """Return the resolved behavior settings as a read-only mapping."""
        return dict(self._behavior)

    @property
    def model_ids(self) -> dict[str, int]:
        """Return the resolved model-name to ``ir.model`` id map (a copy, #686).

        The map is managed by hand precisely so no code path has to read
        ``ir.model`` to turn a model name into the id a ``res_model_id`` /
        ``model_id`` field or a model-reference domain wants. Empty by default.
        """
        return dict(self._model_ids)

    def model_id(self, model: str) -> Optional[int]:
        """Return the configured ``ir.model`` id for ``model``, or ``None`` (#686).

        The non-raising lookup, for callers that have their own fallback. A caller
        that needs the id to proceed should use :meth:`require_model_id` so the
        user gets the actionable "add this config entry" message instead of a bare
        ``None``.
        """
        return self._model_ids.get(model)

    def require_model_id(self, model: str) -> int:
        """Return the configured ``ir.model`` id for ``model``, or raise (#686).

        Never falls back to reading ``ir.model``: that administrative table must
        not be granted to a least-privileged service account (#444), and probing
        it is exactly the defect this map exists to remove. The Epic C error
        boundary renders the raised ``ValueError`` as
        ``{"error": {"type": "ValueError", "message": <the message>}}``, so an LLM
        caller sees the config entry to add.

        :raises ValueError: When ``model`` is absent from the map.
        """
        resolved = self._model_ids.get(model)
        if resolved is None:
            raise ValueError(model_id_unavailable_message(model))
        return resolved

    def set_model_id(self, model: str, ir_model_id: int) -> Path:
        """Write one ``[model_ids]`` entry to the config file, in place (#890).

        The only writer in this otherwise read-only resolver, and deliberately
        narrow: it touches exactly one key of one section, preserving every
        other section, key, comment and spelling in the file. The environment
        override (:data:`MODEL_IDS_ENV_VAR`) is never written and never read
        here — it keeps winning or losing by the same precedence as before.

        The in-memory map is updated to match, so a caller that persists and
        then reads back within the same process sees the new id.

        :param model: Odoo model name, e.g. ``project.task``.
        :param ir_model_id: The ``ir.model`` id to record.
        :return: The config file that was written.
        :raises ValueError: When ``ir_model_id`` is not a positive integer.
        :raises ModelIdsNotWritableError: When the config file cannot be written;
            callers report this rather than failing (the hand-edit path still
            works, and the message says so).
        """
        path = write_model_id(model, ir_model_id, self._config_path)
        self._model_ids[str(model).strip()] = _coerce_model_id(model, ir_model_id)
        return path

    def get(self, key: str, default: Any = None) -> Any:
        """Return one resolved behavior setting, or ``default`` when absent."""
        return self._behavior.get(key, default)

    @property
    def profiling(self) -> bool:
        """Return whether per-call MCP profiling is enabled (``[behavior] profiling``)."""
        return _coerce_flag(self._behavior.get("profiling", False))

    @property
    def session_gap_mins(self) -> int:
        """Return the fixed sessionization inactivity gap in minutes (default ``60``).

        A stable session-identity constant: sessions are runs separated by more than
        this gap, and the value must not change per query or the identity of
        already-detected sessions would shift. An invalid value falls back to the
        default rather than raising.
        """
        return _coerce_positive_int(
            self._behavior.get("session_gap_mins"), _DEFAULT_SESSION_GAP_MINS
        )

    @property
    def session_gap_secs(self) -> int:
        """Return the fixed sessionization inactivity gap in whole seconds."""
        return self.session_gap_mins * 60

    @property
    def min_session_hours(self) -> float:
        """Return the per-session billing floor in hours (default ``0.25``, #355).

        Every billable derived session is raised to at least this many hours, so a
        single-event session (zero wall-clock span) bills the minimum rather than
        nothing. ``0`` is honoured (no floor); an invalid value falls back to the
        default.
        """
        return _coerce_non_negative_float(
            self._behavior.get("min_session_hours"), _DEFAULT_MIN_SESSION_HOURS
        )

    @property
    def round_session_hours(self) -> float:
        """Return the per-session rounding step in hours (default ``0.05``, #355).

        A billable session's wall-clock span is rounded to the nearest multiple of
        this step (half-up). ``0`` is honoured and disables rounding (the raw span is
        billed, subject to the minimum); an invalid value falls back to the default.
        """
        return _coerce_non_negative_float(
            self._behavior.get("round_session_hours"), _DEFAULT_ROUND_SESSION_HOURS
        )

    @property
    def calendar_tick_mins(self) -> int:
        """Return the meeting-expansion tick interval in minutes (default ``5``, #370).

        A meeting is expanded into synthetic point events this many minutes apart
        (with a terminal tick on the exact end) so the gap derivation reconstructs it
        as a single session. The invariant that this stay strictly below both the
        session gap and the sweep floor is enforced at resync, not here. An invalid
        value falls back to the default.
        """
        return _coerce_positive_int(
            self._behavior.get("calendar_tick_mins"), _DEFAULT_CALENDAR_TICK_MINS
        )

    @property
    def ingest_subjects(self) -> bool:
        """Return whether ingested meeting/email subjects are stored (default on, #370).

        Any non-truthy string disables subject capture so a client's correspondence
        titles can be kept out of the central DB.
        """
        return _coerce_flag(
            self._behavior.get("ingest_subjects", _DEFAULT_INGEST_SUBJECTS)
        )

    @property
    def google_sync_window_days(self) -> int:
        """Return the Google reconcile window radius in days (default ``30``, #370).

        Calendar mutates retroactively and the sent window is backward-looking, so a
        resync reconciles events within this many days each side of now. An invalid
        value falls back to the default.
        """
        return _coerce_positive_int(
            self._behavior.get("google_sync_window_days"),
            _DEFAULT_GOOGLE_SYNC_WINDOW_DAYS,
        )

    @property
    def google_token_path(self) -> Optional[str]:
        """Return an explicit Google token file path override, or None (#370).

        When unset the puller derives the path from the existing ``ODOO_SDK_CONFIG``
        mount, so the token lives beside the other host-provisioned SDK config.
        """
        value = self._behavior.get("google_token_path")
        return str(value) if value else None

    @property
    def day_bucket_tz(self) -> ZoneInfo:
        """Return the day-bucketing timezone (default ``America/Chicago``, #378).

        Scoring and rendering bucket a session's wall-clock span onto a calendar day
        in this zone; a wrong zone mis-buckets evening sessions that cross local
        midnight. An unknown or malformed IANA key falls back to the default.
        """
        value = self._behavior.get("day_bucket_tz") or _DEFAULT_DAY_BUCKET_TZ
        try:
            return ZoneInfo(str(value))
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo(_DEFAULT_DAY_BUCKET_TZ)

    @property
    def resync_window_days(self) -> int:
        """Return the resync-capture window radius in days (default ``30``, #378).

        Bounds the git ``--since``, GitHub, and Odoo-chatter resync queries so a
        re-run scans only recent history. An invalid value falls back to the default.
        """
        return _coerce_positive_int(
            self._behavior.get("resync_window_days"), _DEFAULT_RESYNC_WINDOW_DAYS
        )

    @property
    def resync_authors(self) -> list[str]:
        """Return the configured resync author identities in first-seen order (#378 item 4).

        The author identities the GitHub/git pullers capture work for — GitHub logins
        and/or git commit emails (an entry with ``@`` is treated as an email matched
        against ``git log``; one without as a GitHub login). Accepts a TOML list or a
        comma/whitespace-separated string. When empty, each puller falls back to its
        active identity, so single-account users need no config.
        """
        value = self._behavior.get("resync_authors")
        if value in (None, ""):
            return []
        raw = (
            value
            if isinstance(value, (list, tuple))
            else re.split(r"[,\s]+", str(value))
        )
        seen: list[str] = []
        for item in raw:
            identity = str(item).strip()
            if identity and identity not in seen:
                seen.append(identity)
        return seen

    def connection_settings(self) -> OdooConnectionSettings:
        """Build validated :class:`OdooConnectionSettings` from the resolved values.

        The connection mapping has already been resolved (File > Env > Default) by
        :meth:`load`, so this validates and coerces it directly.

        :raises ValueError: When required connection settings are unresolved.
        """
        return _build_connection_settings(self._connection)


def _resolve_local_config_path(config_path: Optional[str]) -> Optional[Path]:
    """Return the config file to read, honoring the override, env var, defaults.

    The explicit override and the ``ODOO_SDK_CONFIG`` env var may name either a
    config **file** or a **directory**; a directory is probed for ``config.toml``
    then ``config.ini`` so the devcontainer feature can point ``ODOO_SDK_CONFIG``
    at its mounted config directory regardless of which file the user created.
    When no override applies, the current working directory and then the default
    ``~/.config/odoo_sdk`` directory are searched. Only an existing file is
    returned; otherwise ``None`` so callers fall back to environment variables and
    defaults.
    """
    candidate = config_path or os.environ.get(LOCAL_CONFIG_ENV_VAR)
    if candidate:
        return _resolve_config_candidate(Path(candidate).expanduser())
    for basename in _CWD_CONFIG_BASENAMES:
        cwd_path = Path(basename)
        if cwd_path.is_file():
            return cwd_path
    return _probe_config_dir(Path(DEFAULT_CONFIG_DIR).expanduser())


def _resolve_config_candidate(path: Path) -> Optional[Path]:
    """Resolve an explicit or env-provided candidate to an existing config file.

    A directory candidate is probed for the known config filenames; a file
    candidate is returned when it exists; anything else yields ``None`` (and, per
    the single-override contract, does not fall through to the default locations).
    """
    if path.is_dir():
        return _probe_config_dir(path)
    return path if path.is_file() else None


def _probe_config_dir(directory: Path) -> Optional[Path]:
    """Return the first existing ``config.toml`` / ``config.ini`` in ``directory``."""
    for filename in _CONFIG_DIR_FILENAMES:
        candidate = directory / filename
        if candidate.is_file():
            return candidate
    return None


def _load_local_config_file(config_path: Optional[str]) -> dict[str, dict[str, Any]]:
    """Load the ``[connection]``, ``[behavior]``, and ``[model_ids]`` sections.

    Supports TOML (``.toml``) and INI files. Returns an empty mapping when no
    file applies.
    """
    path = _resolve_local_config_path(config_path)
    if path is None:
        return {}
    if path.suffix == ".toml":
        return _load_toml_sections(path)
    return _load_ini_sections(path)


def _import_toml_module() -> ModuleType:
    """Return a module exposing ``load(binary_file)``, tolerating Python 3.10.

    ``tomllib`` is stdlib only from Python 3.11, but the supported floor is 3.10
    (``odoo:17`` ships 3.10), where the ``tomli`` backport provides the identical
    ``load`` API. Probed in order and imported lazily, so ``import odoo_sdk``
    never depends on a TOML parser and INI-only hosts are unaffected.

    :raises RuntimeError: When neither module is importable.
    """
    for module_name in _TOML_MODULE_NAMES:
        try:
            return importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
    raise RuntimeError(
        "Reading a TOML config file needs the stdlib 'tomllib' (Python 3.11+) or "
        "the 'tomli' backport on Python 3.10; neither is importable. Install "
        "'tomli' or use an INI config file instead."
    )


def _load_toml_sections(path: Path) -> dict[str, dict[str, Any]]:
    """Parse the ``connection``, ``behavior``, and ``model_ids`` tables from TOML.

    ``model_ids`` is handed back as parsed, nesting and all;
    :func:`_flatten_model_id_table` reconciles the dotted-key spelling at resolve
    time.
    """
    toml = _import_toml_module()
    with path.open("rb") as handle:
        data = toml.load(handle)
    return {
        "connection": dict(data.get("connection", {})),
        "behavior": dict(data.get("behavior", {})),
        _MODEL_IDS_SECTION: dict(data.get(_MODEL_IDS_SECTION, {})),
    }


def _load_ini_sections(path: Path) -> dict[str, dict[str, Any]]:
    """Parse the ``connection`` and ``behavior`` sections from an INI file.

    ``[odoo]`` is accepted as an alias for ``[connection]`` (used only when no
    explicit ``[connection]`` section is present) so an already-persisted
    ``~/.config/odoo_sdk/config.ini`` keeps working unchanged.

    Deliberate: ``configparser.optionxform`` lowercases option names, so a
    ``[model_ids]`` key is read back lower-cased. Odoo model names are lowercase
    by construction, so this is left at the default rather than overridden — but
    it means ``Project.Task`` and ``project.task`` are the same INI entry.
    """
    parser = configparser.ConfigParser()
    parser.read(path)
    sections: dict[str, dict[str, Any]] = {}
    if parser.has_section(_CONNECTION_SECTION):
        sections["connection"] = dict(parser.items(_CONNECTION_SECTION))
    elif parser.has_section(_CONNECTION_SECTION_ALIAS):
        sections["connection"] = dict(parser.items(_CONNECTION_SECTION_ALIAS))
    if parser.has_section(_BEHAVIOR_SECTION):
        sections["behavior"] = dict(parser.items(_BEHAVIOR_SECTION))
    if parser.has_section(_MODEL_IDS_SECTION):
        # ``parser.items(section)`` folds in ``[DEFAULT]`` keys. The fixed-key
        # sections above ignore anything they do not recognize, but a model-id
        # entry that fails to parse raises, so an unrelated ``[DEFAULT]`` value
        # would turn into a hard load error; drop the defaults here.
        defaults = parser.defaults()
        sections[_MODEL_IDS_SECTION] = {
            key: value
            for key, value in parser.items(_MODEL_IDS_SECTION)
            if key not in defaults
        }
    return sections


def _resolve_section(
    file_values: Mapping[str, Any],
    env_vars: Mapping[str, str],
    defaults: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge one section with File > Environment Variable > Default precedence."""
    keys = set(defaults) | set(env_vars) | set(file_values)
    resolved: dict[str, Any] = {}
    for key in keys:
        if key in file_values and file_values[key] not in (None, ""):
            resolved[key] = file_values[key]
            continue
        env_name = env_vars.get(key)
        env_value = os.environ.get(env_name) if env_name else None
        if env_value not in (None, ""):
            resolved[key] = env_value
            continue
        resolved[key] = defaults.get(key)
    return resolved
