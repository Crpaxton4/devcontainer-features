from __future__ import annotations

import threading
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

MetadataLoader = Callable[[], dict[str, Any]]


def _freeze_context_value(value: Any) -> Any:
    """Convert nested context values into hashable, deterministic structures."""
    if isinstance(value, dict):
        return tuple(
            sorted(
                (str(key), _freeze_context_value(item)) for key, item in value.items()
            )
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_context_value(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_freeze_context_value(item) for item in value), key=repr))
    return value


def _normalize_requested_names(
    names: Optional[Sequence[str]],
) -> Optional[tuple[str, ...]]:
    """Return sorted unique names for cache-key use, or None when none were requested."""
    if names is None:
        return None
    return tuple(sorted(set(names)))


def _normalize_context(
    context: Optional[dict[str, Any]],
) -> Optional[tuple[tuple[str, Any], ...]]:
    """Return an Odoo context frozen for cache-key use, or None when empty.

    Delegates to :func:`_freeze_context_value`, whose dict branch already produces
    the sorted ``(key, frozen-value)`` tuple this needs.
    """
    return _freeze_context_value(context) if context else None


@dataclass(frozen=True)
class MetadataRequestKey:
    """Identify one cached ``fields_get`` request.

    Keyed by model, requested field subset, requested attribute subset, and
    context-sensitive shape, each already normalized for stable hashing.
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
        """Build a normalized cache key from raw metadata request inputs."""
        return cls(
            model_name=model_name,
            fields=_normalize_requested_names(fields),
            attributes=_normalize_requested_names(attributes),
            context=_normalize_context(context),
        )


class MetadataCache:
    """Thread-safe cache of raw ``fields_get`` payloads for one runtime boundary."""

    def __init__(self) -> None:
        self._entries: dict[MetadataRequestKey, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def __len__(self) -> int:
        """Return the number of cached metadata entries."""
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
        """Return a deep-copied cached payload, or load, store, and return a fresh one.

        ``refresh=True`` bypasses any existing entry. Payloads are deep-copied on both
        store and return so a caller can never mutate the cached copy.
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

    def clear(self, model_name: Optional[str] = None) -> None:
        """Clear cached metadata for ``model_name``, or the whole cache when None."""
        with self._lock:
            if model_name is None:
                self._entries.clear()
                return

            self._entries = {
                k: v for k, v in self._entries.items() if k.model_name != model_name
            }
