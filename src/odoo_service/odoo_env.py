from copy import deepcopy
from typing import Any, Dict, Optional

from .odoo_executor import OdooExecutor
from .odoo_model import OdooModel


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
        return OdooModel(self._executor, model_name)