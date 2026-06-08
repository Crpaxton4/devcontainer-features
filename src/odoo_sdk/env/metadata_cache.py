from __future__ import annotations

import threading
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

MetadataLoader = Callable[[], dict[str, Any]]


def _freeze_context_value(value: Any) -> Any:
    """Convert nested context values into hashable, deterministic structures.

    This helper is necessary because metadata cache keys include context, and Python
    dictionaries, lists, and sets must be normalized before they can participate in a
    stable key.

    :param value: Context value to freeze.
    :type value: Any
    :return: Hashable representation of the input value.
    :rtype: Any
    """
    if isinstance(value, dict):
        return tuple(
            sorted(
                (str(key), _freeze_context_value(item))
                for key, item in value.items()
            )
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_context_value(item) for item in value)
    if isinstance(value, set):
        return tuple(
            sorted((_freeze_context_value(item) for item in value), key=repr)
        )
    return value


def _normalize_requested_names(
    names: Optional[Sequence[str]],
) -> Optional[tuple[str, ...]]:
    """Normalize requested field or attribute names for cache-key use.

    This helper is necessary because cache keys must treat duplicate names and input
    order as irrelevant while still distinguishing omission from an explicit request.

    :param names: Requested field or attribute names, defaults to None.
    :type names: Optional[Sequence[str]]
    :return: Sorted unique names, or None when no names were requested.
    :rtype: Optional[tuple[str, ...]]
    """
    if names is None:
        return None
    return tuple(sorted(set(names)))


def _normalize_context(
    context: Optional[dict[str, Any]],
) -> Optional[tuple[tuple[str, Any], ...]]:
    """Normalize an Odoo context mapping for cache-key use.

    This helper is necessary because `fields_get` results can depend on context, so
    the cache must distinguish materially different context payloads deterministically.

    :param context: Context mapping to normalize, defaults to None.
    :type context: Optional[dict[str, Any]]
    :return: Sorted frozen context items, or None when the context is empty.
    :rtype: Optional[tuple[tuple[str, Any], ...]]
    """
    if not context:
        return None
    return tuple(
        sorted(
            (str(key), _freeze_context_value(value))
            for key, value in context.items()
        )
    )


@dataclass(frozen=True)
class MetadataRequestKey:
    """Identify one cached `fields_get` request.

    This key object is necessary because metadata lookups must be cached by model,
    requested field subset, requested attribute subset, and context-sensitive shape.

    :param model_name: Model whose metadata was requested.
    :type model_name: str
    :param fields: Normalized requested field names, or None.
    :type fields: Optional[tuple[str, ...]]
    :param attributes: Normalized requested attribute names, or None.
    :type attributes: Optional[tuple[str, ...]]
    :param context: Normalized context key, or None.
    :type context: Optional[tuple[tuple[str, Any], ...]]
    """

    model_name: str
    fields: Optional[tuple[str, ...]]
    attributes: Optional[tuple[str, ...]]
    context: Optional[tuple[tuple[str, Any], ...]]

    @classmethod
    def from_request(
        cls,
        model_name: str,
        *,
        fields: Optional[Sequence[str]] = None,
        attributes: Optional[Sequence[str]] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> "MetadataRequestKey":
        """Build a cache key from raw metadata request inputs.

        This factory is necessary because callers should not duplicate the rules that
        normalize requested fields, attributes, and context into cache-safe values.

        :param model_name: Model whose metadata was requested.
        :type model_name: str
        :param fields: Requested field names, defaults to None.
        :type fields: Optional[Sequence[str]]
        :param attributes: Requested attribute names, defaults to None.
        :type attributes: Optional[Sequence[str]]
        :param context: Context affecting the request, defaults to None.
        :type context: Optional[dict[str, Any]]
        :return: Normalized request key.
        :rtype: MetadataRequestKey
        """
        return cls(
            model_name=model_name,
            fields=_normalize_requested_names(fields),
            attributes=_normalize_requested_names(attributes),
            context=_normalize_context(context),
        )


class MetadataCache:
    """Cache raw `fields_get` payloads for one runtime boundary.

    This cache is necessary because repeated metadata lookups are expensive and Phase
    B semantic features rely on the same metadata across recordset and compatibility
    flows.
    """

    def __init__(self) -> None:
        """Initialize the metadata cache storage and lock.

        This constructor is necessary because metadata is shared across threads and
        derived environments, so access must be coordinated.

        :return: None.
        :rtype: None
        """
        self._entries: dict[MetadataRequestKey, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def __len__(self) -> int:
        """Return the number of cached metadata entries.

        This helper is necessary for tests and diagnostics that need to inspect cache
        growth without reaching into private storage.

        :return: Number of cached request entries.
        :rtype: int
        """
        with self._lock:
            return len(self._entries)

    def get_or_load(
        self,
        model_name: str,
        *,
        fields: Optional[Sequence[str]] = None,
        attributes: Optional[Sequence[str]] = None,
        context: Optional[dict[str, Any]] = None,
        refresh: bool = False,
        loader: MetadataLoader,
    ) -> dict[str, Any]:
        """Return cached metadata or load and store a fresh payload.

        This method is necessary because the environment needs one synchronized place
        to enforce cache-key normalization, refresh behavior, and defensive copying.

        :param model_name: Model whose metadata is requested.
        :type model_name: str
        :param fields: Requested field names, defaults to None.
        :type fields: Optional[Sequence[str]]
        :param attributes: Requested metadata attributes, defaults to None.
        :type attributes: Optional[Sequence[str]]
        :param context: Context that affects the metadata response, defaults to None.
        :type context: Optional[dict[str, Any]]
        :param refresh: When True, bypass an existing cache entry, defaults to False.
        :type refresh: bool
        :param loader: Callable that loads metadata when the cache misses.
        :type loader: MetadataLoader
        :return: Deep-copied metadata payload.
        :rtype: dict[str, Any]
        """
        key = MetadataRequestKey.from_request(
            model_name,
            fields=fields,
            attributes=attributes,
            context=context,
        )
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None and not refresh:
                return deepcopy(cached)

            loaded = deepcopy(loader())
            self._entries[key] = loaded
            return deepcopy(loaded)

    def clear(self, *, model_name: Optional[str] = None) -> None:
        """Clear cached metadata globally or for one model.

        This method is necessary because metadata may change at runtime and callers
        need an explicit invalidation path instead of waiting for process restart.

        :param model_name: Model whose entries should be removed, or None to clear the
            full cache, defaults to None.
        :type model_name: Optional[str]
        :return: None.
        :rtype: None
        """
        with self._lock:
            if model_name is None:
                self._entries.clear()
                return

            self._entries = {
                k: v for k, v in self._entries.items() if k.model_name != model_name
            }
