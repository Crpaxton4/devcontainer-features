"""Settings read from ``$ODOO_DEV_HOME/config.toml``.

:class:`LocalConfig` resolves each setting of the ``[connection]`` and
``[behavior]`` tables, and the open-ended ``[model_ids]`` table. The
precedence for every key is:

    File  >  Environment Variable  >  Sensible Default

The file is TOML only and has one location: ``paths(resolve_home(env)).config_toml``.
A missing file is not an error. The environment and the defaults then apply.

``[model_ids]`` maps an Odoo model name to its ``ir.model`` id. Nothing reads
``ir.model`` at call time. :meth:`LocalConfig.set_model_id` writes one entry.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from odoo_dev_data.home import paths, resolve_home

MODEL_IDS_ENV_VAR = "ODOO_MODEL_IDS"

CONNECTION_ENV_VARS = {
    "url": "ODOO_URL",
    "db": "ODOO_DB",
    "username": "ODOO_USERNAME",
    "password": "ODOO_PASSWORD",
    "api_key": "ODOO_API_KEY",
    "transport": "ODOO_TRANSPORT",
    "timeout": "ODOO_TIMEOUT",
}
CONNECTION_DEFAULTS: dict[str, Any] = {
    "url": None,
    "db": None,
    "username": None,
    "password": None,
    "api_key": None,
    "transport": "xmlrpc",
    "timeout": None,
}
BEHAVIOR_ENV_VARS: dict[str, str] = {
    "profiling": "ODOO_PROFILING",
    "session_gap_mins": "ODOO_SESSION_GAP_MINS",
    "min_session_hours": "ODOO_MIN_SESSION_HOURS",
    "round_session_hours": "ODOO_ROUND_SESSION_HOURS",
    "calendar_tick_mins": "ODOO_CALENDAR_TICK_MINS",
    "ingest_subjects": "ODOO_INGEST_SUBJECTS",
    "day_bucket_tz": "ODOO_DAY_BUCKET_TZ",
    "resync_window_days": "ODOO_RESYNC_WINDOW_DAYS",
    "resync_authors": "ODOO_RESYNC_AUTHORS",
}
BEHAVIOR_DEFAULTS: dict[str, Any] = {
    "profiling": False,
    "session_gap_mins": 60,
    "min_session_hours": 0.25,
    "round_session_hours": 0.05,
    "calendar_tick_mins": 5,
    "ingest_subjects": True,
    "day_bucket_tz": "America/Chicago",
    "resync_window_days": 30,
    "resync_authors": None,
}

_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})
_MODEL_IDS_HEADER = re.compile(r"^\s*\[model_ids\]\s*(#.*)?$")


def _coerce_positive_int(value: Any, default: int) -> int:
    """Coerce a raw value to a positive ``int``, else ``default``."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _coerce_non_negative_float(value: Any, default: float) -> float:
    """Coerce a raw value to a finite float ``>= 0``, else ``default``."""
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number >= 0 and number != float("inf") else default


