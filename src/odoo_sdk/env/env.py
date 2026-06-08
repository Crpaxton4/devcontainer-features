from __future__ import annotations

import threading
from copy import deepcopy
from typing import Any, Dict, Optional, Sequence, TYPE_CHECKING, Union

from .metadata_cache import MetadataCache
from odoo_sdk.transport.executor import OdooExecutor

if TYPE_CHECKING:
    from odoo_sdk.records.recordset import OdooRecordset


class OdooEnv:
    """Own shared executor, context, and metadata state for one Odoo runtime boundary.

    The environment is the internal root of the recordset-first architecture. It is
    necessary because context propagation, metadata caching, and model binding must
    live in one reusable object instead of being recomputed separately by model,
    query, and recordset compatibility layers.

    :param executor: Executor used to issue Odoo calls for this environment.
    :type executor: OdooExecutor
    :param context: Initial Odoo context values to copy into the environment,
        defaults to None.
    :type context: Optional[Dict[str, Any]]
    :param metadata_cache: Existing metadata cache to share with derived
        environments, defaults to None.
    :type metadata_cache: Optional[MetadataCache]
    """

    def __init__(
        self,
        executor: OdooExecutor,
        context: Optional[Dict[str, Any]] = None,
        metadata_cache: Optional[MetadataCache] = None,
    ):
        """Initialize an environment with executor, context, and cache ownership.

        This constructor is necessary because all recordset and compatibility flows
        need one place to anchor execution policy, cached metadata, and immutable-ish
        context derivation.

        :param executor: Executor used to issue Odoo calls for this environment.
        :type executor: OdooExecutor
        :param context: Initial Odoo context values to copy into the environment,
            defaults to None.
        :type context: Optional[Dict[str, Any]]
        :param metadata_cache: Existing metadata cache to share with derived
            environments, defaults to None.
        :type metadata_cache: Optional[MetadataCache]
        :return: None.
        :rtype: None
        """
        self._executor = executor
        self._context: Dict[str, Any] = (
            deepcopy(context) if context is not None else {}
        )
        self._metadata_cache = metadata_cache if metadata_cache is not None else MetadataCache()
        self._record_value_cache: dict[tuple[str, int], dict[str, Any]] = {}
        self._record_value_lock = threading.Lock()

    @property
    def executor(self) -> OdooExecutor:
        """Expose the executor bound to this environment.

        This property is necessary because recordsets and model proxies delegate
        actual transport work through the environment rather than storing their own
        independent executor state.

        :return: Executor that backs this environment.
        :rtype: OdooExecutor
        """
        return self._executor

    @property
    def context(self) -> Dict[str, Any]:
        """Return a defensive copy of the current Odoo context.

        Returning a copy is necessary so callers can inspect context safely without
        mutating the environment's shared runtime state.

        :return: Copied Odoo context mapping.
        :rtype: Dict[str, Any]
        """
        return deepcopy(self._context)

    @property
    def metadata_cache(self) -> MetadataCache:
        """Expose the shared metadata cache for this runtime boundary.

        This property is necessary so advanced internal flows can coordinate cache
        reuse and invalidation without bypassing the environment abstraction.

        :return: Metadata cache shared by related environments.
        :rtype: MetadataCache
        """
        return self._metadata_cache

    def with_context(self, context: Dict[str, Any]) -> "OdooEnv":
        """Create a derived environment with merged context values.

        This method is necessary because Odoo context is request-scoped, but callers
        need to fork that state without mutating the root environment shared by other
        model, query, or recordset objects.

        :param context: Additional context keys to merge into the current state.
        :type context: Dict[str, Any]
        :return: A new environment sharing the same executor and metadata cache.
        :rtype: OdooEnv
        """
        merged = deepcopy({**self._context, **context})
        return OdooEnv(
            self._executor,
            merged,
            metadata_cache=self._metadata_cache,
        )

    def clear_metadata_cache(self, model_name: Optional[str] = None) -> None:
        """Clear cached metadata for this runtime boundary.

        This method is necessary when callers know `fields_get` results may have
        changed and must invalidate cached metadata either globally or for one model.

        :param model_name: Model whose cached metadata should be removed, or None to
            clear all cached metadata, defaults to None.
        :type model_name: Optional[str]
        :return: None.
        :rtype: None
        """
        self._metadata_cache.clear(model_name=model_name)

    def get_missing_field_ids(
        self,
        model_name: str,
        record_ids: Sequence[int],
        field_name: str,
    ) -> list[int]:
        """Return ids that do not yet have a cached value for one field.

        :param model_name: Model whose cached values are being queried.
        :type model_name: str
        :param record_ids: Candidate record ids to inspect.
        :type record_ids: Sequence[int]
        :param field_name: Field whose cached presence should be checked.
        :type field_name: str
        :return: Ordered ids missing the requested field value in cache.
        :rtype: list[int]
        """
        with self._record_value_lock:
            return [
                record_id
                for record_id in record_ids
                if field_name
                not in self._record_value_cache.get((model_name, record_id), {})
            ]

    def get_cached_field_value(
        self,
        model_name: str,
        record_id: int,
        field_name: str,
    ) -> tuple[bool, Any]:
        """Return one cached field value when present.

        :param model_name: Model whose cached values are being queried.
        :type model_name: str
        :param record_id: Record id whose cached field value is requested.
        :type record_id: int
        :param field_name: Field name to retrieve from cache.
        :type field_name: str
        :return: Tuple of cache-hit flag and cached value.
        :rtype: tuple[bool, Any]
        """
        with self._record_value_lock:
            values = self._record_value_cache.get((model_name, record_id), {})
            if field_name not in values:
                return False, None
            return True, values[field_name]

    def cache_record_field_values(
        self,
        model_name: str,
        record_id: int,
        values: Dict[str, Any],
    ) -> None:
        """Store cached field values for one record.

        :param model_name: Model whose cached values are being updated.
        :type model_name: str
        :param record_id: Record id whose values are being updated.
        :type record_id: int
        :param values: Field values to cache for the record.
        :type values: Dict[str, Any]
        :return: None.
        :rtype: None
        """
        if not values:
            return

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
        """Fetch and cache raw `fields_get` metadata for one model.

        This method is necessary because metadata-driven behaviors such as field
        adaptation and x2many normalization require a single shared loading and cache
        boundary instead of ad hoc `fields_get` calls scattered across the codebase.

        :param model_name: Name of the Odoo model whose metadata is requested.
        :type model_name: str
        :param fields: Optional subset of field names to request, defaults to None.
        :type fields: Optional[Sequence[str]]
        :param attributes: Optional subset of metadata attributes to request,
            defaults to None.
        :type attributes: Optional[Sequence[str]]
        :param refresh: When True, bypass the cached entry and load fresh metadata,
            defaults to False.
        :type refresh: bool
        :return: Raw metadata keyed by field name.
        :rtype: Dict[str, Any]
        """
        context = self.context
        kwargs = self._build_fields_get_kwargs(fields, attributes, context)
        return self._metadata_cache.get_or_load(
            model_name,
            fields=fields,
            attributes=attributes,
            context=context,
            refresh=refresh,
            loader=lambda: self._executor.execute(model_name, "fields_get", **kwargs),
        )

    def _build_fields_get_kwargs(
        self,
        fields: Optional[Sequence[str]],
        attributes: Optional[Sequence[str]],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build the ``fields_get`` keyword arguments from optional parameters.

        This helper is necessary because ``get_field_metadata`` should read as a
        single-purpose orchestrator rather than embedding conditional kwargs assembly
        inline.

        :param fields: Optional subset of field names to request.
        :type fields: Optional[Sequence[str]]
        :param attributes: Optional subset of metadata attributes to request.
        :type attributes: Optional[Sequence[str]]
        :param context: Current Odoo context to include when non-empty.
        :type context: Dict[str, Any]
        :return: Keyword arguments for the ``fields_get`` executor call.
        :rtype: Dict[str, Any]
        """
        kwargs: Dict[str, Any] = {}
        if fields is not None:
            kwargs["allfields"] = list(fields)
        if attributes is not None:
            kwargs["attributes"] = list(attributes)
        if context:
            kwargs["context"] = context
        return kwargs

    def __getitem__(self, model_name: str) -> OdooRecordset:
        """Return an empty model-bound recordset anchored to this environment.

        This convenience is necessary because env-first flows and public callers both
        use Odoo-style `env["model"]` lookup to start from a model-bound recordset.

        :param model_name: Name of the Odoo model to bind.
        :type model_name: str
        :return: Empty recordset bound to this environment and model.
        :rtype: OdooRecordset
        """
        return self.recordset(model_name)

    def recordset(
        self,
        model_name: str,
        ids: Union[int, Sequence[int]] = (),
    ) -> OdooRecordset:
        """Return a recordset bound to this environment and model.

        This factory is necessary because recordsets are the architectural center of
        the SDK, and callers need one canonical way to bind ids, model identity, and
        shared environment state together.

        :param model_name: Name of the Odoo model the recordset targets.
        :type model_name: str
        :param ids: Record identifier or ordered identifiers to bind, defaults to an
            empty recordset.
        :type ids: Union[int, Sequence[int]]
        :return: Recordset bound to the requested model and ids.
        :rtype: OdooRecordset
        """
        from odoo_sdk.records.recordset import OdooRecordset

        return OdooRecordset(self, model_name, ids)
