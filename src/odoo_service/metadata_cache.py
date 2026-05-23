from __future__ import annotations

import threading
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

MetadataLoader = Callable[[], dict[str, Any]]


def _freeze_context_value(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(
            (str(key), _freeze_context_value(item))
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
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
    if names is None:
        return None
    return tuple(sorted(set(names)))


def _normalize_context(
    context: Optional[dict[str, Any]],
) -> Optional[tuple[tuple[str, Any], ...]]:
    if not context:
        return None
    return tuple(
        (str(key), _freeze_context_value(value))
        for key, value in sorted(context.items(), key=lambda pair: str(pair[0]))
    )


@dataclass(frozen=True)
class MetadataRequestKey:
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
        return cls(
            model_name=model_name,
            fields=_normalize_requested_names(fields),
            attributes=_normalize_requested_names(attributes),
            context=_normalize_context(context),
        )


class MetadataCache:
    def __init__(self) -> None:
        self._entries: dict[MetadataRequestKey, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def __len__(self) -> int:
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
        with self._lock:
            if model_name is None:
                self._entries.clear()
                return

            keys_to_remove = [
                key for key in self._entries if key.model_name == model_name
            ]
            for key in keys_to_remove:
                del self._entries[key]