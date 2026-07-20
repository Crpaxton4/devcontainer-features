from __future__ import annotations

import re as _re
import threading
from collections.abc import Mapping
from collections.abc import Sequence as SequenceABC
from copy import deepcopy
from typing import Any, Dict, Iterator, Optional, Sequence, TypeAlias, Union

from odoo_sdk._utils import _dedup_field_names
from odoo_sdk.env.metadata_cache import MetadataCache
from odoo_sdk.fields import adapt_field_value, adapt_record_values
from odoo_sdk.fields.commands import X2ManyCommand, normalize_x2many_commands
from odoo_sdk.fields.values import RelationCollection, RelationValue
from odoo_sdk.query import extract_comparison_value
from odoo_sdk.query.domain import DomainExpression, DomainInput
from odoo_sdk.transport.executor import OdooExecutor, guarded_execute

Record: TypeAlias = Mapping[str, Any]

_SORT_SPEC_RE = _re.compile(
    r"(\w+)(?:\s+(ASC|DESC))?(?:\s+NULLS\s+(FIRST|LAST))?",
    _re.IGNORECASE,
)


class OdooRecordset:
    """Represent immutable record identity with owned runtime state and model binding.

    The recordset is the architectural center of the SDK: it carries ids, model
    identity, executor, context, metadata-driven reads, and x2many write normalization
    through one unified abstraction. A shared executor, metadata cache, and locks are
    threaded across all recordsets for a given runtime boundary; the record-value cache
    is optional and lazily created when not supplied.
    """

    def __init__(
        self,
        executor: OdooExecutor,
        model_name: str,
        ids: Union[int, Sequence[int]] = (),
        context: Optional[Dict[str, Any]] = None,
        metadata_cache: Optional[MetadataCache] = None,
        record_value_cache: Optional[dict[tuple[str, int], Dict[str, Any]]] = None,
        record_value_lock: Optional[threading.Lock] = None,
        prefetch_ids: Union[int, Sequence[int], None] = None,
    ):
        self._executor = executor
        self._model_name = model_name
        self._ids = self._normalize_ids(ids)
        self._context: Dict[str, Any] = deepcopy(context) if context is not None else {}
        self._metadata_cache = (
            metadata_cache if metadata_cache is not None else MetadataCache()
        )
        self._record_value_cache = (
            record_value_cache if record_value_cache is not None else {}
        )
        self._record_value_lock = (
            record_value_lock if record_value_lock is not None else threading.Lock()
        )
        self._prefetch_ids = self._normalize_ids(
            self._ids if prefetch_ids is None else prefetch_ids
        )

    @staticmethod
    def _normalize_ids(ids: Union[int, Sequence[int]]) -> tuple[int, ...]:
        """Normalize record ids into an immutable tuple."""
        if isinstance(ids, int):
            return (ids,)
        return tuple(ids)

    @property
    def executor(self) -> OdooExecutor:
        """Expose the executor bound to this recordset."""
        return self._executor

    @property
    def context(self) -> Dict[str, Any]:
        """Return a defensive copy of the current Odoo context."""
        return deepcopy(self._context)

    @property
    def metadata_cache(self) -> MetadataCache:
        """Expose the shared metadata cache for this runtime boundary."""
        return self._metadata_cache

    @property
    def model_name(self) -> str:
        """Expose the model name carried by this recordset."""
        return self._model_name

    @property
    def ids(self) -> tuple[int, ...]:
        """Expose the ordered record ids for this recordset."""
        return self._ids

    @property
    def id(self) -> int:
        """Return the singleton identifier; errors on empty or multi-record sets."""
        return self.ensure_one()._ids[0]

    def ensure_one(self) -> OdooRecordset:
        """Assert that the recordset is a singleton and return it.

        :raises ValueError: When the recordset is empty or contains multiple ids.
        """
        if len(self._ids) != 1:
            raise ValueError(f"Expected singleton: {self}")
        return self

    def with_context(self, context: Dict[str, Any]) -> "OdooRecordset":
        """Create a derived recordset with merged context values."""
        return self._build_recordset(
            ids=self._ids,
            prefetch_ids=self._prefetch_ids,
            context={**self._context, **context},
        )

    def with_company(self, company_id: int) -> "OdooRecordset":
        """Create a derived recordset with ``allowed_company_ids`` set to ``[company_id]``."""
        return self.with_context({"allowed_company_ids": [company_id]})

    def _get_missing_field_ids(
        self,
        record_ids: Sequence[int],
        field_name: str,
    ) -> list[int]:
        """Return ids that do not yet have a cached value for one field."""
        with self._record_value_lock:
            return [
                record_id
                for record_id in record_ids
                if field_name
                not in self._record_value_cache.get(
                    (self._model_name, record_id), {}
                )
            ]

    def _get_cached_field_value(
        self,
        record_id: int,
        field_name: str,
    ) -> tuple[bool, Any]:
        """Return ``(hit, value)`` for one cached field value."""
        with self._record_value_lock:
            values = self._record_value_cache.get((self._model_name, record_id), {})
            if field_name not in values:
                return False, None
            return True, values[field_name]

    def cache_record_field_values(
        self,
        model_name: str,
        record_id: int,
        values: Dict[str, Any],
    ) -> None:
        """Store cached field values for one record of *model_name*."""
        with self._record_value_lock:
            cached = self._record_value_cache.setdefault((model_name, record_id), {})
            cached.update(values)

    def get_field_metadata(
        self,
        model_name: str,
        fields: Optional[Sequence[str]] = None,
        attributes: Optional[Sequence[str]] = None,
        *,
        refresh: bool = False,
    ) -> Dict[str, Any]:
        """Fetch and cache raw ``fields_get`` metadata for one model.

        This is the single shared loading/caching boundary for metadata-driven
        behaviors (field adaptation, x2many normalization).
        """
        context = self.context
        kwargs: Dict[str, Any] = {}
        if fields is not None:
            kwargs["allfields"] = list(fields)
        if attributes is not None:
            kwargs["attributes"] = list(attributes)
        if context:
            kwargs["context"] = context
        return self._metadata_cache.get_or_load(
            model_name,
            fields=fields,
            attributes=attributes,
            context=context,
            refresh=refresh,
            loader=lambda: self._executor.execute(model_name, "fields_get", **kwargs),
        )

    def __getitem__(self, model_name: str) -> "OdooRecordset":
        """Return an empty model-bound recordset (Odoo-style ``recordset["model"]``)."""
        return self.recordset(model_name)

    def recordset(
        self,
        model_name: str,
        ids: Union[int, Sequence[int]] = (),
    ) -> OdooRecordset:
        """Return a recordset bound to this runtime and the given model and ids."""
        return self._build_recordset(model_name=model_name, ids=ids)

    def _derive(
        self,
        ids: Union[int, Sequence[int]] = (),
        prefetch_ids: Union[int, Sequence[int], None] = None,
    ) -> OdooRecordset:
        """Create a same-model recordset with optionally new ids, sharing runtime.

        When *prefetch_ids* is None the current prefetch set is preserved.
        """
        return self._build_recordset(
            ids=ids,
            prefetch_ids=(self._prefetch_ids if prefetch_ids is None else prefetch_ids),
        )

    def _build_recordset(
        self,
        model_name: Optional[str] = None,
        ids: Union[int, Sequence[int]] = (),
        prefetch_ids: Union[int, Sequence[int], None] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> OdooRecordset:
        """Construct a recordset sharing this runtime.

        Defaults to the current model (when *model_name* is None) and to a copy of the
        current context (when *context* is None). This is the single place the shared
        executor, metadata cache, record-value cache, and lock wiring is defined, reused
        by :meth:`recordset`, :meth:`_derive`, :meth:`with_context`, and the relational
        traversal helpers.
        """
        return OdooRecordset(
            executor=self._executor,
            model_name=self._model_name if model_name is None else model_name,
            ids=ids,
            context=deepcopy(self._context if context is None else context),
            metadata_cache=self._metadata_cache,
            record_value_cache=self._record_value_cache,
            record_value_lock=self._record_value_lock,
            prefetch_ids=prefetch_ids,
        )

    def __bool__(self) -> bool:
        """Return whether the recordset contains at least one bound id."""
        return bool(self._ids)

    def __len__(self) -> int:
        """Return the number of bound ids in this recordset."""
        return len(self._ids)

    def __iter__(self) -> Iterator[OdooRecordset]:
        """Iterate over this recordset as singleton recordsets in order."""
        for record_id in self._ids:
            yield self._derive((record_id,))

    def __getattr__(self, name: str) -> Any:
        """Resolve singleton field access using metadata and cached values.

        :raises AttributeError: When the name does not describe a model field.
        """
        metadata = self.get_field_metadata(
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
        """Return a compact debug representation (model name and ids)."""
        return f"{self._model_name}{self._ids!r}"

    # ------------------------------------------------------------------
    # Set algebra operators
    # ------------------------------------------------------------------

    def _check_same_model(self, other: OdooRecordset) -> None:
        """Raise ValueError when *other* belongs to a different model.

        :raises ValueError: When model names differ.
        """
        if self._model_name != other._model_name:
            raise ValueError(
                f"Set operations require the same model: "
                f"{self._model_name!r} != {other._model_name!r}"
            )

    def __or__(self, other: OdooRecordset) -> OdooRecordset:
        """Return the union of two same-model recordsets.

        Preserves the order of *self*, then appends ids from *other* not already
        present. Deduplicates by id.

        :raises ValueError: When operands are from different models.
        """
        self._check_same_model(other)
        seen = set(self._ids)
        extra = tuple(i for i in other._ids if i not in seen)
        return self._derive(self._ids + extra)

    def __and__(self, other: OdooRecordset) -> OdooRecordset:
        """Return the intersection of two same-model recordsets (order of *self*).

        :raises ValueError: When operands are from different models.
        """
        self._check_same_model(other)
        other_set = set(other._ids)
        return self._derive(tuple(i for i in self._ids if i in other_set))

    def __sub__(self, other: OdooRecordset) -> OdooRecordset:
        """Return the difference of two same-model recordsets (order of *self*).

        :raises ValueError: When operands are from different models.
        """
        self._check_same_model(other)
        other_set = set(other._ids)
        return self._derive(tuple(i for i in self._ids if i not in other_set))

    def __contains__(self, record: object) -> bool:
        """Test whether *record* (a singleton recordset) is present in this recordset.

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
        """Return ``True`` if *self* is a subset of *other*.

        :raises ValueError: When operands are from different models.
        """
        self._check_same_model(other)
        return set(self._ids) <= set(other._ids)

    def __lt__(self, other: OdooRecordset) -> bool:
        """Return ``True`` if *self* is a strict subset of *other*.

        :raises ValueError: When operands are from different models.
        """
        self._check_same_model(other)
        return set(self._ids) < set(other._ids)

    def __ge__(self, other: OdooRecordset) -> bool:
        """Return ``True`` if *self* is a superset of *other*.

        :raises ValueError: When operands are from different models.
        """
        self._check_same_model(other)
        return set(self._ids) >= set(other._ids)

    def __gt__(self, other: OdooRecordset) -> bool:
        """Return ``True`` if *self* is a strict superset of *other*.

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

        :raises AttributeError: When the field cannot be resolved after fetching.
        """
        record_id = self.id
        found, value = self._get_cached_field_value(record_id, field_name)
        if found:
            return value

        ids_to_fetch = self._get_missing_field_ids(self._prefetch_ids, field_name)
        if ids_to_fetch:
            self._populate_field_cache(field_name, field_metadata, ids_to_fetch)

        found, value = self._get_cached_field_value(record_id, field_name)
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
        """Fetch field values from Odoo and store them in the environment cache."""
        rows = self._materialize_records(ids=ids_to_fetch, fields=[field_name])
        for row in rows:
            adapted_value = self._adapt_field_access_value(
                row.get(field_name),
                field_metadata,
            )
            self.cache_record_field_values(
                self._model_name,
                row["id"],
                {field_name: adapted_value},
            )

    def _adapt_field_access_value(
        self,
        value: Any,
        field_metadata: Dict[str, Any],
    ) -> Any:
        """Adapt one field value for singleton dot-access semantics."""
        field_type = field_metadata.get("type")
        relation_model = field_metadata.get("relation")

        if field_type == "many2one" and relation_model:
            relation = adapt_field_value(value, field_metadata)
            if relation is None:
                return self.recordset(relation_model)
            if isinstance(relation, RelationValue):
                related = self.recordset(relation.model_name, relation.id)
                if relation.label is not None:
                    self.cache_record_field_values(
                        relation.model_name,
                        relation.id,
                        {"display_name": relation.label},
                    )
                return related
            return value

        if field_type in {"one2many", "many2many"} and relation_model:
            relation = adapt_field_value(value, field_metadata)
            if isinstance(relation, RelationCollection):
                return self.recordset(relation.model_name, relation.ids)
            return self.recordset(relation_model)

        return adapt_field_value(value, field_metadata)

    def _execute(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Execute one method on the bound model through the shared guarded seam.

        Routes through :func:`guarded_execute` — the single chokepoint applying the
        cross-cutting ``forbid_unlink`` guard — so a recordset-originated ``unlink`` is
        blocked identically to a client-originated one.
        """
        return guarded_execute(self._executor, self._model_name, method, *args, **kwargs)

    def _context_kwargs(self) -> Dict[str, Any]:
        """Build RPC keyword arguments for the current context, or an empty mapping."""
        context = self.context
        if not context:
            return {}
        return {"context": context}

    def _normalize_write_values(self, values: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize write values, converting x2many helper inputs into tuple commands."""
        normalized = dict(values)
        fields_to_check = [
            field_name
            for field_name, value in normalized.items()
            if _needs_write_field_metadata(value)
        ]
        if not fields_to_check:
            return normalized

        metadata = self.get_field_metadata(
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
        """Build search keyword arguments from pagination and ordering options."""
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
        """Search for matching records and return their ids."""
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
        """Run raw ``search_read`` and materialize record mappings."""
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
        """Materialize records by direct ids (``read``) or a search-derived ``search_read``."""
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
        """Execute a ``read`` call for the given ids."""
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
        """Execute a ``search_read`` call for the given domain."""
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
        """Apply shared field adaptation to materialized records using cached metadata."""
        metadata_fields = self._metadata_fields(records, fields)
        if not metadata_fields:
            return [dict(record) for record in records]

        metadata = self.get_field_metadata(
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

        Uses explicit *fields* when given; otherwise the union of record keys. The
        synthetic ``id`` field is deduplicated along with the rest.
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
        """Run adapted ``search_read`` and materialize semantic record values."""
        return self._materialize_records(
            domain=domain,
            fields=fields,
            limit=limit,
            offset=offset,
            order=order,
            adapt=True,
        )

    def search_count(self, domain: DomainInput = None) -> int:
        """Return the number of records matching a domain."""
        serialized_domain = DomainExpression.normalize(domain).serialize()
        return self._execute(
            "search_count",
            serialized_domain,
            **self._context_kwargs(),
        )

    def create(self, values: Dict[str, Any]) -> int:
        """Create one record on the bound model and return its id."""
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
        """Return cached model metadata for the bound model."""
        return self.get_field_metadata(
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
        """Search for matching records and update them."""
        return self.search(
            domain,
            limit=limit,
            offset=offset,
            order=order,
        ).write(values)

    def read(self, fields: Optional[list[str]] = None) -> list[Record]:
        """Read the current ids using raw semantics."""
        return self._materialize_records(
            ids=self._ids,
            fields=fields,
        )

    def read_adapted(self, fields: Optional[list[str]] = None) -> list[Record]:
        """Read the current ids using field adaptation."""
        return self._materialize_records(
            ids=self._ids,
            fields=fields,
            adapt=True,
        )

    def write(self, values: Dict[str, Any]) -> bool:
        """Write values to the current ids."""
        normalized_values = self._normalize_write_values(values)
        return self._execute(
            "write",
            list(self._ids),
            normalized_values,
            **self._context_kwargs(),
        )

    def name_create(self, name: str) -> OdooRecordset:
        """Create a record from a display name and return it as a singleton recordset."""
        result = self._execute("name_create", name, **self._context_kwargs())
        return self._derive(result[0])

    def name_search(
        self,
        name: str = "",
        domain: DomainInput = None,
        operator: str = "ilike",
        limit: int = 100,
    ) -> list[tuple[int, str]]:
        """Search records by display name and return ``(id, display_name)`` pairs."""
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

        Only fields for which the server has a default are included.
        """
        return self._execute("default_get", fields, **self._context_kwargs())

    def copy(self, default: dict | None = None) -> OdooRecordset:
        """Duplicate the singleton record and return the copy as a new recordset.

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

        Each dict has keys ``id``, ``create_uid``, ``create_date``, ``write_uid``,
        ``write_date``, ``xmlid``, ``xmlids``, ``noupdate``; one per record.
        """
        return self._execute(
            "get_metadata",
            list(self._ids),
            **self._context_kwargs(),
        )

    def exists(self) -> OdooRecordset:
        """Return a new recordset containing only ids that still exist (order preserved)."""
        if not self._ids:
            return self._derive()

        existing_ids = set(self.search([("id", "in", self._ids)]).ids)
        surviving_ids = [
            record_id for record_id in self._ids if record_id in existing_ids
        ]
        return self._derive(surviving_ids)

    def browse(self, ids: Union[int, Sequence[int]]) -> OdooRecordset:
        """Return a same-model recordset for the provided ids without I/O."""
        return self._derive(ids, prefetch_ids=ids)

    def search(
        self,
        domain: DomainInput = None,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[str] = None,
    ) -> OdooRecordset:
        """Search the bound model and return a new recordset of matching ids."""
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

        The implementation calls the public ``read_group`` XML-RPC method (not the
        internal ``_read_group`` ORM method, which is not accessible over XML-RPC).
        Response dicts from ``read_group`` use base field names as keys (e.g.
        ``amount_total`` for an ``amount_total:sum`` aggregate), so the response is
        mapped back to the specifier-ordered tuple shape before returning.

        ``__count`` is a special aggregate that ``read_group`` always includes in
        its response dict; it does not need to be passed to the server as a field.

        :raises NotImplementedError: When a non-empty ``having`` domain is provided
            (unsupported by the XML-RPC ``read_group`` method).
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
        """Convert raw ``read_group`` row dicts into specifier-ordered tuples.

        ``:recordset`` aggregates are wrapped into ``OdooRecordset`` values using the
        related model resolved from field metadata; all other specifiers map to the
        server value keyed by the specifier's base field name.
        """
        if not rows:
            return []

        all_specs = list(groupby) + list(aggregates)
        base_field = {spec: spec.split(":")[0] for spec in all_specs}
        recordset_specs = {spec for spec in aggregates if spec.endswith(":recordset")}

        metadata: Dict[str, Any] = {}
        if recordset_specs:
            metadata = self.get_field_metadata(
                self._model_name,
                fields=[base_field[spec] for spec in recordset_specs],
                attributes=["relation"],
            )

        return [
            tuple(
                self._resolve_recordset_value(
                    base_field[spec], row[base_field[spec]], metadata
                )
                if spec in recordset_specs
                else row[base_field[spec]]
                for spec in all_specs
            )
            for row in rows
        ]

    def _resolve_recordset_value(
        self,
        field_name: str,
        value: Any,
        metadata: Dict[str, Any],
    ) -> OdooRecordset:
        """Wrap raw ids for one ``:recordset`` aggregate into an ``OdooRecordset``."""
        relation_model = (metadata.get(field_name) or {}).get(
            "relation", self._model_name
        )
        return self._build_recordset(model_name=relation_model, ids=value or [])

    def action_archive(self) -> bool:
        """Set ``active=False`` on all records in this recordset."""
        return self.write({"active": False})

    def action_unarchive(self) -> bool:
        """Set ``active=True`` on all records in this recordset."""
        return self.write({"active": True})

    # ------------------------------------------------------------------
    # In-memory functional operations
    # ------------------------------------------------------------------

    def _ensure_fields_cached(self, field_names: list[str]) -> None:
        """Fetch and cache all given fields for every id in this recordset.

        :raises AttributeError: When a field name is not a known model field.
        """
        if not self._ids or not field_names:
            return
        metadata = self.get_field_metadata(
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
            missing = self._get_missing_field_ids(list(self._ids), field_name)
            ids_to_fetch.update(missing)
        if not ids_to_fetch:
            return
        rows = self._materialize_records(ids=list(ids_to_fetch), fields=field_names)
        for row in rows:
            record_id = row["id"]
            for field_name in field_names:
                if field_name not in row:
                    continue
                field_meta = metadata.get(field_name, {})
                adapted = self._adapt_field_access_value(row[field_name], field_meta)
                self.cache_record_field_values(
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

        :raises TypeError: When ``func`` is not a supported type.
        """
        if not self._ids:
            return self._derive(())

        if isinstance(func, (list, DomainExpression)):
            return self.filtered_domain(func)

        if isinstance(func, str):
            parts = func.split(".")

            def pred(record: OdooRecordset) -> Any:
                return _eval_dotted_path(record, parts)

        elif callable(func):
            pred = func
        else:
            raise TypeError(
                f"filtered() argument must be a callable, field path string, domain"
                f" list, or DomainExpression; got {type(func)!r}"
            )

        matching_ids = [r.id for r in self if pred(r)]
        return self._derive(matching_ids)

    def filtered_domain(
        self,
        domain: Any,
    ) -> OdooRecordset:
        """Return a new recordset containing only records that match ``domain``.

        Evaluates the domain in-memory against cached field values; no additional
        server call is issued beyond the initial field fetch.

        :raises AttributeError: When a domain field is not a known model field.
        :raises NotImplementedError: When the domain uses ``child_of`` or ``parent_of``.
        """
        expr = DomainExpression.normalize(domain)
        if expr.is_empty():
            return self._derive(self._ids)

        field_names = list(expr.field_names())
        self._ensure_fields_cached(field_names)

        matching_ids: list[int] = []
        for record_id in self._ids:
            record_values: Dict[str, Any] = {}
            for field_name in field_names:
                found, value = self._get_cached_field_value(record_id, field_name)
                if found:
                    record_values[field_name] = value
            if expr.matches(record_values):
                matching_ids.append(record_id)

        return self._derive(matching_ids)

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

        Returns a list for scalar terminals and a deduplicated ``OdooRecordset`` for
        relational terminals.
        """
        if not parts:
            return list(self)

        if not self._ids:
            return []

        field_name = parts[0]
        remaining = parts[1:]

        metadata = self.get_field_metadata(
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
        if is_relational:
            return self._build_recordset(
                model_name=relation_model, ids=_dedup_relation_ids(values)
            )
        return values

    def _mapped_path_hop(
        self,
        values: list[Any],
        relation_model: str,
        remaining: list[str],
        field_name: str,
    ) -> list[Any] | OdooRecordset:
        """Continue a dotted-path traversal through a relational hop.

        :raises ValueError: When the current field is not relational.
        """
        if not any(isinstance(v, OdooRecordset) for v in values):
            raise ValueError(
                f"Cannot traverse dotted path: field {field_name!r} is"
                f" not a relational field"
            )
        merged = self._build_recordset(
            model_name=relation_model, ids=_dedup_relation_ids(values)
        )
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

        :raises TypeError: When ``key`` is not a supported type.
        """
        if not self._ids:
            return self._derive(())

        if key is None:
            sorted_ids = sorted(self._ids, reverse=reverse)
            return self._derive(sorted_ids)

        if callable(key):
            pairs = [(key(r), r.id) for r in self]
            pairs.sort(key=lambda x: x[0], reverse=reverse)
            sorted_ids = [p[1] for p in pairs]
            return self._derive(sorted_ids)

        if isinstance(key, str):
            return self._sorted_by_field_specs(key, reverse)

        raise TypeError(
            f"sorted() key must be a callable, field spec string, or None;"
            f" got {type(key)!r}"
        )

    def _sorted_by_field_specs(self, key: str, reverse: bool) -> OdooRecordset:
        """Sort the recordset by a comma-separated field spec string.

        Each spec is applied as a stable pass in reverse order so the leftmost spec
        becomes the primary sort key.
        """
        specs = _parse_sort_specs(key)
        field_names = [spec[0] for spec in specs]
        self._ensure_fields_cached(field_names)

        record_ids = list(self._ids)
        for field_spec_name, direction, nulls_first in reversed(specs):
            spec_reverse = direction == "DESC"
            effective_nulls_first = (
                nulls_first if not spec_reverse else not nulls_first
            )
            key_fn = self._make_field_sort_key(field_spec_name, effective_nulls_first)
            record_ids = sorted(record_ids, key=key_fn, reverse=spec_reverse)

        if reverse:
            record_ids = list(reversed(record_ids))
        return self._derive(record_ids)

    def _make_field_sort_key(
        self,
        field_name: str,
        nulls_first: bool,
    ) -> Any:
        """Build a ``sorted`` key callable mapping a record id to a :class:`_SortKey`."""

        def make_key(rid: int) -> _SortKey:
            found, v = self._get_cached_field_value(rid, field_name)
            extracted = extract_comparison_value(v if found else None)
            return _SortKey(extracted, nulls_first=nulls_first)

        return make_key

    def grouped(
        self,
        key: Any,
    ) -> dict[Any, OdooRecordset]:
        """Group records by a key and return a mapping of key to recordset.

        ``key`` may be a callable (grouped by ``key(record)``) or a field name string
        (grouped by the field value). Returned recordsets share the source prefetch set.

        :raises TypeError: When ``key`` is not callable and not a string.
        """
        if not isinstance(key, str) and not callable(key):
            raise TypeError(
                f"grouped() key must be a callable or field name string;"
                f" got {type(key)!r}"
            )

        key_fn = key if callable(key) else (lambda record: getattr(record, key))

        groups: dict[Any, list[int]] = {}
        for record in self:
            k = _to_grouping_key(key_fn(record))
            groups.setdefault(k, []).append(record.id)

        return {k: self._derive(ids) for k, ids in groups.items()}


def _needs_write_field_metadata(value: Any) -> bool:
    """Return whether a write value requires field-metadata inspection (x2many-like)."""
    if isinstance(value, X2ManyCommand):
        return True
    return isinstance(value, SequenceABC) and not isinstance(
        value,
        (str, bytes, bytearray),
    )


def _eval_dotted_path(record: OdooRecordset, parts: list[str]) -> Any:
    """Traverse a dotted field path on a singleton recordset and return the terminal.

    Returns ``None`` when any intermediate value is an empty recordset or when
    attribute access fails.
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
    """Return deduplicated ordered ids from a collection of recordset values."""
    seen: set[int] = set()
    result: list[int] = []
    for v in values:
        if isinstance(v, OdooRecordset):
            for rid in v.ids:
                if rid not in seen:
                    seen.add(rid)
                    result.append(rid)
    return result


# ---------------------------------------------------------------------------
# Helpers for sorted() and grouped()
# ---------------------------------------------------------------------------


def _parse_sort_specs(key: str) -> list[tuple[str, str, bool]]:
    """Parse a comma-separated sort spec string into field/direction/nulls triples.

    Each segment may be ``field``, ``field ASC``, ``field DESC``,
    ``field ASC NULLS FIRST``, or ``field DESC NULLS LAST`` (case-insensitive).

    :raises ValueError: When a segment does not match the expected pattern.
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
    """Comparable sort key placing ``None`` / ``False`` first or last per directive.

    Python's ``sorted()`` cannot natively place null values first or last per column;
    this comparison object provides that without sentinel numeric infinities that would
    break string comparisons. ``sorted()`` only needs ``__lt__``.
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

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _SortKey):
            return NotImplemented
        if self.is_null and other.is_null:
            return True
        if self.is_null != other.is_null:
            return False
        return self.value == other.value  # type: ignore[operator]


def _to_grouping_key(value: Any) -> Any:
    """Convert an adapted field value to a hashable grouping key.

    Relational values (duck-typed as recordsets exposing ``ids``) become the id
    integer for a singleton many2one, a tuple of ids for x2many, or ``False`` when
    empty.
    """
    if hasattr(value, "ids"):
        ids = tuple(value.ids)
        if not ids:
            return False
        if len(ids) == 1:
            return ids[0]
        return ids
    return value
