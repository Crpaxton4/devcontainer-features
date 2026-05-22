from __future__ import annotations

from copy import deepcopy
import logging
from typing import Any, Dict, Iterable, Optional

from ..utils import Record
from ..utils.types import DomainInput
from .domain_expression import DomainExpression
from .odoo_env import OdooEnv

_logger = logging.getLogger(__name__)


class OdooRecordset:
    """Immutable, env-bound representation of model identity and ids."""

    def __init__(
        self,
        env: OdooEnv,
        model_name: str,
        ids: int | Iterable[int] = (),
    ):
        self._env = env
        self._model_name = model_name
        self._ids = self._normalize_ids(ids)

    @staticmethod
    def _normalize_ids(ids: int | Iterable[int]) -> tuple[int, ...]:
        if isinstance(ids, int):
            return (ids,)
        return tuple(ids)

    @property
    def env(self) -> OdooEnv:
        """Returns the environment that owns this recordset's execution context."""
        return self._env

    @property
    def model_name(self) -> str:
        """Returns the model name bound to this recordset."""
        return self._model_name

    @property
    def ids(self) -> tuple[int, ...]:
        """Returns the ordered ids carried by this recordset."""
        return self._ids

    def _context_kwargs(self) -> Dict[str, Any]:
        context = self._env.context
        if not context:
            return {}
        return {"context": context}

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

    def read(self, fields: Optional[list[str]] = None) -> list[Record]:
        """Reads the current ids and returns raw record dictionaries."""
        if not self._ids:
            return []

        kwargs = self._context_kwargs()
        if fields is not None:
            kwargs["fields"] = list(fields)

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
        """Writes values to the current ids using the recordset environment."""
        if not self._ids:
            raise ValueError("write requires at least one id")
        if not values:
            raise ValueError("write requires at least one value")

        _logger.debug("Writing recordset model=%s ids=%s", self._model_name, self._ids)
        return self._env.executor.execute(
            self._model_name,
            "write",
            list(self._ids),
            deepcopy(values),
            **self._context_kwargs(),
        )

    def unlink(self) -> bool:
        """Deletes the current ids using the recordset environment."""
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
        """Returns a recordset of ids that still exist, preserving input order."""
        if not self._ids:
            return self.browse(())

        existing_ids = set(
            self.search([("id", "in", list(self._ids))]).ids
        )
        return self.browse(
            record_id for record_id in self._ids if record_id in existing_ids
        )

    def browse(self, ids: int | Iterable[int]) -> OdooRecordset:
        """Returns a recordset for the same model and the provided ids."""
        return OdooRecordset(self._env, self._model_name, ids)

    def search(
        self,
        domain: DomainInput = None,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[str] = None,
    ) -> OdooRecordset:
        """Searches within the same model and returns a new recordset."""
        serialized_domain = DomainExpression.normalize(domain).serialize()
        _logger.debug(
            "Searching recordset model=%s domain=%s",
            self._model_name,
            serialized_domain,
        )
        ids = self._env.executor.execute(
            self._model_name,
            "search",
            serialized_domain,
            **self._search_kwargs(limit=limit, offset=offset, order=order),
        )
        return self.browse(ids)

    def with_context(self, context: Dict[str, Any]) -> OdooRecordset:
        """Returns a new recordset bound to a derived environment."""
        return OdooRecordset(
            self._env.with_context(context),
            self._model_name,
            self._ids,
        )