def _coerce_flag(value: Any) -> bool:
    """Coerce a bool or a truthy token (``1``/``true``/``yes``/``on``) to a flag."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUTHY_VALUES


# -- model ids ---------------------------------------------------------------


def model_id_unavailable_message(model: str) -> str:
    """Return the error text for a model missing from ``[model_ids]``."""
    return (
        f"No ir.model id is configured for {model!r}. Add it to the "
        f'[model_ids] table of config.toml ("{model}" = <id>) or export '
        f'{MODEL_IDS_ENV_VAR}="{model}:<id>". Nothing reads ir.model at call '
        "time, because a least-privileged service account must not hold that "
        "privilege. An operator who does hold it can look the id up, and write "
        f"it into config.toml once: {persist_model_id_command(model)}"
    )


def persist_model_id_command(model: str) -> str:
    """Return the CLI command that resolves the id of ``model`` and writes it."""
    return f"""odoo-dev cmd get_models --args '{{"persist": ["{model}"]}}'"""


def model_id_not_persisted_message(model: str, path: Any, reason: str) -> str:
    """Return the warning text for an id that was resolved but not written."""
    return (
        f"Resolved the ir.model id for {model!r} but could not write it to "
        f"{path}: {reason}. Add it by hand instead "
        f'("{model}" = <id>) or export {MODEL_IDS_ENV_VAR}="{model}:<id>".'
    )


class ModelIdsNotWritableError(RuntimeError):
    """Raised when the ``[model_ids]`` table cannot be written to disk.

    A bad id raises :class:`ValueError`. An unwritable file is an environment
    fact that the caller reports and carries on from.
    """


def _invalid_model_id_message(model: str, value: Any) -> str:
    return (
        f"Invalid [model_ids] entry for {model!r}: {value!r}. An ir.model id must "
        "be a positive integer."
    )


def _invalid_model_ids_env_message(token: str) -> str:
    return (
        f"Invalid {MODEL_IDS_ENV_VAR} entry {token!r}: expected comma- or "
        f'whitespace-separated "model:id" pairs, e.g. '
        f'{MODEL_IDS_ENV_VAR}="project.task:123,res.partner:77".'
    )


def _flatten_model_id_table(
    table: Mapping[str, Any], prefix: str = ""
) -> dict[str, Any]:
    """Flatten nested TOML tables into dotted model names.

    An unquoted ``project.task = 123`` is a dotted key, so TOML parses it as
    ``{"project": {"task": 123}}``. Flattening makes every spelling resolve to
    the same ``project.task`` key.
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
    """Parse comma- or whitespace-separated ``model:id`` pairs.

    :raises ValueError: When a token is not a well-formed pair.
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

    A bad value raises. A record id has no sensible default. ``bool`` is
    rejected because ``int(True)`` is ``1``.

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
    """Coerce every entry of a raw model-id mapping.

    :raises ValueError: When any value is not a positive integer.
    """
    return {
        str(model).strip(): _coerce_model_id(str(model).strip(), value)
        for model, value in values.items()
    }


def _resolve_model_ids(
    file_values: Mapping[str, Any], env: Mapping[str, str]
) -> dict[str, int]:
    """Merge the model-id map. The file wins per model name, the sources union.

    A file entry with an empty value counts as unset.

    :raises ValueError: When a value is not a positive integer or the
        environment variable is malformed.
    """
    merged: dict[str, Any] = {}
    raw_env = env.get(MODEL_IDS_ENV_VAR)
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


def _resolve_section(
    file_values: Mapping[str, Any],
    env_vars: Mapping[str, str],
    defaults: Mapping[str, Any],
    env: Mapping[str, str],
) -> dict[str, Any]:
    """Resolve one fixed-key table. Empty and ``None`` count as unset."""
    resolved: dict[str, Any] = {}
    for key in set(defaults) | set(env_vars) | set(file_values):
        env_name = env_vars.get(key)
        for candidate in (
            file_values.get(key),
            env.get(env_name) if env_name else None,
        ):
            if candidate not in (None, ""):
                resolved[key] = candidate
                break
        else:
            resolved[key] = defaults.get(key)
    return resolved


# -- connection values ---------------------------------------------------------


@dataclass(frozen=True)
class ConnectionValues:
    """Plain connection values. The Odoo client that consumes them validates them."""

    url: str | None = None
    db: str | None = None
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    api_key: str | None = field(default=None, repr=False)
    transport: str = "xmlrpc"
    timeout: str | float | None = None


def connection_values(config: LocalConfig, **explicit: object) -> ConnectionValues:
    """Build :class:`ConnectionValues` from ``config`` plus explicit overrides.

    An explicit value that is not ``None`` wins over the resolved value. See
    the module docstring for the rest of the precedence.

    :raises TypeError: When a keyword is not a connection field.
    """
    names = {item.name for item in fields(ConnectionValues)}
    unknown = sorted(set(explicit) - names)
    if unknown:
        raise TypeError(f"unknown connection setting(s): {', '.join(unknown)}")
    values = dict(config.connection)
    values.update({k: v for k, v in explicit.items() if v is not None})
    return ConnectionValues(
        **{name: values.get(name) for name in names if name in values}
    )


# -- the config ----------------------------------------------------------------


class LocalConfig:
    """Resolved settings. Read-only except for :meth:`set_model_id`."""

    def __init__(
        self,
        connection: Mapping[str, Any] | None = None,
        behavior: Mapping[str, Any] | None = None,
        model_ids: Mapping[str, Any] | None = None,
        path: Path | None = None,
    ):
        self.path = path if path is not None else paths(resolve_home()).config_toml
        self._connection: dict[str, Any] = {**CONNECTION_DEFAULTS, **(connection or {})}
        self._behavior: dict[str, Any] = {**BEHAVIOR_DEFAULTS, **(behavior or {})}
        # Coerced here so a directly built config rejects a bad id as loudly.
        self._model_ids: dict[str, int] = _coerce_model_id_map(model_ids or {})

    @classmethod
    def load(
        cls, path: Path | None = None, env: Mapping[str, str] | None = None
    ) -> LocalConfig:
        """Resolve the settings from the file, ``env`` (default ``os.environ``) and defaults.

        :raises ValueError: When a ``[model_ids]`` entry is not a positive
            integer or ``ODOO_MODEL_IDS`` is malformed.
        :raises tomllib.TOMLDecodeError: When the file is not valid TOML.
        """
        env = os.environ if env is None else env
        path = path if path is not None else paths(resolve_home(env)).config_toml
        data = tomllib.loads(path.read_text("utf-8")) if path.is_file() else {}
        return cls(
            connection=_resolve_section(
                data.get("connection", {}),
                CONNECTION_ENV_VARS,
                CONNECTION_DEFAULTS,
                env,
            ),
            behavior=_resolve_section(
                data.get("behavior", {}), BEHAVIOR_ENV_VARS, BEHAVIOR_DEFAULTS, env
            ),
            model_ids=_resolve_model_ids(data.get("model_ids", {}), env),
            path=path,
        )

    @property
    def connection(self) -> Mapping[str, Any]:
        """Return a copy of the resolved connection settings."""
        return dict(self._connection)

    @property
    def behavior(self) -> Mapping[str, Any]:
        """Return a copy of the resolved behavior settings."""
        return dict(self._behavior)

    @property
    def model_ids(self) -> dict[str, int]:
        """Return a copy of the model-name to ``ir.model`` id map."""
        return dict(self._model_ids)

    def model_id(self, model: str) -> int | None:
        """Return the configured id for ``model``, or ``None``."""
        return self._model_ids.get(model)

    def require_model_id(self, model: str) -> int:
        """Return the configured id for ``model``.

        :raises ValueError: When ``model`` is absent from the map.
        """
        resolved = self._model_ids.get(model)
        if resolved is None:
            raise ValueError(model_id_unavailable_message(model))
        return resolved

    def set_model_id(self, model: str, ir_model_id: int) -> Path:
        """Write one entry into the ``[model_ids]`` table of :attr:`path`.

        Only the ``[model_ids]`` block is rewritten, as sorted ``"model" = id``
        lines merged from the existing table and the new entry. Comments inside
        that block are not preserved. Every other byte of the file stays. The
        write is atomic, and the entry is read back before the call returns.
        The directory of the file must exist.

        :return: The file that was written.
        :raises ValueError: When ``ir_model_id`` is not a positive integer.
        :raises ModelIdsNotWritableError: When the file cannot be written.
        """
        name = str(model).strip()
        number = _coerce_model_id(name, ir_model_id)
        try:
            text = self.path.read_text("utf-8") if self.path.exists() else ""
            existing = _flatten_model_id_table(tomllib.loads(text).get("model_ids", {}))
            merged = {**_coerce_model_id_map(existing), name: number}
            _write_atomic(self.path, _replace_model_ids_block(text, merged))
            written = tomllib.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError) as error:  # TOMLDecodeError is a ValueError
            raise ModelIdsNotWritableError(
                model_id_not_persisted_message(name, self.path, str(error))
            ) from error
        if _flatten_model_id_table(written.get("model_ids", {})).get(name) != number:
            raise ModelIdsNotWritableError(
                model_id_not_persisted_message(name, self.path, "read-back mismatch")
            )
        self._model_ids[name] = number
        return self.path

    def get(self, key: str, default: Any = None) -> Any:
        """Return one resolved behavior setting, or ``default`` when absent."""
        return self._behavior.get(key, default)

    @property
    def profiling(self) -> bool:
        """Return whether per-call profiling is enabled."""
        return _coerce_flag(self._behavior.get("profiling", False))

    @property
    def session_gap_mins(self) -> int:
        """Return the sessionization inactivity gap in minutes (default ``60``)."""
        return _coerce_positive_int(self._behavior.get("session_gap_mins"), 60)

    @property
    def session_gap_secs(self) -> int:
        """Return the sessionization inactivity gap in seconds."""
        return self.session_gap_mins * 60

    @property
    def min_session_hours(self) -> float:
        """Return the per-session billing floor in hours (default ``0.25``)."""
        return _coerce_non_negative_float(self._behavior.get("min_session_hours"), 0.25)

    @property
    def round_session_hours(self) -> float:
        """Return the per-session rounding step in hours (default ``0.05``; ``0`` disables)."""
        return _coerce_non_negative_float(
            self._behavior.get("round_session_hours"), 0.05
        )

    @property
    def calendar_tick_mins(self) -> int:
        """Return the meeting-expansion tick interval in minutes (default ``5``)."""
        return _coerce_positive_int(self._behavior.get("calendar_tick_mins"), 5)

    @property
    def ingest_subjects(self) -> bool:
        """Return whether ingested meeting and email subjects are stored (default on)."""
        return _coerce_flag(self._behavior.get("ingest_subjects", True))

    @property
    def day_bucket_tz(self) -> ZoneInfo:
        """Return the day-bucketing timezone. An invalid key gives ``America/Chicago``."""
        default = BEHAVIOR_DEFAULTS["day_bucket_tz"]
        try:
            return ZoneInfo(str(self._behavior.get("day_bucket_tz") or default))
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo(default)

    @property
    def resync_window_days(self) -> int:
        """Return the resync-capture window radius in days (default ``30``)."""
        return _coerce_positive_int(self._behavior.get("resync_window_days"), 30)

    @property
    def resync_authors(self) -> list[str]:
        """Return the resync author identities in first-seen order.

        Accepts a TOML list or a comma- or whitespace-separated string.
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


# -- the [model_ids] writer ----------------------------------------------------


def _replace_model_ids_block(text: str, entries: Mapping[str, int]) -> str:
    """Return ``text`` with its ``[model_ids]`` block replaced, or appended."""
    block = ["[model_ids]\n"] + [
        f'"{model}" = {entries[model]}\n' for model in sorted(entries)
    ]
    lines = text.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if _MODEL_IDS_HEADER.match(l)), None)
    if start is None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        return "".join(lines + (["\n"] if lines else []) + block)
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("[")),
        len(lines),
    )
    if end < len(lines):
        block.append("\n")
    return "".join(lines[:start] + block + lines[end:])


def _write_atomic(path: Path, text: str) -> None:
    """Write ``text`` through a temp file in the same directory and ``os.replace``."""
    if not path.parent.is_dir():
        raise OSError(f"directory {path.parent} does not exist")
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".config-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        if path.exists():
            os.chmod(temp_name, path.stat().st_mode & 0o777)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
