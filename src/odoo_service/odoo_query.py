from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..utils import Record
from ..utils.types import DomainInput
from .domain_expression import DomainExpression
from .odoo_executor import OdooExecutor

if TYPE_CHECKING:
    from .odoo_env import OdooEnv
    from .odoo_recordset import OdooRecordset

_logger = logging.getLogger(__name__)


class OdooQuery:
    """
    Fluent query builder for Odoo models.
    Chains domain filters, limits, and offsets before executing the read.
    """

    def __init__(
        self,
        client: OdooExecutor,
        model_name: str,
        domain: DomainInput = None,
        env: Optional[OdooEnv] = None,
    ):
        self.client = client
        self.model_name = model_name
        if env is None:
            from .odoo_env import OdooEnv

            env = OdooEnv(client)
        self._env = env
        # Normalize domain explicitly to avoid relying on truthiness.
        self._domain = DomainExpression.normalize(domain)
        self._limit: Optional[int] = None
        self._offset: Optional[int] = None
        self._order: Optional[str] = None

    def _clone(self) -> OdooQuery:
        _logger.debug("Cloning query for model=%s", self.model_name)
        query = OdooQuery(self.client, self.model_name, self._domain, env=self._env)
        query._limit = self._limit
        query._offset = self._offset
        query._order = self._order
        return query

    def _recordset(self) -> OdooRecordset:
        from .odoo_recordset import OdooRecordset

        return OdooRecordset(self._env, self.model_name)

    def _search_recordset(self) -> OdooRecordset:
        return self._recordset().search(
            self._domain,
            limit=self._limit,
            offset=self._offset,
            order=self._order,
        )

    def _search_kwargs(self) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {}
        if self._limit is not None:
            kwargs["limit"] = self._limit
        if self._offset is not None:
            kwargs["offset"] = self._offset
        if self._order is not None:
            kwargs["order"] = self._order
        context = self._env.context
        if context:
            kwargs["context"] = context
        return kwargs

    def _context_kwargs(self) -> Dict[str, Any]:
        context = self._env.context
        if not context:
            return {}
        return {"context": context}

    def _serialized_domain(self) -> List[Any]:
        return self._domain.serialize()

    def search(self, domain: DomainInput) -> OdooQuery:
        """Sets or replaces the search domain."""
        _logger.debug(
            "Updating query domain for model=%s domain=%s", self.model_name, domain
        )
        query = self._clone()
        query._domain = DomainExpression.normalize(domain)
        return query

    def limit(self, limit: int) -> OdooQuery:
        """Sets the maximum number of records to return."""
        _logger.debug("Applying limit=%s to model=%s", limit, self.model_name)
        query = self._clone()
        query._limit = limit
        return query

    def offset(self, offset: int) -> OdooQuery:
        """Sets the offset for pagination."""
        _logger.debug("Applying offset=%s to model=%s", offset, self.model_name)
        query = self._clone()
        query._offset = offset
        return query

    def order_by(self, order: str) -> OdooQuery:
        """Sets ordering for search-derived operations."""
        _logger.debug("Applying order=%s to model=%s", order, self.model_name)
        query = self._clone()
        query._order = order
        return query

    def with_context(self, context: Dict[str, Any]) -> OdooQuery:
        """Merges Odoo context for subsequent operations."""
        _logger.debug("Applying context to model=%s", self.model_name)
        query = self._clone()
        query._env = query._env.with_context(context)
        return query

    def ids(self) -> List[int]:
        """Executes `search` and returns matching record ids."""
        serialized_domain = self._serialized_domain()
        _logger.debug(
            "Executing search on model=%s domain=%s",
            self.model_name,
            serialized_domain,
        )
        return list(self._search_recordset().ids)

    def read(self, fields: Optional[List[str]] = None) -> List[Record]:
        """Executes a search_read operation to fetch record dictionaries."""
        serialized_domain = self._serialized_domain()
        _logger.debug(
            "Executing search_read on model=%s domain=%s fields=%s",
            self.model_name,
            serialized_domain,
            fields,
        )
        kwargs = self._search_kwargs()
        if fields is not None:
            kwargs["fields"] = fields

        return self.client.execute(
            self.model_name, "search_read", serialized_domain, **kwargs
        )

    def write(self, values: Dict[str, Any]) -> bool:
        """Searches for matching records and updates them."""
        _logger.debug(
            "Executing query write on model=%s domain=%s", self.model_name, self._domain
        )
        ids = list(self._search_recordset().ids)
        return self.client.execute(
            self.model_name,
            "write",
            ids,
            values,
            **self._context_kwargs(),
        )

    def unlink(self) -> bool:
        """Searches for matching records and deletes them."""
        _logger.debug(
            "Executing query unlink on model=%s domain=%s",
            self.model_name,
            self._domain,
        )
        ids = list(self._search_recordset().ids)
        return self.client.execute(
            self.model_name,
            "unlink",
            ids,
            **self._context_kwargs(),
        )

    def count(self) -> int:
        """Executes a search_count operation."""
        serialized_domain = self._serialized_domain()
        _logger.debug(
            "Executing search_count on model=%s domain=%s",
            self.model_name,
            serialized_domain,
        )
        return self.client.execute(
            self.model_name,
            "search_count",
            serialized_domain,
            **self._context_kwargs(),
        )
