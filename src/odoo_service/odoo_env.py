from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional, Sequence, TYPE_CHECKING, Union

from .metadata_cache import MetadataCache
from .odoo_executor import OdooExecutor

if TYPE_CHECKING:
    from .odoo_model import OdooModel
    from .odoo_recordset import OdooRecordset


class OdooEnv:
    """Thin environment root for executor access, metadata, and context state."""

    def __init__(
        self,
        executor: OdooExecutor,
        context: Optional[Dict[str, Any]] = None,
        metadata_cache: Optional[MetadataCache] = None,
    ):
        self._executor = executor
        self._context: Dict[str, Any] = (
            deepcopy(context) if context is not None else {}
        )
        self._metadata_cache = metadata_cache if metadata_cache is not None else MetadataCache()

    @property
    def executor(self) -> OdooExecutor:
        """Returns the executor that backs this environment."""
        return self._executor

    @property
    def context(self) -> Dict[str, Any]:
        """Returns a defensive copy of the current Odoo context."""
        return deepcopy(self._context)

    @property
    def metadata_cache(self) -> MetadataCache:
        """Returns the shared metadata cache for this runtime boundary."""
        return self._metadata_cache

    def with_context(self, context: Dict[str, Any]) -> "OdooEnv":
        """Returns a derived environment with merged context values."""
        merged = deepcopy(self._context)
        merged.update(deepcopy(context))
        return OdooEnv(
            self._executor,
            merged,
            metadata_cache=self._metadata_cache,
        )

    def clear_metadata_cache(self, model_name: Optional[str] = None) -> None:
        """Clears cached metadata for the current runtime boundary."""
        self._metadata_cache.clear(model_name=model_name)

    def get_field_metadata(
        self,
        model_name: str,
        fields: Optional[Sequence[str]] = None,
        attributes: Optional[Sequence[str]] = None,
        *,
        refresh: bool = False,
    ) -> Dict[str, Any]:
        """Fetches and caches raw fields_get metadata for a model."""
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

    def __getitem__(self, model_name: str) -> OdooModel:
        """Returns a model proxy anchored to this environment's executor."""
        from .odoo_model import OdooModel

        return OdooModel(self._executor, model_name, env=self)

    def recordset(
        self,
        model_name: str,
        ids: Union[int, Sequence[int]] = (),
    ) -> OdooRecordset:
        """Returns a recordset bound to this environment and model."""
        from .odoo_recordset import OdooRecordset

        return OdooRecordset(self, model_name, ids)
