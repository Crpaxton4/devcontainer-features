from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from ..utils import Record
from ..utils.types import DomainInput
from .domain_expression import DomainExpression
from .odoo_executor import OdooExecutor
from .odoo_query import OdooQuery

if TYPE_CHECKING:
    from .odoo_env import OdooEnv
    from .odoo_recordset import OdooRecordset

_logger = logging.getLogger(__name__)


class OdooModel:
    """
    Client for a specific Odoo table/model.
    """

    def __init__(
        self,
        client: OdooExecutor,
        name: str,
        env: Optional[OdooEnv] = None,
    ):
        self.client = client
        self.name = name
        if env is None:
            from .odoo_env import OdooEnv

            env = OdooEnv(client)
        self._env = env

    @property
    def env(self) -> OdooEnv:
        """Returns the environment bound to this model proxy."""
        return self._env

    def _context_kwargs(self) -> Dict[str, Any]:
        context = self._env.context
        if not context:
            return {}
        return {"context": context}

    def _execute(self, method: str, *args: Any, **kwargs: Any) -> Any:
        return self.client.execute(self.name, method, *args, **kwargs)

    def _execute_with_context(self, method: str, *args: Any, **kwargs: Any) -> Any:
        call_kwargs = dict(kwargs)
        call_kwargs.update(self._context_kwargs())
        return self._execute(method, *args, **call_kwargs)

    def _recordset(self, ids: Union[int, List[int]]) -> OdooRecordset:
        return self._env.recordset(self.name, ids)

    def _read_from_recordset(
        self,
        ids: Union[int, List[int]],
        fields: Optional[List[str]],
        *,
        adapted: bool = False,
    ) -> List[Record]:
        recordset = self._recordset(ids)
        reader_name = "read_adapted" if adapted else "read"
        mode = " adapted" if adapted else ""
        _logger.debug(
            "Reading%s model=%s ids=%s fields=%s",
            mode,
            self.name,
            recordset.ids,
            fields,
        )
        return getattr(recordset, reader_name)(fields)

    def _read_from_query(
        self,
        domain: DomainInput,
        fields: Optional[List[str]],
        *,
        adapted: bool = False,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[str] = None,
    ) -> List[Record]:
        reader_name = "read_adapted" if adapted else "read"
        mode = "adapted " if adapted else ""
        _logger.debug("Executing %ssearch_read on model=%s", mode, self.name)
        query = self._search_query(
            domain,
            limit=limit,
            offset=offset,
            order=order,
        )
        return getattr(query, reader_name)(fields)

    @staticmethod
    def _serialize_domain(domain: DomainInput) -> List[Any]:
        return DomainExpression.normalize(domain).serialize()

    def _search_query(
        self,
        domain: DomainInput = None,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> OdooQuery:
        query = self.search(domain)
        if limit is not None:
            query = query.limit(limit)
        if offset is not None:
            query = query.offset(offset)
        if order is not None:
            query = query.order_by(order)
        if context is not None:
            query = query.with_context(context)
        return query

    def search(self, domain: DomainInput = None) -> OdooQuery:
        """Starts a fluent query. Returns an OdooQuery builder."""
        _logger.debug("Building query for model=%s domain=%s", self.name, domain)
        return OdooQuery(self.client, self.name, domain, env=self._env)

    def read(
        self, ids: Union[int, List[int]], fields: Optional[List[str]] = None
    ) -> List[Record]:
        """Reads specific records by their IDs.

        :param ids: The IDs of the records to read.
        :type ids: List[int]
        :param fields: The fields to read, defaults to None
        :type fields: Optional[List[str]], optional
        :return: The list of records with the requested fields.
        :rtype: List[Record]
        """
        return self._read_from_recordset(ids, fields)

    def read_adapted(
        self, ids: Union[int, List[int]], fields: Optional[List[str]] = None
    ) -> List[Record]:
        """Reads specific records by ID with Phase B field adaptation applied."""
        return self._read_from_recordset(ids, fields, adapted=True)

    def browse(
        self, ids: Union[int, List[int]], fields: Optional[List[str]] = None
    ) -> List[Record]:
        """Convenience alias for raw Phase A reads to mirror ORM-style call sites."""
        return self._read_from_recordset(ids, fields)

    def browse_adapted(
        self, ids: Union[int, List[int]], fields: Optional[List[str]] = None
    ) -> List[Record]:
        """Convenience alias for Phase B adapted reads to mirror ORM-style call sites."""
        return self._read_from_recordset(ids, fields, adapted=True)

    def search_ids(
        self,
        domain: DomainInput = None,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[int]:
        """Runs `search` and returns matching record ids."""
        return self._search_query(
            domain,
            limit=limit,
            offset=offset,
            order=order,
            context=context,
        ).ids()

    def exists(self, ids: Union[int, List[int]]) -> List[int]:
        """Returns ids that still exist on the server, preserving input order."""
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
    ) -> List[Record]:
        """Runs raw Phase A `search_read` with optional pagination and field selection."""
        return self._read_from_query(
            domain,
            fields,
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
    ) -> List[Record]:
        """Runs Phase B `search_read` with field adaptation applied."""
        return self._read_from_query(
            domain,
            fields,
            adapted=True,
            limit=limit,
            offset=offset,
            order=order,
        )

    def search_count(self, domain: DomainInput = None) -> int:
        """Runs `search_count` and returns the number of matched records."""
        _logger.debug("Executing search_count on model=%s", self.name)
        return self.search(domain).count()

    def name_search(
        self,
        name: str = "",
        domain: DomainInput = None,
        *,
        operator: str = "ilike",
        limit: int = 100,
    ) -> List[List[Any]]:
        """Runs `name_search` and returns [id, display_name] rows."""
        kwargs: Dict[str, Any] = {"operator": operator, "limit": limit}
        if domain is not None:
            kwargs["args"] = self._serialize_domain(domain)
        kwargs.update(self._context_kwargs())

        _logger.debug("Executing name_search on model=%s name=%s", self.name, name)
        return self._execute("name_search", name, **kwargs)

    def name_get(self, ids: Union[int, List[int]]) -> List[List[Any]]:
        """Runs `name_get` for the provided ids."""
        normalized_ids = list(self._recordset(ids).ids)
        _logger.debug(
            "Executing name_get on model=%s ids=%s", self.name, normalized_ids
        )
        return self._execute_with_context("name_get", normalized_ids)

    def default_get(self, fields: List[str]) -> Dict[str, Any]:
        """Runs `default_get` and returns default values for the requested fields."""
        _logger.debug("Executing default_get on model=%s fields=%s", self.name, fields)
        return self._execute_with_context("default_get", fields)

    def copy(self, record_id: int, default: Optional[Record] = None) -> int:
        """Runs `copy` and returns the newly created record id."""
        values = dict(default) if default is not None else {}
        _logger.debug("Executing copy on model=%s id=%s", self.name, record_id)
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
        """Runs `read_group` for grouped aggregate queries."""
        kwargs: Dict[str, Any] = {"lazy": lazy}
        if offset is not None:
            kwargs["offset"] = offset
        if limit is not None:
            kwargs["limit"] = limit
        if orderby is not None:
            kwargs["orderby"] = orderby
        kwargs.update(self._context_kwargs())

        search_domain = self._serialize_domain(domain)
        _logger.debug(
            "Executing read_group on model=%s domain=%s", self.name, search_domain
        )
        return self.client.execute(
            self.name,
            "read_group",
            search_domain,
            fields,
            groupby,
            **kwargs,
        )

    def create(self, vals: Record) -> int:
        """Creates a single record and returns its ID.

        :param vals: The values for the new record.
        :type vals: Record
        :return: The ID of the newly created record.
        :rtype: int
        """
        _logger.debug("Creating record in model=%s", self.name)
        return self._execute_with_context("create", vals)

    def write(self, ids: Union[int, List[int]], vals: Dict[str, Any]) -> bool:
        """Updates existing records.

        :param ids: The IDs of the records to update.
        :type ids: Union[int, List[int]]
        :param vals: The values to update the records with.
        :type vals: Dict[str, Any]
        :return: True if the update was successful, False otherwise.
        :rtype: bool
        """
        recordset = self._recordset(ids)
        _logger.debug("Writing model=%s ids=%s", self.name, recordset.ids)
        return recordset.write(vals)

    def unlink(self, ids: Union[int, List[int]]) -> bool:
        """Deletes existing records.

        :param ids: The IDs of the records to delete.
        :type ids: Union[int, List[int]]
        :return: True if the deletion was successful, False otherwise.
        :rtype: bool
        """
        recordset = self._recordset(ids)
        _logger.debug("Unlinking model=%s ids=%s", self.name, recordset.ids)
        return recordset.unlink()

    def fields_get(
        self, fields: Optional[List[str]] = None, attributes: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Fetches schema metadata for the model.

        :param fields: The fields to fetch metadata for, defaults to None
        :type fields: Optional[List[str]], optional
        :param attributes: The attributes to fetch for each field, defaults to None
        :type attributes: Optional[List[str]], optional
        :return: The schema metadata for the model.
        :rtype: Dict[str, Any]
        """
        _logger.debug(
            "Fetching fields_get for model=%s fields=%s attributes=%s",
            self.name,
            fields,
            attributes,
        )
        return self._env.get_field_metadata(
            self.name,
            fields,
            attributes,
        )
