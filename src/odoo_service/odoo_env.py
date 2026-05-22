from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional, Sequence, TYPE_CHECKING, Union

from .odoo_executor import OdooExecutor

if TYPE_CHECKING:
    from .odoo_model import OdooModel
    from .odoo_recordset import OdooRecordset


class OdooEnv:
    """Thin environment root for executor access and immutable context state."""

    def __init__(
        self,
        executor: OdooExecutor,
        context: Optional[Dict[str, Any]] = None,
    ):
        self._executor = executor
        self._context: Dict[str, Any] = (
            deepcopy(context) if context is not None else {}
        )

    @property
    def executor(self) -> OdooExecutor:
        """Returns the executor that backs this environment."""
        return self._executor

    @property
    def context(self) -> Dict[str, Any]:
        """Returns a defensive copy of the current Odoo context."""
        return deepcopy(self._context)

    def with_context(self, context: Dict[str, Any]) -> "OdooEnv":
        """Returns a derived environment with merged context values."""
        merged = deepcopy(self._context)
        merged.update(deepcopy(context))
        return OdooEnv(self._executor, merged)

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