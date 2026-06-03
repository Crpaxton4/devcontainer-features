from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence as SequenceABC
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Iterator,
    Optional,
    Sequence,
    TypeAlias,
    Union,
)

from .domain_expression import DomainExpression, DomainInput
from .field_adapters import adapt_field_value, adapt_record_values
from .field_values import RelationCollection, RelationValue
from .relational_command import Command, normalize_x2many_commands

if TYPE_CHECKING:
    from .odoo_env import OdooEnv


Record: TypeAlias = Mapping[str, Any]


class OdooRecordset:
    """Represent immutable record identity bound to one environment and model.

    The recordset is the architectural center of the SDK. It is necessary because it
    carries ids, model identity, environment context, metadata-driven reads, and x2many
    write normalization through one reusable abstraction.

    :param env: Environment that owns executor, context, and metadata state.
    :type env: OdooEnv
    :param model_name: Name of the Odoo model represented by the recordset.
    :type model_name: str
    :param ids: Record id or ordered ids bound to the recordset, defaults to an empty
        recordset.
    :type ids: Union[int, Sequence[int]]
    """

    def __init__(
        self,
        env: OdooEnv,
        model_name: str,
        ids: Union[int, Sequence[int]] = (),
        *,
        prefetch_ids: Union[int, Sequence[int], None] = None,
    ):
        """Initialize a recordset with environment, model, and identity state.

        The constructor is necessary because recordsets must retain immutable identity
        while sharing the environment boundary that powers execution and metadata.

        :param env: Environment that owns executor, context, and metadata state.
        :type env: OdooEnv
        :param model_name: Name of the Odoo model represented by the recordset.
        :type model_name: str
        :param ids: Record id or ordered ids bound to the recordset, defaults to an
            empty recordset.
        :type ids: Union[int, Sequence[int]]
        :return: None.
        :rtype: None
        """
        self._env = env
        self._model_name = model_name
        self._ids = self._normalize_ids(ids)
        self._prefetch_ids = self._normalize_ids(
            self._ids if prefetch_ids is None else prefetch_ids
        )

    @staticmethod
    def _normalize_ids(ids: Union[int, Sequence[int]]) -> tuple[int, ...]:
        """Normalize record ids into an immutable tuple.

        This helper is necessary because recordset identity should always use one
        canonical tuple representation regardless of how callers supplied ids.

        :param ids: Record id or iterable of ids to normalize.
        :type ids: Union[int, Sequence[int]]
        :return: Immutable ordered ids.
        :rtype: tuple[int, ...]
        """
        if isinstance(ids, int):
            return (ids,)
        return tuple(ids)

    @property
    def env(self) -> OdooEnv:
        """Expose the environment bound to this recordset.

        This property is necessary because derived recordsets and advanced callers may
        need access to the shared context and metadata boundary.

        :return: Environment bound to this recordset.
        :rtype: OdooEnv
        """
        return self._env

    @property
    def model_name(self) -> str:
        """Expose the model name carried by this recordset.

        This property is necessary because recordset operations often need to describe
        or derive additional same-model objects without guessing the model identity.

        :return: Model name represented by the recordset.
        :rtype: str
        """
        return self._model_name

    @property
    def ids(self) -> tuple[int, ...]:
        """Expose the ordered record ids for this recordset.

        This property is necessary because identity is the core payload a recordset
        carries between search, exists, browse, and write operations.

        :return: Immutable ordered record ids.
        :rtype: tuple[int, ...]
        """
        return self._ids

    @property
    def id(self) -> int:
        """Return the singleton identifier carried by this recordset.

        This property is necessary because Odoo-style recordsets expose `id` only for
        singleton recordsets and treat it as an error on empty or multi-record sets.

        :return: The singleton record id.
        :rtype: int
        """
        return self.ensure_one()._ids[0]

    def ensure_one(self) -> OdooRecordset:
        """Assert that the recordset is a singleton and return it.

        This method is necessary because many Odoo-style record operations are only
        valid on one-record recordsets and should fail loudly otherwise.

        :return: The current recordset when it contains exactly one id.
        :rtype: OdooRecordset
        :raises ValueError: When the recordset is empty or contains multiple ids.
        """
        if len(self._ids) != 1:
            raise ValueError(f"Expected singleton: {self}")
        return self

    def _derive(
        self,
        ids: Union[int, Sequence[int]] = (),
        *,
        env: OdooEnv | None = None,
        prefetch_ids: Union[int, Sequence[int], None] = None,
    ) -> OdooRecordset:
        """Create a same-model recordset with optionally new ids or environment.

        This helper is necessary because recordset operations should return new objects
        rather than mutating existing identity or context state in place.

        :param ids: Replacement record id or ids, defaults to an empty recordset.
        :type ids: Union[int, Sequence[int]]
        :param env: Replacement environment to bind, defaults to None.
        :type env: OdooEnv | None
        :return: Derived recordset sharing the model identity.
        :rtype: OdooRecordset
        """
        return OdooRecordset(
            self._env if env is None else env,
            self._model_name,
            ids,
            prefetch_ids=self._prefetch_ids if prefetch_ids is None else prefetch_ids,
        )

    def __bool__(self) -> bool:
        """Return whether the recordset contains at least one bound id.

        :return: True when the recordset is non-empty.
        :rtype: bool
        """
        return bool(self._ids)

    def __len__(self) -> int:
        """Return the number of bound ids in this recordset.

        :return: Count of bound record ids.
        :rtype: int
        """
        return len(self._ids)

    def __iter__(self) -> Iterator[OdooRecordset]:
        """Iterate over this recordset as singleton recordsets in order.

        :return: Iterator of one-record recordsets.
        :rtype: Iterator[OdooRecordset]
        """
        for record_id in self._ids:
            yield self._derive((record_id,))

    def __getitem__(self, key: int | slice) -> OdooRecordset:
        """Return a singleton recordset or sliced subset by positional lookup.

        :param key: Integer index or slice over the bound ids.
        :type key: int | slice
        :return: Singleton recordset for integer indexing or subset recordset for a slice.
        :rtype: OdooRecordset
        :raises IndexError: Propagated when the integer index is out of range.
        """
        if isinstance(key, slice):
            return self._derive(self._ids[key])
        return self._derive((self._ids[key],))

    def __getattr__(self, name: str) -> Any:
        """Resolve singleton field access using metadata and cached values.

        :param name: Field name to resolve lazily.
        :type name: str
        :return: Adapted field value or related recordset.
        :rtype: Any
        :raises AttributeError: When the name does not describe a model field.
        """
        metadata = self._env.get_field_metadata(
            self._model_name,
            fields=[name],
            attributes=["type", "relation"],
        )
        field_metadata = metadata.get(name)
        if field_metadata is None:
            raise AttributeError(
                f"{type(self).__name__!s} object has no attribute {name!r}"
            )

        self.ensure_one()
        return self._get_field_value(name, field_metadata)

    def __repr__(self) -> str:
        """Return a compact debug representation of the recordset.

        :return: Debug representation containing model name and ids.
        :rtype: str
        """
        return f"{self._model_name}{self._ids!r}"

    def _get_field_value(
        self,
        field_name: str,
        field_metadata: Dict[str, Any],
    ) -> Any:
        """Return one lazily loaded field value for the singleton recordset.

        :param field_name: Field name to resolve.
        :type field_name: str
        :param field_metadata: Metadata describing the field.
        :type field_metadata: Dict[str, Any]
        :return: Adapted field value.
        :rtype: Any
        """
        record_id = self.id
        found, value = self._env.get_cached_field_value(
            self._model_name,
            record_id,
            field_name,
        )
        if found:
            return value

        ids_to_fetch = self._env.get_missing_field_ids(
            self._model_name,
            self._prefetch_ids,
            field_name,
        )
        if ids_to_fetch:
            rows = self._materialize_records(ids=ids_to_fetch, fields=[field_name])
            for row in rows:
                if "id" not in row:
                    continue
                adapted_value = self._adapt_field_access_value(
                    row.get(field_name),
                    field_metadata,
                )
                self._env.cache_record_field_values(
                    self._model_name,
                    row["id"],
                    {field_name: adapted_value},
                )

        found, value = self._env.get_cached_field_value(
            self._model_name,
            record_id,
            field_name,
        )
        if found:
            return value
        raise AttributeError(
            f"{type(self).__name__!s} object has no attribute {field_name!r}"
        )

    def _adapt_field_access_value(
        self,
        value: Any,
        field_metadata: Dict[str, Any],
    ) -> Any:
        """Adapt one field value for singleton dot-access semantics.

        :param value: Raw field value returned by Odoo.
        :type value: Any
        :param field_metadata: Metadata describing the field.
        :type field_metadata: Dict[str, Any]
        :return: Dot-access value semantics for the field.
        :rtype: Any
        """
        field_type = field_metadata.get("type")
        relation_model = field_metadata.get("relation")

        if field_type == "many2one" and relation_model:
            relation = adapt_field_value(value, field_metadata)
            if relation is None:
                return self._env.recordset(relation_model)
            if isinstance(relation, RelationValue):
                related = self._env.recordset(relation.model_name, relation.id)
                if relation.label is not None:
                    self._env.cache_record_field_values(
                        relation.model_name,
                        relation.id,
                        {"display_name": relation.label},
                    )
                return related
            return value

        if field_type in {"one2many", "many2many"} and relation_model:
            relation = adapt_field_value(value, field_metadata)
            if isinstance(relation, RelationCollection):
                return self._env.recordset(relation.model_name, relation.ids)
            return self._env.recordset(relation_model)

        return adapt_field_value(value, field_metadata)

    def _execute(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Execute one method on the bound model through the environment executor.

        This helper is necessary because recordsets centralize model identity and thus
        can issue executor calls without every public method rebuilding that context.

        :param method: Name of the Odoo method to invoke.
        :type method: str
        :param args: Positional arguments forwarded to the executor.
        :type args: Any
        :param kwargs: Keyword arguments forwarded to the executor.
        :type kwargs: Any
        :return: Result returned by Odoo.
        :rtype: Any
        """
        return self._env.executor.execute(self._model_name, method, *args, **kwargs)

    def _context_kwargs(self) -> Dict[str, Any]:
        """Build RPC keyword arguments for the current environment context.

        This helper is necessary because every recordset operation that crosses the RPC
        boundary must reuse the same context propagation rules.

        :return: Context keyword arguments, or an empty mapping.
        :rtype: Dict[str, Any]
        """
        context = self._env.context
        if not context:
            return {}
        return {"context": context}

    def _normalize_write_values(self, values: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize write values that require metadata-aware adaptation.

        This helper is necessary because x2many write fields accept ergonomic helper
        inputs that must be converted into canonical Odoo tuple commands before the
        write reaches the server.

        :param values: Field values intended for a write operation.
        :type values: Dict[str, Any]
        :return: Normalized write values safe for Odoo RPC submission.
        :rtype: Dict[str, Any]
        """
        normalized = dict(values)
        fields_to_check = [
            field_name
            for field_name, value in normalized.items()
            if _needs_write_field_metadata(value)
        ]
        if not fields_to_check:
            return normalized

        metadata = self._env.get_field_metadata(
            self._model_name,
            fields=fields_to_check,
            attributes=["type"],
        )
        for field_name in fields_to_check:
            field_metadata = metadata.get(field_name)
            if field_metadata is None:
                continue
            field_type = field_metadata.get("type")
            if field_type in {"one2many", "many2many"}:
                normalized[field_name] = normalize_x2many_commands(
                    normalized[field_name]
                )
        return normalized

    def _search_kwargs(
        self,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build search keyword arguments from pagination and ordering options.

        This helper is necessary because all search-derived operations should share one
        translation step from recordset method arguments to Odoo RPC kwargs.

        :param limit: Maximum number of records to return, defaults to None.
        :type limit: Optional[int]
        :param offset: Number of matched rows to skip, defaults to None.
        :type offset: Optional[int]
        :param order: Odoo order expression, defaults to None.
        :type order: Optional[str]
        :return: Search keyword arguments including context when present.
        :rtype: Dict[str, Any]
        """
        kwargs = self._context_kwargs()
        if limit is not None:
            kwargs["limit"] = limit
        if offset is not None:
            kwargs["offset"] = offset
        if order is not None:
            kwargs["order"] = order
        return kwargs

    def search_ids(
        self,
        domain: DomainInput = None,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[str] = None,
    ) -> list[int]:
        """Search for matching records and return their ids.

        This method is necessary because callers often need only identity while still
        benefiting from recordset-owned domain normalization and context handling.

        :param domain: Domain used to select records, defaults to None.
        :type domain: DomainInput
        :param limit: Maximum number of ids to return, defaults to None.
        :type limit: Optional[int]
        :param offset: Number of matched rows to skip, defaults to None.
        :type offset: Optional[int]
        :param order: Odoo order expression, defaults to None.
        :type order: Optional[str]
        :return: Matching record ids.
        :rtype: list[int]
        """
        return list(
            self.search(
                domain,
                limit=limit,
                offset=offset,
                order=order,
            ).ids
        )

    def search_read(
        self,
        domain: DomainInput = None,
        *,
        fields: Optional[list[str]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[str] = None,
    ) -> list[Record]:
        """Run raw `search_read` and materialize record mappings.

        This method is necessary because Phase A preserves explicit raw extraction for
        search-derived reads even after richer semantic layers were introduced.

        :param domain: Domain used to select records, defaults to None.
        :type domain: DomainInput
        :param fields: Optional field names to request, defaults to None.
        :type fields: Optional[list[str]]
        :param limit: Maximum number of rows to return, defaults to None.
        :type limit: Optional[int]
        :param offset: Number of matched rows to skip, defaults to None.
        :type offset: Optional[int]
        :param order: Odoo order expression, defaults to None.
        :type order: Optional[str]
        :return: Raw record mappings.
        :rtype: list[Record]
        """
        return self._materialize_records(
            domain=domain,
            fields=fields,
            limit=limit,
            offset=offset,
            order=order,
        )

    def _materialize_records(
        self,
        *,
        ids: Optional[Sequence[int]] = None,
        domain: DomainInput = None,
        fields: Optional[list[str]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[str] = None,
        adapt: bool = False,
    ) -> list[Record]:
        """Materialize records by direct ids or by a search-derived read.

        This helper is necessary because raw and adapted reads share the same decision
        tree for direct `read` versus `search_read` execution.

        :param ids: Explicit record ids to read, defaults to None.
        :type ids: Optional[Sequence[int]]
        :param domain: Domain used when ids are not supplied, defaults to None.
        :type domain: DomainInput
        :param fields: Optional field names to request, defaults to None.
        :type fields: Optional[list[str]]
        :param limit: Maximum number of rows to return, defaults to None.
        :type limit: Optional[int]
        :param offset: Number of matched rows to skip, defaults to None.
        :type offset: Optional[int]
        :param order: Odoo order expression, defaults to None.
        :type order: Optional[str]
        :param adapt: When True, adapt the resulting records, defaults to False.
        :type adapt: bool
        :return: Raw or adapted record mappings.
        :rtype: list[Record]
        """
        if ids is not None:
            if not ids:
                return []

            kwargs = self._context_kwargs()
            if fields is not None:
                kwargs["fields"] = fields
            records = self._execute("read", list(ids), **kwargs)
        else:
            serialized_domain = DomainExpression.normalize(domain).serialize()
            kwargs = self._search_kwargs(limit=limit, offset=offset, order=order)
            if fields is not None:
                kwargs["fields"] = fields
            records = self._execute("search_read", serialized_domain, **kwargs)

        if not adapt or not records:
            return records

        return self._adapt_records(records, fields)

    def _adapt_records(
        self,
        records: list[Record],
        fields: Optional[list[str]] = None,
    ) -> list[Record]:
        """Apply shared field adaptation to materialized records.

        This helper is necessary because adapted read paths must use cached metadata
        and one shared adaptation layer instead of per-call decoding logic.

        :param records: Raw records returned by Odoo.
        :type records: list[Record]
        :param fields: Optional requested field names, defaults to None.
        :type fields: Optional[list[str]]
        :return: Adapted record mappings.
        :rtype: list[Record]
        """
        metadata_fields = self._metadata_fields(records, fields)
        if not metadata_fields:
            return [dict(record) for record in records]

        metadata = self._env.get_field_metadata(
            self._model_name,
            metadata_fields,
            ["type", "relation"],
        )
        return [adapt_record_values(record, metadata) for record in records]

    @staticmethod
    def _metadata_fields(
        records: Sequence[Record],
        fields: Optional[Sequence[str]],
    ) -> list[str]:
        """Determine which field names require metadata lookup for adaptation.

        This helper is necessary because the adapter layer should fetch only the field
        metadata it actually needs and should ignore the synthetic `id` field.

        :param records: Materialized records whose keys may require metadata.
        :type records: Sequence[Record]
        :param fields: Optional explicitly requested field names.
        :type fields: Optional[Sequence[str]]
        :return: Ordered unique field names that require metadata.
        :rtype: list[str]
        """
        if fields is not None:
            return [
                field_name
                for field_name in dict.fromkeys(fields)
                if field_name != "id"
            ]

        names: list[str] = []
        for record in records:
            names.extend(record.keys())

        return [
            field_name
            for field_name in dict.fromkeys(names)
            if field_name != "id"
        ]

    def search_read_adapted(
        self,
        domain: DomainInput = None,
        *,
        fields: Optional[list[str]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[str] = None,
    ) -> list[Record]:
        """Run adapted `search_read` and materialize semantic record values.

        This method is necessary because Phase B exposes richer relation and temporal
        semantics for search-derived reads without replacing the raw path.

        :param domain: Domain used to select records, defaults to None.
        :type domain: DomainInput
        :param fields: Optional field names to request, defaults to None.
        :type fields: Optional[list[str]]
        :param limit: Maximum number of rows to return, defaults to None.
        :type limit: Optional[int]
        :param offset: Number of matched rows to skip, defaults to None.
        :type offset: Optional[int]
        :param order: Odoo order expression, defaults to None.
        :type order: Optional[str]
        :return: Adapted record mappings.
        :rtype: list[Record]
        """
        return self._materialize_records(
            domain=domain,
            fields=fields,
            limit=limit,
            offset=offset,
            order=order,
            adapt=True,
        )

    def search_count(self, domain: DomainInput = None) -> int:
        """Return the number of records matching a domain.

        This method is necessary because callers often need cardinality information
        without materializing ids or records.

        :param domain: Domain used to count records, defaults to None.
        :type domain: DomainInput
        :return: Number of matched records.
        :rtype: int
        """
        serialized_domain = DomainExpression.normalize(domain).serialize()
        return self._execute(
            "search_count",
            serialized_domain,
            **self._context_kwargs(),
        )

    def create(self, values: Dict[str, Any]) -> int:
        """Create one record on the bound model.

        This method is necessary because model-bound recordsets are now the primary
        public entry path and therefore must expose model-level create behavior.

        :param values: Field values for the new record.
        :type values: Dict[str, Any]
        :return: Identifier of the created record.
        :rtype: int
        """
        normalized_values = self._normalize_write_values(values)
        return self._execute(
            "create",
            normalized_values,
            **self._context_kwargs(),
        )

    def fields_get(
        self,
        allfields: Optional[list[str]] = None,
        attributes: Optional[list[str]] = None,
        *,
        refresh: bool = False,
    ) -> Dict[str, Any]:
        """Return cached model metadata for the bound model.

        This method is necessary because model-bound recordsets replace model proxies
        as the main public entry point and must still expose field metadata lookup.

        :param allfields: Optional subset of field names to request.
        :type allfields: Optional[list[str]]
        :param attributes: Optional subset of metadata attributes to request.
        :type attributes: Optional[list[str]]
        :param refresh: When True, bypass the metadata cache.
        :type refresh: bool
        :return: Raw metadata keyed by field name.
        :rtype: Dict[str, Any]
        """
        return self._env.get_field_metadata(
            self._model_name,
            fields=allfields,
            attributes=attributes,
            refresh=refresh,
        )

    def search_write(
        self,
        domain: DomainInput,
        values: Dict[str, Any],
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[str] = None,
    ) -> bool:
        """Search for matching records and update them.

        This method is necessary because search-driven write flows should still reuse
        recordset-owned write semantics after ids are resolved.

        :param domain: Domain used to select records for update.
        :type domain: DomainInput
        :param values: Field values to write.
        :type values: Dict[str, Any]
        :param limit: Maximum number of records to update, defaults to None.
        :type limit: Optional[int]
        :param offset: Number of matched rows to skip, defaults to None.
        :type offset: Optional[int]
        :param order: Odoo order expression, defaults to None.
        :type order: Optional[str]
        :return: True when Odoo reports a successful update.
        :rtype: bool
        """
        return self.search(
            domain,
            limit=limit,
            offset=offset,
            order=order,
        ).write(values)

    def search_unlink(
        self,
        domain: DomainInput,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[str] = None,
    ) -> bool:
        """Search for matching records and delete them.

        This method is necessary because search-driven delete flows should still reuse
        recordset-owned unlink semantics after ids are resolved.

        :param domain: Domain used to select records for deletion.
        :type domain: DomainInput
        :param limit: Maximum number of records to delete, defaults to None.
        :type limit: Optional[int]
        :param offset: Number of matched rows to skip, defaults to None.
        :type offset: Optional[int]
        :param order: Odoo order expression, defaults to None.
        :type order: Optional[str]
        :return: True when Odoo reports a successful delete.
        :rtype: bool
        """
        return self.search(
            domain,
            limit=limit,
            offset=offset,
            order=order,
        ).unlink()

    def read(self, fields: Optional[list[str]] = None) -> list[Record]:
        """Read the current ids using raw Phase A semantics.

        This method is necessary because explicit raw extraction remains part of the
        supported compatibility contract even in the recordset-centered architecture.

        :param fields: Optional field names to request, defaults to None.
        :type fields: Optional[list[str]]
        :return: Raw record mappings for the current ids.
        :rtype: list[Record]
        """
        return self._materialize_records(
            ids=self._ids,
            fields=fields,
        )

    def read_adapted(self, fields: Optional[list[str]] = None) -> list[Record]:
        """Read the current ids using Phase B field adaptation.

        This method is necessary because recordset-centered reads are the canonical
        place to expose richer semantic values for the current identity set.

        :param fields: Optional field names to request, defaults to None.
        :type fields: Optional[list[str]]
        :return: Adapted record mappings for the current ids.
        :rtype: list[Record]
        """
        return self._materialize_records(
            ids=self._ids,
            fields=fields,
            adapt=True,
        )

    def write(self, values: Dict[str, Any]) -> bool:
        """Write values to the current ids.

        This method is necessary because the recordset is the canonical owner of write
        semantics for its bound identity and environment.

        :param values: Field values to write.
        :type values: Dict[str, Any]
        :return: True when Odoo reports a successful update.
        :rtype: bool
        """
        normalized_values = self._normalize_write_values(values)
        return self._execute(
            "write",
            list(self._ids),
            normalized_values,
            **self._context_kwargs(),
        )

    def unlink(self) -> bool:
        """Delete the current ids.

        This method is necessary because the recordset is the canonical owner of unlink
        semantics for its bound identity and environment.

        :return: True when Odoo reports a successful delete.
        :rtype: bool
        """
        return self._execute(
            "unlink",
            list(self._ids),
            **self._context_kwargs(),
        )

    def exists(self) -> OdooRecordset:
        """Return a new recordset containing only ids that still exist.

        This method is necessary because remote state can drift and callers often need
        to revalidate a recordset's identity without losing ordering.

        :return: Recordset containing only surviving ids.
        :rtype: OdooRecordset
        """
        if not self._ids:
            return self._derive()

        existing_ids = set(
            self.search([("id", "in", list(self._ids))]).ids
        )
        surviving_ids = [record_id for record_id in self._ids if record_id in existing_ids]
        return self._derive(surviving_ids)

    def browse(self, ids: Union[int, Sequence[int]]) -> OdooRecordset:
        """Return a same-model recordset for the provided ids without I/O.

        This method is necessary because recordsets model identity separately from data
        loading, so callers need a cheap way to bind new ids without reading them.

        :param ids: Record id or ids to bind.
        :type ids: Union[int, Sequence[int]]
        :return: Derived recordset bound to the provided ids.
        :rtype: OdooRecordset
        """
        return self._derive(ids, prefetch_ids=ids)

    def search(
        self,
        domain: DomainInput = None,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[str] = None,
    ) -> OdooRecordset:
        """Search the bound model and return a new recordset of matching ids.

        This method is necessary because recordset search is the core identity-producing
        operation in the recordset-first architecture.

        :param domain: Domain used to select records, defaults to None.
        :type domain: DomainInput
        :param limit: Maximum number of ids to return, defaults to None.
        :type limit: Optional[int]
        :param offset: Number of matched rows to skip, defaults to None.
        :type offset: Optional[int]
        :param order: Odoo order expression, defaults to None.
        :type order: Optional[str]
        :return: Recordset containing the matching ids.
        :rtype: OdooRecordset
        """
        serialized_domain = DomainExpression.normalize(domain).serialize()
        kwargs = self._search_kwargs(limit=limit, offset=offset, order=order)
        ids = self._execute("search", serialized_domain, **kwargs)
        return self._derive(ids, prefetch_ids=ids)

    def with_context(self, context: Dict[str, Any]) -> OdooRecordset:
        """Return a new recordset bound to a derived environment context.

        This method is necessary because context changes should fork the environment
        while preserving the current model identity and record ids.

        :param context: Additional Odoo context keys to merge.
        :type context: Dict[str, Any]
        :return: Derived recordset bound to a derived environment.
        :rtype: OdooRecordset
        """
        return self._derive(self._ids, env=self._env.with_context(context))


def _needs_write_field_metadata(value: Any) -> bool:
    """Return whether a write value requires field metadata inspection.

    This helper is necessary because only x2many-like values need metadata-aware
    normalization before a write operation is issued.

    :param value: Candidate write value.
    :type value: Any
    :return: True when metadata lookup may be required to normalize the value.
    :rtype: bool
    """
    if isinstance(value, Command):
        return True
    return isinstance(value, SequenceABC) and not isinstance(
        value,
        (str, bytes, bytearray),
    )
