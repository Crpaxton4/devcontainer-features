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
        """Execute one method on an Odoo model.

        The base implementation exists to make the abstract contract explicit even
        under mutation testing. Concrete executors must override it with real
        transport behavior.

        :param model: Name of the Odoo model to call.
        :type model: str
        :param method: Name of the method to execute.
        :type method: str
        :param args: Positional arguments to pass to Odoo.
        :type args: Any
        :param kwargs: Keyword arguments to pass to Odoo.
        :type kwargs: Any
        :raises NotImplementedError: Raised when a subclass does not provide a
            concrete implementation.
        :return: The executor-specific result.
        :rtype: Any
        """
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

    :param executor: Executor that owns the concrete transport implementation.
    :type executor: OdooExecutor
    :param model: Name of the Odoo model to call.
    :type model: str
    :param method: Name of the Odoo method to invoke.
    :type method: str
    :param args: Positional RPC arguments forwarded to the executor.
    :type args: Any
    :param kwargs: Keyword RPC arguments forwarded to the executor.
    :type kwargs: Any
    :raises DeletionNotSupportedError: When ``method`` is ``unlink``.
    :return: Result returned by the executor.
    :rtype: Any
    """
    forbid_unlink(method)
    return executor.execute(model, method, *args, **kwargs)
