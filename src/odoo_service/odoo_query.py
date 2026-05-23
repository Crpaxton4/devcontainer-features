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
    Transitional fluent compatibility wrapper over env and recordset execution.
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

            resolved_env = getattr(client, "env", None)
            env = resolved_env if isinstance(resolved_env, OdooEnv) else OdooEnv(client)
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
        return self._env.recordset(self.model_name)

    def _search_recordset(self) -> OdooRecordset:
        return self._recordset().search(
            self._domain,
            limit=self._limit,
            offset=self._offset,
            order=self._order,
        )

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
        serialized_domain = self._domain.serialize()
        _logger.debug(
            "Executing search on model=%s domain=%s",
            self.model_name,
            serialized_domain,
        )
        return list(self._search_recordset().ids)

    def read(self, fields: Optional[List[str]] = None) -> List[Record]:
        """Executes a search_read operation to fetch record dictionaries."""
        serialized_domain = self._domain.serialize()
        _logger.debug(
            "Executing search_read on model=%s domain=%s fields=%s",
            self.model_name,
            serialized_domain,
            fields,
        )
        return self._recordset()._search_read(
            self._domain,
            fields=fields,
            limit=self._limit,
            offset=self._offset,
            order=self._order,
        )

    def write(self, values: Dict[str, Any]) -> bool:
        """Searches for matching records and updates them."""
        _logger.debug(
            "Executing query write on model=%s domain=%s", self.model_name, self._domain
        )
        return self._search_recordset()._write_current(
            values,
            allow_empty_ids=True,
            allow_empty_values=True,
        )

    def unlink(self) -> bool:
        """Searches for matching records and deletes them."""
        _logger.debug(
            "Executing query unlink on model=%s domain=%s",
            self.model_name,
            self._domain,
        )
        return self._search_recordset()._unlink_current(allow_empty=True)

    def count(self) -> int:
        """Executes a search_count operation."""
        serialized_domain = self._domain.serialize()
        _logger.debug(
            "Executing search_count on model=%s domain=%s",
            self.model_name,
            serialized_domain,
        )
        return self._recordset()._search_count(self._domain)
