from abc import ABC
from typing import Any

from odoo_sdk.transport.errors import forbid_unlink


class OdooExecutor(ABC):
    """Define the minimal execution contract shared by SDK facade objects.

    The executor interface is necessary because models, queries, clients, and test
    doubles all need one stable way to issue Odoo operations without depending on a
    specific transport implementation.
    """

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        """Execute one method on an Odoo model; concrete executors must override."""
        raise NotImplementedError("Subclasses must implement `execute`")


def guarded_execute(
    executor: OdooExecutor, model: str, method: str, *args: Any, **kwargs: Any
) -> Any:
    """Route one model-method call through the single guarded transport seam.

    This gateway is the ONE chokepoint every model-method call crosses. Both
    :meth:`OdooClient.execute` and :meth:`OdooRecordset._execute` delegate here so
    the cross-cutting :func:`forbid_unlink` guard is applied in exactly one place,
    before any executor delegation — so even an injected test executor cannot let
    an explicit ``unlink`` through.

    :raises DeletionNotSupportedError: When ``method`` is ``unlink``.
    """
    forbid_unlink(method)
    return executor.execute(model, method, *args, **kwargs)
