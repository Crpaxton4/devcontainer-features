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

    def _context_kwargs(self) -> Dict[str, Any]:
        context = self._env.context
        if not context:
            return {}
        return {"context": context}

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
        return self._env.executor.execute(
            self._model_name,
            "read",
            list(self._ids),
            **kwargs,
        )

    def write(self, values: Dict[str, Any]) -> bool:
        """Updates the current ids using the bound environment context."""
        if not self._ids:
            raise ValueError("write requires at least one id")
        if not values:
            raise ValueError("write requires at least one value")

        _logger.debug("Writing recordset model=%s ids=%s", self._model_name, self._ids)
        return self._env.executor.execute(
            self._model_name,
            "write",
            list(self._ids),
            values,
            **self._context_kwargs(),
        )

    def unlink(self) -> bool:
        """Deletes the current ids using the bound environment context."""
        if not self._ids:
            raise ValueError("unlink requires at least one id")

        _logger.debug(
            "Unlinking recordset model=%s ids=%s", self._model_name, self._ids
        )
        return self._env.executor.execute(
            self._model_name,
            "unlink",
            list(self._ids),
            **self._context_kwargs(),
        )

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
        serialized_domain = DomainExpression.normalize(domain).serialize()
        kwargs = self._context_kwargs()
        if limit is not None:
            kwargs["limit"] = limit
        if offset is not None:
            kwargs["offset"] = offset
        if order is not None:
            kwargs["order"] = order

        _logger.debug(
            "Searching recordset model=%s domain=%s kwargs=%s",
            self._model_name,
            serialized_domain,
            kwargs,
        )
        ids = self._env.executor.execute(
            self._model_name,
            "search",
            serialized_domain,
            **kwargs,
        )
        return OdooRecordset(self._env, self._model_name, ids)

    def with_context(self, context: Dict[str, Any]) -> OdooRecordset:
        """Returns a new recordset bound to a derived environment."""
        return OdooRecordset(
            self._env.with_context(context),
            self._model_name,
            self._ids,
        )