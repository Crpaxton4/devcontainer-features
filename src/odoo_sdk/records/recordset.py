from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence as SequenceABC
from typing import Any, Dict, Iterator, Optional, Sequence, TypeAlias, Union

from odoo_sdk._utils import _dedup_field_names
from odoo_sdk.env import OdooEnv
from odoo_sdk.fields import adapt_field_value, adapt_record_values
from odoo_sdk.fields.commands import Command, normalize_x2many_commands
from odoo_sdk.fields.values import RelationCollection, RelationValue
from odoo_sdk.query.domain import DomainExpression, DomainInput

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

    # ------------------------------------------------------------------
    # Set algebra operators
    # ------------------------------------------------------------------

    def _check_same_model(self, other: OdooRecordset) -> None:
        """Raise ValueError when *other* belongs to a different model.

        :param other: The other operand to validate.
        :type other: OdooRecordset
        :raises ValueError: When model names differ.
        """
        if self._model_name != other._model_name:
            raise ValueError(
                f"Set operations require the same model: "
                f"{self._model_name!r} != {other._model_name!r}"
            )

    def __or__(self, other: OdooRecordset) -> OdooRecordset:
        """Return the union of two same-model recordsets.

        Preserves the order of *self*, then appends any ids from *other* not
        already present.  Deduplicates by id.

        :param other: Recordset to merge into this one.
        :type other: OdooRecordset
        :return: New recordset containing all ids from both operands.
        :rtype: OdooRecordset
        :raises ValueError: When operands are from different models.
        """
        self._check_same_model(other)
        seen = set(self._ids)
        extra = tuple(i for i in other._ids if i not in seen)
        return self._derive(self._ids + extra)

    def __and__(self, other: OdooRecordset) -> OdooRecordset:
        """Return the intersection of two same-model recordsets.

        Preserves the order of *self*; only ids present in *other* are kept.

        :param other: Recordset to intersect with.
        :type other: OdooRecordset
        :return: New recordset with ids common to both operands.
        :rtype: OdooRecordset
        :raises ValueError: When operands are from different models.
        """
        self._check_same_model(other)
        other_set = set(other._ids)
        return self._derive(tuple(i for i in self._ids if i in other_set))

    def __sub__(self, other: OdooRecordset) -> OdooRecordset:
        """Return the difference of two same-model recordsets.

        Preserves the order of *self*; ids present in *other* are removed.

        :param other: Recordset whose ids will be excluded.
        :type other: OdooRecordset
        :return: New recordset with ids in *self* that are absent from *other*.
        :rtype: OdooRecordset
        :raises ValueError: When operands are from different models.
        """
        self._check_same_model(other)
        other_set = set(other._ids)
        return self._derive(tuple(i for i in self._ids if i not in other_set))

    def __contains__(self, record: object) -> bool:
        """Test whether *record* (a singleton recordset) is present in this recordset.

        :param record: A singleton ``OdooRecordset`` to look up.
        :type record: object
        :return: ``True`` when the record's id is present.
        :rtype: bool
        :raises TypeError: When *record* is not an ``OdooRecordset``.
        :raises ValueError: When *record* is not a singleton or models differ.
        """
        if not isinstance(record, OdooRecordset):
            raise TypeError(
                f"'in' requires an OdooRecordset singleton, got {type(record).__name__!r}"
            )
        if len(record._ids) != 1:
            raise ValueError(
                f"'in' requires a singleton recordset, got {len(record._ids)} records"
            )
        self._check_same_model(record)
        return record._ids[0] in set(self._ids)

    def __le__(self, other: OdooRecordset) -> bool:
        """Return ``True`` if every id in *self* is present in *other* (subset test).

        :param other: The potential superset.
        :type other: OdooRecordset
        :return: ``True`` when *self* is a subset of *other*.
        :rtype: bool
        :raises ValueError: When operands are from different models.
        """
        self._check_same_model(other)
        return set(self._ids) <= set(other._ids)

    def __lt__(self, other: OdooRecordset) -> bool:
        """Return ``True`` if *self* is a strict subset of *other*.

        :param other: The potential strict superset.
        :type other: OdooRecordset
        :return: ``True`` when *self* is a proper subset of *other*.
        :rtype: bool
        :raises ValueError: When operands are from different models.
        """
        self._check_same_model(other)
        return set(self._ids) < set(other._ids)

    def __ge__(self, other: OdooRecordset) -> bool:
        """Return ``True`` if every id in *other* is present in *self* (superset test).

        :param other: The potential subset.
        :type other: OdooRecordset
        :return: ``True`` when *self* is a superset of *other*.
        :rtype: bool
        :raises ValueError: When operands are from different models.
        """
        self._check_same_model(other)
        return set(self._ids) >= set(other._ids)

    def __gt__(self, other: OdooRecordset) -> bool:
        """Return ``True`` if *self* is a strict superset of *other*.

        :param other: The potential strict subset.
        :type other: OdooRecordset
        :return: ``True`` when *self* is a proper superset of *other*.
        :rtype: bool
        :raises ValueError: When operands are from different models.
        """
        self._check_same_model(other)
        return set(self._ids) > set(other._ids)

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
            self._populate_field_cache(field_name, field_metadata, ids_to_fetch)

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

    def _populate_field_cache(
        self,
        field_name: str,
        field_metadata: Dict[str, Any],
        ids_to_fetch: list[int],
    ) -> None:
        """Fetch field values from Odoo and store them in the environment cache.

        This helper is necessary because the lazy-load path in ``_get_field_value``
        should be a single named step rather than an inline side-effect block.

        :param field_name: Field name to load.
        :type field_name: str
        :param field_metadata: Metadata describing the field for adaptation.
        :type field_metadata: Dict[str, Any]
        :param ids_to_fetch: Record ids that are missing the cached value.
        :type ids_to_fetch: list[int]
        :return: None.
        :rtype: None
        """
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
        tree for direct ``read`` versus ``search_read`` execution.

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
            records = self._read_by_ids(ids, fields)
        else:
            records = self._search_read_core(
                domain, fields, limit=limit, offset=offset, order=order
            )

        if not adapt or not records:
            return records

        return self._adapt_records(records, fields)

    def _read_by_ids(
        self,
        ids: Sequence[int],
        fields: Optional[list[str]] = None,
    ) -> list[Record]:
        """Execute a ``read`` call for the given ids.

        This helper is necessary because ``_materialize_records`` should delegate
        each execution branch to a focused method rather than building kwargs inline
        in two places.

        :param ids: Record ids to read.
        :type ids: Sequence[int]
        :param fields: Optional field names to request, defaults to None.
        :type fields: Optional[list[str]]
        :return: Raw record mappings.
        :rtype: list[Record]
        """
        kwargs = self._context_kwargs()
        if fields is not None:
            kwargs["fields"] = fields
        return self._execute("read", list(ids), **kwargs)

    def _search_read_core(
        self,
        domain: DomainInput = None,
        fields: Optional[list[str]] = None,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[str] = None,
    ) -> list[Record]:
        """Execute a ``search_read`` call for the given domain.

        This helper is necessary because ``_materialize_records`` should delegate
        each execution branch to a focused method rather than building kwargs inline
        in two places.

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
        serialized_domain = DomainExpression.normalize(domain).serialize()
        kwargs = self._search_kwargs(limit=limit, offset=offset, order=order)
        if fields is not None:
            kwargs["fields"] = fields
        return self._execute("search_read", serialized_domain, **kwargs)

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
        metadata it actually needs and should ignore the synthetic ``id`` field.

        :param records: Materialized records whose keys may require metadata.
        :type records: Sequence[Record]
        :param fields: Optional explicitly requested field names.
        :type fields: Optional[Sequence[str]]
        :return: Ordered unique field names that require metadata.
        :rtype: list[str]
        """
        if fields is not None:
            return _dedup_field_names(fields)

        names: list[str] = []
        for record in records:
            names.extend(record.keys())
        return _dedup_field_names(names)

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

    def name_create(self, name: str) -> OdooRecordset:
        """Create a new record from a display name and return it as a singleton recordset.

        This method is necessary because ``name_create`` is a standard Odoo shortcut
        that creates a record using only its ``name`` field and must return an
        ``OdooRecordset`` rather than a raw id.

        :param name: Display name for the new record.
        :type name: str
        :return: Singleton recordset for the newly created record.
        :rtype: OdooRecordset
        """
        result = self._execute("name_create", name, **self._context_kwargs())
        return self._derive(result[0])

    def name_search(
        self,
        name: str = "",
        domain: DomainInput = None,
        operator: str = "ilike",
        limit: int = 100,
    ) -> list[tuple[int, str]]:
        """Search records by display name and return (id, display_name) pairs.

        This method is necessary because ``name_search`` is the standard Odoo lookup
        for picking-widget style queries and must be reachable through the recordset API
        so context is forwarded automatically.

        :param name: Display name fragment to match, defaults to ``''``.
        :type name: str
        :param domain: Additional domain to narrow results, defaults to None.
        :type domain: DomainInput
        :param operator: Comparison operator applied to the name, defaults to
            ``'ilike'``.
        :type operator: str
        :param limit: Maximum number of pairs to return, defaults to 100.
        :type limit: int
        :return: List of ``(id, display_name)`` pairs.
        :rtype: list[tuple[int, str]]
        """
        serialized_domain = DomainExpression.normalize(domain).serialize()
        return self._execute(
            "name_search",
            name,
            serialized_domain,
            operator,
            limit,
            **self._context_kwargs(),
        )

    def default_get(self, fields: list[str]) -> dict:
        """Return server-side default values for the requested fields.

        This method is necessary because ``default_get`` is the authoritative source
        for server-configured defaults and must honour the current environment context
        when computing them.

        :param fields: Field names for which defaults should be fetched.
        :type fields: list[str]
        :return: Dict of default values keyed by field name; only fields for which the
            server has a default are included.
        :rtype: dict
        """
        return self._execute("default_get", fields, **self._context_kwargs())

    def copy(self, default: dict | None = None) -> OdooRecordset:
        """Duplicate the singleton record and return the copy as a new recordset.

        This method is necessary because ``copy`` is only meaningful for a single
        record and must return an ``OdooRecordset`` so the caller stays within the
        recordset-first API.

        :param default: Field values to override on the copy, defaults to None.
        :type default: dict | None
        :return: Singleton recordset for the duplicated record.
        :rtype: OdooRecordset
        :raises ValueError: When the recordset contains more than one record.
        """
        self.ensure_one()
        result = self._execute(
            "copy",
            self._ids[0],
            default or {},
            **self._context_kwargs(),
        )
        return self._derive(result)

    def get_metadata(self) -> list[dict]:
        """Return audit metadata for all records in the recordset.

        This method is necessary because ``get_metadata`` is the standard Odoo RPC
        call for retrieving audit fields and must be accessible through the recordset
        API so context is forwarded automatically.

        :return: List of dicts with keys ``id``, ``create_uid``, ``create_date``,
            ``write_uid``, ``write_date``, ``xmlid``, ``xmlids``, ``noupdate``; one
            dict per record in the recordset.
        :rtype: list[dict]
        """
        return self._execute(
            "get_metadata",
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

        existing_ids = set(self.search([("id", "in", self._ids)]).ids)
        surviving_ids = [
            record_id for record_id in self._ids if record_id in existing_ids
        ]
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

    def _read_group(
        self,
        domain: DomainInput = None,
        groupby: Sequence[str] = (),
        aggregates: Sequence[str] = (),
        having: DomainInput = None,
        offset: int = 0,
        limit: Optional[int] = None,
        order: Optional[str] = None,
    ) -> list[tuple]:
        """Run server-side aggregation using Odoo's public ``read_group`` XML-RPC method.

        This method is necessary because server-side grouping and aggregation are
        impractical to reproduce client-side, especially when access rules filter
        the underlying records.

        The implementation calls the public ``read_group`` XML-RPC method (not the
        internal ``_read_group`` ORM method, which is not accessible over XML-RPC).
        Response dicts from ``read_group`` use base field names as keys (e.g.
        ``amount_total`` for an ``amount_total:sum`` aggregate), so the response is
        mapped back to the specifier-ordered tuple shape before returning.

        ``__count`` is a special aggregate that ``read_group`` always includes in
        its response dict; it does not need to be passed to the server as a field.

        :param domain: Domain used to filter records before grouping, defaults to None.
        :type domain: DomainInput
        :param groupby: Sequence of field names or ``'field:granularity'`` strings,
            defaults to an empty sequence.
        :type groupby: Sequence[str]
        :param aggregates: Sequence of ``'field:agg'`` aggregate specifier strings.
            ``'__count'`` is accepted and extracts the per-group record count from
            the server response without being forwarded as a field to the server.
            Defaults to an empty sequence.
        :type aggregates: Sequence[str]
        :param having: Not supported over XML-RPC. A non-empty value raises
            ``NotImplementedError``.
        :type having: DomainInput
        :param offset: Number of result groups to skip, defaults to 0.
        :type offset: int
        :param limit: Maximum number of result groups to return, defaults to None.
        :type limit: Optional[int]
        :param order: Odoo order expression for result groups, defaults to None.
        :type order: Optional[str]
        :return: List of tuples matching the Odoo ``_read_group`` response shape.
        :rtype: list[tuple]
        :raises NotImplementedError: When a non-empty ``having`` domain is provided.
        """
        serialized_domain = DomainExpression.normalize(domain).serialize()
        serialized_having = DomainExpression.normalize(having).serialize()
        if serialized_having:
            raise NotImplementedError(
                "_read_group 'having' is not supported by the Odoo XML-RPC "
                "'read_group' method. Filter results client-side after retrieval, "
                "or express the filter as part of the 'domain' parameter instead."
            )
        kwargs: Dict[str, Any] = self._context_kwargs()
        kwargs["lazy"] = False
        if offset:
            kwargs["offset"] = offset
        if limit is not None:
            kwargs["limit"] = limit
        if order is not None:
            kwargs["orderby"] = order
        # __count is always present in read_group responses; omit from server fields.
        server_aggregates = [f for f in aggregates if f != "__count"]
        rows = self._execute(
            "read_group",
            serialized_domain,
            list(server_aggregates),
            list(groupby),
            **kwargs,
        )
        return self._convert_read_group_rows(rows, groupby, aggregates)

    def _convert_read_group_rows(
        self,
        rows: list[Any],
        groupby: Sequence[str],
        aggregates: Sequence[str],
    ) -> list[tuple]:
        """Convert raw ``_read_group`` row dicts to a list of tuples.

        This helper is necessary because the public ``_read_group`` method should
        separate RPC dispatch from response shaping and recordset reconstruction.

        :param rows: Raw row dicts returned by the server.
        :type rows: list[Any]
        :param groupby: Groupby specifier strings used in the request.
        :type groupby: Sequence[str]
        :param aggregates: Aggregate specifier strings used in the request.
        :type aggregates: Sequence[str]
        :return: Ordered tuples of group keys and aggregate values.
        :rtype: list[tuple]
        """
        if not rows:
            return []

        all_specs = list(groupby) + list(aggregates)
        recordset_specs = self._find_recordset_specs(aggregates)

        if not recordset_specs:
            return self._build_simple_group_tuples(rows, all_specs)

        metadata = self._fetch_recordset_metadata(recordset_specs)
        return [
            self._build_group_tuple(row, all_specs, recordset_specs, metadata)
            for row in rows
        ]

    @staticmethod
    def _find_recordset_specs(aggregates: Sequence[str]) -> set[str]:
        """Return the subset of aggregate specifiers that end in ``:recordset``.

        This helper is necessary because isolating the detection step keeps
        ``_convert_read_group_rows`` below the A-grade cyclomatic complexity threshold.

        :param aggregates: Aggregate specifier strings to inspect.
        :type aggregates: Sequence[str]
        :return: Specifiers that require ``OdooRecordset`` wrapping.
        :rtype: set[str]
        """
        return {spec for spec in aggregates if spec.endswith(":recordset")}

    def _build_simple_group_tuples(
        self,
        rows: list[Any],
        all_specs: list[str],
    ) -> list[tuple]:
        """Build tuples from rows that contain no recordset aggregates.

        This helper is necessary because extracting the simple path from
        ``_convert_read_group_rows`` keeps each branch as a flat, analysable unit.

        :param rows: Raw row dicts returned by the server.
        :type rows: list[Any]
        :param all_specs: Ordered groupby and aggregate specifiers.
        :type all_specs: list[str]
        :return: Ordered tuples of group keys and aggregate values.
        :rtype: list[tuple]
        """
        return [tuple(row[spec.split(":")[0]] for spec in all_specs) for row in rows]

    def _fetch_recordset_metadata(
        self,
        recordset_specs: set[str],
    ) -> Dict[str, Any]:
        """Fetch field relation metadata for recordset aggregate specs.

        This helper is necessary because ``_convert_read_group_rows`` should delegate
        the metadata lookup to a single-purpose step rather than inlining it.

        :param recordset_specs: Aggregate specifier strings ending in ``:recordset``.
        :type recordset_specs: set[str]
        :return: Field metadata keyed by field name.
        :rtype: Dict[str, Any]
        """
        field_names = [spec.split(":")[0] for spec in recordset_specs]
        return self._env.get_field_metadata(
            self._model_name,
            fields=field_names,
            attributes=["relation"],
        )

    def _build_group_tuple(
        self,
        row: Any,
        all_specs: list[str],
        recordset_specs: set[str],
        metadata: Dict[str, Any],
    ) -> tuple:
        """Build one result tuple from a raw ``_read_group`` row.

        This helper is necessary because ``_convert_read_group_rows`` should express
        the per-row transformation as a named step rather than an inline nested loop.

        :param row: Raw row dict from the server.
        :type row: Any
        :param all_specs: Ordered groupby and aggregate specifiers.
        :type all_specs: list[str]
        :param recordset_specs: Specifiers that should produce ``OdooRecordset`` values.
        :type recordset_specs: set[str]
        :param metadata: Field relation metadata for recordset conversion.
        :type metadata: Dict[str, Any]
        :return: One result tuple matching the specifier order.
        :rtype: tuple
        """
        return tuple(
            (
                self._resolve_recordset_value(spec, row[spec.split(":")[0]], metadata)
                if spec in recordset_specs
                else row[spec.split(":")[0]]
            )
            for spec in all_specs
        )

    def _resolve_recordset_value(
        self,
        spec: str,
        value: Any,
        metadata: Dict[str, Any],
    ) -> OdooRecordset:
        """Wrap a raw list of ids for one recordset aggregate into an ``OdooRecordset``.

        This helper is necessary because the relation model resolution and recordset
        construction for a single ``:recordset`` aggregate value is a distinct concern
        from iterating over the group row.

        :param spec: Aggregate specifier ending in ``:recordset``.
        :type spec: str
        :param value: Raw ids returned by the server, or ``None``.
        :type value: Any
        :param metadata: Field relation metadata keyed by field name.
        :type metadata: Dict[str, Any]
        :return: ``OdooRecordset`` bound to the related model and returned ids.
        :rtype: OdooRecordset
        """
        field_name = spec.split(":")[0]
        relation_model = (metadata.get(field_name) or {}).get(
            "relation", self._model_name
        )
        return OdooRecordset(self._env, relation_model, value or [])

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

    def with_company(self, company_id: int) -> OdooRecordset:
        """Return a new recordset bound to an environment restricted to one company.

        This method is necessary for multi-company workflows that need to switch
        ``allowed_company_ids`` in context without mutating the current recordset.

        :param company_id: Id of the company to set as the active company.
        :type company_id: int
        :return: Derived recordset bound to an environment with
            ``allowed_company_ids`` set to ``[company_id]``.
        :rtype: OdooRecordset
        """
        return self._derive(self._ids, env=self._env.with_company(company_id))

    def action_archive(self) -> bool:
        """Set ``active=False`` on all records in this recordset.

        This method is necessary for archive workflows that need to deactivate records
        without manually constructing the write payload.

        :return: ``True`` when the write succeeds.
        :rtype: bool
        """
        return self.write({"active": False})

    def action_unarchive(self) -> bool:
        """Set ``active=True`` on all records in this recordset.

        This method is necessary for unarchive workflows that need to reactivate
        records without manually constructing the write payload.

        :return: ``True`` when the write succeeds.
        :rtype: bool
        """
        return self.write({"active": True})

    # ------------------------------------------------------------------
    # In-memory functional operations
    # ------------------------------------------------------------------

    def _ensure_fields_cached(self, field_names: list[str]) -> None:
        """Fetch and cache all given fields for every id in this recordset.

        This helper is necessary so functional operations can batch-prime the field
        cache before iterating, avoiding per-record round-trips.

        :param field_names: Field names to ensure are present in the cache.
        :type field_names: list[str]
        :raises AttributeError: When a field name is not a known model field.
        :return: None.
        :rtype: None
        """
        if not self._ids or not field_names:
            return
        metadata = self._env.get_field_metadata(
            self._model_name,
            fields=field_names,
            attributes=["type", "relation"],
        )
        for field_name in field_names:
            if metadata.get(field_name) is None:
                raise AttributeError(
                    f"{type(self).__name__!s} object has no attribute {field_name!r}"
                )
        ids_to_fetch: set[int] = set()
        for field_name in field_names:
            missing = self._env.get_missing_field_ids(
                self._model_name, list(self._ids), field_name
            )
            ids_to_fetch.update(missing)
        if not ids_to_fetch:
            return
        rows = self._materialize_records(ids=list(ids_to_fetch), fields=field_names)
        for row in rows:
            if "id" not in row:
                continue
            record_id = row["id"]
            for field_name in field_names:
                if field_name not in row:
                    continue
                field_meta = metadata.get(field_name, {})
                adapted = self._adapt_field_access_value(row[field_name], field_meta)
                self._env.cache_record_field_values(
                    self._model_name, record_id, {field_name: adapted}
                )

    def filtered(
        self,
        func: Any,
    ) -> OdooRecordset:
        """Return a new recordset containing only records that satisfy ``func``.

        ``func`` may be:

        * A callable predicate — keep records where ``func(record)`` is truthy.
        * A dotted field-path string (e.g. ``'partner_id.is_company'``) — keep
          records where the terminal path value is truthy.
        * A domain list or ``DomainExpression`` — delegate to
          :meth:`filtered_domain`.

        :param func: Predicate, dotted field path, domain list, or
            ``DomainExpression``.
        :type func: Any
        :return: Filtered recordset sharing the original prefetch set.
        :rtype: OdooRecordset
        :raises TypeError: When ``func`` is not a supported type.
        """
        if not self._ids:
            return self._derive(())

        if isinstance(func, (list, DomainExpression)):
            return self.filtered_domain(func)

        if isinstance(func, str):
            parts = func.split(".")
            matching_ids = [r.id for r in self if _eval_dotted_path(r, parts)]
            return self._derive(matching_ids, prefetch_ids=self._prefetch_ids)

        if callable(func):
            matching_ids = [r.id for r in self if func(r)]
            return self._derive(matching_ids, prefetch_ids=self._prefetch_ids)

        raise TypeError(
            f"filtered() argument must be a callable, field path string, domain list,"
            f" or DomainExpression; got {type(func)!r}"
        )

    def filtered_domain(
        self,
        domain: Any,
    ) -> OdooRecordset:
        """Return a new recordset containing only records that match ``domain``.

        Evaluates the domain in-memory against cached field values; no additional
        server call is issued beyond the initial field fetch.

        :param domain: Domain list or ``DomainExpression`` to evaluate.
        :type domain: Any
        :return: Filtered recordset sharing the original prefetch set.
        :rtype: OdooRecordset
        :raises AttributeError: When a domain field is not a known model field.
        :raises NotImplementedError: When the domain uses ``child_of`` or
            ``parent_of``.
        """
        expr = DomainExpression.normalize(domain)
        if expr.is_empty():
            return self._derive(self._ids, prefetch_ids=self._prefetch_ids)

        field_names = list(expr.field_names())
        self._ensure_fields_cached(field_names)

        matching_ids: list[int] = []
        for record_id in self._ids:
            record_values: Dict[str, Any] = {}
            for field_name in field_names:
                found, value = self._env.get_cached_field_value(
                    self._model_name, record_id, field_name
                )
                if found:
                    record_values[field_name] = value
            if expr.matches(record_values):
                matching_ids.append(record_id)

        return self._derive(matching_ids, prefetch_ids=self._prefetch_ids)

    def mapped(
        self,
        func: Any,
    ) -> list[Any] | OdooRecordset:
        """Apply ``func`` to every record and return the collected results.

        ``func`` may be:

        * A callable — returns ``[func(record) for record in self]``.
        * A dotted field-path string (e.g. ``'partner_id.is_company'``) — for
          scalar terminal fields returns a list of values; for relational terminal
          fields returns a deduplicated ``OdooRecordset`` of the related model.
          Intermediate Many2one hops are followed automatically; x2many
          intermediate fields fan out across all related ids.

        :param func: Callable or dotted field path string.
        :type func: Any
        :return: List of values for scalar/callable results; ``OdooRecordset`` for
            relational terminal fields.
        :rtype: list[Any] | OdooRecordset
        :raises TypeError: When ``func`` is not callable and not a string.
        """
        if callable(func):
            return [func(r) for r in self]

        if not isinstance(func, str):
            raise TypeError(
                f"mapped() argument must be a callable or field path string;"
                f" got {type(func)!r}"
            )

        parts = func.split(".")
        return self._mapped_path(parts)

    def _mapped_path(self, parts: list[str]) -> list[Any] | OdooRecordset:
        """Traverse a field path list and collect terminal values.

        This helper is necessary so ``mapped`` can recurse cleanly through
        relational hops without duplicating traversal logic.

        :param parts: Remaining path segments to traverse.
        :type parts: list[str]
        :return: List for scalar terminals; ``OdooRecordset`` for relational
            terminals.
        :rtype: list[Any] | OdooRecordset
        """
        if not parts:
            return list(self)

        if not self._ids:
            return []

        field_name = parts[0]
        remaining = parts[1:]

        metadata = self._env.get_field_metadata(
            self._model_name,
            fields=[field_name],
            attributes=["type", "relation"],
        )
        field_meta = metadata.get(field_name)
        if field_meta is None:
            raise AttributeError(
                f"{type(self).__name__!s} object has no attribute {field_name!r}"
            )
        field_type = field_meta.get("type")
        is_relational = field_type in {"many2one", "one2many", "many2many"}
        relation_model = field_meta.get("relation") or self._model_name
        values = [getattr(r, field_name) for r in self]

        if remaining:
            return self._mapped_path_hop(values, relation_model, remaining, field_name)
        return _mapped_path_terminal(self._env, relation_model, is_relational, values)

    def _mapped_path_hop(
        self,
        values: list[Any],
        relation_model: str,
        remaining: list[str],
        field_name: str,
    ) -> list[Any] | OdooRecordset:
        """Continue a dotted-path traversal through a relational hop.

        This helper is necessary so ``_mapped_path`` can recurse into the next
        path segment without duplicating the id-dedup and merge logic.

        :param values: Field values returned for the current segment.
        :type values: list[Any]
        :param relation_model: Model name of the relation to merge into.
        :type relation_model: str
        :param remaining: Remaining path segments after the current hop.
        :type remaining: list[str]
        :param field_name: Current field name, used in the error message.
        :type field_name: str
        :raises ValueError: When the current field is not relational.
        :return: Terminal list or ``OdooRecordset``.
        :rtype: list[Any] | OdooRecordset
        """
        if not any(isinstance(v, OdooRecordset) for v in values):
            raise ValueError(
                f"Cannot traverse dotted path: field {field_name!r} is"
                f" not a relational field"
            )
        merged = OdooRecordset(self._env, relation_model, _dedup_relation_ids(values))
        return merged._mapped_path(remaining)

    def sorted(
        self,
        key: Any = None,
        reverse: bool = False,
    ) -> OdooRecordset:
        """Return a new recordset with records in a defined order.

        ``key`` may be:

        * A callable — records are sorted by ``key(record)``.
        * A comma-separated field spec string, e.g.
          ``'name DESC, amount_total ASC NULLS LAST'``.  Each spec supports an
          optional ``ASC`` / ``DESC`` qualifier and an optional
          ``NULLS FIRST`` / ``NULLS LAST`` qualifier.
        * ``None`` — records are sorted by id ascending (model default order is
          not available via XML-RPC and id order is used as a deterministic
          fallback; this is documented as a known limitation).

        :param key: Sort key: callable, field spec string, or ``None``.
        :type key: Any
        :param reverse: When True, reverse the final sort order.
        :type reverse: bool
        :return: Sorted recordset sharing the original prefetch set.
        :rtype: OdooRecordset
        """
        if not self._ids:
            return self._derive(())

        if key is None:
            sorted_ids = sorted(self._ids, reverse=reverse)
            return self._derive(sorted_ids, prefetch_ids=self._prefetch_ids)

        if callable(key):
            pairs = [(key(r), r.id) for r in self]
            pairs.sort(key=lambda x: x[0], reverse=reverse)
            sorted_ids = [p[1] for p in pairs]
            return self._derive(sorted_ids, prefetch_ids=self._prefetch_ids)

        if isinstance(key, str):
            specs = _parse_sort_specs(key)
            field_names = [spec[0] for spec in specs]
            self._ensure_fields_cached(field_names)

            record_ids = list(self._ids)
            for field_spec_name, direction, nulls_first in reversed(specs):
                spec_reverse = direction == "DESC"
                effective_nulls_first = (
                    nulls_first if not spec_reverse else not nulls_first
                )

                def make_key(
                    rid: int,
                    fn: str = field_spec_name,
                    nf: bool = effective_nulls_first,
                ) -> _SortKey:
                    found, v = self._env.get_cached_field_value(
                        self._model_name, rid, fn
                    )
                    from odoo_sdk.query.domain import _extract_comparison_value

                    extracted = _extract_comparison_value(v if found else None)
                    return _SortKey(extracted, nulls_first=nf)

                record_ids = sorted(record_ids, key=make_key, reverse=spec_reverse)

            if reverse:
                record_ids = list(reversed(record_ids))
            return self._derive(record_ids, prefetch_ids=self._prefetch_ids)

        raise TypeError(
            f"sorted() key must be a callable, field spec string, or None;"
            f" got {type(key)!r}"
        )

    def grouped(
        self,
        key: Any,
    ) -> dict[Any, OdooRecordset]:
        """Group records by a key and return a mapping of key to recordset.

        ``key`` may be:

        * A callable — groups by ``key(record)``.
        * A field name string — groups by the field value of each record.

        Returned ``OdooRecordset`` values all share the same prefetch set as the
        source recordset.

        :param key: Grouping callable or field name string.
        :type key: Any
        :return: Ordered dict mapping each distinct key value to a recordset.
        :rtype: dict[Any, OdooRecordset]
        :raises TypeError: When ``key`` is not callable and not a string.
        """
        if not isinstance(key, str) and not callable(key):
            raise TypeError(
                f"grouped() key must be a callable or field name string;"
                f" got {type(key)!r}"
            )

        groups: dict[Any, list[int]] = {}
        for record in self:
            if callable(key):
                k = key(record)
            else:
                k = getattr(record, key)
            k = _to_grouping_key(k)
            if k not in groups:
                groups[k] = []
            groups[k].append(record.id)

        return {
            k: self._derive(ids, prefetch_ids=self._prefetch_ids)
            for k, ids in groups.items()
        }


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


def _eval_dotted_path(record: OdooRecordset, parts: list[str]) -> Any:
    """Traverse a dotted field path on a singleton recordset and return the terminal value.

    Returns ``None`` when any intermediate value is an empty recordset or when
    attribute access fails.

    :param record: Singleton recordset to traverse from.
    :type record: OdooRecordset
    :param parts: Field path segments to follow in order.
    :type parts: list[str]
    :return: Terminal field value, or ``None`` if traversal cannot continue.
    :rtype: Any
    """
    val: Any = record
    for part in parts:
        if isinstance(val, OdooRecordset) and not val:
            return None
        try:
            val = getattr(val, part)
        except (AttributeError, ValueError):
            return None
    return val


def _dedup_relation_ids(values: list[Any]) -> list[int]:
    """Return a deduplicated ordered list of ids from a collection of recordset values.

    This helper is necessary so both ``_mapped_path`` and ``_mapped_path_hop``
    share one canonical way to merge relational field values into a flat id list.

    :param values: List of adapted field values, expected to be ``OdooRecordset``
        instances for relational fields.
    :type values: list[Any]
    :return: Deduplicated record ids in encounter order.
    :rtype: list[int]
    """
    seen: set[int] = set()
    result: list[int] = []
    for v in values:
        if isinstance(v, OdooRecordset):
            for rid in v.ids:
                if rid not in seen:
                    seen.add(rid)
                    result.append(rid)
    return result


def _mapped_path_terminal(
    env: Any,
    relation_model: str,
    is_relational: bool,
    values: list[Any],
) -> list[Any] | OdooRecordset:
    """Return the terminal result for a completed dotted-path traversal.

    This helper is necessary so ``_mapped_path`` can separate terminal-result
    logic from the traversal loop without duplicating the id-dedup code.

    :param env: Environment bound to the originating recordset.
    :type env: OdooEnv
    :param relation_model: Related model name to use for recordset construction.
    :type relation_model: str
    :param is_relational: True when the terminal field is many2one/x2many.
    :type is_relational: bool
    :param values: Collected field values for all records.
    :type values: list[Any]
    :return: Deduplicated ``OdooRecordset`` for relational terminals; plain list
        otherwise.
    :rtype: list[Any] | OdooRecordset
    """
    if is_relational:
        return OdooRecordset(env, relation_model, _dedup_relation_ids(values))
    return values


# ---------------------------------------------------------------------------
# Helpers for sorted() and grouped()
# ---------------------------------------------------------------------------

import re as _re

_SORT_SPEC_RE = _re.compile(
    r"(\w+)(?:\s+(ASC|DESC))?(?:\s+NULLS\s+(FIRST|LAST))?",
    _re.IGNORECASE,
)


def _parse_sort_specs(key: str) -> list[tuple[str, str, bool]]:
    """Parse a comma-separated sort spec string into field/direction/nulls triples.

    Each comma-separated segment may be ``field``, ``field ASC``, ``field DESC``,
    ``field ASC NULLS FIRST``, or ``field DESC NULLS LAST`` (case-insensitive).

    :param key: Sort spec string.
    :type key: str
    :raises ValueError: When a segment does not match the expected pattern.
    :return: List of ``(field_name, direction, nulls_first)`` triples.
    :rtype: list[tuple[str, str, bool]]
    """
    results: list[tuple[str, str, bool]] = []
    for segment in key.split(","):
        segment = segment.strip()
        m = _SORT_SPEC_RE.fullmatch(segment)
        if m is None:
            raise ValueError(f"Invalid sort spec segment: {segment!r}")
        field_name = m.group(1)
        direction = (m.group(2) or "ASC").upper()
        nulls_str = (m.group(3) or "LAST").upper()
        results.append((field_name, direction, nulls_str == "FIRST"))
    return results


class _SortKey:
    """Comparable sort key that handles ``None`` / ``False`` with configurable placement.

    This class is necessary because Python's ``sorted()`` cannot natively place
    ``None`` values first or last depending on a per-column directive; a custom
    comparison object provides the required flexibility without relying on
    sentinel numeric infinities that would break string comparisons.

    :param value: Field value to compare, or ``None`` / ``False`` for null records.
    :type value: Any
    :param nulls_first: When True, null values sort before non-null values.
    :type nulls_first: bool
    """

    __slots__ = ("is_null", "value", "nulls_first")

    def __init__(self, value: Any, *, nulls_first: bool) -> None:
        self.is_null: bool = value is None or value is False
        self.value = value
        self.nulls_first = nulls_first

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, _SortKey):
            return NotImplemented
        if self.is_null and other.is_null:
            return False
        if self.is_null:
            return self.nulls_first
        if other.is_null:
            return not self.nulls_first
        return self.value < other.value  # type: ignore[operator]

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, _SortKey):
            return NotImplemented
        return other.__lt__(self)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _SortKey):
            return NotImplemented
        if self.is_null and other.is_null:
            return True
        if self.is_null != other.is_null:
            return False
        return self.value == other.value  # type: ignore[operator]

    def __le__(self, other: object) -> bool:
        eq = self.__eq__(other)
        if eq is NotImplemented:
            return NotImplemented
        lt = self.__lt__(other)
        if lt is NotImplemented:
            return NotImplemented
        return lt or eq

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, _SortKey):
            return NotImplemented
        return other.__le__(self)  # type: ignore[return-value]


def _to_grouping_key(value: Any) -> Any:
    """Convert an adapted field value to a hashable grouping key.

    Relational field values that are duck-typed as recordsets (expose ``ids``)
    are converted to their id integer (for singleton many2one), a tuple of ids
    (for x2many), or ``False`` for empty recordsets.

    :param value: Adapted field value.
    :type value: Any
    :return: Hashable grouping key.
    :rtype: Any
    """
    if hasattr(value, "ids"):
        ids = tuple(value.ids)
        if not ids:
            return False
        if len(ids) == 1:
            return ids[0]
        return ids
    return value
