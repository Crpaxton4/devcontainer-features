from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Sequence, TYPE_CHECKING, Union

from ..utils import Record
from ..utils.types import DomainInput
from .domain_expression import DomainExpression

if TYPE_CHECKING:
    from .odoo_env import OdooEnv


_logger = logging.getLogger(__name__)


class OdooRecordset:
    """Immutable record identity bound to an environment and model."""

    def __init__(
        self,
        env: OdooEnv,
        model_name: str,
        ids: Union[int, Sequence[int]] = (),
    ):
        self._env = env
        self._model_name = model_name
        self._ids = self._normalize_ids(ids)

    @staticmethod
    def _normalize_ids(ids: Union[int, Sequence[int]]) -> tuple[int, ...]:
        if isinstance(ids, int):
            return (ids,)
        return tuple(ids)

    @property
    def env(self) -> OdooEnv:
        """Returns the bound environment for subsequent record operations."""
        return self._env

    @property
    def model_name(self) -> str:
        """Returns the model name carried by this recordset."""
        return self._model_name

    @property
    def ids(self) -> tuple[int, ...]:
        """Returns the ordered record ids as immutable identity state."""
        return self._ids

    def _execute(self, method: str, *args: Any, **kwargs: Any) -> Any:
        return self._env.executor.execute(self._model_name, method, *args, **kwargs)

    def _context_kwargs(self) -> Dict[str, Any]:
        context = self._env.context
        if not context:
            return {}
        return {"context": context}

    def _serialized_domain(self, domain: DomainInput) -> list[Any]:
        return DomainExpression.normalize(domain).serialize()

    def _search_kwargs(
        self,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[str] = None,
    ) -> Dict[str, Any]:
        kwargs = self._context_kwargs()
        if limit is not None:
            kwargs["limit"] = limit
        if offset is not None:
            kwargs["offset"] = offset
        if order is not None:
            kwargs["order"] = order
        return kwargs

    def _search_read(
        self,
        domain: DomainInput = None,
        *,
        fields: Optional[list[str]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[str] = None,
    ) -> list[Record]:
        serialized_domain = self._serialized_domain(domain)
        kwargs = self._search_kwargs(limit=limit, offset=offset, order=order)
        if fields is not None:
            kwargs["fields"] = fields

        _logger.debug(
            "Search reading recordset model=%s domain=%s kwargs=%s",
            self._model_name,
            serialized_domain,
            kwargs,
        )
        return self._execute("search_read", serialized_domain, **kwargs)

    def _search_count(self, domain: DomainInput = None) -> int:
        serialized_domain = self._serialized_domain(domain)
        _logger.debug(
            "Counting recordset model=%s domain=%s",
            self._model_name,
            serialized_domain,
        )
        return self._execute(
            "search_count",
            serialized_domain,
            **self._context_kwargs(),
        )

    def _write_current(
        self,
        values: Dict[str, Any],
        *,
        allow_empty_ids: bool = False,
        allow_empty_values: bool = False,
    ) -> bool:
        if not self._ids and not allow_empty_ids:
            raise ValueError("write requires at least one id")
        if not values and not allow_empty_values:
            raise ValueError("write requires at least one value")

        _logger.debug("Writing recordset model=%s ids=%s", self._model_name, self._ids)
        return self._execute(
            "write",
            list(self._ids),
            values,
            **self._context_kwargs(),
        )

    def _unlink_current(self, *, allow_empty: bool = False) -> bool:
        if not self._ids and not allow_empty:
            raise ValueError("unlink requires at least one id")

        _logger.debug(
            "Unlinking recordset model=%s ids=%s", self._model_name, self._ids
        )
        return self._execute(
            "unlink",
            list(self._ids),
            **self._context_kwargs(),
        )

    def read(self, fields: Optional[list[str]] = None) -> list[Record]:
        """Materializes raw rows for the current ids."""
        if not self._ids:
            return []

        kwargs = self._context_kwargs()
        if fields is not None:
            kwargs["fields"] = fields

        _logger.debug(
            "Reading recordset model=%s ids=%s fields=%s",
            self._model_name,
            self._ids,
            fields,
        )
        return self._execute(
            "read",
            list(self._ids),
            **kwargs,
        )

    def write(self, values: Dict[str, Any]) -> bool:
        """Updates the current ids using the bound environment context."""
        return self._write_current(values)

    def unlink(self) -> bool:
        """Deletes the current ids using the bound environment context."""
        return self._unlink_current()

    def exists(self) -> OdooRecordset:
        """Returns a new recordset for ids that still exist on the server."""
        if not self._ids:
            return OdooRecordset(self._env, self._model_name, ())

        existing_ids = set(
            self.search([("id", "in", list(self._ids))]).ids
        )
        surviving_ids = [record_id for record_id in self._ids if record_id in existing_ids]
        return OdooRecordset(self._env, self._model_name, surviving_ids)

    def browse(self, ids: Union[int, Sequence[int]]) -> OdooRecordset:
        """Returns a same-model recordset for the provided ids without I/O."""
        return OdooRecordset(self._env, self._model_name, ids)

    def search(
        self,
        domain: DomainInput = None,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[str] = None,
    ) -> OdooRecordset:
        """Searches the bound model in the current environment."""
        serialized_domain = self._serialized_domain(domain)
        kwargs = self._search_kwargs(limit=limit, offset=offset, order=order)

        _logger.debug(
            "Searching recordset model=%s domain=%s kwargs=%s",
            self._model_name,
            serialized_domain,
            kwargs,
        )
        ids = self._execute("search", serialized_domain, **kwargs)
        return OdooRecordset(self._env, self._model_name, ids)

    def with_context(self, context: Dict[str, Any]) -> OdooRecordset:
        """Returns a new recordset bound to a derived environment."""
        return OdooRecordset(
            self._env.with_context(context),
            self._model_name,
            self._ids,
        )