from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .domain import DomainExpression, DomainInput
from odoo_sdk.transport.executor import OdooExecutor
from odoo_sdk.records.recordset import Record

if TYPE_CHECKING:
    from odoo_sdk.env.env import OdooEnv
    from odoo_sdk.records.recordset import OdooRecordset


class OdooQuery:
    """Preserve the legacy fluent query surface over recordset-owned execution.

    This compatibility wrapper keeps existing chained query call sites working while
    routing all terminal behavior through the environment and recordset internals.
    It is necessary during the Phase A and Phase B transition so public callers do
    not need an immediate rewrite while the recordset-first architecture becomes the
    controlling implementation path.

    .. note::
         This class is a legacy compatibility surface. Normal lookup and search flows
         now return `OdooRecordset` directly, so `OdooQuery` should only be used by
         callers that intentionally preserve older builder-style chaining.

    :param client: Executor used to perform Odoo model operations.
    :type client: OdooExecutor
    :param model_name: Name of the Odoo model targeted by the query.
    :type model_name: str
    :param domain: Initial search domain to normalize and carry through clones.
    :type domain: DomainInput
    :param env: Existing environment to reuse for context and metadata sharing,
        defaults to None.
    :type env: Optional[OdooEnv]
    """

    def __init__(
        self,
        client: OdooExecutor,
        model_name: str,
        domain: DomainInput = None,
        env: Optional[OdooEnv] = None,
    ):
        """Initialize a query wrapper for one model and one normalized domain state.

        The constructor captures the model, executor, and environment boundary that
        later clones reuse. It is necessary because compatibility callers still
        construct and chain `OdooQuery` objects directly even though the terminal
        operations are delegated elsewhere.

        :param client: Executor used to perform Odoo model operations.
        :type client: OdooExecutor
        :param model_name: Name of the Odoo model targeted by the query.
        :type model_name: str
        :param domain: Initial search domain to normalize and store.
        :type domain: DomainInput
        :param env: Existing environment to reuse for context and metadata sharing,
            defaults to None.
        :type env: Optional[OdooEnv]
        :return: None.
        :rtype: None
        """
        self.client = client
        self.model_name = model_name
        if env is None:
            from odoo_sdk.env.env import OdooEnv

            env = OdooEnv(client)
        self._env = env
        # Normalize domain explicitly to avoid relying on truthiness.
        self._domain = DomainExpression.normalize(domain)
        self._limit: Optional[int] = None
        self._offset: Optional[int] = None
        self._order: Optional[str] = None

    def _clone(self) -> OdooQuery:
        """Copy the query state so fluent modifiers remain immutable.

        Cloning is necessary because the compatibility builder must support chained
        calls without mutating previously shared query instances.

        :return: A new query instance carrying the same search configuration.
        :rtype: OdooQuery
        """
        query = OdooQuery(self.client, self.model_name, self._domain, env=self._env)
        query._limit = self._limit
        query._offset = self._offset
        query._order = self._order
        return query

    def _recordset(self) -> OdooRecordset:
        """Create an empty same-model recordset for terminal delegation.

        This helper is necessary because recordsets own the real search, read, and
        write behavior, so the compatibility layer must obtain a recordset anchor
        before executing any terminal operation.

        :return: An empty recordset bound to the current model and environment.
        :rtype: OdooRecordset
        """
        return self._env.recordset(self.model_name)

    def _search_options(self) -> Dict[str, Any]:
        """Collect pagination and ordering options for delegated searches.

        This helper is necessary to keep the query's mutable-looking builder state
        in one place before it is forwarded into the recordset-owned execution path.

        :return: Search keyword arguments derived from the stored query state.
        :rtype: Dict[str, Any]
        """
        return {
            "limit": self._limit,
            "offset": self._offset,
            "order": self._order,
        }

    def search(self, domain: DomainInput) -> OdooQuery:
        """Return a new query with a replaced normalized search domain.

        This method is necessary to preserve the historical fluent API where later
        calls can swap the domain without mutating earlier query objects.

        :param domain: Domain expression to normalize for the next query state.
        :type domain: DomainInput
        :return: A cloned query carrying the new domain.
        :rtype: OdooQuery
        """
        query = self._clone()
        query._domain = DomainExpression.normalize(domain)
        return query

    def limit(self, limit: int) -> OdooQuery:
        """Return a new query with a result-size cap.

        This method is necessary so compatibility callers can express Odoo search
        pagination without dropping down to raw keyword argument management.

        :param limit: Maximum number of records to return.
        :type limit: int
        :return: A cloned query carrying the requested limit.
        :rtype: OdooQuery
        """
        query = self._clone()
        query._limit = limit
        return query

    def offset(self, offset: int) -> OdooQuery:
        """Return a new query with a pagination offset.

        This method is necessary to preserve fluent paging semantics for legacy
        callers while still keeping the query object immutable.

        :param offset: Number of matched rows to skip before returning results.
        :type offset: int
        :return: A cloned query carrying the requested offset.
        :rtype: OdooQuery
        """
        query = self._clone()
        query._offset = offset
        return query

    def order_by(self, order: str) -> OdooQuery:
        """Return a new query with Odoo ordering applied.

        This method is necessary because search, read, and count compatibility calls
        all need one place to carry an order clause through to recordset execution.

        :param order: Odoo order expression such as ``name asc``.
        :type order: str
        :return: A cloned query carrying the requested order clause.
        :rtype: OdooQuery
        """
        query = self._clone()
        query._order = order
        return query

    def with_context(self, context: Dict[str, Any]) -> OdooQuery:
        """Return a new query bound to a derived Odoo context.

        This method is necessary because context ownership lives in `OdooEnv`, but
        the fluent compatibility API still needs a way to carry per-query context
        changes without mutating the shared root environment.

        :param context: Additional Odoo context keys to merge.
        :type context: Dict[str, Any]
        :return: A cloned query bound to a derived environment.
        :rtype: OdooQuery
        """
        query = self._clone()
        query._env = query._env.with_context(context)
        return query

    def ids(self) -> List[int]:
        """Execute the current search and return matching ids.

        This terminal operation is necessary for legacy callers that need only record
        identity while still benefiting from the recordset-owned domain and context
        normalization path.

        :return: Matching record identifiers in server order.
        :rtype: List[int]
        """
        return self._recordset().search_ids(self._domain, **self._search_options())

    def read(self, fields: Optional[List[str]] = None) -> List[Record]:
        """Execute raw `search_read` semantics for the current query state.

        This method is necessary because Phase A explicitly preserves raw extraction
        behavior for compatibility even while the search path itself moves to
        recordset-centered internals.

        :param fields: Optional field names to request from Odoo, defaults to None.
        :type fields: Optional[List[str]]
        :return: Raw Odoo row dictionaries.
        :rtype: List[Record]
        """
        return self._recordset().search_read(
            self._domain,
            fields=fields,
            **self._search_options(),
        )

    def read_adapted(self, fields: Optional[List[str]] = None) -> List[Record]:
        """Execute adapted `search_read` semantics for the current query state.

        This method is necessary because Phase B exposes richer relation and temporal
        semantics without forcing those adaptations onto the raw compatibility path.

        :param fields: Optional field names to request from Odoo, defaults to None.
        :type fields: Optional[List[str]]
        :return: Adapted record dictionaries produced by the shared field layer.
        :rtype: List[Record]
        """
        return self._recordset().search_read_adapted(
            self._domain,
            fields=fields,
            **self._search_options(),
        )

    def write(self, values: Dict[str, Any]) -> bool:
        """Search for matching records and update them.

        This terminal operation is necessary so compatibility callers can still use a
        query object as the entry point for write flows while the recordset layer owns
        x2many normalization and execution semantics.

        :param values: Field values to write to every matched record.
        :type values: Dict[str, Any]
        :return: ``True`` when Odoo reports a successful update.
        :rtype: bool
        """
        return self._recordset().search_write(
            self._domain,
            values,
            **self._search_options(),
        )

    def unlink(self) -> bool:
        """Search for matching records and delete them.

        This method is necessary because the historical fluent API allowed deletion
        from a query object, but the actual delete semantics now belong to the
        recordset layer that resolves the matched ids first.

        :return: ``True`` when Odoo reports a successful delete.
        :rtype: bool
        """
        return self._recordset().search_unlink(
            self._domain,
            **self._search_options(),
        )

    def count(self) -> int:
        """Execute `search_count` for the current query state.

        This terminal operation is necessary so callers can compute cardinality using
        the same normalized domain, context, and ordering state that their fluent
        query already carries.

        :return: Number of records matching the current query state.
        :rtype: int
        """
        return self._recordset().search_count(self._domain)
