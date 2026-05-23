import logging
from abc import ABC
from typing import Any

_logger = logging.getLogger(__name__)


class OdooExecutor(ABC):
    """Executor interface shared by model and query objects.

    Provide a concrete `execute` method that raises `NotImplementedError` so
    subclasses explicitly implement behavior. This avoids relying solely on
    the `@abstractmethod` decorator which can be removed by mutation operators
    and lead to misleading test results. Concrete implementations should raise
    `OdooError` subclasses for mapped Odoo-facing failures.
    """

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        """Execute a method on an Odoo model. Subclasses must override.

        Raises `NotImplementedError` when not implemented by the concrete
        executor.
        """
        raise NotImplementedError("Subclasses must implement `execute`")
