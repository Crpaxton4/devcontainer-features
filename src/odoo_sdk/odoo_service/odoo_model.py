from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from .domain_expression import DomainExpression, DomainInput
from .odoo_executor import OdooExecutor
from .odoo_recordset import Record

if TYPE_CHECKING:
    from .odoo_env import OdooEnv
    from .odoo_recordset import OdooRecordset


class OdooModel:
    """Legacy model-style adapter over the recordset-first core.

    This compatibility adapter remains available for callers that still construct or
    import `OdooModel` directly, but the primary public entry path is now model-bound
    `OdooRecordset` lookup from `OdooClient` or `OdooEnv`.

    :param client: Executor used to issue model method calls.
    :type client: OdooExecutor
    :param name: Name of the Odoo model represented by this proxy.
    :type name: str
    :param env: Existing environment to bind to the proxy, defaults to None.
    :type env: Optional[OdooEnv]
    """

    def __init__(
        self,
        client: OdooExecutor,
        name: str,
        env: Optional[OdooEnv] = None,
    ):
        """Initialize a model proxy for one Odoo model name.

        The constructor is necessary because the compatibility layer must capture the
        executor, model identity, and shared environment state that later recordset and
        query delegations reuse.

        :param client: Executor used to issue model method calls.
        :type client: OdooExecutor
        :param name: Name of the Odoo model represented by this proxy.
        :type name: str
        :param env: Existing environment to bind to the proxy, defaults to None.
        :type env: Optional[OdooEnv]
        :return: None.
        :rtype: None
        """
        self.client = client
        self.name = name
        if env is None:
            from .odoo_env import OdooEnv

            env = OdooEnv(client)
        self._env = env

    @property
    def env(self) -> OdooEnv:
        """Expose the environment bound to this model proxy.

        This property is necessary because advanced callers and compatibility methods
        sometimes need direct access to the shared context and metadata boundary.

        :return: Environment bound to this model proxy.
        :rtype: OdooEnv
        """
        return self._env

    def _context_kwargs(self) -> Dict[str, Any]:
        """Build RPC keyword arguments for the current environment context.

        This helper is necessary because direct RPC-style model methods still need to
        propagate environment context without duplicating context-merge logic.

        :return: Context keyword arguments, or an empty mapping.
        :rtype: Dict[str, Any]
        """
        context = self._env.context
        if not context:
            return {}
        return {"context": context}

    def _execute(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Execute one method on this model through the bound executor.

        This helper is necessary because most compatibility methods ultimately reduce
        to the same executor call shape once model identity is fixed.

        :param method: Name of the Odoo method to invoke.
        :type method: str
        :param args: Positional arguments forwarded to the executor.
        :type args: Any
        :param kwargs: Keyword arguments forwarded to the executor.
        :type kwargs: Any
        :return: Result returned by Odoo.
        :rtype: Any
        """
        return self.client.execute(self.name, method, *args, **kwargs)

    def _execute_with_context(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Execute one model method while injecting environment context.

        This helper is necessary because several model-level compatibility methods call
        Odoo methods directly and must still honor the environment-owned context.

        :param method: Name of the Odoo method to invoke.
        :type method: str
        :param args: Positional arguments forwarded to the executor.
        :type args: Any
        :param kwargs: Keyword arguments forwarded to the executor.
        :type kwargs: Any
        :return: Result returned by Odoo.
        :rtype: Any
        """
        call_kwargs = dict(kwargs)
        call_kwargs.update(self._context_kwargs())
        return self._execute(method, *args, **call_kwargs)

    def _recordset(self, ids: Union[int, List[int]]) -> OdooRecordset:
        """Create a same-model recordset for the provided ids.

        This helper is necessary because recordsets own the actual Phase A and Phase B
        read, write, and existence semantics that the model proxy delegates to.

        :param ids: Record id or ids to bind to the recordset.
        :type ids: Union[int, List[int]]
        :return: Recordset bound to this model and the provided ids.
        :rtype: OdooRecordset
        """
        return self._env.recordset(self.name, ids)

    def _search_recordset(
        self,
        domain: DomainInput = None,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> OdooRecordset:
        """Search through the recordset-first core for compatibility callers.

        This helper is necessary because legacy model-style methods still need to
        expose model-level search operations while delegating directly to the shared
        recordset path instead of rebuilding fluent query-builder behavior.

        :param domain: Domain used to select records, defaults to None.
        :type domain: DomainInput
        :param limit: Maximum number of matched rows to return, defaults to None.
        :type limit: Optional[int]
        :param offset: Number of matched rows to skip, defaults to None.
        :type offset: Optional[int]
        :param order: Odoo order expression, defaults to None.
        :type order: Optional[str]
        :param context: Additional Odoo context to merge, defaults to None.
        :type context: Optional[Dict[str, Any]]
        :return: Matching recordset produced by the recordset-first core.
        :rtype: OdooRecordset
        """
        env = self._env.with_context(context) if context is not None else self._env
        return env.recordset(self.name).search(
            domain,
            limit=limit,
            offset=offset,
            order=order,
        )

    def search(
        self,
        domain: DomainInput = None,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> OdooRecordset:
        """Search the model and return a recordset.

        This method is necessary because legacy `OdooModel` callers still need model-
        level search operations, but normal wrapper use should now align with native
        Odoo semantics and return `OdooRecordset` directly rather than an
        `OdooQuery` builder.

        .. note::
           Direct `OdooQuery` construction remains available only as a legacy
           compatibility surface. `OdooModel.search()` is no longer the path that
           exposes query-builder-first behavior.

        :param domain: Domain used to select records, defaults to None.
        :type domain: DomainInput
        :param limit: Maximum number of matched rows to return, defaults to None.
        :type limit: Optional[int]
        :param offset: Number of matched rows to skip, defaults to None.
        :type offset: Optional[int]
        :param order: Odoo order expression, defaults to None.
        :type order: Optional[str]
        :param context: Additional Odoo context to merge, defaults to None.
        :type context: Optional[Dict[str, Any]]
        :return: Matching recordset.
        :rtype: OdooRecordset
        """
        return self._search_recordset(
            domain,
            limit=limit,
            offset=offset,
            order=order,
            context=context,
        )

    def read(
        self, ids: Union[int, List[int]], fields: Optional[List[str]] = None
    ) -> List[Record]:
        """Read specific records by id using raw Phase A semantics.

        This method is necessary because explicit raw extraction remains part of the
        supported compatibility story even while richer recordset semantics exist.

        :param ids: Record id or ids to read.
        :type ids: Union[int, List[int]]
        :param fields: Optional field names to request, defaults to None.
        :type fields: Optional[List[str]]
        :return: Raw record mappings for the requested ids.
        :rtype: List[Record]
        """
        recordset = self._recordset(ids)
        return recordset.read(fields)

    def read_adapted(
        self, ids: Union[int, List[int]], fields: Optional[List[str]] = None
    ) -> List[Record]:
        """Read specific records by id with Phase B field adaptation applied.

        This method is necessary because compatibility callers need access to adapted
        values without bypassing the recordset-owned adaptation path.

        :param ids: Record id or ids to read.
        :type ids: Union[int, List[int]]
        :param fields: Optional field names to request, defaults to None.
        :type fields: Optional[List[str]]
        :return: Adapted record mappings for the requested ids.
        :rtype: List[Record]
        """
        recordset = self._recordset(ids)
        return recordset.read_adapted(fields)

    def browse(self, ids: Union[int, List[int]]) -> OdooRecordset:
        """Bind ids to a same-model recordset without triggering I/O.

        This method is necessary because browse-like compatibility should now align
        with native Odoo semantics and return a recordset rather than row payloads.

        .. note::
           Use `recordset.read()` or singleton field access for extraction after
           browsing. Returning rows directly here is no longer the preferred or
           documented behavior.

        :param ids: Record id or ids to bind.
        :type ids: Union[int, List[int]]
        :return: Recordset bound to the requested ids.
        :rtype: OdooRecordset
        """
        return self._recordset(ids)

    def browse_adapted(self, ids: Union[int, List[int]]) -> OdooRecordset:
        """Preserve the old adapted-browse name as a recordset alias.

        This method is necessary only to preserve old call sites that referenced the
        adapted browse name. Field adaptation now happens through recordset access and
        extraction methods, not through a distinct browse result type.

        .. note::
           This remains a compatibility alias over `browse()` and should not be used
           to imply a separate preferred API.

        :param ids: Record id or ids to bind.
        :type ids: Union[int, List[int]]
        :return: Recordset bound to the requested ids.
        :rtype: OdooRecordset
        """
        return self.browse(ids)

    def search_ids(
        self,
        domain: DomainInput = None,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[int]:
        """Search for matching records and return their ids.

        This method is necessary because some callers need only identity and do not
        want to materialize rows or adapted values.

        :param domain: Domain used to select records, defaults to None.
        :type domain: DomainInput
        :param limit: Maximum number of ids to return, defaults to None.
        :type limit: Optional[int]
        :param offset: Number of matched rows to skip, defaults to None.
        :type offset: Optional[int]
        :param order: Odoo order expression, defaults to None.
        :type order: Optional[str]
        :param context: Additional Odoo context to merge, defaults to None.
        :type context: Optional[Dict[str, Any]]
        :return: Matching record ids.
        :rtype: List[int]
        """
        return list(
            self._search_recordset(
                domain,
                limit=limit,
                offset=offset,
                order=order,
                context=context,
            ).ids
        )

    def exists(self, ids: Union[int, List[int]]) -> List[int]:
        """Return the subset of ids that still exist on the server.

        This method is necessary because callers often hold cached ids and need one
        compatibility entry point that checks remote existence while preserving order.

        :param ids: Record id or ids to check.
        :type ids: Union[int, List[int]]
        :return: Existing ids in the same order as the input.
        :rtype: List[int]
        """
        recordset = self._recordset(ids)
        if not recordset.ids:
            return []

        return list(recordset.exists().ids)

    def search_read(
        self,
        domain: DomainInput = None,
        fields: Optional[List[str]] = None,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[Record]:
        """Run raw `search_read` with optional pagination and field selection.

        This method is necessary because explicit row extraction remains available for
        compatibility callers, but it should delegate directly to the recordset-owned
        `search_read` helper instead of rebuilding a query-builder-shaped flow.

        :param domain: Domain used to select records, defaults to None.
        :type domain: DomainInput
        :param fields: Optional field names to request, defaults to None.
        :type fields: Optional[List[str]]
        :param limit: Maximum number of records to return, defaults to None.
        :type limit: Optional[int]
        :param offset: Number of matched rows to skip, defaults to None.
        :type offset: Optional[int]
        :param order: Odoo order expression, defaults to None.
        :type order: Optional[str]
        :param context: Additional Odoo context to merge, defaults to None.
        :type context: Optional[Dict[str, Any]]
        :return: Raw record mappings.
        :rtype: List[Record]
        """
        env = self._env.with_context(context) if context is not None else self._env
        return env.recordset(self.name).search_read(
            domain,
            fields=fields,
            limit=limit,
            offset=offset,
            order=order,
        )

    def search_read_adapted(
        self,
        domain: DomainInput = None,
        fields: Optional[List[str]] = None,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[Record]:
        """Run adapted `search_read` with optional pagination and field selection.

        This method is necessary because compatibility callers may still want adapted
        row payloads while the primary search contract is recordset-first.

        :param domain: Domain used to select records, defaults to None.
        :type domain: DomainInput
        :param fields: Optional field names to request, defaults to None.
        :type fields: Optional[List[str]]
        :param limit: Maximum number of records to return, defaults to None.
        :type limit: Optional[int]
        :param offset: Number of matched rows to skip, defaults to None.
        :type offset: Optional[int]
        :param order: Odoo order expression, defaults to None.
        :type order: Optional[str]
        :param context: Additional Odoo context to merge, defaults to None.
        :type context: Optional[Dict[str, Any]]
        :return: Adapted record mappings.
        :rtype: List[Record]
        """
        env = self._env.with_context(context) if context is not None else self._env
        return env.recordset(self.name).search_read_adapted(
            domain,
            fields=fields,
            limit=limit,
            offset=offset,
            order=order,
        )

    def search_count(self, domain: DomainInput = None) -> int:
        """Return the number of records matching a domain.

        This method is necessary because callers often need cardinality checks without
        materializing the record ids or rows themselves.

        :param domain: Domain used to count records, defaults to None.
        :type domain: DomainInput
        :return: Number of matched records.
        :rtype: int
        """
        return self._env.recordset(self.name).search_count(domain)

    def name_search(
        self,
        name: str = "",
        domain: DomainInput = None,
        *,
        operator: str = "ilike",
        limit: int = 100,
    ) -> List[List[Any]]:
        """Run Odoo `name_search` for lightweight display-name lookup.

        This method is necessary because many relational workflows need compact
        ``[id, display_name]`` results without a full read.

        :param name: Search string used by Odoo, defaults to an empty string.
        :type name: str
        :param domain: Additional domain filter, defaults to None.
        :type domain: DomainInput
        :param operator: Comparison operator applied to the search string, defaults to
            ``ilike``.
        :type operator: str
        :param limit: Maximum number of matches to return, defaults to 100.
        :type limit: int
        :return: ``[id, display_name]`` rows returned by Odoo.
        :rtype: List[List[Any]]
        """
        kwargs: Dict[str, Any] = {"operator": operator, "limit": limit}
        if domain is not None:
            kwargs["args"] = DomainExpression.normalize(domain).serialize()
        kwargs.update(self._context_kwargs())
        return self._execute("name_search", name, **kwargs)

    def name_get(self, ids: Union[int, List[int]]) -> List[List[Any]]:
        """Run Odoo `name_get` for specific ids.

        This method is necessary because many consumers need the canonical display
        labels for known ids without reading full rows.

        :param ids: Record id or ids to resolve.
        :type ids: Union[int, List[int]]
        :return: ``[id, display_name]`` rows for the provided ids.
        :rtype: List[List[Any]]
        """
        normalized_ids = list(self._recordset(ids).ids)
        return self._execute_with_context("name_get", normalized_ids)

    def default_get(self, fields: List[str]) -> Dict[str, Any]:
        """Return Odoo default values for the requested fields.

        This method is necessary because create flows often need server-defined default
        values before building a final payload.

        :param fields: Field names whose default values should be resolved.
        :type fields: List[str]
        :return: Mapping of field names to default values.
        :rtype: Dict[str, Any]
        """
        return self._execute_with_context("default_get", fields)

    def copy(self, record_id: int, default: Optional[Record] = None) -> int:
        """Copy one record and return the new record id.

        This method is necessary because Odoo exposes duplication as a server-side
        operation that can also accept default overrides for the copied row.

        :param record_id: Identifier of the record to copy.
        :type record_id: int
        :param default: Optional override values applied during copy, defaults to None.
        :type default: Optional[Record]
        :return: Identifier of the newly created record.
        :rtype: int
        """
        values = dict(default) if default is not None else {}
        return self._execute_with_context("copy", record_id, values)

    def read_group(
        self,
        domain: DomainInput,
        fields: List[str],
        groupby: List[str],
        *,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
        orderby: Optional[str] = None,
        lazy: bool = True,
    ) -> List[Dict[str, Any]]:
        """Run `read_group` for grouped aggregate queries.

        This method is necessary because grouped reporting uses a distinct Odoo RPC
        shape that is not modeled as a recordset read.

        :param domain: Domain used to select grouped records.
        :type domain: DomainInput
        :param fields: Aggregate or grouped field expressions to request.
        :type fields: List[str]
        :param groupby: Field names used to group the result.
        :type groupby: List[str]
        :param offset: Number of grouped rows to skip, defaults to None.
        :type offset: Optional[int]
        :param limit: Maximum number of grouped rows to return, defaults to None.
        :type limit: Optional[int]
        :param orderby: Odoo group ordering expression, defaults to None.
        :type orderby: Optional[str]
        :param lazy: Whether Odoo should defer deeper group expansion, defaults to
            True.
        :type lazy: bool
        :return: Grouped aggregate rows returned by Odoo.
        :rtype: List[Dict[str, Any]]
        """
        kwargs: Dict[str, Any] = {"lazy": lazy}
        if offset is not None:
            kwargs["offset"] = offset
        if limit is not None:
            kwargs["limit"] = limit
        if orderby is not None:
            kwargs["orderby"] = orderby
        kwargs.update(self._context_kwargs())

        search_domain = DomainExpression.normalize(domain).serialize()
        return self.client.execute(
            self.name,
            "read_group",
            search_domain,
            fields,
            groupby,
            **kwargs,
        )

    def create(self, vals: Record) -> int:
        """Create one record and return its id.

        This method is necessary because direct model-level create calls remain part of
        the public facade even though richer identity semantics live in recordsets.

        :param vals: Field values for the new record.
        :type vals: Record
        :return: Identifier of the newly created record.
        :rtype: int
        """
        return self._execute_with_context("create", vals)

    def write(self, ids: Union[int, List[int]], vals: Dict[str, Any]) -> bool:
        """Update specific records by id.

        This method is necessary because the public model facade still exposes direct
        write operations, but it delegates the actual write semantics to recordsets so
        x2many normalization and context handling stay centralized.

        :param ids: Record id or ids to update.
        :type ids: Union[int, List[int]]
        :param vals: Field values to write.
        :type vals: Dict[str, Any]
        :return: True when Odoo reports a successful update.
        :rtype: bool
        """
        recordset = self._recordset(ids)
        return recordset.write(vals)

    def unlink(self, ids: Union[int, List[int]]) -> bool:
        """Delete specific records by id.

        This method is necessary because the public model facade still exposes direct
        delete operations, but it delegates the actual unlink semantics to recordsets.

        :param ids: Record id or ids to delete.
        :type ids: Union[int, List[int]]
        :return: True when Odoo reports a successful delete.
        :rtype: bool
        """
        recordset = self._recordset(ids)
        return recordset.unlink()

    def fields_get(
        self, fields: Optional[List[str]] = None, attributes: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Fetch cached schema metadata for this model.

        This method is necessary because metadata-driven features such as field
        adaptation and x2many serialization still need a public compatibility access
        point for callers that inspect model schema directly.

        :param fields: Optional field names to request, defaults to None.
        :type fields: Optional[List[str]]
        :param attributes: Optional field metadata attributes to request, defaults to
            None.
        :type attributes: Optional[List[str]]
        :return: Field metadata keyed by field name.
        :rtype: Dict[str, Any]
        """
        return self._env.get_field_metadata(
            self.name,
            fields,
            attributes,
        )
