import logging
from typing import Any, Dict, List, Optional

from ..utils import Domain, Record
from .odoo_executor import OdooExecutor

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
        domain: Optional[Domain] = None,
    ):
        self.client = client
        self.model_name = model_name
        # Normalize domain explicitly to avoid relying on truthiness.
        self._domain = list(domain) if domain is not None else []
        self._limit: Optional[int] = None
        self._offset: Optional[int] = None
        self._order: Optional[str] = None
        self._context: Optional[Dict[str, Any]] = None

    def _clone(self) -> OdooQuery:
        _logger.debug("Cloning query for model=%s", self.model_name)
        query = OdooQuery(self.client, self.model_name, self._domain)
        query._limit = self._limit
        query._offset = self._offset
        query._order = self._order
        query._context = dict(self._context) if self._context is not None else None
        return query

    def _search_kwargs(self) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {}
        if self._limit is not None:
            kwargs["limit"] = self._limit
        if self._offset is not None:
            kwargs["offset"] = self._offset
        if self._order is not None:
            kwargs["order"] = self._order
        if self._context is not None:
            kwargs["context"] = self._context
        return kwargs

    def _context_kwargs(self) -> Dict[str, Any]:
        if self._context is None:
            return {}
        return {"context": self._context}

    def search(self, domain: Domain) -> OdooQuery:
        """Sets or replaces the search domain."""
        _logger.debug(
            "Updating query domain for model=%s domain=%s", self.model_name, domain
        )
        query = self._clone()
        query._domain = list(domain)
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
        merged = dict(query._context) if query._context is not None else {}
        merged.update(context)
        query._context = merged
        return query

    def ids(self) -> List[int]:
        """Executes `search` and returns matching record ids."""
        _logger.debug(
            "Executing search on model=%s domain=%s",
            self.model_name,
            self._domain,
        )
        return self.client.execute(
            self.model_name,
            "search",
            self._domain,
            **self._search_kwargs(),
        )

    def read(self, fields: Optional[List[str]] = None) -> List[Record]:
        """Executes a search_read operation to fetch record dictionaries."""
        _logger.debug(
            "Executing search_read on model=%s domain=%s fields=%s",
            self.model_name,
            self._domain,
            fields,
        )
        kwargs = self._search_kwargs()
        if fields is not None:
            kwargs["fields"] = fields

        return self.client.execute(
            self.model_name, "search_read", self._domain, **kwargs
        )

    def write(self, values: Dict[str, Any]) -> bool:
        """Searches for matching records and updates them."""
        _logger.debug(
            "Executing query write on model=%s domain=%s", self.model_name, self._domain
        )
        ids = self.ids()
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
        ids = self.ids()
        return self.client.execute(
            self.model_name,
            "unlink",
            ids,
            **self._context_kwargs(),
        )

    def count(self) -> int:
        """Executes a search_count operation."""
        _logger.debug(
            "Executing search_count on model=%s domain=%s",
            self.model_name,
            self._domain,
        )
        return self.client.execute(
            self.model_name,
            "search_count",
            self._domain,
            **self._context_kwargs(),
        )
