from collections.abc import Mapping, Sequence
from typing import Any, TypeAlias, Union

DomainCondition: TypeAlias = tuple[str, str, Any]
Domain: TypeAlias = list[DomainCondition]
DomainInput: TypeAlias = Union[DomainCondition, Sequence[Any], None]
Record: TypeAlias = Mapping[str, Any]
