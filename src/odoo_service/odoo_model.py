import logging
from typing import Any, Dict, List, Optional, Union

from ..utils import Record
from ..utils.types import DomainInput
from .domain_expression import DomainExpression
from .odoo_executor import OdooExecutor
from .odoo_query import OdooQuery

_logger = logging.getLogger(__name__)


class OdooModel:
    """
    Client for a specific Odoo table/model.
    """

    def __init__(self, client: OdooExecutor, name: str):
        self.client = client
        self.name = name

    @staticmethod
    def _normalize_ids(ids: Union[int, List[int]]) -> List[int]:
        if isinstance(ids, int):
            return [ids]
        return list(ids)

    @staticmethod
    def _serialize_domain(domain: DomainInput) -> List[Any]:
        return DomainExpression.normalize(domain).serialize()

    def search(self, domain: DomainInput = None) -> OdooQuery:
        """Starts a fluent query. Returns an OdooQuery builder."""
        _logger.debug("Building query for model=%s domain=%s", self.name, domain)
        return OdooQuery(self.client, self.name, domain)

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
        normalized_ids = self._normalize_ids(ids)
        _logger.debug(
            "Reading model=%s ids=%s fields=%s", self.name, normalized_ids, fields
        )
        kwargs = {}
        if fields is not None:
            kwargs["fields"] = fields
        return self.client.execute(self.name, "read", normalized_ids, **kwargs)

    def browse(
        self, ids: Union[int, List[int]], fields: Optional[List[str]] = None
    ) -> List[Record]:
        """Convenience alias for read() to mirror ORM-style call sites."""
        return self.read(self._normalize_ids(ids), fields)

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
        query = self.search(domain)
        if limit is not None:
            query = query.limit(limit)
        if offset is not None:
            query = query.offset(offset)
        if order is not None:
            query = query.order_by(order)
        if context is not None:
            query = query.with_context(context)

        return query.ids()

    def exists(self, ids: Union[int, List[int]]) -> List[int]:
        """Returns ids that still exist on the server, preserving input order."""
        normalized_ids = self._normalize_ids(ids)
        if not normalized_ids:
            return []

        existing_ids = set(self.search_ids([("id", "in", normalized_ids)]))
        return [record_id for record_id in normalized_ids if record_id in existing_ids]

    def search_read(
        self,
        domain: DomainInput = None,
        fields: Optional[List[str]] = None,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[str] = None,
    ) -> List[Record]:
        """Runs `search_read` with optional pagination and field selection."""
        kwargs: Dict[str, Any] = {}
        if fields is not None:
            kwargs["fields"] = fields
        if limit is not None:
            kwargs["limit"] = limit
        if offset is not None:
            kwargs["offset"] = offset
        if order is not None:
            kwargs["order"] = order

        search_domain = self._serialize_domain(domain)
        _logger.debug(
            "Executing search_read on model=%s domain=%s kwargs=%s",
            self.name,
            search_domain,
            kwargs,
        )
        return self.client.execute(self.name, "search_read", search_domain, **kwargs)

    def search_count(self, domain: DomainInput = None) -> int:
        """Runs `search_count` and returns the number of matched records."""
        search_domain = self._serialize_domain(domain)
        _logger.debug(
            "Executing search_count on model=%s domain=%s", self.name, search_domain
        )
        return self.client.execute(self.name, "search_count", search_domain)

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

        _logger.debug("Executing name_search on model=%s name=%s", self.name, name)
        return self.client.execute(self.name, "name_search", name, **kwargs)

    def name_get(self, ids: Union[int, List[int]]) -> List[List[Any]]:
        """Runs `name_get` for the provided ids."""
        normalized_ids = self._normalize_ids(ids)
        if not normalized_ids:
            raise ValueError("name_get requires at least one id")
        _logger.debug(
            "Executing name_get on model=%s ids=%s", self.name, normalized_ids
        )
        return self.client.execute(self.name, "name_get", normalized_ids)

    def default_get(self, fields: List[str]) -> Dict[str, Any]:
        """Runs `default_get` and returns default values for the requested fields."""
        _logger.debug("Executing default_get on model=%s fields=%s", self.name, fields)
        return self.client.execute(self.name, "default_get", fields)

    def copy(self, record_id: int, default: Optional[Record] = None) -> int:
        """Runs `copy` and returns the newly created record id."""
        values = dict(default) if default is not None else {}
        _logger.debug("Executing copy on model=%s id=%s", self.name, record_id)
        return self.client.execute(self.name, "copy", record_id, values)

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
        if not fields:
            raise ValueError("read_group requires at least one field")
        if not groupby:
            raise ValueError("read_group requires at least one groupby field")

        kwargs: Dict[str, Any] = {"lazy": lazy}
        if offset is not None:
            kwargs["offset"] = offset
        if limit is not None:
            kwargs["limit"] = limit
        if orderby is not None:
            kwargs["orderby"] = orderby

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
        return self.client.execute(self.name, "create", vals)

    def write(self, ids: Union[int, List[int]], vals: Dict[str, Any]) -> bool:
        """Updates existing records.

        :param ids: The IDs of the records to update.
        :type ids: Union[int, List[int]]
        :param vals: The values to update the records with.
        :type vals: Dict[str, Any]
        :return: True if the update was successful, False otherwise.
        :rtype: bool
        """
        normalized_ids = self._normalize_ids(ids)
        if not normalized_ids:
            raise ValueError("write requires at least one id")
        if not vals:
            raise ValueError("write requires at least one value")

        _logger.debug("Writing model=%s ids=%s", self.name, normalized_ids)
        return self.client.execute(self.name, "write", normalized_ids, vals)

    def unlink(self, ids: Union[int, List[int]]) -> bool:
        """Deletes existing records.

        :param ids: The IDs of the records to delete.
        :type ids: Union[int, List[int]]
        :return: True if the deletion was successful, False otherwise.
        :rtype: bool
        """
        normalized_ids = self._normalize_ids(ids)
        if not normalized_ids:
            raise ValueError("unlink requires at least one id")
        _logger.debug("Unlinking model=%s ids=%s", self.name, normalized_ids)
        return self.client.execute(self.name, "unlink", normalized_ids)

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
        kwargs: Dict[str, Any] = {}
        if fields is not None:
            kwargs["allfields"] = fields
        if attributes is not None:
            kwargs["attributes"] = attributes
        _logger.debug(
            "Fetching fields_get for model=%s fields=%s attributes=%s",
            self.name,
            fields,
            attributes,
        )
        return self.client.execute(self.name, "fields_get", **kwargs)
