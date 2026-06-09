from abc import ABC
from typing import Any


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

    def execute_as(self, uid: int, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        """Execute one method on an Odoo model using an explicit user id.

        This method is necessary so callers can override the authenticated uid on a
        per-call basis without mutating the executor's own credentials.

        :param uid: User id to use for this call instead of the executor's own uid.
        :type uid: int
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
        raise NotImplementedError("Subclasses must implement `execute_as`")
